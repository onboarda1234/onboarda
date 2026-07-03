"""
monitoring_sla.py — Derived SLA / aging fields for monitoring alerts (M2.1 PR-1).
================================================================================

READ-ONLY by construction. This module is a set of PURE functions: it never
opens a DB connection, never writes, never mutates, and never emits a
``monitoring_alerts.status`` value. SLA state is a *derived display* computed
from timestamps already on the alert row (mirrors the M1.1/M2.2 derived-state
approach) — it must never be persisted.

SLA model (M2.1 conservative pilot defaults, time-to-first-decision):

    severity   business-day target
    --------   -------------------
    critical   1
    high       3
    medium     10
    low        20

* The SLA clock STARTS at ``discovered_at`` (fallback ``created_at``).
* The clock STOPS at the terminal/decision timestamp (``resolved_at``) for a
  closed alert; open alerts age against "now".
* "Business days" is Mon-Fri, no holiday calendar — a documented pilot
  approximation, not a legal SLA guarantee.

The single entry point is :func:`derive`, returning a flat dict of the derived
fields to merge onto a projected alert row. It never raises on malformed input
(returns ``sla_state='unknown'`` with null fields instead).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import monitoring_status as _ms

# ── SLA config (business days to first decision) ─────────────────────────────
SLA_BUSINESS_DAYS: Dict[str, int] = {
    "critical": 1,
    "high": 3,
    "medium": 10,
    "low": 20,
}
# Severity that cannot be classified falls back to the medium target: tight
# enough to surface, loose enough to avoid false-overdue noise.
_DEFAULT_SLA_DAYS = SLA_BUSINESS_DAYS["medium"]

# Allowed derived states (locked by test).
SLA_STATES = ("within_sla", "due_soon", "overdue", "closed", "unknown")

_SEVERITY_ALIASES = {
    "urgent": "critical",
    "very_high": "critical",
    "veryhigh": "critical",
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "med": "medium",
    "low": "low",
    "info": "low",
    "informational": "low",
}


def _norm_severity(value: Any) -> str:
    tok = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _SEVERITY_ALIASES.get(tok, "unknown")


def sla_days_for_severity(value: Any) -> int:
    return SLA_BUSINESS_DAYS.get(_norm_severity(value), _DEFAULT_SLA_DAYS)


# ── Timestamp parsing (SQLite TEXT + Postgres datetime, tz-naive UTC) ────────
def _parse_ts(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("T", " ")
        if s.endswith("Z"):
            s = s[:-1]
        # Drop an explicit offset like "+00:00" for naive-UTC comparison.
        for cut in ("+", ):
            if cut in s[10:]:
                s = s[:10] + s[10:].split(cut)[0]
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s.strip(), fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Business-day helpers (Mon-Fri) ───────────────────────────────────────────
def business_days_between(start: datetime, end: datetime) -> int:
    """Number of business days (Mon-Fri) elapsed in the half-open (start, end]."""
    if end <= start:
        return 0
    d0, d1 = start.date(), end.date()
    total = (d1 - d0).days
    weeks, extra = divmod(total, 7)
    count = weeks * 5
    for i in range(1, extra + 1):
        if (d0 + timedelta(days=i)).weekday() < 5:
            count += 1
    return count


def add_business_days(start: datetime, n: int) -> datetime:
    """Return the datetime n business days after start (n small: SLA targets)."""
    d = start
    added = 0
    while added < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _chip(state: str, days_overdue: Optional[int]) -> tuple:
    """(label, tone) for the UI chip. tone: green/amber/red/grey."""
    if state == "overdue":
        return (f"Overdue {days_overdue}d" if days_overdue else "Overdue", "red")
    if state == "due_soon":
        return ("Due soon", "amber")
    if state == "within_sla":
        return ("On track", "green")
    if state == "closed":
        return ("Closed", "grey")
    return ("—", "grey")


# ── Entry point ──────────────────────────────────────────────────────────────
def derive(alert: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return derived SLA/aging fields for one alert row (dict or Row).

    Pure and read-only. Never persists, never emits a status value. Keys:
    age_days, business_age_days, sla_days, sla_due_at, sla_state, sla_breached,
    days_until_due, days_overdue, sla_label, sla_tone, closed_within_sla.
    """
    def _get(key):
        try:
            v = alert.get(key) if hasattr(alert, "get") else alert[key]
        except (KeyError, IndexError, TypeError):
            return None
        return v

    now = now or _utcnow()
    start = _parse_ts(_get("discovered_at")) or _parse_ts(_get("created_at"))
    sla_days = sla_days_for_severity(_get("severity"))

    # Closed clock stops at the terminal timestamp; is_terminal also honours a
    # non-empty resolved_at (mirrors monitoring_status.is_terminal exactly).
    resolved_at = _parse_ts(_get("resolved_at"))
    terminal = _ms.is_terminal(_get("status"), _get("resolved_at"))
    stop = resolved_at if (terminal and resolved_at) else (now if not terminal else None)

    base = {
        "age_days": None,
        "business_age_days": None,
        "sla_days": sla_days,
        "sla_due_at": None,
        "sla_state": "unknown",
        "sla_breached": False,
        "days_until_due": None,
        "days_overdue": None,
        "sla_label": "—",
        "sla_tone": "grey",
        "closed_within_sla": None,
    }
    if start is None:
        return base

    ref = stop or now  # closed-without-timestamp falls back to now for age only
    age_days = max((ref.date() - start.date()).days, 0)
    business_age = business_days_between(start, ref)
    due_at = add_business_days(start, sla_days)
    breached = business_age > sla_days

    base.update({
        "age_days": age_days,
        "business_age_days": business_age,
        "sla_due_at": due_at.strftime("%Y-%m-%d %H:%M:%S"),
        "sla_breached": bool(breached),
    })

    if terminal:
        # Closed alerts are never shown as active overdue.
        base["sla_state"] = "closed"
        base["closed_within_sla"] = (not breached)
        label, tone = _chip("closed", None)
        base["sla_label"], base["sla_tone"] = label, tone
        return base

    remaining = sla_days - business_age
    if business_age > sla_days:
        state, days_overdue, days_until = "overdue", business_age - sla_days, None
    elif remaining <= 1:
        state, days_overdue, days_until = "due_soon", None, remaining
    else:
        state, days_overdue, days_until = "within_sla", None, remaining
    label, tone = _chip(state, days_overdue)
    base.update({
        "sla_state": state,
        "days_overdue": days_overdue,
        "days_until_due": days_until,
        "sla_label": label,
        "sla_tone": tone,
    })
    return base


__all__ = [
    "SLA_BUSINESS_DAYS",
    "SLA_STATES",
    "add_business_days",
    "business_days_between",
    "derive",
    "sla_days_for_severity",
]
