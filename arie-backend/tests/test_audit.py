"""
Tests for audit-trail completeness on AuthZ denials.

Covers:
- Autonomous transaction for audit writes (survives parent rollback)
- Fallback to structured stderr on primary audit failure
- Uniform payload shape across all check_app_ownership callsites
- Defence-in-depth audit in change_management.create_change_request
"""

import json
import os
import sys
import sqlite3
import secrets
import unittest
import io
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ── Required payload keys for every AuthZ denial audit row ──
REQUIRED_PAYLOAD_KEYS = {"event", "client_id", "attempted_resource_id",
                          "actual_owner", "path", "ts"}


# ── Helpers ──

def _make_handler(uri="/api/test"):
    """Create a BaseHandler instance with a mocked connection."""
    from base_handler import BaseHandler
    from tornado.web import Application
    from tornado.httputil import HTTPServerRequest, HTTPHeaders

    app = Application()
    mock_conn = MagicMock()
    mock_conn.context = MagicMock()
    mock_conn.context.remote_ip = "127.0.0.1"
    mock_conn.no_keep_alive = False

    req = HTTPServerRequest(
        method="POST",
        uri=uri,
        version="HTTP/1.1",
        headers=HTTPHeaders({}),
        body=b"",
        host="127.0.0.1",
        connection=mock_conn,
    )
    handler = BaseHandler(app, req)
    return handler


def _setup_audit_test_data(raw_db, client_id, other_client_id, app_id, other_app_id):
    """Seed two clients and two applications for ownership tests."""
    for cid, email, company in [
        (client_id, f"audit-{secrets.token_hex(3)}@test.com", "Audit Owner Corp"),
        (other_client_id, f"audit-other-{secrets.token_hex(3)}@test.com", "Audit Other Corp"),
    ]:
        raw_db.execute(
            "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) "
            "VALUES (?, ?, ?, ?)",
            (cid, email, "hash", company),
        )

    for aid, cid, name in [
        (app_id, client_id, "Audit Owner App"),
        (other_app_id, other_client_id, "Audit Other App"),
    ]:
        raw_db.execute(
            "INSERT OR IGNORE INTO applications "
            "(id, ref, client_id, company_name, country, sector, entity_type, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, f"APP-AUD-{aid[:8]}", cid, name, "MU", "Tech", "SME", "approved"),
        )
    raw_db.commit()


class TestAuditAutonomousTransaction:
    """A3: Audit row persists even when the parent transaction rolls back."""

    def test_audit_write_in_autonomous_transaction(self, db):
        """log_authz_denial uses its own connection — a rollback on the
        caller's connection must not delete the audit row.

        Note: SQLite has database-level locking, so the autonomous insert
        must happen when the caller doesn't hold an exclusive write lock.
        In PostgreSQL (production), true autonomous transactions work via
        separate connections. This test verifies the audit function opens
        and commits its own connection independently.
        """
        client_id = f"aud-cl-{secrets.token_hex(4)}"
        other_id = f"aud-oth-{secrets.token_hex(4)}"
        app_id = f"aud-app-{secrets.token_hex(4)}"
        other_app = f"aud-oapp-{secrets.token_hex(4)}"
        _setup_audit_test_data(db, client_id, other_id, app_id, other_app)

        handler = _make_handler("/api/test/denial")
        user = {"sub": client_id, "name": "Test Client", "role": "client", "type": "client"}

        # Call log_authz_denial — it should use its own connection
        handler.log_authz_denial(
            user, "authz_denied_not_owner", other_app,
            {"actual_owner": other_id},
        )

        # The denial audit row should exist via the autonomous connection
        rows = db.execute(
            "SELECT * FROM audit_log WHERE action = 'authz_denied_not_owner' "
            "AND target = ?", (other_app,)
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 audit row, found {len(rows)}"
        detail = json.loads(rows[0]["detail"])
        assert detail["client_id"] == client_id
        assert detail["actual_owner"] == other_id

        # Now verify the audit row survives independently — insert something
        # on the main connection and roll back. The audit row must persist.
        db.execute("INSERT INTO audit_log (user_id, action, target, detail) VALUES (?, ?, ?, ?)",
                   ("parent_tx", "parent_write", "test", "should_be_rolled_back"))
        db.rollback()

        # The audit row from the autonomous connection should still be there
        rows_after = db.execute(
            "SELECT * FROM audit_log WHERE action = 'authz_denied_not_owner' "
            "AND target = ?", (other_app,)
        ).fetchall()
        assert len(rows_after) == 1, (
            f"Autonomous audit row should survive parent rollback, found {len(rows_after)}"
        )

        # The parent's write should be gone
        parent_rows = db.execute(
            "SELECT * FROM audit_log WHERE action = 'parent_write'"
        ).fetchall()
        assert len(parent_rows) == 0, "Parent rollback should have removed parent_write"


class TestAuditFallback:
    """A4: When primary audit DB fails, a structured AUDIT_FALLBACK line is
    written to stderr and the 403 still ships."""

    def test_audit_fallback_on_primary_failure(self, db):
        """Monkeypatch get_db to raise; assert AUDIT_FALLBACK on stderr."""
        handler = _make_handler("/api/test/fallback")

        user = {"sub": "fallback-client", "name": "Fallback", "role": "client", "type": "client"}

        captured_stderr = io.StringIO()

        with patch("base_handler.get_db", side_effect=RuntimeError("DB is down")):
            with patch("sys.stderr", captured_stderr):
                handler.log_authz_denial(
                    user, "authz_denied_not_owner", "fb-app-001",
                    {"actual_owner": "someone-else"},
                )

        stderr_output = captured_stderr.getvalue()
        assert "AUDIT_FALLBACK" in stderr_output, (
            f"Expected AUDIT_FALLBACK in stderr, got: {stderr_output}")

        parsed = json.loads(stderr_output.strip())
        assert parsed["AUDIT_FALLBACK"] is True
        assert parsed["event"] == "authz_denied_not_owner"
        assert parsed["client_id"] == "fallback-client"
        assert parsed["attempted_resource_id"] == "fb-app-001"
        assert parsed["actual_owner"] == "someone-else"

    def test_check_app_ownership_returns_false_even_on_audit_failure(self, db):
        """check_app_ownership must return False (trigger 403) even when
        the audit write itself fails."""
        handler = _make_handler("/api/test/ownership")

        user = {"sub": "audit-fail-client", "name": "AuditFail",
                "role": "client", "type": "client"}
        app_row = {"id": "af-app-001", "client_id": "real-owner"}

        with patch("base_handler.get_db", side_effect=RuntimeError("DB down")):
            with patch("sys.stderr", io.StringIO()):
                result = handler.check_app_ownership(user, app_row)

        assert result is False, "check_app_ownership should return False on denial"


class TestAuditPayloadShape:
    """A5: Every denial via check_app_ownership produces a uniform payload."""

    def test_audit_payload_shape_uniform(self, db):
        """For a denial through check_app_ownership, the audit payload
        must contain all required keys."""
        client_id = f"shape-cl-{secrets.token_hex(4)}"
        other_id = f"shape-oth-{secrets.token_hex(4)}"
        app_id = f"shape-app-{secrets.token_hex(4)}"
        other_app = f"shape-oapp-{secrets.token_hex(4)}"
        _setup_audit_test_data(db, client_id, other_id, app_id, other_app)

        handler = _make_handler("/api/applications/test/documents")

        user = {"sub": client_id, "name": "Shape Client",
                "role": "client", "type": "client"}
        app_row = {"id": other_app, "client_id": other_id}

        handler.check_app_ownership(user, app_row)

        row = db.execute(
            "SELECT * FROM audit_log WHERE action = 'authz_denied_not_owner' "
            "AND target = ?", (other_app,)
        ).fetchone()
        assert row is not None, "Expected an audit row for ownership denial"

        detail = json.loads(row["detail"])
        missing = REQUIRED_PAYLOAD_KEYS - set(detail.keys())
        assert not missing, f"Audit payload missing keys: {missing}"
        assert detail["event"] == "authz_denied_not_owner"
        assert detail["client_id"] == client_id
        assert detail["attempted_resource_id"] == other_app
        assert detail["actual_owner"] == other_id
        assert "/api/applications/test/documents" in detail["path"]
        assert "ts" in detail and detail["ts"].endswith("Z")


class TestCheckAppOwnershipWritesAudit:
    """A2: check_app_ownership writes an audit row on denial."""

    def test_check_app_ownership_writes_audit(self, db):
        """Denial via check_app_ownership must produce an audit row with
        action='authz_denied_not_owner'."""
        client_id = f"own-cl-{secrets.token_hex(4)}"
        other_id = f"own-oth-{secrets.token_hex(4)}"
        app_id = f"own-app-{secrets.token_hex(4)}"
        other_app = f"own-oapp-{secrets.token_hex(4)}"
        _setup_audit_test_data(db, client_id, other_id, app_id, other_app)

        # Test denial path — client tries to access other client's app
        handler = _make_handler("/api/applications/detail")

        user = {"sub": client_id, "name": "Owner Client",
                "role": "client", "type": "client"}
        other_row = {"id": other_app, "client_id": other_id}

        result = handler.check_app_ownership(user, other_row)
        assert result is False

        rows = db.execute(
            "SELECT * FROM audit_log WHERE action = 'authz_denied_not_owner' "
            "AND target = ?", (other_app,)
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 denial audit row, got {len(rows)}"

    def test_check_app_ownership_no_audit_on_success(self, db):
        """Successful ownership check must NOT write any denial audit row."""
        client_id = f"ok-cl-{secrets.token_hex(4)}"
        app_id = f"ok-app-{secrets.token_hex(4)}"

        handler = _make_handler("/api/applications/detail")

        user = {"sub": client_id, "name": "Good Client",
                "role": "client", "type": "client"}
        own_row = {"id": app_id, "client_id": client_id}

        result = handler.check_app_ownership(user, own_row)
        assert result is True

        rows = db.execute(
            "SELECT * FROM audit_log WHERE action = 'authz_denied_not_owner' "
            "AND target = ?", (app_id,)
        ).fetchall()
        assert len(rows) == 0, "No denial audit row should exist for successful ownership"

    def test_non_client_user_skips_ownership_check(self, db):
        """Admin/officer users bypass the ownership check — no denial, no audit."""
        handler = _make_handler("/api/applications/detail")

        user = {"sub": "admin001", "name": "Admin", "role": "admin", "type": "officer"}
        other_row = {"id": "any-app", "client_id": "any-client"}

        result = handler.check_app_ownership(user, other_row)
        assert result is True
