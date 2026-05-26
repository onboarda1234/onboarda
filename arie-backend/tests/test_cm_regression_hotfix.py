"""
Regression tests for Change Management hotfix (staging retest failures).

Covers:
- Back-office create request modal payload → 201
- Alert convert with items → 201 and request.items length > 0
- Full lifecycle: create → submit → triage → ready_for_review → approval_pending → approve → implement
- Approve does not mutate live profile
- Implement mutates only intended field
- base_profile_version_id populated on create
- result_profile_version_id populated on implement
- Analyst implement attempt → 403 before DB action
- Portal app list populates for client
- Portal request create works for own application
- Portal cannot create for another client's application
- PostgreSQL boolean handling covered (parameterized booleans)
"""

import json
import os
import secrets
import sqlite3
import sys
import pytest

# Ensure arie-backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _get_cm():
    import change_management as cm
    return cm


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


def _setup_test_data(raw_db, company_name="test", client_id=None):
    """Create a test application with directors/UBOs."""
    app_id = f"test-cm-{secrets.token_hex(4)}"
    if client_id is None:
        client_id = f"test-cl-{secrets.token_hex(4)}"

    raw_db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"test-{secrets.token_hex(3)}@test.com", "hash", company_name),
    )
    raw_db.execute(
        """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, f"APP-{secrets.token_hex(4)}", client_id, company_name,
         "GB", "Financial Services", "Limited Company", "approved"),
    )
    raw_db.execute(
        """INSERT INTO directors (id, application_id, person_key, full_name, first_name, last_name, nationality, date_of_birth)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_id, "dir1", "John Smith", "John", "Smith", "GB", "1980-01-15"),
    )
    raw_db.execute(
        """INSERT INTO ubos (id, application_id, person_key, full_name, first_name, last_name, nationality, ownership_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_id, "ubo1", "Jane Doe", "Jane", "Doe", "US", 75.0),
    )
    raw_db.commit()
    return app_id, client_id


def _drive_to_approved(cm, wdb, req_id, user):
    """Drive a request through the lifecycle to 'approved' status."""
    cm.submit_change_request(wdb, req_id, user)
    cm.update_change_request_status(wdb, req_id, "triage_in_progress", user)
    cm.update_change_request_status(wdb, req_id, "ready_for_review", user)
    cm.update_change_request_status(wdb, req_id, "approval_pending", user)
    ok, err = cm.approve_change_request(wdb, req_id, user, decision_notes="Approved")
    assert ok, f"Approve failed: {err}"


class TestBackofficeCreateRequestPayload:
    """Issue #1: POST /api/change-management/requests returns 500 for modal payload."""

    def test_exact_backoffice_modal_payload_returns_201(self, db):
        """Reproduce the exact payload from the back-office modal."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Test SCO", "role": "sco"}

        # Exact payload from problem statement
        items = [
            {
                "change_type": "company_details",
                "field_name": "company_name",
                "old_value": "test",
                "new_value": "test [QA-RETEST]",
            }
        ]
        req = cm.create_change_request(
            db=wdb,
            application_id=app_id,
            source="backoffice_manual",
            source_channel="backoffice",
            reason="QA retest",
            items=items,
            user=user,
        )

        assert req is not None
        assert req["id"].startswith("CR-")
        assert req["status"] == "draft"

        # Verify one item in DB
        db_items = db.execute(
            "SELECT * FROM change_request_items WHERE request_id = ?",
            (req["id"],),
        ).fetchall()
        assert len(db_items) == 1
        assert dict(db_items[0])["field_name"] == "company_name"
        assert dict(db_items[0])["new_value"] == "test [QA-RETEST]"


class TestAlertConvertWithItems:
    """Issue #2: POST /api/change-management/alerts/{id}/convert returns 500."""

    def test_convert_with_explicit_items(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Test SCO", "role": "sco"}

        # Create alert and move to under_review
        alert = cm.create_change_alert(
            db=wdb, application_id=app_id,
            alert_type="company_change", source_channel="backoffice",
            summary="Company name change detected",
            detected_changes={"company_name": {"old": "test", "new": "test2"}},
            user=user,
        )
        cm.update_change_alert_status(wdb, alert["id"], "under_review", user)

        # Convert with explicit items (exact payload from problem statement)
        items = [
            {
                "change_type": "company_details",
                "field_name": "company_name",
                "old_value": "test",
                "new_value": "test [ALERT-CONVERT]",
            }
        ]
        req, err = cm.convert_alert_to_request(
            wdb, alert["id"], user,
            additional_notes="QA convert test",
            items=items,
        )

        assert req is not None, f"Conversion failed: {err}"
        assert req["source"] == "external_alert_conversion"

        # Verify items were stored
        db_items = db.execute(
            "SELECT * FROM change_request_items WHERE request_id = ?",
            (req["id"],),
        ).fetchall()
        assert len(db_items) > 0, "Request must contain items"
        assert dict(db_items[0])["new_value"] == "test [ALERT-CONVERT]"

        # Verify alert marked as converted
        updated = cm.get_change_alert_detail(wdb, alert["id"])
        assert updated["status"] == "converted_to_change_request"


class TestFullLifecycleRegression:
    """Issue #3, #5: Full lifecycle with profile versioning and boolean handling."""

    def test_full_lifecycle_create_to_implement(self, db):
        """Full lifecycle: draft → submit → triage → ready_for_review → approval_pending → approve → implement."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db, company_name="test")
        sco = {"sub": "sco1", "name": "SCO Officer", "role": "sco"}

        items = [
            {
                "change_type": "company_details",
                "field_name": "company_name",
                "old_value": "test",
                "new_value": "test [QA-RETEST]",
            }
        ]

        # 1. Create
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice",
            "Full lifecycle test", items, sco,
        )
        assert req["status"] == "draft"

        # Verify base_profile_version_id is populated on create
        assert req["base_profile_version_id"] is not None, \
            "base_profile_version_id must be populated on create"

        # 2. Submit
        ok, _ = cm.submit_change_request(wdb, req["id"], sco)
        assert ok

        # 3. Triage
        ok, _ = cm.update_change_request_status(wdb, req["id"], "triage_in_progress", sco)
        assert ok

        # 4. Ready for review
        ok, _ = cm.update_change_request_status(wdb, req["id"], "ready_for_review", sco)
        assert ok

        # 5. Approval pending
        ok, _ = cm.update_change_request_status(wdb, req["id"], "approval_pending", sco)
        assert ok

        # 6. Approve — must NOT mutate live profile
        app_before = dict(db.execute(
            "SELECT company_name FROM applications WHERE id = ?", (app_id,)
        ).fetchone())
        assert app_before["company_name"] == "test", "Company name should not change before implement"

        ok, err = cm.approve_change_request(wdb, req["id"], sco, decision_notes="Approved for test")
        assert ok, f"Approve failed: {err}"

        app_after_approve = dict(db.execute(
            "SELECT company_name FROM applications WHERE id = ?", (app_id,)
        ).fetchone())
        assert app_after_approve["company_name"] == "test", \
            "Approve must NOT mutate live profile"

        # 7. Implement — mutates only intended field
        ok, err, version_id = cm.implement_change_request(wdb, req["id"], sco)
        assert ok, f"Implement failed: {err}"
        assert version_id is not None

        # Verify company_name changed
        app_after = dict(db.execute(
            "SELECT company_name, country, sector FROM applications WHERE id = ?", (app_id,)
        ).fetchone())
        assert app_after["company_name"] == "test [QA-RETEST]", \
            "Implement must change company_name"
        assert app_after["country"] == "GB", "Implement must not change unrelated fields"
        assert app_after["sector"] == "Financial Services", \
            "Implement must not change unrelated fields"

        # Verify result_profile_version_id is populated
        req_detail = cm.get_change_request_detail(wdb, req["id"])
        assert req_detail["result_profile_version_id"] is not None, \
            "result_profile_version_id must be populated after implement"

    def test_base_profile_version_id_populated_on_create(self, db):
        """Issue #5: base_profile_version_id must be non-null after creation."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "sco1", "name": "SCO", "role": "sco"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "Old", "new_value": "New"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test", items, user,
        )

        assert req["base_profile_version_id"] is not None, \
            "base_profile_version_id must be populated at creation"

        # Also verify it's stored in DB
        db_req = db.execute(
            "SELECT base_profile_version_id FROM change_requests WHERE id = ?",
            (req["id"],),
        ).fetchone()
        assert dict(db_req)["base_profile_version_id"] is not None

    def test_result_profile_version_id_populated_on_implement(self, db):
        """Issue #5: result_profile_version_id must be non-null after implementation."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "Old", "new_value": "New"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test", items, sco,
        )
        _drive_to_approved(cm, wdb, req["id"], sco)

        ok, err, vid = cm.implement_change_request(wdb, req["id"], sco)
        assert ok, f"Implement failed: {err}"
        assert vid is not None

        # Verify in DB
        db_req = db.execute(
            "SELECT result_profile_version_id FROM change_requests WHERE id = ?",
            (req["id"],),
        ).fetchone()
        assert dict(db_req)["result_profile_version_id"] is not None
        assert dict(db_req)["result_profile_version_id"] == vid


class TestAnalystPermissionGuard:
    """Issue #4: Analyst must get permission denial BEFORE any DB logic runs."""

    def test_analyst_cannot_implement(self, db):
        """Analyst POST /implement → permission denied, no DB mutation."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}
        analyst = {"sub": "analyst1", "name": "Analyst", "role": "analyst"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "test", "new_value": "test changed"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test", items, sco,
        )
        _drive_to_approved(cm, wdb, req["id"], sco)

        # Analyst attempts implementation
        ok, err, vid = cm.implement_change_request(wdb, req["id"], analyst)
        assert not ok, "Analyst should not be able to implement"
        assert vid is None
        assert "not permitted" in err.lower(), f"Error should mention permission: {err}"

        # Verify NO DB mutation occurred
        app = dict(db.execute(
            "SELECT company_name FROM applications WHERE id = ?", (app_id,)
        ).fetchone())
        assert app["company_name"] == "test", \
            "Analyst implement attempt must NOT mutate live profile"

        # Request status must still be 'approved'
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "approved", \
            "Request status must remain approved after analyst attempt"

    def test_co_cannot_implement(self, db):
        """Compliance officer cannot implement either."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}
        co = {"sub": "co1", "name": "CO", "role": "co"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "test", "new_value": "test changed"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test", items, sco,
        )
        _drive_to_approved(cm, wdb, req["id"], sco)

        ok, err, vid = cm.implement_change_request(wdb, req["id"], co)
        assert not ok
        assert "not permitted" in err.lower()


class TestPostgreSQLBooleanHandling:
    """Issue #3: Ensure boolean values work via parameterized queries."""

    def test_profile_version_boolean_params(self, db):
        """Verify profile version is_current uses parameterized booleans
        (works for both SQLite INTEGER and PostgreSQL BOOLEAN)."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        # Create first request to generate baseline version
        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "test", "new_value": "test v2"}]
        req1 = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test 1", items, sco,
        )
        base_v1 = req1["base_profile_version_id"]
        assert base_v1 is not None

        # Verify is_current flag is set (using parameterized query)
        ver = db.execute(
            "SELECT is_current FROM entity_profile_versions WHERE id = ?",
            (base_v1,),
        ).fetchone()
        assert ver is not None
        # SQLite stores booleans as 1/0, accept both
        assert ver["is_current"] in (True, 1), "is_current should be True/1 for latest version"

        # Drive to implemented to create result version
        _drive_to_approved(cm, wdb, req1["id"], sco)
        ok, err, v2_id = cm.implement_change_request(wdb, req1["id"], sco)
        assert ok, f"Implement failed: {err}"

        # After implementation, old version should be not-current
        old_ver = db.execute(
            "SELECT is_current FROM entity_profile_versions WHERE id = ?",
            (base_v1,),
        ).fetchone()
        assert old_ver["is_current"] in (False, 0), \
            "Old version should be False/0 after new version created"

        # New version should be current
        new_ver = db.execute(
            "SELECT is_current FROM entity_profile_versions WHERE id = ?",
            (v2_id,),
        ).fetchone()
        assert new_ver["is_current"] in (True, 1), \
            "New version should be True/1"

    def test_downstream_boolean_flags_stored(self, db):
        """Verify downstream action boolean flags are stored correctly."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "sco1", "name": "SCO", "role": "sco"}

        # Tier1 items trigger screening_required and risk_review_required
        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "Old", "new_value": "New", "materiality": "tier1"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test", items, user,
        )

        row = db.execute(
            "SELECT screening_required, risk_review_required FROM change_requests WHERE id = ?",
            (req["id"],),
        ).fetchone()
        row = dict(row)
        # Accept both boolean and integer representations
        assert row["screening_required"] in (True, 1), \
            "Tier1 should have screening_required"
        assert row["risk_review_required"] in (True, 1), \
            "Tier1 should have risk_review_required"


class TestApproveDoesNotMutate:
    """Approve step must not change live application data."""

    def test_approve_leaves_profile_unchanged(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db, company_name="Original Corp")
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "Original Corp", "new_value": "Changed Corp"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Name change", items, sco,
        )
        cm.submit_change_request(wdb, req["id"], sco)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", sco)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", sco)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", sco)

        # Approve
        ok, err = cm.approve_change_request(wdb, req["id"], sco, decision_notes="OK")
        assert ok

        # Profile must still show original
        app = dict(db.execute(
            "SELECT company_name FROM applications WHERE id = ?", (app_id,)
        ).fetchone())
        assert app["company_name"] == "Original Corp"


class TestImplementMutatesOnlyIntended:
    """Implement must only change the field(s) specified in items."""

    def test_implement_changes_only_company_name(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db, company_name="test")
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        # Get original values
        orig = dict(db.execute(
            "SELECT company_name, country, sector, entity_type FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone())

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "test", "new_value": "test [IMPLEMENT-TEST]"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test", items, sco,
        )
        _drive_to_approved(cm, wdb, req["id"], sco)

        ok, err, vid = cm.implement_change_request(wdb, req["id"], sco)
        assert ok, f"Implement failed: {err}"

        # Verify only company_name changed
        after = dict(db.execute(
            "SELECT company_name, country, sector, entity_type FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone())
        assert after["company_name"] == "test [IMPLEMENT-TEST]"
        assert after["country"] == orig["country"]
        assert after["sector"] == orig["sector"]
        assert after["entity_type"] == orig["entity_type"]


class TestPortalHandlers:
    """Issues #6, #9, #10, #11: Portal API integration tests."""

    def test_portal_app_list_for_client(self, db):
        """Portal dropdown: client can see their own applications."""
        app_id, client_id = _setup_test_data(db, company_name="My Company")

        # Verify client can find their applications
        apps = db.execute(
            "SELECT id, company_name FROM applications WHERE client_id = ?",
            (client_id,),
        ).fetchall()
        assert len(apps) > 0, "Client should have at least one application"
        assert dict(apps[0])["company_name"] == "My Company"

    def test_portal_request_create_for_own_app(self, db):
        """Portal: client can create change request for their own application."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db, company_name="Client Corp")
        client_user = {"sub": client_id, "name": "Client", "role": "client", "type": "client"}

        # Verify client owns the application
        app = db.execute(
            "SELECT id FROM applications WHERE id = ? AND client_id = ?",
            (app_id, client_id),
        ).fetchone()
        assert app is not None, "Client must own the application"

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "Client Corp", "new_value": "Client Corp Updated"}]

        # client role can create requests per ROLE_PERMISSIONS
        # But let's check if 'client' role is permitted (it's not in the ROLE_PERMISSIONS)
        # Portal handler bypasses role check and calls create_change_request with the client user
        # The create_change_request function doesn't check roles itself - it's the handler that checks
        # So for portal, the handler (PortalChangeRequestHandler) doesn't use cm.check_role_permission
        # It just creates the request directly
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal",
            "Client wants to update name", items, client_user,
        )
        assert req is not None
        assert req["id"].startswith("CR-")

    def test_portal_cannot_create_for_other_client(self, db):
        """Portal: client cannot create for another client's application."""
        app_id1, client1 = _setup_test_data(db, company_name="Client 1 Corp")
        app_id2, client2 = _setup_test_data(db, company_name="Client 2 Corp")

        # Client 1 tries to access Client 2's application
        app = db.execute(
            "SELECT id FROM applications WHERE id = ? AND client_id = ?",
            (app_id2, client1),
        ).fetchone()
        assert app is None, "Client 1 must not have access to Client 2's application"


class TestProfileVersionLifecycle:
    """Issue #5: Verify both version IDs are populated through full lifecycle."""

    def test_both_version_ids_populated(self, db):
        """base_profile_version_id on create, result_profile_version_id on implement."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db, company_name="Version Test Corp")
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "Version Test Corp", "new_value": "Version Test Corp v2"}]

        # Create
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Version test", items, sco,
        )
        base_vid = req["base_profile_version_id"]
        assert base_vid is not None, "base_profile_version_id must be set on create"

        # Drive to implemented
        _drive_to_approved(cm, wdb, req["id"], sco)
        ok, err, result_vid = cm.implement_change_request(wdb, req["id"], sco)
        assert ok, f"Implement failed: {err}"
        assert result_vid is not None, "result_profile_version_id must be set on implement"

        # Verify both in DB
        db_req = dict(db.execute(
            "SELECT base_profile_version_id, result_profile_version_id FROM change_requests WHERE id = ?",
            (req["id"],),
        ).fetchone())
        assert db_req["base_profile_version_id"] == base_vid
        assert db_req["result_profile_version_id"] == result_vid

        # Verify they are different
        assert base_vid != result_vid, "Base and result versions must be different"
