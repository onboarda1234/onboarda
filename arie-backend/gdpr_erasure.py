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

Also deferred (PC-4): the residual-PII guard verifies the taxonomy-*classified*
columns (``party_utils.PII_FIELDS_*`` + registry originals); discovering an
*unclassified* PII-shaped column via a live column-name probe, and erasing weak
positional identifiers not classified as PII (e.g. ``person_key``), are follow-ups.
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
_ERASED_JSON = json.dumps({"erased": True})
# The verbatim registry record (name/DOB/address) is cached in this JSON column
# on directors/ubos/intermediaries (party_utils.hydrate_party_record reads
# source_metadata_json.registry_originals back out for display). It is PII and
# MUST be erased even though the flat name columns are erased separately.
_REGISTRY_ORIGINAL_JSON = "source_metadata_json"

# ── Retention categories that govern a person-subject erasure ────────────────
# Each is resolved from data_retention_policies and FAILS CLOSED if absent.
# The effective retention window is the MOST CONSERVATIVE (longest) of these.
_SUBJECT_RETENTION_CATEGORIES = ("client_pii", "application_data")

# ── Authoritative PII taxonomy ───────────────────────────────────────────────
# The erase-column set is driven by the SAME taxonomy the app uses to *encrypt*
# PII on write (party_utils.PII_FIELDS_*), so the eraser cannot silently drift
# out of sync with what the product classifies as personal data (adversarial
# B1). A hardcoded snapshot is the fallback if the import is unavailable.
try:  # pragma: no cover - exercised via the taxonomy-parity test
    import party_utils as _party_utils
    _TAXONOMY_PII: Dict[str, List[str]] = {
        "directors": list(getattr(_party_utils, "PII_FIELDS_DIRECTORS", []) or []),
        "ubos": list(getattr(_party_utils, "PII_FIELDS_UBOS", []) or []),
        "intermediaries": list(getattr(_party_utils, "PII_FIELDS_INTERMEDIARIES", []) or []),
        "applications": list(getattr(_party_utils, "PII_FIELDS_APPLICATIONS", []) or []),
    }
except Exception:  # pragma: no cover - defensive fallback
    _TAXONOMY_PII = {
        "directors": ["passport_number", "nationality", "id_number",
                      "country_of_residence", "residential_address",
                      "professional_profile_url"],
        "ubos": ["passport_number", "nationality", "country_of_residence",
                 "residential_address", "professional_profile_url"],
        "intermediaries": ["owned_or_controlled_by"],
        "applications": ["pep_flags"],
    }

# Columns that identify the subject account rather than a document/onboarding
# record; discovery recognises any of these as a subject link (adversarial N2:
# company_intake_sessions links via client_user_id, not client_id).
_SUBJECT_CLIENT_FK_COLUMNS = ("client_id", "client_user_id")
_SUBJECT_APPLICATION_FK_COLUMNS = ("application_id",)
_SUBJECT_APPLICATION_REF_COLUMNS = ("application_ref",)

# Non-FK, target-oriented subject linkage discovered by schema sweep. These
# must be counted with exact target matches only; free-text detail/narrative is
# PII content, not a safe join key.
_TARGET_LINKED_RETAINED_TABLES = frozenset({"audit_log"})


def _erasable_columns(table: str) -> List[str]:
    """The full set of PII columns to anonymise for `table`: the explicit
    structural/identity columns below UNIONed with the authoritative taxonomy
    and the registry-originals JSON. Missing columns are filtered at write time
    by _has_column, so listing a not-yet-migrated column is safe."""
    base = _ERASABLE_TABLES.get(table, {}).get("columns", [])
    extra = list(_TAXONOMY_PII.get(table, [])) + [_REGISTRY_ORIGINAL_JSON]
    seen, out = set(), []
    for col in list(base) + extra:
        if col not in seen:
            seen.add(col)
            out.append(col)
    return out


# ── Erasable tables: PII columns anonymised per table. Structural/FK columns
# (ids, application_id) are preserved so retained AML records that reference
# them stay internally consistent. The `columns` here are the explicit
# identity/onboarding fields; the authoritative taxonomy (party_utils) and the
# registry-originals JSON are folded in by _erasable_columns() so no
# taxonomy-classified PII column is ever missed (adversarial B1).
_ERASABLE_TABLES: Dict[str, Dict[str, Any]] = {
    "applications": {
        "scope": "application",
        "columns": ["company_name", "brn", "ownership_structure",
                    "prescreening_data", "decision_notes", "pre_approval_notes",
                    "pep_flags"],
        "json_columns": {"prescreening_data", "pep_flags", _REGISTRY_ORIGINAL_JSON},
    },
    "directors": {
        "scope": "application",
        "columns": ["first_name", "last_name", "full_name", "nationality",
                    "passport_number", "id_number", "date_of_birth",
                    "country_of_residence", "residential_address",
                    "pep_declaration"],
        "json_columns": {"pep_declaration", _REGISTRY_ORIGINAL_JSON},
    },
    "ubos": {
        "scope": "application",
        "columns": ["first_name", "last_name", "full_name", "nationality",
                    "passport_number", "date_of_birth", "country_of_residence",
                    "residential_address", "pep_declaration"],
        "json_columns": {"pep_declaration", _REGISTRY_ORIGINAL_JSON},
    },
    "intermediaries": {
        "scope": "application",
        "columns": ["entity_name", "registered_address", "registration_number",
                    "owned_or_controlled_by"],
        "json_columns": {_REGISTRY_ORIGINAL_JSON},
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
                  "legal_basis": "regulatory accountability / audit trail integrity"},
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
# deferred bucket so they do not spuriously block a live erasure). This list is
# deliberately CONSERVATIVE: a table is excluded only if it genuinely holds no
# subject personal data. Anything holding subject PII must NOT be here — it must
# surface (as erasable or deferred) so the live-path invariant can act on it.
# NOTE: client_sessions is intentionally NOT excluded — its form_data blob holds
# save-and-resume PII (contact email, names, DOB, nationality, ownership), so it
# must surface as deferred and block a "completed" erasure (adversarial F1).
_NON_SUBJECT_TABLES = frozenset({
    "schema_version", "schema_migrations", "data_migration_markers",
    "data_retention_policies", "supervisor_audit_migrations",
    "risk_config", "ai_agents", "ai_checks", "system_settings",
    "revoked_tokens", "enhanced_requirement_rules",
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


def _columns_present(db, table: str, columns) -> List[str]:
    """Strict variant of _has_column: return the subset of `columns` present on
    `table`, and PROPAGATE any probe error instead of swallowing it. Used on the
    fail-closed count/discovery paths so a schema-probe failure surfaces as a
    _RowCountError (→ deferred) rather than silently undercounting to 0
    (adversarial N1)."""
    wanted = list(columns)
    if getattr(db, "is_postgres", False):
        rows = db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        present = {(r["column_name"] if not isinstance(r, tuple) else r[0]) for r in rows}
    else:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        present = {(r["name"] if not isinstance(r, tuple) else r[1]) for r in rows}
    return [c for c in wanted if c in present]


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


def _subject_app_refs(db, client_id: str) -> List[str]:
    if not _has_column(db, "applications", "ref"):
        return []
    return [
        (r["ref"] if not isinstance(r, tuple) else r[0])
        for r in db.execute(
            "SELECT ref FROM applications WHERE client_id = ? AND ref IS NOT NULL",
            (client_id,),
        ).fetchall()
    ]


class _RowCountError(Exception):
    """A subject-row count could not be computed — must fail CLOSED, not to 0."""


def _target_linked_audit_log_row_count(db, app_ids: List[str], app_refs: List[str]) -> int:
    """Rows in audit_log linked to this subject by exact target.

    audit_log is target-oriented, not FK-oriented. Live code writes application
    refs and sometimes ids into audit_log.target; audit_log.detail is free text
    and must NEVER be used as a linkage key because substring matching can pull
    in another subject's records.
    """
    try:
        present = _columns_present(db, "audit_log", ("target",))
        if "target" not in present:
            return 0
        targets = list(dict.fromkeys([*app_refs, *app_ids]))
        if not targets:
            return 0
        ph = ",".join("?" for _ in targets)
        row = db.execute(
            f"SELECT COUNT(*) AS c FROM audit_log WHERE target IN ({ph})",
            tuple(targets),
        ).fetchone()
        return int(row["c"] if not isinstance(row, tuple) else row[0]) if row else 0
    except Exception as exc:
        raise _RowCountError(f"cannot count exact target-linked audit_log rows: {exc}") from exc


def _subject_row_count(db, table: str, client_id: str, app_ids: List[str],
                       app_refs: Optional[List[str]] = None) -> int:
    """Rows in `table` belonging to this subject.

    ORs every subject link that exists — any of client_id / client_user_id
    (adversarial N2) and application_id — so a row linked by only one of them
    (e.g. client_id NULL but application_id set) is never undercounted to 0
    (adversarial F2). Raises _RowCountError on ANY probe/query failure so the
    ledger fails closed rather than silently treating the table as empty
    (adversarial F3/N1: the strict _columns_present propagates probe errors).
    """
    try:
        app_refs = app_refs or []
        if table == "audit_log":
            return _target_linked_audit_log_row_count(db, app_ids, app_refs)
        if table == "clients":
            row = db.execute("SELECT COUNT(*) AS c FROM clients WHERE id = ?", (client_id,)).fetchone()
            return int(row["c"] if not isinstance(row, tuple) else row[0]) if row else 0
        present = _columns_present(
            db,
            table,
            (*_SUBJECT_CLIENT_FK_COLUMNS, *_SUBJECT_APPLICATION_FK_COLUMNS,
             *_SUBJECT_APPLICATION_REF_COLUMNS),
        )
        preds: List[str] = []
        params: List[Any] = []
        for fk in _SUBJECT_CLIENT_FK_COLUMNS:
            if fk in present:
                preds.append(f"{fk} = ?")
                params.append(client_id)
        if "application_id" in present and app_ids:
            ph = ",".join("?" for _ in app_ids)
            preds.append(f"application_id IN ({ph})")
            params.extend(app_ids)
        if "application_ref" in present and app_refs:
            ph = ",".join("?" for _ in app_refs)
            preds.append(f"application_ref IN ({ph})")
            params.extend(app_refs)
        if not preds:
            return 0
        row = db.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE " + " OR ".join(preds),
            tuple(params),
        ).fetchone()
        return int(row["c"] if not isinstance(row, tuple) else row[0]) if row else 0
    except Exception as exc:
        raise _RowCountError(f"cannot count subject rows in {table}: {exc}") from exc


def _discover_subject_linked_tables(db) -> List[str]:
    """Every table carrying a subject identifier (client_id / client_user_id /
    application_id).

    The ledger is complete-by-construction: a NEW table with a subject FK
    automatically surfaces (as deferred until given an explicit rule), so
    silently-retained subject data is structurally impossible. A table whose
    columns cannot be probed is INCLUDED (fail-closed) so the count step
    evaluates it (and, if that also fails, marks it deferred).
    """
    linked = []
    for table in _list_tables(db):
        if table in _NON_SUBJECT_TABLES:
            continue
        if table == "clients":
            linked.append(table)
            continue
        if table in _TARGET_LINKED_RETAINED_TABLES:
            linked.append(table)
            continue
        try:
            present = _columns_present(
                db,
                table,
                (*_SUBJECT_CLIENT_FK_COLUMNS, *_SUBJECT_APPLICATION_FK_COLUMNS,
                 *_SUBJECT_APPLICATION_REF_COLUMNS),
            )
        except Exception:
            linked.append(table)  # fail-closed: cannot probe ⇒ evaluate downstream
            continue
        if present:
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
    app_refs = _subject_app_refs(db, client_id)
    entries: List[Dict[str, Any]] = []

    for table in _discover_subject_linked_tables(db):
        try:
            count = _subject_row_count(db, table, client_id, app_ids, app_refs)
        except _RowCountError as exc:
            # Fail-closed: a table whose subject-row count cannot be computed
            # BLOCKS a live completion rather than vanishing to not_applicable.
            entries.append({
                "table": table, "rows": None, "disposition": "deferred_not_implemented",
                "detail": f"subject-row count unavailable ({exc}) — fail-closed",
            })
            continue
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
            # The retained disposition is governed by the cited LEGAL BASIS, not
            # a numeric window; a missing peripheral-category policy must NOT
            # abort read-only planning for any subject who merely has a SAR or an
            # audited row (adversarial N4). Window is reported as None (unknown).
            try:
                retention_days = resolved_categories.get(meta["category"]) \
                    or _resolve_retention_days(db, meta["category"])
            except RetentionPolicyError:
                retention_days = None
            entries.append({
                "table": table, "rows": count,
                "disposition": "retained_under_legal_obligation",
                "category": meta["category"],
                "legal_basis": meta["legal_basis"],
                "retention_days": retention_days,
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


def _savepoint(db, name: str) -> None:
    db.execute(f"SAVEPOINT {name}")


def _rollback_to_savepoint(db, name: str) -> None:
    db.execute(f"ROLLBACK TO SAVEPOINT {name}")


def _release_savepoint(db, name: str) -> None:
    db.execute(f"RELEASE SAVEPOINT {name}")


def _is_json_column(db, table: str, column: str) -> bool:
    """Whether `column` is a JSON/JSONB type. Best-effort (False on probe error).

    Used to choose the erased marker by ACTUAL column type rather than only the
    hand-maintained json_columns set, so a taxonomy PII field that happens to be
    JSONB but was never registered in json_columns cannot get a scalar
    '[ERASED]' written to it (which would raise on PostgreSQL mid-erase — NIT-5).
    """
    try:
        if getattr(db, "is_postgres", False):
            row = db.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = ? AND column_name = ?",
                (table, column),
            ).fetchone()
            dtype = (row["data_type"] if row and not isinstance(row, tuple) else (row[0] if row else "")) or ""
        else:
            dtype = ""
            for r in db.execute(f"PRAGMA table_info({table})").fetchall():
                name = r["name"] if not isinstance(r, tuple) else r[1]
                if name == column:
                    dtype = (r["type"] if not isinstance(r, tuple) else r[2]) or ""
                    break
        return "json" in str(dtype).lower()
    except Exception:
        return False


def _anonymise_application(db, application_id: str) -> List[str]:
    affected = []
    for table, spec in _ERASABLE_TABLES.items():
        if spec["scope"] != "application":
            continue
        cols = [c for c in _erasable_columns(table) if _has_column(db, table, c)]
        if not cols:
            continue
        assignments = ", ".join(f"{c} = ?" for c in cols)
        # Choose the erased marker by actual type OR explicit json_columns intent,
        # so a JSONB column never receives a scalar token (NIT-5, fail-safe).
        values = [
            _ERASED_JSON if (c in spec["json_columns"] or _is_json_column(db, table, c))
            else _REDACTION_TOKEN
            for c in cols
        ]
        key = "id" if table == "applications" else "application_id"
        db.execute(f"UPDATE {table} SET {assignments} WHERE {key} = ?", (*values, application_id))
        affected.append(table)
    return affected


# Values an already-erased PII column may legitimately hold. A residual is any
# subject value that is NONE of these. Columns are CAST to text so the guard
# works uniformly across scalar TEXT and PG JSONB/JSON columns (e.g.
# pep_declaration, prescreening_data) — a bare `jsonb = ''`/`jsonb LIKE ?` would
# raise on PostgreSQL and make the guard false-positive on every erasure.
_ERASED_SENTINEL_SQL = (
    "(CAST({col} AS TEXT) IS NULL OR CAST({col} AS TEXT) = '' "
    "OR CAST({col} AS TEXT) = ? OR CAST({col} AS TEXT) LIKE ? OR CAST({col} AS TEXT) LIKE ?)"
)
_ERASED_SENTINEL_PARAMS = (_REDACTION_TOKEN, '%"erased"%', "erased+%@erased.invalid")


def _residual_guard_columns(table: str) -> List[str]:
    """Every column that MUST read as tokenised after erasing `table`: the same
    taxonomy-driven set _erasable_columns() anonymises (plus the taxonomy and
    registry-originals JSON, which are already subsets of it).

    NOTE (scope, adversarial NB-1): because this equals the anonymised set, the
    guard is a WRITE-VERIFICATION backstop — it proves the anonymise actually
    cleared the KNOWN-PII columns (catching a no-op/failed UPDATE) and fails
    closed on any column it cannot verify. It does NOT discover an *unknown*
    (unclassified) PII column; that would need a live column-name probe and is a
    documented follow-up (PC-4), deliberately deferred to avoid false-positives
    on structural columns that would break every erasure.
    """
    cols = set(_erasable_columns(table)) | set(_TAXONOMY_PII.get(table, ())) | {_REGISTRY_ORIGINAL_JSON}
    return sorted(cols)


def _detect_residual_pii(db, erased_application_ids: List[str], client_erased: bool,
                         client_id: str) -> List[Dict[str, Any]]:
    """After anonymisation, verify every KNOWN-PII column actually reads erased.

    Write-path analogue of the complete-ledger guarantee: the ledger guarantees
    no subject TABLE is silently dropped; this guarantees the anonymise of each
    classified PII COLUMN actually took effect. Any surviving non-sentinel value
    — or a column that cannot be verified — makes the run fail-closed, so it can
    never be reported 'executed' (B1). (It verifies the classified set; catching
    an *unclassified* column is a deferred follow-up — see _residual_guard_columns.)"""
    findings: List[Dict[str, Any]] = []
    for table, spec in _ERASABLE_TABLES.items():
        if spec["scope"] == "application":
            if not erased_application_ids:
                continue
            key = "id" if table == "applications" else "application_id"
            id_scope = erased_application_ids
        else:  # client scope
            if not client_erased:
                continue
            key, id_scope = "id", [client_id]
        ph = ",".join("?" for _ in id_scope)
        for col in _residual_guard_columns(table):
            if not _has_column(db, table, col):
                continue
            predicate = _ERASED_SENTINEL_SQL.format(col=col)
            try:
                row = db.execute(
                    f"SELECT COUNT(*) AS c FROM {table} "
                    f"WHERE {key} IN ({ph}) AND NOT {predicate}",
                    (*id_scope, *_ERASED_SENTINEL_PARAMS),
                ).fetchone()
            except Exception as exc:
                # Cannot verify ⇒ fail-closed: treat as residual.
                findings.append({"table": table, "column": col, "rows": None,
                                 "detail": f"residual check failed: {exc}"})
                continue
            n = int(row["c"] if not isinstance(row, tuple) else row[0]) if row else 0
            if n > 0:
                findings.append({"table": table, "column": col, "rows": n})
    return findings


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

    savepoint_name = "gdpr_erasure_mutation"
    _savepoint(db, savepoint_name)
    erased_apps, retained_refused, tables_touched = [], [], set()
    client_erased = False
    residual: List[Dict[str, Any]] = []
    try:
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

        if not retained_refused:
            if _has_column(db, "clients", "email"):
                # Null any password-reset token/expiry too, so no reset artefact
                # survives an "erasure" (adversarial N5).
                reset_cols = [c for c in ("password_reset_token", "password_reset_expires")
                              if _has_column(db, "clients", c)]
                extra_set = "".join(f", {c} = NULL" for c in reset_cols)
                db.execute(
                    f"UPDATE clients SET email = ?, company_name = ?, password_hash = ?{extra_set} WHERE id = ?",
                    (f"erased+{client_id}@erased.invalid", _REDACTION_TOKEN, secrets.token_hex(32), client_id),
                )
                tables_touched.add("clients")
                client_erased = True
            _log_erasure(db, client_id=client_id, application_id=None, requested_by=requested_by,
                         action="client_account_erased" if client_erased else "client_account_skipped",
                         outcome="erased" if client_erased else "skipped",
                         dsar_request_id=dsar_request_id,
                         tables_affected=["clients"] if client_erased else [])

        # RESIDUAL-PII GUARD (adversarial B1): 'executed' must imply no known-PII
        # column survived. Re-read every taxonomy/erasable column on the rows we
        # just anonymised; any residual (or unverifiable column) fails the run
        # closed and rolls back the mutation savepoint.
        residual = _detect_residual_pii(db, erased_apps, client_erased, client_id)
        if residual:
            _rollback_to_savepoint(db, savepoint_name)
            _release_savepoint(db, savepoint_name)
            _log_erasure(db, client_id=client_id, application_id=None, requested_by=requested_by,
                         action="refused_residual_pii", outcome="refused_incomplete",
                         dry_run=False, dsar_request_id=dsar_request_id,
                         disposition=residual, tables_affected=[],
                         note="known-PII columns survived anonymisation: "
                              + ",".join(f"{f['table']}.{f['column']}" for f in residual))
            return {**plan, "action": "refused_incomplete",
                    "changes_made": False,
                    "erased_application_ids": [],
                    "retained_refused_application_ids": [],
                    "client_account_erased": False,
                    "tables_affected": [],
                    "residual_pii": residual,
                    "erasure_executed": False,
                    "error": "erasure incomplete: known-PII columns survived anonymisation; "
                             "mutation rolled back and completion refused (fail-closed — B1)"}
        _release_savepoint(db, savepoint_name)
    except Exception:
        try:
            _rollback_to_savepoint(db, savepoint_name)
            _release_savepoint(db, savepoint_name)
        except Exception:
            pass
        raise

    # Only a fully-satisfied request (nothing refused/deferred, no residual PII)
    # counts as executed. A distinct completion marker is written ONLY here,
    # carrying the subject client_id — it is the SOLE evidence gdpr.verify_dsar_
    # erasure_evidence accepts, so a partial run (which never reaches this) can
    # never be mistaken for a completed erasure (adversarial F4), and the
    # client_id bind prevents a shared correlation id from marking another
    # subject (F5).
    fully_done = (not retained_refused) and (not residual) and (bool(erased_apps) or client_erased)

    # (dry_run already returned above, so reaching here implies a live run.)
    if fully_done:
        _log_erasure(db, client_id=client_id, application_id=None, requested_by=requested_by,
                     action="erasure_completed", outcome="completed", dsar_request_id=dsar_request_id,
                     tables_affected=sorted(tables_touched),
                     note="all subject applications erased; no records refused, deferred, or residual")
    return {
        **plan,
        "action": "executed" if fully_done else "partial",
        "changes_made": bool(erased_apps or client_erased),
        "erased_application_ids": erased_apps,
        "retained_refused_application_ids": retained_refused,
        "client_account_erased": client_erased,
        "residual_pii": residual,
        "tables_affected": sorted(tables_touched),
        "erasure_executed": bool(fully_done),
    }
