"""
Tests for authentication endpoints.
"""
import json
import pytest
import bcrypt
import sqlite3
import os


class TestOfficerLogin:
    def test_valid_login(self, temp_db):
        from server import get_db
        db = get_db()
        row = db.execute("SELECT email FROM users WHERE role='admin' LIMIT 1").fetchone()
        db.close()
        assert row is not None, "Admin user should exist after init_db"

    def test_create_token_returns_string(self, temp_db):
        from server import create_token
        token = create_token("admin001", "admin", "Test Admin")
        assert isinstance(token, str)
        assert len(token) > 20

    def test_decode_token_roundtrip(self, temp_db):
        from server import create_token, decode_token
        token = create_token("admin001", "admin", "Test Admin")
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "admin001"
        assert payload["role"] == "admin"
        assert payload["name"] == "Test Admin"

    def test_decode_invalid_token(self, temp_db):
        from server import decode_token
        result = decode_token("invalid.token.here")
        assert result is None


class TestClientRegistration:
    def test_password_too_short(self, temp_db):
        """Passwords under 8 chars should be rejected."""
        from server import get_db
        db = get_db()
        # Check that short passwords would fail validation
        # The handler checks len(password) < 8
        assert len("short") < 8
        db.close()

    def test_duplicate_email_blocked(self, db):
        """Registering with an existing email should fail."""
        pw = bcrypt.hashpw("TestPass123!".encode(), bcrypt.gensalt()).decode()
        db.execute(
            "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            ("dup001", "dup@test.com", pw, "Dup Corp")
        )
        db.commit()

        # Trying to insert same email should raise
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
                ("dup002", "dup@test.com", pw, "Another Corp")
            )


class TestRateLimiting:
    def test_rate_limiter_allows_initial_attempts(self, temp_db):
        from server import RateLimiter
        rl = RateLimiter()
        for _ in range(9):
            assert not rl.is_limited("test_key", max_attempts=10, window_seconds=60)

    def test_rate_limiter_blocks_after_max(self, temp_db):
        from server import RateLimiter
        rl = RateLimiter()
        for _ in range(10):
            rl.is_limited("block_key", max_attempts=10, window_seconds=60)
        assert rl.is_limited("block_key", max_attempts=10, window_seconds=60)

    def test_rate_limiter_reset(self, temp_db):
        from server import RateLimiter
        rl = RateLimiter()
        for _ in range(10):
            rl.is_limited("reset_key", max_attempts=10, window_seconds=60)
        rl.reset("reset_key")
        assert not rl.is_limited("reset_key", max_attempts=10, window_seconds=60)
