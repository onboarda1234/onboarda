"""P-04 — Screening reliance defensibility.

**What it asks.** Was the screening this case relied on actually defensible —
live, complete, dispositioned, current — and where it reads as clear, is that a
clearance or merely an absence?

**Four checks, not five.** Provider defensibility, match disposition, freshness
and adverse-media absence. A separate terminality check was implemented and then
removed: ``derive_screening_state`` derives the state from the same provider
record the mode comes from, so a non-terminal state is entailed by a non-live
mode and reporting both produced two findings per subject saying one thing. The
provider finding names the derived state instead.

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

#: The probe's primary category from the governed register (§5.2).
#: Used when the runner has to report that this probe could not run at
#: all, so that failure lands in a governed category rather than
#: inventing one.
PROBE_CATEGORY = "screening"

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

#: The authoritative terminal states, carried for the entailment test in
#: ``test_supervisor_probe_p04_screening_defensibility``. There is deliberately
#: **no separate terminality check**: ``screening_state.derive_screening_state``
#: maps every non-live provider mode onto a non-terminal state and every live
#: one onto a terminal state, so "this subject did not complete" is entailed by
#: the provider-mode check and never independent of it. An earlier draft ran
#: both and produced two findings per subject saying the same thing — 33
#: findings across 8 production-path cases, half of them redundant.
TERMINAL_STATES = frozenset({"completed_clear", "completed_match"})

#: The one provider mode that constitutes a defensible answer.
LIVE_PROVIDER_MODE = "live_provider"

#: Every governed screening disposition code, flattened from the three outcome
#: buckets ``server._SCREENING_DISPOSITION_CODES`` defines. Mirrored here rather
#: than imported: importing ``server`` builds the route table, which the
#: read-only foundation must never do. A test asserts the two sets agree.
#:
#: Anything outside this set — including an empty or whitespace-only value, which
#: the column permits — is not a disposition. Treating it as one would let a
#: blank field close a material match.
GOVERNED_DISPOSITION_CODES = frozenset({
    "false_positive_cleared", "false_positive", "identity_mismatch",
    "provider_no_relevant_match", "duplicate_or_irrelevant",
    "low_risk_context_accepted",
    "confirmed_match", "true_match", "material_concern", "escalated_to_edd",
    "potential_sanctions_match", "potential_pep_match", "adverse_media_match",
    "director_ubo_sensitive_hit", "high_risk_jurisdiction", "provider_unresolved",
    "needs_more_information", "client_clarification_required",
    "missing_identity_data", "provider_pending_or_unavailable",
    "documentation_required",
})

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
    state = str(subject.get("screening_state") or "").strip().lower()
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
                    f"{UNDEFENSIBLE_MODES[mode]}, not a live provider, so its "
                    f"screening state is '{state or 'unrecorded'}' rather than "
                    "a terminal result."
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

    # Everything that is not an explicitly recognised defensible answer.
    # ``provider_mode_from_record`` returns six known values today and falls
    # back to ``pending``, so an unrecognised mode is unreachable through the
    # authoritative helper — but the default must still be closed. A mode added
    # upstream tomorrow would otherwise fall through here as defensible and
    # carry the subject all the way to ``clear``.
    if mode != LIVE_PROVIDER_MODE:
        if mode == "not_configured":
            availability = AvailabilityStatus.CREDENTIALS_ABSENT
        else:
            availability = AvailabilityStatus.DATA_ABSENT
        recognised = mode in UNAVAILABLE_MODES or not mode
        detail = (
            f"is '{mode or 'unrecorded'}'"
            if recognised
            else (
                f"is '{mode}', which is not a provider mode this platform is "
                "known to record"
            )
        )
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="screening",
                severity=MEDIUM,
                status=FindingStatus.UNAVAILABLE,
                availability_status=availability,
                confidence=1.0,
                claim=(
                    f"Screening provider state for subject '{key}' {detail} and "
                    f"its screening state is '{state or 'unrecorded'}', so "
                    "whether this subject was screened against a live provider "
                    "cannot be established."
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

    # Reached only for ``live_provider``: the one mode that constitutes a
    # defensible answer, and the only one that lets the subject proceed to the
    # freshness and disposition checks.
    return []


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
    raw_code = review.get("disposition_code")
    disposition_code = str(raw_code or "").strip().lower()

    # A review row is not a disposition. ``screening_reviews.disposition_code``
    # is nullable and unconstrained, so a row can exist carrying nothing, blank
    # space, or a value no workflow produces — and the presence of that row
    # would otherwise close the material match.
    if disposition_code not in GOVERNED_DISPOSITION_CODES:
        blank = not str(raw_code or "").strip()
        detail = (
            "records no disposition code"
            if blank
            else (
                f"records disposition code {str(raw_code)!r}, which is not one "
                "of the governed screening dispositions"
            )
        )
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
                    f"Screening subject '{key}' carries a material match and a "
                    f"screening review exists against it, but that review "
                    f"{detail}. The match is not dispositioned."
                    + (
                        " The application was approved on this review."
                        if is_approved(bundle)
                        else ""
                    )
                ),
                evidence_refs=refs,
                primary_evidence_ref=anchor,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "An open review row reads as an assessment on every surface "
                    "that counts rows. Without a governed disposition there is "
                    "no recorded conclusion — nothing states whether the match "
                    "was cleared, escalated or is still being worked."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    f"What conclusion was reached on the match for subject "
                    f"'{key}'?"
                ),
                required_action=(
                    "Record a governed disposition on this screening review "
                    "through the existing workflow."
                ),
                close_condition=(
                    "The screening review records a disposition code drawn from "
                    "the governed screening disposition vocabulary."
                ),
            )
        ]

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
        # Two materially different situations, and conflating them would waste
        # the finding. No report at all is a missing step. A report that exists
        # and carries no subject records is worse: the screening surface reads
        # as complete while nothing about any individual was actually recorded.
        if screening.get("report_present"):
            claim = (
                "A screening report is stored for this application but it "
                "carries no per-subject records: no company, director or UBO "
                "screening evidence is present in it. Provider defensibility, "
                "completion and match disposition cannot be examined for any "
                "subject."
            )
        else:
            claim = (
                "No screening report exists for this application, so provider "
                "defensibility, completion and match disposition cannot be "
                "examined."
            )
        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="screening",
                severity=severity_by_reliance(bundle, approved=HIGH, pending=MEDIUM),
                status=FindingStatus.UNAVAILABLE,
                availability_status=AvailabilityStatus.DATA_ABSENT,
                confidence=1.0,
                claim=claim,
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

    # ── Freshness needs a readable review instant, or it cannot run ──
    # Skipping the check silently and then reporting subjects "within their
    # validity period" would assert something never established — the exact
    # failure this probe set exists to prevent. One application-level finding is
    # emitted instead, which also keeps the `clear` fallback below unreachable.
    raw_as_of = (bundle.get("meta") or {}).get("as_of")
    as_of_dt = _parse_timestamp(raw_as_of)
    if as_of_dt is None:
        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="screening",
                severity=MEDIUM,
                status=FindingStatus.UNAVAILABLE,
                availability_status=AvailabilityStatus.DATA_ABSENT,
                confidence=1.0,
                claim=(
                    f"The review instant {raw_as_of!r} is not a readable "
                    "timestamp, so screening currency cannot be evaluated for "
                    "any subject on this application."
                ),
                evidence_refs=unique_refs([f"{subject}#as_of"]),
                primary_evidence_ref=f"{subject}#as_of",
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "Freshness is the one check that compares stored evidence "
                    "against a point in time. Without a readable instant it did "
                    "not run, and a screening result that has not been checked "
                    "for currency must never be reported as current."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "What review instant should this application be assessed "
                    "against?"
                ),
                required_action=(
                    "Re-run the review with a valid ISO-8601 as_of instant."
                ),
                close_condition=(
                    "The review is assembled with a readable as_of and the "
                    "freshness check evaluates."
                ),
            )
        )

    reviews_by_id = {
        review.get("id"): review
        for review in (screening.get("reviews") or [])
        if isinstance(review, Mapping)
    }

    for row in subjects:
        provider = _provider_findings(bundle, row, application)
        drafts.extend(provider)

        # Disposition runs regardless of provider quality: an undispositioned
        # material match is a failure of officer process, not of the provider,
        # and it stays open after a re-screen.
        drafts.extend(_disposition_findings(bundle, row, reviews_by_id, application))

        # Freshness does not. "Is this result still current?" presupposes a
        # result worth being current; when the provider answer is undefensible
        # or absent, the required action is already "re-run screening" and a
        # second finding restates it. Suppressed only when the provider check
        # itself reported — never silently.
        if not provider and as_of_dt is not None:
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
