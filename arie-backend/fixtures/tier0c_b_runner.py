"""Guarded exact-41 runner for the controlled Tier 0C-B operation.

The runner never activates RSMP and never selects applications by lifecycle
status.  It locks the exact deterministic RM-PILOT-001..041 scope, validates
the reviewed manifest/runtime/configuration contract, and delegates all
recomputation and audit writes to the caller-owned transaction from
``fixtures.tier0c_b``.

``dry-run`` performs the complete recomputation and validation, then rolls
the transaction back.  Its plan hash must be supplied to the separately
gated ``apply`` mode, preventing a reviewed run from being applied after any
application, build, manifest, or risk-configuration drift.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from fixtures.pilot_canonical import (
    DATASET_NAME,
    DATASET_VERSION,
    EXPECTED_SCENARIO_COUNT,
    REFERENCE_PREFIX,
    load_manifest,
    manifest_sha256,
    stable_evidence,
    validate_manifest,
    validate_runtime_alignment,
    validate_tier0c_b_approval_routes,
)
from fixtures.pilot_canonical_seeder import IDENTITY_SOURCE
from fixtures.tier0c_b import run_tier0c_b_recomputation_transaction
from risk_controlled_values import mapping_fidelity_enabled
from rule_engine import load_risk_config


EXPECTED_REFERENCES = tuple(
    f"{REFERENCE_PREFIX}{number:03d}"
    for number in range(1, EXPECTED_SCENARIO_COUNT + 1)
)
EXPECTED_FACTOR_KEYS = {
    "D1": (
        "entity_type",
        "ownership_structure",
        "pep_status",
        "adverse_media",
        "source_of_wealth",
        "source_of_funds",
    ),
    "D2": (
        "country_of_incorporation",
        "ubo_nationalities",
        "intermediary_jurisdictions",
        "countries_of_operation",
        "target_markets",
    ),
    "D3": ("service_type", "monthly_volume", "transaction_complexity"),
    "D4": ("industry_sector",),
    "D5": ("introduction_method", "delivery_channel"),
}
EXPECTED_FACTOR_COUNT = sum(len(keys) for keys in EXPECTED_FACTOR_KEYS.values())
APPLY_CONFIRMATION = "APPLY-TIER0C-B-EXACT-41"
APPLY_ENVIRONMENT_GATE = "ALLOW_TIER0C_B_EXACT41"
APPLY_ENVIRONMENT_VALUE = "1"
ARITHMETIC_TOLERANCE = Decimal("0.0001")
ALLOWED_OPERATOR_ROLES = {"admin", "sco"}
FACTOR_EVIDENCE_FIELDS = {
    "dimension_id",
    "factor_key",
    "factor_label",
    "raw_value",
    "normalized_value",
    "rule_score",
    "factor_weight",
    "weighted_factor_contribution",
    "resolution_status",
    "rule_identifier",
    "evidence_source",
}
CANONICAL_SCREENING_REVIEW_CODES = {
    "false_positive": "false_positive_cleared",
    "cleared": "no_match",
    "escalated": "true_match",
    "sanctions_hit": "true_match",
    "adverse_media": "material_adverse_media",
}


class Tier0CBRunnerError(RuntimeError):
    """The dedicated runner refused an unsafe or inconsistent operation."""


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _bind_persisted_application_id(value: Any, application_id: str) -> Any:
    """Project manifest replay evidence onto its deterministic stored ID."""
    if isinstance(value, Mapping):
        return {
            key: (
                application_id
                if key == "application_id"
                else _bind_persisted_application_id(item, application_id)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _bind_persisted_application_id(item, application_id)
            for item in value
        ]
    return value


def _service_evidence_semantics(value: Any) -> Any:
    """Compare service outcomes independently from replay/storage payload shape."""
    if not isinstance(value, Mapping):
        return value
    return {
        key: item
        for key, item in value.items()
        if key not in {"payload_shape", "selection_source"}
    }


def _reviewed_trigger_context(value: Any) -> Any:
    """Remove only run-time timestamps from the reviewed plan projection.

    The full, raw enhanced-requirement row remains part of the mutation hash
    used to prove dry-run rollback.  This projection is narrower: it permits
    a separately executed apply to reproduce the reviewed semantic plan even
    though routing stamps a fresh ``evaluated_at`` timestamp.
    """
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return value
    if isinstance(parsed, Mapping):
        return {
            key: _reviewed_trigger_context(item)
            for key, item in parsed.items()
            if key != "evaluated_at"
        }
    if isinstance(parsed, list):
        return [_reviewed_trigger_context(item) for item in parsed]
    return parsed


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _decimal(value: Any, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise Tier0CBRunnerError(f"{label} must be numeric") from exc


def _equal_number(left: Any, right: Any) -> bool:
    return abs(_decimal(left, "left value") - _decimal(right, "right value")) <= ARITHMETIC_TOLERANCE


def risk_config_sha256(config: Mapping[str, Any]) -> str:
    """Fingerprint the validated runtime configuration used by scoring."""
    return _sha256(dict(config or {}))


def _assert_runtime_gates(expected_deploy_sha: str) -> None:
    environment = str(os.environ.get("ENVIRONMENT") or "").strip().lower()
    if environment != "staging":
        raise Tier0CBRunnerError(
            f"exact-41 Tier 0C-B runner requires ENVIRONMENT=staging, got {environment!r}"
        )
    expected = str(expected_deploy_sha or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise Tier0CBRunnerError(
            "expected deploy SHA must be a full lowercase 40-character Git SHA"
        )
    for variable in ("GIT_SHA", "IMAGE_TAG"):
        actual = str(os.environ.get(variable) or "").strip()
        if actual != expected:
            raise Tier0CBRunnerError(
                f"runtime drift: {variable} expected {expected!r}, got {actual!r}"
            )
    if not mapping_fidelity_enabled():
        raise Tier0CBRunnerError(
            "RSMP Tier 0 mapping fidelity must already be enabled; the runner will not activate it"
        )


def _assert_clean_postgres_connection(db: Any) -> None:
    """Require a dedicated idle connection before starting the one transaction."""
    if not bool(getattr(db, "is_postgres", False)):
        raise Tier0CBRunnerError("exact-41 Tier 0C-B requires PostgreSQL")
    raw_connection = getattr(db, "conn", None)
    status_reader = getattr(raw_connection, "get_transaction_status", None)
    if not callable(status_reader):
        raise Tier0CBRunnerError("PostgreSQL transaction status is unavailable")
    try:
        from psycopg2.extensions import TRANSACTION_STATUS_IDLE
    except ImportError as exc:  # pragma: no cover - PostgreSQL-only runtime guard
        raise Tier0CBRunnerError("psycopg2 transaction status support is unavailable") from exc
    if status_reader() != TRANSACTION_STATUS_IDLE:
        raise Tier0CBRunnerError(
            "exact-41 Tier 0C-B requires a dedicated idle connection; "
            "caller transaction was not touched"
        )


def _load_locked_runtime_config(
    db: Any,
    *,
    expected_version: str,
    expected_sha256: str,
) -> tuple[dict[str, Any], str]:
    if not bool(getattr(db, "is_postgres", False)):
        raise Tier0CBRunnerError("exact-41 Tier 0C-B requires PostgreSQL")

    row = db.execute(
        "SELECT id,updated_at FROM risk_config WHERE id=1 FOR SHARE"
    ).fetchone()
    if not row:
        raise Tier0CBRunnerError("risk_config row id=1 is unavailable")

    config = load_risk_config(db=db)
    if not config:
        raise Tier0CBRunnerError("validated runtime risk configuration is unavailable")
    actual_version = str(config.get("_config_version") or "")
    actual_hash = risk_config_sha256(config)
    if actual_version != str(expected_version or "").strip():
        raise Tier0CBRunnerError(
            "risk configuration version drift: "
            f"expected {expected_version!r}, got {actual_version!r}"
        )
    if actual_hash != str(expected_sha256 or "").strip():
        raise Tier0CBRunnerError(
            "risk configuration hash drift: "
            f"expected {expected_sha256!r}, got {actual_hash!r}"
        )
    return dict(config), actual_hash


def _validated_operator(db: Any, supplied: Mapping[str, Any]) -> dict[str, str]:
    """Bind immutable audit identity to an active privileged database user."""
    actor_id = str((supplied or {}).get("sub") or "").strip()
    supplied_name = str((supplied or {}).get("name") or "").strip()
    supplied_role = str((supplied or {}).get("role") or "").strip().lower()
    if not actor_id or not supplied_name or not supplied_role:
        raise Tier0CBRunnerError("audit actor id, name and role are required")
    row = db.execute(
        "SELECT id,full_name,role,status FROM users WHERE id=? FOR SHARE",
        (actor_id,),
    ).fetchone()
    if not row:
        raise Tier0CBRunnerError("audit actor is not a registered staging user")
    actual = {
        "sub": str(row["id"] or "").strip(),
        "name": str(row["full_name"] or "").strip(),
        "role": str(row["role"] or "").strip().lower(),
    }
    if str(row["status"] or "").strip().lower() != "active":
        raise Tier0CBRunnerError("audit actor is not active")
    if actual["role"] not in ALLOWED_OPERATOR_ROLES:
        raise Tier0CBRunnerError("audit actor must be an Admin or SCO")
    if supplied_name != actual["name"] or supplied_role != actual["role"]:
        raise Tier0CBRunnerError(
            "supplied audit actor name/role does not match the database identity"
        )
    return actual


def _canonical_screening_floor_overlay(
    db: Any,
    row: Mapping[str, Any],
    manifest_row: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the recompute-only screening overlay independently of scoring.

    The reviewed canonical workflow determines which disposition row the
    seeder must persist.  This intentionally does not call the risk engine's
    screening-floor helper, so the validator can detect a missing or altered
    runtime overlay instead of merely comparing the engine to itself.
    """
    workflow = dict(manifest_row.get("workflow_state") or {})
    state = str(workflow.get("screening") or "").strip().lower()
    expected_code = CANONICAL_SCREENING_REVIEW_CODES.get(state)
    latest = db.execute(
        "SELECT disposition_code FROM screening_reviews "
        "WHERE application_id=? "
        "ORDER BY updated_at DESC,created_at DESC,id DESC LIMIT 1",
        (row["id"],),
    ).fetchone()
    actual_code = str((latest or {}).get("disposition_code") or "").strip().lower()
    if expected_code and actual_code != expected_code:
        raise Tier0CBRunnerError(
            f"{row['ref']}: persisted screening disposition differs from "
            "the canonical workflow contract"
        )
    if not expected_code and actual_code:
        raise Tier0CBRunnerError(
            f"{row['ref']}: unexpected screening disposition exists"
        )
    if expected_code != "true_match":
        return {}
    reason_text = (
        "Screening disposition floor: true_match creates or preserves material "
        "screening/EDD controls and requires at least HIGH final risk"
    )
    return {
        "code": "true_match",
        "minimum_level": "HIGH",
        "reason_code": "material_screening_disposition_floor",
        "reason_text": reason_text,
        "sets_edd_lane": True,
        "edd_trigger": "edd_flag:material_screening_concern",
    }


def _duplicates(values: Sequence[Any]) -> list[Any]:
    seen = set()
    duplicates = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _validate_exact_scope(
    rows: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    actual = [dict(row) for row in rows]
    manifest_rows = [dict(row) for row in manifest.get("scenarios") or []]
    expected_by_ref = {str(row.get("reference") or ""): row for row in manifest_rows}
    actual_refs = [str(row.get("ref") or "") for row in actual]

    duplicate_refs = _duplicates(actual_refs)
    if duplicate_refs:
        raise Tier0CBRunnerError(
            f"duplicate canonical references: {duplicate_refs}"
        )
    if tuple(sorted(expected_by_ref)) != EXPECTED_REFERENCES:
        raise Tier0CBRunnerError("manifest does not define exact RM-PILOT-001..041 scope")
    if set(actual_refs) != set(EXPECTED_REFERENCES):
        raise Tier0CBRunnerError(
            "canonical application scope mismatch: "
            f"missing={sorted(set(EXPECTED_REFERENCES) - set(actual_refs))}, "
            f"unexpected={sorted(set(actual_refs) - set(EXPECTED_REFERENCES))}"
        )
    if len(actual) != EXPECTED_SCENARIO_COUNT:
        raise Tier0CBRunnerError(
            f"canonical application count must be {EXPECTED_SCENARIO_COUNT}, got {len(actual)}"
        )

    by_ref = {row["ref"]: row for row in actual}
    for reference in EXPECTED_REFERENCES:
        row = by_ref[reference]
        manifest_row = expected_by_ref[reference]
        expected_id = manifest_row.get("application_id")
        if row.get("id") != expected_id:
            raise Tier0CBRunnerError(
                f"{reference}: deterministic application ID mismatch"
            )
        if not _truthy(row.get("is_fixture")):
            raise Tier0CBRunnerError(f"{reference}: canonical record is not a fixture")

        identity = _json_object(row.get("prescreening_data"))
        required_identity = {
            "dataset_name": DATASET_NAME,
            "dataset_version": DATASET_VERSION,
            "dataset_hash": manifest_sha256(),
            "fixture": True,
            "synthetic": True,
            "non_production": True,
            "visible_in_back_office": True,
            "source": IDENTITY_SOURCE,
            "scenario_reference": reference,
            "scenario_slug": manifest_row.get("slug"),
        }
        mismatches = {
            key: {"expected": value, "actual": identity.get(key)}
            for key, value in required_identity.items()
            if identity.get(key) != value
        }
        if mismatches:
            raise Tier0CBRunnerError(
                f"{reference}: canonical fixture identity mismatch: {sorted(mismatches)}"
            )
    return [by_ref[reference] for reference in EXPECTED_REFERENCES]


def _load_exact_scope(db: Any, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    manifest_ids = [row["application_id"] for row in manifest["scenarios"]]
    manifest_refs = [row["reference"] for row in manifest["scenarios"]]
    id_placeholders = ",".join("?" for _ in manifest_ids)
    ref_placeholders = ",".join("?" for _ in manifest_refs)
    rows = db.execute(
        "SELECT * FROM applications "
        f"WHERE ref IN ({ref_placeholders}) OR id IN ({id_placeholders}) "
        "ORDER BY ref FOR UPDATE",
        (*manifest_refs, *manifest_ids),
    ).fetchall()
    return _validate_exact_scope(rows, manifest)


def _application_state_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    return _sha256([dict(row) for row in rows])


def _mutation_scope_sha256(
    db: Any,
    rows: Sequence[Mapping[str, Any]],
) -> str:
    """Hash every table the controlled recomputation path may mutate."""
    application_ids = [row["id"] for row in rows]
    references = [row["ref"] for row in rows]
    id_placeholders = ",".join("?" for _ in application_ids)
    target_values = [
        *references,
        *application_ids,
        *(f"application:{value}" for value in references),
        *(f"application:{value}" for value in application_ids),
    ]
    target_placeholders = ",".join("?" for _ in target_values)
    tables = {
        "applications": [
            dict(row)
            for row in db.execute(
                f"SELECT * FROM applications WHERE id IN ({id_placeholders}) ORDER BY id",
                tuple(application_ids),
            ).fetchall()
        ],
        "audit_log": [
            dict(row)
            for row in db.execute(
                f"SELECT * FROM audit_log WHERE application_id IN ({id_placeholders}) "
                f"OR target IN ({target_placeholders}) ORDER BY id",
                (*application_ids, *target_values),
            ).fetchall()
        ],
        "edd_cases": [
            dict(row)
            for row in db.execute(
                f"SELECT * FROM edd_cases WHERE application_id IN ({id_placeholders}) ORDER BY id",
                tuple(application_ids),
            ).fetchall()
        ],
        "edd_findings": [
            dict(row)
            for row in db.execute(
                "SELECT f.* FROM edd_findings f JOIN edd_cases e ON e.id=f.edd_case_id "
                f"WHERE e.application_id IN ({id_placeholders}) ORDER BY f.id",
                tuple(application_ids),
            ).fetchall()
        ],
        "application_enhanced_requirements": [
            dict(row)
            for row in db.execute(
                "SELECT * FROM application_enhanced_requirements "
                f"WHERE application_id IN ({id_placeholders}) ORDER BY id",
                tuple(application_ids),
            ).fetchall()
        ],
    }
    return _sha256(tables)


def _input_scope_sha256(
    db: Any,
    rows: Sequence[Mapping[str, Any]],
) -> str:
    """Fingerprint every persisted input used by scoring/routing validation."""
    application_ids = [row["id"] for row in rows]
    references = [row["ref"] for row in rows]
    id_placeholders = ",".join("?" for _ in application_ids)
    ref_placeholders = ",".join("?" for _ in references)
    tables: dict[str, Any] = {}
    for table in (
        "directors",
        "ubos",
        "intermediaries",
        "application_corrections",
        "screening_reviews",
        "screening_hit_dispositions",
        "screening_report_archive",
        "screening_reports_normalized",
    ):
        tables[table] = [
            dict(row)
            for row in db.execute(
                f'SELECT * FROM "{table}" WHERE application_id IN '
                f"({id_placeholders}) ORDER BY id",
                tuple(application_ids),
            ).fetchall()
        ]
    tables["decision_records"] = [
        dict(row)
        for row in db.execute(
            f"SELECT * FROM decision_records WHERE application_ref IN "
            f"({ref_placeholders}) ORDER BY id",
            tuple(references),
        ).fetchall()
    ]
    return _sha256(tables)


def _baseline_plan(
    *,
    db: Any,
    expected_deploy_sha: str,
    reviewed_manifest_sha256: str,
    risk_config_version: str,
    risk_config_hash: str,
    reason: str,
    user: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], str, str, str]:
    state_hash = _application_state_sha256(rows)
    mutation_hash = _mutation_scope_sha256(db, rows)
    input_hash = _input_scope_sha256(db, rows)
    plan = {
        "contract": "tier0c-b-exact-41-v1",
        "deploy_sha": expected_deploy_sha,
        "image_tag": expected_deploy_sha,
        "database_identity_sha256": _sha256(
            str(getattr(db, "database_identity", "") or "")
        ),
        "manifest_sha256": reviewed_manifest_sha256,
        "manifest_version": DATASET_VERSION,
        "risk_config_version": risk_config_version,
        "risk_config_sha256": risk_config_hash,
        "activation_enabled": True,
        "reason": reason,
        "audit_actor": {
            key: str((user or {}).get(key) or "").strip()
            for key in ("sub", "name", "role")
        },
        "ordered_applications": [
            {"reference": row["ref"], "application_id": row["id"]}
            for row in rows
        ],
        "primary_recompute_audit_counts": _primary_recompute_audit_counts(
            db,
            [row["ref"] for row in rows],
        ),
        "application_state_sha256": state_hash,
        "mutation_scope_sha256": mutation_hash,
        "input_scope_sha256": input_hash,
    }
    return plan, state_hash, mutation_hash, input_hash


def _deterministic_recomputation_summary(
    recomputations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    fields = (
        "application_id",
        "recomputed",
        "changed",
        "old_score",
        "old_level",
        "new_score",
        "new_level",
        "base_risk_score",
        "base_risk_level",
        "final_risk_level",
        "elevation_reason_text",
        "risk_escalations",
        "edd_routing_route",
        "edd_routing_triggers",
        "screening_disposition_floor",
        "manual_workflow_preservation",
    )
    result = []
    for row in recomputations:
        summary = {key: row.get(key) for key in fields}
        routing = row.get("routing_policy_result")
        if isinstance(routing, Mapping):
            summary["routing_policy_result"] = {
                key: routing.get(key)
                for key in (
                    "ran",
                    "route",
                    "policy_version",
                    "triggers",
                    "lane_persisted",
                    "errors",
                    "enhanced_requirements_deferred",
                    "enhanced_requirement_triggers",
                )
            }
        else:
            summary["routing_policy_result"] = routing
        result.append(summary)
    return result


def _reviewed_plan_components(
    baseline_plan: Mapping[str, Any],
    recomputations: Sequence[Mapping[str, Any]],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "baseline": dict(baseline_plan),
        "recomputations": _deterministic_recomputation_summary(recomputations),
        "approval_results": [
            dict(row) for row in validation.get("approval_results") or []
        ],
        "tier_policy_results": [
            dict(row) for row in validation.get("tier_policy_results") or []
        ],
        "screening_policy_results": [
            dict(row) for row in validation.get("screening_policy_results") or []
        ],
        "validated_application_state": list(
            validation.get("validated_application_state") or []
        ),
        "validated_control_state": dict(
            validation.get("validated_control_state") or {}
        ),
    }


def _reviewed_plan_sha256(
    baseline_plan: Mapping[str, Any],
    recomputations: Sequence[Mapping[str, Any]],
    validation: Mapping[str, Any],
) -> str:
    return _sha256(_reviewed_plan_components(
        baseline_plan,
        recomputations,
        validation,
    ))


def _primary_recompute_audit_counts(
    db: Any,
    references: Sequence[str],
) -> dict[str, int]:
    placeholders = ",".join("?" for _ in references)
    rows = db.execute(
        "SELECT target,COUNT(*) AS n FROM audit_log "
        "WHERE action='Risk Recomputed' "
        f"AND target IN ({placeholders}) GROUP BY target ORDER BY target",
        tuple(references),
    ).fetchall()
    actual = {str(row["target"]): int(row["n"]) for row in rows}
    return {reference: actual.get(reference, 0) for reference in references}


def _deterministic_application_post_state(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    fields = (
        "id",
        "ref",
        "risk_score",
        "risk_level",
        "base_risk_level",
        "final_risk_level",
        "elevation_reason_text",
        "onboarding_lane",
        "status",
        "risk_config_version",
    )
    return [
        {
            **{key: row.get(key) for key in fields},
            "risk_dimensions": stable_evidence(
                _json_object(row.get("risk_dimensions"))
            ),
            "risk_escalations": stable_evidence(
                _json_list(row.get("risk_escalations"))
            ),
        }
        for row in rows
    ]


def _deterministic_control_state(
    db: Any,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    application_ids = [row["id"] for row in rows]
    references = [row["ref"] for row in rows]
    id_placeholders = ",".join("?" for _ in application_ids)
    audit_targets = [
        *references,
        *application_ids,
        *(f"application:{value}" for value in references),
        *(f"application:{value}" for value in application_ids),
    ]
    target_placeholders = ",".join("?" for _ in audit_targets)
    return {
        "audit_events": [
            dict(row)
            for row in db.execute(
                "SELECT action,target,user_id,user_name,user_role,COUNT(*) AS count "
                "FROM audit_log "
                f"WHERE application_id IN ({id_placeholders}) "
                f"OR target IN ({target_placeholders}) "
                "GROUP BY action,target,user_id,user_name,user_role "
                "ORDER BY action,target,user_id,user_name,user_role",
                (*application_ids, *audit_targets),
            ).fetchall()
        ],
        "edd_cases": [
            dict(row)
            for row in db.execute(
                "SELECT application_id,risk_level,risk_score,stage,trigger_source,"
                "trigger_notes,origin_context,priority,assigned_officer,decision,"
                "decision_reason "
                f"FROM edd_cases WHERE application_id IN ({id_placeholders}) "
                "ORDER BY application_id,stage,trigger_source,origin_context",
                tuple(application_ids),
            ).fetchall()
        ],
        "enhanced_requirements": [
            {
                **dict(row),
                "trigger_context": _reviewed_trigger_context(
                    row["trigger_context"]
                ),
            }
            for row in db.execute(
                "SELECT application_id,trigger_key,trigger_label,trigger_category,"
                "requirement_key,requirement_label,requirement_description,audience,"
                "requirement_type,subject_scope,blocking_approval,waivable,mandatory,"
                "status,generation_source,trigger_reason,trigger_context,active "
                "FROM application_enhanced_requirements "
                f"WHERE application_id IN ({id_placeholders}) "
                "ORDER BY application_id,trigger_key,requirement_key",
                tuple(application_ids),
            ).fetchall()
        ],
    }


def _dimension_weight_contract(
    config: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    contract: dict[str, dict[str, Any]] = {}
    for dimension in config.get("dimensions") or []:
        dimension_id = str((dimension or {}).get("id") or "").upper()
        if not dimension_id:
            continue
        contract[dimension_id] = {
            "dimension_weight": (dimension or {}).get("weight"),
            "factor_weights": tuple(
                (factor or {}).get("weight")
                for factor in ((dimension or {}).get("subcriteria") or [])
            ),
        }
    if set(contract) != set(EXPECTED_FACTOR_KEYS):
        raise Tier0CBRunnerError("locked runtime configuration lacks exact D1-D5 weights")
    return contract


def _validate_factor_ledger(
    reference: str,
    row: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
) -> None:
    dimensions = _json_object(row.get("risk_dimensions"))
    ledger = _json_object(dimensions.get("factor_computation_evidence"))
    if ledger.get("schema_version") != "risk-factor-evidence-v1":
        raise Tier0CBRunnerError(f"{reference}: factor evidence schema is missing")

    factors = ledger.get("factors")
    dimension_rows = ledger.get("dimensions")
    if not isinstance(factors, list) or len(factors) != EXPECTED_FACTOR_COUNT:
        raise Tier0CBRunnerError(
            f"{reference}: expected {EXPECTED_FACTOR_COUNT} factor rows"
        )
    if not isinstance(dimension_rows, list) or len(dimension_rows) != 5:
        raise Tier0CBRunnerError(f"{reference}: expected five dimension ledgers")
    weight_contract = _dimension_weight_contract(config)

    factors_by_dimension: dict[str, list[Mapping[str, Any]]] = {}
    for factor in factors:
        if not isinstance(factor, Mapping):
            raise Tier0CBRunnerError(f"{reference}: malformed factor evidence row")
        if set(factor) != FACTOR_EVIDENCE_FIELDS:
            raise Tier0CBRunnerError(
                f"{reference}: factor evidence fields are incomplete or unexpected"
            )
        for required_text in (
            "factor_label",
            "resolution_status",
            "rule_identifier",
            "evidence_source",
        ):
            if not str(factor.get(required_text) or "").strip():
                raise Tier0CBRunnerError(
                    f"{reference}.{factor.get('factor_key')}: {required_text} is missing"
                )
        dimension_id = str(factor.get("dimension_id") or "")
        factors_by_dimension.setdefault(dimension_id, []).append(factor)
        contribution = _decimal(
            factor.get("weighted_factor_contribution"),
            f"{reference}.{dimension_id}.{factor.get('factor_key')}.contribution",
        )
        expected_contribution = (
            _decimal(
                factor.get("rule_score"),
                f"{reference}.{dimension_id}.{factor.get('factor_key')}.rule_score",
            )
            * _decimal(
                factor.get("factor_weight"),
                f"{reference}.{dimension_id}.{factor.get('factor_key')}.factor_weight",
            )
            / Decimal("100")
        )
        if abs(contribution - expected_contribution) > ARITHMETIC_TOLERANCE:
            raise Tier0CBRunnerError(
                f"{reference}.{dimension_id}.{factor.get('factor_key')}: "
                "factor contribution does not equal rule score x factor weight"
            )

    dimension_by_id: dict[str, Mapping[str, Any]] = {}
    for item in dimension_rows:
        if not isinstance(item, Mapping):
            raise Tier0CBRunnerError(f"{reference}: malformed dimension evidence row")
        dimension_id = str(item.get("dimension_id") or "")
        if dimension_id in dimension_by_id:
            raise Tier0CBRunnerError(
                f"{reference}: duplicate {dimension_id} dimension evidence"
            )
        dimension_by_id[dimension_id] = item

    if set(dimension_by_id) != set(EXPECTED_FACTOR_KEYS):
        raise Tier0CBRunnerError(f"{reference}: incomplete D1-D5 dimension evidence")

    for index, (dimension_id, expected_keys) in enumerate(
        EXPECTED_FACTOR_KEYS.items(), start=1
    ):
        dimension_factors = factors_by_dimension.get(dimension_id) or []
        actual_keys = tuple(str(item.get("factor_key") or "") for item in dimension_factors)
        if actual_keys != expected_keys:
            raise Tier0CBRunnerError(
                f"{reference}.{dimension_id}: expected ordered factors {expected_keys}, "
                f"got {actual_keys}"
            )
        dimension = dimension_by_id[dimension_id]
        if tuple(dimension.get("factor_keys") or ()) != expected_keys:
            raise Tier0CBRunnerError(
                f"{reference}.{dimension_id}: dimension factor_keys are inconsistent"
            )
        expected_dimension_weight = weight_contract[dimension_id]["dimension_weight"]
        if not _equal_number(
            dimension.get("dimension_weight"), expected_dimension_weight
        ):
            raise Tier0CBRunnerError(
                f"{reference}.{dimension_id}: dimension weight differs from locked config"
            )
        expected_factor_weights = weight_contract[dimension_id]["factor_weights"]
        actual_factor_weights = tuple(item.get("factor_weight") for item in dimension_factors)
        if len(expected_factor_weights) != len(actual_factor_weights) or any(
            not _equal_number(actual, expected)
            for actual, expected in zip(actual_factor_weights, expected_factor_weights)
        ):
            raise Tier0CBRunnerError(
                f"{reference}.{dimension_id}: factor weights differ from locked config"
            )
        factor_total = sum(
            (
                _decimal(
                    item.get("weighted_factor_contribution"),
                    f"{reference}.{dimension_id}.factor_total",
                )
                for item in dimension_factors
            ),
            Decimal("0"),
        )
        reproduced = factor_total + _decimal(
            dimension.get("rounding_adjustment"),
            f"{reference}.{dimension_id}.rounding_adjustment",
        )
        stored_dimension = _decimal(
            dimension.get("dimension_score"),
            f"{reference}.{dimension_id}.dimension_score",
        )
        app_dimension = _decimal(
            dimensions.get(f"d{index}"),
            f"{reference}.d{index}",
        )
        if abs(reproduced - stored_dimension) > ARITHMETIC_TOLERANCE:
            raise Tier0CBRunnerError(
                f"{reference}.{dimension_id}: factors plus rounding adjustment "
                "do not reproduce the dimension score"
            )
        if abs(stored_dimension - app_dimension) > ARITHMETIC_TOLERANCE:
            raise Tier0CBRunnerError(
                f"{reference}.{dimension_id}: ledger score differs from stored dimension"
            )
        expected_composite_contribution = (
            (factor_total - Decimal("1"))
            * _decimal(dimension.get("dimension_weight"), "dimension weight")
            / Decimal("3")
        )
        actual_composite_contribution = _decimal(
            dimension.get("composite_contribution"),
            f"{reference}.{dimension_id}.composite_contribution",
        )
        if abs(
            actual_composite_contribution - expected_composite_contribution
        ) > Decimal("0.0002"):
            raise Tier0CBRunnerError(
                f"{reference}.{dimension_id}: composite contribution does not "
                "reconcile to factor total x dimension weight"
            )

    composite = sum(
        (
            _decimal(item.get("composite_contribution"), "composite contribution")
            for item in dimension_rows
        ),
        Decimal("0"),
    ) + _decimal(ledger.get("policy_adjustment"), "policy adjustment")
    final_evidence = _decimal(ledger.get("final_composite_score"), "final composite score")
    stored_score = _decimal(row.get("risk_score"), f"{reference}.risk_score")
    if abs(composite - final_evidence) > ARITHMETIC_TOLERANCE:
        raise Tier0CBRunnerError(
            f"{reference}: dimension contributions plus policy adjustment "
            "do not reproduce final composite evidence"
        )
    if abs(final_evidence - stored_score) > ARITHMETIC_TOLERANCE:
        raise Tier0CBRunnerError(
            f"{reference}: final composite evidence differs from stored score"
        )


def _validate_persisted_results(
    *,
    db: Any,
    rows: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    config_version: str,
    config: Mapping[str, Any],
    recomputations: Sequence[Mapping[str, Any]],
    baseline_audit_counts: Mapping[str, Any],
) -> dict[str, Any]:
    manifest_by_ref = {
        row["reference"]: row for row in manifest["scenarios"]
    }
    expected_by_ref = {
        row["reference"]: row["expected"] for row in manifest["scenarios"]
    }
    expected_ids = [row["id"] for row in rows]
    recomputed_ids = [row.get("application_id") for row in recomputations]
    if recomputed_ids != expected_ids:
        raise Tier0CBRunnerError(
            "recomputation order/scope differs from exact RM-PILOT-001..041"
        )
    recomputed_by_id = {
        row.get("application_id"): row for row in recomputations
    }
    for position, recomputation in enumerate(recomputations, start=1):
        routing = recomputation.get("routing_policy_result")
        if not isinstance(routing, Mapping) or routing.get("ran") is not True:
            raise Tier0CBRunnerError(
                f"RM-PILOT-{position:03d}: routing policy did not run successfully"
            )
        if list(routing.get("errors") or []):
            raise Tier0CBRunnerError(
                f"RM-PILOT-{position:03d}: routing policy returned errors"
            )

    screening_policy_results = []
    for row in rows:
        reference = row["ref"]
        expected = expected_by_ref[reference]
        recomputed = recomputed_by_id[row["id"]]
        if not _equal_number(row.get("risk_score"), expected.get("score")):
            raise Tier0CBRunnerError(f"{reference}: risk score differs from manifest")
        tier = str(row.get("final_risk_level") or row.get("risk_level") or "")
        if tier != expected.get("tier"):
            raise Tier0CBRunnerError(f"{reference}: risk tier differs from manifest")
        if str(row.get("risk_level") or "") != expected.get("tier"):
            raise Tier0CBRunnerError(f"{reference}: stored risk_level differs from manifest")
        if str(row.get("base_risk_level") or "") != expected.get("base_tier"):
            raise Tier0CBRunnerError(
                f"{reference}: base risk level differs from manifest"
            )
        expected_floor = _canonical_screening_floor_overlay(
            db,
            row,
            manifest_by_ref[reference],
        )
        actual_floor = recomputed.get("screening_disposition_floor")
        if not isinstance(actual_floor, Mapping):
            actual_floor = {}
        expected_engine_floor = {
            key: value
            for key, value in expected_floor.items()
            if key != "edd_trigger"
        }
        if dict(actual_floor) != expected_engine_floor:
            raise Tier0CBRunnerError(
                f"{reference}: recompute screening floor differs from the "
                "canonical workflow contract"
            )
        allowed_floor_reason = str(expected_floor.get("reason_code") or "")
        allowed_floor_text = str(expected_floor.get("reason_text") or "")
        expected_elevation = str(expected.get("elevation_reason_text") or "").strip()
        if allowed_floor_text and allowed_floor_text not in expected_elevation:
            expected_elevation = (
                f"{expected_elevation}; {allowed_floor_text}"
                if expected_elevation
                else allowed_floor_text
            )
        if str(row.get("elevation_reason_text") or "") != expected_elevation:
            raise Tier0CBRunnerError(
                f"{reference}: elevation reason differs from the reviewed "
                "manifest plus canonical screening overlay"
            )
        if str(recomputed.get("elevation_reason_text") or "") != expected_elevation:
            raise Tier0CBRunnerError(
                f"{reference}: recompute result omitted the expected elevation reason"
            )
        if str(row.get("status") or "") != str(expected.get("application_status") or ""):
            raise Tier0CBRunnerError(
                f"{reference}: lifecycle status changed during recomputation"
            )
        if str(row.get("onboarding_lane") or "") != str(expected.get("lane") or ""):
            raise Tier0CBRunnerError(f"{reference}: onboarding lane differs from manifest")
        if str(row.get("risk_config_version") or "") != config_version:
            raise Tier0CBRunnerError(
                f"{reference}: risk_config_version differs from locked runtime configuration"
            )

        stored_dimensions = _json_object(row.get("risk_dimensions"))
        expected_dimensions = dict(expected.get("dimensions") or {})
        for index in range(1, 6):
            key = f"d{index}"
            if not _equal_number(stored_dimensions.get(key), expected_dimensions.get(key)):
                raise Tier0CBRunnerError(
                    f"{reference}.{key}: dimension score differs from manifest"
                )
        ledger = _json_object(stored_dimensions.get("factor_computation_evidence"))
        if not _equal_number(
            ledger.get("base_composite_score"), expected.get("base_score")
        ):
            raise Tier0CBRunnerError(
                f"{reference}: base composite evidence differs from manifest"
            )
        if stable_evidence(stored_dimensions.get("controlled_mapping_evidence")) != (
            stable_evidence(_bind_persisted_application_id(
                expected.get("controlled_mapping_evidence"),
                row["id"],
            ))
        ):
            raise Tier0CBRunnerError(
                f"{reference}: controlled mapping evidence differs from manifest"
            )
        stored_service_evidence = _json_object(
            stored_dimensions.get("service_selection_evidence")
        )
        if (
            stored_service_evidence.get("selection_source") != "_service_selections"
            or stored_service_evidence.get("payload_shape") != "list"
        ):
            raise Tier0CBRunnerError(
                f"{reference}: persisted canonical service payload provenance is invalid"
            )
        if stable_evidence(_service_evidence_semantics(stored_service_evidence)) != (
            stable_evidence(_service_evidence_semantics(
                _bind_persisted_application_id(
                    expected.get("service_selection_evidence"),
                    row["id"],
                )
            ))
        ):
            raise Tier0CBRunnerError(
                f"{reference}: service selection evidence differs from manifest"
            )
        actual_escalations = _json_list(row.get("risk_escalations"))
        expected_escalations = list(expected.get("escalations") or [])
        if allowed_floor_reason and allowed_floor_reason not in expected_escalations:
            expected_escalations.append(allowed_floor_reason)
        if stable_evidence(actual_escalations) != stable_evidence(expected_escalations):
            raise Tier0CBRunnerError(
                f"{reference}: risk escalations differ from the reviewed "
                "manifest plus canonical screening overlay"
            )
        if stable_evidence(list(recomputed.get("risk_escalations") or [])) != (
            stable_evidence(expected_escalations)
        ):
            raise Tier0CBRunnerError(
                f"{reference}: recompute result omitted expected risk escalations"
            )
        if expected_floor:
            routing = dict(recomputed.get("routing_policy_result") or {})
            if (
                routing.get("route") != "edd"
                or expected_floor["edd_trigger"] not in list(routing.get("triggers") or [])
                or str(row.get("onboarding_lane") or "") != "EDD"
            ):
                raise Tier0CBRunnerError(
                    f"{reference}: screening disposition did not retain the EDD route "
                    f"(route={routing.get('route')!r}, "
                    f"triggers={list(routing.get('triggers') or [])!r}, "
                    f"lane={row.get('onboarding_lane')!r})"
                )
        screening_policy_results.append({
            "reference": reference,
            "workflow_state": str(
                (manifest_by_ref[reference].get("workflow_state") or {}).get("screening")
                or ""
            ),
            "persisted_disposition": str(expected_floor.get("code") or ""),
            "overlay_applied": bool(expected_floor),
        })
        _validate_factor_ledger(reference, row, config=config)

    approval = validate_tier0c_b_approval_routes(rows, db=db, manifest=manifest)
    approval_by_ref = {
        item["reference"]: item for item in approval["results"]
    }
    tier_policy_routes = {
        "LOW": "direct_low_medium",
        "MEDIUM": "compliance_required",
        "HIGH": "dual_control_required",
        "VERY_HIGH": "dual_control_required",
    }
    tier_policy_results = []
    for row in rows:
        reference = row["ref"]
        route_result = approval_by_ref[reference]
        expected_route = (
            "rejected"
            if str(row.get("status") or "").strip().lower() == "rejected"
            else tier_policy_routes.get(
                str(row.get("final_risk_level") or row.get("risk_level") or "")
            )
        )
        if not expected_route or route_result.get("approval_route") != expected_route:
            raise Tier0CBRunnerError(
                f"{reference}: underlying approval route differs from the "
                "approved tier policy"
            )
        tier_policy_results.append({
            "reference": reference,
            "approval_route": route_result["approval_route"],
            "decision_eligibility": route_result["decision_eligibility"],
            "eligibility_reason": route_result["eligibility_reason"],
        })
    post_audit_counts = _primary_recompute_audit_counts(
        db,
        [row["ref"] for row in rows],
    )
    invalid_audit_deltas = {
        reference: {
            "before": int(baseline_audit_counts.get(reference, 0)),
            "after": post_audit_counts.get(reference, 0),
        }
        for reference in EXPECTED_REFERENCES
        if post_audit_counts.get(reference, 0)
        != int(baseline_audit_counts.get(reference, 0)) + 1
    }
    if invalid_audit_deltas:
        raise Tier0CBRunnerError(
            "exactly one primary Risk Recomputed audit is required for every "
            f"canonical application: {sorted(invalid_audit_deltas)}"
        )
    return {
        "valid": True,
        "scenario_count": EXPECTED_SCENARIO_COUNT,
        "factor_count_per_application": EXPECTED_FACTOR_COUNT,
        "factor_ledgers_valid": True,
        "dimension_ledgers_valid": True,
        "composite_arithmetic_valid": True,
        "approval_routes_valid": approval["approval_routes_valid"],
        "decision_eligibility_valid": approval["decision_eligibility_valid"],
        "approval_results": approval["results"],
        "tier_policy_results": tier_policy_results,
        "primary_recompute_audit_delta_valid": True,
        "screening_policy_results": screening_policy_results,
        "validated_application_state": _deterministic_application_post_state(rows),
        "validated_control_state": _deterministic_control_state(db, rows),
        "application_state_sha256": _application_state_sha256(rows),
    }


def run_exact_41_tier0c_b(
    db: Any,
    *,
    mode: str,
    expected_deploy_sha: str,
    reviewed_manifest_sha256: str,
    expected_risk_config_version: str,
    expected_risk_config_sha256: str,
    reason: str,
    user: Mapping[str, Any],
    log_audit_fn: Callable[..., Any],
    reviewed_plan_sha256: str = "",
    confirmation: str = "",
) -> dict[str, Any]:
    """Run exact-41 dry-run/apply using one caller-owned transaction."""
    operation = str(mode or "").strip().lower()
    if operation not in {"dry-run", "apply"}:
        raise Tier0CBRunnerError("mode must be 'dry-run' or 'apply'")
    _assert_runtime_gates(expected_deploy_sha)
    reason = str(reason or "").strip()
    if not reason:
        raise Tier0CBRunnerError("a non-empty recomputation reason is required")
    if not callable(log_audit_fn):
        raise Tier0CBRunnerError("an audit writer is required")
    _assert_clean_postgres_connection(db)

    manifest = load_manifest()
    validate_manifest(manifest)
    actual_manifest_hash = manifest_sha256()
    if actual_manifest_hash != str(reviewed_manifest_sha256 or "").strip():
        raise Tier0CBRunnerError(
            "reviewed manifest hash mismatch: "
            f"expected exact bytes {actual_manifest_hash}"
        )

    try:
        # SERIALIZABLE adds predicate protection for child scoring/routing
        # inputs while application rows are locked, so concurrent drift fails
        # the transaction rather than committing stale evidence.
        db.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        audit_user = _validated_operator(db, user)
        config, config_hash = _load_locked_runtime_config(
            db,
            expected_version=expected_risk_config_version,
            expected_sha256=expected_risk_config_sha256,
        )
        runtime_alignment = validate_runtime_alignment(
            manifest=manifest,
            config=config,
        )
        rows = _load_exact_scope(db, manifest)
        (
            baseline_plan,
            pre_state_hash,
            pre_mutation_hash,
            pre_input_hash,
        ) = _baseline_plan(
            db=db,
            expected_deploy_sha=expected_deploy_sha,
            reviewed_manifest_sha256=actual_manifest_hash,
            risk_config_version=str(config.get("_config_version") or ""),
            risk_config_hash=config_hash,
            reason=reason,
            user=audit_user,
            rows=rows,
        )

        if operation == "apply":
            if os.environ.get(APPLY_ENVIRONMENT_GATE) != APPLY_ENVIRONMENT_VALUE:
                raise Tier0CBRunnerError(
                    f"apply requires {APPLY_ENVIRONMENT_GATE}={APPLY_ENVIRONMENT_VALUE}"
                )
            if confirmation != APPLY_CONFIRMATION:
                raise Tier0CBRunnerError(
                    f"apply requires confirmation token {APPLY_CONFIRMATION!r}"
                )
            if len(str(reviewed_plan_sha256 or "").strip()) != 64:
                raise Tier0CBRunnerError(
                    "apply requires the full reviewed dry-run plan SHA-256"
                )

        application_ids = [row["id"] for row in rows]

        def validator(*, db, recomputations, audit_writer):
            del audit_writer
            pending_rows = _load_exact_scope(db, manifest)
            validation = _validate_persisted_results(
                db=db,
                rows=pending_rows,
                manifest=manifest,
                config_version=str(config.get("_config_version") or ""),
                config=config,
                recomputations=recomputations,
                baseline_audit_counts=baseline_plan[
                    "primary_recompute_audit_counts"
                ],
            )
            candidate_plan_hash = _reviewed_plan_sha256(
                baseline_plan,
                recomputations,
                validation,
            )
            validation["reviewed_plan_sha256"] = candidate_plan_hash
            validation["reviewed_plan_component_sha256"] = {
                key: _sha256(value)
                for key, value in _reviewed_plan_components(
                    baseline_plan,
                    recomputations,
                    validation,
                ).items()
            }
            if (
                operation == "apply"
                and str(reviewed_plan_sha256).strip() != candidate_plan_hash
            ):
                raise Tier0CBRunnerError(
                    "reviewed dry-run plan hash does not match the validated apply result"
                )
            return validation

        result = run_tier0c_b_recomputation_transaction(
            db,
            application_ids,
            reason=reason,
            user=audit_user,
            log_audit_fn=log_audit_fn,
            validator_fn=validator,
            commit_on_success=operation == "apply",
            risk_config_override=config,
            capture_routing_result=True,
        )

        if operation == "dry-run":
            restored_rows = _load_exact_scope(db, manifest)
            restored_hash = _application_state_sha256(restored_rows)
            restored_mutation_hash = _mutation_scope_sha256(db, restored_rows)
            restored_input_hash = _input_scope_sha256(db, restored_rows)
            db.rollback()
            if restored_hash != pre_state_hash:
                raise Tier0CBRunnerError(
                    "dry-run rollback did not restore the exact canonical application state"
                )
            if restored_mutation_hash != pre_mutation_hash:
                raise Tier0CBRunnerError(
                    "dry-run rollback left application, audit, EDD, or requirement residue"
                )
            if restored_input_hash != pre_input_hash:
                raise Tier0CBRunnerError(
                    "dry-run changed an authoritative scoring or routing input"
                )
        else:
            restored_hash = None
            restored_mutation_hash = None
            restored_input_hash = None

        return {
            "mode": operation,
            "contract": "tier0c-b-exact-41-v1",
            "applications_selected": EXPECTED_SCENARIO_COUNT,
            "references": list(EXPECTED_REFERENCES),
            "application_ids": application_ids,
            "includes_terminal_records": True,
            "noncanonical_applications_selected": 0,
            "manifest_sha256": actual_manifest_hash,
            "risk_config_version": str(config.get("_config_version") or ""),
            "risk_config_sha256": config_hash,
            "deploy_sha": expected_deploy_sha,
            "reason": reason,
            "audit_actor": dict(audit_user),
            "plan_sha256": result["validation"]["reviewed_plan_sha256"],
            "pre_application_state_sha256": pre_state_hash,
            "pre_mutation_scope_sha256": pre_mutation_hash,
            "pre_input_scope_sha256": pre_input_hash,
            "post_rollback_application_state_sha256": restored_hash,
            "post_rollback_mutation_scope_sha256": restored_mutation_hash,
            "post_rollback_input_scope_sha256": restored_input_hash,
            "runtime_alignment": runtime_alignment,
            "applications_recomputed": result["applications_recomputed"],
            "validation": result["validation"],
            "committed": result["committed"],
            "rolled_back": result["rolled_back"],
        }
    except BaseException:
        db.rollback()
        raise


def _standard_audit_writer() -> Callable[..., Any]:
    from base_handler import BaseHandler

    class AuditContext:
        @staticmethod
        def get_client_ip() -> str:
            return "127.0.0.1"

    context = AuditContext()

    def writer(user, action, target, detail, **kwargs):
        return BaseHandler.log_audit(
            context,
            user,
            action,
            target,
            detail,
            **kwargs,
        )

    return writer


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-deploy-sha", required=True)
    parser.add_argument("--reviewed-manifest-hash", required=True)
    parser.add_argument("--expected-risk-config-version", required=True)
    parser.add_argument("--expected-risk-config-hash", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--actor-name", required=True)
    parser.add_argument("--actor-role", required=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fixtures.tier0c_b_runner",
        description="Guarded exact-41 Tier 0C-B dry-run/apply runner",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    dry_run = subparsers.add_parser(
        "dry-run",
        help="recompute and validate exact RM-PILOT-001..041, then roll back",
    )
    _add_common_arguments(dry_run)
    apply = subparsers.add_parser(
        "apply",
        help="apply the exact reviewed dry-run plan in one transaction",
    )
    _add_common_arguments(apply)
    apply.add_argument("--reviewed-plan-hash", required=True)
    apply.add_argument("--confirm", required=True)
    args = parser.parse_args(argv)

    from db import get_db

    db = get_db()
    try:
        # PostgreSQL checkout performs a liveness SELECT. Clear only that
        # freshly-created transaction before the dedicated runner asserts an
        # idle connection and starts its caller-owned SERIALIZABLE unit.
        db.rollback()
        result = run_exact_41_tier0c_b(
            db,
            mode=args.mode,
            expected_deploy_sha=args.expected_deploy_sha,
            reviewed_manifest_sha256=args.reviewed_manifest_hash,
            expected_risk_config_version=args.expected_risk_config_version,
            expected_risk_config_sha256=args.expected_risk_config_hash,
            reason=args.reason,
            user={
                "sub": args.actor_id,
                "name": args.actor_name,
                "role": args.actor_role,
            },
            log_audit_fn=_standard_audit_writer(),
            reviewed_plan_sha256=getattr(args, "reviewed_plan_hash", ""),
            confirmation=getattr(args, "confirm", ""),
        )
    finally:
        db.close()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "APPLY_CONFIRMATION",
    "APPLY_ENVIRONMENT_GATE",
    "EXPECTED_FACTOR_COUNT",
    "EXPECTED_REFERENCES",
    "Tier0CBRunnerError",
    "risk_config_sha256",
    "run_exact_41_tier0c_b",
]
