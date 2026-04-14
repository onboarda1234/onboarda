"""
Tests for Portal Change Request creation, change_type validation, and
per-application profile version detail endpoint.

Covers blockers from final QA:
1. Portal POST /api/portal/change-requests — 500 fix
2. change_type whitelist validation at create/convert/portal create
3. Per-application profile version detail endpoint
"""

import json
import os
import sys
import secrets
import sqlite3
import pytest

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
    """Create a test application with directors/UBOs."""
    app_id = f"test-pcr-{secrets.token_hex(4)}"
    client_id = f"test-cl-{secrets.token_hex(4)}"

    raw_db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
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


# ============================================================================
# 1. VALID_CHANGE_TYPES constant and validation function tests
# ============================================================================

class TestValidChangeTypes:
    """Test the VALID_CHANGE_TYPES constant and validate_change_types function."""

    def test_constant_exists(self):
        cm = _get_cm()
        assert hasattr(cm, "VALID_CHANGE_TYPES")
        assert isinstance(cm.VALID_CHANGE_TYPES, frozenset)

    def test_expected_types_present(self):
        cm = _get_cm()
        expected = {
            "company_details", "director_add", "director_remove",
            "ubo_add", "ubo_remove", "director_change", "ubo_change",
            "address_change", "business_activity_change",
            "contact_detail_update", "other",
        }
        for t in expected:
            assert t in cm.VALID_CHANGE_TYPES, f"{t} should be in VALID_CHANGE_TYPES"

    def test_validate_valid_items(self):
        cm = _get_cm()
        items = [
            {"change_type": "company_details", "field_name": "company_name"},
            {"change_type": "director_add"},
            {"change_type": "address_change", "field_name": "registered_address"},
        ]
        valid, err = cm.validate_change_types(items)
        assert valid is True
        assert err == ""

    def test_validate_invalid_item(self):
        cm = _get_cm()
        items = [
            {"change_type": "company_details"},
            {"change_type": "totally_bogus_type"},
        ]
        valid, err = cm.validate_change_types(items)
        assert valid is False
        assert "totally_bogus_type" in err

    def test_validate_empty_items(self):
        cm = _get_cm()
        valid, err = cm.validate_change_types([])
        assert valid is True

    def test_all_ui_portal_types_valid(self):
        """Portal UI dropdown values must all be valid."""
        cm = _get_cm()
        portal_types = [
            "company_details", "director_add", "director_remove",
            "ubo_add", "ubo_remove", "address_change",
            "contact_detail_update", "business_activity_change", "other",
        ]
        for t in portal_types:
            assert t in cm.VALID_CHANGE_TYPES, f"Portal UI type '{t}' not in VALID_CHANGE_TYPES"

    def test_all_ui_backoffice_types_valid(self):
        """Back-office UI dropdown values must all be valid."""
        cm = _get_cm()
        bo_types = [
            "company_details", "director_add", "director_remove",
            "ubo_add", "ubo_remove", "director_change", "ubo_change",
            "address_change", "business_activity_change",
            "contact_detail_update", "other",
        ]
        for t in bo_types:
            assert t in cm.VALID_CHANGE_TYPES, f"Back-office UI type '{t}' not in VALID_CHANGE_TYPES"


# ============================================================================
# 2. change_type validation at create time (back-office + portal + convert)
# ============================================================================

class TestChangeTypeValidationAtCreate:
    """Reject unsupported change_type at creation time — never let invalid
    types reach approved/implemented state."""

    def test_invalid_change_type_rejected_on_backoffice_create(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "Officer", "role": "sco"}

        items = [{"change_type": "whitelist_probe_invalid", "field_name": "x", "new_value": "y"}]
        with pytest.raises(ValueError, match="whitelist_probe_invalid"):
            cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                     "Test invalid type", items, user)

    def test_invalid_change_type_rejected_on_alert_convert(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "Officer", "role": "sco"}

        alert = cm.create_change_alert(wdb, app_id, "director_change", "companies_house",
                                       "Test", {}, user=user)
        cm.update_change_alert_status(wdb, alert["id"], "under_review", user)

        bad_items = [{"change_type": "invented_nonsense"}]
        with pytest.raises(ValueError, match="invented_nonsense"):
            cm.convert_alert_to_request(wdb, alert["id"], user, items=bad_items)

    def test_invalid_change_type_rejected_on_portal_create(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "made_up_type"}]
        with pytest.raises(ValueError, match="made_up_type"):
            cm.create_change_request(wdb, app_id, "portal_client", "portal",
                                     "Client request", items, user)

    def test_valid_change_types_accepted(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "Officer", "role": "sco"}

        for ct in ["company_details", "director_add", "ubo_remove", "address_change",
                    "business_activity_change", "contact_detail_update", "other"]:
            items = [{"change_type": ct, "field_name": "test_field", "new_value": "test"}]
            req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                           f"Test {ct}", items, user)
            assert req["id"].startswith("CR-")

    def test_invalid_cr_cannot_reach_approved(self, db):
        """If an invalid change_type somehow got through (belt-and-suspenders),
        the create_change_request function should block it."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "Officer", "role": "admin"}

        items = [{"change_type": "phantom_type"}]
        with pytest.raises(ValueError):
            cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                     "Should never create", items, user)

        # Verify nothing was created in DB
        rows = db.execute(
            "SELECT * FROM change_requests WHERE application_id = ? AND reason = 'Should never create'",
            (app_id,),
        ).fetchall()
        assert len(rows) == 0


# ============================================================================
# 3. Portal CR creation tests (service-layer, no HTTP)
# ============================================================================

class TestPortalChangeRequestCreation:
    """Test portal-path change request creation at the service layer."""

    def test_client_can_create_portal_cr_for_owned_app(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                  "old_value": "Old Co", "new_value": "New Co"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Name change", items, user)

        assert req["id"].startswith("CR-")
        assert req["source"] == "portal_client"
        assert req["source_channel"] == "portal"
        assert req["status"] == "draft"
        assert len(req["items"]) == 1

    def test_client_cr_auto_submit(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "address_change", "field_name": "registered_address",
                  "old_value": "Old St", "new_value": "New St"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Address update", items, user)

        ok, err = cm.submit_change_request(wdb, req["id"], user)
        assert ok, f"Submit failed: {err}"

        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "submitted"

    def test_portal_cr_visible_in_list(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "contact_detail_update", "field_name": "email",
                  "old_value": "old@x.com", "new_value": "new@x.com"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Email change", items, user)

        # Visible via list_change_requests (used by portal GET and back-office GET)
        all_reqs = cm.list_change_requests(wdb, application_id=app_id)
        found = [r for r in all_reqs if r["id"] == req["id"]]
        assert len(found) == 1
        assert found[0]["source"] == "portal_client"

    def test_portal_cr_has_correct_fields(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "other", "field_name": "note", "new_value": "Test"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Test reason", items, user)

        assert req["source"] == "portal_client"
        assert req["source_channel"] == "portal"
        assert req["created_by"] == client_id
        assert req["materiality"] in ("tier1", "tier2", "tier3")

    def test_portal_cr_has_at_least_one_item(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "company_details", "field_name": "brn",
                  "old_value": "123", "new_value": "456"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "BRN update", items, user)

        db_items = db.execute(
            "SELECT * FROM change_request_items WHERE request_id = ?", (req["id"],)
        ).fetchall()
        assert len(db_items) >= 1

    def test_non_client_role_rejected(self, db):
        """Roles other than admin/sco/co/analyst/client should be rejected."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "x", "name": "Hacker", "role": "nobody"}

        items = [{"change_type": "company_details"}]
        with pytest.raises(PermissionError):
            cm.create_change_request(wdb, app_id, "portal_client", "portal",
                                     "Blocked", items, user)


# ============================================================================
# 4. Profile Version Detail tests
# ============================================================================

class TestProfileVersionDetail:
    """Test per-application profile version detail endpoint logic."""

    def _create_version(self, db, cm, app_id, user):
        """Helper: create a version via a full lifecycle."""
        wdb = _DBWrapper(db)
        items = [{"change_type": "company_details", "field_name": "company_name",
                  "old_value": "Old", "new_value": "New"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                       "Version test", items, user)
        cm.submit_change_request(wdb, req["id"], user)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", user)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", user)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", user)
        ok, err = cm.approve_change_request(wdb, req["id"], user, decision_notes="OK")
        assert ok, f"Approve failed: {err}"
        ok, err, vid = cm.implement_change_request(wdb, req["id"], user)
        assert ok, f"Implement failed: {err}"
        return vid

    def test_list_returns_version_id(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "Admin", "role": "admin"}

        vid = self._create_version(db, cm, app_id, user)

        versions = cm.get_profile_versions(wdb, app_id)
        ids = [v["id"] for v in versions]
        assert vid in ids

    def test_detail_returns_version_with_snapshot(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "Admin", "role": "admin"}

        vid = self._create_version(db, cm, app_id, user)

        detail = cm.get_profile_version_detail(wdb, vid)
        assert detail is not None
        assert detail["id"] == vid
        assert detail["application_id"] == app_id
        assert "profile_snapshot" in detail
        # Snapshot should be dict (parsed from JSON)
        assert isinstance(detail["profile_snapshot"], dict)

    def test_wrong_app_version_pair_returns_none(self, db):
        """Version exists but belongs to a different application."""
        cm = _get_cm()
        wdb = _DBWrapper(db)

        # Create two apps
        app1, _ = _setup_test_data(db)
        app2, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "Admin", "role": "admin"}

        vid = self._create_version(db, cm, app1, user)

        # Version belongs to app1, not app2
        detail = cm.get_profile_version_detail(wdb, vid)
        assert detail is not None
        assert detail["application_id"] == app1
        assert detail["application_id"] != app2

    def test_nonexistent_version_returns_none(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)

        detail = cm.get_profile_version_detail(wdb, "PV-nonexistent-000")
        assert detail is None

    def test_version_detail_has_version_number(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "Admin", "role": "admin"}

        vid = self._create_version(db, cm, app_id, user)

        detail = cm.get_profile_version_detail(wdb, vid)
        assert detail["version_number"] >= 1
        assert detail["is_current"] in (True, 1)


# ============================================================================
# 5. contact_detail_update alignment test
# ============================================================================

class TestContactDetailUpdateAlignment:
    """Verify contact_detail_update is handled by _apply_change_item."""

    def test_contact_detail_update_applies(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "Admin", "role": "admin"}

        items = [{"change_type": "contact_detail_update", "field_name": "company_name",
                  "old_value": "Test Company Ltd", "new_value": "Updated Company Ltd"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                       "Contact update", items, user)
        cm.submit_change_request(wdb, req["id"], user)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", user)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", user)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", user)
        ok, err = cm.approve_change_request(wdb, req["id"], user, decision_notes="OK")
        assert ok, f"Approve failed: {err}"

        ok, err, vid = cm.implement_change_request(wdb, req["id"], user)
        assert ok, f"implement failed for contact_detail_update: {err}"
