from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from fixtures.document_renewal_staging import fixture_spec, manifest_sha256
import monitoring_document_renewal_staging_cleanup as staging_cleanup
from scripts.qa import staging_document_renewal_activation as control


SHA = "a" * 40
SOURCE_ARN = (
    "arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:1002"
)


def _source(*, flag_value=None):
    environment = [
        {"name": "ENVIRONMENT", "value": "staging"},
        {"name": "SERVICE_NAME", "value": "regmind-backend"},
        {"name": "GIT_SHA", "value": SHA},
        {"name": "IMAGE_TAG", "value": SHA},
        {"name": "BUILD_TIME", "value": "2026-08-03T07:55:00Z"},
        {"name": "UNCHANGED_SETTING", "value": "preserve-me"},
    ]
    if flag_value is not None:
        environment.append(
            {
                "name": "ENABLE_DOCUMENT_RENEWAL_AUTOMATION",
                "value": flag_value,
            }
        )
    return {
        "taskDefinitionArn": SOURCE_ARN,
        "family": "regmind-staging",
        "revision": 1002,
        "taskRoleArn": "arn:aws:iam::782913119880:role/regmind-staging-task-role",
        "executionRoleArn": "arn:aws:iam::782913119880:role/regmind-staging-execution-role",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "1024",
        "memory": "3072",
        "containerDefinitions": [
            {
                "name": "regmind-backend",
                "image": (
                    "782913119880.dkr.ecr.af-south-1.amazonaws.com/"
                    f"regmind-backend:{SHA}"
                ),
                "environment": environment,
                "secrets": [
                    {
                        "name": "DATABASE_URL",
                        "valueFrom": "arn:aws:secretsmanager:af-south-1:782913119880:secret:example",
                    }
                ],
                "essential": True,
            }
        ],
    }


def _window():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return (
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        (now + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def test_render_clones_exact_release_and_changes_only_reviewed_harness_values():
    issued_at, expires_at = _window()
    source = _source()
    rendered = control.render_activation_task_definition(
        source,
        sha=SHA,
        run_id="gh-12345-1",
        issued_at=issued_at,
        expires_at=expires_at,
        fixture_manifest_sha256=manifest_sha256(),
    )
    container = rendered["containerDefinitions"][0]
    environment = {item["name"]: item["value"] for item in container["environment"]}
    assert container["image"] == source["containerDefinitions"][0]["image"]
    assert container["secrets"] == source["containerDefinitions"][0]["secrets"]
    assert rendered["taskRoleArn"] == source["taskRoleArn"]
    assert rendered["executionRoleArn"] == source["executionRoleArn"]
    assert environment["UNCHANGED_SETTING"] == "preserve-me"
    assert environment["ENABLE_DOCUMENT_RENEWAL_AUTOMATION"] == "true"
    assert all(environment[name] == "false" for name in control.OTHER_MONITORING_FLAGS)
    assert environment[control.ORIGINAL_TASK_DEFINITION_ENV] == SOURCE_ARN
    assert "taskDefinitionArn" not in rendered
    assert {item["key"] for item in rendered["tags"]} == {
        "RegMindHarness",
        "HarnessRunId",
        "HarnessOriginalTaskDefinition",
        "HarnessExpiresAt",
        "HarnessReleaseSha",
    }


def test_render_refuses_any_preexisting_governed_flag_on():
    issued_at, expires_at = _window()
    with pytest.raises(control.ActivationControlError, match="must be OFF"):
        control.render_activation_task_definition(
            _source(flag_value="true"),
            sha=SHA,
            run_id="gh-12345-1",
            issued_at=issued_at,
            expires_at=expires_at,
            fixture_manifest_sha256=manifest_sha256(),
        )


def test_render_refuses_wrong_sha_manifest_and_expired_window():
    issued_at, expires_at = _window()
    with pytest.raises(control.ActivationControlError, match="deployed release"):
        control.render_activation_task_definition(
            _source(),
            sha="b" * 40,
            run_id="gh-12345-1",
            issued_at=issued_at,
            expires_at=expires_at,
            fixture_manifest_sha256=manifest_sha256(),
        )


def test_render_and_inspection_reject_same_sha_from_wrong_registry():
    issued_at, expires_at = _window()
    source = _source()
    source["containerDefinitions"][0]["image"] = f"evil.invalid/backend:{SHA}"
    with pytest.raises(control.ActivationControlError, match="deployed release"):
        control.render_activation_task_definition(
            source,
            sha=SHA,
            run_id="gh-12345-1",
            issued_at=issued_at,
            expires_at=expires_at,
            fixture_manifest_sha256=manifest_sha256(),
        )
    with pytest.raises(control.ActivationControlError, match="provenance"):
        control.inspect_task_definition(
            source,
            expected_sha=SHA,
            expected_mode="off",
        )
    with pytest.raises(control.ActivationControlError, match="reviewed repository"):
        control.render_activation_task_definition(
            _source(),
            sha=SHA,
            run_id="gh-12345-1",
            issued_at=issued_at,
            expires_at=expires_at,
            fixture_manifest_sha256="b" * 64,
        )
    with pytest.raises(Exception):
        control.render_activation_task_definition(
            _source(),
            sha=SHA,
            run_id="gh-12345-1",
            issued_at="2026-01-01T00:00:00Z",
            expires_at="2026-01-01T00:10:00Z",
            fixture_manifest_sha256=manifest_sha256(),
        )


def test_off_inspection_rejects_harness_residue_and_active_inspection_is_safe():
    off = _source(flag_value="false")
    off_result = control.inspect_task_definition(
        off,
        expected_sha=SHA,
        expected_mode="off",
    )
    assert off_result["effective_renewal_enabled"] is False
    assert set(off_result["governed_flags"].values()) == {"OFF"}

    issued_at, expires_at = _window()
    active = control.render_activation_task_definition(
        _source(),
        sha=SHA,
        run_id="gh-12345-1",
        issued_at=issued_at,
        expires_at=expires_at,
        fixture_manifest_sha256=manifest_sha256(),
    )
    active["taskDefinitionArn"] = SOURCE_ARN.replace(":1002", ":1003")
    active_result = control.inspect_task_definition(
        active,
        expected_sha=SHA,
        expected_mode="active",
        expected_run_id="gh-12345-1",
    )
    assert active_result["effective_renewal_enabled"] is True
    assert active_result["governed_flags"][control.RENEWAL_FLAG] == "ON"
    assert all(
        active_result["governed_flags"][name] == "OFF"
        for name in control.OTHER_MONITORING_FLAGS
    )
    active_without_persisted_tags = dict(active)
    active_without_persisted_tags["tags"] = []
    with pytest.raises(
        control.ActivationControlError,
        match="recovery metadata",
    ):
        control.inspect_task_definition(
            active_without_persisted_tags,
            expected_sha=SHA,
            expected_mode="active",
            expected_run_id="gh-12345-1",
        )


@pytest.mark.parametrize("residue", sorted(control.HARNESS_ENVIRONMENT_NAMES))
def test_off_inspection_rejects_every_harness_environment_residue(residue):
    source = _source(flag_value="false")
    source["containerDefinitions"][0]["environment"].append(
        {"name": residue, "value": "orphaned"}
    )
    with pytest.raises(control.ActivationControlError, match="not fully OFF"):
        control.inspect_task_definition(
            source,
            expected_sha=SHA,
            expected_mode="off",
        )


def test_recovery_requires_exact_tags_and_never_restores_an_active_lease():
    issued_at, expires_at = _window()
    active = control.render_activation_task_definition(
        _source(),
        sha=SHA,
        run_id="gh-12345-1",
        issued_at=issued_at,
        expires_at=expires_at,
        fixture_manifest_sha256=manifest_sha256(),
    )
    active["taskDefinitionArn"] = SOURCE_ARN.replace(":1002", ":1003")
    candidate = control.recovery_candidate(active)
    assert candidate["action"] == "wait"
    assert candidate["wait_seconds"] > 0
    assert candidate["original_task_definition"] == SOURCE_ARN
    assert candidate["run_id"] == "gh-12345-1"

    active["tags"][0]["value"] = "drifted"
    with pytest.raises(control.ActivationControlError, match="tags do not match"):
        control.recovery_candidate(active)


def test_recovery_plan_restores_only_exact_expired_revision():
    issued_at, expires_at = _window()
    temporary = control.render_activation_task_definition(
        _source(),
        sha=SHA,
        run_id="gh-12345-1",
        issued_at=issued_at,
        expires_at=expires_at,
        fixture_manifest_sha256=manifest_sha256(),
    )
    temporary["taskDefinitionArn"] = SOURCE_ARN.replace(":1002", ":1003")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    past_issued = (now - timedelta(minutes=21)).strftime("%Y-%m-%dT%H:%M:%SZ")
    past_expiry = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    environment = temporary["containerDefinitions"][0]["environment"]
    for item in environment:
        if item["name"] == control.HARNESS_ISSUED_AT_ENV:
            item["value"] = past_issued
        if item["name"] == control.HARNESS_EXPIRES_AT_ENV:
            item["value"] = past_expiry
    for item in temporary["tags"]:
        if item["key"] == "HarnessExpiresAt":
            item["value"] = past_expiry

    candidate = control.recovery_candidate(temporary)
    assert candidate["action"] == "restore"
    plan = control.verify_recovery_plan(temporary, _source(flag_value="false"))
    assert plan["temporary_task_definition"] == temporary["taskDefinitionArn"]
    assert plan["original_task_definition"] == SOURCE_ARN


def test_inactive_residue_discovery_is_exact_expired_and_not_running():
    issued_at, expires_at = _window()
    candidate = control.render_activation_task_definition(
        _source(),
        sha=SHA,
        run_id="gh-12345-1",
        issued_at=issued_at,
        expires_at=expires_at,
        fixture_manifest_sha256=manifest_sha256(),
    )
    candidate["taskDefinitionArn"] = SOURCE_ARN.replace(":1002", ":1003")
    candidate["status"] = "ACTIVE"
    waiting = control.inactive_harness_residue(
        _source(flag_value="false"),
        [candidate],
        run_id="gh-12345-1",
        running_task_definition_arns=[],
    )
    assert waiting["action"] == "wait"

    now = datetime.now(timezone.utc).replace(microsecond=0)
    past_issued = (now - timedelta(minutes=21)).strftime("%Y-%m-%dT%H:%M:%SZ")
    past_expiry = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for item in candidate["containerDefinitions"][0]["environment"]:
        if item["name"] == control.HARNESS_ISSUED_AT_ENV:
            item["value"] = past_issued
        if item["name"] == control.HARNESS_EXPIRES_AT_ENV:
            item["value"] = past_expiry
    for item in candidate["tags"]:
        if item["key"] == "HarnessExpiresAt":
            item["value"] = past_expiry
    result = control.inactive_harness_residue(
        _source(flag_value="false"),
        [candidate],
        run_id="gh-12345-1",
        running_task_definition_arns=[],
    )
    assert result["action"] == "deregister"
    with pytest.raises(control.ActivationControlError, match="running task"):
        control.inactive_harness_residue(
            _source(flag_value="false"),
            [candidate],
            run_id="gh-12345-1",
            running_task_definition_arns=[candidate["taskDefinitionArn"]],
        )
    with pytest.raises(control.ActivationControlError, match="ambiguous"):
        control.inactive_harness_residue(
            _source(flag_value="false"),
            [candidate, dict(candidate)],
            run_id="gh-12345-1",
            running_task_definition_arns=[],
        )


def _expired_candidate(*, sha=SHA, run_id="gh-12345-1"):
    issued_at, expires_at = _window()
    source = _source()
    if sha != SHA:
        source["taskDefinitionArn"] = SOURCE_ARN.replace(":1002", ":2002")
        container = source["containerDefinitions"][0]
        container["image"] = (
            "782913119880.dkr.ecr.af-south-1.amazonaws.com/"
            f"regmind-backend:{sha}"
        )
        for item in container["environment"]:
            if item["name"] in {"GIT_SHA", "IMAGE_TAG"}:
                item["value"] = sha
    candidate = control.render_activation_task_definition(
        source,
        sha=sha,
        run_id=run_id,
        issued_at=issued_at,
        expires_at=expires_at,
        fixture_manifest_sha256=manifest_sha256(),
    )
    candidate["taskDefinitionArn"] = source["taskDefinitionArn"].replace(
        ":1002", ":1003"
    ).replace(":2002", ":2003")
    candidate["status"] = "ACTIVE"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    past_issued = (now - timedelta(minutes=21)).strftime("%Y-%m-%dT%H:%M:%SZ")
    past_expiry = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for item in candidate["containerDefinitions"][0]["environment"]:
        if item["name"] == control.HARNESS_ISSUED_AT_ENV:
            item["value"] = past_issued
        if item["name"] == control.HARNESS_EXPIRES_AT_ENV:
            item["value"] = past_expiry
    for item in candidate["tags"]:
        if item["key"] == "HarnessExpiresAt":
            item["value"] = past_expiry
    return source, candidate


def test_recovery_context_pins_old_lease_sha_when_current_main_advanced():
    old_source, candidate = _expired_candidate()
    newer_sha = "b" * 40
    current = _source()
    current["taskDefinitionArn"] = SOURCE_ARN.replace(":1002", ":2002")
    container = current["containerDefinitions"][0]
    container["image"] = (
        "782913119880.dkr.ecr.af-south-1.amazonaws.com/"
        f"regmind-backend:{newer_sha}"
    )
    for item in container["environment"]:
        if item["name"] in {"GIT_SHA", "IMAGE_TAG"}:
            item["value"] = newer_sha
    result = control.select_recovery_context(
        current,
        [candidate],
        requested_run_id="gh-12345-1",
        running_task_definition_arns=[],
    )
    assert result["action"] == "cleanup"
    assert result["service_action"] == "none"
    assert result["recovery_sha"] == SHA
    assert result["current_release_sha"] == newer_sha
    assert result["cleanup_task_definition"] == old_source["taskDefinitionArn"]


def test_recovery_context_handles_cancelled_mid_rollout_and_no_input_probe():
    _source_definition, candidate = _expired_candidate()
    result = control.select_recovery_context(
        candidate,
        [candidate],
        requested_run_id="gh-12345-1",
        running_task_definition_arns=[candidate["taskDefinitionArn"]],
    )
    assert result["action"] == "restore"
    assert result["service_action"] == "restore"
    assert result["running_temporary_tasks"] == 1

    probe = control.select_recovery_context(
        _source(flag_value="false"),
        [],
        requested_run_id=None,
        running_task_definition_arns=[],
    )
    assert probe["action"] == "clean_probe"
    assert probe["run_id"] is None
    assert probe["recovery_sha"] == SHA


def test_recovery_context_global_discovery_fails_closed_on_ambiguity_and_drift():
    _source_definition, candidate = _expired_candidate()
    other = json.loads(json.dumps(candidate))
    other["taskDefinitionArn"] = SOURCE_ARN.replace(":1002", ":1004")
    with pytest.raises(control.ActivationControlError, match="ambiguous"):
        control.select_recovery_context(
            _source(flag_value="false"),
            [candidate, other],
            requested_run_id=None,
            running_task_definition_arns=[],
        )
    with pytest.raises(control.ActivationControlError, match="does not own"):
        control.select_recovery_context(
            _source(flag_value="false"),
            [candidate],
            requested_run_id="gh-99999-1",
            running_task_definition_arns=[],
        )


def test_final_residue_proof_requires_zero_tags_and_zero_running_harness_tasks():
    _source_definition, candidate = _expired_candidate()
    with pytest.raises(control.ActivationControlError, match="tagged"):
        control.verify_recovery_residue_clear(
            _source(flag_value="false"),
            [candidate],
            running_task_definition_arns=[],
        )
    candidate["status"] = "INACTIVE"
    with pytest.raises(control.ActivationControlError, match="tagged"):
        control.verify_recovery_residue_clear(
            _source(flag_value="false"),
            [candidate],
            running_task_definition_arns=[],
        )
    result = control.verify_recovery_residue_clear(
        _source(flag_value="false"),
        [],
        running_task_definition_arns=[],
    )
    assert result["tagged_task_definitions"] == 0
    assert result["running_harness_tasks"] == 0
    with pytest.raises(control.ActivationControlError, match="running harness"):
        control.verify_recovery_residue_clear(
            _source(flag_value="false"),
            [],
            running_task_definition_arns=[candidate["taskDefinitionArn"]],
            running_task_definitions=[candidate],
        )
    with pytest.raises(control.ActivationControlError, match="incomplete"):
        control.verify_recovery_residue_clear(
            _source(flag_value="false"),
            [],
            running_task_definition_arns=[
                _source(flag_value="false")["taskDefinitionArn"]
            ],
            running_task_definitions=[],
        )
    ordinary = _source(flag_value="false")
    with pytest.raises(control.ActivationControlError, match="mismatched"):
        control.verify_recovery_residue_clear(
            ordinary,
            [],
            running_task_definition_arns=[ordinary["taskDefinitionArn"]],
            running_task_definitions=[
                {
                    **ordinary,
                    "taskDefinitionArn": ordinary["taskDefinitionArn"].replace(
                        ":1002", ":1003"
                    ),
                }
            ],
        )
    with pytest.raises(control.ActivationControlError, match="duplicate"):
        control.verify_recovery_residue_clear(
            ordinary,
            [],
            running_task_definition_arns=[ordinary["taskDefinitionArn"]],
            running_task_definitions=[ordinary, dict(ordinary)],
        )
    clean = control.verify_recovery_residue_clear(
        ordinary,
        [],
        running_task_definition_arns=[ordinary["taskDefinitionArn"]],
        running_task_definitions=[ordinary],
    )
    assert clean["running_harness_tasks"] == 0

    flag_on = _source(flag_value="true")
    with pytest.raises(control.ActivationControlError, match="fully-OFF"):
        control.verify_recovery_residue_clear(
            ordinary,
            [],
            running_task_definition_arns=[flag_on["taskDefinitionArn"]],
            running_task_definitions=[flag_on],
        )

    partial_harness = _source(flag_value="false")
    partial_harness["containerDefinitions"][0]["environment"].append(
        {"name": control.HARNESS_RUN_ID_ENV, "value": "gh-12345-1"}
    )
    with pytest.raises(control.ActivationControlError, match="running harness"):
        control.verify_recovery_residue_clear(
            ordinary,
            [],
            running_task_definition_arns=[partial_harness["taskDefinitionArn"]],
            running_task_definitions=[partial_harness],
        )


def _fingerprint(
    *,
    counts,
    actions,
    audit_count,
    chained,
    unchained,
    active,
    legacy_rows=staging_cleanup.STAGING_AUDIT_BASELINE_LEGACY_ROWS,
    coverage_gaps=staging_cleanup.STAGING_AUDIT_BASELINE_COVERAGE_GAPS,
    verified=True,
    broken_link_count=0,
    total_adjustment=0,
    coverage_complete=None,
):
    flags = {
        control.RENEWAL_FLAG: bool(active),
        **{name: False for name in control.OTHER_MONITORING_FLAGS},
    }
    relations = {
        name: {"row_count": count, "sha256": "a" * 64}
        for name, count in counts.items()
    }
    for name, count in {
        "fixture_client": 1,
        "fixture_application": 1,
        "fixture_person": 1,
        "fixture_document": 1,
        "fixture_monitoring_alert": 1,
        "fixture_application_directors": 1,
        "fixture_application_ubos": 0,
        "fixture_application_intermediaries": 0,
        "fixture_application_documents": 1,
        "fixture_application_monitoring_alerts": 1,
        "fixture_application_other_notifications": 0,
        "fixture_agent_executions": 0,
        "fixture_verification_jobs": 0,
        "fixture_legacy_requirements": 0,
        "other_harness_rate_limits": 0,
    }.items():
        relations.setdefault(name, {"row_count": count, "sha256": "c" * 64})
    relations["fixture_audit"] = {
        "row_count": audit_count,
        "sha256": "b" * 64,
    }
    spec = fixture_spec()
    global_chained = (
        staging_cleanup.STAGING_AUDIT_BASELINE_CHAINED_ROWS + audit_count
    )
    if coverage_complete is None:
        coverage_complete = coverage_gaps == 0
    return {
        "run_id": "gh-12345-1",
        "fixture": {
            key: spec[key]
            for key in (
                "fixture_key",
                "fixture_marker",
                "fixture_version",
                "manifest_sha256",
                "customer_id",
                "application_id",
                "application_ref",
                "person_id",
                "person_type",
                "document_id",
                "document_type",
                "document_version",
                "request_id",
            )
        }
        | {"alert_id": 1},
        "relations": relations,
        "core_fingerprint": "core",
        "operational_fingerprint": "operational",
        "isolation_fingerprint": "isolation",
        "scheduler_fingerprint": "scheduler",
        "audit_evidence_fingerprint": f"audit-{audit_count}",
        "fixture_audit_actions": dict(actions),
        "fixture_audit_chained_count": chained,
        "fixture_audit_unchained_count": unchained,
        "audit_chain": {
            "verified": verified,
            "entries_checked": global_chained,
            "chained_rows": global_chained,
            "legacy_rows": legacy_rows,
            "coverage_gaps": coverage_gaps,
            "coverage_complete": coverage_complete,
            "broken_link_count": broken_link_count,
            "total_entries": (
                global_chained
                + legacy_rows
                + coverage_gaps
                + total_adjustment
            ),
        },
        "feature_state": flags,
        "all_monitoring_flags_off": not active,
    }


def test_clean_recovery_fingerprint_rejects_any_governed_residue():
    clean = _fingerprint(
        counts={name: 0 for name in control.RECOVERY_OPERATIONAL_RELATIONS},
        actions={},
        audit_count=0,
        chained=0,
        unchained=0,
        active=False,
    )
    result = control.verify_clean_recovery_fingerprint(clean)
    assert result["ok"] is True
    assert all(result["checks"].values())

    dirty = json.loads(json.dumps(clean))
    dirty["relations"]["other_harness_rate_limits"]["row_count"] = 1
    with pytest.raises(control.ActivationControlError, match="not clean"):
        control.verify_clean_recovery_fingerprint(dirty)
    dirty = json.loads(json.dumps(clean))
    dirty["relations"]["renewal_requests"]["row_count"] = 1
    with pytest.raises(control.ActivationControlError, match="not clean"):
        control.verify_clean_recovery_fingerprint(dirty)


def test_clean_recovery_fingerprint_rejects_unchained_fixture_audit():
    fixture_gap = _fingerprint(
        counts={name: 0 for name in control.RECOVERY_OPERATIONAL_RELATIONS},
        actions={},
        audit_count=0,
        chained=0,
        unchained=1,
        active=False,
    )
    with pytest.raises(control.ActivationControlError, match="not clean"):
        control.verify_clean_recovery_fingerprint(fixture_gap)


@pytest.mark.parametrize(
    ("field", "value", "total_delta"),
    (
        ("verified", False, 0),
        ("broken_link_count", 1, 0),
        ("coverage_complete", True, 0),
        ("coverage_complete", None, 0),
        (
            "coverage_gaps",
            staging_cleanup.STAGING_AUDIT_BASELINE_COVERAGE_GAPS + 1,
            1,
        ),
        (
            "legacy_rows",
            staging_cleanup.STAGING_AUDIT_BASELINE_LEGACY_ROWS + 1,
            1,
        ),
        (
            "entries_checked",
            staging_cleanup.STAGING_AUDIT_BASELINE_CHAINED_ROWS + 1,
            0,
        ),
        (
            "total_entries",
            staging_cleanup.STAGING_AUDIT_BASELINE_TOTAL_ENTRIES + 1,
            0,
        ),
    ),
)
def test_clean_recovery_fingerprint_rejects_audit_baseline_drift(
    field, value, total_delta
):
    clean = _fingerprint(
        counts={name: 0 for name in control.RECOVERY_OPERATIONAL_RELATIONS},
        actions={},
        audit_count=0,
        chained=0,
        unchained=0,
        active=False,
    )
    clean["audit_chain"][field] = value
    clean["audit_chain"]["total_entries"] += total_delta
    with pytest.raises(control.ActivationControlError, match="not clean"):
        control.verify_clean_recovery_fingerprint(clean)


def test_fingerprint_reconciliation_requires_exact_audit_action_deltas():
    expected_counts = {
        "renewal_requests": 1,
        "renewal_events": 3,
        "renewal_uploads": 1,
        "renewal_upload_bindings": 1,
        "renewal_upload_cleanup": 1,
        "renewal_notifications": 1,
        "harness_rate_limit": 1,
    }
    prior = {"monitoring.document_renewal.fixture_cleanup": 2}
    before = _fingerprint(
        counts={name: 0 for name in expected_counts},
        actions=prior,
        audit_count=2,
        chained=2,
        unchained=0,
        active=False,
    )
    after_actions = dict(prior)
    for action, count in control.EXPECTED_VALIDATION_AUDIT_DELTA.items():
        after_actions[action] = after_actions.get(action, 0) + count
    validation_count = sum(control.EXPECTED_VALIDATION_AUDIT_DELTA.values())
    after = _fingerprint(
        counts=expected_counts,
        actions=after_actions,
        audit_count=2 + validation_count,
        chained=2 + validation_count,
        unchained=0,
        active=True,
    )
    final_actions = dict(after_actions)
    final_actions["monitoring.document_renewal.fixture_cleanup"] += 1
    final = _fingerprint(
        counts={name: 0 for name in expected_counts},
        actions=final_actions,
        audit_count=3 + validation_count,
        chained=3 + validation_count,
        unchained=0,
        active=False,
    )
    result = control.compare_fingerprints(
        before,
        after,
        {"cleanup": {"final_fingerprint": final}},
    )
    assert result["validation_audit_delta"] == (
        control.EXPECTED_VALIDATION_AUDIT_DELTA
    )
    assert result["cleanup_audit_delta"] == control.EXPECTED_CLEANUP_AUDIT_DELTA
    assert result["comparisons"]["audit_chain_growth_exact"] is True

    inconsistent_before = json.loads(json.dumps(before))
    inconsistent_after = json.loads(json.dumps(after))
    inconsistent_final = json.loads(json.dumps(final))
    for value in (inconsistent_before, inconsistent_after, inconsistent_final):
        value["fixture_audit_actions"][
            "monitoring.document_renewal.unaccounted"
        ] = 1
    with pytest.raises(control.ActivationControlError):
        control.compare_fingerprints(
            inconsistent_before,
            inconsistent_after,
            {"cleanup": {"final_fingerprint": inconsistent_final}},
        )

    for field in ("legacy_rows", "coverage_gaps"):
        drifted = json.loads(json.dumps(after))
        drifted["audit_chain"][field] += 1
        drifted["audit_chain"]["total_entries"] += 1
        with pytest.raises(control.ActivationControlError):
            control.compare_fingerprints(
                before,
                drifted,
                {"cleanup": {"final_fingerprint": final}},
            )

    unchained = json.loads(json.dumps(after))
    unchained["fixture_audit_unchained_count"] = 1
    with pytest.raises(control.ActivationControlError):
        control.compare_fingerprints(
            before,
            unchained,
            {"cleanup": {"final_fingerprint": final}},
        )

    after["fixture_audit_actions"] = dict(after_actions)
    after["fixture_audit_actions"][
        "monitoring.document_renewal.unexpected"
    ] = 1
    after["relations"]["fixture_audit"]["row_count"] = 3 + validation_count
    after["fixture_audit_chained_count"] = 3 + validation_count
    with pytest.raises(control.ActivationControlError):
        control.compare_fingerprints(
            before,
            after,
            {"cleanup": {"final_fingerprint": final}},
        )


def test_recovery_reconciliation_allows_only_cleanup_and_empty_final_state():
    counts = {
        "renewal_requests": 1,
        "renewal_events": 3,
        "renewal_uploads": 1,
        "renewal_upload_bindings": 1,
        "renewal_upload_cleanup": 1,
        "renewal_notifications": 1,
        "harness_rate_limit": 1,
    }
    validation_count = sum(control.EXPECTED_VALIDATION_AUDIT_DELTA.values())
    before = _fingerprint(
        counts=counts,
        actions=control.EXPECTED_VALIDATION_AUDIT_DELTA,
        audit_count=validation_count,
        chained=validation_count,
        unchained=0,
        active=False,
    )
    final_actions = dict(control.EXPECTED_VALIDATION_AUDIT_DELTA)
    final_actions.update(control.EXPECTED_CLEANUP_AUDIT_DELTA)
    final = _fingerprint(
        counts={name: 0 for name in counts},
        actions=final_actions,
        audit_count=validation_count + 1,
        chained=validation_count + 1,
        unchained=0,
        active=False,
    )
    result = control.compare_recovery_fingerprints(
        before,
        final,
        {"cleanup": {"idempotent": False}},
    )
    assert result["ok"] is True
    assert result["comparisons"]["final_operational_state_empty"] is True
    drifted = json.loads(json.dumps(final))
    drifted["audit_chain"]["coverage_gaps"] += 1
    drifted["audit_chain"]["total_entries"] += 1
    with pytest.raises(control.ActivationControlError):
        control.compare_recovery_fingerprints(
            before,
            drifted,
            {"cleanup": {"idempotent": False}},
        )
    unchained = json.loads(json.dumps(final))
    unchained["fixture_audit_unchained_count"] = 1
    with pytest.raises(control.ActivationControlError):
        control.compare_recovery_fingerprints(
            before,
            unchained,
            {"cleanup": {"idempotent": False}},
        )
    final["relations"]["fixture_agent_executions"]["row_count"] = 1
    with pytest.raises(control.ActivationControlError, match="do not reconcile"):
        control.compare_recovery_fingerprints(
            before,
            final,
            {"cleanup": {"idempotent": False}},
        )


def test_idempotent_recovery_accepts_the_unchanged_pinned_baseline():
    clean = _fingerprint(
        counts={name: 0 for name in control.RECOVERY_OPERATIONAL_RELATIONS},
        actions={},
        audit_count=0,
        chained=0,
        unchained=0,
        active=False,
    )
    result = control.compare_recovery_fingerprints(
        clean,
        json.loads(json.dumps(clean)),
        {"cleanup": {"idempotent": True}},
    )
    assert result["ok"] is True
    assert result["cleanup_audit_delta"] == {}


def test_context_is_main_only_and_requires_exact_confirmation():
    assert control.validate_release_context(
        SHA,
        "refs/heads/main",
        control.CONFIRM_TOKEN,
    ) == SHA
    with pytest.raises(control.ActivationControlError, match="main-only"):
        control.validate_release_context(
            SHA,
            "refs/heads/feature",
            control.CONFIRM_TOKEN,
        )
    with pytest.raises(control.ActivationControlError, match="confirmation"):
        control.validate_release_context(SHA, "refs/heads/main", "wrong")


def test_cli_output_is_sanitized_and_contains_no_secret_values(tmp_path, capsys):
    path = tmp_path / "source.json"
    path.write_text(json.dumps(_source()), encoding="utf-8")
    out = tmp_path / "evidence.json"
    assert control.main(
        [
            "inspect",
            "--task-definition",
            str(path),
            "--expected-sha",
            SHA,
            "--expected-mode",
            "off",
            "--output",
            str(out),
        ]
    ) == 0
    captured = capsys.readouterr().out
    assert "secret:example" not in captured
    assert "secret:example" not in out.read_text(encoding="utf-8")
