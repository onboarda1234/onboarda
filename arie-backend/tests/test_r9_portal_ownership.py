"""
Round 9 QA — Portal Change Request cross-tenant ownership enforcement.

Confirms the ownership guard in PortalChangeRequestHandler.post blocks
cross-tenant change-request creation.  Uses the exact same ownership
predicate as GET /api/portal/applications (WHERE client_id = ?).

Tests:
 1. portal client owned existing app → 201
 2. portal client existing non-owned app → 403
 3. portal client non-existent app → 404
 4. non-owned attempt creates zero change_requests
 5. non-owned attempt creates zero change_request_items
 6. denied ownership attempt is audit/security logged
 7. portal happy path still visible in portal list
 8. portal happy path still visible in back-office list
"""

import json
import os
import sys
import secrets
import sqlite3
import unittest
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


def _setup_two_clients(raw_db):
    """Create two clients, each with their own application.

    Returns (owner_cid, owner_aid, other_cid, other_aid).
    """
    owner_cid = f"r9-owner-{secrets.token_hex(4)}"
    owner_aid = f"r9-ownapp-{secrets.token_hex(4)}"
    other_cid = f"r9-other-{secrets.token_hex(4)}"
    other_aid = f"r9-othapp-{secrets.token_hex(4)}"

    for cid in (owner_cid, other_cid):
        raw_db.execute(
            "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            (cid, f"{cid}@test.com", "hash", f"R9 Co {cid[:8]}"),
        )

    for aid, cid, name in [
        (owner_aid, owner_cid, "Owner Corp"),
        (other_aid, other_cid, "Other Corp"),
    ]:
        raw_db.execute(
            """INSERT INTO applications
               (id, ref, client_id, company_name, country, sector, entity_type, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (aid, f"APP-{secrets.token_hex(4)}", cid, name,
             "MU", "Tech", "SME", "approved"),
        )

    # Need a director for profile-version support
    raw_db.execute(
        """INSERT INTO directors
           (id, application_id, person_key, full_name, first_name, last_name,
            nationality, date_of_birth)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), owner_aid, "dir1", "R9 Director",
         "R9", "Director", "MU", "1985-03-10"),
    )

    raw_db.commit()
    return owner_cid, owner_aid, other_cid, other_aid


# ============================================================================
# Unit-level tests (direct DB / service-layer)
# ============================================================================

class TestPortalOwnershipUnit:
    """Unit tests exercising the ownership guard via create_change_request."""

    # --- Test 4: non-owned attempt creates zero change_requests -----------
    def test_nonowned_creates_zero_change_requests(self, db):
        """Non-owned portal attempt must not insert any change_requests row."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        owner_cid, owner_aid, other_cid, other_aid = _setup_two_clients(db)

        before = db.execute(
            "SELECT COUNT(*) AS cnt FROM change_requests"
        ).fetchone()["cnt"]

        # Attempt as other_cid against owner_aid
        user = {"sub": other_cid, "name": "Intruder", "role": "client"}
        items = [{"change_type": "company_details", "field_name": "company_name",
                  "new_value": "Hijacked"}]

        with pytest.raises(PermissionError):
            cm.create_change_request(
                wdb, owner_aid, "portal_client", "portal",
                "Cross-tenant attempt", items, user,
            )

        after = db.execute(
            "SELECT COUNT(*) AS cnt FROM change_requests"
        ).fetchone()["cnt"]
        assert after == before, (
            f"change_requests count changed from {before} to {after} "
            "on a denied cross-tenant attempt"
        )

    # --- Test 5: non-owned attempt creates zero change_request_items ------
    def test_nonowned_creates_zero_change_request_items(self, db):
        """Non-owned portal attempt must not insert any change_request_items."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        owner_cid, owner_aid, other_cid, other_aid = _setup_two_clients(db)

        before = db.execute(
            "SELECT COUNT(*) AS cnt FROM change_request_items"
        ).fetchone()["cnt"]

        user = {"sub": other_cid, "name": "Intruder", "role": "client"}
        items = [{"change_type": "company_details", "field_name": "company_name",
                  "new_value": "Hijacked"}]

        with pytest.raises(PermissionError):
            cm.create_change_request(
                wdb, owner_aid, "portal_client", "portal",
                "Cross-tenant attempt", items, user,
            )

        after = db.execute(
            "SELECT COUNT(*) AS cnt FROM change_request_items"
        ).fetchone()["cnt"]
        assert after == before, (
            f"change_request_items count changed from {before} to {after} "
            "on a denied cross-tenant attempt"
        )


# ============================================================================
# HTTP-level tests using Tornado test client
# ============================================================================

class TestPortalOwnershipHTTP:
    """Full HTTP integration tests through the Tornado endpoint."""

    def _make_two_clients(self, db):
        """Set up two independent clients + apps for HTTP tests."""
        return _setup_two_clients(db)

    # --- Test 1: portal client owned existing app → 201 -------------------
    def test_owned_app_returns_201(self, db, app):
        """POST with owned application_id returns 201."""
        from server import make_app, create_token

        owner_cid, owner_aid, _, _ = self._make_two_clients(db)
        token = create_token(owner_cid, "client", "Owner", "client")

        class _Test(unittest.TestCase):
            def runTest(_self):
                from tornado.testing import AsyncHTTPTestCase

                class _App(AsyncHTTPTestCase):
                    def get_app(inner): return make_app()
                    def test_post(inner):
                        payload = json.dumps({
                            "application_id": owner_aid,
                            "items": [{"change_type": "company_details",
                                       "field_name": "company_name",
                                       "new_value": "Updated Corp"}],
                            "reason": "R9 owned test",
                        })
                        resp = inner.fetch(
                            "/api/portal/change-requests", method="POST",
                            body=payload,
                            headers={"Content-Type": "application/json",
                                     "Authorization": f"Bearer {token}"})
                        assert resp.code == 201, (
                            f"Expected 201, got {resp.code}: "
                            f"{resp.body.decode()}")
                        body = json.loads(resp.body)
                        assert body["id"].startswith("CR-")

                suite = unittest.TestLoader().loadTestsFromName(
                    "test_post", _App)
                result = unittest.TextTestRunner(
                    verbosity=0,
                    stream=__import__("io").StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP owned-app test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()

    # --- Test 2: portal client existing non-owned app → 403 ---------------
    def test_nonowned_app_returns_403(self, db, app):
        """POST with another client's application_id returns exactly 403."""
        from server import make_app, create_token

        owner_cid, owner_aid, other_cid, _ = self._make_two_clients(db)
        # Authenticate as other_cid but target owner_aid
        token = create_token(other_cid, "client", "Intruder", "client")

        class _Test(unittest.TestCase):
            def runTest(_self):
                from tornado.testing import AsyncHTTPTestCase

                class _App(AsyncHTTPTestCase):
                    def get_app(inner): return make_app()
                    def test_post(inner):
                        payload = json.dumps({
                            "application_id": owner_aid,
                            "items": [{"change_type": "company_details",
                                       "field_name": "company_name",
                                       "new_value": "Hijacked"}],
                            "reason": "Cross-tenant attempt",
                        })
                        resp = inner.fetch(
                            "/api/portal/change-requests", method="POST",
                            body=payload,
                            headers={"Content-Type": "application/json",
                                     "Authorization": f"Bearer {token}"})
                        assert resp.code == 403, (
                            f"Expected 403 for non-owned app, got {resp.code}: "
                            f"{resp.body.decode()}")

                suite = unittest.TestLoader().loadTestsFromName(
                    "test_post", _App)
                result = unittest.TextTestRunner(
                    verbosity=0,
                    stream=__import__("io").StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP non-owned test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()

    # --- Test 3: portal client non-existent app → 404 ---------------------
    def test_nonexistent_app_returns_404(self, db, app):
        """POST with a non-existent application_id returns 404."""
        from server import make_app, create_token

        owner_cid, _, _, _ = self._make_two_clients(db)
        token = create_token(owner_cid, "client", "Owner", "client")
        fake_aid = "doesnotexist_r9_000000"

        class _Test(unittest.TestCase):
            def runTest(_self):
                from tornado.testing import AsyncHTTPTestCase

                class _App(AsyncHTTPTestCase):
                    def get_app(inner): return make_app()
                    def test_post(inner):
                        payload = json.dumps({
                            "application_id": fake_aid,
                            "items": [{"change_type": "company_details",
                                       "field_name": "company_name",
                                       "new_value": "Ghost"}],
                        })
                        resp = inner.fetch(
                            "/api/portal/change-requests", method="POST",
                            body=payload,
                            headers={"Content-Type": "application/json",
                                     "Authorization": f"Bearer {token}"})
                        assert resp.code == 404, (
                            f"Expected 404 for non-existent app, got {resp.code}: "
                            f"{resp.body.decode()}")

                suite = unittest.TestLoader().loadTestsFromName(
                    "test_post", _App)
                result = unittest.TextTestRunner(
                    verbosity=0,
                    stream=__import__("io").StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP non-existent test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()

    # --- Test 6: denied ownership attempt is audit logged -----------------
    def test_denied_ownership_is_audit_logged(self, db, app):
        """Cross-tenant POST must write portal_cr_denied_not_owner to audit_log."""
        from server import make_app, create_token

        owner_cid, owner_aid, other_cid, _ = self._make_two_clients(db)
        token = create_token(other_cid, "client", "Intruder", "client")

        class _Test(unittest.TestCase):
            def runTest(_self):
                from tornado.testing import AsyncHTTPTestCase

                class _App(AsyncHTTPTestCase):
                    def get_app(inner): return make_app()
                    def test_audit(inner):
                        payload = json.dumps({
                            "application_id": owner_aid,
                            "items": [{"change_type": "company_details",
                                       "field_name": "company_name",
                                       "new_value": "Hijacked"}],
                        })
                        resp = inner.fetch(
                            "/api/portal/change-requests", method="POST",
                            body=payload,
                            headers={"Content-Type": "application/json",
                                     "Authorization": f"Bearer {token}"})
                        assert resp.code == 403

                        # Check audit_log for the denial event
                        from db import get_db
                        check_db = get_db()
                        try:
                            row = check_db.execute(
                                "SELECT action, detail FROM audit_log "
                                "WHERE action = ? AND target = ? "
                                "ORDER BY rowid DESC LIMIT 1",
                                ("portal_cr_denied_not_owner", owner_aid),
                            ).fetchone()
                            assert row is not None, (
                                "Expected audit_log entry with action "
                                "'portal_cr_denied_not_owner' not found")
                            detail = json.loads(row["detail"])
                            assert detail["reason"] == "not_owner"
                            assert detail["client_id"] == other_cid
                            assert detail["attempted_application_id"] == owner_aid
                        finally:
                            check_db.close()

                suite = unittest.TestLoader().loadTestsFromName(
                    "test_audit", _App)
                result = unittest.TextTestRunner(
                    verbosity=0,
                    stream=__import__("io").StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP audit test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()

    # --- Test 7: portal happy path still visible in portal list -----------
    def test_happy_path_visible_in_portal_list(self, db, app):
        """Created CR is returned by GET /api/portal/change-requests."""
        from server import make_app, create_token

        owner_cid, owner_aid, _, _ = self._make_two_clients(db)
        token = create_token(owner_cid, "client", "Owner", "client")

        class _Test(unittest.TestCase):
            def runTest(_self):
                from tornado.testing import AsyncHTTPTestCase

                class _App(AsyncHTTPTestCase):
                    def get_app(inner): return make_app()
                    def test_portal_list(inner):
                        # Create
                        payload = json.dumps({
                            "application_id": owner_aid,
                            "items": [{"change_type": "company_details",
                                       "field_name": "sector",
                                       "new_value": "Fintech"}],
                            "reason": "R9 portal list test",
                        })
                        resp = inner.fetch(
                            "/api/portal/change-requests", method="POST",
                            body=payload,
                            headers={"Content-Type": "application/json",
                                     "Authorization": f"Bearer {token}"})
                        assert resp.code == 201
                        cr_id = json.loads(resp.body)["id"]

                        # List
                        resp = inner.fetch(
                            "/api/portal/change-requests",
                            headers={"Authorization": f"Bearer {token}"})
                        assert resp.code == 200
                        body = json.loads(resp.body)
                        ids = [r["id"] for r in body["requests"]]
                        assert cr_id in ids, (
                            f"CR {cr_id} not found in portal list")

                suite = unittest.TestLoader().loadTestsFromName(
                    "test_portal_list", _App)
                result = unittest.TextTestRunner(
                    verbosity=0,
                    stream=__import__("io").StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP portal-list test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()

    # --- Test 8: portal happy path still visible in back-office list ------
    def test_happy_path_visible_in_backoffice_list(self, db, app):
        """Created CR is returned by GET /api/change-requests (back office)."""
        from server import make_app, create_token

        owner_cid, owner_aid, _, _ = self._make_two_clients(db)
        portal_token = create_token(owner_cid, "client", "Owner", "client")
        officer_token = create_token("admin001", "admin", "Admin", "officer")

        class _Test(unittest.TestCase):
            def runTest(_self):
                from tornado.testing import AsyncHTTPTestCase

                class _App(AsyncHTTPTestCase):
                    def get_app(inner): return make_app()
                    def test_backoffice_list(inner):
                        # Create via portal
                        payload = json.dumps({
                            "application_id": owner_aid,
                            "items": [{"change_type": "company_details",
                                       "field_name": "sector",
                                       "new_value": "Insurance"}],
                            "reason": "R9 backoffice list test",
                        })
                        resp = inner.fetch(
                            "/api/portal/change-requests", method="POST",
                            body=payload,
                            headers={"Content-Type": "application/json",
                                     "Authorization":
                                         f"Bearer {portal_token}"})
                        assert resp.code == 201
                        cr_id = json.loads(resp.body)["id"]

                        # List via back-office
                        resp = inner.fetch(
                            f"/api/change-management/requests"
                            f"?application_id={owner_aid}",
                            headers={"Authorization":
                                         f"Bearer {officer_token}"})
                        assert resp.code == 200
                        body = json.loads(resp.body)
                        ids = [r["id"] for r in body["requests"]]
                        assert cr_id in ids, (
                            f"CR {cr_id} not found in backoffice list")

                suite = unittest.TestLoader().loadTestsFromName(
                    "test_backoffice_list", _App)
                result = unittest.TextTestRunner(
                    verbosity=0,
                    stream=__import__("io").StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP backoffice-list test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()
