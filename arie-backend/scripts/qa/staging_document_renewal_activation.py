#!/usr/bin/env python3
"""Operator controls for one temporary Document Renewal staging activation.

This helper never edits an ECS service by itself.  It renders and verifies the
single temporary backend task definition used by the manual GitHub workflow,
and provides fail-closed inspection primitives for activation and recovery.
The workflow remains responsible for the explicit update-service and captured
rollback calls so those mutations are visible in release evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Optional


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from document_renewal_staging_activation import (  # noqa: E402
    HARNESS_EXPIRES_AT_ENV,
    HARNESS_ISSUED_AT_ENV,
    HARNESS_MANIFEST_SHA_ENV,
    HARNESS_MODE_ENV,
    HARNESS_RELEASE_SHA_ENV,
    HARNESS_RUN_ID_ENV,
    OTHER_MONITORING_FLAGS,
    RENEWAL_FLAG,
    activation_state,
)
from fixtures.document_renewal_staging import manifest_sha256  # noqa: E402
from scripts.qa.staging_deployment_control import (  # noqa: E402
    EXPECTED_BACKEND_SERVICE,
    EXPECTED_CLUSTER,
    EXPECTED_CONTAINER,
    EXPECTED_REGION,
    StagingControlError,
    render_task_definition,
)


EXPECTED_ACCOUNT = "782913119880"
EXPECTED_REPOSITORY_URI = (
    "782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend"
)
EXPECTED_REF = "refs/heads/main"
CONFIRM_TOKEN = "I-UNDERSTAND-STAGING-DOCUMENT-RENEWAL-HARNESS"
ORIGINAL_TASK_DEFINITION_ENV = (
    "DOCUMENT_RENEWAL_STAGING_HARNESS_ORIGINAL_TASK_DEFINITION"
)
HARNESS_TAG = "document-renewal-staging-harness-v1"
HARNESS_ENVIRONMENT_NAMES = frozenset(
    {
        HARNESS_MODE_ENV,
        HARNESS_RUN_ID_ENV,
        HARNESS_ISSUED_AT_ENV,
        HARNESS_EXPIRES_AT_ENV,
        HARNESS_MANIFEST_SHA_ENV,
        HARNESS_RELEASE_SHA_ENV,
        ORIGINAL_TASK_DEFINITION_ENV,
    }
)
EXPECTED_VALIDATION_AUDIT_DELTA = {
    "monitoring.document_renewal.artifact_reserved": 1,
    "monitoring.document_renewal.artifact_stored": 1,
    "monitoring.document_renewal.duplicate_upload": 1,
    "monitoring.document_renewal.request_created": 1,
    "monitoring.document_renewal.request_sent": 1,
    "monitoring.document_renewal.upload_bound": 1,
    "monitoring.document_renewal.upload_received": 1,
}
EXPECTED_CLEANUP_AUDIT_DELTA = {
    "monitoring.document_renewal.fixture_cleanup": 1,
}
TASK_DEFINITION_RE = re.compile(
    r"^arn:aws:ecs:af-south-1:782913119880:task-definition/"
    r"regmind-staging:[1-9][0-9]*$"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"^gh-[1-9][0-9]{0,19}-[1-9][0-9]{0,2}$")
EVIDENCE_PREFIX = "DOCUMENT_RENEWAL_HARNESS_EVIDENCE="

RECOVERY_OPERATIONAL_RELATIONS = (
    "renewal_requests",
    "renewal_events",
    "renewal_uploads",
    "renewal_upload_bindings",
    "renewal_upload_cleanup",
    "renewal_notifications",
    "harness_rate_limit",
)
RECOVERY_BASELINE_COUNTS = {
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
}


class ActivationControlError(RuntimeError):
    """Temporary ECS activation evidence cannot be proven safe."""


def _load_json(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActivationControlError(f"invalid JSON input: {path}") from exc
    if not isinstance(value, dict):
        raise ActivationControlError("JSON input must be an object")
    return value


def _write_json(path: str, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _environment(container: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in container.get("environment") or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "")
        if not name or name in result:
            raise ActivationControlError(
                "task definition has a missing or duplicate environment name"
            )
        result[name] = str(item.get("value") or "")
    return result


def _release_container(task_definition: Mapping[str, Any]) -> dict[str, Any]:
    matches = [
        item
        for item in task_definition.get("containerDefinitions") or []
        if isinstance(item, dict) and item.get("name") == EXPECTED_CONTAINER
    ]
    if len(matches) != 1:
        raise ActivationControlError(
            "task definition must contain exactly one backend container"
        )
    return matches[0]


def _set_environment(container: dict[str, Any], updates: Mapping[str, str]) -> None:
    items = container.setdefault("environment", [])
    by_name = {str(item.get("name") or ""): item for item in items}
    for name, value in updates.items():
        if name in by_name:
            by_name[name]["value"] = value
        else:
            item = {"name": name, "value": value}
            items.append(item)
            by_name[name] = item
    items.sort(key=lambda item: str(item.get("name") or ""))


def _image_tag(image: Any) -> str:
    value = str(image or "").split("@", 1)[0]
    return value.rsplit(":", 1)[1] if ":" in value else ""


def _exact_release_image(image: Any, sha: str) -> bool:
    return str(image or "") == f"{EXPECTED_REPOSITORY_URI}:{sha}"


def validate_release_context(sha: str, ref: str, confirm: str) -> str:
    sha = str(sha or "")
    if not SHA_RE.fullmatch(sha):
        raise ActivationControlError(
            "release SHA must be a full lowercase 40-character SHA"
        )
    if ref != EXPECTED_REF:
        raise ActivationControlError("staging harness is main-only")
    if confirm != CONFIRM_TOKEN:
        raise ActivationControlError("staging harness confirmation is invalid")
    return sha


def render_activation_task_definition(
    source_task_definition: Mapping[str, Any],
    *,
    sha: str,
    run_id: str,
    issued_at: str,
    expires_at: str,
    fixture_manifest_sha256: str,
) -> dict[str, Any]:
    """Clone the exact live backend revision and add only scoped lease values."""

    source_arn = str(source_task_definition.get("taskDefinitionArn") or "")
    if not TASK_DEFINITION_RE.fullmatch(source_arn):
        raise ActivationControlError("source task definition is not live staging backend")
    source_container = _release_container(source_task_definition)
    source_environment = _environment(source_container)
    if (
        source_environment.get("ENVIRONMENT") != "staging"
        or source_environment.get("SERVICE_NAME") != EXPECTED_BACKEND_SERVICE
        or source_environment.get("GIT_SHA") != sha
        or source_environment.get("IMAGE_TAG") != sha
        or not _exact_release_image(source_container.get("image"), sha)
    ):
        raise ActivationControlError(
            "source task definition does not match the exact deployed release"
        )
    for flag in (RENEWAL_FLAG, *OTHER_MONITORING_FLAGS):
        if source_environment.get(flag, "false").strip().lower() not in {
            "",
            "0",
            "false",
            "no",
            "off",
        }:
            raise ActivationControlError(
                "all governed Monitoring flags must be OFF before activation"
            )
    if fixture_manifest_sha256 != manifest_sha256():
        raise ActivationControlError(
            "fixture manifest fingerprint differs from the reviewed repository manifest"
        )

    try:
        rendered = render_task_definition(
            source_task_definition=source_task_definition,
            image=str(source_container.get("image") or ""),
            sha=sha,
            build_time=source_environment["BUILD_TIME"],
            service_name=EXPECTED_BACKEND_SERVICE,
        )
    except (KeyError, StagingControlError) as exc:
        raise ActivationControlError(
            "source task definition cannot be safely cloned"
        ) from exc
    container = _release_container(rendered)
    activation_values = {
        "ENVIRONMENT": "staging",
        RENEWAL_FLAG: "true",
        **{name: "false" for name in OTHER_MONITORING_FLAGS},
        HARNESS_MODE_ENV: "1",
        HARNESS_RUN_ID_ENV: run_id,
        HARNESS_ISSUED_AT_ENV: issued_at,
        HARNESS_EXPIRES_AT_ENV: expires_at,
        HARNESS_MANIFEST_SHA_ENV: fixture_manifest_sha256,
        HARNESS_RELEASE_SHA_ENV: sha,
        ORIGINAL_TASK_DEFINITION_ENV: source_arn,
    }
    _set_environment(container, activation_values)

    # Exercise the same runtime parser that the backend will use.  Supplying a
    # complete synthetic environment avoids dependence on the operator host.
    runtime_environment = _environment(container)
    state = activation_state(
        environ=runtime_environment,
        now=datetime.now(timezone.utc),
        expected_manifest_sha256=fixture_manifest_sha256,
    )
    if not state.active or state.run_id != run_id:
        raise ActivationControlError("rendered staging activation lease is not active")
    rendered["tags"] = [
        {"key": "RegMindHarness", "value": HARNESS_TAG},
        {"key": "HarnessRunId", "value": run_id},
        {"key": "HarnessOriginalTaskDefinition", "value": source_arn},
        {"key": "HarnessExpiresAt", "value": expires_at},
        {"key": "HarnessReleaseSha", "value": sha},
    ]
    return rendered


def inspect_task_definition(
    task_definition: Mapping[str, Any],
    *,
    expected_sha: str,
    expected_mode: str,
    expected_run_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return allowlisted ON/OFF evidence for a live or temporary revision."""

    arn = str(task_definition.get("taskDefinitionArn") or "")
    if not TASK_DEFINITION_RE.fullmatch(arn):
        raise ActivationControlError("task definition is outside the staging backend")
    container = _release_container(task_definition)
    environment = _environment(container)
    if (
        environment.get("ENVIRONMENT") != "staging"
        or environment.get("GIT_SHA") != expected_sha
        or environment.get("IMAGE_TAG") != expected_sha
        or not _exact_release_image(container.get("image"), expected_sha)
    ):
        raise ActivationControlError("task definition release provenance mismatch")
    raw_flags = {
        name: environment.get(name, "false").strip().lower()
        in {"1", "true", "yes", "on"}
        for name in (RENEWAL_FLAG, *OTHER_MONITORING_FLAGS)
    }
    if expected_mode == "off":
        if any(raw_flags.values()) or HARNESS_ENVIRONMENT_NAMES.intersection(
            environment
        ):
            raise ActivationControlError("restored task definition is not fully OFF")
        effective = False
        run_id = None
        expires_at = None
    elif expected_mode == "active":
        state = activation_state(environ=environment)
        if not state.active or state.run_id != expected_run_id:
            raise ActivationControlError("temporary task definition lease is not active")
        if raw_flags != {
            RENEWAL_FLAG: True,
            **{name: False for name in OTHER_MONITORING_FLAGS},
        }:
            raise ActivationControlError("temporary governed flag set is unsafe")
        original = str(environment.get(ORIGINAL_TASK_DEFINITION_ENV) or "")
        expected_tags = {
            "RegMindHarness": HARNESS_TAG,
            "HarnessRunId": state.run_id or "",
            "HarnessOriginalTaskDefinition": original,
            "HarnessExpiresAt": environment.get(HARNESS_EXPIRES_AT_ENV, ""),
            "HarnessReleaseSha": expected_sha,
        }
        if (
            not TASK_DEFINITION_RE.fullmatch(original)
            or original == arn
            or _task_tags(task_definition) != expected_tags
        ):
            raise ActivationControlError(
                "temporary task definition recovery metadata is invalid"
            )
        effective = True
        run_id = state.run_id
        expires_at = state.expires_at.isoformat() if state.expires_at else None
    else:
        raise ActivationControlError("expected mode must be off or active")
    return {
        "schema_version": "document_renewal_staging_activation_task_v1",
        "ok": True,
        "task_definition": arn,
        "image_tag": expected_sha,
        "mode": expected_mode,
        "effective_renewal_enabled": effective,
        "governed_flags": {
            name: ("ON" if value else "OFF") for name, value in raw_flags.items()
        },
        "run_id": run_id,
        "expires_at": expires_at,
    }


def _task_tags(task_definition: Mapping[str, Any]) -> dict[str, str]:
    tags: dict[str, str] = {}
    for item in task_definition.get("tags") or []:
        if not isinstance(item, Mapping):
            raise ActivationControlError("recovery candidate tags are malformed")
        key = str(item.get("key") or "")
        value = str(item.get("value") or "")
        if not key or key in tags:
            raise ActivationControlError("recovery candidate tags are malformed")
        tags[key] = value
    return tags


def recovery_candidate(
    task_definition: Mapping[str, Any],
) -> dict[str, Any]:
    """Identify an exact harness revision and restore only after lease expiry."""

    arn = str(task_definition.get("taskDefinitionArn") or "")
    if not TASK_DEFINITION_RE.fullmatch(arn):
        raise ActivationControlError("recovery candidate is outside staging backend")
    container = _release_container(task_definition)
    environment = _environment(container)
    sha = str(environment.get("GIT_SHA") or "")
    if (
        not SHA_RE.fullmatch(sha)
        or environment.get("IMAGE_TAG") != sha
        or environment.get("ENVIRONMENT") != "staging"
        or environment.get("SERVICE_NAME") != EXPECTED_BACKEND_SERVICE
        or not _exact_release_image(container.get("image"), sha)
    ):
        raise ActivationControlError("recovery candidate provenance is invalid")
    if HARNESS_MODE_ENV not in environment:
        inspect_task_definition(
            task_definition,
            expected_sha=sha,
            expected_mode="off",
        )
        return {
            "schema_version": "document_renewal_staging_recovery_candidate_v1",
            "ok": True,
            "action": "none",
            "current_task_definition": arn,
            "release_sha": sha,
            "run_id": None,
            "original_task_definition": None,
        }
    try:
        state = activation_state(environ=environment)
    except Exception as exc:
        raise ActivationControlError("configured recovery lease is malformed") from exc
    original = str(environment.get(ORIGINAL_TASK_DEFINITION_ENV) or "")
    if (
        not TASK_DEFINITION_RE.fullmatch(original)
        or original == arn
        or not state.run_id
        or state.release_sha != sha
    ):
        raise ActivationControlError("configured recovery target is invalid")
    expected_tags = {
        "RegMindHarness": HARNESS_TAG,
        "HarnessRunId": state.run_id,
        "HarnessOriginalTaskDefinition": original,
        "HarnessExpiresAt": environment.get(HARNESS_EXPIRES_AT_ENV, ""),
        "HarnessReleaseSha": sha,
    }
    if _task_tags(task_definition) != expected_tags:
        raise ActivationControlError("configured recovery tags do not match")
    if state.active:
        if not state.expires_at:
            raise ActivationControlError("active recovery lease has no expiry")
        wait_seconds = max(
            1,
            int((state.expires_at - datetime.now(timezone.utc)).total_seconds()) + 2,
        )
        return {
            "schema_version": "document_renewal_staging_recovery_candidate_v1",
            "ok": True,
            "action": "wait",
            "current_task_definition": arn,
            "release_sha": sha,
            "run_id": state.run_id,
            "lease_state": state.reason,
            "expires_at": state.expires_at.isoformat(),
            "wait_seconds": wait_seconds,
            "original_task_definition": original,
        }
    if state.reason != "expired":
        raise ActivationControlError("configured recovery lease is not expired")
    return {
        "schema_version": "document_renewal_staging_recovery_candidate_v1",
        "ok": True,
        "action": "restore",
        "current_task_definition": arn,
        "release_sha": sha,
        "run_id": state.run_id,
        "lease_state": state.reason,
        "expires_at": state.expires_at.isoformat() if state.expires_at else None,
        "original_task_definition": original,
    }


def verify_recovery_plan(
    current_task_definition: Mapping[str, Any],
    original_task_definition: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = recovery_candidate(current_task_definition)
    if candidate["action"] != "restore":
        raise ActivationControlError("recovery plan has no harness revision to restore")
    original_arn = str(original_task_definition.get("taskDefinitionArn") or "")
    if original_arn != candidate["original_task_definition"]:
        raise ActivationControlError("recovery original task definition does not match")
    inspect_task_definition(
        original_task_definition,
        expected_sha=candidate["release_sha"],
        expected_mode="off",
    )
    return {
        "schema_version": "document_renewal_staging_recovery_plan_v1",
        "ok": True,
        "action": "restore",
        "temporary_task_definition": candidate["current_task_definition"],
        "original_task_definition": original_arn,
        "release_sha": candidate["release_sha"],
        "run_id": candidate["run_id"],
        "lease_state": candidate["lease_state"],
    }


def inactive_harness_residue(
    current_task_definition: Mapping[str, Any],
    candidate_task_definitions: list[Mapping[str, Any]],
    *,
    run_id: str,
    running_task_definition_arns: list[str],
) -> dict[str, Any]:
    """Select at most one exact run-tagged inactive-service revision."""

    if not re.fullmatch(r"^gh-[1-9][0-9]{0,19}-[1-9][0-9]{0,2}$", run_id):
        raise ActivationControlError("inactive residue run ID is invalid")
    current_container = _release_container(current_task_definition)
    current_environment = _environment(current_container)
    current_sha = str(current_environment.get("GIT_SHA") or "")
    inspect_task_definition(
        current_task_definition,
        expected_sha=current_sha,
        expected_mode="off",
    )
    matches = []
    for candidate in candidate_task_definitions:
        tags = _task_tags(candidate)
        if (
            tags.get("RegMindHarness") == HARNESS_TAG
            and tags.get("HarnessRunId") == run_id
        ):
            matches.append(candidate)
        else:
            raise ActivationControlError(
                "inactive residue discovery returned an unrelated task definition"
            )
    if len(matches) > 1:
        raise ActivationControlError("inactive harness task-definition residue is ambiguous")
    if not matches:
        return {
            "schema_version": "document_renewal_staging_inactive_residue_v1",
            "ok": True,
            "action": "none",
            "run_id": run_id,
            "task_definition": None,
            "wait_seconds": 0,
        }
    candidate = matches[0]
    recovery = recovery_candidate(candidate)
    arn = str(candidate.get("taskDefinitionArn") or "")
    if recovery.get("run_id") != run_id or arn == str(
        current_task_definition.get("taskDefinitionArn") or ""
    ):
        raise ActivationControlError("inactive harness residue identity is invalid")
    if arn in {str(value) for value in running_task_definition_arns}:
        raise ActivationControlError("inactive harness revision still has a running task")
    status = str(candidate.get("status") or "ACTIVE")
    if status == "INACTIVE":
        action = "none"
        wait_seconds = 0
    elif status != "ACTIVE":
        raise ActivationControlError("inactive harness residue status is invalid")
    elif recovery["action"] == "wait":
        action = "wait"
        wait_seconds = int(recovery.get("wait_seconds") or 0)
    elif recovery["action"] == "restore":
        action = "deregister"
        wait_seconds = 0
    else:
        raise ActivationControlError("inactive harness residue is not recoverable")
    return {
        "schema_version": "document_renewal_staging_inactive_residue_v1",
        "ok": True,
        "action": action,
        "run_id": run_id,
        "task_definition": arn,
        "wait_seconds": wait_seconds,
    }


def select_recovery_context(
    current_task_definition: Mapping[str, Any],
    candidate_task_definitions: list[Mapping[str, Any]],
    *,
    requested_run_id: Optional[str],
    running_task_definition_arns: list[str],
) -> dict[str, Any]:
    """Select one immutable lease release without depending on moving ``main``.

    The AWS tagging query is deliberately global for the one governed harness
    tag.  A workflow event run ID is only an ownership assertion; it is never
    used to narrow discovery because doing so could hide residue left by a
    cancelled run.
    """

    requested = str(requested_run_id or "")
    if requested and not RUN_ID_RE.fullmatch(requested):
        raise ActivationControlError("requested recovery run ID is invalid")
    if any(not TASK_DEFINITION_RE.fullmatch(str(value)) for value in running_task_definition_arns):
        raise ActivationControlError("running task-definition inventory is invalid")

    current = recovery_candidate(current_task_definition)
    current_arn = str(current_task_definition.get("taskDefinitionArn") or "")
    matches: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for candidate_definition in candidate_task_definitions:
        arn = str(candidate_definition.get("taskDefinitionArn") or "")
        if arn in seen:
            raise ActivationControlError(
                "tagged harness task-definition discovery is ambiguous"
            )
        seen.add(arn)
        tags = _task_tags(candidate_definition)
        if tags.get("RegMindHarness") != HARNESS_TAG:
            raise ActivationControlError(
                "tagged recovery discovery returned an unrelated task definition"
            )
        candidate = recovery_candidate(candidate_definition)
        if candidate.get("action") == "none":
            raise ActivationControlError("tagged recovery candidate is not a harness lease")
        status = str(candidate_definition.get("status") or "ACTIVE")
        if status not in {"ACTIVE", "INACTIVE"}:
            raise ActivationControlError("tagged recovery candidate status is invalid")
        matches.append((candidate_definition, candidate))
    if len(matches) > 1:
        raise ActivationControlError(
            "tagged harness task-definition discovery is ambiguous"
        )

    if current.get("action") != "none":
        if not matches or str(matches[0][0].get("taskDefinitionArn") or "") != current_arn:
            raise ActivationControlError(
                "live harness task definition is absent from global tag discovery"
            )

    if not matches:
        # A failed workflow may have stopped before registering its temporary
        # revision.  Its exact event/manual run ID remains safe cleanup scope;
        # a scheduled run without ownership can only perform a clean probe.
        run_id = requested or None
        return {
            "schema_version": "document_renewal_staging_recovery_context_v1",
            "ok": True,
            "action": "cleanup" if run_id else "clean_probe",
            "service_action": "none",
            "run_id": run_id,
            "recovery_sha": current["release_sha"],
            "current_release_sha": current["release_sha"],
            "current_task_definition": current_arn,
            "cleanup_task_definition": current_arn,
            "temporary_task_definition": None,
            "original_task_definition": None,
            "wait_seconds": 0,
            "running_temporary_tasks": 0,
        }

    candidate_definition, candidate = matches[0]
    candidate_arn = str(candidate_definition.get("taskDefinitionArn") or "")
    run_id = str(candidate.get("run_id") or "")
    if not RUN_ID_RE.fullmatch(run_id):
        raise ActivationControlError("tagged recovery candidate run ID is invalid")
    if requested and requested != run_id:
        raise ActivationControlError(
            "requested recovery run does not own discovered harness residue"
        )
    if current.get("action") != "none" and current.get("run_id") != run_id:
        raise ActivationControlError("live harness run changed during recovery")

    running_count = sum(
        1 for arn in running_task_definition_arns if str(arn) == candidate_arn
    )
    candidate_status = str(candidate_definition.get("status") or "ACTIVE")
    if candidate.get("action") == "wait":
        action = "wait"
        wait_seconds = int(candidate.get("wait_seconds") or 0)
    else:
        action = "restore" if current_arn == candidate_arn else "cleanup"
        wait_seconds = 0
    if current_arn == candidate_arn and candidate_status != "ACTIVE":
        raise ActivationControlError("ECS service references an inactive harness revision")
    return {
        "schema_version": "document_renewal_staging_recovery_context_v1",
        "ok": True,
        "action": action,
        "service_action": "restore" if action == "restore" else "none",
        "run_id": run_id,
        "recovery_sha": candidate["release_sha"],
        "current_release_sha": current["release_sha"],
        "current_task_definition": current_arn,
        "cleanup_task_definition": candidate["original_task_definition"],
        "temporary_task_definition": candidate_arn,
        "original_task_definition": candidate["original_task_definition"],
        "wait_seconds": wait_seconds,
        "running_temporary_tasks": running_count,
    }


def verify_recovery_residue_clear(
    current_task_definition: Mapping[str, Any],
    candidate_task_definitions: list[Mapping[str, Any]],
    *,
    running_task_definition_arns: list[str],
    running_task_definitions: Optional[list[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Prove the service is OFF with no ACTIVE tagged or running residue."""

    current = recovery_candidate(current_task_definition)
    if current.get("action") != "none":
        raise ActivationControlError("backend remains on a harness task definition")
    harness_arns: set[str] = set()
    for candidate_definition in candidate_task_definitions:
        arn = str(candidate_definition.get("taskDefinitionArn") or "")
        if arn in harness_arns:
            raise ActivationControlError("final harness residue inventory is ambiguous")
        harness_arns.add(arn)
        if _task_tags(candidate_definition).get("RegMindHarness") != HARNESS_TAG:
            raise ActivationControlError("final residue inventory is unrelated")
        candidate = recovery_candidate(candidate_definition)
        if candidate.get("action") == "none":
            raise ActivationControlError("final tagged residue is not a harness lease")
    if harness_arns:
        raise ActivationControlError("tagged harness task-definition residue remains")
    running = {
        str(arn) for arn in running_task_definition_arns if str(arn) in harness_arns
    }
    for definition in running_task_definitions or []:
        arn = str(definition.get("taskDefinitionArn") or "")
        if not TASK_DEFINITION_RE.fullmatch(arn):
            raise ActivationControlError("running task-definition evidence is invalid")
        environment = _environment(_release_container(definition))
        tags = _task_tags(definition)
        is_harness = HARNESS_MODE_ENV in environment or tags.get(
            "RegMindHarness"
        ) == HARNESS_TAG
        if is_harness:
            candidate = recovery_candidate(definition)
            if candidate.get("action") == "none":
                raise ActivationControlError("running harness evidence is malformed")
            running.add(arn)
    running = sorted(running)
    if running:
        raise ActivationControlError("running harness task residue remains")
    return {
        "schema_version": "document_renewal_staging_recovery_residue_v1",
        "ok": True,
        "current_task_definition": current_task_definition.get("taskDefinitionArn"),
        "current_release_sha": current.get("release_sha"),
        "tagged_task_definitions": 0,
        "running_harness_tasks": 0,
    }


def verify_clean_recovery_fingerprint(
    fingerprint: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed unless one OFF snapshot is the exact governed baseline."""

    relations = fingerprint.get("relations") or {}
    if not isinstance(relations, Mapping):
        raise ActivationControlError("clean fingerprint relation metadata is invalid")

    def count(name: str) -> int:
        try:
            return int((relations.get(name) or {}).get("row_count", -1))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ActivationControlError(
                "clean fingerprint relation metadata is invalid"
            ) from exc

    from environment import MONITORING_FEATURE_FLAGS
    from fixtures.document_renewal_staging import fixture_spec

    flags = fingerprint.get("feature_state") or {}
    chain = fingerprint.get("audit_chain") or {}
    fixture = fingerprint.get("fixture") or {}
    spec = fixture_spec()
    fixture_keys = (
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
    checks = {
        "operational_state_empty": all(
            count(name) == 0 for name in RECOVERY_OPERATIONAL_RELATIONS
        ),
        "exact_governed_baseline": all(
            count(name) == expected
            for name, expected in RECOVERY_BASELINE_COUNTS.items()
        ),
        "manifest_roots_exact": (
            isinstance(fixture, Mapping)
            and all(fixture.get(key) == spec[key] for key in fixture_keys)
            and isinstance(fixture.get("alert_id"), int)
            and int(fixture.get("alert_id")) > 0
        ),
        "flags_off": (
            isinstance(flags, Mapping)
            and set(flags) == set(MONITORING_FEATURE_FLAGS)
            and not any(bool(value) for value in flags.values())
            and fingerprint.get("all_monitoring_flags_off") is True
        ),
        "audit_chain_intact": (
            isinstance(chain, Mapping)
            and chain.get("verified") is True
            and chain.get("coverage_complete") is True
            and int(chain.get("coverage_gaps", -1)) == 0
            and int(chain.get("broken_link_count", -1)) == 0
            and int(chain.get("entries_checked", -1))
            == int(chain.get("chained_rows", -2))
            and int(chain.get("chained_rows", -1))
            + int(chain.get("legacy_rows", -1))
            == int(chain.get("total_entries", -3))
        ),
    }
    if not all(checks.values()):
        raise ActivationControlError("recovery final fingerprint is not clean")
    return {
        "schema_version": "document_renewal_staging_recovery_clean_v1",
        "ok": True,
        "run_id": fingerprint.get("run_id"),
        "checks": checks,
    }


def run_fixture_eligibility() -> dict[str, Any]:
    """Run the one repository-owned eligibility target inside staging VPC."""

    state = activation_state()
    if not state.active or not state.run_id:
        raise ActivationControlError(
            "fixture eligibility requires one active staging harness lease"
        )
    from db import get_db
    from monitoring_document_renewal import (
        RenewalError,
        run_fixture_only_renewal_eligibility,
    )

    db = get_db()
    try:
        result = run_fixture_only_renewal_eligibility(
            db,
            actor={
                "sub": f"system:document-renewal-harness:{state.run_id}",
                "name": "Document Renewal Staging Harness",
                "role": "system",
                "type": "system",
            },
        )
        if (
            result.get("mode") != "fixture_only"
            or result.get("manifest_sha256") != state.manifest_sha256
            or int(result.get("scanned") or 0) != 1
            or int(result.get("created") or 0)
            + int(result.get("already_active") or 0)
            != 1
        ):
            raise ActivationControlError(
                "fixture-only scheduler returned unexpected cardinality"
            )
        return {
            "schema_version": "document_renewal_staging_eligibility_v1",
            "ok": True,
            "run_id": state.run_id,
            "fixture_key": result.get("fixture_key"),
            "fixture_version": result.get("fixture_version"),
            "manifest_sha256": result.get("manifest_sha256"),
            "application_id": result.get("application_id"),
            "alert_id": result.get("alert_id"),
            "request_id": result.get("request_id"),
            "scanned": 1,
            "created": int(result.get("created") or 0),
            "already_active": int(result.get("already_active") or 0),
        }
    except RenewalError as exc:
        try:
            db.rollback()
        except Exception:
            pass
        raise ActivationControlError(
            f"fixture eligibility failed with controlled code {exc.code}"
        ) from exc
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        raise ActivationControlError("fixture eligibility failed") from exc
    finally:
        db.close()


def _fixture_with_alert_id(db: Any) -> dict[str, Any]:
    """Resolve the manifest's permanent alert without accepting caller scope."""

    from fixtures.document_renewal_staging import fixture_spec

    spec = dict(fixture_spec())
    rows = db.execute(
        """
        SELECT id FROM monitoring_alerts
         WHERE application_id = ? AND source_reference = ?
         ORDER BY id
        """,
        (spec["application_id"], spec["alert_source_reference"]),
    ).fetchall()
    if len(rows) != 1:
        raise ActivationControlError(
            "governed fixture alert did not resolve to exactly one row"
        )
    spec["alert_id"] = int(dict(rows[0])["id"])
    return spec


def capture_fixture_fingerprint(*, run_id: str) -> dict[str, Any]:
    """Capture one sanitized fingerprint from inside the exact ECS revision."""

    from db import get_db
    from monitoring_document_renewal_staging_cleanup import (
        capture_renewal_harness_fingerprint,
    )

    db = get_db()
    try:
        return capture_renewal_harness_fingerprint(
            db,
            _fixture_with_alert_id(db),
            run_id=run_id,
        )
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        raise ActivationControlError("fixture fingerprint capture failed") from exc
    finally:
        db.close()


def cleanup_fixture(*, run_id: str, actor_id: str) -> dict[str, Any]:
    """Plan and atomically clean the exact fixture after full flag restoration."""

    from document_renewal_staging_activation import harness_mode_configured
    from db import get_db
    from monitoring_document_renewal_staging_cleanup import (
        CLEANUP_CONFIRMATION,
        STAGING_DATABASE_FINGERPRINT_ENV,
        build_renewal_harness_cleanup_plan,
        cleanup_renewal_harness_operational_state,
    )

    if harness_mode_configured():
        raise ActivationControlError(
            "cleanup must run from the exact restored non-harness task definition"
        )
    expected_database_fingerprint = os.environ.get(
        STAGING_DATABASE_FINGERPRINT_ENV
    )
    if not expected_database_fingerprint:
        raise ActivationControlError(
            f"cleanup requires {STAGING_DATABASE_FINGERPRINT_ENV}"
        )
    db = get_db()
    try:
        fixture = _fixture_with_alert_id(db)
        plan = build_renewal_harness_cleanup_plan(
            db,
            fixture,
            run_id=run_id,
        )
        result = cleanup_renewal_harness_operational_state(
            db,
            fixture,
            run_id=run_id,
            expected_plan_fingerprint=plan["fingerprint"],
            actor_id=actor_id,
            confirmation=CLEANUP_CONFIRMATION,
            expected_database_fingerprint=expected_database_fingerprint,
        )
        return {
            "schema_version": "document_renewal_staging_cleanup_execution_v1",
            "ok": True,
            "run_id": run_id,
            "fixture_key": plan["fixture"]["fixture_key"],
            "plan_fingerprint": plan["fingerprint"],
            "planned_counts": plan["counts"],
            "already_clean": plan["already_clean"],
            "cleanup": result,
        }
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        raise ActivationControlError("fixture cleanup failed") from exc
    finally:
        db.close()


def compare_fingerprints(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    cleanup_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove exact synthetic changes followed by complete baseline restoration."""

    from monitoring_document_renewal_staging_cleanup import (
        compare_renewal_harness_baseline,
    )

    final = (cleanup_evidence.get("cleanup") or {}).get("final_fingerprint")
    if not isinstance(final, Mapping):
        raise ActivationControlError("cleanup evidence has no final fingerprint")
    expected_after_counts = {
        "renewal_requests": 1,
        "renewal_events": 3,
        "renewal_uploads": 1,
        "renewal_upload_bindings": 1,
        "renewal_upload_cleanup": 1,
        "renewal_notifications": 1,
        "harness_rate_limit": 1,
    }
    before_counts = {
        name: int((before.get("relations") or {}).get(name, {}).get("row_count", -1))
        for name in expected_after_counts
    }
    after_counts = {
        name: int((after.get("relations") or {}).get(name, {}).get("row_count", -1))
        for name in expected_after_counts
    }
    final_counts = {
        name: int((final.get("relations") or {}).get(name, {}).get("row_count", -1))
        for name in expected_after_counts
    }
    def audit_actions(value: Mapping[str, Any]) -> dict[str, int]:
        raw = value.get("fixture_audit_actions") or {}
        if not isinstance(raw, Mapping):
            raise ActivationControlError("fixture audit action metadata is invalid")
        try:
            result = {str(name): int(count) for name, count in raw.items()}
        except (TypeError, ValueError) as exc:
            raise ActivationControlError(
                "fixture audit action metadata is invalid"
            ) from exc
        if any(count < 0 for count in result.values()):
            raise ActivationControlError("fixture audit action metadata is invalid")
        return result

    def audit_delta(
        earlier: Mapping[str, Any],
        later: Mapping[str, Any],
    ) -> dict[str, int]:
        left = audit_actions(earlier)
        right = audit_actions(later)
        names = set(left) | set(right)
        delta = {name: right.get(name, 0) - left.get(name, 0) for name in names}
        if any(count < 0 for count in delta.values()):
            raise ActivationControlError("fixture audit evidence was removed")
        return dict(sorted((name, count) for name, count in delta.items() if count))

    def audit_count(value: Mapping[str, Any]) -> int:
        try:
            return int(
                ((value.get("relations") or {}).get("fixture_audit") or {}).get(
                    "row_count", -1
                )
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ActivationControlError("fixture audit relation metadata is invalid") from exc

    try:
        before_chained = int(before.get("fixture_audit_chained_count", -1))
        after_chained = int(after.get("fixture_audit_chained_count", -1))
        final_chained = int(final.get("fixture_audit_chained_count", -1))
        before_unchained = int(before.get("fixture_audit_unchained_count", -1))
        after_unchained = int(after.get("fixture_audit_unchained_count", -1))
        final_unchained = int(final.get("fixture_audit_unchained_count", -1))
    except (TypeError, ValueError) as exc:
        raise ActivationControlError("fixture audit chain metadata is invalid") from exc
    before_audit_count = audit_count(before)
    after_audit_count = audit_count(after)
    final_audit_count = audit_count(final)
    validation_audit_delta = audit_delta(before, after)
    cleanup_audit_delta = audit_delta(after, final)
    def chain_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
        raw = value.get("audit_chain") or {}
        if not isinstance(raw, Mapping):
            raise ActivationControlError("audit-chain metadata is invalid")
        try:
            result = {
                "verified": raw.get("verified") is True,
                "coverage_complete": raw.get("coverage_complete") is True,
                "entries_checked": int(raw.get("entries_checked", -1)),
                "chained_rows": int(raw.get("chained_rows", -1)),
                "legacy_rows": int(raw.get("legacy_rows", -1)),
                "coverage_gaps": int(raw.get("coverage_gaps", -1)),
                "broken_link_count": int(raw.get("broken_link_count", -1)),
                "total_entries": int(raw.get("total_entries", -1)),
            }
        except (TypeError, ValueError) as exc:
            raise ActivationControlError("audit-chain metadata is invalid") from exc
        return result

    before_chain = chain_metadata(before)
    after_chain = chain_metadata(after)
    final_chain = chain_metadata(final)
    audit_chain_integrity_exact = (
        all(
            value["verified"]
            and value["coverage_complete"]
            and value["coverage_gaps"] == 0
            and value["broken_link_count"] == 0
            and value["entries_checked"] == value["chained_rows"]
            and value["chained_rows"] + value["legacy_rows"]
            == value["total_entries"]
            for value in (before_chain, after_chain, final_chain)
        )
        and before_chain["legacy_rows"]
        == after_chain["legacy_rows"]
        == final_chain["legacy_rows"]
        and after_chain["chained_rows"] - before_chain["chained_rows"]
        == after_chain["total_entries"] - before_chain["total_entries"]
        and final_chain["chained_rows"] - after_chain["chained_rows"]
        == final_chain["total_entries"] - after_chain["total_entries"]
    )
    exact_audit_chain = (
        before_audit_count >= 0
        and after_audit_count >= before_audit_count
        and final_audit_count >= after_audit_count
        and before_unchained == after_unchained == final_unchained
        and after_chained - before_chained
        == after_audit_count - before_audit_count
        and final_chained - after_chained
        == final_audit_count - after_audit_count
    )
    baseline = compare_renewal_harness_baseline(before, final)
    flags_before = before.get("feature_state") or {}
    flags_after = after.get("feature_state") or {}
    exact_active_flags = (
        flags_after.get(RENEWAL_FLAG) is True
        and all(flags_after.get(name) is False for name in OTHER_MONITORING_FLAGS)
        and set(flags_after) == {RENEWAL_FLAG, *OTHER_MONITORING_FLAGS}
    )
    comparisons = {
        "baseline_was_clean": all(value == 0 for value in before_counts.values()),
        "synthetic_changes_exact": after_counts == expected_after_counts,
        "final_operational_state_empty": all(
            value == 0 for value in final_counts.values()
        ),
        "core_unchanged_during_validation": before.get("core_fingerprint")
        == after.get("core_fingerprint"),
        "nonfixture_unchanged_during_validation": before.get("isolation_fingerprint")
        == after.get("isolation_fingerprint"),
        "scheduler_unchanged_during_validation": before.get("scheduler_fingerprint")
        == after.get("scheduler_fingerprint"),
        "flags_before_off": bool(before.get("all_monitoring_flags_off"))
        and bool(flags_before)
        and not any(bool(value) for value in flags_before.values()),
        "temporary_flags_exact": exact_active_flags,
        "validation_audit_actions_exact": validation_audit_delta
        == EXPECTED_VALIDATION_AUDIT_DELTA,
        "cleanup_audit_action_exact": cleanup_audit_delta
        == EXPECTED_CLEANUP_AUDIT_DELTA,
        "audit_chain_growth_exact": exact_audit_chain,
        "global_audit_chain_integrity_exact": audit_chain_integrity_exact,
        "baseline_restored": bool(baseline.get("baseline_restored")),
    }
    if not all(comparisons.values()):
        raise ActivationControlError("fixture fingerprints do not reconcile")
    return {
        "schema_version": "document_renewal_staging_reconciliation_v1",
        "ok": True,
        "run_id": before.get("run_id"),
        "expected_after_counts": expected_after_counts,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "final_counts": final_counts,
        "validation_audit_delta": validation_audit_delta,
        "cleanup_audit_delta": cleanup_audit_delta,
        "audit_counts": {
            "before": before_audit_count,
            "after": after_audit_count,
            "final": final_audit_count,
        },
        "audit_chain": {
            "before": before_chain,
            "after": after_chain,
            "final": final_chain,
        },
        "comparisons": comparisons,
        "baseline": baseline,
    }


def compare_recovery_fingerprints(
    before_cleanup: Mapping[str, Any],
    final: Mapping[str, Any],
    cleanup_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove recovery removed only synthetic operational state."""

    operational = (
        "renewal_requests",
        "renewal_events",
        "renewal_uploads",
        "renewal_upload_bindings",
        "renewal_upload_cleanup",
        "renewal_notifications",
        "harness_rate_limit",
    )
    exact_baseline_counts = {
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
    }

    def relation_count(value: Mapping[str, Any], name: str) -> int:
        try:
            return int(
                ((value.get("relations") or {}).get(name) or {}).get(
                    "row_count", -1
                )
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ActivationControlError(
                "recovery fingerprint relation metadata is invalid"
            ) from exc

    def actions(value: Mapping[str, Any]) -> dict[str, int]:
        raw = value.get("fixture_audit_actions") or {}
        if not isinstance(raw, Mapping):
            raise ActivationControlError("recovery audit metadata is invalid")
        try:
            return {str(name): int(count) for name, count in raw.items()}
        except (TypeError, ValueError) as exc:
            raise ActivationControlError("recovery audit metadata is invalid") from exc

    left_actions = actions(before_cleanup)
    right_actions = actions(final)
    names = set(left_actions) | set(right_actions)
    action_delta = {
        name: right_actions.get(name, 0) - left_actions.get(name, 0)
        for name in names
        if right_actions.get(name, 0) != left_actions.get(name, 0)
    }
    if any(count < 0 for count in action_delta.values()):
        raise ActivationControlError("recovery removed fixture audit evidence")
    cleanup = cleanup_evidence.get("cleanup") or {}
    if not isinstance(cleanup, Mapping):
        raise ActivationControlError("recovery cleanup evidence is invalid")
    expected_action_delta = (
        {}
        if cleanup.get("idempotent") is True
        else EXPECTED_CLEANUP_AUDIT_DELTA
    )

    def chain(value: Mapping[str, Any]) -> dict[str, Any]:
        raw = value.get("audit_chain") or {}
        if not isinstance(raw, Mapping):
            raise ActivationControlError("recovery audit-chain metadata is invalid")
        try:
            return {
                "verified": raw.get("verified") is True,
                "coverage_complete": raw.get("coverage_complete") is True,
                "entries_checked": int(raw.get("entries_checked", -1)),
                "chained_rows": int(raw.get("chained_rows", -1)),
                "legacy_rows": int(raw.get("legacy_rows", -1)),
                "coverage_gaps": int(raw.get("coverage_gaps", -1)),
                "broken_link_count": int(raw.get("broken_link_count", -1)),
                "total_entries": int(raw.get("total_entries", -1)),
            }
        except (TypeError, ValueError) as exc:
            raise ActivationControlError(
                "recovery audit-chain metadata is invalid"
            ) from exc

    before_chain = chain(before_cleanup)
    final_chain = chain(final)
    chain_exact = (
        all(
            value["verified"]
            and value["coverage_complete"]
            and value["coverage_gaps"] == 0
            and value["broken_link_count"] == 0
            and value["entries_checked"] == value["chained_rows"]
            and value["chained_rows"] + value["legacy_rows"]
            == value["total_entries"]
            for value in (before_chain, final_chain)
        )
        and before_chain["legacy_rows"] == final_chain["legacy_rows"]
        and final_chain["chained_rows"] - before_chain["chained_rows"]
        == final_chain["total_entries"] - before_chain["total_entries"]
    )
    before_flags = before_cleanup.get("feature_state") or {}
    final_flags = final.get("feature_state") or {}
    expected_flag_names = {RENEWAL_FLAG, *OTHER_MONITORING_FLAGS}
    from fixtures.document_renewal_staging import fixture_spec

    spec = fixture_spec()
    expected_fixture = {
        key: spec[key]
        for key in (
            "fixture_key",
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
    final_fixture = final.get("fixture") or {}
    before_fixture = before_cleanup.get("fixture") or {}
    manifest_roots_exact = (
        isinstance(final_fixture, Mapping)
        and isinstance(before_fixture, Mapping)
        and all(final_fixture.get(key) == value for key, value in expected_fixture.items())
        and all(before_fixture.get(key) == value for key, value in expected_fixture.items())
        and final_fixture.get("alert_id") == before_fixture.get("alert_id")
        and isinstance(final_fixture.get("alert_id"), int)
        and final_fixture.get("alert_id") > 0
    )
    comparisons = {
        "permanent_fixture_unchanged": before_cleanup.get("core_fingerprint")
        == final.get("core_fingerprint"),
        "nonfixture_unchanged": before_cleanup.get("isolation_fingerprint")
        == final.get("isolation_fingerprint"),
        "scheduler_unchanged": before_cleanup.get("scheduler_fingerprint")
        == final.get("scheduler_fingerprint"),
        "final_operational_state_empty": all(
            relation_count(final, name) == 0 for name in operational
        ),
        "exact_governed_baseline": all(
            relation_count(final, name) == expected
            for name, expected in exact_baseline_counts.items()
        ),
        "manifest_roots_exact": manifest_roots_exact,
        "flags_remained_off": (
            set(before_flags) == expected_flag_names
            and set(final_flags) == expected_flag_names
            and not any(bool(value) for value in before_flags.values())
            and not any(bool(value) for value in final_flags.values())
        ),
        "cleanup_audit_delta_exact": action_delta == expected_action_delta,
        "audit_chain_integrity_exact": chain_exact,
    }
    if not all(comparisons.values()):
        raise ActivationControlError("recovery fingerprints do not reconcile")
    return {
        "schema_version": "document_renewal_staging_recovery_reconciliation_v1",
        "ok": True,
        "run_id": before_cleanup.get("run_id"),
        "before_counts": {
            name: relation_count(before_cleanup, name) for name in operational
        },
        "final_counts": {name: relation_count(final, name) for name in operational},
        "cleanup_idempotent": cleanup.get("idempotent") is True,
        "cleanup_audit_delta": dict(sorted(action_delta.items())),
        "comparisons": comparisons,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    context = sub.add_parser("validate-context")
    context.add_argument("--sha", required=True)
    context.add_argument("--ref", required=True)
    context.add_argument("--confirm", required=True)

    render = sub.add_parser("render")
    render.add_argument("--source", required=True)
    render.add_argument("--sha", required=True)
    render.add_argument("--run-id", required=True)
    render.add_argument("--issued-at", required=True)
    render.add_argument("--expires-at", required=True)
    render.add_argument("--manifest-sha256", required=True)
    render.add_argument("--output", required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--task-definition", required=True)
    inspect.add_argument("--expected-sha", required=True)
    inspect.add_argument("--expected-mode", choices=("off", "active"), required=True)
    inspect.add_argument("--expected-run-id")
    inspect.add_argument("--output", required=True)
    sub.add_parser(
        "run-fixture-eligibility",
        help="run the exact fixture-only scheduler inside the active staging lease",
    )
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--run-id", required=True)
    cleanup = sub.add_parser("cleanup-fixture")
    cleanup.add_argument("--run-id", required=True)
    cleanup.add_argument("--actor-id", required=True)
    compare = sub.add_parser("compare-fingerprints")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)
    compare.add_argument("--cleanup", required=True)
    compare.add_argument("--output", required=True)
    recovery_compare = sub.add_parser("compare-recovery-fingerprints")
    recovery_compare.add_argument("--before", required=True)
    recovery_compare.add_argument("--final", required=True)
    recovery_compare.add_argument("--cleanup", required=True)
    recovery_compare.add_argument("--output", required=True)
    candidate = sub.add_parser("recovery-candidate")
    candidate.add_argument("--task-definition", required=True)
    candidate.add_argument("--output", required=True)
    recovery = sub.add_parser("verify-recovery-plan")
    recovery.add_argument("--current", required=True)
    recovery.add_argument("--original", required=True)
    recovery.add_argument("--output", required=True)
    residue = sub.add_parser("inactive-residue")
    residue.add_argument("--current", required=True)
    residue.add_argument("--candidates", required=True)
    residue.add_argument("--running", required=True)
    residue.add_argument("--run-id", required=True)
    residue.add_argument("--output", required=True)
    select = sub.add_parser("select-recovery-context")
    select.add_argument("--current", required=True)
    select.add_argument("--candidates", required=True)
    select.add_argument("--running", required=True)
    select.add_argument("--requested-run-id")
    select.add_argument("--output", required=True)
    clear = sub.add_parser("verify-recovery-residue-clear")
    clear.add_argument("--current", required=True)
    clear.add_argument("--candidates", required=True)
    clear.add_argument("--running", required=True)
    clear.add_argument("--output", required=True)
    clean = sub.add_parser("verify-clean-recovery-fingerprint")
    clean.add_argument("--fingerprint", required=True)
    clean.add_argument("--output", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-context":
            sha = validate_release_context(args.sha, args.ref, args.confirm)
            print(json.dumps({"ok": True, "release_sha": sha}))
        elif args.command == "render":
            rendered = render_activation_task_definition(
                _load_json(args.source),
                sha=args.sha,
                run_id=args.run_id,
                issued_at=args.issued_at,
                expires_at=args.expires_at,
                fixture_manifest_sha256=args.manifest_sha256,
            )
            _write_json(args.output, rendered)
            print(json.dumps({"ok": True, "output": args.output}))
        elif args.command == "inspect":
            evidence = inspect_task_definition(
                _load_json(args.task_definition),
                expected_sha=args.expected_sha,
                expected_mode=args.expected_mode,
                expected_run_id=args.expected_run_id,
            )
            _write_json(args.output, evidence)
            print(json.dumps(evidence, sort_keys=True))
        elif args.command == "run-fixture-eligibility":
            print(
                EVIDENCE_PREFIX
                + json.dumps(
                    run_fixture_eligibility(),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        elif args.command == "snapshot":
            print(
                EVIDENCE_PREFIX
                + json.dumps(
                    capture_fixture_fingerprint(run_id=args.run_id),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
            )
        elif args.command == "cleanup-fixture":
            print(
                EVIDENCE_PREFIX
                + json.dumps(
                    cleanup_fixture(run_id=args.run_id, actor_id=args.actor_id),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
            )
        elif args.command == "compare-fingerprints":
            evidence = compare_fingerprints(
                _load_json(args.before),
                _load_json(args.after),
                _load_json(args.cleanup),
            )
            _write_json(args.output, evidence)
            print(json.dumps(evidence, sort_keys=True))
        elif args.command == "compare-recovery-fingerprints":
            evidence = compare_recovery_fingerprints(
                _load_json(args.before),
                _load_json(args.final),
                _load_json(args.cleanup),
            )
            _write_json(args.output, evidence)
            print(json.dumps(evidence, sort_keys=True))
        elif args.command == "recovery-candidate":
            evidence = recovery_candidate(
                _load_json(args.task_definition),
            )
            _write_json(args.output, evidence)
            print(json.dumps(evidence, sort_keys=True))
        elif args.command == "inactive-residue":
            candidates = _load_json(args.candidates).get("task_definitions")
            running = _load_json(args.running).get("task_definition_arns")
            if not isinstance(candidates, list) or not isinstance(running, list):
                raise ActivationControlError("inactive residue inputs are invalid")
            evidence = inactive_harness_residue(
                _load_json(args.current),
                candidates,
                run_id=args.run_id,
                running_task_definition_arns=running,
            )
            _write_json(args.output, evidence)
            print(json.dumps(evidence, sort_keys=True))
        elif args.command == "select-recovery-context":
            candidates = _load_json(args.candidates).get("task_definitions")
            running = _load_json(args.running).get("task_definition_arns")
            if not isinstance(candidates, list) or not isinstance(running, list):
                raise ActivationControlError("recovery discovery inputs are invalid")
            evidence = select_recovery_context(
                _load_json(args.current),
                candidates,
                requested_run_id=args.requested_run_id,
                running_task_definition_arns=running,
            )
            _write_json(args.output, evidence)
            print(json.dumps(evidence, sort_keys=True))
        elif args.command == "verify-recovery-residue-clear":
            candidates = _load_json(args.candidates).get("task_definitions")
            running_input = _load_json(args.running)
            running = running_input.get("task_definition_arns")
            running_definitions = running_input.get("task_definitions", [])
            if not isinstance(candidates, list) or not isinstance(running, list):
                raise ActivationControlError("final residue inputs are invalid")
            if not isinstance(running_definitions, list):
                raise ActivationControlError("final running definition input is invalid")
            evidence = verify_recovery_residue_clear(
                _load_json(args.current),
                candidates,
                running_task_definition_arns=running,
                running_task_definitions=running_definitions,
            )
            _write_json(args.output, evidence)
            print(json.dumps(evidence, sort_keys=True))
        elif args.command == "verify-clean-recovery-fingerprint":
            evidence = verify_clean_recovery_fingerprint(
                _load_json(args.fingerprint)
            )
            _write_json(args.output, evidence)
            print(json.dumps(evidence, sort_keys=True))
        else:
            evidence = verify_recovery_plan(
                _load_json(args.current),
                _load_json(args.original),
            )
            _write_json(args.output, evidence)
            print(json.dumps(evidence, sort_keys=True))
    except (ActivationControlError, StagingControlError) as exc:
        print(f"staging_document_renewal_activation: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ActivationControlError",
    "CONFIRM_TOKEN",
    "EVIDENCE_PREFIX",
    "HARNESS_TAG",
    "ORIGINAL_TASK_DEFINITION_ENV",
    "capture_fixture_fingerprint",
    "cleanup_fixture",
    "compare_fingerprints",
    "compare_recovery_fingerprints",
    "inspect_task_definition",
    "inactive_harness_residue",
    "render_activation_task_definition",
    "recovery_candidate",
    "run_fixture_eligibility",
    "validate_release_context",
    "verify_recovery_plan",
]
