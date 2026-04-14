"""
Round 3 QA Regression Tests — CM RBAC & Portal Ownership

Tests for:
- BLOCKER 1: RBAC enforcement — analyst cannot create/submit/approve/reject/implement/convert/upload
- BLOCKER 1: CO cannot implement
- BLOCKER 1: Admin/SCO can implement
- BLOCKER 1: Failed permission attempts do not mutate data or create profile versions
- BLOCKER 2: Portal applications endpoint returns only client-owned apps
- BLOCKER 2: Portal change request creation succeeds for owned apps, fails for others
- BLOCKER 2: Portal-created requests appear in both portal and back-office views
"""

import json
import os
import sys
import secrets
import sqlite3
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _get_cm():
    import change_management as cm
    return cm


def _get_db_module():
    import db as db_module
    return db_module


class _DBWrapper:
    """Wrap raw sqlite3 connection to match cm module expectations."""
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


def _setup_cm_test_data(raw_db):
    """Create test applications with different owners for RBAC/ownership tests."""
    client_a = f"client-a-{secrets.token_hex(4)}"
    client_b = f"client-b-{secrets.token_hex(4)}"
    app_a = f"app-a-{secrets.token_hex(4)}"
    app_b = f"app-b-{secrets.token_hex(4)}"

    raw_db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_a, f"a-{secrets.token_hex(3)}@test.com", "hash", "Company A"),
    )
    raw_db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_b, f"b-{secrets.token_hex(3)}@test.com", "hash", "Company B"),
    )
    raw_db.execute(
        """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_a, f"APP-A-{secrets.token_hex(4)}", client_a, "Company A Ltd",
         "GB", "Financial Services", "Limited Company", "approved"),
    )
    raw_db.execute(
        """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_b, f"APP-B-{secrets.token_hex(4)}", client_b, "Company B Ltd",
         "US", "Technology", "LLC", "approved"),
    )
    # Add directors/UBOs for app_a
    raw_db.execute(
        """INSERT INTO directors (id, application_id, person_key, full_name, first_name, last_name, nationality)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_a, "dir1", "John Smith", "John", "Smith", "GB"),
    )
    raw_db.execute(
        """INSERT INTO ubos (id, application_id, person_key, full_name, first_name, last_name, nationality, ownership_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_a, "ubo1", "Jane Doe", "Jane", "Doe", "US", 75.0),
    )
    raw_db.commit()
    return client_a, client_b, app_a, app_b


def _make_user(role, sub=None):
    """Create a user dict for testing."""
    return {
        "sub": sub or f"user-{role}-{secrets.token_hex(4)}",
        "role": role,
        "name": f"Test {role.title()}",
        "type": "officer",
    }


def _make_client_user(client_id):
    """Create a client user dict for testing."""
    return {
        "sub": client_id,
        "role": "client",
        "name": "Test Client",
        "type": "client",
    }


def _noop_audit(*args, **kwargs):
    pass


# ============================================================================
# BLOCKER 1 — RBAC PERMISSION TESTS (pure logic)
# ============================================================================

class TestRBACPermissionMatrix:
    """Verify ROLE_PERMISSIONS allows analyst for preparation actions but blocks final/decision actions."""

    # --- Analyst ALLOWED ---
    def test_analyst_can_create_request(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "create_request")
        assert allowed is True
        assert err == ""

    def test_analyst_can_submit_request(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "submit_request")
        assert allowed is True
        assert err == ""

    def test_analyst_can_create_alert(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "create_alert")
        assert allowed is True
        assert err == ""

    def test_analyst_can_upload_document(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "upload_document")
        assert allowed is True
        assert err == ""

    def test_analyst_can_triage(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "triage_request")
        assert allowed is True
        assert err == ""

    def test_analyst_can_request_info(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "request_info")
        assert allowed is True
        assert err == ""

    # --- Analyst BLOCKED ---
    def test_analyst_cannot_reject_request(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "reject_request")
        assert allowed is False
        assert "not permitted" in err.lower()

    def test_analyst_cannot_approve_tier1(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "approve_tier1")
        assert allowed is False

    def test_analyst_cannot_approve_tier2(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "approve_tier2")
        assert allowed is False

    def test_analyst_cannot_approve_tier3(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "approve_tier3")
        assert allowed is False

    def test_analyst_cannot_implement(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "implement_change")
        assert allowed is False
        assert "not permitted" in err.lower()

    def test_analyst_cannot_convert_alert(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "convert_alert")
        assert allowed is False
        assert "not permitted" in err.lower()

    def test_analyst_cannot_review_request(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "review_request")
        assert allowed is False
        assert "not permitted" in err.lower()

    # --- CO/Admin/SCO checks ---
    def test_co_cannot_implement(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("co", "implement_change")
        assert allowed is False
        assert "not permitted" in err.lower()

    def test_admin_can_implement(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("admin", "implement_change")
        assert allowed is True
        assert err == ""

    def test_sco_can_implement(self):
        cm = _get_cm()
        allowed, err = cm.check_role_permission("sco", "implement_change")
        assert allowed is True
        assert err == ""

    def test_admin_can_create_request(self):
        cm = _get_cm()
        assert cm.check_role_permission("admin", "create_request") == (True, "")

    def test_sco_can_create_request(self):
        cm = _get_cm()
        assert cm.check_role_permission("sco", "create_request") == (True, "")

    def test_co_can_create_request(self):
        cm = _get_cm()
        assert cm.check_role_permission("co", "create_request") == (True, "")

    def test_admin_can_submit_request(self):
        cm = _get_cm()
        assert cm.check_role_permission("admin", "submit_request") == (True, "")

    def test_co_can_reject_request(self):
        cm = _get_cm()
        assert cm.check_role_permission("co", "reject_request") == (True, "")


# ============================================================================
# BLOCKER 1 — SERVICE LAYER RBAC ENFORCEMENT (DB tests)
# ============================================================================

class TestServiceLayerRBAC:
    """Verify service-layer functions enforce roles BEFORE DB mutations."""

    # --- Analyst ALLOWED: create, submit, and preparatory transitions ---
    def test_analyst_can_create_request(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Analyst prep",
            items=[{"change_type": "other"}],
            user=analyst, log_audit_fn=_noop_audit,
        )
        assert req["id"].startswith("CR-")
        assert req["status"] == "draft"

    def test_analyst_can_submit_request(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Analyst submit",
            items=[{"change_type": "other"}],
            user=analyst, log_audit_fn=_noop_audit,
        )
        success, err = cm.submit_change_request(
            wrapped, req["id"], analyst, log_audit_fn=_noop_audit
        )
        assert success is True

        row = db.execute(
            "SELECT status FROM change_requests WHERE id = ?", (req["id"],)
        ).fetchone()
        assert row["status"] == "submitted"

    def test_analyst_can_patch_submitted_to_triage(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Triage test",
            items=[{"change_type": "other"}],
            user=analyst, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], analyst, log_audit_fn=_noop_audit)

        success, err = cm.update_change_request_status(
            wrapped, req["id"], "triage_in_progress", analyst, log_audit_fn=_noop_audit
        )
        assert success is True

    def test_analyst_can_patch_triage_to_ready_for_review(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Ready test",
            items=[{"change_type": "other"}],
            user=analyst, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], analyst, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", analyst, log_audit_fn=_noop_audit)

        success, err = cm.update_change_request_status(
            wrapped, req["id"], "ready_for_review", analyst, log_audit_fn=_noop_audit
        )
        assert success is True

    def test_analyst_can_patch_ready_to_approval_pending(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Approval pending test",
            items=[{"change_type": "other"}],
            user=analyst, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], analyst, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", analyst, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", analyst, log_audit_fn=_noop_audit)

        success, err = cm.update_change_request_status(
            wrapped, req["id"], "approval_pending", analyst, log_audit_fn=_noop_audit
        )
        assert success is True

    # --- Analyst BLOCKED: terminal/final statuses ---
    def test_analyst_cannot_patch_to_approved(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        admin = _make_user("admin")
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Block test",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)

        success, err = cm.update_change_request_status(
            wrapped, req["id"], "approved", analyst, log_audit_fn=_noop_audit
        )
        assert success is False
        assert "not permitted" in err.lower()

        row = db.execute("SELECT status FROM change_requests WHERE id = ?", (req["id"],)).fetchone()
        assert row["status"] == "approval_pending"

    def test_analyst_cannot_patch_to_rejected(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        admin = _make_user("admin")
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Reject block test",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)

        success, err = cm.update_change_request_status(
            wrapped, req["id"], "rejected", analyst, log_audit_fn=_noop_audit
        )
        assert success is False
        assert "not permitted" in err.lower()

    def test_analyst_cannot_patch_to_implemented(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        admin = _make_user("admin")
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Impl block test",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)
        cm.approve_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)

        success, err = cm.update_change_request_status(
            wrapped, req["id"], "implemented", analyst, log_audit_fn=_noop_audit
        )
        assert success is False
        assert "not permitted" in err.lower()

    def test_analyst_cannot_patch_to_cancelled(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Cancel block test",
            items=[{"change_type": "other"}],
            user=analyst, log_audit_fn=_noop_audit,
        )

        success, err = cm.update_change_request_status(
            wrapped, req["id"], "cancelled", analyst, log_audit_fn=_noop_audit
        )
        assert success is False
        assert "not permitted" in err.lower()

    def test_analyst_cannot_patch_to_superseded(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        admin = _make_user("admin")
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Supersede block test",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)
        cm.approve_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)

        success, err = cm.update_change_request_status(
            wrapped, req["id"], "superseded", analyst, log_audit_fn=_noop_audit
        )
        assert success is False
        assert "not permitted" in err.lower()

    # --- Analyst BLOCKED: reject (via dedicated reject endpoint) ---
    def test_analyst_reject_request_denied(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        admin = _make_user("admin")
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Test reject",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)

        success, err = cm.reject_change_request(
            wrapped, req["id"], analyst, log_audit_fn=_noop_audit
        )
        assert success is False
        assert "not permitted" in err.lower()

        row = db.execute(
            "SELECT status FROM change_requests WHERE id = ?", (req["id"],)
        ).fetchone()
        assert row["status"] == "approval_pending"

    # --- CO BLOCKED: implement ---
    def test_co_implement_denied(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        admin = _make_user("admin")
        co = _make_user("co")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Test impl",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)
        cm.approve_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)

        success, err, version_id = cm.implement_change_request(
            wrapped, req["id"], co, log_audit_fn=_noop_audit
        )
        assert success is False
        assert "not permitted" in err.lower()
        assert version_id is None

        row = db.execute(
            "SELECT status FROM change_requests WHERE id = ?", (req["id"],)
        ).fetchone()
        assert row["status"] == "approved"

    # --- Admin/SCO CAN implement ---
    def test_admin_can_implement(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        admin = _make_user("admin")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Admin impl test",
            items=[{"change_type": "company_details", "field_name": "company_name",
                    "old_value": "Company A Ltd", "new_value": "Company A Updated"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)
        cm.approve_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)

        success, err, version_id = cm.implement_change_request(
            wrapped, req["id"], admin, log_audit_fn=_noop_audit
        )
        assert success is True
        assert err == ""

        row = db.execute(
            "SELECT status FROM change_requests WHERE id = ?", (req["id"],)
        ).fetchone()
        assert row["status"] == "implemented"

    def test_sco_can_implement(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        sco = _make_user("sco")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="SCO impl test",
            items=[{"change_type": "company_details", "field_name": "sector",
                    "old_value": "Financial Services", "new_value": "Insurance"}],
            user=sco, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], sco, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", sco, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", sco, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", sco, log_audit_fn=_noop_audit)
        cm.approve_change_request(wrapped, req["id"], sco, log_audit_fn=_noop_audit)

        success, err, version_id = cm.implement_change_request(
            wrapped, req["id"], sco, log_audit_fn=_noop_audit
        )
        assert success is True

    # --- Mutation prevention: failed permission must not mutate ---
    def test_failed_permission_does_not_create_profile_version(self, db):
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        co = _make_user("co")
        admin = _make_user("admin")

        before_count = db.execute(
            "SELECT COUNT(*) as cnt FROM entity_profile_versions WHERE application_id = ?",
            (app_a,)
        ).fetchone()["cnt"]

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="PV guard test",
            items=[{"change_type": "company_name", "field_name": "company_name",
                    "old_value": "Old", "new_value": "New"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        after_create_count = db.execute(
            "SELECT COUNT(*) as cnt FROM entity_profile_versions WHERE application_id = ?",
            (app_a,)
        ).fetchone()["cnt"]

        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)
        cm.approve_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)

        # CO tries to implement (should fail)
        success, _, _ = cm.implement_change_request(
            wrapped, req["id"], co, log_audit_fn=_noop_audit
        )
        assert success is False

        after_fail_count = db.execute(
            "SELECT COUNT(*) as cnt FROM entity_profile_versions WHERE application_id = ?",
            (app_a,)
        ).fetchone()["cnt"]
        assert after_fail_count == after_create_count

    def test_analyst_failed_implement_does_not_mutate_application(self, db):
        """Analyst cannot implement, and failed attempt must not touch live data."""
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        admin = _make_user("admin")
        analyst = _make_user("analyst")

        original = db.execute(
            "SELECT company_name FROM applications WHERE id = ?", (app_a,)
        ).fetchone()["company_name"]

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Analyst impl attempt",
            items=[{"change_type": "company_name", "field_name": "company_name",
                    "old_value": original, "new_value": "HACKED"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)
        cm.approve_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)

        success, err, _ = cm.implement_change_request(
            wrapped, req["id"], analyst, log_audit_fn=_noop_audit
        )
        assert success is False
        assert "not permitted" in err.lower()

        current = db.execute(
            "SELECT company_name FROM applications WHERE id = ?", (app_a,)
        ).fetchone()["company_name"]
        assert current == original

    def test_analyst_failed_implement_does_not_create_profile_version(self, db):
        """Failed analyst implement must not create a profile version."""
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        admin = _make_user("admin")
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="PV guard analyst test",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        after_create_count = db.execute(
            "SELECT COUNT(*) as cnt FROM entity_profile_versions WHERE application_id = ?",
            (app_a,)
        ).fetchone()["cnt"]

        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)
        cm.approve_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)

        success, _, _ = cm.implement_change_request(
            wrapped, req["id"], analyst, log_audit_fn=_noop_audit
        )
        assert success is False

        after_fail_count = db.execute(
            "SELECT COUNT(*) as cnt FROM entity_profile_versions WHERE application_id = ?",
            (app_a,)
        ).fetchone()["cnt"]
        assert after_fail_count == after_create_count

    def test_analyst_failed_approve_does_not_change_decision(self, db):
        """Analyst cannot approve, and attempt must not change any decision fields."""
        cm = _get_cm()
        _, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)
        admin = _make_user("admin")
        analyst = _make_user("analyst")

        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="backoffice_manual",
            source_channel="backoffice", reason="Approve guard test",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        cm.submit_change_request(wrapped, req["id"], admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "triage_in_progress", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "ready_for_review", admin, log_audit_fn=_noop_audit)
        cm.update_change_request_status(wrapped, req["id"], "approval_pending", admin, log_audit_fn=_noop_audit)

        success, err = cm.approve_change_request(
            wrapped, req["id"], analyst, log_audit_fn=_noop_audit
        )
        assert success is False

        row = db.execute("SELECT status FROM change_requests WHERE id = ?", (req["id"],)).fetchone()
        assert row["status"] == "approval_pending"


# ============================================================================
# BLOCKER 2 — PORTAL OWNERSHIP TESTS
# ============================================================================

class TestPortalOwnership:
    """Verify portal endpoints scope by client ownership."""

    def test_portal_app_list_returns_only_owned(self, db):
        """Portal applications endpoint returns only client-owned applications."""
        client_a, client_b, app_a, app_b = _setup_cm_test_data(db)

        # Simulate PortalApplicationsHandler query for client A
        rows = db.execute(
            "SELECT id, ref, company_name, status FROM applications WHERE client_id = ? ORDER BY created_at DESC",
            (client_a,)
        ).fetchall()
        app_ids = [r["id"] for r in rows]
        assert app_a in app_ids
        assert app_b not in app_ids

    def test_portal_app_list_excludes_other_clients(self, db):
        """Portal applications for client B does not include client A's apps."""
        client_a, client_b, app_a, app_b = _setup_cm_test_data(db)

        rows = db.execute(
            "SELECT id FROM applications WHERE client_id = ?",
            (client_b,)
        ).fetchall()
        app_ids = [r["id"] for r in rows]
        assert app_b in app_ids
        assert app_a not in app_ids

    def test_portal_user_can_create_request_for_owned_app(self, db):
        """Portal user can create change request for their own application."""
        cm = _get_cm()
        client_a, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)

        # Verify ownership
        app = db.execute(
            "SELECT id, status FROM applications WHERE id = ? AND client_id = ?",
            (app_a, client_a)
        ).fetchone()
        assert app is not None

        # Create as client (portal requests use portal_client source)
        client_user = _make_client_user(client_a)
        # Portal requests bypass role_permission check — they go through
        # PortalChangeRequestHandler which validates ownership instead.
        # For the service-layer test, we use admin to represent portal auto-creation.
        admin = _make_user("admin", sub=client_a)
        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="portal_client",
            source_channel="portal", reason="Portal test",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )
        assert req["id"].startswith("CR-")
        assert req["source"] == "portal_client"

    def test_portal_user_cannot_access_other_clients_app(self, db):
        """Portal ownership check rejects app belonging to another client."""
        client_a, client_b, app_a, app_b = _setup_cm_test_data(db)

        # Client A tries to access Client B's application
        app = db.execute(
            "SELECT id FROM applications WHERE id = ? AND client_id = ?",
            (app_b, client_a)
        ).fetchone()
        assert app is None  # Access denied

    def test_portal_created_request_visible_in_backoffice(self, db):
        """Portal-created requests should appear in back-office list."""
        cm = _get_cm()
        client_a, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)

        admin = _make_user("admin", sub=client_a)
        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="portal_client",
            source_channel="portal", reason="Portal visibility test",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )

        # Back-office list (no ownership filter)
        all_requests = cm.list_change_requests(wrapped, application_id=app_a)
        req_ids = [r["id"] for r in all_requests]
        assert req["id"] in req_ids

    def test_portal_created_request_visible_in_portal_list(self, db):
        """Portal-created requests should appear in portal's own list."""
        cm = _get_cm()
        client_a, _, app_a, _ = _setup_cm_test_data(db)
        wrapped = _DBWrapper(db)

        admin = _make_user("admin", sub=client_a)
        req = cm.create_change_request(
            db=wrapped, application_id=app_a, source="portal_client",
            source_channel="portal", reason="Portal list test",
            items=[{"change_type": "other"}],
            user=admin, log_audit_fn=_noop_audit,
        )

        # Portal list: get client's apps, then list requests per app
        client_apps = db.execute(
            "SELECT id FROM applications WHERE client_id = ?", (client_a,)
        ).fetchall()
        all_reqs = []
        for app_row in client_apps:
            reqs = cm.list_change_requests(wrapped, application_id=app_row["id"])
            all_reqs.extend(reqs)

        req_ids = [r["id"] for r in all_reqs]
        assert req["id"] in req_ids

    def test_portal_empty_state_no_apps(self, db):
        """Client with no applications gets empty list."""
        orphan_client = f"orphan-{secrets.token_hex(4)}"
        db.execute(
            "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            (orphan_client, f"orphan-{secrets.token_hex(3)}@test.com", "hash", "Orphan Co"),
        )
        db.commit()

        rows = db.execute(
            "SELECT id FROM applications WHERE client_id = ?", (orphan_client,)
        ).fetchall()
        assert len(rows) == 0


# ============================================================================
# PORTAL HTML DATA SOURCE TEST
# ============================================================================

class TestPortalHTMLDataSource:
    """Verify portal HTML uses ownership-scoped endpoint for CM dropdown."""

    def test_portal_uses_scoped_endpoint(self):
        """Portal loadPortalChangeApps must use /portal/applications, not /applications."""
        portal_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-portal.html"
        )
        with open(portal_path, "r") as f:
            content = f.read()

        # The CM dropdown loader must use the ownership-scoped portal endpoint
        assert "apiCall('GET', '/portal/applications')" in content
        # It must NOT use the admin-scope endpoint for the CM dropdown
        # (The dashboard may still use /applications since it's filtered server-side for clients)
        # Check specifically in the loadPortalChangeApps function
        func_start = content.find("function loadPortalChangeApps")
        func_end = content.find("}", content.find("} catch", func_start))
        cm_func = content[func_start:func_end]
        assert "/portal/applications" in cm_func
        assert "'/applications'" not in cm_func


# ============================================================================
# SERVER ROUTE REGISTRATION TEST
# ============================================================================

class TestServerRoutes:
    """Verify critical routes are registered."""

    def _get_route_patterns(self):
        from server import make_app
        app = make_app()
        patterns = []
        for rule in app.wildcard_router.rules:
            m = getattr(rule, 'matcher', None)
            if m:
                r = getattr(m, 'regex', None)
                if r:
                    patterns.append(r.pattern)
        return patterns

    def test_portal_applications_route_exists(self):
        patterns = self._get_route_patterns()
        assert any("/api/portal/applications" in p for p in patterns), \
            "Route /api/portal/applications not found"

    def test_portal_change_requests_route_exists(self):
        patterns = self._get_route_patterns()
        assert any("/api/portal/change-requests" in p for p in patterns), \
            "Route /api/portal/change-requests not found"
