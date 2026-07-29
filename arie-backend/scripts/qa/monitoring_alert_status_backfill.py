#!/usr/bin/env python3
"""Operator CLI for the controlled Monitoring Alert status backfill.

The default command is read-only ``plan``. Apply requires the exact founder
approval phrase plus the fingerprint produced by the approved dry run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import get_db  # noqa: E402
from environment import get_environment, get_monitoring_feature_state  # noqa: E402
from monitoring_status_backfill import (  # noqa: E402
    ReconciliationError,
    apply_backfill,
    apply_rollback,
    begin_read_only_transaction,
    build_plan,
    build_rollback_plan,
    load_manifest,
    reconciliation_error_payload,
)


def _write(value: dict[str, Any], output: str | None) -> None:
    rendered = json.dumps(value, default=str, indent=2, sort_keys=True) + "\n"
    if output:
        Path(output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Fail-closed Monitoring Alert status reconciliation"
    )
    result.add_argument(
        "command",
        nargs="?",
        choices=("plan", "apply", "rollback-plan", "rollback"),
        default="plan",
    )
    result.add_argument("--manifest")
    result.add_argument("--output")
    result.add_argument("--actor")
    result.add_argument("--expected-plan-fingerprint")
    result.add_argument("--approval-phrase")
    result.add_argument("--expected-rollback-fingerprint")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    manifest = load_manifest(args.manifest)
    flags = get_monitoring_feature_state()
    db = get_db()
    try:
        if args.command in ("apply", "rollback") and get_environment() != "staging":
            raise ReconciliationError(
                "controlled mutation commands are restricted to staging"
            )
        if args.command == "plan":
            begin_read_only_transaction(db)
            result = build_plan(db, manifest, flags)
            db.rollback()
        elif args.command == "apply":
            result = apply_backfill(
                db,
                manifest,
                flags,
                actor=args.actor or "",
                expected_plan_fingerprint=args.expected_plan_fingerprint or "",
                approval_phrase=args.approval_phrase or "",
            )
        elif args.command == "rollback-plan":
            begin_read_only_transaction(db)
            result = build_rollback_plan(db, manifest, flags)
            db.rollback()
        else:
            result = apply_rollback(
                db,
                manifest,
                flags,
                actor=args.actor or "",
                expected_rollback_fingerprint=args.expected_rollback_fingerprint
                or "",
            )
        _write(result, args.output)
        return 0
    except ReconciliationError as exc:
        try:
            db.rollback()
        except Exception:
            # A post-commit verification error may be caused by a lost
            # connection. Cleanup must never mask the durable-commit evidence.
            pass
        _write(reconciliation_error_payload(exc), args.output)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
