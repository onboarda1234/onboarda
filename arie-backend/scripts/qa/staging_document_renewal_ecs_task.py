#!/usr/bin/env python3
"""Run one exact Document Renewal harness command as a staging ECS task.

The helper derives networking from the live backend service, keeps container
overrides (including the database-identity fingerprint) only in process
memory, and persists only an allowlisted task summary plus an optional
``DOCUMENT_RENEWAL_HARNESS_EVIDENCE=`` JSON record. Raw task responses,
environment overrides and CloudWatch messages are never printed or written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping, Optional, Sequence


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.qa.staging_document_renewal_activation import (  # noqa: E402
    EVIDENCE_PREFIX,
    inspect_task_definition,
)
from fixtures.document_renewal_staging import manifest_sha256  # noqa: E402
from monitoring_document_renewal_staging_cleanup import (  # noqa: E402
    STAGING_DATABASE_FINGERPRINT_ENV,
)


EXPECTED_REGION = "af-south-1"
EXPECTED_CLUSTER = "regmind-staging"
EXPECTED_SERVICE = "regmind-backend"
EXPECTED_CONTAINER = "regmind-backend"
EXPECTED_LOG_GROUP = "/ecs/regmind-staging"
TASK_DEFINITION_RE = re.compile(
    r"^arn:aws:ecs:af-south-1:782913119880:task-definition/"
    r"regmind-staging:[1-9][0-9]*$"
)
STARTED_BY_RE = re.compile(r"^gh-[1-9][0-9]{0,19}-[1-9][0-9]{0,2}-[a-z0-9-]{1,20}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DATABASE_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
OPERATIONS = frozenset(
    {"seed", "snapshot-off", "eligibility", "snapshot-active", "cleanup"}
)
ACTIVE_OPERATIONS = frozenset({"eligibility", "snapshot-active"})


class EcsHarnessTaskError(RuntimeError):
    """The one-off staging task could not be proven exact and successful."""


def _run(arguments: Sequence[str]) -> str:
    result = subprocess.run(
        list(arguments),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        executable = Path(arguments[0]).name if arguments else "command"
        # AWS errors can echo serialized overrides. Never include stdout/stderr.
        raise EcsHarnessTaskError(f"{executable} command failed")
    return result.stdout


def _aws(arguments: Sequence[str]) -> dict[str, Any]:
    try:
        payload = json.loads(_run(["aws", *arguments, "--output", "json"]) or "{}")
    except json.JSONDecodeError as exc:
        raise EcsHarnessTaskError("AWS returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise EcsHarnessTaskError("AWS returned a non-object response")
    return payload


def _aws_stdin(arguments: Sequence[str], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Pass secret-bearing AWS input via stdin, never argv or a file."""

    result = subprocess.run(
        ["aws", *arguments, "--cli-input-json", "file:///dev/stdin", "--output", "json"],
        input=json.dumps(dict(payload), separators=(",", ":")),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise EcsHarnessTaskError("AWS stdin command failed")
    try:
        value = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise EcsHarnessTaskError("AWS returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise EcsHarnessTaskError("AWS returned a non-object response")
    return value


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dict(payload), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _network_configuration() -> dict[str, Any]:
    payload = _aws(
        [
            "ecs",
            "describe-services",
            "--cluster",
            EXPECTED_CLUSTER,
            "--services",
            EXPECTED_SERVICE,
            "--region",
            EXPECTED_REGION,
        ]
    )
    if payload.get("failures") or len(payload.get("services") or []) != 1:
        raise EcsHarnessTaskError("live staging backend service is unavailable")
    service = payload["services"][0]
    if service.get("status") != "ACTIVE":
        raise EcsHarnessTaskError("live staging backend service is not active")
    awsvpc = ((service.get("networkConfiguration") or {}).get(
        "awsvpcConfiguration"
    ) or {})
    subnets = sorted({str(value) for value in awsvpc.get("subnets") or []})
    security_groups = sorted(
        {str(value) for value in awsvpc.get("securityGroups") or []}
    )
    if (
        not subnets
        or not security_groups
        or awsvpc.get("assignPublicIp") != "DISABLED"
        or any(not re.fullmatch(r"subnet-[0-9a-f]+", value) for value in subnets)
        or any(not re.fullmatch(r"sg-[0-9a-f]+", value) for value in security_groups)
    ):
        raise EcsHarnessTaskError("live staging network configuration is unsafe")
    return {
        "awsvpcConfiguration": {
            "subnets": subnets,
            "securityGroups": security_groups,
            "assignPublicIp": "DISABLED",
        }
    }


def _log_configuration(
    task_definition: str,
    *,
    expected_sha: str,
    expected_mode: str,
    expected_run_id: str,
) -> tuple[str, str]:
    payload = _aws(
        [
            "ecs",
            "describe-task-definition",
            "--task-definition",
            task_definition,
            "--include",
            "TAGS",
            "--region",
            EXPECTED_REGION,
        ]
    )
    definition = payload.get("taskDefinition") or {}
    definition["tags"] = payload.get("tags") or []
    if str(definition.get("taskDefinitionArn") or "") != task_definition:
        raise EcsHarnessTaskError("task definition lookup was not exact")
    containers = [
        item
        for item in definition.get("containerDefinitions") or []
        if isinstance(item, Mapping) and item.get("name") == EXPECTED_CONTAINER
    ]
    if len(containers) != 1:
        raise EcsHarnessTaskError("task definition backend container is ambiguous")
    try:
        inspect_task_definition(
            definition,
            expected_sha=expected_sha,
            expected_mode=expected_mode,
            expected_run_id=(expected_run_id if expected_mode == "active" else None),
        )
    except Exception as exc:
        raise EcsHarnessTaskError("one-off task release or flag provenance is invalid") from exc
    config = containers[0].get("logConfiguration") or {}
    options = config.get("options") or {}
    group = str(options.get("awslogs-group") or "")
    prefix = str(options.get("awslogs-stream-prefix") or "")
    if (
        config.get("logDriver") != "awslogs"
        or group != EXPECTED_LOG_GROUP
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", prefix)
    ):
        raise EcsHarnessTaskError("task definition log configuration is unsafe")
    return group, prefix


def _operation_contract(
    operation: str,
    *,
    run_id: str,
) -> tuple[list[str], list[dict[str, str]], bool, str]:
    """Return only repository-reviewed commands and environment overrides."""

    if operation not in OPERATIONS:
        raise EcsHarnessTaskError("one-off operation is outside the reviewed boundary")
    database_fingerprint = str(
        os.environ.get(STAGING_DATABASE_FINGERPRINT_ENV) or ""
    ).strip().lower()
    if not DATABASE_FINGERPRINT_RE.fullmatch(database_fingerprint):
        raise EcsHarnessTaskError("approved staging database fingerprint is missing")
    common_environment = [
        {"name": "ENVIRONMENT", "value": "staging"},
        {
            "name": STAGING_DATABASE_FINGERPRINT_ENV,
            "value": database_fingerprint,
        },
    ]
    if operation == "seed":
        command = [
            "python",
            "-m",
            "fixtures.document_renewal_staging_cli",
            "apply",
            "--confirm",
            "APPLY-DOCUMENT-RENEWAL-STAGING-FIXTURE-V1",
            "--reviewed-hash",
            manifest_sha256(),
        ]
        environment = [
            *common_environment,
            {"name": "ALLOW_DOCUMENT_RENEWAL_STAGING_FIXTURE", "value": "1"},
        ]
        return command, environment, False, "off"
    if operation in {"snapshot-off", "snapshot-active"}:
        return (
            [
                "python",
                "scripts/qa/staging_document_renewal_activation.py",
                "snapshot",
                "--run-id",
                run_id,
            ],
            common_environment,
            True,
            "active" if operation == "snapshot-active" else "off",
        )
    if operation == "eligibility":
        return (
            [
                "python",
                "scripts/qa/staging_document_renewal_activation.py",
                "run-fixture-eligibility",
            ],
            common_environment,
            True,
            "active",
        )
    return (
        [
            "python",
            "scripts/qa/staging_document_renewal_activation.py",
            "cleanup-fixture",
            "--run-id",
            run_id,
            "--actor-id",
            f"github-actions:{run_id}",
        ],
        common_environment,
        True,
        "off",
    )


def _extract_evidence(
    *,
    log_group: str,
    log_stream: str,
    run_id: str,
    operation: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 45
    matches: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        payload = _aws(
            [
                "logs",
                "get-log-events",
                "--log-group-name",
                log_group,
                "--log-stream-name",
                log_stream,
                "--start-from-head",
                "--region",
                EXPECTED_REGION,
            ]
        )
        matches = []
        for event in payload.get("events") or []:
            message = str((event or {}).get("message") or "").strip()
            if not message.startswith(EVIDENCE_PREFIX):
                continue
            try:
                item = json.loads(message[len(EVIDENCE_PREFIX):])
            except json.JSONDecodeError as exc:
                raise EcsHarnessTaskError("harness evidence marker is malformed") from exc
            if isinstance(item, dict) and str(item.get("run_id") or "") == run_id:
                _validate_evidence(item, operation=operation)
                matches.append(item)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise EcsHarnessTaskError("harness evidence marker is ambiguous")
        time.sleep(3)
    raise EcsHarnessTaskError("harness evidence marker was not observed")


def _contains_sensitive_key(value: Any) -> bool:
    sensitive = re.compile(
        r"(?:secret|password|token|cookie|authorization|database_url|jwt|credential)",
        re.IGNORECASE,
    )
    if isinstance(value, Mapping):
        return any(
            sensitive.search(str(key)) or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _validate_evidence(value: Mapping[str, Any], *, operation: str) -> None:
    if _contains_sensitive_key(value):
        raise EcsHarnessTaskError("harness evidence contains a sensitive field")
    allowed_top_level = {
        "snapshot-off": {
            "contract_version", "run_id", "fixture", "relations",
            "core_fingerprint", "operational_fingerprint", "isolation_fingerprint",
            "scheduler_fingerprint", "audit_evidence_fingerprint", "feature_state",
            "fixture_audit_actions", "fixture_audit_chained_count",
            "fixture_audit_unchained_count", "audit_chain",
            "all_monitoring_flags_off",
        },
        "snapshot-active": {
            "contract_version", "run_id", "fixture", "relations",
            "core_fingerprint", "operational_fingerprint", "isolation_fingerprint",
            "scheduler_fingerprint", "audit_evidence_fingerprint", "feature_state",
            "fixture_audit_actions", "fixture_audit_chained_count",
            "fixture_audit_unchained_count", "audit_chain",
            "all_monitoring_flags_off",
        },
        "eligibility": {
            "schema_version", "ok", "run_id", "fixture_key", "fixture_version",
            "manifest_sha256", "application_id", "alert_id", "request_id",
            "scanned", "created", "already_active",
        },
        "cleanup": {
            "schema_version", "ok", "run_id", "fixture_key", "plan_fingerprint",
            "planned_counts", "already_clean", "cleanup",
        },
    }.get(operation)
    if allowed_top_level is None or set(value) != allowed_top_level:
        raise EcsHarnessTaskError("harness evidence schema is not exact")
    if operation == "eligibility" and (
        value.get("ok") is not True
        or value.get("schema_version") != "document_renewal_staging_eligibility_v1"
        or value.get("scanned") != 1
        or int(value.get("created") or 0) + int(value.get("already_active") or 0) != 1
    ):
        raise EcsHarnessTaskError("eligibility evidence is invalid")
    if operation == "cleanup" and (
        value.get("ok") is not True
        or value.get("schema_version")
        != "document_renewal_staging_cleanup_execution_v1"
        or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("plan_fingerprint") or ""))
    ):
        raise EcsHarnessTaskError("cleanup evidence is invalid")
    if operation.startswith("snapshot"):
        if (
            value.get("contract_version")
            != "monitoring_document_renewal_staging_cleanup_v1"
            or not isinstance(value.get("relations"), Mapping)
            or not isinstance(value.get("feature_state"), Mapping)
        ):
            raise EcsHarnessTaskError("fingerprint evidence is invalid")


def run_one_off_task(
    *,
    task_definition: str,
    operation: str,
    expected_sha: str,
    run_id: str,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    if not TASK_DEFINITION_RE.fullmatch(task_definition):
        raise EcsHarnessTaskError("one-off task definition is outside staging backend")
    if not SHA_RE.fullmatch(expected_sha):
        raise EcsHarnessTaskError("expected deployed SHA is invalid")
    if not re.fullmatch(r"^gh-[1-9][0-9]{0,19}-[1-9][0-9]{0,2}$", run_id):
        raise EcsHarnessTaskError("harness run ID is invalid")
    command, environment, require_evidence, expected_mode = _operation_contract(
        operation,
        run_id=run_id,
    )
    started_by = f"{run_id}-{operation}".replace("snapshot-active", "snap-active").replace("snapshot-off", "snap-off")
    if not STARTED_BY_RE.fullmatch(started_by):
        raise EcsHarnessTaskError("one-off started-by identity is invalid")
    log_group, log_prefix = _log_configuration(
        task_definition,
        expected_sha=expected_sha,
        expected_mode=expected_mode,
        expected_run_id=run_id,
    )
    overrides = {
        "containerOverrides": [
            {
                "name": EXPECTED_CONTAINER,
                "command": list(command),
                "environment": [dict(item) for item in environment],
            }
        ]
    }
    response = _aws_stdin(
        [
            "ecs",
            "run-task",
            "--region",
            EXPECTED_REGION,
        ],
        {
            "cluster": EXPECTED_CLUSTER,
            "taskDefinition": task_definition,
            "launchType": "FARGATE",
            "count": 1,
            "networkConfiguration": _network_configuration(),
            "overrides": overrides,
            "startedBy": started_by,
        },
    )
    if response.get("failures") or len(response.get("tasks") or []) != 1:
        raise EcsHarnessTaskError("ECS refused the one-off staging task")
    task_arn = str(response["tasks"][0].get("taskArn") or "")
    if not re.fullmatch(
        r"arn:aws:ecs:af-south-1:782913119880:task/regmind-staging/[0-9a-f]{32}",
        task_arn,
    ):
        raise EcsHarnessTaskError("ECS returned an invalid one-off task ARN")
    _run(
        [
            "aws",
            "ecs",
            "wait",
            "tasks-stopped",
            "--cluster",
            EXPECTED_CLUSTER,
            "--tasks",
            task_arn,
            "--region",
            EXPECTED_REGION,
        ]
    )
    described = _aws(
        [
            "ecs",
            "describe-tasks",
            "--cluster",
            EXPECTED_CLUSTER,
            "--tasks",
            task_arn,
            "--region",
            EXPECTED_REGION,
        ]
    )
    if described.get("failures") or len(described.get("tasks") or []) != 1:
        raise EcsHarnessTaskError("one-off task result is unavailable")
    task = described["tasks"][0]
    containers = [
        item
        for item in task.get("containers") or []
        if isinstance(item, Mapping) and item.get("name") == EXPECTED_CONTAINER
    ]
    if (
        task.get("lastStatus") != "STOPPED"
        or str(task.get("taskDefinitionArn") or "") != task_definition
        or len(containers) != 1
        or containers[0].get("exitCode") != 0
        or containers[0].get("lastStatus") != "STOPPED"
    ):
        raise EcsHarnessTaskError("one-off staging task did not exit successfully")
    task_id = task_arn.rsplit("/", 1)[-1]
    evidence = None
    if require_evidence:
        evidence = _extract_evidence(
            log_group=log_group,
            log_stream=f"{log_prefix}/{EXPECTED_CONTAINER}/{task_id}",
            run_id=run_id,
            operation=operation,
        )
    summary = {
        "schema_version": "document_renewal_staging_ecs_task_v1",
        "ok": True,
        "run_id": run_id,
        "operation": operation,
        "task_definition": task_definition,
        "task_arn_sha256": hashlib.sha256(task_arn.encode("utf-8")).hexdigest(),
        "exit_code": 0,
        "network_source": "live_regmind_backend_service",
        "public_ip": False,
        "evidence_observed": evidence is not None,
        "secret_values_recorded": False,
    }
    return summary, evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-definition", required=True)
    parser.add_argument("--operation", choices=sorted(OPERATIONS), required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--evidence-output")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        operation_requires_evidence = args.operation != "seed"
        if bool(args.evidence_output) is not operation_requires_evidence:
            raise EcsHarnessTaskError(
                "evidence output must match the fixed operation contract"
            )
        summary, evidence = run_one_off_task(
            task_definition=args.task_definition,
            operation=args.operation,
            expected_sha=args.expected_sha,
            run_id=args.run_id,
        )
        _write_json(args.output, summary)
        if args.evidence_output:
            if evidence is None:
                raise EcsHarnessTaskError("required one-off evidence is missing")
            _write_json(args.evidence_output, evidence)
        print(json.dumps(summary, sort_keys=True))
        return 0
    except EcsHarnessTaskError as exc:
        print(f"staging_document_renewal_ecs_task: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EcsHarnessTaskError",
    "run_one_off_task",
]
