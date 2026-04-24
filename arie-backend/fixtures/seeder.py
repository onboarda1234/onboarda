"""Idempotent fixture seeder (Path A: real-schema-adapted).

All inserts target columns that exist in BOTH the SQLite (dev) and
PostgreSQL (production/staging) schemas declared in
``arie-backend/db.py``. No schema changes are made.

Idempotency keys (per registry conventions):
- applications: ``id`` (deterministic ``f1xed...`` hex)
- monitoring_alerts: ``source_reference`` (e.g. FIX_SCEN01_ALERT)
- periodic_reviews: ``trigger_reason`` LIKE 'FIX_SCENxx_REVIEW%'
- edd_cases: ``trigger_notes`` LIKE 'FIX_SCENxx_EDD%'
- compliance_memos: per-application (one fixture memo per scenario)
- documents: ``file_path`` ('fixture://FIX_SCENxx_DOC_<purpose>')

Every mutating call writes a paired ``fixture.*`` row to
``audit_log`` via ``fixtures.audit.make_fixture_audit_writer``.

Transaction model:
- The seeder does NOT call db.commit() inside per-scenario helpers.
- ``seed_all`` opens one transaction, runs all scenarios, then either
  commits (apply) or rolls back (dry-run). On any exception the
  transaction is rolled back via ``db.conn.rollback()``.

This module bypasses the API layer by design. It does not invoke
screening, rule evaluation, or notifications.
"""

import json
import logging
import re
import secrets
from typing import Any, Dict, List, Optional

from db import get_db, init_db, USE_POSTGRESQL
from rule_engine import compute_risk_score
from fixtures.audit import make_fixture_audit_writer
from fixtures.registry import (
    APP_ID,
    APP_REF,
    SCENARIOS,
    ScenarioDef,
    _now_iso,
)

logger = logging.getLogger(__name__)

# Sentinel embedded in periodic_reviews.trigger_reason and edd_cases.trigger_notes
# after the leading fixture marker. Everything between the colon and end-of-string
# is a JSON payload. Tooling parses it back as JSON; the leading marker keeps the
# ``LIKE 'FIX_SCENxx_<KIND>%'`` idempotency lookup working.
REVIEW_PAYLOAD_SENTINEL = "FIX_REVIEW_JSON:"
EDD_PAYLOAD_SENTINEL = "FIX_EDD_JSON:"
DISMISSAL_PAYLOAD_SENTINEL = "FIX_PAYLOAD_JSON:"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _new_text_id() -> str:
    """Generate a TEXT primary-key value (matches the SQLite/PG default
    of ``lower(hex(randomblob(8)))`` / ``encode(gen_random_bytes(8),'hex')``)."""
    return secrets.token_hex(8)


def _safe_compute_risk_score(scen: ScenarioDef) -> float:
    """Call the real ``compute_risk_score(app_data, config_override=None)``.

    The current ``compute_risk_score`` signature takes a single
    ``app_data`` dict (see arie-backend/rule_engine.py:617). It depends
    on ``load_risk_config()`` reading the DB; in a fresh environment
    that lookup may return defaults. We pass a minimal dict that covers
    the fields the engine reads. On any unexpected error we fall back
    to a coarse band-based score so the seeder never aborts on a
    rule-engine edge case.
    """
    app_data = {
        "country": scen.country,
        "country_of_incorporation": scen.country,
        "sector": scen.sector,
        "entity_type": scen.entity_type,
        "ownership_structure": scen.ownership_structure,
        "company_name": scen.company_name,
    }
    try:
        result = compute_risk_score(app_data)
        if isinstance(result, dict) and "score" in result:
            return float(result["score"])
        if isinstance(result, (int, float)):
            return float(result)
    except Exception as exc:
        logger.warning(
            "compute_risk_score fell back for %s: %s", scen.code, exc
        )
    return {"LOW": 25.0, "MEDIUM": 50.0, "HIGH": 65.0, "VERY_HIGH": 85.0}.get(
        scen.risk_level, 50.0
    )


def _fetch_id(db, sql: str, params: tuple):
    """Run a ``SELECT id FROM ...`` style query and return the id (or None).

    Callers MUST select the ``id`` column (alone or first). DBConnection
    returns dicts on both backends; we read the explicit ``id`` key when
    present and fall back to the first value otherwise.
    """
    row = db.execute(sql, params).fetchone()
    if not row:
        return None
    if isinstance(row, dict) and "id" in row:
        return row["id"]
    return list(row.values())[0]


def _insert_returning_id(db, table: str, cols: str, values: tuple) -> int:
    """INSERT and return the new SERIAL/AUTOINCREMENT id.

    Uses backend-native placeholders directly against the underlying
    DB-API connection (``db.conn``) rather than relying on the
    ``DBConnection._translate_query`` ``?``-to-``%s`` text rewrite.
    String-rewriting placeholders is fragile when any value contains a
    literal ``%`` or ``?`` character; using the driver's own paramstyle
    eliminates that class of bug. We also avoid touching the private
    ``DBConnection._cursor`` attribute.

    - Postgres: ``%s`` placeholders + ``RETURNING id``.
    - SQLite:   ``?`` placeholders + ``cursor.lastrowid``.

    Caller is responsible for ensuring the table has an integer PK.
    """
    if USE_POSTGRESQL:
        placeholders = ", ".join(["%s"] * len(values))
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) RETURNING id"
        cursor = db.conn.cursor()
        try:
            cursor.execute(sql, values)
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            raise RuntimeError(f"INSERT ... RETURNING id produced no row for {table}")
        # psycopg2 default cursor returns a tuple; RealDictCursor returns a dict.
        if isinstance(row, dict):
            return row["id"]
        return row[0]
    placeholders = ", ".join(["?"] * len(values))
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    cursor = db.conn.cursor()
    try:
        cursor.execute(sql, values)
        last_id = cursor.lastrowid
    finally:
        cursor.close()
    if last_id is None:
        raise RuntimeError(f"INSERT into {table} did not produce a lastrowid")
    return last_id


# ---------------------------------------------------------------------------
# applications
# ---------------------------------------------------------------------------

def _upsert_application(db, audit, scen: ScenarioDef) -> str:
    app_id = APP_ID[scen.code]
    ref = APP_REF[scen.code]
    risk_dimensions = json.dumps(
        {
            "country": scen.country,
            "sector": scen.sector,
            "ownership": scen.ownership_structure,
            "fixture": scen.code,
        }
    )
    risk_score = _safe_compute_risk_score(scen)

    existing = _fetch_id(db, "SELECT id FROM applications WHERE id = ?", (app_id,))
    now = _now_iso()
    prescreening = json.dumps({"fixture": scen.code, "source": "fixtures.seeder"})

    # NB: applications.status CHECK enum does NOT include 'active'. We use
    # 'in_review' which is a real, valid value for a fixture under monitoring.
    status_value = "in_review"

    if existing:
        db.execute(
            "UPDATE applications SET ref=?, company_name=?, brn=?, country=?, "
            "sector=?, entity_type=?, ownership_structure=?, prescreening_data=?, "
            "risk_score=?, risk_level=?, risk_dimensions=?, onboarding_lane=?, "
            "status=?, is_fixture=1, updated_at=? WHERE id=?",
            (
                ref,
                scen.company_name,
                f"BRN-FIX-{scen.code}",
                scen.country,
                scen.sector,
                scen.entity_type,
                scen.ownership_structure,
                prescreening,
                risk_score,
                scen.risk_level,
                risk_dimensions,
                "standard",
                status_value,
                now,
                app_id,
            ),
        )
        audit(
            action="upsert_application",
            target=f"application:{app_id}",
            detail=f"Updated fixture application for {scen.code} ({scen.company_name})",
            after_state={"id": app_id, "ref": ref, "company_name": scen.company_name},
        )
        return app_id

    db.execute(
        "INSERT INTO applications "
        "(id, ref, company_name, brn, country, sector, entity_type, "
        "ownership_structure, prescreening_data, risk_score, risk_level, "
        "risk_dimensions, onboarding_lane, status, is_fixture, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
        (
            app_id,
            ref,
            scen.company_name,
            f"BRN-FIX-{scen.code}",
            scen.country,
            scen.sector,
            scen.entity_type,
            scen.ownership_structure,
            prescreening,
            risk_score,
            scen.risk_level,
            risk_dimensions,
            "standard",
            status_value,
            now,
            now,
        ),
    )
    audit(
        action="insert_application",
        target=f"application:{app_id}",
        detail=f"Inserted fixture application for {scen.code} ({scen.company_name})",
        after_state={"id": app_id, "ref": ref, "company_name": scen.company_name},
    )
    return app_id


# ---------------------------------------------------------------------------
# monitoring_alerts
# ---------------------------------------------------------------------------

def _encode_alert_officer_notes(spec) -> Optional[str]:
    """Pack dismissal_payload (drafted but no column) into officer_notes."""
    if spec.dismissal_payload:
        return f"{DISMISSAL_PAYLOAD_SENTINEL}{json.dumps(spec.dismissal_payload, sort_keys=True, default=str)}"
    return spec.officer_notes


def _upsert_alert(db, audit, app_id: str, scen: ScenarioDef) -> Optional[int]:
    if not scen.alert_spec:
        return None
    spec = scen.alert_spec
    existing_id = _fetch_id(
        db,
        "SELECT id FROM monitoring_alerts WHERE source_reference = ?",
        (spec.source_reference,),
    )
    officer_notes = _encode_alert_officer_notes(spec)

    if existing_id:
        db.execute(
            "UPDATE monitoring_alerts SET application_id=?, client_name=?, "
            "alert_type=?, severity=?, summary=?, status=?, "
            "officer_action=?, officer_notes=? WHERE id=?",
            (
                app_id,
                scen.company_name,
                spec.alert_type,
                spec.severity,
                spec.summary,
                spec.status,
                spec.officer_action,
                officer_notes,
                existing_id,
            ),
        )
        audit(
            action="update_alert",
            target=f"monitoring_alert:{existing_id}",
            detail=f"Updated fixture alert for {scen.code}",
            after_state={"id": existing_id, "source_reference": spec.source_reference},
        )
        return existing_id

    new_id = _insert_returning_id(
        db,
        "monitoring_alerts",
        "application_id, client_name, alert_type, severity, detected_by, "
        "summary, source_reference, status, officer_action, officer_notes",
        (
            app_id,
            scen.company_name,
            spec.alert_type,
            spec.severity,
            "fixture_seed",
            spec.summary,
            spec.source_reference,
            spec.status,
            spec.officer_action,
            officer_notes,
        ),
    )
    audit(
        action="insert_alert",
        target=f"monitoring_alert:{new_id}",
        detail=f"Seeded fixture alert for {scen.code}",
        after_state={"id": new_id, "source_reference": spec.source_reference},
    )
    return new_id


# ---------------------------------------------------------------------------
# periodic_reviews
# ---------------------------------------------------------------------------

def _build_review_trigger_reason(spec, alert_id: Optional[int]) -> str:
    """Pack marker + structured payload into trigger_reason.

    Format: '<FIX_SCENxx_REVIEW> FIX_REVIEW_JSON:{...}'

    The leading fixture marker is preserved as a stable prefix so that
    ``... WHERE trigger_reason LIKE 'FIX_SCENxx_REVIEW%'`` still works as
    the idempotency lookup. Everything after the sentinel is canonical JSON,
    so the round-trip cannot be broken by ``;`` or ``=`` characters that may
    appear in human-authored memo text.
    """
    payload = {
        "status": spec.status,
        "source_alert_id": alert_id,
        "review_memo": spec.review_memo,
        "outcome": spec.outcome,
    }
    return (
        f"{spec.fixture_marker} "
        f"{REVIEW_PAYLOAD_SENTINEL}"
        f"{json.dumps(payload, sort_keys=True, default=str)}"
    )


def _upsert_review(
    db, audit, app_id: str, scen: ScenarioDef, alert_id: Optional[int]
) -> Optional[int]:
    if not scen.review_spec:
        return None
    spec = scen.review_spec
    # Match marker at start (LIKE for portability across SQLite/PG).
    existing_id = _fetch_id(
        db,
        "SELECT id FROM periodic_reviews WHERE trigger_reason LIKE ?",
        (f"{spec.fixture_marker}%",),
    )
    trigger_reason = _build_review_trigger_reason(spec, alert_id)
    # trigger_type carries the fixture status sub-state (e.g. 'fixture_completed').
    trigger_type = spec.status

    if existing_id:
        db.execute(
            "UPDATE periodic_reviews SET application_id=?, client_name=?, "
            "risk_level=?, trigger_type=?, trigger_reason=?, "
            "completed_at=?, decision=?, decision_reason=? WHERE id=?",
            (
                app_id,
                scen.company_name,
                scen.risk_level,
                trigger_type,
                trigger_reason,
                spec.completed_at_iso,
                spec.outcome,
                spec.outcome,
                existing_id,
            ),
        )
        audit(
            action="update_review",
            target=f"periodic_review:{existing_id}",
            detail=f"Updated fixture review for {scen.code}",
            after_state={
                "id": existing_id,
                "fixture_marker": spec.fixture_marker,
                "status": spec.status,
            },
        )
        return existing_id

    new_id = _insert_returning_id(
        db,
        "periodic_reviews",
        "application_id, client_name, risk_level, trigger_type, "
        "trigger_reason, completed_at, decision, decision_reason",
        (
            app_id,
            scen.company_name,
            scen.risk_level,
            trigger_type,
            trigger_reason,
            spec.completed_at_iso,
            spec.outcome,
            spec.outcome,
        ),
    )
    audit(
        action="insert_review",
        target=f"periodic_review:{new_id}",
        detail=f"Seeded fixture review for {scen.code}",
        after_state={
            "id": new_id,
            "fixture_marker": spec.fixture_marker,
            "has_memo": spec.review_memo is not None,
        },
    )
    return new_id


# ---------------------------------------------------------------------------
# compliance_memos
# ---------------------------------------------------------------------------

def _build_fixture_memo_data(scen: ScenarioDef) -> str:
    reference = f"FIX_{scen.code.replace('-', '_')}_COMPLIANCE_MEMO"
    return json.dumps(
        {
            "reference": reference,
            "title": f"FIXTURE compliance memo for {scen.code}",
            "body": "Auto-seeded fixture compliance memo. Do not edit.",
            "fixture": True,
            "scenario": scen.code,
        },
        sort_keys=True,
    )


def _find_existing_fixture_memo_id(db, app_id: str, scen: ScenarioDef) -> Optional[int]:
    """Find an existing fixture memo by application_id + reference marker.

    Marker is embedded inside the memo_data JSON; we use a portable LIKE
    on the JSON-as-text column (memo_data is TEXT in both schemas).
    """
    reference = f"FIX_{scen.code.replace('-', '_')}_COMPLIANCE_MEMO"
    return _fetch_id(
        db,
        "SELECT id FROM compliance_memos WHERE application_id = ? "
        "AND memo_data LIKE ?",
        (app_id, f"%{reference}%"),
    )


def _upsert_fixture_compliance_memo(
    db, audit, app_id: str, scen: ScenarioDef
) -> Optional[int]:
    existing_id = _find_existing_fixture_memo_id(db, app_id, scen)
    if existing_id:
        return existing_id
    memo_data = _build_fixture_memo_data(scen)
    new_id = _insert_returning_id(
        db,
        "compliance_memos",
        "application_id, version, memo_data, ai_recommendation, "
        "review_status, supervisor_status, rule_engine_status",
        (
            app_id,
            1,
            memo_data,
            "fixture",
            "approved",
            "approved",
            "pass",
        ),
    )
    audit(
        action="insert_compliance_memo",
        target=f"compliance_memo:{new_id}",
        detail=f"Seeded fixture compliance memo for {scen.code}",
        after_state={"id": new_id, "scenario": scen.code},
    )
    return new_id


# ---------------------------------------------------------------------------
# edd_cases
# ---------------------------------------------------------------------------

# ``edd_cases.stage`` is constrained to this enum (see db.py CHECK clause).
_VALID_EDD_STAGES = {
    "triggered",
    "information_gathering",
    "analysis",
    "pending_senior_review",
    "edd_approved",
    "edd_rejected",
}


def _build_edd_trigger_notes(
    spec, review_id: Optional[int], alert_id: Optional[int]
) -> str:
    """Pack marker + structured payload into trigger_notes.

    Format: '<FIX_SCENxx_EDD> FIX_EDD_JSON:{...}'

    Leading fixture marker preserved for ``LIKE 'FIX_SCENxx_EDD%'``
    idempotency. Source linkage and ``kind`` are stored as JSON to avoid
    free-text-delimiter parsing fragility.
    """
    payload = {
        "kind": spec.kind,
        "source_review_id": review_id,
        "source_alert_id": alert_id,
    }
    return (
        f"{spec.fixture_marker} "
        f"{EDD_PAYLOAD_SENTINEL}"
        f"{json.dumps(payload, sort_keys=True, default=str)}"
    )


def _upsert_edd(
    db,
    audit,
    app_id: str,
    scen: ScenarioDef,
    review_id: Optional[int],
    alert_id: Optional[int],
) -> Optional[int]:
    if not scen.edd_spec:
        return None
    spec = scen.edd_spec
    stage = spec.stage if spec.stage in _VALID_EDD_STAGES else "information_gathering"
    existing_id = _fetch_id(
        db,
        "SELECT id FROM edd_cases WHERE trigger_notes LIKE ?",
        (f"{spec.fixture_marker}%",),
    )
    if spec.seed_compliance_memo and spec.kind == "onboarding":
        _upsert_fixture_compliance_memo(db, audit, app_id, scen)
    # ``trigger_source`` carries the drafted ``kind`` (e.g. 'onboarding'
    # or 'periodic_review'). It is free text in both schemas.
    trigger_source = spec.kind
    trigger_notes = _build_edd_trigger_notes(spec, review_id, alert_id)
    now = _now_iso()

    if existing_id:
        db.execute(
            "UPDATE edd_cases SET application_id=?, client_name=?, risk_level=?, "
            "stage=?, trigger_source=?, trigger_notes=?, updated_at=? WHERE id=?",
            (
                app_id,
                scen.company_name,
                spec.risk_level,
                stage,
                trigger_source,
                trigger_notes,
                now,
                existing_id,
            ),
        )
        audit(
            action="update_edd",
            target=f"edd_case:{existing_id}",
            detail=f"Updated fixture EDD for {scen.code}",
            after_state={
                "id": existing_id,
                "kind": spec.kind,
                "stage": stage,
            },
        )
        return existing_id

    new_id = _insert_returning_id(
        db,
        "edd_cases",
        "application_id, client_name, risk_level, stage, trigger_source, "
        "trigger_notes, triggered_at, updated_at",
        (
            app_id,
            scen.company_name,
            spec.risk_level,
            stage,
            trigger_source,
            trigger_notes,
            now,
            now,
        ),
    )
    audit(
        action="insert_edd",
        target=f"edd_case:{new_id}",
        detail=f"Seeded fixture EDD for {scen.code}",
        after_state={
            "id": new_id,
            "kind": spec.kind,
            "stage": stage,
            "fixture_marker": spec.fixture_marker,
        },
    )
    return new_id


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def _doc_file_path(marker: str) -> str:
    """Idempotency key for documents: a fixture:// URI carrying the marker."""
    return f"fixture://{marker}"


def _upsert_documents(db, audit, app_id: str, scen: ScenarioDef) -> List[str]:
    if not scen.documents:
        return []
    ids: List[str] = []
    for doc in scen.documents:
        file_path = _doc_file_path(doc.fixture_marker)
        existing_id = _fetch_id(
            db,
            "SELECT id FROM documents WHERE application_id = ? AND file_path = ?",
            (app_id, file_path),
        )
        if existing_id:
            db.execute(
                "UPDATE documents SET doc_type=?, doc_name=?, "
                "verification_status=?, uploaded_at=? WHERE id=?",
                (
                    doc.purpose,
                    f"{doc.purpose}.fixture",
                    doc.verification_status,
                    doc.uploaded_at_iso,
                    existing_id,
                ),
            )
            ids.append(existing_id)
            audit(
                action="update_document",
                target=f"document:{existing_id}",
                detail=f"Updated fixture document {doc.purpose} for {scen.code}",
            )
            continue
        new_id = _new_text_id()
        db.execute(
            "INSERT INTO documents "
            "(id, application_id, doc_type, doc_name, file_path, "
            "verification_status, review_status, uploaded_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                new_id,
                app_id,
                doc.purpose,
                f"{doc.purpose}.fixture",
                file_path,
                doc.verification_status,
                "accepted",
                doc.uploaded_at_iso,
            ),
        )
        ids.append(new_id)
        audit(
            action="insert_document",
            target=f"document:{new_id}",
            detail=f"Seeded fixture document {doc.purpose} for {scen.code}",
            after_state={"id": new_id, "file_path": file_path},
        )
    return ids


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _seed_one(db, audit, scen: ScenarioDef) -> Dict[str, Any]:
    app_id = _upsert_application(db, audit, scen)
    doc_ids = _upsert_documents(db, audit, app_id, scen)
    alert_id = _upsert_alert(db, audit, app_id, scen)
    review_id = _upsert_review(db, audit, app_id, scen, alert_id)
    edd_id = _upsert_edd(db, audit, app_id, scen, review_id, alert_id)
    return {
        "scenario": scen.code,
        "company_name": scen.company_name,
        "application_id": app_id,
        "application_ref": APP_REF[scen.code],
        "document_ids": doc_ids,
        "alert_id": alert_id,
        "review_id": review_id,
        "edd_id": edd_id,
        "proves": scen.proves,
    }


def _rollback(db) -> None:
    """Best-effort rollback against the underlying connection.

    DBConnection itself does not expose ``rollback()`` (see repo memory:
    "DBConnection rollback gap"). We reach through to ``db.conn`` which
    is the live psycopg2 / sqlite3 connection.
    """
    try:
        db.conn.rollback()
    except Exception as exc:
        logger.warning("rollback raised: %s", exc)


def seed_all(
    dry_run: bool, only: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Seed selected scenarios. Returns a list of result dicts.

    Single-transaction semantics:
    - In ``dry_run=True`` mode all writes (including audit rows) are
      rolled back at the end.
    - In ``dry_run=False`` mode all writes are committed atomically.
    - On any exception the transaction is rolled back.
    """
    init_db()
    db = get_db()
    audit = make_fixture_audit_writer(db)
    selected = [s for s in SCENARIOS if (not only or s.code in only)]
    results: List[Dict[str, Any]] = []
    try:
        for scen in selected:
            results.append(_seed_one(db, audit, scen))
        if dry_run:
            _rollback(db)
            logger.info(
                "fixtures.seeder: dry-run complete (%d scenarios rolled back)",
                len(results),
            )
        else:
            audit(
                action="apply_complete",
                target="fixtures:all",
                detail=f"Applied {len(results)} scenarios",
            )
            db.commit()
            logger.info(
                "fixtures.seeder: apply complete (%d scenarios committed)",
                len(results),
            )
    except Exception:
        _rollback(db)
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass
    return results


# ---------------------------------------------------------------------------
# Read-back helpers (used by tests / REGISTER renderer)
# ---------------------------------------------------------------------------

_DISMISSAL_RE = re.compile(re.escape(DISMISSAL_PAYLOAD_SENTINEL) + r"(.*)$", re.DOTALL)
_REVIEW_PAYLOAD_RE = re.compile(re.escape(REVIEW_PAYLOAD_SENTINEL) + r"(.*)$", re.DOTALL)
_EDD_PAYLOAD_RE = re.compile(re.escape(EDD_PAYLOAD_SENTINEL) + r"(.*)$", re.DOTALL)


def _parse_sentinel_json(text: Optional[str], pattern) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    m = pattern.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def parse_dismissal_payload(officer_notes: Optional[str]) -> Optional[Dict[str, Any]]:
    """Recover a structured dismissal payload from monitoring_alerts.officer_notes.

    Returns ``None`` if no FIX_PAYLOAD_JSON: sentinel is present.
    """
    return _parse_sentinel_json(officer_notes, _DISMISSAL_RE)


def parse_review_payload(trigger_reason: Optional[str]) -> Optional[Dict[str, Any]]:
    """Recover the structured fixture payload from periodic_reviews.trigger_reason.

    Returns ``None`` if no FIX_REVIEW_JSON: sentinel is present.
    """
    return _parse_sentinel_json(trigger_reason, _REVIEW_PAYLOAD_RE)


def parse_edd_payload(trigger_notes: Optional[str]) -> Optional[Dict[str, Any]]:
    """Recover the structured fixture payload from edd_cases.trigger_notes.

    Returns ``None`` if no FIX_EDD_JSON: sentinel is present.
    """
    return _parse_sentinel_json(trigger_notes, _EDD_PAYLOAD_RE)


# ---------------------------------------------------------------------------
# SCEN-05 fixture validation
# ---------------------------------------------------------------------------
#
# SCEN-05 ("completed periodic review with NO memo") is now an explicit,
# deterministic fixture in the registry — it is the negative control / no-memo
# counterpart to SCEN-04 (memo-positive). The historical assumption that
# pre-existing legacy ``periodic_reviews`` rows in staging would cover this
# scenario has been removed.
#
# This check is a non-fatal probe that validates the SCEN-05 *fixture
# definition* (the registry contract), not the presence of legacy rows. It
# never raises and never writes. It returns a structured result that the CLI
# surfaces.


def check_scen05_fixture() -> Dict[str, Any]:
    """Validate the explicit SCEN-05 fixture contract.

    SCEN-05 must be:

    - present in the scenario registry
    - a completed periodic review (``review_spec.completed_at_iso`` set,
      ``review_spec.status == 'fixture_completed'``)
    - WITHOUT a memo (``review_spec.review_memo is None``) and without an
      ``edd_spec`` (so no compliance memo is seeded either)

    Returns a dict with at minimum:
      - ``satisfied`` (bool): True iff the registry contract is met.
      - ``message`` (str):    human-readable summary.
      - ``error`` (bool):     True only on unexpected internal errors.

    Never raises. Caller is responsible for surfacing the result.
    """
    try:
        from fixtures.registry import by_code

        scen = by_code("SCEN-05")
        problems: List[str] = []
        if scen.review_spec is None:
            problems.append("missing review_spec")
        else:
            rs = scen.review_spec
            if rs.review_memo is not None:
                problems.append("review_spec.review_memo must be None (no-memo case)")
            if not rs.completed_at_iso:
                problems.append("review_spec.completed_at_iso must be set (completed case)")
            if rs.status != "fixture_completed":
                problems.append(
                    f"review_spec.status must be 'fixture_completed' "
                    f"(got '{rs.status}')"
                )
        if scen.edd_spec is not None:
            problems.append(
                "edd_spec must be None (no compliance memo is seeded for SCEN-05)"
            )
        if problems:
            return {
                "satisfied": False,
                "message": (
                    "SCEN-05 explicit fixture contract violated: "
                    + "; ".join(problems)
                ),
                "error": False,
            }
        return {
            "satisfied": True,
            "message": (
                "SCEN-05 explicit fixture validated: completed periodic "
                "review with no memo (negative control to SCEN-04)."
            ),
            "error": False,
        }
    except KeyError:
        return {
            "satisfied": False,
            "message": "SCEN-05 missing from registry (expected explicit fixture).",
            "error": False,
        }
    except Exception as exc:
        return {
            "satisfied": False,
            "message": f"SCEN-05 fixture validation failed unexpectedly: {exc}",
            "error": True,
        }
