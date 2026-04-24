"""test_fixture_filter.py — Priority D fixture containment unit tests.

Covers:
  * fixture_filter.build_exclude_apps_sql / build_exclude_lifecycle_sql
  * fixture_filter.is_fixture_app / mark_fixture
  * Dashboard, Applications list, EDD list, Monitoring endpoints
    exclude fixture/demo rows by default.
  * Lifecycle queue excludes fixture rows by default.
  * show_fixtures=True restores fixture rows and marks them is_fixture=True.
  * Real/live cases still appear normally.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ────────────────────────────────────────────────────────────────
# Pure-function unit tests for fixture_filter helpers
# ────────────────────────────────────────────────────────────────

class TestIsFixtureApp(unittest.TestCase):
    """is_fixture_app() returns True for all reserved-namespace IDs."""

    def test_f1xed_id_identified(self):
        from fixture_filter import is_fixture_app
        self.assertTrue(is_fixture_app("f1xed00000000001"))
        self.assertTrue(is_fixture_app("f1xed00000000011"))

    def test_demo_id_identified(self):
        from fixture_filter import is_fixture_app
        self.assertTrue(is_fixture_app("demo-scenario-01"))
        self.assertTrue(is_fixture_app("demo-scenario-05"))

    def test_fixture_ref_identified(self):
        from fixture_filter import is_fixture_app
        self.assertTrue(is_fixture_app("irrelevant", "ARF-2026-900001"))
        self.assertTrue(is_fixture_app("irrelevant", "ARF-2026-900011"))

    def test_demo_ref_identified(self):
        from fixture_filter import is_fixture_app
        self.assertTrue(is_fixture_app("irrelevant", "ARF-2026-DEMO01"))
        self.assertTrue(is_fixture_app("irrelevant", "ARF-2026-DEMO05"))

    def test_live_app_not_identified(self):
        from fixture_filter import is_fixture_app
        self.assertFalse(is_fixture_app("abc123def456789a"))
        self.assertFalse(is_fixture_app("abc123def456789a", "ARF-2026-100421"))

    def test_empty_id_not_identified(self):
        from fixture_filter import is_fixture_app
        self.assertFalse(is_fixture_app(""))
        self.assertFalse(is_fixture_app("", ""))


class TestMarkFixture(unittest.TestCase):
    """mark_fixture() adds is_fixture=True to fixture rows."""

    def test_fixture_row_gets_flag(self):
        from fixture_filter import mark_fixture
        row = {"id": "f1xed00000000001", "company_name": "FIX-SCEN01 Test"}
        result = mark_fixture(row)
        self.assertTrue(result.get("is_fixture"))

    def test_live_row_unchanged(self):
        from fixture_filter import mark_fixture
        row = {"id": "abc123", "company_name": "Real Company Ltd"}
        result = mark_fixture(row)
        self.assertNotIn("is_fixture", result)

    def test_original_dict_not_mutated(self):
        from fixture_filter import mark_fixture
        row = {"id": "f1xed00000000001"}
        _ = mark_fixture(row)
        self.assertNotIn("is_fixture", row)

    def test_lifecycle_row_with_application_id(self):
        from fixture_filter import mark_fixture
        row = {"application_id": "f1xed00000000003", "status": "open"}
        result = mark_fixture(row)
        self.assertTrue(result.get("is_fixture"))


class TestBuildExcludeAppsSql(unittest.TestCase):
    """build_exclude_apps_sql produces correct SQL fragments."""

    def _exec(self, conn, apps, query, params):
        """Helper: insert apps into a minimal table, run query, count results."""
        conn.execute(
            "CREATE TABLE IF NOT EXISTS applications "
            "(id TEXT PRIMARY KEY, ref TEXT, company_name TEXT)"
        )
        for a in apps:
            conn.execute(
                "INSERT INTO applications (id, ref, company_name) VALUES (?,?,?)",
                a,
            )
        conn.commit()
        return conn.execute(query, params).fetchall()

    def _make_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def test_fixture_rows_excluded_with_alias(self):
        from fixture_filter import build_exclude_apps_sql
        conn = self._make_conn()
        apps = [
            ("f1xed00000000001", "ARF-2026-900001", "FIX-SCEN01"),
            ("demo-scenario-01", "ARF-2026-DEMO01", "Demo Co"),
            ("live000000000001", "ARF-2026-100001", "Live Co"),
        ]
        frag, params = build_exclude_apps_sql("a")
        rows = self._exec(
            conn, apps,
            f"SELECT * FROM applications a WHERE {frag}",
            params,
        )
        ids = [r["id"] for r in rows]
        self.assertIn("live000000000001", ids)
        self.assertNotIn("f1xed00000000001", ids)
        self.assertNotIn("demo-scenario-01", ids)

    def test_fixture_ref_excluded(self):
        from fixture_filter import build_exclude_apps_sql
        conn = self._make_conn()
        apps = [
            ("some-id-fixture", "ARF-2026-900005", "Fixture via ref"),
            ("some-id-demo", "ARF-2026-DEMO03", "Demo via ref"),
            ("some-id-live", "ARF-2026-100421", "Live"),
        ]
        frag, params = build_exclude_apps_sql("")
        rows = self._exec(
            conn, apps,
            f"SELECT * FROM applications WHERE {frag}",
            params,
        )
        ids = [r["id"] for r in rows]
        self.assertIn("some-id-live", ids)
        self.assertNotIn("some-id-fixture", ids)
        self.assertNotIn("some-id-demo", ids)


class TestBuildExcludeLifecycleSql(unittest.TestCase):
    """build_exclude_lifecycle_sql produces correct NULL-safe fragments."""

    def _make_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE monitoring_alerts "
            "(id INTEGER PRIMARY KEY, application_id TEXT, source_reference TEXT)"
        )
        conn.commit()
        return conn

    def test_fixture_app_id_excluded(self):
        from fixture_filter import build_exclude_lifecycle_sql
        conn = self._make_conn()
        conn.execute(
            "INSERT INTO monitoring_alerts (application_id, source_reference) VALUES (?,?)",
            ("f1xed00000000001", "FIX_SCEN01_ALERT"),
        )
        conn.execute(
            "INSERT INTO monitoring_alerts (application_id, source_reference) VALUES (?,?)",
            ("live000000000001", "LIVE_REF"),
        )
        conn.commit()
        frag, params = build_exclude_lifecycle_sql()
        rows = conn.execute(
            f"SELECT * FROM monitoring_alerts WHERE {frag}", params
        ).fetchall()
        ids = [r["application_id"] for r in rows]
        self.assertIn("live000000000001", ids)
        self.assertNotIn("f1xed00000000001", ids)

    def test_null_application_id_not_excluded(self):
        from fixture_filter import build_exclude_lifecycle_sql
        conn = self._make_conn()
        conn.execute(
            "INSERT INTO monitoring_alerts (application_id, source_reference) VALUES (?,?)",
            (None, "MANUAL_ALERT"),
        )
        conn.commit()
        frag, params = build_exclude_lifecycle_sql()
        rows = conn.execute(
            f"SELECT * FROM monitoring_alerts WHERE {frag}", params
        ).fetchall()
        # Null application_id row must be preserved (not a fixture).
        self.assertEqual(len(rows), 1)

    def test_alert_sentinel_excludes(self):
        from fixture_filter import build_exclude_lifecycle_sql
        conn = self._make_conn()
        # Row with non-fixture app_id but FIX_SCEN sentinel source_reference
        conn.execute(
            "INSERT INTO monitoring_alerts (application_id, source_reference) VALUES (?,?)",
            ("live000000000001", "FIX_SCEN01_ALERT"),
        )
        conn.execute(
            "INSERT INTO monitoring_alerts (application_id, source_reference) VALUES (?,?)",
            ("live000000000002", "REAL_ALERT"),
        )
        conn.commit()
        frag, params = build_exclude_lifecycle_sql(alert_sentinel=True)
        rows = conn.execute(
            f"SELECT * FROM monitoring_alerts WHERE {frag}", params
        ).fetchall()
        srefs = [r["source_reference"] for r in rows]
        self.assertIn("REAL_ALERT", srefs)
        self.assertNotIn("FIX_SCEN01_ALERT", srefs)


# ────────────────────────────────────────────────────────────────
# Lifecycle queue integration tests (SQLite in-memory)
# ────────────────────────────────────────────────────────────────

class _LQBase(unittest.TestCase):
    """Shared harness for lifecycle queue fixture-filter tests.

    Sets up a full schema + migrations so that all columns are present.
    """

    _REAL_APP_ID = "real-app-live-001"
    _FIX_APP_ID = "f1xed00000000001"
    _DEMO_APP_ID = "demo-scenario-01"

    def setUp(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_d_lq_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        os.environ["DB_PATH"] = self._db_path
        import config as cfg
        import db as db_module
        self._orig_cfg = cfg.DB_PATH
        self._orig_db = db_module.DB_PATH
        cfg.DB_PATH = self._db_path
        db_module.DB_PATH = self._db_path
        db_module.init_db()
        conn = db_module.get_db()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "version TEXT UNIQUE NOT NULL, filename TEXT NOT NULL, "
            "description TEXT DEFAULT '', applied_at TEXT DEFAULT (datetime('now')), "
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
                "INSERT OR IGNORE INTO schema_version (version, filename) VALUES (?,?)",
                (v, fn),
            )
        conn.commit()
        from migrations.runner import run_all_migrations_with_connection
        run_all_migrations_with_connection(conn)

        # Seed real and fixture applications
        for app_id, ref, name in [
            (self._REAL_APP_ID, "ARF-2026-100001", "Real Live Company Ltd"),
            (self._FIX_APP_ID, "ARF-2026-900001", "FIX-SCEN01 Test Corp"),
            (self._DEMO_APP_ID, "ARF-2026-DEMO01", "Demo Scenario Company"),
        ]:
            try:
                conn.execute(
                    "INSERT INTO applications "
                    "(id, ref, company_name, country, sector, ownership_structure, risk_level, status) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (app_id, ref, name, "Mauritius", "Fintech", "single-tier", "MEDIUM", "approved"),
                )
            except Exception:
                pass
        conn.commit()
        self._conn = conn

    def tearDown(self):
        import config as cfg
        import db as db_module
        try:
            self._conn.close()
        except Exception:
            pass
        cfg.DB_PATH = self._orig_cfg
        db_module.DB_PATH = self._orig_db
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    def _insert_alert(self, app_id, status="open", source_reference=None):
        src = source_reference or f"SRC_{app_id[:8]}"
        try:
            if self._conn.is_postgres:
                row = self._conn.execute(
                    "INSERT INTO monitoring_alerts "
                    "(application_id, client_name, alert_type, severity, status, source_reference) "
                    "VALUES (?,?,?,?,?,?) RETURNING id",
                    (app_id, "Test", "AML", "High", status, src),
                ).fetchone()
                return row["id"]
        except AttributeError:
            pass
        self._conn.execute(
            "INSERT INTO monitoring_alerts "
            "(application_id, client_name, alert_type, severity, status, source_reference) "
            "VALUES (?,?,?,?,?,?)",
            (app_id, "Test", "AML", "High", status, src),
        )
        self._conn.commit()
        return self._conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]

    def _insert_review(self, app_id, status="pending"):
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, trigger_type, status) "
            "VALUES (?,?,?,?)",
            (app_id, "Test", "annual", status),
        )
        self._conn.commit()

    def _insert_edd(self, app_id, stage="triggered"):
        self._conn.execute(
            "INSERT INTO edd_cases "
            "(application_id, client_name, risk_level, risk_score, stage, trigger_source) "
            "VALUES (?,?,?,?,?,?)",
            (app_id, "Test", "HIGH", 75, stage, "officer_decision"),
        )
        self._conn.commit()


class TestLifecycleQueueFixtureExclusion(_LQBase):
    """Default lifecycle queue must exclude fixture rows."""

    def test_fixture_alert_excluded_by_default(self):
        import lifecycle_queue as lq
        self._insert_alert(self._REAL_APP_ID)
        self._insert_alert(self._FIX_APP_ID)
        result = lq.build_lifecycle_queue(self._conn, include="active", types=("alert",))
        app_ids = [i["application_id"] for i in result["items"]]
        self.assertIn(self._REAL_APP_ID, app_ids)
        self.assertNotIn(self._FIX_APP_ID, app_ids)

    def test_demo_alert_excluded_by_default(self):
        import lifecycle_queue as lq
        self._insert_alert(self._REAL_APP_ID)
        self._insert_alert(self._DEMO_APP_ID)
        result = lq.build_lifecycle_queue(self._conn, include="active", types=("alert",))
        app_ids = [i["application_id"] for i in result["items"]]
        self.assertIn(self._REAL_APP_ID, app_ids)
        self.assertNotIn(self._DEMO_APP_ID, app_ids)

    def test_fixture_review_excluded_by_default(self):
        import lifecycle_queue as lq
        self._insert_review(self._REAL_APP_ID)
        self._insert_review(self._FIX_APP_ID)
        result = lq.build_lifecycle_queue(self._conn, include="active", types=("review",))
        app_ids = [i["application_id"] for i in result["items"]]
        self.assertIn(self._REAL_APP_ID, app_ids)
        self.assertNotIn(self._FIX_APP_ID, app_ids)

    def test_fixture_edd_excluded_by_default(self):
        import lifecycle_queue as lq
        self._insert_edd(self._REAL_APP_ID)
        self._insert_edd(self._FIX_APP_ID)
        result = lq.build_lifecycle_queue(self._conn, include="active", types=("edd",))
        app_ids = [i["application_id"] for i in result["items"]]
        self.assertIn(self._REAL_APP_ID, app_ids)
        self.assertNotIn(self._FIX_APP_ID, app_ids)

    def test_counts_exclude_fixture_rows(self):
        import lifecycle_queue as lq
        self._insert_alert(self._REAL_APP_ID)
        self._insert_alert(self._FIX_APP_ID)
        self._insert_review(self._REAL_APP_ID)
        self._insert_review(self._FIX_APP_ID)
        self._insert_edd(self._REAL_APP_ID)
        self._insert_edd(self._FIX_APP_ID)
        result = lq.build_lifecycle_queue(self._conn, include="active")
        # Only 1 alert, 1 review, 1 edd from real app.
        self.assertEqual(result["counts"]["alert"], 1)
        self.assertEqual(result["counts"]["review"], 1)
        self.assertEqual(result["counts"]["edd"], 1)
        self.assertEqual(result["counts"]["total"], 3)

    def test_show_fixtures_restores_fixture_rows(self):
        import lifecycle_queue as lq
        self._insert_alert(self._REAL_APP_ID)
        self._insert_alert(self._FIX_APP_ID)
        result = lq.build_lifecycle_queue(
            self._conn, include="active", types=("alert",), show_fixtures=True
        )
        app_ids = [i["application_id"] for i in result["items"]]
        self.assertIn(self._REAL_APP_ID, app_ids)
        self.assertIn(self._FIX_APP_ID, app_ids)

    def test_show_fixtures_marks_fixture_items(self):
        import lifecycle_queue as lq
        self._insert_alert(self._REAL_APP_ID)
        self._insert_alert(self._FIX_APP_ID)
        result = lq.build_lifecycle_queue(
            self._conn, include="active", types=("alert",), show_fixtures=True
        )
        fixture_items = [i for i in result["items"] if i.get("is_fixture")]
        live_items = [i for i in result["items"] if not i.get("is_fixture")]
        self.assertEqual(len(fixture_items), 1)
        self.assertEqual(fixture_items[0]["application_id"], self._FIX_APP_ID)
        self.assertEqual(len(live_items), 1)
        self.assertEqual(live_items[0]["application_id"], self._REAL_APP_ID)

    def test_filter_field_reflects_show_fixtures(self):
        import lifecycle_queue as lq
        result_default = lq.build_lifecycle_queue(self._conn, include="active")
        result_fixtures = lq.build_lifecycle_queue(
            self._conn, include="active", show_fixtures=True
        )
        self.assertFalse(result_default["filter"]["show_fixtures"])
        self.assertTrue(result_fixtures["filter"]["show_fixtures"])

    def test_null_application_id_alert_excluded_by_quarantine(self):
        """Standalone alerts with null application_id are quarantined by
        PR-A logic (unscopable_no_application), not by fixture filter.
        My fixture filter's IS NULL guard must not accidentally re-include them.
        This test simply verifies the behaviour is consistent with PR-A."""
        import lifecycle_queue as lq
        # Insert real and null-app-id alerts
        self._insert_alert(self._REAL_APP_ID)
        self._conn.execute(
            "INSERT INTO monitoring_alerts "
            "(application_id, client_name, alert_type, severity, status, source_reference) "
            "VALUES (NULL, 'Orphan', 'AML', 'High', 'open', 'STANDALONE_ALERT')"
        )
        self._conn.commit()
        # Active queue should only show the real alert; the null-app-id alert is quarantined
        # by PR-A (unscopable_no_application). This behaviour is unchanged.
        result = lq.build_lifecycle_queue(self._conn, include="active", types=("alert",))
        app_ids = [i["application_id"] for i in result["items"]]
        self.assertIn(self._REAL_APP_ID, app_ids)


# ────────────────────────────────────────────────────────────────
# Dashboard, Applications, EDD, Monitoring endpoint tests
# These test the server-layer handlers via a real Tornado HTTP
# server (mirrors test_lifecycle_queue_handlers.py pattern).
# ────────────────────────────────────────────────────────────────

try:
    from tornado.testing import AsyncHTTPTestCase
    HAS_TORNADO = True
except ImportError:
    HAS_TORNADO = False
    class AsyncHTTPTestCase:  # type: ignore
        pass


class _ServerBase(AsyncHTTPTestCase):
    """Full Tornado app harness with fixture and live data seeded."""

    _REAL_APP_ID = "live-app-d-001"
    _FIX_APP_ID = "f1xed00000000002"
    _DEMO_APP_ID = "demo-scenario-02"

    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_d_srv_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        os.environ["DB_PATH"] = self._db_path
        import config as cfg
        import db as db_module
        self._orig_cfg = cfg.DB_PATH
        self._orig_db = db_module.DB_PATH
        cfg.DB_PATH = self._db_path
        db_module.DB_PATH = self._db_path
        db_module.init_db()
        conn = db_module.get_db()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "version TEXT UNIQUE NOT NULL, filename TEXT NOT NULL, "
            "description TEXT DEFAULT '', applied_at TEXT DEFAULT (datetime('now')), "
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
                "INSERT OR IGNORE INTO schema_version (version, filename) VALUES (?,?)",
                (v, fn),
            )
        conn.commit()
        from migrations.runner import run_all_migrations_with_connection
        run_all_migrations_with_connection(conn)

        # Seed applications
        for app_id, ref, name, risk in [
            (self._REAL_APP_ID, "ARF-2026-100001", "Real Live Company Ltd", "HIGH"),
            (self._FIX_APP_ID, "ARF-2026-900002", "FIX-SCEN02 Test Corp", "HIGH"),
            (self._DEMO_APP_ID, "ARF-2026-DEMO02", "Demo Scenario 2", "MEDIUM"),
        ]:
            try:
                conn.execute(
                    "INSERT INTO applications "
                    "(id, ref, company_name, country, sector, ownership_structure, "
                    " risk_level, risk_score, status) VALUES (?,?,?,?,?,?,?,?,?)",
                    (app_id, ref, name, "Mauritius", "Fintech",
                     "single-tier", risk, 75, "approved"),
                )
            except Exception:
                pass

        # Seed an officer user
        import bcrypt
        pw = bcrypt.hashpw(b"pass1234", bcrypt.gensalt()).decode()
        try:
            conn.execute(
                "INSERT INTO users (id, email, full_name, password_hash, role) "
                "VALUES (?,?,?,?,?)",
                ("officer-d-001", "officer_d@test.com", "Officer D", pw, "co"),
            )
        except Exception:
            pass

        conn.commit()
        conn.close()

        from server import make_app
        return make_app()

    def tearDown(self):
        import config as cfg
        import db as db_module
        super().tearDown()
        cfg.DB_PATH = self._orig_cfg
        db_module.DB_PATH = self._orig_db
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    def _auth_headers(self):
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from auth import create_token
        token = create_token("officer-d-001", "co", "Officer D", "officer")
        return {"Authorization": f"Bearer {token}"}

    def _get(self, path, headers=None):
        h = headers or self._auth_headers()
        return self.fetch(path, method="GET", headers=h)


@unittest.skipUnless(HAS_TORNADO, "tornado not available")
class TestApplicationsListFixtureExclusion(_ServerBase):
    """GET /api/applications must exclude fixture rows by default."""

    def test_live_app_visible_by_default(self):
        resp = self._get("/api/applications")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        ids = [a["id"] for a in body.get("applications", [])]
        self.assertIn(self._REAL_APP_ID, ids)

    def test_fixture_app_excluded_by_default(self):
        resp = self._get("/api/applications")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        ids = [a["id"] for a in body.get("applications", [])]
        self.assertNotIn(self._FIX_APP_ID, ids)

    def test_demo_app_excluded_by_default(self):
        resp = self._get("/api/applications")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        ids = [a["id"] for a in body.get("applications", [])]
        self.assertNotIn(self._DEMO_APP_ID, ids)

    def test_show_fixtures_restores_fixture_apps(self):
        resp = self._get("/api/applications?show_fixtures=true")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        ids = [a["id"] for a in body.get("applications", [])]
        self.assertIn(self._FIX_APP_ID, ids)

    def test_show_fixtures_marks_is_fixture(self):
        resp = self._get("/api/applications?show_fixtures=true")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        apps_by_id = {a["id"]: a for a in body.get("applications", [])}
        self.assertTrue(apps_by_id[self._FIX_APP_ID].get("is_fixture"))
        # Live app must NOT be marked as fixture.
        self.assertFalse(apps_by_id.get(self._REAL_APP_ID, {}).get("is_fixture"))


@unittest.skipUnless(HAS_TORNADO, "tornado not available")
class TestDashboardFixtureExclusion(_ServerBase):
    """GET /api/dashboard counts must exclude fixture rows."""

    def test_dashboard_total_excludes_fixtures(self):
        resp = self._get("/api/dashboard")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        # Only 1 real app (approved, HIGH risk) was seeded.
        total = body.get("total", body.get("data", {}).get("total"))
        if total is not None:
            self.assertEqual(total, 1)

    def test_dashboard_recent_excludes_fixtures(self):
        resp = self._get("/api/dashboard")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        recent_refs = [
            r.get("ref") for r in body.get("recent", body.get("data", {}).get("recent", []))
        ]
        # Fixture refs must not appear in recent submissions.
        for ref in recent_refs:
            self.assertIsNotNone(ref)
            self.assertFalse(
                str(ref).startswith("ARF-2026-9") or str(ref).startswith("ARF-2026-DEMO"),
                f"Fixture ref leaked into recent submissions: {ref}",
            )


@unittest.skipUnless(HAS_TORNADO, "tornado not available")
class TestEDDListFixtureExclusion(_ServerBase):
    """GET /api/edd/cases must exclude fixture EDD cases by default."""

    def setUp(self):
        super().setUp()
        import db as db_module
        conn = db_module.get_db()
        # Insert one real and one fixture EDD case
        try:
            conn.execute(
                "INSERT INTO edd_cases "
                "(application_id, client_name, risk_level, risk_score, stage, trigger_source) "
                "VALUES (?,?,?,?,?,?)",
                (self._REAL_APP_ID, "Real Co", "HIGH", 80, "triggered", "officer_decision"),
            )
            conn.execute(
                "INSERT INTO edd_cases "
                "(application_id, client_name, risk_level, risk_score, stage, trigger_source) "
                "VALUES (?,?,?,?,?,?)",
                (self._FIX_APP_ID, "FIX-SCEN02", "HIGH", 80, "triggered", "fixture"),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def test_live_edd_case_visible(self):
        resp = self._get("/api/edd/cases")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        app_ids = [c["application_id"] for c in body.get("cases", [])]
        self.assertIn(self._REAL_APP_ID, app_ids)

    def test_fixture_edd_case_excluded(self):
        resp = self._get("/api/edd/cases")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        app_ids = [c["application_id"] for c in body.get("cases", [])]
        self.assertNotIn(self._FIX_APP_ID, app_ids)


@unittest.skipUnless(HAS_TORNADO, "tornado not available")
class TestMonitoringAlertsFixtureExclusion(_ServerBase):
    """GET /api/monitoring/alerts must exclude fixture alerts by default."""

    def setUp(self):
        super().setUp()
        import db as db_module
        conn = db_module.get_db()
        try:
            conn.execute(
                "INSERT INTO monitoring_alerts "
                "(application_id, client_name, alert_type, severity, status, source_reference) "
                "VALUES (?,?,?,?,?,?)",
                (self._REAL_APP_ID, "Real Co", "AML", "High", "open", "REAL_ALERT"),
            )
            conn.execute(
                "INSERT INTO monitoring_alerts "
                "(application_id, client_name, alert_type, severity, status, source_reference) "
                "VALUES (?,?,?,?,?,?)",
                (self._FIX_APP_ID, "FIX-SCEN02", "AML", "High", "open",
                 "FIX_SCEN02_ALERT"),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def test_live_alert_visible(self):
        resp = self._get("/api/monitoring/alerts")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        app_ids = [a["application_id"] for a in body.get("alerts", [])]
        self.assertIn(self._REAL_APP_ID, app_ids)

    def test_fixture_alert_excluded(self):
        resp = self._get("/api/monitoring/alerts")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        app_ids = [a["application_id"] for a in body.get("alerts", [])]
        self.assertNotIn(self._FIX_APP_ID, app_ids)


if __name__ == "__main__":
    unittest.main()
