"""Regression contract for Pilot Canonical Dataset v1."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

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
    REQUIRED_CONFIRM_TOKEN,
    _enforce_apply_gates,
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
        for table in (
            "compliance_memos", "edd_cases", "periodic_reviews", "monitoring_alerts",
            "screening_reviews", "documents", "intermediaries", "ubos", "directors",
        ):
            connection.execute(f"DELETE FROM {table} WHERE application_id=?", (app_id,))
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
    assert result["scenario_count"] == EXPECTED_SCENARIO_COUNT == 38
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
        "scenario_count": 38,
        "synthetic": True,
        "version": "v1",
        "visible_in_back_office": True,
    }
    assert [row["reference"] for row in manifest["scenarios"]] == [
        f"RM-PILOT-{number:03d}" for number in range(1, 39)
    ]
    assert [row["application_id"] for row in manifest["scenarios"]] == [
        f"pcdv1{number:011d}" for number in range(1, 39)
    ]


def test_founder_document_pins_hash_and_every_permanent_reference():
    document = (ROOT / "docs" / "pilot" / "PILOT_CANONICAL_DATASET.md").read_text(
        encoding="utf-8"
    )
    assert manifest_sha256() in document
    for number in range(1, 39):
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
    assert result["scenario_count"] == 38


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
    assert len(results) == 38
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
                    "screening_reviews", "audit_log",
                )
            }
            second = seed_pilot_canonical_dataset(dry_run=False)
            second_counts = {table: _count(temp_db, table) for table in first_counts}
        assert [item["reference"] for item in first] == [item["reference"] for item in second]
        assert first_counts == second_counts
        assert _count(temp_db, "applications", "ref LIKE 'RM-PILOT-%'") == 38

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
