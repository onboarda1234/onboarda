"""
Round 10 QA — Portal Change Request cross-tenant ownership enforcement.

Uses the EXACT client/application IDs specified by the retest:

  Client:        21eb50f952e54634
  Owned app:     4428154d80e1474f
  Non-owned app: aa8817ef78484777

Tests:
 1. Owned existing app       → HTTP 201
 2. Non-owned existing app   → HTTP 403
 3. Non-existent app         → HTTP 404
 4. Non-owned creates zero change_requests rows
 5. Non-owned creates zero change_request_items rows
 6. Portal happy path visible in portal list
 7. Portal happy path visible in back-office list
"""

import json
import os
import sys
import sqlite3
import unittest
import io

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

# ── Fixed IDs from the retest spec ──
CLIENT_ID = "21eb50f952e54634"
OWNED_APP_ID = "4428154d80e1474f"
NON_OWNED_APP_ID = "aa8817ef78484777"
OTHER_CLIENT_ID = "r10_other_client_bb"


# ── Helpers ──

def _seed_r10_data(raw_db):
    """Insert the two clients and two applications with the exact retest IDs."""
    for cid, email, company in [
        (CLIENT_ID, "r10owner@test.com", "R10 Owner Corp"),
        (OTHER_CLIENT_ID, "r10other@test.com", "R10 Other Corp"),
    ]:
        raw_db.execute(
            "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) "
            "VALUES (?, ?, ?, ?)",
            (cid, email, "hash", company),
        )

    for aid, cid, name in [
        (OWNED_APP_ID, CLIENT_ID, "R10 Owner App"),
        (NON_OWNED_APP_ID, OTHER_CLIENT_ID, "R10 Other App"),
    ]:
        raw_db.execute(
            "INSERT OR IGNORE INTO applications "
            "(id, ref, client_id, company_name, country, sector, entity_type, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, f"APP-R10-{aid[:8]}", cid, name, "MU", "Tech", "SME", "approved"),
        )

    # Director row needed for profile-version support
    raw_db.execute(
        "INSERT OR IGNORE INTO directors "
        "(id, application_id, person_key, full_name, first_name, last_name, "
        "nationality, date_of_birth) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("dir-r10-4428154d", OWNED_APP_ID, "dir1",
         "R10 Director", "R10", "Director", "MU", "1985-03-10"),
    )
    raw_db.commit()


# ============================================================================
# Unit-level tests (direct service-layer via create_change_request)
# ============================================================================

class _DBWrapper:
    """Minimal wrapper so raw sqlite3.Connection matches cm expectations."""
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


class TestR10PortalOwnershipUnit:
    """Unit tests exercising the defence-in-depth ownership guard in
    create_change_request() with the exact retest IDs."""

    # --- Test 4: non-owned app creates zero change_requests ----------------
    def test_nonowned_creates_zero_change_requests(self, db):
        """POST as 21eb50f952e54634 targeting aa8817ef78484777 must NOT
        insert any change_requests row."""
        import change_management as cm
        _seed_r10_data(db)
        wdb = _DBWrapper(db)

        before = db.execute(
            "SELECT COUNT(*) AS cnt FROM change_requests"
        ).fetchone()["cnt"]

        user = {"sub": CLIENT_ID, "name": "Intruder", "role": "client"}
        items = [{"change_type": "company_details",
                  "field_name": "company_name", "new_value": "Hijacked"}]

        with pytest.raises(PermissionError):
            cm.create_change_request(
                wdb, NON_OWNED_APP_ID, "portal_client", "portal",
                "R10 cross-tenant attempt", items, user,
            )

        after = db.execute(
            "SELECT COUNT(*) AS cnt FROM change_requests"
        ).fetchone()["cnt"]
        assert after == before, (
            f"change_requests count changed from {before} to {after} "
            "on a denied cross-tenant attempt"
        )

    # --- Test 5: non-owned app creates zero change_request_items -----------
    def test_nonowned_creates_zero_change_request_items(self, db):
        """POST as 21eb50f952e54634 targeting aa8817ef78484777 must NOT
        insert any change_request_items row."""
        import change_management as cm
        _seed_r10_data(db)
        wdb = _DBWrapper(db)

        before = db.execute(
            "SELECT COUNT(*) AS cnt FROM change_request_items"
        ).fetchone()["cnt"]

        user = {"sub": CLIENT_ID, "name": "Intruder", "role": "client"}
        items = [{"change_type": "company_details",
                  "field_name": "company_name", "new_value": "Hijacked"}]

        with pytest.raises(PermissionError):
            cm.create_change_request(
                wdb, NON_OWNED_APP_ID, "portal_client", "portal",
                "R10 cross-tenant attempt", items, user,
            )

        after = db.execute(
            "SELECT COUNT(*) AS cnt FROM change_request_items"
        ).fetchone()["cnt"]
        assert after == before, (
            f"change_request_items count changed from {before} to {after} "
            "on a denied cross-tenant attempt"
        )


# ============================================================================
# HTTP-level tests (full Tornado AsyncHTTPTestCase)
# ============================================================================

class TestR10PortalOwnershipHTTP:
    """Full HTTP integration tests using the EXACT retest IDs through
    the PortalChangeRequestHandler endpoint."""

    # --- Test 1: owned existing app → 201 ---------------------------------
    def test_owned_app_returns_201(self, db, app):
        """POST /api/portal/change-requests as 21eb50f952e54634 with
        application_id 4428154d80e1474f → 201."""
        from server import make_app, create_token
        from tornado.testing import AsyncHTTPTestCase

        _seed_r10_data(db)
        token = create_token(CLIENT_ID, "client", "R10 Owner", "client")

        class _Test(unittest.TestCase):
            def runTest(_self):
                class _App(AsyncHTTPTestCase):
                    def get_app(inner):
                        return make_app()

                    def test_post(inner):
                        payload = json.dumps({
                            "application_id": OWNED_APP_ID,
                            "items": [{"change_type": "company_details",
                                       "field_name": "company_name",
                                       "new_value": "Updated R10 Corp"}],
                            "reason": "R10 owned test",
                        })
                        resp = inner.fetch(
                            "/api/portal/change-requests", method="POST",
                            body=payload,
                            headers={"Content-Type": "application/json",
                                     "Authorization": f"Bearer {token}"})
                        assert resp.code == 201, (
                            f"Expected 201, got {resp.code}: {resp.body.decode()}")
                        body = json.loads(resp.body)
                        assert body["id"].startswith("CR-"), (
                            f"Expected CR- prefixed id, got {body['id']}")

                suite = unittest.TestLoader().loadTestsFromName(
                    "test_post", _App)
                result = unittest.TextTestRunner(
                    verbosity=0, stream=io.StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP owned-app test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()

    # --- Test 2: non-owned existing app → 403 -----------------------------
    def test_nonowned_app_returns_403(self, db, app):
        """POST /api/portal/change-requests as 21eb50f952e54634 with
        application_id aa8817ef78484777 → 403."""
        from server import make_app, create_token
        from tornado.testing import AsyncHTTPTestCase

        _seed_r10_data(db)
        token = create_token(CLIENT_ID, "client", "R10 Owner", "client")

        class _Test(unittest.TestCase):
            def runTest(_self):
                class _App(AsyncHTTPTestCase):
                    def get_app(inner):
                        return make_app()

                    def test_post(inner):
                        payload = json.dumps({
                            "application_id": NON_OWNED_APP_ID,
                            "items": [{"change_type": "company_details",
                                       "field_name": "company_name",
                                       "new_value": "Hijacked R10"}],
                            "reason": "R10 cross-tenant attempt",
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
                    verbosity=0, stream=io.StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP non-owned test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()

    # --- Test 3: non-existent app → 404 -----------------------------------
    def test_nonexistent_app_returns_404(self, db, app):
        """POST with a fabricated application_id → 404."""
        from server import make_app, create_token
        from tornado.testing import AsyncHTTPTestCase

        _seed_r10_data(db)
        token = create_token(CLIENT_ID, "client", "R10 Owner", "client")
        fake_aid = "doesnotexist_r10_000000"

        class _Test(unittest.TestCase):
            def runTest(_self):
                class _App(AsyncHTTPTestCase):
                    def get_app(inner):
                        return make_app()

                    def test_post(inner):
                        payload = json.dumps({
                            "application_id": fake_aid,
                            "items": [{"change_type": "company_details",
                                       "field_name": "company_name",
                                       "new_value": "Ghost R10"}],
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
                    verbosity=0, stream=io.StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP non-existent test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()

    # --- Test 6: portal happy path visible in portal list -----------------
    def test_happy_path_visible_in_portal_list(self, db, app):
        """Created CR for owned app is returned by
        GET /api/portal/change-requests."""
        from server import make_app, create_token
        from tornado.testing import AsyncHTTPTestCase

        _seed_r10_data(db)
        token = create_token(CLIENT_ID, "client", "R10 Owner", "client")

        class _Test(unittest.TestCase):
            def runTest(_self):
                class _App(AsyncHTTPTestCase):
                    def get_app(inner):
                        return make_app()

                    def test_portal_list(inner):
                        # Create via portal
                        payload = json.dumps({
                            "application_id": OWNED_APP_ID,
                            "items": [{"change_type": "company_details",
                                       "field_name": "sector",
                                       "new_value": "Fintech R10"}],
                            "reason": "R10 portal list test",
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
                    verbosity=0, stream=io.StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP portal-list test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()

    # --- Test 7: portal happy path visible in back-office list ------------
    def test_happy_path_visible_in_backoffice_list(self, db, app):
        """Created CR for owned app is returned by
        GET /api/change-management/requests (back-office)."""
        from server import make_app, create_token
        from tornado.testing import AsyncHTTPTestCase

        _seed_r10_data(db)
        portal_token = create_token(CLIENT_ID, "client", "R10 Owner", "client")
        officer_token = create_token("admin001", "admin", "Admin", "officer")

        class _Test(unittest.TestCase):
            def runTest(_self):
                class _App(AsyncHTTPTestCase):
                    def get_app(inner):
                        return make_app()

                    def test_backoffice_list(inner):
                        # Create via portal
                        payload = json.dumps({
                            "application_id": OWNED_APP_ID,
                            "items": [{"change_type": "company_details",
                                       "field_name": "sector",
                                       "new_value": "Insurance R10"}],
                            "reason": "R10 backoffice list test",
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
                            f"?application_id={OWNED_APP_ID}",
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
                    verbosity=0, stream=io.StringIO()).run(suite)
                assert result.wasSuccessful(), (
                    f"HTTP backoffice-list test failed: "
                    f"{result.failures + result.errors}")

        _Test().runTest()
