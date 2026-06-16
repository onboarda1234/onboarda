import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


_CA_ENV = {
    "COMPLYADVANTAGE_API_BASE_URL": "https://api.ca.example.test",
    "COMPLYADVANTAGE_AUTH_URL": "https://auth.ca.example.test/token",
    "COMPLYADVANTAGE_REALM": "regmind",
    "COMPLYADVANTAGE_USERNAME": "ca-user",
    "COMPLYADVANTAGE_PASSWORD": "ca-password",
    "COMPLYADVANTAGE_SCREENING_CONFIG_ID": "cfg-123",
    "COMPLYADVANTAGE_WORKSPACE_MODE": "sandbox",
    "COMPLYADVANTAGE_WORKSPACE_LABEL": "ca-sandbox",
    "COMPLYADVANTAGE_SCREENING_CONFIG_LABEL": "regmind-default-screening-v1",
}


def _clear_ca_env(monkeypatch):
    for key in _CA_ENV:
        monkeypatch.delenv(key, raising=False)


def _set_ca_env(monkeypatch):
    for key, value in _CA_ENV.items():
        monkeypatch.setenv(key, value)


def test_complyadvantage_status_is_not_live_when_unconfigured(monkeypatch):
    from server import _complyadvantage_runtime_status

    _clear_ca_env(monkeypatch)
    monkeypatch.setenv("SCREENING_PROVIDER", "sumsub")
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")

    status = _complyadvantage_runtime_status()

    assert status["configured"] is False
    assert status["active"] is False
    assert status["status"] == "not_configured"
    assert status["implementation_status"] == "not_configured"
    assert "AML sanctions" in status["role"]
    assert status["provider_display_name"] == "ComplyAdvantage Mesh"
    assert status["simulation_fallback_enabled"] is False
    assert status["mode"] == "unknown"
    assert status["workspace_label"] == "unknown"
    assert status["last_token_auth_probe_result"]["status"] == "not_run"


def test_complyadvantage_status_ready_but_blocked_until_abstraction_enabled(monkeypatch):
    from server import _complyadvantage_runtime_status

    _set_ca_env(monkeypatch)
    monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")

    status = _complyadvantage_runtime_status()

    assert status["configured"] is True
    assert status["active"] is False
    assert status["status"] == "ready"
    assert status["mode"] == "sandbox"
    assert status["workspace_label"] == "ca-sandbox"
    assert status["screening_configuration_identifier"] == "cfg-123"
    assert status["screening_configuration_label"] == "regmind-default-screening-v1"
    assert "ENABLE_SCREENING_ABSTRACTION is false" in status["blockers"]


def test_complyadvantage_status_misconfigured_when_partial_env_present(monkeypatch):
    from server import _complyadvantage_runtime_status

    _clear_ca_env(monkeypatch)
    monkeypatch.setenv("COMPLYADVANTAGE_API_BASE_URL", "https://api.ca.example.test")
    monkeypatch.setenv("SCREENING_PROVIDER", "sumsub")
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")

    status = _complyadvantage_runtime_status()

    assert status["configured"] is False
    assert status["active"] is False
    assert status["status"] == "misconfigured"
    assert status["blockers"]
    assert status["last_error_category"] == "configuration"


def test_complyadvantage_status_live_only_with_provider_and_abstraction(monkeypatch):
    from server import _complyadvantage_runtime_status

    _set_ca_env(monkeypatch)
    monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")

    status = _complyadvantage_runtime_status()

    assert status["configured"] is True
    assert status["active"] is True
    assert status["status"] == "live"
    assert status["blockers"] == []
    assert status["fallback_mode"] == "disabled"


def test_complyadvantage_status_auth_probe_success(monkeypatch):
    from server import _complyadvantage_runtime_status

    _set_ca_env(monkeypatch)
    monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")
    monkeypatch.setattr("screening_complyadvantage.auth.ComplyAdvantageTokenClient.force_refresh", lambda self: "token")

    status = _complyadvantage_runtime_status(probe_auth=True)

    assert status["last_provider_health_result"] == "ok"
    assert status["last_token_auth_probe_result"]["status"] == "ok"
    assert status["last_error_category"] is None


def test_complyadvantage_status_auth_probe_failure_is_sanitized(monkeypatch):
    from screening_complyadvantage.exceptions import CAAuthenticationFailed
    from server import _complyadvantage_runtime_status

    _set_ca_env(monkeypatch)
    monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")

    def fail_refresh(self):
        raise CAAuthenticationFailed("password=ca-password username=ca-user rejected")

    monkeypatch.setattr("screening_complyadvantage.auth.ComplyAdvantageTokenClient.force_refresh", fail_refresh)

    status = _complyadvantage_runtime_status(probe_auth=True)
    serialized = json.dumps(status)

    assert status["last_provider_health_result"] == "unavailable"
    assert status["last_token_auth_probe_result"]["status"] == "unavailable"
    assert status["last_token_auth_probe_result"]["error_category"] == "CAAuthenticationFailed"
    assert status["last_error_category"] == "CAAuthenticationFailed"
    assert "ca-password" not in serialized
    assert "ca-user" not in serialized


def test_complyadvantage_status_payload_does_not_expose_secrets(monkeypatch):
    from server import _complyadvantage_runtime_status

    _set_ca_env(monkeypatch)
    monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")
    monkeypatch.setenv("COMPLYADVANTAGE_PASSWORD", "top-secret-password")
    monkeypatch.setenv("COMPLYADVANTAGE_USERNAME", "sandbox-user@example.test")

    status = _complyadvantage_runtime_status()
    serialized = json.dumps(status)

    assert "top-secret-password" not in serialized
    assert "sandbox-user@example.test" not in serialized
    assert status["mode"] == "sandbox"
