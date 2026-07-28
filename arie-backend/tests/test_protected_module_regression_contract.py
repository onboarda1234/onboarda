"""Contracts for the permanent protected-module regression baseline."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = BACKEND_ROOT / "tests" / "protected_module_regression_manifest.json"
CANONICAL_PATH = BACKEND_ROOT / "fixtures" / "pilot_canonical_dataset_v1.json"
RUNNER_PATH = (
    BACKEND_ROOT / "scripts" / "qa" / "run_protected_module_regression.py"
)
PLUGIN_PATH = (
    BACKEND_ROOT / "scripts" / "qa" / "protected_regression_pytest_plugin.py"
)

EXPECTED_CASES = {
    "low_risk_complete_kyc",
    "high_risk_enhanced_controls",
    "verified_kyc_documents",
    "screening_hits",
    "cleared_screening",
    "four_eyes_screening_review",
    "edd_case",
    "change_management_request",
    "authoritative_rsmp_evidence",
    "monitoring_alert_unchanged",
}

REQUIRED_GUARDS = {
    "applications_kyc": {
        "tests/test_application_review_audit_fixes_static.py",
        "tests/test_application_detail_perf_index.py",
        "tests/test_approval_ux_gates_static.py",
        "tests/test_pr5_memo_governance.py",
        "tests/test_memo_staleness_hard_gate.py",
        "tests/test_dual_approval_race.py",
    },
    "screening": {
        "tests/test_screening_queue.py",
        "tests/test_screening_queue_state_integrity.py",
        "tests/test_seed_screening_qa_fixtures.py",
        "tests/test_fixture_exclusion.py",
        "tests/test_inline_screening_runtime.py",
        "tests/test_backoffice_ca_truthflow_static.py",
        "tests/test_provider_label_policy.py",
        "tests/test_declared_pep_truthfulness_priority_a2.py",
        "tests/test_srp2_refresh_stale_screening_reports.py",
        "tests/test_srp2_batch1_regressions.py",
        "tests/test_screening_review.py",
    },
    "rsmp": {
        "tests/test_rsmp_tier0a_dry_run.py",
        "tests/test_rsmp_tier0a_mapping_fidelity.py",
        "tests/test_rsmp_tier0b_fail_closed_routing.py",
        "tests/test_rsmp_tier0c_multiservice_max.py",
        "tests/test_rsmp_pr1b_pep_runtime_alignment.py",
        "tests/test_authoritative_risk_pdf_explainability.py",
        "tests/test_pilot_canonical_postgresql.py",
    },
    "change_management_edd": {
        "tests/test_change_management.py",
        "tests/test_cm_approval_preconditions.py",
        "tests/test_edd_completion_recognition.py",
        "tests/test_edd_memo_integration.py",
        "tests/test_lifecycle_linkage.py",
    },
}

EXPECTED_GROUPS = {
    "applications_kyc": [
        "tests/test_application_review_audit_fixes_static.py",
        "tests/test_application_detail_perf_index.py",
        "tests/test_approval_ux_gates_static.py",
        "tests/test_pr5_memo_governance.py",
        "tests/test_memo_staleness_hard_gate.py",
        "tests/test_dual_approval_race.py",
        "tests/test_application.py",
        "tests/test_application_role_matrix.py",
        "tests/test_application_role_ui_authz_static.py",
        "tests/test_approval_gate.py",
        "tests/test_portal_to_approval_e2e.py",
        "tests/test_fail_closed_decision_persistence.py",
        "tests/test_decision_path_integrity_priority_b.py",
        "tests/test_evidence_pack_risk_pdf.py",
        "tests/test_pr20_memo_blocked_persistence.py",
        "tests/test_p0_backend_document_link_regressions.py",
        "tests/test_p0_kyc_party_document_integrity.py",
        "tests/test_kyc_1a_sumsub_idv_visibility.py",
        "tests/test_kyc_docs_action_stability_static.py",
        "tests/test_ex11_ai_advisory_labels.py",
        "tests/test_document_versioning.py",
        "tests/test_document_download_safety.py",
        "tests/test_document_reliance_gate.py",
        "tests/test_r10_portal_ownership.py",
        "tests/test_authenticated_staging_browser_smoke.py",
        "tests/test_protected_module_regression_contract.py",
        "tests/test_protected_module_staging_smoke_contract.py",
    ],
    "screening": [
        "tests/test_screening_queue.py",
        "tests/test_screening_queue_state_integrity.py",
        "tests/test_seed_screening_qa_fixtures.py",
        "tests/test_fixture_exclusion.py",
        "tests/test_inline_screening_runtime.py",
        "tests/test_backoffice_ca_truthflow_static.py",
        "tests/test_provider_label_policy.py",
        "tests/test_declared_pep_truthfulness_priority_a2.py",
        "tests/test_srp2_refresh_stale_screening_reports.py",
        "tests/test_srp2_batch1_regressions.py",
        "tests/test_screening_review.py",
        "tests/test_screening_state_priority_a.py",
        "tests/test_srp2a_rescreen_existing_customer.py",
        "tests/test_ca_webhook_idempotency.py",
        "tests/test_complyadvantage_webhook_storage.py",
        "tests/test_screening_freshness.py",
        "tests/test_pilot_data_segmentation_fixture_hiding.py",
        "tests/test_fixture_false_exclusion_bound.py",
        "tests/test_screening_truthflow_fix_batch.py",
        "tests/test_screening_clearance_validation_supervisor.py",
        "tests/test_srp4_phase_c_agent3_triage_narrative.py",
    ],
    "rsmp": [
        "tests/test_rsmp_tier0a_dry_run.py",
        "tests/test_rsmp_tier0a_mapping_fidelity.py",
        "tests/test_rsmp_tier0b_fail_closed_routing.py",
        "tests/test_rsmp_tier0c_multiservice_max.py",
        "tests/test_rsmp_pr1b_pep_runtime_alignment.py",
        "tests/test_authoritative_risk_pdf_explainability.py",
        "tests/test_rsmp_tier0d_runtime_ui_alignment.py",
        "tests/test_pilot_canonical_dataset_v1.py",
        "tests/test_pilot_canonical_postgresql.py",
        "tests/test_risk_config_fail_closed.py",
        "tests/test_risk_config_integrity.py",
        "tests/test_risk_config_shape.py",
    ],
    "change_management_edd": [
        "tests/test_change_management.py",
        "tests/test_cm_approval_preconditions.py",
        "tests/test_cm_audit_reconstruction.py",
        "tests/test_cm_implement_propagation.py",
        "tests/test_cm_rbac_portal.py",
        "tests/test_cm_risk_screening_before_implementation.py",
        "tests/test_edd_completion_recognition.py",
        "tests/test_edd_memo_integration.py",
        "tests/test_edd_actuation_fk_safety.py",
        "tests/test_lifecycle_linkage.py",
        "tests/test_monitoring_routing.py",
        "tests/test_monitoring_refresh_status_decoupling.py",
        "tests/test_monitoring_status_model.py",
    ],
}


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_has_named_groups_and_required_existing_guards():
    groups = _manifest()["groups"]
    assert groups == EXPECTED_GROUPS, "Protected inventory changed without review"
    all_paths = [path for paths in groups.values() for path in paths]
    assert len(all_paths) == len(set(all_paths)), "A protected test must not run twice"
    for group, required in REQUIRED_GUARDS.items():
        assert required <= set(groups[group])
    for relative in all_paths:
        assert (BACKEND_ROOT / relative).is_file(), relative


def test_runner_always_executes_contracts_and_fail_closed_pytest_policy():
    runner = RUNNER_PATH.read_text(encoding="utf-8")
    plugin = PLUGIN_PATH.read_text(encoding="utf-8")
    assert "CONTRACT_TESTS" in runner
    assert "tests/test_protected_module_regression_contract.py" in runner
    assert "tests/test_protected_module_staging_smoke_contract.py" in runner
    assert "protected_regression_pytest_plugin" in runner
    assert "pytest_collection_finish" in plugin
    assert "requested - collected" in plugin
    assert "report.skipped" in plugin
    assert "wasxfail" in plugin
    assert "pytest.ExitCode.TESTS_FAILED" in plugin


def test_runner_rejects_pytest_arguments_that_can_weaken_execution():
    forbidden_arguments = (
        ("--collect-only", "-q"),
        ("--co",),
        ("-k", "screening"),
        ("-m", "not slow"),
        ("--ignore=tests/test_screening_queue.py",),
        ("--deselect=tests/test_screening_queue.py",),
        ("--maxfail=1",),
        ("-p", "no:scripts.qa.protected_regression_pytest_plugin"),
        ("--override-ini=addopts=--collect-only",),
        ("tests/test_screening_queue.py",),
    )
    for arguments in forbidden_arguments:
        result = subprocess.run(
            [sys.executable, str(RUNNER_PATH), "--", *arguments],
            cwd=BACKEND_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 2, arguments
        assert "Refused pytest argument" in result.stderr


def test_runner_accepts_only_benign_pytest_presentation_arguments():
    from scripts.qa.run_protected_module_regression import (
        validated_pytest_args,
    )

    accepted = [
        "-q",
        "-vv",
        "--tb=short",
        "--color=no",
        "--durations=10",
        "--durations-min=0.25",
        "--showlocals",
        "--no-header",
        "--no-summary",
    ]
    assert validated_pytest_args(["--", *accepted]) == accepted


def test_runner_rejects_pytest_environment_execution_controls():
    forbidden_environment = {
        "PYTEST_ADDOPTS": "--collect-only",
        "PYTEST_PLUGINS": "scripts.qa.protected_regression_pytest_plugin",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    for name, value in forbidden_environment.items():
        env = dict(os.environ)
        env[name] = value
        result = subprocess.run(
            [sys.executable, str(RUNNER_PATH), "--list"],
            cwd=BACKEND_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 2, name
        assert name in result.stderr


def test_runner_forces_empty_configured_addopts(monkeypatch):
    from scripts.qa import run_protected_module_regression as runner

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return type("Completed", (), {"returncode": 0})()

    for name in runner.FORBIDDEN_PYTEST_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(RUNNER_PATH), "--group", "rsmp", "--", "--tb=short"],
    )
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    assert runner.main() == 0
    command = captured["command"]
    assert command[command.index("-o") + 1] == "addopts="
    assert command[command.index("-p") + 1] == runner.PYTEST_PLUGIN


def test_fixture_catalog_is_explicit_deterministic_hidden_and_non_mutating():
    fixtures = _manifest()["canonical_smoke_fixtures"]
    assert len(fixtures) == 10
    assert {fixture["case"] for fixture in fixtures} == EXPECTED_CASES
    for fixture in fixtures:
        assert fixture["reference"]
        assert fixture["application_id"]
        assert fixture["explicit_markers"].get("is_fixture") is True
        assert fixture["default_officer_visibility"] is False
        assert fixture["deterministic"] is True
        assert fixture["safe_to_recreate"] is True
        assert fixture["staging_write_allowed"] is False
        assert "name" not in fixture["explicit_markers"]
        assert "company_name" not in fixture["explicit_markers"]


def test_pilot_fixture_identifiers_match_frozen_canonical_manifest():
    canonical = json.loads(CANONICAL_PATH.read_text(encoding="utf-8"))
    by_ref = {
        scenario["reference"]: scenario["application_id"]
        for scenario in canonical["scenarios"]
    }
    fixtures = _manifest()["canonical_smoke_fixtures"]
    pilot_fixtures = [
        fixture
        for fixture in fixtures
        if fixture["source"] == "pilot_canonical_dataset_v1"
    ]
    assert pilot_fixtures
    for fixture in pilot_fixtures:
        assert by_ref[fixture["reference"]] == fixture["application_id"]


def test_four_eyes_fixture_matches_the_governed_seeder():
    import seed_screening_qa_fixtures as screening_fixtures

    fixture = next(
        item
        for item in _manifest()["canonical_smoke_fixtures"]
        if item["case"] == "four_eyes_screening_review"
    )
    seeded = next(
        item
        for item in screening_fixtures.FIXTURES
        if item["ref"] == fixture["reference"]
    )
    assert seeded["id"] == fixture["application_id"]
    assert seeded["review"]["requires_four_eyes"] is True
    assert seeded["review"]["disposition"] == "cleared"


def test_local_change_management_fixture_uses_real_workflow_and_default_filter(
    temp_db, monkeypatch
):
    """The only new mutable fixture stays in the disposable test database.

    It uses the real Change Management service and canonical fixture filter;
    only ID generation is fixed so the fixture is safe and reproducible.
    """
    import change_management as cm
    import db as db_module
    from fixture_filter import fixture_app_id_exclude_clause

    fixture = next(
        item
        for item in _manifest()["canonical_smoke_fixtures"]
        if item["case"] == "change_management_request"
    )
    connection = db_module.get_db()
    try:
        client_id = "protected-baseline-client"
        connection.execute(
            """
            INSERT INTO clients (id, email, password_hash, company_name)
            VALUES (?, ?, ?, ?)
            """,
            (
                client_id,
                "protected-baseline@fixtures.invalid",
                "not-a-login-credential",
                "Protected Baseline Client",
            ),
        )
        connection.execute(
            """
            INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, is_fixture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fixture["application_id"],
                fixture["reference"],
                client_id,
                "Protected Baseline CM Ltd",
                "GB",
                "Technology",
                "Limited Company",
                "approved",
                1,
            ),
        )
        connection.commit()

        monkeypatch.setattr(
            cm, "generate_change_request_id", lambda: fixture["request_id"]
        )
        monkeypatch.setattr(
            cm,
            "generate_profile_version_id",
            lambda: "PV-PROTECTED-BASELINE-001",
        )
        request = cm.create_change_request(
            db=connection,
            application_id=fixture["application_id"],
            source="backoffice_manual",
            source_channel="backoffice",
            reason="Protected baseline local fixture",
            items=[
                {
                    "change_type": "contact_detail_update",
                    "field_name": "contact_email",
                    "old_value": "old@fixtures.invalid",
                    "new_value": "new@fixtures.invalid",
                }
            ],
            user={"sub": "protected-reviewer", "name": "Reviewer", "role": "sco"},
        )
        assert request["id"] == fixture["request_id"]
        assert request["application_id"] == fixture["application_id"]
        assert request["source"] == "backoffice_manual"
        assert request["status"] == "draft"

        clause, params = fixture_app_id_exclude_clause(
            "application_id", include_text_patterns=True
        )
        hidden = cm.list_change_requests(
            connection,
            fixture_filter_sql=clause,
            fixture_filter_params=params,
        )
        visible = cm.list_change_requests(connection)
        assert fixture["request_id"] not in {row["id"] for row in hidden}
        assert fixture["request_id"] in {row["id"] for row in visible}
    finally:
        connection.close()
