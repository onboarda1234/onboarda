import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-staging.yml"
RUNBOOK = ROOT / "docs" / "DEPLOYMENT_RUNBOOK.md"


def _text():
    return WORKFLOW.read_text(encoding="utf-8")


def _steps():
    return re.findall(r"^      - name: (.+)$", _text(), flags=re.MULTILINE)


def _names():
    return _steps()


def test_workflow_is_main_full_sha_and_least_privilege_scoped():
    text = _text()
    assert "\npermissions:\n  contents: read\n" in text
    assert 'ref != "refs/heads/main"' in text
    assert r"[0-9a-f]{40}" in text
    assert "git rev-parse HEAD" in text


def test_immutable_reuse_and_scan_gate_precede_every_ecs_mutation():
    names = _names()
    prepare = names.index("Prepare or safely reuse exact SHA image")
    scan = names.index("Gate exact image vulnerability findings")
    comprehensive_scan = names.index("Gate exact image OS and Python findings")
    first_mutation = names.index(
        "Register new task definition with SHA-pinned image"
    )
    assert prepare < scan < comprehensive_scan < first_mutation
    text = _text()
    assert "release_image_control.py prepare" in text
    assert "release_image_control.py scan" in text
    comprehensive_block = text.split(
        "Gate exact image OS and Python findings",
        1,
    )[1].split("\n      - name:", 1)[0]
    assert "aquasec/trivy:0.72.0@" in text
    assert '--digest "$RELEASE_DIGEST"' in comprehensive_block
    assert '"$RELEASE_DIGEST_URI"' in comprehensive_block
    assert "--scanners vuln" in comprehensive_block
    assert "--severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL" in comprehensive_block
    assert "release_image_control.py trivy" in comprehensive_block
    assert "batch-delete-image" not in text
    assert "docker tag" not in text
    assert ":latest" not in text


def test_task_definitions_come_from_live_services_and_rollback_is_paired():
    text = _text()
    assert "capture-predeploy" in text
    assert "backend_source_task_definition" in text
    assert "worker_source_task_definition" in text
    assert "--task-definition $ECS_TASK_FAMILY" not in text
    assert "--task-definition $ECS_WORKER_TASK_FAMILY" not in text
    assert "predeploy.json" in text


def test_runtime_gate_covers_digest_counts_alb_and_cloudwatch():
    text = _text()
    assert "verify-runtime" in text
    assert '--digest "${{ steps.image.outputs.image_digest }}"' in text
    assert "collect-cloudwatch" in text
    assert "runtime.json" in text
    assert "cloudwatch.json" in text


def test_authenticated_version_evidence_is_mandatory_and_ephemeral():
    text = _text()
    assert "Collect mandatory authenticated staging release evidence" in text
    assert "aws secretsmanager get-secret-value" in text
    assert '"exp": now + 900' in text
    assert "echo \"::add-mask::$SMOKE_TOKEN\"" in text
    assert 'BACKOFFICE_TOKEN="$SMOKE_TOKEN"' in text
    assert "--expected-sha \"$RELEASE_SHA\"" in text
    assert "--expected-environment staging" in text
    assert "--strict" in text
    assert '>> "$GITHUB_ENV"' not in text
    assert "--token \"$SMOKE_TOKEN\"" not in text
    assert "optional" not in text.lower()
    assert "skipping runtime /api/version" not in text


def test_sanitized_evidence_is_uploaded_and_completion_is_explicit():
    text = _text()
    assert "staging_deployment_control.py finalize" in text
    assert "--trivy-scan-evidence" in text
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in text
    assert "RELEASE_EVIDENCE_DIR: ${{ github.workspace }}/release-evidence" in text
    assert "path: release-evidence/" in text
    assert "/.release-evidence" not in text
    assert "if-no-files-found: error" in text
    assert "id: upload-release-evidence" in text
    assert (
        "EVIDENCE_UPLOAD_OUTCOME: ${{ steps.upload-release-evidence.outcome }}"
        in text
    )
    assert '[ "$EVIDENCE_UPLOAD_OUTCOME" = "success" ]' in text
    assert '"status":"complete"' in text
    assert "deployment is not releasable" in text


def test_no_remote_convenience_tag_or_mutability_downgrade_path():
    text = _text()
    forbidden = (
        "image-tag-mutability MUTABLE",
        "batch-delete-image",
        "put-image",
        "docker push ${",
    )
    for value in forbidden:
        assert value not in text
    assert not re.search(
        r"regmind-backend:(latest|main|staging)(?:\\s|['\"]|$)",
        text,
    )


def test_runbook_has_no_convenience_tag_or_ungated_manual_release_path():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "docker tag regmind-backend:latest" not in text
    assert "docker build --platform linux/amd64 -t regmind-backend ." not in text
    assert "docker push 782913119880" not in text
    assert "raw `docker push`" in text
    assert "approved staging release path" in text
    assert '--build-arg "GIT_SHA=$GIT_SHA"' in text
    assert '--build-arg "IMAGE_TAG=$GIT_SHA"' in text
    assert '--label "git.sha=$GIT_SHA"' in text


def test_runbook_documents_mandatory_ephemeral_machine_auth():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "`AWS_ACCESS_KEY_ID`" in text
    assert "`AWS_SECRET_ACCESS_KEY`" in text
    assert "`secretsmanager:GetSecretValue`" in text
    assert "secret ID `regmind/staging`" in text
    assert "extracts only `JWT_SECRET`" in text
    assert "15-minute token" in text
    assert "`STAGING_VERSION_BEARER_TOKEN` is required or stored" in text
    assert text.count("--expected-environment staging") >= 2
