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
