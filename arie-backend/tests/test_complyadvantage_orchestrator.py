import json
import logging
import os
from copy import deepcopy
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from screening_complyadvantage.exceptions import CATimeout, CAUnexpectedResponse
from screening_complyadvantage.normalizer import ScreeningApplicationContext
from screening_complyadvantage.orchestrator import (
    ComplyAdvantageScreeningOrchestrator,
    _category_from_aml_type,
    _mark_report_pending_after_timeout,
    _normalise_risk_as_alert,
)


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


def _orchestrator(
    client,
    *,
    clock=lambda: 0,
    sleep_fn=lambda _: None,
    poll_timeout_seconds=300,
    allow_pending_on_timeout=False,
):
    return ComplyAdvantageScreeningOrchestrator(
        client,
        poll_timeout_seconds=poll_timeout_seconds,
        clock=clock,
        sleep_fn=sleep_fn,
        allow_pending_on_timeout=allow_pending_on_timeout,
    )


def test_create_and_screen_accepts_workflow_handle_only_response():
    data = _fixture("pep_canonical.json")
    client = _client_for_single(data)
    orch = _orchestrator(client)

    result = orch.create_and_screen(_customer("strict"), screening_configuration_identifier="cfg-123")

    assert result.workflow_instance_identifier == "wf-pep"
    assert result.customer_input.person.full_name == "Test strict"
    assert result.monitoring_enabled is True
    assert client.posts[0][1]["configuration"]["screening_configuration_identifier"] == "cfg-123"
    assert "screening" not in client.posts[0][1]

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
        "status": "NOT-STARTED",
        "step_details": {"case-creation": {"status": "IN-PROGRESS"}},
    }
    complete = {
        **in_progress,
        "status": "COMPLETED",
        "step_details": {
            "case-creation": {"status": "COMPLETED"},
            "customer-creation": {"status": "COMPLETED", "step_output": {"customer_identifier": "cust-test"}},
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


def test_submit_safe_poll_timeout_returns_pending_non_terminal_report():
    pending_workflow = {
        "workflow_type": "screening",
        "status": "IN-PROGRESS",
        "steps": ["initial_screening"],
        "step_details": {
            "customer-creation": {
                "status": "COMPLETED",
                "step_output": {"customer_identifier": "cust-pending"},
            },
            "case-creation": {"status": "IN-PROGRESS"},
        },
    }
    data = {
        "strict_workflow": {
            **pending_workflow,
            "workflow_instance_identifier": "wf-pending-strict",
        },
        "relaxed_workflow": {
            **pending_workflow,
            "workflow_instance_identifier": "wf-pending-relaxed",
        },
        "strict_alerts_risks": {},
        "relaxed_alerts_risks": {},
        "strict_deep_risks": {},
        "relaxed_deep_risks": {},
    }
    client = _client_for_two_pass(data)

    report = _orchestrator(
        client,
        poll_timeout_seconds=0,
        allow_pending_on_timeout=True,
    ).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("relaxed"),
        application_context=_context(),
        monitoring_enabled=False,
    )

    assert report["any_non_terminal_subject"] is True
    assert report["company_screening_state"] == "pending_provider"
    assert "complyadvantage_workflow_pending" in report["degraded_sources"]
    director = report["director_screenings"][0]
    assert director["screening_state"] == "pending_provider"
    assert director["screening"]["api_status"] == "pending"
    assert report["provider_specific"]["complyadvantage"]["pending_timeout"] is True


def test_pending_timeout_preserves_completed_company_match():
    workflow = type("Workflow", (), {"workflow_instance_identifier": "wf-completed"})()
    pass_result = type("PassResult", (), {"workflow": workflow})()
    report = {
        "company_screening": {
            "matched": True,
            "results": [{"name": "Known Match"}],
        },
        "provider_specific": {"complyadvantage": {}},
        "degraded_sources": [],
        "overall_flags": [],
    }

    _mark_report_pending_after_timeout(
        report,
        strict=pass_result,
        relaxed=pass_result,
    )

    assert report["company_screening"]["matched"] is True
    assert report["company_screening"]["results"] == [{"name": "Known Match"}]
    assert report["company_screening"]["screening_state"] == "pending_provider"


def test_polling_accepts_not_started_step_statuses_seen_in_ca_sandbox():
    raw = {
        "workflow_instance_identifier": "wf-not-started",
        "workflow_type": "screening",
        "status": "IN-PROGRESS",
        "step_details": {
            "initial-risk-scoring": {"status": "NOT-STARTED"},
            "customer-screening": {"status": "NOT-STARTED"},
            "alerting": {"status": "NOT-STARTED"},
            "case-creation": {"status": "NOT-STARTED"},
        },
    }
    client = PathStrictFakeCAClient({}, {"/v2/workflows/wf-not-started": raw})

    with pytest.raises(CATimeout):
        ComplyAdvantageScreeningOrchestrator(
            client,
            poll_timeout_seconds=0,
            clock=lambda: 0,
            sleep_fn=lambda _: None,
        ).poll_workflow_until_complete("wf-not-started")


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


def test_case_creation_extracts_alerts_from_alerting_step_output():
    data = deepcopy(_fixture("pep_canonical.json"))
    alert = data["workflow"].pop("alerts")[0]
    data["workflow"]["step_details"]["alerting"] = {
        "status": "COMPLETED",
        "step_output": {"alerts": [alert]},
    }
    client = _client_for_single(data)

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("strict"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    assert "/v2/alerts/alert-pep/risks?page=1" in client.called_paths()
    assert report["total_hits"] == 1
    assert report["any_pep_hits"] is True


def test_case_creation_maps_mesh_profile_risk_indicators_to_pep_hit():
    data = deepcopy(_fixture("pep_canonical.json"))
    alert = data["workflow"].pop("alerts")[0]
    data["workflow"]["step_details"]["alerting"] = {
        "status": "COMPLETED",
        "step_output": {"alerts": [alert]},
    }
    mesh_risk = {
        "identifier": "risk-mesh-pep",
        "type": "ENTITY_SCREENING",
        "decision": "NOT_REVIEWED",
        "detail": {
            "profile": {
                "identifier": "profile-mesh-pep",
                "matching_name": "pravind jugnauth",
                "match_score": 0.7,
                "match_types": ["exact_match"],
                "risk_indicators": {
                    "aml_types": ["pep-class-1"],
                    "peps": [{
                        "aml_types": ["pep-class-1"],
                        "active_start_dates": ["2003-10-30"],
                        "country_codes": ["MU"],
                        "fields": [
                            {"tag": "political_position", "value": "Leader"},
                            {"tag": "political_region", "value": "Mauritius"},
                        ],
                    }],
                    "media": [],
                    "lists": [],
                },
            },
        },
    }
    data["alerts_risks"] = {"alert-pep": [mesh_risk]}
    data["deep_risks"] = {"risk-mesh-pep": mesh_risk}
    client = _client_for_single(data)

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("strict"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    assert report["total_hits"] == 1
    assert report["any_pep_hits"] is True
    assert report["overall_flags"] == ["ComplyAdvantage PEP hit: pravind jugnauth"]
    assert report["director_screenings"][0]["has_pep_hit"] is True
    assert report["director_screenings"][0]["pep_classes"] == ["PEP_CLASS_1"]
    screening = report["director_screenings"][0]["screening"]
    result = screening["results"][0]
    assert result["name"] == "pravind jugnauth"
    assert result["match_score"] is None
    assert result["provider_match_score_raw"] == 0.7
    assert result["provider_match_types"] == ["exact_match"]
    assert result["provider_aml_types_raw"] == ["pep-class-1"]
    provider_match = report["provider_specific"]["complyadvantage"]["matches"][0]
    assert provider_match["indicators"][0]["taxonomy_key"] == "r_pep_class_1"
    assert provider_match["indicators"][0]["value"]["class"] == "PEP_CLASS_1"
    assert provider_match["provider_match_score_raw"] == 0.7
    assert provider_match["provider_match_types"] == ["exact_match"]
    assert provider_match["provider_aml_types_raw"] == ["pep-class-1"]


def test_case_creation_maps_mesh_list_risk_indicators_to_pep_hit():
    data = deepcopy(_fixture("pep_canonical.json"))
    alert = data["workflow"].pop("alerts")[0]
    data["workflow"]["step_details"]["alerting"] = {
        "status": "COMPLETED",
        "step_output": {"alerts": [alert]},
    }
    mesh_risk = {
        "identifier": "risk-mesh-pep-list",
        "type": "ENTITY_SCREENING",
        "decision": "NOT_REVIEWED",
        "detail": {
            "profile": {
                "identifier": "profile-mesh-pep-list",
                "person": {"names": {"values": [{"name": "Pravin Jugnauth"}]}},
                "match_details": {},
                "risk_types": ["r_pep_class_1"],
                "risk_indicators": [{
                    "risk_types": [{
                        "key": "r_pep_class_1",
                        "name": "PEP class 1",
                        "taxonomy": "r_political_exposure.r_politically_exposed_persons.r_pep_class_1",
                    }],
                    "pep_indicators": {
                        "values": [{
                            "source_identifier": "S:7VP70D",
                            "source_name": "Mauritius political leadership",
                            "issuing_jurisdictions": ["MU"],
                            "url": "https://example.test/pep-source",
                            "class": "PEP_CLASS_1",
                            "level": "NATIONAL",
                            "scope_of_influence": "POLITICAL_PARTIES_BOARD_MEMBERS",
                            "institution_type": "POLITICAL_PARTY",
                            "political_positions": ["Leader"],
                            "political_position_type": "SENIOR_POLITICAL_PARTY_OFFICIAL",
                            "political_parties": ["Militant Socialist Movement"],
                            "active_start_date": {"date": "2003-10-30T00:00:00Z"},
                            "active_end_date": None,
                            "locations": [{"full_address": "Mauritius", "country": "MU"}],
                        }]
                    },
                }],
            },
        },
    }
    data["alerts_risks"] = {"alert-pep": [mesh_risk]}
    data["deep_risks"] = {"risk-mesh-pep-list": mesh_risk}
    client = _client_for_single(data)

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("strict"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    assert report["total_hits"] == 1
    assert report["any_pep_hits"] is True
    assert report["overall_flags"] == ["ComplyAdvantage PEP hit: risk-mesh-pep-list"]
    assert report["director_screenings"][0]["has_pep_hit"] is True
    assert report["director_screenings"][0]["pep_classes"] == ["PEP_CLASS_1"]
    screening = report["director_screenings"][0]["screening"]
    assert screening["matched"] is True
    assert screening["results"][0]["is_pep"] is True
    assert screening["results"][0]["is_sanctioned"] is False
    assert screening["results"][0]["pep_classes"] == ["PEP_CLASS_1"]
    provider_match = report["provider_specific"]["complyadvantage"]["matches"][0]
    assert provider_match["indicators"][0]["taxonomy_key"] == "r_pep_class_1"
    assert provider_match["indicators"][0]["value"]["class"] == "PEP_CLASS_1"
    assert provider_match["indicators"][0]["value"]["position"] == "Leader"


@pytest.mark.parametrize(
    ("aml_type", "expected"),
    [
        ("sanction", "sanctions"),
        ("sanctions", "sanctions"),
        ("pep", "pep"),
        ("pep-class-1", "pep"),
        ("pep-class-4", "pep"),
        ("politically-exposed", "pep"),
        ("adverse-media-v2-regulatory", "adverse_media"),
        ("adverse-media-financial-crime", "adverse_media"),
        ("adverse-media-terrorism", "adverse_media"),
        ("adverse-media-general", "adverse_media"),
        ("adverse_media", "adverse_media"),
        ("negative-news", "adverse_media"),
        ("warning", "watchlist"),
        ("fitness-probity", "watchlist"),
        ("watchlist", "watchlist"),
        ("unknown-taxonomy-key", "other"),
    ],
)
def test_mesh_alert_aml_type_category_mapping(aml_type, expected):
    assert _category_from_aml_type(aml_type) == expected


def test_alert_risk_profile_name_fallbacks_from_live_shape():
    matching = _normalise_risk_as_alert(
        "risk-name",
        {"detail": {"profile": {"identifier": "profile-name", "matching_name": "Matched Provider Name"}}},
    )["profile"]
    assert matching["matching_name"] == "Matched Provider Name"
    assert matching["company"]["names"]["values"][0]["name"] == "Matched Provider Name"

    company = _normalise_risk_as_alert(
        "risk-company",
        {"detail": {"profile": {"identifier": "profile-company", "company": {"names": ["Company Alias Ltd"]}}}},
    )["profile"]
    assert company["company"]["names"]["values"][0]["name"] == "Company Alias Ltd"

    person = _normalise_risk_as_alert(
        "risk-person",
        {"detail": {"profile": {"identifier": "profile-person", "person": {"names": [{"name": "Person Alias"}]}}}},
    )["profile"]
    assert person["person"]["names"]["values"][0]["name"] == "Person Alias"

    fallback = _normalise_risk_as_alert(
        "risk-no-name",
        {"detail": {"profile": {"identifier": "profile-no-name"}}},
    )["profile"]
    assert fallback["identifier"] == "profile-no-name"
    assert fallback["company"]["names"]["values"] == []
    assert fallback["matching_name"] is None


def test_case_creation_captures_mesh_media_evidence_and_raw_score_without_percentage():
    data = deepcopy(_fixture("pep_canonical.json"))
    alert = data["workflow"].pop("alerts")[0]
    data["workflow"]["step_details"]["alerting"] = {
        "status": "COMPLETED",
        "step_output": {"alerts": [alert]},
    }
    mesh_risk = {
        "identifier": "risk-mesh-media",
        "type": "ENTITY_SCREENING",
        "decision": "NOT_REVIEWED",
        "detail": {
            "profile": {
                "identifier": "profile-mesh-media",
                "matching_name": "Adverse Media Subject",
                "match_score": 1.7,
                "match_types": ["exact_match"],
                "risk_indicators": {
                    "aml_types": ["adverse-media-v2-regulatory"],
                    "media": [{
                        "url": "https://news.example.test/adverse-media",
                        "title": "Adverse media headline",
                        "snippet": "Stored provider snippet.",
                        "publishing_date": "2024-01-02",
                        "identifier": "media-evidence-1",
                    }],
                    "lists": [],
                    "peps": [],
                },
            },
        },
    }
    data["alerts_risks"] = {"alert-pep": [mesh_risk]}
    data["deep_risks"] = {"risk-mesh-media": mesh_risk}
    client = _client_for_single(data)

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("strict"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    assert report["total_hits"] == 1
    assert report["has_adverse_media_hit"] is True
    result = report["director_screenings"][0]["screening"]["results"][0]
    assert result["name"] == "Adverse Media Subject"
    assert result["match_score"] is None
    assert result["provider_match_score_raw"] == 1.7
    assert result["provider_match_types"] == ["exact_match"]
    assert result["provider_aml_types_raw"] == ["adverse-media-v2-regulatory"]
    assert result["provider_media_evidence"] == [{
        "url": "https://news.example.test/adverse-media",
        "title": "Adverse media headline",
        "snippet": "Stored provider snippet.",
        "publishing_date": "2024-01-02",
        "identifier": "media-evidence-1",
    }]
    assert result["source_url"] == "https://news.example.test/adverse-media"
    assert result["media_title"] == "Adverse media headline"
    assert result["media_snippet"] == "Stored provider snippet."
    assert result["publication_date"] == "2024-01-02"
    assert result["provider_media_identifier"] == "media-evidence-1"
    provider_match = report["provider_specific"]["complyadvantage"]["matches"][0]
    assert provider_match["provider_match_score_raw"] == 1.7
    assert provider_match["provider_match_types"] == ["exact_match"]
    assert provider_match["provider_aml_types_raw"] == ["adverse-media-v2-regulatory"]
    assert provider_match["provider_media_evidence"][0]["url"] == "https://news.example.test/adverse-media"


def test_case_creation_tolerates_malformed_mesh_profile_without_crashing():
    data = deepcopy(_fixture("pep_canonical.json"))
    alert = data["workflow"].pop("alerts")[0]
    data["workflow"]["step_details"]["alerting"] = {
        "status": "COMPLETED",
        "step_output": {"alerts": [alert]},
    }
    mesh_risk = {
        "identifier": "risk-mesh-malformed",
        "type": "ENTITY_SCREENING",
        "decision": "NOT_REVIEWED",
        "detail": {
            "profile": {
                "identifier": "profile-mesh-malformed",
                "company": {"names": "not-a-list"},
                "risk_indicators": "not-a-dict",
                "match_score": "not-a-number",
                "match_types": "not-a-list",
            },
        },
    }
    data["alerts_risks"] = {"alert-pep": [mesh_risk]}
    data["deep_risks"] = {"risk-mesh-malformed": mesh_risk}
    client = _client_for_single(data)

    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("strict"),
        application_context=_context(data),
        monitoring_enabled=False,
    )

    assert report["total_hits"] == 1
    result = report["director_screenings"][0]["screening"]["results"][0]
    assert result["name"] == "profile-mesh-malformed"
    assert result["match_score"] is None
    assert result["match_category"] == "other"
    assert "provider_match_score_raw" not in result


def test_alert_risk_profile_missing_or_non_dict_profile_does_not_raise():
    missing = _normalise_risk_as_alert("risk-missing", {"detail": {}})
    assert missing["identifier"] == "risk-missing"
    assert "profile" not in missing

    malformed = _normalise_risk_as_alert("risk-malformed", {"detail": {"profile": "not-a-dict"}})
    assert malformed["identifier"] == "risk-malformed"
    assert "profile" not in malformed


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


def test_two_pass_uses_single_screening_configuration_for_both_passes():
    data = _fixture("two_pass_strict_misses_relaxed_catches.json")
    client = _client_for_two_pass(data)

    _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("relaxed"),
        application_context=_context(data),
        monitoring_enabled=False,
        screening_configuration_identifier="cfg-123",
    )

    assert len(client.post_calls) == 2
    assert all(
        call["configuration"]["screening_configuration_identifier"] == "cfg-123"
        for call in client.post_calls
    )
    assert all("screening" not in call for call in client.post_calls)


def test_two_pass_uses_distinct_external_identifiers_per_pass():
    data = _fixture("two_pass_strict_misses_relaxed_catches.json")
    client = _client_for_two_pass(data)

    _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("relaxed"),
        application_context=_context(data),
        monitoring_enabled=False,
        strict_external_identifier="app-1:director:key-d-1:strict",
        relaxed_external_identifier="app-1:director:key-d-1:relaxed",
    )

    identifiers_by_pass = {
        call["customer"]["person"]["metadata"]["pass"]: call["customer"]["external_identifier"]
        for call in client.post_calls
    }
    references_by_pass = {
        call["customer"]["person"]["metadata"]["pass"]: call["customer"]["reference"]
        for call in client.post_calls
    }
    assert identifiers_by_pass == {
        "strict": "app-1:director:key-d-1:strict",
        "relaxed": "app-1:director:key-d-1:relaxed",
    }
    assert references_by_pass == identifiers_by_pass


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


# ── CA workflow ERRORED handling (regression: ERRORED status used to 500 the
#    screening run via a CAWorkflowResponse enum ValidationError) ──

def test_poll_returns_errored_result_for_errored_workflow_status():
    errored = {
        "workflow_instance_identifier": "wf-err",
        "workflow_type": "screening",
        "status": "ERRORED",
        "step_details": {"case-creation": {"status": "ERRORED"}},
    }
    client = PathStrictFakeCAClient({}, {"/v2/workflows/wf-err": errored})
    result = ComplyAdvantageScreeningOrchestrator(
        client, clock=lambda: 0, sleep_fn=lambda _: None
    ).poll_workflow_until_complete("wf-err")
    assert result.errored is True
    assert result.timed_out is False


def test_unknown_workflow_status_degrades_to_errored_without_crashing():
    # A CA status we don't model must not raise — it degrades to ERRORED.
    unknown = {
        "workflow_instance_identifier": "wf-unknown",
        "workflow_type": "screening",
        "status": "SOME-BRAND-NEW-CA-STATUS",
        "step_details": {},
    }
    client = PathStrictFakeCAClient({}, {"/v2/workflows/wf-unknown": unknown})
    result = ComplyAdvantageScreeningOrchestrator(
        client, clock=lambda: 0, sleep_fn=lambda _: None
    ).poll_workflow_until_complete("wf-unknown")
    assert result.errored is True


def test_errored_workflow_marks_report_degraded_and_does_not_raise():
    errored_workflow = {
        "workflow_type": "screening",
        "status": "ERRORED",
        "steps": ["initial_screening"],
        "step_details": {"case-creation": {"status": "ERRORED"}},
    }
    data = {
        "strict_workflow": {**errored_workflow, "workflow_instance_identifier": "wf-err-strict"},
        "relaxed_workflow": {**errored_workflow, "workflow_instance_identifier": "wf-err-relaxed"},
        "strict_alerts_risks": {},
        "relaxed_alerts_risks": {},
        "strict_deep_risks": {},
        "relaxed_deep_risks": {},
    }
    client = _client_for_two_pass(data)

    # Previously this raised a CAWorkflowResponse ValidationError -> 500. It must
    # now return a degraded, non-approvable report instead.
    report = _orchestrator(client).screen_customer_two_pass(
        strict_customer=_customer("strict"),
        relaxed_customer=_customer("relaxed"),
        application_context=_context(),
        monitoring_enabled=False,
    )

    assert report["any_non_terminal_subject"] is True
    assert report["company_screening_state"] == "pending_provider"
    assert "complyadvantage_workflow_errored" in report["degraded_sources"]
    assert report["provider_specific"]["complyadvantage"]["workflow_errored"] is True
    assert any("errored" in str(flag).lower() for flag in report["overall_flags"])
    director = report["director_screenings"][0]
    assert director["screening_state"] == "pending_provider"
    assert director["screening"]["pending_reason"] == "workflow_errored"
