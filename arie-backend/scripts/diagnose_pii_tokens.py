#!/usr/bin/env python3
"""
Diagnose and optionally repair unreadable party PII tokens.

Default mode is read-only and prints safe metadata only: table, row id,
application reference, field, token fingerprint, and classification. Raw PII and
ciphertext are never printed.

If an encrypted-looking token cannot be decrypted with the active
PII_ENCRYPTION_KEY, the only deterministic repair is to clear that unreadable
field and recollect/restore it from an authoritative source. Use
--apply-null-invalid only after reviewing the dry-run output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SCRIPT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from db import get_db  # noqa: E402
from party_utils import classify_pii_value  # noqa: E402


LOGGER = logging.getLogger("pii_diagnostics")

PII_TABLE_FIELDS = {
    "directors": ("passport_number", "nationality", "id_number"),
    "ubos": ("passport_number", "nationality"),
}


def _fingerprint(value) -> str:
    return hashlib.sha256(str(value).encode("utf-8", "ignore")).hexdigest()[:16]


def _row_to_dict(row):
    return dict(row) if hasattr(row, "keys") else dict(row)


def _table_exists(db, table_name: str) -> bool:
    if getattr(db, "is_postgres", False):
        return bool(db.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = ?
            LIMIT 1
            """,
            (table_name,),
        ).fetchone())
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone())


def _column_exists(db, table_name: str, column_name: str) -> bool:
    if getattr(db, "is_postgres", False):
        return bool(db.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = ? AND column_name = ?
            LIMIT 1
            """,
            (table_name, column_name),
        ).fetchone())
    rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any((_row_to_dict(row).get("name") or row[1]) == column_name for row in rows)


def _audit_log_exists(db) -> bool:
    return _table_exists(db, "audit_log")


def _safe_update_sql(table_name: str, field_name: str) -> str:
    if table_name not in PII_TABLE_FIELDS or field_name not in PII_TABLE_FIELDS[table_name]:
        raise ValueError("Unsafe PII table/field")
    return f"UPDATE {table_name} SET {field_name} = NULL WHERE id = ?"


def _scan_rows(db, table_name: str, fields):
    selected_fields = [field for field in fields if _column_exists(db, table_name, field)]
    if not selected_fields:
        return []

    join_ref = _table_exists(db, "applications") and _column_exists(db, "applications", "ref")
    if join_ref:
        sql = (
            f"SELECT p.id, p.application_id, a.ref AS application_ref, "
            f"{', '.join('p.' + field for field in selected_fields)} "
            f"FROM {table_name} p LEFT JOIN applications a ON a.id = p.application_id"
        )
    else:
        sql = (
            f"SELECT p.id, p.application_id, "
            f"{', '.join('p.' + field for field in selected_fields)} "
            f"FROM {table_name} p"
        )

    findings = []
    for row in db.execute(sql).fetchall():
        row_dict = _row_to_dict(row)
        for field in selected_fields:
            value = row_dict.get(field)
            status = classify_pii_value(value)
            if status in {"empty", "valid_encrypted"}:
                continue
            findings.append({
                "table": table_name,
                "row_id": row_dict.get("id"),
                "application_id": row_dict.get("application_id"),
                "application_ref": row_dict.get("application_ref"),
                "field": field,
                "status": status,
                "value_sha256_16": _fingerprint(value),
                "value_length": len(str(value)) if value is not None else 0,
            })
    return findings


def scan_pii_tokens(db):
    findings = []
    for table_name, fields in PII_TABLE_FIELDS.items():
        if _table_exists(db, table_name):
            findings.extend(_scan_rows(db, table_name, fields))
    return findings


def _write_repair_audit(db, finding, reason: str):
    if not _audit_log_exists(db):
        return
    detail = {
        "reason": reason,
        "repair": "null_unreadable_pii_token",
        "table": finding["table"],
        "row_id": finding["row_id"],
        "application_id": finding.get("application_id"),
        "field": finding["field"],
        "status": finding["status"],
        "value_sha256_16": finding["value_sha256_16"],
        "value_length": finding["value_length"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    db.execute(
        """
        INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "system",
            "PII diagnostics",
            "system",
            "PII Repair",
            finding.get("application_ref") or finding.get("application_id") or finding["row_id"],
            json.dumps(detail, sort_keys=True),
            "script",
        ),
    )


def apply_null_invalid_tokens(db, findings, reason: str):
    repaired = []
    for finding in findings:
        if finding["status"] != "invalid_encrypted_token":
            continue
        db.execute(_safe_update_sql(finding["table"], finding["field"]), (finding["row_id"],))
        _write_repair_audit(db, finding, reason)
        repaired.append(finding)
    db.commit()
    return repaired


def build_summary(findings, repaired=None):
    counts = {}
    for finding in findings:
        key = f"{finding['table']}.{finding['field']}:{finding['status']}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "counts": counts,
        "findings": findings,
        "repaired_count": len(repaired or []),
        "repaired": repaired or [],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Diagnose unreadable party PII tokens without exposing PII.")
    parser.add_argument("--apply-null-invalid", action="store_true",
                        help="Set invalid encrypted-looking PII fields to NULL after dry-run review.")
    parser.add_argument("--reason", default="manual PII token repair",
                        help="Reason recorded in audit_log when --apply-null-invalid is used.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db = get_db()
    try:
        findings = scan_pii_tokens(db)
        repaired = []
        if args.apply_null_invalid:
            repaired = apply_null_invalid_tokens(db, findings, args.reason)
            findings = scan_pii_tokens(db)
        print(json.dumps(build_summary(findings, repaired), indent=2, sort_keys=True, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
