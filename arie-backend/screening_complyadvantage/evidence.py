"""Structured evidence extraction for ComplyAdvantage monitoring alerts."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME


SOURCE_LINK_UNAVAILABLE = "Source article link not available from ComplyAdvantage payload."

# Operator-configured deep-link to the provider console for a matched profile.
# Set this to the real ComplyAdvantage console URL scheme for the tenant, e.g.
#   COMPLYADVANTAGE_CONSOLE_PROFILE_URL_TEMPLATE=https://app.complyadvantage.com/#/profiles/{profile_id}
# Supported tokens: {profile_id}, {alert_id}, {case_id}, {risk_id}. We NEVER guess
# or hardcode a provider URL: when the template is unset, we leave provider_case_url
# empty and the "Open in ComplyAdvantage" button stays hidden (current behavior).
CONSOLE_PROFILE_URL_TEMPLATE_ENV = "COMPLYADVANTAGE_CONSOLE_PROFILE_URL_TEMPLATE"


def _console_profile_url(identifiers: dict[str, Any]) -> str:
    """Build an operator-configured provider-console deep-link, or "" if unavailable.

    Honesty contract (mirrors the front-end "provider URLs are never constructed"
    rule): a link is produced ONLY when the operator has set the console URL
    template AND every token the template references resolves to a real provider
    identifier AND no unresolved placeholder remains AND the result is an https URL.
    Any of those failing yields "" — no guessed, partial, malformed, or non-https
    link ever ships. Tokens must occupy the path/query, never the host: identifier
    values are percent-encoded, so a provider-controlled id cannot alter the host.
    """
    template = (os.environ.get(CONSOLE_PROFILE_URL_TEMPLATE_ENV) or "").strip()
    if not template:
        return ""
    url = template
    resolved_any = False
    for token in ("profile_id", "alert_id", "case_id", "risk_id"):
        placeholder = "{" + token + "}"
        if placeholder not in url:
            continue
        value = identifiers.get(token)
        text = str(value).strip() if value not in (None, "") else ""
        if not text:
            return ""  # template needs this id but the match has none — no partial link
        url = url.replace(placeholder, quote(text, safe=""))
        resolved_any = True
    if not resolved_any:
        return ""  # template carried no recognized token — refuse a non-specific link
    if "{" in url or "}" in url:
        # A brace survived: the template referenced an unrecognized/misspelled token
        # (resolved identifier values are percent-encoded, so any literal brace here
        # can only be an unresolved placeholder). Refuse the broken link.
        return ""
    if not url.lower().startswith("https://"):
        return ""  # provider consoles are TLS-only — never emit a non-https link
    return url


def extract_monitoring_evidence(normalized_report: dict[str, Any] | None, *, case_identifier: str | None = None,
                                alert_identifier: str | None = None) -> list[dict[str, Any]]:
    """Return officer-safe, structured evidence rows from normalized CA provider truth."""
    report = normalized_report if isinstance(normalized_report, dict) else {}
    provider = (report.get("provider_specific") or {}).get(COMPLYADVANTAGE_PROVIDER_NAME) or {}
    matches = provider.get("matches") if isinstance(provider.get("matches"), list) else []
    fallback_alert_id = alert_identifier or _first_alert_identifier(report)
    fetched_at = _utc_now()
    evidence = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        indicators = match.get("indicators") if isinstance(match.get("indicators"), list) else []
        if not indicators:
            evidence.append(_entry_from_match(match, {}, case_identifier, fallback_alert_id, fetched_at))
            continue
        for indicator in indicators:
            if not isinstance(indicator, dict):
                continue
            evidence.append(_entry_from_match(match, indicator, case_identifier, fallback_alert_id, fetched_at))
    return evidence


def evidence_hash(entry: dict[str, Any]) -> str:
    stable = {
        key: entry.get(key)
        for key in (
            "provider",
            "case_identifier",
            "alert_identifier",
            "risk_identifier",
            "profile_identifier",
            "evidence_type",
            "source_title",
            "source_url",
            "publication_date",
        )
    }
    raw = json.dumps(stable, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _entry_from_match(match: dict[str, Any], indicator: dict[str, Any], case_identifier: str | None,
                      alert_identifier: str | None, fetched_at: str) -> dict[str, Any]:
    value = indicator.get("value") if isinstance(indicator.get("value"), dict) else {}
    match_details = _match_details(match.get("profile"))
    source_metadata = value.get("source_metadata") if isinstance(value.get("source_metadata"), dict) else {}
    source_url = _first_non_empty(
        value.get("url"),
        value.get("source_url"),
        value.get("raw_url"),
        _nested(value, "canonical_url", "url"),
        _nested(value, "source", "url"),
        source_metadata.get("url"),
    )
    entry = {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "case_identifier": case_identifier,
        "alert_identifier": alert_identifier,
        "risk_identifier": _first_non_empty(match.get("risk_id"), match.get("risk_identifier")),
        "profile_identifier": match.get("profile_identifier"),
        "evidence_type": _evidence_type(indicator, match),
        "matched_subject_name": _first_non_empty(
            _matched_name(match.get("profile")),
            match_details.get("matched_name"),
            match.get("matched_name"),
            match.get("profile_identifier"),
        ),
        "relationship_to_client": _relationship_to_client(match),
        "match_category": _match_category(indicator, match),
        "risk_indicator": _first_non_empty(indicator.get("taxonomy_label"), indicator.get("taxonomy_key"), indicator.get("type")),
        "match_confidence": _first_non_empty(
            match_details.get("match_score"),
            match.get("match_score"),
            match.get("confidence"),
        ),
        "source_title": _first_non_empty(
            value.get("title"),
            value.get("headline"),
            value.get("name"),
            value.get("list_name"),
            value.get("authority"),
            value.get("position"),
        ),
        "source_name": _first_non_empty(
            value.get("source_name"),
            value.get("publisher"),
            value.get("source_type"),
            source_metadata.get("source_name"),
            source_metadata.get("source_identifier"),
            source_metadata.get("source_type"),
            value.get("authority"),
            value.get("list_name"),
        ),
        "source_url": source_url,
        "source_url_available": bool(source_url),
        "source_url_unavailable_reason": "" if source_url else SOURCE_LINK_UNAVAILABLE,
        "publication_date": _first_non_empty(
            value.get("publication_date"),
            value.get("published_at"),
            value.get("date"),
            value.get("start_date"),
            value.get("active_start_date"),
        ),
        "snippet": _snippet(value),
        "provider_case_url": _console_profile_url({
            "profile_id": match.get("profile_identifier"),
            "alert_id": alert_identifier,
            "case_id": case_identifier,
            "risk_id": _first_non_empty(match.get("risk_id"), match.get("risk_identifier")),
        }),
        "raw_provider_reference": {
            "profile_identifier": match.get("profile_identifier"),
            "risk_identifier": _first_non_empty(match.get("risk_id"), match.get("risk_identifier")),
            "indicator_type": indicator.get("type"),
            "taxonomy_key": indicator.get("taxonomy_key"),
        },
        "evidence_status": "fetched",
        "fetched_at": fetched_at,
    }
    entry["evidence_json"] = {
        key: value
        for key, value in {
            "indicator": _safe_subset(indicator, ("type", "taxonomy_key", "taxonomy_label", "value")),
            "rollups": match.get("rollups") if isinstance(match.get("rollups"), dict) else None,
            "relationships": match.get("relationships") if isinstance(match.get("relationships"), list) else None,
            "raw_extras": match.get("raw_extras") if isinstance(match.get("raw_extras"), dict) else None,
        }.items()
        if value not in (None, "", [], {})
    }
    return entry


def _first_alert_identifier(normalized_report: dict[str, Any]) -> str | None:
    provider = (normalized_report.get("provider_specific") or {}).get(COMPLYADVANTAGE_PROVIDER_NAME) or {}
    workflow = ((provider.get("workflows") or {}).get("strict") or {})
    alerts = workflow.get("alerts") if isinstance(workflow.get("alerts"), list) else []
    for alert in alerts:
        if isinstance(alert, dict):
            value = _first_non_empty(alert.get("identifier"), alert.get("id"))
        else:
            value = alert
        if value:
            return str(value)
    return None


def _evidence_type(indicator: dict[str, Any], match: dict[str, Any]) -> str:
    text = " ".join(str(v or "").lower() for v in (
        indicator.get("type"),
        indicator.get("taxonomy_key"),
        indicator.get("taxonomy_label"),
        match.get("match_category"),
    ))
    if "media" in text:
        return "adverse_media"
    if "pep" in text or "political" in text:
        return "pep"
    if "sanction" in text:
        return "sanctions"
    if "watchlist" in text or "law_enforcement" in text:
        return "watchlist"
    return "screening_match"


def _match_category(indicator: dict[str, Any], match: dict[str, Any]) -> str:
    category = _first_non_empty(indicator.get("taxonomy_label"), indicator.get("taxonomy_key"), indicator.get("type"))
    if category:
        return str(category).replace("_", " ")
    categories = match.get("match_categories") if isinstance(match.get("match_categories"), list) else []
    return ", ".join(str(item) for item in categories if item)


def _relationship_to_client(match: dict[str, Any]) -> str:
    subject = match.get("screening_subject") if isinstance(match.get("screening_subject"), dict) else {}
    text = " ".join(str(value or "").lower() for value in (
        match.get("subject_scope"),
        match.get("screening_subject_kind"),
        subject.get("scope"),
        subject.get("kind"),
        subject.get("person_key"),
    ))
    if "ubo" in text:
        return "UBO"
    if "director" in text or "dir" in text:
        return "Director"
    if "shareholder" in text:
        return "Shareholder"
    if "intermediary" in text:
        return "Intermediary"
    if "person" in text or "subject" in text:
        return "Associated person"
    if "entity" in text or "company" in text:
        return "Company"
    return "Unknown"


def _matched_name(profile: Any) -> str:
    if not isinstance(profile, dict):
        return ""
    for root in ("person", "company"):
        names = (((profile.get(root) or {}).get("names") or {}).get("values") or [])
        if isinstance(names, list):
            for item in names:
                if isinstance(item, dict) and item.get("name"):
                    return str(item["name"])
    return ""


def _match_details(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    details = profile.get("match_details")
    return details if isinstance(details, dict) else {}


def _snippet(value: dict[str, Any]) -> str:
    snippets = value.get("snippets")
    if isinstance(snippets, list) and snippets:
        first = snippets[0]
        if isinstance(first, dict):
            return str(first.get("text") or "")
        return str(first)
    return str(_first_non_empty(
        value.get("snippet"),
        value.get("summary"),
        value.get("reason"),
        value.get("position"),
        value.get("status"),
    ) or "")


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _safe_subset(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: value.get(key) for key in keys if value.get(key) not in (None, "", [], {})}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
