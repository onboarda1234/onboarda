"""P-02 — Risk factor resolution integrity.

**What it asks.** Not "is the risk score right?" — that is the rule engine's
answer and this probe has no standing to contradict it. It asks whether the
score was computed from inputs that actually resolved, under a valid
configuration, with any elevation explained.

**Why it is not a restatement.** The risk panel shows the score and the level.
It does not show that the jurisdiction factor never resolved to a controlled
value, so the score was formed around a silent default. ``resolution_status`` is
written into ``factor_computation_evidence`` on every scoring run and surfaced
on no screen. A LOW rating produced by an unresolved country alias is
indistinguishable, on screen, from a LOW rating produced on merit.

**What it must never say.** That the customer should carry a different rating.
The finding is about the *provenance* of the assessment, not its conclusion.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..contracts import AvailabilityStatus, FindingStatus
from ._draft import (
    HIGH,
    INFO,
    MEDIUM,
    finding_draft,
    subject_ref,
    unique_refs,
)

PROBE_ID = "P-02"
PROBE_VERSION = "p02-v1"

SOURCE_MODULES = (
    "rule_engine.compute_risk_score",
    "rule_engine._is_valid_risk_config_version",
    "risk_controlled_values",
    "supervisor_foundation.bundle",
)

#: The authoritative resolution vocabulary, read from the two modules that
#: write it. ``risk_controlled_values.ControlledResolution`` emits ``mapped`` or
#: ``unresolved``; ``rule_engine`` additionally emits ``resolved`` for factors
#: resolved outside the controlled registry and ``unmatched`` for a service
#: label scored by a fallback rule.
#:
#: Both spellings of success are accepted. Validating against ``resolved`` alone
#: — as an earlier draft of this probe did — reported every controlled-registry
#: factor in the corpus as defective: 253 findings across 41 cases, all wrong.
RESOLVED_STATUSES = frozenset({"mapped", "resolved"})

#: Failure states, and what each one means to an officer. ``unmatched`` is the
#: subtle one: the factor *was* scored, by a fallback rule, without ever mapping
#: to a controlled entry. ``rule_engine`` promotes it to ``unresolved`` only
#: under strict mode, so outside strict mode it is exactly the silent default
#: this probe exists to surface.
UNRESOLVED_STATUSES = {
    "unresolved": (
        "records resolution status 'unresolved', so its value did not map to "
        "any entry in the controlled-value registry"
    ),
    "unmatched": (
        "records resolution status 'unmatched': it was scored by a fallback "
        "rule rather than by mapping to a controlled-value entry"
    ),
}

#: `rule_engine` maps these factor keys onto jurisdiction inputs; used only to
#: pick a finding category, never to re-derive a score.
JURISDICTION_FACTORS = frozenset(
    {
        "country_of_incorporation",
        "countries_of_operation",
        "intermediary_jurisdictions",
        "target_markets",
        "ubo_nationalities",
    }
)
PRODUCT_FACTORS = frozenset({"service_type"})

#: At or above this rule score a factor is materially shaping the assessment,
#: so an unresolved input there is a higher-severity provenance defect. Below
#: it, the defect is real but its influence on the outcome is smaller.
MATERIAL_RULE_SCORE = 3


def _category_for(factor_key: str) -> str:
    if factor_key in JURISDICTION_FACTORS:
        return "jurisdiction"
    if factor_key in PRODUCT_FACTORS:
        return "products"
    return "risk"


def _config_version_findings(risk: Mapping[str, Any], subject: str) -> list[dict[str, Any]]:
    """Check 2 — configuration integrity, using the governed validator only."""
    from rule_engine import (  # local: authoritative, pure string checks
        RISK_CONFIG_VERSION_CM_RECOMPUTE_PENDING,
        RISK_CONFIG_VERSION_RECOMPUTE_FAILED,
        _is_valid_risk_config_version,
    )

    version = risk.get("risk_config_version")
    refs = unique_refs([f"{subject}#risk_config_version", "risk_config:" + str(version)])

    sentinels = {
        RISK_CONFIG_VERSION_RECOMPUTE_FAILED: (
            "a risk recomputation failed and was never completed"
        ),
        RISK_CONFIG_VERSION_CM_RECOMPUTE_PENDING: (
            "a change-management recomputation is still pending"
        ),
    }

    if version in sentinels:
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="policy",
                severity=HIGH,
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    f"The recorded risk configuration version is the staleness "
                    f"sentinel '{version}', meaning {sentinels[version]}. The "
                    "stored risk assessment was not produced under a valid "
                    "configuration edition."
                ),
                evidence_refs=refs,
                primary_evidence_ref=f"{subject}#risk_config_version",
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "The sentinel records that the engine could not finish "
                    "re-scoring this case. Any rating read from it predates the "
                    "configuration change that triggered the recompute, so its "
                    "provenance cannot be evidenced to a reviewer."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "Which configuration edition was this assessment actually "
                    "produced under, and why did the recomputation not complete?"
                ),
                required_action=(
                    "Re-run the risk recomputation for this application through "
                    "the existing authoritative path and confirm a valid "
                    "risk_config:<timestamp> version is stamped."
                ),
                close_condition=(
                    "applications.risk_config_version holds a valid timestamped "
                    "risk_config:<timestamp> value and no staleness sentinel."
                ),
            )
        ]

    if version is None or not str(version).strip():
        claim = (
            "No risk configuration version is recorded against this "
            "application, so the configuration edition the stored assessment "
            "was produced under cannot be established."
        )
    elif not _is_valid_risk_config_version(version):
        claim = (
            f"The recorded risk configuration version {version!r} is not a "
            "valid timestamped edition, so the configuration the stored "
            "assessment was produced under cannot be established."
        )
    else:
        return []

    return [
        finding_draft(
            probe_id=PROBE_ID,
            probe_version=PROBE_VERSION,
            category="policy",
            severity=HIGH,
            status=FindingStatus.HIT,
            availability_status=AvailabilityStatus.AVAILABLE,
            confidence=1.0,
            claim=claim,
            evidence_refs=refs,
            primary_evidence_ref=f"{subject}#risk_config_version",
            source_modules=list(SOURCE_MODULES),
            why_it_matters=(
                "Without a valid configuration version the assessment cannot be "
                "reproduced or defended: a reviewer cannot establish which "
                "thresholds and country tiers were in force when it was made."
            ),
            regulatory_or_policy_basis="internal_policy_only",
            officer_question=(
                "Under which risk configuration edition was this assessment "
                "produced?"
            ),
            required_action=(
                "Re-score the application through the existing authoritative "
                "path so a valid configuration version is recorded."
            ),
            close_condition=(
                "applications.risk_config_version holds a valid timestamped "
                "risk_config:<timestamp> value."
            ),
        )
    ]


def _unresolved_factor_findings(
    risk: Mapping[str, Any], subject: str
) -> list[dict[str, Any]]:
    """Check 1 — one finding per factor that did not resolve."""
    drafts: list[dict[str, Any]] = []
    for factor in risk.get("factors") or []:
        if not isinstance(factor, Mapping):
            continue
        factor_key = str(factor.get("factor_key") or "")
        if not factor_key:
            continue

        has_status = "resolution_status" in factor
        status_value = factor.get("resolution_status")
        normalised = str(status_value or "").strip().lower()
        if has_status and normalised in RESOLVED_STATUSES:
            continue

        rule_score = factor.get("rule_score")
        try:
            material = int(rule_score) >= MATERIAL_RULE_SCORE
        except (TypeError, ValueError):
            material = False

        dimension = factor.get("dimension_id")
        if not has_status:
            detail = (
                "carries no resolution status in the stored factor evidence, so "
                "whether it resolved to a controlled value cannot be established"
            )
        elif normalised in UNRESOLVED_STATUSES:
            detail = UNRESOLVED_STATUSES[normalised]
        else:
            # An unrecognised token. Reported as unrecognised rather than
            # assumed either way: guessing "probably fine" is how a new failure
            # spelling would go unnoticed, and guessing "broken" is how a new
            # success spelling would flood the officer with noise.
            detail = (
                f"records resolution status {str(status_value)!r}, which is not "
                "a status this platform's risk engine is known to write, so "
                "whether it resolved to a controlled value cannot be established"
            )

        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category=_category_for(factor_key),
                severity=HIGH if material else MEDIUM,
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    f"The authoritative {factor_key} factor (dimension "
                    f"{dimension}) {detail}. The stored risk assessment was "
                    f"recorded with this factor scored {rule_score}."
                ),
                evidence_refs=unique_refs(
                    [
                        f"risk:{subject}#factor:{factor_key}",
                        f"risk:{subject}#dimension:{dimension}",
                        f"{subject}#risk_config_version",
                    ]
                ),
                # One finding per unresolved factor: the factor reference is
                # what keeps them separate identities.
                primary_evidence_ref=f"risk:{subject}#factor:{factor_key}",
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "An unresolved input does not stop the engine scoring — it "
                    "scores around it. The resulting rating is indistinguishable "
                    "on screen from one derived from fully controlled values, so "
                    "nobody is prompted to check the input that was never mapped."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    f"What value was intended for {factor_key}, and does it map "
                    "to a controlled entry in the current risk registry?"
                ),
                required_action=(
                    f"Correct or map the {factor_key} value so it resolves under "
                    "the controlled-value registry, then re-score through the "
                    "existing authoritative path."
                ),
                close_condition=(
                    f"The {factor_key} factor records resolution status "
                    "'resolved' under a valid risk configuration version."
                ),
            )
        )
    return drafts


def _elevation_reason_findings(
    risk: Mapping[str, Any], subject: str
) -> list[dict[str, Any]]:
    """Check 3 — an elevation or floor was applied with no reason recorded."""
    escalations = [str(item) for item in (risk.get("escalations") or []) if str(item)]
    base_level = str(risk.get("base_risk_level") or "").strip()
    final_level = str(risk.get("final_risk_level") or "").strip()
    level_moved = bool(base_level and final_level and base_level != final_level)

    if not escalations and not level_moved:
        return []
    if str(risk.get("elevation_reason_text") or "").strip():
        return []

    if escalations:
        trigger = f"escalations {sorted(escalations)} are recorded"
    else:
        trigger = f"the level moved from {base_level} to {final_level}"

    return [
        finding_draft(
            probe_id=PROBE_ID,
            probe_version=PROBE_VERSION,
            category="risk",
            severity=MEDIUM,
            status=FindingStatus.HIT,
            availability_status=AvailabilityStatus.AVAILABLE,
            confidence=1.0,
            claim=(
                f"A risk elevation was applied to this application — {trigger} — "
                "but no elevation reason text is recorded against it."
            ),
            evidence_refs=unique_refs(
                [
                    f"{subject}#risk_escalations",
                    f"{subject}#elevation_reason_text",
                    f"{subject}#base_risk_level",
                    f"{subject}#final_risk_level",
                ]
            ),
            primary_evidence_ref=f"{subject}#elevation_reason_text",
            source_modules=list(SOURCE_MODULES),
            why_it_matters=(
                "An unexplained elevation cannot be attributed to a rule or an "
                "officer. A reviewer asked why this customer was uplifted has "
                "nothing to point to, and the uplift cannot be reproduced if the "
                "case is re-scored."
            ),
            regulatory_or_policy_basis="internal_policy_only",
            officer_question=(
                "Which rule or judgement produced this elevation, and can it be "
                "attributed?"
            ),
            required_action=(
                "Record the elevation reason against the application through the "
                "existing authoritative path."
            ),
            close_condition=(
                "applications.elevation_reason_text records a reason attributable "
                "to a rule or a named actor."
            ),
        )
    ]


def probe(bundle: Mapping[str, Any], policy: Any) -> Sequence[Mapping[str, Any]]:
    """Run P-02 over a bundle. Pure; no clock, no database, no provider."""
    risk = bundle.get("risk") or {}
    subject = subject_ref(bundle)

    if not risk.get("evidence_present"):
        # The stored assessment predates `risk-factor-evidence-v1`, so factor
        # provenance cannot be examined at all. Reported honestly rather than
        # scored as clean — this is exactly the case most likely to hide a
        # defect, and calling it clear would be the worst possible answer.
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="risk",
                severity=MEDIUM,
                status=FindingStatus.NOT_REPLAYABLE,
                availability_status=AvailabilityStatus.SNAPSHOT_INCOMPLETE,
                confidence=1.0,
                claim=(
                    "Risk factor resolution cannot be examined: this application "
                    "carries no stored factor computation evidence, so whether "
                    "its inputs resolved to controlled values is unknown."
                ),
                evidence_refs=unique_refs([f"{subject}#risk_dimensions"]),
                primary_evidence_ref=f"{subject}#risk_dimensions",
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "The absence of factor evidence is not the absence of a "
                    "defect. This check did not run, and its result must not be "
                    "read as a pass."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "Can this application be re-scored so factor-level evidence "
                    "is recorded?"
                ),
                required_action=(
                    "Re-score through the existing authoritative path to produce "
                    "factor computation evidence, then re-run this check."
                ),
                close_condition=(
                    "risk_dimensions carries factor_computation_evidence under "
                    "schema version risk-factor-evidence-v1."
                ),
            )
        ]

    drafts: list[dict[str, Any]] = []
    drafts.extend(_unresolved_factor_findings(risk, subject))
    drafts.extend(_config_version_findings(risk, subject))
    drafts.extend(_elevation_reason_findings(risk, subject))

    if drafts:
        return drafts

    return [
        finding_draft(
            probe_id=PROBE_ID,
            probe_version=PROBE_VERSION,
            category="risk",
            severity=INFO,
            status=FindingStatus.CLEAR,
            availability_status=AvailabilityStatus.AVAILABLE,
            confidence=1.0,
            claim=(
                f"All {risk.get('factor_count')} scored risk factors resolved to "
                "controlled values under a valid configuration version, and any "
                "elevation carries a recorded reason."
            ),
            evidence_refs=unique_refs(
                [f"{subject}#risk_config_version", f"risk:{subject}#factors"]
            ),
            primary_evidence_ref=f"risk:{subject}#factors",
            source_modules=list(SOURCE_MODULES),
            why_it_matters=(
                "Recorded so the completeness count distinguishes a check that "
                "ran and passed from one that never ran."
            ),
            regulatory_or_policy_basis="internal_policy_only",
            officer_question="None — this check passed.",
            required_action="None.",
            close_condition="Already satisfied.",
        )
    ]
