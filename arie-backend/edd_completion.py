"""Canonical EDD completion recognition helpers.

These helpers deliberately avoid importing ``server`` so they can be used by
memo generation, supervisor/routing actuation, and test harnesses without
creating import cycles.  They are read-only and never commit transactions.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, Mapping, Optional, Set

logger = logging.getLogger(__name__)

APPROVED_EDD_STAGES = {"edd_approved", "approved", "completed"}
SATISFIED_REQUIREMENT_STATUSES = {"accepted", "waived", "cancelled"}
DERIVED_TRIGGER_ALIASES = {"high_or_very_high_risk"}
SPECIFIC_EDD_TRIGGERS = {
    "declared_pep_present",
    "high_risk_sector",
    "crypto_or_virtual_asset_sector",
    "elevated_jurisdiction",
    "opaque_or_incomplete_ownership",
    "material_screening_concern",
    "screening_needs_more_information",
    "edd_flag:screening_needs_more_information",
    "confirmed_sanctions_or_fatf_blacklist",
    "sanctions_fatf_blacklist",
}


def _row_dict(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _json_load(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _trigger_key(value: Any) -> str:
    key = _text(value).lower().replace(" ", "_")
    if key.startswith("edd_flag:"):
        return key
    return key


def _current_triggers(routing: Optional[Mapping[str, Any]] = None, triggers: Optional[Iterable[Any]] = None) -> Set[str]:
    source = []
    if routing and isinstance(routing, Mapping):
        source.extend(routing.get("triggers") or [])
    if triggers:
        source.extend(list(triggers))
    return {_trigger_key(item) for item in source if _trigger_key(item)}


def _case_triggers(case: Mapping[str, Any]) -> Set[str]:
    found: Set[str] = set()
    notes = _json_load(case.get("edd_notes"), [])
    if isinstance(notes, list):
        for item in notes:
            if not isinstance(item, Mapping):
                continue
            for trigger in item.get("triggers") or []:
                key = _trigger_key(trigger)
                if key:
                    found.add(key)
            for reason in item.get("mandatory_escalation_reasons") or []:
                key = _trigger_key(reason)
                if key:
                    found.add(key)
    for field in ("trigger_notes", "trigger_source", "origin_context"):
        raw = _text(case.get(field)).lower()
        if not raw:
            continue
        for key in SPECIFIC_EDD_TRIGGERS | DERIVED_TRIGGER_ALIASES:
            if key.replace("_", " ") in raw or key in raw:
                found.add(key)
    return found


def _coverage_required(current: Set[str], covered: Set[str]) -> Set[str]:
    required = set(current)
    if required & DERIVED_TRIGGER_ALIASES and covered & SPECIFIC_EDD_TRIGGERS:
        required -= DERIVED_TRIGGER_ALIASES
    return required


def _list_has_content(value: Any) -> bool:
    parsed = _json_load(value, value)
    if not isinstance(parsed, list):
        parsed = [parsed]
    return any(_text(item) for item in parsed)


def _findings_complete(db: Any, case_id: Any) -> Dict[str, Any]:
    try:
        row = db.execute(
            "SELECT * FROM edd_findings WHERE edd_case_id = ? ORDER BY id DESC LIMIT 1",
            (case_id,),
        ).fetchone()
    except Exception as exc:
        return {"complete": False, "present": False, "error": str(exc)}
    findings = _row_dict(row)
    if not findings:
        return {"complete": False, "present": False}
    recommended = _text(findings.get("recommended_outcome"))
    summary = _text(findings.get("findings_summary"))
    complete = bool(
        recommended
        and (
            len(summary) >= 12
            or _list_has_content(findings.get("key_concerns"))
            or _list_has_content(findings.get("mitigating_evidence"))
        )
    )
    return {
        "complete": complete,
        "present": True,
        "recommended_outcome": recommended,
        "findings_id": findings.get("id"),
    }


def _enhanced_requirements_status(db: Any, application_id: Any) -> Dict[str, Any]:
    try:
        rows = db.execute(
            """
            SELECT id, trigger_key, requirement_key, status, mandatory, blocking_approval, active
              FROM application_enhanced_requirements
             WHERE application_id = ?
               AND COALESCE(active, 1) = 1
               AND (COALESCE(mandatory, 1) = 1 OR COALESCE(blocking_approval, 1) = 1)
            """,
            (application_id,),
        ).fetchall()
    except Exception as exc:
        return {"satisfied": False, "total": 0, "unresolved_count": 0, "error": str(exc)}
    total = 0
    unresolved = []
    for row in rows or []:
        item = _row_dict(row)
        total += 1
        status = _text(item.get("status")).lower()
        if status not in SATISFIED_REQUIREMENT_STATUSES:
            unresolved.append({
                "id": item.get("id"),
                "trigger_key": item.get("trigger_key"),
                "requirement_key": item.get("requirement_key"),
                "status": status,
            })
    return {
        "satisfied": total > 0 and not unresolved,
        "total": total,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved[:10],
    }


def _closure_audit_present(db: Any, application_ref: str, case_id: Any) -> bool:
    targets = [application_ref, "application:" + application_ref, str(case_id), "EDD-" + str(case_id)]
    try:
        placeholders = ",".join("?" for _ in targets)
        row = db.execute(
            "SELECT id FROM audit_log WHERE action IN ('EDD Closure (dual-control)', 'EDD Update') "
            f"AND target IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            tuple(targets),
        ).fetchone()
    except Exception:
        return False
    return bool(row)


def collect_edd_completion_status(
    db: Any,
    application_id: Any,
    *,
    routing: Optional[Mapping[str, Any]] = None,
    triggers: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """Return whether an approved EDD case satisfies the current route.

    ``satisfied`` is true only when an approved/completed EDD case exists,
    structured findings are complete, senior approval evidence exists, active
    enhanced requirements are resolved, audit evidence exists, and the approved
    case covers the current trigger set.
    """
    current = _current_triggers(routing, triggers)
    result: Dict[str, Any] = {
        "satisfied": False,
        "application_id": application_id,
        "current_triggers": sorted(current),
        "case_id": None,
        "case_stage": None,
        "case_decision": None,
        "covered_triggers": [],
        "covers_current_triggers": False,
        "findings_complete": False,
        "enhanced_requirements_satisfied": False,
        "senior_approval_present": False,
        "audit_present": False,
        "reason": "no_approved_edd_case",
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }
    if db is None or application_id in (None, ""):
        result["reason"] = "missing_db_or_application_id"
        return result
    try:
        cases = db.execute(
            """
            SELECT *
              FROM edd_cases
             WHERE application_id = ?
               AND (stage IN ('edd_approved', 'approved', 'completed') OR decision IN ('edd_approved', 'approved'))
             ORDER BY COALESCE(decided_at, closed_at, updated_at, triggered_at) DESC, id DESC
            """,
            (application_id,),
        ).fetchall()
    except Exception as exc:
        result["reason"] = "edd_case_lookup_failed"
        result["error"] = str(exc)
        return result

    if not cases:
        return result

    req_status = _enhanced_requirements_status(db, application_id)
    result["enhanced_requirements"] = _json_ready(req_status)
    for row in cases:
        case = _row_dict(row)
        case_id = case.get("id")
        case_stage = _text(case.get("stage")).lower()
        case_decision = _text(case.get("decision")).lower()
        covered = _case_triggers(case)
        required = _coverage_required(current, covered)
        covers = not required or required.issubset(covered)
        findings = _findings_complete(db, case_id)
        senior_approval = bool(
            _text(case.get("senior_reviewer"))
            and _text(case.get("decided_by"))
            and _text(case.get("decided_at"))
            and _text(case.get("decision_reason"))
        )
        app_ref = ""
        try:
            app_row = db.execute("SELECT ref FROM applications WHERE id = ?", (application_id,)).fetchone()
            app_ref = _text(_row_dict(app_row).get("ref"))
        except Exception:
            app_ref = ""
        audit_present = _closure_audit_present(db, app_ref, case_id) if app_ref else False
        candidate = {
            "case_id": case_id,
            "case_stage": case_stage,
            "case_decision": case_decision,
            "covered_triggers": sorted(covered),
            "covers_current_triggers": covers,
            "findings_complete": bool(findings.get("complete")),
            "findings": _json_ready(findings),
            "enhanced_requirements_satisfied": bool(req_status.get("satisfied")),
            "senior_approval_present": senior_approval,
            "audit_present": audit_present,
        }
        satisfied = bool(
            (case_stage in APPROVED_EDD_STAGES or case_decision in APPROVED_EDD_STAGES)
            and covers
            and findings.get("complete")
            and req_status.get("satisfied")
            and senior_approval
            and audit_present
        )
        candidate["satisfied"] = satisfied
        if satisfied:
            candidate["reason"] = "approved_edd_satisfies_current_triggers"
            result.update(candidate)
            return _json_ready(result)
        if result.get("case_id") is None:
            reason_bits = []
            if not covers:
                reason_bits.append("trigger_coverage_missing")
            if not findings.get("complete"):
                reason_bits.append("findings_incomplete")
            if not req_status.get("satisfied"):
                reason_bits.append("enhanced_requirements_unresolved")
            if not senior_approval:
                reason_bits.append("senior_approval_missing")
            if not audit_present:
                reason_bits.append("closure_audit_missing")
            candidate["reason"] = ",".join(reason_bits) or "approved_edd_incomplete"
            result.update(candidate)
    return _json_ready(result)


def edd_completion_satisfies_route(status: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(status, Mapping):
        return False
    return bool(status.get("satisfied") and status.get("covers_current_triggers"))
