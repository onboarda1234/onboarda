#!/usr/bin/env python3
"""Sanitized staging deployment, runtime, rollback, and evidence controls.

All persisted output is allowlisted.  Raw task-definition environments, secret
values, log messages, and bearer tokens are never written to evidence files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


EXPECTED_ACCOUNT_ID = "782913119880"
EXPECTED_REGION = "af-south-1"
EXPECTED_CLUSTER = "regmind-staging"
EXPECTED_REPOSITORY = "regmind-backend"
EXPECTED_BACKEND_SERVICE = "regmind-backend"
EXPECTED_WORKER_SERVICE = "regmind-verification-worker"
EXPECTED_CONTAINER = "regmind-backend"
EXPECTED_ACCEPTANCE_MANIFEST_VERSION = "container_high_acceptances_v1"
EXPECTED_REPOSITORY_URI = (
    "782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend"
)
EXPECTED_TRIVY_SCANNER = (
    "aquasec/trivy:0.72.0@"
    "sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
)
SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
HASH_RE = re.compile(r"[0-9a-f]{64}")
TASK_DEFINITION_RE = re.compile(
    r"arn:aws:ecs:af-south-1:782913119880:task-definition/"
    r"(regmind-staging|regmind-verification-worker):[1-9][0-9]*"
)
PROVENANCE_ENV = {
    "BUILD_TIME",
    "ENVIRONMENT",
    "GIT_SHA",
    "IMAGE_TAG",
    "SERVICE_NAME",
}
LOG_PATTERNS = (
    "ERROR",
    "CRITICAL",
    "Traceback",
    "connection pool exhausted",
    "falling back to mock mode",
)


class StagingControlError(RuntimeError):
    """Raised when required staging release evidence cannot be proven."""


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        executable = Path(command[0]).name if command else "<command>"
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise StagingControlError(f"{executable} failed: {detail}")
    return result


def _aws_json(
    arguments: Sequence[str],
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run,
) -> dict[str, Any]:
    result = runner(["aws", *arguments, "--output", "json"])
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise StagingControlError("AWS CLI returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise StagingControlError("AWS CLI returned a non-object response")
    return payload


def _validate_target(region: str, cluster: str) -> None:
    if region != EXPECTED_REGION or cluster != EXPECTED_CLUSTER:
        raise StagingControlError("release control is pinned to the staging cluster")


def _validate_sha(sha: str) -> str:
    sha = str(sha or "").strip()
    if not SHA_RE.fullmatch(sha):
        raise StagingControlError("expected a full 40-character lowercase Git SHA")
    return sha


def _image_tag(image: str) -> str:
    image = str(image or "")
    if "@" in image:
        image = image.split("@", 1)[0]
    if ":" not in image:
        return ""
    return image.rsplit(":", 1)[1]


def _container_environment(container: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name")): str(item.get("value") or "")
        for item in container.get("environment") or []
        if isinstance(item, Mapping) and item.get("name") in PROVENANCE_ENV
    }


def _release_container(task_definition: Mapping[str, Any]) -> Mapping[str, Any]:
    matches = [
        item
        for item in task_definition.get("containerDefinitions") or []
        if isinstance(item, Mapping) and item.get("name") == EXPECTED_CONTAINER
    ]
    if len(matches) != 1:
        raise StagingControlError(
            "task definition must contain exactly one regmind-backend container"
        )
    return matches[0]


def _task_definition_summary(task_definition: Mapping[str, Any]) -> dict[str, Any]:
    arn = str(task_definition.get("taskDefinitionArn") or "")
    if not TASK_DEFINITION_RE.fullmatch(arn):
        raise StagingControlError("task-definition ARN is outside the staging families")
    container = _release_container(task_definition)
    return {
        "task_definition_arn": arn,
        "family": task_definition.get("family"),
        "revision": task_definition.get("revision"),
        "image": container.get("image"),
        "image_tag": _image_tag(str(container.get("image") or "")),
        "environment": _container_environment(container),
    }


def _healthy_service(service: Mapping[str, Any]) -> dict[str, Any]:
    deployments = service.get("deployments") or []
    primary = [
        item
        for item in deployments
        if isinstance(item, Mapping) and item.get("status") == "PRIMARY"
    ]
    desired = int(service.get("desiredCount") or 0)
    running = int(service.get("runningCount") or 0)
    pending = int(service.get("pendingCount") or 0)
    if (
        service.get("status") != "ACTIVE"
        or desired <= 0
        or running != desired
        or pending != 0
        or len(deployments) != 1
        or len(primary) != 1
        or primary[0].get("rolloutState") != "COMPLETED"
    ):
        raise StagingControlError(
            f"ECS service {service.get('serviceName') or '<missing>'} is not stable"
        )
    task_definition = str(service.get("taskDefinition") or "")
    if not TASK_DEFINITION_RE.fullmatch(task_definition):
        raise StagingControlError("live service task-definition ARN is invalid")
    return {
        "service_name": service.get("serviceName"),
        "status": "ACTIVE",
        "desired_count": desired,
        "running_count": running,
        "pending_count": pending,
        "task_definition_arn": task_definition,
        "rollout_state": "COMPLETED",
    }


def _service_map(
    payload: Mapping[str, Any],
    names: Iterable[str],
) -> dict[str, Mapping[str, Any]]:
    if payload.get("failures"):
        raise StagingControlError("ECS describe-services returned failures")
    services = {
        str(item.get("serviceName")): item
        for item in payload.get("services") or []
        if isinstance(item, Mapping)
    }
    expected = set(names)
    if set(services) != expected:
        raise StagingControlError("ECS service inventory does not match release scope")
    return services


def _ecr_digest(
    *,
    image_tag: str,
    region: str,
    aws_json: Callable[[Sequence[str]], dict[str, Any]],
) -> str:
    if not SHA_RE.fullmatch(image_tag):
        raise StagingControlError("live task definition does not use a full-SHA tag")
    payload = aws_json(
        [
            "ecr",
            "describe-images",
            "--repository-name",
            EXPECTED_REPOSITORY,
            "--image-ids",
            f"imageTag={image_tag}",
            "--region",
            region,
        ]
    )
    details = payload.get("imageDetails") or []
    if len(details) != 1:
        raise StagingControlError("rollback image tag is missing or ambiguous")
    digest = str(details[0].get("imageDigest") or "")
    if not DIGEST_RE.fullmatch(digest):
        raise StagingControlError("rollback image digest is malformed")
    if image_tag not in (details[0].get("imageTags") or []):
        raise StagingControlError("rollback image tag-to-digest mapping is inconsistent")
    return digest


def _ecr_repository_controls(
    *,
    region: str,
    aws_json: Callable[[Sequence[str]], dict[str, Any]],
) -> dict[str, Any]:
    payload = aws_json(
        [
            "ecr",
            "describe-repositories",
            "--repository-names",
            EXPECTED_REPOSITORY,
            "--region",
            region,
        ]
    )
    repositories = payload.get("repositories") or []
    if len(repositories) != 1:
        raise StagingControlError("expected exactly one staging ECR repository")
    repository = repositories[0]
    scan_on_push = bool(
        (repository.get("imageScanningConfiguration") or {}).get("scanOnPush")
    )
    if (
        repository.get("repositoryName") != EXPECTED_REPOSITORY
        or str(repository.get("registryId") or "") != EXPECTED_ACCOUNT_ID
        or repository.get("imageTagMutability") != "IMMUTABLE"
        or not scan_on_push
    ):
        raise StagingControlError(
            "staging ECR repository is not the pinned immutable scan-on-push target"
        )
    return {
        "repository": EXPECTED_REPOSITORY,
        "registry_id": EXPECTED_ACCOUNT_ID,
        "region": region,
        "image_tag_mutability": "IMMUTABLE",
        "scan_on_push": True,
    }


def _running_tasks(
    *,
    service_name: str,
    expected_task_definition: str,
    expected_tag: str,
    expected_digest: str,
    expected_count: int,
    region: str,
    cluster: str,
    aws_json: Callable[[Sequence[str]], dict[str, Any]],
) -> list[dict[str, Any]]:
    listed = aws_json(
        [
            "ecs",
            "list-tasks",
            "--cluster",
            cluster,
            "--service-name",
            service_name,
            "--desired-status",
            "RUNNING",
            "--region",
            region,
        ]
    )
    task_arns = listed.get("taskArns") or []
    if len(task_arns) != expected_count:
        raise StagingControlError(
            f"{service_name} running task count does not match desired count"
        )
    described = aws_json(
        [
            "ecs",
            "describe-tasks",
            "--cluster",
            cluster,
            "--tasks",
            *task_arns,
            "--region",
            region,
        ]
    )
    if described.get("failures"):
        raise StagingControlError("ECS describe-tasks returned failures")
    tasks = described.get("tasks") or []
    if len(tasks) != expected_count:
        raise StagingControlError("ECS task evidence is incomplete")
    summaries: list[dict[str, Any]] = []
    for task in tasks:
        if (
            task.get("lastStatus") != "RUNNING"
            or task.get("taskDefinitionArn") != expected_task_definition
        ):
            raise StagingControlError(f"{service_name} has a stale running task")
        containers = [
            item
            for item in task.get("containers") or []
            if isinstance(item, Mapping) and item.get("name") == EXPECTED_CONTAINER
        ]
        if len(containers) != 1:
            raise StagingControlError("running task container evidence is ambiguous")
        container = containers[0]
        if (
            container.get("lastStatus") != "RUNNING"
            or _image_tag(str(container.get("image") or "")) != expected_tag
            or container.get("imageDigest") != expected_digest
        ):
            raise StagingControlError(
                f"{service_name} running task image digest/tag mismatch"
            )
        summaries.append(
            {
                "task_arn_hash": hashlib.sha256(
                    str(task.get("taskArn") or "").encode("utf-8")
                ).hexdigest(),
                "task_definition_arn": expected_task_definition,
                "last_status": "RUNNING",
                "image_tag": expected_tag,
                "image_digest": expected_digest,
            }
        )
    return summaries


def capture_predeploy(
    *,
    region: str = EXPECTED_REGION,
    cluster: str = EXPECTED_CLUSTER,
    aws_json: Callable[[Sequence[str]], dict[str, Any]] = _aws_json,
    clock: Callable[[], dt.datetime] | None = None,
) -> dict[str, Any]:
    _validate_target(region, cluster)
    timestamp = (clock or (lambda: dt.datetime.now(dt.timezone.utc)))()
    services_payload = aws_json(
        [
            "ecs",
            "describe-services",
            "--cluster",
            cluster,
            "--services",
            EXPECTED_BACKEND_SERVICE,
            EXPECTED_WORKER_SERVICE,
            "--region",
            region,
        ]
    )
    services = _service_map(
        services_payload,
        (EXPECTED_BACKEND_SERVICE, EXPECTED_WORKER_SERVICE),
    )
    result_services: dict[str, Any] = {}
    task_definitions: dict[str, Any] = {}
    for label, name in (
        ("backend", EXPECTED_BACKEND_SERVICE),
        ("worker", EXPECTED_WORKER_SERVICE),
    ):
        service_summary = _healthy_service(services[name])
        task_payload = aws_json(
            [
                "ecs",
                "describe-task-definition",
                "--task-definition",
                service_summary["task_definition_arn"],
                "--region",
                region,
            ]
        )
        task_summary = _task_definition_summary(
            task_payload.get("taskDefinition") or {}
        )
        environment = task_summary["environment"]
        tag = task_summary["image_tag"]
        if (
            environment.get("GIT_SHA") != tag
            or environment.get("IMAGE_TAG") != tag
            or environment.get("ENVIRONMENT") != "staging"
            or environment.get("SERVICE_NAME") != name
            or not environment.get("BUILD_TIME")
        ):
            raise StagingControlError(
                f"{name} live task-definition provenance is inconsistent"
            )
        result_services[label] = service_summary
        task_definitions[label] = task_summary
    if task_definitions["backend"]["image_tag"] != task_definitions["worker"]["image_tag"]:
        raise StagingControlError("backend and worker are not on one rollback SHA")
    rollback_tag = task_definitions["backend"]["image_tag"]
    rollback_digest = _ecr_digest(
        image_tag=rollback_tag,
        region=region,
        aws_json=aws_json,
    )

    for label, name in (
        ("backend", EXPECTED_BACKEND_SERVICE),
        ("worker", EXPECTED_WORKER_SERVICE),
    ):
        result_services[label]["tasks"] = _running_tasks(
            service_name=name,
            expected_task_definition=result_services[label]["task_definition_arn"],
            expected_tag=rollback_tag,
            expected_digest=rollback_digest,
            expected_count=result_services[label]["desired_count"],
            region=region,
            cluster=cluster,
            aws_json=aws_json,
        )
    backend_td = result_services["backend"]["task_definition_arn"]
    worker_td = result_services["worker"]["task_definition_arn"]
    return {
        "schema_version": "staging_predeploy_evidence_v1",
        "ok": True,
        "captured_at": timestamp.isoformat(),
        "window_start_epoch_ms": int(timestamp.timestamp() * 1000),
        "region": region,
        "cluster": cluster,
        "services": result_services,
        "task_definitions": task_definitions,
        "rollback": {
            "image_tag": rollback_tag,
            "image_digest": rollback_digest,
            "backend_task_definition": backend_td,
            "worker_task_definition": worker_td,
            "commands": [
                (
                    "aws ecs update-service --cluster regmind-staging "
                    "--service regmind-backend "
                    f"--task-definition {backend_td} --force-new-deployment "
                    "--region af-south-1"
                ),
                (
                    "aws ecs update-service --cluster regmind-staging "
                    "--service regmind-verification-worker "
                    f"--task-definition {worker_td} --force-new-deployment "
                    "--region af-south-1"
                ),
                (
                    "aws ecs wait services-stable --cluster regmind-staging "
                    "--services regmind-backend regmind-verification-worker "
                    "--region af-south-1"
                ),
            ],
        },
    }


def render_task_definition(
    *,
    source_task_definition: Mapping[str, Any],
    image: str,
    sha: str,
    build_time: str,
    service_name: str,
) -> dict[str, Any]:
    sha = _validate_sha(sha)
    if service_name not in {
        EXPECTED_BACKEND_SERVICE,
        EXPECTED_WORKER_SERVICE,
    }:
        raise StagingControlError("unexpected release service")
    if _image_tag(image) != sha:
        raise StagingControlError("new task-definition image must use the exact SHA tag")
    # Round-trip through JSON to create a deterministic deep copy.
    task_definition = json.loads(json.dumps(source_task_definition))
    container = _release_container(task_definition)
    container["image"] = image
    environment = container.setdefault("environment", [])

    def upsert(name: str, value: str) -> None:
        for item in environment:
            if item.get("name") == name:
                item["value"] = value
                return
        environment.append({"name": name, "value": value})

    upsert("ENVIRONMENT", "staging")
    upsert("SERVICE_NAME", service_name)
    upsert("GIT_SHA", sha)
    upsert("BUILD_TIME", build_time)
    upsert("IMAGE_TAG", sha)
    for key in (
        "taskDefinitionArn",
        "revision",
        "status",
        "requiresAttributes",
        "compatibilities",
        "registeredAt",
        "registeredBy",
        "deregisteredAt",
    ):
        task_definition.pop(key, None)
    return task_definition


def render_live_task_definition(
    *,
    source_task_definition_arn: str,
    image: str,
    sha: str,
    build_time: str,
    service_name: str,
    region: str = EXPECTED_REGION,
    aws_json: Callable[[Sequence[str]], dict[str, Any]] = _aws_json,
) -> dict[str, Any]:
    if not TASK_DEFINITION_RE.fullmatch(source_task_definition_arn):
        raise StagingControlError("source task-definition ARN is invalid")
    payload = aws_json(
        [
            "ecs",
            "describe-task-definition",
            "--task-definition",
            source_task_definition_arn,
            "--region",
            region,
        ]
    )
    return render_task_definition(
        source_task_definition=payload.get("taskDefinition") or {},
        image=image,
        sha=sha,
        build_time=build_time,
        service_name=service_name,
    )


def verify_runtime(
    *,
    sha: str,
    digest: str,
    backend_task_definition: str,
    worker_task_definition: str,
    region: str = EXPECTED_REGION,
    cluster: str = EXPECTED_CLUSTER,
    aws_json: Callable[[Sequence[str]], dict[str, Any]] = _aws_json,
) -> dict[str, Any]:
    sha = _validate_sha(sha)
    if not DIGEST_RE.fullmatch(digest):
        raise StagingControlError("expected image digest is malformed")
    if not TASK_DEFINITION_RE.fullmatch(backend_task_definition):
        raise StagingControlError("expected backend task-definition ARN is invalid")
    if not TASK_DEFINITION_RE.fullmatch(worker_task_definition):
        raise StagingControlError("expected worker task-definition ARN is invalid")
    _validate_target(region, cluster)

    # Re-check repository controls after deployment as well as re-resolving the
    # immutable tag, so a mid-run configuration downgrade cannot pass.
    repository_controls = _ecr_repository_controls(
        region=region,
        aws_json=aws_json,
    )
    observed_digest = _ecr_digest(
        image_tag=sha,
        region=region,
        aws_json=aws_json,
    )
    if observed_digest != digest:
        raise StagingControlError("ECR SHA tag no longer resolves to the scanned digest")

    services_payload = aws_json(
        [
            "ecs",
            "describe-services",
            "--cluster",
            cluster,
            "--services",
            EXPECTED_BACKEND_SERVICE,
            EXPECTED_WORKER_SERVICE,
            "--region",
            region,
        ]
    )
    services = _service_map(
        services_payload,
        (EXPECTED_BACKEND_SERVICE, EXPECTED_WORKER_SERVICE),
    )
    expected_task_definitions = {
        "backend": backend_task_definition,
        "worker": worker_task_definition,
    }
    result_services: dict[str, Any] = {}
    for label, name in (
        ("backend", EXPECTED_BACKEND_SERVICE),
        ("worker", EXPECTED_WORKER_SERVICE),
    ):
        summary = _healthy_service(services[name])
        expected_td = expected_task_definitions[label]
        if summary["task_definition_arn"] != expected_td:
            raise StagingControlError(f"{name} did not activate the expected task definition")
        task_payload = aws_json(
            [
                "ecs",
                "describe-task-definition",
                "--task-definition",
                expected_td,
                "--region",
                region,
            ]
        )
        task_summary = _task_definition_summary(
            task_payload.get("taskDefinition") or {}
        )
        environment = task_summary["environment"]
        if (
            task_summary["image_tag"] != sha
            or environment.get("GIT_SHA") != sha
            or environment.get("IMAGE_TAG") != sha
            or environment.get("ENVIRONMENT") != "staging"
            or environment.get("SERVICE_NAME") != name
        ):
            raise StagingControlError(f"{name} task-definition provenance mismatch")
        summary["tasks"] = _running_tasks(
            service_name=name,
            expected_task_definition=expected_td,
            expected_tag=sha,
            expected_digest=digest,
            expected_count=summary["desired_count"],
            region=region,
            cluster=cluster,
            aws_json=aws_json,
        )
        summary["task_definition"] = task_summary
        result_services[label] = summary

    load_balancers = services[EXPECTED_BACKEND_SERVICE].get("loadBalancers") or []
    if len(load_balancers) != 1:
        raise StagingControlError("backend must have exactly one ALB target group")
    target_group_arn = str(load_balancers[0].get("targetGroupArn") or "")
    if not target_group_arn:
        raise StagingControlError("backend ALB target-group ARN is missing")
    target_payload = aws_json(
        [
            "elbv2",
            "describe-target-health",
            "--target-group-arn",
            target_group_arn,
            "--region",
            region,
        ]
    )
    descriptions = target_payload.get("TargetHealthDescriptions") or []
    desired = result_services["backend"]["desired_count"]
    states = [
        str((item.get("TargetHealth") or {}).get("State") or "")
        for item in descriptions
    ]
    if len(states) != desired or any(state != "healthy" for state in states):
        raise StagingControlError("ALB target health is incomplete or unhealthy")
    return {
        "schema_version": "staging_runtime_release_evidence_v1",
        "ok": True,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "region": region,
        "cluster": cluster,
        "git_sha": sha,
        "image_digest": digest,
        "repository_controls": repository_controls,
        "services": result_services,
        "alb": {
            "target_group_arn": target_group_arn,
            "expected_target_count": desired,
            "healthy_target_count": len(states),
            "states": states,
        },
    }


def collect_cloudwatch(
    *,
    start_time_ms: int,
    log_group: str = "/ecs/regmind-staging",
    region: str = EXPECTED_REGION,
    aws_json: Callable[[Sequence[str]], dict[str, Any]] = _aws_json,
    now_ms: Callable[[], int] | None = None,
) -> dict[str, Any]:
    if start_time_ms <= 0:
        raise StagingControlError("CloudWatch review start time is invalid")
    end_time_ms = (now_ms or (lambda: int(time.time() * 1000)))()
    if end_time_ms < start_time_ms:
        raise StagingControlError("CloudWatch review window is invalid")
    results: list[dict[str, Any]] = []
    unexpected_total = 0
    for pattern in LOG_PATTERNS:
        # Keep the AWS CLI paginator enabled. CloudWatch can return an empty
        # service page with a continuation token; the CLI follows and aggregates
        # those pages unless --no-paginate is supplied.
        arguments = [
            "logs",
            "filter-log-events",
            "--log-group-name",
            log_group,
            "--start-time",
            str(start_time_ms),
            "--end-time",
            str(end_time_ms),
            "--filter-pattern",
            f'"{pattern}"',
            "--region",
            region,
        ]
        payload = aws_json(arguments)
        events = payload.get("events")
        if not isinstance(events, list):
            raise StagingControlError(
                "CloudWatch filter response contains malformed events"
            )
        count = len(events)
        unexpected_total += count
        results.append({"pattern": pattern, "count": count})
    if unexpected_total:
        raise StagingControlError(
            f"CloudWatch release window contains {unexpected_total} error-pattern event(s)"
        )
    return {
        "schema_version": "cloudwatch_release_review_v1",
        "ok": True,
        "region": region,
        "log_group": log_group,
        "start_time_ms": start_time_ms,
        "end_time_ms": end_time_ms,
        "queries": results,
        "unexpected_event_count": 0,
    }


def _validate_service_inventory(
    services: Any,
    *,
    expected_tag: str,
    expected_digest: str,
    expected_task_definitions: Mapping[str, str],
    require_task_definition_summary: bool,
) -> None:
    if not isinstance(services, Mapping) or set(services) != {"backend", "worker"}:
        raise StagingControlError(
            "backend and worker service evidence is missing or ambiguous"
        )
    expected_names = {
        "backend": EXPECTED_BACKEND_SERVICE,
        "worker": EXPECTED_WORKER_SERVICE,
    }
    expected_families = {
        "backend": "regmind-staging",
        "worker": "regmind-verification-worker",
    }
    for label in ("backend", "worker"):
        service = services.get(label)
        if not isinstance(service, Mapping):
            raise StagingControlError(f"{label} service evidence is malformed")
        expected_name = expected_names[label]
        expected_task_definition = expected_task_definitions.get(label, "")
        desired = int(service.get("desired_count") or 0)
        task_definition = str(service.get("task_definition_arn") or "")
        if (
            service.get("service_name") != expected_name
            or service.get("status") != "ACTIVE"
            or desired <= 0
            or int(service.get("running_count") or 0) != desired
            or int(service.get("pending_count") or 0) != 0
            or service.get("rollout_state") != "COMPLETED"
            or task_definition != expected_task_definition
            or not TASK_DEFINITION_RE.fullmatch(task_definition)
            or f"/{expected_families[label]}:" not in task_definition
        ):
            raise StagingControlError(f"{label} service evidence is incomplete")
        tasks = service.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != desired:
            raise StagingControlError(f"{label} running-task evidence is incomplete")
        task_hashes: set[str] = set()
        for task in tasks:
            if not isinstance(task, Mapping):
                raise StagingControlError(f"{label} running-task evidence is malformed")
            task_hash = str(task.get("task_arn_hash") or "")
            if (
                not HASH_RE.fullmatch(task_hash)
                or task_hash in task_hashes
                or task.get("task_definition_arn") != task_definition
                or task.get("last_status") != "RUNNING"
                or task.get("image_tag") != expected_tag
                or task.get("image_digest") != expected_digest
            ):
                raise StagingControlError(
                    f"{label} running-task provenance is incomplete"
                )
            task_hashes.add(task_hash)
        if require_task_definition_summary:
            summary = service.get("task_definition")
            if not isinstance(summary, Mapping):
                raise StagingControlError(
                    f"{label} task-definition evidence is missing"
                )
            environment = summary.get("environment")
            if (
                summary.get("task_definition_arn") != task_definition
                or summary.get("image_tag") != expected_tag
                or not isinstance(environment, Mapping)
                or environment.get("GIT_SHA") != expected_tag
                or environment.get("IMAGE_TAG") != expected_tag
                or environment.get("ENVIRONMENT") != "staging"
                or environment.get("SERVICE_NAME") != expected_name
                or not environment.get("BUILD_TIME")
            ):
                raise StagingControlError(
                    f"{label} task-definition provenance is incomplete"
                )


def _validate_predeploy_evidence(
    predeploy: Mapping[str, Any],
    *,
    rollback: Mapping[str, Any],
    rollback_tag: str,
    rollback_digest: str,
) -> None:
    if (
        predeploy.get("schema_version") != "staging_predeploy_evidence_v1"
        or predeploy.get("region") != EXPECTED_REGION
        or predeploy.get("cluster") != EXPECTED_CLUSTER
        or not predeploy.get("captured_at")
        or int(predeploy.get("window_start_epoch_ms") or 0) <= 0
    ):
        raise StagingControlError("pre-deployment evidence is incomplete")
    backend_td = str(rollback.get("backend_task_definition") or "")
    worker_td = str(rollback.get("worker_task_definition") or "")
    expected_task_definitions = {
        "backend": backend_td,
        "worker": worker_td,
    }
    _validate_service_inventory(
        predeploy.get("services"),
        expected_tag=rollback_tag,
        expected_digest=rollback_digest,
        expected_task_definitions=expected_task_definitions,
        require_task_definition_summary=False,
    )
    summaries = predeploy.get("task_definitions")
    if not isinstance(summaries, Mapping) or set(summaries) != {"backend", "worker"}:
        raise StagingControlError("pre-deployment task-definition evidence is missing")
    for label, service_name in (
        ("backend", EXPECTED_BACKEND_SERVICE),
        ("worker", EXPECTED_WORKER_SERVICE),
    ):
        summary = summaries.get(label)
        if not isinstance(summary, Mapping):
            raise StagingControlError(
                f"pre-deployment {label} task-definition evidence is malformed"
            )
        environment = summary.get("environment")
        if (
            summary.get("task_definition_arn")
            != expected_task_definitions[label]
            or summary.get("image_tag") != rollback_tag
            or not isinstance(environment, Mapping)
            or environment.get("GIT_SHA") != rollback_tag
            or environment.get("IMAGE_TAG") != rollback_tag
            or environment.get("ENVIRONMENT") != "staging"
            or environment.get("SERVICE_NAME") != service_name
            or not environment.get("BUILD_TIME")
        ):
            raise StagingControlError(
                f"pre-deployment {label} task-definition provenance is incomplete"
            )


def _validate_cloudwatch_evidence(cloudwatch: Mapping[str, Any]) -> None:
    queries = cloudwatch.get("queries")
    if (
        cloudwatch.get("schema_version") != "cloudwatch_release_review_v1"
        or cloudwatch.get("region") != EXPECTED_REGION
        or cloudwatch.get("log_group") != "/ecs/regmind-staging"
        or int(cloudwatch.get("start_time_ms") or 0) <= 0
        or int(cloudwatch.get("end_time_ms") or 0)
        < int(cloudwatch.get("start_time_ms") or 0)
        or int(cloudwatch.get("unexpected_event_count") or 0) != 0
        or not isinstance(queries, list)
        or len(queries) != len(LOG_PATTERNS)
    ):
        raise StagingControlError("CloudWatch release evidence is inconsistent")
    if [
        (item.get("pattern"), item.get("count"))
        for item in queries
        if isinstance(item, Mapping)
    ] != [(pattern, 0) for pattern in LOG_PATTERNS]:
        raise StagingControlError("CloudWatch pattern evidence is incomplete")


def _validate_high_acceptance_evidence(rows: list[Any]) -> None:
    required = {
        "finding_id",
        "package_name",
        "installed_version",
        "owner",
        "technical_justification",
        "reachability_analysis",
        "compensating_controls",
        "approved_by",
        "expires_at",
    }
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or any(not str(row.get(field) or "").strip() for field in required)
        ):
            raise StagingControlError("accepted HIGH governance evidence is incomplete")
        try:
            expires_at = dt.date.fromisoformat(str(row["expires_at"]))
        except ValueError as exc:
            raise StagingControlError(
                "accepted HIGH expiry evidence is malformed"
            ) from exc
        if expires_at <= dt.datetime.now(dt.timezone.utc).date():
            raise StagingControlError("accepted HIGH evidence is expired")


def finalize_evidence(
    *,
    reviewed_sha: str,
    image: Mapping[str, Any],
    scan: Mapping[str, Any],
    trivy_scan: Mapping[str, Any],
    predeploy: Mapping[str, Any],
    runtime: Mapping[str, Any],
    authenticated: Mapping[str, Any],
    cloudwatch: Mapping[str, Any],
    workflow_run_url: str,
    workflow_run_id: str,
    workflow_run_attempt: str,
    actor: str,
) -> dict[str, Any]:
    reviewed_sha = _validate_sha(reviewed_sha)
    required_ok = {
        "image": image,
        "scan": scan,
        "trivy_scan": trivy_scan,
        "predeploy": predeploy,
        "runtime": runtime,
        "authenticated": authenticated,
        "cloudwatch": cloudwatch,
    }
    for label, evidence in required_ok.items():
        if evidence.get("ok") is not True:
            raise StagingControlError(f"{label} evidence is missing or unsuccessful")
    digest = str(image.get("image_digest") or "")
    if not DIGEST_RE.fullmatch(digest):
        raise StagingControlError("release evidence image digest is invalid")
    image_repository = image.get("repository") or {}
    image_provenance = image.get("provenance") or {}
    runtime_repository = runtime.get("repository_controls") or {}
    severity_counts = scan.get("severity_counts") or {}
    accepted_highs = scan.get("accepted_highs")
    trivy_counts = trivy_scan.get("severity_counts") or {}
    trivy_accepted_highs = trivy_scan.get("accepted_highs")
    if not isinstance(accepted_highs, list) or not isinstance(
        trivy_accepted_highs, list
    ):
        raise StagingControlError("accepted HIGH evidence is malformed")
    _validate_high_acceptance_evidence(accepted_highs)
    _validate_high_acceptance_evidence(trivy_accepted_highs)
    expected_image = f"{EXPECTED_REPOSITORY_URI}:{reviewed_sha}"
    expected_digest_uri = f"{EXPECTED_REPOSITORY_URI}@{digest}"
    if (
        image.get("schema_version") != "release_image_evidence_v1"
        or image.get("action") not in {"built_and_pushed", "reused"}
        or image.get("image") != expected_image
        or image.get("digest_uri") != expected_digest_uri
        or image.get("image_tag") != reviewed_sha
        or image_repository.get("account_id") != EXPECTED_ACCOUNT_ID
        or image_repository.get("region") != EXPECTED_REGION
        or image_repository.get("repository") != EXPECTED_REPOSITORY
        or image_repository.get("repository_uri") != EXPECTED_REPOSITORY_URI
        or image_repository.get("image_tag_mutability") != "IMMUTABLE"
        or image_repository.get("scan_on_push") is not True
        or image_provenance.get("git_sha") != reviewed_sha
        or image_provenance.get("git_ref") != "refs/heads/main"
        or not image_provenance.get("build_time")
        or image_provenance.get("architecture") != "amd64"
        or image_provenance.get("os") != "linux"
        or scan.get("schema_version") != "container_scan_evidence_v1"
        or scan.get("repository") != EXPECTED_REPOSITORY
        or scan.get("region") != EXPECTED_REGION
        or scan.get("image_digest") != digest
        or scan.get("status") != "COMPLETE"
        or not scan.get("completed_at")
        or scan.get("acceptance_manifest_version")
        != EXPECTED_ACCEPTANCE_MANIFEST_VERSION
        or not {
            "CRITICAL",
            "HIGH",
            "MEDIUM",
            "LOW",
            "INFORMATIONAL",
            "UNDEFINED",
        }
        <= set(severity_counts)
        or int(severity_counts.get("CRITICAL") or 0) != 0
        or int(severity_counts.get("HIGH") or 0) != len(accepted_highs)
        or scan.get("unaccepted_highs") != []
        or trivy_scan.get("schema_version")
        != "container_security_pr_scan_v1"
        or trivy_scan.get("status") != "COMPLETE"
        or trivy_scan.get("image_tag") != reviewed_sha
        or trivy_scan.get("image_digest") != digest
        or trivy_scan.get("artifact_type") not in {
            "container_image",
            "container image",
        }
        or trivy_scan.get("scanner") != EXPECTED_TRIVY_SCANNER
        or trivy_scan.get("acceptance_manifest_version")
        != EXPECTED_ACCEPTANCE_MANIFEST_VERSION
        or not {
            "CRITICAL",
            "HIGH",
            "MEDIUM",
            "LOW",
            "INFORMATIONAL",
            "UNKNOWN",
            "UNDEFINED",
        }
        <= set(trivy_counts)
        or int(trivy_counts.get("CRITICAL") or 0) != 0
        or int(trivy_counts.get("HIGH") or 0) != len(trivy_accepted_highs)
        or trivy_scan.get("unaccepted_highs") != []
        or runtime.get("git_sha") != reviewed_sha
        or runtime.get("image_digest") != digest
        or runtime_repository.get("repository") != EXPECTED_REPOSITORY
        or runtime_repository.get("registry_id") != EXPECTED_ACCOUNT_ID
        or runtime_repository.get("region") != EXPECTED_REGION
        or runtime_repository.get("image_tag_mutability") != "IMMUTABLE"
        or runtime_repository.get("scan_on_push") is not True
    ):
        raise StagingControlError("release evidence provenance is inconsistent")
    trivy_targets = trivy_scan.get("targets")
    if not isinstance(trivy_targets, list):
        raise StagingControlError("Trivy scan target evidence is missing")
    has_os = any(
        isinstance(item, Mapping) and item.get("class") == "os-pkgs"
        for item in trivy_targets
    )
    has_python = any(
        isinstance(item, Mapping)
        and item.get("class") == "lang-pkgs"
        and item.get("type") in {"pip", "python-pkg"}
        for item in trivy_targets
    )
    if not has_os or not has_python:
        raise StagingControlError("Trivy OS/Python scan coverage is incomplete")
    version = authenticated.get("version") or {}
    files = authenticated.get("files")
    if (
        authenticated.get("api_base") != "https://staging.regmind.co/api"
        or authenticated.get("expected_sha") != reviewed_sha
        or authenticated.get("expected_environment") != "staging"
        or not isinstance(files, Mapping)
        or not {"health", "liveness", "version", "runtime_baseline", "summary"}
        <= set(files)
        or any(
            not isinstance(files.get(name), str) or not files.get(name)
            for name in (
                "health",
                "liveness",
                "version",
                "runtime_baseline",
                "summary",
            )
        )
        or version.get("git_sha") != reviewed_sha
        or version.get("image_tag") != reviewed_sha
        or version.get("environment") != "staging"
        or version.get("git_sha_matches_expected") is not True
        or version.get("image_tag_matches_expected") is not True
        or version.get("environment_matches_expected") is not True
    ):
        raise StagingControlError("authenticated /api/version evidence is inconsistent")
    rollback = predeploy.get("rollback") or {}
    rollback_tag = str(rollback.get("image_tag") or "")
    rollback_digest = str(rollback.get("image_digest") or "")
    if (
        not TASK_DEFINITION_RE.fullmatch(
            str(rollback.get("backend_task_definition") or "")
        )
        or not TASK_DEFINITION_RE.fullmatch(
            str(rollback.get("worker_task_definition") or "")
        )
        or not SHA_RE.fullmatch(rollback_tag)
        or not DIGEST_RE.fullmatch(rollback_digest)
        or len(rollback.get("commands") or []) != 3
    ):
        raise StagingControlError("rollback evidence is incomplete")
    backend_rollback_td = str(rollback["backend_task_definition"])
    worker_rollback_td = str(rollback["worker_task_definition"])
    expected_rollback_commands = [
        (
            "aws ecs update-service --cluster regmind-staging "
            "--service regmind-backend "
            f"--task-definition {backend_rollback_td} --force-new-deployment "
            "--region af-south-1"
        ),
        (
            "aws ecs update-service --cluster regmind-staging "
            "--service regmind-verification-worker "
            f"--task-definition {worker_rollback_td} --force-new-deployment "
            "--region af-south-1"
        ),
        (
            "aws ecs wait services-stable --cluster regmind-staging "
            "--services regmind-backend regmind-verification-worker "
            "--region af-south-1"
        ),
    ]
    if rollback.get("commands") != expected_rollback_commands:
        raise StagingControlError("rollback commands are not linked to captured state")
    _validate_predeploy_evidence(
        predeploy,
        rollback=rollback,
        rollback_tag=rollback_tag,
        rollback_digest=rollback_digest,
    )

    runtime_services = runtime.get("services")
    if not isinstance(runtime_services, Mapping) or set(runtime_services) != {
        "backend",
        "worker",
    } or not all(
        isinstance(runtime_services.get(label), Mapping)
        for label in ("backend", "worker")
    ):
        raise StagingControlError("runtime service evidence is missing")
    runtime_task_definitions = {
        label: str((runtime_services.get(label) or {}).get("task_definition_arn") or "")
        for label in ("backend", "worker")
    }
    if (
        runtime.get("schema_version") != "staging_runtime_release_evidence_v1"
        or runtime.get("region") != EXPECTED_REGION
        or runtime.get("cluster") != EXPECTED_CLUSTER
        or not runtime.get("captured_at")
    ):
        raise StagingControlError("runtime deployment evidence is incomplete")
    _validate_service_inventory(
        runtime_services,
        expected_tag=reviewed_sha,
        expected_digest=digest,
        expected_task_definitions=runtime_task_definitions,
        require_task_definition_summary=True,
    )

    alb = runtime.get("alb") or {}
    expected_target_count = int(alb.get("expected_target_count") or 0)
    states = alb.get("states")
    if (
        expected_target_count <= 0
        or int(alb.get("healthy_target_count") or 0)
        != expected_target_count
        or expected_target_count
        != int(runtime_services["backend"].get("desired_count") or 0)
        or not str(alb.get("target_group_arn") or "").startswith(
            "arn:aws:elasticloadbalancing:af-south-1:"
            "782913119880:targetgroup/"
        )
        or not isinstance(states, list)
        or states != ["healthy"] * expected_target_count
    ):
        raise StagingControlError("ALB release evidence is incomplete")
    _validate_cloudwatch_evidence(cloudwatch)
    if int(cloudwatch.get("start_time_ms") or 0) != int(
        predeploy.get("window_start_epoch_ms") or 0
    ):
        raise StagingControlError(
            "CloudWatch review is not linked to the deployment window"
        )
    expected_workflow_url = (
        "https://github.com/onboarda1234/onboarda/actions/runs/"
        f"{workflow_run_id}"
    )
    if (
        not workflow_run_id.isdigit()
        or not workflow_run_attempt.isdigit()
        or int(workflow_run_attempt) <= 0
        or workflow_run_url != expected_workflow_url
        or not actor.strip()
    ):
        raise StagingControlError("GitHub workflow provenance is incomplete")
    return {
        "schema_version": "staging_release_evidence_v1",
        "ok": True,
        "reviewed_merge_sha": reviewed_sha,
        "workflow": {
            "run_url": workflow_run_url,
            "run_id": workflow_run_id,
            "run_attempt": workflow_run_attempt,
            "actor": actor,
        },
        "image": {
            "repository": (image.get("repository") or {}).get("repository"),
            "tag": image.get("image_tag"),
            "digest": digest,
            "action": image.get("action"),
            "immutable": (
                image_repository.get("image_tag_mutability") == "IMMUTABLE"
            ),
            "provenance": image.get("provenance"),
        },
        "vulnerability_scans": {
            "ecr": {
                "status": scan.get("status"),
                "completed_at": scan.get("completed_at"),
                "severity_counts": scan.get("severity_counts"),
                "acceptance_manifest_version": scan.get(
                    "acceptance_manifest_version"
                ),
                "accepted_highs": scan.get("accepted_highs"),
                "unaccepted_highs": [],
            },
            "trivy_exact_digest": {
                "status": trivy_scan.get("status"),
                "scanner": trivy_scan.get("scanner"),
                "targets": trivy_scan.get("targets"),
                "severity_counts": trivy_scan.get("severity_counts"),
                "acceptance_manifest_version": trivy_scan.get(
                    "acceptance_manifest_version"
                ),
                "accepted_highs": trivy_scan.get("accepted_highs"),
                "unaccepted_highs": [],
            },
        },
        "predeploy": {
            "captured_at": predeploy.get("captured_at"),
            "services": predeploy.get("services"),
        },
        "deployment": {
            "captured_at": runtime.get("captured_at"),
            "services": runtime.get("services"),
            "alb": runtime.get("alb"),
        },
        "authenticated_version": version,
        "cloudwatch": cloudwatch,
        "rollback": rollback,
    }


def _load_json(path: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagingControlError(f"cannot read evidence file {path}") from exc
    if not isinstance(payload, dict):
        raise StagingControlError(f"evidence file {path} is not an object")
    return payload


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _github_outputs(path: str, values: Mapping[str, Any]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = str(value)
            if "\n" in text or "\r" in text:
                raise StagingControlError("GitHub output values must be single-line")
            handle.write(f"{key}={text}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture-predeploy")
    capture.add_argument("--region", default=EXPECTED_REGION)
    capture.add_argument("--cluster", default=EXPECTED_CLUSTER)
    capture.add_argument("--output", required=True)
    capture.add_argument("--github-output", default="")

    render = subparsers.add_parser("render-task-definition")
    render.add_argument("--source-task-definition", required=True)
    render.add_argument("--image", required=True)
    render.add_argument("--sha", required=True)
    render.add_argument("--build-time", required=True)
    render.add_argument("--service-name", required=True)
    render.add_argument("--region", default=EXPECTED_REGION)
    render.add_argument("--output", required=True)

    verify = subparsers.add_parser("verify-runtime")
    verify.add_argument("--sha", required=True)
    verify.add_argument("--digest", required=True)
    verify.add_argument("--backend-task-definition", required=True)
    verify.add_argument("--worker-task-definition", required=True)
    verify.add_argument("--region", default=EXPECTED_REGION)
    verify.add_argument("--cluster", default=EXPECTED_CLUSTER)
    verify.add_argument("--output", required=True)

    cloudwatch = subparsers.add_parser("collect-cloudwatch")
    cloudwatch.add_argument("--predeploy-evidence", required=True)
    cloudwatch.add_argument("--log-group", default="/ecs/regmind-staging")
    cloudwatch.add_argument("--region", default=EXPECTED_REGION)
    cloudwatch.add_argument("--output", required=True)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--sha", required=True)
    finalize.add_argument("--image-evidence", required=True)
    finalize.add_argument("--scan-evidence", required=True)
    finalize.add_argument("--trivy-scan-evidence", required=True)
    finalize.add_argument("--predeploy-evidence", required=True)
    finalize.add_argument("--runtime-evidence", required=True)
    finalize.add_argument("--authenticated-evidence", required=True)
    finalize.add_argument("--cloudwatch-evidence", required=True)
    finalize.add_argument("--workflow-run-url", required=True)
    finalize.add_argument("--workflow-run-id", required=True)
    finalize.add_argument("--workflow-run-attempt", required=True)
    finalize.add_argument("--actor", required=True)
    finalize.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "capture-predeploy":
            result = capture_predeploy(region=args.region, cluster=args.cluster)
            _write_json(args.output, result)
            _github_outputs(
                args.github_output,
                {
                    "backend_source_task_definition": result["services"]["backend"][
                        "task_definition_arn"
                    ],
                    "worker_source_task_definition": result["services"]["worker"][
                        "task_definition_arn"
                    ],
                    "window_start_epoch_ms": result["window_start_epoch_ms"],
                },
            )
        elif args.command == "render-task-definition":
            result = render_live_task_definition(
                source_task_definition_arn=args.source_task_definition,
                image=args.image,
                sha=args.sha,
                build_time=args.build_time,
                service_name=args.service_name,
                region=args.region,
            )
            _write_json(args.output, result)
        elif args.command == "verify-runtime":
            result = verify_runtime(
                sha=args.sha,
                digest=args.digest,
                backend_task_definition=args.backend_task_definition,
                worker_task_definition=args.worker_task_definition,
                region=args.region,
                cluster=args.cluster,
            )
            _write_json(args.output, result)
        elif args.command == "collect-cloudwatch":
            predeploy = _load_json(args.predeploy_evidence)
            result = collect_cloudwatch(
                start_time_ms=int(predeploy.get("window_start_epoch_ms") or 0),
                log_group=args.log_group,
                region=args.region,
            )
            _write_json(args.output, result)
        else:
            result = finalize_evidence(
                reviewed_sha=args.sha,
                image=_load_json(args.image_evidence),
                scan=_load_json(args.scan_evidence),
                trivy_scan=_load_json(args.trivy_scan_evidence),
                predeploy=_load_json(args.predeploy_evidence),
                runtime=_load_json(args.runtime_evidence),
                authenticated=_load_json(args.authenticated_evidence),
                cloudwatch=_load_json(args.cloudwatch_evidence),
                workflow_run_url=args.workflow_run_url,
                workflow_run_id=args.workflow_run_id,
                workflow_run_attempt=args.workflow_run_attempt,
                actor=args.actor,
            )
            _write_json(args.output, result)
    except (StagingControlError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
