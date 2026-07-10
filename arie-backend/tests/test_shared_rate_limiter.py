import concurrent.futures
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def _delete_shared_key(key):
    from db import get_db

    db = get_db()
    try:
        db.execute("DELETE FROM shared_rate_limits WHERE key = ?", (key,))
        db.commit()
    finally:
        db.close()


def test_shared_rate_limiter_allows_until_limit_then_blocks(temp_db):
    from auth import RateLimiter

    key = "test:shared:limit-boundary"
    _delete_shared_key(key)
    limiter = RateLimiter()

    first = limiter.check_shared_limit(key, max_attempts=2, window_seconds=60, now=1000)
    second = limiter.check_shared_limit(key, max_attempts=2, window_seconds=60, now=1001)
    third = limiter.check_shared_limit(key, max_attempts=2, window_seconds=60, now=1002)

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.attempts == 3
    assert third.retry_after > 0


def test_shared_rate_limiter_persists_across_instances(temp_db):
    from auth import RateLimiter

    key = "test:shared:cross-instance"
    _delete_shared_key(key)

    assert RateLimiter().check_shared_limit(key, max_attempts=1, window_seconds=60, now=2000).allowed
    assert not RateLimiter().check_shared_limit(key, max_attempts=1, window_seconds=60, now=2001).allowed


def test_shared_rate_limiter_window_expiry_allows_again(temp_db):
    from auth import RateLimiter

    key = "test:shared:expiry"
    _delete_shared_key(key)
    limiter = RateLimiter()

    assert limiter.check_shared_limit(key, max_attempts=1, window_seconds=60, now=3000).allowed
    assert not limiter.check_shared_limit(key, max_attempts=1, window_seconds=60, now=3001).allowed
    assert limiter.check_shared_limit(key, max_attempts=1, window_seconds=60, now=3061).allowed


def test_shared_rate_limiter_db_failure_fails_closed(monkeypatch, temp_db):
    import db as db_module
    from auth import RateLimitBackendUnavailable, RateLimiter

    def fail_get_db():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db_module, "get_db", fail_get_db)

    with pytest.raises(RateLimitBackendUnavailable):
        RateLimiter().check_shared_limit("test:shared:db-down", max_attempts=1, window_seconds=60)


def test_shared_rate_limiter_concurrent_attempts_do_not_bypass_limit(temp_db):
    from auth import RateLimitBackendUnavailable, RateLimiter

    key = "test:shared:concurrent"
    _delete_shared_key(key)

    def attempt(i):
        try:
            result = RateLimiter().check_shared_limit(
                key,
                max_attempts=5,
                window_seconds=60,
                now=4000 + (i / 1000),
            )
            return result.allowed
        except RateLimitBackendUnavailable:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(20)))

    assert sum(1 for allowed in results if allowed) <= 5
    assert any(not allowed for allowed in results)


def test_shared_rate_limiter_key_hashes_sensitive_dimensions():
    from auth import build_shared_rate_limit_key

    key = build_shared_rate_limit_key(
        "forgot_password",
        email="Victim.User+reset@example.com",
        ip="203.0.113.45",
        token="raw-reset-token-secret",
    )

    assert key.startswith("shared:v1:forgot_password:")
    assert "Victim" not in key
    assert "victim" not in key
    assert "example.com" not in key
    assert "203.0.113.45" not in key
    assert "raw-reset-token-secret" not in key
    assert len(key.split(":")[-1]) == 64


def test_sensitive_rate_limit_over_limit_writes_429(monkeypatch):
    import base_handler

    class DummyHandler:
        request = SimpleNamespace(path="/api/applications/app/documents")
        request_id = "req-test"
        evaluate_sensitive_rate_limit = base_handler.BaseHandler.evaluate_sensitive_rate_limit

        def __init__(self):
            self.status = None
            self.body = None

        def get_client_ip(self):
            return "198.51.100.8"

        def get_current_user_token(self):
            return {"sub": "client-1"}

        def set_status(self, status):
            self.status = status

        def write(self, body):
            self.body = body

    monkeypatch.setattr(
        base_handler.rate_limiter,
        "check_shared_limit",
        lambda *args, **kwargs: SimpleNamespace(allowed=False, retry_after=42),
    )

    handler = DummyHandler()
    allowed = base_handler.BaseHandler.check_sensitive_rate_limit(
        handler,
        "doc_upload",
        max_attempts=1,
        window_seconds=60,
        error_message="Rate limit exceeded for doc_upload. Try again later.",
    )

    assert allowed is False
    assert handler.status == 429
    assert handler.body == {
        "error": "Rate limit exceeded for doc_upload. Try again later.",
        "retry_after": 42,
    }


def test_sensitive_rate_limit_backend_failure_writes_503(monkeypatch):
    import base_handler
    from auth import RateLimitBackendUnavailable

    class DummyHandler:
        request = SimpleNamespace(path="/api/auth/client/forgot-password")
        request_id = "req-test"
        evaluate_sensitive_rate_limit = base_handler.BaseHandler.evaluate_sensitive_rate_limit

        def __init__(self):
            self.status = None
            self.body = None

        def get_client_ip(self):
            return "198.51.100.9"

        def get_current_user_token(self):
            return None

        def set_status(self, status):
            self.status = status

        def write(self, body):
            self.body = body

    def fail_limit(*args, **kwargs):
        raise RateLimitBackendUnavailable("database down")

    monkeypatch.setattr(base_handler.rate_limiter, "check_shared_limit", fail_limit)

    handler = DummyHandler()
    allowed = base_handler.BaseHandler.check_sensitive_rate_limit(
        handler,
        "forgot_password_ip",
        max_attempts=1,
        window_seconds=60,
        include_user=False,
    )

    assert allowed is False
    assert handler.status == 503
    assert handler.body == {"error": "Rate limiter unavailable. Try again later."}


def _pg_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


def test_live_postgres_shared_rate_limiter_cross_connection_concurrency(monkeypatch):
    dsn = _pg_dsn()
    if not dsn:
        pytest.skip("Set TEST_POSTGRES_DSN or DATABASE_URL_TEST for live PostgreSQL validation.")

    import psycopg2
    import db as db_module
    from auth import RateLimiter

    with psycopg2.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS shared_rate_limits")
            cur.execute(
                """
                CREATE TABLE shared_rate_limits (
                    key TEXT PRIMARY KEY,
                    window_start DOUBLE PRECISION NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    expires_at DOUBLE PRECISION NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                "CREATE INDEX idx_shared_rate_limits_expires_at "
                "ON shared_rate_limits(expires_at)"
            )

    def pg_get_db():
        return db_module.DBConnection(psycopg2.connect(dsn), is_postgres=True)

    monkeypatch.setattr(db_module, "get_db", pg_get_db)

    key = "test:pg:shared:concurrent"

    assert RateLimiter().check_shared_limit(
        key, max_attempts=2, window_seconds=60, now=5000
    ).allowed
    assert RateLimiter().check_shared_limit(
        key, max_attempts=2, window_seconds=60, now=5001
    ).allowed
    assert not RateLimiter().check_shared_limit(
        key, max_attempts=2, window_seconds=60, now=5002
    ).allowed

    expired = RateLimiter().check_shared_limit(
        key, max_attempts=2, window_seconds=60, now=5061
    )
    assert expired.allowed
    assert expired.attempts == 1

    def attempt(i):
        result = RateLimiter().check_shared_limit(
            key,
            max_attempts=5,
            window_seconds=60,
            now=6000 + (i / 1000),
        )
        return result.allowed

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(24)))

    assert sum(1 for allowed in results if allowed) <= 5
    assert any(not allowed for allowed in results)


def test_sensitive_endpoints_use_shared_fail_closed_limiter():
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")

    for marker in (
        '"forgot_password_ip"',
        '"forgot_password_email"',
        '"reset_password_ip"',
        '"reset_password_token"',
        '"doc_upload"',
        '"ai_verify"',
    ):
        assert marker in source
    assert "check_sensitive_rate_limit" in source
    assert "evaluate_sensitive_rate_limit" in source
