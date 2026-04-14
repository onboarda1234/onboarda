"""
Round 6 QA — Portal POST 500 fix, change_type validation, legacy payload rejection,
and alert convert validation.

Covers all 11 required test cases:
 1. portal owned app valid payload → 201
 2. portal request appears in portal list
 3. portal request appears in back-office list
 4. portal non-owned app → 403/404
 5. portal invalid change_type → 400
 6. back-office invalid change_type → 400
 7. back-office legacy top-level field/new_value without items → 400
 8. back-office valid items[] payload → 201
 9. alert convert invalid change_type under_review → 400
10. alert convert valid items[] under_review → 201
11. no zero-item request created from any create endpoint
"""

import json
import os
import sys
import secrets
import sqlite3
import pytest
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


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
    """Create a test application with directors/UBOs and a client."""
    app_id = f"r6-app-{secrets.token_hex(4)}"
    client_id = f"r6-cl-{secrets.token_hex(4)}"

    raw_db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"r6-{secrets.token_hex(3)}@test.com", "hash", "R6 Test Company"),
    )
    raw_db.execute(
        """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, f"APP-R6-{secrets.token_hex(4)}", client_id, "R6 Company Ltd",
         "GB", "Financial Services", "Limited Company", "approved"),
    )
    raw_db.execute(
        """INSERT INTO directors (id, application_id, person_key, full_name, first_name, last_name, nationality, date_of_birth)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_id, "dir1", "R6 Director", "R6", "Director", "GB", "1980-01-15"),
    )
    raw_db.execute(
        """INSERT INTO ubos (id, application_id, person_key, full_name, first_name, last_name, nationality, ownership_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_id, "ubo1", "R6 UBO", "R6", "UBO", "US", 75.0),
    )
    raw_db.commit()
    return app_id, client_id


# ============================================================================
# 1-3. Portal change request — owned app, list visibility
# ============================================================================

class TestPortalOwnedApp:
    """Test 1-3: Portal-created CR for owned app returns 201 and appears in lists."""

    def test_portal_owned_app_valid_payload_returns_201(self, db):
        """Test 1: portal owned app valid payload → 201"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                  "old_value": "R6 Company Ltd", "new_value": "New R6 Company"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Name change", items, user)

        assert req["id"].startswith("CR-")
        assert req["source"] == "portal_client"
        assert req["source_channel"] == "portal"
        assert req["status"] == "draft"
        assert len(req["items"]) >= 1

    def test_portal_request_appears_in_portal_list(self, db):
        """Test 2: portal request appears in portal list"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "address_change", "field_name": "registered_address",
                  "old_value": "Old St", "new_value": "New St"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Address change", items, user)

        # Auto-submit like the portal handler does
        ok, err = cm.submit_change_request(wdb, req["id"], user)
        assert ok, f"Submit failed: {err}"

        # Portal list: filter by application_id (portal scopes to client's apps)
        reqs = cm.list_change_requests(wdb, application_id=app_id)
        found = [r for r in reqs if r["id"] == req["id"]]
        assert len(found) == 1, "Portal-created request not found in list"
        assert found[0]["source"] == "portal_client"

    def test_portal_request_appears_in_backoffice_list(self, db):
        """Test 3: portal request appears in back-office list"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "contact_detail_update", "field_name": "email",
                  "old_value": "old@co.com", "new_value": "new@co.com"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Email change", items, user)
        cm.submit_change_request(wdb, req["id"], user)

        # Back-office list: also uses list_change_requests
        all_reqs = cm.list_change_requests(wdb, application_id=app_id)
        found = [r for r in all_reqs if r["id"] == req["id"]]
        assert len(found) == 1, "Portal CR not visible in back-office list"
        assert found[0]["source_channel"] == "portal"

    def test_portal_cr_has_at_least_one_db_item(self, db):
        """Portal-created request must have at least one item in DB."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "company_details", "field_name": "brn",
                  "old_value": "BRN-001", "new_value": "BRN-002"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "BRN update", items, user)

        db_items = db.execute(
            "SELECT * FROM change_request_items WHERE request_id = ?", (req["id"],)
        ).fetchall()
        assert len(db_items) >= 1


# ============================================================================
# 4. Portal non-owned app → 403/404
# ============================================================================

class TestPortalNonOwnedApp:
    """Test 4: Non-owned application requests are blocked."""

    def test_portal_nonowned_app_ownership_check(self, db):
        """Test 4: portal non-owned app → 403/404 (ownership enforced at handler level)

        The service layer allows any client to create; ownership is enforced
        by PortalChangeRequestHandler via SQL WHERE clause checking client_id.
        This test verifies that a different client_id won't match the application.
        """
        app_id, client_id = _setup_test_data(db)

        # A different client should not find this app
        different_client = "other-client-12345"
        app = db.execute(
            "SELECT id FROM applications WHERE id = ? AND client_id = ?",
            (app_id, different_client),
        ).fetchone()
        assert app is None, "Non-owned client should not find the application"

    def test_portal_nonexistent_app_not_found(self, db):
        """Non-existent application_id returns no result."""
        _setup_test_data(db)

        app = db.execute(
            "SELECT id FROM applications WHERE id = ? AND client_id = ?",
            ("nonexistent-app-id", "any-client"),
        ).fetchone()
        assert app is None


# ============================================================================
# 5. Portal invalid change_type → 400
# ============================================================================

class TestPortalInvalidChangeType:
    """Test 5: Portal with invalid change_type returns 400 (ValueError)."""

    def test_portal_invalid_change_type_rejected(self, db):
        """Test 5: portal invalid change_type → 400"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "profile_update", "field_name": "x", "new_value": "y"}]
        with pytest.raises(ValueError, match="profile_update"):
            cm.create_change_request(
                wdb, app_id, "portal_client", "portal", "Bad type", items, user)

    def test_portal_empty_change_type_rejected(self, db):
        """Empty change_type string should be rejected."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "", "field_name": "x", "new_value": "y"}]
        with pytest.raises(ValueError):
            cm.create_change_request(
                wdb, app_id, "portal_client", "portal", "Empty type", items, user)


# ============================================================================
# 6. Back-office invalid change_type → 400
# ============================================================================

class TestBackofficeInvalidChangeType:
    """Test 6: Back-office with invalid change_type returns 400 (ValueError)."""

    def test_backoffice_invalid_change_type_rejected(self, db):
        """Test 6: back-office invalid change_type → 400"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "sco"}

        items = [{"change_type": "profile_update", "field_name": "x", "new_value": "y"}]
        with pytest.raises(ValueError, match="profile_update"):
            cm.create_change_request(
                wdb, app_id, "backoffice_manual", "backoffice",
                "Bad type", items, user)

    def test_backoffice_nonsense_type_rejected(self, db):
        """Completely nonsensical change_type should be rejected."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "sco"}

        items = [{"change_type": "xyzzy_magic_type", "field_name": "x"}]
        with pytest.raises(ValueError, match="xyzzy_magic_type"):
            cm.create_change_request(
                wdb, app_id, "backoffice_manual", "backoffice",
                "Nonsense", items, user)


# ============================================================================
# 7. Back-office legacy top-level field/new_value without items → 400
# ============================================================================

class TestBackofficeLegacyPayload:
    """Test 7: Legacy top-level field/new_value payload is rejected."""

    def test_legacy_payload_no_items_raises_valueerror(self, db):
        """Test 7: back-office legacy top-level field/new_value without items → 400

        When items=[] (empty), create_change_request should raise ValueError.
        The back-office handler also checks at the HTTP layer.
        """
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "sco"}

        # Simulates legacy payload: change_type at root, no items
        with pytest.raises(ValueError, match="At least one change item"):
            cm.create_change_request(
                wdb, app_id, "backoffice_manual", "backoffice",
                "Legacy payload", [], user)

    def test_empty_items_no_request_created(self, db):
        """Empty items must not create any request in the DB."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "sco"}

        with pytest.raises(ValueError):
            cm.create_change_request(
                wdb, app_id, "backoffice_manual", "backoffice",
                "Should not exist", [], user)

        rows = db.execute(
            "SELECT * FROM change_requests WHERE application_id = ? AND reason = 'Should not exist'",
            (app_id,),
        ).fetchall()
        assert len(rows) == 0, "Zero-item request was incorrectly created"


# ============================================================================
# 8. Back-office valid items[] payload → 201
# ============================================================================

class TestBackofficeValidItems:
    """Test 8: Back-office with valid items[] returns 201."""

    def test_backoffice_valid_items_creates_request(self, db):
        """Test 8: back-office valid items[] payload → 201"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "sco"}

        items = [
            {"change_type": "company_details", "field_name": "company_name",
             "old_value": "Old", "new_value": "New"},
            {"change_type": "address_change", "field_name": "registered_address",
             "old_value": "Old Addr", "new_value": "New Addr"},
        ]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice",
            "Multi-item change", items, user)

        assert req["id"].startswith("CR-")
        assert req["source"] == "backoffice_manual"
        assert len(req["items"]) == 2

        # Verify items exist in DB
        db_items = db.execute(
            "SELECT * FROM change_request_items WHERE request_id = ?", (req["id"],)
        ).fetchall()
        assert len(db_items) == 2

    def test_backoffice_all_valid_types_accepted(self, db):
        """All VALID_CHANGE_TYPES should be accepted."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "admin"}

        for ct in sorted(cm.VALID_CHANGE_TYPES):
            items = [{"change_type": ct, "field_name": "test_field", "new_value": "test"}]
            req = cm.create_change_request(
                wdb, app_id, "backoffice_manual", "backoffice",
                f"Test {ct}", items, user)
            assert req["id"].startswith("CR-"), f"Failed for change_type: {ct}"


# ============================================================================
# 9. Alert convert invalid change_type under_review → 400
# ============================================================================

class TestAlertConvertInvalidChangeType:
    """Test 9: Alert convert with invalid change_type returns 400 (ValueError)."""

    def test_alert_convert_invalid_change_type_rejected(self, db):
        """Test 9: alert convert invalid change_type under_review → 400"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "sco"}

        # Create and advance alert to under_review
        alert = cm.create_change_alert(
            wdb, app_id, "director_change", "companies_house",
            "Director name changed", {"full_name": {"old": "A", "new": "B"}},
            user=user)
        cm.update_change_alert_status(wdb, alert["id"], "under_review", user)

        # Try to convert with invalid change_type items
        bad_items = [{"change_type": "invalid_type_xyz", "field_name": "x", "new_value": "y"}]
        with pytest.raises(ValueError, match="invalid_type_xyz"):
            cm.convert_alert_to_request(wdb, alert["id"], user, items=bad_items)

    def test_alert_convert_profile_update_rejected(self, db):
        """The specific 'profile_update' type mentioned in QA must be rejected."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "sco"}

        alert = cm.create_change_alert(
            wdb, app_id, "ubo_change", "companies_house",
            "UBO changed", {"ownership_pct": {"old": "50", "new": "60"}},
            user=user)
        cm.update_change_alert_status(wdb, alert["id"], "under_review", user)

        bad_items = [{"change_type": "profile_update"}]
        with pytest.raises(ValueError, match="profile_update"):
            cm.convert_alert_to_request(wdb, alert["id"], user, items=bad_items)


# ============================================================================
# 10. Alert convert valid items[] under_review → 201
# ============================================================================

class TestAlertConvertValidItems:
    """Test 10: Alert convert with valid items returns 201."""

    def test_alert_convert_valid_items_creates_request(self, db):
        """Test 10: alert convert valid items[] under_review → 201"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "sco"}

        alert = cm.create_change_alert(
            wdb, app_id, "director_change", "companies_house",
            "Director name changed", {"full_name": {"old": "Old Name", "new": "New Name"}},
            user=user)
        cm.update_change_alert_status(wdb, alert["id"], "under_review", user)

        valid_items = [{"change_type": "director_change", "field_name": "full_name",
                        "old_value": "Old Name", "new_value": "New Name"}]
        request, err = cm.convert_alert_to_request(
            wdb, alert["id"], user, items=valid_items)

        assert request is not None, f"Convert failed: {err}"
        assert request["id"].startswith("CR-")
        assert request["source"] == "external_alert_conversion"
        assert len(request["items"]) >= 1

    def test_alert_convert_auto_derives_items(self, db):
        """Alert convert without explicit items derives from detected_changes."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "sco"}

        alert = cm.create_change_alert(
            wdb, app_id, "company_details", "companies_house",
            "Company name changed",
            {"company_name": {"old": "Old Co", "new": "New Co"}},
            user=user)
        cm.update_change_alert_status(wdb, alert["id"], "under_review", user)

        request, err = cm.convert_alert_to_request(wdb, alert["id"], user)
        assert request is not None, f"Auto-derive convert failed: {err}"
        assert len(request["items"]) >= 1


# ============================================================================
# 11. No zero-item request from any create endpoint
# ============================================================================

class TestNoZeroItemRequests:
    """Test 11: No zero-item request created from any create endpoint."""

    def test_service_layer_rejects_empty_items(self, db):
        """Service layer rejects empty items."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "admin"}

        with pytest.raises(ValueError, match="At least one change item"):
            cm.create_change_request(
                wdb, app_id, "backoffice_manual", "backoffice",
                "Empty items test", [], user)

    def test_service_layer_rejects_none_items_equivalent(self, db):
        """Passing None-equivalent empty list is rejected."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "sco"}

        # data.get("items", []) returns [] when items key is missing
        with pytest.raises(ValueError, match="At least one change item"):
            cm.create_change_request(
                wdb, app_id, "backoffice_manual", "backoffice",
                "Missing items", [], user)

    def test_no_orphan_request_after_validation_failure(self, db):
        """No request row should exist after a validation failure."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "officer1", "name": "Officer", "role": "admin"}

        # Count existing requests
        before = db.execute(
            "SELECT COUNT(*) as cnt FROM change_requests WHERE application_id = ?",
            (app_id,),
        ).fetchone()["cnt"]

        # Try to create with invalid type
        try:
            cm.create_change_request(
                wdb, app_id, "backoffice_manual", "backoffice",
                "Invalid create", [{"change_type": "bogus"}], user)
        except ValueError:
            pass

        # Try to create with empty items
        try:
            cm.create_change_request(
                wdb, app_id, "backoffice_manual", "backoffice",
                "Empty create", [], user)
        except ValueError:
            pass

        after = db.execute(
            "SELECT COUNT(*) as cnt FROM change_requests WHERE application_id = ?",
            (app_id,),
        ).fetchone()["cnt"]

        assert after == before, (
            f"Expected no new requests, but {after - before} were created"
        )


# ============================================================================
# HTTP-level tests (tornado test client)
# ============================================================================

class TestPortalHTTPEndpoint:
    """HTTP-level tests for the portal change request endpoint."""

    def _make_client_and_app(self, db):
        """Create client + owned app for HTTP tests."""
        cid = f"httpcl-{secrets.token_hex(4)}"
        aid = f"httpapp-{secrets.token_hex(4)}"
        db.execute(
            "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            (cid, f"{cid}@test.com", "hash", "HTTP Test Co"),
        )
        db.execute(
            """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (aid, f"APP-{secrets.token_hex(4)}", cid, "HTTP Test Co",
             "MU", "Tech", "SME", "approved"),
        )
        db.commit()
        return cid, aid

    def test_portal_post_valid_returns_201(self, db, app):
        """HTTP test: portal POST with valid payload returns 201."""
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app, create_token
        import unittest

        cid, aid = self._make_client_and_app(db)
        token = create_token(cid, "client", "Test Client", "client")

        class _Test(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_post(self_inner):
                payload = json.dumps({
                    "application_id": aid,
                    "items": [{"change_type": "company_details", "field_name": "company_name",
                               "new_value": "New Name"}],
                    "reason": "Test change"
                })
                resp = self_inner.fetch(
                    "/api/portal/change-requests", method="POST", body=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {token}"})
                assert resp.code == 201, f"Expected 201, got {resp.code}: {resp.body.decode()}"
                body = json.loads(resp.body)
                assert body["id"].startswith("CR-")
                assert body["status"] == "submitted"  # auto-submitted

        suite = unittest.TestLoader().loadTestsFromName("test_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w")).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"

    def test_portal_post_nonowned_returns_404(self, db, app):
        """HTTP test: portal POST for non-owned app returns 404."""
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app, create_token

        cid, aid = self._make_client_and_app(db)
        other_cid = f"other-{secrets.token_hex(4)}"
        # Create token for a different client
        token = create_token(other_cid, "client", "Other Client", "client")

        class _Test(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_post(self_inner):
                payload = json.dumps({
                    "application_id": aid,
                    "items": [{"change_type": "company_details", "field_name": "x",
                               "new_value": "y"}],
                })
                resp = self_inner.fetch(
                    "/api/portal/change-requests", method="POST", body=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {token}"})
                assert resp.code in (403, 404), f"Expected 403/404, got {resp.code}"

        import unittest
        suite = unittest.TestLoader().loadTestsFromName("test_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w")).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"

    def test_portal_post_invalid_change_type_returns_400(self, db, app):
        """HTTP test: portal POST with invalid change_type returns 400."""
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app, create_token

        cid, aid = self._make_client_and_app(db)
        token = create_token(cid, "client", "Test Client", "client")

        class _Test(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_post(self_inner):
                payload = json.dumps({
                    "application_id": aid,
                    "items": [{"change_type": "profile_update", "field_name": "x",
                               "new_value": "y"}],
                })
                resp = self_inner.fetch(
                    "/api/portal/change-requests", method="POST", body=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {token}"})
                assert resp.code == 400, f"Expected 400, got {resp.code}: {resp.body.decode()}"
                body = json.loads(resp.body)
                assert "profile_update" in body.get("error", "")

        import unittest
        suite = unittest.TestLoader().loadTestsFromName("test_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w")).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"


class TestBackofficeHTTPEndpoint:
    """HTTP-level tests for the back-office change request endpoint."""

    def _make_app_and_token(self, db):
        """Create application + officer token for HTTP tests."""
        aid = f"boapp-{secrets.token_hex(4)}"
        db.execute(
            """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (aid, f"APP-{secrets.token_hex(4)}", "someclient", "BO Test Co",
             "MU", "Tech", "SME", "approved"),
        )
        db.commit()
        from server import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        return aid, token

    def test_backoffice_post_valid_items_returns_201(self, db, app):
        """HTTP test: back-office POST with valid items returns 201."""
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app

        aid, token = self._make_app_and_token(db)

        class _Test(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_post(self_inner):
                payload = json.dumps({
                    "application_id": aid,
                    "items": [{"change_type": "company_details", "field_name": "company_name",
                               "old_value": "Old", "new_value": "New"}],
                    "reason": "Test update"
                })
                resp = self_inner.fetch(
                    "/api/change-management/requests", method="POST", body=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {token}"})
                assert resp.code == 201, f"Expected 201, got {resp.code}: {resp.body.decode()}"

        import unittest
        suite = unittest.TestLoader().loadTestsFromName("test_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w")).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"

    def test_backoffice_post_legacy_payload_returns_400(self, db, app):
        """HTTP test: back-office POST with legacy top-level payload returns 400."""
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app

        aid, token = self._make_app_and_token(db)

        class _Test(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_post(self_inner):
                # Legacy payload: change_type at root, no items
                payload = json.dumps({
                    "application_id": aid,
                    "change_type": "profile_update",
                    "field": "company_name",
                    "new_value": "New Name"
                })
                resp = self_inner.fetch(
                    "/api/change-management/requests", method="POST", body=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {token}"})
                assert resp.code == 400, f"Expected 400, got {resp.code}: {resp.body.decode()}"

        import unittest
        suite = unittest.TestLoader().loadTestsFromName("test_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w")).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"

    def test_backoffice_post_invalid_change_type_returns_400(self, db, app):
        """HTTP test: back-office POST with invalid change_type returns 400."""
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app

        aid, token = self._make_app_and_token(db)

        class _Test(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_post(self_inner):
                payload = json.dumps({
                    "application_id": aid,
                    "items": [{"change_type": "profile_update", "field_name": "x",
                               "new_value": "y"}],
                })
                resp = self_inner.fetch(
                    "/api/change-management/requests", method="POST", body=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {token}"})
                assert resp.code == 400, f"Expected 400, got {resp.code}: {resp.body.decode()}"

        import unittest
        suite = unittest.TestLoader().loadTestsFromName("test_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w")).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"

    def test_backoffice_post_empty_items_returns_400(self, db, app):
        """HTTP test: back-office POST with empty items returns 400."""
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app

        aid, token = self._make_app_and_token(db)

        class _Test(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_post(self_inner):
                payload = json.dumps({
                    "application_id": aid,
                    "items": [],
                })
                resp = self_inner.fetch(
                    "/api/change-management/requests", method="POST", body=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {token}"})
                assert resp.code == 400, f"Expected 400, got {resp.code}: {resp.body.decode()}"

        import unittest
        suite = unittest.TestLoader().loadTestsFromName("test_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w")).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"
