"""PR-13 (audit H8) — one canonical answer to "which environment am I in?".

Previously ENVIRONMENT=prod produced three different answers:
  - environment.py rejected it and fell back to "development" (stripping every
    production safety gate),
  - config.py kept the raw "prod" so IS_PRODUCTION/IS_STAGING were all False
    (validate_config silently skipped),
  - party_utils string-matched "prod" as production.
Both modules now canonicalize through environment.canonicalize_environment,
and missing DATABASE_URL in staging/production is a hard startup error (the
SQLite fallback would put regulated data on ephemeral container disk).
"""

import importlib
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# canonicalize_environment (pure function)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("prod", "production"),
        ("PROD", "production"),
        (" Production ", "production"),
        ("stage", "staging"),
        ("dev", "development"),
        ("test", "testing"),
        ("demo", "demo"),
        ("staging", "staging"),
        ("production", "production"),
        (None, "development"),
        ("", "development"),
        ("garbage-env", "development"),
    ],
)
def test_canonicalize_environment(raw, expected):
    from environment import canonicalize_environment

    assert canonicalize_environment(raw) == expected


def test_unknown_environment_logs_error(caplog):
    from environment import canonicalize_environment

    with caplog.at_level(logging.ERROR):
        assert canonicalize_environment("qa-lab-7") == "development"
    assert any("Invalid ENVIRONMENT" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# config.py and environment.py must agree for every input
# ---------------------------------------------------------------------------

def _reload_both(monkeypatch, raw):
    monkeypatch.setenv("ENVIRONMENT", raw)
    monkeypatch.delenv("ENV", raising=False)
    import environment as environment_module
    import config as config_module

    importlib.reload(environment_module)
    importlib.reload(config_module)
    return environment_module, config_module


@pytest.fixture()
def restore_modules():
    yield
    # Re-canonicalize against the real (test) environment for later tests.
    import environment as environment_module
    import config as config_module

    try:
        importlib.reload(environment_module)
        importlib.reload(config_module)
    except Exception as exc:  # teardown must not mask the test result
        logging.getLogger(__name__).warning(
            "module reload during teardown failed — later tests may see stale "
            "config/environment state: %s", exc
        )


@pytest.mark.parametrize("raw", ["prod", "PROD", "production", " staging ", "stage", "garbage"])
def test_config_and_environment_agree(monkeypatch, restore_modules, raw):
    environment_module, config_module = _reload_both(monkeypatch, raw)
    assert config_module.ENVIRONMENT == environment_module.ENV, (
        f"split-brain: config saw {config_module.ENVIRONMENT!r} but "
        f"environment saw {environment_module.ENV!r} for ENVIRONMENT={raw!r}"
    )
    # Never a non-canonical value on either side.
    assert config_module.ENVIRONMENT in environment_module.VALID_ENVIRONMENTS


def test_env_prod_is_production_everywhere(monkeypatch, restore_modules):
    """The H8 headline: ENVIRONMENT=prod must mean production, not development."""
    environment_module, config_module = _reload_both(monkeypatch, "prod")
    assert environment_module.ENV == "production"
    assert environment_module.is_production() is True
    assert config_module.ENVIRONMENT == "production"
    assert config_module.IS_PRODUCTION is True
    assert config_module.IS_DEVELOPMENT is False


# ---------------------------------------------------------------------------
# DATABASE_URL is required in staging/production
# ---------------------------------------------------------------------------

def test_validate_config_errors_without_database_url_in_staging(monkeypatch, restore_modules):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Satisfy the other hard requirements so the assertion isolates DATABASE_URL.
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("PII_ENCRYPTION_KEY", "y" * 44)

    import environment as environment_module
    import config as config_module

    importlib.reload(environment_module)
    importlib.reload(config_module)

    with pytest.raises(config_module.ConfigError) as excinfo:
        config_module.validate_config()
    assert "DATABASE_URL" in str(excinfo.value)


def test_validate_config_allows_missing_database_url_in_development(monkeypatch, restore_modules):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import environment as environment_module
    import config as config_module

    importlib.reload(environment_module)
    importlib.reload(config_module)
    config_module.validate_config()  # must not raise


def test_readiness_reports_missing_database_url(monkeypatch):
    """The operator surface mirrors the boot gate."""
    import server

    monkeypatch.setattr(server, "ENVIRONMENT", "staging")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PII_ENCRYPTION_KEY", "y" * 44)

    ready, payload = server._readiness_status_payload()
    config_check = payload["checks"]["config"]
    assert config_check["status"] == "failed"
    assert "DATABASE_URL" in config_check["missing"]
    assert ready is False
