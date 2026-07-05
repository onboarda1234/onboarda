"""B6-B5 — AML screening readiness truthfulness + provider provenance honesty.

Audit B6: deployment readiness must distinguish MISCONFIGURATION (permanent
readiness failure — visible red, deliberately not a boot crash, no restart
loop) from TRANSIENT provider unreachability (degraded, stays up, self-heals).
Audit B5: the platform must never present sandbox/unknown-mode screening as
live production screening — "unknown evidence must stay unknown".

Override policy under test: there is NO override of any kind that can turn
aml_screening green. Provider truth is the only input.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CA_ENV_KEYS = (
    "COMPLYADVANTAGE_API_BASE_URL",
    "COMPLYADVANTAGE_AUTH_URL",
    "COMPLYADVANTAGE_REALM",
    "COMPLYADVANTAGE_USERNAME",
    "COMPLYADVANTAGE_PASSWORD",
    "COMPLYADVANTAGE_SCREENING_CONFIG_ID",
    "COMPLYADVANTAGE_WORKSPACE_MODE",
    "COMPLYADVANTAGE_WORKSPACE_LABEL",
    "COMPLYADVANTAGE_SCREENING_CONFIG_LABEL",
    "COMPLYADVANTAGE_WEBHOOK_SECRET",
)


def _clear_ca_env(monkeypatch):
    for key in CA_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
    monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)


def _set_valid_ca_env(monkeypatch, mode="production"):
    monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")
    monkeypatch.setenv("COMPLYADVANTAGE_API_BASE_URL", "https://api.mesh.example.com")
    monkeypatch.setenv("COMPLYADVANTAGE_AUTH_URL", "https://auth.mesh.example.com")
    monkeypatch.setenv("COMPLYADVANTAGE_REALM", "regmind")
    monkeypatch.setenv("COMPLYADVANTAGE_USERNAME", "svc-user")
    monkeypatch.setenv("COMPLYADVANTAGE_PASSWORD", "svc-pass")
    monkeypatch.setenv("COMPLYADVANTAGE_SCREENING_CONFIG_ID", "cfg-123")
    if mode is not None:
        monkeypatch.setenv("COMPLYADVANTAGE_WORKSPACE_MODE", mode)


def _env(monkeypatch, name):
    import server

    monkeypatch.setattr(server, "ENVIRONMENT", name)
    monkeypatch.setattr(server, "ENV", name)
    # Readiness requires deployed config to isolate the AML assertions.
    monkeypatch.setenv("PII_ENCRYPTION_KEY", "y" * 44)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example:5432/x")
    return server


def _aml(payload):
    return payload["checks"]["aml_screening"]


# ---------------------------------------------------------------------------
# Acceptance test 1 — production + missing CA config => loud misconfiguration
# ---------------------------------------------------------------------------

def test_production_missing_ca_config_is_misconfigured_not_ready(monkeypatch, temp_db):
    server = _env(monkeypatch, "production")
    _clear_ca_env(monkeypatch)

    ready, payload = server._readiness_status_payload()
    aml = _aml(payload)
    assert aml["status"] == "misconfigured"
    assert aml["active"] is False
    assert ready is False


# ---------------------------------------------------------------------------
# Acceptance test 2 — production + garbage CA config => loud misconfiguration
# ---------------------------------------------------------------------------

def test_production_garbage_ca_config_is_misconfigured_not_ready(monkeypatch, temp_db):
    server = _env(monkeypatch, "production")
    _clear_ca_env(monkeypatch)
    _set_valid_ca_env(monkeypatch, mode="production")
    monkeypatch.setenv("COMPLYADVANTAGE_REALM", "wrong-realm")  # garbage

    ready, payload = server._readiness_status_payload()
    aml = _aml(payload)
    assert aml["status"] == "misconfigured"
    assert "realm" in (aml.get("detail") or "").lower()
    assert ready is False


# ---------------------------------------------------------------------------
# Acceptance test 3 — production + valid config + provider 503/timeout =>
# degraded (unreachable), process stays up, no external call unless probed
# ---------------------------------------------------------------------------

def test_production_unreachable_provider_degrades_but_stays_up(monkeypatch, temp_db):
    server = _env(monkeypatch, "production")
    _clear_ca_env(monkeypatch)
    _set_valid_ca_env(monkeypatch, mode="production")

    from screening_complyadvantage.auth import ComplyAdvantageTokenClient

    def _boom(self):
        raise ConnectionError("simulated CA outage: 503")

    monkeypatch.setattr(ComplyAdvantageTokenClient, "force_refresh", _boom)

    # Probed readiness: transient outage => unreachable, not ready — but the
    # payload function completes normally (no crash, liveness unaffected).
    ready, payload = server._readiness_status_payload(probe_aml=True)
    aml = _aml(payload)
    assert aml["status"] == "unreachable"
    assert ready is False

    # Ordinary readiness polls make NO external call: with the token client
    # poisoned, an unprobed poll must still complete and report config truth.
    _ready2, payload2 = server._readiness_status_payload(probe_aml=False)
    assert _aml(payload2)["status"] == "ok"


# ---------------------------------------------------------------------------
# Acceptance test 4 — staging + CA sandbox active => explicitly sandbox
# ---------------------------------------------------------------------------

def test_staging_sandbox_reports_sandbox_never_live(monkeypatch, temp_db):
    server = _env(monkeypatch, "staging")
    _clear_ca_env(monkeypatch)
    _set_valid_ca_env(monkeypatch, mode="sandbox")

    ready, payload = server._readiness_status_payload()
    aml = _aml(payload)
    assert aml["status"] == "sandbox"
    assert aml["mode"] == "sandbox"
    # Staging's expected posture: sandbox is explicit but non-gating there.
    assert ready is True

    ca = server._complyadvantage_runtime_status()
    assert ca["mode"] == "sandbox"


# ---------------------------------------------------------------------------
# Acceptance test 5 — no configuration path yields ok without an
# authoritative production AML provider
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", ["no_config", "garbage", "sandbox_mode", "unknown_mode", "abstraction_off"])
def test_production_never_ok_without_authoritative_provider(monkeypatch, temp_db, scenario):
    server = _env(monkeypatch, "production")
    _clear_ca_env(monkeypatch)

    if scenario == "garbage":
        _set_valid_ca_env(monkeypatch, mode="production")
        monkeypatch.setenv("COMPLYADVANTAGE_PASSWORD", "")
    elif scenario == "sandbox_mode":
        _set_valid_ca_env(monkeypatch, mode="sandbox")
    elif scenario == "unknown_mode":
        _set_valid_ca_env(monkeypatch, mode=None)  # WORKSPACE_MODE unset
    elif scenario == "abstraction_off":
        _set_valid_ca_env(monkeypatch, mode="production")
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")

    ready, payload = server._readiness_status_payload()
    aml = _aml(payload)
    assert aml["status"] != "ok", f"{scenario}: production reported AML-ready"
    assert ready is False, f"{scenario}: overall readiness passed"


# ---------------------------------------------------------------------------
# Acceptance test 6 — no override can green a failing AML check
# ---------------------------------------------------------------------------

def test_no_env_override_can_green_aml_readiness(monkeypatch, temp_db):
    server = _env(monkeypatch, "production")
    _clear_ca_env(monkeypatch)

    for candidate in (
        "AML_READINESS_OVERRIDE",
        "SCREENING_READINESS_OVERRIDE",
        "FORCE_AML_READY",
        "READINESS_OVERRIDE",
        "PILOT_MODE",
        "CONTROLLED_PILOT_OVERRIDE",
        "SKIP_SCREENING_READINESS",
    ):
        monkeypatch.setenv(candidate, "true")

    ready, payload = server._readiness_status_payload()
    aml = _aml(payload)
    assert aml["status"] == "misconfigured"
    assert ready is False, "an override env var turned AML readiness green"


# ---------------------------------------------------------------------------
# B5 regression — unknown workspace mode must stay unknown (previously the
# mode defaulted to "production" whenever ENV was production)
# ---------------------------------------------------------------------------

def test_unknown_workspace_mode_is_never_inferred_from_environment(monkeypatch, temp_db):
    server = _env(monkeypatch, "production")
    _clear_ca_env(monkeypatch)
    _set_valid_ca_env(monkeypatch, mode=None)  # WORKSPACE_MODE unset

    ca = server._complyadvantage_runtime_status()
    assert ca["mode"] == "unknown", (
        "workspace mode was inferred from ENVIRONMENT — unknown provider "
        "evidence must remain unknown (audit B5)"
    )
    assert ca["mode_source"] == "unknown"

    ready, payload = server._readiness_status_payload()
    aml = _aml(payload)
    assert aml["status"] == "mode_unverified"
    assert ready is False


# ---------------------------------------------------------------------------
# Retention-policy gating (upgraded from PR-31's observability-only probe)
# ---------------------------------------------------------------------------

def test_empty_retention_table_fails_readiness_in_deployed_envs(monkeypatch, temp_db):
    server = _env(monkeypatch, "staging")
    _clear_ca_env(monkeypatch)
    _set_valid_ca_env(monkeypatch, mode="sandbox")

    from db import get_db
    import db as db_module

    db = get_db()
    try:
        db.execute("DELETE FROM data_retention_policies")
        db.commit()
    finally:
        db.close()

    # try/finally: an assertion failure must not leak an empty retention
    # table into later tests sharing the session temp_db (review finding).
    try:
        ready, payload = server._readiness_status_payload()
        rp = payload["checks"]["retention_policies"]
        assert rp["status"] == "empty"
        assert ready is False, "empty retention-policy table must fail deployed readiness"
    finally:
        db = get_db()
        try:
            db_module._ensure_retention_policies(db)
        finally:
            db.close()

    ready_after, payload_after = server._readiness_status_payload()
    assert payload_after["checks"]["retention_policies"]["status"] == "ok"
    assert ready_after is True


def test_empty_retention_table_stays_nongating_outside_deployed_envs(temp_db):
    """PR-31's testing-environment behavior is unchanged (its test still holds)."""
    import server
    from db import get_db
    import db as db_module

    db = get_db()
    try:
        db.execute("DELETE FROM data_retention_policies")
        db.commit()
    finally:
        db.close()

    try:
        ready_empty, payload = server._readiness_status_payload()
        assert payload["checks"]["retention_policies"]["status"] == "empty"
    finally:
        db = get_db()
        try:
            db_module._ensure_retention_policies(db)
        finally:
            db.close()

    ready_ok, _ = server._readiness_status_payload()
    assert ready_empty == ready_ok


# ---------------------------------------------------------------------------
# Webhook signature posture surfacing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "env_name,secret,expected",
    [
        ("staging", None, "deployed_fail_closed_missing_secret"),
        ("production", None, "deployed_fail_closed_missing_secret"),
        ("staging", "s3cret", "strict"),
        ("development", None, "sandbox_fail_open_signature_disabled"),
    ],
)
def test_webhook_signature_mode_is_surfaced(monkeypatch, temp_db, env_name, secret, expected):
    server = _env(monkeypatch, env_name)
    # The posture classifier lives in webhook_handler and reads the real
    # ENVIRONMENT env var (the enforcer's own detection) — set it too.
    monkeypatch.setenv("ENVIRONMENT", env_name)
    _clear_ca_env(monkeypatch)
    if secret:
        monkeypatch.setenv("COMPLYADVANTAGE_WEBHOOK_SECRET", secret)

    assert server._ca_webhook_signature_mode() == expected

    _set_valid_ca_env(monkeypatch, mode="sandbox")
    if secret:
        monkeypatch.setenv("COMPLYADVANTAGE_WEBHOOK_SECRET", secret)
    _, payload = server._readiness_status_payload()
    assert _aml(payload)["webhook_signature_mode"] == expected


# ---------------------------------------------------------------------------
# Review-hardening coverage: probed-success, exception path, mode casing
# ---------------------------------------------------------------------------

def test_production_valid_and_probed_reachable_is_ok(monkeypatch, temp_db):
    server = _env(monkeypatch, "production")
    _clear_ca_env(monkeypatch)
    _set_valid_ca_env(monkeypatch, mode="production")

    from screening_complyadvantage.auth import ComplyAdvantageTokenClient

    monkeypatch.setattr(ComplyAdvantageTokenClient, "force_refresh", lambda self: None)

    ready, payload = server._readiness_status_payload(probe_aml=True)
    aml = _aml(payload)
    assert aml["status"] == "ok"
    assert aml["mode_source"] == "attested_env"
    assert ready is True


def test_aml_check_exception_fails_closed_in_production(monkeypatch, temp_db):
    server = _env(monkeypatch, "production")
    _clear_ca_env(monkeypatch)

    def _boom(probe_auth=False):
        raise RuntimeError("status computation exploded")

    monkeypatch.setattr(server, "_complyadvantage_runtime_status", _boom)

    ready, payload = server._readiness_status_payload()
    aml = _aml(payload)
    assert aml["status"] == "unknown"
    assert ready is False, "an aml-check crash must fail closed in production"


def test_miscased_production_mode_is_unverified_not_sandbox(monkeypatch, temp_db):
    """'Production' (miscased) must stay red but not be mislabelled sandbox."""
    server = _env(monkeypatch, "production")
    _clear_ca_env(monkeypatch)
    _set_valid_ca_env(monkeypatch, mode="weird-label")

    ready, payload = server._readiness_status_payload()
    aml = _aml(payload)
    assert aml["status"] == "mode_unverified"
    assert ready is False

    # Miscased "Production" is accepted case-insensitively (fail direction
    # unchanged: only an explicit production attestation reaches ok).
    monkeypatch.setenv("COMPLYADVANTAGE_WORKSPACE_MODE", "Production")
    ready2, payload2 = server._readiness_status_payload()
    assert _aml(payload2)["status"] == "ok"
    assert ready2 is True


def test_webhook_posture_reporter_matches_enforcer(monkeypatch, temp_db):
    """The readiness-reported posture must come from the webhook handler's own
    classifier — reporter/enforcer divergence would overstate security."""
    from screening_complyadvantage import webhook_handler as wh
    import server

    # ENVIRONMENT unset but legacy ENV=production: the enforcer's raw
    # _environment() sees 'development' (fail-open) — the reporter must say
    # the same, not claim fail-closed.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("COMPLYADVANTAGE_WEBHOOK_SECRET", raising=False)

    assert server._ca_webhook_signature_mode() == wh.current_signature_mode()


def test_prod_alias_fails_closed_on_webhook_signatures(monkeypatch, temp_db):
    """CodeRabbit finding: a raw ENVIRONMENT=prod must fail CLOSED on webhook
    signatures in BOTH the enforcing path and the reporter (composes with
    #673's canonicalization, where this literal becomes unreachable)."""
    from screening_complyadvantage import webhook_handler as wh

    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("COMPLYADVANTAGE_WEBHOOK_SECRET", raising=False)

    assert wh._signature_status(b"{}", {}) == "deployed_secret_missing"
    assert wh.current_signature_mode() == "deployed_fail_closed_missing_secret"


def test_whitespace_only_secret_agrees_reporter_and_enforcer(monkeypatch, temp_db):
    """CodeRabbit finding: a whitespace-only secret must be treated as ABSENT by
    BOTH the enforcer (_signature_status) and the reporter (current_signature_
    mode). Before the fix the enforcer's truthy `if secret:` attempted
    verification while the reporter (which strips) reported 'missing secret' —
    a divergence where readiness claims fail-closed but the gate acts otherwise."""
    from screening_complyadvantage import webhook_handler as wh

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("COMPLYADVANTAGE_WEBHOOK_SECRET", "   \t  ")

    # Enforcer: whitespace secret is not a usable secret -> fail closed in prod.
    assert wh._signature_status(b"{}", {}) == "deployed_secret_missing"
    # Reporter: same posture.
    assert wh.current_signature_mode() == "deployed_fail_closed_missing_secret"
    # And they map to the SAME metric mode (no reporter/enforcer divergence).
    assert wh._metric_signature_mode(wh._signature_status(b"{}", {})) == "deployed_fail_closed"
