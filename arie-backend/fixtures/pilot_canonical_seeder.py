"""Guarded, idempotent seeder for Pilot Canonical Dataset v1.

This module is an implementation artifact only.  Nothing runs on import and
no staging execution is part of this PR.  A separately approved operator run
must use :mod:`fixtures.pilot_canonical_cli`, which verifies the reviewed
manifest hash and staging-only write gates before calling this module.

The seeder never invokes providers, notifications, screening, recomputation,
or feature-flag mutation.  It persists the already-reviewed synthetic states
in one transaction and uses the existing fixture audit writer.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

from db import USE_POSTGRESQL, get_db
from fixtures.audit import make_fixture_audit_writer
from fixtures.pilot_canonical import (
    DATASET_NAME,
    DATASET_VERSION,
    PilotDatasetValidationError,
    load_manifest,
    manifest_sha256,
    select_scenarios,
    validate_manifest,
    validate_runtime_alignment,
)
from fixtures.seeder import _insert_returning_id
from rule_engine import load_risk_config


logger = logging.getLogger(__name__)
DETERMINISTIC_EPOCH = datetime(2026, 7, 1, tzinfo=timezone.utc)
IDENTITY_SOURCE = "fixtures.pilot_canonical_seeder"

# A manifest change is not, by itself, permission to adopt an existing
# RM-PILOT namespace.  Each accepted hash must be explicitly reviewed as part
# of the same dataset-version lineage.  Unknown hashes and versions remain
# foreign identities and fail closed in ``_preflight_references``.
APPROVED_MANIFEST_LINEAGE = {
    (
        "v1",
        "fee7436a6bf6ead1cc9a8090ceaa3de7071a9b745e43f2c69a445cf74efdf9c9",
    ): (
        "v1",
        "825d267a6488545ee892789f09869362faabdf77fb23df8d1d63b99f6dc27951",
    ),
    (
        "v1",
        "825d267a6488545ee892789f09869362faabdf77fb23df8d1d63b99f6dc27951",
    ): (
        "v1",
        "45ceaa32d592f754289fb888bbb6d6a863349cf9bde406e7d7055b6c7dc23d25",
    ),
}

# Keep fixture persistence on the same canonical document-type contract used
# by backend startup normalization.  The manifest may retain human-facing
# legacy labels for scenario evidence, but persisted ``documents.doc_type``
# and linked document-request rows must never reintroduce those aliases.
CANONICAL_DOCUMENT_TYPE_ALIASES = {
    "certificate_of_incorporation": "cert_inc",
    "proof_of_address": "poa",
}


def _canonical_document_type(value: Any) -> str:
    raw = str(value or "").strip()
    return CANONICAL_DOCUMENT_TYPE_ALIASES.get(raw.casefold(), raw)


class PilotDatasetReferenceCollision(RuntimeError):
    """Reserved canonical identity is occupied by an unrelated record."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _bind_runtime_config_version(value: Any, version: str) -> Any:
    """Bind stable manifest evidence to the preflighted live config version."""
    if isinstance(value, dict):
        return {
            key: (version if key == "config_version" else _bind_runtime_config_version(item, version))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_bind_runtime_config_version(item, version) for item in value]
    return value


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _iso(row: Mapping[str, Any], *, offset: int = 0) -> str:
    value = DETERMINISTIC_EPOCH + timedelta(minutes=int(row["number"]) * 10 + offset)
    return value.isoformat()


def _identity(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "dataset_hash": manifest_sha256(),
        "fixture": True,
        "synthetic": True,
        "non_production": True,
        "visible_in_back_office": True,
        "source": IDENTITY_SOURCE,
        "scenario_reference": row["reference"],
        "scenario_slug": row["slug"],
    }


def _identity_matches(record: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    data = _json_object(record.get("prescreening_data"))
    expected = _identity(row)
    immutable_keys = (
        "dataset_name",
        "fixture",
        "synthetic",
        "non_production",
        "visible_in_back_office",
        "source",
        "scenario_reference",
        "scenario_slug",
    )
    if any(data.get(key) != expected[key] for key in immutable_keys):
        return False

    stored_identity = (data.get("dataset_version"), data.get("dataset_hash"))
    current_identity = (DATASET_VERSION, manifest_sha256())
    return stored_identity == current_identity or (
        APPROVED_MANIFEST_LINEAGE.get(stored_identity) == current_identity
    )


_RUNTIME_PRESCREENING_INPUT_KEYS = (
    "adverse_media",
    "cross_border",
    "customer_interaction",
    "introduction_method",
    "monthly_volume",
    "operating_countries",
    "screening_concern",
    "screening_results",
    "source_of_funds",
    "source_of_wealth",
    "target_markets",
    "transaction_complexity",
)


def _runtime_prescreening_projection(inputs: Mapping[str, Any]) -> Dict[str, Any]:
    """Project fixture inputs onto the normal runtime submission fields.

    The nested ``risk_inputs`` object is retained as fixture provenance, but
    ``build_prescreening_risk_input`` intentionally consumes normalized
    top-level prescreening fields.
    """
    projected = {
        key: deepcopy(inputs[key])
        for key in _RUNTIME_PRESCREENING_INPUT_KEYS
        if key in inputs
    }
    services = (
        ((inputs.get("business") or {}).get("services") or {}).get(
            "primary_services"
        )
        or []
    )
    projected["services_required"] = deepcopy(services)
    projected["primary_service"] = inputs.get("primary_service") or (
        services[0] if services else ""
    )
    projected["service_required"] = projected["primary_service"]
    return projected


def _preflight_references(db, rows: Sequence[Mapping[str, Any]]) -> None:
    """Check every reserved ref and deterministic application id before writes."""
    lock = " FOR UPDATE" if USE_POSTGRESQL else ""
    for row in rows:
        existing = db.execute(
            "SELECT id, ref, is_fixture, prescreening_data FROM applications "
            f"WHERE ref=? OR id=?{lock}",
            (row["reference"], row["application_id"]),
        ).fetchall()
        if len(existing) > 1:
            raise PilotDatasetReferenceCollision(
                f"{row['reference']}: deterministic id and reference are split across records"
            )
        if not existing:
            continue
        record = existing[0]
        if not _truthy(record.get("is_fixture")):
            raise PilotDatasetReferenceCollision(
                f"{row['reference']}: reserved reference belongs to non-fixture data"
            )
        if record.get("id") != row["application_id"] or record.get("ref") != row["reference"]:
            raise PilotDatasetReferenceCollision(
                f"{row['reference']}: deterministic id/reference collision"
            )
        if not _identity_matches(record, row):
            raise PilotDatasetReferenceCollision(
                f"{row['reference']}: reserved reference belongs to another fixture identity"
            )


def _application_payload(
    row: Mapping[str, Any], *, risk_config_version: str, risk_config: Mapping[str, Any]
) -> tuple:
    from rule_engine import compute_risk_score

    inputs = row["risk_inputs"]
    expected = row["expected"]
    workflow = row["workflow_state"]
    screening_state = workflow["screening"]
    screening_report = {
        "status": screening_state,
        "synthetic": True,
        "provider_called": False,
        "summary": f"Canonical fixture screening state: {screening_state}",
    }
    prescreening = {
        **_runtime_prescreening_projection(inputs),
        **_identity(row),
        "purpose": row["purpose"],
        "category": row["category"],
        "coverage": row["coverage"],
        "screens": row["screens"],
        "demo_step": row["demo_step"],
        "risk_inputs": inputs,
        "expected": expected,
        "workflow_state": workflow,
        "screening_report": screening_report,
        "risk_config_version": risk_config_version,
        "scenario_evidence": row.get("scenario_evidence") or {},
        "correction_workflow": row.get("correction_workflow") or {},
        "evidence_export": row.get("evidence_export") or {},
        "supervisor_evidence": row.get("supervisor_evidence") or {},
    }
    runtime_dimensions = _bind_runtime_config_version(expected["dimensions"], risk_config_version)
    computed = compute_risk_score(inputs, config_override=dict(risk_config))
    factor_computation_evidence = (computed.get("dimensions") or {}).get(
        "factor_computation_evidence"
    )
    if not factor_computation_evidence:
        raise PilotDatasetValidationError(
            f"{row['reference']}: authoritative factor computation evidence is missing"
        )
    risk_dimensions = {
        **runtime_dimensions,
        "factor_computation_evidence": factor_computation_evidence,
        "controlled_mapping_evidence": _bind_runtime_config_version(
            expected["controlled_mapping_evidence"], risk_config_version
        ),
        "service_selection_evidence": _bind_runtime_config_version(
            expected["service_selection_evidence"], risk_config_version
        ),
        "canonical_dataset": _identity(row),
        "expected_approval_route": expected["approval_route"],
        "expected_edd_required": expected["edd_required"],
    }
    manual_compliance = (row.get("scenario_evidence") or {}).get(
        "manual_compliance_escalation"
    ) or {}
    submitted_to_compliance_at = (
        _iso(row, offset=3) if manual_compliance else None
    )
    submitted_to_compliance_by = (
        manual_compliance.get("submitted_by") if manual_compliance else None
    )
    decided = expected["application_status"] in {"approved", "rejected"}
    return (
        row["reference"],
        row["company_name"],
        f"BRN-{row['reference']}",
        inputs.get("country") or None,
        inputs.get("sector"),
        inputs.get("entity_type"),
        inputs.get("ownership_structure"),
        _json(prescreening),
        expected["score"],
        expected["tier"],
        _json(risk_dimensions),
        expected["lane"],
        expected["application_status"],
        submitted_to_compliance_at,
        submitted_to_compliance_by,
        "co001",
        _iso(row, offset=1),
        _iso(row, offset=8) if decided else None,
        "sco001" if decided else None,
        expected["outcome"],
        "fixture",
        True,
        _iso(row),
        _iso(row, offset=9),
        _iso(row, offset=1),
        _iso(row, offset=2),
        risk_config_version,
        _json(expected["escalations"]),
        expected["base_tier"],
        expected["tier"],
        expected["elevation_reason_text"],
    )


def _upsert_application(
    db, audit, row: Mapping[str, Any], *, risk_config_version: str,
    risk_config: Mapping[str, Any]
) -> str:
    values = _application_payload(
        row, risk_config_version=risk_config_version, risk_config=risk_config
    )
    existing = db.execute(
        "SELECT id FROM applications WHERE ref=?", (row["reference"],)
    ).fetchone()
    common_columns = (
        "ref, company_name, brn, country, sector, entity_type, ownership_structure, "
        "prescreening_data, risk_score, risk_level, risk_dimensions, onboarding_lane, "
        "status, submitted_to_compliance_at, submitted_to_compliance_by, assigned_to, "
        "submitted_at, decided_at, decision_by, decision_notes, "
        "screening_mode, is_fixture, created_at, updated_at, inputs_updated_at, "
        "risk_computed_at, risk_config_version, risk_escalations, base_risk_level, "
        "final_risk_level, elevation_reason_text"
    )
    if existing:
        assignments = ", ".join(f"{name.strip()}=?" for name in common_columns.split(","))
        db.execute(
            f"UPDATE applications SET {assignments} WHERE id=?",
            (*values, row["application_id"]),
        )
    else:
        placeholders = ",".join("?" for _ in range(len(values) + 1))
        db.execute(
            f"INSERT INTO applications (id, {common_columns}) VALUES ({placeholders})",
            (row["application_id"], *values),
        )
    audit(
        action="pilot_canonical_application",
        target=f"application:{row['application_id']}",
        detail=f"Converged {DATASET_NAME} {DATASET_VERSION} {row['reference']}",
        after_state={"reference": row["reference"], "manifest_sha256": manifest_sha256()},
    )
    return row["application_id"]


def _upsert_people(db, audit, row: Mapping[str, Any]) -> Dict[str, List[str]]:
    inputs = row["risk_inputs"]
    app_id = row["application_id"]
    result = {"director_ids": [], "ubo_ids": [], "intermediary_ids": []}
    for table, people, target_key in (
        ("directors", inputs.get("directors") or [], "director_ids"),
        ("ubos", inputs.get("ubos") or [], "ubo_ids"),
    ):
        for index, person in enumerate(people, start=1):
            person_id = f"{app_id}{'d' if table == 'directors' else 'u'}{index:02d}"
            person_key = f"{row['reference']}:{table}:{index}"
            pep = person.get("pep_declaration") or {}
            existing = db.execute(f"SELECT id FROM {table} WHERE id=?", (person_id,)).fetchone()
            if table == "directors":
                columns = "application_id=?, person_key=?, full_name=?, nationality=?, is_pep=?, pep_declaration=?"
                values = (app_id, person_key, person["full_name"], person.get("nationality"), bool(person.get("is_pep")), _json(pep))
            else:
                columns = "application_id=?, person_key=?, full_name=?, nationality=?, ownership_pct=?, is_pep=?, pep_declaration=?"
                values = (app_id, person_key, person["full_name"], person.get("nationality"), person.get("ownership_pct"), bool(person.get("is_pep")), _json(pep))
            if existing:
                db.execute(f"UPDATE {table} SET {columns} WHERE id=?", (*values, person_id))
            else:
                names = columns.replace("=?", "").replace(" ", "")
                placeholders = ",".join("?" for _ in range(len(values) + 1))
                db.execute(f"INSERT INTO {table} (id,{names}) VALUES ({placeholders})", (person_id, *values))
            result[target_key].append(person_id)

    for index, entity in enumerate(inputs.get("intermediary_shareholders") or [], start=1):
        entity_id = f"{app_id}i{index:02d}"
        person_key = f"{row['reference']}:intermediaries:{index}"
        values = (app_id, person_key, entity["entity_name"], entity.get("jurisdiction"), f"REG-{row['number']:03d}-{index:02d}", entity.get("ownership_pct"))
        existing = db.execute("SELECT id FROM intermediaries WHERE id=?", (entity_id,)).fetchone()
        if existing:
            db.execute("UPDATE intermediaries SET application_id=?,person_key=?,entity_name=?,jurisdiction=?,registration_number=?,ownership_pct=? WHERE id=?", (*values, entity_id))
        else:
            db.execute("INSERT INTO intermediaries (id,application_id,person_key,entity_name,jurisdiction,registration_number,ownership_pct) VALUES (?,?,?,?,?,?,?)", (entity_id, *values))
        result["intermediary_ids"].append(entity_id)
    audit(
        action="pilot_canonical_parties",
        target=f"application:{app_id}",
        detail=f"Converged canonical parties for {row['reference']}",
        after_state=result,
    )
    return result


def _document_specs(row: Mapping[str, Any]) -> List[Dict[str, str]]:
    state = row["workflow_state"]["documents"]
    idv = row["workflow_state"]["idv"]
    if state == "missing":
        return [{"type": "passport", "verification": "verified", "review": "accepted"}]
    passport_verification = "failed" if idv == "failed" else "verified"
    passport_review = "rejected" if idv == "failed" else "accepted"
    default_specs = [
        {"type": "passport", "verification": passport_verification, "review": passport_review},
        {"type": "cert_inc", "verification": "verified", "review": "accepted"},
        {"type": "ownership_evidence", "verification": "verified", "review": "accepted"},
    ]
    existing_types = {item["type"] for item in default_specs}
    for evidence in row.get("evidence_documents") or []:
        document_type = _canonical_document_type(evidence.get("type"))
        if not document_type or document_type in existing_types:
            continue
        default_specs.append({
            "type": document_type,
            "verification": str(evidence.get("verification") or "verified"),
            "review": str(evidence.get("review") or "accepted"),
        })
        existing_types.add(document_type)
    return default_specs


def _upsert_documents(db, audit, row: Mapping[str, Any]) -> List[str]:
    ids: List[str] = []
    for index, spec in enumerate(_document_specs(row), start=1):
        document_id = f"{row['application_id']}x{index:02d}"
        file_path = f"fixture://pilot-canonical/v1/{row['reference']}/{spec['type']}"
        existing = db.execute("SELECT id FROM documents WHERE id=?", (document_id,)).fetchone()
        values = (
            row["application_id"], spec["type"], f"{row['reference']}-{spec['type']}.fixture",
            file_path, spec["verification"], spec["review"], "test_only_synthetic",
            "Pilot Canonical Dataset: synthetic non-production fixture", "fixture_seed",
            "pilot_canonical_dataset", _iso(row, offset=3),
        )
        assignments = "application_id=?,doc_type=?,doc_name=?,file_path=?,verification_status=?,review_status=?,evidence_class=?,evidence_classification_note=?,uploaded_by_actor_type=?,upload_source=?,uploaded_at=?"
        if existing:
            db.execute(f"UPDATE documents SET {assignments} WHERE id=?", (*values, document_id))
        else:
            names = assignments.replace("=?", "").replace(" ", "")
            placeholders = ",".join("?" for _ in range(len(values) + 1))
            db.execute(f"INSERT INTO documents (id,{names}) VALUES ({placeholders})", (document_id, *values))
        ids.append(document_id)
    audit(
        action="pilot_canonical_documents",
        target=f"application:{row['application_id']}",
        detail=f"Converged canonical documents for {row['reference']}",
        after_state={"document_ids": ids, "state": row["workflow_state"]["documents"]},
    )
    return ids


def _upsert_monitoring(db, audit, row: Mapping[str, Any]) -> Optional[int]:
    state = row["workflow_state"]["monitoring"]
    if state == "none":
        return None
    source_reference = f"{row['reference']}:MONITORING"
    definitions = {
        "false_positive": ("adverse_media", "low", "dismissed", "dismiss", "Synthetic false positive dismissed"),
        "cleared": ("sanctions", "low", "resolved", "clear", "Synthetic alert cleared"),
        "escalated": ("sanctions", "high", "in_review", "create_edd", "Synthetic confirmed match escalated"),
        "open": ("adverse_media", "medium", "open", None, "Synthetic alert awaiting officer review"),
    }
    alert_type, severity, status, officer_action, summary = definitions[state]
    existing = db.execute("SELECT id FROM monitoring_alerts WHERE source_reference=?", (source_reference,)).fetchone()
    values = (
        row["application_id"], "synthetic_fixture", f"PILOT-{row['number']:03d}", "manual",
        _iso(row, offset=4), row["company_name"], alert_type, severity, "fixture_seed",
        summary, source_reference, "Manual review only", status, officer_action,
        f"{DATASET_NAME} {DATASET_VERSION}; provider_called=false", _iso(row, offset=4),
        _iso(row, offset=7) if status in {"resolved", "dismissed"} else None, "co001" if status in {"resolved", "dismissed"} else None,
    )
    columns = "application_id,provider,case_identifier,discovered_via,discovered_at,client_name,alert_type,severity,detected_by,summary,source_reference,ai_recommendation,status,officer_action,officer_notes,created_at,reviewed_at,reviewed_by"
    if existing:
        assignments = ",".join(f"{name}=?" for name in columns.split(","))
        db.execute(f"UPDATE monitoring_alerts SET {assignments} WHERE id=?", (*values, existing["id"]))
        alert_id = existing["id"]
    else:
        alert_id = _insert_returning_id(db, "monitoring_alerts", columns, values)
    audit(action="pilot_canonical_monitoring", target=f"monitoring_alert:{alert_id}", detail=f"Converged canonical monitoring state for {row['reference']}", after_state={"state": state, "source_reference": source_reference})
    return alert_id


def _upsert_screening_review(db, row: Mapping[str, Any]) -> Optional[int]:
    state = row["workflow_state"]["screening"]
    if state not in {"false_positive", "cleared", "escalated", "sanctions_hit", "adverse_media"}:
        return None
    subject_name = row["company_name"]
    existing = db.execute("SELECT id FROM screening_reviews WHERE application_id=? AND subject_type=? AND subject_name=?", (row["application_id"], "entity", subject_name)).fetchone()
    disposition = "escalated" if state in {"escalated", "sanctions_hit", "adverse_media"} else "cleared"
    code = {
        "false_positive": "false_positive_cleared",
        "cleared": "no_match",
        "escalated": "true_match",
        "sanctions_hit": "true_match",
        "adverse_media": "material_adverse_media",
    }[state]
    values = (disposition, f"{DATASET_NAME} synthetic {state}", code, f"Deterministic {state} screening disposition", "co001", "Canonical Fixture Officer", _iso(row, offset=6))
    if existing:
        db.execute("UPDATE screening_reviews SET disposition=?,notes=?,disposition_code=?,rationale=?,reviewer_id=?,reviewer_name=?,updated_at=? WHERE id=?", (*values, existing["id"]))
        return existing["id"]
    return _insert_returning_id(
        db,
        "screening_reviews",
        "application_id,subject_type,subject_name,disposition,notes,disposition_code,"
        "rationale,reviewer_id,reviewer_name,created_at,updated_at",
        (row["application_id"], "entity", subject_name, *values[:-1], values[-1], values[-1]),
    )


def _upsert_periodic_review(db, audit, row: Mapping[str, Any], alert_id: Optional[int]) -> Optional[int]:
    state = row["workflow_state"]["periodic_review"]
    if state == "none":
        return None
    marker = f"{row['reference']}:PERIODIC"
    status = "completed" if state == "completed" else "in_progress"
    expected = row["expected"]
    review_anchor = (DETERMINISTIC_EPOCH + timedelta(minutes=int(row["number"]) * 10)).date()
    if state == "completed":
        last_review_date = review_anchor.isoformat()
        next_review_date = (review_anchor + timedelta(days=365)).isoformat()
    else:
        last_review_date = (review_anchor - timedelta(days=365)).isoformat()
        next_review_date = (review_anchor + timedelta(days=365)).isoformat()
    priority = {
        "LOW": "low",
        "MEDIUM": "normal",
        "HIGH": "high",
        "VERY_HIGH": "urgent",
    }[expected["tier"]]
    existing = db.execute("SELECT id FROM periodic_reviews WHERE trigger_reason=?", (marker,)).fetchone()
    values = (
        row["application_id"], row["company_name"], expected["tier"], last_review_date,
        next_review_date, "pilot_canonical_fixture",
        marker, "pilot_canonical_dataset", alert_id, expected["base_tier"], expected["tier"],
        f"{DATASET_NAME} deterministic {state} review", status, next_review_date,
        _iso(row, offset=5), _iso(row, offset=7) if state == "completed" else None,
        "co001", priority, expected["outcome"],
        expected["outcome"], DATASET_VERSION, 12, "canonical_dataset_fixture", _iso(row, offset=5),
    )
    columns = (
        "application_id,client_name,risk_level,last_review_date,next_review_date,trigger_type,"
        "trigger_reason,trigger_source,linked_monitoring_alert_id,previous_risk_level,"
        "new_risk_level,review_memo,status,due_date,started_at,completed_at,assigned_officer,"
        "priority,decision,decision_reason,policy_version,frequency_months,calculation_basis,created_at"
    )
    if existing:
        assignments = ",".join(f"{name}=?" for name in columns.split(","))
        db.execute(f"UPDATE periodic_reviews SET {assignments} WHERE id=?", (*values, existing["id"]))
        review_id = existing["id"]
    else:
        review_id = _insert_returning_id(db, "periodic_reviews", columns, values)
    audit(action="pilot_canonical_periodic_review", target=f"periodic_review:{review_id}", detail=f"Converged canonical periodic review for {row['reference']}", after_state={"state": state})
    audit(
        action="pilot_canonical_notification_suppressed",
        target=f"periodic_review:{review_id}",
        detail=f"Recorded synthetic notification suppression for {row['reference']}",
        after_state={
            "notification_suppressed": True,
            "notification_suppression_reason": "fixture_application",
            "enforcement_key": "applications.is_fixture",
        },
    )
    return review_id


def _edd_workflow_provenance(row: Mapping[str, Any]) -> tuple[str, str]:
    """Return persisted provenance for automatic or officer-imposed fixture EDD."""
    expected = row.get("expected") or {}
    officer_review = (row.get("scenario_evidence") or {}).get("officer_review")
    manually_imposed = (
        expected.get("lane") == "EDD"
        and expected.get("scorer_lane") != "EDD"
        and bool(officer_review)
    )
    if manually_imposed:
        return "officer_escalate_edd", "manual_onboarding_escalation"
    return "pilot_canonical_fixture", "pilot_canonical_dataset"


def _upsert_edd(db, audit, row: Mapping[str, Any], alert_id: Optional[int], review_id: Optional[int]) -> Optional[int]:
    if not row["expected"]["edd_required"]:
        return None
    marker = f"{row['reference']}:EDD"
    existing = db.execute("SELECT id FROM edd_cases WHERE trigger_notes LIKE ?", (f"{marker}%",)).fetchone()
    stage = "analysis" if row["workflow_state"]["monitoring"] in {"escalated", "open"} else "information_gathering"
    notes = marker + " " + _json({"dataset": DATASET_NAME, "reference": row["reference"], "synthetic": True, "source_alert_id": alert_id, "source_review_id": review_id})
    trigger_source, origin_context = _edd_workflow_provenance(row)
    values = (row["application_id"], row["company_name"], row["expected"]["tier"], row["expected"]["score"], stage, "co001", trigger_source, notes, origin_context, alert_id, review_id, _iso(row, offset=5), _iso(row, offset=5), _iso(row, offset=6))
    columns = "application_id,client_name,risk_level,risk_score,stage,assigned_officer,trigger_source,trigger_notes,origin_context,linked_monitoring_alert_id,linked_periodic_review_id,assigned_at,triggered_at,updated_at"
    if existing:
        assignments = ",".join(f"{name}=?" for name in columns.split(","))
        db.execute(f"UPDATE edd_cases SET {assignments} WHERE id=?", (*values, existing["id"]))
        edd_id = existing["id"]
    else:
        edd_id = _insert_returning_id(db, "edd_cases", columns, values)
    audit(action="pilot_canonical_edd", target=f"edd_case:{edd_id}", detail=f"Converged canonical EDD state for {row['reference']}", after_state={"stage": stage, "trigger_source": trigger_source, "origin_context": origin_context})
    return edd_id


def _upsert_edd_findings(
    db, audit, row: Mapping[str, Any], edd_id: Optional[int]
) -> Optional[int]:
    evidence = row.get("edd_evidence") or {}
    if not edd_id or not evidence:
        return None
    existing = db.execute(
        "SELECT id FROM edd_findings WHERE edd_case_id=?", (edd_id,)
    ).fetchone()
    values = (
        evidence.get("findings_summary"),
        _json(evidence.get("key_concerns") or []),
        _json(evidence.get("mitigating_evidence") or []),
        _json(evidence.get("conditions") or []),
        evidence.get("rationale"),
        _json(evidence.get("supporting_notes") or []),
        evidence.get("recommended_outcome"),
        "fixture_seed",
        _iso(row, offset=6),
        "fixture_seed",
        _iso(row, offset=7),
    )
    columns = (
        "findings_summary,key_concerns,mitigating_evidence,conditions,rationale,"
        "supporting_notes,recommended_outcome,created_by,created_at,updated_by,updated_at"
    )
    if existing:
        assignments = ",".join(f"{name}=?" for name in columns.split(","))
        db.execute(
            f"UPDATE edd_findings SET {assignments} WHERE id=?",
            (*values, existing["id"]),
        )
        finding_id = existing["id"]
    else:
        finding_id = _insert_returning_id(
            db, "edd_findings", f"edd_case_id,{columns}", (edd_id, *values)
        )
    audit(
        action="pilot_canonical_edd_findings",
        target=f"edd_finding:{finding_id}",
        detail=f"Converged canonical EDD evidence for {row['reference']}",
        after_state={"reference": row["reference"], "recommended_outcome": evidence.get("recommended_outcome")},
    )
    return finding_id


def _upsert_correction_workflow(db, audit, row: Mapping[str, Any]) -> Dict[str, Any]:
    workflow = row.get("correction_workflow") or {}
    if not workflow:
        return {"correction_id": None, "rmi_request_id": None, "rmi_item_id": None}

    app_id = row["application_id"]
    request_id = f"{app_id}:correction-request"
    item_id = f"{app_id}:correction-item"
    correction_source = "pilot_canonical_officer_correction"
    before_state = workflow["before_state"]
    after_state = workflow["after_state"]
    field_scope = str(workflow["field_scope"])

    document = db.execute(
        "SELECT id FROM documents WHERE application_id=? AND doc_type=? ORDER BY id LIMIT 1",
        (
            app_id,
            _canonical_document_type(
                workflow.get("supporting_document_type") or "poa"
            ),
        ),
    ).fetchone()
    if not document:
        raise PilotDatasetValidationError(
            f"{row['reference']}: correction supporting document is missing"
        )

    request_values = (
        app_id, "fulfilled", workflow["request_reason"], _iso(row, offset=20),
        "co001", "Canonical Fixture Officer", _iso(row, offset=3),
        _iso(row, offset=6), _iso(row, offset=6),
    )
    request_columns = (
        "application_id,status,reason,deadline,created_by,created_by_name,"
        "created_at,updated_at,fulfilled_at"
    )
    if db.execute("SELECT id FROM rmi_requests WHERE id=?", (request_id,)).fetchone():
        assignments = ",".join(f"{name}=?" for name in request_columns.split(","))
        db.execute(f"UPDATE rmi_requests SET {assignments} WHERE id=?", (*request_values, request_id))
    else:
        db.execute(
            f"INSERT INTO rmi_requests (id,{request_columns}) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (request_id, *request_values),
        )

    item_values = (
        request_id,
        _canonical_document_type(
            workflow.get("requested_document_type") or "poa"
        ),
        workflow["request_label"], workflow["request_description"], "accepted",
        document["id"], _iso(row, offset=5), _iso(row, offset=6), _iso(row, offset=3),
    )
    item_columns = (
        "request_id,doc_type,label,description,status,document_id,uploaded_at,reviewed_at,created_at"
    )
    if db.execute("SELECT id FROM rmi_request_items WHERE id=?", (item_id,)).fetchone():
        assignments = ",".join(f"{name}=?" for name in item_columns.split(","))
        db.execute(f"UPDATE rmi_request_items SET {assignments} WHERE id=?", (*item_values, item_id))
    else:
        db.execute(
            f"INSERT INTO rmi_request_items (id,{item_columns}) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (item_id, *item_values),
        )

    correction_values = (
        app_id, "application", app_id, "entity", field_scope, "tier_0",
        workflow["correction_reason"], workflow["evidence_source"],
        workflow["correction_note"], correction_source, _json(before_state),
        _json(after_state), _json(workflow["downstream_state"]), "co001",
        "Canonical Fixture Officer", "co", _iso(row, offset=6),
    )
    correction_columns = (
        "application_id,target_type,target_id,subject_type,field_scope,materiality,"
        "correction_reason,evidence_source,correction_note,correction_source,before_state,"
        "after_state,downstream_state,corrected_by,corrected_by_name,corrected_by_role,corrected_at"
    )
    existing = db.execute(
        "SELECT id FROM application_corrections WHERE application_id=? AND correction_source=?",
        (app_id, correction_source),
    ).fetchone()
    if existing:
        assignments = ",".join(f"{name}=?" for name in correction_columns.split(","))
        db.execute(
            f"UPDATE application_corrections SET {assignments} WHERE id=?",
            (*correction_values, existing["id"]),
        )
        correction_id = existing["id"]
    else:
        correction_id = _insert_returning_id(
            db, "application_corrections", correction_columns, correction_values
        )

    audit(
        action="pilot_canonical_correction_requested",
        target=f"application:{app_id}",
        detail=f"Officer requested correction for {row['reference']}",
        after_state={"request_id": request_id, "reason": workflow["request_reason"]},
    )
    audit(
        action="pilot_canonical_applicant_correction",
        target=f"application:{app_id}",
        detail=f"Applicant supplied correction for {row['reference']}",
        before_state=before_state,
        after_state=after_state,
    )
    audit(
        action="pilot_canonical_officer_correction",
        target=f"application:{app_id}",
        detail=f"Officer verified correction for {row['reference']}",
        after_state={"correction_id": correction_id, "final_disposition": "approved"},
    )
    return {
        "correction_id": correction_id,
        "rmi_request_id": request_id,
        "rmi_item_id": item_id,
    }


def _upsert_decision_record(db, audit, row: Mapping[str, Any]) -> Optional[str]:
    evidence = row.get("decision_evidence") or {}
    if not evidence:
        return None
    decision_id = f"{row['application_id']}:decision"
    values = (
        row["reference"], evidence.get("decision_type") or "approve",
        row["expected"]["tier"], float(evidence.get("confidence_score") or 1.0),
        evidence.get("source") or "manual", evidence.get("actor_user_id") or "co001",
        evidence.get("actor_role") or "co", _iso(row, offset=8),
        _json(evidence.get("key_flags") or []), 0, None,
        _json({"dataset": DATASET_NAME, "reasoning": evidence.get("reasoning"), "final_disposition": evidence.get("final_disposition")}),
    )
    columns = (
        "application_ref,decision_type,risk_level,confidence_score,source,actor_user_id,"
        "actor_role,timestamp,key_flags,override_flag,override_reason,extra_json"
    )
    if db.execute("SELECT id FROM decision_records WHERE id=?", (decision_id,)).fetchone():
        assignments = ",".join(f"{name}=?" for name in columns.split(","))
        db.execute(f"UPDATE decision_records SET {assignments} WHERE id=?", (*values, decision_id))
    else:
        placeholders = ",".join("?" for _ in range(len(values) + 1))
        db.execute(
            f"INSERT INTO decision_records (id,{columns}) VALUES ({placeholders})",
            (decision_id, *values),
        )
    audit(
        action="pilot_canonical_final_disposition",
        target=f"application:{row['application_id']}",
        detail=f"Recorded final disposition for {row['reference']}",
        after_state={"decision_id": decision_id, "decision": evidence.get("final_disposition")},
    )
    return decision_id


def _canonical_memo_decision(row: Mapping[str, Any]) -> str:
    expected = row["expected"]
    decision_evidence = row.get("decision_evidence") or {}
    final_disposition = str(
        decision_evidence.get("final_disposition") or ""
    ).strip().lower()
    if final_disposition:
        return {
            "approved": "APPROVE",
            "approved_with_conditions": "APPROVE_WITH_CONDITIONS",
            "approved_with_enhanced_monitoring": "APPROVE_WITH_ENHANCED_MONITORING",
            "rejected": "REJECT",
            "review": "REVIEW",
        }.get(final_disposition, "REVIEW")
    if expected["application_status"] == "rejected":
        return "REJECT"
    if expected["approval_route"] == "blocked" or expected["memo_status"] == "blocked":
        return "REVIEW"
    if str(row["workflow_state"].get("monitoring") or "").lower() in {
        "open", "escalated"
    }:
        return "REVIEW"
    if expected["application_status"] == "approved":
        return "APPROVE"
    return "REVIEW"


def _canonical_dimension_rating(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "MEDIUM"
    if score < 1.5:
        return "LOW"
    if score < 2.5:
        return "MEDIUM"
    if score < 3.5:
        return "HIGH"
    return "VERY_HIGH"


def _canonical_memo_payload(
    row: Mapping[str, Any], *, risk_config_version: str
) -> Dict[str, Any]:
    """Build deterministic fixture evidence for the existing memo renderer.

    The payload narrates only reviewed manifest inputs and stored expected
    outcomes.  It does not invoke the production memo builder or create a new
    compliance conclusion.
    """

    inputs = row["risk_inputs"]
    expected = row["expected"]
    workflow = row["workflow_state"]
    dimensions = expected["dimensions"]
    decision = _canonical_memo_decision(row)
    decision_evidence = row.get("decision_evidence") or {}
    decision_source = (
        "explicit stored officer decision evidence"
        if decision_evidence
        else "stored canonical application and workflow state"
    )
    blocked = expected["memo_status"] == "blocked"
    escalations = list(expected.get("escalations") or [])
    supervisor_evidence = dict(row.get("supervisor_evidence") or {})
    supervisor = {
        **supervisor_evidence,
        "pilot_scope": "excluded_from_controlled_pilot",
        "evidence_retained": bool(supervisor_evidence),
    }

    screening_results = inputs.get("screening_results") or {}
    screening_summary = ", ".join(
        f"{name}: {(value or {}).get('status', 'not recorded')}"
        for name, value in sorted(screening_results.items())
    ) or f"workflow state: {workflow['screening']}"
    ubo_names = [
        str(person.get("full_name") or "").strip()
        for person in inputs.get("ubos") or []
        if str(person.get("full_name") or "").strip()
    ]
    ownership_evidence = (
        f"Ownership structure: {inputs.get('ownership_structure') or 'not recorded'}. "
        + (
            "Recorded UBOs: " + ", ".join(ubo_names) + "."
            if ubo_names
            else "No fully identified UBO is recorded for this scenario."
        )
    )
    pep_roles = sorted({
        str((person.get("pep_declaration") or {}).get("pep_role_type") or "").strip()
        for person in list(inputs.get("directors") or []) + list(inputs.get("ubos") or [])
        if person.get("is_pep")
    } - {""})

    risk_sub_sections = {}
    for key, dimension_key, title in (
        ("jurisdiction_risk", "d1", "Jurisdiction Risk"),
        ("ownership_risk", "d2", "Ownership Risk"),
        ("transaction_risk", "d3", "Transaction Risk"),
        ("business_risk", "d4", "Business Risk"),
        ("financial_crime_risk", "d5", "Financial Crime Risk"),
    ):
        value = dimensions.get(dimension_key)
        risk_sub_sections[key] = {
            "title": title,
            "rating": _canonical_dimension_rating(value),
            "content": f"Stored runtime dimension {dimension_key.upper()} = {value}.",
        }

    red_flags = escalations[:]
    for family, state in sorted(workflow.items()):
        if state in {"failed", "missing", "pending", "open", "escalated"}:
            red_flags.append(f"{family.replace('_', ' ').title()}: {state}")
    if not red_flags:
        red_flags = ["No unresolved escalation is recorded in the canonical evidence."]
    mitigants = []
    if workflow["screening"] in {"clear", "cleared", "false_positive"}:
        mitigants.append(f"Stored synthetic screening disposition: {workflow['screening']}.")
    if workflow["documents"] == "complete":
        mitigants.append("Required canonical document evidence is recorded as complete.")
    if workflow["idv"] == "passed":
        mitigants.append("Canonical identity-verification evidence is recorded as passed.")
    if not mitigants:
        mitigants = ["No mitigating control is asserted beyond the stored workflow evidence."]

    sections = {
        "executive_summary": {
            "title": "Executive Summary",
            "content": (
                f"{row['company_name']} is the synthetic {row['purpose']} scenario. "
                f"Authoritative stored risk is {expected['score']:.1f} ({expected['tier']}); "
                f"the recorded workflow outcome is: {expected['outcome']}."
            ),
        },
        "client_overview": {
            "title": "Client Overview",
            "content": (
                f"Entity type: {inputs.get('entity_type') or 'not recorded'}. "
                f"Sector: {inputs.get('sector') or 'not recorded'}. "
                f"Incorporation country: {inputs.get('country') or 'missing'}. "
                f"Monthly volume: {inputs.get('monthly_volume') or 'not recorded'}."
            ),
        },
        "ownership_and_control": {
            "title": "Ownership and Control",
            "content": ownership_evidence,
            "structure_complexity": inputs.get("ownership_structure") or "Not recorded",
            "control_statement": "Synthetic canonical evidence; no live ownership conclusion.",
        },
        "risk_assessment": {
            "title": "Risk Assessment",
            "content": (
                f"Stored weighted score {expected['score']:.1f}, base tier {expected['base_tier']}, "
                f"final tier {expected['tier']}, lane {expected['lane']}, and approval route "
                f"{expected['approval_route']}."
            ),
            "sub_sections": risk_sub_sections,
        },
        "screening_results": {
            "title": "Screening Results",
            "content": f"Synthetic screening evidence — {screening_summary}.",
            "approval_blocked_reasons": [expected["outcome"]] if blocked else [],
        },
        "document_verification": {
            "title": "Document Verification",
            "content": (
                f"Canonical document state: {workflow['documents']}; "
                f"identity-verification state: {workflow['idv']}."
            ),
        },
        "ai_explainability": {
            "title": "Deterministic Risk Evidence",
            "content": (
                "This fixture memo is assembled deterministically from reviewed manifest and "
                "stored runtime evidence. AI Supervisor is excluded from the controlled pilot."
            ),
            "risk_increasing_factors": escalations,
            "risk_decreasing_factors": mitigants,
        },
        "red_flags_and_mitigants": {
            "title": "Red Flags and Mitigants",
            "red_flags": red_flags,
            "mitigants": mitigants,
            "approval_blockers": [expected["outcome"]] if blocked else [],
        },
        "compliance_decision": {
            "title": "Compliance Decision",
            "decision": decision,
            "content": (
                f"Canonical fixture disposition: {decision}. Stored route: "
                f"{expected['approval_route']}. Stored outcome: {expected['outcome']}. "
                f"Decision source: {decision_source}. "
                "This is synthetic demonstration evidence and does not replace officer judgment."
            ),
        },
        "ongoing_monitoring": {
            "title": "Ongoing Monitoring",
            "content": (
                f"Monitoring state: {workflow['monitoring']}; periodic-review state: "
                f"{workflow['periodic_review']}. No provider refresh is performed by the seeder."
            ),
        },
        "audit_and_governance": {
            "title": "Audit and Governance",
            "content": (
                f"Pilot Canonical Dataset {DATASET_VERSION}; manifest {manifest_sha256()}; "
                f"risk configuration {risk_config_version}; deterministic memo version 1. "
                "Synthetic, non-production fixture evidence."
            ),
        },
    }
    if expected["edd_required"]:
        sections["enhanced_review_edd"] = {
            "title": "Enhanced Due Diligence",
            "content": (
                f"EDD is recorded as required for this canonical scenario. Stored lane: "
                f"{expected['lane']}; stored outcome: {expected['outcome']}."
            ),
        }

    risk_calculated_at = _iso(row, offset=2)
    memo_generated_at = _iso(row, offset=7)
    return {
        "reference": f"{row['reference']}:MEMO",
        "application_ref": row["reference"],
        "application_id": row["application_id"],
        "dataset": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "manifest_sha256": manifest_sha256(),
        "synthetic": True,
        "non_production": True,
        "risk": expected,
        "workflow_state": workflow,
        "body": expected["outcome"],
        "scenario_evidence": row.get("scenario_evidence") or {},
        "supervisor": supervisor,
        "supervisor_evidence": supervisor_evidence,
        "evidence_export": row.get("evidence_export") or {},
        "memo_generated": memo_generated_at,
        "sections": sections,
        "metadata": {
            "application_ref": row["reference"],
            "authoritative": True,
            "has_authoritative_risk": True,
            "risk_rating": expected["tier"],
            "risk_score": expected["score"],
            "display_risk_rating": expected["tier"],
            "display_risk_score": expected["score"],
            "aggregated_risk": expected["tier"],
            "original_risk_level": expected["base_tier"],
            "canonical_risk": {
                "available": True,
                "level": expected["tier"],
                "score": expected["score"],
                "source": "applications.risk_score",
                "calculated_at": risk_calculated_at,
                "risk_config_version": risk_config_version,
            },
            "risk_calculated_at": risk_calculated_at,
            "risk_config_version": risk_config_version,
            "approval_recommendation": decision,
            "approval_route": expected["approval_route"],
            "edd_required": expected["edd_required"],
            "lane": expected["lane"],
            "escalations": escalations,
            "blocked": blocked,
            "block_reason": expected["outcome"] if blocked else None,
            "primary_blockers": [expected["outcome"]] if blocked else [],
            "memo_source": "pilot_canonical_fixture",
            "ai_source": "deterministic",
            "ai_supervisor_scope": "excluded_from_controlled_pilot",
            "memo_generated_at": memo_generated_at,
            "memo_version": 1,
        },
    }


def _upsert_memo(
    db, audit, row: Mapping[str, Any], *, risk_config_version: str
) -> Optional[int]:
    state = row["expected"]["memo_status"]
    if state == "none":
        return None
    reference = f"{row['reference']}:MEMO"
    existing = db.execute("SELECT id FROM compliance_memos WHERE application_id=? AND memo_data LIKE ?", (row["application_id"], f"%{reference}%")).fetchone()
    blocked = state == "blocked"
    memo = _canonical_memo_payload(row, risk_config_version=risk_config_version)
    review_status = "approved" if state == "approved" else "draft"
    validation_status = "pass" if state == "approved" else "pending"
    supervisor = row.get("supervisor_evidence") or {}
    supervisor_status = supervisor.get("verdict") or (
        "approved" if state == "approved" else "pending"
    )
    ai_recommendation = supervisor.get("recommendation") or "fixture"
    approved = state == "approved"
    values = (
        row["application_id"], 1, _json(memo), ai_recommendation, review_status,
        validation_status, supervisor_status, supervisor.get("reasoning"),
        _json(supervisor.get("contradictions") or []),
        "pass" if approved else "pending", blocked,
        row["expected"]["outcome"] if blocked else None,
        "sco001" if approved else None, _iso(row, offset=8) if approved else None,
        supervisor.get("officer_review") or (row["expected"]["outcome"] if approved else None),
        _iso(row, offset=7),
    )
    columns = (
        "application_id,version,memo_data,ai_recommendation,review_status,validation_status,"
        "supervisor_status,supervisor_summary,supervisor_contradictions,rule_engine_status,"
        "blocked,block_reason,approved_by,approved_at,approval_reason,created_at"
    )
    if existing:
        assignments = ",".join(f"{name}=?" for name in columns.split(","))
        db.execute(f"UPDATE compliance_memos SET {assignments} WHERE id=?", (*values, existing["id"]))
        memo_id = existing["id"]
    else:
        memo_id = _insert_returning_id(db, "compliance_memos", columns, values)
    audit(action="pilot_canonical_memo", target=f"compliance_memo:{memo_id}", detail=f"Converged canonical memo for {row['reference']}", after_state={"state": state, "blocked": blocked})
    return memo_id


def _seed_one(
    db, audit, row: Mapping[str, Any], *, risk_config_version: str,
    risk_config: Mapping[str, Any]
) -> Dict[str, Any]:
    app_id = _upsert_application(
        db, audit, row, risk_config_version=risk_config_version,
        risk_config=risk_config,
    )
    parties = _upsert_people(db, audit, row)
    documents = _upsert_documents(db, audit, row)
    alert_id = _upsert_monitoring(db, audit, row)
    screening_review_id = _upsert_screening_review(db, row)
    review_id = _upsert_periodic_review(db, audit, row, alert_id)
    edd_id = _upsert_edd(db, audit, row, alert_id, review_id)
    edd_finding_id = _upsert_edd_findings(db, audit, row, edd_id)
    correction = _upsert_correction_workflow(db, audit, row)
    memo_id = _upsert_memo(
        db, audit, row, risk_config_version=risk_config_version
    )
    decision_id = _upsert_decision_record(db, audit, row)
    return {
        "reference": row["reference"],
        "application_id": app_id,
        "company_name": row["company_name"],
        "score": row["expected"]["score"],
        "tier": row["expected"]["tier"],
        "workflow": row["expected"]["lane"],
        "document_ids": documents,
        "alert_id": alert_id,
        "screening_review_id": screening_review_id,
        "periodic_review_id": review_id,
        "edd_id": edd_id,
        "edd_finding_id": edd_finding_id,
        "memo_id": memo_id,
        "decision_id": decision_id,
        **correction,
        **parties,
    }


def _rollback(db) -> None:
    try:
        db.conn.rollback()
    except Exception as exc:  # pragma: no cover - defensive parity with fixture seeder
        logger.warning("pilot canonical rollback raised: %s", exc)


def seed_pilot_canonical_dataset(
    *,
    dry_run: bool,
    references: Optional[Sequence[str]] = None,
    validate_runtime: bool = True,
) -> List[Dict[str, Any]]:
    """Converge selected canonical scenarios in one commit or rollback.

    The database schema must already be initialised.  Deliberately omitting an
    ``init_db`` call prevents this dataset operation from running migrations.
    """
    manifest = load_manifest()
    validate_manifest(manifest)
    rows = select_scenarios(references)
    config = load_risk_config()
    if validate_runtime:
        validate_runtime_alignment(manifest=manifest, config=config)
    if not config:
        raise PilotDatasetValidationError("risk_config is required before canonical seeding")
    risk_config_version = str(config.get("_config_version") or "").strip()
    if not risk_config_version:
        raise PilotDatasetValidationError("risk_config version is missing")

    db = get_db()
    audit = make_fixture_audit_writer(db)
    results: List[Dict[str, Any]] = []
    try:
        _preflight_references(db, rows)
        for row in rows:
            results.append(_seed_one(
                db, audit, row, risk_config_version=risk_config_version,
                risk_config=config,
            ))
        if dry_run:
            _rollback(db)
        else:
            audit(
                action="pilot_canonical_apply_complete",
                target="dataset:pilot-canonical-v1",
                detail=f"Applied {len(results)} canonical scenarios at manifest {manifest_sha256()}",
                after_state={"references": [row["reference"] for row in rows]},
            )
            db.commit()
    except Exception:
        _rollback(db)
        raise
    finally:
        db.close()
    return results
