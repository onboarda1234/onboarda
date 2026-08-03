#!/usr/bin/env python3
"""Temporary least-privilege S3 version-inventory grant for harness cleanup.

The existing staging task policy is never edited. A fixed, separate inline
policy grants only version inventory plus exact-version deletion for the one
synthetic client's renewal-candidate prefix. Install refuses any pre-existing
policy. Removal deletes only an exact policy match.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import unquote


EXPECTED_REGION = "af-south-1"
ROLE_NAME = "regmind-staging-task-role"
POLICY_NAME = "document-renewal-staging-harness-list-versions"
BUCKET_ARN = "arn:aws:s3:::regmind-documents-staging"
FIXTURE_PREFIX = (
    "clients/f1xedrnwcli00001/monitoring_renewal_candidate/"
    "fxrnwreq00000001/*"
)
POLICY_DOCUMENT = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "FixtureRenewalCandidateVersionInventory",
            "Effect": "Allow",
            "Action": "s3:ListBucketVersions",
            "Resource": BUCKET_ARN,
            "Condition": {"StringLike": {"s3:prefix": [FIXTURE_PREFIX]}},
        },
        {
            "Sid": "FixtureRenewalCandidateVersionDelete",
            "Effect": "Allow",
            "Action": "s3:DeleteObjectVersion",
            "Resource": (
                "arn:aws:s3:::regmind-documents-staging/"
                "clients/f1xedrnwcli00001/monitoring_renewal_candidate/"
                "fxrnwreq00000001/*"
            ),
        },
    ],
}


class HarnessIamError(RuntimeError):
    """The temporary cleanup permission is missing, stale or too broad."""


def _run(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        capture_output=True,
        text=True,
        check=False,
    )


def _aws(arguments: Sequence[str], *, allow_missing: bool = False) -> dict[str, Any]:
    result = _run(["aws", *arguments, "--output", "json"])
    if result.returncode:
        combined = f"{result.stdout}\n{result.stderr}"
        if allow_missing and "NoSuchEntity" in combined:
            return {"missing": True}
        raise HarnessIamError("AWS IAM command failed")
    try:
        value = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HarnessIamError("AWS IAM response is invalid") from exc
    if not isinstance(value, dict):
        raise HarnessIamError("AWS IAM response is not an object")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _existing_policy() -> Optional[dict[str, Any]]:
    value = _aws(
        [
            "iam",
            "get-role-policy",
            "--role-name",
            ROLE_NAME,
            "--policy-name",
            POLICY_NAME,
        ],
        allow_missing=True,
    )
    if value.get("missing"):
        return None
    document = value.get("PolicyDocument")
    if isinstance(document, str):
        try:
            document = json.loads(unquote(document))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HarnessIamError("temporary policy document is malformed") from exc
    if not isinstance(document, Mapping):
        raise HarnessIamError("temporary policy document is missing")
    return dict(document)


def _is_exact(document: Mapping[str, Any]) -> bool:
    return _canonical(document) == _canonical(POLICY_DOCUMENT)


def _summary(*, status: str, installed: bool) -> dict[str, Any]:
    return {
        "schema_version": "document_renewal_staging_harness_iam_v1",
        "ok": True,
        "role_name": ROLE_NAME,
        "policy_name": POLICY_NAME,
        "status": status,
        "installed": installed,
        "actions": ["s3:ListBucketVersions", "s3:DeleteObjectVersion"],
        "bucket": "regmind-documents-staging",
        "fixture_prefix": FIXTURE_PREFIX,
        "other_permissions_changed": False,
    }


def install() -> dict[str, Any]:
    if _existing_policy() is not None:
        raise HarnessIamError("temporary harness policy already exists")
    result = _run(
        [
            "aws",
            "iam",
            "put-role-policy",
            "--role-name",
            ROLE_NAME,
            "--policy-name",
            POLICY_NAME,
            "--policy-document",
            json.dumps(POLICY_DOCUMENT, separators=(",", ":")),
        ]
    )
    if result.returncode:
        raise HarnessIamError("temporary harness policy install failed")
    current = _existing_policy()
    if current is None or not _is_exact(current):
        raise HarnessIamError("temporary harness policy verification failed")
    return _summary(status="installed_exact", installed=True)


def verify() -> dict[str, Any]:
    current = _existing_policy()
    if current is None or not _is_exact(current):
        raise HarnessIamError("temporary harness policy is absent or not exact")
    return _summary(status="verified_exact", installed=True)


def remove() -> dict[str, Any]:
    current = _existing_policy()
    if current is None:
        return _summary(status="already_absent", installed=False)
    if not _is_exact(current):
        raise HarnessIamError("refusing to remove a non-exact inline policy")
    result = _run(
        [
            "aws",
            "iam",
            "delete-role-policy",
            "--role-name",
            ROLE_NAME,
            "--policy-name",
            POLICY_NAME,
        ]
    )
    if result.returncode:
        raise HarnessIamError("temporary harness policy removal failed")
    if _existing_policy() is not None:
        raise HarnessIamError("temporary harness policy remains after removal")
    return _summary(status="removed", installed=False)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "verify", "remove"))
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = {"install": install, "verify": verify, "remove": remove}[
            args.command
        ]()
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(result, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except HarnessIamError as exc:
        print(f"staging_document_renewal_iam: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BUCKET_ARN",
    "FIXTURE_PREFIX",
    "HarnessIamError",
    "POLICY_DOCUMENT",
    "POLICY_NAME",
    "ROLE_NAME",
    "install",
    "remove",
    "verify",
]
