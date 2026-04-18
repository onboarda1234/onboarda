#!/usr/bin/env python3
"""
cleanup_named_application.py
─────────────────────────────
One-time script to hard-delete all applications matching a given company name
from the Onboarda/RegMind database, cascading to all child tables.

Matching strategy: exact, case-insensitive, whitespace-normalised comparison.
  UPPER(TRIM(company_name)) == UPPER(TRIM(<target>))
No fuzzy or LIKE matching is used.

Usage
─────
  # Dry-run (default) — shows what WOULD be deleted, touches nothing
  python scripts/cleanup_named_application.py "1947 OIL & GAS PLC"

  # Live deletion — prints a full deletion report
  python scripts/cleanup_named_application.py "1947 OIL & GAS PLC" --execute

  # Point at a specific SQLite file (default: onboarda.db in backend root)
  python scripts/cleanup_named_application.py "1947 OIL & GAS PLC" --execute --db /path/to/db.sqlite

  # For PostgreSQL, set DATABASE_URL env var before running.
  DATABASE_URL=postgresql://user:pass@host/dbname \
    python scripts/cleanup_named_application.py "1947 OIL & GAS PLC" --execute

Deletion report
───────────────
After a successful --execute run the script prints:
  • Applications found (id, ref, status, created_at)
  • Rows deleted per child table
  • Local file paths attempted / removed
  • S3 keys that were queued for deletion (actual S3 deletion requires
    AWS credentials to be configured; see note below)
  • Anything that could not be verified

S3 note
───────
The script calls s3_client.delete_document() for each s3_key found in the
documents table.  This requires AWS credentials (AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME) to be present in the environment.
If credentials are absent the s3_keys are listed but NOT deleted — they are
flagged as "NOT VERIFIED / requires manual S3 cleanup".
"""

import argparse
import os
import sys
import json
import logging

# ── allow running from repo root OR from arie-backend/ ──────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)          # arie-backend/
sys.path.insert(0, _BACKEND)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def _get_db():
    """Return a DBConnection using get_db() from db module (honours DATABASE_URL)."""
    from db import get_db
    return get_db()


def _normalise(name: str) -> str:
    return name.upper().strip()


# Tables whose rows reference application_id
_CHILD_TABLES_BY_APP_ID = [
    "client_sessions",
    "documents",
    "client_notifications",
    "monitoring_alerts",
    "periodic_reviews",
    "sar_reports",
    "compliance_memos",
    "edd_cases",
    "directors",
    "ubos",
    "intermediaries",
    "transactions",
    "agent_executions",
    "sumsub_applicant_mappings",
    "supervisor_pipeline_results",
    "supervisor_audit_log",
    "screening_reports_normalized",
]
# Tables whose rows reference application_ref (not id)
_CHILD_TABLES_BY_REF = [
    "decision_records",
]


def run_cleanup(target_name: str, execute: bool):
    db = _get_db()

    # ── 1. Find matching applications ──────────────────────────────────────
    all_apps = db.execute("SELECT * FROM applications").fetchall()
    matching = [
        a for a in all_apps
        if _normalise(a["company_name"] or "") == _normalise(target_name)
    ]

    if not matching:
        log.info("No applications found matching company name: %r", target_name)
        db.close()
        return

    log.info("")
    log.info("═" * 70)
    log.info("  DELETION REPORT — %s", target_name.upper())
    log.info("  Mode: %s", "LIVE EXECUTION" if execute else "DRY RUN (pass --execute to delete)")
    log.info("═" * 70)
    log.info("")
    log.info("Applications matched (%d):", len(matching))
    for app in matching:
        log.info(
            "  id=%-36s  ref=%-22s  status=%-22s  created_at=%s",
            app["id"], app["ref"], app["status"], app["created_at"]
        )
    log.info("")

    total_deleted = {}
    s3_unverified = []

    for app in matching:
        app_id = app["id"]
        app_ref = app["ref"]
        log.info("── Processing app id=%s ref=%s ──────────────────────────────────", app_id, app_ref)

        # ── 2. Documents: local files + S3 ─────────────────────────────────
        docs = db.execute(
            "SELECT id, file_path, s3_key FROM documents WHERE application_id=?",
            (app_id,)
        ).fetchall()

        local_removed = []
        local_missing = []
        s3_queued = []

        for doc in docs:
            fp = doc["file_path"]
            if fp:
                if os.path.exists(fp):
                    if execute:
                        try:
                            os.remove(fp)
                            local_removed.append(fp)
                        except OSError as exc:
                            log.warning("  [WARN] Could not remove file %s: %s", fp, exc)
                    else:
                        local_removed.append(fp + "  [DRY RUN]")
                else:
                    local_missing.append(fp)

            s3k = doc.get("s3_key")
            if s3k:
                s3_queued.append(s3k)

        if local_removed:
            log.info("  Local files removed: %d", len(local_removed))
            for f in local_removed:
                log.info("    %s", f)
        if local_missing:
            log.info("  Local files NOT found on disk (already absent): %d", len(local_missing))
            for f in local_missing:
                log.info("    %s", f)

        # ── 3. S3 deletion ──────────────────────────────────────────────────
        if s3_queued:
            has_s3 = False
            try:
                from s3_client import get_s3_client
                has_s3 = True
            except Exception:
                pass

            if has_s3 and execute:
                try:
                    s3 = get_s3_client()
                    for key in s3_queued:
                        deleted, msg = s3.delete_document(key)
                        if deleted:
                            log.info("  S3 deleted: %s", key)
                        else:
                            log.warning("  S3 deletion FAILED for key %s: %s", key, msg)
                            s3_unverified.append(key)
                except Exception as exc:
                    log.warning("  S3 client unavailable: %s — keys NOT deleted", exc)
                    s3_unverified.extend(s3_queued)
            else:
                log.info("  S3 keys found (%d) — %s:",
                         len(s3_queued),
                         "NOT deleted (dry run)" if not execute else "credentials absent")
                for key in s3_queued:
                    log.info("    %s", key)
                s3_unverified.extend(s3_queued)

        # ── 4. Child table rows ─────────────────────────────────────────────
        for table in _CHILD_TABLES_BY_APP_ID:
            try:
                count_row = db.execute(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE application_id=?", (app_id,)
                ).fetchone()
                count = count_row["c"] if count_row else 0
            except Exception:
                count = 0
            if count:
                if execute:
                    db.execute(f"DELETE FROM {table} WHERE application_id=?", (app_id,))
                total_deleted[table] = total_deleted.get(table, 0) + count
                log.info("  %-42s %d row(s) %s", table + ":", count,
                         "deleted" if execute else "(would delete)")

        for table in _CHILD_TABLES_BY_REF:
            try:
                count_row = db.execute(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE application_ref=?", (app_ref,)
                ).fetchone()
                count = count_row["c"] if count_row else 0
            except Exception:
                count = 0
            if count:
                if execute:
                    db.execute(f"DELETE FROM {table} WHERE application_ref=?", (app_ref,))
                total_deleted[table] = total_deleted.get(table, 0) + count
                log.info("  %-42s %d row(s) %s", table + ":", count,
                         "deleted" if execute else "(would delete)")

        # ── 5. Application row itself ───────────────────────────────────────
        if execute:
            db.execute("DELETE FROM applications WHERE id=?", (app_id,))
            log.info("  applications:                              1 row deleted (id=%s ref=%s)", app_id, app_ref)
        else:
            log.info("  applications:                              1 row (would delete)")
        log.info("")

    if execute:
        db.commit()

    db.close()

    # ── 6. Summary ──────────────────────────────────────────────────────────
    log.info("═" * 70)
    log.info("SUMMARY")
    log.info("═" * 70)
    log.info("  Applications processed : %d", len(matching))
    if total_deleted:
        log.info("  Child rows deleted     :")
        for table, count in sorted(total_deleted.items()):
            log.info("    %-42s %d", table, count)
    else:
        log.info("  Child rows             : none found")

    if s3_unverified:
        log.info("")
        log.info("  ⚠  NOT VERIFIED — S3 objects that require manual deletion:")
        for key in s3_unverified:
            log.info("    s3://<bucket>/%s", key)
        log.info("  Run with AWS credentials (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)")
        log.info("  and S3_BUCKET_NAME set to perform automatic S3 cleanup.")
    else:
        log.info("  S3 objects             : none found or all deleted")

    if not execute:
        log.info("")
        log.info("  DRY RUN complete. Re-run with --execute to apply changes.")
    else:
        log.info("")
        log.info("  ✓ Deletion complete.")

    log.info("═" * 70)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("company_name", help='Company name to delete, e.g. "1947 OIL & GAS PLC"')
    parser.add_argument("--execute", action="store_true",
                        help="Actually perform deletion (default is dry-run)")
    parser.add_argument("--db", help="SQLite DB path (overrides SQLITE_PATH env / default path)")
    args = parser.parse_args()

    if args.db:
        os.environ["SQLITE_PATH"] = args.db

    run_cleanup(args.company_name, execute=args.execute)


if __name__ == "__main__":
    main()
