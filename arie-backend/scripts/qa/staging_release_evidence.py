#!/usr/bin/env python3
"""Collect staging release evidence for remediation closure.

The collector is read-only. It writes:
  - health.json
  - liveness.json
  - version.json
  - runtime_baseline.json or runtime_baseline_error.json
  - api_smoke.json when --run-api-smoke is used
  - summary.json

Bearer tokens are read from an environment variable only and are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
for import_path in (SCRIPT_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import staging_runtime_baseline  # noqa: E402
import day5_closing_smoke  # noqa: E402


class EvidenceFailure(AssertionError):
    """Raised when required release evidence cannot be collected."""


def _api_url(api_base: str, path: str) -> str:
    base = str(api_base or "").strip().rstrip("/")
    if not base:
        raise EvidenceFailure("--api-base is required")
    suffix = path if path.startswith("/") else f"/{path}"
    return base + suffix


def _headers(token: str | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request_json(api_base: str, path: str, *, token: str | None = None) -> dict[str, Any]:
    req = urllib.request.Request(_api_url(api_base, path), headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            body = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise EvidenceFailure(f"{path} returned HTTP {exc.code}: {body[:300]!r}") from exc
    except urllib.error.URLError as exc:
        raise EvidenceFailure(f"{path} failed: {exc}") from exc

    if status != 200:
        raise EvidenceFailure(f"{path} returned HTTP {status}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise EvidenceFailure(f"{path} did not return JSON") from exc
    if not isinstance(payload, dict):
        raise EvidenceFailure(f"{path} returned non-object JSON")
    return payload


def _write_json(evidence_dir: Path, filename: str, payload: dict[str, Any]) -> str:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / filename
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return str(path)


def _sha_matches(value: Any, expected_sha: str) -> bool:
    expected_sha = str(expected_sha or "").strip()
    if not expected_sha:
        return True
    value_text = str(value or "").strip()
    return value_text == expected_sha or value_text.startswith(expected_sha)


def _token_from_env(token_env: str) -> str:
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise EvidenceFailure(f"{token_env} is required for authenticated /api/version evidence")
    return token


def _run_api_smoke(args: argparse.Namespace, token: str) -> dict[str, Any]:
    smoke_args = argparse.Namespace(
        api_base=args.api_base,
        token="",
        token_env=args.token_env,
        expected_sha=args.expected_sha,
        expected_total=args.expected_total,
        expected_pending=args.expected_pending,
        expected_edd=args.expected_edd,
        show_fixtures=args.show_fixtures,
        skip_applications=args.skip_applications,
    )
    os.environ[args.token_env] = token
    return day5_closing_smoke.run_smoke(smoke_args)


def collect_release_evidence(args: argparse.Namespace) -> dict[str, Any]:
    evidence_dir = Path(args.evidence_dir).expanduser().resolve()
    token = _token_from_env(args.token_env)
    summary: dict[str, Any] = {
        "ok": True,
        "api_base": args.api_base,
        "evidence_dir": str(evidence_dir),
        "expected_sha": args.expected_sha,
        "files": {},
    }

    health = _request_json(args.api_base, "/health")
    summary["files"]["health"] = _write_json(evidence_dir, "health.json", health)

    liveness = _request_json(args.api_base, "/liveness")
    summary["files"]["liveness"] = _write_json(evidence_dir, "liveness.json", liveness)

    version = _request_json(args.api_base, "/version", token=token)
    summary["files"]["version"] = _write_json(evidence_dir, "version.json", version)
    summary["version"] = {
        "git_sha": version.get("git_sha"),
        "image_tag": version.get("image_tag"),
        "git_sha_matches_expected": _sha_matches(version.get("git_sha"), args.expected_sha),
        "image_tag_matches_expected": _sha_matches(version.get("image_tag"), args.expected_sha),
    }
    if args.expected_sha and not (
        summary["version"]["git_sha_matches_expected"]
        and summary["version"]["image_tag_matches_expected"]
    ):
        summary["ok"] = False

    if not args.skip_aws:
        try:
            runtime = staging_runtime_baseline.collect_runtime_baseline(
                region=args.region,
                cluster=args.cluster,
                backend_service=args.backend_service,
                worker_service=args.worker_service,
                expected_sha=args.expected_sha or None,
            )
            summary["files"]["runtime_baseline"] = _write_json(evidence_dir, "runtime_baseline.json", runtime)
            summary["runtime_alignment"] = runtime.get("alignment", {})
            if args.strict and not runtime.get("alignment", {}).get("aligned"):
                summary["ok"] = False
        except Exception as exc:
            error_payload = {"ok": False, "error": str(exc)}
            summary["files"]["runtime_baseline_error"] = _write_json(
                evidence_dir,
                "runtime_baseline_error.json",
                error_payload,
            )
            summary["runtime_alignment"] = error_payload
            if args.strict:
                summary["ok"] = False

    if args.run_api_smoke:
        try:
            api_smoke = _run_api_smoke(args, token)
            summary["files"]["api_smoke"] = _write_json(evidence_dir, "api_smoke.json", {"ok": True, "results": api_smoke})
        except Exception as exc:
            summary["ok"] = False
            summary["files"]["api_smoke"] = _write_json(
                evidence_dir,
                "api_smoke.json",
                {"ok": False, "error": str(exc)},
            )

    summary["files"]["summary"] = _write_json(evidence_dir, "summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="https://staging.regmind.co/api")
    parser.add_argument("--token-env", default="BACKOFFICE_TOKEN")
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--region", default="af-south-1")
    parser.add_argument("--cluster", default="regmind-staging")
    parser.add_argument("--backend-service", default="regmind-backend")
    parser.add_argument("--worker-service", default="regmind-verification-worker")
    parser.add_argument("--skip-aws", action="store_true")
    parser.add_argument("--strict", action="store_true", help="exit non-zero when expected SHA/runtime alignment fails")
    parser.add_argument("--run-api-smoke", action="store_true")
    parser.add_argument("--expected-total", type=int, default=None)
    parser.add_argument("--expected-pending", type=int, default=None)
    parser.add_argument("--expected-edd", type=int, default=None)
    parser.add_argument("--show-fixtures", action="store_true")
    parser.add_argument("--skip-applications", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = collect_release_evidence(args)
    except EvidenceFailure as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0 if summary.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
