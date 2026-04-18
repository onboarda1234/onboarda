#!/usr/bin/env python3
"""
Staging Shadow-Parity Validation Script — SCR-012 (Bidirectional)
=================================================================
For every application in the staging database that has both a legacy
screening_report and a normalized record, verifies **bidirectional**
round-trip fidelity:

    Direction 1 (Forward):  denormalize_to_legacy(normalized) == legacy
    Direction 2 (Reverse):  normalize_screening_report(legacy) == normalized
    Source Hash:            compute_report_hash(legacy) == stored hash

No PII is emitted in output — only application IDs, client IDs,
source hashes, and structural diff summaries.

Manual Runbook
--------------
**When to run:**
    Run after every migration, schema change, or screening-normalizer
    code change against the staging database before promoting to
    production.

**How to run:**
    ENVIRONMENT=staging python scripts/staging_shadow_parity.py

    Or with a specific database path:
    DB_PATH=/path/to/staging.db python scripts/staging_shadow_parity.py

**What to look for:**
    - The final summary line reports Forward-pass, Reverse-pass,
      Hash-match counts and total Failures.
    - Any line containing PARITY_FAILURE indicates a mismatch.

**What a failure means:**
    - Forward failure: denormalize_to_legacy does not reconstruct the
      original legacy report — the denormalization logic has drifted.
    - Reverse failure: normalize_screening_report does not reproduce
      the stored normalized report — the normalization logic has
      drifted or the stored record is stale.
    - Hash mismatch: the legacy report in prescreening_data has
      changed since the normalized record was created.

**When to escalate:**
    - Any failure count > 0 blocks promotion to production.
    - If failures persist after re-running normalization, escalate to
      the screening-normalizer maintainer.

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

from screening_normalizer import denormalize_to_legacy, normalize_screening_report
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
    Run bidirectional parity check across all applications with both
    legacy and normalized data.
    """
    from db import get_db

    logger.info("=== Staging Shadow-Parity Check (Bidirectional) ===")

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

    total_apps = len(unique_rows)
    logger.info("Found %d applications with normalized records", total_apps)

    failures = 0
    checked = 0
    forward_pass = 0
    reverse_pass = 0
    hash_match = 0

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

        checked += 1
        row_failed = False

        # --- Direction 1 (Forward): denormalize_to_legacy(normalized) == legacy ---
        try:
            denormalized = denormalize_to_legacy(normalized_report)
        except Exception as e:
            logger.error(
                "PARITY_FAILURE: Forward denormalization error: app_id=%s client_id=%s hash=%s error=%s",
                app_id, client_id, source_hash, type(e).__name__,
            )
            row_failed = True
            denormalized = None

        if denormalized is not None:
            if denormalized == legacy_report:
                forward_pass += 1
            else:
                diffs = _diff_summary(legacy_report, denormalized)
                logger.error(
                    "PARITY_FAILURE: Forward mismatch: app_id=%s client_id=%s hash=%s diffs=%s",
                    app_id, client_id, source_hash, "; ".join(diffs),
                )
                row_failed = True

        # --- Direction 2 (Reverse): normalize_screening_report(legacy) == normalized ---
        try:
            re_normalized = normalize_screening_report(legacy_report)
        except Exception as e:
            logger.error(
                "PARITY_FAILURE: Reverse normalization error: app_id=%s client_id=%s hash=%s error=%s",
                app_id, client_id, source_hash, type(e).__name__,
            )
            row_failed = True
            re_normalized = None

        if re_normalized is not None:
            if re_normalized == normalized_report:
                reverse_pass += 1
            else:
                diffs = _diff_summary(normalized_report, re_normalized)
                logger.error(
                    "PARITY_FAILURE: Reverse mismatch: app_id=%s client_id=%s hash=%s diffs=%s",
                    app_id, client_id, source_hash, "; ".join(diffs),
                )
                row_failed = True

        # --- Source hash check ---
        try:
            legacy_hash = compute_report_hash(legacy_report)
        except Exception as e:
            logger.error(
                "PARITY_FAILURE: Hash computation error: app_id=%s client_id=%s error=%s",
                app_id, client_id, type(e).__name__,
            )
            row_failed = True
            legacy_hash = None

        if legacy_hash is not None:
            if source_hash and legacy_hash != source_hash:
                logger.error(
                    "PARITY_FAILURE: Source hash mismatch: app_id=%s client_id=%s stored=%s computed=%s",
                    app_id, client_id, source_hash, legacy_hash,
                )
                row_failed = True
            else:
                hash_match += 1

        if row_failed:
            failures += 1

    db.close()

    logger.info(
        "Total: %d applications, Checked: %d, Forward-pass: %d, Reverse-pass: %d, Hash-match: %d, Failures: %d",
        total_apps, checked, forward_pass, reverse_pass, hash_match, failures,
    )

    if failures > 0:
        logger.error("FAIL — %d failure(s) detected", failures)
        return 1

    logger.info("PASS — All %d applications passed bidirectional parity", checked)
    return 0


if __name__ == "__main__":
    sys.exit(run_parity_check())
