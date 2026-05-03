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


class PathStrictFakeCAClient:
    def __init__(self, post_routes, get_routes, *, config=None):
        self.config = config or FakeConfig()
        self.post_routes = dict(post_routes)
        self.get_routes = {k: list(v) if isinstance(v, list) else [v] for k, v in get_routes.items()}
        self.posts = []
        self.gets = []

    def post(self, path, json_body=None):
        self.posts.append((path, json_body))
        if path not in self.post_routes:
            raise AssertionError(f"unexpected POST path: {path}")
        return self.post_routes[path]

    def get(self, path, params=None):
        self.gets.append((path, params))
        if params:
            raise AssertionError(f"unexpected params for {path}: {params}")
        if path not in self.get_routes:
            raise AssertionError(f"unexpected GET path: {path}")
        responses = self.get_routes[path]
        if len(responses) > 1:
            response = responses.pop(0)
        else:
            response = responses[0]
        if isinstance(response, Exception):
            raise response
        return response

    def called_paths(self):
        return [path for path, _ in self.gets]


def _workflow_post(workflow):
    return {"workflow_instance_identifier": workflow["workflow_instance_identifier"]}


def _alert_risks_page(alert_id, risks=None, next_link=None, prev_link=None, total_count=None, self_link=None):
    risks = list(risks or [])
    path = f"/v2/alerts/{alert_id}/risks?page=1"
    return {
        "first": f"/v2/alerts/{alert_id}/risks?page=1",
        "next": next_link,
        "prev": prev_link,
        "risks": risks,
        "self": self_link or path,
        "total_count": len(risks) if total_count is None else total_count,
    }


def _routes_for_fixture(data, *, prefix="", deep_failure=False):
    workflow = data[prefix + "workflow"] if prefix else data["workflow"]
    alerts_key = prefix + "alerts_risks" if prefix else "alerts_risks"
    deep_key = prefix + "deep_risks" if prefix else "deep_risks"
    get_routes = {f"/v2/workflows/{workflow['workflow_instance_identifier']}": workflow}
    for alert_id, risks in data.get(alerts_key, {}).items():
        get_routes[f"/v2/alerts/{alert_id}/risks?page=1"] = _alert_risks_page(alert_id, risks)
        for risk in risks:
            risk_id = risk["identifier"]
            get_routes[f"/v2/entity-screening/risks/{risk_id}"] = (
                CAUnexpectedResponse("deep risk failed") if deep_failure else data[deep_key][risk_id]
            )
    return get_routes


def _client_for_single(data, *, deep_failure=False):
    return PathStrictFakeCAClient(
        {"/v2/workflows/create-and-screen": _workflow_post(data["workflow"])},
        _routes_for_fixture(data, deep_failure=deep_failure),
    )


def _client_for_two_pass(data):
    post_calls = []

    class TwoPassClient(PathStrictFakeCAClient):
        def post(self, path, json_body=None):
            self.posts.append((path, json_body))
            if path != "/v2/workflows/create-and-screen":
                raise AssertionError(f"unexpected POST path: {path}")
            post_calls.append(json_body)
            marker = json_body["customer"]["person"]["metadata"]["pass"]
            workflow = data["relaxed_workflow"] if marker == "relaxed" else data["strict_workflow"]
            return _workflow_post(workflow)

    routes = {}
    routes.update(_routes_for_fixture(data, prefix="strict_"))
    routes.update(_routes_for_fixture(data, prefix="relaxed_"))
    client = TwoPassClient({}, routes)
    client.post_calls = post_calls
    return client


def _orchestrator(client, *, clock=lambda: 0, sleep_fn=lambda _: None, poll_timeout_seconds=300):
    return ComplyAdvantageScreeningOrchestrator(
        client,
        poll_timeout_seconds=poll_timeout_seconds,
        clock=clock,
        sleep_fn=sleep_fn,
    )


def test_create_and_screen_accepts_workflow_handle_only_response():
    data = _fixture("pep_canonical.json")
    client = _client_for_single(data)
    orch = _orchestrator(client)

    result = orch.create_and_screen(_customer("strict"))

    assert result.workflow_instance_identifier == "wf-pep"
    assert result.customer_input.person.full_name == "Test strict"
    assert result.monitoring_enabled is True

    client.post_routes["/v2/workflows/create-and-screen"] = {"customer": {}}
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
    complete = {
        **in_progress,
        "status": "COMPLETED",
        "step_details": {
            "case-creation": {"status": "COMPLETED"},
            "customer-creation": {"status": "COMPLETED", "output": {"customer_identifier": "cust-test"}},
        },
    }
    client = PathStrictFakeCAClient({}, {"/v2/workflows/wf": [in_progress, in_progress, complete]})
    result = ComplyAdvantageScreeningOrchestrator(client, clock=clock, sleep_fn=sleep_fn).poll_workflow_until_complete("wf")

    assert result.workflow.status == "COMPLETED"
    assert sleeps == [1.0, 1.6]

    timeout_client = PathStrictFakeCAClient({}, {"/v2/workflows/wf": [in_progress] * 10})
    with pytest.raises(CATimeout):
        ComplyAdvantageScreeningOrchestrator(
            timeout_client, poll_timeout_seconds=1, clock=clock, sleep_fn=sleep_fn
        ).poll_workflow_until_complete("wf")


def test_case_creation_completed_returns_full_report_with_three_layer_paths():
    data = _fixture("pep_canonical.json")
    client = _client_for_single(data)

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("strict"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    paths = client.called_paths()
    assert "/v2/workflows/wf-pep" in paths
    assert "/v2/alerts/alert-pep/risks?page=1" in paths
    assert "/v2/entity-screening/risks/risk-pep" in paths
    assert "/v2/workflows/wf-pep/alerts" not in paths
    assert report["total_hits"] == 1
    assert report["any_pep_hits"] is True


def test_case_creation_skipped_clean_path_fetches_no_risks_or_deep_risks():
    data = _fixture("clean_baseline.json")
    client = _client_for_single(data)

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("strict"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    assert report["total_hits"] == 0
    assert report["any_pep_hits"] is False
    assert client.called_paths() == ["/v2/workflows/wf-clean", "/v2/workflows/wf-clean"]


def test_pagination_boundary_uses_alert_risk_layer_and_includes_26th_match():
    data = _fixture("pep_canonical.json")
    canonical_risk = data["alerts_risks"]["alert-pep"][0]
    canonical_deep = data["deep_risks"]["risk-pep"]
    workflow = json.loads(json.dumps(data["workflow"]))
    workflow["workflow_instance_identifier"] = "wf-boundary"
    workflow["alerts"] = [{"identifier": "alert-pep-1"}]
    workflow["step_details"]["customer-creation"]["output"]["customer_identifier"] = "cust-boundary"
    first_page = _alert_risks_page(
        "alert-pep-1",
        [{"identifier": f"risk-dummy-{i}", "profile": canonical_risk["profile"]} for i in range(25)],
        next_link="/v2/alerts/alert-pep-1/risks?page=2",
    )
    second_page = _alert_risks_page(
        "alert-pep-1",
        [{"identifier": "risk-pep-26", "profile": canonical_risk["profile"]}],
        self_link="/v2/alerts/alert-pep-1/risks?page=2",
        total_count=26,
    )
    get_routes = {
        "/v2/workflows/wf-boundary": workflow,
        "/v2/alerts/alert-pep-1/risks?page=1": first_page,
        "/v2/alerts/alert-pep-1/risks?page=2": second_page,
        "/v2/entity-screening/risks/risk-pep-26": canonical_deep,
    }
    for i in range(25):
        get_routes[f"/v2/entity-screening/risks/risk-dummy-{i}"] = {"values": []}
    client = PathStrictFakeCAClient(
        {"/v2/workflows/create-and-screen": {"workflow_instance_identifier": "wf-boundary"}},
        get_routes,
    )

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("strict"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    identifiers = [m["profile_identifier"] for m in report["provider_specific"]["complyadvantage"]["matches"]]
    assert "prof-pep" in identifiers
    paths = client.called_paths()
    assert "/v2/alerts/alert-pep-1/risks?page=1" in paths
    assert "/v2/alerts/alert-pep-1/risks?page=2" in paths
    assert "/v2/entity-screening/risks/risk-pep-26" in paths
    assert "/v2/workflows/wf-boundary/alerts" not in paths


def test_pagination_next_relative_absolute_and_wrong_host():
    data = _fixture("pep_canonical.json")
    client = PathStrictFakeCAClient(
        {},
        {
            "/v2/alerts/alert-pep/risks?page=1": _alert_risks_page(
                "alert-pep",
                next_link="https://api.example.test/v2/alerts/alert-pep/risks?page=2",
            ),
            "/v2/alerts/alert-pep/risks?page=2": _alert_risks_page(
                "alert-pep",
                self_link="/v2/alerts/alert-pep/risks?page=2",
            ),
        },
    )

    assert _orchestrator(client).fetch_risks_paginated_for_alert("alert-pep") == []
    assert client.called_paths()[-1] == "/v2/alerts/alert-pep/risks?page=2"

    bad_client = PathStrictFakeCAClient(
        {},
        {
            "/v2/alerts/alert-pep/risks?page=1": _alert_risks_page(
                "alert-pep",
                next_link="https://evil.example.test/v2/alerts/alert-pep/risks?page=2",
            )
        },
    )
    with pytest.raises(CAUnexpectedResponse):
        _orchestrator(bad_client).fetch_risks_paginated_for_alert("alert-pep")


def test_two_pass_relaxed_catches_canonical_match():
    data = _fixture("two_pass_strict_misses_relaxed_catches.json")
    client = _client_for_two_pass(data)

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("relaxed"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    matches = report["provider_specific"]["complyadvantage"]["matches"]
    canonical = [m for m in matches if m["profile_identifier"] == "prof-canonical"][0]
    assert canonical["surfaced_by_pass"] == "relaxed"
    assert "/v2/alerts/alert-relaxed-1/risks?page=1" in client.called_paths()
    assert "/v2/entity-screening/risks/risk-canonical" in client.called_paths()


def test_two_pass_uses_distinct_strict_and_relaxed_workflow_ids():
    data = _fixture("two_pass_strict_misses_relaxed_catches.json")
    client = _client_for_two_pass(data)

    _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("relaxed"),
        application_context=_context(data),
        monitoring_enabled=False,
        strict_workflow_id="workflow-strict",
        relaxed_workflow_id="workflow-relaxed",
    )

    calls = {call["customer"]["person"]["metadata"]["pass"]: call for call in client.post_calls}
    assert calls["strict"]["screening"] == {"workflow_id": "workflow-strict"}
    assert calls["relaxed"]["screening"] == {"workflow_id": "workflow-relaxed"}


def test_partial_deep_risk_failure_raises():
    data = _fixture("pep_canonical.json")
    routes = _routes_for_fixture(data)
    routes["/v2/entity-screening/risks/risk-pep"] = CAUnexpectedResponse("deep risk failed")
    client = PathStrictFakeCAClient(
        {"/v2/workflows/create-and-screen": _workflow_post(data["workflow"])},
        routes,
    )
    with pytest.raises(CAUnexpectedResponse):
        _orchestrator(client).screen_customer_two_pass(
            strict_customer=_customer("strict"),
            relaxed_customer=_customer("strict"),
            application_context=_context(),
            monitoring_enabled=False,
        )


def test_db_injected_monitoring_enabled_seeds_from_workflow_customer_identifier(caplog):
    data = _fixture("clean_baseline.json")
    db = object()
    with patch("screening_complyadvantage.orchestrator.seed_monitoring_subscription") as seed:
        with caplog.at_level(logging.INFO, logger="screening_complyadvantage.orchestrator"):
            report = _orchestrator(_client_for_single(data)).screen_customer_two_pass(
                strict_customer=_customer("strict"),
                relaxed_customer=_customer("strict"),
                application_context=_context(data),
                monitoring_enabled=True,
                db=db,
            )

    assert report["total_hits"] == 0
    seed.assert_called_once_with(db, "client-test", "app-clean", "cust-test", person_key=None)
    assert "ca_monitoring_subscription_seeded" in caplog.text


def test_db_absent_monitoring_enabled_warns_and_still_returns(caplog):
    data = _fixture("clean_baseline.json")
    with patch("screening_complyadvantage.orchestrator.seed_monitoring_subscription") as seed:
        with caplog.at_level(logging.WARNING, logger="screening_complyadvantage.orchestrator"):
            report = _orchestrator(_client_for_single(data)).screen_customer_two_pass(
                strict_customer=_customer("strict"),
                relaxed_customer=_customer("strict"),
                application_context=_context(data),
                monitoring_enabled=True,
                db=None,
            )

    assert report["total_hits"] == 0
    seed.assert_not_called()
    assert "db_handle_not_injected" in caplog.text


def test_monitoring_disabled_does_not_seed_even_with_db():
    with patch("screening_complyadvantage.orchestrator.seed_monitoring_subscription") as seed:
        _orchestrator(_client_for_single(_fixture("pep_canonical.json"))).screen_customer_two_pass(
            strict_customer=_customer("strict"),
            relaxed_customer=_customer("strict"),
            application_context=_context(),
            monitoring_enabled=False,
            db=object(),
        )

    seed.assert_not_called()
