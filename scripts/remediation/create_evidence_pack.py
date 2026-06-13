#!/usr/bin/env python3
"""Create a standard remediation evidence-pack folder."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path


STANDARD_FILES = [
    "diagnosis.md",
    "root_cause.md",
    "test_results.md",
    "full_suite_results.md",
    "staging_deploy.md",
    "api_smoke.md",
    "browser_smoke.md",
    "closure_report.md",
]

STANDARD_DIRS = [
    "screenshots",
    "runtime_json",
]


def slugify(value: str) -> str:
    cleaned = []
    previous_dash = False
    for char in value.strip().lower():
        if char.isalnum():
            cleaned.append(char)
            previous_dash = False
        elif not previous_dash:
            cleaned.append("-")
            previous_dash = True
    return "".join(cleaned).strip("-")


def build_pack_path(base_dir: Path, pr_id: str, short_name: str, timestamp: str | None) -> Path:
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return base_dir / f"{pr_id}_{slugify(short_name)}_{stamp}"


def write_stub(path: Path, title: str) -> None:
    if path.exists():
        return
    path.write_text(f"# {title}\n\nTBD.\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr_id", help="PR identifier, for example PR-1")
    parser.add_argument("short_name", help="Short remediation name, for example security-client-api-boundary-hardening")
    parser.add_argument(
        "--base-dir",
        default="docs/audits/evidence/remediation_sprints",
        help="Evidence pack root directory",
    )
    parser.add_argument(
        "--timestamp",
        help="UTC timestamp in YYYYMMDDTHHMMSSZ format. Defaults to current UTC time.",
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    pack_dir = build_pack_path(base_dir, args.pr_id, args.short_name, args.timestamp)
    pack_dir.mkdir(parents=True, exist_ok=True)

    for directory in STANDARD_DIRS:
        (pack_dir / directory).mkdir(exist_ok=True)

    for filename in STANDARD_FILES:
        title = filename.replace("_", " ").replace(".md", "").title()
        write_stub(pack_dir / filename, title)

    print(pack_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
