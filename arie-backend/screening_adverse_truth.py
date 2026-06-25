"""ComplyAdvantage screening/adverse-media source-of-truth projection.

This module intentionally sits above the lower-level ``screening_state``
readiness model. ``screening_state`` answers whether a stored screening record
is live, terminal, stale, or approval-blocking. This module adds the provider
authority rule, adverse-media monitoring evidence, officer dispositions, and
the explicit approval-effect vocabulary used by approval gates and the Case
Command Centre.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from screening_state import build_screening_truth_summary


SCREENING_PROVIDER = "complyadvantage"
SCREENING_PROVIDER_DISPLAY = "ComplyAdvantage Mesh"
IDV_PROVIDER = "sumsub"
IDV_PROVIDER_DISPLAY = "Sumsub IDV/KYC"

STATE_CLEAR = "clear"
STATE_POSSIBLE_MATCH = "possible_match"
STATE_TRUE_MATCH = "true_match"
STATE_FALSE_POSITIVE = "false_positive"
STATE_UNRESOLVED = "unresolved"
STATE_STALE = "stale"
STATE_EXPIRED = "expired"
STATE_PROVIDER_FAILED = "provider_failed"
STATE_MATERIAL_CONCERN = "material_concern"
STATE_PEP_DETECTED = "pep_detected"
STATE_SANCTIONS_HIT = "sanctions_hit"
STATE_ADVERSE_MEDIA_HIT = "adverse_media_hit"
STATE_ADVERSE_MEDIA_FALSE_POSITIVE = "adverse_media_false_positive"
STATE_CLEARED_BY_OFFICER = "cleared_by_officer"
STATE_ESCALATED_TO_COMPLIANCE = "escalated_to_compliance"
STATE_SECOND_REVIEW_REQUIRED = "second_review_required"

MATERIALITY_NONE = "none"
MATERIALITY_LOW = "low"
MATERIALITY_MEDIUM = "medium"
MATERIALITY_HIGH = "high"
MATERIALITY_CRITICAL = "critical"

EFFECT_ALLOW = "allow_direct_approval"
EFFECT_BLOCK = "block_until_review"
EFFECT_COMPLIANCE = "submit_to_compliance_required"
EFFECT_PROHIBITED = "prohibited_fail_closed"

FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_EXPIRED = "expired"
FRESHNESS_UNKNOWN = "unknown"

SOURCE_URL_UNAVAILABLE_MESSAGE = (
    "Source article link not available from ComplyAdvantage Mesh payload."
)

_EFFECT_RANK = {
    EFFECT_ALLOW: 0,
    EFFECT_BLOCK: 1,
    EFFECT_COMPLIANCE: 2,
    EFFECT_PROHIBITED: 3,
}

_FALSE_POSITIVE_ACTIONS = {
    "false_positive",
    "false_positive_cleared",
    "no_material_impact",
    "dismissed_false_positive",
}

_TERMINAL_FALSE_POSITIVE_STATUSES = {"dismissed", "resolved", "closed"}

_ESCALATION_ACTIONS = {
    "route_to_edd",
    "routed_to_edd",
    "escalated_to_edd",
    "escalated_to_compliance",
    "submit_to_compliance",
    "sar_filed",
}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _token(value) in {
        "1",
        "true",
        "t",
        "yes",
        "y",
        "match",
        "matched",
        "hit",
        "material",
        "risk",
        "review",
    }


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _text(value)
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _provider_is_complyadvantage(*values: Any) -> bool:
    for value in values:
        token = _token(value)
        if token in {"complyadvantage", "complyadvantage_mesh", "ca_mesh", "ca"}:
            return True
        if "complyadvantage" in token:
            return True
    return False


def _provider_is_sumsub(*values: Any) -> bool:
    return any(_token(value) == "sumsub" for value in values)


def _freshness_state(prescreening: Mapping[str, Any], screening_report: Mapping[str, Any], *, now: datetime | None = None) -> tuple[str, list[str]]:
    now = now or datetime.now(timezone.utc)
    screened_at = _parse_timestamp(
        screening_report.get("screened_at")
        or screening_report.get("timestamp")
        or prescreening.get("last_screened_at")
    )
    valid_until = _parse_timestamp(prescreening.get("screening_valid_until"))
    input_updated_at = _parse_timestamp(
        prescreening.get("screening_input_updated_at")
        or prescreening.get("risk_inputs_updated_at")
        or prescreening.get("inputs_updated_at")
        or prescreening.get("submitted_at")
    )
    reasons: list[str] = []
    if valid_until and now > valid_until:
        return FRESHNESS_EXPIRED, ["screening_valid_until_elapsed"]
    if screened_at and input_updated_at and input_updated_at > screened_at:
        return FRESHNESS_STALE, ["screening_inputs_updated_after_screening"]
    if screened_at or valid_until:
        return FRESHNESS_FRESH, reasons
    return FRESHNESS_UNKNOWN, ["screening_timestamp_unknown"]


def _iter_screening_records(report: Mapping[str, Any]):
    company = report.get("company_screening")
    if isinstance(company, Mapping):
        yield "entity", company.get("company_name") or company.get("name") or "", company
        for key in ("sanctions", "adverse_media", "watchlist", "pep"):
            sub = company.get(key)
            if isinstance(sub, Mapping):
                yield "entity", company.get("company_name") or company.get("name") or "", sub
    for bucket, subject_type in (
        ("director_screenings", "director"),
        ("ubo_screenings", "ubo"),
        ("intermediary_screenings", "intermediary"),
        ("kyc_applicants", "applicant"),
    ):
        for item in report.get(bucket) or []:
            if not isinstance(item, Mapping):
                continue
            name = item.get("person_name") or item.get("entity_name") or item.get("name") or ""
            screening = item.get("screening") if isinstance(item.get("screening"), Mapping) else item
            yield subject_type, name, screening
    for key in ("sanctions", "kyc"):
        record = report.get(key)
        if isinstance(record, Mapping):
            yield "legacy", key, record


def _result_categories(result: Mapping[str, Any]) -> set[str]:
    categories: set[str] = set()
    if _truthy(result.get("is_sanctioned")) or _truthy(result.get("sanctions")):
        categories.add("sanctions")
    if _truthy(result.get("is_pep")) or _truthy(result.get("pep")):
        categories.add("pep")
    if _truthy(result.get("is_adverse_media")) or _truthy(result.get("adverse_media")):
        categories.add("adverse_media")
    raw_categories = []
    for key in ("match_category", "match_categories", "categories", "risk_type_labels", "risk_indicator"):
        value = result.get(key)
        if isinstance(value, str):
            raw_categories.append(value)
        elif isinstance(value, (list, tuple, set)):
            raw_categories.extend(value)
    text = " ".join(str(value or "").lower() for value in raw_categories)
    if "sanction" in text:
        categories.add("sanctions")
    if "watchlist" in text or "law_enforcement" in text or "law enforcement" in text:
        categories.add("watchlist")
    if "pep" in text or "political" in text:
        categories.add("pep")
    if "adverse" in text or "media" in text or "negative_news" in text:
        categories.add("adverse_media")
    return categories


def _screening_flags(report: Mapping[str, Any]) -> dict[str, bool]:
    flags = {
        "sanctions": _truthy(report.get("any_sanctions_hits")),
        "pep": _truthy(report.get("any_pep_hits")),
        "adverse_media": _truthy(report.get("has_adverse_media_hit")),
        "watchlist": False,
        "matched": False,
    }
    for _subject_type, _name, record in _iter_screening_records(report):
        if _truthy(record.get("matched")):
            flags["matched"] = True
        for result in record.get("results") or []:
            if not isinstance(result, Mapping):
                continue
            flags["matched"] = True
            for category in _result_categories(result):
                flags[category] = True
    return flags


def _declared_pep_present(app: Mapping[str, Any], prescreening: Mapping[str, Any], report: Mapping[str, Any]) -> bool:
    for key in ("declared_pep_present", "has_declared_pep", "client_declared_pep", "declared_pep"):
        if _truthy(prescreening.get(key)) or _truthy(app.get(key)):
            return True
    for bucket in ("director_screenings", "ubo_screenings", "intermediary_screenings"):
        for item in report.get(bucket) or []:
            if isinstance(item, Mapping) and _truthy(item.get("declared_pep")):
                return True
    return False


def _latest_review_code(screening_reviews: list[Mapping[str, Any]]) -> str:
    for review in screening_reviews:
        code = _token(
            review.get("disposition_code")
            or review.get("canonical_disposition")
            or review.get("review_disposition_code")
            or review.get("disposition")
        )
        if code:
            return code
    return ""


def _second_review_required(screening_reviews: list[Mapping[str, Any]]) -> bool:
    for review in screening_reviews:
        if _truthy(review.get("requires_four_eyes")) or _truthy(review.get("second_review_required")):
            if not review.get("second_reviewer_id") and not review.get("second_reviewed_by"):
                return True
    return False


def _component(category: str, state: str, materiality: str, approval_effect: str, *, source: str, reason: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "provider": SCREENING_PROVIDER,
        "provider_display": SCREENING_PROVIDER_DISPLAY,
        "category": category,
        "state": state,
        "materiality": materiality,
        "approval_effect": approval_effect,
        "source": source,
        "reason": reason,
    }
    payload.update({key: value for key, value in extra.items() if value not in (None, "", [], {})})
    return payload


def _monitoring_alert_terminal_false_positive(alert: Mapping[str, Any]) -> bool:
    action = _token(alert.get("officer_action") or alert.get("outcome"))
    status = _token(alert.get("status"))
    if action in _FALSE_POSITIVE_ACTIONS:
        return True
    if status in _TERMINAL_FALSE_POSITIVE_STATUSES and action in _FALSE_POSITIVE_ACTIONS | {"dismiss", "dismissed"}:
        return True
    return False


def _monitoring_alert_escalated(alert: Mapping[str, Any]) -> bool:
    return _token(alert.get("officer_action") or alert.get("status") or alert.get("outcome")) in _ESCALATION_ACTIONS


def _monitoring_alert_category(alert: Mapping[str, Any]) -> str | None:
    text = " ".join(
        str(value or "").lower()
        for value in (
            alert.get("alert_type"),
            alert.get("summary"),
            alert.get("source_reference"),
            alert.get("detected_by"),
        )
    )
    if "sanction" in text:
        return "sanctions"
    if "watchlist" in text:
        return "watchlist"
    if "pep" in text:
        return "pep"
    if "media" in text or "adverse" in text:
        return "adverse_media"
    return None


def _source_link_rows(
    monitoring_alerts: list[Mapping[str, Any]],
    monitoring_alert_evidence: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    alert_by_id = {str(alert.get("id")): alert for alert in monitoring_alerts if alert.get("id") is not None}
    seen_alerts: set[str] = set()
    for evidence in monitoring_alert_evidence:
        category_text = " ".join(
            str(evidence.get(key) or "").lower()
            for key in ("evidence_type", "match_category", "risk_indicator")
        )
        if "media" not in category_text and "adverse" not in category_text:
            continue
        alert_id = str(evidence.get("monitoring_alert_id") or "")
        seen_alerts.add(alert_id)
        url = _text(evidence.get("source_url"))
        rows.append({
            "monitoring_alert_id": evidence.get("monitoring_alert_id"),
            "case_identifier": evidence.get("case_identifier"),
            "source_title": evidence.get("source_title"),
            "source_name": evidence.get("source_name"),
            "source_url": url,
            "source_url_available": bool(url or evidence.get("source_url_available")),
            "source_url_status": "available" if url else "unavailable",
            "source_url_unavailable_reason": (
                evidence.get("source_url_unavailable_reason")
                or ("" if url else SOURCE_URL_UNAVAILABLE_MESSAGE)
            ),
            "publication_date": evidence.get("publication_date"),
            "snippet": evidence.get("snippet"),
        })
    for alert in monitoring_alerts:
        alert_id = str(alert.get("id") or "")
        if alert_id in seen_alerts:
            continue
        if _monitoring_alert_category(alert) == "adverse_media":
            rows.append({
                "monitoring_alert_id": alert.get("id"),
                "case_identifier": alert.get("case_identifier"),
                "source_url": "",
                "source_url_available": False,
                "source_url_status": "unavailable",
                "source_url_unavailable_reason": SOURCE_URL_UNAVAILABLE_MESSAGE,
            })
    return rows


def _load_rows(db, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    rows = db.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def load_monitoring_truth_inputs(db, application_id: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load monitoring alerts and evidence needed by the SOT projection."""
    if not db or not application_id:
        return [], []
    alerts = _load_rows(
        db,
        """
        SELECT id, application_id, provider, case_identifier, alert_type, severity,
               detected_by, summary, source_reference, status, officer_action,
               officer_notes, reviewed_at, reviewed_by, resolved_at, created_at
          FROM monitoring_alerts
         WHERE application_id = ?
         ORDER BY id ASC
        """,
        (application_id,),
    )
    evidence = _load_rows(
        db,
        """
        SELECT monitoring_alert_id, application_id, provider, case_identifier,
               alert_identifier, match_identifier, risk_identifier,
               profile_identifier, evidence_type, matched_subject_name,
               relationship_to_client, match_category, risk_indicator,
               match_confidence, source_title, source_name, source_url,
               source_url_available, source_url_unavailable_reason,
               publication_date, snippet, provider_case_url, evidence_status,
               fetched_at, created_at
          FROM monitoring_alert_evidence
         WHERE application_id = ?
         ORDER BY id ASC
        """,
        (application_id,),
    )
    return alerts, evidence


def build_screening_adverse_truth_summary(
    app: Mapping[str, Any] | None = None,
    *,
    prescreening: Mapping[str, Any] | None = None,
    screening_report: Mapping[str, Any] | None = None,
    screening_reviews: list[Mapping[str, Any]] | None = None,
    monitoring_alerts: list[Mapping[str, Any]] | None = None,
    monitoring_alert_evidence: list[Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the unified CA screening/adverse-media truth summary."""
    app = app if isinstance(app, Mapping) else {}
    prescreening = (
        dict(prescreening)
        if isinstance(prescreening, Mapping)
        else _json_object(app.get("prescreening_data"))
    )
    report = (
        dict(screening_report)
        if isinstance(screening_report, Mapping)
        else prescreening.get("screening_report")
    )
    report = report if isinstance(report, Mapping) else {}
    reviews = [dict(review) for review in (screening_reviews or []) if isinstance(review, Mapping)]
    alerts = [dict(alert) for alert in (monitoring_alerts or []) if isinstance(alert, Mapping)]
    evidence = [dict(item) for item in (monitoring_alert_evidence or []) if isinstance(item, Mapping)]

    freshness, freshness_reasons = _freshness_state(prescreening, report, now=now)
    base_truth = build_screening_truth_summary(report, prescreening, reviews)
    required_evidence = list(base_truth.get("required_evidence") or [])
    ca_records = [
        item for item in required_evidence
        if _provider_is_complyadvantage(item.get("provider"), item.get("source"))
    ]
    sumsub_records = [
        item for item in required_evidence
        if _provider_is_sumsub(item.get("provider"), item.get("source"))
    ]
    report_provider_ca = _provider_is_complyadvantage(
        report.get("provider"),
        report.get("source"),
        report.get("screening_provider"),
        (report.get("company_screening") or {}).get("provider") if isinstance(report.get("company_screening"), Mapping) else None,
        (report.get("company_screening") or {}).get("source") if isinstance(report.get("company_screening"), Mapping) else None,
    )
    report_provider_sumsub = _provider_is_sumsub(
        report.get("provider"),
        report.get("source"),
        report.get("screening_provider"),
        (report.get("company_screening") or {}).get("provider") if isinstance(report.get("company_screening"), Mapping) else None,
        (report.get("company_screening") or {}).get("source") if isinstance(report.get("company_screening"), Mapping) else None,
    )
    has_ca_authority = bool(ca_records or report_provider_ca)
    legacy_non_authoritative = bool(sumsub_records or report_provider_sumsub or required_evidence) and not has_ca_authority
    flags = _screening_flags(report)
    review_code = _latest_review_code(reviews)
    components: list[dict[str, Any]] = []

    if _second_review_required(reviews):
        components.append(_component(
            "aml",
            STATE_SECOND_REVIEW_REQUIRED,
            MATERIALITY_MEDIUM,
            EFFECT_BLOCK,
            source="screening_reviews",
            reason="screening_second_review_required",
        ))

    if freshness == FRESHNESS_EXPIRED:
        components.append(_component(
            "aml",
            STATE_EXPIRED,
            MATERIALITY_MEDIUM,
            EFFECT_BLOCK,
            source="screening_freshness",
            reason="screening_expired",
        ))
    elif freshness == FRESHNESS_STALE:
        components.append(_component(
            "aml",
            STATE_STALE,
            MATERIALITY_MEDIUM,
            EFFECT_BLOCK,
            source="screening_freshness",
            reason="screening_stale",
        ))

    if report and not has_ca_authority:
        components.append(_component(
            "aml",
            STATE_UNRESOLVED,
            MATERIALITY_MEDIUM,
            EFFECT_BLOCK,
            source="legacy_screening_report",
            reason="complyadvantage_truth_missing_legacy_screening_non_authoritative",
            legacy_non_authoritative=True,
        ))
    elif not report:
        components.append(_component(
            "provider_failure",
            STATE_UNRESOLVED,
            MATERIALITY_MEDIUM,
            EFFECT_BLOCK,
            source="screening_report",
            reason="screening_report_missing",
        ))

    canonical_state = _token(base_truth.get("canonical_state"))
    if has_ca_authority:
        if canonical_state in {"failed", "not_configured"}:
            components.append(_component(
                "provider_failure",
                STATE_PROVIDER_FAILED,
                MATERIALITY_HIGH,
                EFFECT_BLOCK,
                source="screening_truth_summary",
                reason=canonical_state or "provider_failed",
            ))
        elif canonical_state in {"pending", "pending_provider", "partial_result", "not_started", "sandbox_provider", "simulated_fallback"}:
            components.append(_component(
                "aml",
                STATE_UNRESOLVED,
                MATERIALITY_MEDIUM,
                EFFECT_BLOCK,
                source="screening_truth_summary",
                reason=canonical_state or "screening_unresolved",
            ))
        elif canonical_state == "completed_match":
            if base_truth.get("has_formally_cleared_match") and not base_truth.get("has_uncleared_completed_match"):
                components.append(_component(
                    "aml",
                    STATE_FALSE_POSITIVE,
                    MATERIALITY_NONE,
                    EFFECT_ALLOW,
                    source="screening_reviews",
                    reason="officer_false_positive_clearance",
                ))
                components.append(_component(
                    "aml",
                    STATE_CLEARED_BY_OFFICER,
                    MATERIALITY_NONE,
                    EFFECT_ALLOW,
                    source="screening_reviews",
                    reason="screening_match_cleared_by_officer",
                ))
            elif review_code in {"true_match", "confirmed_match"}:
                components.append(_component(
                    "aml",
                    STATE_TRUE_MATCH,
                    MATERIALITY_HIGH,
                    EFFECT_COMPLIANCE,
                    source="screening_reviews",
                    reason="officer_confirmed_true_match",
                ))
            elif review_code in {"material_concern", "escalated_to_edd", "escalated_to_compliance"}:
                components.append(_component(
                    "aml",
                    STATE_MATERIAL_CONCERN,
                    MATERIALITY_HIGH,
                    EFFECT_COMPLIANCE,
                    source="screening_reviews",
                    reason=review_code,
                ))
            elif flags["sanctions"]:
                components.append(_component(
                    "sanctions",
                    STATE_SANCTIONS_HIT,
                    MATERIALITY_CRITICAL,
                    EFFECT_PROHIBITED,
                    source="complyadvantage_screening",
                    reason="sanctions_or_watchlist_hit",
                ))
            elif flags["pep"]:
                components.append(_component(
                    "pep",
                    STATE_PEP_DETECTED,
                    MATERIALITY_HIGH,
                    EFFECT_COMPLIANCE,
                    source="complyadvantage_screening",
                    reason="provider_detected_pep",
                ))
            elif flags["adverse_media"]:
                components.append(_component(
                    "adverse_media",
                    STATE_ADVERSE_MEDIA_HIT,
                    MATERIALITY_HIGH,
                    EFFECT_COMPLIANCE,
                    source="complyadvantage_screening",
                    reason="provider_detected_adverse_media",
                ))
            elif flags["watchlist"]:
                components.append(_component(
                    "watchlist",
                    STATE_TRUE_MATCH,
                    MATERIALITY_HIGH,
                    EFFECT_COMPLIANCE,
                    source="complyadvantage_screening",
                    reason="watchlist_hit",
                ))
            else:
                components.append(_component(
                    "aml",
                    STATE_POSSIBLE_MATCH,
                    MATERIALITY_MEDIUM,
                    EFFECT_BLOCK,
                    source="complyadvantage_screening",
                    reason="possible_match_requires_officer_review",
                ))
        elif canonical_state == "completed_clear" and not components:
            components.append(_component(
                "aml",
                STATE_CLEAR,
                MATERIALITY_NONE,
                EFFECT_ALLOW,
                source="complyadvantage_screening",
                reason="live_terminal_clear",
            ))

    if _declared_pep_present(app, prescreening, report):
        components.append(_component(
            "pep",
            STATE_PEP_DETECTED,
            MATERIALITY_HIGH,
            EFFECT_COMPLIANCE,
            source="client_declaration",
            reason="declared_pep",
            declared=True,
        ))

    for alert in alerts:
        if not _provider_is_complyadvantage(alert.get("provider"), alert.get("detected_by")):
            continue
        category = _monitoring_alert_category(alert)
        if not category:
            continue
        if _monitoring_alert_terminal_false_positive(alert):
            state = STATE_ADVERSE_MEDIA_FALSE_POSITIVE if category == "adverse_media" else STATE_FALSE_POSITIVE
            components.append(_component(
                category,
                state,
                MATERIALITY_NONE,
                EFFECT_ALLOW,
                source="monitoring_alerts",
                reason="monitoring_alert_false_positive_or_no_material_impact",
                monitoring_alert_id=alert.get("id"),
            ))
            continue
        if _monitoring_alert_escalated(alert):
            components.append(_component(
                category,
                STATE_ESCALATED_TO_COMPLIANCE,
                MATERIALITY_HIGH,
                EFFECT_COMPLIANCE,
                source="monitoring_alerts",
                reason="monitoring_alert_escalated",
                monitoring_alert_id=alert.get("id"),
            ))
            continue
        if category == "sanctions":
            components.append(_component(
                category,
                STATE_SANCTIONS_HIT,
                MATERIALITY_CRITICAL,
                EFFECT_PROHIBITED,
                source="monitoring_alerts",
                reason="monitoring_sanctions_hit",
                monitoring_alert_id=alert.get("id"),
            ))
        elif category == "pep":
            components.append(_component(
                category,
                STATE_PEP_DETECTED,
                MATERIALITY_HIGH,
                EFFECT_COMPLIANCE,
                source="monitoring_alerts",
                reason="monitoring_pep_hit",
                monitoring_alert_id=alert.get("id"),
            ))
        elif category == "adverse_media":
            components.append(_component(
                category,
                STATE_ADVERSE_MEDIA_HIT,
                MATERIALITY_HIGH,
                EFFECT_COMPLIANCE,
                source="monitoring_alerts",
                reason="monitoring_adverse_media_hit",
                monitoring_alert_id=alert.get("id"),
            ))
        else:
            components.append(_component(
                category,
                STATE_TRUE_MATCH,
                MATERIALITY_HIGH,
                EFFECT_COMPLIANCE,
                source="monitoring_alerts",
                reason="monitoring_watchlist_hit",
                monitoring_alert_id=alert.get("id"),
            ))

    if not components:
        components.append(_component(
            "aml",
            STATE_CLEAR,
            MATERIALITY_NONE,
            EFFECT_ALLOW,
            source="complyadvantage_screening",
            reason="no_screening_or_adverse_media_blockers",
        ))

    approval_effect = max(
        (component["approval_effect"] for component in components),
        key=lambda effect: _EFFECT_RANK.get(effect, 0),
    )
    materiality = max(
        (component["materiality"] for component in components),
        key=lambda value: {
            MATERIALITY_NONE: 0,
            MATERIALITY_LOW: 1,
            MATERIALITY_MEDIUM: 2,
            MATERIALITY_HIGH: 3,
            MATERIALITY_CRITICAL: 4,
        }.get(value, 0),
    )
    blocking_reasons = [
        component["reason"]
        for component in components
        if component["approval_effect"] != EFFECT_ALLOW
    ]
    states = [component["state"] for component in components]
    overall_state = next(
        (
            component["state"]
            for component in sorted(
                components,
                key=lambda component: _EFFECT_RANK.get(component["approval_effect"], 0),
                reverse=True,
            )
            if component["approval_effect"] == approval_effect
        ),
        STATE_CLEAR,
    )
    if approval_effect == EFFECT_ALLOW:
        for preferred in (
            STATE_ADVERSE_MEDIA_FALSE_POSITIVE,
            STATE_FALSE_POSITIVE,
            STATE_CLEARED_BY_OFFICER,
            STATE_CLEAR,
        ):
            if preferred in states:
                overall_state = preferred
                break

    return {
        "provider": SCREENING_PROVIDER,
        "provider_display": SCREENING_PROVIDER_DISPLAY,
        "idv_provider": IDV_PROVIDER,
        "idv_provider_display": IDV_PROVIDER_DISPLAY,
        "provider_authority": {
            "screening_adverse_media": SCREENING_PROVIDER,
            "idv": IDV_PROVIDER,
            "sumsub_screening_authoritative": False,
        },
        "authoritative_provider_present": has_ca_authority,
        "legacy_sumsub_screening_present": bool(sumsub_records or report_provider_sumsub),
        "legacy_non_authoritative": legacy_non_authoritative,
        "state": overall_state,
        "states": states,
        "materiality": materiality,
        "approval_effect": approval_effect,
        "approval_blocking": approval_effect in {EFFECT_BLOCK, EFFECT_PROHIBITED},
        "submit_to_compliance_required": approval_effect == EFFECT_COMPLIANCE,
        "prohibited_fail_closed": approval_effect == EFFECT_PROHIBITED,
        "allow_direct_approval": approval_effect == EFFECT_ALLOW,
        "freshness": freshness,
        "freshness_reasons": freshness_reasons,
        "components": components,
        "blocking_reasons": blocking_reasons,
        "approval_blocked_reasons": blocking_reasons,
        "base_screening_truth_summary": base_truth,
        "adverse_media_sources": _source_link_rows(alerts, evidence),
    }


def screening_adverse_truth_blocks_final_approval(summary: Mapping[str, Any]) -> bool:
    return _token(summary.get("approval_effect")) in {EFFECT_BLOCK, EFFECT_PROHIBITED}


def screening_adverse_truth_requires_compliance(summary: Mapping[str, Any]) -> bool:
    return _token(summary.get("approval_effect")) == EFFECT_COMPLIANCE


def screening_adverse_truth_blocker_message(summary: Mapping[str, Any]) -> str:
    effect = _token(summary.get("approval_effect"))
    reasons = "; ".join(summary.get("blocking_reasons") or summary.get("approval_blocked_reasons") or [])
    if effect == EFFECT_PROHIBITED:
        return (
            "ComplyAdvantage Mesh screening/adverse-media truth produced a prohibited fail-closed result"
            + (f": {reasons}." if reasons else ".")
        )
    if effect == EFFECT_BLOCK:
        states = set(summary.get("states") or [])
        if states.intersection({STATE_STALE, STATE_EXPIRED}):
            if STATE_STALE in states:
                return (
                    "Application data with screening-relevant inputs was modified after screening. "
                    "A re-screen is required before approval can proceed"
                    + (f": {reasons}." if reasons else ".")
                )
            base_freshness = (
                (summary.get("base_screening_truth_summary") or {}).get("freshness")
                if isinstance(summary.get("base_screening_truth_summary"), Mapping)
                else {}
            ) or {}
            valid_until = _parse_timestamp(base_freshness.get("screening_valid_until"))
            validity_days = base_freshness.get("screening_validity_days")
            if valid_until:
                age_days = max(0, (datetime.now(timezone.utc) - valid_until).days)
                validity_text = f" (validity period: {validity_days} days)" if validity_days else ""
                return (
                    f"Screening results expired {age_days} day(s) ago{validity_text}. "
                    "A re-screen is required before approval can proceed"
                    + (f": {reasons}." if reasons else ".")
                )
            return (
                "ComplyAdvantage Mesh screening/adverse-media truth is stale or expired. "
                "A re-screen is required before approval can proceed"
                + (f": {reasons}." if reasons else ".")
            )
        return (
            "ComplyAdvantage Mesh screening/adverse-media truth is unresolved or not reliance-ready"
            + (f": {reasons}." if reasons else ".")
        )
    if effect == EFFECT_COMPLIANCE:
        return (
            "ComplyAdvantage Mesh screening/adverse-media truth requires Compliance review"
            + (f": {reasons}." if reasons else ".")
        )
    return ""
