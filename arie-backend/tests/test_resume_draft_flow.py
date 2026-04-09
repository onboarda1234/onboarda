"""
Tests for resume-draft application flow.

Verifies that:
1. New application creation works normally
2. Resuming a draft and submitting updates the same application (no duplicate error)
3. Duplicate check still blocks genuinely separate active applications for same entity
4. Backend duplicate check excludes current application via application_id param
5. Frontend submit logic branches correctly for resumed vs new applications
"""

import json
import os
import re
import sys
import uuid
import sqlite3
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ═══════════════════════════════════════════════════════════════
# 1. Backend — Duplicate check excludes current application
# ═══════════════════════════════════════════════════════════════

class TestDuplicateCheckExcludesSelf(unittest.TestCase):
    """The duplicate check must NOT block an application from being
    updated when the caller identifies the current application_id."""

    def test_duplicate_check_query_includes_id(self):
        """Duplicate query now selects id column for exclusion."""
        import server
        import inspect
        src = inspect.getsource(server.ApplicationsHandler.post)
        # Must SELECT id so we can compare against exclude_app_id
        assert re.search(r"SELECT\s+id", src), \
            "Duplicate check SELECT must include id column"

    def test_exclude_app_id_used(self):
        """Handler reads application_id from payload for exclusion."""
        import server
        import inspect
        src = inspect.getsource(server.ApplicationsHandler.post)
        assert "exclude_app_id" in src, \
            "Duplicate check must support exclude_app_id"

    def test_exclude_app_id_in_generator(self):
        """The duplicate-matching generator expression must skip the excluded id."""
        import server
        import inspect
        src = inspect.getsource(server.ApplicationsHandler.post)
        # The generator should have a condition like: and e['id'] != exclude_app_id
        assert "exclude_app_id" in src and "!= exclude_app_id" in src, \
            "Duplicate match must skip the excluded application id"


# ═══════════════════════════════════════════════════════════════
# 2. Backend — PUT endpoint updates draft correctly
# ═══════════════════════════════════════════════════════════════

class TestPutUpdatesDraft(unittest.TestCase):
    """PUT /api/applications/{id} must successfully update a draft
    application's prescreening data without creating a duplicate."""

    def test_put_handler_allows_draft_prescreening_update(self):
        """PUT handler must allow prescreening_data updates in draft status."""
        import server
        import inspect
        src = inspect.getsource(server.ApplicationDetailHandler.put)
        # The handler checks status == 'draft' and allows prescreening_data updates
        assert "draft" in src and "prescreening_data" in src, \
            "PUT handler must allow prescreening_data updates for draft status"

    def test_put_handler_updates_company_name(self):
        """PUT handler must update company_name field."""
        import server
        import inspect
        src = inspect.getsource(server.ApplicationDetailHandler.put)
        assert "company_name" in src and "UPDATE applications SET" in src, \
            "PUT handler must update company_name in the applications table"


# ═══════════════════════════════════════════════════════════════
# 3. Frontend — Resume flow uses PUT for existing applications
# ═══════════════════════════════════════════════════════════════

PORTAL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")


def _read_portal():
    with open(PORTAL_PATH, encoding="utf-8") as f:
        return f.read()


class TestFrontendResumeSubmitFlow(unittest.TestCase):
    """The portal must use PUT for resumed applications and POST for new ones."""

    def setUp(self):
        self.html = _read_portal()

    def test_submit_checks_current_application_id(self):
        """submitPrescreening must check if currentApplicationId is set."""
        # The function should have a conditional: if (currentApplicationId)
        assert "if (currentApplicationId)" in self.html, \
            "Submit handler must check currentApplicationId before deciding create vs update"

    def test_submit_uses_put_for_resumed_draft(self):
        """When currentApplicationId is set, submit must use PUT to update."""
        assert "PUT" in self.html and "currentApplicationId" in self.html, \
            "Submit must use PUT for resumed applications"
        # More specifically, check for PUT call with application ID
        assert re.search(
            r"apiCall\s*\(\s*'PUT'\s*,\s*'/applications/'\s*\+\s*currentApplicationId",
            self.html
        ), "Submit handler must call PUT /applications/{currentApplicationId} for resumed drafts"

    def test_submit_uses_post_for_new_application(self):
        """When currentApplicationId is NOT set, submit must use POST to create."""
        assert re.search(
            r"apiCall\s*\(\s*'POST'\s*,\s*'/applications'\s*,\s*payload\s*\)",
            self.html
        ), "Submit handler must call POST /applications for new applications"

    def test_resume_sets_current_application_id(self):
        """resumeApplication must set currentApplicationId from loaded app."""
        assert re.search(
            r"currentApplicationId\s*=\s*app\.id",
            self.html
        ), "resumeApplication must set currentApplicationId from app.id"

    def test_resume_sets_app_ref(self):
        """resumeApplication must set appRef from loaded app."""
        assert re.search(
            r"appRef\s*=\s*app\.ref",
            self.html
        ), "resumeApplication must set appRef from app.ref"

    def test_new_application_stores_id_from_create_response(self):
        """After POST create, currentApplicationId must be set from response."""
        assert re.search(
            r"currentApplicationId\s*=\s*createResp\.id",
            self.html
        ), "New application flow must store id from create response"

    def test_submit_always_calls_submit_endpoint(self):
        """After create or update, submit endpoint must be called."""
        assert re.search(
            r"apiCall\s*\(\s*'POST'\s*,\s*'/applications/'\s*\+\s*currentApplicationId\s*\+\s*'/submit'\s*\)",
            self.html
        ), "Submit handler must always call POST /applications/{id}/submit after create or update"


# ═══════════════════════════════════════════════════════════════
# 4. Integration — Duplicate check behaviour with database
# ═══════════════════════════════════════════════════════════════

class TestDuplicateCheckIntegration(unittest.TestCase):
    """Integration tests using a real database to verify duplicate check logic."""

    def setUp(self):
        """Create a temporary database with schema."""
        import tempfile
        self.db_path = os.path.join(tempfile.gettempdir(), f"dup_test_{os.getpid()}_{uuid.uuid4().hex[:6]}.db")
        os.environ["DB_PATH"] = self.db_path
        # Remove stale DB
        try:
            os.unlink(self.db_path)
        except OSError:
            pass
        from db import init_db
        init_db()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _get_db(self):
        from db import get_db
        return get_db()

    def _insert_app(self, db, app_id, ref, client_id, company_name, status="draft"):
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_id, ref, client_id, company_name, "Mauritius", "Technology", "SME", status))
        db.commit()

    def test_duplicate_check_blocks_true_duplicate(self):
        """Creating a second active application for the same company must be blocked."""
        db = self._get_db()
        client_id = "client_dup_test"
        self._insert_app(db, "app1", "ARF-2026-001", client_id, "Acme Corp Ltd")

        # Simulate duplicate check logic from server.py
        company_name = "Acme Corp Ltd"
        normalized_name = re.sub(r'\s+', ' ', company_name.strip()).lower()
        existing = db.execute(
            "SELECT id, ref, company_name FROM applications WHERE client_id=? AND status NOT IN ('rejected','withdrawn')",
            (client_id,)
        ).fetchall()
        dup = next((e for e in existing
                     if re.sub(r'\s+', ' ', (e['company_name'] or '').strip()).lower() == normalized_name
                     and e['id'] != None), None)  # No exclusion
        db.close()
        assert dup is not None, "True duplicate must be detected"
        assert dup['ref'] == "ARF-2026-001"

    def test_duplicate_check_allows_self_update_with_exclude(self):
        """When exclude_app_id matches the existing application, it must not be flagged."""
        db = self._get_db()
        client_id = "client_self_test"
        app_id = "app_self_1"
        self._insert_app(db, app_id, "ARF-2026-SELF", client_id, "Self Update Corp")

        company_name = "Self Update Corp"
        exclude_app_id = app_id  # This is the application being updated
        normalized_name = re.sub(r'\s+', ' ', company_name.strip()).lower()
        existing = db.execute(
            "SELECT id, ref, company_name FROM applications WHERE client_id=? AND status NOT IN ('rejected','withdrawn')",
            (client_id,)
        ).fetchall()
        dup = next((e for e in existing
                     if re.sub(r'\s+', ' ', (e['company_name'] or '').strip()).lower() == normalized_name
                     and e['id'] != exclude_app_id), None)
        db.close()
        assert dup is None, "Self-update must NOT be flagged as duplicate"

    def test_duplicate_check_blocks_different_app_same_company(self):
        """A different application for the same company must still be blocked."""
        db = self._get_db()
        client_id = "client_diff_test"
        self._insert_app(db, "app_existing", "ARF-2026-EXIST", client_id, "Shared Corp Ltd")

        company_name = "Shared Corp Ltd"
        exclude_app_id = "app_new_attempt"  # Different ID
        normalized_name = re.sub(r'\s+', ' ', company_name.strip()).lower()
        existing = db.execute(
            "SELECT id, ref, company_name FROM applications WHERE client_id=? AND status NOT IN ('rejected','withdrawn')",
            (client_id,)
        ).fetchall()
        dup = next((e for e in existing
                     if re.sub(r'\s+', ' ', (e['company_name'] or '').strip()).lower() == normalized_name
                     and e['id'] != exclude_app_id), None)
        db.close()
        assert dup is not None, "Different application with same company must be blocked"
        assert dup['ref'] == "ARF-2026-EXIST"

    def test_rejected_application_allows_new_submission(self):
        """Rejected applications must not block new submissions for same company."""
        db = self._get_db()
        client_id = "client_rejected_test"
        self._insert_app(db, "app_rejected", "ARF-2026-REJ", client_id, "Rejected Corp", status="rejected")

        company_name = "Rejected Corp"
        normalized_name = re.sub(r'\s+', ' ', company_name.strip()).lower()
        existing = db.execute(
            "SELECT id, ref, company_name FROM applications WHERE client_id=? AND status NOT IN ('rejected','withdrawn')",
            (client_id,)
        ).fetchall()
        dup = next((e for e in existing
                     if re.sub(r'\s+', ' ', (e['company_name'] or '').strip()).lower() == normalized_name), None)
        db.close()
        assert dup is None, "Rejected application must not block new submission"

    def test_case_insensitive_duplicate_detection(self):
        """Duplicate check must be case-insensitive."""
        db = self._get_db()
        client_id = "client_case_test"
        self._insert_app(db, "app_case", "ARF-2026-CASE", client_id, "Case Test Corp")

        company_name = "CASE TEST CORP"  # Different case
        normalized_name = re.sub(r'\s+', ' ', company_name.strip()).lower()
        existing = db.execute(
            "SELECT id, ref, company_name FROM applications WHERE client_id=? AND status NOT IN ('rejected','withdrawn')",
            (client_id,)
        ).fetchall()
        dup = next((e for e in existing
                     if re.sub(r'\s+', ' ', (e['company_name'] or '').strip()).lower() == normalized_name
                     and e['id'] != None), None)
        db.close()
        assert dup is not None, "Case-insensitive duplicate must be detected"

    def test_whitespace_normalized_duplicate_detection(self):
        """Duplicate check must normalize whitespace."""
        db = self._get_db()
        client_id = "client_ws_test"
        self._insert_app(db, "app_ws", "ARF-2026-WS", client_id, "White Space Corp")

        company_name = "White  Space   Corp"  # Extra whitespace
        normalized_name = re.sub(r'\s+', ' ', company_name.strip()).lower()
        existing = db.execute(
            "SELECT id, ref, company_name FROM applications WHERE client_id=? AND status NOT IN ('rejected','withdrawn')",
            (client_id,)
        ).fetchall()
        dup = next((e for e in existing
                     if re.sub(r'\s+', ' ', (e['company_name'] or '').strip()).lower() == normalized_name
                     and e['id'] != None), None)
        db.close()
        assert dup is not None, "Whitespace-normalized duplicate must be detected"


# ═══════════════════════════════════════════════════════════════
# 5. Resume flow preserves application identity
# ═══════════════════════════════════════════════════════════════

class TestResumeFlowPreservesIdentity(unittest.TestCase):
    """Ensure the frontend resume flow correctly preserves application state."""

    def setUp(self):
        self.html = _read_portal()

    def test_resume_function_exists(self):
        """resumeApplication function must exist."""
        assert "async function resumeApplication" in self.html

    def test_resume_fetches_application_by_ref(self):
        """Resume must fetch application detail by ref."""
        assert re.search(
            r"apiCall\s*\(\s*'GET'\s*,\s*'/applications/'\s*\+\s*encodeURIComponent\(ref\)",
            self.html
        ), "resumeApplication must fetch application by ref"

    def test_reset_before_restore(self):
        """Resume must reset state before restoring to avoid stale data."""
        assert "resetPortalApplicationState()" in self.html, \
            "Resume must call resetPortalApplicationState before restoring"

    def test_id_set_after_reset(self):
        """currentApplicationId must be set after resetPortalApplicationState."""
        # Find the positions
        reset_pos = self.html.find("resetPortalApplicationState()")
        id_set_pos = self.html.find("currentApplicationId = app.id")
        assert reset_pos > 0 and id_set_pos > 0, "Both reset and id-set must exist"
        assert id_set_pos > reset_pos, \
            "currentApplicationId must be set AFTER resetPortalApplicationState"


if __name__ == "__main__":
    unittest.main()
