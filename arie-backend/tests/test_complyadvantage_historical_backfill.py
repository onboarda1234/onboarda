import asyncio
import sqlite3
from unittest.mock import MagicMock

import pytest

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_complyadvantage import historical_backfill as hb
from screening_complyadvantage.historical_backfill import (
    rerun_historical_backfill_for_customer,
    run_historical_backfill_for_subscription,
)
from screening_complyadvantage.subscriptions import seed_monitoring_subscription


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.gets = []

    def get(self, path, params=None):
        self.gets.append((path, params))
        key = (path, tuple(sorted((params or {}).items()))) if params else path
        if key not in self.routes:
            raise AssertionError(f"unexpected GET {path} params={params}")
        value = self.routes[key]
        if callable(value):
            return value(path, params, self)
        return value


class NoCloseDB:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args):
        return self.conn.execute(*args)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _reset_guard():
    hb._BACKFILL_SEMAPHORE = asyncio.Semaphore(hb._MAX_CONCURRENT_BACKFILLS)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE screening_monitoring_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            person_key TEXT,
            customer_identifier TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'migration_scaffolding',
            status TEXT NOT NULL DEFAULT 'active',
            monitoring_event_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX uq_screening_monitoring_subs_customer
            ON screening_monitoring_subscriptions(client_id, provider, customer_identifier);
        CREATE TABLE monitoring_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT,
            case_identifier TEXT,
            discovered_via TEXT NOT NULL DEFAULT 'webhook_live'
                CHECK(discovered_via IN ('webhook_live','webhook_backfill','manual_backfill')),
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            backfill_run_id TEXT,
            application_id TEXT,
            client_name TEXT,
            alert_type TEXT,
            severity TEXT,
            detected_by TEXT,
            summary TEXT,
            source_reference TEXT,
            status TEXT DEFAULT 'open'
        );
        CREATE UNIQUE INDEX uq_monitoring_alerts_provider_case
            ON monitoring_alerts(provider, case_identifier)
            WHERE provider IS NOT NULL AND case_identifier IS NOT NULL;
        CREATE TABLE screening_reports_normalized (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'sumsub',
            normalized_version TEXT NOT NULL DEFAULT '1.0',
            source_screening_report_hash TEXT,
            normalized_report_json TEXT,
            normalization_status TEXT NOT NULL DEFAULT 'success',
            normalization_error TEXT,
            is_authoritative INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'migration_scaffolding',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX uq_screening_normalized_app_provider_hash
            ON screening_reports_normalized(application_id, provider, source_screening_report_hash);
        """
    )
    return conn


def _case(case_id="case-1", customer="cust-1"):
    return {"identifier": case_id, "customer_identifier": customer, "case_state": "open"}


def _alert_page(alert_id="alert-1", next_link=None):
    return {"alerts": [{"identifier": alert_id}], "next": next_link, "first": "", "prev": None, "self": "", "total_count": 1}


def _risk_page(risk_id="risk-1", next_link=None):
    return {
        "risks": [
            {
                "identifier": risk_id,
                "match_score": 0.91,
                "profile": {
                    "identifier": f"profile-{risk_id}",
                    "entity_type": "person",
                    "match_details": {"match_score": 0.91, "matched_name": "Synthetic Person"},
                    "risk_types": ["r_pep_class_2"],
                    "risk_indicators": [],
                    "person": {"names": {"values": [{"name": "Synthetic Person", "type": "PRIMARY"}]}},
                },
            }
        ],
        "next": next_link,
        "first": "",
        "prev": None,
        "self": "",
        "total_count": 1,
    }


def _deep():
    return {
        "values": [
            {
                "risk_type": {"key": "r_pep_class_2", "label": "PEP class 2", "name": "PEP class 2"},
                "indicators": [{"value": {"class": "PEP_CLASS_2", "position": "Synthetic Role", "country": "XX"}}],
            }
        ]
    }


def _routes(customer="cust-1", case_id="case-1"):
    return {
        ("/v2/cases", (("customer_identifier", customer),)): {"cases": [_case(case_id, customer)], "next": None},
        f"/v2/cases/{case_id}": _case(case_id, customer),
        f"/v2/cases/{case_id}/alerts": _alert_page(),
        "/v2/alerts/alert-1/risks": _risk_page(),
        "/v2/entity-screening/risks/risk-1": _deep(),
    }


@pytest.mark.asyncio
async def test_one_shot_backfill_runs_on_seeded_subscription_path(monkeypatch):
    conn = _db()
    scheduled = []

    def scheduler(**kwargs):
        scheduled.append(kwargs)

    seed_monitoring_subscription(
        conn,
        "client-1",
        "app-1",
        "cust-1",
        backfill_scheduler=scheduler,
    )

    assert scheduled == [{"application_id": "app-1", "client_id": "client-1", "customer_identifier": "cust-1"}]


@pytest.mark.asyncio
async def test_manual_rerun_helper_writes_manual_provenance(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: "sumsub")
    conn = _db()

    result = await rerun_historical_backfill_for_customer(
        db=conn,
        ca_client=FakeClient(_routes()),
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
        backfill_run_id="bf-manual",
    )

    row = conn.execute("SELECT discovered_via, backfill_run_id FROM monitoring_alerts").fetchone()
    assert result["status"] == "completed"
    assert row["discovered_via"] == "manual_backfill"
    assert row["backfill_run_id"] == "bf-manual"


@pytest.mark.asyncio
async def test_case_enumeration_matches_customer_and_fetch_chain_preserves_listing_and_deep(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: "sumsub")
    conn = _db()
    client = FakeClient(_routes())

    await run_historical_backfill_for_subscription(
        db=conn,
        ca_client=client,
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
        backfill_run_id="bf-chain",
    )

    assert client.gets == [
        ("/v2/cases", {"customer_identifier": "cust-1"}),
        ("/v2/cases/case-1", None),
        ("/v2/cases/case-1/alerts", None),
        ("/v2/alerts/alert-1/risks", None),
        ("/v2/entity-screening/risks/risk-1", None),
    ]
    normalized = conn.execute("SELECT normalized_report_json FROM screening_reports_normalized").fetchone()[0]
    assert "alert_risk_listing" in normalized
    assert "PEP_CLASS_2" in normalized


@pytest.mark.asyncio
async def test_call_budget_truncation(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: "sumsub")
    monkeypatch.setattr(hb, "_MAX_API_CALLS_PER_BACKFILL", 2)
    conn = _db()

    result = await run_historical_backfill_for_subscription(
        db=conn,
        ca_client=FakeClient(_routes()),
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
    )

    assert result["truncated"] is True
    assert result["truncation"]["reason"] == "api_call_budget"


@pytest.mark.asyncio
async def test_page_cap_truncation(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: "sumsub")
    monkeypatch.setattr(hb, "_MAX_PAGES_PER_RESOURCE", 1)
    conn = _db()
    routes = _routes()
    routes[("/v2/cases", (("customer_identifier", "cust-1"),))] = {
        "cases": [_case()],
        "next": "/v2/cases?page=2",
    }

    result = await run_historical_backfill_for_subscription(
        db=conn,
        ca_client=FakeClient(routes),
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
    )

    assert result["truncated"] is True
    assert result["truncation"]["reason"] == "page_cap"


@pytest.mark.asyncio
async def test_global_concurrency_guard_respected(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: "sumsub")
    hb._BACKFILL_SEMAPHORE = asyncio.Semaphore(3)
    active = 0
    max_active = 0

    original = hb._run_backfill

    async def wrapped(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        try:
            return await original(**kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(hb, "_run_backfill", wrapped)

    await asyncio.gather(*[
        run_historical_backfill_for_subscription(
            db=_db(),
            ca_client=FakeClient(_routes(case_id=f"case-{i}")),
            application_id=f"app-{i}",
            client_id="client-1",
            customer_identifier="cust-1",
        )
        for i in range(6)
    ])

    assert max_active <= 3


@pytest.mark.asyncio
async def test_insert_provenance_and_live_not_downgraded_on_conflict(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: "sumsub")
    conn = _db()
    conn.execute(
        """
        INSERT INTO monitoring_alerts
            (provider, case_identifier, application_id, client_name, alert_type, severity,
             detected_by, summary, source_reference, status, discovered_via, discovered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (COMPLYADVANTAGE_PROVIDER_NAME, "case-1", "app-1", "cust-1", "pep", "medium", "complyadvantage", "live", "{}", "open", "webhook_live", "2025-01-01T00:00:00"),
    )
    conn.commit()

    await run_historical_backfill_for_subscription(
        db=conn,
        ca_client=FakeClient(_routes()),
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
        backfill_run_id="bf-conflict",
    )

    rows = conn.execute("SELECT * FROM monitoring_alerts").fetchall()
    assert len(rows) == 1
    assert rows[0]["discovered_via"] == "webhook_live"
    assert rows[0]["discovered_at"] == "2025-01-01T00:00:00"
    assert rows[0]["backfill_run_id"] == "bf-conflict"


@pytest.mark.asyncio
async def test_backfill_metrics_emitted(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: "sumsub")
    emitted = []
    monkeypatch.setattr(hb, "emit_metric", lambda name, **fields: emitted.append(fields.get("metric_name")))

    await run_historical_backfill_for_subscription(
        db=_db(),
        ca_client=FakeClient(_routes()),
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
    )

    assert "BackfillCustomersStarted" in emitted
    assert "BackfillApiCallsTotal" in emitted
    assert "BackfillCasesMatchedTotal" in emitted
    assert "BackfillRowsInserted" in emitted
    assert "BackfillRowsUpdated" in emitted
    assert "BackfillCustomersCompleted" in emitted


@pytest.mark.asyncio
async def test_active_provider_sumsub_skips_agent7_push(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: "sumsub")
    agent = MagicMock()

    result = await run_historical_backfill_for_subscription(
        db=_db(),
        ca_client=FakeClient(_routes()),
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
        agent_executor=agent,
    )

    assert result["agent7_push"] == "skipped"
    agent.assert_not_called()


@pytest.mark.asyncio
async def test_active_provider_complyadvantage_allows_agent7_push(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: COMPLYADVANTAGE_PROVIDER_NAME)
    monkeypatch.setattr(hb, "_default_db_path", lambda: "/tmp/test.db")
    agent = MagicMock()

    result = await run_historical_backfill_for_subscription(
        db=_db(),
        ca_client=FakeClient(_routes()),
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
        agent_executor=agent,
    )

    assert result["agent7_push"] == "attempted"
    agent.assert_called_once_with("app-1", {"db_path": "/tmp/test.db"})


@pytest.mark.asyncio
async def test_idempotent_rerun_does_not_duplicate_monitoring_rows(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: "sumsub")
    conn = _db()

    await rerun_historical_backfill_for_customer(
        db=conn,
        ca_client=FakeClient(_routes()),
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
        backfill_run_id="bf-1",
    )
    await rerun_historical_backfill_for_customer(
        db=conn,
        ca_client=FakeClient(_routes()),
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
        backfill_run_id="bf-2",
    )

    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_provider_truth_normalized_writes_happen_for_clean_case(monkeypatch):
    monkeypatch.setattr(hb, "get_active_provider_name", lambda: "sumsub")
    conn = _db()
    routes = {
        ("/v2/cases", (("customer_identifier", "cust-1"),)): {"cases": [_case()], "next": None},
        "/v2/cases/case-1": _case(),
        "/v2/cases/case-1/alerts": {"alerts": [], "next": None},
    }

    result = await run_historical_backfill_for_subscription(
        db=conn,
        ca_client=FakeClient(routes),
        application_id="app-1",
        client_id="client-1",
        customer_identifier="cust-1",
    )

    assert result["normalized_records_written"] == 1
    assert conn.execute("SELECT COUNT(*) FROM screening_reports_normalized").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 0
