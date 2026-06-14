import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_complyadvantage.models.webhooks import CACaseAlertListUpdatedWebhook
from screening_complyadvantage.webhook_storage import (
    process_complyadvantage_webhook,
    reconcile_complyadvantage_webhook_deliveries,
    record_complyadvantage_webhook_receipt,
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


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _provider_refs():
    return {
        "case_id": "case-runtime-1",
        "customer_id": "cust-runtime-1",
        "workflow_id": "workflow-runtime-1",
        "alert_id": "alert-runtime-1",
        "risk_id": "risk-runtime-1",
        "profile_id": "profile-runtime-1",
    }


def _company_screening(*, api_status="live", matched=False, results=None, evidence_quality="complete"):
    return {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "source": COMPLYADVANTAGE_PROVIDER_NAME,
        "api_status": api_status,
        "matched": matched,
        "results": list(results or []),
        "evidence_quality": evidence_quality,
        "provider_references": _provider_refs(),
        "screened_at": _iso(datetime.now(timezone.utc)),
    }


def _prescreening(company_screening, *, valid_until=None):
    now = datetime.now(timezone.utc)
    return {
        "screening_report": {
            "screening_mode": "live",
            "screened_at": _iso(now),
            "provider": COMPLYADVANTAGE_PROVIDER_NAME,
            "company_name": "Runtime E2E Ltd",
            "company_screening": company_screening,
            "director_screenings": [],
            "ubo_screenings": [],
            "intermediary_screenings": [],
            "total_hits": len(company_screening.get("results") or []),
            "has_adverse_media_hit": any(
                item.get("is_adverse_media") for item in company_screening.get("results") or []
            ),
        },
        "screening_valid_until": valid_until or _iso(now + timedelta(days=30)),
        "screening_validity_days": 30,
    }


def _insert_application_and_memo(db, prescreening):
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-ca3-runtime-{suffix}"
    app_ref = f"ARF-CA3-RUNTIME-{suffix}"
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            app_ref,
            f"client-ca3-runtime-{suffix}",
            "Runtime E2E Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "compliance_review",
            "MEDIUM",
            45,
            json.dumps(prescreening),
        ),
    )
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score, validation_status, supervisor_status, approval_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            json.dumps({"ai_source": "deterministic", "metadata": {"ai_source": "deterministic"}}),
            "system",
            "APPROVE_WITH_CONDITIONS",
            "approved",
            8.5,
            "pass",
            "CONSISTENT",
            "Runtime E2E approval reason.",
        ),
    )
    verified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    for doc_type in (
        "cert_inc",
        "memarts",
        "reg_sh",
        "reg_dir",
        "fin_stmt",
        "poa",
        "board_res",
        "structure_chart",
    ):
        doc_id = f"doc-ca3-runtime-{suffix}-{doc_type}"
        db.execute(
            """
            INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path, slot_key,
             verification_status, verification_results, verified_at)
            VALUES (?, ?, ?, ?, ?, ?, 'verified', ?, ?)
            """,
            (
                doc_id,
                app_id,
                doc_type,
                f"{doc_type}.pdf",
                f"/tmp/{doc_type}.pdf",
                f"entity:{doc_type}",
                json.dumps({"overall": "verified", "checks": [{"result": "pass"}], "verified_at": verified_at}),
                verified_at,
            ),
        )
        db.execute(
            """
            INSERT INTO agent_executions
            (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
            VALUES (?, ?, 'verify_document', 1, 'verified', ?, 0)
            """,
            (app_id, doc_id, json.dumps([{"result": "pass"}])),
        )
    db.commit()
    return dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())


def test_runtime_e2e_truth_paths_no_hit_hit_adverse_failure_stale_and_rescreen():
    from screening_state import build_screening_terminality_summary, build_screening_truth_summary

    clean = _prescreening(_company_screening())
    clean_summary = build_screening_truth_summary(clean["screening_report"], clean)
    assert clean_summary["canonical_state"] == "completed_clear"
    assert clean_summary["approval_ready"] is True
    assert clean_summary["approval_blocking"] is False

    unresolved_hit = _prescreening(
        _company_screening(
            matched=True,
            results=[{"name": "Sanctions Candidate", "is_sanctioned": True}],
        )
    )
    hit_summary = build_screening_truth_summary(unresolved_hit["screening_report"], unresolved_hit)
    assert hit_summary["canonical_state"] == "completed_match"
    assert hit_summary["approval_blocking"] is True

    adverse_media = _prescreening(
        _company_screening(
            matched=True,
            results=[{
                "name": "Runtime E2E Ltd",
                "is_adverse_media": True,
                "match_categories": ["adverse_media"],
            }],
        )
    )
    adverse_summary = build_screening_terminality_summary(adverse_media["screening_report"], adverse_media)
    assert adverse_summary["has_terminal_match"] is True
    assert adverse_summary["approval_blocking"] is True

    provider_failure = _prescreening(
        _company_screening(api_status="error", evidence_quality="provider_error")
    )
    failure_summary = build_screening_truth_summary(provider_failure["screening_report"], provider_failure)
    assert failure_summary["canonical_state"] == "failed"
    assert failure_summary["provider_availability"] == "failed"
    assert failure_summary["approval_blocking"] is True

    stale = _prescreening(
        _company_screening(),
        valid_until=_iso(datetime.now(timezone.utc) - timedelta(days=1)),
    )
    stale_summary = build_screening_truth_summary(stale["screening_report"], stale)
    assert stale_summary["canonical_state"] == "stale"
    assert stale_summary["approval_blocked_reasons"] == ["screening:stale_requires_refresh"]

    rescreened = _prescreening(_company_screening())
    rescreened_summary = build_screening_truth_summary(rescreened["screening_report"], rescreened)
    assert rescreened_summary["canonical_state"] == "completed_clear"
    assert rescreened_summary["approval_blocking"] is False
    assert rescreened_summary["freshness"]["screening_valid_until"] != stale["screening_valid_until"]


@pytest.mark.parametrize(
    "company_screening,valid_until,expected_can_approve,expected_message",
    [
        (_company_screening(), None, True, ""),
        (
            _company_screening(matched=True, results=[{"name": "PEP Candidate", "is_pep": True}]),
            None,
            False,
            "completed_match",
        ),
        (
            _company_screening(api_status="error", evidence_quality="provider_error"),
            None,
            False,
            "failed",
        ),
        (
            _company_screening(),
            _iso(datetime.now(timezone.utc) - timedelta(days=1)),
            False,
            "expired",
        ),
    ],
)
def test_runtime_e2e_approval_gate_blocks_unresolved_failure_stale_and_allows_clean_no_hit(
    db,
    company_screening,
    valid_until,
    expected_can_approve,
    expected_message,
):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, _prescreening(company_screening, valid_until=valid_until))

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is expected_can_approve
    assert expected_message in message


def test_runtime_e2e_queue_quarantines_no_adverse_media_claim_with_adverse_evidence():
    from screening_state import resolve_screening_queue_state

    resolved = resolve_screening_queue_state({
        "status_key": "screened_no_match",
        "status_label": "No Match",
        "screening_state": "completed_clear",
        "screening_truth_state": "completed_clear",
        "provider_availability": "available",
        "screening_result": "clear",
        "terminal": True,
        "defensible_clear": True,
        "adverse_media_status": "clear",
        "total_hits": 0,
        "provider_evidence": [{
            "evidence_type": "adverse_media",
            "match_category": "adverse media",
            "source_title": "Runtime adverse media article",
        }],
    })

    assert resolved["status_key"] == "review_required"
    assert resolved["terminal"] is False
    assert "adverse_media_evidence_claimed_clear" in resolved["state_integrity_flags"]
    assert "unresolved_screening_hits" in resolved["blocking_flags"]


def _webhook_db():
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
        ("client-runtime-1", "app-runtime-1", COMPLYADVANTAGE_PROVIDER_NAME, None, "cust-runtime-1"),
    )
    conn.commit()
    return conn


def _runtime_webhook_envelope():
    return CACaseAlertListUpdatedWebhook.model_validate({
        "webhook_type": "CASE_ALERT_LIST_UPDATED",
        "api_version": "2.0",
        "account_identifier": "acct-runtime",
        "case_identifier": "case-runtime-1",
        "alert_identifiers": ["alert-runtime-1"],
        "customer": {
            "identifier": "cust-runtime-1",
            "external_identifier": "app-runtime-1",
            "version": 1,
        },
        "subjects": [{
            "identifier": "subject-runtime-1",
            "external_identifier": "entity-runtime-1",
            "type": "company",
        }],
    })


def _runtime_normalized(hash_value):
    return {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "source_screening_report_hash": hash_value,
        "subject_scope": "entity",
        "provider_specific": {
            COMPLYADVANTAGE_PROVIDER_NAME: {
                "subject_scope": "entity",
                "screening_subject": {"kind": "entity", "scope": "entity"},
                "matches": [{
                    "profile_identifier": "profile-runtime-1",
                    "risk_id": "risk-runtime-1",
                    "profile": {
                        "company": {"names": {"values": [{"name": "Runtime E2E Ltd"}]}},
                        "match_details": {"match_score": 0.87, "matched_name": "Runtime E2E Ltd"},
                    },
                    "indicators": [{
                        "type": "CAMediaIndicator",
                        "taxonomy_key": "r_adverse_media_general",
                        "taxonomy_label": "Adverse Media",
                        "value": {
                            "title": "Runtime adverse media article",
                            "url": "https://example.test/runtime-e2e",
                            "publication_date": "2026-06-01",
                            "source_name": "Example News",
                            "snippets": [{"text": "Runtime E2E adverse media snippet"}],
                        },
                    }],
                }],
                "workflows": {"strict": {"alerts": [{"identifier": "alert-runtime-1"}]}},
            }
        },
    }


@pytest.mark.asyncio
async def test_runtime_e2e_duplicate_webhook_and_reconciliation_are_idempotent(monkeypatch):
    conn = _webhook_db()
    monkeypatch.setattr(
        "screening_complyadvantage.webhook_storage.get_active_provider_name",
        lambda: COMPLYADVANTAGE_PROVIDER_NAME,
    )
    envelope = _runtime_webhook_envelope()

    record_complyadvantage_webhook_receipt(
        envelope,
        webhook_id="wh-runtime-duplicate",
        trace_id="trace-runtime-duplicate",
        payload=envelope.model_dump(mode="json"),
        db_factory=lambda: NoCloseDB(conn),
    )
    first = await process_complyadvantage_webhook(
        envelope,
        webhook_id="wh-runtime-duplicate",
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _runtime_normalized("hash-runtime-duplicate"),
        agent_executor=lambda application_id, context: None,
    )
    second = await process_complyadvantage_webhook(
        envelope,
        webhook_id="wh-runtime-duplicate",
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _runtime_normalized("hash-runtime-duplicate"),
        agent_executor=lambda application_id, context: None,
    )

    assert first["status"] == "processed"
    assert second["status"] == "duplicate_ignored"
    assert conn.execute("SELECT COUNT(*) FROM screening_reports_normalized").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 1

    record_complyadvantage_webhook_receipt(
        envelope,
        webhook_id="wh-runtime-reconcile",
        trace_id="trace-runtime-reconcile",
        payload=envelope.model_dump(mode="json"),
        db_factory=lambda: NoCloseDB(conn),
    )
    conn.execute(
        "UPDATE complyadvantage_webhook_deliveries SET processing_status = 'retry_pending', processing_result = 'detail_fetch_failed' WHERE webhook_id = ?",
        ("wh-runtime-reconcile",),
    )
    conn.commit()

    result = await reconcile_complyadvantage_webhook_deliveries(
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _runtime_normalized("hash-runtime-reconcile"),
        agent_executor=lambda application_id, context: None,
    )

    assert result["processed"] == 1
    assert result["results"][0]["status"] == "processed"
    assert conn.execute("SELECT COUNT(*) FROM screening_reports_normalized").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 1
    delivery = conn.execute(
        "SELECT processing_status, processing_result, retry_count FROM complyadvantage_webhook_deliveries WHERE webhook_id = ?",
        ("wh-runtime-reconcile",),
    ).fetchone()
    assert delivery["processing_status"] == "processed"
    assert delivery["processing_result"] == "success"
    assert delivery["retry_count"] == 1
