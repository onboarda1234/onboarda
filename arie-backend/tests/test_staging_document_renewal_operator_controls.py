from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import textwrap
from urllib.error import HTTPError

import pytest

from branding import BRAND
from monitoring_document_renewal_staging_cleanup import (
    STAGING_AUDIT_BASELINE_LEGACY_ROWS,
    STAGING_DATABASE_FINGERPRINT_ENV,
)
from scripts.qa import staging_document_renewal_activation as activation
from scripts.qa import staging_document_renewal_ecs_task as ecs_task
from scripts.qa import staging_document_renewal_http_validation as http_validation
from scripts.qa import staging_document_renewal_iam as harness_iam


SHA = "a" * 40
RUN_ID = "gh-12345-1"
TASK_ARN = (
    "arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:1002"
)
REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "document-renewal-staging-harness.yml"
)
RECOVERY_WORKFLOW = (
    REPO_ROOT
    / ".github"
    / "workflows"
    / "document-renewal-staging-harness-recovery.yml"
)


def _task_definition(*, image=None, sha=SHA):
    return {
        "taskDefinitionArn": TASK_ARN,
        "family": "regmind-staging",
        "containerDefinitions": [
            {
                "name": "regmind-backend",
                "image": image
                or (
                    "782913119880.dkr.ecr.af-south-1.amazonaws.com/"
                    f"regmind-backend:{sha}"
                ),
                "environment": [
                    {"name": "ENVIRONMENT", "value": "staging"},
                    {"name": "GIT_SHA", "value": sha},
                    {"name": "IMAGE_TAG", "value": sha},
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": "/ecs/regmind-staging",
                        "awslogs-stream-prefix": "ecs",
                    },
                },
            }
        ],
    }


def test_runner_exposes_only_fixed_operation_enum_and_commands(monkeypatch):
    monkeypatch.setenv(STAGING_DATABASE_FINGERPRINT_ENV, "b" * 64)
    observed = {}
    for operation in sorted(ecs_task.OPERATIONS):
        command, environment, _evidence, _mode = ecs_task._operation_contract(
            operation,
            run_id=RUN_ID,
        )
        observed[operation] = command
        assert command[0] == "python"
        assert "-c" not in command
        assert "DATABASE_URL" not in {item["name"] for item in environment}
        assert "JWT_SECRET" not in {item["name"] for item in environment}
        assert not any("AGENT1" in item["name"] for item in environment)
        assert not any("ENABLE_" in item["name"] for item in environment)
        assert {item["name"] for item in environment} <= {
            "ENVIRONMENT",
            STAGING_DATABASE_FINGERPRINT_ENV,
            "ALLOW_DOCUMENT_RENEWAL_STAGING_FIXTURE",
        }
    assert set(observed) == {
        "seed",
        "snapshot-off",
        "eligibility",
        "snapshot-active",
        "cleanup",
    }


@pytest.mark.parametrize(
    "legacy_input",
    (
        ["--command-json", '["python","-c","drop_database()"]'],
        ["--pass-env", "DATABASE_URL"],
        ["--set-env", "ENABLE_AGENT1_REFRESH_VERIFICATION=true"],
    ),
)
def test_runner_parser_rejects_generic_command_and_environment_inputs(legacy_input):
    parser = ecs_task._parser()
    base = [
        "--task-definition",
        TASK_ARN,
        "--operation",
        "seed",
        "--expected-sha",
        SHA,
        "--run-id",
        RUN_ID,
        "--output",
        "out.json",
    ]
    with pytest.raises(SystemExit):
        parser.parse_args([*base, *legacy_input])


def _network_configuration():
    return {
        "awsvpcConfiguration": {
            "subnets": ["subnet-00000000000000000"],
            "securityGroups": ["sg-00000000000000000"],
            "assignPublicIp": "DISABLED",
        }
    }


def test_secret_bearing_run_task_overrides_are_stdin_only(monkeypatch):
    calls = []

    def fake_run(arguments, **kwargs):
        calls.append((list(arguments), dict(kwargs)))
        return subprocess.CompletedProcess(arguments, 0, stdout="{}", stderr="")

    monkeypatch.setattr(ecs_task.subprocess, "run", fake_run)
    secret = "do-not-place-on-command-line"
    overrides = {
        "containerOverrides": [
            {
                "name": "regmind-backend",
                "environment": [{"name": "FINGERPRINT", "value": secret}],
            }
        ]
    }
    ecs_task._aws_run_task(
        task_definition=TASK_ARN,
        network_configuration=_network_configuration(),
        overrides=overrides,
        started_by=f"{RUN_ID}-seed",
    )
    arguments, kwargs = calls[0]
    assert secret not in " ".join(arguments)
    assert arguments[:3] == ["aws", "ecs", "run-task"]
    assert arguments[arguments.index("--cluster") + 1] == "regmind-staging"
    assert arguments[arguments.index("--task-definition") + 1] == TASK_ARN
    assert arguments[arguments.index("--launch-type") + 1] == "FARGATE"
    assert arguments[arguments.index("--count") + 1] == "1"
    network_index = arguments.index("--network-configuration")
    assert json.loads(arguments[network_index + 1]) == _network_configuration()
    assert arguments[arguments.index("--started-by") + 1] == f"{RUN_ID}-seed"
    assert arguments[arguments.index("--region") + 1] == "af-south-1"
    assert "--cli-input-json" not in arguments
    override_index = arguments.index("--overrides")
    assert arguments[override_index + 1] == "file:///dev/stdin"
    token_index = arguments.index("--client-token")
    assert arguments[token_index + 1] == f"{RUN_ID}-seed"
    assert secret in kwargs["input"]
    assert json.loads(kwargs["input"]) == overrides


def test_run_task_cli_failure_never_exposes_captured_output(monkeypatch):
    secret = "never-echo-this-fingerprint"

    def fake_run(arguments, **kwargs):
        return subprocess.CompletedProcess(
            arguments,
            252,
            stdout=f"request={secret}",
            stderr=f"validation={secret}",
        )

    monkeypatch.setattr(ecs_task.subprocess, "run", fake_run)
    with pytest.raises(ecs_task.EcsHarnessTaskError) as raised:
        ecs_task._aws_run_task(
            task_definition=TASK_ARN,
            network_configuration=_network_configuration(),
            overrides={
                "containerOverrides": [
                    {"environment": [{"name": "FINGERPRINT", "value": secret}]}
                ]
            },
            started_by=f"{RUN_ID}-seed",
        )
    assert str(raised.value) == "AWS stdin command failed"
    assert secret not in str(raised.value)


@pytest.mark.skipif(shutil.which("aws") is None, reason="AWS CLI is unavailable")
def test_installed_aws_cli_accepts_run_task_overrides_from_stdin():
    """Exercise CLI parsing without contacting ECS or creating a task."""

    overrides = {
        "containerOverrides": [
            {
                "name": "regmind-backend",
                "environment": [{"name": "FINGERPRINT", "value": "b" * 64}],
            }
        ]
    }
    arguments = ecs_task._run_task_cli_arguments(
        task_definition=TASK_ARN,
        network_configuration=_network_configuration(),
        started_by=f"{RUN_ID}-seed",
    )
    result = subprocess.run(
        [*arguments, "--generate-cli-skeleton", "output"],
        input=json.dumps(overrides, separators=(",", ":")),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), dict)


def test_runner_rejects_wrong_registry_even_with_matching_sha(monkeypatch):
    definition = _task_definition(image=f"evil.invalid/backend:{SHA}")
    monkeypatch.setattr(
        ecs_task,
        "_aws",
        lambda _args: {"taskDefinition": definition},
    )
    with pytest.raises(ecs_task.EcsHarnessTaskError, match="provenance"):
        ecs_task._log_configuration(
            TASK_ARN,
            expected_sha=SHA,
            expected_mode="off",
            expected_run_id=RUN_ID,
        )


def test_operation_specific_evidence_rejects_secrets_and_unknown_fields():
    good = {
        "schema_version": "document_renewal_staging_eligibility_v1",
        "ok": True,
        "run_id": RUN_ID,
        "fixture_key": "document-renewal-expired-v1",
        "fixture_version": "document_renewal_staging_fixture_v1",
        "manifest_sha256": "b" * 64,
        "application_id": "f1xedrnwapp00001",
        "alert_id": 1,
        "request_id": "fxrnwreq00000001",
        "scanned": 1,
        "created": 1,
        "already_active": 0,
    }
    ecs_task._validate_evidence(good, operation="eligibility")
    with pytest.raises(ecs_task.EcsHarnessTaskError, match="sensitive"):
        ecs_task._validate_evidence(
            {**good, "JWT_SECRET": "not-allowed"},
            operation="eligibility",
        )
    with pytest.raises(ecs_task.EcsHarnessTaskError, match="schema"):
        ecs_task._validate_evidence(
            {**good, "unexpected": True},
            operation="eligibility",
        )


def test_eligibility_cli_uses_exact_evidence_marker(monkeypatch, capsys):
    monkeypatch.setattr(
        activation,
        "run_fixture_eligibility",
        lambda: {
            "schema_version": "document_renewal_staging_eligibility_v1",
            "ok": True,
            "run_id": RUN_ID,
        },
    )
    assert activation.main(["run-fixture-eligibility"]) == 0
    output = capsys.readouterr().out.strip()
    assert output.startswith(activation.EVIDENCE_PREFIX)
    assert json.loads(output[len(activation.EVIDENCE_PREFIX):])["run_id"] == RUN_ID


def test_http_request_rejects_redirect_without_following(monkeypatch):
    class RedirectingOpener:
        def open(self, request, timeout):
            raise HTTPError(
                request.full_url,
                302,
                "redirect",
                {"Location": "https://evil.invalid/capture"},
                BytesIO(b"{}"),
            )

    monkeypatch.setattr(
        http_validation,
        "build_opener",
        lambda *_args, **_kwargs: RedirectingOpener(),
    )
    with pytest.raises(http_validation.HttpValidationError, match="redirect"):
        http_validation._request(
            "https://staging.regmind.co",
            "GET",
            "/api/version",
            "sensitive-bearer",
        )


def _jwt_payload(token):
    encoded = token.split(".")[1]
    encoded += "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))


def test_harness_tokens_use_governed_fixture_claim_and_existing_smoke_officer():
    from db import QA_SMOKE_USER_ID
    from document_renewal_staging_activation import (
        HARNESS_CLIENT_PURPOSE,
        HARNESS_CLIENT_PURPOSE_CLAIM,
        HARNESS_CLIENT_RUN_CLAIM,
    )

    client, officer = http_validation._machine_tokens(
        json.dumps({"JWT_SECRET": "s" * 64}),
        customer_id="f1xedrnwcli00001",
        run_id=RUN_ID,
    )
    client_payload = _jwt_payload(client)
    officer_payload = _jwt_payload(officer)
    assert client_payload["sub"] == "f1xedrnwcli00001"
    assert client_payload[HARNESS_CLIENT_PURPOSE_CLAIM] == HARNESS_CLIENT_PURPOSE
    assert client_payload[HARNESS_CLIENT_RUN_CLAIM] == RUN_ID
    assert officer_payload["sub"] == QA_SMOKE_USER_ID
    assert http_validation.QA_SMOKE_OFFICER_ID == QA_SMOKE_USER_ID


def _real_route_validation_responses():
    spec = http_validation.fixture_spec()
    upload_id = "0123456789abcdef0123456789abcdef"
    upload_timestamp = "2026-08-05T03:33:49+00:00"
    public_binding = {
        "upload_id": upload_id,
        "renewal_request_id": spec["request_id"],
        "application_id": spec["application_id"],
        "document_type": spec["document_type"],
        "upload_timestamp": upload_timestamp,
        "binding_status": "bound",
        "contract_version": http_validation.UPLOAD_BINDING_CONTRACT_VERSION,
    }
    authoritative_binding = {
        **public_binding,
        "customer_id": spec["customer_id"],
        "person_id": spec["person_id"],
        "person_type": spec["person_type"],
        "original_document_id": spec["document_id"],
        "original_document_version": spec["document_version"],
        "uploaded_document_id": (
            f"{http_validation.UPLOADED_DOCUMENT_ID_PREFIX}{upload_id}"
        ),
        "uploaded_by": spec["customer_id"],
    }
    feature_states = {
        name: {
            "enabled": name == activation.RENEWAL_FLAG,
            "state": "ON" if name == activation.RENEWAL_FLAG else "OFF",
        }
        for name in (activation.RENEWAL_FLAG, *activation.OTHER_MONITORING_FLAGS)
    }
    return [
        (
            200,
            {"git_sha": SHA, "image_tag": SHA, "environment": "staging"},
            {},
        ),
        (
            200,
            {
                "monitoring_feature_flags": {
                    "read_only": True,
                    "activation_controls": False,
                    "features": feature_states,
                }
            },
            {},
        ),
        (
            200,
            {
                "feature_enabled": True,
                "renewal_requests": [
                    {
                        "request_id": spec["request_id"],
                        "request_status": "awaiting_upload",
                        "upload_allowed": True,
                    }
                ],
            },
            {},
        ),
        (
            201,
            {
                "status": "upload_received",
                "binding": dict(public_binding),
                "request": {
                    "request_id": spec["request_id"],
                    "request_status": "upload_received",
                    "upload_binding": dict(public_binding),
                    "uploads": [
                        {
                            "upload_id": upload_id,
                            "renewal_request_id": spec["request_id"],
                            "application_id": spec["application_id"],
                            "original_filename": "synthetic-renewal-passport.pdf",
                            "file_size": 143,
                            "mime_type": "application/pdf",
                            "uploaded_at": upload_timestamp,
                            "upload_timestamp": upload_timestamp,
                        }
                    ],
                },
            },
            {"x-request-id": "upload-request"},
        ),
        (
            409,
            {"error_code": "upload_already_received"},
            {"x-request-id": "duplicate-request"},
        ),
        (
            200,
            {
                "renewal_requests": [
                    {
                        "request_id": spec["request_id"],
                        "request_status": "upload_received",
                        "upload_binding": dict(public_binding),
                    }
                ]
            },
            {},
        ),
        (
            200,
            {
                "alerts": [
                    {
                        "id": 618,
                        "application_id": spec["application_id"],
                        "alert_type": spec["alert_type"],
                    }
                ]
            },
            {},
        ),
        (
            200,
            {
                "id": 618,
                "status": "open",
                "renewal_feature_enabled": True,
                "renewal_request": {
                    "request_id": spec["request_id"],
                    "request_status": "upload_received",
                    "binding_current": True,
                    "upload_binding": authoritative_binding,
                },
            },
            {},
        ),
    ]


def test_http_validator_binding_version_matches_product_contract():
    from monitoring_document_renewal import UPLOAD_BINDING_CONTRACT_VERSION

    assert (
        http_validation.UPLOAD_BINDING_CONTRACT_VERSION
        == UPLOAD_BINDING_CONTRACT_VERSION
    )


def _run_real_route_validation(monkeypatch, responses):
    pending = iter(responses)
    observed = []

    def fake_request(base_url, method, path, token, **kwargs):
        observed.append((base_url, method, path, bool(token), kwargs))
        return next(pending)

    monkeypatch.setattr(http_validation, "_request", fake_request)
    result = http_validation.validate_real_routes(
        base_url=f"https://staging.{BRAND['system_id']}.co",
        expected_sha=SHA,
        run_id=RUN_ID,
        secret_json=json.dumps({"JWT_SECRET": "s" * 64}),
    )
    assert len(observed) == len(responses)
    return result


def test_real_route_validator_accepts_redacted_portal_binding_and_proves_lineage(
    monkeypatch,
):
    result = _run_real_route_validation(
        monkeypatch,
        _real_route_validation_responses(),
    )
    assert result["binding_status"] == "bound"
    assert result["binding_lineage_verified"] is True
    assert result["portal_binding_redaction_verified"] is True
    assert result["monitoring_alert_status"] == "open"


def test_real_route_validator_rejects_protected_portal_binding_leak(monkeypatch):
    responses = _real_route_validation_responses()
    upload_response = responses[3][1]
    for binding in (
        upload_response["binding"],
        upload_response["request"]["upload_binding"],
    ):
        binding["customer_id"] = http_validation.fixture_spec()["customer_id"]
    with pytest.raises(
        http_validation.HttpValidationError,
        match="protected binding linkage",
    ):
        _run_real_route_validation(monkeypatch, responses)


def test_real_route_validator_rejects_protected_nested_upload_leak(monkeypatch):
    responses = _real_route_validation_responses()
    responses[3][1]["request"]["uploads"][0]["document_id"] = (
        http_validation.fixture_spec()["document_id"]
    )
    with pytest.raises(
        http_validation.HttpValidationError,
        match="protected binding linkage",
    ):
        _run_real_route_validation(monkeypatch, responses)


def test_real_route_validator_rejects_protected_followup_portal_leak(monkeypatch):
    responses = _real_route_validation_responses()
    responses[5][1]["renewal_requests"][0]["customer_id"] = (
        http_validation.fixture_spec()["customer_id"]
    )
    with pytest.raises(
        http_validation.HttpValidationError,
        match="follow-up exposed protected binding linkage",
    ):
        _run_real_route_validation(monkeypatch, responses)


def test_real_route_validator_rejects_stale_nested_request_status(monkeypatch):
    responses = _real_route_validation_responses()
    responses[3][1]["request"]["request_status"] = "awaiting_upload"
    with pytest.raises(
        http_validation.HttpValidationError,
        match="did not accept the exact request",
    ):
        _run_real_route_validation(monkeypatch, responses)


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("upload_id", ""),
        ("renewal_request_id", "wrong-request"),
        ("application_id", "wrong-application"),
        ("document_type", "wrong-document-type"),
        ("upload_timestamp", "not-an-iso-timestamp"),
        ("binding_status", "verified"),
        ("contract_version", "monitoring_document_renewal_upload_binding_v0"),
    ),
)
def test_real_route_validator_rejects_invalid_public_binding(
    monkeypatch,
    field,
    invalid,
):
    responses = _real_route_validation_responses()
    upload_response = responses[3][1]
    upload_response["binding"][field] = invalid
    upload_response["request"]["upload_binding"][field] = invalid
    with pytest.raises(
        http_validation.HttpValidationError,
        match="invalid public binding",
    ):
        _run_real_route_validation(monkeypatch, responses)


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("upload_id", "wrong-upload"),
        ("renewal_request_id", "wrong-request"),
        ("application_id", "wrong-application"),
        ("customer_id", "wrong-customer"),
        ("person_id", "wrong-person"),
        ("person_type", "wrong-person-type"),
        ("original_document_id", "wrong-document"),
        ("original_document_version", 999),
        ("uploaded_document_id", "renewal-candidate:wrong-upload"),
        ("document_type", "wrong-document-type"),
        ("upload_timestamp", "2026-08-05T03:33:50+00:00"),
        ("uploaded_by", "wrong-uploader"),
        ("binding_status", "verified"),
        ("contract_version", "monitoring_document_renewal_upload_binding_v0"),
    ),
)
def test_real_route_validator_rejects_cross_wired_authoritative_binding(
    monkeypatch,
    field,
    invalid,
):
    responses = _real_route_validation_responses()
    responses[7][1]["renewal_request"]["upload_binding"][field] = invalid
    with pytest.raises(
        http_validation.HttpValidationError,
        match="invalid authoritative upload binding",
    ):
        _run_real_route_validation(monkeypatch, responses)


def test_real_route_validator_rejects_boolean_document_version(monkeypatch):
    responses = _real_route_validation_responses()
    responses[7][1]["renewal_request"]["upload_binding"][
        "original_document_version"
    ] = True
    with pytest.raises(
        http_validation.HttpValidationError,
        match="invalid authoritative upload binding",
    ):
        _run_real_route_validation(monkeypatch, responses)


def test_real_route_validator_rejects_boolean_upload_size(monkeypatch):
    responses = _real_route_validation_responses()
    responses[3][1]["request"]["uploads"][0]["file_size"] = True
    with pytest.raises(
        http_validation.HttpValidationError,
        match="invalid public upload metadata",
    ):
        _run_real_route_validation(monkeypatch, responses)


def test_real_route_validator_rejects_conflicting_public_aliases(monkeypatch):
    responses = _real_route_validation_responses()
    responses[3][1]["request"]["upload_binding"]["document_type"] = "id_card"
    with pytest.raises(
        http_validation.HttpValidationError,
        match="conflicting bindings",
    ):
        _run_real_route_validation(monkeypatch, responses)


def test_temporary_iam_policy_is_exact_and_removal_refuses_drift(monkeypatch):
    statements = harness_iam.POLICY_DOCUMENT["Statement"]
    assert statements[0] == {
        "Sid": "FixtureRenewalCandidateVersionInventory",
        "Effect": "Allow",
        "Action": "s3:ListBucketVersions",
        "Resource": "arn:aws:s3:::regmind-documents-staging",
        "Condition": {
            "StringLike": {
                "s3:prefix": [
                    "clients/f1xedrnwcli00001/monitoring_renewal_candidate/"
                    "fxrnwreq00000001/*"
                ]
            }
        },
    }
    assert statements[1] == {
        "Sid": "FixtureRenewalCandidateVersionDelete",
        "Effect": "Allow",
        "Action": "s3:DeleteObjectVersion",
        "Resource": (
            "arn:aws:s3:::regmind-documents-staging/"
            "clients/f1xedrnwcli00001/monitoring_renewal_candidate/"
            "fxrnwreq00000001/*"
        ),
    }
    monkeypatch.setattr(
        harness_iam,
        "_existing_policy",
        lambda: {"Version": "2012-10-17", "Statement": []},
    )
    with pytest.raises(harness_iam.HarnessIamError, match="non-exact"):
        harness_iam.remove()


def test_iam_install_refuses_preexisting_policy(monkeypatch):
    monkeypatch.setattr(
        harness_iam,
        "_existing_policy",
        lambda: dict(harness_iam.POLICY_DOCUMENT),
    )
    with pytest.raises(harness_iam.HarnessIamError, match="already exists"):
        harness_iam.install()


@pytest.mark.parametrize("path", (HARNESS_WORKFLOW, RECOVERY_WORKFLOW))
def test_harness_workflow_step_references_have_declared_ids(path):
    text = path.read_text(encoding="utf-8")
    declared = set(
        re.findall(r"^\s+id:\s*([A-Za-z_][A-Za-z0-9_-]*)\s*$", text, re.MULTILINE)
    )
    referenced = set(re.findall(r"steps\.([A-Za-z_][A-Za-z0-9_-]*)", text))
    assert referenced <= declared


def test_main_workflow_proves_live_recovery_tags_before_activation():
    text = HARNESS_WORKFLOW.read_text(encoding="utf-8")
    register = text.index("aws ecs register-task-definition")
    persisted_probe = text.index("registered-temporary-payload.json")
    metadata_evidence = text.index("registered-recovery-metadata.json")
    activation = text.index("name: Activate temporary backend lease only")
    assert register < persisted_probe < metadata_evidence < activation
    probe_region = text[register:activation]
    assert "--include TAGS" in probe_region
    assert "--expected-mode active" in probe_region
    assert "--expected-run-id \"$HARNESS_RUN_ID\"" in probe_region


def test_main_workflow_uses_one_bounded_lease_clock_and_browser_margin():
    text = HARNESS_WORKFLOW.read_text(encoding="utf-8")
    register = text.index("name: Register bounded temporary backend activation")
    activate = text.index("name: Activate temporary backend lease only")
    region = text[register:activate]
    assert "LEASE_SECONDS=1680" in region
    assert "ISSUED_AT_EPOCH=\"$(date -u '+%s')\"" in region
    assert "EXPIRES_AT_EPOCH=\"$((ISSUED_AT_EPOCH + LEASE_SECONDS))\"" in region
    assert '\\"lease_seconds\\":$LEASE_SECONDS' in region
    assert "lease_seconds\":1200" not in region
    assert "lease_seconds\":1800" not in region

    browser = text.index("name: Open bounded read-only browser QA window")
    restore = text.index("name: Restore exact original backend")
    browser_region = text[browser:restore]
    assert "HARNESS_EXPIRES_AT" in browser_region
    assert '"$REMAINING_SECONDS" -lt 240' in browser_region
    assert "insufficient governed lease remains" in browser_region


def test_ordinary_scheduler_contract_remains_global_when_harness_is_absent():
    source = (REPO_ROOT / "arie-backend" / "server.py").read_text(
        encoding="utf-8"
    )
    start = source.index(
        "# PR-MON-DOC-RENEWAL-REQUEST-1 request eligibility + reminder generation."
    )
    end = source.index("# PR-PRS-C2 memo-recovery sweep.", start)
    region = source[start:end]
    harness_branch = region.index("if harness_mode_configured():")
    ordinary_branch = region.index(
        "elif _monitoring_document_renewal.renewal_feature_enabled():"
    )
    scheduler = region.index("_document_renewal_reminder_tick")
    assert harness_branch < ordinary_branch < scheduler
    assert "global scheduler disabled by " in region
    assert "fixture-scoped staging harness" in region


def test_main_workflow_preserves_recovery_handle_until_cleanup_is_proven():
    text = HARNESS_WORKFLOW.read_text(encoding="utf-8")
    cleanup = text.index("name: Clean exact synthetic operational graph")
    reconciliation = text.index("name: Reconcile before, after and final fingerprints")
    iam_remove = text.index("name: Remove exact temporary cleanup permission")
    retirement = text.index(
        "name: Retire exact temporary task definition after complete cleanup proof"
    )
    finalize = text.index("name: Finalize successful harness evidence")
    assert cleanup < reconciliation < iam_remove < retirement < finalize
    condition = text[retirement : text.index("        run: |", retirement)]
    assert "steps.restore.outcome == 'success'" in condition
    assert "steps.cleanup.outcome == 'success'" in condition
    assert "steps.reconciliation.outcome == 'success'" in condition
    assert "steps.iam_remove.outcome == 'success'" in condition
    body = text[retirement:finalize]
    assert "recovery-candidate" in body
    assert 'value.get("action") != "restore"' in body
    assert "aws ecs deregister-task-definition" in body
    assert "aws ecs untag-resource" in body
    assert body.index("deregister-task-definition") < body.index("untag-resource")
    assert "--tag-keys RegMindHarness HarnessRunId HarnessOriginalTaskDefinition" in body


def test_harness_reports_coverage_gaps_without_pinning_the_global_count():
    workflow = HARNESS_WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("name: Capture clean baseline fingerprint")
    end = workflow.index("name: Register bounded temporary backend activation", start)
    baseline = workflow[start:end]
    assert "validated_staging_harness_audit_chain" in baseline
    assert "staging_harness_fixture_audit_chain_is_exact" in baseline
    assert 'chain.get("coverage_complete") is True' not in baseline
    assert 'chain.get("coverage_gaps", -1)) == 0' not in baseline

    source = (
        REPO_ROOT
        / "arie-backend"
        / "monitoring_document_renewal_staging_cleanup.py"
    ).read_text(encoding="utf-8")
    # Legacy history remains an exact, immutable control.
    assert (
        "STAGING_AUDIT_BASELINE_LEGACY_ROWS = "
        f"{STAGING_AUDIT_BASELINE_LEGACY_ROWS:_}"
    ) in source
    assert (
        'metadata["legacy_rows"] == STAGING_AUDIT_BASELINE_LEGACY_ROWS'
    ) in source
    # The global coverage-gap count is reported evidence, never a gate.
    assert (
        'metadata["coverage_gaps"] == STAGING_AUDIT_BASELINE_COVERAGE_GAPS'
    ) not in source
    assert 'metadata["coverage_gaps"] >= 0' in source


def test_main_workflow_proves_global_residue_absent_before_and_after_lease():
    text = HARNESS_WORKFLOW.read_text(encoding="utf-8")
    preflight = text.index("name: Prove no prior harness lease or running task exists")
    register = text.index("aws ecs register-task-definition")
    retirement = text.index(
        "name: Retire exact temporary task definition after complete cleanup proof"
    )
    final_residue = text.index(
        "name: Prove zero tagged or running harness residue after retirement"
    )
    finalize = text.index("name: Finalize successful harness evidence")
    assert preflight < register < retirement < final_residue < finalize

    preflight_body = text[preflight:register]
    assert "resourcegroupstaggingapi get-resources" in preflight_body
    assert "Key=RegMindHarness,Values=document-renewal-staging-harness-v1" in preflight_body
    assert "HarnessRunId,Values=" not in preflight_body
    assert "a prior globally tagged harness task definition remains" in preflight_body
    assert "--desired-status RUNNING" in preflight_body
    assert "--include\", \"TAGS" in preflight_body
    assert "verify-recovery-residue-clear" in preflight_body
    assert "preflight-residue-proof.json" in preflight_body

    final_body = text[final_residue:finalize]
    assert "for attempt in $(seq 1 20)" in final_body
    assert "harness tags remain after primary teardown" in final_body
    assert "--desired-status RUNNING" in final_body
    assert "verify-recovery-residue-clear" in final_body
    assert "final-residue-proof.json" in final_body
    finalize_condition = text[finalize : text.index("        run: |", finalize)]
    assert "steps.final_residue.outcome == 'success'" in finalize_condition


@pytest.mark.parametrize("path", (HARNESS_WORKFLOW, RECOVERY_WORKFLOW))
def test_every_running_task_inventory_fails_on_incomplete_ecs_description(path):
    text = path.read_text(encoding="utf-8")
    describe_count = text.count("describe-tasks")
    assert describe_count > 0
    assert text.count('payload.get("failures")') == describe_count
    assert (
        text.count("ECS did not describe every listed running task")
        == describe_count
    )
    assert text.count("len(set(returned") == describe_count
    assert text.count("set(returned") == 2 * describe_count


def test_recovery_workflow_is_expired_only_exact_and_never_updates_worker():
    text = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_run:" in text
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    job = text.index("  recover:")
    job_if = text.index("    if:", job)
    job_concurrency = text.index("    concurrency:", job)
    assert job_if < job_concurrency
    assert "      group: deploy-staging" in text[job_concurrency:]
    assert "cancel-in-progress: false" in text
    assert "select-recovery-context" in text
    assert "verify-recovery-plan" in text
    assert "--include TAGS" in text
    assert "Wait only until the exact active lease expires" in text
    assert "compare-recovery-fingerprints" in text
    assert "resourcegroupstaggingapi get-resources" in text
    assert "verify-recovery-residue-clear" in text
    assert "verify-clean-recovery-fingerprint" in text
    assert "Key=RegMindHarness,Values=document-renewal-staging-harness-v1" in text
    assert "HarnessRunId,Values=" not in text
    assert "staging_document_renewal_iam.py remove" in text
    assert text.count("aws ecs update-service") == 1
    update_region = text[text.index("aws ecs update-service"):]
    assert '--service "$ECS_BACKEND_SERVICE"' in update_region
    assert '--service "$ECS_WORKER_SERVICE"' not in update_region
    assert "DOCUMENT_RENEWAL_HARNESS_STAGING_DATABASE_FINGERPRINT" in text


def test_recovery_workflow_pins_lease_before_repo_tools_and_never_false_noops():
    text = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    workflow_env, job = text.split("jobs:\n", 1)
    assert "runner.temp" not in workflow_env
    job_header = job[: job.index("    steps:")]
    assert "runner.temp" not in job_header
    assert 'RECOVERY_EVIDENCE_DIR="$RUNNER_TEMP/harness-recovery-evidence"' in job
    assert 'echo "RECOVERY_EVIDENCE_DIR=$RECOVERY_EVIDENCE_DIR" >> "$GITHUB_ENV"' in job
    bootstrap = text.index("Checkout bootstrap recovery controls from current main")
    discovery = text.index("Discover exact tagged lease before strict health checks")
    provenance = text.index("Prove immutable recovery SHA is reviewed main history")
    pinned = text.index("Checkout immutable lease recovery controls")
    prove = text.index("Prove immutable recovery checkout")
    strict = text.index("Capture strict healthy OFF runtime after convergence")
    cleanup = text.index("Clean only the exact owned fixture graph")
    clean = text.index("Capture and prove exact clean final fingerprint")
    deregister = text.index("Deregister exact temporary task definition after clean proof")
    residue = text.index("Prove zero active tagged or running harness residue")
    assert bootstrap < discovery < provenance < pinned < prove < strict
    assert cleanup < clean < deregister < residue
    pinned_region = text[pinned:prove]
    assert "ref: ${{ steps.discovery.outputs.recovery_sha }}" in pinned_region
    discovery_region = text[discovery:pinned]
    assert "git rev-parse HEAD" not in discovery_region
    assert "capture-predeploy" not in discovery_region
    assert "scripts/qa/staging_document_renewal" not in discovery_region
    assert "fetch-depth: 0" in text[bootstrap:discovery]
    provenance_region = text[provenance:pinned]
    assert 'git cat-file -e "$RECOVERY_SHA^{commit}"' in provenance_region
    assert 'git merge-base --is-ancestor "$RECOVERY_SHA" origin/main' in provenance_region
    assert 'git merge-base --is-ancestor "$WORKFLOW_HEAD_SHA" "$RECOVERY_SHA"' in provenance_region
    assert '"$WORKFLOW_HEAD_SHA" != "$RECOVERY_SHA"' in provenance_region
    assert "tagged_candidate" in discovery_region
    assert "--operation snapshot-off" in text
    assert "probe_run_id" in text
    assert "active_tagged_task_definitions" not in text  # emitted by typed verifier
    assert "cancel-in-progress: false" in text
    deregister_region = text[deregister:residue]
    assert "recovery-candidate" in deregister_region
    assert "aws ecs deregister-task-definition" in deregister_region
    assert "aws ecs untag-resource" in deregister_region
    assert deregister_region.index("deregister-task-definition") < deregister_region.index(
        "untag-resource"
    )
    assert "--tag-keys RegMindHarness HarnessRunId" in deregister_region


def test_recovery_cloudwatch_window_starts_before_discovery_and_restoration():
    text = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    initialize = text.index("name: Initialize recovery evidence and trigger identity")
    window = text.index("recovery-window-start.json", initialize)
    discovery = text.index("name: Discover exact tagged lease before strict health checks")
    restore = text.index("name: Verify recovery plan and restore exact original backend")
    cloudwatch = text.index("name: Collect bounded CloudWatch recovery review")
    assert initialize < window < discovery < restore < cloudwatch
    cloudwatch_condition = text[cloudwatch : text.index("        run: |", cloudwatch)]
    assert "always()" in cloudwatch_condition
    assert "steps.aws.outcome == 'success'" in cloudwatch_condition
    assert "steps.runtime.outcome" not in cloudwatch_condition
    cloudwatch_body = text[cloudwatch : text.index("      - name:", cloudwatch + 10)]
    assert '--predeploy-evidence "$RECOVERY_EVIDENCE_DIR/recovery-window-start.json"' in cloudwatch_body
    assert "post-convergence.json" not in cloudwatch_body


def test_recovery_database_gate_cannot_block_restore_or_iam_teardown():
    text = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    aws = text.index("name: Configure AWS credentials")
    discovery = text.index("name: Discover exact tagged lease before strict health checks")
    restore = text.index("name: Verify recovery plan and restore exact original backend")
    runtime = text.index("name: Capture strict healthy OFF runtime after convergence")
    database = text.index("name: Validate cleanup database identity gate")
    residue = text.index("name: Capture exact residue fingerprint on every invocation")
    iam_remove = text.index("name: Remove exact temporary cleanup permission")
    assert aws < discovery < restore < runtime < database < residue < iam_remove
    iam_condition = text[iam_remove : text.index("        run: |", iam_remove)]
    assert "always()" in iam_condition
    assert "steps.aws.outcome == 'success'" in iam_condition


def _recovery_provenance_script() -> str:
    text = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    name = "      - name: Prove immutable recovery SHA is reviewed main history"
    start = text.index(name)
    run_marker = text.index("        run: |\n", start) + len("        run: |\n")
    end = text.index("\n      - name:", run_marker)
    return textwrap.dedent(text[run_marker:end])


def _git(*arguments: str, cwd: Path) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=cwd, text=True
    ).strip()


def _provenance_repo(tmp_path: Path) -> tuple[str, str, str]:
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "harness-test@example.invalid", cwd=tmp_path)
    _git("config", "user.name", "Harness Test", cwd=tmp_path)
    _git("commit", "--allow-empty", "-q", "-m", "reviewed recovery", cwd=tmp_path)
    reviewed = _git("rev-parse", "HEAD", cwd=tmp_path)
    _git("commit", "--allow-empty", "-q", "-m", "current main", cwd=tmp_path)
    current = _git("rev-parse", "HEAD", cwd=tmp_path)
    _git("update-ref", "refs/remotes/origin/main", current, cwd=tmp_path)
    _git("checkout", "--orphan", "rogue", "-q", cwd=tmp_path)
    _git("commit", "--allow-empty", "-q", "-m", "unreviewed", cwd=tmp_path)
    rogue = _git("rev-parse", "HEAD", cwd=tmp_path)
    return reviewed, current, rogue


def _run_recovery_provenance(
    tmp_path: Path,
    *,
    recovery_sha: str,
    tagged_candidate: str,
    event_name: str,
    workflow_head_sha: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _recovery_provenance_script()],
        cwd=tmp_path,
        env={
            **os.environ,
            "RECOVERY_SHA": recovery_sha,
            "TAGGED_CANDIDATE": tagged_candidate,
            "EVENT_NAME": event_name,
            "WORKFLOW_HEAD_SHA": workflow_head_sha,
        },
        text=True,
        capture_output=True,
        check=False,
    )


def test_recovery_provenance_accepts_reviewed_tagged_and_descendant_no_candidate(
    tmp_path,
):
    reviewed, current, _rogue = _provenance_repo(tmp_path)
    tagged = _run_recovery_provenance(
        tmp_path,
        recovery_sha=reviewed,
        tagged_candidate="true",
        event_name="workflow_run",
        workflow_head_sha=reviewed,
    )
    assert tagged.returncode == 0, tagged.stderr
    no_candidate = _run_recovery_provenance(
        tmp_path,
        recovery_sha=current,
        tagged_candidate="false",
        event_name="workflow_run",
        workflow_head_sha=reviewed,
    )
    assert no_candidate.returncode == 0, no_candidate.stderr


def test_recovery_provenance_rejects_non_main_and_mismatched_event_head(tmp_path):
    reviewed, current, rogue = _provenance_repo(tmp_path)
    non_main = _run_recovery_provenance(
        tmp_path,
        recovery_sha=rogue,
        tagged_candidate="false",
        event_name="schedule",
        workflow_head_sha="",
    )
    assert non_main.returncode != 0
    assert "not reviewed main history" in non_main.stderr
    mismatched = _run_recovery_provenance(
        tmp_path,
        recovery_sha=current,
        tagged_candidate="true",
        event_name="workflow_run",
        workflow_head_sha=reviewed,
    )
    assert mismatched.returncode != 0
    assert "does not own the tagged recovery lease" in mismatched.stderr
