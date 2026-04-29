import json
import os
from dataclasses import dataclass

from screening_complyadvantage.models.webhooks import CACaseAlertListUpdatedWebhook
from screening_complyadvantage.normalizer import ScreeningApplicationContext
from screening_complyadvantage.webhook_fetch import fetch_webhook_single_pass

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
        return self.routes[path]


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def test_fetch_webhook_single_pass_uses_shared_three_layer_paths():
    data = _fixture("pep_canonical.json")
    webhook = _fixture("webhook_case_alert_list_updated.json")
    webhook["case_identifier"] = "case-pep"
    webhook["alert_identifiers"] = ["alert-pep"]
    envelope = CACaseAlertListUpdatedWebhook.model_validate(webhook)
    routes = {
        "/v2/workflows/case-pep": data["workflow"],
        "/v2/alerts/alert-pep/risks?page=1": {"values": data["alerts_risks"]["alert-pep"], "pagination": {"next": None}},
        "/v2/entity-screening/risks/risk-pep": data["deep_risks"]["risk-pep"],
    }
    context = ScreeningApplicationContext.model_validate(data["context"])

    report = fetch_webhook_single_pass(FakeClient(routes), envelope, context)

    assert report["provider"] == "complyadvantage"
    assert report["total_hits"] == 1
    assert report["provider_specific"]["complyadvantage"]["resnapshot"]["source_case_identifier"] == "case-pep"
