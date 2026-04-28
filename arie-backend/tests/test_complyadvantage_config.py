import importlib
import os

import pytest


ENV = {
    "COMPLYADVANTAGE_API_BASE_URL": " https://api.example.test/ ",
    "COMPLYADVANTAGE_AUTH_URL": " https://auth.example.test/v2/token ",
    "COMPLYADVANTAGE_REALM": "regmind",
    "COMPLYADVANTAGE_USERNAME": " officer@example.test ",
    "COMPLYADVANTAGE_PASSWORD": " secret ",
}


def test_from_env_loads_and_normalizes(monkeypatch):
    from screening_complyadvantage.config import CAConfig

    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    config = CAConfig.from_env()

    assert config.api_base_url == "https://api.example.test"
    assert config.auth_url == "https://auth.example.test/v2/token"
    assert config.realm == "regmind"
    assert config.username == "officer@example.test"
    assert config.password == "secret"


@pytest.mark.parametrize("name", ENV.keys())
def test_from_env_missing_or_empty_raises(monkeypatch, name):
    from screening_complyadvantage.config import CAConfig
    from screening_complyadvantage.exceptions import CAConfigurationError

    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    monkeypatch.delenv(name, raising=False)
    with pytest.raises(CAConfigurationError):
        CAConfig.from_env()

    monkeypatch.setenv(name, "   ")
    with pytest.raises(CAConfigurationError):
        CAConfig.from_env()


@pytest.mark.parametrize("realm", ["RegMind", "REGMIND", " regmind ", " regmind-other ", "demo"])
def test_realm_is_exact_lowercase_regmind_without_variations(monkeypatch, realm):
    from screening_complyadvantage.config import CAConfig
    from screening_complyadvantage.exceptions import CAConfigurationError

    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("COMPLYADVANTAGE_REALM", realm)

    with pytest.raises(CAConfigurationError, match="realm must be 'regmind'"):
        CAConfig.from_env()


def test_import_does_not_read_environment(monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)

    module = importlib.reload(importlib.import_module("screening_complyadvantage.config"))

    assert hasattr(module, "CAConfig")
    assert "COMPLYADVANTAGE_REALM" not in os.environ
