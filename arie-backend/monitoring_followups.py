"""
monitoring_followups.py — Officer follow-up tracker for monitoring alerts (M2.1 PR-2).
=====================================================================================

An additive annotation ledger: officers record what they did / what's next on an
alert that isn't yet closable (a note, a next step, a snooze date, a client
contact, a pending-review marker). A follow-up **never changes the alert's
status** — "open follow-ups" and "next due" are DERIVED from these rows and
surfaced on the list/detail, mirroring the M1.1/M2.2 derived-state approach.

All writes are additive to ``monitoring_alert_followups`` (migration 038); no
column on ``monitoring_alerts`` is read for mutation or written here. The two
mutating entry points (:func:`add_followup`, :func:`resolve_followup`) emit
``monitoring.alert.followup_added`` / ``monitoring.alert.followup_resolved``
audit events via the caller-supplied ``audit_writer``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

ACTIONS = ("note", "next_step", "snooze_until", "contacted_client", "pending_review", "other")
# Actions whose whole point is a date; a due_at is mandatory for these.
_DATE_REQUIRED_ACTIONS = ("snooze_until",)


class FollowupError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


def _row(row) -> Optional[Dict[str, Any]]:
    return dict(row) if row else None


def _norm_action(value: Any) -> str:
    tok = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return tok or "note"


# ── Writes ───────────────────────────────────────────────────────────────────
def add_followup(db, *, alert_id, action, note, due_at, user, audit_writer) -> Dict[str, Any]:
    """Record a follow-up on an alert. Additive only — never touches the alert row."""
    act = _norm_action(action)
    if act not in ACTIONS:
        raise FollowupError(f"Unsupported follow-up action '{act}'.", 400)
    note = (note or "").strip() or None
    due_at = (str(due_at).strip() or None) if due_at is not None else None
    if act in _DATE_REQUIRED_ACTIONS and not due_at:
        raise FollowupError("A due/snooze date is required for this follow-up.", 400)
    if not note and not due_at:
        raise FollowupError("A follow-up needs a note or a due date.", 400)

    created_by = (user or {}).get("sub", "")
    # RETURNING binds the response/audit to the EXACT row this call inserted —
    # a concurrent insert on the same alert cannot be mis-selected (a plain
    # "ORDER BY id DESC" by alert_id could pick up another officer's row).
    row = db.execute(
        """
        INSERT INTO monitoring_alert_followups (alert_id, action, note, due_at, created_by)
        VALUES (?,?,?,?,?)
        RETURNING *
        """,
        (alert_id, act, note, due_at, created_by),
    ).fetchone()
    db.commit()
    followup = _row(row)
    audit_writer(
        dict(user or {}),
        "monitoring.alert.followup_added",
        f"monitoring_alert:{alert_id}",
        json.dumps({
            "followup_id": (followup or {}).get("id"),
            "alert_id": alert_id,
            "action": act,
            "has_note": bool(note),
            "due_at": due_at,
            "created_by": created_by,
        }, sort_keys=True),
        db=db,
    )
    db.commit()
    return followup


def resolve_followup(db, *, followup_id, alert_id, user, audit_writer) -> Dict[str, Any]:
    """Close an open follow-up. Idempotency-safe: verifies the row was actually
    open before recording (DBConnection has no rowcount), so a duplicate/racing
    resolve is a handled 409 rather than a false audit."""
    existing = fetch(db, followup_id)
    if not existing or str(existing.get("alert_id")) != str(alert_id):
        raise FollowupError("Follow-up not found.", 404)
    if existing.get("resolved_at"):
        raise FollowupError("Follow-up is already resolved.", 409)
    resolver = (user or {}).get("sub", "")
    db.execute(
        """
        UPDATE monitoring_alert_followups
           SET resolved_at = CURRENT_TIMESTAMP, resolved_by = ?
         WHERE id = ? AND resolved_at IS NULL
        """,
        (resolver, followup_id),
    )
    db.commit()
    after = fetch(db, followup_id)
    if not after or not after.get("resolved_at"):
        raise FollowupError("Follow-up is already resolved.", 409)
    audit_writer(
        dict(user or {}),
        "monitoring.alert.followup_resolved",
        f"monitoring_alert:{alert_id}",
        json.dumps({
            "followup_id": followup_id,
            "alert_id": alert_id,
            "resolved_by": resolver,
        }, sort_keys=True),
        db=db,
    )
    db.commit()
    return after


# ── Reads / derived surfacing ────────────────────────────────────────────────
def fetch(db, followup_id) -> Optional[Dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM monitoring_alert_followups WHERE id = ?", (followup_id,)
    ).fetchone()
    return _row(row)


def list_for_alert(db, alert_id) -> List[Dict[str, Any]]:
    """All follow-ups for an alert: open first, then most recent."""
    rows = db.execute(
        """
        SELECT * FROM monitoring_alert_followups
         WHERE alert_id = ?
         ORDER BY (CASE WHEN resolved_at IS NULL THEN 0 ELSE 1 END), created_at DESC, id DESC
        """,
        (alert_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def open_summary(db, alert_id) -> Dict[str, Any]:
    """Derived {open_count, next_due_at} for one alert."""
    rows = db.execute(
        "SELECT due_at FROM monitoring_alert_followups WHERE alert_id = ? AND resolved_at IS NULL",
        (alert_id,),
    ).fetchall()
    open_count = len(rows)
    due_dates = sorted(str(r["due_at"]) for r in rows if r["due_at"])
    return {"open_count": open_count, "next_due_at": (due_dates[0] if due_dates else None)}


def open_summary_for_alerts(db, rows) -> Dict[Any, Dict[str, Any]]:
    """Batch {alert_id: {open_count, next_due_at}} for the list projection.
    Degrades quietly to empty if the table predates migration 038."""
    alert_ids = [r["id"] for r in rows if r["id"] is not None]
    if not alert_ids:
        return {}
    summary: Dict[Any, Dict[str, Any]] = {}
    chunk_size = 400
    for start in range(0, len(alert_ids), chunk_size):
        chunk = alert_ids[start:start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        try:
            found = db.execute(
                f"SELECT alert_id, due_at FROM monitoring_alert_followups "
                f"WHERE resolved_at IS NULL AND alert_id IN ({placeholders})",
                chunk,
            ).fetchall()
        except Exception:
            return {}
        for r in found:
            entry = summary.setdefault(r["alert_id"], {"open_count": 0, "next_due_at": None})
            entry["open_count"] += 1
            due = r["due_at"]
            if due and (entry["next_due_at"] is None or str(due) < str(entry["next_due_at"])):
                entry["next_due_at"] = str(due)
    return summary


__all__ = [
    "ACTIONS",
    "FollowupError",
    "add_followup",
    "fetch",
    "list_for_alert",
    "open_summary",
    "open_summary_for_alerts",
    "resolve_followup",
]
