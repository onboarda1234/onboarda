"""Pure mapping from normalized CA report to monitoring_alerts row shape."""

import json

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME

_PROVIDER = COMPLYADVANTAGE_PROVIDER_NAME
_PRIORITY = {"sanctions": 0, "watchlist": 1, "pep": 2, "media": 3}
_SEVERITY = {
    "sanctions": "critical",
    "watchlist": "high",
    "pep": "medium",
    "media": "medium",
}


def map_normalized_to_monitoring_alert(normalized_report, *, case_identifier, customer_identifier, normalized_record_id=None):
    """Build the deterministic one-row-per-CA-case monitoring_alerts payload."""
    matches = _matches(normalized_report)
    top_alert_type = _top_indicator(matches)
    if top_alert_type is None:
        top_alert_type = "media"
    alert_identifier = _first_alert_identifier(normalized_report)
    source_reference = {
        "provider": _PROVIDER,
        "case_identifier": case_identifier,
        "alert_identifier": alert_identifier,
        "normalized_record_id": normalized_record_id,
    }
    subject_scope = _subject_scope(normalized_report)
    if subject_scope:
        source_reference["subject_scope"] = subject_scope
    screening_subject = _screening_subject(normalized_report)
    if screening_subject:
        source_reference["screening_subject"] = screening_subject
    return {
        "provider": _PROVIDER,
        "case_identifier": case_identifier,
        "application_id": normalized_report.get("application_id"),
        "client_name": customer_identifier,
        "alert_type": top_alert_type,
        "severity": _SEVERITY[top_alert_type],
        "detected_by": _PROVIDER,
        "summary": (
            f"CA case {case_identifier} surfaced {len(matches)} match(es); "
            f"top indicator: {top_alert_type} for customer {customer_identifier}"
        ),
        "source_reference": json.dumps(source_reference, sort_keys=True),
        "status": "open",
    }


def _matches(normalized_report):
    provider = normalized_report.get("provider_specific", {}).get(_PROVIDER, {})
    return list(provider.get("matches") or [])


def _top_indicator(matches):
    best = None
    for match in matches:
        for indicator in match.get("indicators") or []:
            kind = _indicator_kind(indicator)
            if kind is None:
                continue
            if best is None or _PRIORITY[kind] < _PRIORITY[best]:
                best = kind
    return best


def _indicator_kind(indicator):
    taxonomy_key = (indicator.get("taxonomy_key") or "").lower()
    indicator_type = (indicator.get("type") or "").lower()
    if "sanction" in indicator_type and not taxonomy_key.startswith("r_sanctions_exposure"):
        return "sanctions"
    if taxonomy_key.startswith("r_direct_sanctions"):
        return "sanctions"
    if "watchlist" in indicator_type or taxonomy_key in {"r_watchlist", "r_law_enforcement"}:
        return "watchlist"
    if taxonomy_key.startswith("r_sanctions_exposure"):
        return "watchlist"
    if "pep" in indicator_type or taxonomy_key.startswith("r_pep") or taxonomy_key == "r_rca":
        return "pep"
    if "media" in indicator_type or taxonomy_key.startswith("r_adverse_media"):
        return "media"
    return None


def _first_alert_identifier(normalized_report):
    workflow = normalized_report.get("provider_specific", {}).get(_PROVIDER, {}).get("workflows", {}).get("strict", {})
    alerts = workflow.get("alerts") or []
    if not alerts:
        return None
    first = alerts[0]
    if isinstance(first, dict):
        return first.get("identifier") or first.get("id")
    return first


def _subject_scope(normalized_report):
    for value in (
        normalized_report.get("subject_scope"),
        _provider(normalized_report).get("subject_scope"),
        (_provider(normalized_report).get("screening_subject") or {}).get("scope"),
    ):
        normalized = _normalize_scope(value)
        if normalized:
            return normalized
    screening_subject = normalized_report.get("screening_subject_kind") or (_provider(normalized_report).get("screening_subject") or {}).get("kind")
    normalized = _normalize_scope(screening_subject)
    if normalized:
        return normalized
    customer_input = _provider(normalized_report).get("customer_input") or {}
    if isinstance(customer_input, dict):
        if isinstance(customer_input.get("company"), dict):
            return "entity"
        if isinstance(customer_input.get("person"), dict):
            return "person"
    if normalized_report.get("company_screening"):
        return "entity"
    if normalized_report.get("director_screenings") or normalized_report.get("ubo_screenings"):
        return "person"
    return None


def _screening_subject(normalized_report):
    subject = _provider(normalized_report).get("screening_subject")
    if isinstance(subject, dict):
        compact = {key: value for key, value in subject.items() if value}
        return compact or None
    kind = normalized_report.get("screening_subject_kind")
    scope = normalized_report.get("subject_scope")
    person_key = normalized_report.get("screening_subject_person_key")
    compact = {
        key: value
        for key, value in {
            "kind": kind,
            "scope": scope,
            "person_key": person_key,
        }.items()
        if value
    }
    return compact or None


def _provider(normalized_report):
    return normalized_report.get("provider_specific", {}).get(_PROVIDER, {})


def _normalize_scope(value):
    value = str(value or "").strip().lower()
    if value in ("entity", "company", "business", "organisation", "organization", "legal_entity"):
        return "entity"
    if value in ("person", "individual", "director", "ubo"):
        return "person"
    return None
