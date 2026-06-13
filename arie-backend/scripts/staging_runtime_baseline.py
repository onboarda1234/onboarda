#!/usr/bin/env python3
"""Collect a redacted staging backend/worker runtime baseline.

This helper is read-only. It uses the AWS CLI already required by the staging
runbook and emits enough ECS/task-definition evidence to prove whether the
backend and verification worker are running the expected SHA-pinned image.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Optional


PROVENANCE_ENV_ALLOWLIST = {
    "APP_ENV",
    "BUILD_TIME",
    "ENVIRONMENT",
    "FF_ASYNC_VERIFY",
    "GIT_SHA",
    "IMAGE_TAG",
    "VERIFICATION_OBSERVABILITY_INTERVAL_SECONDS",
    "VERIFICATION_WORKER_ID",
}


def extract_image_tag(image: str) -> str:
    image = str(image or "")
    if "@" in image:
        image = image.split("@", 1)[0]
    if ":" not in image:
        return ""
    return image.rsplit(":", 1)[1]


def _run_aws(args: Iterable[str]) -> Dict[str, Any]:
    cmd = ["aws", *args, "--output", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "AWS CLI command failed").strip())
    return json.loads(proc.stdout or "{}")


def _container_env(container: Dict[str, Any]) -> Dict[str, str]:
    return {
        item.get("name"): item.get("value", "")
        for item in container.get("environment", [])
        if item.get("name")
    }


def summarize_container(container: Dict[str, Any]) -> Dict[str, Any]:
    env = _container_env(container)
    log_options = (container.get("logConfiguration") or {}).get("options", {})
    return {
        "name": container.get("name"),
        "image": container.get("image"),
        "image_tag": extract_image_tag(container.get("image", "")),
        "command": container.get("command"),
        "essential": container.get("essential"),
        "log_group": log_options.get("awslogs-group"),
        "log_stream_prefix": log_options.get("awslogs-stream-prefix"),
        "env_provenance": {
            key: env[key]
            for key in sorted(PROVENANCE_ENV_ALLOWLIST)
            if key in env
        },
        "secret_names": [
            item.get("name")
            for item in container.get("secrets", [])
            if item.get("name")
        ],
    }


def summarize_task_definition(task_definition: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "taskDefinitionArn": task_definition.get("taskDefinitionArn"),
        "family": task_definition.get("family"),
        "revision": task_definition.get("revision"),
        "taskRoleArn": task_definition.get("taskRoleArn"),
        "executionRoleArn": task_definition.get("executionRoleArn"),
        "networkMode": task_definition.get("networkMode"),
        "cpu": task_definition.get("cpu"),
        "memory": task_definition.get("memory"),
        "containers": [
            summarize_container(container)
            for container in task_definition.get("containerDefinitions", [])
        ],
    }


def summarize_service(service: Dict[str, Any]) -> Dict[str, Any]:
    primary = next(
        (
            deployment
            for deployment in service.get("deployments", [])
            if deployment.get("status") == "PRIMARY"
        ),
        {},
    )
    return {
        "serviceName": service.get("serviceName"),
        "status": service.get("status"),
        "desiredCount": service.get("desiredCount"),
        "runningCount": service.get("runningCount"),
        "pendingCount": service.get("pendingCount"),
        "taskDefinition": service.get("taskDefinition"),
        "primaryRolloutState": primary.get("rolloutState"),
        "primaryTaskDefinition": primary.get("taskDefinition"),
    }


def evaluate_alignment(summary: Dict[str, Any], expected_sha: Optional[str]) -> Dict[str, Any]:
    expected_sha = str(expected_sha or "").strip()
    services = {item["serviceName"]: item for item in summary.get("services", [])}
    task_defs = summary.get("task_definitions", {})
    backend_container = (task_defs.get("backend", {}).get("containers") or [{}])[0]
    worker_container = (task_defs.get("worker", {}).get("containers") or [{}])[0]

    backend_tag = backend_container.get("image_tag") or ""
    worker_tag = worker_container.get("image_tag") or ""
    backend_env = backend_container.get("env_provenance") or {}
    worker_env = worker_container.get("env_provenance") or {}
    worker_service = services.get(summary.get("worker_service_name"), {})
    backend_service = services.get(summary.get("backend_service_name"), {})

    alignment = {
        "expected_sha": expected_sha,
        "backend_service_healthy": (
            backend_service.get("status") == "ACTIVE"
            and int(backend_service.get("desiredCount") or 0) > 0
            and backend_service.get("runningCount") == backend_service.get("desiredCount")
            and backend_service.get("primaryRolloutState") == "COMPLETED"
        ),
        "worker_service_healthy": (
            worker_service.get("status") == "ACTIVE"
            and int(worker_service.get("desiredCount") or 0) > 0
            and worker_service.get("runningCount") == worker_service.get("desiredCount")
            and worker_service.get("primaryRolloutState") == "COMPLETED"
        ),
        "backend_image_tag": backend_tag,
        "worker_image_tag": worker_tag,
        "backend_env_git_sha": backend_env.get("GIT_SHA", ""),
        "backend_env_image_tag": backend_env.get("IMAGE_TAG", ""),
        "worker_env_git_sha": worker_env.get("GIT_SHA", ""),
        "worker_env_image_tag": worker_env.get("IMAGE_TAG", ""),
    }
    if expected_sha:
        alignment.update({
            "backend_image_matches_expected": backend_tag == expected_sha,
            "backend_env_matches_expected": (
                backend_env.get("GIT_SHA") == expected_sha
                and backend_env.get("IMAGE_TAG") == expected_sha
            ),
            "worker_image_matches_expected": worker_tag == expected_sha,
            "worker_env_matches_expected": (
                worker_env.get("GIT_SHA") == expected_sha
                and worker_env.get("IMAGE_TAG") == expected_sha
            ),
        })
        alignment["aligned"] = all(
            alignment[key]
            for key in (
                "backend_service_healthy",
                "worker_service_healthy",
                "backend_image_matches_expected",
                "backend_env_matches_expected",
                "worker_image_matches_expected",
                "worker_env_matches_expected",
            )
        )
    else:
        alignment["aligned"] = (
            alignment["backend_service_healthy"]
            and alignment["worker_service_healthy"]
            and bool(backend_tag)
            and backend_tag == worker_tag
        )
    return alignment


def collect_runtime_baseline(
    *,
    region: str,
    cluster: str,
    backend_service: str,
    worker_service: str,
    expected_sha: Optional[str] = None,
) -> Dict[str, Any]:
    services_payload = _run_aws([
        "ecs",
        "describe-services",
        "--region",
        region,
        "--cluster",
        cluster,
        "--services",
        backend_service,
        worker_service,
    ])
    services = services_payload.get("services", [])
    failures = services_payload.get("failures", [])
    task_defs: Dict[str, Any] = {}
    for label, service_name in (("backend", backend_service), ("worker", worker_service)):
        service = next(
            (item for item in services if item.get("serviceName") == service_name),
            {},
        )
        task_def_arn = service.get("taskDefinition")
        if not task_def_arn:
            task_defs[label] = {"error": "service taskDefinition not found"}
            continue
        task_payload = _run_aws([
            "ecs",
            "describe-task-definition",
            "--region",
            region,
            "--task-definition",
            task_def_arn,
        ])
        task_defs[label] = summarize_task_definition(task_payload.get("taskDefinition", {}))

    summary = {
        "region": region,
        "cluster": cluster,
        "backend_service_name": backend_service,
        "worker_service_name": worker_service,
        "services": [summarize_service(service) for service in services],
        "failures": failures,
        "task_definitions": task_defs,
    }
    summary["alignment"] = evaluate_alignment(summary, expected_sha)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="af-south-1")
    parser.add_argument("--cluster", default="regmind-staging")
    parser.add_argument("--backend-service", default="regmind-backend")
    parser.add_argument("--worker-service", default="regmind-verification-worker")
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--strict", action="store_true", help="exit non-zero when runtime is not aligned")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    summary = collect_runtime_baseline(
        region=args.region,
        cluster=args.cluster,
        backend_service=args.backend_service,
        worker_service=args.worker_service,
        expected_sha=args.expected_sha or None,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.strict and not summary["alignment"].get("aligned"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
