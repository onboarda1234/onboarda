"""Operator CLI for the governed Document Renewal staging fixture."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from fixtures.document_renewal_staging import (
    fixture_spec,
    manifest_sha256,
    validate_manifest,
)


REQUIRED_ALLOW_VARIABLE = "ALLOW_DOCUMENT_RENEWAL_STAGING_FIXTURE"
REQUIRED_CONFIRM_TOKEN = "APPLY-DOCUMENT-RENEWAL-STAGING-FIXTURE-V1"


def _enforce_apply_gates(*, confirm: str, reviewed_hash: str) -> None:
    environment = str(os.environ.get("ENVIRONMENT") or "").strip().lower()
    if environment != "staging":
        raise SystemExit("REFUSED: fixture apply requires ENVIRONMENT=staging")
    if os.environ.get(REQUIRED_ALLOW_VARIABLE) != "1":
        raise SystemExit(f"REFUSED: fixture apply requires {REQUIRED_ALLOW_VARIABLE}=1")
    if confirm != REQUIRED_CONFIRM_TOKEN:
        raise SystemExit(
            f"REFUSED: fixture apply requires --confirm {REQUIRED_CONFIRM_TOKEN!r}"
        )
    if reviewed_hash != manifest_sha256():
        raise SystemExit(
            "REFUSED: --reviewed-hash does not match the exact fixture manifest"
        )


def _print(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def cmd_validate(_args: argparse.Namespace) -> int:
    _print(validate_manifest())
    return 0


def cmd_show(_args: argparse.Namespace) -> int:
    _print(fixture_spec())
    return 0


def cmd_dry_run(_args: argparse.Namespace) -> int:
    from fixtures.document_renewal_staging_seeder import (
        seed_document_renewal_staging_fixture,
    )

    _print(
        seed_document_renewal_staging_fixture(
            dry_run=True,
        )
    )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    _enforce_apply_gates(confirm=args.confirm, reviewed_hash=args.reviewed_hash)
    from fixtures.document_renewal_staging_seeder import (
        seed_document_renewal_staging_fixture,
    )

    _print(
        seed_document_renewal_staging_fixture(
            dry_run=False,
        )
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fixtures.document_renewal_staging_cli",
        description="Governed Document Renewal staging fixture v1",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate the reviewed manifest without DB access").set_defaults(
        func=cmd_validate
    )
    sub.add_parser("show", help="show the safe fixture identity without DB access").set_defaults(
        func=cmd_show
    )
    sub.add_parser("dry-run", help="produce a deterministic read-only seed plan").set_defaults(
        func=cmd_dry_run
    )
    apply = sub.add_parser("apply", help="seed the fixture after all staging gates")
    apply.add_argument("--confirm", required=True)
    apply.add_argument("--reviewed-hash", required=True)
    apply.set_defaults(func=cmd_apply)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "REQUIRED_ALLOW_VARIABLE",
    "REQUIRED_CONFIRM_TOKEN",
    "_enforce_apply_gates",
    "main",
]
