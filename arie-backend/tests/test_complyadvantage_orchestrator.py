import json
import logging
import os
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from screening_complyadvantage.exceptions import CATimeout, CAUnexpectedResponse
from screening_complyadvantage.normalizer import ScreeningApplicationContext
from screening_complyadvantage.orchestrator import ComplyAdvantageScreeningOrchestrator


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "complyadvantage")


@dataclass
class FakeConfig:
    api_base_url: str = "https://api.example.test"


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _context(data=None):
    return ScreeningApplicationContext.model_validate((data or _fixture("pep_canonical.json"))["context"])


def _customer(marker):
    return {
        "person": {
            "first_name": "Test",
            "last_name": marker,
            "full_name": f"Test {marker}",
            "metadata": {"pass": marker},
        }
    }


def _alert_without_deep(alert):
    return {"identifier": alert["identifier"], "profile": alert["profile"]}


class FakeCAClient:
    def __init__(self, fixture, *, skipped=False, pages=None, deep_failure=False, workflow_sequence=None):
        self.config = FakeConfig()
        self.fixture = fixture
        self.skipped = skipped
        self.pages = pages
        self.deep_failure = deep_failure
        self.workflow_sequence = list(workflow_sequence or [])
        self.posts = []
        self.gets = []
        self.alert_fetches = 0
        self.deep_fetches = 0

    def post(self, path, json_body=None):
        self.posts.append((path, json_body))
        marker = json_body["customer"].get("person", {}).get("metadata", {}).get("pass", "strict")
        prefix = "relaxed_" if marker == "relaxed" else "strict_"
        workflow = self.fixture.get(prefix + "workflow") or self.fixture["workflow"]
        response = {
            "workflow": workflow,
            "customer": self.fixture["customer_response"],
        }
        return response

    def get(self, path, params=None):
        self.gets.append((path, params))
        if path.startswith("/v2/workflows/") and path.endswith("/alerts"):
            self.alert_fetches += 1
            return self._page(path, first=True)
        if path.startswith("/v2/workflows/") and "/alerts" in path:
            self.alert_fetches += 1
            return self._page(path, first=False)
        if path.startswith("/v2/workflows/"):
            if self.workflow_sequence:
                return self.workflow_sequence.pop(0)
            workflow_id = path.rsplit("/", 1)[-1]
            workflow = self._workflow_for_id(workflow_id)
            if self.skipped:
                workflow = dict(workflow)
                workflow["step_details"] = {"case-creation": {"status": "SKIPPED"}}
            return workflow
        if path.startswith("/v2/entity-screening/risks/"):
            self.deep_fetches += 1
            if self.deep_failure:
                raise CAUnexpectedResponse("deep risk failed")
            alert_id = path.rsplit("/", 1)[-1]
            for key in ("strict_alerts", "relaxed_alerts", "alerts"):
                for alert in self.fixture.get(key, []):
                    if alert["identifier"] == alert_id:
                        return alert["risk_detail"]
            return {"values": []}
        raise AssertionError(path)

    def _workflow_for_id(self, workflow_id):
        for key in ("strict_workflow", "relaxed_workflow", "workflow"):
            workflow = self.fixture.get(key)
            if workflow and workflow["workflow_instance_identifier"] == workflow_id:
                return workflow
        return self.fixture.get("workflow") or self.fixture["strict_workflow"]

    def _page(self, path, first):
        if self.pages is not None:
            return self.pages[path if not first else "first"]
        workflow_id = path.split("/")[3]
        if workflow_id == self.fixture.get("relaxed_workflow", {}).get("workflow_instance_identifier"):
            alerts = self.fixture.get("relaxed_alerts", [])
        elif workflow_id == self.fixture.get("strict_workflow", {}).get("workflow_instance_identifier"):
            alerts = self.fixture.get("strict_alerts", [])
        else:
            alerts = self.fixture.get("alerts") or self.fixture.get("strict_alerts") or []
        return {
            "values": [_alert_without_deep(alert) for alert in alerts],
            "pagination": {"next": None},
        }


def _orchestrator(client):
    return ComplyAdvantageScreeningOrchestrator(client, clock=lambda: 0, sleep_fn=lambda _: None)


def test_create_and_screen_success_failure_and_malformed():
    client = FakeCAClient(_fixture("pep_canonical.json"))
    orch = _orchestrator(client)

    workflow, customer, customer_input, enabled = orch.create_and_screen(_customer("strict"))

    assert workflow.workflow_instance_identifier == "wf-pep"
    assert customer.identifier == "cust-test"
    assert customer_input.person.full_name == "Test strict"
    assert enabled is True

    client.post = lambda *a, **k: {"customer": {}}
    with pytest.raises(CAUnexpectedResponse):
        orch.create_and_screen(_customer("strict"))

    client.post = lambda *a, **k: {"workflow": {"status": "COMPLETED"}}
    with pytest.raises(CAUnexpectedResponse):
        orch.create_and_screen(_customer("strict"))


def test_polling_loop_uses_backoff_and_times_out_with_fake_clock():
    now = {"value": 0.0}
    sleeps = []

    def clock():
        return now["value"]

    def sleep_fn(delay):
        sleeps.append(delay)
        now["value"] += delay

    in_progress = {
        "workflow_instance_identifier": "wf",
        "workflow_type": "screening",
        "status": "IN-PROGRESS",
        "step_details": {"case-creation": {"status": "IN-PROGRESS"}},
    }
    complete = {**in_progress, "status": "COMPLETED", "step_details": {"case-creation": {"status": "COMPLETED"}}}
    client = FakeCAClient(_fixture("pep_canonical.json"), workflow_sequence=[in_progress, in_progress, complete])
    workflow = ComplyAdvantageScreeningOrchestrator(client, clock=clock, sleep_fn=sleep_fn).poll_workflow_until_complete("wf")

    assert workflow.status == "COMPLETED"
    assert sleeps == [1.0, 1.6]

    timeout_client = FakeCAClient(_fixture("pep_canonical.json"), workflow_sequence=[in_progress] * 10)
    with pytest.raises(CATimeout):
        ComplyAdvantageScreeningOrchestrator(timeout_client, poll_timeout_seconds=1, clock=clock, sleep_fn=sleep_fn).poll_workflow_until_complete("wf")


def test_case_creation_completed_returns_full_report():
    report = _orchestrator(FakeCAClient(_fixture("pep_canonical.json"))).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("relaxed"),
        application_context=_context(),
        monitoring_enabled=False,
    )

    assert report["total_hits"] == 1
    assert report["any_pep_hits"] is True


def test_case_creation_skipped_clean_path_fetches_no_alerts_or_deep_risks():
    client = FakeCAClient(_fixture("clean_baseline.json"), skipped=True)

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("relaxed"),
        application_context=_context(_fixture("clean_baseline.json")),
        monitoring_enabled=False,
    )

    assert report["total_hits"] == 0
    assert report["any_pep_hits"] is False
    assert client.alert_fetches == 0
    assert client.deep_fetches == 0


def test_pagination_boundary_includes_26th_canonical_match():
    data = _fixture("pep_canonical.json")
    canonical = data["alerts"][0]
    dummy_alerts = []
    for i in range(25):
        alert = json.loads(json.dumps(canonical))
        alert["identifier"] = f"risk-dummy-{i}"
        alert["profile"]["identifier"] = f"prof-dummy-{i}"
        alert["risk_detail"] = {"values": []}
        dummy_alerts.append(alert)
    client_data = {**data, "alerts": dummy_alerts + [canonical]}
    first_values = [_alert_without_deep(a) for a in client_data["alerts"][:25]]
    second_values = [_alert_without_deep(client_data["alerts"][25])]
    pages = {
        "first": {"values": first_values, "pagination": {"next": "/v2/workflows/wf-pep/alerts?page=2"}},
        "/v2/workflows/wf-pep/alerts?page=2": {"values": second_values, "pagination": {"next": None}},
    }
    client = FakeCAClient(client_data, pages=pages)

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("relaxed"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    identifiers = [m["profile_identifier"] for m in report["provider_specific"]["complyadvantage"]["matches"]]
    assert "prof-pep" in identifiers


def test_pagination_next_relative_absolute_and_wrong_host():
    data = _fixture("pep_canonical.json")
    pages = {
        "first": {"values": [], "pagination": {"next": "https://api.example.test/v2/workflows/wf-pep/alerts?page=2"}},
        "/v2/workflows/wf-pep/alerts?page=2": {"values": [], "pagination": {"next": None}},
    }
    client = FakeCAClient(data, pages=pages)

    assert _orchestrator(client).fetch_alerts_paginated("wf-pep") == []
    assert client.gets[-1][0] == "/v2/workflows/wf-pep/alerts?page=2"

    pages["first"]["pagination"]["next"] = "https://evil.example.test/v2/workflows/wf-pep/alerts?page=2"
    with pytest.raises(CAUnexpectedResponse):
        _orchestrator(FakeCAClient(data, pages=pages)).fetch_alerts_paginated("wf-pep")


def test_two_pass_relaxed_catches_canonical_match():
    data = _fixture("two_pass_strict_misses_relaxed_catches.json")

    report = _orchestrator(FakeCAClient(data)).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("relaxed"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    matches = report["provider_specific"]["complyadvantage"]["matches"]
    canonical = [m for m in matches if m["profile_identifier"] == "prof-canonical"][0]
    assert canonical["surfaced_by_pass"] == "relaxed"


def test_partial_deep_risk_failure_raises():
    with pytest.raises(CAUnexpectedResponse):
        _orchestrator(FakeCAClient(_fixture("pep_canonical.json"), deep_failure=True)).screen_customer_two_pass(
            strict_customer=_customer("strict"),
            relaxed_customer=_customer("relaxed"),
            application_context=_context(),
            monitoring_enabled=False,
        )


def test_db_injected_monitoring_enabled_seeds_and_logs_info(caplog):
    db = object()
    with patch("screening_complyadvantage.orchestrator.seed_monitoring_subscription") as seed:
        with caplog.at_level(logging.INFO, logger="screening_complyadvantage.orchestrator"):
            report = _orchestrator(FakeCAClient(_fixture("clean_baseline.json"), skipped=True)).screen_customer_two_pass(
                strict_customer=_customer("strict"),
                relaxed_customer=_customer("relaxed"),
                application_context=_context(_fixture("clean_baseline.json")),
                monitoring_enabled=True,
                db=db,
            )

    assert report["total_hits"] == 0
    seed.assert_called_once_with(db, "client-test", "app-clean", "cust-test", person_key=None)
    assert "ca_monitoring_subscription_seeded" in caplog.text


def test_db_absent_monitoring_enabled_warns_and_still_returns(caplog):
    with patch("screening_complyadvantage.orchestrator.seed_monitoring_subscription") as seed:
        with caplog.at_level(logging.WARNING, logger="screening_complyadvantage.orchestrator"):
            report = _orchestrator(FakeCAClient(_fixture("clean_baseline.json"), skipped=True)).screen_customer_two_pass(
                strict_customer=_customer("strict"),
                relaxed_customer=_customer("relaxed"),
                application_context=_context(_fixture("clean_baseline.json")),
                monitoring_enabled=True,
                db=None,
            )

    assert report["total_hits"] == 0
    seed.assert_not_called()
    assert "db_handle_not_injected" in caplog.text


def test_monitoring_disabled_does_not_seed_even_with_db():
    with patch("screening_complyadvantage.orchestrator.seed_monitoring_subscription") as seed:
        _orchestrator(FakeCAClient(_fixture("pep_canonical.json"))).screen_customer_two_pass(
            strict_customer=_customer("strict"),
            relaxed_customer=_customer("relaxed"),
            application_context=_context(),
            monitoring_enabled=False,
            db=object(),
        )

    seed.assert_not_called()
