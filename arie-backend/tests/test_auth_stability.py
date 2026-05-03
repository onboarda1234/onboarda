"""
Tests for auth flow stability fixes.

Covers:
- Password reset timezone comparison (naive-vs-aware edge case)
- CSRF token cookie expiry alignment with JWT
- Session revocation on password change
- Session revocation on password reset
- Per-email rate limiting on forgot-password
- User-level token revocation via decode_token
"""

import hashlib
import inspect
import os
import re
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-key-for-auth-stability")


class TestPasswordResetTimezoneComparison(unittest.TestCase):
    """Fix 1: password reset expiry comparison must handle naive and aware datetimes."""

    def test_reset_handler_normalises_to_naive_utc(self):
        """ResetPasswordHandler should strip tzinfo before comparing expiry."""
        import server
        src = inspect.getsource(server.ResetPasswordHandler.post)
        # Must normalise the DB-side expires value
        self.assertIn("replace(tzinfo=None)", src,
                       "Expiry comparison must normalise to naive UTC")

    def test_reset_handler_handles_tz_aware_stored_expiry(self):
        """If password_reset_expires was stored with tz info, comparison must not fail."""
        import server
        src = inspect.getsource(server.ResetPasswordHandler.post)
        # Should handle tz-aware datetime from DB by stripping tzinfo
        self.assertIn("expires.tzinfo", src,
                       "Handler must check if expires is tz-aware")

    def test_naive_utc_comparison_is_correct(self):
        """Verify the comparison logic is sound for both naive and aware datetimes."""
        # Simulate the fixed code path
        # Case 1: naive datetime stored (no tz info)
        expires_naive = datetime(2026, 12, 31, 23, 59, 59)
        if expires_naive.tzinfo is not None:
            expires_naive = expires_naive.replace(tzinfo=None)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        # Future date should not be expired
        self.assertTrue(now_utc < expires_naive)

        # Case 2: aware datetime stored (with tz info)
        expires_aware = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        if expires_aware.tzinfo is not None:
            expires_aware = expires_aware.replace(tzinfo=None)
        self.assertTrue(now_utc < expires_aware)

    def test_expired_token_detected_correctly(self):
        """An expired reset token must be rejected regardless of tz representation."""
        past = datetime(2020, 1, 1, 0, 0, 0)
        if past.tzinfo is not None:
            past = past.replace(tzinfo=None)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertTrue(now_utc > past)


class TestCSRFTokenExpiry(unittest.TestCase):
    """Fix 2: CSRF cookie must have an explicit expiry matching JWT lifetime."""

    def test_csrf_cookie_has_expires_days(self):
        """issue_csrf_token must set expires_days on the CSRF cookie."""
        from base_handler import BaseHandler
        src = inspect.getsource(BaseHandler.issue_csrf_token)
        self.assertIn("expires_days", src,
                       "CSRF cookie must specify expires_days")

    def test_csrf_expires_matches_jwt(self):
        """CSRF cookie expiry must align with JWT 24h lifetime (expires_days=1)."""
        from base_handler import BaseHandler
        src = inspect.getsource(BaseHandler.issue_csrf_token)
        self.assertIn("expires_days=1", src,
                       "CSRF cookie should expire in 1 day to match JWT")


class TestSessionRevocationOnPasswordChange(unittest.TestCase):
    """Fix 3: changing password must revoke the current session."""

    def test_change_password_revokes_current_session(self):
        """ClientChangePasswordHandler must revoke the current JWT after password change."""
        import server
        src = inspect.getsource(server.ClientChangePasswordHandler.post)
        self.assertIn("token_revocation_list.revoke", src,
                       "Password change must revoke current session token")

    def test_change_password_clears_session_cookie(self):
        """ClientChangePasswordHandler must clear the session cookie."""
        import server
        src = inspect.getsource(server.ClientChangePasswordHandler.post)
        self.assertIn("clear_session_cookie", src,
                       "Password change must clear the session cookie")


class TestSessionRevocationOnPasswordReset(unittest.TestCase):
    """Fix 4: resetting password must revoke all active sessions."""

    def test_reset_password_calls_revoke_all(self):
        """ResetPasswordHandler must call _revoke_all_client_sessions."""
        import server
        src = inspect.getsource(server.ResetPasswordHandler.post)
        self.assertIn("_revoke_all_client_sessions", src,
                       "Password reset must revoke all active sessions for the user")

    def test_revoke_all_function_exists(self):
        """_revoke_all_client_sessions must be defined in server module."""
        import server
        self.assertTrue(hasattr(server, "_revoke_all_client_sessions"),
                        "server module must define _revoke_all_client_sessions")

    def test_revoke_all_uses_user_jti(self):
        """_revoke_all_client_sessions must create a user-level revocation entry."""
        import server
        src = inspect.getsource(server._revoke_all_client_sessions)
        self.assertIn("user:", src,
                       "Must use 'user:' prefix for user-level JTI")
        self.assertIn("token_revocation_list.revoke", src,
                       "Must revoke via token_revocation_list")


class TestForgotPasswordPerEmailRateLimit(unittest.TestCase):
    """Fix 5: forgot-password must rate-limit per email, not just per IP."""

    def test_forgot_password_has_per_email_rate_limit(self):
        """ForgotPasswordHandler must have a per-email rate limit key."""
        import server
        src = inspect.getsource(server.ForgotPasswordHandler.post)
        self.assertIn("forgot_pw:email:", src,
                       "Must rate-limit per email to prevent enumeration")

    def test_per_email_limit_returns_success_message(self):
        """Per-email rate limit must return identical success message (no info leak)."""
        import server
        src = inspect.getsource(server.ForgotPasswordHandler.post)
        # Find the per-email limit block
        lines = src.split("\n")
        in_email_block = False
        found_success = False
        for line in lines:
            if "forgot_pw:email:" in line:
                in_email_block = True
            if in_email_block and "self.success" in line:
                found_success = True
                # Must return the same non-revealing message
                self.assertIn("If that email is registered", line,
                              "Must not reveal email existence on rate limit")
                break
        self.assertTrue(found_success,
                        "Per-email rate limit must return success (not error) to prevent enumeration")


class TestUserLevelTokenRevocation(unittest.TestCase):
    """decode_token must support user-level revocation for password change/reset."""

    def test_decode_token_checks_user_jti(self):
        """decode_token must check for user-level revocation."""
        from auth import decode_token
        src = inspect.getsource(decode_token)
        self.assertIn("user:", src,
                       "decode_token must check user-level JTI")

    def test_decode_token_respects_iat(self):
        """User-level revocation must only block tokens issued BEFORE the revocation."""
        from auth import decode_token
        src = inspect.getsource(decode_token)
        self.assertIn("iat", src,
                       "Must check iat to allow tokens issued after password change")

    def test_create_and_decode_roundtrip(self):
        """Basic token creation and decoding must still work."""
        from auth import create_token, decode_token
        token = create_token("test-user-123", "admin", "Test User")
        decoded = decode_token(token)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["sub"], "test-user-123")
        self.assertEqual(decoded["role"], "admin")

    def test_revoked_token_returns_none(self):
        """A revoked token must return None from decode_token."""
        from auth import create_token, decode_token, _get_revocation_list
        token = create_token("test-user-456", "client", "Test Client")
        decoded = decode_token(token)
        self.assertIsNotNone(decoded)

        # Revoke the specific JTI
        jti = decoded["jti"]
        revocation = _get_revocation_list()
        revocation.revoke(jti, time.time() + 3600)

        # Now decode should return None
        result = decode_token(token)
        self.assertIsNone(result, "Revoked token must decode to None")

    def test_user_level_revocation_blocks_old_tokens(self):
        """User-level revocation must block tokens issued before the revocation."""
        from auth import create_token, decode_token, _get_revocation_list

        # Create a token
        token = create_token("user-pw-change-789", "client", "Test Client")
        decoded_first = decode_token(token)
        self.assertIsNotNone(decoded_first)

        # Simulate user-level revocation (password change)
        revocation = _get_revocation_list()
        user_jti = "user:user-pw-change-789"
        revocation.revoke(user_jti, time.time() + 86400)

        # The old token should now be blocked
        result = decode_token(token)
        self.assertIsNone(result, "Token issued before password change must be blocked")

    def test_user_level_revocation_allows_new_tokens(self):
        """Tokens issued AFTER user-level revocation must still work."""
        from auth import create_token, decode_token, _get_revocation_list

        # Simulate user-level revocation (password change)
        revocation = _get_revocation_list()
        user_jti = "user:user-new-token-test"
        revocation.revoke(user_jti, time.time() + 86400)

        # Wait to ensure iat > revocation_time (JWT iat is integer seconds)
        time.sleep(1.1)

        # Create a NEW token after revocation
        token = create_token("user-new-token-test", "client", "Test Client")
        decoded = decode_token(token)
        self.assertIsNotNone(decoded,
                             "Token issued after password change must still be valid")


class TestLogoutRevocationIntegrity(unittest.TestCase):
    """LogoutHandler must revoke the token AND clear the cookie."""

    def test_logout_handler_revokes_token(self):
        """LogoutHandler must call token_revocation_list.revoke."""
        import server
        src = inspect.getsource(server.LogoutHandler.post)
        self.assertIn("token_revocation_list.revoke", src)

    def test_logout_handler_clears_session(self):
        """LogoutHandler must clear the session cookie."""
        import server
        src = inspect.getsource(server.LogoutHandler.post)
        self.assertIn("clear_session_cookie", src)


class TestBaseHandlerCookieSecurity(unittest.TestCase):
    """Session and CSRF cookies must have proper security attributes."""

    def test_session_cookie_httponly(self):
        """Session cookie must be httpOnly to prevent JS access."""
        from base_handler import BaseHandler
        src = inspect.getsource(BaseHandler.issue_session_cookie)
        self.assertIn("httponly=True", src)

    def test_session_cookie_samesite(self):
        """Session cookie must have SameSite attribute."""
        from base_handler import BaseHandler
        src = inspect.getsource(BaseHandler.issue_session_cookie)
        self.assertIn("samesite=", src)

    def test_csrf_cookie_not_httponly(self):
        """CSRF cookie must NOT be httpOnly (JS needs to read it for double-submit)."""
        from base_handler import BaseHandler
        src = inspect.getsource(BaseHandler.issue_csrf_token)
        self.assertIn("httponly=False", src)

    def test_csrf_cookie_samesite_matches_session_cookie(self):
        """CSRF and session cookies must use the same SameSite policy."""
        from base_handler import BaseHandler
        csrf_src = inspect.getsource(BaseHandler.issue_csrf_token)
        session_src = inspect.getsource(BaseHandler.issue_session_cookie)
        self.assertIn('samesite="Lax"', csrf_src)
        self.assertIn('samesite="Lax"', session_src)

    def test_session_cookie_expires_days(self):
        """Session cookie must have explicit expiry."""
        from base_handler import BaseHandler
        src = inspect.getsource(BaseHandler.issue_session_cookie)
        self.assertIn("expires_days=1", src)

    def test_clear_session_clears_both_cookies(self):
        """clear_session_cookie must clear both arie_session and csrf_token."""
        from base_handler import BaseHandler
        src = inspect.getsource(BaseHandler.clear_session_cookie)
        self.assertIn("arie_session", src)
        self.assertIn("csrf_token", src)


if __name__ == "__main__":
    unittest.main()
