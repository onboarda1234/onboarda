#!/usr/bin/env python3
"""Verify the DR/backup posture and emit drill evidence (P9-8 / DCI-027 / FEO-009).

DCI-027 is a CRITICAL production blocker: the DR/backup drill with a proven
restore path and stated RTO/RPO. The repo currently documents the *intent*
(ROLLBACK_RUNBOOK.md branch B2 restore-from-snapshot, DEPLOYMENT_RUNBOOK.md
backup-retention checks) but has no way to CHECK the posture or record that a
drill happened.

This script is read-only against AWS. It reports, and fails non-zero when the
posture does not meet the baseline, so it can gate a release or run on a
schedule:

* backup retention >= the required minimum (default 7 days)
* deletion protection enabled
* storage encrypted
* Multi-AZ (advisory — reported, not enforced, since staging is single-AZ by
  design and the register tracks prod separately)
* the latest restorable time is recent enough to make the stated RPO true —
  this is the check that actually proves PITR is WORKING rather than merely
  configured

`--evidence-out` writes a JSON evidence document in the closure-report
convention so a drill can be filed against the register row. Recording an
evidence file is NOT the same as passing: the verdict is stated in the file.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Dict, List, Optional

DEFAULT_INSTANCE = "regmind-staging-db"
DEFAULT_REGION = "af-south-1"

# Baseline from DEPLOYMENT_RUNBOOK.md ("retention at least 7 days and deletion
# protection enabled") and ROLLBACK_RUNBOOK.md precondition 4.
REQUIRED_RETENTION_DAYS = 7
# The B2 restore path is only real if PITR can reach a point close to now.
MAX_RESTORE_LAG_SECONDS = 3600


def _aws_json(args: List[str]) -> Any:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise RuntimeError(
            "REFUSED: the aws CLI is not installed or not on PATH.") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("aws call timed out after 120s") from None
    if proc.returncode != 0:
        raise RuntimeError(f"aws call failed: {' '.join(args)}\n{proc.stderr.strip()}")
    return json.loads(proc.stdout or "null")


def describe_instance(instance_id: str, region: str) -> Dict[str, Any]:
    payload = _aws_json([
        "aws", "rds", "describe-db-instances",
        "--db-instance-identifier", instance_id,
        "--region", region, "--output", "json",
    ])
    instances = (payload or {}).get("DBInstances") or []
    if not instances:
        raise RuntimeError(f"RDS instance {instance_id!r} not found in {region}")
    return instances[0]


def evaluate_posture(instance: Dict[str, Any], *, now_iso: Optional[str] = None,
                     required_retention_days: int = REQUIRED_RETENTION_DAYS,
                     max_restore_lag_seconds: int = MAX_RESTORE_LAG_SECONDS,
                     ) -> Dict[str, Any]:
    """Pure evaluation so the policy is unit-testable without AWS."""
    from datetime import datetime, timezone

    checks: List[Dict[str, Any]] = []

    retention = int(instance.get("BackupRetentionPeriod") or 0)
    checks.append({
        "check": "backup_retention_days",
        "observed": retention,
        "required": f">= {required_retention_days}",
        "passed": retention >= required_retention_days,
        "blocking": True,
    })

    deletion_protection = bool(instance.get("DeletionProtection"))
    checks.append({
        "check": "deletion_protection",
        "observed": deletion_protection,
        "required": True,
        "passed": deletion_protection,
        "blocking": True,
    })

    encrypted = bool(instance.get("StorageEncrypted"))
    checks.append({
        "check": "storage_encrypted",
        "observed": encrypted,
        "required": True,
        "passed": encrypted,
        "blocking": True,
    })

    # PITR is only proven by a RECENT latest-restorable-time. A configured
    # retention window with a stale restore point is the failure mode this
    # check exists to catch.
    latest = instance.get("LatestRestorableTime")
    lag_seconds: Optional[float] = None
    if latest:
        try:
            parsed = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
            reference = (
                datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
                if now_iso else datetime.now(timezone.utc)
            )
            # A naive timestamp (aws CLI output, some botocore paths) against
            # an aware reference raises TypeError, not ValueError — catching
            # only ValueError crashed the real code path, which is the one no
            # test covered because every test injects now_iso.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            lag_seconds = (reference - parsed).total_seconds()
        except (ValueError, TypeError):
            lag_seconds = None
    # A NEGATIVE lag means the restore point is in the future — clock skew or a
    # misparsed timestamp. Treating that as "very fresh" would pass the check
    # on bad data, so it fails.
    pitr_ok = (
        lag_seconds is not None
        and 0 <= lag_seconds <= max_restore_lag_seconds
    )
    checks.append({
        "check": "pitr_latest_restorable_recent",
        "observed": {"latest_restorable_time": latest, "lag_seconds": lag_seconds},
        "required": f"0 <= lag <= {max_restore_lag_seconds}s",
        "passed": pitr_ok,
        "blocking": True,
    })

    multi_az = bool(instance.get("MultiAZ"))
    checks.append({
        "check": "multi_az",
        "observed": multi_az,
        "required": "advisory — required for production, not for staging",
        "passed": multi_az,
        "blocking": False,
    })

    blocking_failures = [c["check"] for c in checks if c["blocking"] and not c["passed"]]
    advisory_failures = [c["check"] for c in checks if not c["blocking"] and not c["passed"]]
    return {
        "instance": instance.get("DBInstanceIdentifier"),
        "engine_version": instance.get("EngineVersion"),
        "checks": checks,
        "blocking_failures": blocking_failures,
        "advisory_failures": advisory_failures,
        "verdict": "PASS" if not blocking_failures else "FAIL",
        # RPO is bounded by the PITR lag; RTO must come from an actual timed
        # restore drill and is deliberately NOT asserted here.
        "rpo_seconds_observed": lag_seconds,
        # RTO must come from a timed restore drill, not from configuration.
        "rto_seconds_observed": None,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", default=DEFAULT_INSTANCE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--required-retention-days", type=int,
                        default=REQUIRED_RETENTION_DAYS)
    parser.add_argument("--max-restore-lag-seconds", type=int,
                        default=MAX_RESTORE_LAG_SECONDS)
    parser.add_argument("--evidence-out", default="",
                        help="Write a JSON evidence document to this path")
    args = parser.parse_args(argv)

    instance = describe_instance(args.instance_id, args.region)
    report = evaluate_posture(
        instance,
        required_retention_days=args.required_retention_days,
        max_restore_lag_seconds=args.max_restore_lag_seconds,
    )
    print(json.dumps(report, indent=2, default=str))

    if args.evidence_out:
        with open(args.evidence_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
        print(f"\nEvidence written to {args.evidence_out}")

    if report["verdict"] != "PASS":
        print(f"\nFAIL: {', '.join(report['blocking_failures'])}", file=sys.stderr)
        return 1
    if report["advisory_failures"]:
        print(f"\nPASS with advisories: {', '.join(report['advisory_failures'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
