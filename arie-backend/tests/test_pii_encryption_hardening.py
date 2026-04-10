"""
Tests for PII encryption security hardening:
- Startup fails when PII_ENCRYPTION_KEY is missing in staging/production
- Startup fails when key is malformed
- Startup succeeds with valid key
- No random fallback in staging
- Boot-time self-test
- Readiness endpoint fails when encryption init failed
- Local/dev behavior allows auto-generation
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# PIIEncryptor unit tests
# ═══════════════════════════════════════════════════════════════

class TestPIIEncryptorInit:
    """Test PIIEncryptor initialization with various key states."""

    def test_valid_key_succeeds(self):
        """PIIEncryptor initializes successfully with a valid Fernet key."""
        from cryptography.fernet import Fernet
        from security_hardening import PIIEncryptor
        key = Fernet.generate_key().decode()
        enc = PIIEncryptor(key)
        assert enc.cipher is not None

    def test_missing_key_raises_runtime_error(self, monkeypatch):
        """PIIEncryptor raises RuntimeError when no key is provided and env is empty."""
        monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
        from security_hardening import PIIEncryptor
        with pytest.raises(RuntimeError, match="PII_ENCRYPTION_KEY"):
            PIIEncryptor()

    def test_malformed_key_raises_value_error(self):
        """PIIEncryptor raises ValueError when key is not valid Fernet format."""
        from security_hardening import PIIEncryptor
        with pytest.raises(ValueError, match="Invalid PII_ENCRYPTION_KEY format|Fernet key"):
            PIIEncryptor("not-a-valid-fernet-key-at-all!")

    def test_short_key_raises_value_error(self):
        """PIIEncryptor rejects a key that decodes to fewer than 32 bytes."""
        import base64
        from security_hardening import PIIEncryptor
        short_key = base64.urlsafe_b64encode(b"tooshort").decode()
        with pytest.raises(ValueError, match="32 bytes"):
            PIIEncryptor(short_key)

    def test_valid_key_encrypt_decrypt_roundtrip(self):
        """Encrypt then decrypt returns original plaintext."""
        from cryptography.fernet import Fernet
        from security_hardening import PIIEncryptor
        key = Fernet.generate_key().decode()
        enc = PIIEncryptor(key)
        plaintext = "John Doe Passport P12345678"
        ciphertext = enc.encrypt(plaintext)
        assert ciphertext != plaintext
        assert enc.decrypt(ciphertext) == plaintext

    def test_key_from_env_var(self, monkeypatch):
        """PIIEncryptor reads PII_ENCRYPTION_KEY from environment."""
        from cryptography.fernet import Fernet
        from security_hardening import PIIEncryptor
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("PII_ENCRYPTION_KEY", key)
        enc = PIIEncryptor()
        assert enc.cipher is not None


# ═══════════════════════════════════════════════════════════════
# Config validation tests (PII_ENCRYPTION_KEY in staging/prod)
# ═══════════════════════════════════════════════════════════════

class TestConfigValidationPII:
    """Test that config.validate_config() enforces PII_ENCRYPTION_KEY in staging and production."""

    def test_staging_requires_pii_key(self, monkeypatch):
        """validate_config raises ConfigError when PII_ENCRYPTION_KEY is missing in staging."""
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("JWT_SECRET", "test-staging-jwt-secret-key-1234567890abcdef")
        monkeypatch.setenv("SECRET_KEY", "test-staging-secret-key-1234567890abcdef")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
        import importlib
        import config as cfg
        importlib.reload(cfg)
        try:
            with pytest.raises(cfg.ConfigError, match="PII_ENCRYPTION_KEY"):
                cfg.validate_config()
        finally:
            os.environ["ENVIRONMENT"] = "testing"
            importlib.reload(cfg)

    def test_production_requires_pii_key(self, monkeypatch):
        """validate_config raises ConfigError when PII_ENCRYPTION_KEY is missing in production."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("JWT_SECRET", "test-prod-jwt-secret-key-1234567890abcdefgh")
        monkeypatch.setenv("SECRET_KEY", "test-prod-secret-key-1234567890abcdefgh")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("S3_BUCKET", "prod-bucket")
        monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
        import importlib
        import config as cfg
        importlib.reload(cfg)
        try:
            with pytest.raises(cfg.ConfigError, match="PII_ENCRYPTION_KEY"):
                cfg.validate_config()
        finally:
            os.environ["ENVIRONMENT"] = "testing"
            importlib.reload(cfg)

    def test_staging_passes_with_valid_key(self, monkeypatch):
        """validate_config succeeds when PII_ENCRYPTION_KEY is set in staging."""
        from cryptography.fernet import Fernet
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("JWT_SECRET", "test-staging-jwt-secret-key-1234567890abcdef")
        monkeypatch.setenv("SECRET_KEY", "test-staging-secret-key-1234567890abcdef")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
        import importlib
        import config as cfg
        importlib.reload(cfg)
        try:
            result = cfg.validate_config()
            assert result is True
        finally:
            os.environ["ENVIRONMENT"] = "testing"
            importlib.reload(cfg)

    def test_dev_does_not_require_pii_key(self, monkeypatch):
        """validate_config succeeds in development even without PII_ENCRYPTION_KEY."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
        import importlib
        import config as cfg
        importlib.reload(cfg)
        try:
            result = cfg.validate_config()
            assert result is True
        finally:
            os.environ["ENVIRONMENT"] = "testing"
            importlib.reload(cfg)

    def test_testing_does_not_require_pii_key(self, monkeypatch):
        """validate_config succeeds in testing even without PII_ENCRYPTION_KEY."""
        monkeypatch.setenv("ENVIRONMENT", "testing")
        monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
        import importlib
        import config as cfg
        importlib.reload(cfg)
        result = cfg.validate_config()
        assert result is True


# ═══════════════════════════════════════════════════════════════
# Environment validation tests (PII_ENCRYPTION_KEY in staging)
# ═══════════════════════════════════════════════════════════════

class TestEnvironmentValidationPII:
    """Test that validate_environment checks PII_ENCRYPTION_KEY in staging."""

    def test_staging_validation_errors_without_pii_key(self, monkeypatch):
        """validate_environment returns error when PII_ENCRYPTION_KEY is missing in staging."""
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
        import importlib
        import environment as env_mod
        importlib.reload(env_mod)
        try:
            errors = env_mod.validate_environment()
            pii_errors = [e for e in errors if "PII_ENCRYPTION_KEY" in e]
            assert len(pii_errors) > 0, f"Expected PII_ENCRYPTION_KEY error in staging, got: {errors}"
        finally:
            os.environ["ENVIRONMENT"] = "testing"
            importlib.reload(env_mod)

    def test_staging_validation_passes_with_pii_key(self, monkeypatch):
        """validate_environment returns no PII errors when PII_ENCRYPTION_KEY is set in staging."""
        from cryptography.fernet import Fernet
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
        import importlib
        import environment as env_mod
        importlib.reload(env_mod)
        try:
            errors = env_mod.validate_environment()
            pii_errors = [e for e in errors if "PII_ENCRYPTION_KEY" in e]
            assert len(pii_errors) == 0, f"Unexpected PII_ENCRYPTION_KEY error: {pii_errors}"
        finally:
            os.environ["ENVIRONMENT"] = "testing"
            importlib.reload(env_mod)


# ═══════════════════════════════════════════════════════════════
# Environment default safety
# ═══════════════════════════════════════════════════════════════

class TestEnvironmentDefault:
    """Ensure invalid/missing ENVIRONMENT defaults to 'development', not 'demo'."""

    def test_missing_env_defaults_to_development(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("ENV", raising=False)
        from environment import get_environment
        result = get_environment()
        assert result == "development"

    def test_unknown_env_defaults_to_development(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "prod-staging-hybrid")
        from environment import get_environment
        result = get_environment()
        assert result == "development"

    def test_accepted_environments_are_explicit(self):
        from environment import VALID_ENVIRONMENTS
        assert set(VALID_ENVIRONMENTS) == {"development", "testing", "demo", "staging", "production"}


# ═══════════════════════════════════════════════════════════════
# No random fallback in staging
# ═══════════════════════════════════════════════════════════════

class TestNoRandomFallbackStaging:
    """Ensure staging never auto-generates a PII encryption key."""

    def test_pii_encryptor_raises_when_missing_key(self, monkeypatch):
        """PIIEncryptor raises RuntimeError regardless of environment when key is missing."""
        monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
        from security_hardening import PIIEncryptor
        with pytest.raises(RuntimeError, match="PII_ENCRYPTION_KEY"):
            PIIEncryptor()

    def test_server_pii_init_would_exit_in_staging(self, monkeypatch):
        """
        In staging, server.py's PII init block should sys.exit(1) when key is missing.
        We verify by checking that ENVIRONMENT=staging is in the fatal exit path.
        """
        # This is a design-level test: confirm the code checks for staging in the exit path
        import ast
        server_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py")
        with open(server_path) as f:
            source = f.read()
        # The critical guard: staging must be in the same branch as sys.exit(1)
        assert '"staging"' in source or "'staging'" in source
        # Verify the exit path checks for staging
        assert 'ENVIRONMENT in ("production", "prod", "staging")' in source


# ═══════════════════════════════════════════════════════════════
# Boot-time self-test
# ═══════════════════════════════════════════════════════════════

class TestBootTimeSelfTest:
    """Verify boot-time encryption self-test logic."""

    def test_encrypt_decrypt_roundtrip(self):
        """The self-test pattern: encrypt canary → decrypt → compare."""
        from cryptography.fernet import Fernet
        from security_hardening import PIIEncryptor
        key = Fernet.generate_key().decode()
        enc = PIIEncryptor(key)
        canary = "selftest-canary-abc123"
        encrypted = enc.encrypt(canary)
        decrypted = enc.decrypt(encrypted)
        assert decrypted == canary, "Self-test mismatch: decrypt did not return original canary"

    def test_selftest_code_exists_in_server(self):
        """Verify server.py contains the self-test pattern."""
        server_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py")
        with open(server_path) as f:
            source = f.read()
        assert "pii-selftest-canary-" in source
        assert "_encrypted_canary" in source
        assert "_decrypted_canary" in source
        assert "self-test FAILED" in source or "self-test passed" in source

    def test_selftest_exits_on_failure_in_staging(self):
        """Verify the self-test failure path calls sys.exit for staging."""
        server_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py")
        with open(server_path) as f:
            source = f.read()
        # After "self-test FAILED" there should be a staging check and sys.exit
        idx_fail = source.find("self-test FAILED")
        assert idx_fail > 0
        subsequent = source[idx_fail:idx_fail + 300]
        assert "staging" in subsequent
        assert "sys.exit" in subsequent


# ═══════════════════════════════════════════════════════════════
# Readiness endpoint
# ═══════════════════════════════════════════════════════════════

class TestReadinessEndpoint:
    """Test the /api/readiness endpoint."""

    def test_readiness_route_registered(self, app):
        """Verify /api/readiness is in the server routes."""
        import server
        # Check by inspecting the source pattern in make_app
        import inspect
        source = inspect.getsource(server.make_app)
        assert "/api/readiness" in source, "No /api/readiness route found in make_app()"

    def test_readiness_handler_exists(self):
        """ReadinessHandler class exists in server module."""
        from server import ReadinessHandler
        assert ReadinessHandler is not None
        assert hasattr(ReadinessHandler, "get")

    def test_readiness_checks_encryption(self):
        """ReadinessHandler should check _pii_encryption_ok."""
        import server
        # In testing, _pii_encryption_ok should be True (auto key generated)
        assert hasattr(server, "_pii_encryption_ok")
        assert server._pii_encryption_ok is True

    def test_readiness_fails_when_encryption_broken(self, monkeypatch):
        """When _pii_encryption_ok is False, readiness should report failure."""
        import server
        original = server._pii_encryption_ok
        try:
            server._pii_encryption_ok = False
            # Simulate calling the handler logic
            handler = server.ReadinessHandler(
                server.make_app(),
                server.tornado.httputil.HTTPServerRequest(
                    method="GET",
                    uri="/api/readiness",
                    connection=type("FakeConn", (), {
                        "no_keep_alive": False,
                        "set_close_callback": lambda self, cb: None,
                        "finish": lambda self: None,
                        "write_headers": lambda self, *a, **kw: None,
                        "write": lambda self, data, callback=None: callback() if callback else None,
                    })()
                )
            )
            handler._transforms = []
            handler.get()
            # Should have set 503
            assert handler._status_code == 503
        finally:
            server._pii_encryption_ok = original


# ═══════════════════════════════════════════════════════════════
# Deployment workflow determinism
# ═══════════════════════════════════════════════════════════════

class TestDeployWorkflowDeterminism:
    """Verify GitHub workflow uses deterministic image tagging."""

    def test_deploy_staging_uses_git_sha_tag(self):
        """deploy-staging.yml must tag Docker images with git SHA, not just :latest."""
        workflow_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            ".github", "workflows", "deploy-staging.yml"
        )
        with open(workflow_path) as f:
            content = f.read()
        assert "github.sha" in content, "Workflow must use github.sha for deterministic tagging"
        assert "IMAGE_TAG" in content

    def test_deploy_staging_updates_task_definition(self):
        """deploy-staging.yml must update ECS task definition with specific image."""
        workflow_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            ".github", "workflows", "deploy-staging.yml"
        )
        with open(workflow_path) as f:
            content = f.read()
        # Should register new task definition, not hardcode a revision number
        assert "register-task-definition" in content or "task-definition" in content


# ═══════════════════════════════════════════════════════════════
# Secrets not hardcoded in version control
# ═══════════════════════════════════════════════════════════════

class TestNoHardcodedSecrets:
    """Ensure render.yaml does not contain hardcoded passwords."""

    def test_render_yaml_no_hardcoded_demo_passwords(self):
        """render.yaml must not contain plaintext password values."""
        render_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "render.yaml"
        )
        with open(render_path) as f:
            content = f.read()
        # Should not contain actual password values
        assert "Arie2026!" not in content, "render.yaml contains hardcoded DEMO_PORTAL_PASSWORD"
        assert "Onboarda2026!" not in content, "render.yaml contains hardcoded DEMO_BACKOFFICE_PASSWORD"
        assert "DemoPass2026!" not in content, "render.yaml contains hardcoded DEMO_CLIENT_PASSWORD"

    def test_render_yaml_uses_sync_false_for_secrets(self):
        """Passwords in render.yaml should use sync: false (managed in dashboard)."""
        render_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "render.yaml"
        )
        with open(render_path) as f:
            content = f.read()
        # All secret keys should use sync: false pattern
        assert "sync: false" in content
