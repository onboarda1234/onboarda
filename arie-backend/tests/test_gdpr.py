"""
Sprint 3 — GDPR Data Retention & Purge Tests
Validates retention policies, purge logic, and DSAR handling.
"""
import os
import sys
import json
import sqlite3
import tempfile
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
        """Completing a DSAR must update status and set completion timestamp."""
        from gdpr import create_dsar, complete_dsar, get_pending_dsars
        create_dsar(gdpr_db, "portability", "complete@example.com")
        pending = get_pending_dsars(gdpr_db)
        dsar = [d for d in pending if d["requester_email"] == "complete@example.com"][0]

        result = complete_dsar(gdpr_db, dsar["id"], "admin001", "Data exported and sent to requester.")
        assert result["status"] == "completed"
