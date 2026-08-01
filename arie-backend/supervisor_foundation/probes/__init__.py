"""Phase 0B-2 Supervisor probes.

Four probes, listed explicitly in :data:`PROBES`. There is no registry, no
discovery, no plugin loader and no DSL — adding a fifth probe means importing it
and adding a line to the tuple, which is exactly as much machinery as four
probes justify.

Ordering in :data:`PROBES` is fixed and part of the reproducibility contract in
spirit only: :func:`supervisor_foundation.review.run_review` sorts findings
canonically, so probe order does not affect ``review_hash``. The list is ordered
by probe identifier anyway, because a reader should not have to check.

**Import direction is one-way.** These modules import from the foundation and
read authoritative modules for *pure* helpers (policy tables, routing
evaluation, string validators). No authoritative module imports this package,
and a guard test enforces it. Nothing here writes, and nothing here reads a
clock — the only permitted time source is ``bundle["meta"]["as_of"]``.
"""

from __future__ import annotations

from . import (
    edd_divergence,
    monitoring_requirement,
    risk_resolution,
    screening_defensibility,
)

#: The Phase 0B-2 probe set, in probe-identifier order.
#:
#: P-01 (document reliance), P-05 (source-of-funds corroboration) and P-07
#: (policy-artifact drift) are specified in the Phase 0A framework but are
#: **not** authorised for this phase and are deliberately absent.
PROBES = (
    risk_resolution.probe,          # P-02 Risk factor resolution integrity
    edd_divergence.probe,           # P-03 EDD routing divergence
    screening_defensibility.probe,  # P-04 Screening reliance defensibility
    monitoring_requirement.probe,   # P-06 Monitoring requirement not established
)

#: Probe identifiers in the same order, for tests and reporting.
PROBE_IDS = (
    risk_resolution.PROBE_ID,
    edd_divergence.PROBE_ID,
    screening_defensibility.PROBE_ID,
    monitoring_requirement.PROBE_ID,
)

#: ``{probe_id: probe_version}`` for the shipped set. A version bump here must
#: be accompanied by a ``PROBE_SET_VERSION`` bump in ``contracts``.
PROBE_VERSIONS = {
    risk_resolution.PROBE_ID: risk_resolution.PROBE_VERSION,
    edd_divergence.PROBE_ID: edd_divergence.PROBE_VERSION,
    screening_defensibility.PROBE_ID: screening_defensibility.PROBE_VERSION,
    monitoring_requirement.PROBE_ID: monitoring_requirement.PROBE_VERSION,
}

__all__ = [
    "PROBES",
    "PROBE_IDS",
    "PROBE_VERSIONS",
    "edd_divergence",
    "monitoring_requirement",
    "risk_resolution",
    "screening_defensibility",
]
