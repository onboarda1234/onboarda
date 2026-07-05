"""GDPR Article 17 (right to erasure) — subject erasure with AML-retention arbitration.

WIRED-BUT-OFF (audit finding H2 / H2B).
=========================================================================
`gdpr.complete_dsar()` only flips a DSAR's *response-workflow* status — it does
NOT erase PII and MUST NOT set ``erasure_executed`` (caveat A). Real erasure is
performed only by ``execute_subject_erasure`` here, which:

  * resolves each subject-PII category's retention window from
    ``data_retention_policies`` and FAILS CLOSED if a needed policy is missing,
    empty, or non-positive — never a hardcoded default ("retained must never be
    silent theatre");
  * builds a COMPLETE per-table erase / retain / defer ledger over every
    subject-linked table discovered in the live schema, so nothing is silently
    retained;
  * enforces the live-path invariant: a live (non-dry-run) execution can never
    report ``executed`` while any subject-linked table holding this subject's
    rows is deferred/unhandled — it must erase, legally-retain-with-cited-basis,
    or REFUSE the request as incomplete;
  * writes an expanded ``gdpr_erasure_log`` record (PG BOOLEAN correct, with a
    DSAR correlation id, per-category disposition, actor, dry-run flag and
    retention/override basis) so DSAR erasure status can be *verified from
    evidence*, not trusted from a flag (caveat B).

SAFETY: ``execute_subject_erasure`` defaults to ``dry_run=True``. This module is
NOT imported by any live runtime path and is NOT auto-run by any scheduler or by
DSAR completion. Physical file/S3 object deletion, DB-level append-only
protection for ``gdpr_erasure_log``, an operator UI, and a second-person
approval step remain deferred follow-ups (production condition PC-4) — and,
because ``documents`` (and other unimplemented tables) surface as *deferred* in
the ledger, the live-path invariant structurally BLOCKS a "completed" erasure
for any subject who has such rows until those follow-ups land.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("arie")


class RetentionPolicyError(Exception):
    """Raised when a required retention policy is missing/empty/invalid.

    Fail-closed: erasure must refuse rather than guess a window (audit C5).
    """


_REDACTION_TOKEN = "[ERASED]"

# ── Retention categories that govern a person-subject erasure ────────────────
# Each is resolved from data_retention_policies and FAILS CLOSED if absent.
# The effective retention window is the MOST CONSERVATIVE (longest) of these.
_SUBJECT_RETENTION_CATEGORIES = ("client_pii", "application_data")

# ── Erasable tables: PII columns anonymised per table. Structural/FK columns
# (ids, application_id) are preserved so retained AML records that reference
# them stay internally consistent. `category` is the retention policy that
# governs the person/onboarding record.
_ERASABLE_TABLES: Dict[str, Dict[str, Any]] = {
    "applications": {
        "scope": "application",
        "columns": ["company_name", "brn", "prescreening_data",
                    "decision_notes", "pre_approval_notes"],
        "json_columns": {"prescreening_data"},
    },
    "directors": {
        "scope": "application",
        "columns": ["first_name", "last_name", "full_name", "nationality",
                    "date_of_birth", "country_of_residence",
                    "residential_address", "pep_declaration"],
        "json_columns": {"pep_declaration"},
    },
    "ubos": {
        "scope": "application",
        "columns": ["first_name", "last_name", "full_name", "nationality",
                    "date_of_birth", "country_of_residence",
                    "residential_address", "pep_declaration"],
        "json_columns": {"pep_declaration"},
    },
    "intermediaries": {
        "scope": "application",
        "columns": ["entity_name", "registered_address", "registration_number"],
        "json_columns": set(),
    },
    "clients": {
        "scope": "client",
        "columns": ["email", "company_name"],
        "json_columns": set(),
    },
}

# ── Tables that MUST be retained regardless of an erasure request, each with
# the legal basis cited (never "required" without a basis — audit refinement).
# Erasing these would destroy AML/FIU/audit-integrity obligations.
_RETAINED_REQUIRED_TABLES: Dict[str, Dict[str, str]] = {
    "sar_reports": {"category": "sar_reports",
                    "legal_basis": "Regulatory obligation (FIU reporting) — SARs are never erasable"},
    "audit_log": {"category": "audit_logs",
                  "legal_basis": "Legitimate interest + regulatory accountability (audit trail)"},
    "supervisor_audit_log": {"category": "audit_logs",
                             "legal_basis": "AML decision hash-chain integrity — erasure would break the chain"},
    "data_subject_requests": {"category": "audit_logs",
                              "legal_basis": "Erasure-request audit trail (proof the request was handled)"},
    "gdpr_erasure_log": {"category": "audit_logs",
                         "legal_basis": "Erasure-execution audit trail"},
    "data_purge_log": {"category": "audit_logs",
                       "legal_basis": "Retention-purge audit trail"},
}

# Infrastructure/reference tables that are never subject PII (excluded from the
# deferred bucket so they do not spuriously block a live erasure).
_NON_SUBJECT_TABLES = frozenset({
    "schema_version", "schema_migrations", "data_migration_markers",
    "data_retention_policies", "supervisor_audit_migrations", "rate_limits",
    "risk_config", "ai_agents", "ai_checks", "system_settings",
    "revoked_tokens", "client_sessions", "enhanced_requirement_rules",
    "country_risk_entries", "country_risk_snapshots",
})


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


def _list_tables(db) -> List[str]:
    try:
        if getattr(db, "is_postgres", False):
            rows = db.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            ).fetchall()
            return [(r["table_name"] if not isinstance(r, tuple) else r[0]) for r in rows]
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return [(r["name"] if not isinstance(r, tuple) else r[0]) for r in rows]
    except Exception:
        return []


def _resolve_retention_days(db, category: str) -> int:
    """Fail-closed, category-keyed retention window (audit C5).

    Raises RetentionPolicyError if the category's policy row is missing, empty,
    or non-positive. NEVER returns a hardcoded default — an empty/broken policy
    table must refuse erasure, not silently apply 7 years.
    """
    try:
        row = db.execute(
            "SELECT retention_days FROM data_retention_policies WHERE data_category = ?",
            (category,),
        ).fetchone()
    except Exception as exc:
        raise RetentionPolicyError(
            f"retention policy lookup failed for category '{category}': {exc}"
        ) from exc
    if row is None:
        raise RetentionPolicyError(
            f"no data_retention_policies row for category '{category}' — "
            "cannot compute an erasure window; refusing (fail-closed)"
        )
    days = row["retention_days"] if not isinstance(row, tuple) else row[0]
    if days is None or int(days) <= 0:
        raise RetentionPolicyError(
            f"data_retention_policies['{category}'].retention_days is "
            f"{days!r} (non-positive) — refusing (fail-closed)"
        )
    return int(days)


def _effective_retention(db) -> Tuple[int, Dict[str, int]]:
    """Most-conservative window across the person-subject categories, fail-closed."""
    resolved = {cat: _resolve_retention_days(db, cat) for cat in _SUBJECT_RETENTION_CATEGORIES}
    return max(resolved.values()), resolved


def _subject_app_ids(db, client_id: str) -> List[str]:
    return [
        (r["id"] if not isinstance(r, tuple) else r[0])
        for r in db.execute(
            "SELECT id FROM applications WHERE client_id = ?", (client_id,)
        ).fetchall()
    ]


def _subject_row_count(db, table: str, client_id: str, app_ids: List[str]) -> int:
    """Rows in `table` belonging to this subject (via client_id or application_id)."""
    try:
        if table == "clients":
            row = db.execute("SELECT COUNT(*) AS c FROM clients WHERE id = ?", (client_id,)).fetchone()
        elif _has_column(db, table, "client_id"):
            row = db.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE client_id = ?", (client_id,)).fetchone()
        elif _has_column(db, table, "application_id"):
            if not app_ids:
                return 0
            placeholders = ",".join("?" for _ in app_ids)
            row = db.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE application_id IN ({placeholders})",
                tuple(app_ids),
            ).fetchone()
        else:
            return 0
        return int(row["c"] if not isinstance(row, tuple) else row[0]) if row else 0
    except Exception:
        return 0


def _discover_subject_linked_tables(db) -> List[str]:
    """Every table carrying a subject identifier (client_id / application_id).

    The ledger is complete-by-construction: a NEW table with application_id
    automatically surfaces (as deferred until given an explicit rule), so
    silently-retained subject data is structurally impossible.
    """
    linked = []
    for table in _list_tables(db):
        if table in _NON_SUBJECT_TABLES:
            continue
        if table == "clients" or _has_column(db, table, "client_id") or _has_column(db, table, "application_id"):
            linked.append(table)
    return sorted(set(linked))


# ── Ledger ───────────────────────────────────────────────────────────────────

def build_erasure_ledger(db, client_id: str) -> Dict[str, Any]:
    """Complete per-table erase / retain / defer disposition for a subject.

    Raises RetentionPolicyError (fail-closed) if a required retention policy is
    missing/empty/invalid. Every subject-linked table holding this subject's
    rows appears with an explicit disposition — nothing is silently omitted.
    """
    effective_days, resolved_categories = _effective_retention(db)
    app_ids = _subject_app_ids(db, client_id)
    entries: List[Dict[str, Any]] = []

    for table in _discover_subject_linked_tables(db):
        count = _subject_row_count(db, table, client_id, app_ids)
        if count == 0:
            entries.append({"table": table, "rows": 0, "disposition": "not_applicable"})
            continue
        if table in _ERASABLE_TABLES:
            entries.append({
                "table": table, "rows": count, "disposition": "erasable",
                "retention_days": effective_days,
            })
        elif table in _RETAINED_REQUIRED_TABLES:
            meta = _RETAINED_REQUIRED_TABLES[table]
            entries.append({
                "table": table, "rows": count,
                "disposition": "retained_under_legal_obligation",
                "category": meta["category"],
                "legal_basis": meta["legal_basis"],
                "retention_days": resolved_categories.get(meta["category"])
                or _resolve_retention_days(db, meta["category"]),
            })
        else:
            # Subject-linked but no erase/retain rule yet → honest gap.
            entries.append({
                "table": table, "rows": count,
                "disposition": "deferred_not_implemented",
                "detail": "carries subject data but has no erasure/retention rule "
                          "(e.g. document files/S3, narrative PII) — must be built "
                          "before a live erasure can complete for this subject",
            })

    deferred = [e for e in entries if e["disposition"] == "deferred_not_implemented"]
    return {
        "client_id": client_id,
        "effective_retention_days": effective_days,
        "resolved_categories": resolved_categories,
        "entries": entries,
        "deferred_tables": [e["table"] for e in deferred],
        "complete": not deferred,
    }


def plan_subject_erasure(db, client_id: str) -> Dict[str, Any]:
    """Read-only: application retention classification + the complete ledger.

    An application is RETAINED while inside the (fail-closed, category-keyed) AML
    retention window measured from its relationship-end reference date. If no
    reference date can be determined the record is conservatively RETAINED.
    """
    effective_days, _ = _effective_retention(db)
    now = _now()
    has_closed = _has_column(db, "applications", "closed_at")
    cols = "id, ref, status, decided_at, closed_at, updated_at, created_at" if has_closed \
        else "id, ref, status, decided_at, updated_at, created_at"
    apps = [dict(r) for r in db.execute(
        f"SELECT {cols} FROM applications WHERE client_id = ?", (client_id,)
    ).fetchall()]

    classified = []
    for app in apps:
        ref_raw = app.get("decided_at") or app.get("closed_at") or app.get("updated_at") or app.get("created_at")
        ref_dt = _parse_dt(ref_raw)
        if ref_dt is None:
            retained, reason, age_days = True, "no_reference_date_conservatively_retained", None
        else:
            age_days = (now - ref_dt).days
            if age_days < effective_days:
                retained, reason = True, f"within_aml_retention ({age_days}/{effective_days} days)"
            else:
                retained, reason = False, f"outside_aml_retention ({age_days}/{effective_days} days)"
        classified.append({
            "application_id": app.get("id"), "ref": app.get("ref"),
            "status": app.get("status"), "reference_date": ref_raw,
            "age_days": age_days, "retained": retained, "reason": reason,
        })

    ledger = build_erasure_ledger(db, client_id)
    return {
        "client_id": client_id,
        "retention_days": effective_days,
        "applications": classified,
        "erasable_application_ids": [c["application_id"] for c in classified if not c["retained"]],
        "retained_application_ids": [c["application_id"] for c in classified if c["retained"]],
        "fully_erasable": bool(classified) and all(not c["retained"] for c in classified),
        "ledger": ledger,
    }


# ── Erasure log (expanded, PG-correct) ───────────────────────────────────────

def _ensure_erasure_log_table(db) -> None:
    is_pg = getattr(db, "is_postgres", False)
    ts_type = "TIMESTAMP" if is_pg else "TEXT"
    ts_default = "CURRENT_TIMESTAMP" if is_pg else "(datetime('now'))"
    bool_type = "BOOLEAN" if is_pg else "INTEGER"
    bool_false = "FALSE" if is_pg else "0"
    db.executescript(f"""
    CREATE TABLE IF NOT EXISTS gdpr_erasure_log (
        id TEXT PRIMARY KEY,
        executed_at {ts_type} NOT NULL DEFAULT {ts_default},
        dsar_request_id TEXT,
        client_id TEXT,
        application_id TEXT,
        requested_by TEXT,
        action TEXT NOT NULL,
        outcome TEXT,
        category TEXT,
        tables_affected TEXT,
        disposition TEXT,
        dry_run {bool_type} NOT NULL DEFAULT {bool_false},
        retention_overridden {bool_type} NOT NULL DEFAULT {bool_false},
        retention_basis TEXT,
        override_reason TEXT,
        note TEXT
    );
    """)


def _log_erasure(db, *, client_id, application_id, requested_by, action,
                 outcome=None, category=None, tables_affected=None, disposition=None,
                 dry_run=False, retention_overridden=False, retention_basis=None,
                 override_reason=None, note=None, dsar_request_id=None):
    db.execute(
        "INSERT INTO gdpr_erasure_log "
        "(id, dsar_request_id, client_id, application_id, requested_by, action, outcome, "
        " category, tables_affected, disposition, dry_run, retention_overridden, "
        " retention_basis, override_reason, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            secrets.token_hex(8), dsar_request_id, client_id, application_id, requested_by, action,
            outcome, category,
            ",".join(tables_affected) if tables_affected else "",
            json.dumps(disposition, default=str) if disposition is not None else None,
            bool(dry_run), bool(retention_overridden), retention_basis, override_reason, note,
        ),
    )


def _anonymise_application(db, application_id: str) -> List[str]:
    affected = []
    for table, spec in _ERASABLE_TABLES.items():
        if spec["scope"] != "application":
            continue
        cols = [c for c in spec["columns"] if _has_column(db, table, c)]
        if not cols:
            continue
        assignments = ", ".join(f"{c} = ?" for c in cols)
        values = [json.dumps({"erased": True}) if c in spec["json_columns"] else _REDACTION_TOKEN for c in cols]
        key = "id" if table == "applications" else "application_id"
        db.execute(f"UPDATE {table} SET {assignments} WHERE {key} = ?", (*values, application_id))
        affected.append(table)
    return affected


def execute_subject_erasure(
    db,
    client_id: str,
    *,
    requested_by: str,
    dry_run: bool = True,
    override_retention: bool = False,
    override_reason: Optional[str] = None,
    dsar_request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Erase (anonymise) a subject's PII for records outside AML retention.

    SAFETY: defaults to dry_run=True (no changes). Fail-closed on missing
    retention policy. Live-path invariant: refuses as INCOMPLETE (never
    'executed', never sets erasure evidence) while any subject-linked table
    holding this subject's rows is deferred/unhandled. Records inside the AML
    retention window are refused unless override_retention=True WITH a written
    override_reason. Every action — refusals, overrides, executions — is logged.
    Does not commit; the caller owns the transaction.
    """
    plan = plan_subject_erasure(db, client_id)  # raises RetentionPolicyError (fail-closed)
    ledger = plan["ledger"]

    if dry_run:
        plan["action"] = "dry_run"
        plan["changes_made"] = False
        return plan

    _ensure_erasure_log_table(db)

    if override_retention and not (override_reason and override_reason.strip()):
        _log_erasure(db, client_id=client_id, application_id=None, requested_by=requested_by,
                     action="refused_override_without_reason", outcome="refused",
                     dsar_request_id=dsar_request_id, retention_overridden=True,
                     note="override_retention requested without a written override_reason")
        return {**plan, "action": "refused", "changes_made": False,
                "error": "override_retention requires a written override_reason"}

    # LIVE-PATH INVARIANT: cannot truthfully "complete" while subject data sits
    # in deferred/unhandled tables — refuse as incomplete (audit live invariant).
    if ledger["deferred_tables"]:
        _log_erasure(db, client_id=client_id, application_id=None, requested_by=requested_by,
                     action="refused_incomplete", outcome="refused",
                     dsar_request_id=dsar_request_id, disposition=ledger["entries"],
                     note="deferred tables hold subject data: " + ",".join(ledger["deferred_tables"]))
        return {**plan, "action": "refused_incomplete", "changes_made": False,
                "deferred_tables": ledger["deferred_tables"],
                "error": "erasure cannot complete while subject data remains in unhandled "
                         "tables (physical file/S3 deletion and narrative redaction are "
                         "deferred follow-ups — PC-4)"}

    erased_apps, retained_refused, tables_touched = [], [], set()
    for entry in plan["applications"]:
        app_id = entry["application_id"]
        if entry["retained"] and not override_retention:
            retained_refused.append(app_id)
            _log_erasure(db, client_id=client_id, application_id=app_id, requested_by=requested_by,
                         action="retained_refused", outcome="retained",
                         dsar_request_id=dsar_request_id, category="client_pii",
                         retention_basis="AML/CFT record-retention obligation", note=entry["reason"])
            continue
        affected = _anonymise_application(db, app_id)
        tables_touched.update(affected)
        erased_apps.append(app_id)
        _log_erasure(db, client_id=client_id, application_id=app_id, requested_by=requested_by,
                     action="erased", outcome="erased", dsar_request_id=dsar_request_id,
                     tables_affected=affected,
                     retention_overridden=bool(entry["retained"] and override_retention),
                     override_reason=override_reason if entry["retained"] else None,
                     note=entry["reason"])

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
                     outcome="erased" if client_erased else "skipped",
                     dsar_request_id=dsar_request_id,
                     tables_affected=["clients"] if client_erased else [])

    # Only a fully-satisfied request (nothing refused/deferred) counts as executed.
    fully_done = not retained_refused
    return {
        **plan,
        "action": "executed" if fully_done else "partial",
        "changes_made": bool(erased_apps or client_erased),
        "erased_application_ids": erased_apps,
        "retained_refused_application_ids": retained_refused,
        "client_account_erased": client_erased,
        "tables_affected": sorted(tables_touched),
        "erasure_executed": bool(fully_done and (erased_apps or client_erased)),
    }
