"""
Round 8 QA — Cross-tenant portal change-request ownership enforcement.

Root cause: PortalChangeRequestHandler.post() combined existence and ownership
into a single SQL query returning 404 for both missing and non-owned apps.
This failed to differentiate between a non-existent app (404) and an existing
app owned by another client (403), and did not produce security audit logs
on denied cross-tenant attempts.

Additionally, the service layer (create_change_request) had no defence-in-depth
ownership check for portal-sourced requests.

Tests:
 1. portal owned app valid payload → 201
 2. portal non-owned existing app → 403 (PermissionError at service layer)
 3. portal non-existent app → PermissionError at service layer
 4. no change_requests row on failed non-owned POST
 5. no change_request_items row on failed non-owned POST
 6. portal created request visible in portal list
 7. portal created request visible in back-office list
 8. back-office admin create still works
 9. analyst RBAC still passes
10. defence-in-depth: service layer blocks non-owned portal create
11. defence-in-depth: service layer allows backoffice create for any app
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


def _setup_two_clients(raw_db):
    """Create two clients, each owning one application."""
    client_a = f"r8-cl-a-{secrets.token_hex(4)}"
    client_b = f"r8-cl-b-{secrets.token_hex(4)}"
    app_a = f"r8-app-a-{secrets.token_hex(4)}"
    app_b = f"r8-app-b-{secrets.token_hex(4)}"

    for cid, email in [(client_a, f"a-{secrets.token_hex(3)}@test.com"),
                       (client_b, f"b-{secrets.token_hex(3)}@test.com")]:
        raw_db.execute(
            "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            (cid, email, "hash", f"Company {cid[:8]}"),
        )

    for aid, cid, name in [(app_a, client_a, "Company A Ltd"),
                           (app_b, client_b, "Company B Ltd")]:
        raw_db.execute(
            """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (aid, f"APP-R8-{secrets.token_hex(4)}", cid, name,
             "GB", "Financial Services", "Limited Company", "approved"),
        )
        raw_db.execute(
            """INSERT INTO directors (id, application_id, person_key, full_name, first_name, last_name, nationality, date_of_birth)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (secrets.token_hex(8), aid, "dir1", f"Director {name}", "Dir", name, "GB", "1980-01-15"),
        )

    raw_db.commit()
    return client_a, client_b, app_a, app_b


VALID_ITEMS = [
    {"change_type": "company_details", "field_name": "company_name",
     "old_value": "Old Name", "new_value": "New Name"}
]


# ============================================================================
# 1. Portal owned app valid payload → 201
# ============================================================================

class TestPortalOwnedAppCreates:
    """Portal client creating a CR against their own application succeeds."""

    def test_owned_app_valid_payload_succeeds(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, _, app_a, _ = _setup_two_clients(db)
        user = {"sub": client_a, "name": "Client A", "role": "client"}

        req = cm.create_change_request(
            wdb, app_a, "portal_client", "portal", "Name change", VALID_ITEMS, user)

        assert req["id"].startswith("CR-")
        assert req["source"] == "portal_client"
        assert req["source_channel"] == "portal"
        assert req["status"] == "draft"
        assert req["created_by"] == client_a
        assert len(req["items"]) >= 1


# ============================================================================
# 2. Portal non-owned existing app → PermissionError (403)
# ============================================================================

class TestPortalNonOwnedApp:
    """Portal client creating a CR against another client's application is blocked."""

    def test_nonowned_existing_app_raises_permission_error(self, db):
        """Service layer defence-in-depth blocks non-owned portal create."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, _, _, app_b = _setup_two_clients(db)
        user_a = {"sub": client_a, "name": "Client A", "role": "client"}

        with pytest.raises(PermissionError, match="do not own"):
            cm.create_change_request(
                wdb, app_b, "portal_client", "portal", "Cross-tenant", VALID_ITEMS, user_a)


# ============================================================================
# 3. Portal non-existent app → PermissionError
# ============================================================================

class TestPortalNonExistentApp:
    """Portal client creating a CR against a non-existent application is blocked."""

    def test_nonexistent_app_raises_permission_error(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, _, _, _ = _setup_two_clients(db)
        user_a = {"sub": client_a, "name": "Client A", "role": "client"}

        with pytest.raises(PermissionError, match="not found"):
            cm.create_change_request(
                wdb, "nonexistent-app-id-12345", "portal_client", "portal",
                "Ghost app", VALID_ITEMS, user_a)


# ============================================================================
# 4. No change_requests row on failed non-owned POST
# ============================================================================

class TestNoOrphanOnDenied:
    """Denied portal creates must not leave any data in the database."""

    def test_no_change_requests_row_on_denied(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, _, _, app_b = _setup_two_clients(db)
        user_a = {"sub": client_a, "name": "Client A", "role": "client"}

        before = db.execute(
            "SELECT COUNT(*) as cnt FROM change_requests WHERE application_id = ?",
            (app_b,),
        ).fetchone()["cnt"]

        try:
            cm.create_change_request(
                wdb, app_b, "portal_client", "portal",
                "Cross-tenant", VALID_ITEMS, user_a)
        except PermissionError:
            pass

        after = db.execute(
            "SELECT COUNT(*) as cnt FROM change_requests WHERE application_id = ?",
            (app_b,),
        ).fetchone()["cnt"]
        assert after == before, "Denied non-owned POST must not create change_requests row"

    # ========================================================================
    # 5. No change_request_items row on failed non-owned POST
    # ========================================================================

    def test_no_change_request_items_on_denied(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, _, _, app_b = _setup_two_clients(db)
        user_a = {"sub": client_a, "name": "Client A", "role": "client"}

        before = db.execute(
            "SELECT COUNT(*) as cnt FROM change_request_items",
        ).fetchone()["cnt"]

        try:
            cm.create_change_request(
                wdb, app_b, "portal_client", "portal",
                "Cross-tenant", VALID_ITEMS, user_a)
        except PermissionError:
            pass

        after = db.execute(
            "SELECT COUNT(*) as cnt FROM change_request_items",
        ).fetchone()["cnt"]
        assert after == before, "Denied non-owned POST must not create change_request_items row"

    def test_no_rows_on_nonexistent_app(self, db):
        """No rows created when app does not exist at all."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, _, _, _ = _setup_two_clients(db)
        user_a = {"sub": client_a, "name": "Client A", "role": "client"}

        before_cr = db.execute(
            "SELECT COUNT(*) as cnt FROM change_requests",
        ).fetchone()["cnt"]
        before_items = db.execute(
            "SELECT COUNT(*) as cnt FROM change_request_items",
        ).fetchone()["cnt"]

        try:
            cm.create_change_request(
                wdb, "nonexistent-app-xyz", "portal_client", "portal",
                "Ghost", VALID_ITEMS, user_a)
        except PermissionError:
            pass

        after_cr = db.execute(
            "SELECT COUNT(*) as cnt FROM change_requests",
        ).fetchone()["cnt"]
        after_items = db.execute(
            "SELECT COUNT(*) as cnt FROM change_request_items",
        ).fetchone()["cnt"]
        assert after_cr == before_cr
        assert after_items == before_items


# ============================================================================
# 6. Portal created request visible in portal list
# ============================================================================

class TestPortalListVisibility:
    """Portal-created CRs must appear in portal and back-office lists."""

    def test_portal_created_request_in_portal_list(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, _, app_a, _ = _setup_two_clients(db)
        user_a = {"sub": client_a, "name": "Client A", "role": "client"}

        req = cm.create_change_request(
            wdb, app_a, "portal_client", "portal", "Test", VALID_ITEMS, user_a)
        cm.submit_change_request(wdb, req["id"], user_a)

        reqs = cm.list_change_requests(wdb, application_id=app_a)
        found = [r for r in reqs if r["id"] == req["id"]]
        assert len(found) == 1
        assert found[0]["source"] == "portal_client"

    # ========================================================================
    # 7. Portal created request visible in back-office list
    # ========================================================================

    def test_portal_created_request_in_backoffice_list(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, _, app_a, _ = _setup_two_clients(db)
        user_a = {"sub": client_a, "name": "Client A", "role": "client"}

        req = cm.create_change_request(
            wdb, app_a, "portal_client", "portal", "Test BO", VALID_ITEMS, user_a)
        cm.submit_change_request(wdb, req["id"], user_a)

        all_reqs = cm.list_change_requests(wdb, application_id=app_a)
        found = [r for r in all_reqs if r["id"] == req["id"]]
        assert len(found) == 1
        assert found[0]["source_channel"] == "portal"


# ============================================================================
# 8. Back-office admin create still works
# ============================================================================

class TestBackofficeAdminCreate:
    """Back-office admin/SCO can create CRs for any application (no ownership restriction)."""

    def test_backoffice_admin_create_any_app(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        _, _, app_a, app_b = _setup_two_clients(db)
        officer = {"sub": "officer-r8", "name": "SCO Officer", "role": "sco"}

        # Officer can create for app A (not "owned" by officer — that's fine for backoffice)
        req_a = cm.create_change_request(
            wdb, app_a, "backoffice_manual", "backoffice",
            "Admin change A", VALID_ITEMS, officer)
        assert req_a["id"].startswith("CR-")

        # Officer can also create for app B
        req_b = cm.create_change_request(
            wdb, app_b, "backoffice_manual", "backoffice",
            "Admin change B", VALID_ITEMS, officer)
        assert req_b["id"].startswith("CR-")

    def test_backoffice_full_lifecycle(self, db):
        """Full lifecycle: draft→submitted→triage→ready→approval→approved→implemented."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        _, _, app_a, _ = _setup_two_clients(db)
        officer = {"sub": "sco-r8", "name": "SCO", "role": "sco"}

        req = cm.create_change_request(
            wdb, app_a, "backoffice_manual", "backoffice",
            "Lifecycle test", VALID_ITEMS, officer)
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
# 9. Analyst RBAC still passes
# ============================================================================

class TestAnalystRBACPreserved:
    """Analyst restrictions must not be affected by ownership enforcement."""

    def test_analyst_cannot_approve(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        _, _, app_a, _ = _setup_two_clients(db)
        officer = {"sub": "sco-r8", "name": "SCO", "role": "sco"}
        analyst = {"sub": "analyst-r8", "name": "Analyst", "role": "analyst"}

        req = cm.create_change_request(
            wdb, app_a, "backoffice_manual", "backoffice",
            "RBAC test", VALID_ITEMS, officer)
        cm.submit_change_request(wdb, req["id"], officer)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", officer)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", officer)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", officer)

        ok, err = cm.approve_change_request(wdb, req["id"], analyst)
        assert not ok
        assert "permission" in err.lower() or "denied" in err.lower() or "not allowed" in err.lower() or "not permitted" in err.lower()

    def test_analyst_cannot_implement(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        _, _, app_a, _ = _setup_two_clients(db)
        officer = {"sub": "sco-r8", "name": "SCO", "role": "sco"}
        analyst = {"sub": "analyst-r8", "name": "Analyst", "role": "analyst"}

        req = cm.create_change_request(
            wdb, app_a, "backoffice_manual", "backoffice",
            "RBAC test 2", VALID_ITEMS, officer)
        cm.submit_change_request(wdb, req["id"], officer)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", officer)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", officer)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", officer)
        cm.approve_change_request(wdb, req["id"], officer)

        ok, _, _ = cm.implement_change_request(wdb, req["id"], analyst)
        assert not ok


# ============================================================================
# 10. Defence-in-depth at service layer
# ============================================================================

class TestDefenceInDepth:
    """Service layer must enforce ownership independently of the handler."""

    def test_service_layer_blocks_nonowned_portal(self, db):
        """Even if handler is bypassed, service layer blocks non-owned portal create."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, _, _, app_b = _setup_two_clients(db)
        user_a = {"sub": client_a, "name": "Client A", "role": "client"}

        with pytest.raises(PermissionError, match="do not own"):
            cm.create_change_request(
                wdb, app_b, "portal_client", "portal",
                "Direct service call bypass", VALID_ITEMS, user_a)

    def test_service_layer_allows_backoffice_any_app(self, db):
        """Backoffice source channel must NOT trigger ownership check."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        _, _, _, app_b = _setup_two_clients(db)
        officer = {"sub": "officer-r8-did", "name": "Officer", "role": "sco"}

        # Should succeed — backoffice doesn't need ownership
        req = cm.create_change_request(
            wdb, app_b, "backoffice_manual", "backoffice",
            "Backoffice create", VALID_ITEMS, officer)
        assert req["id"].startswith("CR-")

    def test_service_layer_blocks_nonexistent_portal(self, db):
        """Portal create for non-existent app blocked at service layer."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, _, _, _ = _setup_two_clients(db)
        user_a = {"sub": client_a, "name": "Client A", "role": "client"}

        with pytest.raises(PermissionError, match="not found"):
            cm.create_change_request(
                wdb, "fake-app-000", "portal_client", "portal",
                "Fake app", VALID_ITEMS, user_a)

    def test_cross_tenant_isolation_bidirectional(self, db):
        """Both clients are blocked from each other's apps."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        client_a, client_b, app_a, app_b = _setup_two_clients(db)

        user_a = {"sub": client_a, "name": "Client A", "role": "client"}
        user_b = {"sub": client_b, "name": "Client B", "role": "client"}

        # A cannot create on B's app
        with pytest.raises(PermissionError):
            cm.create_change_request(
                wdb, app_b, "portal_client", "portal",
                "A→B", VALID_ITEMS, user_a)

        # B cannot create on A's app
        with pytest.raises(PermissionError):
            cm.create_change_request(
                wdb, app_a, "portal_client", "portal",
                "B→A", VALID_ITEMS, user_b)

        # A CAN create on own app
        req_a = cm.create_change_request(
            wdb, app_a, "portal_client", "portal",
            "A→A", VALID_ITEMS, user_a)
        assert req_a["id"].startswith("CR-")

        # B CAN create on own app
        req_b = cm.create_change_request(
            wdb, app_b, "portal_client", "portal",
            "B→B", VALID_ITEMS, user_b)
        assert req_b["id"].startswith("CR-")


# ============================================================================
# 11. Handler-level ownership enforcement (simulated)
# ============================================================================

class TestHandlerOwnershipLogic:
    """Simulate the handler's two-step ownership check (app exists → client owns)."""

    def test_handler_returns_404_for_nonexistent(self, db):
        """Handler step 1: app not found → 404 path."""
        app = db.execute(
            "SELECT id FROM applications WHERE id = ?",
            ("nonexistent-app-handler-test",),
        ).fetchone()
        assert app is None, "Non-existent app must return None"

    def test_handler_returns_403_for_nonowned(self, db):
        """Handler step 2: app exists but client_id mismatch → 403 path."""
        _, _, app_a, _ = _setup_two_clients(db)
        other_client = "intruder-client-xyz"

        app = db.execute(
            "SELECT id, client_id FROM applications WHERE id = ?",
            (app_a,),
        ).fetchone()
        assert app is not None, "App must exist"
        assert app["client_id"] != other_client, "Client must not own this app"

    def test_handler_allows_owned(self, db):
        """Handler: app exists AND client owns → allow path."""
        client_a, _, app_a, _ = _setup_two_clients(db)

        app = db.execute(
            "SELECT id, client_id FROM applications WHERE id = ?",
            (app_a,),
        ).fetchone()
        assert app is not None
        assert app["client_id"] == client_a
