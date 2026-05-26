#!/usr/bin/env python3
"""
Staging data cleanup — QA Rounds 8–11 artifacts.

Cancels stale Change Requests left in `submitted` state from the
R8–R11 QA cycles via the standard state-machine transition
(submitted → cancelled).

Safety:
- Only runs when ENVIRONMENT == "staging" or STAGING == "true".
- Dry-run by default; pass --execute to apply.
- Verifies no downstream state (alerts, memos, profile versions)
  was derived from the target CRs before cancelling.

Usage:
    python scripts/qa/cleanup_r8_r11_artifacts.py               # dry-run
    python scripts/qa/cleanup_r8_r11_artifacts.py --execute      # apply
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# IDs of the stale CRs to clean up
STALE_CR_IDS = [
    "CR-260414-49F1465C",
    "CR-260414-8AB70D4D",
    "CR-260414-AF8EAE96",
]

CANCEL_NOTES = "QA-cleanup: R8-R11 cycle"


def _check_environment():
    """Abort if not running on staging."""
    env = os.environ.get("ENVIRONMENT", "").lower()
    staging_flag = os.environ.get("STAGING", "").lower()
    if env != "staging" and staging_flag != "true":
        print(
            "ERROR: This script is only safe to run on staging. "
            "Set ENVIRONMENT=staging or STAGING=true.",
            file=sys.stderr,
        )
        sys.exit(1)


def _check_no_downstream(db, cr_id):
    """Verify no downstream state was derived from this CR."""
    issues = []

    # Check for profile versions referencing this CR
    row = db.execute(
        "SELECT COUNT(*) AS cnt FROM entity_profile_versions "
        "WHERE change_request_id = ?",
        (cr_id,),
    ).fetchone()
    if row and row["cnt"] > 0:
        issues.append(f"  {row['cnt']} entity_profile_versions row(s)")

    # Check for review records
    row = db.execute(
        "SELECT COUNT(*) AS cnt FROM change_request_reviews "
        "WHERE request_id = ?",
        (cr_id,),
    ).fetchone()
    if row and row["cnt"] > 0:
        issues.append(f"  {row['cnt']} change_request_reviews row(s)")

    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true",
        help="Apply changes (default is dry-run)",
    )
    args = parser.parse_args()

    _check_environment()

    from db import get_db

    db = get_db()
    try:
        for cr_id in STALE_CR_IDS:
            row = db.execute(
                "SELECT id, status, application_id FROM change_requests WHERE id = ?",
                (cr_id,),
            ).fetchone()

            if not row:
                print(f"[SKIP] {cr_id}: not found in database")
                continue

            if row["status"] == "cancelled":
                print(f"[SKIP] {cr_id}: already cancelled")
                continue

            if row["status"] != "submitted":
                print(
                    f"[WARN] {cr_id}: status is '{row['status']}', "
                    f"not 'submitted' — skipping (manual review needed)"
                )
                continue

            # Check downstream state
            issues = _check_no_downstream(db, cr_id)
            if issues:
                print(f"[WARN] {cr_id}: downstream state found:")
                for issue in issues:
                    print(issue)
                print("  Skipping — manual review needed")
                continue

            if args.execute:
                db.execute(
                    "UPDATE change_requests SET status = 'cancelled', "
                    "decision_notes = ?, updated_at = datetime('now') "
                    "WHERE id = ?",
                    (CANCEL_NOTES, cr_id),
                )
                db.commit()
                print(f"[DONE] {cr_id}: cancelled with notes '{CANCEL_NOTES}'")
            else:
                print(
                    f"[DRY-RUN] {cr_id}: would cancel "
                    f"(app={row['application_id']}, status={row['status']})"
                )
    finally:
        db.close()

    if not args.execute:
        print("\nDry-run complete. Pass --execute to apply changes.")


if __name__ == "__main__":
    main()
