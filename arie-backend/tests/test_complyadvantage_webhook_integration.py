import json
import os
import sqlite3

import pytest

from screening_complyadvantage.models.webhooks import CACaseAlertListUpdatedWebhook
from screening_complyadvantage.webhook_storage import process_complyadvantage_webhook
from tests.test_complyadvantage_webhook_storage import NoCloseDB, _db

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "complyadvantage")


class FakeClient:
    def __init__(self, routes):
        self.config = type("Config", (), {"api_base_url": "https://api.example.test"})()
        self.routes = routes

    def get(self, path, params=None):
        return self.routes[path]


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_fixture_driven_end_to_end_dual_write(monkeypatch):
    conn = _db()
    conn.execute("UPDATE screening_monitoring_subscriptions SET customer_identifier = ?", ("cust-test",))
    conn.commit()
    data = _fixture("sanctions_canonical.json")
    webhook = _fixture("webhook_case_alert_list_updated.json")
    webhook["case_identifier"] = "case-san"
    envelope = CACaseAlertListUpdatedWebhook.model_validate(webhook)
    routes = {
        "/v2/workflows/case-san": data["workflow"],
        "/v2/alerts/alert-san/risks?page=1": {"values": data["alerts_risks"]["alert-san"], "pagination": {"next": None}},
        "/v2/entity-screening/risks/risk-san": data["deep_risks"]["risk-san"],
    }
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")

    result = await process_complyadvantage_webhook(
        envelope,
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: FakeClient(routes),
    )

    assert result["status"] == "processed"
    assert conn.execute("SELECT COUNT(*) FROM screening_reports_normalized").fetchone()[0] == 1
    alert = conn.execute("SELECT alert_type, severity, source_reference FROM monitoring_alerts").fetchone()
    assert alert["alert_type"] == "sanctions"
    assert alert["severity"] == "critical"
    assert json.loads(alert["source_reference"])["case_identifier"] == "case-san"
