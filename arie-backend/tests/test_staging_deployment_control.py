import datetime as dt
import json

import pytest

from scripts.qa import staging_deployment_control as control


SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
OLD_SHA = "c" * 40
OLD_DIGEST = "sha256:" + "d" * 64
BACKEND_TD = (
    "arn:aws:ecs:af-south-1:782913119880:"
    "task-definition/regmind-staging:100"
)
WORKER_TD = (
    "arn:aws:ecs:af-south-1:782913119880:"
    "task-definition/regmind-verification-worker:90"
)
NEW_BACKEND_TD = BACKEND_TD.replace(":100", ":101")
NEW_WORKER_TD = WORKER_TD.replace(":90", ":91")
IMAGE_PREFIX = (
    "782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:"
)


def _task_definition(arn, sha, service):
    family, revision = arn.rsplit("/", 1)[1].split(":")
    return {
        "taskDefinitionArn": arn,
        "family": family,
        "revision": int(revision),
        "containerDefinitions": [
            {
                "name": control.EXPECTED_CONTAINER,
                "image": IMAGE_PREFIX + sha,
                "environment": [
                    {"name": "ENVIRONMENT", "value": "staging"},
                    {"name": "SERVICE_NAME", "value": service},
                    {"name": "GIT_SHA", "value": sha},
                    {"name": "IMAGE_TAG", "value": sha},
                    {"name": "BUILD_TIME", "value": "2026-07-30T12:00:00Z"},
                    {"name": "UNRELATED", "value": "preserved"},
                ],
                "secrets": [{"name": "JWT_SECRET", "valueFrom": "secret-arn"}],
            }
        ],
    }


def _service(name, task_definition, desired, *, target_group=False):
    value = {
        "serviceName": name,
        "status": "ACTIVE",
        "desiredCount": desired,
        "runningCount": desired,
        "pendingCount": 0,
        "taskDefinition": task_definition,
        "deployments": [
            {
                "status": "PRIMARY",
                "rolloutState": "COMPLETED",
                "taskDefinition": task_definition,
            }
        ],
        "loadBalancers": [],
    }
    if target_group:
        value["loadBalancers"] = [
            {
                "targetGroupArn": (
                    "arn:aws:elasticloadbalancing:af-south-1:782913119880:"
                    "targetgroup/regmind-staging-tg/example"
                )
            }
        ]
    return value


def _aws_runtime(
    *,
    deployed=False,
    unhealthy_target=False,
    mutable_repository=False,
):
    backend_td = NEW_BACKEND_TD if deployed else BACKEND_TD
    worker_td = NEW_WORKER_TD if deployed else WORKER_TD
    sha = SHA if deployed else OLD_SHA
    digest = DIGEST if deployed else OLD_DIGEST

    def aws_json(arguments):
        operation = arguments[:2]
        if operation == ["ecr", "describe-repositories"]:
            return {
                "repositories": [
                    {
                        "repositoryName": control.EXPECTED_REPOSITORY,
                        "registryId": control.EXPECTED_ACCOUNT_ID,
                        "imageTagMutability": (
                            "MUTABLE" if mutable_repository else "IMMUTABLE"
                        ),
                        "imageScanningConfiguration": {"scanOnPush": True},
                    }
                ]
            }
        if operation == ["ecs", "describe-services"]:
            return {
                "services": [
                    _service(
                        control.EXPECTED_BACKEND_SERVICE,
                        backend_td,
                        2,
                        target_group=True,
                    ),
                    _service(
                        control.EXPECTED_WORKER_SERVICE,
                        worker_td,
                        2,
                    ),
                ],
                "failures": [],
            }
        if operation == ["ecs", "describe-task-definition"]:
            arn = arguments[arguments.index("--task-definition") + 1]
            service = (
                control.EXPECTED_BACKEND_SERVICE
                if "regmind-staging:" in arn
                else control.EXPECTED_WORKER_SERVICE
            )
            return {"taskDefinition": _task_definition(arn, sha, service)}
        if operation == ["ecr", "describe-images"]:
            tag = arguments[arguments.index("--image-ids") + 1].split("=", 1)[1]
            return {
                "imageDetails": [
                    {
                        "imageTags": [tag],
                        "imageDigest": digest,
                    }
                ]
            }
        if operation == ["ecs", "list-tasks"]:
            service = arguments[arguments.index("--service-name") + 1]
            count = 2
            return {
                "taskArns": [
                    f"arn:aws:ecs:af-south-1:782913119880:task/regmind-staging/{service}-{index}"
                    for index in range(count)
                ]
            }
        if operation == ["ecs", "describe-tasks"]:
            arns = arguments[
                arguments.index("--tasks") + 1 : arguments.index("--region")
            ]
            service = (
                control.EXPECTED_BACKEND_SERVICE
                if control.EXPECTED_BACKEND_SERVICE in arns[0]
                else control.EXPECTED_WORKER_SERVICE
            )
            task_definition = (
                backend_td
                if service == control.EXPECTED_BACKEND_SERVICE
                else worker_td
            )
            return {
                "tasks": [
                    {
                        "taskArn": arn,
                        "taskDefinitionArn": task_definition,
                        "lastStatus": "RUNNING",
                        "containers": [
                            {
                                "name": control.EXPECTED_CONTAINER,
                                "image": IMAGE_PREFIX + sha,
                                "imageDigest": digest,
                                "lastStatus": "RUNNING",
                            }
                        ],
                    }
                    for arn in arns
                ],
                "failures": [],
            }
        if operation == ["elbv2", "describe-target-health"]:
            states = ["unhealthy", "healthy"] if unhealthy_target else ["healthy", "healthy"]
            return {
                "TargetHealthDescriptions": [
                    {"TargetHealth": {"State": state}} for state in states
                ]
            }
        if operation == ["elbv2", "describe-target-group-attributes"]:
            return {
                "Attributes": [
                    {
                        "Key": "deregistration_delay.timeout_seconds",
                        "Value": "300",
                    }
                ]
            }
        raise AssertionError(arguments)

    return aws_json


def test_capture_uses_live_service_task_definitions_and_pairs_rollback():
    result = control.capture_predeploy(
        aws_json=_aws_runtime(deployed=False),
        clock=lambda: dt.datetime(2026, 7, 30, 12, tzinfo=dt.timezone.utc),
    )
    assert result["services"]["backend"]["task_definition_arn"] == BACKEND_TD
    assert result["services"]["worker"]["task_definition_arn"] == WORKER_TD
    assert result["rollback"]["image_tag"] == OLD_SHA
    assert result["rollback"]["image_digest"] == OLD_DIGEST
    commands = "\n".join(result["rollback"]["commands"])
    assert BACKEND_TD in commands
    assert WORKER_TD in commands
    assert "regmind-backend regmind-verification-worker" in commands
    rendered = json.dumps(result)
    assert "secret-arn" not in rendered
    assert "JWT_SECRET" not in rendered


def test_render_updates_only_release_provenance_and_preserves_live_configuration():
    source = _task_definition(
        BACKEND_TD,
        OLD_SHA,
        control.EXPECTED_BACKEND_SERVICE,
    )
    source["registeredBy"] = "operator"
    result = control.render_task_definition(
        source_task_definition=source,
        image=IMAGE_PREFIX + SHA,
        sha=SHA,
        build_time="2026-07-30T13:00:00Z",
        service_name=control.EXPECTED_BACKEND_SERVICE,
    )
    assert "taskDefinitionArn" not in result
    assert "registeredBy" not in result
    container = result["containerDefinitions"][0]
    assert container["image"] == IMAGE_PREFIX + SHA
    environment = {item["name"]: item["value"] for item in container["environment"]}
    assert environment["GIT_SHA"] == SHA
    assert environment["IMAGE_TAG"] == SHA
    assert environment["BUILD_TIME"] == "2026-07-30T13:00:00Z"
    assert environment["UNRELATED"] == "preserved"
    assert container["secrets"] == [{"name": "JWT_SECRET", "valueFrom": "secret-arn"}]


@pytest.mark.parametrize(
    "harness_name",
    (
        "DOCUMENT_RENEWAL_STAGING_HARNESS",
        "DOCUMENT_RENEWAL_STAGING_HARNESS_RUN_ID",
        "DOCUMENT_RENEWAL_STAGING_HARNESS_RELEASE_SHA",
        "DOCUMENT_RENEWAL_STAGING_HARNESS_EXPIRES_AT",
        "DOCUMENT_RENEWAL_STAGING_HARNESS_ORIGINAL_TASK_DEFINITION",
    ),
)
def test_render_refuses_deployment_from_any_temporary_harness_lease(harness_name):
    source = _task_definition(
        BACKEND_TD,
        OLD_SHA,
        control.EXPECTED_BACKEND_SERVICE,
    )
    source["containerDefinitions"][0]["environment"].append(
        {"name": harness_name, "value": "forged-or-active-lease"}
    )
    with pytest.raises(
        control.StagingControlError,
        match="recover it before deployment",
    ):
        control.render_task_definition(
            source_task_definition=source,
            image=IMAGE_PREFIX + SHA,
            sha=SHA,
            build_time="2026-07-30T13:00:00Z",
            service_name=control.EXPECTED_BACKEND_SERVICE,
        )


def test_verify_runtime_proves_digest_counts_and_alb_health():
    result = control.verify_runtime(
        sha=SHA,
        digest=DIGEST,
        backend_task_definition=NEW_BACKEND_TD,
        worker_task_definition=NEW_WORKER_TD,
        aws_json=_aws_runtime(deployed=True),
    )
    assert result["ok"] is True
    assert result["alb"]["healthy_target_count"] == 2
    for service in result["services"].values():
        assert service["running_count"] == service["desired_count"]
        assert service["pending_count"] == 0
        assert all(task["image_digest"] == DIGEST for task in service["tasks"])


def test_verify_runtime_retries_only_transient_ecs_convergence():
    stable_aws = _aws_runtime(deployed=True)
    describe_service_calls = 0
    sleeps = []

    def eventual_runtime(arguments):
        nonlocal describe_service_calls
        payload = stable_aws(arguments)
        if arguments[:2] == ["ecs", "describe-services"]:
            describe_service_calls += 1
            if describe_service_calls == 1:
                payload["services"][1]["deployments"][0][
                    "rolloutState"
                ] = "IN_PROGRESS"
                payload["services"][1]["deployments"].append(
                    {
                        "status": "ACTIVE",
                        "rolloutState": "COMPLETED",
                        "taskDefinition": WORKER_TD,
                    }
                )
        return payload

    result = control.verify_runtime(
        sha=SHA,
        digest=DIGEST,
        backend_task_definition=NEW_BACKEND_TD,
        worker_task_definition=NEW_WORKER_TD,
        aws_json=eventual_runtime,
        attempts=2,
        interval_seconds=3,
        sleeper=sleeps.append,
    )

    assert result["ok"] is True
    assert describe_service_calls == 3
    assert sleeps == [3]


def test_verify_runtime_retries_transient_alb_convergence():
    stable_aws = _aws_runtime(deployed=True)
    target_health_calls = 0
    sleeps = []

    def eventual_target_health(arguments):
        nonlocal target_health_calls
        payload = stable_aws(arguments)
        if arguments[:2] == ["elbv2", "describe-target-health"]:
            target_health_calls += 1
            if target_health_calls == 1:
                payload["TargetHealthDescriptions"].append(
                    {"TargetHealth": {"State": "draining"}}
                )
        return payload

    result = control.verify_runtime(
        sha=SHA,
        digest=DIGEST,
        backend_task_definition=NEW_BACKEND_TD,
        worker_task_definition=NEW_WORKER_TD,
        aws_json=eventual_target_health,
        attempts=2,
        interval_seconds=3,
        sleeper=sleeps.append,
    )

    assert result["ok"] is True
    assert target_health_calls == 3
    assert sleeps == [3]


def test_verify_runtime_default_alb_budget_outlasts_300_second_drain():
    stable_aws = _aws_runtime(deployed=True)
    target_health_calls = 0
    sleeps = []

    def boundary_target_health(arguments):
        nonlocal target_health_calls
        payload = stable_aws(arguments)
        if arguments[:2] == ["elbv2", "describe-target-health"]:
            target_health_calls += 1
            if target_health_calls <= 31:
                payload["TargetHealthDescriptions"].append(
                    {"TargetHealth": {"State": "draining"}}
                )
        return payload

    result = control.verify_runtime(
        sha=SHA,
        digest=DIGEST,
        backend_task_definition=NEW_BACKEND_TD,
        worker_task_definition=NEW_WORKER_TD,
        aws_json=boundary_target_health,
        interval_seconds=10,
        sleeper=sleeps.append,
    )

    assert result["ok"] is True
    assert result["alb"]["deregistration_delay_seconds"] == 300
    assert result["alb"]["verification_attempt_budget"] == 37
    assert target_health_calls == 33
    assert sleeps == [10] * 31


def test_verify_runtime_fails_immediately_on_failed_rollout():
    stable_aws = _aws_runtime(deployed=True)
    sleeps = []

    def failed_runtime(arguments):
        payload = stable_aws(arguments)
        if arguments[:2] == ["ecs", "describe-services"]:
            payload["services"][1]["deployments"][0]["rolloutState"] = "FAILED"
        return payload

    with pytest.raises(control.StagingControlError, match="rollout FAILED"):
        control.verify_runtime(
            sha=SHA,
            digest=DIGEST,
            backend_task_definition=NEW_BACKEND_TD,
            worker_task_definition=NEW_WORKER_TD,
            aws_json=failed_runtime,
            attempts=2,
            interval_seconds=3,
            sleeper=sleeps.append,
        )

    assert sleeps == []


def test_verify_runtime_fails_after_persistent_ecs_nonconvergence():
    stable_aws = _aws_runtime(deployed=True)
    sleeps = []

    def pending_runtime(arguments):
        payload = stable_aws(arguments)
        if arguments[:2] == ["ecs", "describe-services"]:
            payload["services"][1]["deployments"][0][
                "rolloutState"
            ] = "IN_PROGRESS"
        return payload

    with pytest.raises(control.StagingControlError, match="did not converge"):
        control.verify_runtime(
            sha=SHA,
            digest=DIGEST,
            backend_task_definition=NEW_BACKEND_TD,
            worker_task_definition=NEW_WORKER_TD,
            aws_json=pending_runtime,
            attempts=2,
            interval_seconds=3,
            sleeper=sleeps.append,
        )

    assert sleeps == [3]


def test_verify_runtime_fails_after_persistent_alb_nonconvergence():
    stable_aws = _aws_runtime(deployed=True)
    sleeps = []

    def draining_target(arguments):
        payload = stable_aws(arguments)
        if arguments[:2] == ["elbv2", "describe-target-health"]:
            payload["TargetHealthDescriptions"].append(
                {"TargetHealth": {"State": "draining"}}
            )
        return payload

    with pytest.raises(control.StagingControlError, match="target health"):
        control.verify_runtime(
            sha=SHA,
            digest=DIGEST,
            backend_task_definition=NEW_BACKEND_TD,
            worker_task_definition=NEW_WORKER_TD,
            aws_json=draining_target,
            attempts=2,
            interval_seconds=3,
            sleeper=sleeps.append,
        )

    assert sleeps == [3]


def test_verify_runtime_rejects_service_drift_after_alb_convergence():
    stable_aws = _aws_runtime(deployed=True)
    describe_service_calls = 0

    def drifting_runtime(arguments):
        nonlocal describe_service_calls
        payload = stable_aws(arguments)
        if arguments[:2] == ["ecs", "describe-services"]:
            describe_service_calls += 1
            if describe_service_calls == 2:
                worker = payload["services"][1]
                worker["taskDefinition"] = WORKER_TD
                worker["deployments"][0]["taskDefinition"] = WORKER_TD
        return payload

    with pytest.raises(control.StagingControlError, match="expected task definition"):
        control.verify_runtime(
            sha=SHA,
            digest=DIGEST,
            backend_task_definition=NEW_BACKEND_TD,
            worker_task_definition=NEW_WORKER_TD,
            aws_json=drifting_runtime,
            attempts=2,
            interval_seconds=3,
            sleeper=lambda seconds: None,
        )

    assert describe_service_calls == 2


def test_verify_runtime_fails_on_unhealthy_target():
    with pytest.raises(control.StagingControlError, match="target health"):
        control.verify_runtime(
            sha=SHA,
            digest=DIGEST,
            backend_task_definition=NEW_BACKEND_TD,
            worker_task_definition=NEW_WORKER_TD,
            aws_json=_aws_runtime(deployed=True, unhealthy_target=True),
            attempts=1,
        )


def test_verify_runtime_rechecks_repository_immutability():
    with pytest.raises(control.StagingControlError, match="immutable"):
        control.verify_runtime(
            sha=SHA,
            digest=DIGEST,
            backend_task_definition=NEW_BACKEND_TD,
            worker_task_definition=NEW_WORKER_TD,
            aws_json=_aws_runtime(
                deployed=True,
                mutable_repository=True,
            ),
        )


def test_cloudwatch_collection_is_mandatory_and_fail_closed():
    calls = []

    def clean(arguments):
        calls.append(arguments)
        return {"events": []}

    result = control.collect_cloudwatch(
        start_time_ms=100,
        now_ms=lambda: 200,
        aws_json=clean,
    )
    assert result["ok"] is True
    assert result["unexpected_event_count"] == 0
    assert len(calls) == len(control.LOG_PATTERNS)
    assert all("--no-paginate" not in arguments for arguments in calls)
    assert all("--max-items" not in arguments for arguments in calls)

    def error_event(arguments):
        return {"events": [{"message": "sensitive message is not persisted"}]}

    with pytest.raises(control.StagingControlError, match="error-pattern"):
        control.collect_cloudwatch(
            start_time_ms=100,
            now_ms=lambda: 200,
            aws_json=error_event,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"events": None},
        {"events": ""},
        {"events": {}},
        {"events": 0},
        {"events": False},
    ],
)
def test_cloudwatch_collection_rejects_missing_or_malformed_events(payload):
    with pytest.raises(control.StagingControlError, match="malformed events"):
        control.collect_cloudwatch(
            start_time_ms=100,
            now_ms=lambda: 200,
            aws_json=lambda arguments: payload,
        )


def _final_evidence_inputs():
    captured_at = dt.datetime(2026, 7, 30, 12, tzinfo=dt.timezone.utc)
    predeploy = control.capture_predeploy(
        aws_json=_aws_runtime(deployed=False),
        clock=lambda: captured_at,
    )
    image = {
        "schema_version": "release_image_evidence_v1",
        "ok": True,
        "image_tag": SHA,
        "image_digest": DIGEST,
        "image": IMAGE_PREFIX + SHA,
        "digest_uri": IMAGE_PREFIX.removesuffix(":") + "@" + DIGEST,
        "action": "built_and_pushed",
        "repository": {
            "account_id": control.EXPECTED_ACCOUNT_ID,
            "region": control.EXPECTED_REGION,
            "repository": control.EXPECTED_REPOSITORY,
            "repository_uri": IMAGE_PREFIX.removesuffix(":"),
            "image_tag_mutability": "IMMUTABLE",
            "scan_on_push": True,
        },
        "provenance": {
            "git_sha": SHA,
            "git_ref": "refs/heads/main",
            "build_time": "2026-07-30T12:00:00Z",
            "architecture": "amd64",
            "os": "linux",
        },
    }
    scan = {
        "schema_version": "container_scan_evidence_v1",
        "ok": True,
        "status": "COMPLETE",
        "repository": control.EXPECTED_REPOSITORY,
        "region": control.EXPECTED_REGION,
        "image_digest": DIGEST,
        "completed_at": "2026-07-30T12:01:00Z",
        "severity_counts": {
            "CRITICAL": 0,
            "HIGH": 0,
            "MEDIUM": 0,
            "LOW": 0,
            "INFORMATIONAL": 0,
            "UNDEFINED": 0,
        },
        "acceptance_manifest_version": (
            control.EXPECTED_ACCEPTANCE_MANIFEST_VERSION
        ),
        "accepted_highs": [],
        "unaccepted_highs": [],
    }
    trivy_scan = {
        "schema_version": "container_security_pr_scan_v1",
        "ok": True,
        "status": "COMPLETE",
        "scanner": control.EXPECTED_TRIVY_SCANNER,
        "image_tag": SHA,
        "image_digest": DIGEST,
        "artifact_type": "container_image",
        "severity_counts": {
            "CRITICAL": 0,
            "HIGH": 0,
            "MEDIUM": 0,
            "LOW": 0,
            "INFORMATIONAL": 0,
            "UNKNOWN": 0,
            "UNDEFINED": 0,
        },
        "acceptance_manifest_version": (
            control.EXPECTED_ACCEPTANCE_MANIFEST_VERSION
        ),
        "accepted_highs": [],
        "unaccepted_highs": [],
        "targets": [
            {"target": "alpine", "class": "os-pkgs", "type": "alpine"},
            {
                "target": "Python",
                "class": "lang-pkgs",
                "type": "python-pkg",
            },
        ],
    }
    runtime = control.verify_runtime(
        sha=SHA,
        digest=DIGEST,
        backend_task_definition=NEW_BACKEND_TD,
        worker_task_definition=NEW_WORKER_TD,
        aws_json=_aws_runtime(deployed=True),
    )
    authenticated = {
        "ok": True,
        "api_base": "https://staging.regmind.co/api",
        "expected_sha": SHA,
        "expected_environment": "staging",
        "files": {
            name: f"/safe/{name}.json"
            for name in (
                "health",
                "liveness",
                "version",
                "runtime_baseline",
                "summary",
            )
        },
        "version": {
            "git_sha": SHA,
            "image_tag": SHA,
            "environment": "staging",
            "git_sha_matches_expected": True,
            "image_tag_matches_expected": True,
            "environment_matches_expected": True,
        },
    }
    cloudwatch = control.collect_cloudwatch(
        start_time_ms=predeploy["window_start_epoch_ms"],
        now_ms=lambda: predeploy["window_start_epoch_ms"] + 1,
        aws_json=lambda arguments: {"events": []},
    )
    return (
        image,
        scan,
        trivy_scan,
        predeploy,
        runtime,
        authenticated,
        cloudwatch,
    )


def _finalize(values, *, authenticated=None):
    return control.finalize_evidence(
        reviewed_sha=SHA,
        image=values[0],
        scan=values[1],
        trivy_scan=values[2],
        predeploy=values[3],
        runtime=values[4],
        authenticated=values[5] if authenticated is None else authenticated,
        cloudwatch=values[6],
        workflow_run_url=(
            "https://github.com/onboarda1234/onboarda/actions/runs/1"
        ),
        workflow_run_id="1",
        workflow_run_attempt="1",
        actor="operator",
    )


def test_final_evidence_requires_all_controls_and_remains_sanitized():
    values = _final_evidence_inputs()
    result = _finalize(values)
    assert result["ok"] is True
    assert result["rollback"]["backend_task_definition"] == BACKEND_TD
    assert "Bearer" not in json.dumps(result)

    bad_authenticated = dict(values[5])
    bad_authenticated["version"] = dict(
        values[5]["version"],
        environment="production",
    )
    with pytest.raises(control.StagingControlError, match="/api/version"):
        _finalize(values, authenticated=bad_authenticated)


@pytest.mark.parametrize(
    ("evidence_index", "field"),
    [
        (0, "provenance"),
        (1, "acceptance_manifest_version"),
        (2, "targets"),
        (3, "services"),
        (4, "services"),
        (6, "queries"),
    ],
)
def test_final_evidence_rejects_missing_mandatory_details(
    evidence_index,
    field,
):
    values = list(_final_evidence_inputs())
    evidence = json.loads(json.dumps(values[evidence_index], default=str))
    evidence.pop(field, None)
    values[evidence_index] = evidence
    with pytest.raises(control.StagingControlError):
        _finalize(values)


def test_final_evidence_rejects_unlinked_rollback_commands():
    values = list(_final_evidence_inputs())
    predeploy = json.loads(json.dumps(values[3], default=str))
    predeploy["rollback"]["commands"] = ["backend", "worker", "wait"]
    values[3] = predeploy
    with pytest.raises(control.StagingControlError, match="rollback commands"):
        _finalize(values)
