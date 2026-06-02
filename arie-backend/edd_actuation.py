"""Side-effect-safe EDD routing actuation helpers.

This module intentionally does not import ``server``. It is used by
``routing_actuator`` on pre-screening/risk recompute paths where importing
``server.py`` inside an already-running server process can re-register module
globals such as Prometheus collectors.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

from investigation_scope import (
    routine_onboarding_suppression_detail,
    should_suppress_routine_onboarding_investigation,
)

logger = logging.getLogger(__name__)
try:
    from edd_completion import collect_edd_completion_status, edd_completion_satisfies_route
except Exception:  # pragma: no cover - defensive import guard
    collect_edd_completion_status = None
    edd_completion_satisfies_route = lambda _status: False

_CANONICAL_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}
_EDD_ACTUATION_TERMINAL_STAGES = ("edd_approved", "edd_rejected")
_ASSIGNABLE_OFFICER_ROLES = {"admin", "sco", "co", "analyst"}
_PREAPPROVED_KYC_STATUSES = {"kyc_documents", "kyc_submitted"}


def _canonical_risk_level(value: Any) -> Optional[str]:
    key = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return key if key in _CANONICAL_RISK_LEVELS else None


def _canonical_risk_score(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0 or score > 100:
        return None
    return score


def _application_risk_snapshot(app_row: Mapping[str, Any]) -> Tuple[Optional[str], Optional[float]]:
    if not app_row:
        return None, None
    try:
        app = dict(app_row)
    except Exception:
        app = app_row
    level = (
        _canonical_risk_level(app.get("final_risk_level"))
        or _canonical_risk_level(app.get("risk_level"))
    )
    score = (
        _canonical_risk_score(app.get("final_risk_score"))
        if app.get("final_risk_score") not in (None, "")
        else _canonical_risk_score(app.get("risk_score"))
    )
    if not level:
        return None, None
    if level != "LOW" and score == 0:
        score = None
    return level, score


def resolve_valid_assigned_officer(db, user: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Return a valid officer user id for ``edd_cases.assigned_officer``.

    Portal/client/system actors may trigger EDD routing but that FK points to
    the back-office ``users`` table. Only assign when the actor is an existing
    officer role; otherwise leave the case unassigned and rely on audit/notes
    for actor provenance.
    """

    if db is None or not user:
        return None
    officer_id = str((user or {}).get("sub") or "").strip()
    role = str((user or {}).get("role") or "").strip().lower()
    if not officer_id or role not in _ASSIGNABLE_OFFICER_ROLES:
        return None
    try:
        row = db.execute(
            "SELECT id FROM users WHERE id = ? AND role IN ('admin','sco','co','analyst')",
            (officer_id,),
        ).fetchone()
    except Exception as exc:
        logger.warning("Could not validate EDD assigned officer %s: %s", officer_id, exc)
        return None
    if not row:
        return None
    try:
        return row["id"]
    except Exception:
        return officer_id


def should_preserve_preapproved_kyc_status(app_row: Mapping[str, Any]) -> bool:
    """Return True when EDD re-actuation must not rewind KYC workflow status.

    Once an officer has explicitly PRE_APPROVED an EDD/pre-approval-required
    case and the workflow has advanced to KYC collection/submission, repeated
    deterministic EDD evaluations are confirmations of the same EDD lane. They
    should keep the active EDD case linked and audited, but must not demote the
    application back to ``edd_required`` during ordinary upload/submit flows.
    """
    if not app_row:
        return False
    try:
        app = dict(app_row)
    except Exception:
        app = app_row
    status = str(app.get("status") or "").strip().lower()
    decision = str(app.get("pre_approval_decision") or "").strip().upper()
    return decision == "PRE_APPROVE" and status in _PREAPPROVED_KYC_STATUSES


def actuate_edd_routing(
    db,
    app_row: Mapping[str, Any],
    edd_routing: Mapping[str, Any],
    supervisor_result: Optional[Mapping[str, Any]],
    user: Optional[Mapping[str, Any]],
    client_ip: str = "",
) -> Dict[str, Any]:
    """Create/update the EDD case for a deterministic EDD routing result.

    Mirrors the existing server-owned EDD actuation semantics: one active EDD
    case per application, append notes on re-confirmation, flip status to
    ``edd_required`` when safe, and emit ``edd_routing.actuated`` audit.
    The caller owns the transaction.
    """
    result = {
        "case_id": None,
        "created": False,
        "status_changed": False,
        "status_preserved": False,
        "skipped": False,
        "edd_completion_satisfied": False,
        "completion_recognized": False,
    }
    try:
        if not isinstance(edd_routing, Mapping) or edd_routing.get("route") != "edd":
            result["skipped"] = True
            return result
        if not app_row:
            result["skipped"] = True
            return result

        try:
            app_dict = dict(app_row)
        except Exception:
            app_dict = app_row
        application_id = app_dict.get("id")
        if not application_id:
            result["skipped"] = True
            return result

        if should_suppress_routine_onboarding_investigation(edd_routing, supervisor_result):
            result["skipped"] = True
            result["formal_case_suppressed"] = True
            suppression = routine_onboarding_suppression_detail(edd_routing, supervisor_result)
            try:
                db.execute(
                    "INSERT INTO audit_log (user_id, user_name, user_role, "
                    "action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                    (
                        (user or {}).get("sub") or "system",
                        (user or {}).get("name") or "system",
                        (user or {}).get("role") or "system",
                        "edd_routing.actuated",
                        "application:" + str(app_dict.get("ref") or application_id),
                        json.dumps(
                            {
                                "policy_version": edd_routing.get("policy_version", ""),
                                "route": edd_routing.get("route"),
                                "triggers": list(edd_routing.get("triggers") or []),
                                "evaluated_at": edd_routing.get("evaluated_at", ""),
                                "edd_case_id": None,
                                "edd_case_created": False,
                                "status_changed": False,
                                "status_preserved": True,
                                "origin": "routine_onboarding_enhanced_review",
                                **suppression,
                            },
                            default=str,
                            sort_keys=True,
                        ),
                        client_ip or "",
                    ),
                )
            except Exception as exc:
                logger.warning("Failed to write suppressed EDD actuation audit row: %s", exc)
            return result

        completion = {}
        if collect_edd_completion_status is not None:
            completion = collect_edd_completion_status(db, application_id, routing=edd_routing)
            if edd_completion_satisfies_route(completion):
                result["case_id"] = completion.get("case_id")
                result["edd_completion_satisfied"] = True
                result["completion_recognized"] = True
                result["status_preserved"] = True
                triggers = list(edd_routing.get("triggers") or [])
                mandatory_reasons = list((supervisor_result or {}).get("mandatory_escalation_reasons") or [])
                try:
                    db.execute(
                        "INSERT INTO audit_log (user_id, user_name, user_role, "
                        "action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                        (
                            (user or {}).get("sub") or "system",
                            (user or {}).get("name") or "system",
                            (user or {}).get("role") or "system",
                            "edd_routing.actuated",
                            "application:" + str(app_dict.get("ref") or application_id),
                            json.dumps(
                                {
                                    "policy_version": edd_routing.get("policy_version", ""),
                                    "route": edd_routing.get("route"),
                                    "triggers": triggers,
                                    "mandatory_escalation_reasons": mandatory_reasons,
                                    "evaluated_at": edd_routing.get("evaluated_at", ""),
                                    "edd_case_id": result["case_id"],
                                    "edd_case_created": False,
                                    "status_changed": False,
                                    "status_preserved": True,
                                    "edd_completion_satisfied": True,
                                    "completion_recognized": True,
                                    "completion_reason": completion.get("reason"),
                                    "completion_case_id": completion.get("case_id"),
                                    "completion_current_triggers": completion.get("current_triggers"),
                                    "completion_covered_triggers": completion.get("covered_triggers"),
                                    "origin": "policy_routing",
                                },
                                default=str,
                                sort_keys=True,
                            ),
                            client_ip or "",
                        ),
                    )
                except Exception as exc:
                    logger.warning("Failed to write completed EDD actuation audit row: %s", exc)
                return result

        placeholders = ",".join(["?"] * len(_EDD_ACTUATION_TERMINAL_STAGES))
        existing = db.execute(
            "SELECT id, stage FROM edd_cases WHERE application_id = ? "
            "AND stage NOT IN (" + placeholders + ") "
            "ORDER BY id ASC LIMIT 1",
            (application_id, *_EDD_ACTUATION_TERMINAL_STAGES),
        ).fetchone()

        triggers = list(edd_routing.get("triggers") or [])
        policy_version = edd_routing.get("policy_version", "")
        evaluated_at = edd_routing.get("evaluated_at", "")
        mandatory_reasons = list((supervisor_result or {}).get("mandatory_escalation_reasons") or [])
        routing_source = str(edd_routing.get("source") or "").strip().lower()
        explicit_onboarding_sources = {"screening_update", "officer_correction"}
        case_trigger_source = routing_source if routing_source in explicit_onboarding_sources else "policy_routing"
        case_origin_context = "onboarding_escalation" if routing_source in explicit_onboarding_sources else None
        trigger_notes = (
            "Auto-routed to EDD by policy " + str(policy_version)
            + " | triggers: " + ", ".join(triggers[:8])
            + (
                " | mandatory_escalation: " + ", ".join(mandatory_reasons[:6])
                if mandatory_reasons
                else ""
            )
        )

        if existing:
            case_id = existing["id"]
            try:
                row = db.execute(
                    "SELECT edd_notes FROM edd_cases WHERE id = ?",
                    (case_id,),
                ).fetchone()
                existing_notes = []
                if row and row.get("edd_notes"):
                    raw = row["edd_notes"]
                    if isinstance(raw, str):
                        try:
                            existing_notes = json.loads(raw) or []
                        except Exception:
                            existing_notes = []
                    elif isinstance(raw, list):
                        existing_notes = list(raw)
                existing_notes.append(
                    {
                        "ts": datetime.now().isoformat(),
                        "author": (user or {}).get("name") or "system",
                        "source": case_trigger_source,
                        "policy_version": policy_version,
                        "triggers": triggers,
                        "mandatory_escalation_reasons": mandatory_reasons,
                        "evaluated_at": evaluated_at,
                        "note": "Routing re-confirmed by memo regeneration",
                    }
                )
                db.execute(
                    "UPDATE edd_cases SET edd_notes = ? WHERE id = ?",
                    (json.dumps(existing_notes), case_id),
                )
            except Exception as exc:
                logger.warning("Failed to append routing note to EDD case %s: %s", case_id, exc)
            result["case_id"] = case_id
            result["created"] = False
        else:
            initial_note = json.dumps(
                [
                    {
                        "ts": datetime.now().isoformat(),
                        "author": (user or {}).get("name") or "system",
                        "source": case_trigger_source,
                        "policy_version": policy_version,
                        "triggers": triggers,
                        "mandatory_escalation_reasons": mandatory_reasons,
                        "evaluated_at": evaluated_at,
                        "note": "EDD case auto-created by routing policy actuation",
                    }
                ]
            )
            risk_level, risk_score = _application_risk_snapshot(app_dict)
            assigned_officer = resolve_valid_assigned_officer(db, user)
            insert_params = (
                application_id,
                app_dict.get("company_name") or "",
                risk_level,
                risk_score,
                "triggered",
                assigned_officer,
                case_trigger_source,
                trigger_notes,
                initial_note,
            )
            if getattr(db, "is_postgres", False):
                try:
                    row = db.execute(
                        "INSERT INTO edd_cases (application_id, client_name, "
                        "risk_level, risk_score, stage, assigned_officer, "
                        "trigger_source, trigger_notes, edd_notes, origin_context) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?) RETURNING id",
                        (*insert_params, case_origin_context),
                    ).fetchone()
                except Exception:
                    row = db.execute(
                        "INSERT INTO edd_cases (application_id, client_name, "
                        "risk_level, risk_score, stage, assigned_officer, "
                        "trigger_source, trigger_notes, edd_notes) "
                        "VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
                        insert_params,
                    ).fetchone()
                case_id = row["id"]
            else:
                try:
                    cursor = db.execute(
                        "INSERT INTO edd_cases (application_id, client_name, "
                        "risk_level, risk_score, stage, assigned_officer, "
                        "trigger_source, trigger_notes, edd_notes, origin_context) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (*insert_params, case_origin_context),
                    )
                except Exception:
                    cursor = db.execute(
                        "INSERT INTO edd_cases (application_id, client_name, "
                        "risk_level, risk_score, stage, assigned_officer, "
                        "trigger_source, trigger_notes, edd_notes) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        insert_params,
                    )
                case_id = getattr(cursor, "lastrowid", None)
                if case_id is None:
                    case_id = db.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
            result["case_id"] = case_id
            result["created"] = True

        current_status = (app_dict.get("status") or "")
        preserve_preapproved_kyc_status = should_preserve_preapproved_kyc_status(app_dict)
        if preserve_preapproved_kyc_status:
            result["status_preserved"] = True
        elif current_status not in ("edd_required", "edd_approved", "approved", "rejected"):
            db.execute(
                "UPDATE applications SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                ("edd_required", application_id),
            )
            result["status_changed"] = True

        try:
            db.execute(
                "INSERT INTO audit_log (user_id, user_name, user_role, "
                "action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                (
                    (user or {}).get("sub") or "system",
                    (user or {}).get("name") or "system",
                    (user or {}).get("role") or "system",
                    "edd_routing.actuated",
                    "application:" + str(app_dict.get("ref") or application_id),
                    json.dumps(
                        {
                            "policy_version": policy_version,
                            "route": edd_routing.get("route"),
                            "triggers": triggers,
                            "mandatory_escalation_reasons": mandatory_reasons,
                            "evaluated_at": evaluated_at,
                            "edd_case_id": result["case_id"],
                            "edd_case_created": result["created"],
                            "status_changed": result["status_changed"],
                            "status_preserved": result["status_preserved"],
                            "origin": "policy_routing",
                        },
                        default=str,
                        sort_keys=True,
                    ),
                    client_ip or "",
                ),
            )
        except Exception as exc:
            logger.warning("Failed to write edd_routing.actuated audit row: %s", exc)
    except Exception as exc:
        logger.error("EDD route actuation failed: %s", exc, exc_info=True)
    return result


# Backward-compatible alias for callers/tests that use the old private name.
_actuate_edd_routing = actuate_edd_routing
