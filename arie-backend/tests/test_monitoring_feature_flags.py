"""PR-MON-FEATURE-FLAGS-1 Monitoring feature-governance contracts."""

import json
import logging
from pathlib import Path

import pytest
from tornado.testing import AsyncHTTPTestCase

import environment
from environment import (
    FeatureFlags,
    MONITORING_FEATURE_DEFINITIONS,
    MONITORING_FEATURE_FLAGS,
    _DEFAULT_FLAGS,
    get_environment_info,
    get_monitoring_feature_state,
    get_monitoring_feature_status,
    log_monitoring_feature_state_once,
)


EXPECTED_FLAGS = (
    "ENABLE_DOCUMENT_RENEWAL_AUTOMATION",
    "ENABLE_AGENT1_REFRESH_VERIFICATION",
    "ENABLE_MONITORING_SCREENING_CHANGE",
    "ENABLE_MONITORING_AUTO_RESOLUTION",
)


def _clear_monitoring_env(monkeypatch):
    for flag in EXPECTED_FLAGS:
        monkeypatch.delenv(flag, raising=False)


def test_governed_flag_inventory_is_exact():
    assert MONITORING_FEATURE_FLAGS == EXPECTED_FLAGS
    assert tuple(
        flag for flag, _label, _consumer in MONITORING_FEATURE_DEFINITIONS
    ) == EXPECTED_FLAGS


@pytest.mark.parametrize("env", sorted(_DEFAULT_FLAGS))
def test_all_monitoring_flags_default_off_in_every_environment(env, monkeypatch):
    _clear_monitoring_env(monkeypatch)
    evaluated = get_monitoring_feature_state(FeatureFlags(env))
    assert evaluated == {flag: False for flag in EXPECTED_FLAGS}


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "FALSE"])
def test_explicit_off_remains_off(raw, monkeypatch):
    _clear_monitoring_env(monkeypatch)
    for flag in EXPECTED_FLAGS:
        monkeypatch.setenv(flag, raw)
    assert get_monitoring_feature_state(FeatureFlags("staging")) == {
        flag: False for flag in EXPECTED_FLAGS
    }


@pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "TRUE"])
def test_explicit_on_only_changes_evaluated_state(raw, monkeypatch):
    _clear_monitoring_env(monkeypatch)
    for flag in EXPECTED_FLAGS:
        monkeypatch.setenv(flag, raw)
    assert get_monitoring_feature_state(FeatureFlags("production")) == {
        flag: True for flag in EXPECTED_FLAGS
    }


@pytest.mark.parametrize("raw", ["", "unknown", "enabled", "2", "truthy"])
def test_unknown_values_fail_safely_off(raw, monkeypatch):
    _clear_monitoring_env(monkeypatch)
    for flag in EXPECTED_FLAGS:
        monkeypatch.setenv(flag, raw)
    assert get_monitoring_feature_state(FeatureFlags("development")) == {
        flag: False for flag in EXPECTED_FLAGS
    }


def test_missing_configuration_fails_safely_off(monkeypatch):
    _clear_monitoring_env(monkeypatch)
    assert get_monitoring_feature_state(FeatureFlags("production")) == {
        flag: False for flag in EXPECTED_FLAGS
    }


def test_operator_status_is_read_only_and_shows_evaluated_values(monkeypatch):
    _clear_monitoring_env(monkeypatch)
    monkeypatch.setenv("ENABLE_AGENT1_REFRESH_VERIFICATION", "true")
    status = get_monitoring_feature_status(FeatureFlags("staging"))

    assert status["read_only"] is True
    assert status["activation_controls"] is False
    assert set(status["features"]) == set(EXPECTED_FLAGS)
    for flag, item in status["features"].items():
        expected = flag == "ENABLE_AGENT1_REFRESH_VERIFICATION"
        assert item["enabled"] is expected
        assert item["state"] == ("ON" if expected else "OFF")
        assert item["label"]


def test_environment_endpoint_payload_exposes_only_read_only_monitoring_status(
    monkeypatch,
):
    _clear_monitoring_env(monkeypatch)
    monkeypatch.setattr(environment, "flags", FeatureFlags("staging"))
    payload = get_environment_info()

    assert payload["monitoring_feature_flags"] == get_monitoring_feature_status(
        environment.flags
    )
    assert set(EXPECTED_FLAGS).isdisjoint(payload["features"])
    assert payload["monitoring_feature_flags"]["activation_controls"] is False


def test_environment_status_handler_has_no_mutating_method():
    from server import EnvironmentInfoHandler

    for method in ("post", "put", "patch", "delete"):
        assert method not in EnvironmentInfoHandler.__dict__
    assert "get" in EnvironmentInfoHandler.__dict__


class TestMonitoringOperatorStatusEndpoint(AsyncHTTPTestCase):
    def get_app(self):
        from server import make_app

        return make_app()

    def test_existing_environment_endpoint_reports_read_only_status(self):
        response = self.fetch("/api/config/environment")
        assert response.code == 200
        payload = json.loads(response.body)
        status = payload["monitoring_feature_flags"]
        assert status["read_only"] is True
        assert status["activation_controls"] is False
        assert set(status["features"]) == set(EXPECTED_FLAGS)

    def test_existing_environment_endpoint_rejects_mutation(self):
        response = self.fetch(
            "/api/config/environment",
            method="POST",
            body=b"{}",
            headers={"Content-Type": "application/json"},
            raise_error=False,
        )
        assert response.code == 405


def test_startup_logging_emits_one_record_with_all_evaluated_values(
    monkeypatch,
    caplog,
):
    _clear_monitoring_env(monkeypatch)
    monkeypatch.setenv("ENABLE_MONITORING_SCREENING_CHANGE", "true")
    monkeypatch.setattr(environment, "_monitoring_feature_state_logged", False)
    evaluated = FeatureFlags("staging")

    with caplog.at_level(logging.INFO, logger="arie.environment"):
        assert log_monitoring_feature_state_once(evaluated) is True
        assert log_monitoring_feature_state_once(evaluated) is False

    records = [
        record for record in caplog.records
        if record.getMessage().startswith("Monitoring Feature Flags")
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "Document Renewal: OFF" in message
    assert "Agent 1 Refresh Verification: OFF" in message
    assert "Screening Monitoring: ON" in message
    assert "Automatic Resolution: OFF" in message


def test_generic_startup_summary_does_not_repeat_monitoring_states(
    monkeypatch,
    caplog,
):
    _clear_monitoring_env(monkeypatch)
    monkeypatch.setattr(environment, "flags", FeatureFlags("testing"))
    monkeypatch.setattr(environment, "_monitoring_feature_state_logged", False)
    monkeypatch.setattr(environment, "validate_environment", lambda: [])

    with caplog.at_level(logging.INFO, logger="arie.environment"):
        environment.enforce_startup_safety()

    generic_records = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith(("Enabled flags", "Disabled flags"))
    ]
    assert all(
        flag not in message
        for message in generic_records
        for flag in EXPECTED_FLAGS
    )


def test_startup_safety_wires_monitoring_log_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        environment,
        "log_monitoring_feature_state_once",
        lambda: calls.append("logged"),
    )
    monkeypatch.setattr(environment, "validate_environment", lambda: [])

    environment.enforce_startup_safety()

    assert calls == ["logged"]


def test_only_document_renewal_service_consumes_its_approved_flag():
    """Each first workflow consumer is explicit and narrowly allowlisted."""
    backend = Path(environment.__file__).resolve().parent
    consumers = []
    for path in backend.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name == "environment.py":
            continue
        text = path.read_text(encoding="utf-8")
        used = sorted(flag for flag in EXPECTED_FLAGS if flag in text)
        if used:
            consumers.append((str(path.relative_to(backend)), used))
    assert consumers == [
        (
            "monitoring_document_renewal.py",
            ["ENABLE_DOCUMENT_RENEWAL_AUTOMATION"],
        )
    ]
