"""M2.1 PR-1 — derived SLA/aging tests.

Proves the SLA derivation is READ-ONLY (pure function, no DB, no writes),
emits no monitoring_alerts.status value, and computes the conservative pilot
SLA (critical=1, high=3, medium=10, low=20 business days) correctly, including
that closed alerts never render as active overdue.
"""
import os
import re
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring_sla as sla

# 2024-01-01 is a Monday — anchor so business-day math is explicit.
MON = datetime(2024, 1, 1, 9, 0, 0)   # Mon
TUE = datetime(2024, 1, 2, 9, 0, 0)   # +1 business day
WED = datetime(2024, 1, 3, 9, 0, 0)   # +2
THU = datetime(2024, 1, 4, 9, 0, 0)   # +3
FRI = datetime(2024, 1, 5, 9, 0, 0)   # +4
NEXT_MON = datetime(2024, 1, 8, 9, 0, 0)  # +5 (weekend skipped)


def _alert(**kw):
    base = {"discovered_at": MON.strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": MON.strftime("%Y-%m-%d %H:%M:%S"),
            "severity": "high", "status": "open", "resolved_at": None}
    base.update(kw)
    return base


# ── business-day helpers ─────────────────────────────────────────────────────
def test_business_days_between_skips_weekend():
    assert sla.business_days_between(MON, TUE) == 1
    assert sla.business_days_between(MON, THU) == 3
    assert sla.business_days_between(MON, NEXT_MON) == 5  # weekend skipped
    assert sla.business_days_between(MON, MON) == 0


def test_add_business_days_skips_weekend():
    assert sla.add_business_days(MON, 1).date() == TUE.date()
    assert sla.add_business_days(FRI, 1).date() == NEXT_MON.date()


# ── SLA target per severity ──────────────────────────────────────────────────
@pytest.mark.parametrize("severity,expected", [
    ("critical", 1), ("urgent", 1), ("very_high", 1),
    ("high", 3),
    ("medium", 10), ("med", 10),
    ("low", 20), ("info", 20),
    ("weird-value", 10), (None, 10),  # unknown → medium fallback
])
def test_sla_days_by_severity(severity, expected):
    assert sla.sla_days_for_severity(severity) == expected


def test_critical_is_one_business_day():
    # Tue = exactly at target (business_age 1, remaining 0) → due_soon.
    d = sla.derive(_alert(severity="critical"), now=TUE)
    assert d["sla_days"] == 1 and d["business_age_days"] == 1
    assert d["sla_state"] == "due_soon" and d["sla_breached"] is False
    # Wed = 1 business day past target → overdue by 1.
    d = sla.derive(_alert(severity="critical"), now=WED)
    assert d["sla_state"] == "overdue" and d["days_overdue"] == 1
    assert d["sla_breached"] is True


def test_high_is_three_business_days():
    d = sla.derive(_alert(severity="high"), now=TUE)   # age1, remaining 2
    assert d["sla_state"] == "within_sla" and d["days_until_due"] == 2
    d = sla.derive(_alert(severity="high"), now=THU)   # age3, remaining 0
    assert d["sla_state"] == "due_soon" and d["sla_breached"] is False
    d = sla.derive(_alert(severity="high"), now=NEXT_MON)  # age5 > 3
    assert d["sla_state"] == "overdue" and d["days_overdue"] == 2


def test_due_at_is_start_plus_business_days():
    d = sla.derive(_alert(severity="high"), now=TUE)
    assert d["sla_due_at"] == THU.strftime("%Y-%m-%d %H:%M:%S")  # Mon +3 bdays = Thu


# ── closed alerts never show as active overdue ───────────────────────────────
@pytest.mark.parametrize("status", ["resolved", "dismissed", "closed", "waived", "routed_to_edd"])
def test_closed_alert_is_never_active_overdue(status):
    # Very old start, but terminal → 'closed', not 'overdue'.
    d = sla.derive(_alert(severity="critical", status=status,
                          resolved_at=NEXT_MON.strftime("%Y-%m-%d %H:%M:%S")),
                   now=datetime(2024, 2, 1, 9, 0, 0))
    assert d["sla_state"] == "closed"
    assert d["days_overdue"] is None


def test_closed_within_sla_vs_breached():
    within = sla.derive(_alert(severity="high", status="resolved",
                               resolved_at=TUE.strftime("%Y-%m-%d %H:%M:%S")), now=WED)
    assert within["sla_state"] == "closed" and within["closed_within_sla"] is True
    breached = sla.derive(_alert(severity="high", status="resolved",
                                 resolved_at=datetime(2024, 1, 10, 9, 0, 0).strftime("%Y-%m-%d %H:%M:%S")),
                          now=datetime(2024, 1, 11, 9, 0, 0))
    assert breached["sla_state"] == "closed" and breached["closed_within_sla"] is False
    assert breached["sla_breached"] is True


def test_resolved_at_stops_clock_even_without_terminal_status():
    # is_terminal honours a non-empty resolved_at regardless of status text.
    d = sla.derive(_alert(severity="critical", status="open",
                          resolved_at=TUE.strftime("%Y-%m-%d %H:%M:%S")),
                   now=datetime(2024, 3, 1, 9, 0, 0))
    assert d["sla_state"] == "closed"


# ── robustness ───────────────────────────────────────────────────────────────
def test_discovered_at_preferred_then_created_at_fallback():
    d = sla.derive({"discovered_at": None, "created_at": MON.strftime("%Y-%m-%d %H:%M:%S"),
                    "severity": "high", "status": "open"}, now=TUE)
    assert d["business_age_days"] == 1  # fell back to created_at


def test_missing_timestamps_returns_unknown_not_crash():
    d = sla.derive({"severity": "high", "status": "open"}, now=TUE)
    assert d["sla_state"] == "unknown" and d["sla_due_at"] is None


def test_sla_state_only_allowed_values():
    for status in ("open", "resolved", "dismissed"):
        for sev in ("critical", "high", "medium", "low", None):
            d = sla.derive(_alert(severity=sev, status=status,
                                  resolved_at=(TUE.strftime("%Y-%m-%d %H:%M:%S")
                                               if status != "open" else None)),
                           now=WED)
            assert d["sla_state"] in sla.SLA_STATES


# ── read-only / no-new-status guarantees ─────────────────────────────────────
def test_derive_emits_no_alert_status_value():
    """The derived block must not carry a monitoring_alerts.status value."""
    import monitoring_status as ms
    d = sla.derive(_alert(), now=WED)
    assert "status" not in d  # never re-emits the stored status key
    assert d.get("sla_state") not in ms.CANONICAL_ALERT_STATUSES  # distinct vocabulary


def test_module_is_read_only_no_db_or_writes():
    """Source-level proof PR-1 is read-only: no SQL writes, no db/commit/execute."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "monitoring_sla.py")).read()
    lowered = src.lower()
    for banned in ("insert ", "update ", "delete ", ".commit(", ".execute(", "import db"):
        assert banned not in lowered, f"monitoring_sla must be read-only; found {banned!r}"


def test_derive_does_not_mutate_input():
    row = _alert()
    snapshot = dict(row)
    sla.derive(row, now=WED)
    assert row == snapshot  # input untouched


# ── list-projection consistency ──────────────────────────────────────────────
def test_list_projection_includes_sla_block():
    import server
    row = _alert(id=123, severity="high", status="open")
    item = server._monitoring_list_project_row(dict(row))
    assert "sla" in item
    # list projection is the same single source as the detail handler.
    for key in ("age_days", "business_age_days", "sla_days", "sla_due_at",
                "sla_state", "sla_breached", "days_until_due", "days_overdue",
                "sla_label", "sla_tone"):
        assert key in item["sla"]
    assert item["sla"]["sla_days"] == 3  # high


def test_list_projection_sla_is_sortable_numeric():
    import server
    rows = [_alert(id=1, severity="critical"), _alert(id=2, severity="low")]
    items = [server._monitoring_list_project_row(dict(r)) for r in rows]
    # business_age_days / sla_days usable as sort keys (ints, never None here).
    assert all(isinstance(i["sla"]["sla_days"], int) for i in items)
