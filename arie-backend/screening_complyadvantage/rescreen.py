"""SRP-2a Phase D — delta-merge for existing-customer re-screens.

A Mesh DELTA rescreen returns only NEW or CHANGED risk factors relative to the
customer's previous screening baseline. The stored previous report IS that
baseline and is NEVER replaced by delta output:

* ``completed_no_changes``: the subject's previous section is carried forward
  VERBATIM (plus a ``rescreen`` stamp). "NO CHANGES FOUND" is not zero hits.
* ``completed_changes``: new provider hits are normalized and APPENDED to the
  previous hits with dedup by stable provider reference. Per-subject hit
  counts are monotonically NON-DECREASING.
* any failed pass (errored / timed out / customer lookup failed): the previous
  section is carried forward AND the report is marked degraded so the
  risk-lowering hold engages. A failed rescreen never produces fewer hits or
  a "clear".
"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

from .normalizer import (
    _attach_alert_profiles,
    _legacy_screening_result_from_match,
    compute_match_rollups,
    merge_two_pass_results,
)
from .orchestrator import (
    RESCREEN_COMPLETED_CHANGES,
    RESCREEN_CUSTOMER_NOT_FOUND,
    RESCREEN_ERRORED_DEGRADED_SOURCE,
    RESCREEN_FAILED_OUTCOMES,
    RESCREEN_NOT_FOUND_DEGRADED_SOURCE,
    RESCREEN_TIMED_OUT,
)


RESCREEN_DEGRADED_FLAG = (
    "ComplyAdvantage re-screen did not complete for at least one subject; the previous "
    "screening baseline is carried forward and a live terminal re-screen is required "
    "before approval."
)

# Stable provider reference fields emitted by _legacy_screening_result_from_match.
_RESULT_REF_FIELDS = (
    "risk_id",
    "provider_risk_identifier",
    "alert_identifier",
    "provider_alert_identifier",
    "profile_identifier",
    "provider_profile_identifier",
)


def combined_rescreen_outcome(strict, relaxed):
    """Collapse two pass outcomes into no_changes / delta_applied / failed."""
    outcomes = {strict.outcome, relaxed.outcome}
    if outcomes & RESCREEN_FAILED_OUTCOMES:
        return "failed"
    if RESCREEN_COMPLETED_CHANGES in outcomes:
        return "delta_applied"
    return "no_changes"


def rescreen_degraded_sources(strict, relaxed):
    """Distinct degraded sources for the failed passes (ordered, deduped)."""
    sources = []
    for result in (strict, relaxed):
        if result.outcome == RESCREEN_CUSTOMER_NOT_FOUND:
            source = RESCREEN_NOT_FOUND_DEGRADED_SOURCE
        elif result.outcome in RESCREEN_FAILED_OUTCOMES:
            source = RESCREEN_ERRORED_DEGRADED_SOURCE
        else:
            continue
        if source not in sources:
            sources.append(source)
    return sources


def _rescreen_pending_reason(strict, relaxed):
    for result in (strict, relaxed):
        if result.outcome == RESCREEN_CUSTOMER_NOT_FOUND:
            return "rescreen_customer_not_found"
        if result.outcome == RESCREEN_TIMED_OUT:
            return "rescreen_timed_out"
        if result.outcome in RESCREEN_FAILED_OUTCOMES:
            return "rescreen_errored"
    return None


def delta_result_pairs(strict, relaxed):
    """Normalize both passes' delta alerts into (legacy result row, rollups) pairs.

    Reuses the exact two-pass normalizer machinery (profile attach, profile-id
    dedup across passes, legacy result shape) so appended rows are
    byte-compatible with rows produced by create-and-screen.
    """
    strict_attached = _attach_alert_profiles(strict.deep_risks, strict.alerts)
    relaxed_attached = _attach_alert_profiles(relaxed.deep_risks, relaxed.alerts)
    merged, _provenance = merge_two_pass_results(strict_attached, relaxed_attached)
    pairs = []
    for match in merged:
        rollups = compute_match_rollups(match)
        pairs.append((_legacy_screening_result_from_match(match, rollups), rollups))
    return pairs


def _result_ref_keys(row):
    keys = set()
    if not isinstance(row, dict):
        return keys
    for field in _RESULT_REF_FIELDS:
        value = row.get(field)
        if value:
            keys.add(str(value))
    if not keys:
        # No provider reference at all: fall back to a stable content signature
        # so an identical unreferenced row cannot double on every rescreen.
        keys.add("sig:" + sha256(
            json.dumps(row, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:24])
    return keys


def _collect_existing_refs(rows):
    seen = set()
    for row in rows or []:
        seen |= _result_ref_keys(row)
    return seen


def _append_deduped_pairs(existing_rows, delta_pairs):
    """Return the (row, rollups) pairs from delta_pairs not already present."""
    seen = _collect_existing_refs(existing_rows)
    appended = []
    for row, rollups in delta_pairs or []:
        refs = _result_ref_keys(row)
        if refs & seen:
            continue
        seen |= refs
        appended.append((row, rollups))
    return appended


def _rescreen_stamp(outcome, rescreened_at, strict, relaxed):
    return {
        "outcome": outcome,
        "rescreened_at": rescreened_at,
        "mode": "delta",
        "strict_outcome": strict.outcome,
        "relaxed_outcome": relaxed.outcome,
    }


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truthy_declared_pep(value):
    return str(value or "").strip().lower() in {"yes", "true", "1", "y"}


def merge_rescreen_person_entry(prev_entry, delta_pairs, *, stamp, pending_reason=None):
    """Merge delta hits into one previous person/intermediary entry.

    Append-only: previous results are never removed or replaced, so the
    per-subject hit count is monotonically non-decreasing by construction.
    """
    entry = deepcopy(prev_entry) if isinstance(prev_entry, dict) else {}
    screening = entry.get("screening")
    if not isinstance(screening, dict):
        screening = {}
        entry["screening"] = screening
    existing = list(screening.get("results") or [])
    appended = _append_deduped_pairs(existing, delta_pairs)
    merged_results = existing + [row for row, _ in appended]
    screening["results"] = merged_results
    if appended:
        screening["matched"] = True
        entry["screening_state"] = "completed_match"
        entry["requires_review"] = True
    new_pep = any(rollups.get("has_pep_hit") for _, rollups in appended)
    new_sanctions = any(rollups.get("has_sanctions_hit") for _, rollups in appended)
    new_media = any(rollups.get("has_adverse_media_hit") for _, rollups in appended)
    new_rca = any(rollups.get("is_rca") for _, rollups in appended)
    entry["has_pep_hit"] = bool(entry.get("has_pep_hit") or new_pep)
    entry["provider_detected_pep"] = bool(entry.get("provider_detected_pep") or new_pep)
    entry["undeclared_pep"] = bool(
        entry.get("undeclared_pep")
        or (new_pep and not _truthy_declared_pep(entry.get("declared_pep")))
    )
    entry["has_sanctions_hit"] = bool(entry.get("has_sanctions_hit") or new_sanctions)
    if new_media:
        entry["has_adverse_media_hit"] = True
        entry["adverse_media_coverage"] = "full"
    if new_rca:
        entry["is_rca"] = True
    if pending_reason:
        # Failed rescreen: previous hits stay visible, but the subject is not
        # terminal evidence — same markers the existing degradation path uses.
        entry["screening_state"] = "pending_provider"
        entry["requires_review"] = True
        screening["api_status"] = "pending"
        screening["pending_reason"] = pending_reason
        screening.setdefault("source", "complyadvantage")
        screening.setdefault("provider", "complyadvantage")
    entry["rescreen"] = stamp
    return entry, len(appended)


def merge_rescreen_company_section(prev_company, delta_pairs, *, stamp, pending_reason=None):
    """Merge delta hits into the previous company_screening section."""
    company = deepcopy(prev_company) if isinstance(prev_company, dict) else {}
    existing = list(company.get("results") or [])
    appended = _append_deduped_pairs(existing, delta_pairs)
    company["results"] = existing + [row for row, _ in appended]
    new_sanctions_rows = [row for row, rollups in appended if rollups.get("has_sanctions_hit")]
    new_media_rows = [row for row, rollups in appended if rollups.get("has_adverse_media_hit")]
    if appended:
        company["matched"] = True
    for key, rows in (("sanctions", new_sanctions_rows), ("adverse_media", new_media_rows)):
        if not rows:
            continue
        sub = company.get(key)
        if not isinstance(sub, dict):
            sub = {"source": "complyadvantage", "api_status": "live", "matched": False, "results": []}
            company[key] = sub
        sub["results"] = list(sub.get("results") or []) + rows
        sub["matched"] = True
    if pending_reason:
        company["api_status"] = "pending"
        company["screening_state"] = "pending_provider"
        company["pending_reason"] = pending_reason
        company.setdefault("matched", False)
        company.setdefault("results", [])
    company["rescreen"] = stamp
    return company, len(appended), bool(new_sanctions_rows), bool(new_media_rows)


def _normalized_join_name(value):
    return " ".join(str(value or "").strip().lower().split())


def _conflict_subject_key(kind, person_key):
    """Stable per-subject key for the combined-report harvested-conflict map."""
    if person_key:
        return str(person_key)
    return "entity" if kind == "entity" else ""


def harvested_conflict_customer_identifiers(previous_report, kind, person_key):
    """Per-pass Mesh customer UUIDs harvested from a previous conflict, for ONE subject.

    ARF-QAFIX-001 stamps ``customer_identifier_conflict_existing_customers``
    (``{"strict": uuid, "relaxed": uuid}``) onto the per-subject report when a
    create-and-screen collision names the EXISTING Mesh customer; the adapter's
    combine step persists those maps per subject (keyed by person_key, or
    ``"entity"`` for the company). This reader accepts both shapes:

    * combined shape ``{subject_key: {"strict": ..., "relaxed": ...}}`` —
      returns this subject's entry;
    * flat per-pass shape ``{"strict": ..., "relaxed": ...}`` — accepted ONLY
      when the stored report's own subject scope matches this subject, so a
      UUID can never be attributed to the wrong person. Fail closed: when
      attribution is uncertain, return nothing and let the normal
      subscription/lookup resolution (and its degradation) stand.
    """
    if not isinstance(previous_report, dict):
        return {}
    harvested = previous_report.get("customer_identifier_conflict_existing_customers")
    if not isinstance(harvested, dict) or not harvested:
        return {}

    def _clean(entry):
        if not isinstance(entry, dict):
            return {}
        return {
            pass_name: str(value)
            for pass_name, value in entry.items()
            if pass_name in ("strict", "relaxed") and value
        }

    subject_key = _conflict_subject_key(kind, person_key)
    if subject_key and isinstance(harvested.get(subject_key), dict):
        return _clean(harvested.get(subject_key))
    if set(harvested) <= {"strict", "relaxed"}:
        scope = (
            ((previous_report.get("provider_specific") or {}).get("complyadvantage") or {}).get("screening_subject")
            or {}
        )
        scope_kind = scope.get("kind") or previous_report.get("screening_subject_kind")
        scope_person_key = scope.get("person_key") or previous_report.get("screening_subject_person_key")
        if scope_kind and str(scope_kind) == str(kind):
            if (kind == "entity" and not person_key) or (
                person_key and scope_person_key and str(scope_person_key) == str(person_key)
            ):
                return _clean(harvested)
    return {}


def find_previous_subject_section(previous_report, kind, person_key, subject_name):
    """Locate one subject's section in the stored previous report, or None.

    Persons/intermediaries join by the stable person_key persisted at
    screening time; the display-name fallback mirrors the back office's
    findScreeningRecordForSubject join.
    """
    if not isinstance(previous_report, dict):
        return None
    if kind == "entity":
        company = previous_report.get("company_screening")
        if isinstance(company, dict) and company:
            return {
                "company_screening": company,
                "has_company_screening_hit": previous_report.get("has_company_screening_hit"),
                "company_screening_coverage": previous_report.get("company_screening_coverage") or "full",
            }
        return None
    groups = ("intermediary_screenings",) if kind == "intermediary" else ("director_screenings", "ubo_screenings")
    candidates = []
    for group in groups:
        candidates.extend(
            entry for entry in previous_report.get(group) or [] if isinstance(entry, dict)
        )
    if person_key:
        for entry in candidates:
            screening = entry.get("screening") if isinstance(entry.get("screening"), dict) else {}
            entry_keys = (entry.get("person_key"), screening.get("person_key"))
            if any(key and str(key) == str(person_key) for key in entry_keys):
                return entry
    target = _normalized_join_name(subject_name)
    if not target:
        return None
    for entry in candidates:
        for field in ("person_name", "subject_name", "entity_name", "name"):
            if _normalized_join_name(entry.get(field)) == target:
                return entry
    return None


def build_rescreen_subject_report(*, kind, context, previous_section, strict, relaxed):
    """Build a single-subject normalized report from a delta rescreen.

    The returned dict is shaped exactly like the per-subject reports the
    adapter's _combine_reports consumes, so mixed applications (some subjects
    rescreened, some create-and-screened) combine unchanged.
    """
    outcome = combined_rescreen_outcome(strict, relaxed)
    rescreened_at = _now_iso()
    stamp = _rescreen_stamp(outcome, rescreened_at, strict, relaxed)
    pending_reason = _rescreen_pending_reason(strict, relaxed)
    degraded = rescreen_degraded_sources(strict, relaxed)
    flags = [RESCREEN_DEGRADED_FLAG] if degraded else []
    pairs = delta_result_pairs(strict, relaxed)

    director_screenings = []
    ubo_screenings = []
    intermediary_screenings = []
    company_screening = {}
    company_coverage = "none"
    has_company_hit = None

    if kind == "entity":
        prev_company = (previous_section or {}).get("company_screening") or {}
        prev_count = len(prev_company.get("results") or [])
        company_screening, appended, new_sanctions, new_media = merge_rescreen_company_section(
            prev_company, pairs, stamp=stamp, pending_reason=pending_reason,
        )
        company_coverage = (previous_section or {}).get("company_screening_coverage") or "full"
        has_company_hit = bool(
            (previous_section or {}).get("has_company_screening_hit")
            or new_sanctions
            or new_media
        )
        subject_hits = max(len(company_screening.get("results") or []), prev_count)
    else:
        prev_screening = (previous_section or {}).get("screening")
        prev_count = len((prev_screening or {}).get("results") or []) if isinstance(prev_screening, dict) else 0
        entry, appended = merge_rescreen_person_entry(
            previous_section, pairs, stamp=stamp, pending_reason=pending_reason,
        )
        subject_hits = max(len((entry.get("screening") or {}).get("results") or []), prev_count)
        if kind == "ubo":
            ubo_screenings.append(entry)
        elif kind == "intermediary":
            intermediary_screenings.append(entry)
        else:
            director_screenings.append(entry)

    subject_key = context.screening_subject_person_key or context.screening_subject_name or "unknown"
    report_hash = sha256(
        json.dumps(
            {
                "rescreen": True,
                "application_id": context.application_id,
                "subject": str(subject_key),
                "kind": kind,
                "outcome": outcome,
                "hits": subject_hits,
                "rescreened_at": rescreened_at,
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:32]

    return {
        "provider": "complyadvantage",
        "normalized_version": "2.0",
        "screened_at": rescreened_at,
        "screening_subject_kind": kind,
        "screening_subject_name": context.screening_subject_name,
        "screening_subject_person_key": context.screening_subject_person_key,
        "company_screening_coverage": company_coverage,
        "has_company_screening_hit": has_company_hit,
        "company_screening": company_screening,
        "director_screenings": director_screenings,
        "ubo_screenings": ubo_screenings,
        "intermediary_screenings": intermediary_screenings,
        "overall_flags": flags,
        "total_hits": subject_hits,
        "degraded_sources": degraded,
        "any_non_terminal_subject": bool(degraded),
        "provider_specific": {
            "complyadvantage": {
                "rescreen": dict(stamp),
                "screening_subject": {
                    "kind": kind,
                    "person_key": context.screening_subject_person_key,
                },
                "strict_customer_identifier": strict.customer_identifier or None,
                "relaxed_customer_identifier": relaxed.customer_identifier or None,
            }
        },
        "source_screening_report_hash": report_hash,
    }
