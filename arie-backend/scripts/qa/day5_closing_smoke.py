#!/usr/bin/env python3
"""Day 1-5 staging closing smoke checks.

This script turns the manual Day 4/5 trust-truthfulness gates into a repeatable
probe. It intentionally uses only the Python standard library so it can run from
a laptop, CI job, or one-off operations shell without installing test tooling.

Example:
    BACKOFFICE_TOKEN=... python scripts/qa/day5_closing_smoke.py \
        --api-base https://staging.regmind.co/api \
        --expected-sha 05665c7 \
        --expected-total 22 --expected-pending 21 --expected-edd 1
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_FIELDS = (
    "ref,company_name,status,risk_level,risk_score,sector,country,entity_type,"
    "created_at,assigned_to,director_count,ubo_count,document_count"
)


class SmokeFailure(AssertionError):
    """Raised when a smoke gate fails."""


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def _normalize_api_base(api_base: str) -> str:
    base = str(api_base or "").strip().rstrip("/")
    if not base:
        raise SmokeFailure("--api-base is required")
    return base


def _api_url(api_base: str, path: str) -> str:
    base = _normalize_api_base(api_base)
    suffix = path if path.startswith("/") else f"/{path}"
    return base + suffix


def _query(params: dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "", False)}
    return urllib.parse.urlencode(clean)


def _headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(api_base: str, path: str, token: str | None = None, accept: str | None = None) -> HttpResponse:
    headers = _headers(token)
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(_api_url(api_base, path), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return HttpResponse(
                status=resp.status,
                headers={k: v for k, v in resp.headers.items()},
                body=resp.read(),
            )
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise SmokeFailure(f"{path} returned HTTP {exc.code}: {body[:300]!r}") from exc
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"{path} failed: {exc}") from exc


def _json(resp: HttpResponse, path: str) -> dict[str, Any]:
    if resp.status != 200:
        raise SmokeFailure(f"{path} returned HTTP {resp.status}")
    try:
        payload = json.loads(resp.body.decode("utf-8"))
    except Exception as exc:
        raise SmokeFailure(f"{path} did not return JSON") from exc
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{path} returned non-object JSON")
    return payload


def _csv_rows(body: bytes) -> list[list[str]]:
    text = body.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def _extract_applications(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("applications", "data", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    raise SmokeFailure("/applications response did not expose an applications/data/items/results list")


def _status_key(app: dict[str, Any]) -> str:
    raw = app.get("statusRaw") or app.get("status") or ""
    return str(raw).lower().replace("-", "_").replace(" ", "_")


def _assert_equal(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise SmokeFailure(f"{label}: expected {expected!r}, got {actual!r}")


def _check_reconciliation(summary: dict[str, Any]) -> int:
    required = ("total", "pending", "edd_required", "approved", "rejected", "withdrawn")
    missing = [key for key in required if key not in summary]
    if missing:
        raise SmokeFailure(f"analytics summary missing keys: {missing}")
    classified = sum(int(summary.get(key) or 0) for key in required if key != "total")
    total = int(summary.get("total") or 0)
    _assert_equal("analytics classified total", classified, total)
    return classified


def _check_csv_export(api_base: str, token: str | None, show_fixtures: bool) -> dict[str, Any]:
    params = {"format": "csv", "fields": DEFAULT_FIELDS, "show_fixtures": "true" if show_fixtures else None}
    path = "/reports/generate?" + _query(params)
    resp = _request(api_base, path, token=token, accept="text/csv")
    rows = _csv_rows(resp.body)
    if not rows:
        raise SmokeFailure("CSV export returned no rows")
    record_count = resp.headers.get("X-Report-Record-Count")
    if record_count is None:
        raise SmokeFailure("CSV export missing X-Report-Record-Count")
    _assert_equal("CSV header record count", int(record_count), len(rows) - 1)
    canonical = resp.headers.get("X-Report-Canonical-View")
    if canonical:
        _assert_equal("CSV canonical view", canonical, "applications_report_v1")
    field_header = resp.headers.get("X-Report-Field-List")
    if field_header:
        _assert_equal("CSV field header", field_header, DEFAULT_FIELDS)
    return {"rows": len(rows) - 1, "canonical_view": canonical or "", "field_list": field_header or ""}


def _check_kpi_data(
    api_base: str,
    token: str | None,
    pending_statuses: list[str],
    edd_statuses: list[str],
    expected_pending: int,
    expected_edd: int,
    show_fixtures: bool,
) -> dict[str, int]:
    params = {"limit": 5000, "show_fixtures": "true" if show_fixtures else None}
    payload = _json(_request(api_base, "/applications?" + _query(params), token=token), "/applications")
    apps = _extract_applications(payload)
    pending_set = {str(status) for status in pending_statuses}
    edd_set = {str(status) for status in edd_statuses}
    pending_count = sum(1 for app in apps if _status_key(app) in pending_set)
    edd_count = sum(1 for app in apps if _status_key(app) in edd_set)
    _assert_equal("applications-derived pending count", pending_count, expected_pending)
    _assert_equal("applications-derived EDD routed count", edd_count, expected_edd)
    return {"pending": pending_count, "edd": edd_count}


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    token = args.token or os.environ.get(args.token_env)
    api_base = _normalize_api_base(args.api_base)
    suffix = "?show_fixtures=true" if args.show_fixtures else ""

    results: dict[str, Any] = {"api_base": api_base}

    if args.expected_sha:
        version = _json(_request(api_base, "/version", token=token), "/version")
        sha = str(version.get("git_sha") or version.get("sha") or "")
        if not sha.startswith(args.expected_sha):
            raise SmokeFailure(f"version git_sha {sha!r} does not start with {args.expected_sha!r}")
        results["git_sha"] = sha

    analytics = _json(_request(api_base, "/reports/analytics" + suffix, token=token), "/reports/analytics")
    summary = analytics.get("summary") or {}
    classified = _check_reconciliation(summary)
    report_meta = analytics.get("report") or {}
    pending_statuses = list(report_meta.get("pending_statuses") or [])
    if not pending_statuses:
        raise SmokeFailure("analytics report.pending_statuses is empty")
    edd_statuses = list(report_meta.get("edd_routed_statuses") or ["edd_required"])

    dashboard = _json(_request(api_base, "/dashboard" + suffix, token=token), "/dashboard")
    expected_pending = int(summary.get("pending") or 0)
    expected_edd = int(summary.get("edd_required") or 0)
    _assert_equal("dashboard early_stage_applications", dashboard.get("early_stage_applications"), expected_pending)
    _assert_equal("dashboard in_progress_applications", dashboard.get("in_progress_applications"), expected_pending)
    _assert_equal("dashboard edd", dashboard.get("edd"), expected_edd)
    if dashboard.get("pending_statuses"):
        _assert_equal("dashboard pending_statuses", dashboard["pending_statuses"], pending_statuses)
    if dashboard.get("canonical_view"):
        _assert_equal("dashboard canonical_view", dashboard["canonical_view"], "applications_report_v1")

    if args.expected_total is not None:
        _assert_equal("analytics total", int(summary.get("total") or 0), args.expected_total)
    if args.expected_pending is not None:
        _assert_equal("analytics pending", expected_pending, args.expected_pending)
    if args.expected_edd is not None:
        _assert_equal("analytics edd_required", expected_edd, args.expected_edd)

    csv_result = _check_csv_export(api_base, token, args.show_fixtures)
    kpi_result = None
    if not args.skip_applications:
        kpi_result = _check_kpi_data(
            api_base,
            token,
            pending_statuses,
            edd_statuses,
            expected_pending,
            expected_edd,
            args.show_fixtures,
        )

    results.update({
        "summary": summary,
        "classified_total": classified,
        "pending_statuses": pending_statuses,
        "edd_routed_statuses": edd_statuses,
        "dashboard": {
            "early_stage_applications": dashboard.get("early_stage_applications"),
            "in_progress_applications": dashboard.get("in_progress_applications"),
            "edd": dashboard.get("edd"),
        },
        "csv": csv_result,
        "applications_kpi_data": kpi_result,
    })
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Day 1-5 staging closing smoke checks.")
    parser.add_argument("--api-base", required=True, help="API base URL, e.g. https://staging.regmind.co/api")
    parser.add_argument("--token", default="", help="Bearer token. Prefer --token-env for shell history safety.")
    parser.add_argument("--token-env", default="BACKOFFICE_TOKEN", help="Environment variable containing bearer token.")
    parser.add_argument("--expected-sha", default="", help="Expected /api/version git_sha prefix.")
    parser.add_argument("--expected-total", type=int, default=None)
    parser.add_argument("--expected-pending", type=int, default=None)
    parser.add_argument("--expected-edd", type=int, default=None)
    parser.add_argument("--show-fixtures", action="store_true", help="Pass show_fixtures=true to supported endpoints.")
    parser.add_argument("--skip-applications", action="store_true", help="Skip /applications-derived KPI data checks.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        results = run_smoke(args)
    except SmokeFailure as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "results": results}, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
