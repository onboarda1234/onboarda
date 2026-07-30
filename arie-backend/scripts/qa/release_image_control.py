#!/usr/bin/env python3
"""Fail-closed ECR image preparation and vulnerability gating for staging.

The image preparation path is safe under immutable tags:

* a missing full-SHA tag is built and pushed once;
* an existing full-SHA tag is pulled by digest and verified;
* no code path deletes, retags, or overwrites an existing remote tag.

The scan gate evaluates the exact manifest digest and refuses any incomplete
scan, every CRITICAL finding, and every HIGH finding without an exact,
unexpired entry in the versioned acceptance manifest.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    from scripts.qa import container_vulnerability_acceptances as acceptance_manifest
except ImportError:  # pragma: no cover - direct script execution
    import container_vulnerability_acceptances as acceptance_manifest


EXPECTED_ACCOUNT_ID = "782913119880"
EXPECTED_REGION = "af-south-1"
EXPECTED_REPOSITORY = "regmind-backend"
EXPECTED_REF = "refs/heads/main"
EXPECTED_REPOSITORY_URI = (
    "782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend"
)
TRIVY_PYTHON_PACKAGE_TYPES = {"pip", "python-pkg"}
SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
ACCEPTANCE_FIELDS = (
    "finding_id",
    "package_name",
    "installed_version",
    "owner",
    "technical_justification",
    "reachability_analysis",
    "compensating_controls",
    "approved_by",
    "expires_at",
)


class ReleaseImageError(RuntimeError):
    """Raised when immutable image or scan evidence cannot be proven."""


def _run(
    command: Sequence[str],
    *,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        executable = Path(command[0]).name if command else "<command>"
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise ReleaseImageError(f"{executable} failed: {detail}")
    return result


def _aws_json(
    arguments: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run,
) -> dict[str, Any]:
    result = runner(["aws", *arguments, "--output", "json"])
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ReleaseImageError("AWS CLI returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ReleaseImageError("AWS CLI returned a non-object response")
    return payload


def validate_release_context(sha: str, ref: str) -> str:
    sha = str(sha or "").strip()
    if not SHA_RE.fullmatch(sha):
        raise ReleaseImageError(
            "release SHA must be a full 40-character lowercase hexadecimal value"
        )
    if ref != EXPECTED_REF:
        raise ReleaseImageError(
            f"staging releases must use {EXPECTED_REF}; observed {ref or '<missing>'}"
        )
    return sha


def _repository_preflight(
    *,
    region: str,
    repository: str,
    aws_json: Callable[[Sequence[str]], dict[str, Any]],
) -> dict[str, Any]:
    caller = aws_json(["sts", "get-caller-identity"])
    if str(caller.get("Account") or "") != EXPECTED_ACCOUNT_ID:
        raise ReleaseImageError("AWS account does not match the staging release account")
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
        raise ReleaseImageError("expected exactly one ECR repository")
    observed = repositories[0]
    if (
        observed.get("repositoryName") != EXPECTED_REPOSITORY
        or str(observed.get("registryId") or "") != EXPECTED_ACCOUNT_ID
        or region != EXPECTED_REGION
    ):
        raise ReleaseImageError("ECR repository identity mismatch")
    if observed.get("imageTagMutability") != "IMMUTABLE":
        raise ReleaseImageError("ECR imageTagMutability must equal IMMUTABLE")
    if not bool(
        (observed.get("imageScanningConfiguration") or {}).get("scanOnPush")
    ):
        raise ReleaseImageError("ECR scanOnPush must be enabled")
    repository_uri = str(observed.get("repositoryUri") or "")
    if not repository_uri:
        raise ReleaseImageError("ECR repository URI is missing")
    return {
        "account_id": EXPECTED_ACCOUNT_ID,
        "region": region,
        "repository": repository,
        "repository_uri": repository_uri,
        "image_tag_mutability": "IMMUTABLE",
        "scan_on_push": True,
    }


def _find_image_detail(
    *,
    sha: str,
    region: str,
    repository: str,
    aws_json: Callable[[Sequence[str]], dict[str, Any]],
) -> dict[str, Any] | None:
    payload = aws_json(
        [
            "ecr",
            "describe-images",
            "--repository-name",
            repository,
            "--image-ids",
            f"imageTag={sha}",
            "--region",
            region,
        ]
    )
    details = payload.get("imageDetails") or []
    if not details:
        return None
    if len(details) != 1:
        raise ReleaseImageError("ECR returned multiple records for one exact tag")
    detail = details[0]
    digest = str(detail.get("imageDigest") or "")
    tags = detail.get("imageTags") or []
    if not DIGEST_RE.fullmatch(digest) or sha not in tags:
        raise ReleaseImageError("ECR tag-to-digest evidence is malformed")
    return detail


def _inspect_environment(items: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" in item:
            name, value = item.split("=", 1)
            result[name] = value
    return result


def validate_image_inspect(
    payload: Any,
    *,
    sha: str,
    expected_ref: str = EXPECTED_REF,
) -> dict[str, Any]:
    if not isinstance(payload, list) or len(payload) != 1:
        raise ReleaseImageError("docker inspect must return exactly one image")
    image = payload[0]
    if not isinstance(image, dict):
        raise ReleaseImageError("docker inspect image record is malformed")
    config = image.get("Config") or {}
    labels = config.get("Labels") or {}
    environment = _inspect_environment(config.get("Env") or [])
    failures: list[str] = []
    if labels.get("git.sha") != sha:
        failures.append("git.sha label mismatch")
    if labels.get("git.ref") != expected_ref:
        failures.append("git.ref label mismatch")
    if environment.get("GIT_SHA") != sha:
        failures.append("GIT_SHA image environment mismatch")
    if environment.get("IMAGE_TAG") != sha:
        failures.append("IMAGE_TAG image environment mismatch")
    if image.get("Architecture") != "amd64":
        failures.append("image architecture is not amd64")
    if image.get("Os") != "linux":
        failures.append("image operating system is not linux")
    if failures:
        raise ReleaseImageError("; ".join(failures))
    build_time = str(
        labels.get("build.time") or environment.get("BUILD_TIME") or ""
    ).strip()
    if not build_time:
        raise ReleaseImageError("image build-time provenance is missing")
    return {
        "git_sha": sha,
        "git_ref": expected_ref,
        "build_time": build_time,
        "architecture": "amd64",
        "os": "linux",
    }


def prepare_image(
    *,
    sha: str,
    ref: str,
    region: str,
    repository: str,
    source_dir: str,
    aws_json: Callable[[Sequence[str]], dict[str, Any]] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run,
    now: Callable[[], dt.datetime] | None = None,
) -> dict[str, Any]:
    sha = validate_release_context(sha, ref)
    if region != EXPECTED_REGION or repository != EXPECTED_REPOSITORY:
        raise ReleaseImageError("release image target is not the pinned staging ECR")
    aws_call = aws_json or (lambda args: _aws_json(args, runner=runner))
    repository_state = _repository_preflight(
        region=region,
        repository=repository,
        aws_json=aws_call,
    )
    image_uri = f"{repository_state['repository_uri']}:{sha}"

    detail: dict[str, Any] | None
    try:
        detail = _find_image_detail(
            sha=sha,
            region=region,
            repository=repository,
            aws_json=aws_call,
        )
    except ReleaseImageError as exc:
        # ``describe-images`` represents a missing exact tag as ImageNotFound.
        # Every other AWS failure remains fail-closed.
        if "ImageNotFoundException" not in str(exc):
            raise
        detail = None

    action = "reused"
    if detail is None:
        action = "built_and_pushed"
        clock = now or (lambda: dt.datetime.now(dt.timezone.utc))
        build_time = clock().replace(microsecond=0).isoformat().replace("+00:00", "Z")
        runner(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--build-arg",
                f"GIT_SHA={sha}",
                "--build-arg",
                f"BUILD_TIME={build_time}",
                "--build-arg",
                f"IMAGE_TAG={sha}",
                "--label",
                f"git.sha={sha}",
                "--label",
                f"git.ref={ref}",
                "--label",
                f"build.time={build_time}",
                "-t",
                image_uri,
                ".",
            ],
            cwd=source_dir,
        )
        # Under immutable tags, any race or pre-existing tag causes this push to
        # fail.  The control never deletes/retries/overwrites it.
        runner(["docker", "push", image_uri], cwd=source_dir)
        detail = _find_image_detail(
            sha=sha,
            region=region,
            repository=repository,
            aws_json=aws_call,
        )
        if detail is None:
            raise ReleaseImageError("pushed image is not visible in ECR")

    digest = str(detail.get("imageDigest") or "")
    digest_uri = f"{repository_state['repository_uri']}@{digest}"
    runner(["docker", "pull", digest_uri])
    inspect_result = runner(["docker", "image", "inspect", digest_uri])
    try:
        inspect_payload = json.loads(inspect_result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ReleaseImageError("docker inspect returned invalid JSON") from exc
    provenance = validate_image_inspect(inspect_payload, sha=sha, expected_ref=ref)
    return {
        "schema_version": "release_image_evidence_v1",
        "ok": True,
        "action": action,
        "repository": repository_state,
        "image": image_uri,
        "digest_uri": digest_uri,
        "image_tag": sha,
        "image_digest": digest,
        "image_pushed_at": detail.get("imagePushedAt"),
        "provenance": provenance,
    }


def _finding_identity(finding: Mapping[str, Any]) -> dict[str, str]:
    attributes = {
        str(item.get("key") or ""): str(item.get("value") or "")
        for item in finding.get("attributes") or []
        if isinstance(item, Mapping)
    }
    return {
        "finding_id": str(finding.get("name") or ""),
        "severity": str(finding.get("severity") or "").upper(),
        "package_name": attributes.get("package_name", ""),
        "installed_version": attributes.get("package_version", ""),
        "uri": str(finding.get("uri") or ""),
    }


def validate_acceptances(
    acceptances: Iterable[Mapping[str, Any]],
    *,
    today: dt.date | None = None,
) -> dict[tuple[str, str, str], dict[str, str]]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    validated: dict[tuple[str, str, str], dict[str, str]] = {}
    for raw in acceptances:
        item = {field: str(raw.get(field) or "").strip() for field in ACCEPTANCE_FIELDS}
        missing = [field for field, value in item.items() if not value]
        if missing:
            raise ReleaseImageError(
                "HIGH acceptance is missing required fields: " + ", ".join(missing)
            )
        try:
            expiry = dt.date.fromisoformat(item["expires_at"])
        except ValueError as exc:
            raise ReleaseImageError("HIGH acceptance expiry must use YYYY-MM-DD") from exc
        if expiry <= today:
            raise ReleaseImageError(
                f"HIGH acceptance for {item['finding_id']} is expired"
            )
        key = (
            item["finding_id"],
            item["package_name"],
            item["installed_version"],
        )
        if key in validated:
            raise ReleaseImageError("duplicate HIGH acceptance identity")
        validated[key] = item
    return validated


def evaluate_normalized_findings(
    findings: Iterable[Mapping[str, Any]],
    *,
    acceptances: Iterable[Mapping[str, Any]] = (),
    manifest_version: str = acceptance_manifest.MANIFEST_VERSION,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Apply one acceptance policy to normalized ECR or Trivy findings."""

    normalized: list[dict[str, str]] = []
    for raw in findings:
        item = {
            "finding_id": str(raw.get("finding_id") or "").strip(),
            "severity": str(raw.get("severity") or "").strip().upper(),
            "package_name": str(raw.get("package_name") or "").strip(),
            "installed_version": str(raw.get("installed_version") or "").strip(),
            "uri": str(raw.get("uri") or "").strip(),
            "source": str(raw.get("source") or "").strip(),
        }
        if item["severity"] in {"CRITICAL", "HIGH"} and not all(
            item[field]
            for field in ("finding_id", "package_name", "installed_version")
        ):
            raise ReleaseImageError(
                "CRITICAL/HIGH finding identity is incomplete and cannot be evaluated"
            )
        normalized.append(item)

    critical = [item for item in normalized if item["severity"] == "CRITICAL"]
    high = [item for item in normalized if item["severity"] == "HIGH"]
    if critical:
        raise ReleaseImageError(
            f"exact image has {len(critical)} CRITICAL vulnerability finding(s)"
        )
    accepted = validate_acceptances(acceptances, today=today)
    accepted_rows: list[dict[str, str]] = []
    unaccepted_rows: list[dict[str, str]] = []
    for finding in high:
        key = (
            finding["finding_id"],
            finding["package_name"],
            finding["installed_version"],
        )
        acceptance = accepted.get(key)
        if acceptance is None:
            unaccepted_rows.append(finding)
        else:
            accepted_rows.append(
                {
                    **finding,
                    "owner": acceptance["owner"],
                    "technical_justification": acceptance[
                        "technical_justification"
                    ],
                    "reachability_analysis": acceptance["reachability_analysis"],
                    "compensating_controls": acceptance["compensating_controls"],
                    "approved_by": acceptance["approved_by"],
                    "expires_at": acceptance["expires_at"],
                }
            )
    observed_high_keys = {
        (
            item["finding_id"],
            item["package_name"],
            item["installed_version"],
        )
        for item in high
    }
    if unaccepted_rows:
        raise ReleaseImageError(
            f"exact image has {len(unaccepted_rows)} unaccepted HIGH finding(s)"
        )
    if sorted(set(accepted).difference(observed_high_keys)):
        raise ReleaseImageError("acceptance manifest contains stale/unmatched entries")
    counts: dict[str, int] = {}
    for finding in normalized:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return {
        "ok": True,
        "severity_counts": {
            severity: counts.get(severity, 0)
            for severity in (
                "CRITICAL",
                "HIGH",
                "MEDIUM",
                "LOW",
                "INFORMATIONAL",
                "UNKNOWN",
                "UNDEFINED",
            )
        },
        "acceptance_manifest_version": manifest_version,
        "accepted_highs": accepted_rows,
        "unaccepted_highs": [],
    }


def evaluate_scan(
    payload: Mapping[str, Any],
    *,
    acceptances: Iterable[Mapping[str, Any]] = (),
    manifest_version: str = acceptance_manifest.MANIFEST_VERSION,
    today: dt.date | None = None,
) -> dict[str, Any]:
    status = str((payload.get("imageScanStatus") or {}).get("status") or "")
    if status != "COMPLETE":
        raise ReleaseImageError(
            f"exact-image vulnerability scan is not COMPLETE (status={status or 'missing'})"
        )
    findings_block = payload.get("imageScanFindings") or {}
    findings = [
        _finding_identity(item)
        for item in findings_block.get("findings") or []
        if isinstance(item, Mapping)
    ]
    critical = [item for item in findings if item["severity"] == "CRITICAL"]
    high = [item for item in findings if item["severity"] == "HIGH"]
    summary_counts = findings_block.get("findingSeverityCounts") or {}
    if int(summary_counts.get("CRITICAL") or 0) != len(critical):
        raise ReleaseImageError("CRITICAL scan summary does not match finding rows")
    if int(summary_counts.get("HIGH") or 0) != len(high):
        raise ReleaseImageError("HIGH scan summary does not match finding rows")
    policy = evaluate_normalized_findings(
        findings,
        acceptances=acceptances,
        manifest_version=manifest_version,
        today=today,
    )
    return {
        "schema_version": "container_scan_evidence_v1",
        **policy,
        "status": status,
        "completed_at": findings_block.get("imageScanCompletedAt"),
        "vulnerability_source_updated_at": findings_block.get(
            "vulnerabilitySourceUpdatedAt"
        ),
        "severity_counts": {
            severity: int(summary_counts.get(severity) or 0)
            for severity in (
                "CRITICAL",
                "HIGH",
                "MEDIUM",
                "LOW",
                "INFORMATIONAL",
                "UNDEFINED",
            )
        },
    }


def evaluate_trivy(
    payload: Mapping[str, Any],
    *,
    sha: str,
    digest: str = "",
    acceptances: Iterable[Mapping[str, Any]] = (),
    manifest_version: str = acceptance_manifest.MANIFEST_VERSION,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Normalize Trivy OS/language findings into the shared policy."""

    sha = validate_release_context(sha, EXPECTED_REF)
    if not isinstance(payload, Mapping):
        raise ReleaseImageError("Trivy report is not a JSON object")
    results = payload.get("Results")
    if not isinstance(results, list) or not results:
        raise ReleaseImageError("Trivy report has no scan results")
    if digest and not DIGEST_RE.fullmatch(digest):
        raise ReleaseImageError("Trivy image digest is malformed")
    expected_artifact = (
        f"{EXPECTED_REPOSITORY_URI}@{digest}"
        if digest
        else f"regmind-backend:{sha}"
    )
    artifact_name = str(payload.get("ArtifactName") or "")
    if artifact_name != expected_artifact:
        raise ReleaseImageError(
            "Trivy artifact does not match the exact release image"
        )
    if payload.get("ArtifactType") not in {"container_image", "container image"}:
        raise ReleaseImageError("Trivy artifact type is not a container image")

    normalized: list[dict[str, str]] = []
    scanned_targets: list[dict[str, str | int]] = []
    has_os_package_coverage = False
    has_python_package_coverage = False
    for result in results:
        if not isinstance(result, Mapping):
            raise ReleaseImageError("Trivy result row is malformed")
        target = str(result.get("Target") or "")
        result_class = str(result.get("Class") or "")
        result_type = str(result.get("Type") or "")
        if not target:
            raise ReleaseImageError("Trivy result target is missing")
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            raise ReleaseImageError("Trivy vulnerability list is malformed")
        if result_class == "os-pkgs":
            has_os_package_coverage = True
        # Trivy 0.72.0 emits ``python-pkg`` for installed Python packages.
        # ``pip`` is retained as a narrow compatibility value for reports
        # produced by older supported Trivy schemas.
        if (
            result_class == "lang-pkgs"
            and result_type in TRIVY_PYTHON_PACKAGE_TYPES
        ):
            has_python_package_coverage = True
        scanned_targets.append(
            {
                "target": target,
                "class": result_class,
                "type": result_type,
                "finding_count": len(vulnerabilities),
            }
        )
        for finding in vulnerabilities:
            if not isinstance(finding, Mapping):
                raise ReleaseImageError("Trivy finding row is malformed")
            normalized.append(
                {
                    "finding_id": str(finding.get("VulnerabilityID") or ""),
                    "severity": str(finding.get("Severity") or ""),
                    "package_name": str(finding.get("PkgName") or ""),
                    "installed_version": str(finding.get("InstalledVersion") or ""),
                    "uri": str(finding.get("PrimaryURL") or ""),
                    "source": f"{result_class}:{result_type}:{target}",
                }
            )
    if not has_os_package_coverage:
        raise ReleaseImageError("Trivy OS package coverage is missing")
    if not has_python_package_coverage:
        raise ReleaseImageError("Trivy Python package coverage is missing")
    policy = evaluate_normalized_findings(
        normalized,
        acceptances=acceptances,
        manifest_version=manifest_version,
        today=today,
    )
    return {
        "schema_version": "container_security_pr_scan_v1",
        **policy,
        "status": "COMPLETE",
        "scanner": (
            "aquasec/trivy:0.72.0@"
            "sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
        ),
        "image_tag": sha,
        "image_digest": digest or None,
        "artifact_type": payload.get("ArtifactType"),
        "targets": scanned_targets,
    }


def scan_exact_image(
    *,
    digest: str,
    region: str,
    repository: str,
    aws_json: Callable[[Sequence[str]], dict[str, Any]] | None = None,
    attempts: int = 30,
    interval_seconds: int = 10,
    sleeper: Callable[[float], None] = time.sleep,
    today: dt.date | None = None,
) -> dict[str, Any]:
    if not DIGEST_RE.fullmatch(digest):
        raise ReleaseImageError("image digest is malformed")
    if region != EXPECTED_REGION or repository != EXPECTED_REPOSITORY:
        raise ReleaseImageError("scan target is not the pinned staging image")
    aws_call = aws_json or _aws_json
    transient = {"IN_PROGRESS", "PENDING", "ACTIVE"}
    payload: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        payload = aws_call(
            [
                "ecr",
                "describe-image-scan-findings",
                "--repository-name",
                repository,
                "--image-id",
                f"imageDigest={digest}",
                "--region",
                region,
            ]
        )
        status = str((payload.get("imageScanStatus") or {}).get("status") or "")
        if status == "COMPLETE":
            break
        if status not in transient:
            raise ReleaseImageError(
                f"exact-image vulnerability scan unavailable (status={status or 'missing'})"
            )
        if attempt == attempts:
            raise ReleaseImageError("exact-image vulnerability scan timed out")
        sleeper(interval_seconds)
    if payload is None:
        raise ReleaseImageError("exact-image vulnerability scan returned no evidence")
    result = evaluate_scan(
        payload,
        acceptances=acceptance_manifest.ACCEPTANCES,
        manifest_version=acceptance_manifest.MANIFEST_VERSION,
        today=today,
    )
    result.update(
        {
            "repository": repository,
            "region": region,
            "image_digest": digest,
        }
    )
    return result


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
                raise ReleaseImageError("GitHub output values must be single-line")
            handle.write(f"{key}={text}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--sha", required=True)
    prepare.add_argument("--ref", required=True)
    prepare.add_argument("--region", default=EXPECTED_REGION)
    prepare.add_argument("--repository", default=EXPECTED_REPOSITORY)
    prepare.add_argument("--source-dir", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--github-output", default="")

    scan = subparsers.add_parser("scan")
    scan.add_argument("--image-evidence", required=True)
    scan.add_argument("--region", default=EXPECTED_REGION)
    scan.add_argument("--repository", default=EXPECTED_REPOSITORY)
    scan.add_argument("--output", required=True)

    trivy = subparsers.add_parser("trivy")
    trivy.add_argument("--input", required=True)
    trivy.add_argument("--sha", required=True)
    trivy.add_argument("--digest", default="")
    trivy.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_image(
                sha=args.sha,
                ref=args.ref,
                region=args.region,
                repository=args.repository,
                source_dir=args.source_dir,
            )
            _write_json(args.output, result)
            _github_outputs(
                args.github_output,
                {
                    "image": result["image"],
                    "digest_uri": result["digest_uri"],
                    "image_digest": result["image_digest"],
                    "image_tag": result["image_tag"],
                    "build_time": result["provenance"]["build_time"],
                    "image_action": result["action"],
                },
            )
        elif args.command == "scan":
            image_evidence = json.loads(
                Path(args.image_evidence).read_text(encoding="utf-8")
            )
            result = scan_exact_image(
                digest=str(image_evidence.get("image_digest") or ""),
                region=args.region,
                repository=args.repository,
            )
            _write_json(args.output, result)
        else:
            try:
                trivy_payload = json.loads(
                    Path(args.input).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise ReleaseImageError("Trivy scan output is unavailable or invalid") from exc
            result = evaluate_trivy(
                trivy_payload,
                sha=args.sha,
                digest=args.digest,
                acceptances=acceptance_manifest.ACCEPTANCES,
                manifest_version=acceptance_manifest.MANIFEST_VERSION,
            )
            _write_json(args.output, result)
    except (ReleaseImageError, OSError, json.JSONDecodeError) as exc:
        output_path = getattr(args, "output", "")
        if output_path:
            try:
                _write_json(
                    output_path,
                    {
                        "schema_version": "container_scan_failure_v1",
                        "ok": False,
                        "error": str(exc),
                    },
                )
            except OSError:
                pass
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
