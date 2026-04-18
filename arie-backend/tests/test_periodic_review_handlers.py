"""
HTTP/API-level tests for PR-03 periodic review handlers.

These cover the new additive endpoints introduced by PR-03:

* POST /api/monitoring/reviews/:id/state
* GET  /api/monitoring/reviews/:id/required-items
* POST /api/monitoring/reviews/:id/required-items/generate
* POST /api/monitoring/reviews/:id/escalate
* POST /api/monitoring/reviews/:id/complete

The existing legacy ``PeriodicReviewDecisionHandler`` is intentionally
NOT modified by PR-03 and continues to work (back-compat). PR-02
follow-up reality: nearby PR-02 review-routing handlers remain covered
by ``test_monitoring_routing.py`` (engine-level); these PR-03 handler
tests add HTTP coverage on the periodic-review review handler seam
where PR-03 changes the operating model.
"""
import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tornado.testing import AsyncHTTPTestCase


class _PRReviewHandlerBase(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_pr03_handler_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        # Mutate the module-level DB_PATH directly (matches the pattern
        # used by other server-level handler tests; avoids reloading
        # config/db modules which has cross-test side effects on the
        # Prometheus registry and shared module state). Restored in
        # tearDown so test_periodic_review_handlers does not pollute
        # the DB_PATH seen by other suites in the same pytest session.
        os.environ["DB_PATH"] = self._db_path
        import config as config_module
        import db as db_module
        self._orig_config_db_path = config_module.DB_PATH
        self._orig_db_db_path = db_module.DB_PATH
        config_module.DB_PATH = self._db_path
        db_module.DB_PATH = self._db_path

        db_module.init_db()
        conn = db_module.get_db()

        # Pre-mark migrations 001..007 as applied (init_db reflects the
        # full post-007 schema already; runner would otherwise replay
        # historical migrations and fail with duplicate-column errors).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "version TEXT UNIQUE NOT NULL, "
            "filename TEXT NOT NULL, "
            "description TEXT DEFAULT '', "
            "applied_at TEXT DEFAULT (datetime('now')), "
            "checksum TEXT)"
        )
        for v, fn in [
            ("001", "migration_001_initial.sql"),
            ("002", "migration_002_supervisor_tables.sql"),
            ("003", "migration_003_monitoring_indexes.sql"),
            ("004", "migration_004_documents_s3_key.sql"),
            ("005", "migration_005_applications_truth_schema.sql"),
            ("006", "migration_006_person_dob.sql"),
            ("007", "migration_007_screening_reports_normalized.sql"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, filename) "
                "VALUES (?, ?)", (v, fn),
            )
        conn.commit()
        from migrations.runner import run_all_migrations_with_connection
        run_all_migrations_with_connection(conn)

        # Seed an application for tests
        self._app_id = "test-app-pr03"
        try:
            conn.execute(
                "INSERT INTO applications "
                "(id, ref, company_name, country, sector, "
                " ownership_structure, risk_level, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self._app_id, "APP-PR03", "PR03 Test Co",
                 "Mauritius", "Fintech", "single-tier", "MEDIUM",
                 "approved"),
            )
        except Exception:
            conn.execute(
                "INSERT OR IGNORE INTO applications (id, ref, company_name) "
                "VALUES (?, ?, ?)",
                (self._app_id, "APP-PR03", "PR03 Test Co"),
            )
        conn.commit()
        conn.close()

        from server import make_app, create_token  # noqa: F401
        import server as server_module
        self._server = server_module
        return make_app()

    def setUp(self):
        super().setUp()
        from db import get_db
        self._conn = get_db()
        self.admin_token = self._server.create_token(
            "admin001", "admin", "Test Admin", "officer"
        )
        self.co_token = self._server.create_token(
            "co001", "co", "Test CO", "officer"
        )
        self.client_token = self._server.create_token(
            "client001", "client", "Test Client", "client"
        )

    def tearDown(self):
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            os.unlink(self._db_path)
        except Exception:
            pass
        # Restore module-level DB_PATH to avoid polluting other
        # suites in the same pytest session.
        try:
            import config as config_module
            import db as db_module
            config_module.DB_PATH = self._orig_config_db_path
            db_module.DB_PATH = self._orig_db_db_path
        except Exception:
            pass
        super().tearDown()

    # ---- helpers ----
    def _create_review(self, *, status="pending", risk_level="MEDIUM",
                       trigger_source=None, linked_alert_id=None,
                       review_reason=None, application_id=None):
        application_id = application_id or self._app_id
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status, "
            " trigger_source, linked_monitoring_alert_id, review_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (application_id, "PR03 Test Co", risk_level, status,
             trigger_source, linked_alert_id, review_reason),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _post(self, path, body, token=None):
        return self.fetch(
            path, method="POST",
            body=json.dumps(body or {}),
            headers={
                "Authorization": f"Bearer {token or self.admin_token}",
                "Content-Type": "application/json",
            },
        )

    def _get(self, path, token=None):
        return self.fetch(
            path, method="GET",
            headers={"Authorization": f"Bearer {token or self.admin_token}"},
        )


# ─────────────────────────────────────────────────────────────────
# State endpoint
# ─────────────────────────────────────────────────────────────────
class TestStateHandler(_PRReviewHandlerBase):
    def test_requires_auth(self):
        rid = self._create_review()
        resp = self.fetch(
            f"/api/monitoring/reviews/{rid}/state",
            method="POST", body=json.dumps({"state": "in_progress"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.code, 401)

    def test_client_role_forbidden(self):
        rid = self._create_review()
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/state",
            {"state": "in_progress"}, token=self.client_token,
        )
        self.assertEqual(resp.code, 403)

    def test_pending_to_in_progress_succeeds(self):
        rid = self._create_review()
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/state",
            {"state": "in_progress"},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["status"], "state_changed")
        self.assertEqual(body["result"]["to"], "in_progress")
        # Persisted
        row = self._conn.execute(
            "SELECT status FROM periodic_reviews WHERE id = ?", (rid,)
        ).fetchone()
        self.assertEqual(row["status"], "in_progress")

    def test_invalid_transition_returns_409(self):
        rid = self._create_review(status="pending")
        # pending -> awaiting_information is not allowed
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/state",
            {"state": "awaiting_information"},
        )
        self.assertEqual(resp.code, 409)

    def test_terminal_completion_via_state_blocked(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/state",
            {"state": "completed"},
        )
        self.assertEqual(resp.code, 409)

    def test_missing_state_returns_400(self):
        rid = self._create_review()
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/state", {},
        )
        self.assertEqual(resp.code, 400)


# ─────────────────────────────────────────────────────────────────
# Required-items endpoints
# ─────────────────────────────────────────────────────────────────
class TestRequiredItemsHandler(_PRReviewHandlerBase):
    def test_get_empty_default(self):
        rid = self._create_review()
        resp = self._get(f"/api/monitoring/reviews/{rid}/required-items")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["items"], [])

    def test_generate_persists_items(self):
        rid = self._create_review(risk_level="HIGH")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/required-items/generate", {},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertGreater(body["count"], 0)
        codes = {it["code"] for it in body["items"]}
        self.assertIn("kyc_refresh", codes)
        self.assertIn("source_of_funds_refresh", codes)
        # Read-back via GET returns the same list
        resp2 = self._get(f"/api/monitoring/reviews/{rid}/required-items")
        body2 = json.loads(resp2.body)
        self.assertEqual(len(body2["items"]), body["count"])

    def test_generate_blocked_on_completed(self):
        rid = self._create_review(status="completed")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/required-items/generate", {},
        )
        self.assertEqual(resp.code, 409)


# ─────────────────────────────────────────────────────────────────
# Escalate endpoint
# ─────────────────────────────────────────────────────────────────
class TestEscalateHandler(_PRReviewHandlerBase):
    def test_creates_edd_first_time(self):
        rid = self._create_review(risk_level="HIGH")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/escalate",
            {"trigger_notes": "high risk"},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertTrue(body["result"]["created"])
        edd_id = body["result"]["edd_case_id"]
        # PR-01 origin recorded
        edd = self._conn.execute(
            "SELECT origin_context, linked_periodic_review_id FROM edd_cases "
            "WHERE id = ?", (edd_id,),
        ).fetchone()
        self.assertEqual(edd["origin_context"], "periodic_review")
        self.assertEqual(edd["linked_periodic_review_id"], rid)

    def test_repeat_escalate_is_dedup_safe(self):
        rid = self._create_review(risk_level="HIGH")
        first = json.loads(self._post(
            f"/api/monitoring/reviews/{rid}/escalate", {},
        ).body)
        second = json.loads(self._post(
            f"/api/monitoring/reviews/{rid}/escalate", {},
        ).body)
        self.assertEqual(
            first["result"]["edd_case_id"],
            second["result"]["edd_case_id"],
        )
        self.assertTrue(second["result"]["reused"])
        # Exactly one EDD
        n = self._conn.execute(
            "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id = ?",
            (self._app_id,),
        ).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_escalate_blocked_on_completed(self):
        rid = self._create_review(status="completed")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/escalate", {},
        )
        self.assertEqual(resp.code, 409)

    def test_unknown_review_returns_404(self):
        resp = self._post(
            "/api/monitoring/reviews/99999/escalate", {},
        )
        self.assertEqual(resp.code, 404)


# ─────────────────────────────────────────────────────────────────
# Complete endpoint
# ─────────────────────────────────────────────────────────────────
class TestCompleteHandler(_PRReviewHandlerBase):
    def test_records_outcome_and_closes(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome": "no_change", "outcome_reason": "all checks pass"},
        )
        self.assertEqual(resp.code, 200)
        row = self._conn.execute(
            "SELECT status, outcome, outcome_reason, closed_at "
            "FROM periodic_reviews WHERE id = ?", (rid,)
        ).fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["outcome"], "no_change")
        self.assertIsNotNone(row["closed_at"])

    def test_replay_blocked(self):
        rid = self._create_review(status="in_progress")
        first = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome": "no_change", "outcome_reason": "x"},
        )
        self.assertEqual(first.code, 200)
        second = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome": "no_change", "outcome_reason": "y"},
        )
        self.assertEqual(second.code, 409)

    def test_invalid_outcome_400(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome": "bogus", "outcome_reason": "x"},
        )
        self.assertEqual(resp.code, 400)

    def test_missing_outcome_400(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome_reason": "x"},
        )
        self.assertEqual(resp.code, 400)

    def test_missing_outcome_reason_400(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome": "no_change"},
        )
        self.assertEqual(resp.code, 400)
