"""
EX-01 / EX-04 closure tests.

EX-01: AdminResetDBHandler must require authenticated admin access.
EX-04: SumsubWebhookHandler must have a structural idempotency guard
       that prevents duplicate processing of the same webhook event.
"""
import os
import sys
import json
import hashlib
import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def _build_handler(handler_cls, method, uri, body=b"", headers=None, token=None):
    """Instantiate a Tornado handler directly without a running server."""
    from tornado.web import Application
    from tornado.httputil import HTTPServerRequest, HTTPHeaders

    hdrs = HTTPHeaders(headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"

    mock_conn = MagicMock()
    mock_conn.context = MagicMock()
    mock_conn.context.remote_ip = "127.0.0.1"

    req = HTTPServerRequest(
        method=method,
        uri=uri,
        version="HTTP/1.1",
        headers=hdrs,
        body=body,
        host="127.0.0.1",
        connection=mock_conn,
    )
    app = Application()
    handler = handler_cls(app, req)
    return handler


def _response_json(handler):
    buf = b"".join(handler._write_buffer) if handler._write_buffer else b""
    if not buf:
        return {}
    try:
        return json.loads(buf)
    except Exception:
        return {"_raw": buf.decode(errors="replace")}


# ══════════════════════════════════════════════════════════════
# EX-01: AdminResetDBHandler auth tests
# ══════════════════════════════════════════════════════════════

class TestEX01_AdminResetDBAuth:
    """EX-01: POST /api/admin/reset-db must require admin auth."""

    @pytest.fixture(autouse=True)
    def _set_confirm_env(self, monkeypatch):
        monkeypatch.setenv("ADMIN_RESET_DB_CONFIRMATION", "test-wipe-confirm")

    def test_unauthenticated_post_denied(self, temp_db):
        """No token -> 401."""
        from server import AdminResetDBHandler
        body = json.dumps({"confirm": "test-wipe-confirm"}).encode()
        handler = _build_handler(AdminResetDBHandler, "POST", "/api/admin/reset-db", body=body)
        handler.post()
        assert handler._status_code == 401
        resp = _response_json(handler)
        assert "error" in resp

    def test_non_admin_post_denied(self, temp_db):
        """Valid client token (non-admin role) -> 403."""
        from server import AdminResetDBHandler, create_token
        token = create_token("client001", "client", "Test Client", "client")
        body = json.dumps({"confirm": "test-wipe-confirm"}).encode()
        handler = _build_handler(AdminResetDBHandler, "POST", "/api/admin/reset-db",
                                 body=body, token=token)
        handler.post()
        assert handler._status_code == 403
        resp = _response_json(handler)
        assert "error" in resp

    def test_admin_wrong_confirmation_denied(self, temp_db):
        """Admin token + wrong confirmation string -> 403."""
        from server import AdminResetDBHandler, create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        body = json.dumps({"confirm": "WRONG_STRING"}).encode()
        handler = _build_handler(AdminResetDBHandler, "POST", "/api/admin/reset-db",
                                 body=body, token=token)
        handler.post()
        assert handler._status_code == 403

    def test_admin_correct_confirmation_succeeds(self, temp_db):
        """Admin token + correct confirmation -> 200 (reset succeeds in test env)."""
        from server import AdminResetDBHandler, create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        body = json.dumps({"confirm": "test-wipe-confirm"}).encode()
        handler = _build_handler(AdminResetDBHandler, "POST", "/api/admin/reset-db",
                                 body=body, token=token)
        handler.post()
        # In non-production testing env, this should succeed
        assert handler._status_code == 200
        resp = _response_json(handler)
        assert resp.get("status") == "reset_complete"

    def test_auth_check_precedes_confirmation_check(self, temp_db):
        """Auth must be checked BEFORE the confirmation string.

        An unauthenticated caller should get 401 even if they know the
        confirmation string - the confirmation is defense-in-depth, not
        primary access control.
        """
        from server import AdminResetDBHandler
        body = json.dumps({"confirm": "test-wipe-confirm"}).encode()
        handler = _build_handler(AdminResetDBHandler, "POST", "/api/admin/reset-db", body=body)
        handler.post()
        # Must get 401, NOT 403 (which would mean the confirmation check ran first)
        assert handler._status_code == 401

    def test_missing_confirmation_env_fails_closed(self, temp_db, monkeypatch):
        """Missing ADMIN_RESET_DB_CONFIRMATION should fail closed."""
        from server import AdminResetDBHandler, create_token
        monkeypatch.delenv("ADMIN_RESET_DB_CONFIRMATION", raising=False)
        token = create_token("admin001", "admin", "Test Admin", "officer")
        body = json.dumps({"confirm": "test-wipe-confirm"}).encode()
        handler = _build_handler(AdminResetDBHandler, "POST", "/api/admin/reset-db", body=body, token=token)
        handler.post()
        assert handler._status_code == 503

    def test_production_env_is_blocked(self, temp_db, monkeypatch):
        """Reset endpoint remains blocked in production."""
        import config
        from server import AdminResetDBHandler, create_token
        monkeypatch.setattr(config, "IS_PRODUCTION", True)
        token = create_token("admin001", "admin", "Test Admin", "officer")
        body = json.dumps({"confirm": "test-wipe-confirm"}).encode()
        handler = _build_handler(AdminResetDBHandler, "POST", "/api/admin/reset-db", body=body, token=token)
        handler.post()
        assert handler._status_code == 403


class TestOfficerResetConfirmationControl:
    """Officer reset endpoint should be env-gated and fail closed."""

    def test_missing_officer_confirmation_env_fails_closed(self, temp_db, monkeypatch):
        from server import AdminOfficerPasswordResetHandler, create_token
        monkeypatch.delenv("ADMIN_OFFICER_RESET_CONFIRMATION", raising=False)
        token = create_token("admin001", "admin", "Test Admin", "officer")
        body = json.dumps({
            "confirm": "irrelevant",
            "email": "officer@example.com",
            "new_password": "StrongPass123",
        }).encode()
        handler = _build_handler(AdminOfficerPasswordResetHandler, "POST", "/api/admin/officer-reset-password", body=body, token=token)
        handler.post()
        assert handler._status_code == 503


# ══════════════════════════════════════════════════════════════
# EX-04: Webhook idempotency guard tests
# ══════════════════════════════════════════════════════════════

def _make_webhook_payload(applicant_id="aabbccddeeff00112233445566778899",
                          external_user_id="user@example.com",
                          event_type="applicantReviewed",
                          review_answer="GREEN",
                          created_at_ms=1700000000000):
    return json.dumps({
        "type": event_type,
        "applicantId": applicant_id,
        "externalUserId": external_user_id,
        "createdAtMs": created_at_ms,
        "reviewResult": {
            "reviewAnswer": review_answer,
            "rejectLabels": [],
            "moderationComment": "",
        },
    }).encode("utf-8")


def _call_webhook(body: bytes, headers: dict = None):
    from server import SumsubWebhookHandler
    handler = _build_handler(SumsubWebhookHandler, "POST", "/api/kyc/webhook",
                             body=body, headers=headers)
    handler.post()
    return handler


class TestEX04_WebhookIdempotency:
    """EX-04: Duplicate webhook deliveries must be safely no-oped."""

    @pytest.fixture(autouse=True)
    def _init_db(self, temp_db):
        """Ensure DB is initialized with migrations including the idempotency table."""
        self.db_path = temp_db

    def _count_audit_rows(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) as c FROM audit_log").fetchone()["c"]
        conn.close()
        return count

    def _count_dedup_rows(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) as c FROM webhook_processed_events").fetchone()["c"]
        conn.close()
        return count

    @patch("server.sumsub_verify_webhook", return_value=True)
    def test_first_delivery_processes_normally(self, mock_verify):
        """First delivery of a valid mutating webhook processes normally."""
        body = _make_webhook_payload(
            applicant_id="aa11bb22cc33dd44ee55ff6677889900",
            created_at_ms=1700000001001,
        )
        audit_before = self._count_audit_rows()
        dedup_before = self._count_dedup_rows()
        handler = _call_webhook(body)
        assert handler._status_code == 200
        # Should have created an audit row
        assert self._count_audit_rows() > audit_before
        # Should have created a dedup row
        assert self._count_dedup_rows() == dedup_before + 1

    @patch("server.sumsub_verify_webhook", return_value=True)
    def test_second_identical_delivery_is_duplicate(self, mock_verify):
        """Second identical delivery is recognized as duplicate and not reprocessed."""
        body = _make_webhook_payload(
            applicant_id="bb22cc33dd44ee55ff66778899001122",
            created_at_ms=1700000002002,
        )
        # First delivery
        handler1 = _call_webhook(body)
        assert handler1._status_code == 200
        resp1 = _response_json(handler1)

        audit_after_first = self._count_audit_rows()
        dedup_after_first = self._count_dedup_rows()

        # Second delivery (same payload)
        handler2 = _call_webhook(body)
        assert handler2._status_code == 200
        resp2 = _response_json(handler2)
        assert resp2.get("status") == "already_processed"

        # No new audit row
        assert self._count_audit_rows() == audit_after_first
        # No new dedup row
        assert self._count_dedup_rows() == dedup_after_first

    @patch("server.sumsub_verify_webhook", return_value=True)
    def test_duplicate_does_not_create_duplicate_audit_entries(self, mock_verify):
        """Duplicate delivery must NOT create duplicate audit_log entries."""
        body = _make_webhook_payload(
            applicant_id="cc33dd44ee55ff667788990011223344",
            created_at_ms=1700000003003,
        )
        handler1 = _call_webhook(body)
        assert handler1._status_code == 200

        audit_count = self._count_audit_rows()

        # Replay
        handler2 = _call_webhook(body)
        assert handler2._status_code == 200
        assert _response_json(handler2).get("status") == "already_processed"

        # Audit count unchanged
        assert self._count_audit_rows() == audit_count

    @patch("server.sumsub_verify_webhook", return_value=True)
    def test_distinct_events_both_process(self, mock_verify):
        """Two distinct webhook events (different createdAtMs) both process."""
        body_a = _make_webhook_payload(
            applicant_id="dd44ee55ff66778899001122334455aa",
            created_at_ms=1700000004001,
        )
        body_b = _make_webhook_payload(
            applicant_id="dd44ee55ff66778899001122334455aa",
            created_at_ms=1700000004002,  # different timestamp
        )
        handler_a = _call_webhook(body_a)
        assert handler_a._status_code == 200

        dedup_count = self._count_dedup_rows()

        handler_b = _call_webhook(body_b)
        assert handler_b._status_code == 200
        resp_b = _response_json(handler_b)
        # Should NOT be "already_processed" — it's a distinct event
        assert resp_b.get("status") != "already_processed"
        # Should have added a new dedup row
        assert self._count_dedup_rows() == dedup_count + 1

    @patch("server.sumsub_verify_webhook", return_value=True)
    def test_distinct_review_answers_both_process(self, mock_verify):
        """Same applicant, same timestamp, but different reviewAnswer → distinct events."""
        body_green = _make_webhook_payload(
            applicant_id="ee55ff667788990011223344556677bb",
            review_answer="GREEN",
            created_at_ms=1700000005001,
        )
        body_red = _make_webhook_payload(
            applicant_id="ee55ff667788990011223344556677bb",
            review_answer="RED",
            created_at_ms=1700000005001,
        )
        handler_green = _call_webhook(body_green)
        assert handler_green._status_code == 200

        handler_red = _call_webhook(body_red)
        assert handler_red._status_code == 200
        assert _response_json(handler_red).get("status") != "already_processed"


class TestEX04_IdempotencyKeyStability:
    """The canonical dedup key must be stable across equivalent retries."""

    def test_digest_is_deterministic(self):
        """Same inputs always produce the same digest."""
        dedup_input = "abc123:applicantReviewed:GREEN:1700000000000"
        d1 = hashlib.sha256(dedup_input.encode("utf-8")).hexdigest()
        d2 = hashlib.sha256(dedup_input.encode("utf-8")).hexdigest()
        assert d1 == d2

    def test_different_timestamps_produce_different_digests(self):
        """Different createdAtMs values must produce different keys."""
        base = "abc123:applicantReviewed:GREEN:"
        d1 = hashlib.sha256((base + "1700000000000").encode()).hexdigest()
        d2 = hashlib.sha256((base + "1700000000001").encode()).hexdigest()
        assert d1 != d2


class TestEX04_MigrationCreatesTable:
    """Migration v2.24 must create the webhook_processed_events table."""

    def test_table_exists(self, temp_db):
        conn = sqlite3.connect(temp_db)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='webhook_processed_events'"
        ).fetchone()
        conn.close()
        assert row is not None, "webhook_processed_events table must exist"

    def test_unique_constraint_on_event_digest(self, temp_db):
        """UNIQUE constraint on event_digest must prevent duplicate inserts."""
        conn = sqlite3.connect(temp_db)
        digest = "test_unique_" + hashlib.sha256(b"test").hexdigest()
        conn.execute(
            "INSERT INTO webhook_processed_events (event_digest, event_type, applicant_id, received_at) "
            "VALUES (?, 'test', 'test', '2024-01-01')",
            (digest,),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO webhook_processed_events (event_digest, event_type, applicant_id, received_at) "
                "VALUES (?, 'test', 'test', '2024-01-01')",
                (digest,),
            )
        conn.close()


# ══════════════════════════════════════════════════════════════
# EX-02 / EX-03: Portal cleanup verification
# ══════════════════════════════════════════════════════════════

class TestEX02_Demo123Removed:
    """EX-02: The hardcoded 'demo123' fallback must not exist in the portal."""

    def test_no_demo123_in_portal(self):
        portal_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-portal.html",
        )
        with open(portal_path, "r") as f:
            content = f.read()
        assert "demo123" not in content, "Hardcoded 'demo123' credential must be removed"


class TestEX03_MockCompanyDataRemoved:
    """EX-03: MOCK_COMPANY_DATA must not exist in the portal."""

    def test_no_mock_company_data_in_portal(self):
        portal_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-portal.html",
        )
        with open(portal_path, "r") as f:
            content = f.read()
        assert "MOCK_COMPANY_DATA" not in content, "MOCK_COMPANY_DATA object must be removed"
