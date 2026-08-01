"""P-04 — Screening reliance defensibility.

**What it asks.** Was the screening this case relied on actually defensible —
live, complete, dispositioned, current — and where it reads as clear, is that a
clearance or merely an absence?

**Why it is not a restatement.** The Screening Queue shows a status. It does not
show that an *approved* case was approved on a sandbox result, nor that a
material match was never dispositioned, nor — the sharpest one — that
adverse-media reads clear because no assessment ever ran. `rule_engine` scores
``adverse_media`` as 1 (clear) when no screening data exists, so an absence and
a clean result are indistinguishable downstream.

**Clock discipline.** ``build_screening_truth_summary`` and
``derive_screening_truth`` both reach a wall clock and are never called.
Freshness is derived here from the stored ``screening_valid_until`` and the
injected ``as_of``.

**Coverage honesty.** Every adverse-media finding carries
``ADVERSE_MEDIA_COVERAGE_NOTE``; the platform reads what its configured
screening source provides and claims no universal media coverage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..contracts import AvailabilityStatus, FindingStatus
from ._draft import (
    ADVERSE_MEDIA_COVERAGE_NOTE,
    CRITICAL,
    HIGH,
    INFO,
    MEDIUM,
    approval_refs,
    finding_draft,
    is_approved,
    severity_by_reliance,
    subject_ref,
    unique_refs,
)

PROBE_ID = "P-04"
PROBE_VERSION = "p04-v1"

SOURCE_MODULES = (
    "screening_state.provider_mode_from_record",
    "screening_state.derive_screening_state",
    "rule_engine.compute_risk_score",
    "supervisor_foundation.bundle",
)

#: Provider modes that cannot support a defensible reliance.
UNDEFENSIBLE_MODES = {
    "sandbox_provider": "a sandbox provider",
    "simulated_fallback": "a simulated fallback rather than a provider call",
}
#: Modes meaning the provider never produced an answer at all.
UNAVAILABLE_MODES = {"not_configured", "failed", "pending"}

#: The authoritative terminal states. Anything else means screening did not
#: complete, whatever the aggregate status may suggest.
TERMINAL_STATES = frozenset({"completed_clear", "completed_match"})

#: `rule_engine` scores adverse media 1 when it has nothing to read. That is an
#: absence, not a clearance — which is the whole point of check 5.
ADVERSE_MEDIA_CLEAR_SCORE = 1


def _parse_timestamp(value: Any) -> datetime | None:
    """Deterministic ISO-8601 parse. Returns ``None`` rather than guessing.

    Never substitutes a default. A date the probe cannot read is reported as
    unreadable, because silently treating it as valid is how a defective record
    becomes a passing check.
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _subject_ref(subject: Mapping[str, Any], application: str) -> str:
    return f"screening:{application}#subject:{subject.get('subject_key')}"


def _field_ref(subject: Mapping[str, Any], application: str, field: str) -> str:
    """Reference to one field of one screening subject.

    This probe fires up to four times per subject, and every subject cites the
    same application-level approval references. Naming the exact field examined
    gives each finding a distinct identity anchor; without it, four different
    defects on one subject — and the same defect on two subjects — would collapse
    onto one identifier.
    """
    return f"{_subject_ref(subject, application)}#{field}"


def _provider_findings(
    bundle: Mapping[str, Any], subject: Mapping[str, Any], application: str
) -> list[dict[str, Any]]:
    mode = str(subject.get("provider_mode") or "").strip().lower()
    key = subject.get("subject_key")
    anchor = _field_ref(subject, application, "provider_mode")
    refs = unique_refs(
        [anchor, _subject_ref(subject, application), *approval_refs(bundle)]
    )

    if mode in UNDEFENSIBLE_MODES:
        approved = is_approved(bundle)
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="screening",
                severity=severity_by_reliance(bundle, approved=CRITICAL, pending=HIGH),
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    f"Screening subject '{key}' was screened against "
                    f"{UNDEFENSIBLE_MODES[mode]}, not a live provider."
                    + (
                        " The application was approved relying on this result."
                        if approved
                        else ""
                    )
                ),
                evidence_refs=refs,
                primary_evidence_ref=anchor,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "A sandbox or simulated result carries no assurance about "
                    "the real subject. Downstream it is shaped exactly like a "
                    "live clear result, so nothing on the screening surface "
                    "distinguishes the two."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    f"Was subject '{key}' ever screened against the live "
                    "provider?"
                ),
                required_action=(
                    "Re-run screening for this subject against the live provider "
                    "through the existing authoritative path."
                ),
                close_condition=(
                    f"Subject '{key}' records provider mode 'live_provider' with "
                    "a terminal result."
                ),
            )
        ]

    if mode in UNAVAILABLE_MODES or not mode:
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="screening",
                severity=MEDIUM,
                status=FindingStatus.UNAVAILABLE,
                availability_status=(
                    AvailabilityStatus.CREDENTIALS_ABSENT
                    if mode == "not_configured"
                    else AvailabilityStatus.DATA_ABSENT
                ),
                confidence=1.0,
                claim=(
                    f"Screening provider state for subject '{key}' is "
                    f"'{mode or 'unrecorded'}', so whether this subject was "
                    "screened against a live provider cannot be established."
                ),
                evidence_refs=refs,
                primary_evidence_ref=anchor,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "An unestablished provider state is not a clear result. This "
                    "check did not reach an answer and must not be counted as one."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    f"Is the screening provider configured and reachable for "
                    f"subject '{key}'?"
                ),
                required_action=(
                    "Confirm provider configuration and re-run screening for the "
                    "subject."
                ),
                close_condition=(
                    f"Subject '{key}' records a resolved provider mode with a "
                    "terminal screening state."
                ),
            )
        ]

    return []


def _terminality_findings(
    bundle: Mapping[str, Any], subject: Mapping[str, Any], application: str
) -> list[dict[str, Any]]:
    state = str(subject.get("screening_state") or "").strip().lower()
    if state in TERMINAL_STATES:
        return []
    key = subject.get("subject_key")
    anchor = _field_ref(subject, application, "screening_state")
    return [
        finding_draft(
            probe_id=PROBE_ID,
            probe_version=PROBE_VERSION,
            category="screening",
            severity=severity_by_reliance(bundle, approved=CRITICAL, pending=HIGH),
            status=FindingStatus.HIT,
            availability_status=AvailabilityStatus.AVAILABLE,
            confidence=1.0,
            claim=(
                f"Screening for subject '{key}' did not complete: its recorded "
                f"state is '{state or 'unrecorded'}', which is not a terminal "
                "result."
                + (
                    " The application was approved with this subject unscreened."
                    if is_approved(bundle)
                    else ""
                )
            ),
            evidence_refs=unique_refs(
                [anchor, _subject_ref(subject, application), *approval_refs(bundle)]
            ),
            primary_evidence_ref=anchor,
            source_modules=list(SOURCE_MODULES),
            why_it_matters=(
                "A non-terminal subject has not been screened, only queued. "
                "Aggregate screening status can read as complete while an "
                "individual required subject never returned a result."
            ),
            regulatory_or_policy_basis="internal_policy_only",
            officer_question=(
                f"Why did screening for subject '{key}' not reach a terminal "
                "result?"
            ),
            required_action=(
                "Complete screening for this subject through the existing "
                "authoritative path."
            ),
            close_condition=(
                f"Subject '{key}' records a terminal screening state of "
                "completed_clear or completed_match."
            ),
        )
    ]


def _disposition_findings(
    bundle: Mapping[str, Any],
    subject: Mapping[str, Any],
    reviews_by_id: Mapping[Any, Mapping[str, Any]],
    application: str,
) -> list[dict[str, Any]]:
    if not subject.get("has_material_hit"):
        return []

    key = subject.get("subject_key")
    review_id = subject.get("review_id")
    review = reviews_by_id.get(review_id) if review_id is not None else None
    anchor = _field_ref(subject, application, "material_hit_disposition")
    refs = unique_refs(
        [
            anchor,
            _subject_ref(subject, application),
            f"screening_disposition:{review_id}" if review_id is not None else "",
            *approval_refs(bundle),
        ]
    )

    if review is None:
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="sanctions",
                severity=severity_by_reliance(bundle, approved=CRITICAL, pending=HIGH),
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    f"Screening subject '{key}' carries a material match, but no "
                    "officer disposition is recorded against it."
                    + (
                        " The application was approved with the match "
                        "undispositioned."
                        if is_approved(bundle)
                        else ""
                    )
                ),
                evidence_refs=refs,
                primary_evidence_ref=anchor,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "An undispositioned material match means nobody recorded a "
                    "judgement on a hit the provider returned. There is no "
                    "reviewer, no reason and no date to point to."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    f"Who assessed the match on subject '{key}', and what was the "
                    "conclusion?"
                ),
                required_action=(
                    "Record a disposition for this match through the existing "
                    "screening review workflow."
                ),
                close_condition=(
                    f"A screening review exists for subject '{key}' with a "
                    "disposition, an actor and a rationale."
                ),
            )
        ]

    requires_four_eyes = bool(review.get("requires_four_eyes"))
    second_reviewer = review.get("second_reviewer_id")
    disposition_code = str(review.get("disposition_code") or "").strip().lower()

    if requires_four_eyes and not second_reviewer:
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="sanctions",
                severity=HIGH,
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    f"The match on screening subject '{key}' was dispositioned "
                    f"as '{disposition_code or 'unrecorded'}' and flagged as "
                    "requiring second sign-off, but no second reviewer is "
                    "recorded."
                ),
                evidence_refs=refs,
                primary_evidence_ref=anchor,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "The four-eyes requirement was raised on this disposition "
                    "and then not met. A single officer cleared a match the "
                    "institution's own control said needed two."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "Who provided the second sign-off for this clearance?"
                ),
                required_action=(
                    "Obtain and record the second sign-off through the existing "
                    "screening review workflow."
                ),
                close_condition=(
                    "The screening review records a second reviewer and second "
                    "disposition."
                ),
            )
        ]

    return []


def _freshness_findings(
    bundle: Mapping[str, Any], subject: Mapping[str, Any], application: str, as_of_dt: datetime
) -> list[dict[str, Any]]:
    key = subject.get("subject_key")
    raw_valid_until = subject.get("screening_valid_until")
    anchor = _field_ref(subject, application, "screening_valid_until")
    refs = unique_refs(
        [
            anchor,
            _subject_ref(subject, application),
            f"{subject_ref(bundle)}#as_of",
            *approval_refs(bundle),
        ]
    )

    if raw_valid_until in (None, ""):
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="screening",
                severity=MEDIUM,
                status=FindingStatus.UNAVAILABLE,
                availability_status=AvailabilityStatus.DATA_ABSENT,
                confidence=1.0,
                claim=(
                    f"Screening subject '{key}' records no validity expiry, so "
                    "whether its result is still current cannot be established."
                ),
                evidence_refs=refs,
                primary_evidence_ref=anchor,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "Without a validity date, currency is unknowable. Reporting "
                    "this honestly keeps it distinct from a result confirmed "
                    "in date."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    f"What validity period applies to the screening of subject "
                    f"'{key}'?"
                ),
                required_action=(
                    "Re-run screening so a validity expiry is recorded."
                ),
                close_condition=(
                    f"Subject '{key}' records a screening validity expiry."
                ),
            )
        ]

    valid_until = _parse_timestamp(raw_valid_until)
    if valid_until is None:
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="screening",
                severity=MEDIUM,
                status=FindingStatus.UNAVAILABLE,
                availability_status=AvailabilityStatus.DATA_ABSENT,
                confidence=1.0,
                claim=(
                    f"Screening subject '{key}' records validity expiry "
                    f"{raw_valid_until!r}, which is not a readable timestamp, so "
                    "currency cannot be established."
                ),
                evidence_refs=refs,
                primary_evidence_ref=anchor,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "An unreadable date is not a valid one. Treating it as "
                    "current would turn a data defect into a passing check."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    f"Why is the screening validity date for subject '{key}' "
                    "malformed?"
                ),
                required_action="Correct or re-record the screening validity date.",
                close_condition=(
                    f"Subject '{key}' records a readable validity expiry."
                ),
            )
        ]

    # Boundary: valid *through* the expiry instant. Stale strictly after it.
    if valid_until >= as_of_dt:
        return []

    return [
        finding_draft(
            probe_id=PROBE_ID,
            probe_version=PROBE_VERSION,
            category="screening",
            severity=severity_by_reliance(bundle, approved=HIGH, pending=MEDIUM),
            status=FindingStatus.HIT,
            availability_status=AvailabilityStatus.AVAILABLE,
            confidence=1.0,
            claim=(
                f"Screening for subject '{key}' expired at "
                f"{valid_until.isoformat()}, before the review instant "
                f"{as_of_dt.isoformat()}. The result relied on is out of date."
            ),
            evidence_refs=refs,
            primary_evidence_ref=anchor,
            source_modules=list(SOURCE_MODULES),
            why_it_matters=(
                "An expired screening result reflects the subject as they were, "
                "not as they are. Sanctions and PEP status change between "
                "screenings, which is why the validity period exists."
            ),
            regulatory_or_policy_basis="internal_policy_only",
            officer_question=(
                f"When was subject '{key}' last screened against current lists?"
            ),
            required_action=(
                "Re-run screening for this subject through the existing "
                "authoritative path."
            ),
            close_condition=(
                f"Subject '{key}' records a screening validity expiry later than "
                "the review instant."
            ),
        )
    ]


def _adverse_media_absence_finding(
    bundle: Mapping[str, Any], application: str
) -> list[dict[str, Any]]:
    """Check 5 — a clear adverse-media score produced by having no data."""
    factors = (bundle.get("risk") or {}).get("factors") or []
    adverse = next(
        (
            factor
            for factor in factors
            if isinstance(factor, Mapping)
            and str(factor.get("factor_key") or "") == "adverse_media"
        ),
        None,
    )
    if adverse is None:
        return []

    try:
        score = int(adverse.get("rule_score"))
    except (TypeError, ValueError):
        return []
    if score > ADVERSE_MEDIA_CLEAR_SCORE:
        return []

    screening = bundle.get("screening") or {}
    if screening.get("report_present"):
        return []

    return [
        finding_draft(
            probe_id=PROBE_ID,
            probe_version=PROBE_VERSION,
            category="adverse_media",
            severity=severity_by_reliance(bundle, approved=HIGH, pending=MEDIUM),
            status=FindingStatus.HIT,
            availability_status=AvailabilityStatus.AVAILABLE,
            confidence=1.0,
            claim=(
                "The adverse-media risk factor is scored "
                f"{score} (clear) although no screening report exists for this "
                "application. Absence of an assessment was represented as a "
                "clear result."
            ),
            evidence_refs=unique_refs(
                [
                    f"risk:{application}#factor:adverse_media",
                    f"{application}#screening_report_present",
                    *approval_refs(bundle),
                ]
            ),
            primary_evidence_ref=f"risk:{application}#factor:adverse_media",
            source_modules=list(SOURCE_MODULES),
            why_it_matters=(
                "Downstream, a clear adverse-media score is indistinguishable "
                "from an assessment that ran and found nothing. Nobody is "
                "prompted to ask whether the check happened at all. "
                + ADVERSE_MEDIA_COVERAGE_NOTE
            ),
            regulatory_or_policy_basis="internal_policy_only",
            officer_question=(
                "Has any adverse-media assessment been performed for this "
                "customer?"
            ),
            required_action=(
                "Run screening so adverse-media evidence is assessed, and record "
                "the outcome."
            ),
            close_condition=(
                "A screening report exists and the adverse-media factor reflects "
                "an assessment that was actually performed."
            ),
        )
    ]


def probe(bundle: Mapping[str, Any], policy: Any) -> Sequence[Mapping[str, Any]]:
    """Run P-04 over a bundle. Pure; no clock, no database, no provider."""
    screening = bundle.get("screening") or {}
    application = str((bundle.get("meta") or {}).get("subject_id") or "")
    subject = subject_ref(bundle)
    subjects = [row for row in (screening.get("subjects") or []) if isinstance(row, Mapping)]

    drafts: list[dict[str, Any]] = []

    # Check 5 runs regardless of whether subjects exist — it is precisely about
    # the case where they do not.
    drafts.extend(_adverse_media_absence_finding(bundle, application))

    if not subjects:
        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="screening",
                severity=severity_by_reliance(bundle, approved=HIGH, pending=MEDIUM),
                status=FindingStatus.UNAVAILABLE,
                availability_status=AvailabilityStatus.DATA_ABSENT,
                confidence=1.0,
                claim=(
                    "No per-subject screening evidence exists for this "
                    "application, so provider defensibility, completion and "
                    "match disposition cannot be examined."
                ),
                evidence_refs=unique_refs(
                    [f"{subject}#screening_subjects", *approval_refs(bundle)]
                ),
                primary_evidence_ref=f"{subject}#screening_subjects",
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "Absent screening evidence is not a clean screening result. "
                    "These checks did not run and must not be counted as passes."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "Has screening been performed for this application?"
                ),
                required_action=(
                    "Run screening through the existing authoritative path."
                ),
                close_condition=(
                    "Per-subject screening evidence exists and these checks can "
                    "be evaluated."
                ),
            )
        )
        return drafts

    as_of_dt = _parse_timestamp((bundle.get("meta") or {}).get("as_of"))
    reviews_by_id = {
        review.get("id"): review
        for review in (screening.get("reviews") or [])
        if isinstance(review, Mapping)
    }

    for row in subjects:
        drafts.extend(_provider_findings(bundle, row, application))
        drafts.extend(_terminality_findings(bundle, row, application))
        drafts.extend(_disposition_findings(bundle, row, reviews_by_id, application))
        if as_of_dt is not None:
            drafts.extend(_freshness_findings(bundle, row, application, as_of_dt))

    if drafts:
        return drafts

    return [
        finding_draft(
            probe_id=PROBE_ID,
            probe_version=PROBE_VERSION,
            category="screening",
            severity=INFO,
            status=FindingStatus.CLEAR,
            availability_status=AvailabilityStatus.AVAILABLE,
            confidence=1.0,
            claim=(
                f"All {len(subjects)} screening subjects completed against a live "
                "provider, within their validity period, with any material match "
                "dispositioned."
            ),
            evidence_refs=unique_refs(
                [_subject_ref(row, application) for row in subjects]
            ),
            source_modules=list(SOURCE_MODULES),
            why_it_matters=(
                "Recorded so the completeness count distinguishes screening "
                "checks that ran and passed from checks that never ran. "
                + ADVERSE_MEDIA_COVERAGE_NOTE
            ),
            regulatory_or_policy_basis="internal_policy_only",
            officer_question="None — this check passed.",
            required_action="None.",
            close_condition="Already satisfied.",
        )
    ]
