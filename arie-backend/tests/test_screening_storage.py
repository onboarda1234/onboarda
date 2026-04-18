"""
Tests for SCR-005 — Normalized screening storage.
"""

import json
import sqlite3
from unittest.mock import MagicMock
import pytest

from screening_storage import (
    ensure_normalized_table,
    compute_report_hash,
    persist_normalized_report,
    persist_normalization_failure,
    get_normalized_report,
    delete_normalized_reports_for_application,
)


@pytest.fixture
def norm_db(tmp_path):
    """Create a fresh SQLite DB with the normalized table."""
    db_path = str(tmp_path / "test_norm.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Create the table
    ensure_normalized_table(conn)
    yield conn
    conn.close()


class TestEnsureTable:
    def test_table_created(self, norm_db):
        row = norm_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='screening_reports_normalized'"
        ).fetchone()
        assert row is not None

    def test_idempotent(self, norm_db):
        # Calling again should not raise
        ensure_normalized_table(norm_db)

    def test_indexes_created(self, norm_db):
        rows = norm_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_screening_normalized%'"
        ).fetchall()
        names = [r["name"] for r in rows]
        assert "idx_screening_normalized_client_app" in names
        assert "idx_screening_normalized_app_id" in names


class TestComputeReportHash:
    def test_deterministic(self):
        report = {"key": "value", "num": 42}
        h1 = compute_report_hash(report)
        h2 = compute_report_hash(report)
        assert h1 == h2

    def test_different_reports_different_hash(self):
        r1 = {"key": "value1"}
        r2 = {"key": "value2"}
        assert compute_report_hash(r1) != compute_report_hash(r2)

    def test_key_order_irrelevant(self):
        r1 = {"a": 1, "b": 2}
        r2 = {"b": 2, "a": 1}
        assert compute_report_hash(r1) == compute_report_hash(r2)

    def test_returns_32_char_hex(self):
        h = compute_report_hash({"test": True})
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)


class TestPersistNormalizedReport:
    def test_insert_and_retrieve(self, norm_db):
        report = {"provider": "sumsub", "total_hits": 0}
        row_id = persist_normalized_report(
            norm_db, "client_1", "app_1", report, "hash123"
        )
        norm_db.commit()
        assert row_id > 0

        row = norm_db.execute(
            "SELECT * FROM screening_reports_normalized WHERE id=?", (row_id,)
        ).fetchone()
        assert row["client_id"] == "client_1"
        assert row["application_id"] == "app_1"
        assert row["normalization_status"] == "success"
        assert row["is_authoritative"] == 0
        assert row["source"] == "migration_scaffolding"

    def test_report_json_stored(self, norm_db):
        report = {"total_hits": 5, "director_screenings": []}
        row_id = persist_normalized_report(
            norm_db, "c1", "a1", report, "hash1"
        )
        norm_db.commit()
        row = norm_db.execute(
            "SELECT normalized_report_json FROM screening_reports_normalized WHERE id=?", (row_id,)
        ).fetchone()
        parsed = json.loads(row["normalized_report_json"])
        assert parsed["total_hits"] == 5

    def test_is_authoritative_enforced_false(self, norm_db):
        """is_authoritative CHECK constraint prevents setting to 1."""
        with pytest.raises(Exception):
            norm_db.execute(
                """INSERT INTO screening_reports_normalized
                   (client_id, application_id, is_authoritative)
                   VALUES ('c1', 'a1', 1)"""
            )

    def test_tenant_scoped(self, norm_db):
        persist_normalized_report(norm_db, "client_A", "app_1", {}, "h1")
        persist_normalized_report(norm_db, "client_B", "app_2", {}, "h2")
        norm_db.commit()

        rows = norm_db.execute(
            "SELECT * FROM screening_reports_normalized WHERE client_id='client_A'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["application_id"] == "app_1"


class TestPersistFailure:
    def test_failure_record(self, norm_db):
        row_id = persist_normalization_failure(
            norm_db, "c1", "a1", "hash_fail", "KeyError: 'missing_field'"
        )
        norm_db.commit()
        row = norm_db.execute(
            "SELECT * FROM screening_reports_normalized WHERE id=?", (row_id,)
        ).fetchone()
        assert row["normalization_status"] == "failed"
        assert "KeyError" in row["normalization_error"]
        assert row["normalized_report_json"] is None
        assert row["is_authoritative"] == 0


class TestGetNormalizedReport:
    def test_returns_none_when_empty(self, norm_db):
        result = get_normalized_report(norm_db, "nonexistent")
        assert result is None

    def test_returns_latest(self, norm_db):
        persist_normalized_report(norm_db, "c1", "a1", {"v": 1}, "h1")
        persist_normalized_report(norm_db, "c1", "a1", {"v": 2}, "h2")
        norm_db.commit()
        result = get_normalized_report(norm_db, "a1", "c1")
        assert result["normalized_report"]["v"] == 2

    def test_tenant_scoped_query(self, norm_db):
        persist_normalized_report(norm_db, "c1", "a1", {"v": 1}, "h1")
        persist_normalized_report(norm_db, "c2", "a1", {"v": 2}, "h2")
        norm_db.commit()
        result = get_normalized_report(norm_db, "a1", "c1")
        assert result["normalized_report"]["v"] == 1

    def test_without_client_id(self, norm_db):
        persist_normalized_report(norm_db, "c1", "a1", {"v": 1}, "h1")
        norm_db.commit()
        result = get_normalized_report(norm_db, "a1")
        assert result is not None


class TestStorageTransactionSafety:
    """Persist helpers must NOT call commit — the caller owns the transaction."""

    def _make_mock_db(self):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 42
        mock_db.execute.return_value = mock_cursor
        return mock_db

    def test_persist_normalized_report_does_not_commit(self):
        mock_db = self._make_mock_db()
        row_id = persist_normalized_report(
            mock_db, "c1", "a1", {"key": "val"}, "hash1"
        )
        assert row_id == 42
        mock_db.commit.assert_not_called()

    def test_persist_normalization_failure_does_not_commit(self):
        mock_db = self._make_mock_db()
        row_id = persist_normalization_failure(
            mock_db, "c1", "a1", "hash_fail", "some error"
        )
        assert row_id == 42
        mock_db.commit.assert_not_called()


class TestStorageIsolation:
    """Normalized storage must be completely separate from prescreening_data."""

    def test_no_prescreening_data_column(self, norm_db):
        """Table must not have a prescreening_data column."""
        cols = norm_db.execute("PRAGMA table_info(screening_reports_normalized)").fetchall()
        col_names = [c["name"] for c in cols]
        assert "prescreening_data" not in col_names

    def test_has_required_columns(self, norm_db):
        cols = norm_db.execute("PRAGMA table_info(screening_reports_normalized)").fetchall()
        col_names = [c["name"] for c in cols]
        required = [
            "id", "client_id", "application_id", "provider",
            "normalized_version", "source_screening_report_hash",
            "normalized_report_json", "normalization_status",
            "normalization_error", "is_authoritative", "source",
            "created_at", "updated_at",
        ]
        for col in required:
            assert col in col_names, f"Missing column: {col}"


class TestDeleteNormalizedReports:
    """Sprint 3 Obj 2a — application-delete cascade coverage."""

    def test_deletes_all_records_for_application(self, norm_db):
        """All normalized records for an application are deleted."""
        persist_normalized_report(norm_db, "c1", "app_1", {"v": 1}, "h1")
        persist_normalized_report(norm_db, "c1", "app_1", {"v": 2}, "h2")
        persist_normalized_report(norm_db, "c2", "app_2", {"v": 3}, "h3")
        norm_db.commit()

        deleted = delete_normalized_reports_for_application(norm_db, "app_1")
        norm_db.commit()
        assert deleted == 2

        # app_1 gone
        assert get_normalized_report(norm_db, "app_1") is None
        # app_2 unaffected
        assert get_normalized_report(norm_db, "app_2") is not None

    def test_returns_zero_when_no_records(self, norm_db):
        """Returns 0 when no records exist for the application."""
        deleted = delete_normalized_reports_for_application(norm_db, "nonexistent")
        assert deleted == 0

    def test_does_not_commit(self):
        """Delete helper must not call commit — caller owns the transaction."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 3
        mock_db.execute.return_value = mock_cursor
        deleted = delete_normalized_reports_for_application(mock_db, "app_1")
        assert deleted == 3
        mock_db.commit.assert_not_called()

    def test_handles_missing_table(self):
        """Returns 0 if screening_reports_normalized table does not exist."""
        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("no such table")
        deleted = delete_normalized_reports_for_application(mock_db, "app_1")
        assert deleted == 0
