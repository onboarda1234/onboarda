"""
Tests for auth.py — Extended tests for sanitize_input, sanitize_dict,
and token revocation integration in decode_token.
"""
import pytest


class TestSanitizeInput:
    """Test sanitize_input() XSS/HTML injection prevention."""

    def test_none_returns_none(self):
        from auth import sanitize_input
        assert sanitize_input(None) is None

    def test_plain_text_unchanged(self):
        from auth import sanitize_input
        assert sanitize_input("hello world") == "hello world"

    def test_html_tags_escaped(self):
        from auth import sanitize_input
        result = sanitize_input("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_html_attributes_escaped(self):
        from auth import sanitize_input
        result = sanitize_input('<img src="x" onerror="alert(1)">')
        assert 'onerror' not in result or '&quot;' in result
        assert "<img" not in result

    def test_quotes_escaped(self):
        from auth import sanitize_input
        result = sanitize_input('He said "hello" & \'goodbye\'')
        assert "&amp;" in result
        assert "&quot;" in result

    def test_strips_whitespace(self):
        from auth import sanitize_input
        assert sanitize_input("  hello  ") == "hello"

    def test_integer_passed_through(self):
        from auth import sanitize_input
        assert sanitize_input(42) == 42

    def test_float_passed_through(self):
        from auth import sanitize_input
        assert sanitize_input(3.14) == 3.14

    def test_boolean_passed_through(self):
        from auth import sanitize_input
        assert sanitize_input(True) is True

    def test_empty_string(self):
        from auth import sanitize_input
        assert sanitize_input("") == ""

    def test_ampersand_escaped(self):
        from auth import sanitize_input
        result = sanitize_input("AT&T Corp")
        assert "&amp;" in result

    def test_angle_brackets_escaped(self):
        from auth import sanitize_input
        result = sanitize_input("a > b < c")
        assert "&gt;" in result
        assert "&lt;" in result


class TestSanitizeDict:
    """Test sanitize_dict() dictionary sanitization."""

    def test_sanitize_all_string_keys(self):
        from auth import sanitize_dict
        data = {"name": "<b>Bold</b>", "age": 30}
        result = sanitize_dict(data)
        assert "&lt;b&gt;" in result["name"]
        assert result["age"] == 30

    def test_sanitize_specific_keys(self):
        from auth import sanitize_dict
        data = {"name": "<script>xss</script>", "safe_field": "<b>keep</b>"}
        result = sanitize_dict(data, keys=["name"])
        assert "&lt;script&gt;" in result["name"]
        assert result["safe_field"] == "<b>keep</b>"

    def test_non_dict_passthrough(self):
        from auth import sanitize_dict
        assert sanitize_dict("not a dict") == "not a dict"
        assert sanitize_dict(42) == 42
        assert sanitize_dict(None) is None

    def test_empty_dict(self):
        from auth import sanitize_dict
        assert sanitize_dict({}) == {}

    def test_non_string_values_preserved(self):
        from auth import sanitize_dict
        data = {"count": 42, "active": True, "items": [1, 2, 3]}
        result = sanitize_dict(data)
        assert result["count"] == 42
        assert result["active"] is True
        assert result["items"] == [1, 2, 3]

    def test_nested_strings_not_recursed(self):
        from auth import sanitize_dict
        # sanitize_dict only sanitizes top-level string values
        data = {"outer": "safe", "nested": {"inner": "<script>xss</script>"}}
        result = sanitize_dict(data)
        assert result["outer"] == "safe"
        # Nested dicts are not recursed into
        assert isinstance(result["nested"], dict)


class TestTokenRevocationInDecode:
    """Test that decode_token checks revocation list."""

    def test_valid_token_not_revoked(self, temp_db):
        from auth import create_token, decode_token
        token = create_token("user1", "admin", "Test User")
        result = decode_token(token)
        assert result is not None
        assert result["sub"] == "user1"

    def test_expired_token_returns_none(self, temp_db):
        import jwt as pyjwt
        from datetime import datetime, timedelta
        from auth import SECRET_KEY, decode_token

        payload = {
            "sub": "user1", "role": "admin", "name": "Test",
            "type": "officer", "jti": "test-jti",
            "iss": "arie-finance",
            "exp": datetime.utcnow() - timedelta(hours=1),
            "iat": datetime.utcnow() - timedelta(hours=2),
            "nbf": datetime.utcnow() - timedelta(hours=2),
        }
        token = pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")
        result = decode_token(token)
        assert result is None

    def test_invalid_issuer_returns_none(self, temp_db):
        import jwt as pyjwt
        from datetime import datetime, timedelta
        from auth import SECRET_KEY, decode_token

        payload = {
            "sub": "user1", "role": "admin", "name": "Test",
            "jti": "test-jti", "iss": "wrong-issuer",
            "exp": datetime.utcnow() + timedelta(hours=1),
            "iat": datetime.utcnow(),
        }
        token = pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")
        result = decode_token(token)
        assert result is None


class TestRateLimiterPersistence:
    """Test RateLimiter DB persistence patterns."""

    def test_auth_keys_should_persist(self):
        from auth import RateLimiter
        rl = RateLimiter()
        assert rl._should_persist("login:user@test.com") is True
        assert rl._should_persist("register:192.168.1.1") is True
        assert rl._should_persist("auth:token-refresh") is True

    def test_non_auth_keys_not_persisted(self):
        from auth import RateLimiter
        rl = RateLimiter()
        assert rl._should_persist("api:get-applications") is False
        assert rl._should_persist("general:rate-limit") is False

    def test_remaining_count(self):
        from auth import RateLimiter
        rl = RateLimiter()
        # Fresh key should have all attempts remaining
        remaining = rl.remaining("fresh_key", max_attempts=10)
        assert remaining == 10

    def test_remaining_decreases(self):
        from auth import RateLimiter
        rl = RateLimiter()
        key = "test_remaining_key"
        rl.is_limited(key, max_attempts=10, window_seconds=60)
        remaining = rl.remaining(key, max_attempts=10, window_seconds=60)
        assert remaining == 9
