"""Pure review runner.

Bundle in, deterministic finding list and review hash out. The runner takes no
database handle, opens no connection, reads no clock, calls no external
service, and triggers no authoritative function.

Phase 0B-1 ships **no probes**. ``probes`` defaults to empty, so a review of any
bundle produces an empty finding set and a hash determined entirely by the
bundle and the version constants. The parameter exists so the reproducibility
harness can inject synthetic probes and prove that finding identifiers and the
review hash are stable — it is not a probe framework, and adding a real probe
is Phase 0B-2 work.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    NON_CLEAR_STATUSES,
    PROBE_SET_VERSION,
    REQUIRED_FINDING_FIELDS,
    REVIEW_SCHEMA_VERSION,
    AvailabilityStatus,
    FindingStatus,
)
from .hashing import (
    compute_finding_id,
    compute_input_bundle_hash,
    compute_review_hash,
    sort_findings,
)
from .policy import PolicyProfile, unconfigured_policy

#: A probe receives the bundle and the policy contract and returns finding
#: drafts. It is handed no database handle and no clock by construction.
Probe = Callable[[Mapping[str, Any], PolicyProfile], Sequence[Mapping[str, Any]]]


class ProbeContractError(ValueError):
    """A probe returned a draft that does not satisfy the finding contract."""


def _validate_draft(draft: Mapping[str, Any], index: int) -> None:
    missing = sorted(set(REQUIRED_FINDING_FIELDS) - set(draft))
    if missing:
        raise ProbeContractError(
            f"probe draft {index} is missing required fields: {missing}"
        )

    status = draft["status"]
    status_value = status.value if isinstance(status, FindingStatus) else str(status)
    if status_value not in {member.value for member in FindingStatus}:
        raise ProbeContractError(f"probe draft {index} has unknown status {status!r}")

    availability = draft["availability_status"]
    availability_value = (
        availability.value
        if isinstance(availability, AvailabilityStatus)
        else str(availability)
    )
    if availability_value not in {member.value for member in AvailabilityStatus}:
        raise ProbeContractError(
            f"probe draft {index} has unknown availability_status {availability!r}"
        )

    # The invariant that makes the whole output trustworthy: a check that could
    # not be evaluated is never a check that passed.
    if (
        status_value == FindingStatus.CLEAR.value
        and availability_value != AvailabilityStatus.AVAILABLE.value
    ):
        raise ProbeContractError(
            f"probe draft {index} reports status 'clear' with availability "
            f"{availability_value!r}; an unevaluable check must report "
            "'unavailable' or 'not_replayable', never 'clear'"
        )

    if not isinstance(draft.get("evidence_refs"), (list, tuple)):
        raise ProbeContractError(
            f"probe draft {index} must supply evidence_refs as a list"
        )


def _normalise_draft(
    draft: Mapping[str, Any],
    *,
    subject_type: str,
    subject_id: str,
    policy_version: str,
) -> dict[str, Any]:
    finding = dict(draft)

    for key in ("status", "availability_status", "severity", "category"):
        value = finding.get(key)
        finding[key] = getattr(value, "value", value)

    refs = [str(ref) for ref in finding.get("evidence_refs") or []]
    finding["evidence_refs"] = sorted(refs)
    finding["source_modules"] = sorted(
        str(module) for module in finding.get("source_modules") or []
    )

    # The primary reference is what separates two hits of the same probe on the
    # same subject. Lowest sorted ref is deterministic; a probe that can hit
    # more than once must therefore give each hit a distinct reference set.
    primary = finding["evidence_refs"][0] if finding["evidence_refs"] else ""

    finding["finding_id"] = compute_finding_id(
        subject_type=subject_type,
        subject_id=subject_id,
        probe_id=str(finding.get("probe_id") or ""),
        probe_version=str(finding.get("probe_version") or ""),
        category=str(finding.get("category") or ""),
        primary_evidence_ref=primary,
    )
    finding["subject_type"] = subject_type
    finding["subject_id"] = subject_id
    finding["policy_version"] = policy_version
    return finding


def run_review(
    bundle: Mapping[str, Any],
    *,
    policy: PolicyProfile | None = None,
    probes: Sequence[Probe] = (),
    probe_set_version: str = PROBE_SET_VERSION,
) -> dict[str, Any]:
    """Run a review over an assembled bundle.

    Args:
        bundle: Output of :func:`assemble_application_bundle`. Never mutated.
        policy: Institution policy contract. Defaults to fully unconfigured.
        probes: Probe callables. **Empty in Phase 0B-1.**
        probe_set_version: Version identifier folded into ``review_hash``.

    Returns:
        The review: hashes, ordered findings, and the completeness counts that
        make unevaluable checks visible rather than absorbed into "clear".
    """
    if not isinstance(bundle, Mapping):
        raise TypeError("bundle must be a mapping")

    resolved_policy = policy if policy is not None else unconfigured_policy()
    meta = bundle.get("meta") or {}
    subject_type = str(meta.get("subject_type") or "")
    subject_id = str(meta.get("subject_id") or "")

    input_bundle_hash = compute_input_bundle_hash(bundle)

    findings: list[dict[str, Any]] = []
    for probe in probes:
        drafts = probe(bundle, resolved_policy) or []
        for index, draft in enumerate(drafts):
            _validate_draft(draft, index)
            findings.append(
                _normalise_draft(
                    draft,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    policy_version=resolved_policy.policy_version,
                )
            )

    ordered = sort_findings(findings)

    counts: dict[str, int] = {member.value: 0 for member in FindingStatus}
    for finding in ordered:
        counts[str(finding.get("status"))] = counts.get(str(finding.get("status")), 0) + 1

    review_hash = compute_review_hash(
        policy_version=resolved_policy.policy_version,
        probe_set_version=probe_set_version,
        input_bundle_hash=input_bundle_hash,
        findings=ordered,
    )

    return {
        "as_of": meta.get("as_of"),
        "findings": ordered,
        "finding_count": len(ordered),
        "input_bundle_hash": input_bundle_hash,
        "policy_version": resolved_policy.policy_version,
        "probe_set_version": probe_set_version,
        "review_completeness": {
            "probes_run": len(probes),
            "status_counts": counts,
            # Surfaced separately so a caller cannot read "no hits" as "clean"
            # when checks simply did not run.
            "unevaluable_count": sum(
                counts[status.value] for status in NON_CLEAR_STATUSES
            ),
        },
        "review_hash": review_hash,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "subject_id": subject_id,
        "subject_type": subject_type,
    }
