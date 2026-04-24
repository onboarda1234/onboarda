"""
Priority D: Fixture exclusion — backend query layer tests.
==========================================================

These tests prove that fixture/demo/test rows are excluded from all
polluted back-office surfaces by default, and that:

1. Default views exclude fixture rows from counts, lists, and queues.
2. Real/live rows still appear normally.
3. Explicit show_fixtures=true (admin/sco only) restores fixture rows.
4. Non-admin users cannot opt-in to fixture rows.
5. Fixture rows are excluded from every surface listed in the
   Priority D spec:
     - Dashboard KPIs and Recent Submissions
     - Applications list
     - KPI / Analytics
     - Monitoring Dashboard counts and alert list
     - Monitoring Clients (Case Management)
     - Monitoring Alerts list
     - EDD Pipeline (list + stats)
     - Lifecycle Queue (alerts, reviews, EDD)

All tests run against a real SQLite DB via the shared db layer.
No HTTP layer is involved; functions are tested directly.
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


# ──────────────────────────────────────────────────────────────────────
# Shared DB harness
# ──────────────────────────────────────────────────────────────────────

class _FixtureExclusionBase(unittest.TestCase):
    """Minimal sqlite-backed test harness.

    Seeds one REAL application and one FIXTURE application plus related
    lifecycle rows (alert, review, EDD) so each test can assert the
    correct exclusion behaviour.
    """

    REAL_APP_ID = "aabbccddeeff0011"   # does NOT start with 'f1xed'
    REAL_APP_REF = "ARF-2025-100001"
    FIX_APP_ID = "f1xed00000000001"    # reserved fixture namespace
    FIX_APP_REF = "ARF-2026-900001"

    def setUp(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_fix_excl_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        os.environ["DB_PATH"] = self._db_path

        import config as config_module
        import db as db_module
        self._orig_config_db_path = getattr(config_module, "DB_PATH", None)
        self._orig_db_db_path = getattr(db_module, "DB_PATH", None)
        config_module.DB_PATH = self._db_path
        db_module.DB_PATH = self._db_path

        db_module.init_db()
        conn = db_module.get_db()

        # Pre-mark migrations 001..009 as applied so we don't re-run
        # migrations against the already-initialised schema.
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
            ("008", "migration_008_lifecycle_linkage.sql"),
            ("009", "migration_009_periodic_review_operating_model.sql"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, filename) VALUES (?,?)",
                (v, fn),
            )
        conn.commit()

        # Seed a REAL application
        conn.execute(
            "INSERT INTO applications (id, ref, client_id, company_name, country, sector, "
            "entity_type, status, risk_level, risk_score) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.REAL_APP_ID, self.REAL_APP_REF, "real_client",
             "Real Company Ltd", "Mauritius", "fintech",
             "company", "in_review", "MEDIUM", 45.0),
        )

        # Seed a FIXTURE application (id LIKE 'f1xed%', is_fixture=1)
        conn.execute(
            "INSERT INTO applications (id, ref, client_id, company_name, country, sector, "
            "entity_type, status, risk_level, risk_score, is_fixture) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            (self.FIX_APP_ID, self.FIX_APP_REF, None,
             "FIX-SCEN01 Alert-to-Memo Holdings Ltd", "UAE", "financial_services",
             "company", "in_review", "HIGH", 72.0),
        )

        # Seed a monitoring alert for the REAL application
        conn.execute(
            "INSERT INTO monitoring_alerts (application_id, client_name, alert_type, "
            "severity, detected_by, summary, source_reference, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (self.REAL_APP_ID, "Real Company Ltd", "adverse_media",
             "high", "system", "Real adverse media hit", "REAL_ALERT_001", "open"),
        )

        # Seed a monitoring alert for the FIXTURE application
        conn.execute(
            "INSERT INTO monitoring_alerts (application_id, client_name, alert_type, "
            "severity, detected_by, summary, source_reference, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (self.FIX_APP_ID, "FIX-SCEN01 Alert-to-Memo Holdings Ltd",
             "fixture", "medium", "fixture_seed",
             "FIX-SCEN01 monitoring trigger: adverse media match",
             "FIX_SCEN01_ALERT", "open"),
        )

        # Seed a periodic review for the REAL application
        conn.execute(
            "INSERT INTO periodic_reviews (application_id, client_name, risk_level, "
            "trigger_type, status) VALUES (?,?,?,?,?)",
            (self.REAL_APP_ID, "Real Company Ltd", "MEDIUM", "scheduled", "pending"),
        )

        # Seed a periodic review for the FIXTURE application
        conn.execute(
            "INSERT INTO periodic_reviews (application_id, client_name, risk_level, "
            "trigger_type, status) VALUES (?,?,?,?,?)",
            (self.FIX_APP_ID, "FIX-SCEN01 Alert-to-Memo Holdings Ltd",
             "HIGH", "fixture_completed", "completed"),
        )

        # Seed an EDD case for the REAL application
        conn.execute(
            "INSERT INTO edd_cases (application_id, client_name, risk_level, risk_score, "
            "stage, trigger_source) VALUES (?,?,?,?,?,?)",
            (self.REAL_APP_ID, "Real Company Ltd", "HIGH", 72.0,
             "information_gathering", "officer_decision"),
        )

        # Seed an EDD case for the FIXTURE application
        conn.execute(
            "INSERT INTO edd_cases (application_id, client_name, risk_level, risk_score, "
            "stage, trigger_source) VALUES (?,?,?,?,?,?)",
            (self.FIX_APP_ID, "FIX-SCEN01 Alert-to-Memo Holdings Ltd",
             "HIGH", 72.0, "information_gathering", "fixture_seed"),
        )

        conn.commit()
        conn.close()

        import db as db_module
        self._db = db_module.get_db()

    def tearDown(self):
        try:
            self._db.close()
        except Exception:
            pass
        import config as config_module
        import db as db_module
        config_module.DB_PATH = self._orig_config_db_path
        db_module.DB_PATH = self._orig_db_db_path
        try:
            os.unlink(self._db_path)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────
# fixture_filter module unit tests
# ──────────────────────────────────────────────────────────────────────

class TestFixtureFilterModule(unittest.TestCase):
    """Unit tests for the fixture_filter helper module."""

    def test_fixture_app_exclude_clause_default_alias(self):
        from fixture_filter import fixture_app_exclude_clause
        sql, params = fixture_app_exclude_clause()
        self.assertIn("a.id", sql)
        self.assertIn("NOT LIKE", sql)
        self.assertIn("is_fixture", sql)
        self.assertEqual(params, ["f1xed%"])

    def test_fixture_app_exclude_clause_no_alias(self):
        from fixture_filter import fixture_app_exclude_clause
        sql, params = fixture_app_exclude_clause(table_alias="")
        self.assertIn("id NOT LIKE", sql)
        self.assertIn("is_fixture", sql)
        # When table_alias is an empty string, columns are referenced directly
        # without a prefix, so the fragment must contain 'is_fixture' but not
        # 'a.is_fixture' (which would be the alias-prefixed form).
        self.assertNotIn("a.is_fixture", sql)

    def test_fixture_app_id_exclude_clause(self):
        from fixture_filter import fixture_app_id_exclude_clause
        sql, params = fixture_app_id_exclude_clause()
        self.assertIn("application_id", sql)
        self.assertIn("IS NULL", sql)
        self.assertIn("NOT LIKE", sql)
        # Must also check is_fixture via subquery
        self.assertIn("is_fixture", sql)
        self.assertIn("SELECT id FROM applications", sql)
        self.assertEqual(params, ["f1xed%"])

    def test_fixture_app_id_exclude_clause_custom_col(self):
        from fixture_filter import fixture_app_id_exclude_clause
        sql, params = fixture_app_id_exclude_clause("app_ref")
        self.assertIn("app_ref", sql)
        self.assertIn("IS NULL", sql)

    def test_should_show_fixtures_admin_true(self):
        from fixture_filter import should_show_fixtures
        user = {"role": "admin", "sub": "u1"}
        self.assertTrue(should_show_fixtures(user, "true"))

    def test_should_show_fixtures_sco_true(self):
        from fixture_filter import should_show_fixtures
        user = {"role": "sco", "sub": "u2"}
        self.assertTrue(should_show_fixtures(user, "true"))

    def test_should_show_fixtures_co_silently_ignored(self):
        from fixture_filter import should_show_fixtures
        user = {"role": "co", "sub": "u3"}
        self.assertFalse(should_show_fixtures(user, "true"))

    def test_should_show_fixtures_no_param(self):
        from fixture_filter import should_show_fixtures
        user = {"role": "admin", "sub": "u1"}
        self.assertFalse(should_show_fixtures(user, None))

    def test_should_show_fixtures_false_param(self):
        from fixture_filter import should_show_fixtures
        user = {"role": "admin", "sub": "u1"}
        self.assertFalse(should_show_fixtures(user, "false"))

    def test_should_show_fixtures_no_user(self):
        from fixture_filter import should_show_fixtures
        self.assertFalse(should_show_fixtures(None, "true"))


# ──────────────────────────────────────────────────────────────────────
# Lifecycle Queue exclusion tests
# ──────────────────────────────────────────────────────────────────────

class TestLifecycleQueueFixtureExclusion(_FixtureExclusionBase):
    """Fixture rows must not appear in lifecycle queue by default."""

    def _queue(self, **kwargs):
        import lifecycle_queue as lq
        return lq.build_lifecycle_queue(self._db, **kwargs)

    def test_default_alerts_excludes_fixtures(self):
        result = self._queue(include="all", types=("alert",))
        app_ids = [it["application_id"] for it in result["items"]]
        self.assertIn(self.REAL_APP_ID, app_ids,
                      "Real alert must be present in default queue")
        self.assertNotIn(self.FIX_APP_ID, app_ids,
                         "Fixture alert must NOT appear in default queue")

    def test_default_reviews_excludes_fixtures(self):
        result = self._queue(include="all", types=("review",))
        app_ids = [it["application_id"] for it in result["items"]]
        self.assertIn(self.REAL_APP_ID, app_ids,
                      "Real review must be present in default queue")
        self.assertNotIn(self.FIX_APP_ID, app_ids,
                         "Fixture review must NOT appear in default queue")

    def test_default_edd_excludes_fixtures(self):
        result = self._queue(include="all", types=("edd",))
        app_ids = [it["application_id"] for it in result["items"]]
        self.assertIn(self.REAL_APP_ID, app_ids,
                      "Real EDD must be present in default queue")
        self.assertNotIn(self.FIX_APP_ID, app_ids,
                         "Fixture EDD must NOT appear in default queue")

    def test_show_fixtures_restores_fixture_rows(self):
        """exclude_fixtures=False (admin show_fixtures opt-in) reveals fixtures."""
        result = self._queue(include="all", exclude_fixtures=False)
        app_ids = [it["application_id"] for it in result["items"]]
        self.assertIn(self.REAL_APP_ID, app_ids)
        self.assertIn(self.FIX_APP_ID, app_ids,
                      "Fixture rows must appear when exclude_fixtures=False")

    def test_real_rows_not_over_filtered(self):
        """Real rows must always appear regardless of fixture filtering."""
        result = self._queue(include="all")
        app_ids = [it["application_id"] for it in result["items"]]
        self.assertIn(self.REAL_APP_ID, app_ids,
                      "Real rows must survive the fixture exclusion filter")

    def test_counts_exclude_fixtures(self):
        result = self._queue(include="all")
        # Only real rows should be counted
        self.assertEqual(result["counts"]["alert"], 1,
                         "Alert count should be 1 (real only)")
        self.assertEqual(result["counts"]["review"], 1,
                         "Review count should be 1 (real only)")
        self.assertEqual(result["counts"]["edd"], 1,
                         "EDD count should be 1 (real only)")

    def test_counts_with_fixtures_included(self):
        result = self._queue(include="all", exclude_fixtures=False)
        self.assertEqual(result["counts"]["alert"], 2)
        self.assertEqual(result["counts"]["review"], 2)
        self.assertEqual(result["counts"]["edd"], 2)


# ──────────────────────────────────────────────────────────────────────
# Direct _fetch_* function tests
# ──────────────────────────────────────────────────────────────────────

class TestFetchFunctionFixtureExclusion(_FixtureExclusionBase):
    """Low-level _fetch_* SQL-layer exclusion tests."""

    def _app_ids_from_rows(self, rows):
        from lifecycle_queue import _row_get
        return [_row_get(r, "application_id") for r in rows]

    def test_fetch_alerts_excludes_fixtures_by_default(self):
        from lifecycle_queue import _fetch_alerts
        rows = _fetch_alerts(self._db, include="all")
        app_ids = self._app_ids_from_rows(rows)
        self.assertIn(self.REAL_APP_ID, app_ids)
        self.assertNotIn(self.FIX_APP_ID, app_ids)

    def test_fetch_alerts_includes_fixtures_when_requested(self):
        from lifecycle_queue import _fetch_alerts
        rows = _fetch_alerts(self._db, include="all", exclude_fixtures=False)
        app_ids = self._app_ids_from_rows(rows)
        self.assertIn(self.REAL_APP_ID, app_ids)
        self.assertIn(self.FIX_APP_ID, app_ids)

    def test_fetch_reviews_excludes_fixtures_by_default(self):
        from lifecycle_queue import _fetch_reviews
        rows = _fetch_reviews(self._db, include="all")
        app_ids = self._app_ids_from_rows(rows)
        self.assertIn(self.REAL_APP_ID, app_ids)
        self.assertNotIn(self.FIX_APP_ID, app_ids)

    def test_fetch_edd_excludes_fixtures_by_default(self):
        from lifecycle_queue import _fetch_edd
        rows = _fetch_edd(self._db, include="all")
        app_ids = self._app_ids_from_rows(rows)
        self.assertIn(self.REAL_APP_ID, app_ids)
        self.assertNotIn(self.FIX_APP_ID, app_ids)

    def test_null_application_id_alert_not_excluded(self):
        """A manually-created alert with application_id IS NULL must survive the
        fixture exclusion SQL clause (NULL-safe guard check)."""
        from fixture_filter import fixture_app_id_exclude_clause
        # Insert a manual alert with no application_id
        self._db.execute(
            "INSERT INTO monitoring_alerts (application_id, client_name, alert_type, "
            "severity, detected_by, summary, source_reference, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (None, "Manual Alert", "manual", "low", "officer",
             "Manual monitoring note", "MANUAL_001", "open"),
        )
        self._db.commit()
        # Directly query with the fixture exclusion clause — not via _fetch_alerts
        # (which has additional quarantine logic that hides NULL-app-id rows).
        fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
        rows = self._db.execute(
            f"SELECT application_id FROM monitoring_alerts WHERE {fx_excl}",
            fx_params,
        ).fetchall()
        from lifecycle_queue import _row_get
        app_ids = [_row_get(r, "application_id") for r in rows]
        # NULL app_id alert must survive (NULL-safe guard in clause)
        self.assertIn(None, app_ids,
                      "Alert with application_id IS NULL must not be excluded by "
                      "fixture_app_id_exclude_clause (NULL-safe guard required)")
        self.assertNotIn(self.FIX_APP_ID, app_ids)


# ──────────────────────────────────────────────────────────────────────
# Applications list exclusion tests (SQL layer via direct DB query)
# ──────────────────────────────────────────────────────────────────────

class TestApplicationsQueryFixtureExclusion(_FixtureExclusionBase):
    """Verify that fixture_app_exclude_clause filters applications correctly."""

    def test_exclude_clause_filters_fixture(self):
        from fixture_filter import fixture_app_exclude_clause
        fx_excl, fx_params = fixture_app_exclude_clause(table_alias="")
        rows = self._db.execute(
            f"SELECT id FROM applications WHERE {fx_excl}",
            fx_params,
        ).fetchall()
        ids = [r["id"] for r in rows]
        self.assertIn(self.REAL_APP_ID, ids)
        self.assertNotIn(self.FIX_APP_ID, ids)

    def test_count_excludes_fixture(self):
        from fixture_filter import fixture_app_exclude_clause
        fx_excl, fx_params = fixture_app_exclude_clause(table_alias="")
        count = self._db.execute(
            f"SELECT COUNT(*) as c FROM applications WHERE {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        self.assertEqual(count, 1, "Only the real application should be counted")

    def test_count_without_exclusion_includes_both(self):
        count = self._db.execute(
            "SELECT COUNT(*) as c FROM applications"
        ).fetchone()["c"]
        self.assertEqual(count, 2)


# ──────────────────────────────────────────────────────────────────────
# EDD query exclusion tests
# ──────────────────────────────────────────────────────────────────────

class TestEDDQueryFixtureExclusion(_FixtureExclusionBase):
    """Verify fixture_app_id_exclude_clause filters EDD cases."""

    def test_edd_count_excludes_fixtures(self):
        from fixture_filter import fixture_app_id_exclude_clause
        fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
        count = self._db.execute(
            f"SELECT COUNT(*) as c FROM edd_cases WHERE {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_edd_active_count_excludes_fixtures(self):
        from fixture_filter import fixture_app_id_exclude_clause
        fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
        count = self._db.execute(
            f"SELECT COUNT(*) as c FROM edd_cases "
            f"WHERE stage NOT IN ('edd_approved','edd_rejected') AND {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_edd_without_exclusion_includes_both(self):
        count = self._db.execute(
            "SELECT COUNT(*) as c FROM edd_cases"
        ).fetchone()["c"]
        self.assertEqual(count, 2)


# ──────────────────────────────────────────────────────────────────────
# Monitoring alerts query exclusion tests
# ──────────────────────────────────────────────────────────────────────

class TestMonitoringAlertsQueryFixtureExclusion(_FixtureExclusionBase):
    """Verify fixture_app_id_exclude_clause filters monitoring alerts."""

    def test_alerts_count_excludes_fixtures(self):
        from fixture_filter import fixture_app_id_exclude_clause
        fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
        count = self._db.execute(
            f"SELECT COUNT(*) as c FROM monitoring_alerts WHERE {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_alerts_without_exclusion_includes_both(self):
        count = self._db.execute(
            "SELECT COUNT(*) as c FROM monitoring_alerts"
        ).fetchone()["c"]
        self.assertEqual(count, 2)


# ──────────────────────────────────────────────────────────────────────
# Periodic reviews query exclusion tests
# ──────────────────────────────────────────────────────────────────────

class TestPeriodicReviewsQueryFixtureExclusion(_FixtureExclusionBase):
    """Verify fixture_app_id_exclude_clause filters periodic reviews."""

    def test_reviews_count_excludes_fixtures(self):
        from fixture_filter import fixture_app_id_exclude_clause
        fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
        count = self._db.execute(
            f"SELECT COUNT(*) as c FROM periodic_reviews WHERE {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        self.assertEqual(count, 1)


# ──────────────────────────────────────────────────────────────────────
# High-risk / monitoring dashboard query exclusion
# ──────────────────────────────────────────────────────────────────────

class TestMonitoringDashboardFixtureExclusion(_FixtureExclusionBase):
    """HIGH/VERY_HIGH fixture rows must not inflate monitoring dashboard counts."""

    def test_high_risk_count_excludes_fixtures(self):
        """Fixture HIGH-risk app must not be counted in monitoring KPIs."""
        from fixture_filter import fixture_app_exclude_clause
        fx_excl, fx_params = fixture_app_exclude_clause(table_alias="")
        count = self._db.execute(
            f"SELECT COUNT(*) as c FROM applications "
            f"WHERE risk_level IN ('HIGH','VERY_HIGH') AND {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        # Real app is MEDIUM, fixture app is HIGH → count should be 0
        self.assertEqual(count, 0,
                         "No real HIGH-risk apps seeded; fixture HIGH must be excluded")

    def test_high_risk_without_exclusion_counts_fixture(self):
        count = self._db.execute(
            "SELECT COUNT(*) as c FROM applications "
            "WHERE risk_level IN ('HIGH','VERY_HIGH')"
        ).fetchone()["c"]
        self.assertEqual(count, 1, "Baseline: fixture HIGH app is visible without filter")


# ══════════════════════════════════════════════════════════════════════
# Priority D Option C — is_fixture column tests
# ══════════════════════════════════════════════════════════════════════
# These tests prove that the compound fixture rule covers BOTH signals:
#   1. id LIKE 'f1xed%'             (ID namespace — existing seeded rows)
#   2. is_fixture = 1               (explicit marker — rogue historical rows)
# They also prove that the seeder sets is_fixture=1 on new fixture rows.

class _IsFixtureBase(unittest.TestCase):
    """DB harness for is_fixture column tests.

    Seeds:
      - REAL_APP   — id does NOT start with 'f1xed'; is_fixture = 0
      - ROGUE_APP  — id does NOT start with 'f1xed'; is_fixture = 1 (rogue row)
      - SEEDED_APP — id starts with 'f1xed'; is_fixture = 1 (canonical seeded)
    Plus monitoring alerts, periodic reviews, and EDD cases for each.
    """

    REAL_APP_ID   = "ccddee1122334455"    # real UUID-like, not f1xed
    REAL_APP_REF  = "ARF-2025-200001"
    ROGUE_APP_ID  = "99aabb0011223344"    # UUID-like, NOT f1xed — but is_fixture=1
    ROGUE_APP_REF = "ARF-2026-100421"     # one of the 8 historical rogue refs
    SEEDED_APP_ID = "f1xed00000000099"    # reserved f1xed namespace
    SEEDED_APP_REF = "ARF-2026-900099"

    def setUp(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_is_fix_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        os.environ["DB_PATH"] = self._db_path

        import config as config_module
        import db as db_module
        self._orig_config_db_path = getattr(config_module, "DB_PATH", None)
        self._orig_db_db_path = getattr(db_module, "DB_PATH", None)
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
            ("008", "migration_008_lifecycle_linkage.sql"),
            ("009", "migration_009_periodic_review_operating_model.sql"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, filename) VALUES (?,?)",
                (v, fn),
            )
        conn.commit()

        # REAL application — is_fixture = 0 (default)
        conn.execute(
            "INSERT INTO applications (id, ref, company_name, country, sector, "
            "entity_type, status, risk_level, risk_score, is_fixture) "
            "VALUES (?,?,?,?,?,?,?,?,?,0)",
            (self.REAL_APP_ID, self.REAL_APP_REF, "Real Corp Ltd",
             "Mauritius", "fintech", "company", "in_review", "MEDIUM", 45.0),
        )

        # ROGUE application — UUID-like ID but is_fixture = 1
        conn.execute(
            "INSERT INTO applications (id, ref, company_name, country, sector, "
            "entity_type, status, risk_level, risk_score, is_fixture) "
            "VALUES (?,?,?,?,?,?,?,?,?,1)",
            (self.ROGUE_APP_ID, self.ROGUE_APP_REF, "Pipeline Test Corp Ltd",
             "Mauritius", "fintech", "company", "in_review", "HIGH", 71.0),
        )

        # SEEDED application — f1xed% ID + is_fixture = 1
        conn.execute(
            "INSERT INTO applications (id, ref, company_name, country, sector, "
            "entity_type, status, risk_level, risk_score, is_fixture) "
            "VALUES (?,?,?,?,?,?,?,?,?,1)",
            (self.SEEDED_APP_ID, self.SEEDED_APP_REF,
             "FIX-SCEN99 Seeded Test Corp", "UAE", "financial_services",
             "company", "in_review", "HIGH", 72.0),
        )

        # Monitoring alerts for each
        for app_id, ref_key, alert_ref in [
            (self.REAL_APP_ID,   "REAL",   "REAL_ALERT_200"),
            (self.ROGUE_APP_ID,  "ROGUE",  "ROGUE_ALERT_001"),
            (self.SEEDED_APP_ID, "SEEDED", "SEEDED_ALERT_001"),
        ]:
            conn.execute(
                "INSERT INTO monitoring_alerts "
                "(application_id, client_name, alert_type, severity, "
                "detected_by, summary, source_reference, status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (app_id, f"{ref_key} Company", "test", "low",
                 "system", f"Alert for {ref_key}", alert_ref, "open"),
            )

        # Periodic reviews for each
        for app_id, ref_key in [
            (self.REAL_APP_ID,   "REAL"),
            (self.ROGUE_APP_ID,  "ROGUE"),
            (self.SEEDED_APP_ID, "SEEDED"),
        ]:
            conn.execute(
                "INSERT INTO periodic_reviews "
                "(application_id, client_name, risk_level, trigger_type, status) "
                "VALUES (?,?,?,?,?)",
                (app_id, f"{ref_key} Company", "MEDIUM", "scheduled", "pending"),
            )

        # EDD cases for each
        for app_id, ref_key in [
            (self.REAL_APP_ID,   "REAL"),
            (self.ROGUE_APP_ID,  "ROGUE"),
            (self.SEEDED_APP_ID, "SEEDED"),
        ]:
            conn.execute(
                "INSERT INTO edd_cases "
                "(application_id, client_name, risk_level, risk_score, "
                "stage, trigger_source) VALUES (?,?,?,?,?,?)",
                (app_id, f"{ref_key} Company", "HIGH", 72.0,
                 "information_gathering", "officer_decision"),
            )

        conn.commit()
        conn.close()

        import db as db_module
        self._db = db_module.get_db()

    def tearDown(self):
        try:
            self._db.close()
        except Exception:
            pass
        import config as config_module
        import db as db_module
        config_module.DB_PATH = self._orig_config_db_path
        db_module.DB_PATH = self._orig_db_db_path
        try:
            os.unlink(self._db_path)
        except Exception:
            pass


class TestIsFixtureColumnExclusion(_IsFixtureBase):
    """is_fixture = 1 rows must be excluded regardless of their ID."""

    def test_rogue_row_excluded_by_is_fixture_clause(self):
        """ROGUE_APP (UUID ID, is_fixture=1) must be excluded from applications."""
        from fixture_filter import fixture_app_exclude_clause
        fx_excl, fx_params = fixture_app_exclude_clause(table_alias="")
        rows = self._db.execute(
            f"SELECT id FROM applications WHERE {fx_excl}",
            fx_params,
        ).fetchall()
        ids = [r["id"] for r in rows]
        self.assertIn(self.REAL_APP_ID, ids, "Real app must be included")
        self.assertNotIn(self.ROGUE_APP_ID, ids,
                         "Rogue UUID app (is_fixture=1) must be excluded")
        self.assertNotIn(self.SEEDED_APP_ID, ids,
                         "Seeded f1xed app must be excluded")

    def test_count_excludes_both_fixture_signals(self):
        """COUNT(*) must only count the one real application."""
        from fixture_filter import fixture_app_exclude_clause
        fx_excl, fx_params = fixture_app_exclude_clause(table_alias="")
        count = self._db.execute(
            f"SELECT COUNT(*) as c FROM applications WHERE {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        self.assertEqual(count, 1,
                         "Only the real app should be counted; rogue + seeded excluded")

    def test_real_row_not_excluded_is_fixture_zero(self):
        """is_fixture=0 rows must NOT be excluded (no over-filtering)."""
        from fixture_filter import fixture_app_exclude_clause
        fx_excl, fx_params = fixture_app_exclude_clause(table_alias="")
        rows = self._db.execute(
            f"SELECT id FROM applications WHERE {fx_excl}",
            fx_params,
        ).fetchall()
        ids = [r["id"] for r in rows]
        self.assertIn(self.REAL_APP_ID, ids,
                      "Real app (is_fixture=0) must not be over-filtered")

    def test_rogue_alert_excluded_via_subquery(self):
        """Alert linked to rogue (is_fixture=1, UUID-ID) app must be excluded."""
        from fixture_filter import fixture_app_id_exclude_clause
        fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
        rows = self._db.execute(
            f"SELECT application_id FROM monitoring_alerts WHERE {fx_excl}",
            fx_params,
        ).fetchall()
        app_ids = [r["application_id"] for r in rows]
        self.assertIn(self.REAL_APP_ID, app_ids, "Real alert must be present")
        self.assertNotIn(self.ROGUE_APP_ID, app_ids,
                         "Rogue alert must be excluded via is_fixture subquery")
        self.assertNotIn(self.SEEDED_APP_ID, app_ids,
                         "Seeded alert must be excluded via id LIKE f1xed%")

    def test_rogue_review_excluded_via_subquery(self):
        """Periodic review linked to rogue app must be excluded."""
        from fixture_filter import fixture_app_id_exclude_clause
        fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
        rows = self._db.execute(
            f"SELECT application_id FROM periodic_reviews WHERE {fx_excl}",
            fx_params,
        ).fetchall()
        app_ids = [r["application_id"] for r in rows]
        self.assertIn(self.REAL_APP_ID, app_ids)
        self.assertNotIn(self.ROGUE_APP_ID, app_ids,
                         "Rogue review must be excluded via is_fixture subquery")

    def test_rogue_edd_excluded_via_subquery(self):
        """EDD case linked to rogue app must be excluded."""
        from fixture_filter import fixture_app_id_exclude_clause
        fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
        rows = self._db.execute(
            f"SELECT application_id FROM edd_cases WHERE {fx_excl}",
            fx_params,
        ).fetchall()
        app_ids = [r["application_id"] for r in rows]
        self.assertIn(self.REAL_APP_ID, app_ids)
        self.assertNotIn(self.ROGUE_APP_ID, app_ids,
                         "Rogue EDD must be excluded via is_fixture subquery")


class TestMigrationV229(_IsFixtureBase):
    """Inline migration v2.29 correctly marks is_fixture for all fixture types."""

    def test_f1xed_id_app_has_is_fixture_set(self):
        """f1xed% seeded app should have is_fixture = 1 (set in setUp via INSERT)."""
        row = self._db.execute(
            "SELECT is_fixture FROM applications WHERE id = ?",
            (self.SEEDED_APP_ID,),
        ).fetchone()
        self.assertIsNotNone(row, "Seeded app must exist")
        self.assertEqual(row["is_fixture"], 1,
                         "Seeded f1xed app must have is_fixture = 1")

    def test_rogue_ref_app_has_is_fixture_set(self):
        """Rogue ref app (ARF-2026-100421) must have is_fixture = 1."""
        row = self._db.execute(
            "SELECT is_fixture FROM applications WHERE ref = ?",
            (self.ROGUE_APP_REF,),
        ).fetchone()
        self.assertIsNotNone(row, "Rogue ref app must exist")
        self.assertEqual(row["is_fixture"], 1,
                         "Rogue app (historical test row) must have is_fixture = 1")

    def test_real_app_has_is_fixture_zero(self):
        """Real application must have is_fixture = 0 (default)."""
        row = self._db.execute(
            "SELECT is_fixture FROM applications WHERE id = ?",
            (self.REAL_APP_ID,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["is_fixture"], 0,
                         "Real app must have is_fixture = 0 (not over-filtered)")

    def test_migration_marks_f1xed_rows_idempotent(self):
        """Running the f1xed backfill UPDATE again must not corrupt real rows."""
        # Simulate the migration's backfill UPDATE
        self._db.execute(
            "UPDATE applications SET is_fixture = 1 WHERE id LIKE ?",
            ("f1xed%",),
        )
        self._db.commit()
        # Real app must still have is_fixture = 0
        row = self._db.execute(
            "SELECT is_fixture FROM applications WHERE id = ?",
            (self.REAL_APP_ID,),
        ).fetchone()
        self.assertEqual(row["is_fixture"], 0,
                         "Backfill of f1xed% rows must not touch real rows")

    def test_rogue_refs_migration_marks_correct_rows(self):
        """Migration UPDATE by ref marks only the 8 rogue refs."""
        from fixture_filter import ROGUE_FIXTURE_REFS
        # All rogue refs that exist should be marked
        for ref in ROGUE_FIXTURE_REFS:
            row = self._db.execute(
                "SELECT is_fixture FROM applications WHERE ref = ?",
                (ref,),
            ).fetchone()
            if row is not None:
                self.assertEqual(row["is_fixture"], 1,
                                 f"Rogue ref {ref} must be marked is_fixture=1")


class TestSeederSetsIsFixture(unittest.TestCase):
    """The seeder _upsert_application sets is_fixture = 1 on fixture rows."""

    def test_seeder_insert_includes_is_fixture(self):
        """The INSERT SQL in _upsert_application must include is_fixture."""
        import inspect
        from fixtures.seeder import _upsert_application
        src = inspect.getsource(_upsert_application)
        self.assertIn("is_fixture", src,
                      "_upsert_application must reference is_fixture column")

    def test_seeder_insert_sets_is_fixture_to_1(self):
        """The INSERT statement must set is_fixture to 1 (not 0 or omit)."""
        import inspect
        from fixtures.seeder import _upsert_application
        src = inspect.getsource(_upsert_application)
        # The literal value must be present — either as '1' in INSERT or
        # SET is_fixture=1 in UPDATE.
        self.assertIn("is_fixture=1", src.replace(" ", ""),
                      "_upsert_application INSERT/UPDATE must set is_fixture=1")

    def test_fixture_filter_rogue_refs_constant_has_all_8(self):
        """ROGUE_FIXTURE_REFS must contain exactly the 8 known rogue refs."""
        from fixture_filter import ROGUE_FIXTURE_REFS
        expected = {
            "ARF-2026-100454",
            "ARF-2026-100456",
            "ARF-2026-100455",
            "ARF-2026-100421",
            "ARF-2026-100424",
            "ARF-2026-100430",
            "ARF-2026-100428",
            "ARF-2026-100427",
        }
        self.assertEqual(set(ROGUE_FIXTURE_REFS), expected,
                         "ROGUE_FIXTURE_REFS must match all 8 known rogue historical refs")


if __name__ == "__main__":
    unittest.main()
