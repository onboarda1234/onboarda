#!/usr/bin/env python3
"""
Staging Shadow-Parity Validation Script — SCR-012
==================================================
For every application in the staging database that has both a legacy
screening_report and a normalized record, verifies that:

    denormalize_to_legacy(normalized) == legacy

Outputs:
- Summary of applications checked
- Parity failures with application_id, client_id, source hash, and diff summary
- No PII in output

Usage:
    ENVIRONMENT=staging python scripts/staging_shadow_parity.py

    Or with a specific database path:
    DB_PATH=/path/to/staging.db python scripts/staging_shadow_parity.py

Exit codes:
    0 = all parity checks passed (or no applications to check)
    1 = one or more parity failures detected
"""

import json
import logging
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screening_normalizer import denormalize_to_legacy
from screening_storage import compute_report_hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("staging_shadow_parity")


def _diff_summary(expected, actual, max_keys=10):
    """
    Produce a safe diff summary without PII.
    Only reports key names and types that differ.
    """
    diffs = []
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return ["type mismatch: expected dict"]

    all_keys = set(list(expected.keys()) + list(actual.keys()))
    for key in sorted(all_keys):
        if len(diffs) >= max_keys:
            diffs.append(f"... and {len(all_keys) - max_keys} more keys")
            break
        if key not in expected:
            diffs.append(f"+{key} (extra in actual)")
        elif key not in actual:
            diffs.append(f"-{key} (missing in actual)")
        elif expected[key] != actual[key]:
            diffs.append(f"~{key} (value differs, type={type(expected[key]).__name__})")
    return diffs


def run_parity_check(db_path=None):
    """
    Run parity check across all applications with both legacy and normalized data.
    """
    from db import get_db

    db = get_db()

    # Check if screening_reports_normalized table exists
    try:
        db.execute("SELECT 1 FROM screening_reports_normalized LIMIT 1")
    except Exception:
        logger.info("screening_reports_normalized table does not exist — nothing to check")
        db.close()
        return 0

    # Get all normalized records
    normalized_rows = db.execute("""
        SELECT application_id, client_id, normalized_report_json,
               source_screening_report_hash, normalization_status
        FROM screening_reports_normalized
        WHERE normalization_status = 'success'
        ORDER BY application_id, id DESC
    """).fetchall()

    if not normalized_rows:
        logger.info("No normalized screening records found — nothing to check")
        db.close()
        return 0

    # Deduplicate: keep only the latest per application
    seen_apps = set()
    unique_rows = []
    for row in normalized_rows:
        app_id = row["application_id"]
        if app_id not in seen_apps:
            seen_apps.add(app_id)
            unique_rows.append(row)

    logger.info("Found %d applications with normalized records", len(unique_rows))

    failures = 0
    checked = 0

    for row in unique_rows:
        app_id = row["application_id"]
        client_id = row["client_id"]
        source_hash = row["source_screening_report_hash"]

        # Get legacy screening report from prescreening_data
        app = db.execute(
            "SELECT prescreening_data FROM applications WHERE id=?", (app_id,)
        ).fetchone()

        if not app:
            logger.warning(
                "Application not found: app_id=%s client_id=%s — skipping",
                app_id, client_id,
            )
            continue

        prescreening = json.loads(app["prescreening_data"] or "{}")
        legacy_report = prescreening.get("screening_report")

        if not legacy_report:
            logger.warning(
                "No legacy screening_report: app_id=%s client_id=%s — skipping",
                app_id, client_id,
            )
            continue

        # Parse the normalized report
        try:
            normalized_report = json.loads(row["normalized_report_json"])
        except (json.JSONDecodeError, TypeError):
            logger.error(
                "PARITY_FAILURE: Invalid normalized JSON: app_id=%s client_id=%s hash=%s",
                app_id, client_id, source_hash,
            )
            failures += 1
            continue

        # Denormalize and compare
        try:
            denormalized = denormalize_to_legacy(normalized_report)
        except Exception as e:
            logger.error(
                "PARITY_FAILURE: Denormalization error: app_id=%s client_id=%s hash=%s error=%s",
                app_id, client_id, source_hash, type(e).__name__,
            )
            failures += 1
            continue

        if denormalized == legacy_report:
            checked += 1
        else:
            failures += 1
            diffs = _diff_summary(legacy_report, denormalized)
            logger.error(
                "PARITY_FAILURE: Round-trip mismatch: app_id=%s client_id=%s hash=%s diffs=%s",
                app_id, client_id, source_hash, "; ".join(diffs),
            )

    db.close()

    logger.info(
        "Parity check complete: checked=%d failures=%d total_apps=%d",
        checked, failures, len(unique_rows),
    )

    if failures > 0:
        logger.error("PARITY CHECK FAILED: %d failure(s) detected", failures)
        return 1

    logger.info("PARITY CHECK PASSED: All %d applications match", checked)
    return 0


if __name__ == "__main__":
    sys.exit(run_parity_check())
