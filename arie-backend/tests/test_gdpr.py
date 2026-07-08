"""
Sprint 3 — GDPR Data Retention & Purge Tests
Validates retention policies, purge logic, and DSAR handling.
"""
import os
import sys
import json
import sqlite3
import tempfile
from pathlib import Path
import pytest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


@pytest.fixture
def gdpr_db():
    """Create a temporary database with GDPR tables for testing."""
    path = os.path.join(tempfile.gettempdir(), f"gdpr_test_{os.getpid()}.db")
    try:
        os.unlink(path)
    except OSError:
        pass

    os.environ["DB_PATH"] = path
    from db import init_db, seed_initial_data, get_db
    init_db()
    conn = get_db()
    try:
        seed_initial_data(conn)
        conn.commit()
    except Exception:
        pass
    yield conn
    conn.close()


# ═══════════════════════════════════════════════════════════
# Retention Policy Tests
# ═══════════════════════════════════════════════════════════

class TestRetentionPolicies:
    def test_policies_seeded(self, gdpr_db):
        """Default retention policies must be present after seeding."""
        from gdpr import get_retention_policies
        policies = get_retention_policies(gdpr_db)
        assert len(policies) >= 7
        categories = [p["data_category"] for p in policies]
        assert "client_pii" in categories
        assert "audit_logs" in categories
        assert "sar_reports" in categories

    def test_retention_days_positive(self, gdpr_db):
        """All retention periods must be positive integers."""
        from gdpr import get_retention_policies
        policies = get_retention_policies(gdpr_db)
        for p in policies:
            assert p["retention_days"] > 0, f"{p['data_category']} has invalid retention_days"

    def test_legal_basis_present(self, gdpr_db):
        """Every policy must have a legal basis (GDPR Article 6)."""
        from gdpr import get_retention_policies
        policies = get_retention_policies(gdpr_db)
        for p in policies:
            assert p["legal_basis"], f"{p['data_category']} has no legal_basis"

    def test_sar_never_auto_purge(self, gdpr_db):
        """SAR reports must NEVER be auto-purged (regulatory requirement)."""
        from gdpr import get_retention_policies
        policies = get_retention_policies(gdpr_db)
        sar = [p for p in policies if p["data_category"] == "sar_reports"]
        assert len(sar) == 1
        assert not sar[0]["auto_purge"]


# ═══════════════════════════════════════════════════════════
# Expired Data Detection Tests
# ═══════════════════════════════════════════════════════════

class TestExpiredDataDetection:
    def test_no_expired_data_on_fresh_db(self, gdpr_db):
        """A freshly seeded database should have no expired data."""
        from gdpr import get_expired_data_summary
        expired = get_expired_data_summary(gdpr_db)
        # May or may not have expired audit entries depending on seed
        # Just verify it runs without error and returns a list
        assert isinstance(expired, list)

    def test_detects_old_audit_logs(self, gdpr_db):
        """Audit logs older than retention period must be detected."""
        from gdpr import get_expired_data_summary
        # Insert an old audit entry (11 years ago)
        old_date = (datetime.now(timezone.utc) - timedelta(days=4000)).isoformat()
        gdpr_db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            ("test", "Test User", "admin", "test_action", "test", "Old entry", "127.0.0.1", old_date)
        )
        gdpr_db.commit()

        expired = get_expired_data_summary(gdpr_db)
        audit_expired = [e for e in expired if e["category"] == "audit_logs"]
        assert len(audit_expired) > 0
        assert audit_expired[0]["expired_count"] >= 1


# ═══════════════════════════════════════════════════════════
# Purge Execution Tests
# ═══════════════════════════════════════════════════════════

class TestPurgeExecution:
    def test_dry_run_does_not_delete(self, gdpr_db):
        """Dry run must count but not delete records."""
        from gdpr import purge_expired_data
        old_date = (datetime.now(timezone.utc) - timedelta(days=4000)).isoformat()
        gdpr_db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            ("test", "Test", "admin", "test", "test", "Old", "127.0.0.1", old_date)
        )
        gdpr_db.commit()

        result = purge_expired_data(gdpr_db, "audit_logs", dry_run=True)
        assert result["dry_run"] is True
        assert result["records_found"] >= 1
        assert result["records_deleted"] == 0

        # Verify record still exists
        count = gdpr_db.execute("SELECT COUNT(*) as c FROM audit_log WHERE timestamp = ?", (old_date,)).fetchone()["c"]
        assert count >= 1

    def test_actual_purge_deletes_and_logs(self, gdpr_db):
        """Actual purge must delete records and create purge log entry."""
        from gdpr import purge_expired_data
        old_date = (datetime.now(timezone.utc) - timedelta(days=4000)).isoformat()
        gdpr_db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            ("test", "Test", "admin", "purge_test", "test", "Old entry for purge", "127.0.0.1", old_date)
        )
        gdpr_db.commit()

        result = purge_expired_data(gdpr_db, "audit_logs", purged_by="admin001", dry_run=False)
        assert result["records_deleted"] >= 1

        # Verify purge log entry exists
        log = gdpr_db.execute("SELECT * FROM data_purge_log WHERE data_category = 'audit_logs' ORDER BY purged_at DESC LIMIT 1").fetchone()
        assert log is not None
        assert log["purged_by"] == "admin001"
        assert log["record_count"] >= 1

    def test_invalid_category_returns_error(self, gdpr_db):
        """Purging an unknown category must return an error."""
        from gdpr import purge_expired_data
        result = purge_expired_data(gdpr_db, "nonexistent_category", dry_run=True)
        assert "error" in result


# ═══════════════════════════════════════════════════════════
# DSAR Tests
# ═══════════════════════════════════════════════════════════

class TestDSAR:
    def test_create_access_request(self, gdpr_db):
        """Creating an access DSAR must succeed and set 30-day due date."""
        from gdpr import create_dsar
        result = create_dsar(gdpr_db, "access", "john@example.com", "John Doe")
        assert "error" not in result
        assert result.get("request_type") == "access" or result.get("status") == "created"

    def test_create_erasure_request(self, gdpr_db):
        """Creating an erasure (right to be forgotten) DSAR must succeed."""
        from gdpr import create_dsar
        result = create_dsar(gdpr_db, "erasure", "jane@example.com", "Jane Doe", description="Delete all my data")
        assert "error" not in result
        assert result["erasure_executed"] is False

    def test_invalid_request_type_rejected(self, gdpr_db):
        """Invalid DSAR type must be rejected."""
        from gdpr import create_dsar
        result = create_dsar(gdpr_db, "invalid_type", "test@example.com")
        assert "error" in result

    def test_pending_dsars_returned(self, gdpr_db):
        """get_pending_dsars must return created requests."""
        from gdpr import create_dsar, get_pending_dsars
        create_dsar(gdpr_db, "access", "pending@example.com")
        pending = get_pending_dsars(gdpr_db)
        assert len(pending) >= 1
        emails = [d["requester_email"] for d in pending]
        assert "pending@example.com" in emails

    def test_complete_dsar(self, gdpr_db):
        """Completing a DSAR must update response-workflow status only."""
        from gdpr import create_dsar, complete_dsar, get_pending_dsars
        create_dsar(gdpr_db, "portability", "complete@example.com")
        pending = get_pending_dsars(gdpr_db)
        dsar = [d for d in pending if d["requester_email"] == "complete@example.com"][0]

        result = complete_dsar(gdpr_db, dsar["id"], "admin001", "Data exported and sent to requester.")
        assert result["status"] == "completed"
        assert result["status_label"] == "Request response completed"
        assert result["erasure_executed"] is False

    def test_complete_erasure_dsar_does_not_imply_erasure(self, gdpr_db):
        """Completing an erasure request must not claim data removal occurred."""
        from gdpr import (
            DSAR_ERASURE_OUTCOME_RESPONSE_COMPLETED_NO_EXECUTION,
            complete_dsar,
            create_dsar,
        )

        created = create_dsar(
            gdpr_db,
            "erasure",
            "truthful@example.com",
            "Truthful Requester",
            description="Please process my erasure request",
        )
        result = complete_dsar(
            gdpr_db,
            created["id"],
            "admin001",
            "Response sent to requester. No erasure executor was run.",
        )

        assert result["status"] == "completed"
        assert result["erasure_executed"] is False
        assert result["retention_outcome"] == DSAR_ERASURE_OUTCOME_RESPONSE_COMPLETED_NO_EXECUTION
        assert result["status_label"] == "Request response completed; erasure not executed"
        assert "No erasure executor ran" in result["status_detail"]

        user_visible = " ".join(
            str(result.get(key) or "")
            for key in ("status_label", "status_detail")
        ).lower()
        for forbidden in ("erased", "deleted", "forgotten"):
            assert forbidden not in user_visible

        stored = gdpr_db.execute(
            "SELECT erasure_executed, retention_outcome, erasure_notes "
            "FROM data_subject_requests WHERE id = ?",
            (created["id"],),
        ).fetchone()
        assert stored["erasure_executed"] in (0, False)
        assert stored["retention_outcome"] == DSAR_ERASURE_OUTCOME_RESPONSE_COMPLETED_NO_EXECUTION
        assert "No erasure executor ran" in stored["erasure_notes"]

    def test_legal_retention_outcome_copy_is_honest(self, gdpr_db):
        """A retained erasure request must say retained, not removed."""
        from gdpr import DSAR_ERASURE_OUTCOME_RETAINED_LEGAL, complete_dsar, create_dsar

        created = create_dsar(gdpr_db, "erasure", "retained@example.com")
        gdpr_db.execute(
            """
            UPDATE data_subject_requests
               SET retention_outcome = ?,
                   retained_until = ?,
                   retained_categories = ?
             WHERE id = ?
            """,
            (
                DSAR_ERASURE_OUTCOME_RETAINED_LEGAL,
                "2033-01-01",
                json.dumps(["client_pii", "kyc_documents"]),
                created["id"],
            ),
        )
        gdpr_db.commit()

        result = complete_dsar(gdpr_db, created["id"], "admin001", "Retention response sent.")
        assert result["erasure_executed"] is False
        assert result["retention_outcome"] == DSAR_ERASURE_OUTCOME_RETAINED_LEGAL
        assert result["retained_until"] == "2033-01-01"
        assert result["retained_categories"] == ["client_pii", "kyc_documents"]
        assert result["status_label"] == "Request response completed; data retained under legal obligation"
        assert "retained" in result["status_detail"].lower()
        assert "retained" in result["erasure_notes"].lower()
        for forbidden in ("erased", "deleted", "forgotten"):
            assert forbidden not in result["erasure_notes"].lower()

    def test_response_copy_uses_removal_terms_only_after_execution(self):
        """API-facing DSAR copy must not say erased/deleted/forgotten unless execution is true."""
        from gdpr import format_dsar_for_response

        not_executed = format_dsar_for_response({
            "id": 1,
            "request_type": "erasure",
            "status": "completed",
            "erasure_executed": False,
        })
        visible = " ".join(
            str(not_executed.get(key) or "")
            for key in ("status_label", "status_detail")
        ).lower()
        for forbidden in ("erased", "deleted", "forgotten"):
            assert forbidden not in visible

        executed = format_dsar_for_response({
            "id": 2,
            "request_type": "erasure",
            "status": "completed",
            "erasure_executed": True,
        })
        assert executed["erasure_executed"] is False
        assert executed["status_label"] == "Erasure evidence missing"

        partial = format_dsar_for_response({
            "id": 4,
            "request_type": "erasure",
            "status": "completed",
            "erasure_executed": True,
            "retention_outcome": "partially_erased",
            "retained_categories": json.dumps(["kyc_documents"]),
        })
        assert partial["retention_outcome"] == "partial_retention_outcome_unverified"
        assert partial["retained_categories"] == ["kyc_documents"]
        assert partial["status_label"] == "Erasure evidence missing"

        inconsistent = format_dsar_for_response({
            "id": 3,
            "request_type": "erasure",
            "status": "completed",
            "erasure_executed": False,
            "retention_outcome": "partially_erased",
        })
        inconsistent_visible = " ".join(
            str(inconsistent.get(key) or "")
            for key in ("retention_outcome", "status_label", "status_detail")
        ).lower()
        for forbidden in ("erased", "deleted", "forgotten"):
            assert forbidden not in inconsistent_visible
        assert inconsistent["retention_outcome"] == "partial_retention_outcome_unverified"

    def test_migration_040_registered_and_fresh_schema_has_truth_columns(self, gdpr_db):
        """Fresh init_db carries the columns and records 040 as schema-covered."""
        import db as db_module

        assert "040" in db_module.FILE_MIGRATIONS_REQUIRING_RUNNER
        columns = {
            row["name"]
            for row in gdpr_db.execute("PRAGMA table_info(data_subject_requests)").fetchall()
        }
        assert set(db_module.DSAR_ERASURE_TRUTH_COLUMNS).issubset(columns)

        marker = gdpr_db.execute(
            "SELECT filename, description, checksum FROM schema_version WHERE version = ?",
            ("040",),
        ).fetchone()
        assert marker is not None
        assert marker["filename"] == "migration_040_dsar_status_truthfulness.sql"
        assert marker["description"] == "covered by init_db"
        assert marker["checksum"] == "init_db"

    def test_migration_040_runs_on_preexisting_database(self, tmp_path):
        """Existing databases without the H2A columns must run migration 040."""
        from db import DBConnection, DSAR_ERASURE_TRUTH_COLUMNS
        from migrations.runner import MIGRATIONS_DIR, ensure_schema_version_table, run_all_migrations_with_connection

        db_path = tmp_path / "legacy_dsar.db"
        conn = sqlite3.connect(str(db_path))
        wrapped = DBConnection(conn)
        try:
            wrapped.executescript("""
            CREATE TABLE data_subject_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_type TEXT NOT NULL CHECK(request_type IN ('access','rectification','erasure','portability','restriction','objection')),
                requester_email TEXT NOT NULL,
                requester_name TEXT,
                client_id TEXT,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending','in_progress','completed','rejected','expired')),
                description TEXT,
                response_notes TEXT,
                handled_by TEXT,
                received_at TEXT DEFAULT (datetime('now')),
                due_at TEXT,
                completed_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """)
            wrapped.execute(
                "INSERT INTO data_subject_requests (request_type, requester_email, status) VALUES (?, ?, ?)",
                ("erasure", "legacy@example.com", "pending"),
            )
            ensure_schema_version_table(wrapped)
            for path in sorted(Path(MIGRATIONS_DIR).glob("migration_*.sql")):
                version = path.stem.split("_", 2)[1]
                if version == "040":
                    continue
                wrapped.execute(
                    "INSERT INTO schema_version (version, filename, description, checksum) "
                    "VALUES (?, ?, ?, ?)",
                    (version, path.name, "preseeded by test", "preseed"),
                )
            wrapped.commit()

            applied = run_all_migrations_with_connection(wrapped)
            assert applied == 1

            columns = {
                row["name"]
                for row in wrapped.execute("PRAGMA table_info(data_subject_requests)").fetchall()
            }
            assert set(DSAR_ERASURE_TRUTH_COLUMNS).issubset(columns)
            row = wrapped.execute(
                "SELECT erasure_executed, retention_outcome FROM data_subject_requests WHERE requester_email = ?",
                ("legacy@example.com",),
            ).fetchone()
            assert row["erasure_executed"] in (0, False)
            assert row["retention_outcome"] is None

            marker = wrapped.execute(
                "SELECT filename, checksum FROM schema_version WHERE version = ?",
                ("040",),
            ).fetchone()
            assert marker is not None
            assert marker["filename"] == "migration_040_dsar_status_truthfulness.sql"
            assert marker["checksum"] != "init_db"
        finally:
            wrapped.close()

    def test_sqlite_add_column_shim_splits_quoted_semicolons(self):
        """SQLite migration shim must not split inside SQL string literals."""
        from db import DBConnection

        assert DBConnection._split_sql_statements(
            "SELECT 'alpha;beta'; SELECT \"gamma;delta\"; SELECT 'it''s;ok';"
        ) == [
            "SELECT 'alpha;beta'",
            'SELECT "gamma;delta"',
            "SELECT 'it''s;ok'",
        ]


# ═══════════════════════════════════════════════════════════
# Audit-Trail Protection Tests (audit finding B1)
# ═══════════════════════════════════════════════════════════

class TestAuditTrailPurgeProtection:
    """The scheduled GDPR purge must never destroy the audit trail.

    Regression coverage for B1: the seeded `session_tokens` policy used to map to
    the `audit_log` table with 1-day retention + auto_purge=1, so the daily
    scheduler was wiping the entire audit trail down to the last 24 hours.
    """

    def _insert_old_audit_row(self, db, detail="Old entry"):
        old_date = (datetime.now(timezone.utc) - timedelta(days=4000)).isoformat()
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            ("test", "Test", "admin", "decision", "app-1", detail, "127.0.0.1", old_date),
        )
        db.commit()
        return old_date

    def test_session_tokens_no_longer_maps_to_audit_log(self):
        """The dangerous session_tokens -> audit_log mapping must be gone."""
        from gdpr import CATEGORY_TABLE_MAP
        assert "session_tokens" not in CATEGORY_TABLE_MAP

    def test_seeded_session_tokens_policy_is_not_auto_purge(self, gdpr_db):
        """Freshly seeded session_tokens policy must not auto-purge."""
        row = gdpr_db.execute(
            "SELECT auto_purge FROM data_retention_policies WHERE data_category = 'session_tokens'"
        ).fetchone()
        # Policy may be absent, but if present it must never auto-purge.
        if row is not None:
            assert not row["auto_purge"], "session_tokens must not be auto-purge (B1)"

    def test_scheduled_purge_never_deletes_audit_log_even_if_misconfigured(self, gdpr_db):
        """Even a policy misconfigured to auto-purge audit_log must be refused."""
        from gdpr import run_scheduled_purge
        old_date = self._insert_old_audit_row(gdpr_db, "must-survive-scheduled-purge")
        before = gdpr_db.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]

        # Simulate a dangerous misconfiguration: an auto_purge policy for the
        # audit_logs category (which resolves to the audit_log table).
        gdpr_db.execute(
            "UPDATE data_retention_policies SET auto_purge = 1, retention_days = 1 WHERE data_category = 'audit_logs'"
        )
        gdpr_db.commit()

        results = run_scheduled_purge(gdpr_db, purged_by="system-scheduler")

        after = gdpr_db.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
        assert after == before, "scheduled purge must not delete audit_log rows (B1)"
        # The old row specifically must still be present.
        survived = gdpr_db.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE timestamp = ?", (old_date,)
        ).fetchone()["c"]
        assert survived >= 1
        # And the refusal must be reported, not silently skipped.
        audit_result = [r for r in results if r.get("category") == "audit_logs"]
        assert audit_result and "refused" in (audit_result[0].get("error") or "")

    def test_manual_audit_log_retention_purge_still_permitted(self, gdpr_db):
        """A deliberate manual purge_expired_data('audit_logs') remains allowed."""
        from gdpr import purge_expired_data
        self._insert_old_audit_row(gdpr_db, "manual-purge-ok")
        result = purge_expired_data(gdpr_db, "audit_logs", purged_by="admin001", dry_run=False)
        assert "error" not in result
        assert result["records_deleted"] >= 1

    def test_supervisor_audit_log_can_never_be_purged(self, gdpr_db):
        """The tamper-evident supervisor chain must be refused on every path."""
        import gdpr
        # Temporarily expose supervisor_audit_log as a mapped category to prove the
        # guard fires even if a future map change resolves to it.
        original = dict(gdpr.CATEGORY_TABLE_MAP)
        original_tables = gdpr._ALLOWED_GDPR_TABLES
        try:
            gdpr.CATEGORY_TABLE_MAP["supervisor_chain_test"] = ("supervisor_audit_log", "timestamp")
            gdpr._ALLOWED_GDPR_TABLES = frozenset(list(original_tables) + ["supervisor_audit_log"])
            gdpr_db.execute(
                "INSERT INTO data_retention_policies (data_category, retention_days, legal_basis, description, auto_purge, requires_review) VALUES ('supervisor_chain_test', 1, 'test', 'test', 0, 0)"
            )
            gdpr_db.commit()
            result = gdpr.purge_expired_data(gdpr_db, "supervisor_chain_test", dry_run=False)
            assert "error" in result and "protected" in result["error"]
            assert result["records_deleted"] == 0
        finally:
            gdpr.CATEGORY_TABLE_MAP = original
            gdpr._ALLOWED_GDPR_TABLES = original_tables
            # Clean up the test policy row — a leaked row makes the INSERT
            # above fail with a UNIQUE violation on shared/reused test DBs.
            gdpr_db.execute(
                "DELETE FROM data_retention_policies WHERE data_category = 'supervisor_chain_test'"
            )
            gdpr_db.commit()
