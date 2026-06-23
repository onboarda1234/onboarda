"""
CM implementation gate tests.

PR-CM-RISK-SCREENING-BEFORE-IMPLEMENTATION-1 protects the final live-profile
implementation step. Approval-time gates may already be satisfied or bypassed
by legacy data, so implementation must fail closed when current risk/screening
markers are missing, stale, unresolved, or indeterminate.
"""

import json
import secrets

from tests.cm_evidence_test_helpers import attach_verified_cm_evidence


ADMIN = {"sub": "admin-impl", "name": "Admin Impl", "role": "admin"}
SCO = {"sub": "sco-impl", "name": "SCO Impl", "role": "sco"}


def _get_cm():
    import change_management as cm
    return cm


class _DBWrapper:
    def __init__(self, conn):
        self._conn = conn
        self.is_postgres = False

    def execute(self, sql, params=None):
        if params:
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass


def _setup_app(raw_db):
    app_id = f"app-cm-impl-{secrets.token_hex(4)}"
    client_id = f"client-cm-impl-{secrets.token_hex(4)}"
    raw_db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"cm-impl-{secrets.token_hex(4)}@example.test", "hash", "Impl Test Ltd"),
    )
    raw_db.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            app_id,
            f"CM-IMPL-{secrets.token_hex(3)}",
            client_id,
            "Impl Test Ltd",
            "GB",
            "Financial Services",
            "Limited Company",
            "approved",
            "MEDIUM",
        ),
    )
    raw_db.execute(
        """INSERT INTO directors
           (id, application_id, person_key, full_name, first_name, last_name, nationality, date_of_birth)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_id, "dir1", "Director One", "Director", "One", "GB", "1980-01-01"),
    )
    raw_db.execute(
        """INSERT INTO ubos
           (id, application_id, person_key, full_name, first_name, last_name, nationality, ownership_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_id, "ubo1", "Owner One", "Owner", "One", "GB", 75.0),
    )
    raw_db.commit()
    return app_id


def _create_request(cm, wdb, app_id, item):
    return cm.create_change_request(
        wdb,
        app_id,
        "backoffice_manual",
        "backoffice",
        "Implementation gate test",
        [item],
        ADMIN,
    )


def _approve_directly(cm, wdb, req_id, *, screening=None, risk=None, stale=None):
    """Force an approved request with explicit precondition marker shape.

    This simulates approved legacy/edge requests so implementation can prove it
    fails closed independently of the approval gate.
    """
    attach_verified_cm_evidence(cm, wdb, req_id)
    sig = cm._request_content_signature(wdb, req_id)
    stale = set(stale or ())
    results = {}
    if screening is not None:
        results["screening"] = {
            "result": "recorded",
            "content_sig": "stale" if "screening" in stale else sig,
            "screening_ref": "screen-impl-test",
            "screened_at": "2026-06-23T00:00:00Z",
            "unresolved_match": screening,
        }
    if risk is not None:
        results["risk"] = {
            "result": "recorded",
            "content_sig": "stale" if "risk" in stale else sig,
            "risk_level": risk.get("risk_level", "MEDIUM"),
            "risk_increased": risk.get("risk_increased", False),
            **{k: v for k, v in risk.items() if k not in {"risk_level", "risk_increased"}},
        }
    wdb.execute(
        """UPDATE change_requests
           SET status = 'approved', approved_by = ?, approved_at = ?, precondition_results = ?
           WHERE id = ?""",
        (SCO["sub"], "2026-06-23T00:00:00Z", json.dumps(results), req_id),
    )
    wdb.commit()


def _company_name_item(new_name="Impl Test Renamed Ltd"):
    return {
        "change_type": "company_details",
        "field_name": "company_name",
        "old_value": "Impl Test Ltd",
        "new_value": new_name,
        "materiality": "tier1",
    }


def _director_add_item():
    return {
        "change_type": "director_add",
        "person_action": "add",
        "materiality": "tier1",
        "person_snapshot": {
            "person_key": f"dir-new-{secrets.token_hex(3)}",
            "full_name": "New Director",
            "first_name": "New",
            "last_name": "Director",
            "nationality": "GB",
            "date_of_birth": "1990-01-01",
        },
    }


def _ubo_add_item():
    item = _director_add_item()
    item.update({"change_type": "ubo_add"})
    item["person_snapshot"]["person_key"] = f"ubo-new-{secrets.token_hex(3)}"
    item["person_snapshot"]["ownership_pct"] = 25.0
    return item


def _shareholding_change_item():
    return {
        "change_type": "shareholding_change",
        "field_name": "ownership_pct",
        "old_value": "75.0",
        "new_value": "60.0",
        "materiality": "tier1",
        "person_action": "update",
        "person_snapshot": {"person_key": "ubo1"},
    }


def _audit_collector(bucket):
    def _log(user, action, target, detail, db=None, before_state=None, after_state=None):
        bucket.append({
            "user": user,
            "action": action,
            "target": target,
            "detail": detail,
            "before_state": before_state,
            "after_state": after_state,
        })
    return _log


class TestImplementationGate:
    def test_not_approved_request_cannot_be_implemented(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _company_name_item())

        ok, err, version_id = cm.implement_change_request(wdb, req["id"], ADMIN)

        assert not ok
        assert version_id is None
        assert "cm_implementation_not_approved" in err

    def test_party_change_without_current_screening_blocks_implementation(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _director_add_item())
        _approve_directly(cm, wdb, req["id"], risk={"risk_level": "MEDIUM"})

        ok, err, _ = cm.implement_change_request(wdb, req["id"], ADMIN)

        assert not ok
        assert "cm_implementation_screening_required" in err

    def test_unresolved_screening_match_blocks_implementation(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _ubo_add_item())
        _approve_directly(cm, wdb, req["id"], screening=True, risk={"risk_level": "MEDIUM"})

        ok, err, _ = cm.implement_change_request(wdb, req["id"], ADMIN)

        assert not ok
        assert "cm_implementation_screening_unresolved_match" in err

    def test_indeterminate_screening_blocks_implementation(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _shareholding_change_item())
        _approve_directly(cm, wdb, req["id"], screening=None, risk={"risk_level": "MEDIUM"})
        sig = cm._request_content_signature(wdb, req["id"])
        wdb.execute(
            "UPDATE change_requests SET precondition_results = ? WHERE id = ?",
            (json.dumps({
                "screening": {
                    "result": "recorded",
                    "content_sig": sig,
                    "screening_ref": "screen-indeterminate",
                    "unresolved_match": None,
                },
                "risk": {"result": "recorded", "content_sig": sig, "risk_level": "MEDIUM"},
            }), req["id"]),
        )
        wdb.commit()

        ok, err, _ = cm.implement_change_request(wdb, req["id"], ADMIN)

        assert not ok
        assert "cm_implementation_screening_indeterminate" in err

    def test_material_change_without_current_risk_blocks_implementation(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _company_name_item())
        _approve_directly(cm, wdb, req["id"], screening=False)

        ok, err, _ = cm.implement_change_request(wdb, req["id"], ADMIN)

        assert not ok
        assert "cm_implementation_risk_review_required" in err

    def test_stale_risk_or_screening_marker_blocks_implementation(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _company_name_item())
        _approve_directly(
            cm,
            wdb,
            req["id"],
            screening=False,
            risk={"risk_level": "MEDIUM"},
            stale={"risk"},
        )

        ok, err, _ = cm.implement_change_request(wdb, req["id"], ADMIN)

        assert not ok
        assert "cm_implementation_risk_stale" in err

    def test_risk_escalation_to_high_requires_resolution(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _company_name_item())
        _approve_directly(
            cm,
            wdb,
            req["id"],
            screening=False,
            risk={"risk_level": "HIGH", "risk_increased": True},
        )

        ok, err, _ = cm.implement_change_request(wdb, req["id"], ADMIN)

        assert not ok
        assert "cm_implementation_risk_escalation_required" in err

    def test_current_clean_screening_and_risk_allow_implementation(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _company_name_item("Implemented Name Ltd"))
        _approve_directly(cm, wdb, req["id"], screening=False, risk={"risk_level": "MEDIUM"})

        ok, err, version_id = cm.implement_change_request(wdb, req["id"], ADMIN)

        assert ok, err
        assert version_id
        row = db.execute("SELECT company_name FROM applications WHERE id = ?", (app_id,)).fetchone()
        assert row["company_name"] == "Implemented Name Ltd"

    def test_patch_status_implemented_path_is_gated(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _director_add_item())
        _approve_directly(cm, wdb, req["id"], risk={"risk_level": "MEDIUM"})

        ok, err = cm.update_change_request_status(wdb, req["id"], "implemented", ADMIN)

        assert not ok
        assert "cm_implementation_screening_required" in err
        cr = db.execute("SELECT status FROM change_requests WHERE id = ?", (req["id"],)).fetchone()
        assert cr["status"] == "approved"

    def test_blocked_implementation_attempt_is_audited(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _company_name_item())
        _approve_directly(cm, wdb, req["id"], screening=False)
        audit_events = []

        ok, err, _ = cm.implement_change_request(
            wdb, req["id"], ADMIN, log_audit_fn=_audit_collector(audit_events),
        )

        assert not ok
        assert "cm_implementation_risk_review_required" in err
        assert any(event["action"] == "CM Implementation Blocked" for event in audit_events)

    def test_successful_implementation_is_audited_with_old_new_snapshot(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _company_name_item("Audited Impl Ltd"))
        _approve_directly(cm, wdb, req["id"], screening=False, risk={"risk_level": "MEDIUM"})
        audit_events = []

        ok, err, _ = cm.implement_change_request(
            wdb, req["id"], ADMIN, log_audit_fn=_audit_collector(audit_events),
        )

        assert ok, err
        event = next(event for event in audit_events if event["action"] == "Change Request Implemented")
        assert event["target"] == req["id"]
        assert event["before_state"]["company_name"] == "Impl Test Ltd"
        assert event["after_state"]["company_name"] == "Audited Impl Ltd"

    def test_repeated_implementation_is_idempotent_without_duplicate_version(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _company_name_item("Idempotent Impl Ltd"))
        _approve_directly(cm, wdb, req["id"], screening=False, risk={"risk_level": "MEDIUM"})

        ok1, err1, version1 = cm.implement_change_request(wdb, req["id"], ADMIN)
        count_after_first = db.execute(
            "SELECT COUNT(*) AS count FROM entity_profile_versions WHERE application_id = ?",
            (app_id,),
        ).fetchone()["count"]
        ok2, err2, version2 = cm.implement_change_request(wdb, req["id"], ADMIN)
        count_after_second = db.execute(
            "SELECT COUNT(*) AS count FROM entity_profile_versions WHERE application_id = ?",
            (app_id,),
        ).fetchone()["count"]

        assert ok1, err1
        assert ok2, err2
        assert version2 == version1
        assert count_after_second == count_after_first
        row = db.execute("SELECT company_name FROM applications WHERE id = ?", (app_id,)).fetchone()
        assert row["company_name"] == "Idempotent Impl Ltd"
