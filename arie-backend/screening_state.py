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

from typing import Optional


# ── Canonical screening states ──

NOT_STARTED = "not_started"
PENDING_PROVIDER = "pending_provider"
PARTIAL_RESULT = "partial_result"
COMPLETED_CLEAR = "completed_clear"
COMPLETED_MATCH = "completed_match"
NOT_CONFIGURED = "not_configured"
FAILED = "failed"

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
PENDING_API_STATUSES = frozenset({"pending", "init", "created", "queued", "onHold"})

# Provider api_status values that mean "provider is not configured for this scope".
NOT_CONFIGURED_API_STATUSES = frozenset({"not_configured"})

# Provider api_status values that mean "we tried and failed".
FAILED_API_STATUSES = frozenset({"error", "unavailable", "blocked"})


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

    Positive-evidence override: when the record explicitly carries
    ``matched=True`` with non-empty results, it is treated as
    ``completed_match`` regardless of api_status. This preserves any
    discovered hit as actionable. The reverse is **not** symmetric:
    ``matched=False`` without a terminal api_status is **never** treated as
    ``completed_clear`` — that is the dangerous false-reassurance case
    Priority A is closing.
    """
    if not screening or not isinstance(screening, dict):
        return NOT_STARTED

    api_status = (screening.get("api_status") or "").strip()
    source = (screening.get("source") or "").strip().lower()

    # Positive-evidence override: a recorded hit is always actionable.
    if screening.get("matched") and screening.get("results"):
        return COMPLETED_MATCH

    # Explicit not_configured short-circuit (e.g. Sumsub company KYB level missing)
    if api_status in NOT_CONFIGURED_API_STATUSES:
        return NOT_CONFIGURED

    # Explicit failure / unavailable short-circuit
    if api_status in FAILED_API_STATUSES or source in ("unavailable", "blocked"):
        return FAILED

    # Pending / not-yet-terminal provider state
    if api_status in PENDING_API_STATUSES:
        return PENDING_PROVIDER

    # Terminal answer from a real provider
    if api_status in TERMINAL_API_STATUSES:
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


def build_screening_terminality_summary(report: Optional[dict], prescreening: Optional[dict] = None) -> dict:
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

    return {
        "terminal": terminal,
        "has_non_terminal": bool(has_non_terminal),
        "has_failed": bool(has_failed),
        "has_not_configured": bool(has_not_configured),
        "has_terminal_match": bool(material_hit),
        "company_screening_configured": bool(report.get("company_screening")),
        "company_state": company_state,
        "person_states": person_states,
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
    COMPLETED_CLEAR: "No Provider Match",
    COMPLETED_MATCH: "Provider Match",
    NOT_CONFIGURED: "Screening Not Configured",
    FAILED: "Screening Unavailable",
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
            return "Declared PEP — Provider Clear"
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
