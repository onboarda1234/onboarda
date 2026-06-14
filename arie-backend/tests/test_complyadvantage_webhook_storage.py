import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_complyadvantage.models.webhooks import CACaseAlertListUpdatedWebhook
from screening_complyadvantage.webhook_storage import (
    process_complyadvantage_webhook,
    reconcile_complyadvantage_webhook_deliveries,
    record_complyadvantage_webhook_receipt,
    stable_webhook_id,
)


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
            status TEXT NOT NULL DEFAULT 'active',
            last_event_at TEXT,
            last_webhook_type TEXT,
            monitoring_event_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE monitoring_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT,
            case_identifier TEXT,
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
        CREATE TABLE complyadvantage_webhook_deliveries (
            webhook_id TEXT PRIMARY KEY,
            first_received_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now')),
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            webhook_type TEXT,
            case_identifier TEXT,
            customer_identifier TEXT,
            processing_status TEXT NOT NULL DEFAULT 'processing',
            processing_result TEXT,
            failure_reason TEXT,
            trace_id TEXT,
            payload_json TEXT,
            alert_identifiers_json TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            processed_at TEXT
        );
        CREATE TABLE monitoring_alert_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitoring_alert_id INTEGER NOT NULL,
            application_id TEXT,
            provider TEXT NOT NULL,
            case_identifier TEXT,
            alert_identifier TEXT,
            match_identifier TEXT,
            risk_identifier TEXT,
            profile_identifier TEXT,
            evidence_type TEXT,
            matched_subject_name TEXT,
            relationship_to_client TEXT,
            match_category TEXT,
            risk_indicator TEXT,
            match_confidence TEXT,
            source_title TEXT,
            source_name TEXT,
            source_url TEXT,
            source_url_available INTEGER DEFAULT 0,
            source_url_unavailable_reason TEXT,
            publication_date TEXT,
            snippet TEXT,
            provider_case_url TEXT,
            evidence_json TEXT,
            raw_provider_reference TEXT,
            evidence_status TEXT DEFAULT 'fetched',
            evidence_hash TEXT NOT NULL,
            fetched_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(monitoring_alert_id, evidence_hash)
        );
        """
    )
    conn.execute(
        "INSERT INTO screening_monitoring_subscriptions (client_id, application_id, provider, person_key, customer_identifier) VALUES (?, ?, ?, ?, ?)",
        ("client-1", "app-1", COMPLYADVANTAGE_PROVIDER_NAME, "person-1", "cust-1"),
    )
    conn.commit()
    return conn


def _envelope():
    return CACaseAlertListUpdatedWebhook.model_validate({
        "webhook_type": "CASE_ALERT_LIST_UPDATED",
        "api_version": "2.0",
        "account_identifier": "acct-test",
        "case_identifier": "case-1",
        "alert_identifiers": ["alert-1"],
        "customer": {"identifier": "cust-1", "external_identifier": "app-1", "version": 1},
        "subjects": [{"identifier": "subj-1", "external_identifier": "person-1", "type": "person"}],
    })


def _normalized(hash_value="hash-1"):
    return {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "source_screening_report_hash": hash_value,
        "provider_specific": {
            COMPLYADVANTAGE_PROVIDER_NAME: {
                "matches": [{"indicators": [{"type": "CAPEPIndicator", "taxonomy_key": "r_pep_class_2"}]}],
                "workflows": {"strict": {"alerts": [{"identifier": "alert-1"}]}},
            }
        },
    }


def _normalized_for_context(context, hash_value="hash-scoped"):
    scope = "person" if context.screening_subject_person_key else "entity"
    kind = context.screening_subject_kind
    return {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "source_screening_report_hash": hash_value,
        "subject_scope": scope,
        "screening_subject_kind": kind,
        "screening_subject_person_key": context.screening_subject_person_key,
        "provider_specific": {
            COMPLYADVANTAGE_PROVIDER_NAME: {
                "subject_scope": scope,
                "screening_subject": {
                    "kind": kind,
                    "scope": scope,
                    "person_key": context.screening_subject_person_key,
                },
                "matches": [{"indicators": [{"type": "CAMediaIndicator", "taxonomy_key": "r_adverse_media_general"}]}],
                "workflows": {"strict": {"alerts": [{"identifier": "alert-1"}]}},
            }
        },
    }


def _normalized_with_media_evidence(hash_value="hash-media-evidence"):
    return {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "source_screening_report_hash": hash_value,
        "subject_scope": "entity",
        "provider_specific": {
            COMPLYADVANTAGE_PROVIDER_NAME: {
                "subject_scope": "entity",
                "screening_subject": {"kind": "entity", "scope": "entity"},
                "matches": [{
                    "profile_identifier": "profile-1",
                    "risk_id": "risk-1",
                    "profile": {
                        "company": {"names": {"values": [{"name": "Sprint Two Client Ltd"}]}},
                        "match_details": {"match_score": 0.91, "matched_name": "Sprint Two Client Ltd"},
                    },
                    "rollups": {"has_adverse_media_hit": True},
                    "indicators": [{
                        "type": "CAMediaIndicator",
                        "taxonomy_key": "r_adverse_media_general",
                        "taxonomy_label": "Adverse Media",
                        "value": {
                            "title": "Regulatory enforcement article",
                            "url": "https://example.test/article",
                            "publication_date": "2026-05-01",
                            "source_name": "Example News",
                            "snippets": [{"text": "Example adverse media snippet"}],
                        },
                    }],
                }],
                "workflows": {"strict": {"alerts": [{"identifier": "alert-1"}]}},
            }
        },
    }


@pytest.mark.asyncio
async def test_process_sequence_writes_normalized_alert_subscription_and_agent(monkeypatch):
    conn = _db()
    agent = MagicMock()
    monkeypatch.setattr(
        "screening_complyadvantage.webhook_storage.get_active_provider_name",
        lambda: COMPLYADVANTAGE_PROVIDER_NAME,
    )
    monkeypatch.setattr("screening_complyadvantage.webhook_storage._default_db_path", lambda: "/tmp/test.db")

    result = await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized(),
        agent_executor=agent,
        webhook_id="wh-sequence-1",
    )

    assert result["status"] == "processed"
    assert conn.execute("SELECT COUNT(*) FROM screening_reports_normalized").fetchone()[0] == 1
    alert = conn.execute("SELECT * FROM monitoring_alerts").fetchone()
    assert alert["provider"] == COMPLYADVANTAGE_PROVIDER_NAME
    assert alert["case_identifier"] == "case-1"
    sub = conn.execute("SELECT monitoring_event_count, last_webhook_type FROM screening_monitoring_subscriptions").fetchone()
    assert sub["monitoring_event_count"] == 1
    assert sub["last_webhook_type"] == "CASE_ALERT_LIST_UPDATED"
    agent.assert_called_once_with("app-1", {"db_path": "/tmp/test.db"})
    delivery = conn.execute("SELECT processing_status, processing_result FROM complyadvantage_webhook_deliveries WHERE webhook_id = ?", ("wh-sequence-1",)).fetchone()
    assert delivery["processing_status"] == "processed"
    assert delivery["processing_result"] == "success"


@pytest.mark.asyncio
async def test_entity_subscription_webhook_persists_entity_scoped_media_source_reference(monkeypatch):
    conn = _db()
    conn.execute("DELETE FROM screening_monitoring_subscriptions")
    conn.execute(
        "INSERT INTO screening_monitoring_subscriptions (client_id, application_id, provider, person_key, customer_identifier) VALUES (?, ?, ?, ?, ?)",
        ("client-1", "app-entity", COMPLYADVANTAGE_PROVIDER_NAME, None, "cust-1"),
    )
    conn.commit()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")

    await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized_for_context(context),
    )

    alert = conn.execute("SELECT * FROM monitoring_alerts").fetchone()
    source_reference = json.loads(alert["source_reference"])
    assert source_reference["subject_scope"] == "entity"
    assert source_reference["normalized_record_id"] > 0


@pytest.mark.asyncio
async def test_missing_subscription_halts_before_writes():
    conn = _db()
    conn.execute("DELETE FROM screening_monitoring_subscriptions")
    called = MagicMock()

    result = await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        fetch_normalized=called,
    )

    assert result["status"] == "subscription_missing"
    called.assert_not_called()
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_normalized_write_failure_halts_best_effort_steps(monkeypatch):
    conn = _db()
    agent = MagicMock()
    monkeypatch.setattr(
        "screening_complyadvantage.webhook_storage.get_active_provider_name",
        lambda: COMPLYADVANTAGE_PROVIDER_NAME,
    )

    def fail_persist(*args, **kwargs):
        raise sqlite3.OperationalError("write failed")

    result = await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized(),
        persist_report=fail_persist,
        agent_executor=agent,
    )

    assert result["status"] == "normalized_write_failure"
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 0
    agent.assert_not_called()


@pytest.mark.asyncio
async def test_monitoring_alert_failure_continues_to_subscription_update_and_agent(monkeypatch):
    conn = _db()
    conn.execute("DROP TABLE monitoring_alerts")
    agent = MagicMock()
    monkeypatch.setattr(
        "screening_complyadvantage.webhook_storage.get_active_provider_name",
        lambda: COMPLYADVANTAGE_PROVIDER_NAME,
    )
    monkeypatch.setattr("screening_complyadvantage.webhook_storage._default_db_path", lambda: "/tmp/test.db")

    result = await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized(),
        agent_executor=agent,
    )

    assert result["status"] == "processed"
    sub = conn.execute("SELECT monitoring_event_count FROM screening_monitoring_subscriptions").fetchone()
    assert sub["monitoring_event_count"] == 1
    agent.assert_called_once()


@pytest.mark.asyncio
async def test_sumsub_provider_flag_skips_agent_push(monkeypatch):
    conn = _db()
    agent = MagicMock()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")

    await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized(),
        agent_executor=agent,
    )

    agent.assert_not_called()


@pytest.mark.asyncio
async def test_double_fire_idempotency_dedups_rows_but_counts_each_delivery(monkeypatch):
    conn = _db()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")

    kwargs = {
        "db_factory": lambda: NoCloseDB(conn),
        "client_factory": lambda: object(),
        "fetch_normalized": lambda client, envelope, context: _normalized("hash-double-fire"),
    }

    first = await process_complyadvantage_webhook(_envelope(), webhook_id="wh-double-fire", **kwargs)
    second = await process_complyadvantage_webhook(_envelope(), webhook_id="wh-double-fire", **kwargs)

    assert first["status"] == "processed"
    assert second["status"] == "duplicate_ignored"
    assert conn.execute("SELECT COUNT(*) FROM screening_reports_normalized").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 1
    sub = conn.execute(
        "SELECT monitoring_event_count, last_webhook_type "
        "FROM screening_monitoring_subscriptions WHERE customer_identifier = ?",
        ("cust-1",),
    ).fetchone()
    assert sub["monitoring_event_count"] == 1
    assert sub["last_webhook_type"] == "CASE_ALERT_LIST_UPDATED"
    delivery = conn.execute("SELECT duplicate_count FROM complyadvantage_webhook_deliveries WHERE webhook_id = ?", ("wh-double-fire",)).fetchone()
    assert delivery["duplicate_count"] == 1


@pytest.mark.asyncio
async def test_media_evidence_is_persisted_with_alert(monkeypatch):
    conn = _db()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")

    result = await process_complyadvantage_webhook(
        _envelope(),
        webhook_id="wh-media-evidence",
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized_with_media_evidence(),
    )

    assert result["status"] == "processed"
    alert = conn.execute("SELECT id FROM monitoring_alerts WHERE provider = ?", (COMPLYADVANTAGE_PROVIDER_NAME,)).fetchone()
    evidence = conn.execute("SELECT * FROM monitoring_alert_evidence WHERE monitoring_alert_id = ?", (alert["id"],)).fetchone()
    assert evidence is not None
    assert evidence["evidence_type"] == "adverse_media"
    assert evidence["matched_subject_name"] == "Sprint Two Client Ltd"
    assert evidence["source_title"] == "Regulatory enforcement article"
    assert evidence["source_url"] == "https://example.test/article"


@pytest.mark.asyncio
async def test_missing_article_link_stores_honest_limitation(monkeypatch):
    conn = _db()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")
    report = _normalized_with_media_evidence("hash-no-link")
    report["provider_specific"][COMPLYADVANTAGE_PROVIDER_NAME]["matches"][0]["indicators"][0]["value"].pop("url")

    await process_complyadvantage_webhook(
        _envelope(),
        webhook_id="wh-no-link",
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: report,
    )

    evidence = conn.execute("SELECT source_url, source_url_available, source_url_unavailable_reason FROM monitoring_alert_evidence").fetchone()
    assert evidence["source_url"] is None
    assert evidence["source_url_available"] == 0
    assert "not available from ComplyAdvantage payload" in evidence["source_url_unavailable_reason"]


@pytest.mark.asyncio
async def test_pre_ack_receipt_is_claimed_and_processed_idempotently(monkeypatch):
    conn = _db()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")
    envelope = _envelope()
    payload = envelope.model_dump(mode="json")

    record_complyadvantage_webhook_receipt(
        envelope,
        webhook_id="wh-pre-ack",
        trace_id="trace-pre-ack",
        payload=payload,
        db_factory=lambda: NoCloseDB(conn),
    )

    before = conn.execute(
        "SELECT processing_status, payload_json, alert_identifiers_json FROM complyadvantage_webhook_deliveries WHERE webhook_id = ?",
        ("wh-pre-ack",),
    ).fetchone()
    assert before["processing_status"] == "received"
    assert "alert-1" in before["alert_identifiers_json"]
    assert "webhook-signature" not in before["payload_json"]

    result = await process_complyadvantage_webhook(
        envelope,
        webhook_id="wh-pre-ack",
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized("hash-pre-ack"),
    )

    assert result["status"] == "processed"
    delivery = conn.execute(
        "SELECT processing_status, processing_result, duplicate_count FROM complyadvantage_webhook_deliveries WHERE webhook_id = ?",
        ("wh-pre-ack",),
    ).fetchone()
    assert delivery["processing_status"] == "processed"
    assert delivery["processing_result"] == "success"
    assert delivery["duplicate_count"] == 0


@pytest.mark.asyncio
async def test_detail_fetch_failure_marks_retry_pending_for_reconciliation(monkeypatch):
    conn = _db()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")
    envelope = _envelope()
    record_complyadvantage_webhook_receipt(
        envelope,
        webhook_id="wh-fetch-fails",
        trace_id="trace-fetch-fails",
        payload=envelope.model_dump(mode="json"),
        db_factory=lambda: NoCloseDB(conn),
    )

    with pytest.raises(RuntimeError):
        await process_complyadvantage_webhook(
            envelope,
            webhook_id="wh-fetch-fails",
            db_factory=lambda: NoCloseDB(conn),
            client_factory=lambda: object(),
            fetch_normalized=lambda client, envelope, context: (_ for _ in ()).throw(RuntimeError("provider timeout")),
        )

    delivery = conn.execute(
        "SELECT processing_status, processing_result, failure_reason, next_retry_at FROM complyadvantage_webhook_deliveries WHERE webhook_id = ?",
        ("wh-fetch-fails",),
    ).fetchone()
    assert delivery["processing_status"] == "retry_pending"
    assert delivery["processing_result"] == "exception"
    assert delivery["failure_reason"] == "RuntimeError"
    assert delivery["next_retry_at"]


@pytest.mark.asyncio
async def test_reconciliation_recovers_retry_pending_without_duplicate_rows(monkeypatch):
    conn = _db()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")
    envelope = _envelope()
    record_complyadvantage_webhook_receipt(
        envelope,
        webhook_id="wh-reconcile",
        trace_id="trace-reconcile",
        payload=envelope.model_dump(mode="json"),
        db_factory=lambda: NoCloseDB(conn),
    )
    conn.execute(
        "UPDATE complyadvantage_webhook_deliveries SET processing_status = 'retry_pending', processing_result = 'detail_fetch_failed' WHERE webhook_id = ?",
        ("wh-reconcile",),
    )
    conn.commit()

    result = await reconcile_complyadvantage_webhook_deliveries(
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized("hash-reconcile"),
    )

    assert result["processed"] == 1
    assert result["results"][0]["status"] == "processed"
    assert conn.execute("SELECT COUNT(*) FROM screening_reports_normalized").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 1
    delivery = conn.execute(
        "SELECT processing_status, processing_result, retry_count FROM complyadvantage_webhook_deliveries WHERE webhook_id = ?",
        ("wh-reconcile",),
    ).fetchone()
    assert delivery["processing_status"] == "processed"
    assert delivery["processing_result"] == "success"
    assert delivery["retry_count"] == 1


def test_stable_legacy_webhook_id_is_deterministic():
    payload = _envelope().model_dump(mode="json")
    assert stable_webhook_id(payload) == stable_webhook_id(dict(payload))
    assert stable_webhook_id(payload).startswith("legacy:case_alert_list_updated:case-1:")
