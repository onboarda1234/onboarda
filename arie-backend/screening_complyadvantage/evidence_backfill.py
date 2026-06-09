"""Idempotent evidence backfill for historical ComplyAdvantage monitoring alerts."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME

from .evidence import SOURCE_LINK_UNAVAILABLE, evidence_hash, extract_monitoring_evidence
from .historical_backfill import (
    _BackfillGuard,
    _alert_identifier,
    _fetch_alerts_for_case,
    _normalize_case_backfill,
)
from .normalizer import ScreeningApplicationContext
from .observability import emit_metric, emit_operational, status_family

logger = logging.getLogger(__name__)

DETAIL_UNAVAILABLE = "Detailed provider evidence is not available for this alert."


def backfill_monitoring_alert_evidence(
    db,
    *,
    ca_client=None,
    dry_run: bool = True,
    limit: int = 100,
    alert_ids: list[int | str] | None = None,
    fetch_live_details: bool = False,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Hydrate missing structured CA evidence for existing monitoring alerts.

    The operation never creates monitoring alerts. It first extracts from the
    normalized provider report already referenced by the alert, and only calls
    CA detail APIs when explicitly requested and a client is supplied.
    """

    trace_id = trace_id or f"ca1b-{uuid4().hex}"
    result = {
        "trace_id": trace_id,
        "dry_run": bool(dry_run),
        "fetch_live_details": bool(fetch_live_details and ca_client),
        "alerts_checked": 0,
        "alerts_with_existing_evidence": 0,
        "alerts_would_update": 0,
        "evidence_rows_inserted": 0,
        "evidence_rows_unavailable": 0,
        "evidence_rows_failed": 0,
        "evidence_rows_not_applicable": 0,
        "alerts": [],
    }
    candidates = _candidate_alerts(db, limit=limit, alert_ids=alert_ids)
    for alert in candidates:
        alert_result = _backfill_one_alert(
            db,
            dict(alert),
            ca_client=ca_client,
            dry_run=dry_run,
            fetch_live_details=fetch_live_details,
            trace_id=trace_id,
        )
        result["alerts"].append(alert_result)
        result["alerts_checked"] += 1
        if alert_result["status"] == "existing_evidence":
            result["alerts_with_existing_evidence"] += 1
        if alert_result.get("would_update"):
            result["alerts_would_update"] += 1
        result["evidence_rows_inserted"] += alert_result.get("inserted", 0)
        result["evidence_rows_unavailable"] += 1 if alert_result["status"] == "unavailable" else 0
        result["evidence_rows_failed"] += 1 if alert_result["status"] == "failed" else 0
        result["evidence_rows_not_applicable"] += 1 if alert_result["status"] == "not_applicable" else 0
    _commit(db)
    emit_operational(
        "ca_monitoring_evidence_backfill_completed",
        trace_id=trace_id,
        component="evidence_backfill",
        outcome="success",
        dry_run=dry_run,
        alerts_checked=result["alerts_checked"],
        evidence_rows_inserted=result["evidence_rows_inserted"],
        evidence_rows_unavailable=result["evidence_rows_unavailable"],
        evidence_rows_failed=result["evidence_rows_failed"],
    )
    return result


def _backfill_one_alert(db, alert: dict[str, Any], *, ca_client, dry_run, fetch_live_details, trace_id):
    alert_id = _row_value(alert, "id")
    application_id = _row_value(alert, "application_id")
    source_reference = _safe_json_loads(_row_value(alert, "source_reference") or "{}")
    case_identifier = _first_non_empty(
        _row_value(alert, "case_identifier"),
        source_reference.get("case_identifier"),
        source_reference.get("case_id"),
    )
    alert_identifier = _first_non_empty(
        source_reference.get("alert_identifier"),
        source_reference.get("alert_id"),
        _row_value(alert, "alert_identifier"),
    )
    result = {
        "alert_id": alert_id,
        "application_id": application_id,
        "case_identifier": case_identifier,
        "alert_identifier": alert_identifier,
        "status": "started",
        "inserted": 0,
        "would_update": False,
        "source": "",
    }
    if _existing_evidence_count(db, alert_id) > 0:
        result["status"] = "existing_evidence"
        return result
    if not _is_compliance_provider_alert(alert, source_reference):
        result["status"] = "not_applicable"
        result["reason"] = "alert is not ComplyAdvantage-sourced"
        _persist_marker_if_needed(db, alert, source_reference, "not_applicable", result["reason"], dry_run=dry_run)
        result["would_update"] = True
        result["inserted"] = 0 if dry_run else 1
        return result

    try:
        normalized_report = _stored_normalized_report(db, alert, source_reference)
        if normalized_report:
            result["source"] = "stored_normalized_report"
        evidence_rows = _extract_rows(normalized_report, case_identifier=case_identifier, alert_identifier=alert_identifier)
        if not evidence_rows and fetch_live_details and ca_client and case_identifier:
            emit_operational(
                "detail_fetch_attempted",
                trace_id=trace_id,
                component="evidence_backfill",
                outcome="started",
                alert_id=alert_id,
                case_identifier=case_identifier,
            )
            normalized_report = _fetch_normalized_report_from_ca(
                ca_client,
                alert,
                source_reference,
                case_identifier=case_identifier,
                alert_identifier=alert_identifier,
                trace_id=trace_id,
            )
            result["source"] = "ca_detail_api"
            evidence_rows = _extract_rows(normalized_report, case_identifier=case_identifier, alert_identifier=alert_identifier)
        if evidence_rows:
            result["status"] = "fetched"
            result["would_update"] = True
            result["inserted"] = 0 if dry_run else _persist_evidence_rows(db, alert, evidence_rows)
            emit_metric(
                "detail_fetch_succeeded",
                metric_name="DetailFetchSucceeded",
                trace_id=trace_id,
                component="evidence_backfill",
                outcome="success",
                alert_id=alert_id,
                case_identifier=case_identifier,
            )
            emit_operational(
                "detail_fetch_succeeded",
                trace_id=trace_id,
                component="evidence_backfill",
                outcome="success",
                alert_id=alert_id,
                case_identifier=case_identifier,
                evidence_rows=len(evidence_rows),
            )
            return result
        result["status"] = "unavailable"
        result["reason"] = DETAIL_UNAVAILABLE
        result["would_update"] = True
        result["inserted"] = 0 if dry_run else _persist_marker_if_needed(
            db,
            alert,
            source_reference,
            "unavailable",
            DETAIL_UNAVAILABLE,
            dry_run=False,
        )
        emit_operational(
            "detail_fetch_unavailable",
            trace_id=trace_id,
            component="evidence_backfill",
            outcome="unavailable",
            alert_id=alert_id,
            case_identifier=case_identifier,
        )
        return result
    except Exception as exc:
        logger.warning("ca_monitoring_evidence_backfill_failed alert_id=%s error=%s", alert_id, exc.__class__.__name__)
        result["status"] = "failed"
        result["reason"] = exc.__class__.__name__
        result["would_update"] = True
        result["inserted"] = 0 if dry_run else _persist_marker_if_needed(
            db,
            alert,
            source_reference,
            "failed",
            exc.__class__.__name__,
            dry_run=False,
        )
        emit_metric(
            "detail_fetch_failed",
            metric_name="DetailFetchFailed",
            trace_id=trace_id,
            component="evidence_backfill",
            outcome="failure",
            alert_id=alert_id,
            case_identifier=case_identifier,
            status_family=status_family(error=exc),
        )
        emit_operational(
            "detail_fetch_failed",
            trace_id=trace_id,
            component="evidence_backfill",
            outcome="failure",
            alert_id=alert_id,
            case_identifier=case_identifier,
            failure_reason=exc.__class__.__name__,
        )
        return result


def _candidate_alerts(db, *, limit, alert_ids):
    params: list[Any] = []
    if alert_ids:
        placeholders = ",".join("?" for _ in alert_ids)
        where = f"ma.id IN ({placeholders})"
        params.extend(alert_ids)
    else:
        where = """
            NOT EXISTS (
                SELECT 1 FROM monitoring_alert_evidence e
                 WHERE e.monitoring_alert_id = ma.id
            )
            AND (
                LOWER(COALESCE(ma.provider, '')) = 'complyadvantage'
                OR LOWER(COALESCE(ma.detected_by, '')) = 'complyadvantage'
                OR LOWER(COALESCE(ma.source_reference, '')) LIKE '%complyadvantage%'
            )
        """
    params.append(int(limit))
    return db.execute(
        f"""
        SELECT ma.*
          FROM monitoring_alerts ma
         WHERE {where}
         ORDER BY ma.id DESC
         LIMIT ?
        """,
        tuple(params),
    ).fetchall()


def _stored_normalized_report(db, alert, source_reference):
    normalized_id = _first_non_empty(
        source_reference.get("normalized_record_id"),
        source_reference.get("screening_normalized_record_id"),
    )
    if normalized_id:
        row = db.execute(
            """
            SELECT normalized_report_json
              FROM screening_reports_normalized
             WHERE id = ? AND provider = ?
             LIMIT 1
            """,
            (normalized_id, COMPLYADVANTAGE_PROVIDER_NAME),
        ).fetchone()
        parsed = _normalized_from_row(row)
        if parsed:
            return parsed
    application_id = _row_value(alert, "application_id")
    case_identifier = _first_non_empty(_row_value(alert, "case_identifier"), source_reference.get("case_identifier"))
    if not application_id:
        return None
    rows = db.execute(
        """
        SELECT normalized_report_json
          FROM screening_reports_normalized
         WHERE application_id = ? AND provider = ?
         ORDER BY id DESC
         LIMIT 20
        """,
        (application_id, COMPLYADVANTAGE_PROVIDER_NAME),
    ).fetchall()
    for row in rows:
        parsed = _normalized_from_row(row)
        if parsed and (not case_identifier or case_identifier in json.dumps(parsed, default=str)):
            return parsed
    return None


def _fetch_normalized_report_from_ca(ca_client, alert, source_reference, *, case_identifier, alert_identifier, trace_id):
    case_raw = ca_client.get(f"/v2/cases/{case_identifier}")
    alert_ids = [alert_identifier] if alert_identifier else [
        alert_id for alert_id in (_alert_identifier(a) for a in _fetch_alerts_for_case(
            _BackfillGuard(ca_client, max_calls=25, trace_id=trace_id, backfill_run_id=trace_id),
            case_identifier,
        ))
        if alert_id
    ]
    guard = _BackfillGuard(ca_client, max_calls=50, trace_id=trace_id, backfill_run_id=trace_id)
    customer_identifier = _first_non_empty(
        source_reference.get("customer_identifier"),
        source_reference.get("customer_id"),
        _nested(case_raw, "customer", "identifier"),
        _nested(case_raw, "customer", "external_identifier"),
        _row_value(alert, "client_name"),
        _row_value(alert, "application_id"),
        "unknown",
    )
    person_key = _first_non_empty(source_reference.get("person_key"), _nested(source_reference, "screening_subject", "person_key"))
    subject_kind = _subject_kind(source_reference, person_key)
    context = ScreeningApplicationContext(
        application_id=str(_row_value(alert, "application_id") or ""),
        client_id=str(customer_identifier),
        screening_subject_kind=subject_kind,
        screening_subject_name=str(_first_non_empty(source_reference.get("subject_name"), _row_value(alert, "client_name"), customer_identifier)),
        screening_subject_person_key=person_key,
    )
    return _normalize_case_backfill(
        guard,
        application_context=context,
        case_identifier=case_identifier,
        case_raw=case_raw,
        alert_ids=alert_ids,
        customer_identifier=str(customer_identifier),
        trace_id=trace_id,
    )


def _extract_rows(normalized_report, *, case_identifier, alert_identifier):
    if not normalized_report:
        return []
    return extract_monitoring_evidence(
        normalized_report,
        case_identifier=case_identifier,
        alert_identifier=alert_identifier,
    )


def _persist_evidence_rows(db, alert, evidence_rows):
    count = 0
    for entry in evidence_rows:
        row_hash = evidence_hash(entry)
        _insert_evidence_row(db, alert, entry, row_hash)
        count += 1
    return count


def _persist_marker_if_needed(db, alert, source_reference, status, reason, *, dry_run):
    if dry_run:
        return 0
    case_identifier = _first_non_empty(_row_value(alert, "case_identifier"), source_reference.get("case_identifier"))
    alert_identifier = _first_non_empty(source_reference.get("alert_identifier"), source_reference.get("alert_id"))
    entry = {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "case_identifier": case_identifier,
        "alert_identifier": alert_identifier,
        "evidence_type": "provider_evidence_status",
        "source_url_available": False,
        "source_url_unavailable_reason": reason or SOURCE_LINK_UNAVAILABLE,
        "evidence_json": {"status": status, "reason": reason},
        "raw_provider_reference": {
            "case_identifier": case_identifier,
            "alert_identifier": alert_identifier,
            "normalized_record_id": source_reference.get("normalized_record_id"),
        },
        "evidence_status": status,
        "fetched_at": _utc_now(),
    }
    row_hash = _marker_hash(alert, status)
    _insert_evidence_row(db, alert, entry, row_hash)
    return 1


def _insert_evidence_row(db, alert, entry, row_hash):
    db.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, case_identifier, alert_identifier,
             match_identifier, risk_identifier, profile_identifier, evidence_type,
             matched_subject_name, relationship_to_client, match_category, risk_indicator,
             match_confidence, source_title, source_name, source_url, source_url_available,
             source_url_unavailable_reason, publication_date, snippet, provider_case_url,
             evidence_json, raw_provider_reference, evidence_status, evidence_hash, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(monitoring_alert_id, evidence_hash)
        DO UPDATE SET
            application_id = EXCLUDED.application_id,
            matched_subject_name = EXCLUDED.matched_subject_name,
            relationship_to_client = EXCLUDED.relationship_to_client,
            match_category = EXCLUDED.match_category,
            risk_indicator = EXCLUDED.risk_indicator,
            match_confidence = EXCLUDED.match_confidence,
            source_title = EXCLUDED.source_title,
            source_name = EXCLUDED.source_name,
            source_url = EXCLUDED.source_url,
            source_url_available = EXCLUDED.source_url_available,
            source_url_unavailable_reason = EXCLUDED.source_url_unavailable_reason,
            publication_date = EXCLUDED.publication_date,
            snippet = EXCLUDED.snippet,
            provider_case_url = EXCLUDED.provider_case_url,
            evidence_json = EXCLUDED.evidence_json,
            raw_provider_reference = EXCLUDED.raw_provider_reference,
            evidence_status = EXCLUDED.evidence_status,
            fetched_at = EXCLUDED.fetched_at,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            _row_value(alert, "id"),
            _row_value(alert, "application_id"),
            entry.get("provider") or COMPLYADVANTAGE_PROVIDER_NAME,
            entry.get("case_identifier"),
            entry.get("alert_identifier"),
            entry.get("match_identifier"),
            entry.get("risk_identifier"),
            entry.get("profile_identifier"),
            entry.get("evidence_type"),
            entry.get("matched_subject_name"),
            entry.get("relationship_to_client"),
            entry.get("match_category"),
            entry.get("risk_indicator"),
            str(entry.get("match_confidence") or ""),
            entry.get("source_title"),
            entry.get("source_name"),
            entry.get("source_url"),
            bool(entry.get("source_url_available")),
            entry.get("source_url_unavailable_reason"),
            entry.get("publication_date"),
            entry.get("snippet"),
            entry.get("provider_case_url"),
            _json(entry.get("evidence_json") or {}),
            _json(entry.get("raw_provider_reference") or {}),
            entry.get("evidence_status") or "fetched",
            row_hash,
            entry.get("fetched_at") or _utc_now(),
        ),
    )


def _existing_evidence_count(db, alert_id):
    row = db.execute(
        "SELECT COUNT(*) AS count FROM monitoring_alert_evidence WHERE monitoring_alert_id = ?",
        (alert_id,),
    ).fetchone()
    return int(_row_value(row, "count") or 0)


def _is_compliance_provider_alert(alert, source_reference):
    values = [
        _row_value(alert, "provider"),
        _row_value(alert, "detected_by"),
        source_reference.get("provider"),
        source_reference.get("detected_by"),
    ]
    return any(COMPLYADVANTAGE_PROVIDER_NAME in str(value or "").strip().lower() for value in values)


def _normalized_from_row(row):
    if not row:
        return None
    parsed = _safe_json_loads(_row_value(row, "normalized_report_json") or "{}")
    return parsed if isinstance(parsed, dict) and parsed else None


def _subject_kind(source_reference, person_key):
    text = " ".join(str(value or "").lower() for value in (
        source_reference.get("subject_scope"),
        source_reference.get("subject_type"),
        _nested(source_reference, "screening_subject", "kind"),
        person_key,
    ))
    if "ubo" in text:
        return "ubo"
    if "director" in text or "dir" in text:
        return "director"
    if "entity" in text or "company" in text:
        return "entity"
    return "subject" if person_key else "entity"


def _marker_hash(alert, status):
    stable = {
        "monitoring_alert_id": _row_value(alert, "id"),
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "evidence_status": status,
        "evidence_type": "provider_evidence_status",
    }
    raw = json.dumps(stable, sort_keys=True, default=str, separators=(",", ":"))
    return "status-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:33]


def _row_value(row, key, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        if isinstance(row, dict):
            return row.get(key, default)
        return default


def _safe_json_loads(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nested(value, *keys):
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_non_empty(*values):
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _json(value):
    return json.dumps(value or {}, sort_keys=True, default=str, separators=(",", ":"))


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _commit(db):
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()
