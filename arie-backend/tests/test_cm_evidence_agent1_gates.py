"""
Tests for PR-CM-EVIDENCE-AGENT1-GATES-1.

Material Change Management requests must have request-linked evidence and
Agent 1 verification/acceptance where required before approval.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _get_cm():
    import change_management as cm
    return cm


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


CREATOR = {"sub": "co-maker", "name": "CO Maker", "role": "co"}
SCO = {"sub": "sco-checker", "name": "SCO Checker", "role": "sco"}
ADMIN = {"sub": "admin-checker", "name": "Admin Checker", "role": "admin"}
CLEAN_SCREEN = {"screening_ref": "screen-clean-1", "screened_at": "2026-01-01T00:00:00Z", "unresolved_match": False}
RISK_RESULT = {"risk_level": "MEDIUM"}


def _setup_app(raw_db):
    app_id = f"app-cm-evidence-{secrets.token_hex(4)}"
    client_id = f"client-cm-evidence-{secrets.token_hex(4)}"
    raw_db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"{client_id}@example.test", "hash", "Evidence Co"),
    )
    raw_db.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', 'MEDIUM')""",
        (app_id, f"APP-{secrets.token_hex(4)}", client_id, "Evidence Co Ltd", "GB", "Technology", "Limited Company"),
    )
    raw_db.commit()
    return app_id


def _create_request(cm, wdb, app_id, item, creator=CREATOR):
    return cm.create_change_request(
        wdb,
        app_id,
        "backoffice_manual",
        "backoffice",
        "Evidence gate test",
        [item],
        creator,
    )


def _to_pending(cm, wdb, req_id, user=CREATOR):
    cm.submit_change_request(wdb, req_id, user)
    cm.update_change_request_status(wdb, req_id, "triage_in_progress", user)
    cm.update_change_request_status(wdb, req_id, "ready_for_review", user)
    cm.update_change_request_status(wdb, req_id, "approval_pending", user)


def _record_preconditions(cm, wdb, req_id):
    ok_s, err_s = cm.record_precondition_result(wdb, req_id, "screening", SCO, result=CLEAN_SCREEN)
    assert ok_s, err_s
    ok_r, err_r = cm.record_precondition_result(wdb, req_id, "risk", SCO, result=RISK_RESULT)
    assert ok_r, err_r


def _first_item_id(db, req_id):
    return db.execute(
        "SELECT id FROM change_request_items WHERE request_id = ? ORDER BY id",
        (req_id,),
    ).fetchone()["id"]


def _insert_app_doc(db, app_id, *, doc_type, status="verified", review_status="pending", valid_until=None):
    doc_id = f"doc-cm-evidence-{secrets.token_hex(6)}"
    db.execute(
        """INSERT INTO documents
           (id, application_id, doc_type, doc_name, file_path, verification_status, review_status, valid_until)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            doc_id,
            app_id,
            doc_type,
            f"{doc_type}.pdf",
            f"/tmp/{doc_id}.pdf",
            status,
            review_status,
            valid_until,
        ),
    )
    db.commit()
    return doc_id


def _link_doc(db, req_id, doc_id, *, item_id=None, doc_type="supporting_document"):
    db.execute(
        """INSERT INTO change_request_documents
           (id, request_id, item_id, doc_name, doc_type, file_path, s3_key, uploaded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"crdoc-cm-evidence-{secrets.token_hex(6)}",
            req_id,
            item_id,
            f"{doc_type}.pdf",
            doc_type,
            f"/tmp/{doc_id}.pdf",
            f"document:{doc_id}",
            "test",
        ),
    )
    db.commit()


def _add_linked_doc(db, app_id, req_id, *, doc_type, status="verified", review_status="pending", item_id=None, valid_until=None):
    doc_id = _insert_app_doc(
        db,
        app_id,
        doc_type=doc_type,
        status=status,
        review_status=review_status,
        valid_until=valid_until,
    )
    _link_doc(db, req_id, doc_id, item_id=item_id, doc_type=doc_type)
    return doc_id


def _approve_after_preconditions(cm, wdb, req_id):
    _to_pending(cm, wdb, req_id)
    _record_preconditions(cm, wdb, req_id)
    return cm.approve_change_request(wdb, req_id, ADMIN)


def _legal_name_item():
    return {
        "change_type": "legal_name_change",
        "field_name": "company_name",
        "old_value": "Evidence Co Ltd",
        "new_value": "Evidence Co Renamed Ltd",
        "materiality": "tier1",
    }


class TestCompanyEvidenceGate:
    def test_legal_name_without_required_evidence_blocks_approval(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _legal_name_item())
        ok, err = _approve_after_preconditions(cm, wdb, req["id"])
        assert not ok
        assert "cm_evidence_required_missing" in err

    def test_legal_name_with_linked_pending_agent1_blocks_approval(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _legal_name_item())
        _add_linked_doc(db, app_id, req["id"], doc_type="certificate_name_change", status="pending")
        ok, err = _approve_after_preconditions(cm, wdb, req["id"])
        assert not ok
        assert "cm_evidence_verification_pending" in err

    def test_legal_name_with_linked_passed_agent1_allows_approval(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _legal_name_item())
        _add_linked_doc(db, app_id, req["id"], doc_type="certificate_name_change", status="verified")
        ok, err = _approve_after_preconditions(cm, wdb, req["id"])
        assert ok, err

    def test_application_document_not_linked_to_request_does_not_satisfy_gate(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _legal_name_item())
        _insert_app_doc(db, app_id, doc_type="certificate_name_change", status="verified")
        ok, err = _approve_after_preconditions(cm, wdb, req["id"])
        assert not ok
        assert "cm_evidence_not_linked" in err

    def test_failed_agent1_verification_blocks_approval(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _legal_name_item())
        _add_linked_doc(db, app_id, req["id"], doc_type="certificate_name_change", status="failed")
        ok, err = _approve_after_preconditions(cm, wdb, req["id"])
        assert not ok
        assert "cm_evidence_verification_failed" in err

    def test_stale_evidence_blocks_approval(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _legal_name_item())
        stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _add_linked_doc(db, app_id, req["id"], doc_type="certificate_name_change", status="verified", valid_until=stale)
        ok, err = _approve_after_preconditions(cm, wdb, req["id"])
        assert not ok
        assert "cm_evidence_verification_stale" in err


class TestMaterialChangeEvidenceMatrix:
    def _assert_missing_evidence_blocks(self, db, item, expected_code="cm_evidence_required_missing"):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, item)
        ok, err = _approve_after_preconditions(cm, wdb, req["id"])
        assert not ok
        assert expected_code in err

    def test_director_added_without_id_or_register_evidence_blocks_approval(self, db):
        self._assert_missing_evidence_blocks(db, {
            "change_type": "director_add",
            "field_name": "directors",
            "new_value": json.dumps({"full_name": "New Director"}),
            "materiality": "tier1",
            "person_action": "add",
        })

    def test_ubo_added_without_ownership_or_id_evidence_blocks_approval(self, db):
        self._assert_missing_evidence_blocks(db, {
            "change_type": "ubo_add",
            "field_name": "ubos",
            "new_value": json.dumps({"full_name": "New UBO"}),
            "materiality": "tier1",
            "person_action": "add",
        })

    def test_business_activity_without_supporting_evidence_blocks_approval(self, db):
        self._assert_missing_evidence_blocks(db, {
            "change_type": "business_activity_change",
            "field_name": "sector",
            "old_value": "Technology",
            "new_value": "Virtual assets",
            "materiality": "tier1",
        })

    def test_licence_change_without_licence_evidence_blocks_approval(self, db):
        self._assert_missing_evidence_blocks(db, {
            "change_type": "licensing_status_change",
            "field_name": "licence_status",
            "old_value": "Unregulated",
            "new_value": "Licensed",
            "materiality": "tier1",
        })

    def test_source_of_funds_without_evidence_blocks_approval(self, db):
        self._assert_missing_evidence_blocks(db, {
            "change_type": "source_of_funds_change",
            "field_name": "source_of_funds",
            "old_value": "Revenue",
            "new_value": "Investor funds",
            "materiality": "tier1",
        })


class TestContactAndApprovalPaths:
    def test_contact_detail_change_requires_officer_review_note_without_document(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, {
            "change_type": "contact_detail_update",
            "field_name": "contact_email",
            "old_value": "old@example.test",
            "new_value": "new@example.test",
            "materiality": "tier3",
        }, creator=SCO)
        _to_pending(cm, wdb, req["id"], user=SCO)
        ok, err = cm.approve_change_request(wdb, req["id"], SCO)
        assert not ok
        assert "cm_officer_review_note_missing" in err

    def test_contact_detail_change_with_officer_note_allows_approval(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, {
            "change_type": "contact_detail_update",
            "field_name": "contact_email",
            "old_value": "old@example.test",
            "new_value": "new@example.test",
            "materiality": "tier3",
            "person_snapshot": {"callback_note": "Called registered director and confirmed update."},
        }, creator=SCO)
        _to_pending(cm, wdb, req["id"], user=SCO)
        ok, err = cm.approve_change_request(wdb, req["id"], SCO)
        assert ok, err

    def test_dedicated_approve_endpoint_path_is_blocked_by_evidence_gate(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _legal_name_item())
        ok, err = _approve_after_preconditions(cm, wdb, req["id"])
        assert not ok
        assert "cm_evidence_required_missing" in err

    def test_patch_status_approved_path_is_blocked_by_evidence_gate(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _legal_name_item())
        _to_pending(cm, wdb, req["id"])
        _record_preconditions(cm, wdb, req["id"])
        ok, err = cm.update_change_request_status(wdb, req["id"], "approved", ADMIN)
        assert not ok
        assert "cm_evidence_required_missing" in err

    def test_existing_maker_checker_screening_risk_blockers_still_apply(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db); app_id = _setup_app(db)
        req = _create_request(cm, wdb, app_id, _legal_name_item(), creator=SCO)
        _add_linked_doc(db, app_id, req["id"], doc_type="certificate_name_change", status="verified")
        _to_pending(cm, wdb, req["id"], user=SCO)
        ok, err = cm.approve_change_request(wdb, req["id"], SCO)
        assert not ok
        assert "maker_checker_same_user" in err
        assert "screening_required_uncleared" in err
        assert "risk_review_required_uncleared" in err


def test_cm_document_upload_uses_existing_file_validator_api():
    """Regression: CM evidence upload must not call a non-existent validator."""
    source = Path(__file__).resolve().parents[1] / "server.py"
    text = source.read_text()
    block = text.split("class ChangeRequestDocumentHandler", 1)[1].split(
        "class ChangeManagementStatsHandler", 1
    )[0]

    assert "FileUploadValidator.validate_upload" not in block
    assert "FileUploadValidator.validate_with_reason" in block
    assert 'uploaded.get("content_type", "application/octet-stream")' in block
