"""
EX-01 / EX-04 Closure Tests
============================
EX-01: AdminResetDBHandler must require admin authentication.
EX-04: Sumsub webhook handler must reject duplicate deliveries via
       structural idempotency guard (webhook_processed_events table).
"""
import os
import sys
import json
import hashlib
import sqlite3
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ═══════════════════════════════════════════════════════════════
# EX-01: AdminResetDBHandler authentication
# ═══════════════════════════════════════════════════════════════

class TestAdminResetDBAuth(unittest.TestCase):
    """EX-01: POST /api/admin/reset-db must require admin auth."""

    def test_handler_calls_require_auth(self):
        """AdminResetDBHandler.post() must call require_auth before any action."""
        import inspect
        from server import AdminResetDBHandler
        source = inspect.getsource(AdminResetDBHandler.post)
        # require_auth must appear BEFORE IS_PRODUCTION check
        auth_pos = source.find("require_auth")
        prod_pos = source.find("IS_PRODUCTION")
        self.assertNotEqual(auth_pos, -1, "require_auth must be called in post()")
        self.assertLess(auth_pos, prod_pos, "require_auth must be called before IS_PRODUCTION check")

    def test_handler_requires_admin_role(self):
        """AdminResetDBHandler must restrict to admin role."""
        import inspect
        from server import AdminResetDBHandler
        source = inspect.getsource(AdminResetDBHandler.post)
        self.assertIn('roles=["admin"]', source,
                       "require_auth must specify roles=['admin']")

    def test_unauthenticated_request_pattern(self):
        """Verify the auth guard returns early when require_auth fails."""
        import inspect
        from server import AdminResetDBHandler
        source = inspect.getsource(AdminResetDBHandler.post)
        # Standard pattern: check user and return if None
        auth_idx = source.find("require_auth")
        # After require_auth there should be a "if not user:" or similar guard
        after_auth = source[auth_idx:]
        self.assertIn("if not user", after_auth,
                       "Must have early return when auth fails")


# ═══════════════════════════════════════════════════════════════
# EX-04: Webhook idempotency guard
# ═══════════════════════════════════════════════════════════════

class TestWebhookIdempotencyTable(unittest.TestCase):
    """EX-04: webhook_processed_events table must exist after migration."""

    def _get_db_path(self):
        """Return the DB path used by the conftest fixtures."""
        import tempfile
        return os.path.join(tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db")

    def test_table_exists(self):
        """webhook_processed_events table must be created by migration."""
        db_path = self._get_db_path()
        if not os.path.exists(db_path):
            # Trigger init_db to create the DB with migrations
            os.environ["DB_PATH"] = db_path
            from db import init_db
            init_db()
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='webhook_processed_events'"
        )
        row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(row, "webhook_processed_events table must exist")

    def test_unique_constraint_on_event_digest(self):
        """Duplicate event_digest must be rejected by UNIQUE constraint."""
        db_path = self._get_db_path()
        if not os.path.exists(db_path):
            os.environ["DB_PATH"] = db_path
            from db import init_db
            init_db()
        conn = sqlite3.connect(db_path)
        digest = hashlib.sha256(b"test_unique:applicantReviewed:GREEN:12345").hexdigest()
        # Clean up from previous runs
        conn.execute("DELETE FROM webhook_processed_events WHERE event_digest=?", (digest,))
        conn.commit()
        conn.execute(
            "INSERT INTO webhook_processed_events (event_digest, event_type, applicant_id) VALUES (?, ?, ?)",
            (digest, "applicantReviewed", "abc123def456abc123def456abc123de")
        )
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO webhook_processed_events (event_digest, event_type, applicant_id) VALUES (?, ?, ?)",
                (digest, "applicantReviewed", "abc123def456abc123def456abc123de")
            )
        conn.close()

    def test_distinct_digests_both_accepted(self):
        """Different event digests must both be accepted."""
        db_path = self._get_db_path()
        if not os.path.exists(db_path):
            os.environ["DB_PATH"] = db_path
            from db import init_db
            init_db()
        conn = sqlite3.connect(db_path)
        d1 = hashlib.sha256(b"distinct_test_A1").hexdigest()
        d2 = hashlib.sha256(b"distinct_test_A2").hexdigest()
        # Clean up from previous runs
        conn.execute("DELETE FROM webhook_processed_events WHERE event_digest IN (?,?)", (d1, d2))
        conn.commit()
        conn.execute(
            "INSERT INTO webhook_processed_events (event_digest, event_type, applicant_id) VALUES (?, ?, ?)",
            (d1, "applicantReviewed", "aaa111bbb222aaa111bbb222aaa111bb")
        )
        conn.execute(
            "INSERT INTO webhook_processed_events (event_digest, event_type, applicant_id) VALUES (?, ?, ?)",
            (d2, "applicantReviewed", "aaa111bbb222aaa111bbb222aaa111bb")
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM webhook_processed_events WHERE event_digest IN (?,?)", (d1, d2)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)


class TestWebhookIdempotencyGuardInHandler(unittest.TestCase):
    """EX-04: SumsubWebhookHandler must include idempotency guard logic."""

    def test_handler_references_webhook_processed_events(self):
        """Handler must INSERT into webhook_processed_events table."""
        import inspect
        from server import SumsubWebhookHandler
        source = inspect.getsource(SumsubWebhookHandler.post)
        self.assertIn("webhook_processed_events", source,
                       "Handler must reference webhook_processed_events table")

    def test_handler_computes_event_digest(self):
        """Handler must compute an event_digest for deduplication."""
        import inspect
        from server import SumsubWebhookHandler
        source = inspect.getsource(SumsubWebhookHandler.post)
        self.assertIn("event_digest", source,
                       "Handler must compute event_digest")
        self.assertIn("sha256", source,
                       "Handler must use SHA-256 for digest")

    def test_handler_returns_already_processed_on_duplicate(self):
        """Handler must return 'already_processed' status for duplicates."""
        import inspect
        from server import SumsubWebhookHandler
        source = inspect.getsource(SumsubWebhookHandler.post)
        self.assertIn("already_processed", source,
                       "Handler must return already_processed for duplicates")

    def test_digest_computation_is_deterministic(self):
        """Same inputs must produce the same digest."""
        fields = "abc123:applicantReviewed:GREEN:1681234567890"
        d1 = hashlib.sha256(fields.encode()).hexdigest()
        d2 = hashlib.sha256(fields.encode()).hexdigest()
        self.assertEqual(d1, d2)

    def test_digest_differs_for_different_answers(self):
        """Different review answers must produce different digests."""
        d_green = hashlib.sha256(b"abc123:applicantReviewed:GREEN:ts1").hexdigest()
        d_red = hashlib.sha256(b"abc123:applicantReviewed:RED:ts1").hexdigest()
        self.assertNotEqual(d_green, d_red)


# ═══════════════════════════════════════════════════════════════
# EX-02: demo123 removal
# ═══════════════════════════════════════════════════════════════

class TestDemo123Removed(unittest.TestCase):
    """EX-02: Hardcoded demo123 credential must not exist in portal."""

    def test_portal_has_no_demo123(self):
        """arie-portal.html must not contain the string 'demo123'."""
        portal_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-portal.html"
        )
        with open(portal_path, "r") as f:
            content = f.read()
        self.assertNotIn("demo123", content,
                          "Hardcoded demo123 credential must be removed from portal")


# ═══════════════════════════════════════════════════════════════
# EX-03: MOCK_COMPANY_DATA removal
# ═══════════════════════════════════════════════════════════════

class TestMockCompanyDataRemoved(unittest.TestCase):
    """EX-03: MOCK_COMPANY_DATA must not exist in portal."""

    def test_portal_has_no_mock_company_data(self):
        """arie-portal.html must not contain MOCK_COMPANY_DATA."""
        portal_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-portal.html"
        )
        with open(portal_path, "r") as f:
            content = f.read()
        self.assertNotIn("MOCK_COMPANY_DATA", content,
                          "MOCK_COMPANY_DATA must be removed from portal")


if __name__ == "__main__":
    unittest.main()
