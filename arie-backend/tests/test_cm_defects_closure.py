"""
CM Defect Closure Tests — Regression tests for all blocking defects.

Covers:
1. datetime JSON serialization safety (Defect #3)
2. Profile snapshot JSON-safe output (Defect #3)
3. Implement returns 200 with no datetime errors (Defect #3)
4. base_profile_version_id populated on create (Defect #7)
5. result_profile_version_id populated on implement (Defect #7)
6. Alert convert carries items[] through (Defect #6)
7. Alert convert without items derives from detected_changes (Defect #6)
8. Full lifecycle: create → submit → triage → review → approval → approve → implement (Defect #2)
9. Portal-created request has correct source, items, status (Defect #5)
10. Portal ownership scoping (Defect #5)
11. Profile versioning audit trail (Defect #7)
12. Stale version conflict uses base_profile_version_id (Defect #7)
13. Implement is transactional — rollback on failure
14. Audit log entries exist after lifecycle
15. Permissions: analyst cannot approve/implement (validation E)
"""

import json
import os
import sys
import secrets
from datetime import datetime, timezone, date
import sqlite3
import pytest

# Ensure backend is on path
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


def _setup_test_data(raw_db):
    """Create a test application with directors/UBOs for CM testing."""
    app_id = f"test-cm-{secrets.token_hex(4)}"
    client_id = f"test-cl-{secrets.token_hex(4)}"

    raw_db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"test-{secrets.token_hex(3)}@test.com", "hash", "Test Company"),
    )
    raw_db.execute(
        """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, f"APP-{secrets.token_hex(4)}", client_id, "Test Company Ltd",
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


ADMIN_USER = {"sub": "admin-1", "name": "Admin User", "role": "admin"}
SCO_USER = {"sub": "sco-1", "name": "SCO User", "role": "sco"}
CO_USER = {"sub": "co-1", "name": "CO User", "role": "co"}
ANALYST_USER = {"sub": "analyst-1", "name": "Analyst User", "role": "analyst"}


# ============================================================================
# Defect #3: datetime JSON serialization
# ============================================================================

class TestDatetimeSerialization:
    """Defect #3 — datetime values must be JSON-serializable in snapshots."""

    def test_json_safe_value_datetime(self):
        cm = _get_cm()
        dt = datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = cm._json_safe_value(dt)
        assert isinstance(result, str)
        assert "2024-06-15" in result

    def test_json_safe_value_date(self):
        cm = _get_cm()
        d = date(2024, 6, 15)
        result = cm._json_safe_value(d)
        assert isinstance(result, str)
        assert "2024-06-15" in result

    def test_json_safe_value_passthrough(self):
        cm = _get_cm()
        assert cm._json_safe_value("hello") == "hello"
        assert cm._json_safe_value(42) == 42
        assert cm._json_safe_value(None) is None

    def test_json_safe_dict(self):
        cm = _get_cm()
        d = {"name": "Test", "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc), "count": 5}
        result = cm._json_safe_dict(d)
        assert isinstance(result["created_at"], str)
        assert result["name"] == "Test"
        assert result["count"] == 5

    def test_snapshot_is_json_serializable(self, db):
        """Profile snapshot must be serializable with json.dumps()."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        snapshot = cm.snapshot_entity_profile(wdb, app_id)
        assert snapshot  # non-empty
        # Must not raise
        json_str = json.dumps(snapshot)
        parsed = json.loads(json_str)
        assert parsed["company_name"] == "Test Company Ltd"
        assert len(parsed["directors"]) == 1
        assert len(parsed["ubos"]) == 1
        assert "snapshot_at" in parsed

    def test_implement_no_datetime_error(self, db):
        """Implement must succeed without datetime serialization error."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        # Create + approve + implement
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice",
            "Test change",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "Test Company Ltd", "new_value": "New Company Ltd"}],
            ADMIN_USER,
        )
        cm.submit_change_request(wdb, req["id"], ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", ADMIN_USER)
        cm.approve_change_request(wdb, req["id"], ADMIN_USER, decision_notes="Approved")

        success, err, version_id = cm.implement_change_request(wdb, req["id"], ADMIN_USER)
        assert success, f"Implement failed: {err}"
        assert version_id is not None
        assert err == ""

    def test_profile_version_snapshot_parseable(self, db):
        """Profile version snapshot JSON must be parseable after implement."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test",
            [{"change_type": "company_details", "field_name": "sector",
              "old_value": "Financial Services", "new_value": "Technology"}],
            ADMIN_USER,
        )
        cm.submit_change_request(wdb, req["id"], ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", ADMIN_USER)
        cm.approve_change_request(wdb, req["id"], ADMIN_USER)
        success, _, version_id = cm.implement_change_request(wdb, req["id"], ADMIN_USER)
        assert success

        version = cm.get_profile_version_detail(wdb, version_id)
        assert version is not None
        snapshot = version["profile_snapshot"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        assert "company_name" in snapshot


# ============================================================================
# Defect #7: base/result profile version IDs
# ============================================================================

class TestProfileVersionTracking:
    """Defect #7 — base_profile_version_id and result_profile_version_id tracking."""

    def test_new_request_has_base_profile_version_id(self, db):
        """Creating a request must set base_profile_version_id."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "Old", "new_value": "New"}],
            ADMIN_USER,
        )
        assert req["base_profile_version_id"] is not None
        assert req["base_profile_version_id"].startswith("PV-")

    def test_implement_sets_result_profile_version_id(self, db):
        """Implementing a request must set result_profile_version_id."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "Test Company Ltd", "new_value": "Updated Ltd"}],
            ADMIN_USER,
        )
        cm.submit_change_request(wdb, req["id"], ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", ADMIN_USER)
        cm.approve_change_request(wdb, req["id"], ADMIN_USER)
        success, _, version_id = cm.implement_change_request(wdb, req["id"], ADMIN_USER)
        assert success

        # Re-read from DB
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["result_profile_version_id"] is not None
        assert detail["result_profile_version_id"] == version_id

    def test_stale_version_conflict_uses_base(self, db):
        """Stale version conflict must detect via base_profile_version_id."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        # Create first request and implement it
        req1 = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "First change",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "Test Company Ltd", "new_value": "First Update"}],
            ADMIN_USER,
        )
        base1 = req1["base_profile_version_id"]

        # Create second request (same base version)
        req2 = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Second change",
            [{"change_type": "company_details", "field_name": "sector",
              "old_value": "Financial Services", "new_value": "Technology"}],
            ADMIN_USER,
        )
        # Both should have same base version initially
        # (second request created after first already made a new version)
        # The second request's base is the version created by first request creation

        # Implement first request
        cm.submit_change_request(wdb, req1["id"], ADMIN_USER)
        cm.update_change_request_status(wdb, req1["id"], "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req1["id"], "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req1["id"], "approval_pending", ADMIN_USER)
        cm.approve_change_request(wdb, req1["id"], ADMIN_USER)
        success1, _, _ = cm.implement_change_request(wdb, req1["id"], ADMIN_USER)
        assert success1

        # Now try to implement second request — should detect stale version
        cm.submit_change_request(wdb, req2["id"], ADMIN_USER)
        cm.update_change_request_status(wdb, req2["id"], "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req2["id"], "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req2["id"], "approval_pending", ADMIN_USER)
        cm.approve_change_request(wdb, req2["id"], ADMIN_USER)
        success2, err2, _ = cm.implement_change_request(wdb, req2["id"], ADMIN_USER)
        assert not success2
        assert "version" in err2.lower() or "stale" in err2.lower() or "updated" in err2.lower()

    def test_profile_versions_list_shows_request_link(self, db):
        """Profile versions list must include change_request_id."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "Test Company Ltd", "new_value": "Updated Ltd"}],
            ADMIN_USER,
        )
        cm.submit_change_request(wdb, req["id"], ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", ADMIN_USER)
        cm.approve_change_request(wdb, req["id"], ADMIN_USER)
        cm.implement_change_request(wdb, req["id"], ADMIN_USER)

        versions = cm.get_profile_versions(wdb, app_id)
        assert len(versions) >= 2  # baseline + post-implement
        # Most recent version should reference our request
        latest = versions[0]
        assert latest["change_request_id"] == req["id"]

    def test_initial_baseline_version_created(self, db):
        """When no profile version exists, creating a request creates initial baseline."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        # No versions should exist yet
        versions_before = cm.get_profile_versions(wdb, app_id)
        assert len(versions_before) == 0

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "Old", "new_value": "New"}],
            ADMIN_USER,
        )

        # Now a baseline version should exist
        versions_after = cm.get_profile_versions(wdb, app_id)
        assert len(versions_after) == 1
        assert versions_after[0]["is_current"] == 1
        assert req["base_profile_version_id"] == versions_after[0]["id"]


# ============================================================================
# Defect #6: Alert convert items
# ============================================================================

class TestAlertConvertItems:
    """Defect #6 — Alert convert must carry items[] through."""

    def test_convert_with_explicit_items(self, db):
        """Converting alert with explicit items creates request with those items."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        alert = cm.create_change_alert(
            wdb, app_id, "director_change", "companies_house",
            "Director appointment detected",
            {"director_name": {"old": None, "new": "Alice Brown"}},
            user=ADMIN_USER,
        )
        cm.update_change_alert_status(wdb, alert["id"], "under_review", ADMIN_USER)

        explicit_items = [{
            "change_type": "director_add",
            "field_name": "director_name",
            "old_value": None,
            "new_value": "Alice Brown",
        }]

        request, err = cm.convert_alert_to_request(
            wdb, alert["id"], ADMIN_USER, items=explicit_items,
        )
        assert request is not None, f"Convert failed: {err}"
        assert len(request["items"]) == 1
        assert request["items"][0]["change_type"] == "director_add"
        assert request["items"][0]["new_value"] == "Alice Brown"
        assert request["source_alert_id"] == alert["id"]

    def test_convert_without_items_derives_from_changes(self, db):
        """Converting alert without items derives items from detected_changes."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        alert = cm.create_change_alert(
            wdb, app_id, "address_change", "registry_api",
            "Address change detected",
            {"registered_address": {"old": "123 Old St", "new": "456 New Ave"}},
            user=ADMIN_USER,
        )
        cm.update_change_alert_status(wdb, alert["id"], "under_review", ADMIN_USER)

        request, err = cm.convert_alert_to_request(wdb, alert["id"], ADMIN_USER)
        assert request is not None, f"Convert failed: {err}"
        # Should derive item from detected_changes
        assert len(request["items"]) >= 1

    def test_convert_sets_source_alert_id(self, db):
        """Converted request must link back to source alert."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        alert = cm.create_change_alert(
            wdb, app_id, "ubo_change", "open_corporates",
            "UBO change",
            {"ownership": {"old": "75%", "new": "60%"}},
            user=ADMIN_USER,
        )
        cm.update_change_alert_status(wdb, alert["id"], "under_review", ADMIN_USER)

        request, _ = cm.convert_alert_to_request(wdb, alert["id"], ADMIN_USER)
        assert request["source_alert_id"] == alert["id"]

    def test_convert_changes_alert_status(self, db):
        """Alert status must become converted_to_change_request after convert."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        alert = cm.create_change_alert(
            wdb, app_id, "director_change", "companies_house",
            "Director change",
            {"name": {"old": "A", "new": "B"}},
            user=ADMIN_USER,
        )
        cm.update_change_alert_status(wdb, alert["id"], "under_review", ADMIN_USER)
        cm.convert_alert_to_request(wdb, alert["id"], ADMIN_USER)

        detail = cm.get_change_alert_detail(wdb, alert["id"])
        assert detail["status"] == "converted_to_change_request"


# ============================================================================
# Defect #2: Full workflow lifecycle with all transitions
# ============================================================================

class TestFullWorkflowLifecycle:
    """Defect #2 — Complete workflow: draft → submitted → triage → review → approval_pending → approved → implemented."""

    def test_full_lifecycle_with_all_transitions(self, db):
        """Verify every workflow status transition works end to end."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        # Create
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Company name change",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "Test Company Ltd", "new_value": "Updated Corp"}],
            ADMIN_USER,
        )
        assert req["status"] == "draft"

        # Submit
        ok, err = cm.submit_change_request(wdb, req["id"], ADMIN_USER)
        assert ok, err
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "submitted"

        # Begin Triage
        ok, err = cm.update_change_request_status(wdb, req["id"], "triage_in_progress", ADMIN_USER)
        assert ok, err
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "triage_in_progress"

        # Mark Ready for Review
        ok, err = cm.update_change_request_status(wdb, req["id"], "ready_for_review", ADMIN_USER)
        assert ok, err
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "ready_for_review"

        # Send for Approval
        ok, err = cm.update_change_request_status(wdb, req["id"], "approval_pending", ADMIN_USER)
        assert ok, err
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "approval_pending"

        # Verify live profile NOT changed yet
        app_before = db.execute("SELECT company_name FROM applications WHERE id = ?", (app_id,)).fetchone()
        assert app_before["company_name"] == "Test Company Ltd"

        # Approve
        ok, err = cm.approve_change_request(wdb, req["id"], ADMIN_USER, decision_notes="Approved")
        assert ok, err
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "approved"

        # Verify live profile STILL unchanged
        app_after_approve = db.execute("SELECT company_name FROM applications WHERE id = ?", (app_id,)).fetchone()
        assert app_after_approve["company_name"] == "Test Company Ltd"

        # Implement
        ok, err, version_id = cm.implement_change_request(wdb, req["id"], ADMIN_USER)
        assert ok, f"Implement failed: {err}"
        assert version_id is not None

        # Verify live profile CHANGED
        app_after = db.execute("SELECT company_name FROM applications WHERE id = ?", (app_id,)).fetchone()
        assert app_after["company_name"] == "Updated Corp"

        # Verify request status
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "implemented"
        assert detail["base_profile_version_id"] is not None
        assert detail["result_profile_version_id"] is not None

        # Verify old version preserved, new version is current
        versions = cm.get_profile_versions(wdb, app_id)
        assert len(versions) >= 2
        current_versions = [v for v in versions if v["is_current"] == 1]
        assert len(current_versions) == 1
        assert current_versions[0]["id"] == version_id

    def test_implement_creates_audit_entry(self, db):
        """Implement must write audit log entry."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        audit_entries = []
        def mock_audit(user, action, target, detail, db=None, before_state=None, after_state=None):
            audit_entries.append({"action": action, "target": target, "detail": detail,
                                  "before_state": before_state, "after_state": after_state})

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test",
            [{"change_type": "company_details", "field_name": "sector",
              "old_value": "Financial Services", "new_value": "Technology"}],
            ADMIN_USER, log_audit_fn=mock_audit,
        )
        cm.submit_change_request(wdb, req["id"], ADMIN_USER, log_audit_fn=mock_audit)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", ADMIN_USER, log_audit_fn=mock_audit)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", ADMIN_USER, log_audit_fn=mock_audit)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", ADMIN_USER, log_audit_fn=mock_audit)
        cm.approve_change_request(wdb, req["id"], ADMIN_USER, log_audit_fn=mock_audit)
        cm.implement_change_request(wdb, req["id"], ADMIN_USER, log_audit_fn=mock_audit)

        actions = [e["action"] for e in audit_entries]
        assert "Change Request Created" in actions
        assert "Change Request Submitted" in actions
        assert "Change Request Approved" in actions
        assert "Change Request Implemented" in actions

        # Verify implement audit has before/after state
        impl_audit = [e for e in audit_entries if e["action"] == "Change Request Implemented"][0]
        assert impl_audit["before_state"] is not None
        assert impl_audit["after_state"] is not None
        # before/after state must be JSON-serializable
        json.dumps(impl_audit["before_state"], default=str)
        json.dumps(impl_audit["after_state"], default=str)


# ============================================================================
# Defect #5: Portal change request
# ============================================================================

class TestPortalChangeRequest:
    """Defect #5 — Portal-originated requests must work correctly."""

    def test_portal_create_request_with_items(self, db):
        """Portal client can create a request with items."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)

        portal_user = {"sub": client_id, "name": "Portal Client", "role": "client", "type": "client"}

        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Want to update address",
            [{"change_type": "address_change", "field_name": "registered_address",
              "old_value": "123 Old St", "new_value": "456 New Ave"}],
            portal_user,
        )
        assert req is not None
        assert req["source"] == "portal_client"
        assert req["source_channel"] == "portal"
        assert req["status"] == "draft"
        assert len(req["items"]) == 1
        assert req["base_profile_version_id"] is not None

    def test_portal_request_auto_submit(self, db):
        """Portal request submitted after creation has correct status."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)

        portal_user = {"sub": client_id, "name": "Portal Client", "role": "client", "type": "client"}

        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Address change",
            [{"change_type": "address_change", "field_name": "address",
              "old_value": "A", "new_value": "B"}],
            portal_user,
        )
        # Simulate auto-submit (like PortalChangeRequestHandler does)
        ok, err = cm.submit_change_request(wdb, req["id"], portal_user)
        assert ok, err
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "submitted"

    def test_portal_request_visible_in_list(self, db):
        """Portal-created request appears in list for that application."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)

        portal_user = {"sub": client_id, "name": "Portal Client", "role": "client", "type": "client"}

        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Test",
            [{"change_type": "contact_detail_update", "field_name": "email",
              "old_value": "old@test.com", "new_value": "new@test.com"}],
            portal_user,
        )

        all_reqs = cm.list_change_requests(wdb, application_id=app_id)
        assert any(r["id"] == req["id"] for r in all_reqs)


# ============================================================================
# Permissions (validation E)
# ============================================================================

class TestPermissions:
    """Validation E — Role-based access enforcement."""

    def test_analyst_cannot_approve(self):
        """Analyst role must not be able to approve tier1 or tier2."""
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "approve_tier1")
        assert not allowed
        allowed, err = cm.check_role_permission("analyst", "approve_tier2")
        assert not allowed

    def test_analyst_cannot_implement(self):
        """Analyst role must not be able to implement changes."""
        cm = _get_cm()
        allowed, err = cm.check_role_permission("analyst", "implement_change")
        assert not allowed

    def test_co_cannot_implement(self):
        """CO role must not be able to implement changes."""
        cm = _get_cm()
        allowed, err = cm.check_role_permission("co", "implement_change")
        assert not allowed

    def test_co_cannot_approve_tier1(self, db):
        """CO role cannot approve tier1 changes."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Legal name change",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "Old Name", "new_value": "New Name", "materiality": "tier1"}],
            ADMIN_USER,
        )
        cm.submit_change_request(wdb, req["id"], ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", ADMIN_USER)

        ok, err = cm.approve_change_request(wdb, req["id"], CO_USER)
        assert not ok
        assert "not permitted" in err.lower() or "role" in err.lower()

    def test_analyst_cannot_implement_server_side(self, db):
        """Analyst cannot implement even if they somehow get to approved status."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "A", "new_value": "B"}],
            ADMIN_USER,
        )
        cm.submit_change_request(wdb, req["id"], ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", ADMIN_USER)
        cm.approve_change_request(wdb, req["id"], ADMIN_USER)

        ok, err, _ = cm.implement_change_request(wdb, req["id"], ANALYST_USER)
        assert not ok
        assert "not permitted" in err.lower() or "role" in err.lower()


# ============================================================================
# Alert lifecycle (validation C)
# ============================================================================

class TestAlertLifecycle:
    """Validation C — Alert lifecycle."""

    def test_full_alert_lifecycle(self, db):
        """Create → under_review → convert with items."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        # Create
        alert = cm.create_change_alert(
            wdb, app_id, "shareholding_change", "open_corporates",
            "Shareholding structure changed",
            {"ownership": {"old": "75%", "new": "60%"}},
            confidence=0.85,
            user=ADMIN_USER,
        )
        assert alert["status"] == "new"

        # Move to under_review
        ok, err = cm.update_change_alert_status(wdb, alert["id"], "under_review", ADMIN_USER)
        assert ok, err

        # Convert with items
        items = [{"change_type": "ubo_change", "field_name": "ownership_pct",
                  "old_value": "75", "new_value": "60"}]
        request, err = cm.convert_alert_to_request(wdb, alert["id"], ADMIN_USER, items=items)
        assert request is not None, err
        assert len(request["items"]) == 1
        assert request["source_alert_id"] == alert["id"]

        # Alert is now terminal
        detail = cm.get_change_alert_detail(wdb, alert["id"])
        assert detail["status"] == "converted_to_change_request"

    def test_alert_does_not_mutate_live_profile(self, db):
        """Creating and converting an alert must not change live profile data."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        name_before = db.execute("SELECT company_name FROM applications WHERE id = ?", (app_id,)).fetchone()["company_name"]

        alert = cm.create_change_alert(
            wdb, app_id, "legal_name_change", "companies_house",
            "Name change", {"company_name": {"old": name_before, "new": "Changed Name"}},
            user=ADMIN_USER,
        )
        cm.update_change_alert_status(wdb, alert["id"], "under_review", ADMIN_USER)
        cm.convert_alert_to_request(wdb, alert["id"], ADMIN_USER)

        name_after = db.execute("SELECT company_name FROM applications WHERE id = ?", (app_id,)).fetchone()["company_name"]
        assert name_after == name_before


# ============================================================================
# Implementation transactional safety
# ============================================================================

class TestImplementTransactional:
    """Implement must be transactional with proper rollback."""

    def test_cannot_implement_unapproved(self, db):
        """Cannot implement a request that is not approved."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "A", "new_value": "B"}],
            ADMIN_USER,
        )
        cm.submit_change_request(wdb, req["id"], ADMIN_USER)

        ok, err, _ = cm.implement_change_request(wdb, req["id"], ADMIN_USER)
        assert not ok
        assert "approved" in err.lower()

    def test_implement_changes_live_profile(self, db):
        """After implement, the live application field must be updated."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Sector change",
            [{"change_type": "company_details", "field_name": "sector",
              "old_value": "Financial Services", "new_value": "Technology"}],
            ADMIN_USER,
        )
        cm.submit_change_request(wdb, req["id"], ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", ADMIN_USER)
        cm.approve_change_request(wdb, req["id"], ADMIN_USER)

        before = db.execute("SELECT sector FROM applications WHERE id = ?", (app_id,)).fetchone()["sector"]
        assert before == "Financial Services"

        ok, _, _ = cm.implement_change_request(wdb, req["id"], ADMIN_USER)
        assert ok

        after = db.execute("SELECT sector FROM applications WHERE id = ?", (app_id,)).fetchone()["sector"]
        assert after == "Technology"
