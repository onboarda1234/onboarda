from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "container-security.yml"
TRIVY_DIGEST = (
    "sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
)
def _text():
    return WORKFLOW.read_text(encoding="utf-8")


def test_container_security_runs_for_pr_and_push_with_read_only_permission():
    text = _text()
    assert "\non:\n  pull_request:\n  push:\n" in text
    assert "\npermissions:\n  contents: read\n" in text
    assert "\n  exact-sha-container-scan:\n" in text


def test_container_build_uses_exact_checkout_sha_and_linux_amd64():
    text = _text()
    assert 'RELEASE_SHA="$(git rev-parse HEAD)"' in text
    assert '[0-9a-f]{40}' in text
    assert 'if [ "$RELEASE_SHA" != "${{ github.sha }}" ]; then' in text
    assert "docker build --platform linux/amd64" in text
    assert '--build-arg "GIT_SHA=$RELEASE_SHA"' in text
    assert '--build-arg "IMAGE_TAG=$RELEASE_SHA"' in text
    assert '-t "regmind-backend:$RELEASE_SHA"' in text
    assert "git.sha=$RELEASE_SHA" in text


def test_trivy_is_digest_pinned_and_scans_every_severity_without_ignore():
    text = _text()
    assert "aquasec/trivy:0.72.0@" + TRIVY_DIGEST in text
    assert "--scanners vuln" in text
    assert "--severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL" in text
    assert "--format json" in text
    assert "--ignore-unfixed" not in text
    assert "--ignorefile" not in text
    assert "--exit-code 0" not in text


def test_scanner_failure_and_invalid_json_cannot_pass():
    text = _text()
    assert "Trivy scanner or vulnerability database unavailable" in text
    assert '[ ! -s "$SCAN_DIR/trivy-raw.json" ]' in text
    assert "Trivy scan output is missing" in text
    assert "release_image_control.py trivy" in text
    assert "set -euo pipefail" in text


def test_only_sanitized_summary_is_uploaded():
    text = _text()
    assert "SCAN_DIR: ${{ github.workspace }}/container-security-evidence" in text
    assert "path: container-security-evidence/scan-evidence.json" in text
    assert "/.container-security" not in text
    upload_block = text.split("Upload sanitized container scan evidence", 1)[1]
    assert "trivy-raw.json" not in upload_block
    assert "image-config.json" not in upload_block
    assert "if-no-files-found: error" in upload_block
