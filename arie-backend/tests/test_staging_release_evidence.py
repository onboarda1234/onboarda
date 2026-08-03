import argparse
import io
import json
import urllib.error

import pytest

from scripts.qa import staging_release_evidence as evidence


EXPECTED_SHA = "a" * 40
TOKEN_ENV = "BACKOFFICE_TOKEN"


def _args(tmp_path, **overrides):
    values = {
        "api_base": "https://example.test/api",
        "token_env": TOKEN_ENV,
        "evidence_dir": str(tmp_path),
        "expected_sha": EXPECTED_SHA,
        "expected_environment": "staging",
        "region": "af-south-1",
        "cluster": "regmind-staging",
        "backend_service": "regmind-backend",
        "worker_service": "regmind-verification-worker",
        "skip_aws": True,
        "strict": True,
        "run_api_smoke": False,
        "expected_total": None,
        "expected_pending": None,
        "expected_edd": None,
        "show_fixtures": False,
        "skip_applications": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _stub_core_requests(monkeypatch, version):
    def fake_request_json(api_base, path, *, token=None):
        assert api_base == "https://example.test/api"
        if path == "/health":
            assert token is None
            return {"status": "ok"}
        if path == "/liveness":
            assert token is None
            return {"status": "ok", "service": "regmind-backend"}
        if path == "/version":
            assert token == "redacted-test-token"
            return dict(version)
        raise AssertionError(path)

    monkeypatch.setattr(evidence, "_request_json", fake_request_json)


def test_release_evidence_collector_writes_core_files(monkeypatch, tmp_path):
    monkeypatch.setenv(TOKEN_ENV, "redacted-test-token")

    def fake_request_json(api_base, path, *, token=None):
        assert api_base == "https://example.test/api"
        if path == "/health":
            assert token is None
            return {"status": "ok"}
        if path == "/liveness":
            assert token is None
            return {"status": "ok", "service": "regmind-backend"}
        if path == "/version":
            assert token == "redacted-test-token"
            return {
                "git_sha": EXPECTED_SHA,
                "git_sha_short": EXPECTED_SHA[:7],
                "image_tag": EXPECTED_SHA,
                "build_time": "2026-06-16T12:00:00Z",
                "environment": "staging",
                "service": "regmind-backend",
            }
        raise AssertionError(path)

    def fake_runtime_baseline(**kwargs):
        assert kwargs["expected_sha"] == EXPECTED_SHA
        return {
            "alignment": {
                "aligned": True,
                "backend_image_tag": EXPECTED_SHA,
                "backend_env_git_sha": EXPECTED_SHA,
            }
        }

    monkeypatch.setattr(evidence, "_request_json", fake_request_json)
    monkeypatch.setattr(evidence.staging_runtime_baseline, "collect_runtime_baseline", fake_runtime_baseline)

    args = _args(tmp_path, skip_aws=False)

    summary = evidence.collect_release_evidence(args)

    assert summary["ok"] is True
    for filename in ("health.json", "liveness.json", "version.json", "runtime_baseline.json", "summary.json"):
        assert (tmp_path / filename).exists()
    version = json.loads((tmp_path / "version.json").read_text())
    persisted_summary = json.loads((tmp_path / "summary.json").read_text())
    assert version["git_sha"] == EXPECTED_SHA
    assert persisted_summary == summary
    assert persisted_summary["files"]["summary"] == str(
        tmp_path / "summary.json"
    )
    assert summary["version"]["git_sha_matches_expected"] is True
    assert summary["version"]["image_tag_matches_expected"] is True
    assert summary["version"]["environment_matches_expected"] is True


def test_release_evidence_collector_can_capture_day5_smoke(monkeypatch, tmp_path):
    monkeypatch.setenv(TOKEN_ENV, "redacted-test-token")
    monkeypatch.setattr(
        evidence,
        "_request_json",
        lambda api_base, path, *, token=None: {
            "/health": {"status": "ok"},
            "/liveness": {"status": "ok"},
            "/version": {
                "git_sha": EXPECTED_SHA,
                "image_tag": EXPECTED_SHA,
                "environment": "staging",
            },
        }[path],
    )
    monkeypatch.setattr(evidence.staging_runtime_baseline, "collect_runtime_baseline", lambda **kwargs: {"alignment": {"aligned": True}})
    monkeypatch.setattr(
        evidence.day5_closing_smoke,
        "run_smoke",
        lambda smoke_args: {"git_sha": EXPECTED_SHA, "summary": {"total": 22}},
    )
    args = _args(
        tmp_path,
        skip_aws=False,
        run_api_smoke=True,
        expected_total=22,
        expected_pending=21,
        expected_edd=1,
    )

    summary = evidence.collect_release_evidence(args)

    assert summary["ok"] is True
    api_smoke = json.loads((tmp_path / "api_smoke.json").read_text())
    assert api_smoke["ok"] is True
    assert api_smoke["results"]["summary"]["total"] == 22


@pytest.mark.parametrize(
    "expected_sha",
    ("", "abc1234", "g" * 40, "A" * 40),
)
def test_strict_mode_requires_full_lowercase_git_sha(monkeypatch, tmp_path, expected_sha):
    monkeypatch.setenv(TOKEN_ENV, "redacted-test-token")
    with pytest.raises(evidence.EvidenceFailure, match="full 40-character lowercase hexadecimal"):
        evidence.collect_release_evidence(_args(tmp_path, expected_sha=expected_sha))


def test_strict_mode_requires_expected_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(TOKEN_ENV, "redacted-test-token")
    with pytest.raises(evidence.EvidenceFailure, match="expected-environment"):
        evidence.collect_release_evidence(_args(tmp_path, expected_environment=""))


def test_missing_token_fails_before_any_request(monkeypatch, tmp_path):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    monkeypatch.setattr(
        evidence,
        "_request_json",
        lambda *args, **kwargs: pytest.fail("network must not be called without a token"),
    )

    with pytest.raises(evidence.EvidenceFailure, match=f"{TOKEN_ENV} is required"):
        evidence.collect_release_evidence(_args(tmp_path))


def test_authenticated_http_error_does_not_include_response_body(monkeypatch):
    reflected_secret = "reflected-auth-material-must-not-appear"
    http_error = urllib.error.HTTPError(
        "https://example.test/api/version",
        401,
        "Unauthorized",
        {},
        io.BytesIO(reflected_secret.encode()),
    )

    def fail_request(*args, **kwargs):
        raise http_error

    monkeypatch.setattr(evidence.urllib.request, "urlopen", fail_request)
    with pytest.raises(evidence.EvidenceFailure, match="/version returned HTTP 401") as exc_info:
        evidence._request_json(
            "https://example.test/api",
            "/version",
            token=reflected_secret,
        )
    assert reflected_secret not in str(exc_info.value)


@pytest.mark.parametrize(
    "version",
    (
        {"image_tag": EXPECTED_SHA, "environment": "staging"},
        {"git_sha": "unknown", "image_tag": EXPECTED_SHA, "environment": "staging"},
        {"git_sha": EXPECTED_SHA + "0", "image_tag": EXPECTED_SHA, "environment": "staging"},
        {"git_sha": EXPECTED_SHA, "environment": "staging"},
        {"git_sha": EXPECTED_SHA, "image_tag": "unknown", "environment": "staging"},
        {"git_sha": EXPECTED_SHA, "image_tag": "b" * 40, "environment": "staging"},
        {"git_sha": EXPECTED_SHA, "image_tag": EXPECTED_SHA},
        {"git_sha": EXPECTED_SHA, "image_tag": EXPECTED_SHA, "environment": "unknown"},
        {"git_sha": EXPECTED_SHA, "image_tag": EXPECTED_SHA, "environment": "production"},
    ),
)
def test_strict_version_evidence_fails_closed_on_missing_unknown_or_mismatch(
    monkeypatch,
    tmp_path,
    version,
):
    monkeypatch.setenv(TOKEN_ENV, "redacted-test-token")
    _stub_core_requests(monkeypatch, version)

    summary = evidence.collect_release_evidence(_args(tmp_path))

    assert summary["ok"] is False


def test_version_evidence_is_allowlisted_and_token_never_persisted_or_printed(
    monkeypatch,
    tmp_path,
    capsys,
):
    token = "release-token-sentinel-must-not-leak"
    monkeypatch.setenv(TOKEN_ENV, token)

    def fake_request_json(api_base, path, *, token=None):
        if path == "/health":
            return {"status": "ok"}
        if path == "/liveness":
            return {"status": "ok"}
        assert path == "/version"
        assert token == "release-token-sentinel-must-not-leak"
        return {
            "git_sha": EXPECTED_SHA,
            "git_sha_short": EXPECTED_SHA[:7],
            "image_tag": EXPECTED_SHA,
            "build_time": "2026-07-30T00:00:00Z",
            "environment": "staging",
            "service": "regmind-backend",
            "unexpected_secret": token,
            "provider_status": {"authorization": token},
        }

    monkeypatch.setattr(evidence, "_request_json", fake_request_json)
    result = evidence.main(
        [
            "--api-base",
            "https://example.test/api",
            "--token-env",
            TOKEN_ENV,
            "--evidence-dir",
            str(tmp_path),
            "--expected-sha",
            EXPECTED_SHA,
            "--expected-environment",
            "staging",
            "--skip-aws",
            "--strict",
        ]
    )

    assert result == 0
    output = capsys.readouterr()
    assert token not in output.out
    assert token not in output.err
    for path in tmp_path.iterdir():
        assert token not in path.read_text(encoding="utf-8")
    version = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
    assert set(version) == set(evidence.VERSION_EVIDENCE_FIELDS)
