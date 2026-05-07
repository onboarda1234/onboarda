#!/usr/bin/env python3
"""
One-off repair for non-draft applications with missing or suspect risk fields.

Default mode is dry-run. Pass --apply to persist deterministic recomputation.

Note: risk_score=0 can be a legitimate deterministic LOW score when every
weighted dimension scores at the minimum. This script still treats stored zero
scores as suspect in non-draft applications because the historical bug also
used zero as a missing-risk fallback; the deterministic recomputation decides
whether zero should remain zero.
"""

from __future__ import annotations

import argparse
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
from party_utils import get_application_parties  # noqa: E402
from prescreening.normalize import safe_json_loads  # noqa: E402
from prescreening.risk_inputs import build_prescreening_risk_input  # noqa: E402
from rule_engine import _get_risk_config_version, compute_risk_score  # noqa: E402


LOGGER = logging.getLogger("risk_repair")

SUSPECT_STATUSES = (
    "submitted", "prescreening_submitted", "pricing_review", "pricing_accepted",
    "pre_approval_review", "pre_approved", "kyc_documents", "kyc_submitted",
    "compliance_review", "in_review", "under_review", "edd_required",
    "approved", "rejected", "rmi_sent", "withdrawn",
)


def _column_exists(db, table_name: str, column_name: str) -> bool:
    if getattr(db, "is_postgres", False):
        row = db.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = ? AND column_name = ?
            LIMIT 1
            """,
            (table_name, column_name),
        ).fetchone()
        return bool(row)

    rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any((row["name"] if hasattr(row, "keys") else row[1]) == column_name for row in rows)


def _table_exists(db, table_name: str) -> bool:
    if getattr(db, "is_postgres", False):
        row = db.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = ?
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()
        return bool(row)

    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _risk_update_columns(db):
    optional = [
        "risk_escalations",
        "base_risk_level",
        "final_risk_level",
        "elevation_reason_text",
        "risk_computed_at",
        "risk_config_version",
        "onboarding_lane",
    ]
    return {column: _column_exists(db, "applications", column) for column in optional}


def _list_suspect_applications(db):
    placeholders = ",".join("?" for _ in SUSPECT_STATUSES)
    return db.execute(
        f"""
        SELECT *
        FROM applications
        WHERE status IN ({placeholders})
          AND (
            risk_level IS NULL OR risk_level = ''
            OR risk_score IS NULL
            OR risk_score = 0
          )
        ORDER BY updated_at DESC, created_at DESC
        """,
        SUSPECT_STATUSES,
    ).fetchall()


def recompute_application_risk(db, app_row):
    app = dict(app_row)
    directors, ubos, intermediaries = get_application_parties(db, app["id"])
    scoring_input = build_prescreening_risk_input(
        application=app,
        prescreening_data=safe_json_loads(app.get("prescreening_data")),
        directors=directors,
        ubos=ubos,
        intermediaries=intermediaries,
    )
    return compute_risk_score(scoring_input)


def persist_recomputed_risk(db, app_row, risk, columns):
    assignments = ["risk_score=?", "risk_level=?", "risk_dimensions=?"]
    values = [risk["score"], risk["level"], json.dumps(risk.get("dimensions", {}))]

    if columns.get("onboarding_lane"):
        assignments.append("onboarding_lane=?")
        values.append(risk.get("lane", "Standard Review"))
    if columns.get("risk_computed_at"):
        assignments.append("risk_computed_at=?")
        values.append(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if columns.get("risk_config_version"):
        assignments.append("risk_config_version=?")
        values.append(str(_get_risk_config_version(db) or ""))
    if columns.get("risk_escalations"):
        assignments.append("risk_escalations=?")
        values.append(json.dumps(risk.get("escalations", [])))
    if columns.get("base_risk_level"):
        assignments.append("base_risk_level=?")
        values.append(risk.get("base_risk_level", risk["level"]))
    if columns.get("final_risk_level"):
        assignments.append("final_risk_level=?")
        values.append(risk.get("final_risk_level", risk["level"]))
    if columns.get("elevation_reason_text"):
        assignments.append("elevation_reason_text=?")
        values.append(risk.get("elevation_reason_text", ""))

    assignments.append("updated_at=CURRENT_TIMESTAMP")
    values.append(app_row["id"])

    db.execute(
        "UPDATE applications SET " + ", ".join(assignments) + " WHERE id=?",
        tuple(values),
    )


def repair_missing_risk_scores(apply: bool = False):
    db = get_db()
    repaired = 0
    failed = 0
    by_status = {}
    proposed_changes = []
    unrecomputable = []
    try:
        if not _table_exists(db, "applications"):
            LOGGER.error("applications table not found; check database configuration before running repair")
            return {
                "suspect": 0,
                "recomputed": 0,
                "failed": 0,
                "applied": apply,
                "statuses_affected": {},
                "proposed_changes": [],
                "unrecomputable": [],
                "error": "applications table not found",
            }

        columns = _risk_update_columns(db)
        rows = _list_suspect_applications(db)
        for row in rows:
            app = dict(row)
            status = app.get("status") or "unknown"
            by_status[status] = by_status.get(status, 0) + 1
            try:
                risk = recompute_application_risk(db, app)
                proposed_changes.append({
                    "ref": app.get("ref"),
                    "company_name": app.get("company_name"),
                    "status": status,
                    "from": {
                        "risk_level": app.get("risk_level"),
                        "risk_score": app.get("risk_score"),
                    },
                    "to": {
                        "risk_level": risk.get("level"),
                        "risk_score": risk.get("score"),
                    },
                })
                LOGGER.info(
                    "%s %s %s: %s/%s -> %s/%s",
                    "REPAIR" if apply else "DRY-RUN",
                    app.get("ref"),
                    app.get("company_name"),
                    app.get("risk_level"),
                    app.get("risk_score"),
                    risk.get("level"),
                    risk.get("score"),
                )
                if apply:
                    persist_recomputed_risk(db, app, risk, columns)
                repaired += 1
            except Exception as exc:
                failed += 1
                unrecomputable.append({
                    "ref": app.get("ref"),
                    "company_name": app.get("company_name"),
                    "status": status,
                    "error": str(exc),
                })
                LOGGER.error(
                    "UNRECOMPUTABLE %s %s: %s",
                    app.get("ref"),
                    app.get("company_name"),
                    exc,
                    exc_info=True,
                )
        if apply:
            db.commit()
        else:
            try:
                db.rollback()
            except Exception:
                pass
        return {
            "suspect": len(rows),
            "recomputed": repaired,
            "failed": failed,
            "applied": apply,
            "statuses_affected": by_status,
            "proposed_changes": proposed_changes,
            "unrecomputable": unrecomputable,
        }
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist recomputed risk fields")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s")
    result = repair_missing_risk_scores(apply=args.apply)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
