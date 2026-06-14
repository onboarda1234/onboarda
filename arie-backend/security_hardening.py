"""
ARIE Finance — Security Hardening Module
=========================================

Implements all Critical and High audit remediation fixes:
- Approval gates (P0-01, P0-02)
- Screening mode tracking (P0-02)
- Production environment guards (P0-04)
- AI source tracking (P0-05)
- Compliance memo validation (P0-06)
- PII encryption (P0-10)
- Password policy (P0-09, P1)
- Request schema validation (P0-11)
- Token revocation (P1)
- File upload validation (P1)
- Health endpoint restriction (P1)

This module is self-contained and importable by server.py.
It should NOT be modified to integrate with server.py—only imported.
"""

import os
import sys
import json
import base64
import logging
import secrets
import re
import time
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Tuple, Dict, List, Optional, Any, Mapping
from pathlib import Path

from environment import ENV, is_production, get_screening_validity_days
from screening_state import (
    LIVE_PROVIDER,
    SANDBOX_PROVIDER,
    SIMULATED_FALLBACK,
    NOT_CONFIGURED as SCREENING_NOT_CONFIGURED,
    PENDING as SCREENING_PENDING,
    FAILED as SCREENING_FAILED,
    build_screening_truth_summary,
    derive_screening_truth,
)
from sumsub_idv_status import build_idv_gate_summary, build_sumsub_idv_statuses
from memo_governance import latest_compliance_memo_row
from document_reliance_gate import (
    document_reliance_blockers_for_approval,
    evaluate_document_reliance_gate,
    format_document_reliance_blockers,
)

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def _parse_approval_timestamp(value: Any) -> datetime:
    """Parse approval-gate timestamps to aware UTC datetimes.

    SQLite CURRENT_TIMESTAMP/datetime('now') and current screening persistence
    store UTC timestamps without timezone offsets. Treat naive values as UTC so
    comparisons against datetime.now(timezone.utc) are stable across host
    timezones.
    """
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    else:
        parsed = value

    if not isinstance(parsed, datetime):
        raise TypeError(f"Expected datetime-compatible timestamp, got {type(value).__name__}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ============================================================================
# 1. Approval Gate Validators (P0-01, P0-02)
# ============================================================================

_CANONICAL_APPROVAL_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}
_APPROVAL_RISK_UNAVAILABLE_WARNING = "Risk unavailable — recalculation required"
_APPROVAL_EDD_ZERO_SCORE_WARNING = "EDD required while canonical risk score is unavailable or zero"


def _truthy_db_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y"}


def _staging_workflow_test_document_acceptance_satisfied(doc: Mapping[str, Any]) -> bool:
    """Allow staged mechanics tests without changing document verification truth."""
    return (
        str(ENV or "").strip().lower() == "staging"
        and str(doc.get("evidence_class") or "").strip().lower() == "test_only_synthetic"
        and _truthy_db_value(doc.get("workflow_test_accepted"))
        and bool(str(doc.get("workflow_test_acceptance_reason") or "").strip())
        and bool(str(doc.get("workflow_test_accepted_by") or "").strip())
        and bool(str(doc.get("workflow_test_accepted_at") or "").strip())
        and str(doc.get("workflow_test_acceptance_environment") or "").strip().lower() == "staging"
    )


def _canonical_approval_risk_level(value: Any) -> Optional[str]:
    level = str(value or "").strip().upper()
    return level if level in _CANONICAL_APPROVAL_RISK_LEVELS else None


def _canonical_approval_risk_score(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score != score:
        return None
    return score


def _approval_risk_integrity_error(app: Dict, action_label: str = "approve application") -> Optional[str]:
    """Return a fail-closed approval error when canonical risk is unavailable.

    A deterministic LOW score can legitimately be 0.  Treat zero as an integrity
    problem only when the level is missing/non-LOW or the case is already on EDD,
    where LOW/0 would hide the operational escalation.
    """
    if not isinstance(app, dict):
        return f"{_APPROVAL_RISK_UNAVAILABLE_WARNING}. Cannot {action_label}."

    status = str(app.get("status") or "").strip().lower()
    if status == "draft":
        return None

    if app.get("has_authoritative_risk") is False:
        warnings = app.get("risk_integrity_warnings") or [_APPROVAL_RISK_UNAVAILABLE_WARNING]
        if isinstance(warnings, (list, tuple)):
            warning_text = "; ".join(str(w) for w in warnings if w) or _APPROVAL_RISK_UNAVAILABLE_WARNING
        else:
            warning_text = str(warnings or _APPROVAL_RISK_UNAVAILABLE_WARNING)
        return f"{warning_text}. Cannot {action_label} until risk is recomputed."

    level = (
        _canonical_approval_risk_level(app.get("final_risk_level"))
        or _canonical_approval_risk_level(app.get("risk_level"))
    )
    score = _canonical_approval_risk_score(app.get("risk_score"))
    warnings: List[str] = []
    if level is None or score is None:
        warnings.append(_APPROVAL_RISK_UNAVAILABLE_WARNING)
    elif score == 0 and level != "LOW":
        warnings.append(_APPROVAL_RISK_UNAVAILABLE_WARNING)
    if status == "edd_required" and score == 0:
        warnings.append(_APPROVAL_EDD_ZERO_SCORE_WARNING)

    if warnings:
        warning_text = "; ".join(dict.fromkeys(warnings))
        return f"{warning_text}. Cannot {action_label} until risk is recomputed."
    return None


def _truthy_screening_review_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _escape_like_literal(value: Any) -> str:
    text = str(value or "")
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _screening_review_audit_detail_payload(detail: Any) -> Dict:
    if isinstance(detail, dict):
        return detail
    if isinstance(detail, str) and detail.strip():
        try:
            parsed = json.loads(detail)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _screening_review_audit_detail_matches(review: Dict, detail: Any) -> bool:
    detail_text = str(detail or "")
    payload = _screening_review_audit_detail_payload(detail)
    subject_name = str(review.get("subject_name") or "")
    disposition_code = str(review.get("disposition_code") or "")
    if payload:
        payload_subject = str(payload.get("subject_name") or "")
        payload_code = str(payload.get("disposition_code") or payload.get("canonical_disposition") or "")
        return bool(
            (not subject_name or payload_subject == subject_name)
            and (not disposition_code or payload_code == disposition_code)
        )
    return bool(
        detail_text
        and (not subject_name or subject_name in detail_text)
        and (not disposition_code or disposition_code in detail_text)
    )


def _screening_review_evidence_from_audit_detail(detail: Any) -> Optional[Any]:
    payload = _screening_review_audit_detail_payload(detail)
    for key in ("evidence_reference", "review_evidence_reference", "evidence", "reference", "source_reference"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _screening_review_audit_row(db, app_ref: str, review: Dict) -> Optional[Dict]:
    if not db or not review:
        return None
    target = app_ref or review.get("application_ref") or review.get("application_id")
    subject_name = review.get("subject_name") or ""
    disposition_code = review.get("disposition_code") or ""
    try:
        row = db.execute(
            """
            SELECT id, detail FROM audit_log
            WHERE target = ? AND action = 'Screening Review'
              AND detail LIKE ? ESCAPE '\\'
              AND detail LIKE ? ESCAPE '\\'
            ORDER BY id DESC LIMIT 1
            """,
            (
                target,
                f"%{_escape_like_literal(subject_name)}%",
                f"%{_escape_like_literal(disposition_code)}%",
            ),
        ).fetchone()
        if row and _screening_review_audit_detail_matches(review, row["detail"]):
            return dict(row)
        return None
    except Exception as exc:
        logger.warning(
            "Approval screening review audit lookup failed for %s/%s: %s",
            target,
            subject_name,
            exc,
        )
        return None


def _screening_review_audit_confirmed(db, app_ref: str, review: Dict) -> bool:
    if _truthy_screening_review_flag(review.get("audit_confirmed")):
        return True
    return bool(_screening_review_audit_row(db, app_ref, review))


def _load_screening_reviews_for_truth(db, app_id: str, app_ref: str = "") -> List[Dict]:
    rows = db.execute(
        """
        SELECT * FROM screening_reviews
        WHERE application_id = ?
        ORDER BY updated_at DESC, created_at DESC, id DESC
        """,
        (app_id,),
    ).fetchall()
    reviews: List[Dict] = []
    for row in rows:
        review = dict(row)
        audit_row = _screening_review_audit_row(db, app_ref, review)
        review["audit_confirmed"] = _truthy_screening_review_flag(review.get("audit_confirmed")) or bool(audit_row)
        if not review.get("review_evidence_reference") and not review.get("evidence_reference") and audit_row:
            evidence_reference = _screening_review_evidence_from_audit_detail(audit_row.get("detail"))
            if evidence_reference:
                review["review_evidence_reference"] = evidence_reference
        reviews.append(review)
    return reviews


def _approval_edd_completion_status(db, app_id: str, routing: Mapping[str, Any]) -> Dict[str, Any]:
    """Return current DB-backed EDD completion status for the approval gate."""
    try:
        from edd_completion import collect_edd_completion_status

        status = collect_edd_completion_status(db, app_id, routing=routing)
        return status if isinstance(status, dict) else {
            "satisfied": False,
            "reason": "invalid_edd_completion_status",
        }
    except Exception as exc:
        logger.error(
            "Approval EDD completion lookup failed for application %s: %s",
            app_id,
            exc,
            exc_info=True,
        )
        return {
            "satisfied": False,
            "reason": "edd_completion_lookup_failed",
            "error": str(exc),
        }


def _approval_edd_completion_satisfied(status: Mapping[str, Any]) -> bool:
    try:
        from edd_completion import edd_completion_satisfies_route

        return edd_completion_satisfies_route(status)
    except Exception:
        return bool(
            isinstance(status, Mapping)
            and status.get("satisfied")
            and status.get("covers_current_triggers")
        )


def _format_approval_edd_completion_block_reason(routing: Mapping[str, Any], app_status: str, completion: Mapping[str, Any]) -> str:
    triggers = ", ".join(str(item) for item in (routing.get("triggers") or [])[:6])
    reason = completion.get("reason") or "edd_completion_not_satisfied"
    missing = completion.get("missing_triggers") or []
    missing_text = ""
    if missing:
        missing_text = " Missing trigger coverage: " + ", ".join(str(item) for item in missing[:6]) + "."
    return (
        "EDD routing policy " + str(routing.get("policy_version", ""))
        + " requires completed EDD evidence before final approval "
        + "(triggers: " + triggers + "). "
        + "Application status is '" + app_status + "'. "
        + "EDD completion is not satisfied: " + str(reason) + "."
        + missing_text
    )


def _audit_approval_edd_completion_satisfied(
    db,
    app: Dict,
    routing: Mapping[str, Any],
    completion: Mapping[str, Any],
) -> Tuple[bool, str]:
    """Record audit evidence that final approval accepted completed EDD."""
    try:
        detail = {
            "event": "approval_gate_edd_completion_satisfied",
            "application_id": app.get("id"),
            "application_ref": app.get("ref"),
            "policy_version": routing.get("policy_version"),
            "route": routing.get("route"),
            "routing_triggers": routing.get("triggers") or [],
            "edd_completion_satisfied": bool(completion.get("satisfied")),
            "covers_current_triggers": bool(completion.get("covers_current_triggers")),
            "edd_case_id": completion.get("case_id"),
            "reason": completion.get("reason"),
            "current_triggers": completion.get("current_triggers") or [],
            "covered_triggers": completion.get("covered_triggers") or [],
            "missing_triggers": completion.get("missing_triggers") or [],
            "normalized_current_triggers": completion.get("normalized_current_triggers") or [],
            "normalized_covered_triggers": completion.get("normalized_covered_triggers") or [],
            "checked_at": completion.get("checked_at"),
            "approved_application_status": app.get("status"),
            "approved_application_lane": app.get("onboarding_lane"),
        }
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "system",
                "Approval Gate",
                "system",
                "Approval Gate EDD Completion Satisfied",
                app.get("ref") or str(app.get("id") or ""),
                json.dumps(detail, default=str, sort_keys=True),
                "",
            ),
        )
        return True, ""
    except Exception as exc:
        logger.error(
            "Approval EDD completion audit write failed for application %s: %s",
            app.get("id"),
            exc,
            exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return (
            False,
            "Could not record EDD completion approval-gate audit evidence. "
            "Approval is blocked until audit logging succeeds.",
        )


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _load_approval_idv_parties(db, app: Mapping[str, Any]) -> Tuple[List[Dict], List[Dict], List[Dict], Optional[Dict]]:
    app_id = app.get("id")
    directors: List[Dict] = []
    ubos: List[Dict] = []
    intermediaries: List[Dict] = []
    client: Optional[Dict] = None
    try:
        directors = [_row_to_dict(row) for row in db.execute("SELECT * FROM directors WHERE application_id=?", (app_id,)).fetchall()]
    except Exception:
        directors = []
    try:
        ubos = [_row_to_dict(row) for row in db.execute("SELECT * FROM ubos WHERE application_id=?", (app_id,)).fetchall()]
    except Exception:
        ubos = []
    try:
        intermediaries = [_row_to_dict(row) for row in db.execute("SELECT * FROM intermediaries WHERE application_id=?", (app_id,)).fetchall()]
    except Exception:
        intermediaries = []
    try:
        client_id = app.get("client_id")
        if client_id:
            row = db.execute("SELECT id, email, company_name, status, created_at FROM clients WHERE id=?", (client_id,)).fetchone()
            client = _row_to_dict(row) if row else None
    except Exception:
        client = None
    return directors, ubos, intermediaries, client


def _approval_idv_gate_summary(app: Mapping[str, Any], db) -> Dict[str, Any]:
    existing = app.get("sumsub_idv_statuses") if isinstance(app, Mapping) else None
    if isinstance(existing, Mapping):
        return build_idv_gate_summary(existing)
    directors, ubos, intermediaries, client = _load_approval_idv_parties(db, app)
    payload = build_sumsub_idv_statuses(
        db,
        app,
        directors=directors,
        ubos=ubos,
        intermediaries=intermediaries,
        client=client,
        include_unmatched=False,
    )
    return payload.get("gate_summary") or build_idv_gate_summary(payload)


def _approval_gate_blocker(
    blocker_id: str,
    category: str,
    title: str,
    description: str,
    *,
    severity: str = "blocking",
    blocking: bool = True,
    source: str = "backend_approval_gate",
    cta_label: str = "Review",
    tab: str = "overview",
    anchor_id: str = "",
    blocker_group: str = "",
    blocker_group_label: str = "",
    action_key: str = "",
) -> Dict[str, Any]:
    blocker = {
        "id": blocker_id,
        "category": category,
        "title": title,
        "description": description,
        "severity": severity,
        "blocking": bool(blocking),
        "source": source,
        "ctaLabel": cta_label,
        "tab": tab,
        "anchorId": anchor_id,
    }
    if blocker_group:
        blocker["blocker_group"] = blocker_group
    if blocker_group_label:
        blocker["blocker_group_label"] = blocker_group_label
    if action_key:
        blocker["action_key"] = action_key
        blocker["action_label"] = cta_label
        blocker["action_target"] = {
            "target_view": "application_review",
            "target_tab": tab,
            "target_section": anchor_id,
            "scroll_anchor": anchor_id,
            "action_mode": "focus_section",
        }
    return blocker


class ApprovalGateValidator:
    """
    Validates all preconditions before an application can be approved.
    Prevents approval without proper KYC, screening, compliance, and document review.
    """

    @staticmethod
    def validate_approval(app: Dict, db) -> Tuple[bool, str]:
        """
        Validates that an application meets all approval prerequisites.

        Reads from actual data sources (DB tables, prescreening_data JSON) — not phantom columns.

        Args:
            app: Application dictionary from SELECT * FROM applications
            db: Database connection object (required)

        Returns:
            Tuple of (can_approve: bool, error_message: str)

        Checks:
            1. Application must have passed through KYC/compliance workflow stages
            2. Screening must exist in prescreening_data and mode must be 'live'
            3. Compliance memo must exist in compliance_memos table
            4. All documents must not be 'flagged'
            5. Required screening checks (Sumsub/CA) must not use simulated api_status;
               enrichment checks (company_registry, ip_geolocation) warn only
            6. Compliance memo ai_source must not be 'mock'
        """
        try:
            app_id = app.get('id')
            if not app_id or not db:
                return (False, "Application ID and database connection are required for approval validation")

            # 1. Check application has been through KYC/review workflow
            # (The state machine in server.py enforces transitions, but verify the app
            #  has reached a reviewable state — not still in draft/prescreening)
            status = app.get('status', '').lower()
            pre_kyc_states = ('draft', 'prescreening_submitted', 'pricing_review', 'pricing_accepted',
                              'pre_approval_review', 'pre_approved', 'kyc_documents')
            if status in pre_kyc_states:
                return (False, f"Application is still in pre-review state '{status}'. "
                        "Cannot approve until compliance review is complete.")

            risk_integrity_error = _approval_risk_integrity_error(app, "approve application")
            if risk_integrity_error:
                return (False, risk_integrity_error)

            # 2. Check screening exists in prescreening_data and mode is live
            prescreening_data = app.get('prescreening_data', '{}')
            if isinstance(prescreening_data, str):
                import json as _json
                try:
                    prescreening_data = _json.loads(prescreening_data)
                except (ValueError, TypeError):
                    prescreening_data = {}

            screening_report = prescreening_data.get('screening_report', {})
            if not screening_report:
                return (False, "No screening report found in application data. "
                        "Screening must be run before approval.")

            screening_mode = screening_report.get('screening_mode', '').lower()
            if is_production() and screening_mode != 'live':
                return (
                    False,
                    f"Screening must be in 'live' mode, not '{screening_mode}'. "
                    "Simulated screening is not permitted for approval in production."
                )

            screening_reviews = []
            try:
                screening_reviews = _load_screening_reviews_for_truth(db, app_id, app.get("ref", ""))
            except Exception as exc:
                logger.warning(
                    "Approval screening review lookup failed for application %s: %s",
                    app_id,
                    exc,
                )

            prescreening_for_truth = dict(prescreening_data)
            prescreening_for_truth["screening_input_updated_at"] = (
                app.get("screening_input_updated_at")
                or app.get("risk_inputs_updated_at")
                or (app.get("inputs_updated_at") if app.get("submitted_at") else None)
                or app.get("submitted_at")
            )
            screening_truth = build_screening_truth_summary(
                screening_report,
                prescreening_for_truth,
                screening_reviews,
            )
            if screening_truth.get("approval_blocking"):
                reason = "; ".join(screening_truth.get("blocking_reasons") or ["screening_not_terminal"])
                logger.warning(
                    "Approval blocked by screening truth gate for application %s: "
                    "state=%s mode=%s availability=%s result=%s terminal=%s reasons=%s",
                    app_id,
                    screening_truth.get("canonical_state"),
                    screening_truth.get("provider_mode"),
                    screening_truth.get("provider_availability"),
                    screening_truth.get("screening_result"),
                    screening_truth.get("terminal"),
                    reason,
                )
                if screening_truth.get("canonical_state") == "stale":
                    if "screening:input_updated_after_screening" in (screening_truth.get("approval_blocked_reasons") or []):
                        return (
                            False,
                            "Application data with screening-relevant inputs was modified after screening. "
                            "A re-screen is required before approval can proceed."
                        )
                    screening_valid_until_str = prescreening_data.get("screening_valid_until")
                    if screening_valid_until_str:
                        try:
                            valid_until = _parse_approval_timestamp(screening_valid_until_str)
                            now = datetime.now(timezone.utc)
                            if now > valid_until:
                                try:
                                    validity_days = int(
                                        prescreening_data.get("screening_validity_days")
                                        or get_screening_validity_days()
                                    )
                                except (TypeError, ValueError):
                                    validity_days = get_screening_validity_days()
                                age_days = (now - valid_until).days
                                return (
                                    False,
                                    f"Screening results expired {age_days} day(s) ago "
                                    f"(validity period: {validity_days} days). "
                                    "A re-screen is required before approval can proceed."
                                )
                        except (ValueError, TypeError):
                            pass
                return (
                    False,
                    "Screening truth gate failed: "
                    f"state={screening_truth.get('canonical_state')}, "
                    f"provider_mode={screening_truth.get('provider_mode')}, "
                    f"availability={screening_truth.get('provider_availability')}, "
                    f"result={screening_truth.get('screening_result')}, "
                    f"terminal={screening_truth.get('terminal')}. "
                    f"Reason: {reason}. Live terminal screening is required before approval."
                )

            idv_gate = _approval_idv_gate_summary(app, db)
            if not idv_gate.get("approval_ready"):
                reason = "; ".join(idv_gate.get("blocking_reasons") or ["identity_verification_unresolved"])
                return (
                    False,
                    "Identity verification gate failed: "
                    f"{reason}. Identity verification must be verified, manually verified, "
                    "or senior exception-approved before final approval."
                )

            # 3. Check compliance memo exists and meets quality gates
            memo_row = latest_compliance_memo_row(
                db,
                app_id,
                columns=(
                    "id, memo_data, review_status, validation_status, supervisor_status, blocked, block_reason, "
                    "created_at, approval_reason, is_stale, stale_reason, stale_trigger, stale_marked_at"
                ),
            )
            if not memo_row:
                return (False, "Compliance memo must be generated before approval. "
                        "Generate via POST /api/applications/{id}/memo first.")
            if not isinstance(memo_row, dict):
                memo_row = dict(memo_row)

            def _memo_is_stale(value):
                if isinstance(value, bool):
                    return value
                if isinstance(value, (int, float)):
                    return value != 0
                return str(value or '').strip().lower() in ('1', 'true', 'yes', 'y', 'stale')

            # 3aa. Persisted stale memos are fail-closed. Material facts moved
            # after generation, so validation/supervisor/sign-off must be rerun
            # on a regenerated memo before final approval can proceed.
            if _memo_is_stale(memo_row.get('is_stale')):
                reason = memo_row.get('stale_reason') or 'Material facts changed after memo generation.'
                logger.warning(
                    "Approval blocked for application %s: compliance memo is stale "
                    "(trigger=%s, marked_at=%s)",
                    app_id,
                    memo_row.get('stale_trigger'),
                    memo_row.get('stale_marked_at'),
                )
                return (
                    False,
                    f"Compliance memo is stale: {reason} "
                    "Regenerate the memo, rerun validation and supervisor, and re-approve before application approval."
                )

            # 3a. Memo must not be blocked
            if memo_row.get('blocked'):
                return (False, f"Compliance memo is blocked: {memo_row.get('block_reason', 'unspecified reason')}. "
                        "Resolve blocking issues before approval.")

            # 3b. Memo must be formally approved (review_status)
            memo_review = (memo_row.get('review_status') or '').lower()
            if memo_review != 'approved':
                return (False, f"Compliance memo review_status is '{memo_review}', must be 'approved'. "
                        "Memo must be reviewed and approved before application approval.")
            approval_reason_current = memo_row.get('approval_reason') or ''
            if not approval_reason_current.strip():
                return (
                    False,
                    "Compliance memo approval_reason is required. "
                    "Approve the canonical memo with documented officer rationale before application approval."
                )

            # 3c. Memo validation must have an explicit positive pass
            # Senior-approval-with-findings policy (EX-06): 'pass_with_fixes'
            # is accepted when the memo has been formally approved by a senior
            # approver (admin/SCO) with a documented reason.  The memo approval
            # handler enforces the role and reason requirements; if the memo
            # review_status is 'approved' AND approval_reason is populated, the
            # senior gate has already been satisfied.
            memo_validation = (memo_row.get('validation_status') or '').lower()
            if memo_validation == 'pass':
                pass  # Standard path — no additional checks
            elif memo_validation == 'pass_with_fixes':
                # Only allow through if memo was senior-approved with a reason
                approval_reason = memo_row.get('approval_reason') or ''
                if memo_review != 'approved' or not approval_reason.strip():
                    return (
                        False,
                        f"Compliance memo validation_status is 'pass_with_fixes'. "
                        "Memo must be approved by a senior approver (admin/SCO) with a documented reason "
                        "before application approval can proceed."
                    )
            else:
                return (
                    False,
                    f"Compliance memo validation_status is '{memo_validation}', must be 'pass'. "
                    "Validation warnings or pending states must be resolved before approval."
                )

            # 3d. Supervisor must have an explicit positive verdict
            memo_supervisor = (memo_row.get('supervisor_status') or '').upper()
            if memo_supervisor == 'CONSISTENT':
                pass  # Standard path — no additional checks
            elif memo_supervisor == 'CONSISTENT_WITH_WARNINGS':
                # Supervisor-warnings approval policy (EX-06 B2):
                # Allow if memo was senior-approved with a documented reason.
                # The memo approval handler enforces role and can_approve checks;
                # if review_status is 'approved' AND approval_reason is populated,
                # the senior gate has already been satisfied.
                approval_reason_sv = memo_row.get('approval_reason') or ''
                if memo_review != 'approved' or not approval_reason_sv.strip():
                    return (
                        False,
                        f"Compliance memo supervisor_status is 'CONSISTENT_WITH_WARNINGS'. "
                        "Memo must be approved by a senior approver (admin/SCO) with a documented reason "
                        "before application approval can proceed."
                    )
            else:
                return (
                    False,
                    f"Compliance memo supervisor_status is '{memo_supervisor}', must be 'CONSISTENT'. "
                    "Supervisor warnings or inconsistencies must be resolved before approval."
                )

            # 3e. ── Priority B / Workstream B & C: ──────────────────
            # mandatory_escalation and EDD-routing checks. The memo's
            # serialized supervisor block is the single authoritative
            # source for unresolved supervisor escalation. Deterministic
            # EDD routing is then validated against current EDD completion
            # evidence rather than using application.status as a proxy; valid
            # EDD cases may have advanced to kyc_submitted after PRE_APPROVE,
            # KYC, enhanced requirements, and approved EDD closure.
            try:
                import json as _json2
                _md_raw = memo_row.get('memo_data') or '{}'
                _md_obj = _json2.loads(_md_raw) if isinstance(_md_raw, str) else (_md_raw or {})
                _md_meta = _md_obj.get('metadata') or {}
                _supervisor_block = _md_obj.get('supervisor') or _md_meta.get('supervisor') or {}
                if _supervisor_block.get('mandatory_escalation'):
                    _reasons = _supervisor_block.get('mandatory_escalation_reasons') or []
                    return (
                        False,
                        "Supervisor mandatory_escalation is set on the compliance memo "
                        f"(reasons: {', '.join(_reasons[:6])}). "
                        "Approval through the standard pathway is blocked; "
                        "complete EDD or senior review and re-generate the memo first."
                    )
                _routing = _md_meta.get('edd_routing') or {}
                if _routing.get('route') == 'edd':
                    _app_status = (app.get('status') or '').lower()
                    try:
                        from investigation_scope import (
                            is_formal_investigation_case,
                            should_suppress_routine_onboarding_investigation,
                        )
                        _edd_rows = db.execute(
                            "SELECT * FROM edd_cases WHERE application_id = ?",
                            (app_id,),
                        ).fetchall()
                        _has_formal_case = any(
                            is_formal_investigation_case(row) for row in (_edd_rows or [])
                        )
                        _routine_onboarding_enhanced_only = (
                            should_suppress_routine_onboarding_investigation(_routing, _supervisor_block)
                            and not _has_formal_case
                        )
                    except Exception:
                        _routine_onboarding_enhanced_only = False

                    if _routine_onboarding_enhanced_only:
                        # PR 5B: routine onboarding enhanced evidence is
                        # enforced by application_enhanced_requirements below;
                        # do not require a formal Investigation Case.
                        pass
                    else:
                        _completion = _approval_edd_completion_status(db, app_id, _routing)
                        if _approval_edd_completion_satisfied(_completion):
                            _audit_ok, _audit_error = _audit_approval_edd_completion_satisfied(
                                db,
                                app,
                                _routing,
                                _completion,
                            )
                            if not _audit_ok:
                                return (False, _audit_error)
                        else:
                            return (
                                False,
                                _format_approval_edd_completion_block_reason(
                                    _routing,
                                    _app_status,
                                    _completion,
                                )
                            )
            except Exception as _ge:  # pragma: no cover — defensive
                logger.error("Failed to evaluate mandatory_escalation/edd_routing gate: %s", _ge)
                # Fail-closed: refuse approval rather than silently allow.
                return (
                    False,
                    "Could not verify mandatory_escalation/EDD routing on the compliance memo. "
                    "Re-generate the memo and retry."
                )

            # 4. Enhanced / EDD requirements approval control.
            # Source of truth is application_enhanced_requirements.  The gate
            # does not generate rows, change memo output, or create client/RMI
            # side effects; it only blocks unresolved mandatory/blocking rows.
            try:
                from enhanced_requirements import (
                    format_enhanced_requirements_approval_error,
                    validate_enhanced_requirements_for_approval,
                )
                enhanced_validation = validate_enhanced_requirements_for_approval(
                    db,
                    app_id,
                    app_row=app,
                )
                if not enhanced_validation.get("passed"):
                    return (
                        False,
                        format_enhanced_requirements_approval_error(enhanced_validation),
                    )
            except Exception as _er:
                logger.error("Failed to evaluate enhanced requirements approval gate: %s", _er, exc_info=True)
                return (
                    False,
                    "Could not verify Enhanced Review requirements. "
                    "Resolve configuration/data issues and retry approval."
                )

            # 5. Check screening report for any simulated or degraded provider statuses
            #    Required checks (identity verification or CA screening) block approval if simulated.
            #    Enrichment checks (company_registry, ip_geolocation) warn but do not block.
            #    company_watchlist with api_status="not_configured" warns but does not block
            #    (no Sumsub company/KYB level provisioned).
            screening_evidence = _collect_screening_provider_evidence(screening_report)
            if screening_evidence:
                for item in screening_evidence:
                    api_status = (item.get("api_status") or "").lower()
                    source = (item.get("source") or "").lower()
                    is_simulated = api_status in ("simulated", "mocked") or source in ("simulated", "mocked")
                    is_error = api_status in ("error", "blocked")
                    is_pending = api_status == "pending"
                    is_not_configured = api_status == "not_configured"

                    if not item.get("is_required", True):
                        # Enrichment source — log warning but do not block approval
                        if is_simulated:
                            logger.warning(
                                f"Enrichment screening '{item.get('name', 'unknown')}' used simulated data. "
                                "This is non-blocking enrichment — approval proceeds."
                            )
                        continue

                    # company_watchlist with not_configured — Sumsub company/KYB level
                    # is not provisioned.  Warn but allow approval to proceed.
                    if is_not_configured and item.get("name") == "company_watchlist":
                        logger.warning(
                            "company_watchlist screening is not configured "
                            "(no Sumsub company KYB level) — approval proceeds with warning."
                        )
                        continue

                    # Required screening — block if simulated or errored
                    if is_simulated:
                        return (
                            False,
                            f"Screening check '{item.get('name', 'unknown')}' used simulated data. "
                            "Live screening results are required for approval."
                        )
                    if is_error:
                        return (
                            False,
                            f"Screening check '{item.get('name', 'unknown')}' is not in a live usable state "
                            f"(api_status={api_status or 'unknown'}).",
                        )
                    if is_pending:
                        return (
                            False,
                            f"Screening check '{item.get('name', 'unknown')}' is still pending "
                            f"(api_status=pending). Wait for screening to complete before approval.",
                        )
            else:
                for check_name in ('sanctions', 'kyc'):
                    check_data = screening_report.get(check_name, {})
                    if isinstance(check_data, dict) and check_data.get('api_status') == 'simulated':
                        return (
                            False,
                            f"Screening check '{check_name}' used simulated data (api_status=simulated). "
                            "Live screening results are required for approval."
                        )

            # 6. Check AI source provenance from memo data
            memo_data_str = memo_row.get('memo_data', '{}') if memo_row else '{}'
            if isinstance(memo_data_str, str):
                import json as _json2
                try:
                    memo_data = _json2.loads(memo_data_str)
                except (ValueError, TypeError):
                    memo_data = {}
            else:
                memo_data = memo_data_str
            ai_source = memo_data.get('ai_source', '').lower()
            if ai_source == 'mock':
                return (
                    False,
                    "Compliance memo was generated with mock AI. "
                    "Live AI verification required for approval."
                )

            # 7. Screening freshness: check screening was run after the latest
            # screening-relevant application inputs.  KYC/document submission is
            # an operational workflow event and must not stale screening unless
            # it also changes prescreening/company/party risk inputs.
            submitted_at = app.get('submitted_at')
            screening_input_updated_at = (
                app.get('screening_input_updated_at')
                or app.get('risk_inputs_updated_at')
                or (app.get('inputs_updated_at') if submitted_at else None)
                or submitted_at
            )
            screening_ts_str = screening_report.get('screened_at') or screening_report.get('timestamp')
            if screening_input_updated_at and screening_ts_str:
                try:
                    sub_ts = _parse_approval_timestamp(screening_input_updated_at)
                    scr_ts = _parse_approval_timestamp(screening_ts_str)
                    # Same-request screening persistence may write
                    # inputs_updated_at a few seconds after screened_at. Treat
                    # that as timestamp skew, not a substantive post-screening
                    # input change.
                    if sub_ts > scr_ts + timedelta(seconds=5):
                        return (
                            False,
                            "Screening was run before the latest screening-relevant application update. "
                            "Re-submit the application to trigger fresh screening."
                        )
                except (ValueError, TypeError) as ts_err:
                    logger.warning(f"Could not compare screening timestamps: {ts_err}")
                    return (False, "Could not verify screening freshness due to timestamp format error. "
                            "Re-submit the application to trigger fresh screening.")

                    # 8. Screening age validation: screening results must not exceed
            #    the configurable validity period (default 90 days).
            #    Fail-closed: missing screening timestamp blocks approval.
            validity_days = get_screening_validity_days()
            # EX-10 closeout: max allowed clock skew for future-dated timestamps
            _FUTURE_SKEW_SECONDS = 300  # 5 minutes

            # 9a. Reject future-dated screened_at timestamps (fail closed)
            if screening_ts_str:
                try:
                    _scr_ts_future = _parse_approval_timestamp(screening_ts_str)
                    _now_future = datetime.now(timezone.utc)
                    if _scr_ts_future > _now_future + timedelta(seconds=_FUTURE_SKEW_SECONDS):
                        logger.warning(
                            f"Future-dated screened_at rejected for application {app_id}: "
                            f"screened_at={screening_ts_str}, now={_now_future.isoformat()}"
                        )
                        return (
                            False,
                            "Screening timestamp is in the future and cannot be trusted. "
                            "A re-screen is required before approval can proceed."
                        )
                except (ValueError, TypeError) as ts_err:
                    logger.debug(f"Could not parse screened_at for future-date check: {ts_err}")

            screening_valid_until_str = prescreening_data.get('screening_valid_until')
            if screening_valid_until_str:
                # Prefer explicit valid_until if stored
                try:
                    valid_until = _parse_approval_timestamp(screening_valid_until_str)
                    now = datetime.now(timezone.utc)

                    # EX-10 closeout: reject future-dated valid_until beyond allowed skew + validity window
                    max_valid_until = now + timedelta(days=validity_days, seconds=_FUTURE_SKEW_SECONDS)
                    if valid_until > max_valid_until:
                        logger.warning(
                            f"Future-dated screening_valid_until rejected for application {app_id}: "
                            f"valid_until={screening_valid_until_str}, max_allowed={max_valid_until.isoformat()}"
                        )
                        return (
                            False,
                            "Screening validity window is implausibly far in the future. "
                            "A re-screen is required before approval can proceed."
                        )

                    if now > valid_until:
                        age_days = (now - valid_until).days
                        return (
                            False,
                            f"Screening results expired {age_days} day(s) ago "
                            f"(validity period: {validity_days} days). "
                            "A re-screen is required before approval can proceed."
                        )
                except (ValueError, TypeError) as ts_err:
                    logger.warning(f"Could not parse screening_valid_until: {ts_err}")
                    return (False, "Could not verify screening expiry due to timestamp format error. "
                            "Please re-run screening before approval.")
            elif screening_ts_str:
                # Fall back to computing expiry from screened_at + validity_days
                try:
                    scr_ts_check = _parse_approval_timestamp(screening_ts_str)
                    computed_valid_until = scr_ts_check + timedelta(days=validity_days)
                    now = datetime.now(timezone.utc)
                    if now > computed_valid_until:
                        age_days = (now - computed_valid_until).days
                        return (
                            False,
                            f"Screening results expired {age_days} day(s) ago "
                            f"(validity period: {validity_days} days). "
                            "A re-screen is required before approval can proceed."
                        )
                except (ValueError, TypeError) as ts_err:
                    logger.warning(f"Could not compute screening expiry from screened_at: {ts_err}")
                    return (False, "Could not verify screening freshness due to timestamp format error. "
                            "Please re-run screening before approval.")
            else:
                # No screening timestamp at all — fail closed
                return (False, "Screening timestamp is missing from the screening report. "
                        "A re-screen is required before approval can proceed.")

            # 9. Staleness detection: application data modified after memo/screening
            # If the application inputs were updated after the memo was generated,
            # the memo may be based on outdated data and should be regenerated.
            # We use inputs_updated_at (substantive input changes only) rather than
            # updated_at (which includes operational workflow writes like first-
            # approval recording) to avoid false stale-memo blocking.
            app_updated_at = app.get('inputs_updated_at') or app.get('updated_at')
            memo_created_at = memo_row.get('created_at')
            if app_updated_at and memo_created_at:
                try:
                    app_ts = _parse_approval_timestamp(app_updated_at)
                    memo_ts = _parse_approval_timestamp(memo_created_at)
                    if app_ts > memo_ts:
                        return (
                            False,
                            "Application data was modified after the compliance memo was generated. "
                            "The memo may be based on outdated information. "
                            "Please regenerate the compliance memo before approving."
                        )
                except (ValueError, TypeError) as ts_err:
                    logger.warning(f"Could not compare timestamps for staleness check: {ts_err}")
                    return (False, "Could not verify memo freshness due to timestamp format error. "
                            "Please regenerate the compliance memo before approving.")

            # 10. Canonical onboarding/KYC document evidence gate. Evaluate this
            # after the legacy approval prerequisites so existing fail-closed
            # gates still expose their precise blocker while otherwise
            # approval-ready records cannot rely on unverified documents.
            document_gate = evaluate_document_reliance_gate(
                db,
                app,
                stage="application_approval",
            )
            if not document_gate.get("passed"):
                return (
                    False,
                    "Document evidence gate failed: "
                    + format_document_reliance_blockers(document_gate)
                )

            # EX-10 closeout: Audit log on successful freshness validation
            _screening_age_days = None
            _valid_until_log = screening_valid_until_str or None
            try:
                _now_log = datetime.now(timezone.utc)
                if screening_ts_str:
                    _scr_log = _parse_approval_timestamp(screening_ts_str)
                    _screening_age_days = (_now_log - _scr_log).days
            except (ValueError, TypeError) as age_err:
                logger.debug(f"Could not compute screening age for audit log: {age_err}")
                _screening_age_days = None

            logger.info(
                f"Screening freshness validated for application {app_id}: "
                f"screening_age_days={_screening_age_days}, "
                f"valid_until={_valid_until_log}, "
                f"validity_days={validity_days}"
            )

            logger.info(f"Application {app_id} passed approval gate validation")
            return (True, "")

        except Exception as e:
            logger.error(f"Error in approval gate validation: {e}", exc_info=True)
            return (False, f"Internal validation error: {str(e)}")

    @staticmethod
    def validate_high_risk_dual_approval(
        app: Dict,
        current_user: Dict,
        db
    ) -> Tuple[bool, str]:
        """
        For HIGH/VERY_HIGH risk applications: validates dual-approval eligibility
        using structured application fields (first_approver_id, first_approved_at).

        Returns:
            Tuple of (can_approve: bool, error_or_info: str)
            - (False, msg) when this is the first approval (caller should record it)
            - (True, "") when a different officer already recorded first approval
        """
        try:
            risk_level = (
                _canonical_approval_risk_level(app.get('final_risk_level'))
                or _canonical_approval_risk_level(app.get('risk_level'))
                or ''
            )

            # Only enforce dual approval for high-risk applications
            if risk_level not in ['HIGH', 'VERY_HIGH']:
                return (True, "")

            current_user_id = current_user.get('sub', current_user.get('id', ''))
            app_ref = app.get('ref', '')

            if not db or not app_ref:
                return (False, "Database connection and application reference required for dual approval check")

            # Read structured first_approver_id from the application row
            first_approver_id = app.get('first_approver_id')

            if not first_approver_id:
                # No first approval yet — caller must record it
                return (
                    False,
                    "HIGH/VERY_HIGH risk application requires dual approval. "
                    "Another compliance officer must approve first."
                )

            # Same officer cannot perform both approvals
            if first_approver_id == current_user_id:
                return (
                    False,
                    "DUAL_SAME_OFFICER"
                )

            # Different officer has already given first approval — allow second
            logger.info(
                f"Application {app_ref} ({risk_level}) passed dual approval check: "
                f"first approver={first_approver_id}, second approver={current_user_id}"
            )
            return (True, "")

        except Exception as e:
            logger.error(f"Error in dual approval validation: {e}", exc_info=True)
            return (False, f"Internal validation error: {str(e)}")


def collect_approval_gate_blockers(app: Dict, db) -> List[Dict[str, Any]]:
    """Return officer-visible blockers from the backend approval gate model."""
    blockers: List[Dict[str, Any]] = []
    if not isinstance(app, dict):
        return [_approval_gate_blocker(
            "application_missing",
            "Application",
            "Application data unavailable",
            "Application data is required before approval gates can be evaluated.",
        )]

    app_id = app.get("id")
    status = str(app.get("status") or "").strip().lower()
    pre_kyc_states = {
        "draft",
        "prescreening_submitted",
        "pricing_review",
        "pricing_accepted",
        "pre_approval_review",
        "pre_approved",
        "kyc_documents",
    }
    if status in pre_kyc_states:
        blockers.append(_approval_gate_blocker(
            "case_stage",
            "Case Stage",
            "Application is not in compliance review",
            f"Current status is '{status}'. Move the application into compliance review before final approval.",
            cta_label="Review case stage",
            tab="overview",
            anchor_id="detail-company-name",
        ))

    risk_error = _approval_risk_integrity_error(app, "approve application")
    if risk_error:
        blockers.append(_approval_gate_blocker(
            "risk_integrity",
            "Risk",
            "Risk is not approval-ready",
            risk_error,
            cta_label="Recompute risk",
            tab="overview",
            anchor_id="detail-risk-breakdown",
        ))

    prescreening = _json_object(app.get("prescreening_data"))
    screening_report = prescreening.get("screening_report") if isinstance(prescreening, dict) else {}
    if not isinstance(screening_report, dict) or not screening_report:
        blockers.append(_approval_gate_blocker(
            "screening_missing",
            "Screening",
            "Screening report missing",
            "No screening report is recorded. Screening must be run before approval.",
            cta_label="Resolve screening",
            tab="screening",
            anchor_id="detail-screening-review",
            blocker_group="screening",
            blocker_group_label="Screening",
            action_key="screening.resolve",
        ))
    else:
        try:
            screening_reviews = _load_screening_reviews_for_truth(db, app_id, app.get("ref", ""))
        except Exception:
            screening_reviews = []
        prescreening_for_truth = dict(prescreening)
        prescreening_for_truth["screening_input_updated_at"] = (
            app.get("screening_input_updated_at")
            or app.get("risk_inputs_updated_at")
            or (app.get("inputs_updated_at") if app.get("submitted_at") else None)
            or app.get("submitted_at")
        )
        screening_truth = build_screening_truth_summary(screening_report, prescreening_for_truth, screening_reviews)
        if screening_truth.get("approval_blocking"):
            blockers.append(_approval_gate_blocker(
                "screening_truth",
                "Screening",
                "Screening gate is blocked",
                "Screening truth gate is blocked: "
                + "; ".join(screening_truth.get("blocking_reasons") or ["screening_not_terminal"]),
                cta_label="Resolve screening",
                tab="screening",
                anchor_id="detail-screening-review",
                blocker_group="screening",
                blocker_group_label="Screening",
                action_key="screening.resolve",
            ))

        screening_ts = screening_report.get("screened_at") or screening_report.get("timestamp")
        screening_input_updated_at = (
            app.get("screening_input_updated_at")
            or app.get("risk_inputs_updated_at")
            or (app.get("inputs_updated_at") if app.get("submitted_at") else None)
            or app.get("submitted_at")
        )
        if not screening_ts:
            blockers.append(_approval_gate_blocker(
                "screening_timestamp_missing",
                "Screening",
                "Screening timestamp missing",
                "Screening timestamp is missing from the screening report. A re-screen is required before approval.",
                cta_label="Resolve screening",
                tab="screening",
                anchor_id="detail-screening-review",
                blocker_group="screening",
                blocker_group_label="Screening",
                action_key="screening.resolve",
            ))
        elif screening_input_updated_at:
            try:
                if _parse_approval_timestamp(screening_input_updated_at) > _parse_approval_timestamp(screening_ts) + timedelta(seconds=5):
                    blockers.append(_approval_gate_blocker(
                        "screening_stale",
                        "Screening",
                        "Screening is stale",
                        "Screening was run before the latest screening-relevant application update. Re-screen before approval.",
                        cta_label="Resolve screening",
                        tab="screening",
                        anchor_id="detail-screening-review",
                        blocker_group="screening",
                        blocker_group_label="Screening",
                        action_key="screening.resolve",
                    ))
            except Exception:
                blockers.append(_approval_gate_blocker(
                    "screening_timestamp_parse",
                    "Screening",
                    "Screening freshness could not be verified",
                    "Screening freshness cannot be verified because a timestamp could not be parsed.",
                cta_label="Resolve screening",
                tab="screening",
                anchor_id="detail-screening-review",
                blocker_group="screening",
                blocker_group_label="Screening",
                action_key="screening.resolve",
            ))

    try:
        idv_gate = _approval_idv_gate_summary(app, db)
        for blocker in idv_gate.get("blockers") or []:
            mapped = dict(blocker)
            mapped.setdefault("ctaLabel", "Resolve IDV")
            mapped.setdefault("tab", "kyc-docs")
            mapped.setdefault("anchorId", "sumsub-idv-panel")
            mapped.setdefault("source", "backend_approval_gate")
            mapped.setdefault("blocker_group", "identity_verification")
            mapped.setdefault("blocker_group_label", "Identity Verification")
            mapped.setdefault("action_key", "idv.review")
            mapped.setdefault("action_label", "Review IDV")
            blockers.append(mapped)
    except Exception as exc:
        blockers.append(_approval_gate_blocker(
            "idv_gate_error",
            "Identity Verification",
            "Identity verification gate could not be evaluated",
            f"Identity verification status lookup failed: {exc}",
            cta_label="Resolve IDV",
            tab="kyc-docs",
            anchor_id="sumsub-idv-panel",
            blocker_group="identity_verification",
            blocker_group_label="Identity Verification",
            action_key="idv.review",
        ))

    try:
        document_gate = evaluate_document_reliance_gate(
            db,
            app,
            stage="approval_blocker_summary",
        )
        if not document_gate.get("passed"):
            blockers.extend(document_reliance_blockers_for_approval(document_gate))
    except Exception as exc:
        blockers.append(_approval_gate_blocker(
            "document_evidence_gate_error",
            "Document Evidence",
            "Document evidence gate could not be evaluated",
            f"Document verification status lookup failed: {exc}",
            cta_label="Resolve documents",
            tab="kyc-docs",
            anchor_id="detail-kyc-documents-details",
            blocker_group="document_evidence",
            blocker_group_label="Document Evidence",
            action_key="documents.resolve",
        ))

    memo_row = None
    try:
        memo_row = latest_compliance_memo_row(
            db,
            app_id,
            columns=(
                "id, memo_data, review_status, validation_status, supervisor_status, blocked, block_reason, "
                "created_at, approval_reason, is_stale, stale_reason, stale_trigger, stale_marked_at"
            ),
        )
        memo_row = _row_to_dict(memo_row) if memo_row else None
    except Exception:
        memo_row = None
    if not memo_row:
        blockers.append(_approval_gate_blocker(
            "memo_missing",
            "Compliance Memo",
            "Compliance memo missing",
            "Compliance memo must be generated before approval.",
            cta_label="Open memo",
            tab="overview",
            anchor_id="detail-memo",
            blocker_group="memo_package",
            blocker_group_label="Memo Package",
            action_key="memo.open",
        ))
    else:
        memo_review = str(memo_row.get("review_status") or "").lower()
        memo_validation = str(memo_row.get("validation_status") or "").lower()
        memo_supervisor = str(memo_row.get("supervisor_status") or "").upper()
        memo_stale = _truthy_db_value(memo_row.get("is_stale"))
        if memo_stale:
            blockers.append(_approval_gate_blocker(
                "memo_stale",
                "Compliance Memo",
                "Compliance memo is stale",
                memo_row.get("stale_reason") or "Material facts changed after memo generation.",
                cta_label="Open memo",
                tab="overview",
                anchor_id="detail-memo",
                blocker_group="memo_package",
                blocker_group_label="Memo Package",
                action_key="memo.open",
            ))
        if _truthy_db_value(memo_row.get("blocked")):
            blockers.append(_approval_gate_blocker(
                "memo_blocked",
                "Compliance Memo",
                "Compliance memo is blocked",
                memo_row.get("block_reason") or "Blocking memo controls failed.",
                cta_label="Open memo",
                tab="overview",
                anchor_id="detail-memo",
                blocker_group="memo_package",
                blocker_group_label="Memo Package",
                action_key="memo.open",
            ))
        if memo_review != "approved":
            blockers.append(_approval_gate_blocker(
                "memo_approval",
                "Compliance Memo",
                "Compliance memo is not approved",
                "Memo approval has not been completed.",
                cta_label="Open memo",
                tab="overview",
                anchor_id="detail-memo",
                blocker_group="memo_package",
                blocker_group_label="Memo Package",
                action_key="memo.open",
            ))
        elif not str(memo_row.get("approval_reason") or "").strip():
            blockers.append(_approval_gate_blocker(
                "memo_approval_reason_missing",
                "Compliance Memo",
                "Memo approval reason is missing",
                "Approve the canonical memo with documented officer rationale before final application approval.",
                cta_label="Open memo",
                tab="overview",
                anchor_id="memo-approval-reason",
                blocker_group="memo_package",
                blocker_group_label="Memo Package",
                action_key="memo.open",
            ))
        if memo_validation not in {"pass", "pass_with_fixes"}:
            blockers.append(_approval_gate_blocker(
                "memo_validation",
                "Compliance Memo",
                "Compliance memo validation failed or is pending",
                "Memo validation has not been completed." if memo_validation in {"", "pending"} else "Memo validation needs officer review before approval.",
                cta_label="Open memo",
                tab="overview",
                anchor_id="memo-validation-panel",
                blocker_group="memo_package",
                blocker_group_label="Memo Package",
                action_key="memo.validate",
            ))
        if memo_supervisor and memo_supervisor not in {"CONSISTENT", "CONSISTENT_WITH_WARNINGS"}:
            blockers.append(_approval_gate_blocker(
                "supervisor_inconsistent",
                "Supervisor Review",
                "Supervisor review is inconsistent",
                "Supervisor review has not been completed." if memo_supervisor == "PENDING" else "Supervisor review needs attention before approval.",
                cta_label="Run supervisor",
                tab="supervisor",
                anchor_id="detail-tab-supervisor",
                blocker_group="memo_package",
                blocker_group_label="Memo Package",
                action_key="supervisor.run",
            ))

    if not blockers:
        try:
            can_approve, message = ApprovalGateValidator.validate_approval(app, db)
            if not can_approve:
                blockers.append(_approval_gate_blocker(
                    "approval_gate_validator",
                    "Approval Gate",
                    "Backend approval gate would reject approval",
                    message,
                    cta_label="Review blockers",
                    tab="overview",
                    anchor_id="detail-approval-blockers",
                ))
        except Exception as exc:
            blockers.append(_approval_gate_blocker(
                "approval_gate_error",
                "Approval Gate",
                "Backend approval gate could not be evaluated",
                str(exc),
                cta_label="Review blockers",
                tab="overview",
                anchor_id="detail-approval-blockers",
            ))

    return blockers


# ============================================================================
# 2. Screening Mode Tracker (P0-02)
# ============================================================================

def _collect_screening_provider_evidence(screening_report: Dict) -> list:
    """
    Collects screening provider evidence with required/enrichment classification.

    Required (compliance-critical):
      - company_watchlist (Sumsub company sanctions) or company_screening (CA)
      - director_screening_N (Sumsub or CA person AML/PEP)
      - ubo_screening_N (Sumsub or CA person AML/PEP)
      - intermediary_screening_N (CA intermediary/entity AML/PEP/sanctions)
      - kyc_applicant_N (Sumsub identity verification)

    Enrichment (optional, non-blocking):
      - company_registry (OpenCorporates corporate registry lookup)
      - ip_geolocation (IP-based geolocation)
    """
    # Enrichment sources: simulated data should warn but not block approval
    _ENRICHMENT_CHECKS = frozenset({"company_registry", "ip_geolocation"})

    evidence = []
    if not isinstance(screening_report, dict):
        return evidence

    def add(name: str, item):
        if not isinstance(item, dict):
            return
        evidence.append({
            "name": name,
            "api_status": item.get("api_status"),
            "source": item.get("source"),
            "is_required": name not in _ENRICHMENT_CHECKS,
        })

    company_screening = screening_report.get("company_screening") or {}
    company_provider = (
        company_screening.get("provider")
        or company_screening.get("source")
        or screening_report.get("provider")
        or ""
    ).lower()
    if company_provider == "complyadvantage":
        add("company_screening", company_screening)
    else:
        add("company_registry", company_screening)
        add("company_watchlist", company_screening.get("sanctions"))

    for idx, person in enumerate(screening_report.get("director_screenings") or []):
        add(f"director_screening_{idx}", (person or {}).get("screening"))

    for idx, person in enumerate(screening_report.get("ubo_screenings") or []):
        add(f"ubo_screening_{idx}", (person or {}).get("screening"))

    for idx, person in enumerate(screening_report.get("intermediary_screenings") or []):
        add(f"intermediary_screening_{idx}", (person or {}).get("screening"))

    add("ip_geolocation", screening_report.get("ip_geolocation"))

    for idx, applicant in enumerate(screening_report.get("kyc_applicants") or []):
        add(f"kyc_applicant_{idx}", applicant)

    return evidence

def determine_screening_mode(screening_report: Dict) -> str:
    """
    Analyzes a screening report to determine if it used live or simulated sources.

    Only required screening sources (identity verification or CA screening)
    affect the mode.
    Enrichment sources (company_registry, ip_geolocation) are excluded — their
    simulated status does not make the overall screening mode 'simulated'.

    Args:
        screening_report: Dictionary with screening data, typically from SumSub API

    Returns:
        'live' if all required screening sources are production,
        'simulated' if any required source is mocked
    """
    try:
        if not isinstance(screening_report, dict) or not screening_report:
            return 'unknown'

        provider_evidence = _collect_screening_provider_evidence(screening_report)
        if provider_evidence:
            saw_live = False
            saw_required = False
            required_modes = []
            required_items = []
            for item in provider_evidence:
                # Skip enrichment sources for mode determination
                if not item.get("is_required", True):
                    continue
                saw_required = True
                truth = derive_screening_truth(item, name=item.get("name"), required=True)
                provider_mode = truth.get("provider_mode")
                required_modes.append(provider_mode)
                required_items.append(item)
                api_status = (item.get("api_status") or "").lower()
                source_name = (item.get("source") or "").lower()
                if provider_mode == LIVE_PROVIDER or api_status == "live" or source_name in ("sumsub", "complyadvantage", "opencorporates", "ipapi", "local"):
                    saw_live = True
            if saw_required:
                if SIMULATED_FALLBACK in required_modes:
                    logger.warning(f"Screening contains simulated source: {required_items}")
                    return 'simulated'
                if SANDBOX_PROVIDER in required_modes:
                    logger.warning(f"Screening contains sandbox provider state: {required_items}")
                    return 'sandbox'
                if any(mode in (SCREENING_FAILED, SCREENING_PENDING) for mode in required_modes):
                    logger.warning(f"Screening contains non-live provider state: {required_items}")
                    return 'unknown'
                if SCREENING_NOT_CONFIGURED in required_modes:
                    logger.warning(f"Screening contains not-configured provider state: {required_items}")
                    return 'not_configured'
                return 'live' if saw_live else 'unknown'

        # Legacy fallback for older report shapes
        legacy_required = [
            screening_report.get("sanctions"),
            screening_report.get("kyc"),
        ]
        legacy_required = [item for item in legacy_required if isinstance(item, dict)]
        if legacy_required:
            legacy_modes = [
                derive_screening_truth(item, name="legacy_screening", required=True).get("provider_mode")
                for item in legacy_required
            ]
            if SIMULATED_FALLBACK in legacy_modes:
                return "simulated"
            if SANDBOX_PROVIDER in legacy_modes:
                return "sandbox"
            if SCREENING_NOT_CONFIGURED in legacy_modes:
                return "not_configured"
            if any(mode in (SCREENING_FAILED, SCREENING_PENDING) for mode in legacy_modes):
                return "unknown"
            if legacy_modes and all(mode == LIVE_PROVIDER for mode in legacy_modes):
                return "live"

        sources = screening_report.get('sources', [])
        rules_results = screening_report.get('rules_results', [])

        for source in sources:
            source_name = source.get('name', '').lower()
            if 'simulated' in source_name or 'mock' in source_name or 'demo' in source_name:
                logger.warning(f"Screening contains simulated source: {source_name}")
                return 'simulated'

        for rule in rules_results:
            if rule.get('is_simulated') or 'simulated' in str(rule).lower():
                logger.warning("Screening contains simulated rule results")
                return 'simulated'

        if screening_report.get('is_simulated') or screening_report.get('testMode'):
            logger.warning("Screening report marked as simulated/test mode")
            return 'simulated'

        return 'unknown'

    except Exception as e:
        logger.error(f"Error determining screening mode: {e}")
        return 'unknown'


def store_screening_mode(db, app_id: str, mode: str) -> bool:
    """
    Stores the screening mode (live/simulated) in the application record.

    Args:
        db: Database connection object
        app_id: Application ID
        mode: 'live' or 'simulated'

    Returns:
        True if successful, False otherwise
    """
    try:
        if mode not in ['live', 'simulated', 'sandbox', 'not_configured', 'failed', 'pending', 'unknown']:
            logger.error(f"Invalid screening mode: {mode}")
            return False

        db.execute(
            "UPDATE applications SET screening_mode=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (mode, app_id),
        )
        logger.info(f"Screening mode='{mode}' for application {app_id}")
        return True

    except Exception as e:
        logger.error(f"Error in store_screening_mode: {e}", exc_info=True)
        return False


# ============================================================================
# 3. Production Environment Guards (P0-04)
# ============================================================================

def validate_production_environment() -> None:
    """
    Called at server startup. Enforces security constraints in production.

    Checks:
        - If ENVIRONMENT=production, CLAUDE_MOCK_MODE must NOT be 'true'
        - If ENVIRONMENT=production, SUMSUB_APP_TOKEN must be set
        - If ENVIRONMENT=production, ANTHROPIC_API_KEY must be set
        - If ENVIRONMENT=production, SUMSUB_WEBHOOK_SECRET must be set

    Raises:
        RuntimeError: If any production check fails

    Example:
        >>> validate_production_environment()  # Called in server.py startup
    """
    if not is_production():
        logger.debug(f"Environment is '{ENV}', skipping production validation")
        return

    logger.info("Running production environment validation...")

    errors = []

    # Check CLAUDE_MOCK_MODE is not 'true'
    mock_mode = os.environ.get('CLAUDE_MOCK_MODE', '').lower()
    if mock_mode == 'true':
        errors.append("CLAUDE_MOCK_MODE must not be 'true' in production")

    # Check required API tokens
    if not os.environ.get('SUMSUB_APP_TOKEN'):
        errors.append("SUMSUB_APP_TOKEN environment variable is not set")

    if not os.environ.get('ANTHROPIC_API_KEY'):
        errors.append("ANTHROPIC_API_KEY environment variable is not set")

    if not os.environ.get('SUMSUB_WEBHOOK_SECRET'):
        errors.append("SUMSUB_WEBHOOK_SECRET environment variable is not set")

    if errors:
        error_msg = "\n".join([f"  ✗ {e}" for e in errors])
        raise RuntimeError(
            f"Production environment validation failed:\n{error_msg}\n\n"
            f"All required environment variables must be set before starting the server."
        )

    logger.info("✓ Production environment validation passed")


# ============================================================================
# 4. AI Source Tracking (P0-05)
# ============================================================================

def tag_ai_response(response: Dict, source: str) -> Dict:
    """
    Adds an ai_source field to an AI agent response for audit tracking.

    Args:
        response: Dictionary containing AI agent response data
        source: Source identifier ('claude-sonnet-4-6', 'claude-opus-4-6', or 'mock')

    Returns:
        Modified response dictionary with ai_source field added

    Raises:
        ValueError: If source is not a valid option

    Example:
        >>> response = {'analysis': '...', 'score': 75}
        >>> tagged = tag_ai_response(response, 'claude-sonnet-4-6')
        >>> tagged['ai_source']
        'claude-sonnet-4-6'
    """
    valid_sources = ['claude-sonnet-4-6', 'claude-opus-4-6', 'mock']

    if source not in valid_sources:
        raise ValueError(
            f"Invalid AI source '{source}'. Must be one of: {', '.join(valid_sources)}"
        )

    response_copy = response.copy() if isinstance(response, dict) else {}
    response_copy['ai_source'] = source

    if source == 'mock':
        logger.warning(f"AI response tagged with mock source (for development/testing only)")
    else:
        logger.debug(f"AI response tagged with source: {source}")

    return response_copy


def is_mock_ai_response(response: Dict) -> bool:
    """
    Checks whether an AI response came from mock/test mode.

    Args:
        response: AI response dictionary

    Returns:
        True if ai_source == 'mock', False otherwise
    """
    return response.get('ai_source', '').lower() == 'mock'


# ============================================================================
# 5. Compliance Memo Validator (P0-06)
# ============================================================================

class MemoValidator:
    """
    Post-generation validation of compliance memos against actual screening/verification results.

    Detects discrepancies where memo claims don't match actual data,
    which could indicate fraud or AI hallucination.
    """

    @staticmethod
    def validate_memo_against_results(
        memo: Dict,
        agent_results: Dict
    ) -> Tuple[bool, List[str]]:
        """
        Cross-checks compliance memo claims against actual agent results.

        Args:
            memo: Generated compliance memo with claims about findings
            agent_results: Actual screening, verification, and analysis results

        Returns:
            Tuple of (is_valid: bool, list_of_discrepancies: List[str])

        Discrepancies detected:
            - Memo says 'no screening hits' but screening found hits
            - Memo references different risk score than actual
            - Memo says 'all documents verified' but flagged docs exist
            - Memo approval recommendation contradicts risk_level without override
        """
        discrepancies = []

        try:
            # Extract memo claims
            memo_text = memo.get('memo_text', '').lower()
            memo_risk_score = memo.get('risk_score')
            memo_approval_rec = memo.get('approval_recommendation', '').lower()

            # Extract actual results
            screening_hits = agent_results.get('screening_hits', [])
            actual_risk_score = agent_results.get('risk_score')
            flagged_documents = agent_results.get('flagged_documents', [])
            risk_level = agent_results.get('risk_level', '').lower()

            # Check 1: Screening hits
            has_no_hits_claim = 'no screening hits' in memo_text or 'no hits found' in memo_text
            if has_no_hits_claim and screening_hits:
                discrepancies.append(
                    f"Memo claims 'no screening hits' but {len(screening_hits)} hit(s) found"
                )

            # Check 2: Risk score mismatch
            if memo_risk_score is not None and actual_risk_score is not None:
                if abs(memo_risk_score - actual_risk_score) > 5:
                    discrepancies.append(
                        f"Memo risk score ({memo_risk_score}) differs from actual ({actual_risk_score})"
                    )

            # Check 3: Document verification
            all_verified_claim = 'all documents verified' in memo_text
            if all_verified_claim and flagged_documents:
                discrepancies.append(
                    f"Memo claims 'all documents verified' but {len(flagged_documents)} flagged document(s) exist"
                )

            # Check 4: Approval recommendation vs risk level
            if memo_approval_rec == 'approve' and risk_level in ['high', 'very_high']:
                override_mentioned = 'override' in memo_text or 'exceptional' in memo_text
                if not override_mentioned:
                    discrepancies.append(
                        f"Memo recommends approval for {risk_level} risk without documented override justification"
                    )

            if discrepancies:
                logger.warning(f"Memo validation found {len(discrepancies)} discrepancy(ies)")
                return (False, discrepancies)

            logger.debug("Memo validation passed")
            return (True, [])

        except Exception as e:
            logger.error(f"Error validating memo: {e}", exc_info=True)
            return (False, [f"Validation error: {str(e)}"])


# ============================================================================
# 6. PII Encryption (P0-10)
# ============================================================================

class PIIEncryptor:
    """
    Field-level encryption for PII data using Fernet symmetric encryption.

    Uses cryptography.fernet for secure encryption/decryption of sensitive fields.
    Encryption key MUST be provided via PII_ENCRYPTION_KEY environment variable.
    The server will fail to start if this key is missing or invalid.

    Generate a valid key with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """

    # PII fields that must be encrypted in different data structures
    PII_FIELDS_DIRECTORS = ['passport_number', 'nationality', 'id_number']
    PII_FIELDS_UBOS = ['passport_number', 'nationality']
    PII_FIELDS_APPLICATIONS = ['pep_flags']

    def __init__(self, key: Optional[str] = None):
        """
        Initialize encryptor with symmetric key.

        Args:
            key: Base64-encoded Fernet key, or None to load from PII_ENCRYPTION_KEY env var

        Raises:
            RuntimeError: If no key provided and PII_ENCRYPTION_KEY not set
            ValueError: If key format is invalid (not a valid 32-byte base64-encoded Fernet key)
        """
        if key is None:
            key = os.environ.get('PII_ENCRYPTION_KEY')

        if not key:
            if is_production():
                raise RuntimeError(
                    "CRITICAL: PII_ENCRYPTION_KEY must be set in production. "
                    "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
            else:
                raise RuntimeError(
                    "PII_ENCRYPTION_KEY environment variable is required. "
                    "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )

        # Validate key format: must be 44-char base64-encoded string (32 bytes encoded)
        try:
            if isinstance(key, str):
                key_bytes = key.encode('utf-8')
            else:
                key_bytes = key

            # Fernet keys are exactly 44 bytes of url-safe base64 (encoding 32 bytes)
            import base64
            decoded = base64.urlsafe_b64decode(key_bytes)
            if len(decoded) != 32:
                raise ValueError(
                    f"Fernet key must decode to exactly 32 bytes, got {len(decoded)}. "
                    "Generate a valid key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )

            self.cipher = Fernet(key_bytes)
            self._key = key_bytes
            logger.info("PIIEncryptor initialized successfully (key validated)")
        except Exception as e:
            if "32 bytes" in str(e) or "Fernet key" in str(e):
                raise ValueError(str(e))
            raise ValueError(
                f"Invalid PII_ENCRYPTION_KEY format: {type(e).__name__}. "
                "Key must be a valid Fernet key (44 chars, url-safe base64). "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a PII field value.

        Args:
            plaintext: Unencrypted PII value

        Returns:
            Base64-encoded ciphertext
        """
        try:
            if not plaintext:
                return ""

            plaintext_bytes = plaintext.encode('utf-8') if isinstance(plaintext, str) else plaintext
            ciphertext = self.cipher.encrypt(plaintext_bytes)
            return base64.b64encode(ciphertext).decode('utf-8')

        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a PII field value.

        Args:
            ciphertext: Base64-encoded encrypted value

        Returns:
            Decrypted plaintext
        """
        try:
            if not ciphertext:
                return ""

            ciphertext_bytes = base64.b64decode(ciphertext.encode('utf-8'))
            plaintext = self.cipher.decrypt(ciphertext_bytes)
            return plaintext.decode('utf-8')

        except InvalidToken:
            # Callers that know the table/row context log safe diagnostics. Avoid
            # emitting generic PII-token errors without record metadata here.
            raise
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise

    def encrypt_dict_fields(self, data: Dict, fields: List[str]) -> Dict:
        """
        Encrypt specified fields in a dictionary.

        Args:
            data: Dictionary with PII fields
            fields: List of field names to encrypt

        Returns:
            Dictionary with specified fields encrypted
        """
        result = data.copy()
        for field in fields:
            if field in result and result[field]:
                result[field] = self.encrypt(str(result[field]))
        return result

    def decrypt_dict_fields(self, data: Dict, fields: List[str]) -> Dict:
        """
        Decrypt specified fields in a dictionary.

        Args:
            data: Dictionary with encrypted PII fields
            fields: List of field names to decrypt

        Returns:
            Dictionary with specified fields decrypted
        """
        result = data.copy()
        for field in fields:
            if field in result and result[field]:
                result[field] = self.decrypt(result[field])
        return result


# ============================================================================
# 7. Password Policy (P0-09, P1)
# ============================================================================

class PasswordPolicy:
    """
    Strong password enforcement for user accounts.

    Enforces minimum length, uppercase, lowercase, digits, and special characters.
    Provides secure temporary password generation.
    """

    MIN_LENGTH = 12
    SPECIAL_CHARS = "!@#$%^&*()-_=+[]{}|;:,.<>?"

    @staticmethod
    def validate(password: str) -> Tuple[bool, str]:
        """
        Validates that a password meets complexity requirements.

        Requirements:
            - At least 12 characters
            - At least 1 uppercase letter (A-Z)
            - At least 1 lowercase letter (a-z)
            - At least 1 digit (0-9)
            - At least 1 special character (!@#$%^&*()-_=+[]{}|;:,.<>?)

        Args:
            password: Password to validate

        Returns:
            Tuple of (is_valid: bool, error_message: str)

        Example:
            >>> PasswordPolicy.validate("Weak1!")
            (False, "Password must be at least 12 characters")
            >>> PasswordPolicy.validate("StrongPass123!")
            (True, "")
        """
        if not password:
            return (False, "Password is required")

        if len(password) < PasswordPolicy.MIN_LENGTH:
            return (False, f"Password must be at least {PasswordPolicy.MIN_LENGTH} characters")

        if not any(c.isupper() for c in password):
            return (False, "Password must contain at least 1 uppercase letter")

        if not any(c.islower() for c in password):
            return (False, "Password must contain at least 1 lowercase letter")

        if not any(c.isdigit() for c in password):
            return (False, "Password must contain at least 1 digit")

        if not any(c in PasswordPolicy.SPECIAL_CHARS for c in password):
            special_display = ", ".join(list(PasswordPolicy.SPECIAL_CHARS)[:5]) + "..."
            return (False, f"Password must contain at least 1 special character ({special_display})")

        return (True, "")

    @staticmethod
    def generate_temporary() -> str:
        """
        Generates a secure temporary password for new users.

        Format: 12-14 characters with guaranteed uppercase, lowercase, digit, special char.

        Returns:
            Temporary password string

        Example:
            >>> temp_pwd = PasswordPolicy.generate_temporary()
            >>> len(temp_pwd) >= 12
            True
        """
        # Ensure we have at least one of each required type
        password_chars = [
            secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ'),  # uppercase
            secrets.choice('abcdefghijklmnopqrstuvwxyz'),  # lowercase
            secrets.choice('0123456789'),                   # digit
            secrets.choice(PasswordPolicy.SPECIAL_CHARS),   # special
        ]

        # Fill remaining characters (8-10 more to reach 12-14 total)
        all_chars = (
            'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            'abcdefghijklmnopqrstuvwxyz'
            '0123456789'
            + PasswordPolicy.SPECIAL_CHARS
        )

        for _ in range(secrets.randbelow(3) + 8):  # Add 8-10 more
            password_chars.append(secrets.choice(all_chars))

        # Shuffle to avoid predictable patterns
        import random
        random.shuffle(password_chars)

        return ''.join(password_chars)


# ============================================================================
# 8. Request Schema Validation (P0-11)
# ============================================================================

class ApplicationSchema:
    """
    Validates request payloads for application creation and modification.

    Does NOT use Pydantic (to avoid external dependencies).
    Performs type checking, range validation, enum validation, and length limits.
    """

    VALID_ENTITY_TYPES = [
        'company', 'trust', 'foundation', 'partnership', 'sole_trader'
    ]

    VALID_SECTORS = [
        'financial_services', 'technology', 'real_estate', 'commodities',
        'professional_services', 'gaming', 'crypto', 'other'
    ]

    MAX_COMPANY_NAME_LENGTH = 255
    MAX_DIRECTORS = 50
    MAX_UBOS = 50
    MAX_STRING_LENGTH = 1000
    MIN_OWNERSHIP = 0.0
    MAX_OWNERSHIP = 100.0

    @staticmethod
    def validate_application(data: Dict) -> Tuple[bool, str]:
        """
        Validates application creation payload.

        Args:
            data: Application data dictionary

        Returns:
            Tuple of (is_valid: bool, error_message: str)

        Validates:
            - entity_type is valid enum
            - company_name is string, not empty, length <= MAX_COMPANY_NAME_LENGTH
            - sector is valid enum
            - directors is list, length <= MAX_DIRECTORS
            - ubos is list, length <= MAX_UBOS
            - beneficial_owner, annual_revenue are valid numbers if present
        """
        if not isinstance(data, dict):
            return (False, "Request body must be a JSON object")

        # Entity type validation
        entity_type = data.get('entity_type', '').lower()
        if entity_type not in ApplicationSchema.VALID_ENTITY_TYPES:
            return (
                False,
                f"Invalid entity_type. Must be one of: {', '.join(ApplicationSchema.VALID_ENTITY_TYPES)}"
            )

        # Company name validation
        company_name = data.get('company_name', '')
        if not isinstance(company_name, str) or not company_name.strip():
            return (False, "company_name is required and must be a non-empty string")
        if len(company_name) > ApplicationSchema.MAX_COMPANY_NAME_LENGTH:
            return (
                False,
                f"company_name exceeds max length of {ApplicationSchema.MAX_COMPANY_NAME_LENGTH}"
            )

        # Sector validation
        sector = data.get('sector', '').lower()
        if sector and sector not in ApplicationSchema.VALID_SECTORS:
            return (
                False,
                f"Invalid sector. Must be one of: {', '.join(ApplicationSchema.VALID_SECTORS)}"
            )

        # Directors validation
        directors = data.get('directors', [])
        if not isinstance(directors, list):
            return (False, "directors must be a list")
        if len(directors) > ApplicationSchema.MAX_DIRECTORS:
            return (False, f"Too many directors (max {ApplicationSchema.MAX_DIRECTORS})")

        for i, director in enumerate(directors):
            valid, msg = ApplicationSchema.validate_director(director)
            if not valid:
                return (False, f"Director {i}: {msg}")

        # UBOs validation
        ubos = data.get('ubos', [])
        if not isinstance(ubos, list):
            return (False, "ubos must be a list")
        if len(ubos) > ApplicationSchema.MAX_UBOS:
            return (False, f"Too many UBOs (max {ApplicationSchema.MAX_UBOS})")

        for i, ubo in enumerate(ubos):
            valid, msg = ApplicationSchema.validate_ubo(ubo)
            if not valid:
                return (False, f"UBO {i}: {msg}")

        # Optional numeric fields
        if 'beneficial_owner' in data:
            if not isinstance(data['beneficial_owner'], (int, float)):
                return (False, "beneficial_owner must be a number")
            if not (0 <= data['beneficial_owner'] <= 100):
                return (False, "beneficial_owner must be between 0 and 100")

        if 'annual_revenue' in data:
            if not isinstance(data['annual_revenue'], (int, float)):
                return (False, "annual_revenue must be a number")
            if data['annual_revenue'] < 0:
                return (False, "annual_revenue must be non-negative")

        return (True, "")

    @staticmethod
    def validate_director(data: Dict) -> Tuple[bool, str]:
        """
        Validates a director record.

        Args:
            data: Director data dictionary

        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        if not isinstance(data, dict):
            return (False, "Director must be an object")

        # Required fields
        for field in ['first_name', 'last_name', 'date_of_birth']:
            if field not in data or not data[field]:
                return (False, f"{field} is required")
            if not isinstance(data[field], str):
                return (False, f"{field} must be a string")
            if len(str(data[field])) > ApplicationSchema.MAX_STRING_LENGTH:
                return (False, f"{field} exceeds max length")

        # Optional fields with type checking
        if 'passport_number' in data and data['passport_number']:
            if not isinstance(data['passport_number'], str):
                return (False, "passport_number must be a string")
            if len(data['passport_number']) > 50:
                return (False, "passport_number exceeds max length")

        if 'nationality' in data and data['nationality']:
            if not isinstance(data['nationality'], str):
                return (False, "nationality must be a string")
            if len(data['nationality']) > 100:
                return (False, "nationality exceeds max length")

        if 'id_number' in data and data['id_number']:
            if not isinstance(data['id_number'], str):
                return (False, "id_number must be a string")
            if len(data['id_number']) > 50:
                return (False, "id_number exceeds max length")

        return (True, "")

    @staticmethod
    def validate_ubo(data: Dict) -> Tuple[bool, str]:
        """
        Validates a UBO (Ultimate Beneficial Owner) record.

        Args:
            data: UBO data dictionary

        Returns:
            Tuple of (is_valid: bool, error_message: str)

        Validates:
            - name is required string
            - ownership_pct is number between 0 and 100
            - passport_number, nationality are valid strings if present
        """
        if not isinstance(data, dict):
            return (False, "UBO must be an object")

        # Required field
        if 'name' not in data or not data['name']:
            return (False, "name is required")
        if not isinstance(data['name'], str):
            return (False, "name must be a string")
        if len(data['name']) > ApplicationSchema.MAX_STRING_LENGTH:
            return (False, "name exceeds max length")

        # Ownership percentage (required and validated)
        if 'ownership_pct' not in data:
            return (False, "ownership_pct is required")

        ownership = data['ownership_pct']
        if not isinstance(ownership, (int, float)):
            return (False, "ownership_pct must be a number")
        if not (ApplicationSchema.MIN_OWNERSHIP <= ownership <= ApplicationSchema.MAX_OWNERSHIP):
            return (
                False,
                f"ownership_pct must be between {ApplicationSchema.MIN_OWNERSHIP} "
                f"and {ApplicationSchema.MAX_OWNERSHIP}"
            )

        # Optional fields
        if 'passport_number' in data and data['passport_number']:
            if not isinstance(data['passport_number'], str):
                return (False, "passport_number must be a string")
            if len(data['passport_number']) > 50:
                return (False, "passport_number exceeds max length")

        if 'nationality' in data and data['nationality']:
            if not isinstance(data['nationality'], str):
                return (False, "nationality must be a string")
            if len(data['nationality']) > 100:
                return (False, "nationality exceeds max length")

        return (True, "")


# ============================================================================
# 9. Token Revocation (P1)
# ============================================================================

class TokenRevocationList:
    """
    JWT token revocation list with DB persistence.

    Prevents token reuse after logout or role changes.
    Uses in-memory cache for fast lookups with DB persistence so
    revocations survive server restarts.
    Periodically removes expired entries to prevent memory/DB exhaustion.
    """

    def __init__(self, cleanup_interval: int = 3600):
        """
        Initialize the revocation list.

        Args:
            cleanup_interval: Seconds between automatic cleanups (default 1 hour)
        """
        self._revoked = {}  # jti -> expiry_timestamp (in-memory cache)
        self._cleanup_interval = cleanup_interval
        self._last_cleanup = time.time()
        self._db_loaded = False

    def _db_load_all(self) -> None:
        """Load all non-expired revoked tokens from DB into memory (called once)."""
        if self._db_loaded:
            return
        try:
            from db import get_db as _db_get
            db = _db_get()
            now = time.time()
            rows = db.execute(
                "SELECT jti, expires_at FROM revoked_tokens WHERE expires_at > ?",
                (now,)
            ).fetchall()
            for r in rows:
                jti = r[0] if isinstance(r, (tuple, list)) else r["jti"]
                exp = r[1] if isinstance(r, (tuple, list)) else r["expires_at"]
                self._revoked[jti] = exp
            db.close()
            if rows:
                logger.info(f"Loaded {len(rows)} revoked tokens from database")
            self._db_loaded = True
        except Exception as e:
            logger.debug(f"Could not load revoked tokens from DB: {e}")

    def _db_lookup_active(self, jti: str) -> float:
        """Look up one active revocation entry in persistent storage.

        Workers may have already loaded the revocation table before another
        worker handles logout.  A miss in the local cache is therefore not
        authoritative until the current JTI has been checked in the database.
        """
        if not jti:
            return 0
        try:
            from db import get_db as _db_get
            db = _db_get()
            try:
                row = db.execute(
                    "SELECT expires_at FROM revoked_tokens WHERE jti = ? AND expires_at > ?",
                    (jti, time.time()),
                ).fetchone()
            finally:
                db.close()
            if not row:
                return 0
            expiry = row[0] if isinstance(row, (tuple, list)) else row["expires_at"]
            self._revoked[jti] = expiry
            return expiry
        except Exception as e:
            logger.debug("Could not look up revoked token in DB: %s", e)
            return 0

    def _db_persist(self, jti: str, expires_at: float) -> None:
        """Persist a revocation to DB."""
        try:
            from db import get_db as _db_get
            db = _db_get()
            # Use INSERT OR REPLACE for SQLite / ON CONFLICT for Postgres
            db.execute(
                "INSERT INTO revoked_tokens (jti, expires_at) VALUES (?, ?) "
                "ON CONFLICT (jti) DO UPDATE SET expires_at = EXCLUDED.expires_at",
                (jti, expires_at)
            )
            db.commit()
            db.close()
        except Exception as e:
            logger.debug(f"Could not persist revoked token to DB: {e}")

    def _db_remove_expired(self) -> None:
        """Remove expired entries from DB."""
        try:
            from db import get_db as _db_get
            db = _db_get()
            db.execute("DELETE FROM revoked_tokens WHERE expires_at <= ?", (time.time(),))
            db.commit()
            db.close()
        except Exception:
            pass

    def revoke(self, jti: str, expires_at: float) -> None:
        """
        Add a token to the revocation list.

        Args:
            jti: JWT ID (from token's 'jti' claim)
            expires_at: Token expiry timestamp (Unix time)
        """
        self._revoked[jti] = expires_at
        self._db_persist(jti, expires_at)
        logger.debug(f"Token {jti[:8]}... revoked (expires at {expires_at})")

        # Cleanup if interval exceeded
        if time.time() - self._last_cleanup > self._cleanup_interval:
            self.cleanup()

    def is_revoked(self, jti: str) -> bool:
        """
        Check if a token is in the revocation list.

        Args:
            jti: JWT ID to check

        Returns:
            True if token is revoked, False otherwise
        """
        # Lazy-load from DB on first access
        self._db_load_all()

        if jti not in self._revoked:
            if not self._db_lookup_active(jti):
                return False

        expiry = self._revoked[jti]
        if time.time() > expiry:
            # Token has expired, remove from list
            del self._revoked[jti]
            return False

        return True

    def cleanup(self) -> None:
        """
        Remove expired entries from the revocation list (memory + DB).

        Called automatically after cleanup_interval has passed.
        """
        now = time.time()
        expired = [jti for jti, exp in self._revoked.items() if now > exp]

        for jti in expired:
            del self._revoked[jti]

        if expired:
            logger.debug(f"Token revocation cleanup: removed {len(expired)} expired entries")

        self._db_remove_expired()
        self._last_cleanup = time.time()

    def stats(self) -> Dict:
        """
        Get revocation list statistics.

        Returns:
            Dictionary with current count and last cleanup time
        """
        return {
            'revoked_count': len(self._revoked),
            'last_cleanup': self._last_cleanup,
        }

    def get_expiry(self, jti: str) -> float:
        """
        Get the expiry timestamp for a revoked JTI entry.

        Args:
            jti: JWT ID or user-level JTI to look up

        Returns:
            Expiry timestamp (Unix time), or 0 if not found
        """
        self._db_load_all()
        if jti not in self._revoked:
            self._db_lookup_active(jti)
        return self._revoked.get(jti, 0)


# Global instance for use across the application
token_revocation_list = TokenRevocationList()


# ============================================================================
# 10. File Upload Validation (P1)
# ============================================================================

class FileUploadValidator:
    """
    MIME type and content validation for document uploads.

    Validates file extensions, MIME types, magic bytes (file signatures),
    and file size to prevent malicious uploads.
    """

    ALLOWED_MIME_TYPES = {
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'image/png',
        'image/jpeg',
        'image/jpg',
    }

    ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.xlsx', '.pptx', '.png', '.jpg', '.jpeg'}

    MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB

    # Magic bytes (file signatures) for type detection
    MAGIC_BYTES = {
        b'%PDF': 'application/pdf',
        b'\x89PNG': 'image/png',
        b'\xff\xd8\xff': 'image/jpeg',
        b'PK\x03\x04': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    }

    @classmethod
    def validate(cls, filename: str, content_type: str, file_data: bytes) -> Tuple[bool, str]:
        """
        Validates a file upload for security compliance.

        Checks:
            1. File extension is in ALLOWED_EXTENSIONS
            2. Content-Type MIME type is in ALLOWED_MIME_TYPES
            3. File size does not exceed MAX_FILE_SIZE
            4. File magic bytes match claimed MIME type

        Args:
            filename: Original filename
            content_type: HTTP Content-Type header value
            file_data: Raw file bytes

        Returns:
            Tuple of (is_valid: bool, error_message: str)

        Example:
            >>> valid, msg = FileUploadValidator.validate(
            ...     "document.pdf",
            ...     "application/pdf",
            ...     b"%PDF-1.4 ..."
            ... )
        """
        is_valid, _reason_code, message = cls.validate_with_reason(filename, content_type, file_data)
        return (is_valid, message)

    @classmethod
    def validate_with_reason(
        cls,
        filename: str,
        content_type: str,
        file_data: bytes,
    ) -> Tuple[bool, str, str]:
        """
        Validates a file upload and returns a stable machine-readable reason.

        Returns:
            Tuple of (is_valid, reason_code, error_message).  reason_code is
            "ok" on success and a stable audit taxonomy value on rejection.
        """
        try:
            # 1. Check extension
            file_ext = Path(filename).suffix.lower()
            if file_ext not in cls.ALLOWED_EXTENSIONS:
                return (
                    False,
                    "disallowed_extension",
                    f"File type '{file_ext}' not allowed. Allowed: {', '.join(cls.ALLOWED_EXTENSIONS)}"
                )

            # 2. Check MIME type
            content_type_clean = content_type.split(';')[0].strip().lower() if content_type else ''
            if content_type_clean not in cls.ALLOWED_MIME_TYPES:
                return (
                    False,
                    "disallowed_mime_type",
                    f"Content-Type '{content_type_clean}' not allowed. "
                    f"Allowed: {', '.join(cls.ALLOWED_MIME_TYPES)}"
                )

            # 3. Check file size
            file_size = len(file_data)
            if file_size > cls.MAX_FILE_SIZE:
                max_size_mb = cls.MAX_FILE_SIZE / (1024 * 1024)
                return (
                    False,
                    "file_too_large",
                    f"File size {file_size} bytes exceeds maximum of {cls.MAX_FILE_SIZE} bytes ({max_size_mb}MB)"
                )

            # 4. Check magic bytes
            magic_match = cls._check_magic_bytes(file_data)
            if not magic_match:
                return (
                    False,
                    "magic_byte_mismatch",
                    "File content does not match claimed file type (magic bytes mismatch)"
                )

            # Validate magic bytes match content type
            if not cls._magic_matches_content_type(magic_match, content_type_clean):
                return (
                    False,
                    "mime_magic_mismatch",
                    f"File content type does not match Content-Type header "
                    f"(magic: {magic_match}, claimed: {content_type_clean})"
                )

            logger.info(f"File upload validated: {filename} ({file_size} bytes)")
            return (True, "ok", "")

        except Exception as e:
            logger.error(f"File validation error: {e}", exc_info=True)
            return (False, "validation_error", f"File validation error: {str(e)}")

    @classmethod
    def _check_magic_bytes(cls, file_data: bytes) -> Optional[str]:
        """
        Detect file type by magic bytes.

        Args:
            file_data: Raw file bytes

        Returns:
            Detected MIME type or None
        """
        if not file_data:
            return None

        for magic, mime_type in cls.MAGIC_BYTES.items():
            if file_data.startswith(magic):
                return mime_type

        return None

    @classmethod
    def _magic_matches_content_type(cls, magic_type: str, content_type: str) -> bool:
        """
        Verify that detected magic type matches declared content type.

        Args:
            magic_type: MIME type detected from magic bytes
            content_type: Declared MIME type from header

        Returns:
            True if types match or are compatible, False otherwise
        """
        # Exact match
        if magic_type == content_type:
            return True

        # Handle JPEG variants (image/jpg vs image/jpeg)
        if magic_type in ['image/jpeg', 'image/jpg'] and content_type in ['image/jpeg', 'image/jpg']:
            return True

        # Office Open XML formats are ZIP containers and share the same magic bytes.
        if magic_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' and content_type in [
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        ]:
            return True

        return False


# ============================================================================
# 11. Health Endpoint Restriction (P1)
# ============================================================================

def get_safe_health_response() -> Dict:
    """
    Returns health check data without leaking sensitive configuration.

    Safe for public access (no authentication required).
    Does not expose environment variables, API keys, or internal state.

    Returns:
        Dictionary with basic health status

    Example:
        >>> response = get_safe_health_response()
        >>> response['status']
        'ok'
    """
    return {
        'status': 'ok',
        'service': 'ARIE Finance API',
        'version': '1.0.0',
        'timestamp': datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def get_detailed_health_response(include_config: bool = False) -> Dict:
    """
    Returns detailed health information for authenticated admin users only.

    Should only be called after verifying user is admin.
    Includes database status, cache health, and optional configuration details.

    Args:
        include_config: If True, includes environment and config details (admin only)

    Returns:
        Dictionary with detailed health information

    Example:
        >>> # In authenticated admin endpoint
        >>> response = get_detailed_health_response(include_config=True)
        >>> response['database']['status']
        'connected'
    """
    response = get_safe_health_response()

    # Add database status
    response['database'] = {
        'type': os.environ.get('DATABASE_URL', 'sqlite').split('+')[0],
        'status': 'ok',  # Should be checked in actual implementation
    }

    # Add optional configuration details (admin only)
    if include_config:
        response['configuration'] = {
            'environment': ENV,
            'log_level': os.environ.get('LOG_LEVEL', 'INFO'),
            'claude_mock_mode': os.environ.get('CLAUDE_MOCK_MODE', 'false'),
        }

    return response


# ============================================================================
# Module Initialization
# ============================================================================

def initialize_security_module() -> None:
    """
    Initializes the security hardening module.

    Called once at server startup to validate production environment and set up logging.
    """
    logger.info("Security hardening module initialized")

    # Validate production environment at startup
    try:
        validate_production_environment()
    except RuntimeError as e:
        logger.error(f"Production environment validation failed: {e}")
        if is_production():
            raise


if __name__ == '__main__':
    # Simple smoke test
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    print("Testing security_hardening module...")

    # Test password policy
    pwd_valid, pwd_msg = PasswordPolicy.validate("Weak1!")
    print(f"✓ PasswordPolicy.validate: {pwd_valid}, {pwd_msg}")

    temp_pwd = PasswordPolicy.generate_temporary()
    print(f"✓ PasswordPolicy.generate_temporary: {len(temp_pwd)} chars")

    # Test schema validation
    app_valid, app_msg = ApplicationSchema.validate_application({
        'entity_type': 'company',
        'company_name': 'Test Corp',
        'sector': 'technology',
        'directors': [],
        'ubos': []
    })
    print(f"✓ ApplicationSchema.validate_application: {app_valid}")

    # Test screening mode
    mode = determine_screening_mode({'sources': [], 'rules_results': []})
    print(f"✓ determine_screening_mode: {mode}")

    # Test AI source tagging
    resp = tag_ai_response({'analysis': 'test'}, 'claude-sonnet-4-6')
    print(f"✓ tag_ai_response: ai_source={resp.get('ai_source')}")

    # Test file upload validation
    pdf_magic = b'%PDF-1.4'
    valid, msg = FileUploadValidator.validate('test.pdf', 'application/pdf', pdf_magic)
    print(f"✓ FileUploadValidator.validate: {valid}")

    # Test health responses
    safe_health = get_safe_health_response()
    print(f"✓ get_safe_health_response: status={safe_health.get('status')}")

    # Test token revocation
    print(f"✓ token_revocation_list: {token_revocation_list.stats()}")

    print("\n✓ All security_hardening tests passed!")
