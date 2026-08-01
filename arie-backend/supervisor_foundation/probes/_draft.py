"""Shared finding-draft construction for the Phase 0B-2 probes.

One helper, not a framework. It exists so every probe populates the whole
canonical contract and cannot quietly omit a field — the runner rejects an
incomplete draft, and catching that at construction is cheaper than at review
time.

``finding_id``, ``subject_type``, ``subject_id``, ``policy_version`` and
``created_at`` are stamped by the runner, not here.

``primary_evidence_ref`` is optional but is supplied by every check that can fire
more than once per subject. See :func:`finding_draft` for why.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ..contracts import AvailabilityStatus, FindingStatus

#: Severity vocabulary, mirroring ``supervisor.schemas.Severity``.
CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"
INFO = "info"

#: Must appear in any finding that speaks about adverse media. RegMind reads
#: adverse-media signals from the institution's configured screening source and
#: makes no claim of universal media coverage; a finding that implied otherwise
#: would overstate what the platform can evidence.
ADVERSE_MEDIA_COVERAGE_NOTE = (
    "RegMind evaluates adverse-media evidence available through the "
    "institution's configured screening source. It does not establish "
    "universal media coverage."
)

#: Phrases that would make a finding unfalsifiable. Rejected at construction so
#: they cannot reach an officer, and asserted again by a test over real output.
BANNED_PHRASES = (
    "consider further review",
    "may be elevated",
    "may be incomplete",
    "appears satisfactory",
    "further investigation may",
    "should be reviewed",
    "potentially problematic",
)


class FindingDraftError(ValueError):
    """A probe attempted to build a draft that violates the finding contract."""


def _check_language(field: str, text: str) -> None:
    lowered = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            raise FindingDraftError(
                f"{field} contains the non-specific phrase {phrase!r}; a finding "
                "must make a falsifiable claim"
            )


def finding_draft(
    *,
    probe_id: str,
    probe_version: str,
    category: str,
    severity: str,
    status: FindingStatus,
    availability_status: AvailabilityStatus,
    confidence: float,
    claim: str,
    evidence_refs: Sequence[str],
    source_modules: Sequence[str],
    why_it_matters: str,
    regulatory_or_policy_basis: str,
    officer_question: str,
    required_action: str,
    close_condition: str,
    primary_evidence_ref: str | None = None,
) -> dict[str, Any]:
    """Build one canonical finding draft, validating the parts a test cannot.

    ``primary_evidence_ref`` names the reference the finding's identity derives
    from. Supply it wherever a check can fire more than once for one subject —
    per screening subject, per risk factor, per review. Without it the runner
    falls back to the lowest-sorting reference, and two findings that happen to
    share that reference collapse onto a single identifier. It must be one of
    ``evidence_refs``; the runner rejects a draft that cites a reference it does
    not otherwise carry.
    """
    if not claim.strip():
        raise FindingDraftError("claim must not be empty")
    for field, text in (
        ("claim", claim),
        ("why_it_matters", why_it_matters),
        ("required_action", required_action),
        ("officer_question", officer_question),
    ):
        _check_language(field, text)

    if not 0.0 <= confidence <= 1.0:
        raise FindingDraftError(f"confidence out of range: {confidence!r}")

    refs = list(evidence_refs)
    if primary_evidence_ref is not None and primary_evidence_ref not in refs:
        raise FindingDraftError(
            f"primary_evidence_ref {primary_evidence_ref!r} is not among the "
            "finding's evidence_refs"
        )

    draft: dict[str, Any] = {
        "probe_id": probe_id,
        "probe_version": probe_version,
        "category": category,
        "severity": severity,
        "status": status,
        "availability_status": availability_status,
        "confidence": confidence,
        "claim": claim,
        "evidence_refs": refs,
        "source_modules": list(source_modules),
        "why_it_matters": why_it_matters,
        "regulatory_or_policy_basis": regulatory_or_policy_basis,
        "officer_question": officer_question,
        "required_action": required_action,
        "close_condition": close_condition,
    }
    if primary_evidence_ref is not None:
        draft["primary_evidence_ref"] = primary_evidence_ref
    return draft


# ── Shared bundle readers ────────────────────────────────────────────
# Small accessors used by more than one probe. Kept here rather than
# duplicated, and deliberately not generalised beyond what the four probes need.


def subject_ref(bundle: Mapping[str, Any]) -> str:
    return f"application:{(bundle.get('meta') or {}).get('subject_id')}"


def as_of(bundle: Mapping[str, Any]) -> str:
    return str((bundle.get("meta") or {}).get("as_of") or "")


def is_approved(bundle: Mapping[str, Any]) -> bool:
    """Whether the case reached an approval.

    Two independent signals, either sufficient: the application status, and an
    ``approve`` decision record. Severity for several probes turns on this,
    because a defect an officer *relied on* is materially worse than the same
    defect on a case still in review.
    """
    fields = (bundle.get("application") or {}).get("fields") or {}
    if str(fields.get("status") or "").strip().lower() == "approved":
        return True
    records = (bundle.get("decision") or {}).get("records") or []
    return any(
        str(record.get("decision_type") or "").strip().lower() == "approve"
        for record in records
        if isinstance(record, Mapping)
    )


def approval_refs(bundle: Mapping[str, Any]) -> list[str]:
    """Evidence references establishing the approval state a severity rests on."""
    refs = [f"{subject_ref(bundle)}#status"]
    for record in (bundle.get("decision") or {}).get("records") or []:
        if not isinstance(record, Mapping):
            continue
        if str(record.get("decision_type") or "").strip().lower() == "approve":
            refs.append(f"decision:{record.get('id')}")
    return refs


def severity_by_reliance(bundle: Mapping[str, Any], *, approved: str, pending: str) -> str:
    """Pick a severity by whether the case was approved on the defect."""
    return approved if is_approved(bundle) else pending


def unique_refs(refs: Iterable[Any]) -> list[str]:
    """De-duplicate while dropping empties. Ordering is the runner's job."""
    seen: list[str] = []
    for ref in refs:
        text = str(ref)
        if text and text not in seen:
            seen.append(text)
    return seen
