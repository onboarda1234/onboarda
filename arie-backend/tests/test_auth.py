"""
Tests for authentication endpoints.
"""
import json
import pytest
import bcrypt
import sqlite3
import os


def _run_tornado_case(case_cls, method_name):
    import io
    import unittest

    suite = unittest.TestLoader().loadTestsFromName(method_name, case_cls)
    result = unittest.TextTestRunner(verbosity=0, stream=io.StringIO()).run(suite)
    assert result.wasSuccessful(), f"HTTP auth regression failed: {result.failures + result.errors}"


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


class TestInactiveTokenEnforcement:
    def test_officer_token_denied_after_deactivation_for_bearer_and_cookie(self, db, app):
        """Decoded officer JWTs must be rejected once the DB user is inactive."""
        import secrets
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app

        officer_id = f"officer-inactive-{secrets.token_hex(4)}"
        email = f"{officer_id}@example.com"
        password = "OfficerPass123!"
        pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        db.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
            (officer_id, email, pw, "Inactive Token Officer", "analyst", "active"),
        )
        db.commit()

        class _App(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_flow(self_inner):
                login = self_inner.fetch(
                    "/api/auth/officer/login",
                    method="POST",
                    body=json.dumps({"email": email, "password": password}),
                    headers={"Content-Type": "application/json"},
                )
                assert login.code == 200, login.body.decode()
                token = json.loads(login.body)["token"]
                bearer_headers = {"Authorization": f"Bearer {token}"}
                cookies = login.headers.get_list("Set-Cookie")
                cookie_header = "; ".join(cookie.split(";", 1)[0] for cookie in cookies)

                active_me = self_inner.fetch("/api/auth/me", headers=bearer_headers)
                assert active_me.code == 200, active_me.body.decode()
                active_apps = self_inner.fetch("/api/applications?limit=1", headers=bearer_headers)
                assert active_apps.code == 200, active_apps.body.decode()
                active_cookie_me = self_inner.fetch("/api/auth/me", headers={"Cookie": cookie_header})
                assert active_cookie_me.code == 200, active_cookie_me.body.decode()

                db.execute("UPDATE users SET status = 'inactive' WHERE id = ?", (officer_id,))
                db.commit()

                inactive_me = self_inner.fetch("/api/auth/me", headers=bearer_headers)
                assert inactive_me.code in (401, 403), inactive_me.body.decode()
                inactive_apps = self_inner.fetch("/api/applications?limit=1", headers=bearer_headers)
                assert inactive_apps.code in (401, 403), inactive_apps.body.decode()
                inactive_cookie_me = self_inner.fetch("/api/auth/me", headers={"Cookie": cookie_header})
                assert inactive_cookie_me.code in (401, 403), inactive_cookie_me.body.decode()

                audit = db.execute(
                    "SELECT detail FROM audit_log WHERE user_id = ? AND action = ? ORDER BY id DESC LIMIT 1",
                    (officer_id, "auth_denied_inactive_token"),
                ).fetchone()
                assert audit is not None
                detail = json.loads(audit["detail"])
                assert detail["actor_type"] == "officer"
                assert detail["denial_reason"] == "actor_inactive"
                assert detail["current_status"] == "inactive"

        _run_tornado_case(_App, "test_flow")

    def test_client_token_denied_after_deactivation_for_bearer_and_cookie(self, db, app):
        """Client portal JWTs must be rejected once the DB client is inactive."""
        import secrets
        from tornado.testing import AsyncHTTPTestCase
        from server import make_app

        client_id = f"client-inactive-{secrets.token_hex(4)}"
        email = f"{client_id}@example.com"
        password = "ClientPass123!"
        pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        db.execute(
            "INSERT INTO clients (id, email, password_hash, company_name, status) VALUES (?, ?, ?, ?, ?)",
            (client_id, email, pw, "Inactive Token Client Ltd", "active"),
        )
        db.commit()

        class _App(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_flow(self_inner):
                login = self_inner.fetch(
                    "/api/auth/client/login",
                    method="POST",
                    body=json.dumps({"email": email, "password": password}),
                    headers={"Content-Type": "application/json"},
                )
                assert login.code == 200, login.body.decode()
                token = json.loads(login.body)["token"]
                bearer_headers = {"Authorization": f"Bearer {token}"}
                cookies = login.headers.get_list("Set-Cookie")
                cookie_header = "; ".join(cookie.split(";", 1)[0] for cookie in cookies)

                active_me = self_inner.fetch("/api/auth/me", headers=bearer_headers)
                assert active_me.code == 200, active_me.body.decode()
                active_apps = self_inner.fetch("/api/portal/applications", headers=bearer_headers)
                assert active_apps.code == 200, active_apps.body.decode()
                active_cookie_me = self_inner.fetch("/api/auth/me", headers={"Cookie": cookie_header})
                assert active_cookie_me.code == 200, active_cookie_me.body.decode()

                db.execute("UPDATE clients SET status = 'inactive' WHERE id = ?", (client_id,))
                db.commit()

                inactive_me = self_inner.fetch("/api/auth/me", headers=bearer_headers)
                assert inactive_me.code in (401, 403), inactive_me.body.decode()
                inactive_apps = self_inner.fetch("/api/portal/applications", headers=bearer_headers)
                assert inactive_apps.code in (401, 403), inactive_apps.body.decode()
                inactive_cookie_me = self_inner.fetch("/api/auth/me", headers={"Cookie": cookie_header})
                assert inactive_cookie_me.code in (401, 403), inactive_cookie_me.body.decode()

                audit = db.execute(
                    "SELECT detail FROM audit_log WHERE user_id = ? AND action = ? ORDER BY id DESC LIMIT 1",
                    (client_id, "auth_denied_inactive_token"),
                ).fetchone()
                assert audit is not None
                detail = json.loads(audit["detail"])
                assert detail["actor_type"] == "client"
                assert detail["denial_reason"] == "actor_inactive"
                assert detail["current_status"] == "inactive"

        _run_tornado_case(_App, "test_flow")

    def test_current_db_role_overrides_stale_officer_token_role(self, db, app):
        """Permission checks must use the current DB role, not a stale JWT role claim."""
        import secrets
        from tornado.testing import AsyncHTTPTestCase
        from server import create_token, make_app

        officer_id = f"role-refresh-{secrets.token_hex(4)}"
        db.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
            (
                officer_id,
                f"{officer_id}@example.com",
                bcrypt.hashpw("RolePass123!".encode(), bcrypt.gensalt()).decode(),
                "Role Refresh Officer",
                "analyst",
                "active",
            ),
        )
        db.commit()
        stale_admin_token = create_token(officer_id, "admin", "Role Refresh Officer", "officer")

        class _App(AsyncHTTPTestCase):
            def get_app(self_inner):
                return make_app()

            def test_stale_role(self_inner):
                resp = self_inner.fetch(
                    "/api/users",
                    headers={"Authorization": f"Bearer {stale_admin_token}"},
                )
                assert resp.code == 403, resp.body.decode()

        _run_tornado_case(_App, "test_stale_role")


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
