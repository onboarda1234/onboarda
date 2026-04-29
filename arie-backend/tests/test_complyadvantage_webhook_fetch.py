import json
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


def test_fetch_webhook_single_pass_uses_case_anchor_and_no_workflow_path():
    data = _fixture("pep_canonical.json")
    webhook = _fixture("webhook_case_alert_list_updated.json")
    webhook["case_identifier"] = CASE_ID
    webhook["alert_identifiers"] = ["alert-pep"]
    envelope = CACaseAlertListUpdatedWebhook.model_validate(webhook)
    routes = {
        f"/v2/cases/{CASE_ID}": _case(),
        f"/v2/workflows/{CASE_ID}": AssertionError("workflow path must not be called"),
    }
    context = ScreeningApplicationContext.model_validate(data["context"])
    client = FakeClient(routes)

    report = fetch_webhook_single_pass(client, envelope, context)

    assert client.gets == [f"/v2/cases/{CASE_ID}"]
    assert report["total_hits"] == 0
    assert (
        report["provider_specific"]["complyadvantage"]["resnapshot"]["source_case_identifier"]
        == CASE_ID
    )
    assert report["provider_specific"]["complyadvantage"]["workflows"]["strict"]["alerts"] == [
        {"identifier": "alert-pep"}
    ]


def test_fetch_webhook_single_pass_accepts_early_case_with_no_alerts():
    data = _fixture("pep_canonical.json")
    webhook = _fixture("webhook_case_created.json")
    webhook["case_identifier"] = CASE_ID
    envelope = CACaseCreatedWebhook.model_validate(webhook)
    client = FakeClient({f"/v2/cases/{CASE_ID}": _case(alerts=[])})
    context = ScreeningApplicationContext.model_validate(data["context"])

    report = fetch_webhook_single_pass(client, envelope, context)

    assert client.gets == [f"/v2/cases/{CASE_ID}"]
    assert report["total_hits"] == 0
    assert report["provider_specific"]["complyadvantage"]["workflows"]["strict"]["alerts"] == []
