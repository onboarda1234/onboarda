"""Investigation queue scope helpers.

PR 5B separates routine onboarding enhanced evidence from formal
investigation cases.  Policy routing may still decide that onboarding
requires enhanced review, but that is not the same as creating a
Lifecycle / Investigation Queue case.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


ROUTINE_ONBOARDING_ROUTING_SOURCES = frozenset(
    {
        "prescreening_submit",
        "risk_recompute",
        "manual_reconciliation",
        "memo_generation",
        "memo_supervisor",
    }
)

EXPLICIT_INVESTIGATION_ROUTING_SOURCES = frozenset(
    {
        "screening_update",
        "officer_decision",
        "manual",
        "manual_onboarding_escalation",
        "monitoring_alert",
        "periodic_review",
        "change_request",
    }
)

FORMAL_INVESTIGATION_ORIGINS = frozenset(
    {
        "monitoring_alert",
        "periodic_review",
        "change_request",
        "manual",
        "manual_onboarding_escalation",
        "onboarding_escalation",
    }
)

ROUTINE_POLICY_ORIGINS = frozenset(
    {
        "",
        "onboarding",
        "policy_routing",
        "routine_onboarding",
        "routine_onboarding_enhanced_review",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return getattr(row, key, default)


def should_suppress_routine_onboarding_investigation(
    edd_routing: Optional[Mapping[str, Any]],
    supervisor_result: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Return true when an EDD policy result should *not* create a case.

    Routine onboarding triggers are handled by Enhanced Review Requirements.
    Explicit officer/lifecycle escalation sources still create formal
    Investigation Cases.
    """

    if not isinstance(edd_routing, Mapping):
        return False
    if _lower(edd_routing.get("route")) != "edd":
        return False

    source = _lower(edd_routing.get("source"))
    if not source:
        return False
    if source in EXPLICIT_INVESTIGATION_ROUTING_SOURCES:
        return False
    if source in ROUTINE_ONBOARDING_ROUTING_SOURCES:
        return True

    triggers = {_lower(t) for t in (edd_routing.get("triggers") or []) if _text(t)}
    if not triggers:
        return False

    # Unknown non-explicit routing sources with policy triggers are treated as
    # onboarding enhanced-review work unless they are added to the explicit
    # investigation allow-list above.
    return True


def routine_onboarding_suppression_detail(
    edd_routing: Optional[Mapping[str, Any]],
    supervisor_result: Optional[Mapping[str, Any]] = None,
) -> dict:
    return {
        "formal_case_suppressed": True,
        "suppression_reason": "routine_onboarding_enhanced_review",
        "source": _text((edd_routing or {}).get("source")),
        "triggers": list((edd_routing or {}).get("triggers") or []),
        "mandatory_escalation_reasons": list(
            (supervisor_result or {}).get("mandatory_escalation_reasons") or []
        ),
    }


def is_routine_onboarding_policy_case(case_row: Any, app_row: Any = None) -> bool:
    """Identify legacy/default queue rows that are not formal investigations."""

    linked_alert = _row_get(case_row, "linked_monitoring_alert_id")
    linked_review = _row_get(case_row, "linked_periodic_review_id")
    if linked_alert not in (None, "") or linked_review not in (None, ""):
        return False

    origin = _lower(_row_get(case_row, "origin_context"))
    if origin in FORMAL_INVESTIGATION_ORIGINS:
        return False

    trigger_source = _lower(_row_get(case_row, "trigger_source"))
    if trigger_source and trigger_source != "policy_routing":
        return False

    if origin and origin not in ROUTINE_POLICY_ORIGINS:
        return False

    notes = _lower(_row_get(case_row, "trigger_notes"))
    if "auto-routed to edd by policy" in notes or trigger_source == "policy_routing":
        return True

    return False


def is_formal_investigation_case(case_row: Any, app_row: Any = None) -> bool:
    return not is_routine_onboarding_policy_case(case_row, app_row=app_row)
