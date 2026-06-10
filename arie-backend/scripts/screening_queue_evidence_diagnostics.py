#!/usr/bin/env python3
"""Aggregate safe Screening Queue evidence quality diagnostics.

The script accepts either an exported /api/screening/queue JSON payload or an
API base URL plus bearer token environment variable. It never prints secrets or
raw provider payloads.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import Counter
from typing import Any


def _payload_rows(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    rows = data.get("rows") if isinstance(data, dict) else None
    pagination = data.get("pagination") if isinstance(data, dict) else None
    return (rows if isinstance(rows, list) else []), (pagination if isinstance(pagination, dict) else {})


def _read_input(path: str) -> list[dict[str, Any]]:
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    payload = json.loads(raw)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    rows, _ = _payload_rows(payload if isinstance(payload, dict) else {})
    return rows


def _fetch_api(base_url: str, token: str, *, limit: int) -> list[dict[str, Any]]:
    base = base_url.rstrip("/")
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        url = f"{base}/api/screening/queue?limit={limit}&offset={offset}"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        payload = json.loads(urllib.request.urlopen(request, timeout=60).read())
        batch, pagination = _payload_rows(payload)
        rows.extend(batch)
        if not pagination.get("has_next"):
            break
        offset += limit
    return rows


def _norm(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_norm(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value).strip()


def _category(row: dict[str, Any]) -> list[str]:
    evidence = row.get("screening_evidence") if isinstance(row.get("screening_evidence"), dict) else {}
    diagnostics = (evidence.get("technical_details") or {}).get("diagnostics") or {}
    categories = diagnostics.get("category") if isinstance(diagnostics, dict) else []
    result = []
    for value in categories or []:
        text = _norm(value).lower()
        if "adverse" in text or "media" in text:
            result.append("adverse_media")
        elif "pep" in text or "political" in text:
            result.append("pep")
        elif "sanction" in text or "watchlist" in text:
            result.append("sanctions_watchlist")
        elif text:
            result.append("other")
    return sorted(set(result)) or ["none"]


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "total_rows": len(rows),
        "evidence_quality": Counter(),
        "provider": Counter(),
        "category": Counter(),
        "subject_type": Counter(),
        "failure_reason": Counter(),
        "missing_identifier_type": Counter(),
        "field_presence_rows": Counter(),
        "examples": {},
    }
    for row in rows:
        evidence = row.get("screening_evidence") if isinstance(row.get("screening_evidence"), dict) else {}
        summary = row.get("evidence_summary") if isinstance(row.get("evidence_summary"), dict) else {}
        diagnostics = (evidence.get("technical_details") or {}).get("diagnostics") or {}
        quality = _norm(evidence.get("evidence_status") or summary.get("evidence_status") or "unavailable")
        provider = _norm(diagnostics.get("provider") or "unknown").lower() or "unknown"
        subject_type = _norm(diagnostics.get("subject_type") or row.get("subject_type") or "unknown").lower()
        reason = _norm(diagnostics.get("failure_reason") or evidence.get("evidence_failure_reason") or summary.get("evidence_failure_reason") or "unknown")
        field_presence = diagnostics.get("field_presence") if isinstance(diagnostics.get("field_presence"), dict) else {}

        result["evidence_quality"][quality] += 1
        result["provider"][provider] += 1
        result["subject_type"][subject_type] += 1
        result["failure_reason"][reason] += 1
        for category in _category(row):
            result["category"][category] += 1
        for missing in diagnostics.get("missing_identifier_types") or []:
            result["missing_identifier_type"][_norm(missing)] += 1
        for field, present in field_presence.items():
            if present:
                result["field_presence_rows"][field] += 1
        result["examples"].setdefault(reason, {
            "application_ref": row.get("application_ref"),
            "subject_type": row.get("subject_type"),
            "subject_name": row.get("subject_name"),
            "canonical_status": row.get("status_key"),
            "evidence_status": quality,
            "provider": provider,
        })
    return {
        key: dict(value) if isinstance(value, Counter) else value
        for key, value in result.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="Queue JSON file path, or '-' for stdin.")
    parser.add_argument("--api-base", help="RegMind API base URL, for example https://staging.regmind.co")
    parser.add_argument("--token-env", default="REGMIND_API_TOKEN", help="Environment variable containing bearer token.")
    parser.add_argument("--limit", type=int, default=100, help="Page size for API fetches.")
    args = parser.parse_args()

    if args.input:
        rows = _read_input(args.input)
    elif args.api_base:
        token = os.environ.get(args.token_env, "")
        if not token:
            parser.error(f"{args.token_env} is not set")
        rows = _fetch_api(args.api_base, token, limit=max(1, min(args.limit, 100)))
    else:
        parser.error("provide --input or --api-base")

    print(json.dumps(aggregate(rows), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
