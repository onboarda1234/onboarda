"""
Canonical Screening State Model — Priority A: Truthful, Fail-Closed Semantics
=============================================================================

This module defines the **single source of truth** for what state a screening
subject is in (entity, director, UBO). It exists to stop the legacy code path
that rendered any non-terminal Sumsub provider state as "Clear" / "No Provider
Match", which created false reassurance for compliance officers.

The legacy screening adapters return a sub-record like::

    {
        "matched": False,
        "results": [],
        "source": "sumsub" | "simulated" | "unavailable" | "blocked",
        "api_status": "live" | "pending" | "error" | "not_configured"
                       | "simulated" | "unavailable",
        ...
    }

Treating ``matched is False`` as "Clear" is wrong whenever ``api_status`` is
**non-terminal** (pending / init / created / error / unavailable /
not_configured / simulated). This module supplies the canonical mapping so
that callers can choose truthful labels and fail-closed downstream behaviour.

Canonical states
----------------

* ``not_started``      — no screening attempt recorded for the subject.
* ``pending_provider`` — provider job submitted; no terminal answer yet
                          (Sumsub init/queued/onHold/pending).
* ``partial_result``   — some sub-checks done but the screening is not
                          complete (e.g. company registry returned but
                          sanctions still pending).
* ``completed_clear``  — provider returned a terminal answer and there
                          are no hits.
* ``completed_match``  — provider returned a terminal answer and there
                          is at least one hit.
* ``not_configured``   — the provider/level for this scope is not
                          provisioned (e.g. no Sumsub company KYB level).
* ``failed``           — provider call errored or was unavailable / blocked.

Invariants
----------

* ``completed_clear`` and ``completed_match`` are the **only** terminal
  states. All others are non-terminal and **must not** be presented as
  reassuring screening outcomes.
* ``not_configured`` is **never** flattened into ``completed_clear``.
* Declared (self-reported) PEP signals are preserved separately from
  provider-derived signals; ``derive_subject_state`` exposes both.

This module is purely additive: it computes derived fields from existing
records and never mutates them.
"""

import json
from datetime import datetime, timezone
from typing import Optional


# ── Canonical screening states ──

NOT_STARTED = "not_started"
PENDING_PROVIDER = "pending_provider"
PARTIAL_RESULT = "partial_result"
COMPLETED_CLEAR = "completed_clear"
COMPLETED_MATCH = "completed_match"
NOT_CONFIGURED = "not_configured"
FAILED = "failed"

# Provider-mode / defensibility states used by API, memo, UI, and approval
# gates. These are deliberately separate from the legacy subject terminality
# states above: a live provider can produce completed_clear/completed_match,
# while sandbox/simulated/not_configured/pending/failed are not defensible
# terminal outcomes and must never render as "clear".
LIVE_PROVIDER = "live_provider"
SANDBOX_PROVIDER = "sandbox_provider"
SIMULATED_FALLBACK = "simulated_fallback"
PENDING = "pending"

SCREENING_TRUTH_STATES = (
    LIVE_PROVIDER,
    SANDBOX_PROVIDER,
    SIMULATED_FALLBACK,
    NOT_CONFIGURED,
    PENDING,
    FAILED,
    COMPLETED_CLEAR,
    COMPLETED_MATCH,
)

UNSAFE_PROVIDER_STATES = frozenset({
    SANDBOX_PROVIDER,
    SIMULATED_FALLBACK,
    NOT_CONFIGURED,
    PENDING,
    FAILED,
})

FALSE_POSITIVE_CLEARANCE_DISPOSITIONS = frozenset({
    "false_positive_cleared",
    # Legacy structured clearance codes. These remain accepted so existing
    # reviewed rows do not regress, but new API submissions should use the
    # canonical false_positive_cleared disposition.
    "false_positive",
    "identity_mismatch",
    "provider_no_relevant_match",
    "duplicate_or_irrelevant",
    "low_risk_context_accepted",
})

ALL_STATES = (
    NOT_STARTED,
    PENDING_PROVIDER,
    PARTIAL_RESULT,
    COMPLETED_CLEAR,
    COMPLETED_MATCH,
    NOT_CONFIGURED,
    FAILED,
)

TERMINAL_STATES = frozenset({COMPLETED_CLEAR, COMPLETED_MATCH})

# Provider api_status values that mean "we have a real, reliable answer".
# Only these may yield a terminal state. ``simulated`` is intentionally
# excluded: it is non-production, never a basis for compliance reassurance.
TERMINAL_API_STATUSES = frozenset({"live"})

# Provider api_status values that mean "no terminal answer yet".
PENDING_API_STATUSES = frozenset({
    "pending",
    "init",
    "created",
    "queued",
    "onhold",
    "onHold",
    "processing",
    "in_progress",
})

# Provider api_status values that mean "provider is not configured for this scope".
NOT_CONFIGURED_API_STATUSES = frozenset({"not_configured"})

# Provider api_status values that mean "we tried and failed".
FAILED_API_STATUSES = frozenset({"error", "unavailable", "blocked", "failed", "failure"})

SIMULATED_API_STATUSES = frozenset({"simulated", "mock", "mocked", "stubbed", "demo"})
SANDBOX_API_STATUSES = frozenset({"sandbox", "test", "test_mode"})


def _normalise_token(value) -> str:
    return str(value or "").strip().lower()


def _normalise_subject_type(value) -> str:
    token = _normalise_token(value)
    if token == "company":
        return "entity"
    return token


def _normalise_subject_name(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _truthy_review_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _review_identity_present(screening: dict) -> bool:
    return bool(
        screening.get("reviewer_id")
        or screening.get("reviewer_name")
        or screening.get("reviewed_by")
    )


def _review_reason_present(screening: dict) -> bool:
    return bool(
        screening.get("review_rationale")
        or screening.get("rationale")
        or screening.get("review_reason")
    )


def _review_timestamp_present(screening: dict) -> bool:
    return bool(
        screening.get("reviewed_at")
        or screening.get("review_updated_at")
        or screening.get("created_at")
        or screening.get("updated_at")
    )


def _review_audit_present(screening: dict) -> bool:
    return bool(
        _truthy_review_flag(screening.get("audit_confirmed"))
        or screening.get("audit_log_id")
        or screening.get("review_audit_id")
    )


def _review_second_signoff_satisfied(screening: dict) -> bool:
    if not _truthy_review_flag(screening.get("requires_four_eyes")):
        return True
    return bool(
        screening.get("second_reviewer_id")
        or screening.get("second_reviewed_by")
        or screening.get("second_reviewer_name")
    )


def _review_disposition_code(screening: dict) -> str:
    return _normalise_token(
        screening.get("review_disposition_code")
        or screening.get("canonical_disposition")
        or screening.get("disposition_code")
        or screening.get("review_disposition")
        or screening.get("disposition")
        or screening.get("screening_review_disposition")
    )


def _is_false_positive_clearance(screening: dict) -> bool:
    code = _review_disposition_code(screening)
    storage_disposition = _normalise_token(
        screening.get("review_storage_disposition")
        or screening.get("disposition")
        or screening.get("review_disposition")
    )
    if code not in FALSE_POSITIVE_CLEARANCE_DISPOSITIONS:
        return False
    if storage_disposition and storage_disposition not in FALSE_POSITIVE_CLEARANCE_DISPOSITIONS | {"cleared"}:
        return False
    return (
        _review_identity_present(screening)
        and _review_reason_present(screening)
        and _review_timestamp_present(screening)
        and _review_audit_present(screening)
        and _review_second_signoff_satisfied(screening)
    )


def _parse_review_timestamp(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _review_sort_key(review: dict):
    timestamp = (
        _parse_review_timestamp(review.get("updated_at"))
        or _parse_review_timestamp(review.get("created_at"))
        or _parse_review_timestamp(review.get("review_updated_at"))
        or _parse_review_timestamp(review.get("reviewed_at"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )
    try:
        review_id = int(review.get("id") or 0)
    except Exception:
        review_id = 0
    stable_payload = json.dumps(review, default=str, sort_keys=True)
    return (timestamp, review_id, stable_payload)


def _latest_review(existing, candidate):
    if not existing:
        return candidate
    return candidate if _review_sort_key(candidate) >= _review_sort_key(existing) else existing


def _record_text(record: Optional[dict]) -> str:
    if not isinstance(record, dict):
        return ""
    values = []
    for key in (
        "api_status",
        "source",
        "provider",
        "provider_mode",
        "mode",
        "environment",
        "screening_mode",
    ):
        value = record.get(key)
        if value not in (None, "", [], {}):
            values.append(str(value))
    if record.get("is_simulated") or record.get("testMode") or record.get("test_mode"):
        values.append("simulated")
    return " ".join(values).lower()


def _contains_any_token(text: str, tokens) -> bool:
    if not text:
        return False
    return any(token in text for token in tokens)


def provider_mode_from_record(screening: Optional[dict]) -> str:
    """Return the provider mode/availability truth for a screening record."""
    if not screening or not isinstance(screening, dict):
        return PENDING

    api_status = _normalise_token(screening.get("api_status"))
    source = _normalise_token(screening.get("source"))
    text = _record_text(screening)

    if api_status in NOT_CONFIGURED_API_STATUSES or "not_configured" in text:
        return NOT_CONFIGURED
    if api_status in FAILED_API_STATUSES or source in ("unavailable", "blocked", "failed"):
        return FAILED
    if api_status in SANDBOX_API_STATUSES or _contains_any_token(text, ("sandbox", "test-mode", "test_mode")):
        return SANDBOX_PROVIDER
    if api_status in SIMULATED_API_STATUSES or _contains_any_token(
        text,
        ("simulated", "mock", "stubbed", "demo", "codex-smoke", "smoke"),
    ):
        return SIMULATED_FALLBACK
    if api_status in PENDING_API_STATUSES:
        return PENDING
    if api_status in TERMINAL_API_STATUSES:
        return LIVE_PROVIDER
    if (
        not api_status
        and screening.get("matched")
        and screening.get("results")
        and not _contains_any_token(text, ("simulated", "mock", "stubbed", "demo", "codex-smoke", "smoke", "sandbox"))
    ):
        return LIVE_PROVIDER

    # Unknown non-empty provider statuses are not defensible terminal answers.
    if api_status:
        return PENDING
    return PENDING


def is_terminal(state: str) -> bool:
    """Return True if ``state`` is a terminal screening state."""
    return state in TERMINAL_STATES


def is_reassuring(state: str) -> bool:
    """
    Return True only if ``state`` legitimately means "no compliance concern
    at this layer". This is intentionally narrower than ``is_terminal`` —
    a ``completed_match`` is terminal but **not** reassuring.
    """
    return state == COMPLETED_CLEAR


def derive_screening_state(screening: Optional[dict]) -> str:
    """
    Derive the canonical screening state from a single legacy screening
    sub-record (e.g. ``person.screening`` or ``company_screening.sanctions``).

    Returns ``not_started`` when the record is missing/empty, never
    ``completed_clear``.

    Positive evidence is actionable only when it comes from a live terminal
    provider response. Sandbox, simulated, pending, failed, and not_configured
    records remain non-terminal even if they carry possible-match metadata.
    The reverse is **not** symmetric: ``matched=False`` without a terminal
    api_status is **never** treated as ``completed_clear`` — that is the
    dangerous false-reassurance case Priority A is closing.
    """
    if not screening or not isinstance(screening, dict):
        return NOT_STARTED

    api_status = _normalise_token(screening.get("api_status"))
    source = (screening.get("source") or "").strip().lower()

    # Explicit not_configured short-circuit (e.g. Sumsub company KYB level missing)
    if api_status in NOT_CONFIGURED_API_STATUSES:
        return NOT_CONFIGURED

    # Explicit failure / unavailable short-circuit
    if api_status in FAILED_API_STATUSES or source in ("unavailable", "blocked"):
        return FAILED

    # Sandbox/simulated/pending provider states are non-terminal.
    provider_mode = provider_mode_from_record(screening)
    if provider_mode in (SANDBOX_PROVIDER, SIMULATED_FALLBACK, PENDING):
        return PENDING_PROVIDER

    # Terminal answer from a real provider
    if provider_mode == LIVE_PROVIDER:
        if screening.get("matched"):
            return COMPLETED_MATCH
        # ``matched`` may be False or absent — both are terminal-clear
        # only when api_status is terminal (live).
        return COMPLETED_CLEAR

    # ``simulated`` and any other non-recognised api_status: do NOT treat as
    # a real provider outcome. Keep officers honest by surfacing the
    # not-yet-terminal state. This is the fail-closed default.
    if api_status:
        return PENDING_PROVIDER

    # No api_status at all but record exists — treat as not-started.
    return NOT_STARTED


def combine_states(*states: str) -> str:
    """
    Combine multiple sub-states (e.g. registry + sanctions) into a single
    aggregate state for an entity. The combination is fail-closed:

    * if any sub-state is ``completed_match`` → ``completed_match``
    * else if any is ``failed`` → ``failed``
    * else if any is ``not_configured`` → ``not_configured``
    * else if any is ``pending_provider`` or ``not_started`` → ``pending_provider``
      (when at least one other sub-state exists, otherwise ``partial_result``)
    * else if all are ``completed_clear`` → ``completed_clear``
    * else → ``partial_result``
    """
    states = tuple(s for s in states if s)
    if not states:
        return NOT_STARTED
    if any(s == COMPLETED_MATCH for s in states):
        return COMPLETED_MATCH
    if any(s == FAILED for s in states):
        return FAILED
    if any(s == NOT_CONFIGURED for s in states):
        return NOT_CONFIGURED
    if all(s == COMPLETED_CLEAR for s in states):
        return COMPLETED_CLEAR
    if any(s in (PENDING_PROVIDER, NOT_STARTED) for s in states) and any(
        s == COMPLETED_CLEAR for s in states
    ):
        return PARTIAL_RESULT
    if any(s in (PENDING_PROVIDER, NOT_STARTED) for s in states):
        return PENDING_PROVIDER
    return PARTIAL_RESULT


def _truthy_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in {
        "1",
        "yes",
        "y",
        "true",
        "match",
        "matched",
        "hit",
        "material",
        "material_match",
        "material_screening_concern",
        "screening_concern",
    }


def _normalise_state(value) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    aliases = {
        "clear": COMPLETED_CLEAR,
        "cleared": COMPLETED_CLEAR,
        "no_hit": COMPLETED_CLEAR,
        "no_hits": COMPLETED_CLEAR,
        "no match": COMPLETED_CLEAR,
        "no_match": COMPLETED_CLEAR,
        "match": COMPLETED_MATCH,
        "matched": COMPLETED_MATCH,
        "hit": COMPLETED_MATCH,
        "completed_pep": COMPLETED_MATCH,
        "pending": PENDING_PROVIDER,
        "review": PENDING_PROVIDER,
        "in_review": PENDING_PROVIDER,
        "unavailable": FAILED,
        "error": FAILED,
        "disabled": NOT_CONFIGURED,
    }
    text = aliases.get(text, text)
    return text if text in ALL_STATES else None


def _results_have_material_hit(results) -> bool:
    if not isinstance(results, list):
        return False
    material_keys = (
        "is_pep",
        "is_sanctioned",
        "is_adverse_media",
        "sanctions",
        "adverse_media",
        "pep",
        "sanctioned",
        "media",
    )
    for result in results:
        if not isinstance(result, dict):
            continue
        if any(_truthy_flag(result.get(key)) for key in material_keys):
            return True
        categories = result.get("match_categories") or result.get("categories") or []
        if isinstance(categories, str):
            categories = [categories]
        for category in categories:
            text = str(category or "").strip().lower()
            if any(token in text for token in ("pep", "sanction", "adverse", "media", "watchlist")):
                return True
    return False


def _entry_has_granular_material_fields(entry: dict) -> bool:
    return any(
        key in entry
        for key in (
            "has_pep_hit",
            "has_sanctions_hit",
            "has_adverse_media_hit",
            "provider_detected_pep",
            "undeclared_pep",
        )
    )


def _entry_has_material_hit(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    material_flag_keys = (
        "has_pep_hit",
        "has_sanctions_hit",
        "has_adverse_media_hit",
        "provider_detected_pep",
        "undeclared_pep",
    )
    if any(_truthy_flag(entry.get(key)) for key in material_flag_keys):
        return True

    screening = entry.get("screening") if isinstance(entry.get("screening"), dict) else {}
    results = screening.get("results") if isinstance(screening, dict) else []
    if _results_have_material_hit(results):
        return True

    # Legacy providers sometimes only supplied matched/results without the
    # normalized material category booleans. Preserve that as a true match
    # only when granular fields are absent. If granular fields are present
    # and false/None, they are the canonical materiality answer.
    if _truthy_flag(screening.get("matched")) and results and not _entry_has_granular_material_fields(entry):
        return True

    state = _normalise_state(entry.get("screening_state"))
    if state == COMPLETED_MATCH and not _entry_has_granular_material_fields(entry):
        return True
    return False


def _company_has_material_hit(report: dict, company: dict) -> bool:
    if not isinstance(company, dict):
        company = {}
    if _truthy_flag(report.get("has_company_screening_hit")):
        return True
    if _truthy_flag(report.get("has_adverse_media_hit")):
        return True
    for key in ("sanctions", "adverse_media"):
        sub = company.get(key)
        if isinstance(sub, dict):
            if _truthy_flag(sub.get("matched")):
                return True
            if _results_have_material_hit(sub.get("results")):
                return True
    if _results_have_material_hit(company.get("results")):
        return True
    if _truthy_flag(company.get("matched")) and not any(
        key in report for key in ("has_company_screening_hit", "has_adverse_media_hit")
    ):
        return True
    return False


def _state_from_entry(entry: dict) -> str:
    if not isinstance(entry, dict):
        return NOT_STARTED
    explicit = _normalise_state(entry.get("screening_state"))
    if explicit:
        return explicit
    return derive_screening_state(entry.get("screening") if isinstance(entry.get("screening"), dict) else {})


def _state_from_company(report: dict) -> Optional[str]:
    explicit = _normalise_state(report.get("company_screening_state"))
    if explicit:
        return explicit
    company = report.get("company_screening")
    if not isinstance(company, dict) or not company:
        return None
    sub_states = []
    for key in ("sanctions", "adverse_media"):
        sub = company.get(key)
        if isinstance(sub, dict) and sub:
            sub_states.append(derive_screening_state(sub))
    if sub_states:
        return combine_states(*sub_states)
    return derive_screening_state(company)


def derive_screening_truth(screening: Optional[dict], *, name: Optional[str] = None, required: bool = True) -> dict:
    """
    Derive the regulator-facing screening truth envelope for one required
    provider record.

    ``completed_clear`` is emitted only when the provider mode is
    ``live_provider`` and the provider has returned a terminal no-match
    answer. Sandbox, simulated, not_configured, pending, and failed states
    remain explicit and approval-blocking for required checks.
    """
    screening = screening if isinstance(screening, dict) else {}
    mode = provider_mode_from_record(screening)
    results = screening.get("results") if isinstance(screening.get("results"), list) else []
    has_match = bool(screening.get("matched") or results)
    has_material_hit = _results_have_material_hit(results) or bool(screening.get("matched") and results)

    if mode == LIVE_PROVIDER:
        canonical_state = COMPLETED_MATCH if has_match else COMPLETED_CLEAR
        terminal = True
        provider_availability = "available"
        screening_result = "match" if has_match else "clear"
    else:
        canonical_state = mode
        terminal = False
        screening_result = "match" if has_match else "unknown"
        if mode == NOT_CONFIGURED:
            provider_availability = "not_configured"
        elif mode == FAILED:
            provider_availability = "failed"
        elif mode == PENDING:
            provider_availability = "pending"
        elif mode == SANDBOX_PROVIDER:
            provider_availability = "sandbox"
        elif mode == SIMULATED_FALLBACK:
            provider_availability = "simulated"
        else:
            provider_availability = "unknown"

    defensible_clear = canonical_state == COMPLETED_CLEAR and mode == LIVE_PROVIDER and terminal
    formally_cleared_match = (
        canonical_state == COMPLETED_MATCH
        and _is_false_positive_clearance(screening)
    )
    approval_blocking = bool(
        required
        and (
            canonical_state in UNSAFE_PROVIDER_STATES
            or (canonical_state == COMPLETED_MATCH and not formally_cleared_match)
        )
    )
    if canonical_state == COMPLETED_CLEAR:
        legacy_status = "clear"
    elif canonical_state == COMPLETED_MATCH:
        legacy_status = "match" if has_material_hit or has_match else "review"
    elif canonical_state == NOT_CONFIGURED:
        legacy_status = "not_configured"
    elif canonical_state == FAILED:
        legacy_status = "unavailable"
    else:
        legacy_status = "pending"

    reason_map = {
        COMPLETED_CLEAR: "live_terminal_clear",
        COMPLETED_MATCH: "live_terminal_match",
        SANDBOX_PROVIDER: "provider_mode_sandbox",
        SIMULATED_FALLBACK: "provider_mode_simulated",
        NOT_CONFIGURED: "provider_not_configured",
        PENDING: "provider_pending_not_terminal",
        FAILED: "provider_failed",
    }
    reason = reason_map.get(canonical_state, "provider_not_terminal")

    return {
        "name": name,
        "required": bool(required),
        "canonical_state": canonical_state,
        "provider_mode": mode,
        "provider_availability": provider_availability,
        "screening_result": screening_result,
        "terminal": terminal,
        "defensible_clear": defensible_clear,
        "approval_blocking": approval_blocking,
        "formally_cleared_match": formally_cleared_match,
        "review_disposition": _normalise_token(
            screening.get("review_disposition")
            or screening.get("disposition")
            or screening.get("screening_review_disposition")
        ) or None,
        "review_disposition_code": _review_disposition_code(screening) or None,
        "reason": reason,
        "api_status": screening.get("api_status"),
        "source": screening.get("source"),
        "provider": screening.get("provider"),
        "freshness": {
            "screened_at": screening.get("screened_at"),
            "screening_valid_until": screening.get("screening_valid_until"),
        },
        "legacy_status": legacy_status,
    }


def _screening_review_index(screening_reviews) -> dict:
    index = {"exact": {}, "by_type": {}}
    for review in screening_reviews or []:
        if not isinstance(review, dict):
            continue
        subject_type = _normalise_subject_type(review.get("subject_type"))
        subject_name = _normalise_subject_name(review.get("subject_name"))
        if not subject_type:
            continue
        if subject_name:
            key = (subject_type, subject_name)
            index["exact"][key] = _latest_review(index["exact"].get(key), review)
        index["by_type"].setdefault(subject_type, []).append(review)
    for subject_type, reviews in list(index["by_type"].items()):
        index["by_type"][subject_type] = sorted(reviews, key=_review_sort_key, reverse=True)
    return index


def _review_for_subject(index: dict, subject_type: str, subject_name: str):
    subject_type = _normalise_subject_type(subject_type)
    subject_name = _normalise_subject_name(subject_name)
    if subject_name:
        exact = index.get("exact", {}).get((subject_type, subject_name))
        if exact:
            return exact
    typed = index.get("by_type", {}).get(subject_type) or []
    if subject_type == "entity" and typed:
        return typed[0]
    return None


def _company_subject_name(report: dict, prescreening: dict) -> str:
    company = report.get("company_screening") if isinstance(report.get("company_screening"), dict) else {}
    candidates = (
        prescreening.get("company_name"),
        prescreening.get("registered_name"),
        prescreening.get("legal_name"),
        (prescreening.get("company") or {}).get("name") if isinstance(prescreening.get("company"), dict) else None,
        company.get("company_name"),
        company.get("name"),
        company.get("legal_name"),
    )
    for candidate in candidates:
        if candidate not in (None, "", [], {}):
            return str(candidate)
    return ""


def _screening_record_with_review(record: dict, review: Optional[dict]) -> dict:
    if not isinstance(record, dict):
        record = {}
    if not review:
        return record
    enriched = dict(record)
    code = _normalise_token(
        review.get("canonical_disposition")
        or review.get("disposition_code")
        or review.get("review_disposition_code")
    )
    disposition = _normalise_token(review.get("disposition") or review.get("review_disposition"))
    enriched.update({
        "review_storage_disposition": disposition,
        "review_disposition": code or disposition,
        "review_disposition_code": code or None,
        "review_rationale": review.get("rationale") or review.get("review_rationale"),
        "review_reason": review.get("rationale") or review.get("review_reason"),
        "review_notes": review.get("notes") or review.get("review_notes"),
        "review_evidence_reference": (
            review.get("evidence_reference")
            or review.get("review_evidence_reference")
            or review.get("source_reference")
        ),
        "evidence_reference": review.get("evidence_reference"),
        "reviewer_id": review.get("reviewer_id"),
        "reviewer_name": review.get("reviewer_name") or review.get("reviewed_by"),
        "reviewed_at": review.get("reviewed_at") or review.get("created_at") or review.get("updated_at"),
        "review_updated_at": review.get("review_updated_at") or review.get("updated_at") or review.get("created_at"),
        "audit_confirmed": review.get("audit_confirmed"),
        "review_audit_id": review.get("review_audit_id"),
        "audit_log_id": review.get("audit_log_id"),
        "requires_four_eyes": review.get("requires_four_eyes"),
        "second_reviewer_id": review.get("second_reviewer_id"),
        "second_reviewer_name": review.get("second_reviewer_name") or review.get("second_reviewed_by"),
        "second_reviewed_at": review.get("second_reviewed_at"),
    })
    return enriched


def _collect_required_screening_records(report: dict, prescreening: Optional[dict] = None, screening_reviews=None) -> list:
    records = []
    if not isinstance(report, dict):
        return records
    prescreening = prescreening if isinstance(prescreening, dict) else {}
    review_index = _screening_review_index(screening_reviews)

    company = report.get("company_screening") if isinstance(report.get("company_screening"), dict) else {}
    company_name = _company_subject_name(report, prescreening)
    company_review = _review_for_subject(review_index, "entity", company_name)
    company_provider = _normalise_token(
        company.get("provider")
        or company.get("source")
        or report.get("provider")
    )
    if company_provider == "complyadvantage":
        if company:
            records.append(("company_screening", _screening_record_with_review(company, company_review), True))
    else:
        sanctions = company.get("sanctions") if isinstance(company.get("sanctions"), dict) else None
        if sanctions:
            records.append(("company_watchlist", _screening_record_with_review(sanctions, company_review), True))

    for idx, person in enumerate(report.get("director_screenings") or []):
        if isinstance(person, dict) and isinstance(person.get("screening"), dict):
            review = _review_for_subject(
                review_index,
                "director",
                person.get("person_name") or person.get("name") or "",
            )
            records.append((f"director_screening_{idx}", _screening_record_with_review(person.get("screening"), review), True))
    for idx, person in enumerate(report.get("ubo_screenings") or []):
        if isinstance(person, dict) and isinstance(person.get("screening"), dict):
            review = _review_for_subject(
                review_index,
                "ubo",
                person.get("person_name") or person.get("name") or "",
            )
            records.append((f"ubo_screening_{idx}", _screening_record_with_review(person.get("screening"), review), True))
    for idx, applicant in enumerate(report.get("kyc_applicants") or []):
        if isinstance(applicant, dict):
            review = _review_for_subject(
                review_index,
                "applicant",
                applicant.get("person_name") or applicant.get("name") or "",
            )
            records.append((f"kyc_applicant_{idx}", _screening_record_with_review(applicant, review), True))
    for key in ("sanctions", "kyc"):
        legacy = report.get(key)
        if isinstance(legacy, dict):
            records.append((key, _screening_record_with_review(legacy, company_review), True))
    return records


def _aggregate_truth_state(evidence: list) -> str:
    states = [item.get("canonical_state") for item in evidence if item.get("canonical_state")]
    if not states:
        return PENDING
    for state in (FAILED, NOT_CONFIGURED, SANDBOX_PROVIDER, SIMULATED_FALLBACK, PENDING):
        if state in states:
            return state
    if COMPLETED_MATCH in states:
        return COMPLETED_MATCH
    if states and all(state == COMPLETED_CLEAR for state in states):
        return COMPLETED_CLEAR
    return PENDING


def build_screening_truth_summary(
    report: Optional[dict],
    prescreening: Optional[dict] = None,
    screening_reviews=None,
) -> dict:
    """
    Build the API/memo/approval truth summary for required screening checks.

    Enrichment-only sources are intentionally excluded. The summary separates
    provider availability, provider mode, screening result, terminality, and
    freshness so consumers do not infer "clear" from a non-terminal no-match.
    """
    report = report if isinstance(report, dict) else {}
    prescreening = prescreening if isinstance(prescreening, dict) else {}
    evidence = [
        derive_screening_truth(record, name=name, required=required)
        for name, record, required in _collect_required_screening_records(
            report,
            prescreening,
            screening_reviews,
        )
    ]

    canonical_state = _aggregate_truth_state(evidence)
    terminal = bool(evidence) and all(item.get("terminal") for item in evidence)
    has_match = any(item.get("screening_result") == "match" for item in evidence)
    has_failed = any(item.get("canonical_state") == FAILED for item in evidence)
    has_not_configured = any(item.get("canonical_state") == NOT_CONFIGURED for item in evidence)
    has_pending = any(item.get("canonical_state") == PENDING for item in evidence)
    has_sandbox = any(item.get("canonical_state") == SANDBOX_PROVIDER for item in evidence)
    has_simulated = any(item.get("canonical_state") == SIMULATED_FALLBACK for item in evidence)
    has_formally_cleared_match = any(
        item.get("canonical_state") == COMPLETED_MATCH
        and item.get("formally_cleared_match")
        for item in evidence
    )
    has_uncleared_completed_match = any(
        item.get("canonical_state") == COMPLETED_MATCH
        and not item.get("formally_cleared_match")
        for item in evidence
    )
    blocking_reasons = [
        f"{item.get('name') or 'screening'}:{item.get('reason')}"
        for item in evidence
        if item.get("approval_blocking")
    ]
    completed_match_blocking = canonical_state == COMPLETED_MATCH and bool(blocking_reasons)

    provider_mode = LIVE_PROVIDER if terminal else canonical_state
    if canonical_state == COMPLETED_MATCH:
        screening_result = "match"
    elif canonical_state == COMPLETED_CLEAR and terminal and not has_match:
        screening_result = "clear"
    elif has_match:
        screening_result = "match"
    else:
        screening_result = "unknown"

    provider_availability = "available"
    if has_failed:
        provider_availability = "failed"
    elif has_not_configured:
        provider_availability = "not_configured"
    elif has_sandbox:
        provider_availability = "sandbox"
    elif has_simulated:
        provider_availability = "simulated"
    elif has_pending or not terminal:
        provider_availability = "pending"

    freshness = {
        "screened_at": report.get("screened_at") or prescreening.get("last_screened_at"),
        "screening_valid_until": prescreening.get("screening_valid_until"),
        "screening_validity_days": prescreening.get("screening_validity_days"),
    }

    return {
        "canonical_state": canonical_state,
        "provider_availability": provider_availability,
        "provider_mode": provider_mode,
        "screening_result": screening_result,
        "terminal": terminal,
        "defensible_clear": terminal and canonical_state == COMPLETED_CLEAR and provider_mode == LIVE_PROVIDER,
        "approval_ready": terminal and canonical_state in (COMPLETED_CLEAR, COMPLETED_MATCH),
        "approval_blocking": bool(blocking_reasons) or not terminal,
        "blocking_reasons": blocking_reasons or ([] if terminal else ["screening:not_terminal"]),
        "has_non_terminal": not terminal,
        "has_failed": has_failed,
        "has_not_configured": has_not_configured,
        "has_sandbox": has_sandbox,
        "has_simulated": has_simulated,
        "has_pending": has_pending,
        "has_completed_match": canonical_state == COMPLETED_MATCH or has_match,
        "has_formally_cleared_match": has_formally_cleared_match,
        "has_uncleared_completed_match": has_uncleared_completed_match,
        "completed_match_blocking": completed_match_blocking,
        "required_evidence": evidence,
        "freshness": freshness,
    }


def build_screening_terminality_summary(
    report: Optional[dict],
    prescreening: Optional[dict] = None,
    screening_reviews=None,
) -> dict:
    """
    Build the canonical screening terminality summary used by memo,
    supervisor, EDD routing, and enhanced-requirement generation.

    ``has_terminal_match`` means a terminal *material* screening concern
    exists: PEP, sanctions/watchlist, adverse media, company screening hit,
    or an explicit material screening-concern flag. Clean terminal responses,
    zero-hit reports, and normalized reports with non-material provider
    profiles must not be escalated as material screening concerns.
    """
    report = report if isinstance(report, dict) else {}
    prescreening = prescreening if isinstance(prescreening, dict) else {}
    truth_summary = build_screening_truth_summary(report, prescreening, screening_reviews)

    person_entries = list(report.get("director_screenings") or []) + list(report.get("ubo_screenings") or [])
    person_states = [_state_from_entry(entry) for entry in person_entries if isinstance(entry, dict)]
    company_state = _state_from_company(report)
    states = list(person_states)
    if company_state:
        states.append(company_state)

    has_non_terminal = any(state not in TERMINAL_STATES for state in states)
    if _truthy_flag(report.get("any_non_terminal_subject")):
        has_non_terminal = True
    has_failed = any(state == FAILED for state in states)
    has_not_configured = any(state == NOT_CONFIGURED for state in states)

    terminal = bool(states) and all(state in TERMINAL_STATES for state in states) and not has_non_terminal
    if truth_summary.get("required_evidence"):
        terminal = bool(truth_summary.get("terminal"))
        has_non_terminal = bool(truth_summary.get("has_non_terminal"))
        has_failed = bool(truth_summary.get("has_failed"))
        has_not_configured = bool(truth_summary.get("has_not_configured"))

    terminal_person_hit = any(
        state in TERMINAL_STATES and _entry_has_material_hit(entry)
        for entry, state in zip(
            [entry for entry in person_entries if isinstance(entry, dict)],
            person_states,
        )
    )
    company = report.get("company_screening") if isinstance(report.get("company_screening"), dict) else {}
    terminal_company_hit = (
        company_state in TERMINAL_STATES
        and _company_has_material_hit(report, company)
    )
    material_hit = terminal_person_hit or terminal_company_hit

    report_material_flags = any(
        _truthy_flag(report.get(key))
        for key in ("any_pep_hits", "any_sanctions_hits", "has_adverse_media_hit", "has_company_screening_hit")
    )
    # Report-level rollups are material only when the report is terminal.
    # Pending/partial rows may carry possible-match metadata, but that is
    # handled by the screening completeness gate, not by material EDD routing.
    if report_material_flags and (terminal or not states):
        material_hit = True

    explicit_material = any(_truthy_flag(prescreening.get(key)) for key in ("screening_concern", "material_screening_concern"))
    if explicit_material and (terminal or not states):
        material_hit = True

    granular_fields_present = any(
        key in report
        for key in ("any_pep_hits", "any_sanctions_hits", "has_adverse_media_hit", "has_company_screening_hit")
    ) or any(_entry_has_granular_material_fields(entry) for entry in person_entries if isinstance(entry, dict))
    if not material_hit and not granular_fields_present:
        try:
            legacy_total_hits = int(report.get("total_hits") or 0) > 0
        except Exception:
            legacy_total_hits = False
        if legacy_total_hits and (terminal or not states):
            material_hit = True

    if not states and material_hit:
        # Legacy total_hits-only report: keep it as terminal material evidence.
        terminal = True

    if truth_summary.get("required_evidence") and not terminal:
        # Possible-match metadata from non-terminal/sandbox/simulated providers
        # is handled by screening completeness controls, not by material-match
        # EDD routing. Do not let unsafe provider modes masquerade as terminal
        # material screening concerns.
        material_hit = False
    elif (
        truth_summary.get("has_formally_cleared_match")
        and not truth_summary.get("has_uncleared_completed_match")
    ):
        # A live terminal match with documented false-positive clearance remains
        # visible as completed_match, but no longer acts as an unresolved
        # material screening concern for memo routing.
        material_hit = False

    return {
        "terminal": terminal,
        "has_non_terminal": bool(has_non_terminal),
        "has_failed": bool(has_failed),
        "has_not_configured": bool(has_not_configured),
        "has_terminal_match": bool(material_hit),
        "company_screening_configured": bool(report.get("company_screening")),
        "company_state": company_state,
        "person_states": person_states,
        "canonical_state": truth_summary.get("canonical_state"),
        "provider_availability": truth_summary.get("provider_availability"),
        "provider_mode": truth_summary.get("provider_mode"),
        "screening_result": truth_summary.get("screening_result"),
        "defensible_clear": truth_summary.get("defensible_clear"),
        "approval_ready": truth_summary.get("approval_ready"),
        "approval_blocking": truth_summary.get("approval_blocking"),
        "blocking_reasons": truth_summary.get("blocking_reasons") or [],
        "has_formally_cleared_match": truth_summary.get("has_formally_cleared_match"),
        "has_uncleared_completed_match": truth_summary.get("has_uncleared_completed_match"),
        "completed_match_blocking": truth_summary.get("completed_match_blocking"),
        "required_evidence": truth_summary.get("required_evidence") or [],
        "freshness": truth_summary.get("freshness") or {},
    }


# ── Subject-level helpers (per person / entity row) ──

def derive_subject_state(
    subject_screening: Optional[dict],
    declared_pep: bool = False,
) -> dict:
    """
    Derive the canonical state envelope for a single subject (director / UBO
    / entity-sanctions row).

    Returns a dict with:

    * ``screening_state`` — canonical state of the provider screening
    * ``has_provider_pep_hit`` — True/False/None (None when not terminal)
    * ``has_provider_sanctions_hit`` — True/False/None (None when not terminal)
    * ``declared_pep`` — pass-through of the self-declared PEP flag
    * ``requires_review`` — True when officer attention is required:
        non-terminal state, failed/not_configured, terminal match, or
        declared PEP regardless of provider state
    """
    state = derive_screening_state(subject_screening)
    results = (subject_screening or {}).get("results") or []

    if state in TERMINAL_STATES:
        has_pep = any(isinstance(r, dict) and r.get("is_pep") for r in results)
        has_sanctions = any(
            isinstance(r, dict) and r.get("is_sanctioned") for r in results
        )
    else:
        # Fail-closed: cannot assert absence of hits before terminal answer.
        has_pep = None
        has_sanctions = None

    requires_review = (
        bool(declared_pep)
        or state in (COMPLETED_MATCH, FAILED, NOT_CONFIGURED)
        or state in (NOT_STARTED, PENDING_PROVIDER, PARTIAL_RESULT)
    )

    return {
        "screening_state": state,
        "has_provider_pep_hit": has_pep,
        "has_provider_sanctions_hit": has_sanctions,
        "declared_pep": bool(declared_pep),
        "requires_review": requires_review,
    }


# ── UI-facing labels ──

# These labels are used by backend serializers (screening queue payload).
# They are intentionally explicit so officers cannot confuse "we have not
# completed screening yet" with "screening returned no match".
STATE_LABELS = {
    NOT_STARTED: "Awaiting Screening",
    PENDING_PROVIDER: "Screening Pending Provider",
    PARTIAL_RESULT: "Partial Screening Result",
    COMPLETED_CLEAR: "No Match",
    COMPLETED_MATCH: "Provider Match",
    NOT_CONFIGURED: "Screening Not Configured",
    FAILED: "Screening Unavailable",
    LIVE_PROVIDER: "Live Provider",
    SANDBOX_PROVIDER: "Sandbox Provider",
    SIMULATED_FALLBACK: "Simulated Screening",
    PENDING: "Screening Pending Provider",
}


def state_label(state: str, declared_pep: bool = False) -> str:
    """
    Return a UI-facing label for ``state``. When ``declared_pep`` is True
    and the provider state is not ``completed_match``, the declared PEP
    signal is surfaced explicitly so it cannot be lost behind a "Clear"
    or "Pending" label.
    """
    base = STATE_LABELS.get(state, "Awaiting Screening")
    if declared_pep and state != COMPLETED_MATCH:
        if state == COMPLETED_CLEAR:
            return "Declared PEP — No Match"
        return "Declared PEP — " + base
    return base


# Mapping from canonical state to the legacy three-value
# (clear|match|review|pending|not_configured|unavailable) fields used by
# existing UI code (watchlist_status, pep_screening_status). These are kept
# narrow so non-terminal states **never** map to "clear".
def legacy_status_value(state: str, has_hit: Optional[bool]) -> str:
    """
    Translate a canonical state + per-dimension hit (PEP or sanctions) into
    the legacy ``watchlist_status`` / ``pep_screening_status`` value.

    * ``completed_clear``      → ``clear``
    * ``completed_match`` w/ hit → ``match``
    * ``completed_match`` w/o hit → ``review`` (other-dimension hit only)
    * ``pending_provider``     → ``pending``
    * ``not_started``          → ``pending``
    * ``partial_result``       → ``pending``
    * ``not_configured``       → ``not_configured``
    * ``failed``               → ``unavailable``
    """
    if state == COMPLETED_CLEAR:
        return "clear"
    if state == COMPLETED_MATCH:
        if has_hit is True:
            return "match"
        # Provider returned a terminal answer with hits in another dimension
        return "review"
    if state == NOT_CONFIGURED:
        return "not_configured"
    if state == FAILED:
        return "unavailable"
    # NOT_STARTED, PENDING_PROVIDER, PARTIAL_RESULT
    return "pending"
