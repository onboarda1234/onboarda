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
* Existing lifecycle, monitoring, and EDD runtime contracts are not
  modified by this module being added.

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

import monitoring_routing as mr
from periodic_review_projection_service import list_review_projections

# ── Active / historical vocabularies ─────────────────────────────────
# These mirror the engines' terminal sets (kept in this module so the
# aggregator is self-contained and trivially testable). The engines remain
# the source of truth for transitions; this module is the source of truth
# only for "is this row currently active work for an officer?".

# Monitoring alerts: terminal once dismissed or routed (see
# monitoring_routing.TERMINAL_ALERT_STATUSES).
HISTORICAL_ALERT_STATUSES = mr.TERMINAL_ALERT_STATUSES
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


# ── Agent signal projection ─────────────────────────────────────────
# PR11: monitoring agents are signal sources only. They may surface
# source/timestamp/confidence/linkage metadata into Lifecycle, but they
# must not write officer-owned periodic-review judgment fields.
AGENT_SIGNAL_CATALOG: Dict[int, Dict[str, Any]] = {
    6: {
        "agent_name": "Periodic Review Preparation Agent",
        "signal_source": "Agent 6 - Periodic Review Preparation",
        "implementation": "deterministic",
    },
    7: {
        "agent_name": "Adverse Media & PEP Monitoring Agent",
        "signal_source": "Agent 7 - Adverse Media & PEP Monitoring",
        "implementation": "hybrid",
    },
    8: {
        "agent_name": "Behaviour & Risk Drift Agent",
        "signal_source": "Agent 8 - Behaviour & Risk Drift",
        "implementation": "deterministic",
    },
    10: {
        "agent_name": "Ongoing Compliance Review Agent",
        "signal_source": "Agent 10 - Ongoing Compliance Review",
        "implementation": "hybrid",
    },
}


def _normalise_signal_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _agent_for_alert_type(alert_type: Any, detected_by: Any = None) -> Tuple[int, str, str, float]:
    token = _normalise_signal_text(alert_type)
    detected = _normalise_signal_text(detected_by)
    if token in {"document_expired", "document_expiring_soon", "document_expiry"} or "document_health" in detected:
        return 10, "document_expiry", "KYC Documents", 0.92
    if any(part in token for part in ("screening_stale", "stale_screening", "screening_refresh")):
        return 10, "stale_screening", "Screening Queue", 0.88
    if any(part in token for part in ("adverse", "media", "pep", "sanction", "watchlist")):
        return 7, "adverse_media_pep_sanctions", "Screening Queue", 0.82
    if any(part in token for part in ("risk_drift", "threshold", "velocity", "transaction", "behaviour", "behavior")):
        return 8, "risk_drift", "Ongoing Monitoring", 0.78
    if any(part in token for part in ("ownership", "ubo", "director", "shareholder", "beneficial_owner")):
        return 6, "ownership_change_signal", "Change Management", 0.74
    return 10, "monitoring_signal", "Ongoing Monitoring", 0.70


def _agent_for_edd_item(item: Dict[str, Any]) -> Tuple[int, str, str, float]:
    basis = " ".join(
        str(item.get(k) or "")
        for k in ("trigger_source", "trigger_notes", "origin_context", "risk_level")
    )
    token = _normalise_signal_text(basis)
    if any(part in token for part in ("adverse", "media", "pep", "sanction", "screening")):
        return 7, "adverse_media_pep_sanctions", "EDD", 0.80
    if any(part in token for part in ("risk_drift", "threshold", "velocity", "behaviour", "behavior")):
        return 8, "risk_drift", "EDD", 0.76
    return 10, "ongoing_compliance_escalation", "EDD", 0.72


def _agent_signal_payload(
    *,
    agent_number: int,
    signal_type: str,
    timestamp: Any,
    confidence: float,
    linked_object: Dict[str, Any],
    recommended_destination_module: str,
    summary: Any = None,
    status: Any = None,
) -> Dict[str, Any]:
    agent = AGENT_SIGNAL_CATALOG[agent_number]
    return {
        "signal_type": signal_type,
        "source": agent["signal_source"],
        "source_agent_number": agent_number,
        "source_agent_name": agent["agent_name"],
        "source_agent_implementation": agent["implementation"],
        "authority": "decision_support",
        "timestamp": timestamp,
        "confidence": confidence,
        "linked_object": linked_object,
        "recommended_destination_module": recommended_destination_module,
        "summary": summary,
        "status": status,
    }


def _agent_signals_for_lifecycle_item(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build signal metadata for an already-materialised lifecycle item.

    This is deliberately projection-only: the signal payload mirrors the
    linked lifecycle object and contains no officer-owned fields such as
    rationale, attestation, outcome, completion state or memo conclusion.
    """
    kind = item.get("type")
    linked_object = {
        "type": kind,
        "id": item.get("id"),
        "application_id": item.get("application_id"),
    }
    if kind == "alert":
        agent_number, signal_type, destination, confidence = _agent_for_alert_type(
            item.get("alert_type"),
            item.get("detected_by"),
        )
        return [_agent_signal_payload(
            agent_number=agent_number,
            signal_type=signal_type,
            timestamp=item.get("discovered_at") or item.get("created_at"),
            confidence=confidence,
            linked_object=linked_object,
            recommended_destination_module=destination,
            summary=item.get("summary"),
            status=item.get("state"),
        )]
    if kind == "review":
        return [_agent_signal_payload(
            agent_number=6,
            signal_type="periodic_review_preparation",
            timestamp=item.get("created_at"),
            confidence=0.86,
            linked_object=linked_object,
            recommended_destination_module="Application Lifecycle",
            summary=item.get("review_reason") or item.get("trigger_source") or item.get("status_label"),
            status=item.get("state"),
        )]
    if kind == "edd":
        agent_number, signal_type, destination, confidence = _agent_for_edd_item(item)
        return [_agent_signal_payload(
            agent_number=agent_number,
            signal_type=signal_type,
            timestamp=item.get("created_at"),
            confidence=confidence,
            linked_object=linked_object,
            recommended_destination_module=destination,
            summary=item.get("trigger_notes") or item.get("origin_context") or item.get("trigger_source"),
            status=item.get("state"),
        )]
    return []


def _collect_agent_signals(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    signals: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        for signal in item.get("agent_signals") or []:
            linked = signal.get("linked_object") or {}
            key = (
                signal.get("source_agent_number"),
                signal.get("signal_type"),
                linked.get("type"),
                linked.get("id"),
            )
            if key in seen:
                continue
            seen.add(key)
            signals.append(signal)
    by_source: Dict[str, int] = {}
    for signal in signals:
        src = signal.get("source") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1
    return {
        "items": signals,
        "count": len(signals),
        "by_source": by_source,
    }


# ── Per-row materialisation ──────────────────────────────────────────
def _materialise_alert(row, *, user_names: Dict[str, str],
                       now: datetime) -> Dict[str, Any]:
    from lifecycle_quarantine import is_legacy_unmapped
    status = _normalise_alert_state(row)
    owner_id = _row_get(row, "reviewed_by")
    is_quarantined, quarantine_reasons = is_legacy_unmapped(row)
    item = {
        "type": "alert",
        "id": _row_get(row, "id"),
        "application_id": _row_get(row, "application_id"),
        "client_name": _row_get(row, "client_name", "") or "",
        "state": status,
        # PR-A: a quarantined row is NEITHER active NOR historical even
        # if its status would otherwise place it in one of those buckets.
        # The third bucket is explicit and additive.
        "is_active": (not is_quarantined) and mr.is_alert_unresolved(row) and status in ACTIVE_ALERT_STATUSES,
        "is_historical": (not is_quarantined) and mr.is_alert_terminal(row),
        "is_legacy_unmapped": is_quarantined,
        "quarantine_reasons": quarantine_reasons,
        "severity": _row_get(row, "severity"),
        "alert_type": _row_get(row, "alert_type"),
        "detected_by": _row_get(row, "detected_by"),
        "discovered_at": _row_get(row, "discovered_at"),
        "summary": _row_get(row, "summary"),
        "source_reference": _row_get(row, "source_reference"),
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
    item["agent_signals"] = _agent_signals_for_lifecycle_item(item)
    return item


def _materialise_review(row, *, user_names: Dict[str, str],
                        projection: Dict[str, Any],
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
    owner_id = _row_get(row, "assigned_officer")
    status_label = projection.get("status_label")
    attestation_status = str(projection.get("attestation_status") or "").strip().lower()
    if status in ACTIVE_REVIEW_STATES and attestation_status and attestation_status != "submitted" and status_label not in {"Blocked", "Completed"}:
        status_label = "Awaiting client attestation"
    item = {
        "type": "review",
        "id": _row_get(row, "id"),
        "application_id": _row_get(row, "application_id"),
        "client_name": _row_get(row, "client_name", "") or "",
        "state": status,
        "is_active": status in ACTIVE_REVIEW_STATES,
        "is_historical": status in HISTORICAL_REVIEW_STATES,
        "risk_level": projection.get("risk_level") or _row_get(row, "risk_level"),
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
        "last_review_date": projection.get("last_review_date"),
        "next_review_date": projection.get("next_review_date"),
        "started_at": _row_get(row, "started_at"),
        "completed_at": _row_get(row, "completed_at"),
        "state_changed_at": _row_get(row, "state_changed_at"),
        "assigned_officer": owner_id,
        "status_label": "Historical / Superseded" if status in HISTORICAL_REVIEW_STATES else status_label,
        "blocker_count": projection.get("blocker_count", 0),
        "blocker_summary": projection.get("blocker_summary", []),
        "memo_status": projection.get("memo_status"),
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
    item["agent_signals"] = _agent_signals_for_lifecycle_item(item)
    return item


def _materialise_edd(row, *, user_names: Dict[str, str],
                     findings_present: bool,
                     memo_context: Optional[Dict[str, Any]],
                     now: datetime) -> Dict[str, Any]:
    from investigation_scope import is_routine_onboarding_policy_case

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
    # Only override memo_context from fixture payload data. Non-fixture rows
    # already have their memo_context resolved by _safe_resolve_memo_context;
    # we must not clobber it even when linked_periodic_review_id is set.
    if payload and linked_review is not None and (not memo_context or memo_context.get("kind") != "periodic_review"):
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
    item = {
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
        "is_legacy_onboarding_policy_case": is_routine_onboarding_policy_case(row),
        "scope": (
            "routine_onboarding_enhanced_review"
            if is_routine_onboarding_policy_case(row)
            else "formal_investigation"
        ),
        "findings_present": bool(findings_present),
        "memo_context": memo_context,
        "fixture_payload": payload,
        "next_action": _next_action_for_edd(stage),
    }
    item["agent_signals"] = _agent_signals_for_lifecycle_item(item)
    return item


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
def _is_missing_column_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "no such column" in msg
        or "undefined column" in msg
        or ("column" in msg and "does not exist" in msg)
    )


def _row_matches_alert_include(row, include: str) -> bool:
    from lifecycle_quarantine import is_legacy_unmapped

    status = _normalise_alert_state(row)
    is_quarantined, _ = is_legacy_unmapped(row)
    if include == "active":
        return (not is_quarantined) and mr.is_alert_unresolved(row) and status in ACTIVE_ALERT_STATUSES
    if include == "historical":
        return (not is_quarantined) and mr.is_alert_terminal(row)
    if include == "legacy_unmapped":
        return is_quarantined
    if include == "all":
        return not is_quarantined
    return False


def _python_filter_alert_rows(rows: List[Any], include: str) -> List[Any]:
    return [row for row in rows if _row_matches_alert_include(row, include)]


def _supports_application_fixture_text_filter(db) -> bool:
    """Return true when the applications table has fields needed by text filters."""
    if getattr(db, "is_postgres", False):
        return True
    try:
        columns = {
            str(row["name"] if hasattr(row, "keys") else row[1]).lower()
            for row in db.execute("PRAGMA table_info(applications)").fetchall()
        }
    except Exception:
        return False
    return {"ref", "company_name"}.issubset(columns)


def _fetch_alerts(db, *, application_id=None, include="active",
                  exclude_fixtures=True) -> List[Any]:
    from lifecycle_quarantine import (
        legacy_unmapped_where_clause,
        active_or_historical_exclude_legacy_clause,
    )
    from fixture_filter import fixture_app_id_exclude_clause
    base_sql = "SELECT * FROM monitoring_alerts WHERE 1=1"
    base_params: List[Any] = []
    if application_id is not None:
        base_sql += " AND application_id = ?"
        base_params.append(application_id)
    # Fixture exclusion: omit rows linked to fixture applications.
    # Applied to base_sql so it is also honoured in the fallback path.
    if exclude_fixtures:
        fx_excl, fx_p = fixture_app_id_exclude_clause(
            "application_id",
            include_text_patterns=_supports_application_fixture_text_filter(db),
        )
        base_sql += f" AND {fx_excl}"
        base_params.extend(fx_p)
    sql = base_sql
    params = list(base_params)
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
    if getattr(db, "is_postgres", False):
        # psycopg2 treats `%` in SQL as pyformat control unless escaped.
        # Quarantine clauses include LIKE 'FIX_SCEN%'; make it safe.
        # Note: fixture_app_id_exclude_clause uses parameterised `?`
        # (translated to `%s` by db._translate_query), so the literal
        # `%` in the pattern value is passed as a bound parameter and
        # is NOT affected by this text replacement.
        sql = sql.replace("%", "%%")
    try:
        return db.execute(sql, params).fetchall() or []
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        fallback_sql = base_sql + " ORDER BY created_at DESC"
        rows = db.execute(fallback_sql, base_params).fetchall() or []
        return _python_filter_alert_rows(rows, include)


def _fetch_reviews(db, *, application_id=None, include="active",
                   exclude_fixtures=True) -> List[Any]:
    # PR-A: periodic_reviews are NOT subject to quarantine (they have
    # schema-defined CHECK-constrained states). The legacy_unmapped
    # bucket is monitoring_alerts-only.
    if include == "legacy_unmapped":
        return []
    from fixture_filter import fixture_app_id_exclude_clause
    sql = "SELECT * FROM periodic_reviews WHERE 1=1"
    params: List[Any] = []
    if application_id is not None:
        sql += " AND application_id = ?"
        params.append(application_id)
    if exclude_fixtures:
        fx_excl, fx_p = fixture_app_id_exclude_clause(
            "application_id",
            include_text_patterns=_supports_application_fixture_text_filter(db),
        )
        sql += f" AND {fx_excl}"
        params.extend(fx_p)
    sql += " ORDER BY created_at DESC"
    return db.execute(sql, params).fetchall() or []


def _fetch_edd(db, *, application_id=None, include="active",
               exclude_fixtures=True) -> List[Any]:
    # PR-A: edd_cases are NOT subject to quarantine (schema-defined
    # CHECK-constrained stages). The legacy_unmapped bucket is
    # monitoring_alerts-only.
    if include == "legacy_unmapped":
        return []
    from fixture_filter import fixture_app_id_exclude_clause
    sql = "SELECT * FROM edd_cases WHERE 1=1"
    params: List[Any] = []
    if application_id is not None:
        sql += " AND application_id = ?"
        params.append(application_id)
    if exclude_fixtures:
        # edd_cases.application_id is NOT NULL but use the shared NULL-safe
        # helper for uniformity (the IS NULL guard is a no-op here).
        fx_excl, fx_p = fixture_app_id_exclude_clause(
            "application_id",
            include_text_patterns=_supports_application_fixture_text_filter(db),
        )
        sql += f" AND {fx_excl}"
        params.extend(fx_p)
    sql += " ORDER BY triggered_at DESC"
    rows = db.execute(sql, params).fetchall() or []
    if application_id is None:
        from investigation_scope import is_formal_investigation_case

        rows = [row for row in rows if is_formal_investigation_case(row)]
    return rows


def build_lifecycle_queue(
    db,
    *,
    include: str = "active",
    types: Optional[Tuple[str, ...]] = None,
    application_id: Optional[str] = None,
    now: Optional[datetime] = None,
    exclude_fixtures: bool = True,
    limit: Optional[int] = None,
    offset: int = 0,
) -> Dict[str, Any]:
    """Aggregate monitoring alerts, periodic reviews and EDD cases.

    Args:
        db:               active DB connection.
        include:          'active' (default), 'historical' or 'all'.
        types:            subset of ('alert', 'review', 'edd') or None for all.
        application_id:   scope to a single application or None for all.
        now:              override the reference time for age calculations
                          (test convenience).
        exclude_fixtures: when True (default) rows linked to fixture
                          applications (id LIKE 'f1xed%') are omitted.
                          Pass False (admin/sco only, via show_fixtures=true
                          query param on the HTTP handler) to restore them.

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
        alert_rows = _fetch_alerts(db, application_id=application_id,
                                   include=include, exclude_fixtures=exclude_fixtures)
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
        review_rows = _fetch_reviews(db, application_id=application_id,
                                     include=include, exclude_fixtures=exclude_fixtures)
        owner_ids = [_row_get(r, "assigned_officer") for r in review_rows]
        names = _user_name_map(db, owner_ids)
        projection_map = {
            projection["review_id"]: projection
            for projection in list_review_projections(
                db,
                review_ids=[_row_get(r, "id") for r in review_rows],
            )
        }
        for r in review_rows:
            items_count = len(_decode_required_items(_row_get(r, "required_items")))
            item = _materialise_review(
                r, user_names=names,
                projection=projection_map.get(_row_get(r, "id"), {}),
                required_items_count=items_count,
                now=ref_now,
            )
            if _include_item(item):
                items.append(item)
                counts["review"] += 1

    # --- EDD ----------------------------------------------------------
    edd_rows: List[Any] = []
    if "edd" in selected_types:
        edd_rows = _fetch_edd(db, application_id=application_id,
                              include=include, exclude_fixtures=exclude_fixtures)
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
    total_items = len(items)
    if offset < 0:
        offset = 0
    if limit is None:
        visible_items = items
        page_limit = total_items
    else:
        page_limit = max(1, int(limit))
        visible_items = items[offset:offset + page_limit]
    agent_signals = _collect_agent_signals(visible_items)

    return {
        "items": visible_items,
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
        "pagination": {
            "limit": page_limit,
            "offset": offset,
            "returned": len(visible_items),
            "total_items": total_items,
            "has_next": bool(limit is not None and (offset + page_limit) < total_items),
            "has_prev": offset > 0,
        },
        "agent_signals": agent_signals,
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
        db, include="active", application_id=application_id, now=ref_now, limit=None,
    )
    historical = build_lifecycle_queue(
        db, include="historical", application_id=application_id, now=ref_now, limit=None,
    )
    review_setup = None
    for projection in list_review_projections(db, application_id=application_id):
        status = str(projection.get("status") or "").strip().lower()
        if status not in HISTORICAL_REVIEW_STATES:
            review_setup = {
                **projection,
                "id": projection.get("review_id"),
                "review_id": projection.get("review_id"),
                "is_active_work": status in ACTIVE_REVIEW_STATES,
                "source": "periodic_reviews",
            }
            break

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

    active_signals = (active.get("agent_signals") or {}).get("items", [])
    historical_signals = (historical.get("agent_signals") or {}).get("items", [])

    return {
        "application_id": application_id,
        "active": active,
        "historical": historical,
        "review_setup": review_setup,
        "linkage": {"edges": edges, "count": len(edges)},
        "agent_signals": {
            "items": active_signals,
            "historical_items": historical_signals,
            "count": len(active_signals),
            "historical_count": len(historical_signals),
            "by_source": (active.get("agent_signals") or {}).get("by_source", {}),
        },
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
