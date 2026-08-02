"""What this review could not establish, and why.

Limitations are disclosed on every review they apply to, and each one states
whether it is a **product boundary** — a thing the platform deliberately does
not claim — or an **environment gap** — a dependency that happens to be absent
here and could be present elsewhere. Collapsing the two would be dishonest in
both directions: it would present a deliberate scope decision as a fault, and a
missing credential as a design.

A limitation never softens a finding. "P-04 cannot be validated against a live
provider in this environment" does not make an undefensible screening result
less undefensible; it bounds what a *clear* result from that probe would have
been worth.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, NamedTuple, Sequence

PRODUCT_BOUNDARY = "product_boundary"
ENVIRONMENT_GAP = "environment_gap"
HISTORIC_REPLAY = "historic_replay"

LIMITATION_KINDS = (PRODUCT_BOUNDARY, ENVIRONMENT_GAP, HISTORIC_REPLAY)


class Limitation(NamedTuple):
    limitation_id: str
    kind: str
    statement: str
    #: True when this limitation applies to the given finding set.
    applies: Callable[[Sequence[Mapping[str, Any]], Mapping[str, Any]], bool]


def _any_finding(findings: Sequence[Mapping[str, Any]], **match: str) -> bool:
    return any(
        all(str(finding.get(field) or "") == value for field, value in match.items())
        for finding in findings
    )


def _probe_present(findings: Sequence[Mapping[str, Any]], probe_id: str) -> bool:
    return any(str(finding.get("probe_id") or "") == probe_id for finding in findings)


def _dependency(bundle: Mapping[str, Any], name: str) -> str:
    dependencies = ((bundle.get("availability") or {}).get("dependencies") or {})
    return str(dependencies.get(name) or "")


#: Ordered by identifier at emit time, not here. Every entry names the exact
#: condition that makes it apply, so a limitation cannot be attached out of
#: caution to a review it does not bear on.
LIMITATIONS: tuple[Limitation, ...] = (
    Limitation(
        limitation_id="p03_asymmetric_replay",
        kind=PRODUCT_BOUNDARY,
        statement=(
            "EDD routing divergence is reported in one direction only. The "
            "governed policy reads more inputs than the stored fact contract "
            "declares, so a recomputed standard route against a stored EDD "
            "route cannot be distinguished from an incomplete contract and is "
            "reported as not replayable rather than as divergence. Divergence "
            "in the unsafe direction — policy requires EDD, standard was "
            "stored — is reported in full."
        ),
        applies=lambda findings, bundle: _probe_present(findings, "P-03"),
    ),
    Limitation(
        limitation_id="p04_live_provider_validation",
        kind=ENVIRONMENT_GAP,
        statement=(
            "Screening reliance findings in this environment were evaluated "
            "against results that did not come from a live provider. The probe "
            "is proven to fire correctly on an undefensible result; what a "
            "clear result from it is worth has not been established against a "
            "live provider response."
        ),
        applies=lambda findings, bundle: _dependency(bundle, "screening_report")
        != "available"
        or _any_finding(findings, probe_id="P-04", category="screening", status="hit"),
    ),
    Limitation(
        limitation_id="adverse_media_source_coverage",
        kind=PRODUCT_BOUNDARY,
        statement=(
            "Adverse-media evidence is limited to what the institution's "
            "configured screening source returns. No universal media coverage "
            "is claimed, and no separate adverse-media provider is called."
        ),
        applies=lambda findings, bundle: any(
            str(finding.get("category") or "") == "adverse_media" for finding in findings
        ),
    ),
    Limitation(
        limitation_id="historic_snapshot_not_reconstructable",
        kind=HISTORIC_REPLAY,
        statement=(
            "This review reflects current stored state. Application rows are "
            "mutated in place with no snapshot table, so the state as it stood "
            "at a past decision cannot be reconstructed; any check that depends "
            "on it reports not replayable rather than an answer."
        ),
        applies=lambda findings, bundle: _any_finding(findings, status="not_replayable")
        or _any_finding(findings, availability_status="snapshot_incomplete"),
    ),
    Limitation(
        limitation_id="policy_dependent_probes_not_implemented",
        kind=PRODUCT_BOUNDARY,
        statement=(
            "Ownership coverage, PEP evidence sufficiency and factor evidence "
            "sufficiency are not evaluated. Those checks depend on institution "
            "policy configuration that does not yet exist, and answering them "
            "against platform defaults would assert a standard the institution "
            "has not set."
        ),
        # Always. It is a statement about the probe set, and the probe set is
        # the same on every review this engine aggregates.
        applies=lambda findings, bundle: True,
    ),
    Limitation(
        limitation_id="registry_credentials_absent",
        kind=ENVIRONMENT_GAP,
        statement=(
            "No public-registry credentials are configured in this deployment, "
            "so registry corroboration contributed nothing to this review."
        ),
        applies=lambda findings, bundle: _dependency(bundle, "public_registry")
        not in ("", "available"),
    ),
    Limitation(
        limitation_id="probe_dependency_unavailable",
        kind=ENVIRONMENT_GAP,
        statement=(
            "One or more checks could not run because a dependency they read "
            "was absent, gated, or unconfigured. The unavailable checks section "
            "names each one and the reason; none of them is a passing check."
        ),
        applies=lambda findings, bundle: any(
            str(finding.get("status") or "") == "unavailable" for finding in findings
        ),
    ),
)


def applicable_limitations(
    findings: Sequence[Mapping[str, Any]],
    bundle: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """The limitations bearing on this review, in stable identifier order."""
    return [
        {
            "kind": limitation.kind,
            "limitation_id": limitation.limitation_id,
            "statement": limitation.statement,
        }
        for limitation in sorted(LIMITATIONS, key=lambda item: item.limitation_id)
        if limitation.applies(findings, bundle)
    ]
