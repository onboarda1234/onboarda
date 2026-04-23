"""
Lifecycle Queue Aggregator -- PR-05 (Backoffice Lifecycle UX / Queue Clarity)
=============================================================================

Pure-function read-only aggregator that composes the three lifecycle object
types -- monitoring alerts, periodic reviews and EDD cases -- into a single
operationally usable view for the back-office.

Design contract
---------------
* Read-only. NEVER mutates the database.
* Additive. Does NOT change the shape or semantics of any existing endpoint.
* Provider-agnostic. Does NOT touch screening / Sumsub / ComplyAdvantage.
* Respects PR-01..PR-04a contracts:
    - PR-02 alert routing vocabulary (open / triaged / assigned / dismissed /
      routed_to_review / routed_to_edd) and the rule that
      monitoring-originated reviews are first-class reviews
    - PR-02 reverse-link displacement reality (alerts terminal once routed)
    - PR-03 review state model + outcome semantics (state and outcome are
      DISJOINT; legacy ``decision`` is preserved unchanged but not used as
      the outcome source of truth)
    - PR-03a outcome-vs-legacy-decision clarification
    - PR-04 active memo context resolution lives in
      ``edd_memo_integration.resolve_active_memo_context``; this module
      delegates rather than re-implements it
    - PR-04a onboarding attachment rule -- only attachments with a real
      ``compliance_memos.id`` (memo_id IS NOT NULL when kind='onboarding')
      are surfaced as confirmed onboarding attachments
* No protected file is modified by this module being added.

Public surface
--------------
* :func:`build_lifecycle_queue` -- aggregated queue for the three types.
* :func:`build_application_lifecycle_summary` -- per-application linkage
  view (alerts <-> reviews <-> EDD <-> memo context).
* Vocabularies (``ACTIVE_ALERT_STATUSES`` / ``HISTORICAL_*``) are exposed
  for unit-test parity and for the HTTP handler.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── Active / historical vocabularies ─────────────────────────────────
# These mirror the engines' terminal sets (kept in this module so the
# aggregator is self-contained and trivially testable). The engines remain
# the source of truth for transitions; this module is the source of truth
# only for "is this row currently active work for an officer?".

# Monitoring alerts: terminal once dismissed or routed (see
# monitoring_routing.TERMINAL_ALERT_STATUSES).
HISTORICAL_ALERT_STATUSES = (
    "dismissed",
    "routed_to_review",
    "routed_to_edd",
)
ACTIVE_ALERT_STATUSES = (
    "open",
    "triaged",
    "assigned",
)

# Periodic reviews: terminal once completed. PR-03 introduced the
# in_progress / awaiting_information / pending_senior_review states.
HISTORICAL_REVIEW_STATES = (
    "completed",
)
ACTIVE_REVIEW_STATES = (
    "pending",
    "in_progress",
    "awaiting_information",
    "pending_senior_review",
)

# EDD cases: terminal once approved or rejected (mirrors
# monitoring_routing.TERMINAL_EDD_STAGES).
HISTORICAL_EDD_STAGES = (
    "edd_approved",
    "edd_rejected",
)
ACTIVE_EDD_STAGES = (
    "triggered",
    "information_gathering",
    "analysis",
    "pending_senior_review",
)

VALID_ITEM_TYPES = ("alert", "review", "edd")
# PR-A (Data Trust Hardening) added the third bucket ``legacy_unmapped``
# alongside ``active`` and ``historical``. Quarantined rows are EXCLUDED
# from active / historical / all and ONLY appear when explicitly asked
# for via ``include='legacy_unmapped'``. Reviews and EDD cases are not
# subject to quarantine -- they have schema-defined CHECK-constrained
# states. The bucket is implemented for ``monitoring_alerts`` only.
VALID_INCLUDE = ("active", "historical", "all", "legacy_unmapped")

_FIX_REVIEW_RE = re.compile(r"FIX_REVIEW_JSON:(\{.*\})$", re.DOTALL)
_FIX_EDD_RE = re.compile(r"FIX_EDD_JSON:(\{.*\})$", re.DOTALL)


# ── Internal utilities ──────────────────────────────────────────────
def _row_get(row, key, default=None):
    """Safe accessor that works for sqlite3.Row, psycopg2 RealDictRow, dict.

    Mirrors the convention used in ``lifecycle_linkage._row_get`` /
    ``edd_memo_integration._row_get``. Kept duplicated rather than
    imported to avoid coupling to those modules' internals -- this
    aggregator must remain side-effect-free.
    """
    if row is None:
        return default
    try:
        if key in row.keys():
            value = row[key]
            return default if value is None else value
    except (AttributeError, TypeError):
        pass
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def _parse_iso(value) -> Optional[datetime]:
    """Best-effort parse of a TIMESTAMP column to a tz-aware datetime.

    SQLite stores naive 'YYYY-MM-DD HH:MM:SS', PostgreSQL returns a
    ``datetime``. We accept both. Returns None on any parse failure --
    this is a UX field (age display), never a control gate, so quietly
    degrading to None is the right behaviour.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        # Normalise space separator that SQLite uses
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _age_seconds(value, *, now: Optional[datetime] = None) -> Optional[int]:
    """Compute age in seconds from a TIMESTAMP-shaped value to now."""
    dt = _parse_iso(value)
    if dt is None:
        return None
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    delta = ref - dt
    seconds = int(delta.total_seconds())
    return seconds if seconds >= 0 else 0


def _age_days(value, *, now: Optional[datetime] = None) -> Optional[int]:
    secs = _age_seconds(value, now=now)
    if secs is None:
        return None
    return secs // 86400


def _decode_required_items(raw) -> List[Dict[str, Any]]:
    """Decode ``periodic_reviews.required_items`` JSON to a list."""
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return items if isinstance(items, list) else []


def _parse_fixture_payload(raw, pattern) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    m = pattern.search(str(raw))
    if not m:
        return None
    try:
        payload = json.loads(m.group(1))
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _parse_review_fixture_payload(trigger_reason) -> Optional[Dict[str, Any]]:
    return _parse_fixture_payload(trigger_reason, _FIX_REVIEW_RE)


def _parse_edd_fixture_payload(trigger_notes) -> Optional[Dict[str, Any]]:
    return _parse_fixture_payload(trigger_notes, _FIX_EDD_RE)


def _normalise_review_state(row) -> str:
    explicit = (_row_get(row, "status") or "").strip().lower()
    payload = _parse_review_fixture_payload(_row_get(row, "trigger_reason"))
    fixture_state = str((payload or {}).get("status") or _row_get(row, "trigger_type") or "").strip().lower()
    if fixture_state == "fixture_completed":
        return "completed"
    if fixture_state == "fixture_in_progress":
        return "in_progress"
    if explicit:
        return explicit
    return "pending"


def _normalise_alert_state(row) -> str:
    status = str(_row_get(row, "status", "open") or "open").strip().lower()
    src = str(_row_get(row, "source_reference", "") or "").strip().upper()
    if status == "in_review" and src.startswith("FIX_SCEN"):
        return "triaged"
    return status


def _user_name_map(db, user_ids: List[str]) -> Dict[str, str]:
    """Batch-resolve user IDs to display names. Empty input → empty map.

    Uses a single ``WHERE id IN (...)`` query (mirrors the EX-13 batch
    pattern in party_utils) so this aggregator stays O(1) queries per
    item type regardless of queue size.
    """
    cleaned = [u for u in dict.fromkeys(user_ids) if u]
    if not cleaned:
        return {}
    placeholders = ",".join(["?"] * len(cleaned))
    sql = f"SELECT id, full_name FROM users WHERE id IN ({placeholders})"
    try:
        rows = db.execute(sql, cleaned).fetchall() or []
    except Exception:
        # Defensive: a missing/renamed users table must not break the queue.
        return {}
    return {_row_get(r, "id"): _row_get(r, "full_name", "") or "" for r in rows}


def _next_action_for_alert(status: str) -> Optional[str]:
    """Return a short next-action hint for an alert, by status."""
    return {
        "open": "Triage and assign",
        "triaged": "Assign to officer",
        "assigned": "Investigate and route",
        "dismissed": None,
        "routed_to_review": "Continue in periodic review",
        "routed_to_edd": "Continue in EDD case",
    }.get(status)


def _next_action_for_review(status: str) -> Optional[str]:
    return {
        "pending": "Start review",
        "in_progress": "Generate / collect required items",
        "awaiting_information": "Awaiting client / officer input",
        "pending_senior_review": "Senior officer sign-off",
        "completed": None,
    }.get(status)


def _next_action_for_edd(stage: str) -> Optional[str]:
    return {
        "triggered": "Begin information gathering",
        "information_gathering": "Move to analysis",
        "analysis": "Submit for senior review",
        "pending_senior_review": "Senior officer decision",
        "edd_approved": None,
        "edd_rejected": None,
    }.get(stage)


# ── Per-row materialisation ──────────────────────────────────────────
def _materialise_alert(row, *, user_names: Dict[str, str],
                       now: datetime) -> Dict[str, Any]:
    from lifecycle_quarantine import is_legacy_unmapped
    status = _normalise_alert_state(row)
    owner_id = _row_get(row, "reviewed_by")
    is_quarantined, quarantine_reasons = is_legacy_unmapped(row)
    return {
        "type": "alert",
        "id": _row_get(row, "id"),
        "application_id": _row_get(row, "application_id"),
        "client_name": _row_get(row, "client_name", "") or "",
        "state": status,
        # PR-A: a quarantined row is NEITHER active NOR historical even
        # if its status would otherwise place it in one of those buckets.
        # The third bucket is explicit and additive.
        "is_active": (not is_quarantined) and status in ACTIVE_ALERT_STATUSES,
        "is_historical": (not is_quarantined)
                         and status in HISTORICAL_ALERT_STATUSES,
        "is_legacy_unmapped": is_quarantined,
        "quarantine_reasons": quarantine_reasons,
        "severity": _row_get(row, "severity"),
        "alert_type": _row_get(row, "alert_type"),
        "summary": _row_get(row, "summary"),
        "owner_id": owner_id,
        "owner_name": user_names.get(owner_id) if owner_id else None,
        "created_at": _row_get(row, "created_at"),
        "age_seconds": _age_seconds(_row_get(row, "created_at"), now=now),
        "age_days": _age_days(_row_get(row, "created_at"), now=now),
        "triaged_at": _row_get(row, "triaged_at"),
        "assigned_at": _row_get(row, "assigned_at"),
        "resolved_at": _row_get(row, "resolved_at"),
        "linked_periodic_review_id": _row_get(row, "linked_periodic_review_id"),
        "linked_edd_case_id": _row_get(row, "linked_edd_case_id"),
        "officer_action": _row_get(row, "officer_action"),
        "next_action": _next_action_for_alert(status),
    }


def _materialise_review(row, *, user_names: Dict[str, str],
                        required_items_count: int,
                        now: datetime) -> Dict[str, Any]:
    payload = _parse_review_fixture_payload(_row_get(row, "trigger_reason"))
    status = _normalise_review_state(row)
    linked_alert = _row_get(row, "linked_monitoring_alert_id")
    if linked_alert is None and payload:
        linked_alert = payload.get("source_alert_id")
    review_reason = _row_get(row, "review_reason") or _row_get(row, "trigger_reason")
    if payload and isinstance(review_reason, str) and "FIX_REVIEW_JSON:" in review_reason:
        review_reason = "Seeded fixture review trigger"
    owner_id = _row_get(row, "decided_by")
    return {
        "type": "review",
        "id": _row_get(row, "id"),
        "application_id": _row_get(row, "application_id"),
        "client_name": _row_get(row, "client_name", "") or "",
        "state": status,
        "is_active": status in ACTIVE_REVIEW_STATES,
        "is_historical": status in HISTORICAL_REVIEW_STATES,
        "risk_level": _row_get(row, "risk_level"),
        "trigger_source": _row_get(row, "trigger_source") or _row_get(row, "trigger_type"),
        "trigger_type": _row_get(row, "trigger_type"),
        "review_reason": review_reason,
        "priority": _row_get(row, "priority"),
        "owner_id": owner_id,
        "owner_name": user_names.get(owner_id) if owner_id else None,
        "created_at": _row_get(row, "created_at"),
        "age_seconds": _age_seconds(_row_get(row, "created_at"), now=now),
        "age_days": _age_days(_row_get(row, "created_at"), now=now),
        "due_date": _row_get(row, "due_date"),
        "started_at": _row_get(row, "started_at"),
        "completed_at": _row_get(row, "completed_at"),
        "state_changed_at": _row_get(row, "state_changed_at"),
        # PR-03 outcome (source of truth) is disjoint from the legacy
        # ``decision`` column. Surface both so the UI can show outcome
        # without erasing the legacy decision history (PR-03a contract).
        "outcome": _row_get(row, "outcome") or (payload or {}).get("outcome"),
        "outcome_reason": _row_get(row, "outcome_reason"),
        "outcome_recorded_at": _row_get(row, "outcome_recorded_at"),
        "legacy_decision": _row_get(row, "decision") or (payload or {}).get("outcome"),
        "linked_monitoring_alert_id": linked_alert,
        "linked_edd_case_id": _row_get(row, "linked_edd_case_id"),
        "fixture_payload": payload,
        "required_items_count": required_items_count,
        "required_items_generated_at": _row_get(row, "required_items_generated_at"),
        "next_action": _next_action_for_review(status),
    }


def _materialise_edd(row, *, user_names: Dict[str, str],
                     findings_present: bool,
                     memo_context: Optional[Dict[str, Any]],
                     now: datetime) -> Dict[str, Any]:
    payload = _parse_edd_fixture_payload(_row_get(row, "trigger_notes"))
    stage = _row_get(row, "stage", "triggered") or "triggered"
    origin_context = (_row_get(row, "origin_context")
                      or (payload or {}).get("kind")
                      or _row_get(row, "trigger_source"))
    linked_alert = _row_get(row, "linked_monitoring_alert_id")
    linked_review = _row_get(row, "linked_periodic_review_id")
    if payload:
        if linked_alert is None:
            linked_alert = payload.get("source_alert_id")
        if linked_review is None:
            linked_review = payload.get("source_review_id")
    if linked_review is not None and (not memo_context or memo_context.get("kind") != "periodic_review"):
        memo_context = {
            "kind": "periodic_review",
            "memo_id": None,
            "periodic_review_id": linked_review,
            "origin_context": origin_context,
            "resolution_reason": "fixture_payload_source_review_id",
            "unresolved": False,
            "onboarding_attachment_confirmed": True,
        }
    owner_id = _row_get(row, "assigned_officer")
    senior_id = _row_get(row, "senior_reviewer")
    return {
        "type": "edd",
        "id": _row_get(row, "id"),
        "application_id": _row_get(row, "application_id"),
        "client_name": _row_get(row, "client_name", "") or "",
        "state": stage,
        "is_active": stage in ACTIVE_EDD_STAGES,
        "is_historical": stage in HISTORICAL_EDD_STAGES,
        "risk_level": _row_get(row, "risk_level"),
        "risk_score": _row_get(row, "risk_score"),
        "priority": _row_get(row, "priority"),
        "trigger_source": _row_get(row, "trigger_source"),
        "trigger_notes": _row_get(row, "trigger_notes"),
        "origin_context": origin_context,
        "owner_id": owner_id,
        "owner_name": user_names.get(owner_id) if owner_id else None,
        "senior_reviewer_id": senior_id,
        "senior_reviewer_name": user_names.get(senior_id) if senior_id else None,
        "created_at": _row_get(row, "triggered_at"),
        "age_seconds": _age_seconds(_row_get(row, "triggered_at"), now=now),
        "age_days": _age_days(_row_get(row, "triggered_at"), now=now),
        "assigned_at": _row_get(row, "assigned_at"),
        "escalated_at": _row_get(row, "escalated_at"),
        "closed_at": _row_get(row, "closed_at"),
        "decided_at": _row_get(row, "decided_at"),
        "decision": _row_get(row, "decision"),
        "linked_monitoring_alert_id": linked_alert,
        "linked_periodic_review_id": linked_review,
        "findings_present": bool(findings_present),
        "memo_context": memo_context,
        "fixture_payload": payload,
        "next_action": _next_action_for_edd(stage),
    }


# ── EDD memo-context surfacing ───────────────────────────────────────
def _safe_resolve_memo_context(db, edd_case_id) -> Optional[Dict[str, Any]]:
    """Wrap ``edd_memo_integration.resolve_active_memo_context`` defensively.

    The aggregator is a UX layer. A resolution failure (e.g. an EDD that
    claims periodic-review origin without an explicit linkage) must NOT
    crash the queue; instead we surface a structured ``unresolved`` flag
    so the officer sees the gap rather than an empty page. PR-04
    semantics are preserved -- we never silently invent a context.
    """
    try:
        from edd_memo_integration import (
            resolve_active_memo_context,
            MemoContextResolutionError,
            EDDCaseNotFound,
        )
    except Exception:
        return None
    try:
        ctx = resolve_active_memo_context(db, edd_case_id)
    except MemoContextResolutionError as exc:
        return {
            "kind": None,
            "memo_id": None,
            "periodic_review_id": None,
            "unresolved": True,
            "unresolved_reason": str(exc),
        }
    except EDDCaseNotFound:
        return None
    except Exception:
        # Any other unexpected failure -- never crash the queue. Return
        # an unresolved marker rather than swallowing silently.
        return {
            "kind": None,
            "memo_id": None,
            "periodic_review_id": None,
            "unresolved": True,
            "unresolved_reason": "memo_context_resolution_failed",
        }
    # PR-04a contract: an onboarding attachment is only "confirmed" when
    # memo_id points at a real compliance_memos.id. The resolver itself
    # already guarantees this (it pulls the latest compliance_memos.id),
    # so we just surface a boolean for UI clarity.
    confirmed = True
    if ctx.get("kind") == "onboarding" and ctx.get("memo_id") is None:
        confirmed = False
    out = {
        "kind": ctx.get("kind"),
        "memo_id": ctx.get("memo_id"),
        "periodic_review_id": ctx.get("periodic_review_id"),
        "origin_context": ctx.get("origin_context"),
        "resolution_reason": ctx.get("resolution_reason"),
        "unresolved": False,
        "onboarding_attachment_confirmed": confirmed,
    }
    return out


def _findings_present_map(db, edd_case_ids: List[int]) -> Dict[int, bool]:
    """Batch-check whether ``edd_findings`` rows exist for given EDD ids."""
    cleaned = [i for i in dict.fromkeys(edd_case_ids) if i is not None]
    if not cleaned:
        return {}
    placeholders = ",".join(["?"] * len(cleaned))
    sql = ("SELECT edd_case_id FROM edd_findings "
           f"WHERE edd_case_id IN ({placeholders})")
    try:
        rows = db.execute(sql, cleaned).fetchall() or []
    except Exception:
        # Table missing on a stale schema -- degrade gracefully.
        return {}
    out = {i: False for i in cleaned}
    for r in rows:
        out[_row_get(r, "edd_case_id")] = True
    return out


# ── Public API ───────────────────────────────────────────────────────
def _fetch_alerts(db, *, application_id=None, include="active") -> List[Any]:
    from lifecycle_quarantine import (
        legacy_unmapped_where_clause,
        active_or_historical_exclude_legacy_clause,
    )
    sql = "SELECT * FROM monitoring_alerts WHERE 1=1"
    params: List[Any] = []
    if application_id is not None:
        sql += " AND application_id = ?"
        params.append(application_id)
    if include in ("active", "historical", "all"):
        # active+historical without quarantined rows.
        excl, excl_params = active_or_historical_exclude_legacy_clause()
        sql += f" AND {excl}"
        params.extend(excl_params)
    elif include == "legacy_unmapped":
        # PR-A: only quarantined rows.
        legacy, legacy_params = legacy_unmapped_where_clause()
        sql += f" AND {legacy}"
        params.extend(legacy_params)
    sql += " ORDER BY created_at DESC"
    return db.execute(sql, params).fetchall() or []


def _fetch_reviews(db, *, application_id=None, include="active") -> List[Any]:
    # PR-A: periodic_reviews are NOT subject to quarantine (they have
    # schema-defined CHECK-constrained states). The legacy_unmapped
    # bucket is monitoring_alerts-only.
    if include == "legacy_unmapped":
        return []
    sql = "SELECT * FROM periodic_reviews WHERE 1=1"
    params: List[Any] = []
    if application_id is not None:
        sql += " AND application_id = ?"
        params.append(application_id)
    sql += " ORDER BY created_at DESC"
    return db.execute(sql, params).fetchall() or []


def _fetch_edd(db, *, application_id=None, include="active") -> List[Any]:
    # PR-A: edd_cases are NOT subject to quarantine (schema-defined
    # CHECK-constrained stages). The legacy_unmapped bucket is
    # monitoring_alerts-only.
    if include == "legacy_unmapped":
        return []
    sql = "SELECT * FROM edd_cases WHERE 1=1"
    params: List[Any] = []
    if application_id is not None:
        sql += " AND application_id = ?"
        params.append(application_id)
    sql += " ORDER BY triggered_at DESC"
    return db.execute(sql, params).fetchall() or []


def build_lifecycle_queue(
    db,
    *,
    include: str = "active",
    types: Optional[Tuple[str, ...]] = None,
    application_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Aggregate monitoring alerts, periodic reviews and EDD cases.

    Args:
        db:             active DB connection.
        include:        'active' (default), 'historical' or 'all'.
        types:          subset of ('alert', 'review', 'edd') or None for all.
        application_id: scope to a single application or None for all.
        now:            override the reference time for age calculations
                        (test convenience).

    Returns:
        ``{"items": [...], "counts": {...}, "filter": {...}}`` where
        items are sorted oldest-active-first (so oldest unaddressed work
        is at the top of the queue) and historical items, when included,
        are appended after active items.
    """
    if include not in VALID_INCLUDE:
        raise ValueError(f"include must be one of {VALID_INCLUDE!r}")
    selected_types = tuple(types) if types else VALID_ITEM_TYPES
    for t in selected_types:
        if t not in VALID_ITEM_TYPES:
            raise ValueError(f"unknown type {t!r}; expected one of {VALID_ITEM_TYPES!r}")
    ref_now = now or datetime.now(timezone.utc)
    def _include_item(item):
        if include == "all":
            return True
        if include == "active":
            return bool(item.get("is_active"))
        if include == "historical":
            return bool(item.get("is_historical"))
        if include == "legacy_unmapped":
            return bool(item.get("is_legacy_unmapped"))
        return False

    items: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {"alert": 0, "review": 0, "edd": 0}

    # --- Alerts -------------------------------------------------------
    alert_rows: List[Any] = []
    if "alert" in selected_types:
        alert_rows = _fetch_alerts(db, application_id=application_id, include=include)
        owner_ids = [_row_get(r, "reviewed_by") for r in alert_rows]
        names = _user_name_map(db, owner_ids)
        for r in alert_rows:
            item = _materialise_alert(r, user_names=names, now=ref_now)
            if _include_item(item):
                items.append(item)
                counts["alert"] += 1

    # --- Reviews ------------------------------------------------------
    review_rows: List[Any] = []
    if "review" in selected_types:
        review_rows = _fetch_reviews(db, application_id=application_id, include=include)
        owner_ids = [_row_get(r, "decided_by") for r in review_rows]
        names = _user_name_map(db, owner_ids)
        for r in review_rows:
            items_count = len(_decode_required_items(_row_get(r, "required_items")))
            item = _materialise_review(
                r, user_names=names,
                required_items_count=items_count,
                now=ref_now,
            )
            if _include_item(item):
                items.append(item)
                counts["review"] += 1

    # --- EDD ----------------------------------------------------------
    edd_rows: List[Any] = []
    if "edd" in selected_types:
        edd_rows = _fetch_edd(db, application_id=application_id, include=include)
        officer_ids: List[str] = []
        for r in edd_rows:
            for k in ("assigned_officer", "senior_reviewer"):
                v = _row_get(r, k)
                if v:
                    officer_ids.append(v)
        names = _user_name_map(db, officer_ids)
        edd_ids = [_row_get(r, "id") for r in edd_rows]
        findings_map = _findings_present_map(db, edd_ids)
        for r in edd_rows:
            eid = _row_get(r, "id")
            ctx = _safe_resolve_memo_context(db, eid)
            item = _materialise_edd(
                r, user_names=names,
                findings_present=findings_map.get(eid, False),
                memo_context=ctx,
                now=ref_now,
            )
            if _include_item(item):
                items.append(item)
                counts["edd"] += 1

    # --- Ordering: active oldest-first, historical newest-first -------
    # Active queue = oldest at top (oldest unaddressed work surfaces).
    # Historical block = newest at top (most recent closure first).
    def _sort_key(item):
        # Items without a parseable created_at sink to the bottom of
        # their block but are still surfaced (next_action visible).
        active_flag = 0 if item.get("is_active") else 1
        age = item.get("age_seconds")
        if active_flag == 0:
            # active: largest age first → negative age for asc sort.
            # None ages sort *after* all real ages (truly at the bottom)
            # by mapping them to a value greater than any negated age.
            return (active_flag, 1 if age is None else -age)
        # historical: smallest age (most recent) first; None ages sink.
        return (active_flag, age if age is not None else 10**12)

    items.sort(key=_sort_key)

    return {
        "items": items,
        "counts": {
            "alert": counts["alert"],
            "review": counts["review"],
            "edd": counts["edd"],
            "total": counts["alert"] + counts["review"] + counts["edd"],
        },
        "filter": {
            "include": include,
            "types": list(selected_types),
            "application_id": application_id,
        },
    }


def build_application_lifecycle_summary(
    db,
    application_id: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Per-application lifecycle linkage view.

    Surfaces alerts, reviews and EDD cases for a single application with
    explicit linkage information, so the application detail surface can
    answer "what lifecycle objects exist for this client and how are
    they linked?" without scattering five separate API calls.

    Returns ``{"application_id": str, "active": {...}, "historical": {...},
    "linkage": {...}}`` where ``linkage`` is the cross-table edge set
    (alert<->review, alert<->edd, review<->edd) so the UI can render a
    compact "linked objects" summary without re-deriving it client-side.
    """
    if not application_id:
        raise ValueError("application_id is required")
    ref_now = now or datetime.now(timezone.utc)

    active = build_lifecycle_queue(
        db, include="active", application_id=application_id, now=ref_now,
    )
    historical = build_lifecycle_queue(
        db, include="historical", application_id=application_id, now=ref_now,
    )

    # --- Linkage edges ------------------------------------------------
    edges: List[Dict[str, Any]] = []
    seen = set()

    def _emit(kind, a, b):
        key = (kind, a[0], a[1], b[0], b[1])
        if key in seen:
            return
        seen.add(key)
        edges.append({
            "kind": kind,
            "from": {"type": a[0], "id": a[1]},
            "to":   {"type": b[0], "id": b[1]},
        })

    for blob in (active, historical):
        for it in blob["items"]:
            t = it["type"]
            if t == "alert":
                if it.get("linked_periodic_review_id"):
                    _emit("alert_to_review",
                          ("alert", it["id"]),
                          ("review", it["linked_periodic_review_id"]))
                if it.get("linked_edd_case_id"):
                    _emit("alert_to_edd",
                          ("alert", it["id"]),
                          ("edd", it["linked_edd_case_id"]))
            elif t == "review":
                if it.get("linked_monitoring_alert_id"):
                    _emit("review_from_alert",
                          ("review", it["id"]),
                          ("alert", it["linked_monitoring_alert_id"]))
                if it.get("linked_edd_case_id"):
                    _emit("review_to_edd",
                          ("review", it["id"]),
                          ("edd", it["linked_edd_case_id"]))
            elif t == "edd":
                if it.get("linked_monitoring_alert_id"):
                    _emit("edd_from_alert",
                          ("edd", it["id"]),
                          ("alert", it["linked_monitoring_alert_id"]))
                if it.get("linked_periodic_review_id"):
                    _emit("edd_from_review",
                          ("edd", it["id"]),
                          ("review", it["linked_periodic_review_id"]))

    return {
        "application_id": application_id,
        "active": active,
        "historical": historical,
        "linkage": {"edges": edges, "count": len(edges)},
    }


__all__ = [
    "ACTIVE_ALERT_STATUSES",
    "HISTORICAL_ALERT_STATUSES",
    "ACTIVE_REVIEW_STATES",
    "HISTORICAL_REVIEW_STATES",
    "ACTIVE_EDD_STAGES",
    "HISTORICAL_EDD_STAGES",
    "VALID_ITEM_TYPES",
    "VALID_INCLUDE",
    "build_lifecycle_queue",
    "build_application_lifecycle_summary",
]
