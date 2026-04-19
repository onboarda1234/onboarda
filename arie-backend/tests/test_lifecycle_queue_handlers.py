"""
PR-05: HTTP/API-level tests for the lifecycle queue clarity endpoints.

Covers:
  * GET /api/lifecycle/queue
      - auth required, role-gated to admin/sco/co
      - default include=active, type=all
      - include=historical / include=all
      - type filter (alerts / reviews / edd) accepts plural and singular
      - invalid include / type return 400
      - response shape: items[], counts{alert,review,edd,total}, filter
      - linkage / required-items / memo-context fields surface end-to-end
  * GET /api/lifecycle/applications/:id/summary
      - 404 for unknown application
      - active vs historical split
      - linkage edges emitted
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tornado.testing import AsyncHTTPTestCase


class _LifecycleQueueHandlerBase(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_pr05_h_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        os.environ["DB_PATH"] = self._db_path
        import config as config_module
        import db as db_module
        self._orig_config_db_path = config_module.DB_PATH
        self._orig_db_db_path = db_module.DB_PATH
        config_module.DB_PATH = self._db_path
        db_module.DB_PATH = self._db_path

        db_module.init_db()
        conn = db_module.get_db()
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

        self._app_id = "app-pr05-http"
        try:
            conn.execute(
                "INSERT INTO applications "
                "(id, ref, company_name, country, sector, "
                " ownership_structure, risk_level, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self._app_id, "APP-PR05-H", "PR05 HTTP Co",
                 "Mauritius", "Fintech", "single-tier", "MEDIUM",
                 "approved"),
            )
        except Exception:
            conn.execute(
                "INSERT OR IGNORE INTO applications (id, ref, company_name) "
                "VALUES (?, ?, ?)",
                (self._app_id, "APP-PR05-H", "PR05 HTTP Co"),
            )
        conn.commit()
        conn.close()

        from server import make_app
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
        try:
            import config as config_module
            import db as db_module
            config_module.DB_PATH = self._orig_config_db_path
            db_module.DB_PATH = self._orig_db_db_path
        except Exception:
            pass
        super().tearDown()

    def _get(self, path, token=None):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.fetch(path, method="GET", headers=headers)

    # seed helpers (mirrors engine-test harness)
    def _alert(self, **kw):
        defaults = dict(
            application_id=self._app_id,
            client_name="PR05 HTTP Co",
            alert_type="manual",
            severity="High",
            summary="seeded",
            status="open",
        )
        defaults.update(kw)
        cols = ",".join(defaults.keys())
        ph = ",".join(["?"] * len(defaults))
        self._conn.execute(
            f"INSERT INTO monitoring_alerts ({cols}) VALUES ({ph})",
            tuple(defaults.values()),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM monitoring_alerts ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _review(self, **kw):
        defaults = dict(
            application_id=self._app_id,
            client_name="PR05 HTTP Co",
            risk_level="MEDIUM",
            status="pending",
            trigger_type="time_based",
        )
        defaults.update(kw)
        cols = ",".join(defaults.keys())
        ph = ",".join(["?"] * len(defaults))
        self._conn.execute(
            f"INSERT INTO periodic_reviews ({cols}) VALUES ({ph})",
            tuple(defaults.values()),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _edd(self, **kw):
        defaults = dict(
            application_id=self._app_id,
            client_name="PR05 HTTP Co",
            risk_level="HIGH",
            stage="triggered",
            trigger_source="officer_decision",
        )
        defaults.update(kw)
        cols = ",".join(defaults.keys())
        ph = ",".join(["?"] * len(defaults))
        self._conn.execute(
            f"INSERT INTO edd_cases ({cols}) VALUES ({ph})",
            tuple(defaults.values()),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM edd_cases ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]


# ───────────────────────────────────────────────────────────────────
# /api/lifecycle/queue
# ───────────────────────────────────────────────────────────────────
class TestLifecycleQueueEndpoint(_LifecycleQueueHandlerBase):
    def test_requires_auth(self):
        resp = self._get("/api/lifecycle/queue")
        self.assertEqual(resp.code, 401)

    def test_client_role_forbidden(self):
        resp = self._get("/api/lifecycle/queue", token=self.client_token)
        self.assertEqual(resp.code, 403)

    def test_default_returns_active_queue_with_counts(self):
        self._alert(status="open")
        self._alert(status="dismissed")
        self._review(status="pending")
        self._edd(stage="triggered")
        resp = self._get("/api/lifecycle/queue", token=self.admin_token)
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertIn("items", body)
        self.assertIn("counts", body)
        self.assertIn("filter", body)
        self.assertEqual(body["filter"]["include"], "active")
        # Active: 1 alert + 1 review + 1 edd
        self.assertEqual(body["counts"]["alert"], 1)
        self.assertEqual(body["counts"]["review"], 1)
        self.assertEqual(body["counts"]["edd"], 1)
        self.assertEqual(body["counts"]["total"], 3)
        for it in body["items"]:
            self.assertTrue(it["is_active"])

    def test_include_historical_returns_terminal_only(self):
        self._alert(status="open")
        self._alert(status="dismissed")
        resp = self._get(
            "/api/lifecycle/queue?include=historical&type=alerts",
            token=self.co_token,
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["counts"]["alert"], 1)
        self.assertEqual(body["items"][0]["state"], "dismissed")
        self.assertTrue(body["items"][0]["is_historical"])

    def test_include_all_combines(self):
        self._alert(status="open")
        self._alert(status="routed_to_edd")
        resp = self._get(
            "/api/lifecycle/queue?include=all&type=alerts",
            token=self.admin_token,
        )
        body = json.loads(resp.body)
        self.assertEqual(body["counts"]["alert"], 2)
        # Active first, historical second (deterministic ordering)
        self.assertTrue(body["items"][0]["is_active"])
        self.assertTrue(body["items"][1]["is_historical"])

    def test_type_singular_and_plural_both_accepted(self):
        self._review(status="pending")
        self._edd(stage="triggered")
        for arg in ("type=reviews", "type=review", "types=review"):
            resp = self._get(
                f"/api/lifecycle/queue?{arg}", token=self.admin_token,
            )
            self.assertEqual(resp.code, 200, arg)
            body = json.loads(resp.body)
            kinds = {it["type"] for it in body["items"]}
            self.assertEqual(kinds, {"review"}, arg)

    def test_invalid_include_returns_400(self):
        resp = self._get(
            "/api/lifecycle/queue?include=bogus", token=self.admin_token,
        )
        self.assertEqual(resp.code, 400)

    def test_include_legacy_unmapped_returns_only_quarantined(self):
        # PR-A: handler must accept include=legacy_unmapped and surface
        # only ghost rows. Healthy rows must be absent from this bucket.
        self._alert(status="open")  # healthy active
        self._alert(status="dismissed")  # healthy historical
        ghost_a = self._alert(status="escalated", application_id=None)
        ghost_b = self._alert(status="escalated")  # vocab ghost only
        resp = self._get(
            "/api/lifecycle/queue?include=legacy_unmapped",
            token=self.admin_token,
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        ids = {it["id"] for it in body["items"]}
        self.assertEqual(ids, {ghost_a, ghost_b})
        for it in body["items"]:
            self.assertTrue(it["is_legacy_unmapped"])
            self.assertGreater(len(it["quarantine_reasons"]), 0)

    def test_invalid_type_returns_400(self):
        resp = self._get(
            "/api/lifecycle/queue?type=bogus", token=self.admin_token,
        )
        self.assertEqual(resp.code, 400)

    def test_application_id_scope(self):
        # Seed two applications, alert on each
        other = "app-pr05-other"
        try:
            self._conn.execute(
                "INSERT INTO applications (id, ref, company_name) "
                "VALUES (?, ?, ?)",
                (other, "APP-OTHER", "Other Co"),
            )
            self._conn.commit()
        except Exception:
            pass
        self._alert(status="open")
        self._alert(application_id=other, status="open")
        resp = self._get(
            f"/api/lifecycle/queue?application_id={self._app_id}",
            token=self.admin_token,
        )
        body = json.loads(resp.body)
        self.assertEqual(body["counts"]["alert"], 1)
        self.assertEqual(
            body["items"][0]["application_id"], self._app_id,
        )

    def test_required_items_count_surfaces_via_http(self):
        items_payload = json.dumps([
            {"code": "kyc_refresh", "label": "x", "rationale": "y"}
        ])
        self._review(status="in_progress", required_items=items_payload)
        resp = self._get(
            "/api/lifecycle/queue?type=reviews", token=self.admin_token,
        )
        body = json.loads(resp.body)
        self.assertEqual(body["items"][0]["required_items_count"], 1)

    def test_edd_memo_context_surfaced_via_http(self):
        # Onboarding context with no compliance_memos row → memo_id is
        # None. The aggregator must still surface a memo_context dict
        # rather than omit it (officers need to see the gap).
        self._edd(stage="analysis", origin_context="onboarding")
        resp = self._get(
            "/api/lifecycle/queue?type=edd", token=self.admin_token,
        )
        body = json.loads(resp.body)
        ctx = body["items"][0]["memo_context"]
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["kind"], "onboarding")
        self.assertIsNone(ctx["memo_id"])
        # PR-04a: onboarding context with NULL memo_id is NOT confirmed.
        self.assertFalse(ctx["onboarding_attachment_confirmed"])


# ───────────────────────────────────────────────────────────────────
# /api/lifecycle/applications/:id/summary
# ───────────────────────────────────────────────────────────────────
class TestApplicationSummaryEndpoint(_LifecycleQueueHandlerBase):
    def test_requires_auth(self):
        resp = self._get(
            f"/api/lifecycle/applications/{self._app_id}/summary"
        )
        self.assertEqual(resp.code, 401)

    def test_client_role_forbidden(self):
        resp = self._get(
            f"/api/lifecycle/applications/{self._app_id}/summary",
            token=self.client_token,
        )
        self.assertEqual(resp.code, 403)

    def test_unknown_application_returns_404(self):
        resp = self._get(
            "/api/lifecycle/applications/does-not-exist/summary",
            token=self.admin_token,
        )
        self.assertEqual(resp.code, 404)

    def test_summary_returns_active_historical_and_linkage(self):
        rid = self._review(status="in_progress")
        self._alert(status="routed_to_review",
                    linked_periodic_review_id=rid)
        self._edd(stage="edd_approved")
        resp = self._get(
            f"/api/lifecycle/applications/{self._app_id}/summary",
            token=self.admin_token,
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["application_id"], self._app_id)
        self.assertIn("active", body)
        self.assertIn("historical", body)
        self.assertIn("linkage", body)
        # Active: in_progress review only
        active_kinds = {(it["type"], it["state"])
                        for it in body["active"]["items"]}
        self.assertIn(("review", "in_progress"), active_kinds)
        # Historical: routed alert + approved edd
        hist_states = {it["state"] for it in body["historical"]["items"]}
        self.assertIn("routed_to_review", hist_states)
        self.assertIn("edd_approved", hist_states)
        # Linkage edge present
        kinds = {e["kind"] for e in body["linkage"]["edges"]}
        self.assertIn("alert_to_review", kinds)


if __name__ == "__main__":
    import unittest
    unittest.main()
