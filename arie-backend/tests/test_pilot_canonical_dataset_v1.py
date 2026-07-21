"""Regression contract for Pilot Canonical Dataset v1."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

import environment
import risk_controlled_values
from security_hardening import _policy_approval_route
from fixtures.pilot_canonical import (
    EXPECTED_SCENARIO_COUNT,
    REQUIRED_COVERAGE,
    PilotDatasetValidationError,
    load_manifest,
    manifest_sha256,
    stable_evidence,
    validate_manifest,
    validate_runtime_alignment,
    validate_tier0c_b_approval_routes,
)
from fixtures.pilot_canonical_cli import (
    REQUIRED_CLEANUP_CONFIRM_TOKEN,
    REQUIRED_CONFIRM_TOKEN,
    _enforce_apply_gates,
    _enforce_cleanup_gates,
)
from fixtures.pilot_canonical_seeder import (
    APPROVED_MANIFEST_LINEAGE,
    PilotDatasetReferenceCollision,
    seed_pilot_canonical_dataset,
)


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
MANDATORY_MEMO_SECTIONS = {
    "executive_summary",
    "client_overview",
    "ownership_and_control",
    "risk_assessment",
    "screening_results",
    "document_verification",
    "ai_explainability",
    "red_flags_and_mitigants",
    "compliance_decision",
    "ongoing_monitoring",
    "audit_and_governance",
}


@contextmanager
def _tier0_contract_enabled(monkeypatch):
    flag_name = risk_controlled_values.ACTIVATION_FLAG
    managers = {id(manager): manager for manager in (environment.flags, risk_controlled_values.flags)}
    snapshots = {key: dict(manager._cache) for key, manager in managers.items()}
    monkeypatch.setenv(flag_name, "true")
    try:
        for manager in managers.values():
            manager._cache[flag_name] = True
        yield
    finally:
        for key, manager in managers.items():
            manager._cache.clear()
            manager._cache.update(snapshots[key])


def _cleanup(path: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    app_ids = [
        row[0]
        for row in connection.execute(
            "SELECT id FROM applications WHERE ref LIKE 'RM-PILOT-%'"
        ).fetchall()
    ]
    for app_id in app_ids:
        connection.execute(
            "DELETE FROM rmi_request_items WHERE request_id IN "
            "(SELECT id FROM rmi_requests WHERE application_id=?)", (app_id,)
        )
        connection.execute("DELETE FROM rmi_requests WHERE application_id=?", (app_id,))
        connection.execute("DELETE FROM application_corrections WHERE application_id=?", (app_id,))
        connection.execute(
            "DELETE FROM edd_findings WHERE edd_case_id IN "
            "(SELECT id FROM edd_cases WHERE application_id=?)", (app_id,)
        )
        for table in (
            "compliance_memos", "edd_cases", "periodic_reviews", "monitoring_alerts",
            "screening_reviews", "documents", "intermediaries", "ubos", "directors",
        ):
            connection.execute(f"DELETE FROM {table} WHERE application_id=?", (app_id,))
        connection.execute(
            "DELETE FROM decision_records WHERE application_ref IN "
            "(SELECT ref FROM applications WHERE id=?)", (app_id,)
        )
        connection.execute("DELETE FROM applications WHERE id=?", (app_id,))
    connection.execute("DELETE FROM audit_log WHERE action LIKE 'fixture.pilot_canonical_%'")
    connection.commit()
    connection.close()


def _count(path: str, table: str, where: str = "1=1") -> int:
    connection = sqlite3.connect(path)
    value = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
    connection.close()
    return int(value)


def test_manifest_is_stable_complete_and_tagged():
    manifest = load_manifest()
    result = validate_manifest(manifest)
    assert result["scenario_count"] == EXPECTED_SCENARIO_COUNT == 41
    assert len(manifest_sha256()) == 64
    assert manifest["dataset"] == {
        "deterministic_epoch": "2026-07-01T00:00:00+00:00",
        "fixture": True,
        "generated_from": "rule_engine.compute_risk_score with code-seeded risk_config",
        "name": "Pilot Canonical Dataset",
        "non_production": True,
        "purpose": "Clean deterministic reusable non-production staging dataset",
        "reference_prefix": "RM-PILOT-",
        "risk_model_contract": "approved_rsmp_tier0_flag_on",
        "scenario_count": 41,
        "synthetic": True,
        "version": "v1",
        "visible_in_back_office": True,
    }
    assert [row["reference"] for row in manifest["scenarios"]] == [
        f"RM-PILOT-{number:03d}" for number in range(1, 42)
    ]
    assert [row["application_id"] for row in manifest["scenarios"]] == [
        f"pcdv1{number:011d}" for number in range(1, 42)
    ]


def test_founder_document_pins_hash_and_every_permanent_reference():
    document = (ROOT / "docs" / "pilot" / "PILOT_CANONICAL_DATASET.md").read_text(
        encoding="utf-8"
    )
    assert manifest_sha256() in document
    for number in range(1, 42):
        assert f"RM-PILOT-{number:03d}" in document
    assert "41 permanent records are present on AWS staging" in document
    assert "AI Supervisor is not enabled or validated for this pilot" in document
    assert "No AI Supervisor, pilot-readiness or production-readiness claim" in document


def test_every_required_workflow_is_represented():
    rows = load_manifest()["scenarios"]
    covered = {value for row in rows for value in row["coverage"]}
    assert REQUIRED_COVERAGE <= covered
    assert {row["category"] for row in rows} == {"LOW", "MEDIUM", "HIGH", "EDD", "NEGATIVE"}
    assert {row["workflow_state"]["periodic_review"] for row in rows} >= {"open", "completed"}
    assert {row["workflow_state"]["monitoring"] for row in rows} >= {
        "open", "false_positive", "escalated", "cleared"
    }


def test_policy_sensitive_scenarios_are_internally_consistent():
    by_ref = {row["reference"]: row for row in load_manifest()["scenarios"]}
    assert by_ref["RM-PILOT-014"]["risk_inputs"]["sector"] == "Private Banking"
    assert "floor_rule_high_risk_sector" in by_ref["RM-PILOT-014"]["expected"]["escalations"]

    pep_roles = {
        "RM-PILOT-015": "Domestic PEP",
        "RM-PILOT-016": "Foreign PEP",
        "RM-PILOT-017": "International Organisation PEP",
        "RM-PILOT-018": "Family Member",
        "RM-PILOT-019": "Close Associate",
    }
    for reference, role in pep_roles.items():
        row = by_ref[reference]
        assert row["risk_inputs"]["directors"][0]["pep_declaration"]["pep_role_type"] == role
        assert row["expected"]["score"] == 55.0
        assert "floor_rule_declared_pep" in row["expected"]["escalations"]

    volume = by_ref["RM-PILOT-012"]["expected"]
    assert volume["tier"] == "MEDIUM"
    assert volume["approval_route"] == "compliance_required"
    assert "monthly_volume_score_4" in volume["escalations"]
    assert not any(reason.startswith("floor_rule_high_risk") for reason in volume["escalations"])

    for reference in ("RM-PILOT-033", "RM-PILOT-034", "RM-PILOT-035"):
        assert by_ref[reference]["expected"]["approval_route"] == "blocked"
        assert any(
            reason.startswith("stale:unmapped_")
            for reason in by_ref[reference]["expected"]["escalations"]
        )


def test_manifest_matches_real_runtime_contract(temp_db, monkeypatch):
    with _tier0_contract_enabled(monkeypatch):
        result = validate_runtime_alignment()
    assert result["aligned"] is True
    assert result["scenario_count"] == 41


def test_runtime_validator_never_activates_flag(temp_db, monkeypatch):
    flag_name = risk_controlled_values.ACTIVATION_FLAG
    monkeypatch.setenv(flag_name, "false")
    environment.flags._cache[flag_name] = False
    risk_controlled_values.flags._cache[flag_name] = False
    with pytest.raises(PilotDatasetValidationError, match="will not activate"):
        validate_runtime_alignment()
    assert risk_controlled_values.mapping_fidelity_enabled() is False


def test_tier0c_b_validator_separates_policy_route_from_decision_eligibility():
    manifest = {
        "scenarios": [
            {"reference": "RM-PILOT-A", "expected": {
                "approval_route": "compliance_required", "application_status": "approved",
            }},
            {"reference": "RM-PILOT-B", "expected": {
                "approval_route": "compliance_required", "application_status": "compliance_review",
            }},
            {"reference": "RM-PILOT-C", "expected": {
                "approval_route": "blocked", "application_status": "compliance_review",
            }},
            {"reference": "RM-PILOT-D", "expected": {
                "approval_route": "rejected", "application_status": "rejected",
            }},
        ]
    }
    applications = [
        {
            "id": "a", "ref": "RM-PILOT-A", "status": "approved",
            "risk_level": "MEDIUM", "final_risk_level": "MEDIUM",
            "risk_escalations": "[]",
            "prescreening_data": json.dumps({"screening_report": {"status": "clear"}}),
        },
        {
            "id": "b", "ref": "RM-PILOT-B", "status": "compliance_review",
            "risk_level": "MEDIUM", "final_risk_level": "MEDIUM",
            "risk_escalations": "[]",
            "prescreening_data": json.dumps({"screening_report": {"status": "clear"}}),
        },
        {
            "id": "c", "ref": "RM-PILOT-C", "status": "compliance_review",
            "risk_level": "LOW", "final_risk_level": "LOW",
            "risk_escalations": "[]",
            "prescreening_data": json.dumps(
                {"screening_report": {"screening_state": "pending"}}
            ),
        },
        {
            "id": "d", "ref": "RM-PILOT-D", "status": "rejected",
            "risk_level": "LOW", "final_risk_level": "LOW",
            "risk_escalations": "[]",
            "prescreening_data": json.dumps({"screening_report": {"status": "clear"}}),
        },
    ]

    result = validate_tier0c_b_approval_routes(
        applications, manifest=manifest
    )
    by_ref = {row["reference"]: row for row in result["results"]}

    assert result["approval_routes_valid"] is True
    assert result["decision_eligibility_valid"] is True
    assert by_ref["RM-PILOT-A"] == {
        "reference": "RM-PILOT-A",
        "approval_route": "compliance_required",
        "decision_eligibility": "blocked",
        "eligibility_reason": "terminal_state",
        "effective_route": "blocked",
    }
    assert by_ref["RM-PILOT-B"]["decision_eligibility"] == "eligible"
    assert by_ref["RM-PILOT-C"]["eligibility_reason"] == "screening_pending_or_unresolved"
    assert by_ref["RM-PILOT-D"]["approval_route"] == "rejected"


def test_tier0c_b_validator_rejects_policy_route_mismatch():
    manifest = {
        "scenarios": [{
            "reference": "RM-PILOT-X",
            "expected": {
                "approval_route": "direct_low_medium",
                "application_status": "compliance_review",
            },
        }]
    }
    application = {
        "id": "x", "ref": "RM-PILOT-X", "status": "compliance_review",
        "risk_level": "MEDIUM", "final_risk_level": "MEDIUM",
        "risk_escalations": "[]",
        "prescreening_data": json.dumps({"screening_report": {"status": "clear"}}),
    }

    with pytest.raises(PilotDatasetValidationError, match="approval_route"):
        validate_tier0c_b_approval_routes([application], manifest=manifest)


def test_dry_run_rolls_back_every_record(temp_db, monkeypatch):
    _cleanup(temp_db)
    with _tier0_contract_enabled(monkeypatch):
        results = seed_pilot_canonical_dataset(dry_run=True)
    assert len(results) == 41
    assert _count(temp_db, "applications", "ref LIKE 'RM-PILOT-%'") == 0
    assert _count(temp_db, "audit_log", "action LIKE 'fixture.pilot_canonical_%'") == 0


def test_apply_is_idempotent_and_preserves_authoritative_evidence(temp_db, monkeypatch):
    _cleanup(temp_db)
    try:
        with _tier0_contract_enabled(monkeypatch):
            first = seed_pilot_canonical_dataset(dry_run=False)
            first_counts = {
                table: _count(temp_db, table)
                for table in (
                    "applications", "directors", "ubos", "intermediaries", "documents",
                    "monitoring_alerts", "periodic_reviews", "edd_cases", "compliance_memos",
                    "screening_reviews", "application_corrections", "rmi_requests",
                    "rmi_request_items", "edd_findings", "decision_records", "audit_log",
                )
            }
            second = seed_pilot_canonical_dataset(dry_run=False)
            second_counts = {table: _count(temp_db, table) for table in first_counts}
        assert [item["reference"] for item in first] == [item["reference"] for item in second]
        assert first_counts == second_counts
        assert _count(temp_db, "applications", "ref LIKE 'RM-PILOT-%'") == 41

        connection = sqlite3.connect(temp_db)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT ref,is_fixture,prescreening_data,risk_score,risk_level,"
            "risk_dimensions,risk_escalations,risk_config_version FROM applications "
            "WHERE ref LIKE 'RM-PILOT-%' ORDER BY ref"
        ).fetchall()
        connection.close()
        manifest = {row["reference"]: row for row in load_manifest()["scenarios"]}
        for stored in rows:
            identity = json.loads(stored["prescreening_data"])
            expected = manifest[stored["ref"]]["expected"]
            assert bool(stored["is_fixture"]) is True
            assert identity["dataset_name"] == "Pilot Canonical Dataset"
            assert identity["synthetic"] is True
            assert identity["non_production"] is True
            assert identity["fixture"] is True
            assert identity["visible_in_back_office"] is True
            assert stored["risk_score"] == expected["score"]
            assert stored["risk_level"] == expected["tier"]
            assert json.loads(stored["risk_escalations"]) == expected["escalations"]
            evidence = json.loads(stored["risk_dimensions"])
            assert evidence["expected_approval_route"] == expected["approval_route"]
            assert stable_evidence(evidence["controlled_mapping_evidence"]) == expected["controlled_mapping_evidence"]
            assert {
                item["config_version"] for item in evidence["controlled_mapping_evidence"]
            } == {stored["risk_config_version"]}
    finally:
        _cleanup(temp_db)


def test_seeded_memos_satisfy_renderer_contract_and_preserve_authoritative_risk(
    temp_db, monkeypatch
):
    from server import _memo_risk_snapshot_mismatch

    _cleanup(temp_db)
    try:
        with _tier0_contract_enabled(monkeypatch):
            seed_pilot_canonical_dataset(dry_run=False)

        connection = sqlite3.connect(temp_db)
        connection.row_factory = sqlite3.Row
        applications = {
            row["ref"]: row
            for row in connection.execute(
                "SELECT * FROM applications WHERE ref LIKE 'RM-PILOT-%' ORDER BY ref"
            ).fetchall()
        }
        memo_rows = connection.execute(
            "SELECT cm.*,a.ref,a.risk_score,a.risk_level,a.risk_config_version "
            "FROM compliance_memos cm JOIN applications a ON a.id=cm.application_id "
            "WHERE a.ref LIKE 'RM-PILOT-%' ORDER BY a.ref"
        ).fetchall()
        assert len(memo_rows) == 38
        before = {}
        manifest = {row["reference"]: row for row in load_manifest()["scenarios"]}
        for stored in memo_rows:
            data = json.loads(stored["memo_data"])
            before[stored["ref"]] = stored["memo_data"]
            assert MANDATORY_MEMO_SECTIONS <= set(data["sections"])
            assert data["metadata"]["risk_score"] == stored["risk_score"]
            assert data["metadata"]["risk_rating"] == stored["risk_level"]
            assert data["metadata"]["risk_config_version"] == stored["risk_config_version"]
            assert data["metadata"]["authoritative"] is True
            assert data["metadata"]["ai_supervisor_scope"] == "excluded_from_controlled_pilot"
            assert data["manifest_sha256"] == manifest_sha256()
            assert _memo_risk_snapshot_mismatch(
                applications[stored["ref"]], stored
            ) is None
            retained = manifest[stored["ref"]].get("supervisor_evidence") or {}
            for key, value in retained.items():
                assert data["supervisor"][key] == value
                assert data["supervisor_evidence"][key] == value

        connection.close()
        with _tier0_contract_enabled(monkeypatch):
            seed_pilot_canonical_dataset(dry_run=False)
        connection = sqlite3.connect(temp_db)
        after = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT a.ref,cm.memo_data FROM compliance_memos cm "
                "JOIN applications a ON a.id=cm.application_id "
                "WHERE a.ref LIKE 'RM-PILOT-%' ORDER BY a.ref"
            ).fetchall()
        }
        connection.close()
        assert after == before
    finally:
        _cleanup(temp_db)


def test_seeded_memo_decisions_are_supported_by_stored_scenario_evidence(
    temp_db, monkeypatch
):
    _cleanup(temp_db)
    try:
        with _tier0_contract_enabled(monkeypatch):
            seed_pilot_canonical_dataset(dry_run=False)
        connection = sqlite3.connect(temp_db)
        decisions = {}
        for ref, raw_memo in connection.execute(
            "SELECT a.ref,cm.memo_data FROM compliance_memos cm "
            "JOIN applications a ON a.id=cm.application_id "
            "WHERE a.ref IN ('RM-PILOT-001','RM-PILOT-017','RM-PILOT-024',"
            "'RM-PILOT-025','RM-PILOT-038','RM-PILOT-040')"
        ).fetchall():
            memo = json.loads(raw_memo)
            decisions[ref] = memo["sections"]["compliance_decision"]
        connection.close()

        assert decisions["RM-PILOT-001"]["decision"] == "APPROVE"
        assert decisions["RM-PILOT-017"]["decision"] == "REVIEW"
        assert decisions["RM-PILOT-024"]["decision"] == "REVIEW"
        assert decisions["RM-PILOT-025"]["decision"] == "REVIEW"
        assert decisions["RM-PILOT-038"]["decision"] == "REJECT"
        assert (
            decisions["RM-PILOT-040"]["decision"]
            == "APPROVE_WITH_ENHANCED_MONITORING"
        )
        assert "explicit stored officer decision evidence" in decisions[
            "RM-PILOT-040"
        ]["content"]
    finally:
        _cleanup(temp_db)


def test_periodic_review_fixtures_have_dates_priority_and_auditable_suppression(
    temp_db, monkeypatch
):
    _cleanup(temp_db)
    try:
        with _tier0_contract_enabled(monkeypatch):
            seed_pilot_canonical_dataset(dry_run=False)
        connection = sqlite3.connect(temp_db)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT a.ref,pr.status,pr.last_review_date,pr.next_review_date,pr.due_date,pr.priority "
            "FROM periodic_reviews pr JOIN applications a ON a.id=pr.application_id "
            "WHERE a.ref LIKE 'RM-PILOT-%' ORDER BY a.ref"
        ).fetchall()
        assert [row["ref"] for row in rows] == [
            "RM-PILOT-005", "RM-PILOT-008", "RM-PILOT-014", "RM-PILOT-041"
        ]
        expected_priority = {
            "RM-PILOT-005": "low",
            "RM-PILOT-008": "normal",
            "RM-PILOT-014": "high",
            "RM-PILOT-041": "low",
        }
        for row in rows:
            assert row["last_review_date"]
            assert row["next_review_date"]
            assert row["due_date"] == row["next_review_date"]
            assert row["priority"] == expected_priority[row["ref"]]
        audit_rows = connection.execute(
            "SELECT after_state FROM audit_log "
            "WHERE action='fixture.pilot_canonical_notification_suppressed'"
        ).fetchall()
        assert len(audit_rows) == 4
        for audit_row in audit_rows:
            evidence = json.loads(audit_row["after_state"])
            assert evidence == {
                "enforcement_key": "applications.is_fixture",
                "notification_suppressed": True,
                "notification_suppression_reason": "fixture_application",
            }
        connection.close()
    finally:
        _cleanup(temp_db)


def test_nonfixture_reference_collision_fails_before_writes(temp_db, monkeypatch):
    _cleanup(temp_db)
    connection = sqlite3.connect(temp_db)
    connection.execute(
        "INSERT INTO applications (id,ref,company_name,status,is_fixture) VALUES (?,?,?,?,?)",
        ("nonfixture-collision", "RM-PILOT-001", "Do Not Touch Ltd", "draft", False),
    )
    connection.commit()
    connection.close()
    try:
        with _tier0_contract_enabled(monkeypatch):
            with pytest.raises(PilotDatasetReferenceCollision, match="non-fixture"):
                seed_pilot_canonical_dataset(dry_run=True)
        assert _count(temp_db, "applications", "ref LIKE 'RM-PILOT-%'") == 1
    finally:
        _cleanup(temp_db)


def _mutate_canonical_identity(path: str, reference: str, **changes) -> None:
    connection = sqlite3.connect(path)
    raw = connection.execute(
        "SELECT prescreening_data FROM applications WHERE ref=?", (reference,)
    ).fetchone()[0]
    identity = json.loads(raw)
    identity.update(changes)
    connection.execute(
        "UPDATE applications SET prescreening_data=? WHERE ref=?",
        (json.dumps(identity, sort_keys=True), reference),
    )
    connection.commit()
    connection.close()


def test_reviewed_manifest_lineage_converges_same_deterministic_dataset(
    temp_db, monkeypatch
):
    _cleanup(temp_db)
    previous_version, previous_hash = next(
        identity
        for identity, successor in APPROVED_MANIFEST_LINEAGE.items()
        if successor == ("v1", manifest_sha256())
    )
    try:
        with _tier0_contract_enabled(monkeypatch):
            seed_pilot_canonical_dataset(
                dry_run=False, references=["RM-PILOT-001"]
            )
            _mutate_canonical_identity(
                temp_db,
                "RM-PILOT-001",
                dataset_version=previous_version,
                dataset_hash=previous_hash,
            )
            seed_pilot_canonical_dataset(
                dry_run=False, references=["RM-PILOT-001"]
            )

        connection = sqlite3.connect(temp_db)
        raw = connection.execute(
            "SELECT prescreening_data FROM applications WHERE ref='RM-PILOT-001'"
        ).fetchone()[0]
        connection.close()
        assert json.loads(raw)["dataset_hash"] == manifest_sha256()
    finally:
        _cleanup(temp_db)


@pytest.mark.parametrize(
    "changes",
    [
        {"dataset_version": "foreign-v1"},
        {"source": "fixtures.some_other_dataset"},
        {"dataset_name": "Another Canonical Dataset"},
        {"synthetic": False},
        {"dataset_hash": "0" * 64},
    ],
    ids=[
        "different-dataset-lineage",
        "different-fixture-source",
        "different-dataset-name",
        "different-synthetic-marker",
        "unknown-manifest",
    ],
)
def test_foreign_canonical_identity_still_fails_closed(
    temp_db, monkeypatch, changes
):
    _cleanup(temp_db)
    try:
        with _tier0_contract_enabled(monkeypatch):
            seed_pilot_canonical_dataset(
                dry_run=False, references=["RM-PILOT-001"]
            )
            _mutate_canonical_identity(temp_db, "RM-PILOT-001", **changes)
            with pytest.raises(
                PilotDatasetReferenceCollision, match="another fixture identity"
            ):
                seed_pilot_canonical_dataset(
                    dry_run=True, references=["RM-PILOT-001"]
                )
    finally:
        _cleanup(temp_db)


@pytest.mark.parametrize(
    ("stored_id", "stored_reference"),
    [
        ("different-application-id", "RM-PILOT-001"),
        ("pcdv100000000001", "RM-PILOT-999"),
    ],
)
def test_deterministic_id_or_reference_difference_fails_closed(
    temp_db, monkeypatch, stored_id, stored_reference
):
    _cleanup(temp_db)
    connection = sqlite3.connect(temp_db)
    connection.execute(
        "INSERT INTO applications "
        "(id,ref,company_name,status,is_fixture,prescreening_data) "
        "VALUES (?,?,?,?,?,?)",
        (
            stored_id,
            stored_reference,
            "Foreign Fixture",
            "draft",
            True,
            json.dumps({"fixture": True, "synthetic": True}),
        ),
    )
    connection.commit()
    connection.close()
    try:
        with _tier0_contract_enabled(monkeypatch):
            with pytest.raises(PilotDatasetReferenceCollision):
                seed_pilot_canonical_dataset(
                    dry_run=True, references=["RM-PILOT-001"]
                )
    finally:
        _cleanup(temp_db)


@pytest.mark.parametrize(
    ("environment_value", "allow", "confirm", "reviewed_hash", "message"),
    [
        ("development", "1", REQUIRED_CONFIRM_TOKEN, "valid", "ENVIRONMENT=staging"),
        ("staging", "0", REQUIRED_CONFIRM_TOKEN, "valid", "ALLOW_PILOT_CANONICAL_SEED=1"),
        ("staging", "1", "wrong", "valid", "--confirm"),
        ("staging", "1", REQUIRED_CONFIRM_TOKEN, "wrong", "reviewed-hash"),
    ],
)
def test_apply_is_triple_gated(monkeypatch, environment_value, allow, confirm, reviewed_hash, message):
    monkeypatch.setenv("ENVIRONMENT", environment_value)
    monkeypatch.setenv("ALLOW_PILOT_CANONICAL_SEED", allow)
    digest = manifest_sha256() if reviewed_hash == "valid" else reviewed_hash
    with pytest.raises(SystemExit, match=message):
        _enforce_apply_gates(confirm=confirm, reviewed_hash=digest)


def test_apply_gate_accepts_only_exact_reviewed_staging_contract(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("ALLOW_PILOT_CANONICAL_SEED", "1")
    _enforce_apply_gates(
        confirm=REQUIRED_CONFIRM_TOKEN,
        reviewed_hash=manifest_sha256(),
    )


def test_founder_refinements_are_explicit_and_not_labels_only():
    by_ref = {row["reference"]: row for row in load_manifest()["scenarios"]}

    cash = by_ref["RM-PILOT-020"]
    assert cash["risk_inputs"]["sector"] == "MSB / Money Services Business"
    assert "cash" in cash["risk_inputs"]["source_of_funds"].lower()
    assert cash["expected"]["service_selection_evidence"]["resolution_status"] == "resolved"
    assert not any(
        reason.startswith("stale:unmapped_") for reason in cash["expected"]["escalations"]
    )

    trust = by_ref["RM-PILOT-028"]
    assert {
        "trust_deed", "trustee_identification", "settlor_declaration",
        "beneficiary_register", "trust_relationship_chart",
    } <= {item["type"] for item in trust["evidence_documents"]}
    assert set(trust["scenario_evidence"]["trust_relationship"]) >= {
        "trustee", "settlor", "beneficiaries", "relationship",
    }

    sow = by_ref["RM-PILOT-029"]
    assert {
        "source_of_wealth_declaration", "audited_financial_statements",
        "bank_statements", "supporting_transaction_evidence",
    } <= {item["type"] for item in sow["evidence_documents"]}
    assert sow["scenario_evidence"]["officer_review"]["disposition"]

    for reference in ("RM-PILOT-008", "RM-PILOT-009"):
        medium = by_ref[reference]["expected"]
        assert medium["tier"] == "MEDIUM"
        assert medium["score"] == 42.1
        assert {
            key: medium["dimensions"][key]
            for key in ("d1", "d2", "d3", "d4", "d5")
        } == {"d1": 2.0, "d2": 1.25, "d3": 3.25, "d4": 3, "d5": 2.5}

    for reference in (
        "RM-PILOT-006", "RM-PILOT-007", "RM-PILOT-008", "RM-PILOT-009",
        "RM-PILOT-010", "RM-PILOT-011", "RM-PILOT-013", "RM-PILOT-039",
        "RM-PILOT-040",
    ):
        medium = by_ref[reference]["expected"]
        assert medium["tier"] == "MEDIUM"
        assert medium["approval_route"] == "compliance_required"
        # The route is the MEDIUM-risk policy outcome even with no escalation
        # reasons; generic score-4 evidence is not its policy basis.
        assert _policy_approval_route(medium["tier"], []) == medium["approval_route"]

    combined = by_ref["RM-PILOT-026"]["expected"]
    assert combined["tier"] == "VERY_HIGH"
    assert combined["score"] == 70.0
    assert {key: combined["dimensions"][key] for key in ("d1", "d2", "d3", "d4", "d5")} == {
        "d1": 2.25, "d2": 2.2, "d3": 3.6, "d4": 4, "d5": 1.0,
    }
    assert combined["approval_route"] == "dual_control_required"
    for reference in ("RM-PILOT-014", "RM-PILOT-025", "RM-PILOT-026"):
        assert by_ref[reference]["expected"]["approval_route"] == "dual_control_required"

    manual_review = by_ref["RM-PILOT-030"]
    manual_metadata = manual_review["scenario_evidence"][
        "manual_compliance_escalation"
    ]
    assert manual_review["expected"]["approval_route"] == "compliance_required"
    assert manual_review["expected"]["score"] == 42.1
    assert manual_review["expected"]["tier"] == "MEDIUM"
    assert {
        key: manual_review["expected"]["dimensions"][key]
        for key in ("d1", "d2", "d3", "d4", "d5")
    } == {"d1": 2.0, "d2": 1.25, "d3": 3.0, "d4": 3, "d5": 3.0}
    assert manual_metadata == {
        "origin_context": "manual_onboarding_escalation",
        "reason": "Officer submitted the application for manual Compliance review",
        "submitted_by": "co001",
        "trigger_source": "officer_submitted_to_compliance",
    }

    correction = by_ref["RM-PILOT-037"]
    assert correction["expected"]["application_status"] == "approved"
    assert correction["correction_workflow"]["steps"][-1] == "final_approval"
    assert correction["expected"]["approval_route"] == "direct_low_medium"

    assert by_ref["RM-PILOT-039"]["evidence_export"]["formats"] == ["CSV", "PDF"]
    assert by_ref["RM-PILOT-040"]["supervisor_evidence"]["reasoning"]
    assert by_ref["RM-PILOT-040"]["supervisor_evidence"]["officer_review"]
    assert "end_to_end_happy_path" in by_ref["RM-PILOT-041"]["coverage"]


def test_enriched_workflows_persist_as_real_backend_evidence(temp_db, monkeypatch):
    _cleanup(temp_db)
    try:
        with _tier0_contract_enabled(monkeypatch):
            seed_pilot_canonical_dataset(dry_run=False)

        connection = sqlite3.connect(temp_db)
        connection.row_factory = sqlite3.Row

        trust_types = {
            row["doc_type"] for row in connection.execute(
                "SELECT doc_type FROM documents WHERE application_id=?",
                ("pcdv100000000028",),
            ).fetchall()
        }
        assert "trust_deed" in trust_types
        assert "beneficiary_register" in trust_types
        trust_finding = connection.execute(
            "SELECT findings_summary, mitigating_evidence FROM edd_findings "
            "WHERE edd_case_id IN (SELECT id FROM edd_cases WHERE application_id=?)",
            ("pcdv100000000028",),
        ).fetchone()
        assert "trust" in trust_finding["findings_summary"].lower()
        assert "trust deed" in trust_finding["mitigating_evidence"].lower()

        sow_types = {
            row["doc_type"] for row in connection.execute(
                "SELECT doc_type FROM documents WHERE application_id=?",
                ("pcdv100000000029",),
            ).fetchall()
        }
        assert "source_of_wealth_declaration" in sow_types
        assert "bank_statements" in sow_types
        sow_edd = connection.execute(
            "SELECT trigger_source, origin_context FROM edd_cases WHERE application_id=?",
            ("pcdv100000000029",),
        ).fetchone()
        assert sow_edd["trigger_source"] == "officer_escalate_edd"
        assert sow_edd["origin_context"] == "manual_onboarding_escalation"

        manual_review = connection.execute(
            "SELECT status,submitted_to_compliance_at,submitted_to_compliance_by,"
            "prescreening_data FROM applications WHERE id=?",
            ("pcdv100000000030",),
        ).fetchone()
        stored_manual_metadata = json.loads(manual_review["prescreening_data"])[
            "scenario_evidence"
        ]["manual_compliance_escalation"]
        assert manual_review["status"] == "compliance_review"
        assert manual_review["submitted_to_compliance_at"]
        assert manual_review["submitted_to_compliance_by"] == "co001"
        assert stored_manual_metadata["trigger_source"] == "officer_submitted_to_compliance"
        assert stored_manual_metadata["origin_context"] == "manual_onboarding_escalation"

        request = connection.execute(
            "SELECT status, reason, fulfilled_at FROM rmi_requests WHERE application_id=?",
            ("pcdv100000000037",),
        ).fetchone()
        correction = connection.execute(
            "SELECT before_state, after_state, downstream_state FROM application_corrections "
            "WHERE application_id=?",
            ("pcdv100000000037",),
        ).fetchone()
        final_app = connection.execute(
            "SELECT status FROM applications WHERE id=?", ("pcdv100000000037",)
        ).fetchone()
        assert request["status"] == "fulfilled" and request["fulfilled_at"]
        assert "Pliot" in correction["before_state"]
        assert "approval_block_cleared" in correction["downstream_state"]
        assert final_app["status"] == "approved"
        correction_actions = {
            row["action"] for row in connection.execute(
                "SELECT action FROM audit_log WHERE target=?",
                ("application:pcdv100000000037",),
            ).fetchall()
        }
        assert {
            "fixture.pilot_canonical_correction_requested",
            "fixture.pilot_canonical_applicant_correction",
            "fixture.pilot_canonical_officer_correction",
            "fixture.pilot_canonical_final_disposition",
        } <= correction_actions

        supervisor = connection.execute(
            "SELECT ai_recommendation, supervisor_status, supervisor_summary, "
            "supervisor_contradictions, review_status, approval_reason "
            "FROM compliance_memos WHERE application_id=?",
            ("pcdv100000000040",),
        ).fetchone()
        assert supervisor["supervisor_status"] == "CONSISTENT_WITH_WARNINGS"
        assert supervisor["ai_recommendation"] == "APPROVE_WITH_ENHANCED_MONITORING"
        assert supervisor["supervisor_summary"]
        assert supervisor["review_status"] == "approved"
        assert supervisor["approval_reason"]

        happy_counts = {
            table: connection.execute(
                f"SELECT count(*) AS n FROM {table} WHERE application_id=?",
                ("pcdv100000000041",),
            ).fetchone()["n"]
            for table in (
                "documents", "screening_reviews", "monitoring_alerts",
                "periodic_reviews", "compliance_memos",
            )
        }
        assert all(value > 0 for value in happy_counts.values())
        assert connection.execute(
            "SELECT count(*) AS n FROM decision_records WHERE application_ref=?",
            ("RM-PILOT-041",),
        ).fetchone()["n"] == 1
        connection.close()
    finally:
        _cleanup(temp_db)


def test_evidence_export_scenario_generates_authoritative_pdf_and_csv(
    temp_db, monkeypatch
):
    _cleanup(temp_db)
    try:
        with _tier0_contract_enabled(monkeypatch):
            seed_pilot_canonical_dataset(dry_run=False)
        from db import get_db
        import evidence_pack_export
        from evidence_pack_export import build_evidence_pack_zip

        monkeypatch.setattr(evidence_pack_export, "weasyprint", None)
        db = get_db()
        try:
            app = db.execute(
                "SELECT * FROM applications WHERE ref=?", ("RM-PILOT-039",)
            ).fetchone()
            zip_bytes, metadata = build_evidence_pack_zip(
                db,
                dict(app),
                {
                    "export_type": "internal_case",
                    "reason": "Canonical evidence-export demonstration",
                    "redaction_level": "full_internal",
                    "include_sections": [
                        "client_submission", "risk_assessment", "screening_summary",
                        "compliance_memo", "audit_trail",
                    ],
                },
                {"sub": "sco001", "name": "Canonical SCO", "email": "fixture@invalid", "role": "sco"},
                exported_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
        finally:
            db.close()

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            names = archive.namelist()
            risk_pdf = next(name for name in names if name.endswith("03_risk_assessment.pdf"))
            screening_pdf = next(name for name in names if name.endswith("04_screening_summary.pdf"))
            memo_pdf = next(name for name in names if name.endswith("06_compliance_memo.pdf"))
            audit_csv = next(name for name in names if name.endswith("07_audit_trail.csv"))
            assert archive.read(risk_pdf).startswith(b"%PDF")
            assert archive.read(screening_pdf).startswith(b"%PDF")
            memo_bytes = archive.read(memo_pdf)
            assert memo_bytes.startswith(b"%PDF")
            assert b"Executive Summary" in memo_bytes
            assert b"Risk Assessment" in memo_bytes
            assert b"Compliance Decision" in memo_bytes
            assert b"Audit And Governance" in memo_bytes
            assert b"fixture.pilot_canonical" in archive.read(audit_csv)
        assert metadata["file_count"] >= 7
        assert not metadata["document_retrieval_failures"]
    finally:
        _cleanup(temp_db)


def test_cleanup_is_staging_only_marker_scoped_zero_residue_and_idempotent(
    temp_db, monkeypatch
):
    from db import get_db
    from fixtures.cleanup import FixtureCleanupDenied, cleanup_pilot_canonical_dataset
    from regulated_deletion import FIXTURE_CLEANUP_CONFIRMATION

    _cleanup(temp_db)
    try:
        with _tier0_contract_enabled(monkeypatch):
            seed_pilot_canonical_dataset(dry_run=False)

        monkeypatch.setenv("ENVIRONMENT", "production")
        db = get_db()
        try:
            with pytest.raises(FixtureCleanupDenied, match="only in staging"):
                cleanup_pilot_canonical_dataset(
                    db,
                    actor_id="pytest",
                    confirmation=FIXTURE_CLEANUP_CONFIRMATION,
                    reviewed_hash=manifest_sha256(),
                )
        finally:
            db.close()
        assert _count(temp_db, "applications", "ref LIKE 'RM-PILOT-%'") == 41

        monkeypatch.setenv("ENVIRONMENT", "staging")
        db = get_db()
        try:
            counts = cleanup_pilot_canonical_dataset(
                db,
                actor_id="pytest",
                confirmation=FIXTURE_CLEANUP_CONFIRMATION,
                reviewed_hash=manifest_sha256(),
            )
            second = cleanup_pilot_canonical_dataset(
                db,
                actor_id="pytest",
                confirmation=FIXTURE_CLEANUP_CONFIRMATION,
                reviewed_hash=manifest_sha256(),
            )
        finally:
            db.close()
        assert counts["applications"] == 41
        assert counts["application_corrections"] == 1
        assert counts["rmi_requests"] == 1
        assert all(value == 0 for value in second.values())
        assert _count(temp_db, "applications", "ref LIKE 'RM-PILOT-%'") == 0
        assert _count(
            temp_db, "audit_log", "action LIKE 'fixture.pilot_canonical_%'"
        ) == 0
        assert _count(
            temp_db, "rmi_request_items", "request_id LIKE 'pcdv1%:correction-request'"
        ) == 0
        for table in (
            "rmi_requests", "application_corrections", "compliance_memos", "edd_cases",
            "periodic_reviews", "screening_reviews", "monitoring_alerts",
        ):
            assert _count(temp_db, table, "application_id LIKE 'pcdv1%'") == 0
        assert _count(
            temp_db,
            "edd_findings",
            "created_by='fixture_seed' AND (findings_summary LIKE 'Trust roles%' "
            "OR findings_summary LIKE 'Declared wealth%')",
        ) == 0
        assert _count(
            temp_db, "decision_records", "application_ref LIKE 'RM-PILOT-%'"
        ) == 0
    finally:
        _cleanup(temp_db)


@pytest.mark.parametrize(
    ("environment_value", "allow", "confirm", "reviewed_hash", "message"),
    [
        ("production", "1", REQUIRED_CLEANUP_CONFIRM_TOKEN, "valid", "ENVIRONMENT=staging"),
        ("staging", "0", REQUIRED_CLEANUP_CONFIRM_TOKEN, "valid", "ALLOW_PILOT_CANONICAL_CLEANUP=1"),
        ("staging", "1", "wrong", "valid", "--confirm"),
        ("staging", "1", REQUIRED_CLEANUP_CONFIRM_TOKEN, "wrong", "reviewed-hash"),
    ],
)
def test_cleanup_cli_is_separately_gated(
    monkeypatch, environment_value, allow, confirm, reviewed_hash, message
):
    monkeypatch.setenv("ENVIRONMENT", environment_value)
    monkeypatch.setenv("ALLOW_PILOT_CANONICAL_CLEANUP", allow)
    digest = manifest_sha256() if reviewed_hash == "valid" else reviewed_hash
    with pytest.raises(SystemExit, match=message):
        _enforce_cleanup_gates(confirm=confirm, reviewed_hash=digest)


def test_new_fixture_code_has_no_schema_provider_or_activation_side_effects():
    paths = [
        BACKEND / "fixtures" / "pilot_canonical.py",
        BACKEND / "fixtures" / "pilot_canonical_seeder.py",
        BACKEND / "fixtures" / "pilot_canonical_cli.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "ALTER TABLE" not in source
    assert "CREATE TABLE" not in source
    assert "init_db(" not in source
    assert "recompute" not in source.lower().replace("recomputation", "")
    assert "send_email" not in source
    assert "provider_client" not in source
    assert "flags.set" not in source
    assert "_cache[" not in source
