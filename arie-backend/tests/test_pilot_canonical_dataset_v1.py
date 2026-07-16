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
from fixtures.pilot_canonical import (
    EXPECTED_SCENARIO_COUNT,
    REQUIRED_COVERAGE,
    PilotDatasetValidationError,
    load_manifest,
    manifest_sha256,
    stable_evidence,
    validate_manifest,
    validate_runtime_alignment,
)
from fixtures.pilot_canonical_cli import (
    REQUIRED_CLEANUP_CONFIRM_TOKEN,
    REQUIRED_CONFIRM_TOKEN,
    _enforce_apply_gates,
    _enforce_cleanup_gates,
)
from fixtures.pilot_canonical_seeder import (
    PilotDatasetReferenceCollision,
    seed_pilot_canonical_dataset,
)


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


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
    assert "not seeded" in document.lower()
    assert "No pilot-readiness or production-readiness claim" in document


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
            assert archive.read(memo_pdf).startswith(b"%PDF")
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
