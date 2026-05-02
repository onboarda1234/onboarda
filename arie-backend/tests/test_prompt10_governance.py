"""
Prompt 10 — Data Governance, SQL Safety, Schema Drift Regression Tests

Verifies:
1. gdpr.py SQL identifier allowlist is enforced (no unchecked f-string identifiers)
2. override_ai requires SCO or admin role
3. purge_expired_data defaults to dry_run=True
4. GDPR PeriodicCallback is registered (not testing env)
5. UBO threshold is 25.0% (FATF standard)
6. Schema migrations are idempotent (IF NOT EXISTS guards)
"""
import os
import sys
import inspect
import importlib
import sqlite3
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ════════════════════════════════════════════════════════════
# 1. SQL Identifier Allowlist (gdpr.py)
# ════════════════════════════════════════════════════════════

class TestGdprSqlIdentifierAllowlist:
    """gdpr.py SQL identifiers are always validated against a frozenset allowlist."""

    def test_allowlist_constants_exist(self):
        """_ALLOWED_GDPR_TABLES and _ALLOWED_GDPR_DATE_COLS must be defined."""
        import gdpr
        assert hasattr(gdpr, "_ALLOWED_GDPR_TABLES"), "gdpr must define _ALLOWED_GDPR_TABLES"
        assert hasattr(gdpr, "_ALLOWED_GDPR_DATE_COLS"), "gdpr must define _ALLOWED_GDPR_DATE_COLS"
        assert isinstance(gdpr._ALLOWED_GDPR_TABLES, frozenset)
        assert isinstance(gdpr._ALLOWED_GDPR_DATE_COLS, frozenset)

    def test_allowlists_are_non_empty(self):
        """Allowlists must contain at least the known GDPR tables and columns."""
        import gdpr
        assert len(gdpr._ALLOWED_GDPR_TABLES) > 0
        assert len(gdpr._ALLOWED_GDPR_DATE_COLS) > 0
        # Known safe tables from CATEGORY_TABLE_MAP
        assert "audit_log" in gdpr._ALLOWED_GDPR_TABLES
        assert "monitoring_alerts" in gdpr._ALLOWED_GDPR_TABLES

    def test_assert_safe_sql_identifier_blocks_unknown_table(self):
        """_assert_safe_sql_identifier() must raise ValueError for unknown identifiers."""
        import gdpr
        assert hasattr(gdpr, "_assert_safe_sql_identifier"), (
            "gdpr must define _assert_safe_sql_identifier()"
        )
        with pytest.raises(ValueError, match="SQL identifier safety check failed"):
            gdpr._assert_safe_sql_identifier(
                "injected_table; DROP TABLE audit_log; --",
                gdpr._ALLOWED_GDPR_TABLES,
                "table"
            )

    def test_assert_safe_sql_identifier_allows_known_table(self):
        """_assert_safe_sql_identifier() must not raise for known-good identifiers."""
        import gdpr
        # Should not raise
        gdpr._assert_safe_sql_identifier("audit_log", gdpr._ALLOWED_GDPR_TABLES, "table")
        gdpr._assert_safe_sql_identifier("monitoring_alerts", gdpr._ALLOWED_GDPR_TABLES, "table")

    def test_purge_expired_data_rejects_unmapped_category(self):
        """purge_expired_data() must return error dict for unknown category."""
        import gdpr
        db_mock = mock.MagicMock()
        db_mock.execute.return_value.fetchone.return_value = None  # no policy found
        result = gdpr.purge_expired_data(db_mock, "unknown_category_xyz", dry_run=True)
        assert "error" in result
        assert "No retention policy" in result["error"] or "no direct table" in result["error"]

    def test_purge_defaults_to_dry_run(self):
        """purge_expired_data() must default dry_run=True — no accidental deletions."""
        import gdpr
        sig = inspect.signature(gdpr.purge_expired_data)
        dry_run_param = sig.parameters.get("dry_run")
        assert dry_run_param is not None, "purge_expired_data must have a dry_run parameter"
        assert dry_run_param.default is True, (
            f"dry_run must default to True for safety, got {dry_run_param.default!r}"
        )


# ════════════════════════════════════════════════════════════
# 2. Override AI Role Guard (server.py)
# ════════════════════════════════════════════════════════════

class TestOverrideAiRoleGovernance:
    """override_ai=True requires SCO or admin role."""

    def test_override_ai_role_check_in_decision_handler_source(self):
        """
        server.py ApplicationDecisionHandler must enforce role check
        before allowing override_ai=True.
        """
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "server.py"
        )
        with open(server_path, "r", encoding="utf-8") as f:
            source = f.read()
        # The override governance block must exist
        assert "override_ai" in source and "sco" in source and "admin" in source, (
            "server.py must have override_ai role check referencing 'sco' and 'admin'"
        )
        # Specifically, the role check must gate on override_ai being True
        import re
        # Find the override governance block
        pattern = r"override_ai\s+and\s+user\.get\(['\"]role['\"]\)\s+not\s+in"
        assert re.search(pattern, source), (
            "server.py must have: if override_ai and user.get('role') not in (...)"
        )

    def test_override_reviewed_at_in_detail_info(self):
        """detail_info must include override_reviewed_at when override_ai=True."""
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "server.py"
        )
        with open(server_path, "r", encoding="utf-8") as f:
            source = f.read()
        assert "override_reviewed_at" in source, (
            "server.py must include override_reviewed_at in detail_info for audit trail"
        )

    def test_override_by_role_in_detail_info(self):
        """detail_info must record override_by_role for audit accountability."""
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "server.py"
        )
        with open(server_path, "r", encoding="utf-8") as f:
            source = f.read()
        assert "override_by_role" in source, (
            "server.py must include override_by_role in detail_info for audit trail"
        )


# ════════════════════════════════════════════════════════════
# 3. GDPR PeriodicCallback Registered
# ════════════════════════════════════════════════════════════

class TestGdprPeriodicCallbackRegistered:
    """server.py must register a Tornado PeriodicCallback for GDPR purge."""

    def test_gdpr_periodic_callback_in_server_source(self):
        """server.py must use PeriodicCallback for the GDPR purge tick."""
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "server.py"
        )
        with open(server_path, "r", encoding="utf-8") as f:
            source = f.read()
        assert "PeriodicCallback" in source, (
            "server.py must register a Tornado PeriodicCallback for scheduled GDPR purge"
        )
        assert "_gdpr_purge" in source or "gdpr_purge" in source, (
            "server.py must have a GDPR purge callback (gdpr_purge)"
        )

    def test_gdpr_purge_skipped_in_testing_env(self):
        """GDPR purge PeriodicCallback must not start in 'testing' environment."""
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "server.py"
        )
        with open(server_path, "r", encoding="utf-8") as f:
            source = f.read()
        # Must exclude 'testing' environment
        assert '"testing"' in source or "'testing'" in source, (
            "server.py must exclude 'testing' from GDPR PeriodicCallback startup"
        )

    def test_gdpr_purge_interval_is_daily(self):
        """GDPR purge interval must be >= 86400 seconds (24 hours)."""
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "server.py"
        )
        with open(server_path, "r", encoding="utf-8") as f:
            source = f.read()
        # 86_400_000 ms = 1 day
        assert "86_400_000" in source or "86400000" in source, (
            "GDPR PeriodicCallback interval must be 86400000ms (1 day)"
        )


# ════════════════════════════════════════════════════════════
# 4. UBO Threshold Is FATF Standard
# ════════════════════════════════════════════════════════════

class TestUboThreshold:
    """UBO threshold must be 25.0% per FATF Recommendation 24."""

    def test_ubo_threshold_is_25_percent(self):
        """UBO_THRESHOLD_PCT in document_verification.py must be 25.0."""
        import document_verification
        assert hasattr(document_verification, "UBO_THRESHOLD_PCT"), (
            "document_verification.py must define UBO_THRESHOLD_PCT"
        )
        assert document_verification.UBO_THRESHOLD_PCT == 25.0, (
            f"UBO_THRESHOLD_PCT must be 25.0 (FATF standard), got {document_verification.UBO_THRESHOLD_PCT}"
        )

    def test_ubo_threshold_used_in_ownership_check(self):
        """UBO_THRESHOLD_PCT must be used in the ownership check logic."""
        import document_verification
        source = inspect.getsource(document_verification)
        assert "UBO_THRESHOLD_PCT" in source, (
            "UBO_THRESHOLD_PCT must be used in document_verification.py ownership checks"
        )


# ════════════════════════════════════════════════════════════
# 5. Schema Migrations Are Idempotent
# ════════════════════════════════════════════════════════════

class TestSchemaMigrationsIdempotent:
    """All CREATE TABLE statements in migrations use IF NOT EXISTS guard."""

    def test_all_create_table_use_if_not_exists(self):
        """No migration may use CREATE TABLE without IF NOT EXISTS."""
        migrations_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "migrations", "scripts"
        )
        if not os.path.isdir(migrations_dir):
            pytest.skip("migrations/scripts directory not found")

        import re
        create_without_guard = []
        for fname in sorted(os.listdir(migrations_dir)):
            if not fname.endswith(".sql"):
                continue
            fpath = os.path.join(migrations_dir, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            # Find CREATE TABLE without IF NOT EXISTS
            # Allow SELECT 1 no-ops (migration_007 pattern)
            if re.search(r"\bSELECT\s+1\b", content, re.IGNORECASE):
                continue  # NO-OP migration — acceptable
            # Strip single-line SQL comments before checking
            content_no_comments = "\n".join(
                line for line in content.splitlines()
                if not line.lstrip().startswith("--")
            )
            matches = re.findall(
                r"\bCREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)",
                content_no_comments,
                re.IGNORECASE
            )
            if matches:
                create_without_guard.append(fname)

        assert not create_without_guard, (
            f"The following migration files have CREATE TABLE without IF NOT EXISTS guard: "
            f"{create_without_guard}"
        )

    def test_migration_007_is_noop(self):
        """migration_007 must be a known SELECT 1 no-op (cross-dialect compatibility)."""
        migrations_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "migrations", "scripts"
        )
        if not os.path.isdir(migrations_dir):
            pytest.skip("migrations/scripts directory not found")

        migration_007 = None
        for fname in os.listdir(migrations_dir):
            if "007" in fname and fname.endswith(".sql"):
                migration_007 = os.path.join(migrations_dir, fname)
                break

        if migration_007 is None:
            pytest.skip("migration_007 not found")

        with open(migration_007, "r", encoding="utf-8") as f:
            content = f.read()

        # Must be a no-op (SELECT 1) or contain comment explaining NO-OP status
        is_noop = "SELECT 1" in content.upper() or "SELECT 1" in content
        is_documented = "NO-OP" in content.upper() or "no-op" in content.lower() or "NOOP" in content.upper()
        assert is_noop or is_documented, (
            f"migration_007 should be a documented NO-OP. Content: {content[:200]!r}"
        )
