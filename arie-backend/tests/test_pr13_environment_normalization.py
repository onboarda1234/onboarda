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
    # Snapshot the ambient env vars OURSELVES: pytest finalizes fixtures in
    # reverse setup order, so this teardown runs BEFORE monkeypatch undoes
    # its setenv calls — reloading without restoring first would re-bake the
    # monkeypatched value into config/environment for the rest of the session
    # (adversarial-review finding, empirically proven to leak).
    orig = {var: os.environ.get(var) for var in ("ENVIRONMENT", "ENV")}
    yield
    for var, value in orig.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value

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


def test_readiness_reports_missing_database_url(monkeypatch, temp_db):
    """The operator surface mirrors the boot gate. (temp_db keeps the payload's
    DB-connectivity check off the repo-dir default SQLite file.)"""
    import server

    monkeypatch.setattr(server, "ENVIRONMENT", "staging")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PII_ENCRYPTION_KEY", "y" * 44)

    ready, payload = server._readiness_status_payload()
    config_check = payload["checks"]["config"]
    assert config_check["status"] == "failed"
    assert "DATABASE_URL" in config_check["missing"]
    assert ready is False


# ---------------------------------------------------------------------------
# Set-but-empty ENVIRONMENT must fall through to ENV on BOTH sides
# (adversarial-review finding: getenv-default vs `or`-chain divergence)
# ---------------------------------------------------------------------------

def test_empty_environment_falls_through_to_env_on_both_sides(monkeypatch, restore_modules):
    """render.yaml services set ENV; an IaC layer adding an EMPTY ENVIRONMENT
    previously re-split the brain (config→development, environment→ENV)."""
    monkeypatch.setenv("ENVIRONMENT", "")
    monkeypatch.setenv("ENV", "staging")

    import environment as environment_module
    import config as config_module

    importlib.reload(environment_module)
    importlib.reload(config_module)

    assert environment_module.ENV == "staging"
    assert config_module.ENVIRONMENT == "staging", (
        "set-but-empty ENVIRONMENT shadowed ENV in config.py — split-brain"
    )
    assert config_module.IS_STAGING is True


# ---------------------------------------------------------------------------
# Raw os.environ readers must agree with the canonical value
# ---------------------------------------------------------------------------

def test_webhook_signature_status_fail_closed_under_prod_alias(monkeypatch):
    """The nastiest drift: raw 'prod' used to yield disabled_non_production —
    fail-OPEN webhook signatures on a box booting with production gates."""
    from screening_complyadvantage import webhook_handler as wh

    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("COMPLYADVANTAGE_WEBHOOK_SECRET", raising=False)

    status = wh._signature_status(b"{}", {})
    assert status == "deployed_secret_missing", (
        f"ENVIRONMENT=prod with no webhook secret must fail CLOSED "
        f"(deployed_secret_missing), got {status!r}"
    )


def test_monitoring_automation_enabled_for_stage_alias(monkeypatch):
    """'stage' canonicalizes to staging everywhere — the scheduler must not
    silently stay off for it (and 'prod' must count as production)."""
    import monitoring_automation as ma

    monkeypatch.delenv("MONITORING_AUTOMATION_ENABLED", raising=False)
    monkeypatch.delenv("ENV", raising=False)

    monkeypatch.setenv("ENVIRONMENT", "stage")
    assert ma.automation_enabled() is True
    monkeypatch.setenv("ENVIRONMENT", "prod")
    assert ma.automation_enabled() is True
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert ma.automation_enabled() is False


def test_screening_config_defaults_resolve_for_aliases(monkeypatch):
    import screening_config as sc

    monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
    monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
    monkeypatch.delenv("ENV", raising=False)

    monkeypatch.setenv("ENVIRONMENT", "prod")
    # 'prod' must hit the canonical production defaults, not the fallbacks.
    assert sc.get_active_provider_name() == sc._PROVIDER_DEFAULTS["production"]
    assert sc.is_abstraction_enabled() is sc._ABSTRACTION_DEFAULTS["production"]
