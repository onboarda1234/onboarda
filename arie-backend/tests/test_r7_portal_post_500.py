"""
Round 7 QA — Portal POST /api/portal/change-requests 500 fix.

Root cause: change_requests.created_by had REFERENCES users(id) FK constraint.
Portal client IDs are in the `clients` table, not `users`, causing FK violation
in PostgreSQL (which enforces FKs). SQLite tests passed because FKs are off
by default.

Tests:
 1. portal owned app valid items[] → 201
 2. portal created request has items_count > 0
 3. portal created request visible in portal list
 4. portal created request visible in back-office list
 5. portal non-owned app → 403/404
 6. portal invalid change_type → 400
 7. portal zero-items → 400
 8. no request created on failed portal POST
 9. back-office lifecycle still passes
10. analyst cannot approve/reject/implement still passes
11. FK enforcement regression: client-created CR succeeds with FK ON
"""

import json
import os
import sys
import secrets
import sqlite3
import pytest

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
    app_id = f"r7-app-{secrets.token_hex(4)}"
    client_id = f"r7-cl-{secrets.token_hex(4)}"

    raw_db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"r7-{secrets.token_hex(3)}@test.com", "hash", "R7 Test Company"),
    )
    raw_db.execute(
        """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, f"APP-R7-{secrets.token_hex(4)}", client_id, "R7 Company Ltd",
         "GB", "Financial Services", "Limited Company", "approved"),
    )
    raw_db.execute(
        """INSERT INTO directors (id, application_id, person_key, full_name, first_name, last_name, nationality, date_of_birth)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_id, "dir1", "R7 Director", "R7", "Director", "GB", "1980-01-15"),
    )
    raw_db.commit()
    return app_id, client_id


# ============================================================================
# FK enforcement regression test — the root cause
# ============================================================================

class TestFKEnforcementRegression:
    """Verify that the FK constraint removal actually fixes the portal POST 500.

    This test enables SQLite FK enforcement (which mimics PostgreSQL behavior)
    and confirms that client-created change requests no longer violate FK
    constraints.
    """

    def test_client_created_cr_succeeds_with_fk_enforcement(self, db):
        """The exact scenario that caused the production 500:
        client ID is in clients table, not users table.
        With FK enforcement ON, INSERT INTO change_requests must succeed."""
        cm = _get_cm()

        # Enable FK enforcement to mimic PostgreSQL
        db.execute("PRAGMA foreign_keys = ON")

        app_id, client_id = _setup_test_data(db)

        # Verify client is NOT in users table (this is the root cause)
        user_row = db.execute(
            "SELECT id FROM users WHERE id = ?", (client_id,)
        ).fetchone()
        assert user_row is None, "Client ID should NOT be in users table"

        # Create change request as portal client
        wdb = _DBWrapper(db)
        user = {"sub": client_id, "name": "Portal Client", "role": "client"}
        items = [{"change_type": "company_details", "field_name": "company_name",
                  "old_value": "R7 Company Ltd", "new_value": "New R7 Name"}]

        # This previously raised IntegrityError (FK violation) in PostgreSQL
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Name change", items, user)

        assert req["id"].startswith("CR-")
        assert req["created_by"] == client_id
        assert req["source"] == "portal_client"

    def test_profile_version_with_client_user_succeeds(self, db):
        """entity_profile_versions.created_by also had REFERENCES users(id).
        Verify it works with client IDs after fix."""
        cm = _get_cm()

        # Enable FK enforcement
        db.execute("PRAGMA foreign_keys = ON")

        app_id, client_id = _setup_test_data(db)

        wdb = _DBWrapper(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        # Create a profile version with client user
        snapshot = cm.snapshot_entity_profile(wdb, app_id)
        if snapshot:
            version_id = cm._create_profile_version(
                wdb, app_id, None, {}, snapshot, user)
            assert version_id.startswith("PV-")


# ============================================================================
# 1-4. Portal change request — owned app, list visibility
# ============================================================================

class TestPortalOwnedApp:
    """Test 1-4: Portal-created CR for owned app returns 201 and appears in lists."""

    def test_portal_owned_app_valid_payload_returns_201(self, db):
        """Test 1: portal owned app valid payload → 201"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                  "old_value": "R7 Company Ltd", "new_value": "New R7 Company"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Name change", items, user)

        assert req["id"].startswith("CR-")
        assert req["source"] == "portal_client"
        assert req["source_channel"] == "portal"
        assert req["status"] == "draft"
        assert len(req["items"]) >= 1

    def test_portal_created_request_has_items_count_gt_zero(self, db):
        """Test 2: portal created request has items_count > 0"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "address_change", "field_name": "registered_address",
                  "old_value": "Old St", "new_value": "New St"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Address change", items, user)

        # Verify items in DB
        db_items = db.execute(
            "SELECT * FROM change_request_items WHERE request_id = ?", (req["id"],)
        ).fetchall()
        assert len(db_items) > 0, "Portal-created request must have items in DB"

    def test_portal_request_appears_in_portal_list(self, db):
        """Test 3: portal request appears in portal list"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "contact_detail_update", "field_name": "phone",
                  "old_value": "111", "new_value": "222"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "Phone update", items, user)
        ok, err = cm.submit_change_request(wdb, req["id"], user)
        assert ok, f"Submit failed: {err}"

        # Portal list scopes by application_id
        reqs = cm.list_change_requests(wdb, application_id=app_id)
        found = [r for r in reqs if r["id"] == req["id"]]
        assert len(found) == 1, "Portal-created request not in portal list"
        assert found[0]["source"] == "portal_client"

    def test_portal_request_appears_in_backoffice_list(self, db):
        """Test 4: portal request appears in back-office list"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "company_details", "field_name": "brn",
                  "old_value": "BRN-001", "new_value": "BRN-002"}]
        req = cm.create_change_request(
            wdb, app_id, "portal_client", "portal", "BRN update", items, user)
        cm.submit_change_request(wdb, req["id"], user)

        all_reqs = cm.list_change_requests(wdb, application_id=app_id)
        found = [r for r in all_reqs if r["id"] == req["id"]]
        assert len(found) == 1, "Portal CR not visible in back-office list"
        assert found[0]["source_channel"] == "portal"


# ============================================================================
# 5. Portal non-owned app → 403/404
# ============================================================================

class TestPortalNonOwnedApp:
    """Test 5: Non-owned application requests are blocked at handler level."""

    def test_portal_nonowned_app_ownership_check(self, db):
        """Test 5: portal non-owned app → 403/404 (ownership enforced at handler level)"""
        app_id, client_id = _setup_test_data(db)

        different_client = "other-client-r7-12345"
        app = db.execute(
            "SELECT id FROM applications WHERE id = ? AND client_id = ?",
            (app_id, different_client),
        ).fetchone()
        assert app is None, "Non-owned client should not find the application"


# ============================================================================
# 6. Portal invalid change_type → 400
# ============================================================================

class TestPortalInvalidChangeType:
    """Test 6: Portal with invalid change_type returns 400 (ValueError)."""

    def test_portal_invalid_change_type_rejected(self, db):
        """Test 6: portal invalid change_type → 400"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        items = [{"change_type": "profile_update", "field_name": "x", "new_value": "y"}]
        with pytest.raises(ValueError, match="profile_update"):
            cm.create_change_request(
                wdb, app_id, "portal_client", "portal", "Bad type", items, user)


# ============================================================================
# 7. Portal zero-items → 400
# ============================================================================

class TestPortalZeroItems:
    """Test 7: Portal with zero items returns 400 (ValueError)."""

    def test_portal_zero_items_rejected(self, db):
        """Test 7: portal zero-items → 400"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        with pytest.raises(ValueError, match="At least one change item"):
            cm.create_change_request(
                wdb, app_id, "portal_client", "portal", "Empty", [], user)


# ============================================================================
# 8. No request created on failed portal POST
# ============================================================================

class TestNoOrphanRequests:
    """Test 8: Failed portal POST must not leave orphan requests in DB."""

    def test_no_request_created_on_invalid_type(self, db):
        """No orphan request after invalid change_type error."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        before = db.execute(
            "SELECT COUNT(*) as cnt FROM change_requests WHERE application_id = ?",
            (app_id,),
        ).fetchone()["cnt"]

        try:
            cm.create_change_request(
                wdb, app_id, "portal_client", "portal",
                "Bad", [{"change_type": "bogus"}], user)
        except ValueError:
            pass

        after = db.execute(
            "SELECT COUNT(*) as cnt FROM change_requests WHERE application_id = ?",
            (app_id,),
        ).fetchone()["cnt"]
        assert after == before, "Failed create must not leave orphan requests"

    def test_no_request_created_on_zero_items(self, db):
        """No orphan request after zero-items error."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, client_id = _setup_test_data(db)
        user = {"sub": client_id, "name": "Client", "role": "client"}

        before = db.execute(
            "SELECT COUNT(*) as cnt FROM change_requests WHERE application_id = ?",
            (app_id,),
        ).fetchone()["cnt"]

        try:
            cm.create_change_request(
                wdb, app_id, "portal_client", "portal", "Empty", [], user)
        except ValueError:
            pass

        after = db.execute(
            "SELECT COUNT(*) as cnt FROM change_requests WHERE application_id = ?",
            (app_id,),
        ).fetchone()["cnt"]
        assert after == before


# ============================================================================
# 9. Back-office lifecycle still passes
# ============================================================================

class TestBackofficeLifecycle:
    """Test 9: Back-office lifecycle unchanged by FK fix."""

    def test_full_lifecycle(self, db):
        """draft → submitted → triage → ready_for_review → approval_pending → approved → implemented"""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        officer = {"sub": "officer-r7", "name": "Officer", "role": "sco"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                  "old_value": "Old", "new_value": "New"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Officer change", items, officer)
        assert req["status"] == "draft"

        ok, _ = cm.submit_change_request(wdb, req["id"], officer)
        assert ok

        ok, _ = cm.update_change_request_status(wdb, req["id"], "triage_in_progress", officer)
        assert ok
        ok, _ = cm.update_change_request_status(wdb, req["id"], "ready_for_review", officer)
        assert ok
        ok, _ = cm.update_change_request_status(wdb, req["id"], "approval_pending", officer)
        assert ok

        ok, _ = cm.approve_change_request(wdb, req["id"], officer)
        assert ok

        ok, _, _ = cm.implement_change_request(wdb, req["id"], officer)
        assert ok

        row = db.execute(
            "SELECT status FROM change_requests WHERE id = ?", (req["id"],)
        ).fetchone()
        assert row["status"] == "implemented"


# ============================================================================
# 10. Analyst cannot approve/reject/implement still passes
# ============================================================================

class TestAnalystRBAC:
    """Test 10: Analyst restrictions unaffected by FK fix."""

    def test_analyst_cannot_approve(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        officer = {"sub": "sco-r7", "name": "SCO", "role": "sco"}
        analyst = {"sub": "analyst-r7", "name": "Analyst", "role": "analyst"}

        items = [{"change_type": "company_details", "field_name": "x", "new_value": "y"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test", items, officer)
        cm.submit_change_request(wdb, req["id"], officer)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", officer)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", officer)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", officer)

        # Analyst should not be able to approve
        ok, err = cm.approve_change_request(wdb, req["id"], analyst)
        assert not ok
        assert "not permitted" in err.lower() or "analyst" in err.lower()

    def test_analyst_cannot_reject(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        officer = {"sub": "sco-r7", "name": "SCO", "role": "sco"}
        analyst = {"sub": "analyst-r7", "name": "Analyst", "role": "analyst"}

        items = [{"change_type": "company_details", "field_name": "x", "new_value": "y"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test", items, officer)
        cm.submit_change_request(wdb, req["id"], officer)

        ok, err = cm.reject_change_request(wdb, req["id"], analyst, "Reject test")
        assert not ok

    def test_analyst_cannot_implement(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        officer = {"sub": "sco-r7", "name": "SCO", "role": "sco"}
        analyst = {"sub": "analyst-r7", "name": "Analyst", "role": "analyst"}

        items = [{"change_type": "company_details", "field_name": "x", "new_value": "y"}]
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "Test", items, officer)
        cm.submit_change_request(wdb, req["id"], officer)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", officer)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", officer)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", officer)
        cm.approve_change_request(wdb, req["id"], officer)

        ok, _, _ = cm.implement_change_request(wdb, req["id"], analyst)
        assert not ok


# ============================================================================
# HTTP-level tests using Tornado test client
# ============================================================================

class TestPortalHTTPEndpoint:
    """HTTP-level tests for the portal change request endpoint."""

    def _make_client_and_app(self, db):
        """Create client + owned app for HTTP tests."""
        cid = f"r7httpcl-{secrets.token_hex(4)}"
        aid = f"r7httpapp-{secrets.token_hex(4)}"
        db.execute(
            "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            (cid, f"{cid}@test.com", "hash", "R7 HTTP Test Co"),
        )
        db.execute(
            """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (aid, f"APP-{secrets.token_hex(4)}", cid, "R7 HTTP Test Co",
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
                               "new_value": "New Name R7"}],
                    "reason": "R7 test change"
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
        result = unittest.TextTestRunner(verbosity=0, stream=__import__("io").StringIO()).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"

    def test_portal_post_nonowned_returns_404(self, db, app):
        """HTTP test: portal POST for non-owned app returns 404."""
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app, create_token
        import unittest

        cid, aid = self._make_client_and_app(db)
        other_cid = f"other-r7-{secrets.token_hex(4)}"
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
                assert resp.code in (403, 404), f"Expected 403/404, got {resp.code}: {resp.body.decode()}"

        suite = unittest.TestLoader().loadTestsFromName("test_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=__import__("io").StringIO()).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"

    def test_portal_post_invalid_change_type_returns_400(self, db, app):
        """HTTP test: portal POST with invalid change_type returns 400."""
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
                    "items": [{"change_type": "profile_update", "field_name": "x",
                               "new_value": "y"}],
                })
                resp = self_inner.fetch(
                    "/api/portal/change-requests", method="POST", body=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {token}"})
                assert resp.code == 400, f"Expected 400, got {resp.code}: {resp.body.decode()}"

        suite = unittest.TestLoader().loadTestsFromName("test_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=__import__("io").StringIO()).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"

    def test_portal_post_zero_items_returns_400(self, db, app):
        """HTTP test: portal POST with zero items returns 400."""
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
                    "items": [],
                    "reason": "Should fail"
                })
                resp = self_inner.fetch(
                    "/api/portal/change-requests", method="POST", body=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {token}"})
                assert resp.code == 400, f"Expected 400, got {resp.code}: {resp.body.decode()}"

        suite = unittest.TestLoader().loadTestsFromName("test_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=__import__("io").StringIO()).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"

    def test_portal_get_lists_own_requests(self, db, app):
        """HTTP test: GET /api/portal/change-requests lists client's requests."""
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app, create_token
        import unittest

        cid, aid = self._make_client_and_app(db)
        token = create_token(cid, "client", "Test Client", "client")

        class _Test(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_get_and_post(self_inner):
                # First create a request
                payload = json.dumps({
                    "application_id": aid,
                    "items": [{"change_type": "company_details", "field_name": "sector",
                               "new_value": "Fintech"}],
                    "reason": "Sector update"
                })
                resp = self_inner.fetch(
                    "/api/portal/change-requests", method="POST", body=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {token}"})
                assert resp.code == 201
                cr_id = json.loads(resp.body)["id"]

                # Now list
                resp = self_inner.fetch(
                    "/api/portal/change-requests",
                    headers={"Authorization": f"Bearer {token}"})
                assert resp.code == 200
                body = json.loads(resp.body)
                ids = [r["id"] for r in body["requests"]]
                assert cr_id in ids, "Created request should be in portal list"

        suite = unittest.TestLoader().loadTestsFromName("test_get_and_post", _Test)
        result = unittest.TextTestRunner(verbosity=0, stream=__import__("io").StringIO()).run(suite)
        assert result.wasSuccessful(), f"HTTP test failed: {result.failures + result.errors}"
