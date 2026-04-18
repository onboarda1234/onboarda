"""
Tests for migration_008_lifecycle_linkage.sql -- PR-01 foundation.

Verifies:
  * new nullable columns are present on edd_cases, periodic_reviews,
    and monitoring_alerts after the migration runs;
  * soft-reference indexes exist;
  * existing rows survive the migration with NULL in the new fields;
  * the migration is idempotent (second run is a no-op via the
    schema_version gate + ADD COLUMN IF NOT EXISTS guard);
  * no table outside the three lifecycle tables is modified.
"""

import os
import sys
import sqlite3

import pytest

# Make arie-backend importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


LIFECYCLE_EXPECTED_COLUMNS = {
    "edd_cases": [
        "origin_context",
        "linked_monitoring_alert_id",
        "linked_periodic_review_id",
        "assigned_at",
        "escalated_at",
        "closed_at",
        "sla_due_at",
        "priority",
    ],
    "periodic_reviews": [
        "trigger_source",
        "linked_monitoring_alert_id",
        "linked_edd_case_id",
        "review_reason",
        "assigned_at",
        "closed_at",
        "sla_due_at",
        "priority",
    ],
    "monitoring_alerts": [
        "linked_periodic_review_id",
        "linked_edd_case_id",
        "triaged_at",
        "assigned_at",
        "resolved_at",
    ],
}

EXPECTED_INDEXES = [
    "idx_edd_cases_linked_alert",
    "idx_edd_cases_linked_review",
    "idx_edd_cases_origin_context",
    "idx_periodic_reviews_linked_alert",
    "idx_periodic_reviews_linked_edd",
    "idx_periodic_reviews_trigger_source",
    "idx_monitoring_alerts_linked_edd",
    "idx_monitoring_alerts_linked_review",
]


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    """
    Bootstrap a fresh SQLite DB with the repository schema, insert one
    row per lifecycle table so we can check survival, then apply
    migration 008 via the project's migration runner.
    """
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    import importlib
    import db as db_module
    importlib.reload(db_module)
    db_module._DB_PATH = str(tmp_path / "test.db")
    db_module.init_db()

    conn = db_module.get_db()

    # Seed minimal rows so the "existing rows survive" assertion is meaningful.
    # edd_cases and periodic_reviews require an application_id; use a
    # lightweight applications row if needed.
    try:
        conn.execute(
            "INSERT INTO applications (id, status) VALUES (?, ?)",
            ("test-app-001", "submitted"),
        )
    except Exception:
        # applications may have more NOT NULL columns; try a permissive insert
        conn.execute(
            "INSERT OR IGNORE INTO applications (id) VALUES (?)",
            ("test-app-001",),
        )

    conn.execute(
        "INSERT INTO edd_cases (application_id, client_name, stage) "
        "VALUES (?, ?, ?)",
        ("test-app-001", "Pre-migration Client", "triggered"),
    )
    conn.execute(
        "INSERT INTO monitoring_alerts (application_id, client_name, alert_type, severity, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ("test-app-001", "Pre-migration Client", "adverse_media", "medium", "open"),
    )
    conn.execute(
        "INSERT INTO periodic_reviews (application_id, client_name) "
        "VALUES (?, ?)",
        ("test-app-001", "Pre-migration Client"),
    )
    conn.commit()

    # Apply all pending migrations (including 008) via the project runner.
    from migrations.runner import run_all_migrations_with_connection
    run_all_migrations_with_connection(conn)

    yield conn
    conn.close()


def _column_names(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] if not hasattr(r, "get") else r.get("name") for r in rows]


def _index_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    return [r[0] if not hasattr(r, "get") else r.get("name") for r in rows]


class TestLifecycleLinkageColumns:
    def test_edd_cases_new_columns_present(self, migrated_db):
        cols = set(_column_names(migrated_db, "edd_cases"))
        for c in LIFECYCLE_EXPECTED_COLUMNS["edd_cases"]:
            assert c in cols, f"edd_cases missing column {c}"

    def test_periodic_reviews_new_columns_present(self, migrated_db):
        cols = set(_column_names(migrated_db, "periodic_reviews"))
        for c in LIFECYCLE_EXPECTED_COLUMNS["periodic_reviews"]:
            assert c in cols, f"periodic_reviews missing column {c}"

    def test_monitoring_alerts_new_columns_present(self, migrated_db):
        cols = set(_column_names(migrated_db, "monitoring_alerts"))
        for c in LIFECYCLE_EXPECTED_COLUMNS["monitoring_alerts"]:
            assert c in cols, f"monitoring_alerts missing column {c}"

    def test_existing_trigger_type_not_renamed(self, migrated_db):
        cols = set(_column_names(migrated_db, "periodic_reviews"))
        assert "trigger_type" in cols
        assert "trigger_reason" in cols
        assert "trigger_source" in cols  # and the new disjoint field

    def test_compliance_memos_untouched(self, migrated_db):
        cols = set(_column_names(migrated_db, "compliance_memos"))
        assert "active_memo_id" not in cols
        assert "lifecycle_origin" not in cols


class TestLifecycleLinkageIndexes:
    def test_all_expected_indexes_present(self, migrated_db):
        names = set(_index_names(migrated_db))
        for idx in EXPECTED_INDEXES:
            assert idx in names, f"index {idx} missing"


class TestExistingRowsSurvive:
    def test_seeded_edd_case_still_present_with_nulls(self, migrated_db):
        row = migrated_db.execute(
            "SELECT client_name, origin_context, linked_monitoring_alert_id, "
            "priority, sla_due_at FROM edd_cases WHERE client_name = ?",
            ("Pre-migration Client",),
        ).fetchone()
        assert row is not None
        assert row[0] == "Pre-migration Client"
        assert row[1] is None
        assert row[2] is None
        assert row[3] is None
        assert row[4] is None

    def test_seeded_periodic_review_still_present_with_nulls(self, migrated_db):
        row = migrated_db.execute(
            "SELECT client_name, trigger_source, linked_edd_case_id, review_reason "
            "FROM periodic_reviews WHERE client_name = ?",
            ("Pre-migration Client",),
        ).fetchone()
        assert row is not None
        assert row[0] == "Pre-migration Client"
        assert row[1] is None
        assert row[2] is None
        assert row[3] is None

    def test_seeded_monitoring_alert_still_present_with_nulls(self, migrated_db):
        row = migrated_db.execute(
            "SELECT client_name, linked_edd_case_id, linked_periodic_review_id, "
            "triaged_at, resolved_at FROM monitoring_alerts WHERE client_name = ?",
            ("Pre-migration Client",),
        ).fetchone()
        assert row is not None
        assert row[0] == "Pre-migration Client"
        assert row[1] is None
        assert row[2] is None
        assert row[3] is None
        assert row[4] is None


class TestMigrationIdempotency:
    def test_second_runner_invocation_is_noop(self, migrated_db):
        """A second invocation of the runner must not duplicate or error."""
        from migrations.runner import run_all_migrations_with_connection
        applied_before = migrated_db.execute(
            "SELECT COUNT(*) FROM schema_version WHERE version = ?",
            ("008",),
        ).fetchone()[0]
        assert applied_before == 1
        run_all_migrations_with_connection(migrated_db)
        applied_after = migrated_db.execute(
            "SELECT COUNT(*) FROM schema_version WHERE version = ?",
            ("008",),
        ).fetchone()[0]
        assert applied_after == 1


class TestEnumValuesAcceptedAndJunkRejectedAtAppLayer:
    """
    SQLite cannot add CHECK constraints via ALTER TABLE, so enum
    enforcement is provided by lifecycle_linkage helpers. This test
    just sanity-checks that valid enum string writes and NULLs round-
    trip through the column; invalid-junk rejection is covered in the
    companion test_lifecycle_linkage.py (helper-layer enforcement).
    """

    def test_valid_origin_context_roundtrip(self, migrated_db):
        migrated_db.execute(
            "INSERT INTO edd_cases (application_id, client_name, stage, origin_context) "
            "VALUES (?, ?, ?, ?)",
            ("test-app-001", "OriginOK", "triggered", "monitoring_alert"),
        )
        migrated_db.commit()
        row = migrated_db.execute(
            "SELECT origin_context FROM edd_cases WHERE client_name = ?",
            ("OriginOK",),
        ).fetchone()
        assert row[0] == "monitoring_alert"

    def test_null_origin_context_allowed(self, migrated_db):
        migrated_db.execute(
            "INSERT INTO edd_cases (application_id, client_name, stage) "
            "VALUES (?, ?, ?)",
            ("test-app-001", "OriginNull", "triggered"),
        )
        migrated_db.commit()
        row = migrated_db.execute(
            "SELECT origin_context FROM edd_cases WHERE client_name = ?",
            ("OriginNull",),
        ).fetchone()
        assert row[0] is None
