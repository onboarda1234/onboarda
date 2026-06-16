import argparse
import json

from scripts.qa import staging_release_evidence as evidence


def test_release_evidence_collector_writes_core_files(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKOFFICE_TOKEN", "redacted-test-token")

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
                "git_sha": "abc123456789",
                "git_sha_short": "abc1234",
                "image_tag": "abc123456789",
                "build_time": "2026-06-16T12:00:00Z",
                "environment": "staging",
                "service": "regmind-backend",
            }
        raise AssertionError(path)

    def fake_runtime_baseline(**kwargs):
        assert kwargs["expected_sha"] == "abc123456789"
        return {
            "alignment": {
                "aligned": True,
                "backend_image_tag": "abc123456789",
                "backend_env_git_sha": "abc123456789",
            }
        }

    monkeypatch.setattr(evidence, "_request_json", fake_request_json)
    monkeypatch.setattr(evidence.staging_runtime_baseline, "collect_runtime_baseline", fake_runtime_baseline)

    args = argparse.Namespace(
        api_base="https://example.test/api",
        token_env="BACKOFFICE_TOKEN",
        evidence_dir=str(tmp_path),
        expected_sha="abc123456789",
        region="af-south-1",
        cluster="regmind-staging",
        backend_service="regmind-backend",
        worker_service="regmind-verification-worker",
        skip_aws=False,
        strict=True,
        run_api_smoke=False,
        expected_total=None,
        expected_pending=None,
        expected_edd=None,
        show_fixtures=False,
        skip_applications=False,
    )

    summary = evidence.collect_release_evidence(args)

    assert summary["ok"] is True
    for filename in ("health.json", "liveness.json", "version.json", "runtime_baseline.json", "summary.json"):
        assert (tmp_path / filename).exists()
    version = json.loads((tmp_path / "version.json").read_text())
    assert version["git_sha"] == "abc123456789"
    assert summary["version"]["git_sha_matches_expected"] is True
    assert summary["version"]["image_tag_matches_expected"] is True


def test_release_evidence_collector_can_capture_day5_smoke(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKOFFICE_TOKEN", "redacted-test-token")
    monkeypatch.setattr(
        evidence,
        "_request_json",
        lambda api_base, path, *, token=None: {
            "/health": {"status": "ok"},
            "/liveness": {"status": "ok"},
            "/version": {"git_sha": "abc123456789", "image_tag": "abc123456789"},
        }[path],
    )
    monkeypatch.setattr(evidence.staging_runtime_baseline, "collect_runtime_baseline", lambda **kwargs: {"alignment": {"aligned": True}})
    monkeypatch.setattr(evidence.day5_closing_smoke, "run_smoke", lambda smoke_args: {"git_sha": "abc123456789", "summary": {"total": 22}})

    args = argparse.Namespace(
        api_base="https://example.test/api",
        token_env="BACKOFFICE_TOKEN",
        evidence_dir=str(tmp_path),
        expected_sha="abc123456789",
        region="af-south-1",
        cluster="regmind-staging",
        backend_service="regmind-backend",
        worker_service="regmind-verification-worker",
        skip_aws=False,
        strict=True,
        run_api_smoke=True,
        expected_total=22,
        expected_pending=21,
        expected_edd=1,
        show_fixtures=False,
        skip_applications=False,
    )

    summary = evidence.collect_release_evidence(args)

    assert summary["ok"] is True
    api_smoke = json.loads((tmp_path / "api_smoke.json").read_text())
    assert api_smoke["ok"] is True
    assert api_smoke["results"]["summary"]["total"] == 22
