"""Canonical screening freshness metadata helpers.

These helpers are intentionally write-path only. They are called when the
runtime has just executed screening, so it is safe to stamp a canonical
``screened_at`` when a provider adapter omitted the top-level timestamp.
Approval gates must continue to fail closed for persisted reports where the
timestamp is absent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, MutableMapping, Optional

from environment import get_screening_validity_days


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _candidate_screening_timestamps(report: Mapping[str, Any]):
    yield report.get("screened_at")
    yield report.get("timestamp")

    company = report.get("company_screening") or {}
    if isinstance(company, Mapping):
        yield company.get("screened_at")
        yield company.get("searched_at")
        sanctions = company.get("sanctions") or {}
        if isinstance(sanctions, Mapping):
            yield sanctions.get("screened_at")
            yield sanctions.get("timestamp")
            yield sanctions.get("searched_at")

    for collection_name in ("director_screenings", "ubo_screenings"):
        rows = report.get(collection_name) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            yield row.get("screened_at")
            screening = row.get("screening") or {}
            if isinstance(screening, Mapping):
                yield screening.get("screened_at")
                yield screening.get("timestamp")
                yield screening.get("searched_at")


def populate_screening_freshness_metadata(
    prescreening: MutableMapping[str, Any],
    screening_report: MutableMapping[str, Any],
    *,
    screened_by: Optional[str] = None,
    now: Optional[datetime] = None,
    validity_days: Optional[int] = None,
) -> Dict[str, str]:
    """Populate canonical freshness fields after a successful screening run.

    Returns a small metadata dict with the canonical values written. The caller
    keeps ownership of persistence and transactions.
    """

    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base_dt = None
    for candidate in _candidate_screening_timestamps(screening_report):
        base_dt = _parse_timestamp(candidate)
        if base_dt is not None:
            break
    if base_dt is None:
        base_dt = now_utc

    canonical_screened_at = _format_timestamp(base_dt)
    screening_report["screened_at"] = canonical_screened_at
    screening_report["timestamp"] = canonical_screened_at

    days = int(validity_days if validity_days is not None else get_screening_validity_days())
    valid_until = _format_timestamp(base_dt + timedelta(days=days))

    prescreening["last_screened_at"] = canonical_screened_at
    prescreening["screening_valid_until"] = valid_until
    prescreening["screening_validity_days"] = days
    if screened_by:
        prescreening["screened_by"] = screened_by

    return {
        "screened_at": canonical_screened_at,
        "timestamp": canonical_screened_at,
        "screening_valid_until": valid_until,
        "screening_validity_days": str(days),
    }
