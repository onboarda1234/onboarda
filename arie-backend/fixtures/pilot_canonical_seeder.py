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
    return all(data.get(key) == value for key, value in expected.items())


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
    row: Mapping[str, Any], *, risk_config_version: str
) -> tuple:
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
    }
    runtime_dimensions = _bind_runtime_config_version(expected["dimensions"], risk_config_version)
    risk_dimensions = {
        **runtime_dimensions,
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


def _upsert_application(db, audit, row: Mapping[str, Any], *, risk_config_version: str) -> str:
    values = _application_payload(row, risk_config_version=risk_config_version)
    existing = db.execute(
        "SELECT id FROM applications WHERE ref=?", (row["reference"],)
    ).fetchone()
    common_columns = (
        "ref, company_name, brn, country, sector, entity_type, ownership_structure, "
        "prescreening_data, risk_score, risk_level, risk_dimensions, onboarding_lane, "
        "status, assigned_to, submitted_at, decided_at, decision_by, decision_notes, "
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
    return [
        {"type": "passport", "verification": passport_verification, "review": passport_review},
        {"type": "certificate_of_incorporation", "verification": "verified", "review": "accepted"},
        {"type": "ownership_evidence", "verification": "verified", "review": "accepted"},
    ]


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
    existing = db.execute("SELECT id FROM periodic_reviews WHERE trigger_reason=?", (marker,)).fetchone()
    values = (
        row["application_id"], row["company_name"], expected["tier"], "pilot_canonical_fixture",
        marker, "pilot_canonical_dataset", alert_id, expected["base_tier"], expected["tier"],
        f"{DATASET_NAME} deterministic {state} review", status, _iso(row, offset=5),
        _iso(row, offset=7) if state == "completed" else None, "co001", expected["outcome"],
        expected["outcome"], DATASET_VERSION, 12, "canonical_dataset_fixture", _iso(row, offset=5),
    )
    columns = "application_id,client_name,risk_level,trigger_type,trigger_reason,trigger_source,linked_monitoring_alert_id,previous_risk_level,new_risk_level,review_memo,status,started_at,completed_at,assigned_officer,decision,decision_reason,policy_version,frequency_months,calculation_basis,created_at"
    if existing:
        assignments = ",".join(f"{name}=?" for name in columns.split(","))
        db.execute(f"UPDATE periodic_reviews SET {assignments} WHERE id=?", (*values, existing["id"]))
        review_id = existing["id"]
    else:
        review_id = _insert_returning_id(db, "periodic_reviews", columns, values)
    audit(action="pilot_canonical_periodic_review", target=f"periodic_review:{review_id}", detail=f"Converged canonical periodic review for {row['reference']}", after_state={"state": state})
    return review_id


def _upsert_edd(db, audit, row: Mapping[str, Any], alert_id: Optional[int], review_id: Optional[int]) -> Optional[int]:
    if not row["expected"]["edd_required"]:
        return None
    marker = f"{row['reference']}:EDD"
    existing = db.execute("SELECT id FROM edd_cases WHERE trigger_notes LIKE ?", (f"{marker}%",)).fetchone()
    stage = "analysis" if row["workflow_state"]["monitoring"] in {"escalated", "open"} else "information_gathering"
    notes = marker + " " + _json({"dataset": DATASET_NAME, "reference": row["reference"], "synthetic": True, "source_alert_id": alert_id, "source_review_id": review_id})
    values = (row["application_id"], row["company_name"], row["expected"]["tier"], row["expected"]["score"], stage, "co001", "pilot_canonical_fixture", notes, "pilot_canonical_dataset", alert_id, review_id, _iso(row, offset=5), _iso(row, offset=5), _iso(row, offset=6))
    columns = "application_id,client_name,risk_level,risk_score,stage,assigned_officer,trigger_source,trigger_notes,origin_context,linked_monitoring_alert_id,linked_periodic_review_id,assigned_at,triggered_at,updated_at"
    if existing:
        assignments = ",".join(f"{name}=?" for name in columns.split(","))
        db.execute(f"UPDATE edd_cases SET {assignments} WHERE id=?", (*values, existing["id"]))
        edd_id = existing["id"]
    else:
        edd_id = _insert_returning_id(db, "edd_cases", columns, values)
    audit(action="pilot_canonical_edd", target=f"edd_case:{edd_id}", detail=f"Converged canonical EDD state for {row['reference']}", after_state={"stage": stage})
    return edd_id


def _upsert_memo(db, audit, row: Mapping[str, Any]) -> Optional[int]:
    state = row["expected"]["memo_status"]
    if state == "none":
        return None
    reference = f"{row['reference']}:MEMO"
    existing = db.execute("SELECT id FROM compliance_memos WHERE application_id=? AND memo_data LIKE ?", (row["application_id"], f"%{reference}%")).fetchone()
    blocked = state == "blocked"
    memo = {
        "reference": reference,
        "dataset": DATASET_NAME,
        "synthetic": True,
        "non_production": True,
        "risk": row["expected"],
        "workflow_state": row["workflow_state"],
        "body": row["expected"]["outcome"],
    }
    review_status = "approved" if state == "approved" else "draft"
    validation_status = "pass" if state == "approved" else "pending"
    values = (row["application_id"], 1, _json(memo), "fixture", review_status, validation_status, "approved" if state == "approved" else "pending", "pass" if state == "approved" else "pending", blocked, row["expected"]["outcome"] if blocked else None, _iso(row, offset=7))
    columns = "application_id,version,memo_data,ai_recommendation,review_status,validation_status,supervisor_status,rule_engine_status,blocked,block_reason,created_at"
    if existing:
        assignments = ",".join(f"{name}=?" for name in columns.split(","))
        db.execute(f"UPDATE compliance_memos SET {assignments} WHERE id=?", (*values, existing["id"]))
        memo_id = existing["id"]
    else:
        memo_id = _insert_returning_id(db, "compliance_memos", columns, values)
    audit(action="pilot_canonical_memo", target=f"compliance_memo:{memo_id}", detail=f"Converged canonical memo for {row['reference']}", after_state={"state": state, "blocked": blocked})
    return memo_id


def _seed_one(db, audit, row: Mapping[str, Any], *, risk_config_version: str) -> Dict[str, Any]:
    app_id = _upsert_application(db, audit, row, risk_config_version=risk_config_version)
    parties = _upsert_people(db, audit, row)
    documents = _upsert_documents(db, audit, row)
    alert_id = _upsert_monitoring(db, audit, row)
    screening_review_id = _upsert_screening_review(db, row)
    review_id = _upsert_periodic_review(db, audit, row, alert_id)
    edd_id = _upsert_edd(db, audit, row, alert_id, review_id)
    memo_id = _upsert_memo(db, audit, row)
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
        "memo_id": memo_id,
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
            results.append(_seed_one(db, audit, row, risk_config_version=risk_config_version))
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
