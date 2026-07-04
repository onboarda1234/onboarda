"""GDPR Article 17 (right to erasure) — subject erasure with AML-retention arbitration.

DRAFT / NOT WIRED LIVE (audit finding H2).
=========================================================================
This module is a bounded first cut of a *real* subject-erasure executor. Today
`gdpr.complete_dsar()` only flips a DSAR's status — no PII is actually deleted,
and there is no arbitration against the AML/CFT record-retention obligation
(which legally *forbids* erasing records you must keep). That is worse than
useless: a request can be marked "completed" while all PII remains.

This module provides:
  * `plan_subject_erasure`  — read-only: what would be erased vs retained, and why.
  * `execute_subject_erasure` — performs anonymisation, but ONLY for records that
    are outside the AML retention window, and records an immutable erasure log.

It is intentionally NOT imported by server.py / gdpr.py and is NOT auto-run by
any scheduler or DSAR completion. Wiring it into the DSAR flow, adding an
operator UI, and a second-person approval step are follow-ups that require
review before this touches real PII. `execute_subject_erasure` defaults to
`dry_run=True` so an accidental call changes nothing.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("arie")

# AML/CFT retention default (days). The authoritative value is read from the
# data_retention_policies 'client_pii' policy when present; this is the fallback.
_DEFAULT_AML_RETENTION_DAYS = 2555  # 7 years

# PII columns anonymised per table for a subject erasure. Structural/foreign-key
# columns (ids, application_id) are deliberately preserved so the audit trail and
# AML records that reference them stay internally consistent.
_ERASURE_COLUMN_MAP: Dict[str, List[str]] = {
    "applications": ["company_name", "brn", "prescreening_data", "decision_notes",
                     "pre_approval_notes"],
    "directors": ["first_name", "last_name", "full_name", "nationality",
                  "date_of_birth", "country_of_residence", "residential_address",
                  "pep_declaration"],
    "ubos": ["first_name", "last_name", "full_name", "nationality",
             "date_of_birth", "country_of_residence", "residential_address",
             "pep_declaration"],
    "intermediaries": ["entity_name", "registered_address", "registration_number"],
    # NOTE: `documents` is intentionally omitted. The PII lives in the physical
    # file on disk / in S3, referenced by file_path / s3_key. Nulling those
    # pointers would ORPHAN the file (the real PII survives, now unlocatable),
    # which is the opposite of erasure. Document erasure must first delete the
    # physical object via the pointer and only then redact the row — a follow-up
    # that must land before this draft is wired live.
}

_REDACTION_TOKEN = "[ERASED]"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    for candidate in (text, text.split(".")[0]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _aml_retention_days(db) -> int:
    try:
        row = db.execute(
            "SELECT retention_days FROM data_retention_policies WHERE data_category = 'client_pii'"
        ).fetchone()
        if row is not None:
            days = row["retention_days"] if not isinstance(row, tuple) else row[0]
            if days:
                return int(days)
    except Exception:
        pass
    return _DEFAULT_AML_RETENTION_DAYS


def _ensure_erasure_log_table(db) -> None:
    is_pg = getattr(db, "is_postgres", False)
    ts_type = "TIMESTAMP" if is_pg else "TEXT"
    ts_default = "CURRENT_TIMESTAMP" if is_pg else "(datetime('now'))"
    db.executescript(f"""
    CREATE TABLE IF NOT EXISTS gdpr_erasure_log (
        id TEXT PRIMARY KEY,
        executed_at {ts_type} NOT NULL DEFAULT {ts_default},
        client_id TEXT,
        application_id TEXT,
        requested_by TEXT,
        action TEXT NOT NULL,
        tables_affected TEXT,
        retention_overridden INTEGER DEFAULT 0,
        override_reason TEXT,
        note TEXT
    );
    """)


def plan_subject_erasure(db, client_id: str) -> Dict[str, Any]:
    """Read-only: classify each of a subject's applications as erasable or retained.

    An application is RETAINED (cannot be erased) while it is inside the AML
    retention window measured from its relationship-end reference date
    (decided_at / closed_at / updated_at / created_at, first available). If no
    reference date can be determined the record is conservatively RETAINED.
    """
    retention_days = _aml_retention_days(db)
    now = _now()
    apps = [dict(r) for r in db.execute(
        "SELECT id, ref, status, decided_at, closed_at, updated_at, created_at "
        "FROM applications WHERE client_id = ?",
        (client_id,),
    ).fetchall()] if _has_column(db, "applications", "closed_at") else [dict(r) for r in db.execute(
        "SELECT id, ref, status, decided_at, updated_at, created_at "
        "FROM applications WHERE client_id = ?",
        (client_id,),
    ).fetchall()]

    classified = []
    for app in apps:
        ref_raw = app.get("decided_at") or app.get("closed_at") or app.get("updated_at") or app.get("created_at")
        ref_dt = _parse_dt(ref_raw)
        if ref_dt is None:
            retained, reason = True, "no_reference_date_conservatively_retained"
            age_days = None
        else:
            age_days = (now - ref_dt).days
            if age_days < retention_days:
                retained, reason = True, f"within_aml_retention ({age_days}/{retention_days} days)"
            else:
                retained, reason = False, f"outside_aml_retention ({age_days}/{retention_days} days)"
        classified.append({
            "application_id": app.get("id"),
            "ref": app.get("ref"),
            "status": app.get("status"),
            "reference_date": ref_raw,
            "age_days": age_days,
            "retained": retained,
            "reason": reason,
        })

    return {
        "client_id": client_id,
        "retention_days": retention_days,
        "applications": classified,
        "erasable_application_ids": [c["application_id"] for c in classified if not c["retained"]],
        "retained_application_ids": [c["application_id"] for c in classified if c["retained"]],
        "fully_erasable": bool(classified) and all(not c["retained"] for c in classified),
    }


def _has_column(db, table: str, column: str) -> bool:
    try:
        if getattr(db, "is_postgres", False):
            row = db.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
                (table, column),
            ).fetchone()
            return row is not None
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        names = {(r["name"] if not isinstance(r, tuple) else r[1]) for r in rows}
        return column in names
    except Exception:
        return False


def _anonymise_application(db, application_id: str) -> List[str]:
    affected = []
    # Parent application row.
    cols = [c for c in _ERASURE_COLUMN_MAP["applications"] if _has_column(db, "applications", c)]
    if cols:
        assignments = ", ".join(f"{c} = ?" for c in cols)
        values = [json.dumps({"erased": True}) if c == "prescreening_data" else _REDACTION_TOKEN for c in cols]
        db.execute(f"UPDATE applications SET {assignments} WHERE id = ?", (*values, application_id))
        affected.append("applications")
    # Child PII tables scoped to this application.
    for table in ("directors", "ubos", "intermediaries", "documents"):
        tcols = [c for c in _ERASURE_COLUMN_MAP.get(table, []) if _has_column(db, table, c)]
        if not tcols:
            continue
        assignments = ", ".join(f"{c} = ?" for c in tcols)
        values = [json.dumps({"erased": True}) if c == "pep_declaration" else _REDACTION_TOKEN for c in tcols]
        db.execute(
            f"UPDATE {table} SET {assignments} WHERE application_id = ?",
            (*values, application_id),
        )
        affected.append(table)
    return affected


def _log_erasure(db, *, client_id, application_id, requested_by, action,
                 tables_affected, retention_overridden=False, override_reason=None, note=None):
    db.execute(
        "INSERT INTO gdpr_erasure_log "
        "(id, client_id, application_id, requested_by, action, tables_affected, "
        " retention_overridden, override_reason, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            secrets.token_hex(8), client_id, application_id, requested_by, action,
            ",".join(tables_affected) if tables_affected else "",
            1 if retention_overridden else 0, override_reason, note,
        ),
    )


def execute_subject_erasure(
    db,
    client_id: str,
    *,
    requested_by: str,
    dry_run: bool = True,
    override_retention: bool = False,
    override_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Erase (anonymise) a subject's PII for records outside AML retention.

    SAFETY: defaults to dry_run=True (no changes). Records inside the AML
    retention window are refused unless override_retention=True WITH a written
    override_reason, and every action — including refusals and overrides — is
    written to the immutable gdpr_erasure_log. This function does not commit; the
    caller owns the transaction.
    """
    plan = plan_subject_erasure(db, client_id)

    if dry_run:
        plan["action"] = "dry_run"
        plan["changes_made"] = False
        return plan

    _ensure_erasure_log_table(db)

    if override_retention and not (override_reason and override_reason.strip()):
        # Record the refused attempt in the immutable log before returning, so an
        # override attempt without a written reason leaves an audit trail too.
        _log_erasure(db, client_id=client_id, application_id=None, requested_by=requested_by,
                     action="refused_override_without_reason", tables_affected=[],
                     retention_overridden=True,
                     note="override_retention requested without a written override_reason")
        return {**plan, "action": "refused", "error": "override_retention requires a written override_reason"}

    erased_apps, retained_refused, tables_touched = [], [], set()

    for entry in plan["applications"]:
        app_id = entry["application_id"]
        if entry["retained"] and not override_retention:
            retained_refused.append(app_id)
            _log_erasure(db, client_id=client_id, application_id=app_id, requested_by=requested_by,
                         action="retained_refused", tables_affected=[], note=entry["reason"])
            continue
        affected = _anonymise_application(db, app_id)
        tables_touched.update(affected)
        erased_apps.append(app_id)
        _log_erasure(db, client_id=client_id, application_id=app_id, requested_by=requested_by,
                     action="erased", tables_affected=affected,
                     retention_overridden=bool(entry["retained"] and override_retention),
                     override_reason=override_reason if entry["retained"] else None,
                     note=entry["reason"])

    # The client-account row is only erased when nothing is retained (or override).
    client_erased = False
    if not retained_refused:
        if _has_column(db, "clients", "email"):
            db.execute(
                "UPDATE clients SET email = ?, company_name = ?, password_hash = ? WHERE id = ?",
                (f"erased+{client_id}@erased.invalid", _REDACTION_TOKEN, secrets.token_hex(32), client_id),
            )
            tables_touched.add("clients")
            client_erased = True
        _log_erasure(db, client_id=client_id, application_id=None, requested_by=requested_by,
                     action="client_account_erased" if client_erased else "client_account_skipped",
                     tables_affected=["clients"] if client_erased else [])

    return {
        **plan,
        "action": "executed",
        "changes_made": True,
        "erased_application_ids": erased_apps,
        "retained_refused_application_ids": retained_refused,
        "client_account_erased": client_erased,
        "tables_affected": sorted(tables_touched),
    }
