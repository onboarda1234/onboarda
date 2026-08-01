"""P-06 — Monitoring requirement not established.

**What it asks.** An approved customer whose governed risk level requires
periodic review — does a valid schedule actually exist, within the permitted
frequency?

**Why it is not a restatement.** The monitoring surface lists the reviews that
exist. It cannot show the review that should exist and does not. The coupling
between *final risk level at approval* and *whether a schedule was created* is
not displayed anywhere.

**Clock discipline — the sharp edge.**
``periodic_review_policy.parse_review_date()`` is **prohibited** for Supervisor
use (framework §10.4.3): on malformed or missing input it silently returns
today. A corrupt ``next_review_date`` would therefore read as *scheduled for
today*, i.e. compliant — the probe would pass on precisely the record most
likely to be defective. This module parses dates itself and reports an
unreadable date as a finding, never as a pass.

**Stated limitation.** This probe tests structured risk-based periodic-review
scheduling. It does not detect additional monitoring commitments contained only
in generated memo prose; where the memo promises more than policy requires, that
shortfall is invisible here.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from ..contracts import AvailabilityStatus, FindingStatus
from ._draft import (
    HIGH,
    INFO,
    MEDIUM,
    approval_refs,
    finding_draft,
    is_approved,
    subject_ref,
    unique_refs,
)

PROBE_ID = "P-06"
PROBE_VERSION = "p06-v1"

SOURCE_MODULES = (
    "periodic_review_policy.RISK_FREQUENCY_MONTHS",
    "periodic_review_policy.frequency_months_for_risk",
    "supervisor_foundation.bundle",
)

PROSE_LIMITATION = (
    "This probe tests structured risk-based periodic-review scheduling. It does "
    "not detect additional monitoring commitments contained only in generated "
    "memo prose."
)

#: Risk levels where a missing schedule is a high-severity control gap rather
#: than a medium one. Derived from the governed frequency table: the shorter the
#: required cycle, the more consequential its absence.
HIGH_SEVERITY_LEVELS = frozenset({"HIGH", "VERY_HIGH"})


def parse_iso_date(value: Any) -> date | None:
    """Deterministic date parse. ``None`` when unreadable — never today.

    Deliberately *not* ``periodic_review_policy.parse_review_date``, which falls
    back to the current date and would convert a corrupt record into a passing
    check.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def add_months(anchor: date, months: int) -> date:
    """Calendar-safe month addition, clamped to the end of a short month."""
    index = anchor.month - 1 + int(months)
    year = anchor.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def _governing_review(reviews: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The review that governs the next cycle.

    The earliest readable ``next_review_date`` wins — that is the commitment the
    institution is held to. Rows whose date cannot be read sort last so they are
    reported rather than silently preferred.
    """
    readable = [
        (parse_iso_date(row.get("next_review_date")), row)
        for row in reviews
        if isinstance(row, Mapping)
    ]
    dated = sorted(
        (item for item in readable if item[0] is not None),
        key=lambda item: (item[0], str(item[1].get("id") or "")),
    )
    if dated:
        return dated[0][1]
    return readable[0][1] if readable else None


def probe(bundle: Mapping[str, Any], policy: Any) -> Sequence[Mapping[str, Any]]:
    """Run P-06 over a bundle. Pure; no clock, no database, no provider."""
    subject = subject_ref(bundle)
    monitoring = bundle.get("monitoring") or {}
    reviews = [row for row in (monitoring.get("periodic_reviews") or []) if isinstance(row, Mapping)]

    # ── Check 5 — a case that was never approved carries no obligation ──
    if not is_approved(bundle):
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="monitoring",
                severity=INFO,
                status=FindingStatus.NOT_APPLICABLE,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    "No periodic-review obligation applies: this application has "
                    "not been approved, so no ongoing monitoring cycle has begun."
                ),
                evidence_refs=unique_refs([f"{subject}#status", *approval_refs(bundle)]),
                primary_evidence_ref=f"{subject}#status",
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "Recorded so a pending case is visibly out of scope rather "
                    "than silently absent from the monitoring check."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question="None — the obligation begins at approval.",
                required_action="None.",
                close_condition="Not applicable until the application is approved.",
            )
        ]

    risk_level = str((bundle.get("risk") or {}).get("final_risk_level") or "").strip().upper()
    if not risk_level:
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="monitoring",
                severity=MEDIUM,
                status=FindingStatus.UNAVAILABLE,
                availability_status=AvailabilityStatus.DATA_ABSENT,
                confidence=1.0,
                claim=(
                    "The required review frequency cannot be determined: this "
                    "approved application records no final risk level."
                ),
                evidence_refs=unique_refs([f"{subject}#final_risk_level"]),
                primary_evidence_ref=f"{subject}#final_risk_level",
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "Review frequency is derived from the risk level. Without "
                    "one the obligation is unknowable, which is not the same as "
                    "satisfied. " + PROSE_LIMITATION
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "What final risk level was recorded for this approved "
                    "customer?"
                ),
                required_action=(
                    "Record the final risk level through the existing "
                    "authoritative path."
                ),
                close_condition=(
                    "applications.final_risk_level records a governed risk level."
                ),
            )
        ]

    from periodic_review_policy import (  # local: pure constants and lookup
        RISK_FREQUENCY_MONTHS,
        frequency_months_for_risk,
    )

    # ``normalize_risk_level`` silently maps an unrecognised level to MEDIUM, so
    # ``frequency_months_for_risk`` would answer 24 months for a level the policy
    # does not govern at all. Attributing a governed frequency to an ungoverned
    # level is exactly the false-attribution this probe exists to catch, so the
    # lookup is refused rather than accepted.
    if risk_level not in RISK_FREQUENCY_MONTHS:
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="monitoring",
                severity=MEDIUM,
                status=FindingStatus.UNAVAILABLE,
                availability_status=AvailabilityStatus.DATA_ABSENT,
                confidence=1.0,
                claim=(
                    f"This approved application records final risk level "
                    f"{risk_level!r}, which is not one of the governed levels "
                    f"{sorted(RISK_FREQUENCY_MONTHS)}. No review frequency can be "
                    "attributed to it."
                ),
                evidence_refs=unique_refs(
                    [f"{subject}#final_risk_level", *approval_refs(bundle)]
                ),
                primary_evidence_ref=f"{subject}#final_risk_level",
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "The platform's own frequency helper would resolve this to "
                    "MEDIUM and produce a 24-month cycle that looks governed and "
                    "is not. " + PROSE_LIMITATION
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "Which governed risk level applies to this customer?"
                ),
                required_action=(
                    "Correct the final risk level through the existing "
                    "authoritative path."
                ),
                close_condition=(
                    "applications.final_risk_level holds one of the governed "
                    "levels in RISK_FREQUENCY_MONTHS."
                ),
            )
        ]

    frequency = frequency_months_for_risk(risk_level)
    severity_missing = HIGH if risk_level in HIGH_SEVERITY_LEVELS else MEDIUM
    policy_ref = f"periodic_review_policy:RISK_FREQUENCY_MONTHS#{risk_level}"

    # ── Check 1 — approved, obligation exists, no schedule at all ──
    if not reviews:
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="monitoring",
                severity=severity_missing,
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    f"This approved {risk_level} customer requires a periodic "
                    f"review every {frequency} months under governed policy, but "
                    "no periodic review record exists for the application."
                ),
                evidence_refs=unique_refs(
                    [
                        f"{subject}#final_risk_level",
                        f"{subject}#periodic_reviews",
                        policy_ref,
                        *approval_refs(bundle),
                    ]
                ),
                primary_evidence_ref=f"{subject}#periodic_reviews",
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "The monitoring obligation began at approval and no cycle "
                    "was established. The monitoring surface lists the reviews "
                    "that exist; it cannot show the one that should. "
                    + PROSE_LIMITATION
                ),
                regulatory_or_policy_basis=(
                    f"periodic_review_policy RISK_FREQUENCY_MONTHS[{risk_level}]"
                    f" = {frequency} months"
                ),
                officer_question=(
                    "When is this customer next due for periodic review?"
                ),
                required_action=(
                    "Schedule the periodic review through the existing "
                    "authoritative workflow."
                ),
                close_condition=(
                    f"A periodic review exists with a next review date within "
                    f"{frequency} months of the review anchor."
                ),
            )
        ]

    governing = _governing_review(reviews)
    review_id = (governing or {}).get("id")
    review_ref = f"periodic_review:{review_id}"
    raw_next = (governing or {}).get("next_review_date")
    next_date = parse_iso_date(raw_next)

    # Field-level references. Two checks below can fire on the same review in
    # the same run — a bad date *and* a missing policy edition — so each names
    # the field it examined as its identity anchor. Sharing one would merge two
    # separate defects into a single finding.
    date_ref = f"{review_ref}#next_review_date"
    version_ref = f"{review_ref}#policy_version"

    base_refs = unique_refs(
        [
            f"{subject}#final_risk_level",
            review_ref,
            policy_ref,
            f"{subject}#as_of",
            *approval_refs(bundle),
        ]
    )

    drafts: list[dict[str, Any]] = []

    # ── Check 4 — malformed date. Never treated as compliant. ─────
    if next_date is None:
        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="monitoring",
                severity=HIGH,
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    f"The periodic review for this approved {risk_level} customer "
                    f"records next review date {raw_next!r}, which is not a "
                    "readable date. No enforceable review commitment exists."
                ),
                evidence_refs=unique_refs([*base_refs, date_ref]),
                primary_evidence_ref=date_ref,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "An unreadable date is not a schedule. It would also be "
                    "read as today by the platform's own date helper, which is "
                    "why this probe parses dates itself rather than inheriting a "
                    "fallback that turns a corrupt record into a compliant one."
                ),
                regulatory_or_policy_basis=(
                    f"periodic_review_policy RISK_FREQUENCY_MONTHS[{risk_level}]"
                    f" = {frequency} months"
                ),
                officer_question=(
                    "What is the actual next review date for this customer?"
                ),
                required_action=(
                    "Correct the next review date through the existing "
                    "authoritative workflow."
                ),
                close_condition=(
                    "The periodic review records a readable next review date "
                    "within the governed frequency."
                ),
            )
        )
    else:
        # ── Check 2 — scheduled later than policy permits ─────────
        # Anchor precedence: the last completed review, else the approval date,
        # else the review instant. Each is a defensible start for the cycle;
        # the precedence is fixed so the result is deterministic.
        anchor = (
            parse_iso_date((governing or {}).get("last_review_date"))
            or parse_iso_date((bundle.get("decision") or {}).get("decided_at"))
            or parse_iso_date((bundle.get("meta") or {}).get("as_of"))
        )
        if anchor is not None:
            latest_permitted = add_months(anchor, frequency)
            if next_date > latest_permitted:
                drafts.append(
                    finding_draft(
                        probe_id=PROBE_ID,
                        probe_version=PROBE_VERSION,
                        category="monitoring",
                        severity=MEDIUM,
                        status=FindingStatus.HIT,
                        availability_status=AvailabilityStatus.AVAILABLE,
                        confidence=1.0,
                        claim=(
                            f"The next periodic review for this approved "
                            f"{risk_level} customer is scheduled for "
                            f"{next_date.isoformat()}, later than the "
                            f"{latest_permitted.isoformat()} maximum implied by "
                            f"the governed {frequency}-month frequency from "
                            f"{anchor.isoformat()}."
                        ),
                        evidence_refs=unique_refs([*base_refs, date_ref]),
                        primary_evidence_ref=date_ref,
                        source_modules=list(SOURCE_MODULES),
                        why_it_matters=(
                            "The cycle is longer than the risk level permits, so "
                            "the customer goes unreviewed for a period the "
                            "institution's own policy does not allow. "
                            + PROSE_LIMITATION
                        ),
                        regulatory_or_policy_basis=(
                            f"periodic_review_policy "
                            f"RISK_FREQUENCY_MONTHS[{risk_level}] = {frequency} "
                            "months"
                        ),
                        officer_question=(
                            "Why is the review cycle longer than the governed "
                            "frequency for this risk level?"
                        ),
                        required_action=(
                            "Bring the next review date within the governed "
                            "frequency through the existing workflow."
                        ),
                        close_condition=(
                            f"The next review date is on or before "
                            f"{latest_permitted.isoformat()}."
                        ),
                    )
                )

    # ── Check 3 — no governed policy version on the schedule ──────
    if "policy_version" not in (governing or {}):
        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="monitoring",
                severity=MEDIUM,
                status=FindingStatus.UNAVAILABLE,
                availability_status=AvailabilityStatus.DATA_ABSENT,
                confidence=1.0,
                claim=(
                    "The periodic review for this application carries no "
                    "policy_version field, so the policy edition its frequency "
                    "was set under cannot be established."
                ),
                evidence_refs=unique_refs([*base_refs, version_ref]),
                primary_evidence_ref=version_ref,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "Without a policy edition the schedule cannot be reproduced "
                    "or defended if the frequency table later changes."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "Under which periodic-review policy edition was this "
                    "schedule created?"
                ),
                required_action=(
                    "Re-create or update the schedule so a policy version is "
                    "recorded."
                ),
                close_condition=(
                    "The periodic review records a policy_version."
                ),
            )
        )
    elif not str((governing or {}).get("policy_version") or "").strip():
        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="monitoring",
                severity=MEDIUM,
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    "The periodic review for this approved customer records no "
                    "policy version, so the governed frequency edition behind "
                    "its schedule is not attributable."
                ),
                evidence_refs=unique_refs([*base_refs, version_ref]),
                primary_evidence_ref=version_ref,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "A schedule without a policy edition cannot be shown to "
                    "follow any particular governed frequency, which is what a "
                    "reviewer asks when the table changes."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "Under which periodic-review policy edition was this "
                    "schedule created?"
                ),
                required_action=(
                    "Record the policy version on the periodic review through "
                    "the existing authoritative workflow."
                ),
                close_condition=(
                    "periodic_reviews.policy_version records a governed edition."
                ),
            )
        )

    if drafts:
        return drafts

    return [
        finding_draft(
            probe_id=PROBE_ID,
            probe_version=PROBE_VERSION,
            category="monitoring",
            severity=INFO,
            status=FindingStatus.CLEAR,
            availability_status=AvailabilityStatus.AVAILABLE,
            confidence=1.0,
            claim=(
                f"This approved {risk_level} customer has a periodic review "
                f"scheduled for {next_date.isoformat()}, within the governed "
                f"{frequency}-month frequency, under a recorded policy version."
            ),
            evidence_refs=base_refs,
            primary_evidence_ref=review_ref,
            source_modules=list(SOURCE_MODULES),
            why_it_matters=(
                "Recorded so the completeness count distinguishes a monitoring "
                "check that ran and passed from one that never ran. "
                + PROSE_LIMITATION
            ),
            regulatory_or_policy_basis=(
                f"periodic_review_policy RISK_FREQUENCY_MONTHS[{risk_level}] = "
                f"{frequency} months"
            ),
            officer_question="None — this check passed.",
            required_action="None.",
            close_condition="Already satisfied.",
        )
    ]
