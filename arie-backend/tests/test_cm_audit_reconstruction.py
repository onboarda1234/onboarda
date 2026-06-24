"""
Tests for PR-CM-AUDIT-RECONSTRUCTION-1.

The reconstruction helper/API must let auditors rebuild the CM story without
changing approval, evidence, risk, screening, or implementation gates.
"""

import json
import os
import secrets
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from tornado.testing import AsyncHTTPTestCase

from tests.cm_evidence_test_helpers import attach_verified_cm_evidence


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


CREATOR = {"sub": "cm-audit-maker", "name": "CM Audit Maker", "role": "co"}
ADMIN = {"sub": "cm-audit-admin", "name": "CM Audit Admin", "role": "admin"}
SCO = {"sub": "cm-audit-sco", "name": "CM Audit SCO", "role": "sco"}


class _DBWrapper:
    def __init__(self, conn):
        self._conn = conn
        self.is_postgres = False

    def execute(self, sql, params=None):
        return self._conn.execute(sql, params) if params else self._conn.execute(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass


def _get_cm():
    import change_management as cm
    return cm


def _seed_users(raw_db):
    for user in (CREATOR, ADMIN, SCO):
        raw_db.execute(
            """INSERT OR REPLACE INTO users
               (id, email, password_hash, full_name, role, status)
               VALUES (?, ?, 'test-only', ?, ?, 'active')""",
            (
                user["sub"],
                f"{user['sub']}@example.test",
                user["name"],
                user["role"],
            ),
        )
    raw_db.commit()


def _setup_app(raw_db):
    _seed_users(raw_db)
    suffix = secrets.token_hex(4)
    app_id = f"app-cm-audit-{suffix}"
    client_id = f"client-cm-audit-{suffix}"
    raw_db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"{client_id}@example.test", "hash", "Audit Test Ltd"),
    )
    raw_db.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type,
            ownership_structure, status, risk_level, risk_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', 'MEDIUM', 45)""",
        (
            app_id,
            f"CM-AUDIT-{suffix}",
            client_id,
            "Audit Test Ltd",
            "GB",
            "Technology",
            "Limited Company",
            "Simple",
        ),
    )
    raw_db.commit()
    return app_id


def _audit_writer(default_db):
    def _log(user, action, target, detail, db=None, before_state=None, after_state=None, commit=True):
        target_db = db or default_db
        target_db.execute(
            """INSERT INTO audit_log
               (user_id, user_name, user_role, action, target, detail, ip_address,
                before_state, after_state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user.get("sub", ""),
                user.get("name", ""),
                user.get("role", ""),
                action,
                target,
                detail,
                "127.0.0.1",
                json.dumps(before_state, default=str) if before_state is not None else None,
                json.dumps(after_state, default=str) if after_state is not None else None,
            ),
        )
        if commit:
            target_db.commit()

    return _log


def _company_name_item(new_name="Audit Test Renamed Ltd"):
    return {
        "change_type": "company_details",
        "field_name": "company_name",
        "old_value": "Audit Test Ltd",
        "new_value": new_name,
        "materiality": "tier1",
    }


def _create_request(cm, wdb, app_id, item=None, *, log=None):
    return cm.create_change_request(
        wdb,
        app_id,
        "backoffice_manual",
        "backoffice",
        "Audit reconstruction test",
        [item or _company_name_item()],
        CREATOR,
        log_audit_fn=log,
    )


def _to_approval_pending(cm, wdb, request_id, *, log=None):
    assert cm.submit_change_request(wdb, request_id, CREATOR, log_audit_fn=log)[0]
    assert cm.update_change_request_status(
        wdb, request_id, "triage_in_progress", CREATOR, log_audit_fn=log
    )[0]
    assert cm.update_change_request_status(
        wdb, request_id, "ready_for_review", CREATOR, log_audit_fn=log
    )[0]
    assert cm.update_change_request_status(
        wdb, request_id, "approval_pending", CREATOR, log_audit_fn=log
    )[0]


def _record_preconditions(cm, wdb, request_id, *, log=None):
    ok, err = cm.record_precondition_result(
        wdb,
        request_id,
        "screening",
        SCO,
        result={
            "screening_ref": "screen-cm-audit-1",
            "screened_at": "2026-06-24T00:00:00Z",
            "unresolved_match": False,
        },
        note="clean persisted screening",
        log_audit_fn=log,
    )
    assert ok, err
    ok, err = cm.record_precondition_result(
        wdb,
        request_id,
        "risk",
        SCO,
        result={"risk_level": "MEDIUM", "risk_score": 45},
        note="persisted risk reviewed",
        log_audit_fn=log,
    )
    assert ok, err


def _approve_request(cm, wdb, request_id, *, log=None):
    _to_approval_pending(cm, wdb, request_id, log=log)
    attach_verified_cm_evidence(cm, wdb, request_id, doc_type="certificate_name_change")
    _record_preconditions(cm, wdb, request_id, log=log)
    ok, err = cm.approve_change_request(
        wdb,
        request_id,
        ADMIN,
        decision_notes="approved for audit reconstruction",
        log_audit_fn=log,
    )
    assert ok, err


class TestChangeManagementAuditReconstruction:
    def test_reconstruction_returns_request_identity_and_change_summary(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        log = _audit_writer(wdb)
        req = _create_request(cm, wdb, app_id, log=log)

        reconstruction = cm.get_change_request_audit_reconstruction(wdb, req["id"])

        assert reconstruction["request"]["id"] == req["id"]
        assert reconstruction["application"]["id"] == app_id
        assert reconstruction["application"]["company_name"] == "Audit Test Ltd"
        assert reconstruction["change_summary"]["item_count"] == 1
        item = reconstruction["change_summary"]["items"][0]
        assert item["field_name"] == "company_name"
        assert item["old_value"] == "Audit Test Ltd"
        assert item["requested_new_value"] == "Audit Test Renamed Ltd"
        assert item["evidence_requirement"]["change_key"] == "legal_name_change"
        json.dumps(reconstruction)

    def test_reconstruction_includes_linked_evidence_and_agent1_status(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id)
        attach_verified_cm_evidence(cm, wdb, req["id"], doc_type="certificate_name_change")

        reconstruction = cm.get_change_request_audit_reconstruction(wdb, req["id"])

        assert len(reconstruction["evidence"]) == 1
        evidence = reconstruction["evidence"][0]
        assert evidence["linked_to_request"] is True
        assert evidence["linked_document_id"].startswith("test-cm-doc-")
        verification = reconstruction["agent1_verifications"][0]
        assert verification["verification_status"] == "verified"
        assert verification["agent1_satisfied"] is True

    def test_reconstruction_includes_screening_and_risk_precondition_references(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id)
        _record_preconditions(cm, wdb, req["id"])

        reconstruction = cm.get_change_request_audit_reconstruction(wdb, req["id"])

        assert reconstruction["screening"]["required"] is True
        assert reconstruction["screening"]["status"] == "clean"
        assert reconstruction["screening"]["screening_ref"] == "screen-cm-audit-1"
        assert reconstruction["risk"]["required"] is True
        assert reconstruction["risk"]["status"] == "recorded"
        assert reconstruction["risk"]["risk_level_after"] == "MEDIUM"

    def test_reconstruction_includes_blocked_approval_attempt(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        log = _audit_writer(wdb)
        req = _create_request(cm, wdb, app_id, log=log)
        _to_approval_pending(cm, wdb, req["id"], log=log)

        ok, err = cm.approve_change_request(wdb, req["id"], ADMIN, log_audit_fn=log)
        assert not ok
        assert "blocked by preconditions" in err.lower()

        reconstruction = cm.get_change_request_audit_reconstruction(wdb, req["id"])

        blocked = reconstruction["approval"]["blocked_attempts"]
        assert len(blocked) == 1
        assert blocked[0]["action"] == "CM Approval Blocked"
        codes = [b["code"] for b in blocked[0]["after_state"]["blockers"]]
        assert "cm_evidence_required_missing" in codes
        assert "screening_required_uncleared" in codes

    def test_reconstruction_includes_successful_approval_event(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        log = _audit_writer(wdb)
        req = _create_request(cm, wdb, app_id, log=log)
        _approve_request(cm, wdb, req["id"], log=log)

        reconstruction = cm.get_change_request_audit_reconstruction(wdb, req["id"])

        assert reconstruction["approval"]["status"] == "approved"
        assert reconstruction["approval"]["approved_by"]["id"] == ADMIN["sub"]
        assert reconstruction["approval"]["approver_role"] == "admin"
        assert reconstruction["approval"]["maker_checker"]["passed"] is True
        assert any(e["action"] == "Change Request Approved" for e in reconstruction["approval"]["approval_events"])

    def test_reconstruction_includes_blocked_implementation_attempt(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        log = _audit_writer(wdb)
        req = _create_request(cm, wdb, app_id, log=log)
        attach_verified_cm_evidence(cm, wdb, req["id"], doc_type="certificate_name_change")
        sig = cm._request_content_signature(wdb, req["id"])
        wdb.execute(
            """UPDATE change_requests
               SET status = 'approved', approved_by = ?, approved_at = ?, precondition_results = ?
               WHERE id = ?""",
            (
                ADMIN["sub"],
                "2026-06-24T00:00:00Z",
                json.dumps({
                    "screening": {
                        "result": "recorded",
                        "content_sig": sig,
                        "screening_ref": "screen-cm-audit-impl",
                        "unresolved_match": False,
                    }
                }),
                req["id"],
            ),
        )
        wdb.commit()

        ok, err, _ = cm.implement_change_request(wdb, req["id"], ADMIN, log_audit_fn=log)
        assert not ok
        assert "cm_implementation_risk_review_required" in err

        reconstruction = cm.get_change_request_audit_reconstruction(wdb, req["id"])

        blocked = reconstruction["implementation"]["blocked_attempts"]
        assert len(blocked) == 1
        assert blocked[0]["action"] == "CM Implementation Blocked"
        codes = [b["code"] for b in blocked[0]["after_state"]["blockers"]]
        assert "cm_implementation_risk_review_required" in codes

    def test_reconstruction_includes_successful_implementation_event_and_before_after(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        log = _audit_writer(wdb)
        req = _create_request(cm, wdb, app_id, _company_name_item("Audit Implemented Ltd"), log=log)
        _approve_request(cm, wdb, req["id"], log=log)

        ok, err, version_id = cm.implement_change_request(wdb, req["id"], ADMIN, log_audit_fn=log)
        assert ok, err

        reconstruction = cm.get_change_request_audit_reconstruction(wdb, req["id"])

        assert reconstruction["implementation"]["status"] == "implemented"
        assert reconstruction["implementation"]["profile_version_id"] == version_id
        assert reconstruction["implementation"]["old_live_profile"]["company_name"] == "Audit Test Ltd"
        assert reconstruction["implementation"]["new_live_profile"]["company_name"] == "Audit Implemented Ltd"
        item = reconstruction["change_summary"]["items"][0]
        assert item["final_implemented_value"] == "Audit Implemented Ltd"
        assert any(e["action"] == "Change Request Implemented" for e in reconstruction["implementation"]["implementation_events"])

    def test_reconstruction_handles_missing_optional_data_without_500(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id)

        reconstruction = cm.get_change_request_audit_reconstruction(wdb, req["id"])

        assert reconstruction["evidence"] == []
        assert reconstruction["agent1_verifications"] == []
        assert reconstruction["approval"]["blocked_attempts"] == []
        assert reconstruction["implementation"]["blocked_attempts"] == []
        json.dumps(reconstruction)

    def test_json_safety_handles_decimal_datetime_row_and_none(self, db):
        cm = _get_cm()
        row = db.execute("SELECT 1 AS one, NULL AS empty").fetchone()
        payload = cm._json_safe_precondition_value({
            "decimal": Decimal("12.50"),
            "datetime": datetime(2026, 6, 24, tzinfo=timezone.utc),
            "row": row,
            "none": None,
        })

        assert payload["decimal"] == 12.5
        assert payload["datetime"] == "2026-06-24T00:00:00+00:00"
        assert payload["row"] == {"one": 1, "empty": None}
        assert payload["none"] is None
        json.dumps(payload)

    def test_reconstruction_returns_rejected_request_review(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        log = _audit_writer(wdb)
        req = _create_request(cm, wdb, app_id, log=log)
        _to_approval_pending(cm, wdb, req["id"], log=log)

        ok, err = cm.reject_change_request(
            wdb,
            req["id"],
            ADMIN,
            decision_notes="insufficient rationale",
            log_audit_fn=log,
        )
        assert ok, err

        reconstruction = cm.get_change_request_audit_reconstruction(wdb, req["id"])

        assert reconstruction["request"]["status"] == "rejected"
        assert reconstruction["approval"]["reviews"][0]["decision"] == "rejected"
        assert any(row["action"] == "Change Request Rejected" for row in reconstruction["timeline"])


def _sync_test_db_path(path):
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


class ChangeRequestAuditReconstructionApiTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"cm_audit_reconstruction_api_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        _sync_test_db_path(self._db_path)
        from db import init_db, seed_initial_data, get_db
        from server import make_app

        init_db()
        conn = get_db()
        seed_initial_data(conn)
        conn.execute(
            """INSERT OR REPLACE INTO clients
               (id, email, password_hash, company_name, status)
               VALUES ('cm-audit-client', 'cm-audit-client@example.test', 'hash', 'CM Audit Client', 'active')"""
        )
        conn.commit()
        conn.close()
        return make_app()

    def test_client_cannot_access_backoffice_reconstruction_endpoint(self):
        from auth import create_token

        token = create_token("cm-audit-client", "client", "CM Audit Client", "client")
        response = self.fetch(
            "/api/change-management/requests/CR-NOPE/audit-reconstruction",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.code == 403

