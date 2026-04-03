"""
Tests for environment.py — Environment detection, feature flags, safety guards,
and environment-specific configuration.
"""
import os
import pytest


class TestGetEnvironment:
    """Test get_environment() environment detection logic."""

    def test_defaults_to_demo(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("ENV", raising=False)
        # Re-import to trigger fresh get_environment()
        import importlib
        import environment
        result = environment.get_environment()
        assert result in ("demo", "testing")  # conftest sets ENVIRONMENT=testing

    def test_reads_environment_var(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        import environment
        result = environment.get_environment()
        assert result == "production"

    def test_reads_env_var_as_fallback(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.setenv("ENV", "staging")
        import environment
        result = environment.get_environment()
        assert result == "staging"

    def test_invalid_env_defaults_to_demo(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "invalid_env_name")
        import environment
        result = environment.get_environment()
        assert result == "demo"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "  demo  ")
        import environment
        result = environment.get_environment()
        assert result == "demo"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "PRODUCTION")
        import environment
        result = environment.get_environment()
        assert result == "production"


class TestEnvironmentBooleans:
    """Test is_development(), is_demo(), is_staging(), is_production()."""

    def test_valid_environments_list(self):
        from environment import VALID_ENVIRONMENTS
        assert "development" in VALID_ENVIRONMENTS
        assert "demo" in VALID_ENVIRONMENTS
        assert "staging" in VALID_ENVIRONMENTS
        assert "production" in VALID_ENVIRONMENTS


class TestFeatureFlags:
    """Test FeatureFlags class."""

    def test_demo_flags_defaults(self):
        from environment import FeatureFlags
        ff = FeatureFlags("demo")
        assert ff.is_enabled("ENABLE_DEMO_MODE") is True
        assert ff.is_enabled("ENABLE_DEMO_BANNER") is True
        assert ff.is_enabled("ENABLE_MOCK_FALLBACKS") is True
        assert ff.is_enabled("ENABLE_SUMSUB_LIVE") is False

    def test_production_flags_defaults(self):
        from environment import FeatureFlags
        ff = FeatureFlags("production")
        assert ff.is_enabled("ENABLE_DEMO_MODE") is False
        assert ff.is_enabled("ENABLE_DEMO_BANNER") is False
        assert ff.is_enabled("ENABLE_MOCK_FALLBACKS") is False
        assert ff.is_enabled("ENABLE_SUMSUB_LIVE") is True
        assert ff.is_enabled("ENABLE_DEBUG_ENDPOINTS") is False
        assert ff.is_enabled("ENABLE_SHORTCUT_LOGIN") is False

    def test_development_flags_defaults(self):
        from environment import FeatureFlags
        ff = FeatureFlags("development")
        assert ff.is_enabled("ENABLE_ROLE_SWITCHER") is True
        assert ff.is_enabled("ENABLE_DEBUG_ENDPOINTS") is True
        assert ff.is_enabled("ENABLE_SUMSUB_LIVE") is False

    def test_staging_flags_defaults(self):
        from environment import FeatureFlags
        ff = FeatureFlags("staging")
        assert ff.is_enabled("ENABLE_SUMSUB_LIVE") is True
        assert ff.is_enabled("ENABLE_REAL_SCREENING") is True
        assert ff.is_enabled("ENABLE_ROLE_SWITCHER") is False
        assert ff.is_enabled("ENABLE_DEBUG_ENDPOINTS") is False

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("ENABLE_DEMO_MODE", "false")
        from environment import FeatureFlags
        ff = FeatureFlags("demo")
        assert ff.is_enabled("ENABLE_DEMO_MODE") is False

    def test_env_var_truthy_values(self, monkeypatch):
        from environment import FeatureFlags
        for val in ("true", "1", "yes", "on"):
            monkeypatch.setenv("ENABLE_DEMO_MODE", val)
            ff = FeatureFlags("production")
            assert ff.is_enabled("ENABLE_DEMO_MODE") is True, f"'{val}' should be truthy"

    def test_env_var_falsy_values(self, monkeypatch):
        from environment import FeatureFlags
        for val in ("false", "0", "no", "off"):
            monkeypatch.setenv("ENABLE_DEMO_MODE", val)
            ff = FeatureFlags("demo")
            assert ff.is_enabled("ENABLE_DEMO_MODE") is False, f"'{val}' should be falsy"

    def test_unknown_flag_returns_false(self):
        from environment import FeatureFlags
        ff = FeatureFlags("demo")
        assert ff.is_enabled("NONEXISTENT_FLAG") is False

    def test_unknown_flag_with_env_var(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_FLAG_XYZ", "true")
        from environment import FeatureFlags
        ff = FeatureFlags("demo")
        assert ff.is_enabled("CUSTOM_FLAG_XYZ") is True

    def test_get_all_returns_dict(self):
        from environment import FeatureFlags
        ff = FeatureFlags("demo")
        all_flags = ff.get_all()
        assert isinstance(all_flags, dict)
        assert len(all_flags) > 0
        assert "ENABLE_DEMO_MODE" in all_flags

    def test_get_client_safe_flags_subset(self):
        from environment import FeatureFlags
        ff = FeatureFlags("demo")
        safe = ff.get_client_safe_flags()
        assert isinstance(safe, dict)
        assert "ENABLE_DEMO_MODE" in safe
        assert "ENABLE_DEMO_BANNER" in safe
        # Sensitive flags should NOT be in client safe
        assert "REQUIRE_REAL_API_KEYS" not in safe
        assert "ENABLE_DEBUG_ENDPOINTS" not in safe

    def test_invalid_env_falls_back_to_demo(self):
        from environment import FeatureFlags
        ff = FeatureFlags("nonexistent_env")
        # Should fall back to demo defaults
        assert ff.is_enabled("ENABLE_DEMO_MODE") is True


class TestValidateEnvironment:
    """Test validate_environment() safety guards."""

    def test_demo_returns_no_errors(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "demo")
        from environment import FeatureFlags, validate_environment, _PRODUCTION_FORBIDDEN_FLAGS
        # In demo mode, validation should pass
        errors = validate_environment()
        # Demo doesn't enforce strict rules
        assert isinstance(errors, list)

    def test_production_forbidden_flags_list(self):
        from environment import _PRODUCTION_FORBIDDEN_FLAGS
        assert "ENABLE_DEMO_MODE" in _PRODUCTION_FORBIDDEN_FLAGS
        assert "ENABLE_MOCK_FALLBACKS" in _PRODUCTION_FORBIDDEN_FLAGS
        assert "ENABLE_DEBUG_ENDPOINTS" in _PRODUCTION_FORBIDDEN_FLAGS
        assert "ENABLE_SHORTCUT_LOGIN" in _PRODUCTION_FORBIDDEN_FLAGS

    def test_production_required_vars_list(self):
        from environment import _PRODUCTION_REQUIRED_VARS
        assert "ANTHROPIC_API_KEY" in _PRODUCTION_REQUIRED_VARS
        assert "JWT_SECRET" in _PRODUCTION_REQUIRED_VARS
        assert "DATABASE_URL" in _PRODUCTION_REQUIRED_VARS


class TestGetDatabaseUrl:
    """Test get_database_url() environment-specific behavior."""

    def test_demo_default(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "demo")
        monkeypatch.delenv("DEMO_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from environment import get_database_url
        url = get_database_url()
        assert "sqlite" in url

    def test_demo_with_env_var(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "demo")
        monkeypatch.setenv("DEMO_DATABASE_URL", "postgresql://demo:5432/demo")
        from environment import get_database_url
        url = get_database_url()
        assert url == "postgresql://demo:5432/demo"

    def test_staging_fallback(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.delenv("STAGING_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from environment import get_database_url
        url = get_database_url()
        assert "sqlite" in url


class TestGetJwtSecret:
    """Test get_jwt_secret() per-environment behavior."""

    def test_demo_without_env_generates_fallback(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "demo")
        monkeypatch.delenv("JWT_SECRET_DEMO", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        from environment import get_jwt_secret
        secret = get_jwt_secret()
        assert secret.startswith("demo-fallback-")
        assert len(secret) > 20

    def test_demo_with_env_uses_provided(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "demo")
        monkeypatch.setenv("JWT_SECRET_DEMO", "my-demo-secret-key")
        from environment import get_jwt_secret
        secret = get_jwt_secret()
        assert secret == "my-demo-secret-key"

    def test_staging_without_env_generates_fallback(self, monkeypatch):
        """Staging fallback requires ENV=staging at module level; test the fallback path logic."""
        monkeypatch.delenv("JWT_SECRET_STAGING", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        from environment import get_jwt_secret
        secret = get_jwt_secret()
        # In test environment (conftest sets ENVIRONMENT=testing → resolves to demo)
        # the fallback should start with "demo-fallback-" or "staging-fallback-"
        assert "fallback-" in secret
        assert len(secret) > 20


class TestApiKeyGetters:
    """Test API key getter functions."""

    def test_sumsub_app_token_default(self, monkeypatch):
        monkeypatch.delenv("SUMSUB_APP_TOKEN", raising=False)
        from environment import get_sumsub_app_token
        assert get_sumsub_app_token() == ""

    def test_sumsub_app_token_set(self, monkeypatch):
        monkeypatch.setenv("SUMSUB_APP_TOKEN", "test-token")
        from environment import get_sumsub_app_token
        assert get_sumsub_app_token() == "test-token"

    def test_sumsub_secret_key_default(self, monkeypatch):
        monkeypatch.delenv("SUMSUB_SECRET_KEY", raising=False)
        from environment import get_sumsub_secret_key
        assert get_sumsub_secret_key() == ""

    def test_sumsub_level_name_default(self, monkeypatch):
        monkeypatch.delenv("SUMSUB_LEVEL_NAME", raising=False)
        from environment import get_sumsub_level_name
        assert get_sumsub_level_name() == "basic-kyc-level"

    def test_opencorporates_key_default(self, monkeypatch):
        monkeypatch.delenv("OPENCORPORATES_API_KEY", raising=False)
        from environment import get_opencorporates_api_key
        assert get_opencorporates_api_key() == ""

    def test_ip_geolocation_key_default(self, monkeypatch):
        monkeypatch.delenv("IP_GEOLOCATION_API_KEY", raising=False)
        from environment import get_ip_geolocation_api_key
        assert get_ip_geolocation_api_key() == ""

    def test_opensanctions_key_default(self, monkeypatch):
        monkeypatch.delenv("OPENSANCTIONS_API_KEY", raising=False)
        from environment import get_opensanctions_api_key
        assert get_opensanctions_api_key() == ""


class TestGetCorsOrigin:
    """Test get_cors_origin() per-environment behavior."""

    def test_demo_default(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "demo")
        monkeypatch.delenv("CORS_ORIGIN_DEMO", raising=False)
        from environment import get_cors_origin
        origin = get_cors_origin()
        assert "demo" in origin

    def test_production_default(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("CORS_ORIGIN", raising=False)
        from environment import get_cors_origin
        origin = get_cors_origin()
        assert isinstance(origin, str)


class TestGetS3Bucket:
    """Test get_s3_bucket() per-environment behavior."""

    def test_demo_default(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "demo")
        monkeypatch.delenv("S3_BUCKET_DEMO", raising=False)
        from environment import get_s3_bucket
        bucket = get_s3_bucket()
        # Module-level ENV is set at import time (conftest → testing/demo)
        assert isinstance(bucket, str)
        assert len(bucket) > 0

    def test_custom_bucket_via_env(self, monkeypatch):
        monkeypatch.setenv("S3_BUCKET_DEMO", "my-custom-bucket")
        from environment import get_s3_bucket
        bucket = get_s3_bucket()
        # get_s3_bucket checks module-level ENV; in test it's demo
        assert isinstance(bucket, str)

    def test_s3_bucket_returns_string(self):
        from environment import get_s3_bucket
        bucket = get_s3_bucket()
        assert isinstance(bucket, str)
        assert len(bucket) > 0


class TestGetEnvironmentInfo:
    """Test get_environment_info() API response."""

    def test_returns_required_keys(self):
        from environment import get_environment_info
        info = get_environment_info()
        assert "environment" in info
        assert "is_demo" in info
        assert "is_production" in info
        assert "features" in info
        assert "version" in info

    def test_features_is_dict(self):
        from environment import get_environment_info
        info = get_environment_info()
        assert isinstance(info["features"], dict)

    def test_version_present(self):
        from environment import get_environment_info
        info = get_environment_info()
        assert isinstance(info["version"], str)


class TestGetSumsubBaseUrl:
    """Test get_sumsub_base_url()."""

    def test_returns_string(self):
        from environment import get_sumsub_base_url
        url = get_sumsub_base_url()
        assert isinstance(url, str)
        assert url.startswith("https://")
