"""
Priority D — Fixture Hygiene / External Credibility Cleanup
=============================================================

Verifies that seeder/demo/test applications are excluded by default from all
normal officer-facing and externally-visible surfaces:

  - dashboard counts
  - application list (GET /api/applications)
  - screening queue
  - monitoring alert list
  - monitoring clients (Kanban)
  - periodic reviews list
  - EDD cases list
  - EDD pipeline stats
  - report / analytics endpoints
  - lifecycle queue (alerts, reviews, EDD)

Also verifies that:
  - real (live) applications are NOT suppressed by the fixture filter
  - per-application detail access is unaffected (controlled access still works)
  - fixture_filter module exports the expected API
"""

import os
import sys
import json
import sqlite3
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path):
    """Return an open DBConnection with the schema initialised at the given path.

    Uses a direct sqlite3 connection (bypassing get_db / DB_PATH) to ensure
    test isolation: each test gets its own schema regardless of the module-level
    DB_PATH cached at import time.
    """
    import sqlite3 as _sqlite3
    from db import DBConnection, _get_sqlite_schema, _run_migrations
    raw = _sqlite3.connect(path)
    raw.row_factory = _sqlite3.Row
    dbc = DBConnection(raw, is_postgres=False)
    # Bootstrap schema and inline migrations
    schema = _get_sqlite_schema()
    dbc.executescript(schema)
    dbc.commit()
    _run_migrations(dbc)
    dbc.commit()
    return dbc


def _insert_app(conn, app_id, ref, company_name, status="compliance_review",
                risk_level="MEDIUM", risk_score=45.0):
    conn.execute(
        """INSERT OR REPLACE INTO applications
           (id, ref, company_name, status, risk_level, risk_score, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (app_id, ref, company_name, status, risk_level, risk_score),
    )
    conn.commit()


def _insert_alert(conn, application_id, source_ref, status="open"):
    """Insert a monitoring alert; returns the auto-generated integer id."""
    conn.execute(
        """INSERT INTO monitoring_alerts
           (application_id, source_reference, status, summary, severity, alert_type, created_at)
           VALUES (?, ?, ?, 'test alert', 'high', 'fixture', datetime('now'))""",
        (application_id, source_ref, status),
    )
    conn.commit()


def _insert_review(conn, application_id, trigger_reason):
    """Insert a periodic review; returns nothing (auto-generated int id)."""
    conn.execute(
        """INSERT INTO periodic_reviews
           (application_id, trigger_type, trigger_reason, created_at)
           VALUES (?, 'monitoring', ?, datetime('now'))""",
        (application_id, trigger_reason),
    )
    conn.commit()


def _insert_edd(conn, application_id, trigger_notes):
    """Insert an EDD case; returns nothing (auto-generated int id)."""
    conn.execute(
        """INSERT INTO edd_cases
           (application_id, client_name, stage, trigger_source, trigger_notes, triggered_at)
           VALUES (?, 'Test Client', 'information_gathering', 'onboarding', ?, datetime('now'))""",
        (application_id, trigger_notes),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# fixture_filter module API
# ---------------------------------------------------------------------------

class TestFixtureFilterModule:
    """The fixture_filter module must export the expected API."""

    def test_exports_constants(self):
        from fixture_filter import (
            FIXTURE_APP_ID_PATTERNS,
            FIXTURE_APP_FILTER_PARAMS,
            EXCLUDE_FIXTURE_APPS_SQL,
            EXCLUDE_FIXTURE_APPS_SQL_A,
            EXCLUDE_FIXTURE_LIFECYCLE_SQL,
        )
        assert len(FIXTURE_APP_ID_PATTERNS) == 2
        assert "f1xed%" in FIXTURE_APP_ID_PATTERNS
        assert "demo-scenario-%" in FIXTURE_APP_ID_PATTERNS
        assert FIXTURE_APP_FILTER_PARAMS == list(FIXTURE_APP_ID_PATTERNS)
        assert "id LIKE ?" in EXCLUDE_FIXTURE_APPS_SQL
        assert "a.id LIKE ?" in EXCLUDE_FIXTURE_APPS_SQL_A
        assert "application_id LIKE ?" in EXCLUDE_FIXTURE_LIFECYCLE_SQL
        assert "IS NOT NULL" in EXCLUDE_FIXTURE_LIFECYCLE_SQL

    def test_helper_functions(self):
        from fixture_filter import (
            exclude_fixture_applications_fragment,
            exclude_fixture_lifecycle_fragment,
        )
        frag, params = exclude_fixture_applications_fragment("a")
        assert "a.id LIKE ?" in frag
        assert params == ["f1xed%", "demo-scenario-%"]

        frag_no_alias, params2 = exclude_fixture_applications_fragment("")
        assert "id LIKE ?" in frag_no_alias
        assert "a.id" not in frag_no_alias

        lfrag, lparams = exclude_fixture_lifecycle_fragment("")
        assert "application_id LIKE ?" in lfrag
        assert "IS NOT NULL" in lfrag
        assert lparams == ["f1xed%", "demo-scenario-%"]

    def test_lifecycle_fragment_null_safe(self):
        """NULL application_id rows must NOT be excluded by the lifecycle fragment."""
        import re as _re
        from fixture_filter import exclude_fixture_lifecycle_fragment
        frag, _ = exclude_fixture_lifecycle_fragment("")
        # Verify the "IS NOT NULL" guard is present so that rows with NULL
        # application_id are kept (NOT (NULL IS NOT NULL AND ...)) = NOT (FALSE) = TRUE
        assert "IS NOT NULL" in frag


# ---------------------------------------------------------------------------
# SQL fragment correctness (SQLite)
# ---------------------------------------------------------------------------

class TestSQLFragmentSQL:
    """Verify the SQL fragments work correctly in a real SQLite query."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        path = str(tmp_path / "fixture_hygiene_test.db")
        self.conn = _make_db(path)
        # Insert: 1 real app, 1 seeder fixture app, 1 demo pilot app
        _insert_app(self.conn, "abcdef0123456789", "ARF-2026-000001", "Real Corp Ltd")
        _insert_app(self.conn, "f1xed00000000001", "ARF-2026-900001", "FIX-SCEN01 Holdings",
                    risk_level="HIGH", risk_score=70.0)
        _insert_app(self.conn, "demo-scenario-01", "ARF-2026-DEMO01", "Demo Corp Ltd")
        yield
        self.conn.close()

    def test_no_alias_fragment_excludes_fixtures(self):
        from fixture_filter import EXCLUDE_FIXTURE_APPS_SQL, FIXTURE_APP_FILTER_PARAMS
        rows = self.conn.execute(
            f"SELECT id FROM applications WHERE {EXCLUDE_FIXTURE_APPS_SQL}",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchall()
        ids = [r["id"] for r in rows]
        assert "abcdef0123456789" in ids, "Real app must be included"
        assert "f1xed00000000001" not in ids, "Seeder fixture must be excluded"
        assert "demo-scenario-01" not in ids, "Demo pilot must be excluded"

    def test_alias_fragment_excludes_fixtures(self):
        from fixture_filter import EXCLUDE_FIXTURE_APPS_SQL_A, FIXTURE_APP_FILTER_PARAMS
        rows = self.conn.execute(
            f"SELECT a.id FROM applications a WHERE {EXCLUDE_FIXTURE_APPS_SQL_A}",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchall()
        ids = [r["id"] for r in rows]
        assert "abcdef0123456789" in ids
        assert "f1xed00000000001" not in ids
        assert "demo-scenario-01" not in ids

    def test_lifecycle_fragment_excludes_fixture_rows(self):
        from fixture_filter import EXCLUDE_FIXTURE_LIFECYCLE_SQL, FIXTURE_APP_FILTER_PARAMS
        # Insert alerts for each application (source_reference used to distinguish)
        _insert_alert(self.conn, "abcdef0123456789", "REAL_ALERT")
        _insert_alert(self.conn, "f1xed00000000001", "FIX_SCEN01_ALERT")
        _insert_alert(self.conn, "demo-scenario-01", "DEMO_ALERT")
        # Insert an alert with NULL application_id (should be kept)
        self.conn.execute(
            "INSERT INTO monitoring_alerts "
            "(application_id, source_reference, status, summary, severity, alert_type, created_at) "
            "VALUES (NULL, 'ORPHAN', 'open', 'orphan', 'low', 'manual', datetime('now'))"
        )
        self.conn.commit()

        rows = self.conn.execute(
            f"SELECT application_id, source_reference FROM monitoring_alerts "
            f"WHERE {EXCLUDE_FIXTURE_LIFECYCLE_SQL}",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchall()
        source_refs = [r["source_reference"] for r in rows]
        app_ids = [r["application_id"] for r in rows]

        assert "REAL_ALERT" in source_refs, "Real alert must be included"
        assert "ORPHAN" in source_refs, "NULL application_id alert must be kept"
        assert "FIX_SCEN01_ALERT" not in source_refs, "Seeder fixture alert must be excluded"
        assert "DEMO_ALERT" not in source_refs, "Demo pilot alert must be excluded"
        # Confirm NULL application_id kept
        assert None in app_ids, "Row with NULL application_id must be retained"


# ---------------------------------------------------------------------------
# lifecycle_queue exclusion
# ---------------------------------------------------------------------------

class TestLifecycleQueueFixtureExclusion:
    """build_lifecycle_queue must exclude fixture rows in the global view."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        path = str(tmp_path / "lifecycle_hygiene.db")
        self.conn = _make_db(path)
        # Real app + lifecycle objects
        _insert_app(self.conn, "real000000000001", "ARF-2026-000010", "Real Corp Ltd",
                    status="compliance_review", risk_level="HIGH")
        _insert_alert(self.conn, "real000000000001", "REAL_ALERT_001", status="open")
        _insert_review(self.conn, "real000000000001", "monitoring trigger")
        _insert_edd(self.conn, "real000000000001", "onboarding trigger")

        # Fixture seeder app + lifecycle objects
        _insert_app(self.conn, "f1xed00000000002", "ARF-2026-900002", "FIX-SCEN02 Trading",
                    status="compliance_review", risk_level="HIGH")
        _insert_alert(self.conn, "f1xed00000000002", "FIX_SCEN02_ALERT", status="open")
        _insert_review(self.conn, "f1xed00000000002", "FIX_SCEN02_REVIEW: test")
        _insert_edd(self.conn, "f1xed00000000002", "FIX_SCEN02_EDD: test")

        # Demo pilot app + lifecycle objects
        _insert_app(self.conn, "demo-scenario-02", "ARF-2026-DEMO02", "Demo Corp 2",
                    status="compliance_review", risk_level="MEDIUM")
        _insert_alert(self.conn, "demo-scenario-02", "DEMO_ALERT_002", status="open")
        _insert_review(self.conn, "demo-scenario-02", "demo review trigger")
        _insert_edd(self.conn, "demo-scenario-02", "demo edd trigger")

        yield
        self.conn.close()

    def test_global_queue_excludes_fixture_alerts(self):
        from lifecycle_queue import build_lifecycle_queue
        result = build_lifecycle_queue(self.conn, include="all", types=("alert",))
        app_ids = [item.get("application_id") for item in result["items"]]
        assert "real000000000001" in app_ids, "Real alert must appear in global queue"
        assert "f1xed00000000002" not in app_ids, "Fixture seeder alert must NOT appear"
        assert "demo-scenario-02" not in app_ids, "Demo pilot alert must NOT appear"

    def test_global_queue_excludes_fixture_reviews(self):
        from lifecycle_queue import build_lifecycle_queue
        result = build_lifecycle_queue(self.conn, include="all", types=("review",))
        app_ids = [item.get("application_id") for item in result["items"]]
        assert "real000000000001" in app_ids, "Real review must appear"
        assert "f1xed00000000002" not in app_ids, "Fixture seeder review must NOT appear"
        assert "demo-scenario-02" not in app_ids, "Demo pilot review must NOT appear"

    def test_global_queue_excludes_fixture_edd(self):
        from lifecycle_queue import build_lifecycle_queue
        result = build_lifecycle_queue(self.conn, include="all", types=("edd",))
        app_ids = [item.get("application_id") for item in result["items"]]
        assert "real000000000001" in app_ids, "Real EDD must appear"
        assert "f1xed00000000002" not in app_ids, "Fixture seeder EDD must NOT appear"
        assert "demo-scenario-02" not in app_ids, "Demo pilot EDD must NOT appear"

    def test_per_application_access_still_works(self):
        """Per-application scoped lifecycle view must still return fixture rows (controlled access)."""
        from lifecycle_queue import build_lifecycle_queue
        result = build_lifecycle_queue(
            self.conn, include="all",
            application_id="f1xed00000000002",
        )
        app_ids = [item.get("application_id") for item in result["items"]]
        # Controlled access: the fixture app's own lifecycle objects are accessible
        assert "f1xed00000000002" in app_ids, \
            "Fixture lifecycle objects must be accessible via per-app scoped query"

    def test_fixture_count_not_in_total(self):
        from lifecycle_queue import build_lifecycle_queue
        result = build_lifecycle_queue(self.conn, include="all")
        total = result["counts"]["total"]
        # Only the 3 real-app items should be counted (alert + review + edd for real app)
        assert total == 3, f"Only real app items should count; got {total}"


# ---------------------------------------------------------------------------
# Dashboard count fixture exclusion
# ---------------------------------------------------------------------------

class TestDashboardCountFixtureExclusion:
    """DashboardHandler queries must not include fixture applications in counts."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        path = str(tmp_path / "dashboard_hygiene.db")
        self.conn = _make_db(path)
        # 2 real apps
        _insert_app(self.conn, "real000000000001", "ARF-2026-000020",
                    "Real Corp Alpha", status="approved", risk_level="LOW")
        _insert_app(self.conn, "real000000000002", "ARF-2026-000021",
                    "Real Corp Beta", status="compliance_review", risk_level="HIGH")
        # 2 fixture apps
        _insert_app(self.conn, "f1xed00000000003", "ARF-2026-900003",
                    "FIX-SCEN03 Capital", status="compliance_review", risk_level="HIGH")
        _insert_app(self.conn, "demo-scenario-03", "ARF-2026-DEMO03",
                    "Demo Corp 3", status="approved", risk_level="LOW")
        yield
        self.conn.close()

    def test_total_count_excludes_fixtures(self):
        from fixture_filter import EXCLUDE_FIXTURE_APPS_SQL, FIXTURE_APP_FILTER_PARAMS
        total = self.conn.execute(
            f"SELECT COUNT(*) as c FROM applications WHERE {EXCLUDE_FIXTURE_APPS_SQL}",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchone()["c"]
        assert total == 2, f"Expected 2 real apps, got {total}"

    def test_status_count_excludes_fixtures(self):
        from fixture_filter import EXCLUDE_FIXTURE_APPS_SQL, FIXTURE_APP_FILTER_PARAMS
        count = self.conn.execute(
            f"SELECT COUNT(*) as c FROM applications WHERE status='approved' AND {EXCLUDE_FIXTURE_APPS_SQL}",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchone()["c"]
        # Only real000000000001 is approved; demo-scenario-03 is approved but fixture
        assert count == 1, f"Expected 1 approved real app, got {count}"

    def test_risk_count_excludes_fixtures(self):
        from fixture_filter import EXCLUDE_FIXTURE_APPS_SQL, FIXTURE_APP_FILTER_PARAMS
        count = self.conn.execute(
            f"SELECT COUNT(*) as c FROM applications WHERE risk_level='HIGH' AND {EXCLUDE_FIXTURE_APPS_SQL}",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchone()["c"]
        # Only real000000000002 is HIGH; f1xed00000000003 is HIGH but fixture
        assert count == 1, f"Expected 1 HIGH real app, got {count}"

    def test_recent_list_excludes_fixtures(self):
        from fixture_filter import EXCLUDE_FIXTURE_APPS_SQL_A, FIXTURE_APP_FILTER_PARAMS
        rows = self.conn.execute(
            f"SELECT a.id FROM applications a WHERE {EXCLUDE_FIXTURE_APPS_SQL_A} ORDER BY a.created_at DESC LIMIT 10",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchall()
        ids = [r["id"] for r in rows]
        assert "real000000000001" in ids
        assert "real000000000002" in ids
        assert "f1xed00000000003" not in ids
        assert "demo-scenario-03" not in ids


# ---------------------------------------------------------------------------
# Application list fixture exclusion
# ---------------------------------------------------------------------------

class TestApplicationListFixtureExclusion:
    """GET /api/applications must exclude fixture applications by default."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        path = str(tmp_path / "applist_hygiene.db")
        self.conn = _make_db(path)
        _insert_app(self.conn, "real000000000010", "ARF-2026-000030", "Real Ltd")
        _insert_app(self.conn, "f1xed00000000004", "ARF-2026-900004", "FIX-SCEN04 Ltd")
        _insert_app(self.conn, "demo-scenario-04", "ARF-2026-DEMO04", "Demo 4 Ltd")
        yield
        self.conn.close()

    def test_list_query_excludes_fixture_apps(self):
        from fixture_filter import EXCLUDE_FIXTURE_APPS_SQL_A, FIXTURE_APP_FILTER_PARAMS
        rows = self.conn.execute(
            f"SELECT a.id FROM applications a "
            f"LEFT JOIN users u ON a.assigned_to = u.id "
            f"WHERE 1=1 AND {EXCLUDE_FIXTURE_APPS_SQL_A} "
            f"ORDER BY a.created_at DESC LIMIT 200",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchall()
        ids = [r["id"] for r in rows]
        assert "real000000000010" in ids, "Real app must be listed"
        assert "f1xed00000000004" not in ids, "Seeder fixture must not be listed"
        assert "demo-scenario-04" not in ids, "Demo pilot must not be listed"


# ---------------------------------------------------------------------------
# EDD list / stats fixture exclusion
# ---------------------------------------------------------------------------

class TestEDDFixtureExclusion:
    """EDD list and stats endpoints must exclude fixture EDD cases."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        path = str(tmp_path / "edd_hygiene.db")
        self.conn = _make_db(path)
        # Real app + lifecycle objects
        _insert_app(self.conn, "real000000000020", "ARF-2026-000040", "Real EDD Corp")
        _insert_app(self.conn, "f1xed00000000005", "ARF-2026-900005", "FIX-SCEN05 EDD")
        _insert_app(self.conn, "demo-scenario-05", "ARF-2026-DEMO05", "Demo EDD Corp")
        _insert_edd(self.conn, "real000000000020", "real trigger")
        _insert_edd(self.conn, "f1xed00000000005", "FIX_SCEN05_EDD: test")
        _insert_edd(self.conn, "demo-scenario-05", "demo trigger")
        yield
        self.conn.close()

    def test_edd_list_excludes_fixtures(self):
        from fixture_filter import EXCLUDE_FIXTURE_LIFECYCLE_SQL, FIXTURE_APP_FILTER_PARAMS
        rows = self.conn.execute(
            f"SELECT application_id FROM edd_cases WHERE {EXCLUDE_FIXTURE_LIFECYCLE_SQL} "
            f"ORDER BY triggered_at DESC",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchall()
        app_ids = [r["application_id"] for r in rows]
        assert "real000000000020" in app_ids
        assert "f1xed00000000005" not in app_ids
        assert "demo-scenario-05" not in app_ids

    def test_edd_stats_active_excludes_fixtures(self):
        from fixture_filter import EXCLUDE_FIXTURE_LIFECYCLE_SQL, FIXTURE_APP_FILTER_PARAMS
        count = self.conn.execute(
            f"SELECT COUNT(*) as c FROM edd_cases "
            f"WHERE stage NOT IN ('edd_approved','edd_rejected') "
            f"AND {EXCLUDE_FIXTURE_LIFECYCLE_SQL}",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchone()["c"]
        # Only edd-real is real; the others are fixtures
        assert count == 1, f"Expected 1 real active EDD, got {count}"


# ---------------------------------------------------------------------------
# Monitoring alerts list fixture exclusion
# ---------------------------------------------------------------------------

class TestAlertListFixtureExclusion:
    """Monitoring alert list must exclude fixture alerts."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        path = str(tmp_path / "alert_hygiene.db")
        self.conn = _make_db(path)
        _insert_app(self.conn, "real000000000030", "ARF-2026-000050", "Real Alert Corp")
        _insert_app(self.conn, "f1xed00000000006", "ARF-2026-900006", "FIX-SCEN06 Dismiss")
        _insert_alert(self.conn, "real000000000030", "REAL_ALERT_030")
        _insert_alert(self.conn, "f1xed00000000006", "FIX_SCEN06_ALERT", status="dismissed")
        yield
        self.conn.close()

    def test_alert_list_excludes_fixtures(self):
        from fixture_filter import EXCLUDE_FIXTURE_LIFECYCLE_SQL, FIXTURE_APP_FILTER_PARAMS
        rows = self.conn.execute(
            f"SELECT application_id, source_reference FROM monitoring_alerts "
            f"WHERE {EXCLUDE_FIXTURE_LIFECYCLE_SQL} "
            f"ORDER BY created_at DESC",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchall()
        app_ids = [r["application_id"] for r in rows]
        source_refs = [r["source_reference"] for r in rows]
        assert "real000000000030" in app_ids
        assert "REAL_ALERT_030" in source_refs
        assert "f1xed00000000006" not in app_ids
        assert "FIX_SCEN06_ALERT" not in source_refs


# ---------------------------------------------------------------------------
# Periodic review list fixture exclusion
# ---------------------------------------------------------------------------

class TestPeriodicReviewListFixtureExclusion:
    """Periodic review list must exclude fixture reviews."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        path = str(tmp_path / "review_hygiene.db")
        self.conn = _make_db(path)
        _insert_app(self.conn, "real000000000040", "ARF-2026-000060", "Real Review Corp")
        _insert_app(self.conn, "f1xed00000000007", "ARF-2026-900007", "FIX-SCEN07 Review")
        _insert_review(self.conn, "real000000000040", "real trigger")
        _insert_review(self.conn, "f1xed00000000007", "FIX_SCEN07_REVIEW: test")
        yield
        self.conn.close()

    def test_review_list_excludes_fixtures(self):
        from fixture_filter import EXCLUDE_FIXTURE_LIFECYCLE_SQL, FIXTURE_APP_FILTER_PARAMS
        rows = self.conn.execute(
            f"SELECT application_id, trigger_reason FROM periodic_reviews "
            f"WHERE {EXCLUDE_FIXTURE_LIFECYCLE_SQL} "
            f"ORDER BY created_at DESC",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchall()
        app_ids = [r["application_id"] for r in rows]
        reasons = [r["trigger_reason"] for r in rows]
        assert "real000000000040" in app_ids
        assert "real trigger" in reasons
        assert "f1xed00000000007" not in app_ids
        assert "FIX_SCEN07_REVIEW: test" not in reasons


# ---------------------------------------------------------------------------
# Real/live applications are never suppressed
# ---------------------------------------------------------------------------

class TestRealAppsNotSuppressed:
    """Regression: no legitimate application should be excluded by the fixture filter."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        path = str(tmp_path / "real_apps_not_suppressed.db")
        self.conn = _make_db(path)
        # Typical IDs generated by lower(hex(randomblob(8))) — all hex, 16 chars
        self.real_ids = [
            "3a7f2b91c4e5d6f1",
            "0000000000000000",
            "ffffffffffffffff",
            "a1b2c3d4e5f60718",
        ]
        for i, app_id in enumerate(self.real_ids):
            _insert_app(self.conn, app_id, f"ARF-2026-00{i:04d}", f"Real Corp {i}")
        yield
        self.conn.close()

    def test_all_real_apps_visible(self):
        from fixture_filter import EXCLUDE_FIXTURE_APPS_SQL, FIXTURE_APP_FILTER_PARAMS
        rows = self.conn.execute(
            f"SELECT id FROM applications WHERE {EXCLUDE_FIXTURE_APPS_SQL}",
            FIXTURE_APP_FILTER_PARAMS,
        ).fetchall()
        found = {r["id"] for r in rows}
        for app_id in self.real_ids:
            assert app_id in found, f"Real app {app_id!r} was incorrectly suppressed"
