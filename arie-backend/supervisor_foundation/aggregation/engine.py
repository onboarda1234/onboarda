"""The review aggregation engine.

Findings in, one ``SupervisorReview`` out. Pure, read-only, advisory,
non-authoritative, and free of any model call: every sentence it emits is either
copied verbatim from a finding or rendered from a governed template in
:mod:`.checks` with counts substituted in.

**What this layer is for.** Four probes over a busy case produce a list. A list
is not a review. An officer reading eleven findings — five screening subjects,
two unresolved risk factors, a monitoring gap — has to do the aggregation in
their head before they can act, and the thing they most need to know (which of
these is the same problem, seen five times?) is exactly what a list hides.

**What it is not for.** Aggregation may improve presentation. It must never
remove evidence. Every underlying ``finding_id`` survives into
``grouped_findings``, every evidence reference survives into ``evidence_index``,
and no group may contain findings of differing status — an ``unavailable``
check absorbed into a group of hits would be the one failure that discredits
the whole artifact.

**Everything here is a projection.** Per framework §8.1, sections 3–8 are views
over the single finding list rather than independent content. A finding appears
in ``material_concerns`` *and* ``policy_deviations`` if it qualifies for both;
it is stored once, in ``grouped_findings``, and referenced by ``finding_id``
elsewhere.

Deliberately absent from the output: ``review_history``, which §8.1 defines as
prior reviews of the same subject. It requires persistence, and persistence is
out of scope for this phase. Emitting an always-empty list would read as "this
subject has never been reviewed before", which is a claim this engine cannot
make.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..canonical_json import canonical_bytes
from ..contracts import (
    AGGREGATION_SCHEMA_VERSION,
    BUNDLE_SCHEMA_VERSION,
    REVIEW_SCHEMA_VERSION,
    FindingStatus,
)
from ..hashing import sha256_hex
from .checks import (
    CONTRADICTION_TYPES,
    UNEVALUABLE_CLASS_BY_AVAILABILITY,
    CHECK_UNAVAILABLE,
    EVIDENCE_NOT_REPLAYABLE,
    check_key,
    evidence_type,
    lookup,
    owner_for_category,
)
from .decision_state import approval_established
from .limitations import applicable_limitations

# ── Vocabularies ─────────────────────────────────────────────────────

#: Overall status. Describes *the review*, never the customer. There is no
#: value here that means approved, rejected, compliant, non-compliant, safe or
#: acceptable — the Supervisor is advisory and does not make the decision.
CRITICAL_CONCERNS = "critical_concerns"
MATERIAL_CONCERNS = "material_concerns"
INCOMPLETE_REVIEW = "incomplete_review"
CONCERNS_IDENTIFIED = "concerns_identified"
NO_MATERIAL_FINDINGS = "no_material_findings"

OVERALL_STATUSES = (
    CRITICAL_CONCERNS,
    MATERIAL_CONCERNS,
    INCOMPLETE_REVIEW,
    CONCERNS_IDENTIFIED,
    NO_MATERIAL_FINDINGS,
)

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITY_ORDER)}
#: An unrecognised severity sorts last and is never treated as material.
UNRANKED_SEVERITY = len(SEVERITY_ORDER)

#: Severities that qualify a group as a material concern (framework §8.1:
#: "severity critical|high, ordered").
MATERIAL_SEVERITIES = frozenset({"critical", "high"})

#: How many material concerns the summary list carries.
#:
#: Seven, and the number is a judgement rather than a discovery: the list exists
#: to be read in full before an officer opens the detail, and a summary long
#: enough to need scrolling has stopped summarising. It is a cap on the *summary*
#: only — every group remains in ``grouped_findings``, ``material_concern_count``
#: reports the true total, and ``material_concerns_omitted`` states plainly how
#: many were not listed.
#:
#: The cap never applies to a critical group. If a case somehow carries more
#: than seven critical concerns, all of them are listed and the summary is
#: longer than seven: "the decision is not defensible as it stands" is not a
#: thing to truncate for tidiness.
MATERIAL_CONCERN_LIMIT = 7

#: Categories that make a finding a policy deviation (framework §8.1: F-19,
#: F-20) and a decision inconsistency (F-22, F-23, F-25).
POLICY_DEVIATION_CATEGORIES = frozenset({"edd", "policy"})
DECISION_INCONSISTENCY_CATEGORIES = frozenset({"decision", "override", "governance"})

#: Statuses that mean the check did not produce an answer.
UNEVALUABLE_STATUSES = frozenset(
    {FindingStatus.UNAVAILABLE.value, FindingStatus.NOT_REPLAYABLE.value}
)

#: Statuses that can leave something outstanding. ``clear`` and
#: ``not_applicable`` cannot: those probes write "None." as their required
#: action, which is correct on a finding and noise in an action register.
ACTIONABLE_STATUSES = frozenset(
    {
        FindingStatus.HIT.value,
        FindingStatus.UNAVAILABLE.value,
        FindingStatus.NOT_REPLAYABLE.value,
    }
)

#: Action texts that are not actions. Filtering on status alone was not enough:
#: nothing stops a future probe writing "None." on an ``unavailable`` finding,
#: and one such line teaches an officer that the register contains filler.
#: Compared case-insensitively after stripping whitespace and a trailing period.
NON_ACTIONS = frozenset({"", "none", "n/a", "na", "-", "—", "tbc", "tbd", "not applicable"})


def _is_real_action(text: str) -> bool:
    return text.strip().rstrip(".").strip().lower() not in NON_ACTIONS


class AggregationInputError(ValueError):
    """The review or bundle handed to the engine does not satisfy the contract.

    Raised rather than contained. A malformed input here is a defect in the
    caller, and the deterministic contract the probe runner already enforces
    exists precisely so that this layer can rely on it.
    """


# ── Small helpers ────────────────────────────────────────────────────


def _severity_rank(severity: Any) -> int:
    return SEVERITY_RANK.get(str(severity or ""), UNRANKED_SEVERITY)


def _worst_severity(findings: Sequence[Mapping[str, Any]]) -> str:
    """The most severe severity present. Groups never soften what they contain."""
    ranked = sorted(findings, key=lambda finding: _severity_rank(finding.get("severity")))
    return str(ranked[0].get("severity") or "") if ranked else ""


def _unique_sorted(values: Any) -> list[str]:
    return sorted({str(value) for value in values if str(value)})


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    """A short identifier derived from content, never from a counter or a clock.

    Content-derived so the same action on the same subject keeps the same
    identifier between runs, which is what lets a later phase diff two reviews.
    """
    return f"{prefix}-{sha256_hex(canonical_bytes(payload))[:12]}"


# ── Grouping ─────────────────────────────────────────────────────────


def _group_key(finding: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """What makes two findings the same concern seen twice.

    ``status`` is in the key deliberately. A ``hit`` and an ``unavailable`` on
    the same check are not the same concern: one says the control failed, the
    other says nobody knows. Grouping them would let a check that could not run
    disappear behind one that did.

    ``availability_status`` is in the key for the same reason one level down.
    Two checks that could not run because credentials are absent and because no
    historic snapshot exists are two different operational problems with two
    different owners; merging them would report one reason and drop the other.

    ``category`` is deliberately **not** in the key, and that was a correction.
    One probe check can carry different categories on different anchors: P-02's
    factor check reports ``jurisdiction`` for a country factor and ``products``
    for a service factor. Keying on category split "two risk inputs did not
    resolve" into two single-finding groups and asked the same regulator
    question twice — the exact noise this layer exists to remove, on the exact
    example the specification uses. The categories are not lost: the group
    carries every one of them, and every owner role they route to.
    """
    return (
        str(finding.get("probe_id") or ""),
        str(finding.get("status") or ""),
        str(finding.get("availability_status") or ""),
        check_key(str(finding.get("primary_evidence_ref") or "")),
    )


def _build_groups(findings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Group findings by check, preserving every one of them.

    Ordering is by (severity, probe, category, status, check) with the group
    identifier as the final tie-break, so the same finding set always produces
    the same sequence regardless of the order it arrived in.
    """
    buckets: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    for finding in findings:
        buckets.setdefault(_group_key(finding), []).append(finding)

    groups: list[dict[str, Any]] = []
    for key, members in buckets.items():
        probe_id, status, availability, key_fragment = key
        ordered = sorted(
            members,
            key=lambda finding: (
                _severity_rank(finding.get("severity")),
                str(finding.get("primary_evidence_ref") or ""),
                str(finding.get("finding_id") or ""),
            ),
        )
        check = lookup(probe_id, str(ordered[0].get("primary_evidence_ref") or ""))
        count = len(ordered)
        severity = _worst_severity(ordered)

        if check is None:
            # Unregistered check: the finding stands alone, in its own words.
            # Never merged into anything, because nothing here knows what it is.
            check_id = f"{probe_id}.{key_fragment}" if key_fragment else probe_id
            claim = str(ordered[0].get("claim") or "")
            registered = False
        else:
            check_id = check.check_id
            registered = True
            if count == 1:
                # One finding keeps its own sentence. A template would replace a
                # specific claim with a vaguer one and call it a summary.
                claim = str(ordered[0].get("claim") or "")
            else:
                claim = check.group_claim_template.format(
                    count=count,
                    noun=check.anchor_noun_plural if count != 1 else check.anchor_noun,
                )

        # Every distinct closure condition is exposed. Two findings that close
        # on different evidence may share a group — the officer simply has to be
        # able to see that closing one does not close the other.
        close_conditions = _unique_sorted(
            finding.get("close_condition") for finding in ordered
        )
        categories = _unique_sorted(finding.get("category") for finding in ordered)
        groups.append(
            {
                "affected_count": count,
                "availability_status": availability,
                "categories": categories,
                "check_id": check_id,
                "check_registered": registered,
                "claim": claim,
                "close_conditions": close_conditions,
                "evidence_refs": _unique_sorted(
                    ref for finding in ordered for ref in finding.get("evidence_refs") or []
                ),
                "finding_ids": [str(finding.get("finding_id") or "") for finding in ordered],
                "group_id": _stable_id(
                    "grp",
                    {
                        "availability_status": availability,
                        "check": key_fragment,
                        "finding_ids": sorted(
                            str(finding.get("finding_id") or "") for finding in ordered
                        ),
                        "probe_id": probe_id,
                        "status": status,
                    },
                ),
                "owner_roles": sorted(
                    {owner_for_category(category) for category in categories}
                ),
                "primary_evidence_refs": [
                    str(finding.get("primary_evidence_ref") or "") for finding in ordered
                ],
                "probe_id": probe_id,
                "required_actions": _unique_sorted(
                    finding.get("required_action") for finding in ordered
                ),
                "severity": severity,
                "status": status,
            }
        )

    return sorted(
        groups,
        key=lambda group: (
            _severity_rank(group["severity"]),
            group["probe_id"],
            group["status"],
            group["check_id"],
            group["group_id"],
        ),
    )


# ── Overall status ───────────────────────────────────────────────────


def _overall_status(findings: Sequence[Mapping[str, Any]]) -> str:
    """Five values, one total order, evaluated top to bottom.

    1. any ``hit`` at ``critical``            -> critical_concerns
    2. any ``hit`` at ``high``                -> material_concerns
    3. any ``unavailable`` / ``not_replayable``
                                              -> incomplete_review
    4. any remaining ``hit``                  -> concerns_identified
    5. otherwise                              -> no_material_findings

    Rule 3 above rule 4 is the one judgement call in this ladder, and it is
    deliberate. A medium defect is a known quantity; a control that could not be
    evaluated is not, and ``concerns_identified`` would imply the review was
    complete when it was not. Severity still wins where severity is known —
    a critical or high hit outranks incompleteness, because an established
    material failure is worse news than an unanswered question.
    """
    hits = [
        finding
        for finding in findings
        if str(finding.get("status")) == FindingStatus.HIT.value
    ]
    if any(str(finding.get("severity")) == "critical" for finding in hits):
        return CRITICAL_CONCERNS
    if any(str(finding.get("severity")) == "high" for finding in hits):
        return MATERIAL_CONCERNS
    if any(str(finding.get("status")) in UNEVALUABLE_STATUSES for finding in findings):
        return INCOMPLETE_REVIEW
    if hits:
        return CONCERNS_IDENTIFIED
    return NO_MATERIAL_FINDINGS


# ── Completeness ─────────────────────────────────────────────────────


def _review_completeness(
    findings: Sequence[Mapping[str, Any]],
    *,
    probes_expected: int,
    evidence_reference_count: int,
) -> dict[str, Any]:
    """Counts, not a confidence score.

    A percentage would invite the reading this section exists to prevent — that
    a complete review is a clean one. ``evaluated_fraction`` is published anyway
    because operators ask for it, with its definition stated in the output so it
    cannot be quoted without it.
    """
    by_status = {member.value: 0 for member in FindingStatus}
    by_severity = {name: 0 for name in SEVERITY_ORDER}
    probes_seen: dict[str, set[str]] = {}

    for finding in findings:
        status = str(finding.get("status") or "")
        by_status[status] = by_status.get(status, 0) + 1
        if status == FindingStatus.HIT.value:
            severity = str(finding.get("severity") or "")
            by_severity[severity] = by_severity.get(severity, 0) + 1
        probes_seen.setdefault(str(finding.get("probe_id") or ""), set()).add(status)

    def _probes_with(status: str) -> list[str]:
        return sorted(probe for probe, seen in probes_seen.items() if status in seen)

    unevaluable = [
        probe
        for probe, seen in probes_seen.items()
        if seen & UNEVALUABLE_STATUSES
    ]
    completed = [probe for probe in probes_seen if probe not in set(unevaluable)]

    return {
        "critical_count": by_severity.get("critical", 0),
        "evidence_reference_count": evidence_reference_count,
        # Stated so the number is never quoted without its definition.
        "evaluated_fraction_definition": (
            "probes_completed / probes_expected. Measures how much of the "
            "review ran, not whether the customer is acceptable. A fully "
            "complete review may still carry critical concerns."
        ),
        "finding_count": len(findings),
        "high_count": by_severity.get("high", 0),
        "info_count": by_severity.get("info", 0),
        "low_count": by_severity.get("low", 0),
        "medium_count": by_severity.get("medium", 0),
        "probes_completed": sorted(completed),
        "probes_expected": probes_expected,
        "probes_not_applicable": _probes_with(FindingStatus.NOT_APPLICABLE.value),
        "probes_not_replayable": _probes_with(FindingStatus.NOT_REPLAYABLE.value),
        "probes_reporting": sorted(probes_seen),
        "probes_unavailable": _probes_with(FindingStatus.UNAVAILABLE.value),
        "status_counts": by_status,
        "unevaluable_count": sum(by_status[status] for status in UNEVALUABLE_STATUSES),
    }


# ── Projections over the groups ──────────────────────────────────────


def _concern_view(group: Mapping[str, Any]) -> dict[str, Any]:
    """The shape a group takes when cited by a summary section."""
    return {
        "affected_count": group["affected_count"],
        "categories": list(group["categories"]),
        "check_id": group["check_id"],
        "claim": group["claim"],
        "evidence_refs": list(group["evidence_refs"]),
        "finding_ids": list(group["finding_ids"]),
        "group_id": group["group_id"],
        "owner_roles": list(group["owner_roles"]),
        "probe_id": group["probe_id"],
        "severity": group["severity"],
        "status": group["status"],
    }


def _material_concerns(groups: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """The ordered summary list, and the true total it was drawn from."""
    qualifying = [
        group
        for group in groups
        if str(group["status"]) == FindingStatus.HIT.value
        and str(group["severity"]) in MATERIAL_SEVERITIES
    ]
    critical = [group for group in qualifying if str(group["severity"]) == "critical"]
    remainder = [group for group in qualifying if str(group["severity"]) != "critical"]
    selected = critical + remainder[: max(0, MATERIAL_CONCERN_LIMIT - len(critical))]
    return [_concern_view(group) for group in selected], len(qualifying)


def _critical_findings(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every critical group, uncapped, with its underlying findings exposed."""
    return [
        {
            **_concern_view(group),
            "close_conditions": list(group["close_conditions"]),
            "primary_evidence_refs": list(group["primary_evidence_refs"]),
            "required_actions": list(group["required_actions"]),
        }
        for group in groups
        if str(group["severity"]) == "critical"
    ]


def _contradictions(
    groups: Sequence[Mapping[str, Any]],
    *,
    approved: bool,
) -> list[dict[str, Any]]:
    """Only where two structured facts genuinely conflict.

    Driven from the register, so a contradiction exists because a named check
    fired, not because a finding looked serious.

    ``contradiction_requires_approval`` carries the cases where the conflict
    depends on a decision having been taken: an undefensible screening result is
    a defect on any case and a *contradiction* only where the case was approved
    on it. That gate reads the decision record, not the severity. Severity
    correlates — P-04 raises the finding to critical on an approved case — but
    it is another layer's encoding of reliance, and reading approval back out of
    it would make this layer's grammar depend on that layer's severity policy.
    """
    entries: list[dict[str, Any]] = []
    for group in groups:
        if str(group["status"]) != FindingStatus.HIT.value:
            continue
        check = lookup(group["probe_id"], group["primary_evidence_refs"][0])
        if check is None or check.contradiction_type is None:
            continue
        if check.contradiction_requires_approval and not approved:
            continue
        entries.append(
            {
                "affected_count": group["affected_count"],
                "claim": group["claim"],
                "contradiction_type": check.contradiction_type,
                "conflict": CONTRADICTION_TYPES[check.contradiction_type],
                "evidence_refs": list(group["evidence_refs"]),
                "finding_ids": list(group["finding_ids"]),
                "group_id": group["group_id"],
                "severity": group["severity"],
            }
        )
    return sorted(
        entries,
        key=lambda entry: (
            _severity_rank(entry["severity"]),
            entry["contradiction_type"],
            entry["group_id"],
        ),
    )


def _missing_evidence(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Five classes, kept apart.

    An unevaluable finding is classified by *why* it could not be evaluated, so
    "no registry credentials" and "no historic snapshot" do not arrive as the
    same problem. A hit is classified only where the register says the check is
    about evidence; a hit that is a control failure rather than an evidence gap
    contributes nothing here.
    """
    entries: list[dict[str, Any]] = []
    for group in groups:
        status = str(group["status"])
        check = lookup(group["probe_id"], group["primary_evidence_refs"][0])

        if status == FindingStatus.NOT_REPLAYABLE.value:
            classification = EVIDENCE_NOT_REPLAYABLE
        elif status == FindingStatus.UNAVAILABLE.value:
            classification = UNEVALUABLE_CLASS_BY_AVAILABILITY.get(
                group["availability_status"], CHECK_UNAVAILABLE
            )
        elif status == FindingStatus.HIT.value and check is not None:
            classification = check.missing_evidence_class
        else:
            classification = None

        if classification is None:
            continue

        entries.append(
            {
                "affected_count": group["affected_count"],
                "availability_status": group["availability_status"],
                "classification": classification,
                "close_conditions": list(group["close_conditions"]),
                "evidence_refs": list(group["evidence_refs"]),
                "finding_ids": list(group["finding_ids"]),
                "group_id": group["group_id"],
                "required_actions": list(group["required_actions"]),
                "severity": group["severity"],
                "what_is_missing": group["claim"],
                "why_it_matters": group["why_it_matters"],
            }
        )
    return sorted(
        entries,
        key=lambda entry: (
            entry["classification"],
            _severity_rank(entry["severity"]),
            entry["group_id"],
        ),
    )


def _required_actions(
    findings: Sequence[Mapping[str, Any]],
    groups: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One register, deduplicated on the action text itself.

    Two findings that require literally the same thing are one action; anything
    less exact stays separate. Nothing here assigns work or changes state — the
    owner role is read from the governed category register in framework §5.2
    and is a statement about who the taxonomy already makes responsible.

    Built from **findings**, not groups, and that distinction matters for
    attribution. A group can span categories — an unresolved country factor
    (``jurisdiction``, owned by Ops) and an unresolved service factor
    (``products``, owned by Ops) sit in one "risk inputs did not resolve"
    concern alongside a plain ``risk`` factor owned by the SCO. Taking the owner
    from the group would stamp every action in it with every role the group
    touches, and tell Ops to do the SCO's work. Each action takes its owners
    from the categories of the findings that actually require it.
    """
    group_by_finding = {
        finding_id: group
        for group in groups
        for finding_id in group["finding_ids"]
    }

    merged: dict[str, dict[str, Any]] = {}
    for finding in findings:
        finding_id = str(finding.get("finding_id") or "")
        group = group_by_finding.get(finding_id)
        if group is None or str(group["status"]) not in ACTIONABLE_STATUSES:
            # A clear or not-applicable check has nothing outstanding, and the
            # probes say so literally: their required_action reads "None." An
            # action register containing "None." is worse than useless, because
            # it makes the officer read a line to discover there is no work in
            # it.
            continue
        action_text = str(finding.get("required_action") or "")
        if not _is_real_action(action_text):
            continue
        entry = merged.setdefault(
            action_text,
            {
                "action": action_text,
                "close_conditions": set(),
                "evidence_refs": set(),
                "finding_ids": set(),
                "group_ids": set(),
                "owner_roles": set(),
                "reasons": set(),
                "severity": "info",
            },
        )
        entry["close_conditions"].add(str(finding.get("close_condition") or ""))
        entry["evidence_refs"].update(
            str(ref) for ref in finding.get("evidence_refs") or []
        )
        entry["finding_ids"].add(finding_id)
        entry["group_ids"].add(group["group_id"])
        entry["owner_roles"].add(owner_for_category(str(finding.get("category") or "")))
        entry["reasons"].add(str(finding.get("why_it_matters") or ""))
        if _severity_rank(finding.get("severity")) < _severity_rank(entry["severity"]):
            entry["severity"] = str(finding.get("severity") or "")

    actions = []
    for entry in merged.values():
        finding_ids = sorted(entry["finding_ids"])
        actions.append(
            {
                "action": entry["action"],
                "action_id": _stable_id(
                    "act", {"action": entry["action"], "finding_ids": finding_ids}
                ),
                "close_conditions": sorted(c for c in entry["close_conditions"] if c),
                "evidence_required": sorted(entry["evidence_refs"]),
                "finding_ids": finding_ids,
                "group_ids": sorted(entry["group_ids"]),
                "owner_roles": sorted(entry["owner_roles"]),
                "priority": entry["severity"],
                "reasons": sorted(r for r in entry["reasons"] if r),
            }
        )
    return sorted(
        actions,
        key=lambda action: (
            _severity_rank(action["priority"]),
            action["action"],
            action["action_id"],
        ),
    )


def _regulatory_challenges(
    groups: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    *,
    approved: bool,
) -> list[dict[str, Any]]:
    """Regulator-style questions, from templates, mapped to evidence.

    No model, and no generic questions: every entry is rendered from the
    register entry for a check that actually fired, and carries the findings,
    evidence and actions that answer it. A question with nothing behind it is
    not a challenge, it is a prompt.

    A question that says "approved" is asked only where the bundle establishes
    approval. On a draft, pending, rejected or unreadable case the neutral
    variant is asked instead — "What live, terminal screening result exists…"
    rather than "Why was this customer approved without…". The original code
    asked the approval question unconditionally, which put a false statement
    about a decision into a document an officer may hand to a regulator.
    """
    action_ids_by_finding: dict[str, set[str]] = {}
    for action in actions:
        for finding_id in action["finding_ids"]:
            action_ids_by_finding.setdefault(finding_id, set()).add(action["action_id"])

    entries: list[dict[str, Any]] = []
    for group in groups:
        if str(group["status"]) != FindingStatus.HIT.value:
            continue
        check = lookup(group["probe_id"], group["primary_evidence_refs"][0])
        if check is None or check.regulatory_challenge_template is None:
            continue
        template = check.regulatory_challenge_template
        if not approved and check.regulatory_challenge_template_unapproved is not None:
            template = check.regulatory_challenge_template_unapproved
        count = group["affected_count"]
        entries.append(
            {
                "action_ids": sorted(
                    action_id
                    for finding_id in group["finding_ids"]
                    for action_id in action_ids_by_finding.get(finding_id, ())
                ),
                "challenge_id": _stable_id(
                    "chl", {"check_id": check.check_id, "group_id": group["group_id"]}
                ),
                "check_id": check.check_id,
                "evidence_refs": list(group["evidence_refs"]),
                "finding_ids": list(group["finding_ids"]),
                "group_id": group["group_id"],
                "question": template.format(
                    count=count,
                    noun=check.anchor_noun_plural if count != 1 else check.anchor_noun,
                    noun_singular=check.anchor_noun,
                    noun_plural=check.anchor_noun_plural,
                ),
                "severity": group["severity"],
            }
        )
    return sorted(
        entries,
        key=lambda entry: (
            _severity_rank(entry["severity"]),
            entry["check_id"],
            entry["challenge_id"],
        ),
    )


def _unavailable_checks(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every check that did not produce an answer, with the reason it did not."""
    return [
        {
            "availability_status": group["availability_status"],
            "check_id": group["check_id"],
            "claim": group["claim"],
            "close_conditions": list(group["close_conditions"]),
            "evidence_refs": list(group["evidence_refs"]),
            "finding_ids": list(group["finding_ids"]),
            "group_id": group["group_id"],
            "probe_id": group["probe_id"],
            "status": group["status"],
        }
        for group in groups
        if str(group["status"]) in UNEVALUABLE_STATUSES
    ]


def _evidence_index(
    findings: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One deduplicated record per reference cited anywhere in the review.

    References only. The bundle's references are already pseudonymous — a
    screening subject is addressed by its subject key, never by name — and
    nothing is copied out of a provider payload into this index. It is a map of
    what was looked at, not a second copy of it.
    """
    index: dict[str, dict[str, Any]] = {}
    for finding in findings:
        finding_id = str(finding.get("finding_id") or "")
        availability = str(finding.get("availability_status") or "")
        for ref in finding.get("evidence_refs") or []:
            record = index.setdefault(
                str(ref),
                {
                    "availability_states": set(),
                    "evidence_ref": str(ref),
                    "evidence_type": evidence_type(str(ref)),
                    "finding_ids": set(),
                    "source_modules": set(),
                },
            )
            record["availability_states"].add(availability)
            record["finding_ids"].add(finding_id)
            record["source_modules"].update(
                str(module) for module in finding.get("source_modules") or []
            )

    action_ids_by_ref: dict[str, set[str]] = {}
    for action in actions:
        for ref in action["evidence_required"]:
            action_ids_by_ref.setdefault(ref, set()).add(action["action_id"])

    return [
        {
            "action_ids": sorted(action_ids_by_ref.get(ref, ())),
            "availability_states": sorted(record["availability_states"]),
            "evidence_ref": record["evidence_ref"],
            "evidence_type": record["evidence_type"],
            "finding_ids": sorted(record["finding_ids"]),
            "source_modules": sorted(record["source_modules"]),
        }
        for ref, record in sorted(index.items())
    ]


# ── Entry point ──────────────────────────────────────────────────────


def aggregate_review(
    review: Mapping[str, Any],
    *,
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Aggregate a probe run into one ``SupervisorReview``.

    Args:
        review: Output of :func:`supervisor_foundation.review.run_review`.
            Never mutated.
        bundle: The bundle that review was computed from. Read for its schema
            version and its dependency-availability section only; every
            *finding* in the output is a projection of ``review["findings"]``.

    Returns:
        The ``SupervisorReview``. Deterministic: identical inputs give a
        byte-identical result, on any host, under any hash seed.
    """
    if not isinstance(review, Mapping):
        raise AggregationInputError("review must be a mapping")
    if not isinstance(bundle, Mapping):
        raise AggregationInputError("bundle must be a mapping")

    findings = review.get("findings")
    if not isinstance(findings, (list, tuple)):
        raise AggregationInputError("review must carry findings as a list")
    for index, finding in enumerate(findings):
        if not isinstance(finding, Mapping):
            raise AggregationInputError(f"finding {index} is not a mapping")
        if not str(finding.get("finding_id") or ""):
            raise AggregationInputError(
                f"finding {index} has no finding_id; aggregate the output of "
                "run_review, not raw probe drafts"
            )

    meta = bundle.get("meta") or {}
    bundle_schema_version = str(
        meta.get("bundle_schema_version") or BUNDLE_SCHEMA_VERSION
    )
    as_of = str(review.get("as_of") or meta.get("as_of") or "")
    subject_type = str(review.get("subject_type") or meta.get("subject_type") or "")
    subject_id = str(review.get("subject_id") or meta.get("subject_id") or "")
    review_hash = str(review.get("review_hash") or "")

    groups = _build_groups(findings)
    # ``why_it_matters`` is the one group field copied from a member rather than
    # derived: the control consequence is a property of the check, so every
    # member of a group carries the same sentence. Attached after construction
    # so ``_build_groups`` stays about grouping.
    findings_by_id = {str(finding.get("finding_id") or ""): finding for finding in findings}
    for group in groups:
        members = [findings_by_id[fid] for fid in group["finding_ids"] if fid in findings_by_id]
        group["why_it_matters"] = str(members[0].get("why_it_matters") or "") if members else ""

    approved = approval_established(bundle)
    actions = _required_actions(findings, groups)
    evidence_index = _evidence_index(findings, actions)
    material_concerns, material_concern_count = _material_concerns(groups)

    body: dict[str, Any] = {
        "aggregation_schema_version": AGGREGATION_SCHEMA_VERSION,
        "as_of": as_of,
        "bundle_schema_version": bundle_schema_version,
        "close_conditions": sorted(
            {
                condition
                for group in groups
                if str(group["status"]) != FindingStatus.CLEAR.value
                for condition in group["close_conditions"]
            }
        ),
        "contradictions": _contradictions(groups, approved=approved),
        "created_at": as_of,
        "critical_findings": _critical_findings(groups),
        "decision_inconsistencies": [
            _concern_view(group)
            for group in groups
            if set(group["categories"]) & DECISION_INCONSISTENCY_CATEGORIES
            and str(group["status"]) == FindingStatus.HIT.value
        ],
        "evidence_index": evidence_index,
        "finding_ids": sorted(
            str(finding.get("finding_id") or "") for finding in findings
        ),
        "grouped_findings": groups,
        "input_bundle_hash": str(review.get("input_bundle_hash") or ""),
        "limitations": applicable_limitations(findings, bundle),
        "material_concerns": material_concerns,
        "missing_evidence": _missing_evidence(groups),
        "officer_questions": sorted(
            {
                str(finding.get("officer_question") or "")
                for finding in findings
                if str(finding.get("status")) == FindingStatus.HIT.value
                and str(finding.get("officer_question") or "")
            }
        ),
        "overall_assessment": {
            "findings_by_severity": {
                severity: sum(
                    1
                    for finding in findings
                    if str(finding.get("status")) == FindingStatus.HIT.value
                    and str(finding.get("severity")) == severity
                )
                for severity in SEVERITY_ORDER
            },
            "material_concern_count": material_concern_count,
            "material_concerns_listed": len(material_concerns),
            "material_concerns_omitted": material_concern_count - len(material_concerns),
            "overall_status": _overall_status(findings),
        },
        "policy_deviations": [
            _concern_view(group)
            for group in groups
            if set(group["categories"]) & POLICY_DEVIATION_CATEGORIES
            and str(group["status"]) == FindingStatus.HIT.value
        ],
        "policy_version": str(review.get("policy_version") or ""),
        "potential_regulatory_challenges": _regulatory_challenges(
            groups, actions, approved=approved
        ),
        "probe_set_version": str(review.get("probe_set_version") or ""),
        "required_actions": actions,
        "review_completeness": _review_completeness(
            findings,
            probes_expected=int(
                (review.get("review_completeness") or {}).get("probes_run") or 0
            ),
            evidence_reference_count=len(evidence_index),
        ),
        "review_hash": review_hash,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "subject_id": subject_id,
        "subject_type": subject_type,
        "unavailable_checks": _unavailable_checks(groups),
    }

    # ``review_id`` first, from declared inputs only, so it is stable whatever
    # the aggregation later says. ``aggregation_hash`` then covers the whole
    # body — including ``review_id`` — and is the integrity value for *this*
    # artifact. Neither is derived from a clock, an environment variable or a
    # counter. See the module docstring in ``supervisor_foundation.hashing`` for
    # how these relate to ``input_bundle_hash`` and ``review_hash``.
    body["review_id"] = _stable_id(
        "rev",
        {
            "aggregation_schema_version": AGGREGATION_SCHEMA_VERSION,
            "as_of": as_of,
            "review_hash": review_hash,
            "subject_id": subject_id,
            "subject_type": subject_type,
        },
    )
    body["aggregation_hash"] = sha256_hex(canonical_bytes(body))
    return body
