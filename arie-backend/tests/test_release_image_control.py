import datetime as dt
import json
import subprocess

import pytest

from scripts.qa import release_image_control as control


SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
URI = "782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend"


def _repository():
    return {
        "repositories": [
            {
                "repositoryName": control.EXPECTED_REPOSITORY,
                "registryId": control.EXPECTED_ACCOUNT_ID,
                "repositoryUri": URI,
                "imageTagMutability": "IMMUTABLE",
                "imageScanningConfiguration": {"scanOnPush": True},
            }
        ]
    }


def _inspect(*, sha=SHA):
    return [
        {
            "Architecture": "amd64",
            "Os": "linux",
            "Config": {
                "Labels": {
                    "git.sha": sha,
                    "git.ref": control.EXPECTED_REF,
                    "build.time": "2026-07-30T12:00:00Z",
                },
                "Env": [
                    f"GIT_SHA={sha}",
                    f"IMAGE_TAG={sha}",
                    "BUILD_TIME=2026-07-30T12:00:00Z",
                ],
            },
        }
    ]


def _aws(existing=True, *, mutability="IMMUTABLE"):
    calls = []

    def aws_json(arguments):
        calls.append(list(arguments))
        if arguments[:2] == ["sts", "get-caller-identity"]:
            return {"Account": control.EXPECTED_ACCOUNT_ID}
        if arguments[:2] == ["ecr", "describe-repositories"]:
            payload = _repository()
            payload["repositories"][0]["imageTagMutability"] = mutability
            return payload
        if arguments[:2] == ["ecr", "describe-images"]:
            if not existing:
                return {"imageDetails": []}
            return {
                "imageDetails": [
                    {
                        "imageDigest": DIGEST,
                        "imageTags": [SHA],
                        "imagePushedAt": "2026-07-30T12:00:01Z",
                    }
                ]
            }
        raise AssertionError(arguments)

    return aws_json, calls


def _runner(inspect=None, *, push_error=None):
    calls = []
    inspect_payload = _inspect() if inspect is None else inspect

    def runner(command, **kwargs):
        calls.append((list(command), kwargs))
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(inspect_payload), stderr=""
            )
        if command[:2] == ["docker", "push"] and push_error:
            raise control.ReleaseImageError(push_error)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    return runner, calls


def test_existing_sha_image_is_reused_without_build_or_push():
    aws_json, _ = _aws(existing=True)
    runner, calls = _runner()
    result = control.prepare_image(
        sha=SHA,
        ref=control.EXPECTED_REF,
        region=control.EXPECTED_REGION,
        repository=control.EXPECTED_REPOSITORY,
        source_dir="/tmp/source",
        aws_json=aws_json,
        runner=runner,
    )
    commands = [call[0] for call in calls]
    assert result["action"] == "reused"
    assert result["image_digest"] == DIGEST
    assert not any(command[:2] == ["docker", "build"] for command in commands)
    assert not any(command[:2] == ["docker", "push"] for command in commands)
    assert ["docker", "pull", f"{URI}@{DIGEST}"] in commands


def test_missing_sha_builds_and_pushes_once():
    describe_count = 0

    def aws_json(arguments):
        nonlocal describe_count
        if arguments[:2] == ["sts", "get-caller-identity"]:
            return {"Account": control.EXPECTED_ACCOUNT_ID}
        if arguments[:2] == ["ecr", "describe-repositories"]:
            return _repository()
        if arguments[:2] == ["ecr", "describe-images"]:
            describe_count += 1
            if describe_count == 1:
                return {"imageDetails": []}
            return {
                "imageDetails": [
                    {
                        "imageDigest": DIGEST,
                        "imageTags": [SHA],
                        "imagePushedAt": "2026-07-30T12:00:01Z",
                    }
                ]
            }
        raise AssertionError(arguments)

    runner, calls = _runner()
    result = control.prepare_image(
        sha=SHA,
        ref=control.EXPECTED_REF,
        region=control.EXPECTED_REGION,
        repository=control.EXPECTED_REPOSITORY,
        source_dir="/tmp/source",
        aws_json=aws_json,
        runner=runner,
        now=lambda: dt.datetime(2026, 7, 30, 12, tzinfo=dt.timezone.utc),
    )
    commands = [call[0] for call in calls]
    assert result["action"] == "built_and_pushed"
    assert sum(command[:2] == ["docker", "build"] for command in commands) == 1
    assert sum(command[:2] == ["docker", "push"] for command in commands) == 1
    build = next(command for command in commands if command[:2] == ["docker", "build"])
    assert ["--platform", "linux/amd64"] == build[2:4]
    assert f"GIT_SHA={SHA}" in build
    assert "BUILD_TIME=2026-07-30T12:00:00Z" in build
    assert f"IMAGE_TAG={SHA}" in build
    assert f"git.sha={SHA}" in build
    assert f"git.ref={control.EXPECTED_REF}" in build
    assert "build.time=2026-07-30T12:00:00Z" in build
    assert ["-t", f"{URI}:{SHA}", "."] == build[-3:]


def test_immutable_preflight_and_existing_provenance_fail_closed():
    aws_json, _ = _aws(existing=True, mutability="MUTABLE")
    runner, _ = _runner()
    with pytest.raises(control.ReleaseImageError, match="IMMUTABLE"):
        control.prepare_image(
            sha=SHA,
            ref=control.EXPECTED_REF,
            region=control.EXPECTED_REGION,
            repository=control.EXPECTED_REPOSITORY,
            source_dir="/tmp/source",
            aws_json=aws_json,
            runner=runner,
        )

    immutable, _ = _aws(existing=True)
    wrong_runner, calls = _runner(inspect=_inspect(sha="c" * 40))
    with pytest.raises(control.ReleaseImageError, match="git.sha"):
        control.prepare_image(
            sha=SHA,
            ref=control.EXPECTED_REF,
            region=control.EXPECTED_REGION,
            repository=control.EXPECTED_REPOSITORY,
            source_dir="/tmp/source",
            aws_json=immutable,
            runner=wrong_runner,
        )
    assert not any(call[0][:2] == ["docker", "push"] for call in calls)


def test_immutable_tag_collision_is_not_deleted_overwritten_or_retried():
    aws_json, _ = _aws(existing=False)
    runner, calls = _runner(
        push_error="ImageTagAlreadyExistsException: immutable tag collision"
    )
    with pytest.raises(control.ReleaseImageError, match="ImageTagAlreadyExists"):
        control.prepare_image(
            sha=SHA,
            ref=control.EXPECTED_REF,
            region=control.EXPECTED_REGION,
            repository=control.EXPECTED_REPOSITORY,
            source_dir="/tmp/source",
            aws_json=aws_json,
            runner=runner,
        )
    commands = [call[0] for call in calls]
    assert sum(command[:2] == ["docker", "push"] for command in commands) == 1
    assert not any("delete" in " ".join(command) for command in commands)
    assert not any(command[:2] == ["docker", "tag"] for command in commands)


def _finding(severity, *, finding_id="CVE-2026-1", package="glibc", version="1"):
    return {
        "name": finding_id,
        "severity": severity,
        "uri": f"https://example.test/{finding_id}",
        "attributes": [
            {"key": "package_name", "value": package},
            {"key": "package_version", "value": version},
        ],
    }


def _scan(findings, status="COMPLETE"):
    counts = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return {
        "imageScanStatus": {"status": status},
        "imageScanFindings": {
            "findingSeverityCounts": counts,
            "findings": findings,
            "imageScanCompletedAt": "2026-07-30T12:00:00Z",
            "vulnerabilitySourceUpdatedAt": "2026-07-30T12:00:00Z",
        },
    }


def _acceptance(**overrides):
    value = {
        "finding_id": "CVE-2026-1",
        "package_name": "glibc",
        "installed_version": "1",
        "owner": "Security Owner",
        "technical_justification": "No fixed version is available.",
        "reachability_analysis": "The vulnerable function is not invoked.",
        "compensating_controls": "Network ingress and non-root runtime.",
        "approved_by": "Founder Security Approval",
        "expires_at": "2026-12-31",
    }
    value.update(overrides)
    return value


def test_scan_fails_on_critical_unaccepted_high_and_unavailable():
    with pytest.raises(control.ReleaseImageError, match="CRITICAL"):
        control.evaluate_scan(_scan([_finding("CRITICAL")]))
    with pytest.raises(control.ReleaseImageError, match="unaccepted HIGH"):
        control.evaluate_scan(_scan([_finding("HIGH")]))
    with pytest.raises(control.ReleaseImageError, match="not COMPLETE"):
        control.evaluate_scan(_scan([], status="FAILED"))


def test_high_acceptance_is_exact_versioned_and_expiring():
    result = control.evaluate_scan(
        _scan([_finding("HIGH")]),
        acceptances=[_acceptance()],
        manifest_version="manifest-v1",
        today=dt.date(2026, 7, 30),
    )
    assert result["ok"] is True
    assert result["accepted_highs"][0]["finding_id"] == "CVE-2026-1"
    assert result["acceptance_manifest_version"] == "manifest-v1"

    with pytest.raises(control.ReleaseImageError, match="expired"):
        control.evaluate_scan(
            _scan([_finding("HIGH")]),
            acceptances=[_acceptance(expires_at="2026-07-30")],
            today=dt.date(2026, 7, 30),
        )
    with pytest.raises(control.ReleaseImageError, match="unaccepted HIGH"):
        control.evaluate_scan(
            _scan([_finding("HIGH")]),
            acceptances=[_acceptance(installed_version="different")],
            today=dt.date(2026, 7, 30),
        )


def test_scan_exact_digest_retries_only_transient_states():
    payloads = iter([_scan([], status="IN_PROGRESS"), _scan([])])
    sleeps = []
    result = control.scan_exact_image(
        digest=DIGEST,
        region=control.EXPECTED_REGION,
        repository=control.EXPECTED_REPOSITORY,
        aws_json=lambda arguments: next(payloads),
        attempts=2,
        interval_seconds=3,
        sleeper=sleeps.append,
    )
    assert result["ok"] is True
    assert sleeps == [3]


def test_release_context_rejects_short_sha_and_non_main_ref():
    with pytest.raises(control.ReleaseImageError, match="40-character"):
        control.validate_release_context("abc1234", control.EXPECTED_REF)
    with pytest.raises(control.ReleaseImageError, match="main"):
        control.validate_release_context(SHA, "refs/heads/feature")


def _trivy(findings, *, artifact_name=f"regmind-backend:{SHA}"):
    return {
        "SchemaVersion": 2,
        "ArtifactName": artifact_name,
        "ArtifactType": "container_image",
        "Results": [
            {
                "Target": "debian",
                "Class": "os-pkgs",
                "Type": "debian",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": item["finding_id"],
                        "PkgName": item["package_name"],
                        "InstalledVersion": item["installed_version"],
                        "FixedVersion": item.get("fixed_version", ""),
                        "Severity": item["severity"],
                        "PrimaryURL": item.get("uri", ""),
                    }
                    for item in findings
                ],
            },
            {
                "Target": "Python",
                "Class": "lang-pkgs",
                "Type": "pip",
                "Vulnerabilities": [],
            },
        ],
    }


def test_trivy_os_and_language_results_use_shared_acceptance_policy():
    clean = control.evaluate_trivy(_trivy([]), sha=SHA)
    assert clean["ok"] is True
    assert clean["targets"][0]["class"] == "os-pkgs"
    assert clean["targets"][1]["class"] == "lang-pkgs"
    assert clean["scanner"].endswith(
        "sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
    )

    critical = {
        "finding_id": "CVE-2026-1",
        "package_name": "glibc",
        "installed_version": "1",
        "severity": "CRITICAL",
    }
    with pytest.raises(control.ReleaseImageError, match="CRITICAL"):
        control.evaluate_trivy(_trivy([critical]), sha=SHA)

    high = dict(critical, severity="HIGH")
    with pytest.raises(control.ReleaseImageError, match="unaccepted HIGH"):
        control.evaluate_trivy(_trivy([high]), sha=SHA)
    accepted = control.evaluate_trivy(
        _trivy([high]),
        sha=SHA,
        acceptances=[_acceptance()],
        today=dt.date(2026, 7, 30),
    )
    assert accepted["accepted_highs"][0]["package_name"] == "glibc"


def test_trivy_invalid_json_shape_and_wrong_artifact_fail_closed():
    with pytest.raises(control.ReleaseImageError, match="no scan results"):
        control.evaluate_trivy({}, sha=SHA)
    with pytest.raises(control.ReleaseImageError, match="exact release image"):
        control.evaluate_trivy(
            _trivy([], artifact_name="regmind-backend:latest"),
            sha=SHA,
        )


def test_trivy_can_bind_scan_to_exact_ecr_digest():
    artifact = f"{control.EXPECTED_REPOSITORY_URI}@{DIGEST}"
    result = control.evaluate_trivy(
        _trivy([], artifact_name=artifact),
        sha=SHA,
        digest=DIGEST,
    )
    assert result["image_tag"] == SHA
    assert result["image_digest"] == DIGEST

    with pytest.raises(control.ReleaseImageError, match="exact release image"):
        control.evaluate_trivy(
            _trivy([], artifact_name=artifact),
            sha=SHA,
            digest="sha256:" + "c" * 64,
        )


def test_trivy_requires_os_and_python_package_coverage():
    python_only = _trivy([])
    python_only["Results"] = python_only["Results"][1:]
    with pytest.raises(control.ReleaseImageError, match="OS package coverage"):
        control.evaluate_trivy(python_only, sha=SHA)

    os_only = _trivy([])
    os_only["Results"] = os_only["Results"][:1]
    with pytest.raises(control.ReleaseImageError, match="Python package coverage"):
        control.evaluate_trivy(os_only, sha=SHA)


def test_trivy_accepts_pinned_python_pkg_schema():
    report = _trivy([])
    report["Results"][1]["Type"] = "python-pkg"
    result = control.evaluate_trivy(report, sha=SHA)
    assert result["targets"][1]["type"] == "python-pkg"


def test_trivy_policy_failure_writes_only_sanitized_failure_artifact(tmp_path):
    raw_report = tmp_path / "trivy-raw.json"
    output = tmp_path / "scan-evidence.json"
    report = _trivy(
        [
            {
                "finding_id": "CVE-2026-1",
                "package_name": "glibc",
                "installed_version": "1",
                "severity": "CRITICAL",
                "uri": "https://example.test/CVE-2026-1?token=raw-secret",
            }
        ]
    )
    report["Results"][0]["Vulnerabilities"][0][
        "Description"
    ] = "raw-secret-description"
    raw_report.write_text(json.dumps(report), encoding="utf-8")

    result = control.main(
        [
            "trivy",
            "--input",
            str(raw_report),
            "--sha",
            SHA,
            "--output",
            str(output),
        ]
    )

    assert result == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["ok"] is False
    assert evidence["schema_version"] == "container_scan_failure_v1"
    serialized = json.dumps(evidence)
    assert "raw-secret" not in serialized
    assert "Description" not in serialized
