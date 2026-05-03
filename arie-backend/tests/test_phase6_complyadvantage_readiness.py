import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


_CA_ENV = {
    "COMPLYADVANTAGE_API_BASE_URL": "https://api.ca.example.test",
    "COMPLYADVANTAGE_AUTH_URL": "https://auth.ca.example.test/token",
    "COMPLYADVANTAGE_REALM": "regmind",
    "COMPLYADVANTAGE_USERNAME": "ca-user",
    "COMPLYADVANTAGE_PASSWORD": "ca-password",
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
    assert status["implementation_status"] == "in_progress"
    assert "KYB" in status["role"]


def test_complyadvantage_status_ready_but_blocked_until_abstraction_enabled(monkeypatch):
    from server import _complyadvantage_runtime_status

    _set_ca_env(monkeypatch)
    monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")

    status = _complyadvantage_runtime_status()

    assert status["configured"] is True
    assert status["active"] is False
    assert status["status"] == "ready"
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
