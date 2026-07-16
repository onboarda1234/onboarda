"""Pilot Canonical Dataset v1 manifest and validation contract.

The manifest is intentionally data-only.  Importing this module performs no
database write, provider call, feature-flag mutation, or application
recomputation.  A future, separately approved staging run must pass both the
static checks here and the live runtime-alignment preflight in the seeder.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


MANIFEST_PATH = Path(__file__).with_name("pilot_canonical_dataset_v1.json")
DATASET_NAME = "Pilot Canonical Dataset"
DATASET_VERSION = "v1"
REFERENCE_PREFIX = "RM-PILOT-"
EXPECTED_SCENARIO_COUNT = 38

REQUIRED_COVERAGE = frozenset({
    "low_risk", "professional_services", "trading_company", "simple_domestic",
    "simple_ownership", "low_volume", "medium_risk", "international_trading",
    "investment_management", "family_office", "cross_border_payments",
    "corporate_shareholders", "multiple_services", "higher_volume", "high_risk",
    "private_banking", "declared_pep", "foreign_pep", "cash_intensive",
    "precious_metals", "high_risk_jurisdiction", "opaque_ownership",
    "sanctions_hit", "adverse_media", "combined_risk_factors", "edd",
    "complex_ownership", "trust_structure", "source_of_wealth_review",
    "manual_compliance_review", "officer_escalation", "negative", "failed_idv",
    "missing_documents", "unknown_sector", "unknown_entity", "unknown_country",
    "screening_pending", "approval_blocked", "rejected_application",
    "periodic_low", "periodic_medium", "periodic_high", "periodic_completed",
    "periodic_open", "monitoring_alert", "monitoring_false_positive",
    "monitoring_escalated", "monitoring_cleared",
})


class PilotDatasetValidationError(RuntimeError):
    """Raised when the immutable manifest or its runtime contract diverges."""


def load_manifest() -> Dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def manifest_sha256() -> str:
    """Return the SHA-256 of the exact reviewed manifest bytes."""
    return hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()


def scenarios(manifest: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    source = manifest or load_manifest()
    return [dict(item) for item in source.get("scenarios", [])]


def _tier_for_score(score: float) -> str:
    if score < 40:
        return "LOW"
    if score < 55:
        return "MEDIUM"
    if score < 70:
        return "HIGH"
    return "VERY_HIGH"


def _duplicates(values: Iterable[Any]) -> List[Any]:
    seen = set()
    duplicates = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def stable_evidence(value: Any) -> Any:
    """Remove environment-specific config timestamps from comparable evidence."""
    if isinstance(value, dict):
        return {
            key: (
                "code-seeded-risk-config"
                if key == "config_version"
                else stable_evidence(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [stable_evidence(item) for item in value]
    return value


def validate_manifest(manifest: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Validate deterministic identity, coverage, and workflow consistency."""
    source = manifest or load_manifest()
    metadata = source.get("dataset") or {}
    rows = list(source.get("scenarios") or [])
    errors: List[str] = []

    expected_tags = {
        "name": DATASET_NAME,
        "version": DATASET_VERSION,
        "synthetic": True,
        "non_production": True,
        "fixture": True,
        "visible_in_back_office": True,
        "reference_prefix": REFERENCE_PREFIX,
        "risk_model_contract": "approved_rsmp_tier0_flag_on",
    }
    for key, expected in expected_tags.items():
        if metadata.get(key) != expected:
            errors.append(f"dataset.{key} must be {expected!r}")
    if metadata.get("scenario_count") != EXPECTED_SCENARIO_COUNT:
        errors.append(f"dataset.scenario_count must be {EXPECTED_SCENARIO_COUNT}")
    if len(rows) != EXPECTED_SCENARIO_COUNT or not 35 <= len(rows) <= 40:
        errors.append(f"manifest must contain exactly {EXPECTED_SCENARIO_COUNT} scenarios")

    references = [row.get("reference") for row in rows]
    app_ids = [row.get("application_id") for row in rows]
    slugs = [row.get("slug") for row in rows]
    for label, values in (("reference", references), ("application_id", app_ids), ("slug", slugs)):
        duplicate_values = _duplicates(values)
        if duplicate_values:
            errors.append(f"duplicate {label} values: {duplicate_values}")

    covered = set()
    for index, row in enumerate(rows, start=1):
        reference = f"{REFERENCE_PREFIX}{index:03d}"
        application_id = f"pcdv1{index:011d}"
        if row.get("number") != index:
            errors.append(f"{reference}: number must be {index}")
        if row.get("reference") != reference:
            errors.append(f"row {index}: reference must be {reference}")
        if row.get("application_id") != application_id:
            errors.append(f"{reference}: application_id must be {application_id}")
        if row.get("risk_inputs", {}).get("application_id") != reference:
            errors.append(f"{reference}: scorer application_id must equal permanent reference")

        expected = row.get("expected") or {}
        workflow = row.get("workflow_state") or {}
        score = expected.get("score")
        tier = expected.get("tier")
        base_score = expected.get("base_score")
        base_tier = expected.get("base_tier")
        if not isinstance(score, (int, float)) or not 0 <= float(score) <= 100:
            errors.append(f"{reference}: expected.score must be within 0..100")
        if not isinstance(base_score, (int, float)) or not 0 <= float(base_score) <= 100:
            errors.append(f"{reference}: expected.base_score must be within 0..100")
        elif base_tier != _tier_for_score(float(base_score)):
            errors.append(f"{reference}: base tier does not match base score")
        if tier not in {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}:
            errors.append(f"{reference}: invalid expected tier {tier!r}")
        elif isinstance(score, (int, float)) and tier != _tier_for_score(float(score)):
            errors.append(f"{reference}: final tier does not match final score")

        category = row.get("category")
        if category == "LOW" and tier != "LOW":
            errors.append(f"{reference}: LOW scenario must finish LOW")
        if category == "MEDIUM" and tier != "MEDIUM":
            errors.append(f"{reference}: MEDIUM scenario must finish MEDIUM")
        if category == "HIGH" and tier not in {"HIGH", "VERY_HIGH"}:
            errors.append(f"{reference}: HIGH scenario must finish HIGH or VERY_HIGH")
        if expected.get("edd_required") and expected.get("lane") != "EDD":
            errors.append(f"{reference}: EDD-required scenario must use EDD workflow lane")
        if (
            expected.get("approval_route") == "blocked"
            and expected.get("memo_status") not in {"blocked", "none"}
            and expected.get("application_status") != "approved"
        ):
            errors.append(f"{reference}: active blocked route must have blocked memo evidence")
        if workflow.get("screening") == "pending" and expected.get("approval_route") != "blocked":
            errors.append(f"{reference}: pending screening must block approval")
        if workflow.get("documents") == "missing" and expected.get("approval_route") != "blocked":
            errors.append(f"{reference}: missing documents must block approval")
        if workflow.get("idv") == "failed" and expected.get("approval_route") != "blocked":
            errors.append(f"{reference}: failed IDV must block approval")
        if expected.get("application_status") == "rejected" and expected.get("approval_route") != "rejected":
            errors.append(f"{reference}: rejected status must use rejected route")
        if expected.get("application_status") == "approved" and expected.get("memo_status") != "approved":
            errors.append(f"{reference}: approved application must retain approved memo evidence")
        if workflow.get("periodic_review") != "none" and expected.get("application_status") != "approved":
            errors.append(f"{reference}: periodic-review example must be an approved client")
        if workflow.get("monitoring") != "none" and expected.get("application_status") != "approved":
            errors.append(f"{reference}: monitoring example must be an approved client")
        if tier in {"HIGH", "VERY_HIGH"} and not expected.get("edd_required"):
            errors.append(f"{reference}: High/Very High example must have consistent EDD state")

        inputs = row.get("risk_inputs") or {}
        if inputs.get("ownership_structure") == "Opaque — UBOs cannot be fully identified":
            if inputs.get("ubos"):
                errors.append(f"{reference}: opaque ownership must not fabricate an identified UBO")
            ownership_reasons = set(expected.get("escalations", []))
            if not ownership_reasons.intersection({
                "floor_rule_opaque_ownership", "elevation_grey_sector_opaque"
            }):
                errors.append(f"{reference}: opaque ownership must retain its runtime elevation")
        people = list(inputs.get("directors") or []) + list(inputs.get("ubos") or [])
        declared_pep_roles = {
            str((person.get("pep_declaration") or {}).get("pep_role_type") or "").strip()
            for person in people
            if person.get("is_pep")
        }
        declared_pep_roles.discard("")
        if declared_pep_roles:
            allowed_roles = {
                "Domestic PEP", "Foreign PEP", "International Organisation PEP",
                "Family Member", "Close Associate",
            }
            if not declared_pep_roles <= allowed_roles:
                errors.append(f"{reference}: unsupported declared PEP role {declared_pep_roles}")
            if "floor_rule_declared_pep" not in expected.get("escalations", []):
                errors.append(f"{reference}: declared PEP must retain its runtime High floor")
            if not expected.get("edd_required"):
                errors.append(f"{reference}: declared PEP must have consistent EDD state")

        covered.update(row.get("coverage") or [])

    missing_coverage = sorted(REQUIRED_COVERAGE - covered)
    if missing_coverage:
        errors.append(f"missing workflow coverage: {missing_coverage}")

    by_ref = {row["reference"]: row for row in rows if row.get("reference")}
    volume = by_ref.get("RM-PILOT-012", {}).get("expected", {})
    if "monthly_volume_score_4" not in volume.get("escalations", []):
        errors.append("RM-PILOT-012 must carry monthly_volume_score_4")
    if volume.get("tier") != "MEDIUM" or volume.get("approval_route") != "compliance_required":
        errors.append("RM-PILOT-012 must remain MEDIUM and require compliance review")
    for reference in ("RM-PILOT-033", "RM-PILOT-034", "RM-PILOT-035"):
        escalations = by_ref.get(reference, {}).get("expected", {}).get("escalations", [])
        if not any(str(reason).startswith("stale:unmapped_") for reason in escalations):
            errors.append(f"{reference} must preserve a fail-closed unresolved sentinel")

    if errors:
        raise PilotDatasetValidationError("; ".join(errors))
    return {
        "dataset": f"{DATASET_NAME} {DATASET_VERSION}",
        "scenario_count": len(rows),
        "coverage_count": len(covered),
        "manifest_sha256": manifest_sha256(),
    }


def validate_runtime_alignment(
    *,
    manifest: Optional[Mapping[str, Any]] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Re-score all scenarios and refuse any manifest/runtime divergence.

    This method never persists the computed values.  The activation contract
    must already evaluate true in the caller's process; the validator does not
    toggle or cache the feature flag itself.
    """
    from risk_controlled_values import mapping_fidelity_enabled
    from rule_engine import compute_risk_score, load_risk_config

    source = manifest or load_manifest()
    validate_manifest(source)
    if not mapping_fidelity_enabled():
        raise PilotDatasetValidationError(
            "runtime-alignment preflight requires the approved Tier 0 flag contract; "
            "the validator will not activate it"
        )
    runtime_config = config or load_risk_config()
    if not runtime_config:
        raise PilotDatasetValidationError("runtime risk_config is unavailable")

    errors: List[str] = []
    for row in source["scenarios"]:
        expected = row["expected"]
        actual = compute_risk_score(row["risk_inputs"], config_override=runtime_config)
        comparisons = {
            "score": actual.get("score"),
            "base_score": actual.get("base_risk_score"),
            "base_tier": actual.get("base_risk_level"),
            "tier": actual.get("final_risk_level"),
            "scorer_lane": actual.get("lane"),
            "requires_compliance_approval": actual.get("requires_compliance_approval"),
            "escalations": actual.get("escalations"),
            "dimensions": stable_evidence(actual.get("dimensions")),
            "controlled_mapping_evidence": stable_evidence(actual.get("controlled_mapping_evidence")),
            "service_selection_evidence": stable_evidence(actual.get("service_selection_evidence")),
            "elevation_reason_text": actual.get("elevation_reason_text"),
        }
        for field, value in comparisons.items():
            expected_value = expected.get(field)
            if value != expected_value:
                errors.append(
                    f"{row['reference']}.{field}: expected {expected_value!r}, got {value!r}"
                )
    if errors:
        raise PilotDatasetValidationError(
            "runtime contract does not match the reviewed manifest: " + "; ".join(errors)
        )
    return {
        "scenario_count": len(source["scenarios"]),
        "aligned": True,
        "manifest_sha256": manifest_sha256(),
    }


def select_scenarios(references: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    rows = scenarios()
    if not references:
        return rows
    requested = {str(value).strip().upper() for value in references if str(value).strip()}
    selected = [row for row in rows if row["reference"].upper() in requested]
    found = {row["reference"].upper() for row in selected}
    missing = sorted(requested - found)
    if missing:
        raise PilotDatasetValidationError(f"unknown canonical references: {missing}")
    return selected
