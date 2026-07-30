#!/usr/bin/env python3
"""Dry-run-first operator control for staging ECR tag immutability.

This tool is deliberately narrow.  It can converge exactly one repository to
``IMMUTABLE`` and has no command-line path for weakening it back to mutable.
The normal staging deployment workflow only *verifies* this control; an
authorised operator runs this tool once during release-control activation.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Callable, Sequence


EXPECTED_ACCOUNT_ID = "782913119880"
DEFAULT_REGION = "af-south-1"
DEFAULT_REPOSITORY = "regmind-backend"
CONFIRMATION = "SET-regmind-backend-IMMUTABLE"


class ImmutabilityError(RuntimeError):
    """Raised when the requested ECR control cannot be proven."""


def _aws_json(arguments: Sequence[str]) -> dict[str, Any]:
    command = ["aws", *arguments, "--output", "json"]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "AWS CLI command failed").strip()
        raise ImmutabilityError(detail)
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ImmutabilityError("AWS CLI returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ImmutabilityError("AWS CLI returned a non-object response")
    return payload


def _repository_state(
    *,
    region: str,
    repository: str,
    aws_json: Callable[[Sequence[str]], dict[str, Any]],
) -> dict[str, Any]:
    caller = aws_json(["sts", "get-caller-identity"])
    account_id = str(caller.get("Account") or "")
    if account_id != EXPECTED_ACCOUNT_ID:
        raise ImmutabilityError(
            f"refusing account {account_id or '<missing>'}; expected {EXPECTED_ACCOUNT_ID}"
        )

    payload = aws_json(
        [
            "ecr",
            "describe-repositories",
            "--repository-names",
            repository,
            "--region",
            region,
        ]
    )
    repositories = payload.get("repositories") or []
    if len(repositories) != 1:
        raise ImmutabilityError("expected exactly one ECR repository")
    observed = repositories[0]
    if observed.get("repositoryName") != repository:
        raise ImmutabilityError("ECR repository identity mismatch")
    if str(observed.get("registryId") or "") != EXPECTED_ACCOUNT_ID:
        raise ImmutabilityError("ECR registry account mismatch")
    return {
        "account_id": account_id,
        "region": region,
        "repository": repository,
        "repository_arn": observed.get("repositoryArn"),
        "repository_uri": observed.get("repositoryUri"),
        "image_tag_mutability": observed.get("imageTagMutability"),
        "scan_on_push": bool(
            (observed.get("imageScanningConfiguration") or {}).get("scanOnPush")
        ),
    }


def enforce(
    *,
    region: str = DEFAULT_REGION,
    repository: str = DEFAULT_REPOSITORY,
    apply: bool = False,
    confirmation: str = "",
    aws_json: Callable[[Sequence[str]], dict[str, Any]] = _aws_json,
) -> dict[str, Any]:
    """Plan or apply exact ECR immutability and verify the postcondition."""

    if region != DEFAULT_REGION or repository != DEFAULT_REPOSITORY:
        raise ImmutabilityError(
            "this operator control is pinned to regmind-backend in af-south-1"
        )
    before = _repository_state(
        region=region,
        repository=repository,
        aws_json=aws_json,
    )
    current = before["image_tag_mutability"]
    if current == "IMMUTABLE":
        return {
            "ok": True,
            "mode": "verified_noop",
            "changed": False,
            "before": before,
            "after": before,
        }

    plan = {
        "ok": True,
        "mode": "dry_run",
        "changed": False,
        "before": before,
        "planned_after": {
            **before,
            "image_tag_mutability": "IMMUTABLE",
        },
    }
    if not apply:
        return plan
    if confirmation != CONFIRMATION:
        raise ImmutabilityError(
            f"--confirm must equal {CONFIRMATION!r} when --apply is used"
        )

    aws_json(
        [
            "ecr",
            "put-image-tag-mutability",
            "--repository-name",
            repository,
            "--image-tag-mutability",
            "IMMUTABLE",
            "--region",
            region,
        ]
    )
    after = _repository_state(
        region=region,
        repository=repository,
        aws_json=aws_json,
    )
    if after["image_tag_mutability"] != "IMMUTABLE":
        raise ImmutabilityError("ECR immutability postcondition was not satisfied")
    return {
        "ok": True,
        "mode": "applied",
        "changed": True,
        "before": before,
        "after": after,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = enforce(
            region=args.region,
            repository=args.repository,
            apply=args.apply,
            confirmation=args.confirm,
        )
    except ImmutabilityError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
