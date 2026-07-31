"""Safe adapters over authoritative modules.

Some authoritative functions read a wall clock or emit non-evidentiary runtime
metadata. None of them may be modified to suit the Supervisor — they are
authoritative and, in two cases, frozen. So each is wrapped here, or avoided
entirely and its *inputs* assembled instead.

Audit performed against ``main`` at the time of writing:

===========================================  ======  ======  ==========================
Source                                       Clock   Writes  Phase 0B-1 treatment
===========================================  ======  ======  ==========================
``run_memo_supervisor``                      yes[1]  no      call, strip ``checked_at``
``evaluate_document_reliance_gate``          yes[2]  no      **not called** — assemble inputs
``build_screening_truth_summary``            yes[3]  no      **not called** — assemble inputs
``evaluate_edd_routing``                     yes[4]  no      **not called** — read stored
``compute_risk_score``                       no[5]   no      **not called** — read stored
``build_required_document_expectations``     no      no      called directly
``latest_compliance_memo_row``               no      no      called directly
===========================================  ======  ======  ==========================

[1] ``supervisor_engine.py`` — ``checked_at`` only. The clock reaches exactly
    one terminal output field and influences no other value, so stripping it
    makes the whole result deterministic.
[2] ``document_reliance_gate.py`` — ``generated_at``, and, materially, the
    ``stale_verification`` blocker is derived from ``datetime.now()``. The
    staleness verdict itself would change between days, so stripping the
    timestamp is not sufficient and the gate cannot be called.
[3] ``screening_state.py`` — ``_timestamp_is_past`` compares
    ``screening_valid_until`` against ``datetime.now()``, which feeds
    ``freshness``, ``stale`` and ``approval_blocking``. Same problem as [2].
    (This one is not listed in the Phase 0A document; see the closure report.)
[4] ``edd_routing_policy.py`` — ``evaluated_at`` only, but re-running the policy
    is probe P-03, which is Phase 0B-2 scope. Not called here at all.
[5] The clock in ``rule_engine.py`` lives in the recompute-and-persist helper,
    not in ``compute_risk_score``. Not called regardless: recomputation is a
    side effect and the stored evidence is the current state.

Where a function is "not called", the bundle carries the *inputs* that function
consumes. A later probe derives the verdict from those inputs plus the injected
``as_of``, which is exactly what §10.4 of the framework requires.
"""

from __future__ import annotations

from typing import Any, Mapping

from .hashing import EXCLUDED_HASH_KEYS


def strip_non_evidentiary(value: Any) -> Any:
    """Recursively remove keys in ``EXCLUDED_HASH_KEYS``.

    Applied to anything sourced from an authoritative module before it enters
    the bundle. Removal, not blanking: a key present with a null value still
    signals "this field exists and was empty", which is a different claim.
    """
    if isinstance(value, Mapping):
        return {
            key: strip_non_evidentiary(item)
            for key, item in value.items()
            if key not in EXCLUDED_HASH_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [strip_non_evidentiary(item) for item in value]
    return value


def memo_supervisor_verdict(memo_data: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic projection of ``run_memo_supervisor``.

    ``run_memo_supervisor`` is pure computation over the memo dict — no database
    handle, no writes, no provider calls — and its only clock use is the
    terminal ``checked_at`` field. Calling it and stripping that field yields a
    stable result, and reuses the authoritative consistency logic rather than
    reimplementing it.

    The import is local so that importing this package never pulls in
    ``supervisor_engine`` unless a verdict is actually requested.
    """
    from supervisor_engine import run_memo_supervisor  # local: keep import graph thin

    verdict = run_memo_supervisor(dict(memo_data))
    return strip_non_evidentiary(verdict)
