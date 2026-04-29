import json
import logging
import os
from dataclasses import dataclass

import pytest
from screening_complyadvantage.models.webhooks import CACaseAlertListUpdatedWebhook, CACaseCreatedWebhook
from screening_complyadvantage.normalizer import ScreeningApplicationContext
from screening_complyadvantage.webhook_fetch import (
    WebhookEnvelopeError,
    extract_case_identifier,
    fetch_case_for_webhook,
    fetch_webhook_single_pass,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "complyadvantage")


@dataclass
class FakeConfig:
    api_base_url: str = "https://api.example.test"


class FakeClient:
    def __init__(self, routes):
        self.config = FakeConfig()
        self.routes = routes
        self.gets = []

    def get(self, path, params=None):
        self.gets.append(path)
        if path not in self.routes:
            raise AssertionError(f"unexpected path {path}")
        response = self.routes[path]
        if callable(response):
            response = response(path, self)
        if isinstance(response, Exception):
            raise response
        return response


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


CASE_ID = "12345678-1234-1234-1234-123456789abc"


def _case(alerts=None):
    return {
        "identifier": CASE_ID,
        "case_type": "screening",
        "case_state": "open",
        "case_stage": {"identifier": "stage-review", "display_name": "Review"},
        "alerts": alerts if alerts is not None else [],
    }


def _context():
    return ScreeningApplicationContext.model_validate(_fixture("pep_canonical.json")["context"])


def _listing(risk_id="risk-pep"):
    return {
        "identifier": risk_id,
        "added_mentions": [{"source": "synthetic", "snippet": "listing-only mention"}],
        "match_score": 0.91,
        "matching_name": "Synthetic Listing Subject",
        "match_types": ["name_exact"],
        "media": {"snippets": [{"text": "listing media snippet"}]},
        "profile": {
            "identifier": "profile-" + risk_id,
            "entity_type": "person",
            "person": {"names": {"values": [{"name": "Synthetic Listing Subject", "type": "PRIMARY"}]}},
            "match_details": {"match_score": 0.91, "matched_name": "Synthetic Listing Subject"},
            "risk_types": ["r_pep_class_2"],
            "risk_indicators": [],
        },
    }


def _deep(risk_id="risk-pep"):
    return {
        "match_details": {
            "matching_reasons": {
                "influencing_factors": [{"type": "synthetic_factor", "value": risk_id}]
            }
        },
        "values": [
            {
                "risk_type": {"key": "r_pep_class_2", "label": "PEP class 2", "name": "PEP class 2"},
                "indicators": [
                    {
                        "value": {
                            "class": "PEP_CLASS_2",
                            "position": "Synthetic Role",
                            "country": "XX",
                            "matching_reasons": {
                                "influencing_factors": [{"type": "synthetic_indicator_factor"}]
                            },
                        }
                    }
                ],
            }
        ],
    }


def test_extract_case_identifier_happy_path():
    assert extract_case_identifier({"case_identifier": CASE_ID}) == CASE_ID


def test_extract_case_identifier_missing_field_raises():
    with pytest.raises(WebhookEnvelopeError, match="missing"):
        extract_case_identifier({"data": {"case_identifier": CASE_ID}})


def test_extract_case_identifier_non_string_raises():
    with pytest.raises(WebhookEnvelopeError, match="string"):
        extract_case_identifier({"case_identifier": 123})


def test_extract_case_identifier_wrong_length_raises():
    with pytest.raises(WebhookEnvelopeError, match="36"):
        extract_case_identifier({"case_identifier": "case-pep"})


def test_fetch_case_for_webhook_uses_exact_get_case_path():
    client = FakeClient({f"/v2/cases/{CASE_ID}": _case()})

    assert fetch_case_for_webhook(CASE_ID, client)["identifier"] == CASE_ID
    assert client.gets == [f"/v2/cases/{CASE_ID}"]


def test_case_created_fetch_webhook_single_pass_uses_case_anchor_and_no_workflow_path():
    webhook = _fixture("webhook_case_created.json")
    envelope = CACaseCreatedWebhook.model_validate(webhook)
    case_id = webhook["case_identifier"]
    routes = {
        f"/v2/cases/{case_id}": _case(),
        f"/v2/workflows/{case_id}": AssertionError("workflow path must not be called"),
    }
    client = FakeClient(routes)

    report = fetch_webhook_single_pass(client, envelope, _context())

    assert client.gets == [f"/v2/cases/{case_id}"]
    assert report["total_hits"] == 0
    assert report["provider_specific"]["complyadvantage"]["alert_risk_listings"] == {}


def test_case_alert_list_updated_fetches_listing_then_deep_without_alert_or_workflow_paths():
    webhook = _fixture("webhook_case_alert_list_updated.json")
    envelope = CACaseAlertListUpdatedWebhook.model_validate(webhook)
    case_id = webhook["case_identifier"]
    routes = {
        f"/v2/cases/{case_id}": _case(),
        "/v2/alerts/alert-san/risks": {"values": [_listing("risk-san")], "pagination": {"next": None}},
        "/v2/entity-screening/risks/risk-san": _deep("risk-san"),
        f"/v2/workflows/{case_id}": AssertionError("workflow path must not be called"),
        "/v2/alerts/alert-san": AssertionError("alert detail path must not be called"),
    }
    client = FakeClient(routes)

    report = fetch_webhook_single_pass(client, envelope, _context())

    assert client.gets == [
        f"/v2/cases/{case_id}",
        "/v2/alerts/alert-san/risks",
        "/v2/entity-screening/risks/risk-san",
    ]
    provider = report["provider_specific"]["complyadvantage"]
    assert provider["alert_risk_listings"]["risk-san"]["added_mentions"][0]["snippet"] == "listing-only mention"
    assert provider["alert_risk_listings"]["risk-san"]["media"]["snippets"][0]["text"] == "listing media snippet"
    match = provider["matches"][0]
    assert (
        match["risk_detail"]["match_details"]["matching_reasons"]["influencing_factors"][0]["value"]
        == "risk-san"
    )
    assert match["risk_detail"]["alert_risk_listing"]["match_types"] == ["name_exact"]


def test_fetch_webhook_single_pass_accepts_early_case_with_no_alerts():
    webhook = _fixture("webhook_case_created.json")
    envelope = CACaseCreatedWebhook.model_validate(webhook)
    case_id = webhook["case_identifier"]
    client = FakeClient({f"/v2/cases/{case_id}": _case(alerts=[])})

    report = fetch_webhook_single_pass(client, envelope, _context())

    assert client.gets == [f"/v2/cases/{case_id}"]
    assert report["total_hits"] == 0
    assert report["provider_specific"]["complyadvantage"]["workflows"]["strict"]["alerts"] == []


def test_alert_listing_page_cap_stops_at_50_pages_and_logs(caplog):
    webhook = _fixture("webhook_case_alert_list_updated.json")
    envelope = CACaseAlertListUpdatedWebhook.model_validate(webhook)
    case_id = webhook["case_identifier"]

    def infinite_page(path, client):
        page = 1
        if "page=" in path:
            page = int(path.rsplit("page=", 1)[1])
        return {
            "values": [],
            "pagination": {"next": f"/v2/alerts/alert-san/risks?page={page + 1}"},
        }

    client = FakeClient({
        f"/v2/cases/{case_id}": _case(),
        "/v2/alerts/alert-san/risks": infinite_page,
        **{f"/v2/alerts/alert-san/risks?page={page}": infinite_page for page in range(2, 51)},
    })

    with caplog.at_level(logging.WARNING, logger="screening_complyadvantage.webhook_fetch"):
        report = fetch_webhook_single_pass(client, envelope, _context())

    assert client.gets[0] == f"/v2/cases/{case_id}"
    assert len([path for path in client.gets if path.startswith("/v2/alerts/alert-san/risks")]) == 50
    assert report["provider_specific"]["complyadvantage"]["webhook_enrichment_truncated"]["reason"] == "page_cap"
    assert "ca_webhook_fetch_page_cap_reached" in caplog.text
    assert "alert_id=alert-san" in caplog.text


def test_api_call_budget_stops_gracefully_without_raising_and_logs(caplog):
    webhook = _fixture("webhook_case_alert_list_updated.json")
    webhook["alert_identifiers"] = [f"alert-{index}" for index in range(5)]
    envelope = CACaseAlertListUpdatedWebhook.model_validate(webhook)
    case_id = webhook["case_identifier"]
    routes = {f"/v2/cases/{case_id}": _case()}
    for alert_index in range(5):
        alert_id = f"alert-{alert_index}"
        routes[f"/v2/alerts/{alert_id}/risks"] = {
            "values": [_listing(f"risk-{alert_index}-{risk_index}") for risk_index in range(100)],
            "pagination": {"next": None},
        }
        for risk_index in range(100):
            routes[f"/v2/entity-screening/risks/risk-{alert_index}-{risk_index}"] = _deep(
                f"risk-{alert_index}-{risk_index}"
            )
    client = FakeClient(routes)

    with caplog.at_level(logging.ERROR, logger="screening_complyadvantage.webhook_fetch"):
        report = fetch_webhook_single_pass(client, envelope, _context())

    assert len(client.gets) == 200
    assert report["provider_specific"]["complyadvantage"]["webhook_enrichment_truncated"]["reason"] == "api_call_budget"
    assert "ca_webhook_fetch_api_call_budget_exceeded" in caplog.text


def test_nested_deep_pagination_warns_without_fetching_nested_pages(caplog):
    webhook = _fixture("webhook_case_alert_list_updated.json")
    envelope = CACaseAlertListUpdatedWebhook.model_validate(webhook)
    case_id = webhook["case_identifier"]
    deep = _deep("risk-san")
    deep["profile"] = {
        "person": {
            "passport_numbers": {
                "values": [{"number": "SYNTHETIC-PASSPORT"}],
                "pagination": {"next": "/v2/entity-screening/risks/risk-san/profile/passport_numbers?page=2"},
            }
        }
    }
    client = FakeClient({
        f"/v2/cases/{case_id}": _case(),
        "/v2/alerts/alert-san/risks": {"values": [_listing("risk-san")], "pagination": {"next": None}},
        "/v2/entity-screening/risks/risk-san": deep,
    })

    with caplog.at_level(logging.WARNING, logger="screening_complyadvantage.webhook_fetch"):
        fetch_webhook_single_pass(client, envelope, _context())

    assert "/v2/entity-screening/risks/risk-san/profile/passport_numbers?page=2" not in client.gets
    assert "ca_webhook_nested_pagination" in caplog.text
    assert "risk_id=risk-san" in caplog.text
    assert "path=detail.profile.person.passport_numbers" in caplog.text
