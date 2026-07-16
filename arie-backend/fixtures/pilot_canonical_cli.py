"""Operator CLI for Pilot Canonical Dataset v1.

No command changes the RSMP activation flag.  ``apply`` is intentionally
triple-gated and is not executed as part of this PR.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from fixtures.pilot_canonical import (
    load_manifest,
    manifest_sha256,
    scenarios,
    validate_manifest,
)


REQUIRED_CONFIRM_TOKEN = "APPLY-PILOT-CANONICAL-DATASET-V1"
REQUIRED_ALLOW_VALUE = "1"


def _references(value: Optional[str]) -> Optional[List[str]]:
    return [item.strip() for item in (value or "").split(",") if item.strip()] or None


def _enforce_apply_gates(*, confirm: str, reviewed_hash: str) -> None:
    environment = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    if environment != "staging":
        raise SystemExit(
            f"REFUSED: apply requires ENVIRONMENT=staging (got {environment!r})"
        )
    if os.environ.get("ALLOW_PILOT_CANONICAL_SEED") != REQUIRED_ALLOW_VALUE:
        raise SystemExit("REFUSED: apply requires ALLOW_PILOT_CANONICAL_SEED=1")
    if confirm != REQUIRED_CONFIRM_TOKEN:
        raise SystemExit(
            f"REFUSED: apply requires --confirm {REQUIRED_CONFIRM_TOKEN!r}"
        )
    actual_hash = manifest_sha256()
    if reviewed_hash != actual_hash:
        raise SystemExit(
            "REFUSED: --reviewed-hash does not match the exact manifest bytes "
            f"(expected {actual_hash})"
        )


def cmd_list(_args: argparse.Namespace) -> int:
    rows = scenarios()
    print(f"Pilot Canonical Dataset v1 ({len(rows)} scenarios; no writes)\n")
    for row in rows:
        expected = row["expected"]
        print(
            f"{row['reference']}  {expected['score']:>4}  {expected['tier']:<9} "
            f"{expected['lane']:<15} {row['purpose']}"
        )
    return 0


def cmd_validate(_args: argparse.Namespace) -> int:
    result = validate_manifest(load_manifest())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    from fixtures.pilot_canonical_seeder import seed_pilot_canonical_dataset

    results = seed_pilot_canonical_dataset(
        dry_run=True,
        references=_references(args.reference),
        validate_runtime=True,
    )
    print(
        json.dumps(
            {
                "dry_run": True,
                "rolled_back": True,
                "scenario_count": len(results),
                "manifest_sha256": manifest_sha256(),
                "results": results,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    _enforce_apply_gates(confirm=args.confirm, reviewed_hash=args.reviewed_hash)
    from fixtures.pilot_canonical_seeder import seed_pilot_canonical_dataset

    results = seed_pilot_canonical_dataset(
        dry_run=False,
        references=_references(args.reference),
        validate_runtime=True,
    )
    print(
        json.dumps(
            {
                "applied": True,
                "scenario_count": len(results),
                "manifest_sha256": manifest_sha256(),
                "results": results,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fixtures.pilot_canonical_cli",
        description="Pilot Canonical Dataset v1 (guarded, deterministic, non-production)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list reviewed scenarios without DB access").set_defaults(func=cmd_list)
    sub.add_parser("validate", help="validate manifest without DB access").set_defaults(func=cmd_validate)

    dry = sub.add_parser("dry-run", help="exercise one transaction and roll it back")
    dry.add_argument("--reference", help="optional comma-separated RM-PILOT references")
    dry.set_defaults(func=cmd_dry_run)

    apply = sub.add_parser("apply", help="commit to staging after separate approval")
    apply.add_argument("--reference", help="optional comma-separated RM-PILOT references")
    apply.add_argument("--confirm", required=True)
    apply.add_argument("--reviewed-hash", required=True)
    apply.set_defaults(func=cmd_apply)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
