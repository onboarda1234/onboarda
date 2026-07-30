"""Canonical Monitoring Alert lifecycle and controlled transition service.

``monitoring_alert_state_machine_v1`` is the only runtime path permitted to
change ``monitoring_alerts.status``.  The module deliberately has no top-level
import of :mod:`db`, so ``db.py`` can import the vocabulary for schema
hardening without creating a circular import.  The canonical audit writer is
loaded lazily at transition time.

The service is intentionally narrower than a workflow engine:

* it changes the Monitoring signal state only;
* it validates, but never creates, downstream evidence objects;
* it does not send notifications, run agents, or start workflows;
* every unlisted edge fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
import uuid
from typing import Any, Callable, Dict, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple


logger = logging.getLogger("arie.monitoring_alert_state_machine")

STATE_MACHINE_VERSION = "monitoring_alert_state_machine_v1"
ORIGINATING_PR = "PR-MON-M1-STATE-MACHINE-1"
CANONICAL_AUDIT_ACTION = "monitoring.alert.status_transition"

CANONICAL_STATUSES: Tuple[str, ...] = (
    "open",
    "triaged",
    "assigned",
    "in_review",
    "escalated",
    "routed_to_review",
    "routed_to_edd",
    "dismissed",
    "resolved",
    "waived",
)
CANONICAL_STATUS_SET: FrozenSet[str] = frozenset(CANONICAL_STATUSES)
ACTIVE_STATUSES: FrozenSet[str] = frozenset(
    {"open", "triaged", "assigned", "in_review", "escalated"}
)
HANDOFF_STATUSES: FrozenSet[str] = frozenset(
    {"routed_to_review", "routed_to_edd"}
)
TERMINAL_STATUSES: FrozenSet[str] = frozenset(
    {"dismissed", "resolved", "waived"}
)

OFFICER_ROLES: FrozenSet[str] = frozenset({"co", "sco", "admin"})
SENIOR_ROLES: FrozenSet[str] = frozenset({"sco", "admin"})
ACTOR_TYPES: FrozenSet[str] = frozenset({"officer", "system", "downstream"})

DOCUMENT_ALERT_TYPES: FrozenSet[str] = frozenset(
    {
        "document_expired",
        "document_expiring",
        "document_expiring_soon",
        "document_expiry",
        "document_expiry_missing",
        "document_stale",
        "document_health",
        "missing_document",
        "missing_document_refresh",
        "document_refresh",
        "document_refresh_due",
        "kyc_refresh_missing",
        "licensing_refresh",
        "source_of_funds_refresh",
        "source_of_wealth_refresh",
    }
)
SCREENING_ALERT_TOKENS: Tuple[str, ...] = (
    "screen",
    "sanction",
    "watchlist",
    "pep",
    "adverse",
    "media",
    "terror",
    "prolifer",
)
REVIEW_CONTROL_EVIDENCE_TYPES: FrozenSet[str] = frozenset(
    {
        "case_identifier",
        "screening_case_id",
        "document_id",
        "document_request_id",
    }
)

EVIDENCE_TYPES: FrozenSet[str] = frozenset(
    {
        "source_reference",
        "case_identifier",
        "officer_id",
        "officer_rationale",
        "dismissal_reason",
        "waiver_reason",
        "document_request_id",
        "document_id",
        "verification_execution_id",
        "screening_case_id",
        "edd_case_id",
        "change_request_id",
        "periodic_review_id",
        "review_request_id",
        "escalation_id",
        "downstream_outcome",
    }
)
TEXT_EVIDENCE_TYPES: FrozenSet[str] = frozenset(
    {
        "source_reference",
        "case_identifier",
        "officer_rationale",
        "dismissal_reason",
        "waiver_reason",
        "downstream_outcome",
    }
)

ALLOWED_REASON_CODES: Mapping[str, FrozenSet[str]] = {
    "triaged": frozenset({"triage"}),
    "assigned": frozenset({"assign"}),
    "in_review": frozenset(
        {
            "review_started",
            "risk_profile_update_required",
            "further_information_requested",
            "senior_acknowledged",
        }
    ),
    "escalated": frozenset({"escalated_to_sco", "overdue_escalation"}),
    "routed_to_review": frozenset({"route_to_periodic_review"}),
    "routed_to_edd": frozenset({"route_to_edd"}),
    "dismissed": frozenset(
        {
            "dismiss_false_positive",
            "dismiss_duplicate",
            "dismiss_no_action_needed",
            "dismiss_resolved_externally",
            "dismiss_other",
        }
    ),
    "resolved": frozenset(
        {
            "no_material_impact",
            "document_accepted",
            "document_already_updated",
            "downstream_outcome",
        }
    ),
    "waived": frozenset({"document_waived"}),
}


def _fset(values: Iterable[str]) -> FrozenSet[str]:
    return frozenset(values)


@dataclass(frozen=True)
class TransitionRule:
    """One exact source/target authorization rule."""

    rule_id: str
    source: str
    target: str
    actor_types: FrozenSet[str]
    roles: FrozenSet[str]
    source_workflows: FrozenSet[str]
    reason_codes: FrozenSet[str]
    required_evidence: Tuple[str, ...] = ()
    any_evidence: Tuple[Tuple[str, ...], ...] = ()
    allowed_owners: FrozenSet[str] = frozenset({"any"})
    future_only: bool = False
    four_eyes: str = "risk_tiered"


def _build_transition_rules() -> Tuple[TransitionRule, ...]:
    rules = []

    rules.append(
        TransitionRule(
            "open_to_triaged",
            "open",
            "triaged",
            _fset({"officer"}),
            OFFICER_ROLES,
            _fset({"monitoring"}),
            _fset({"triage"}),
            any_evidence=(("source_reference", "case_identifier"),),
            four_eyes="no",
        )
    )
    for source in ("open", "triaged"):
        rules.append(
            TransitionRule(
                f"{source}_to_assigned",
                source,
                "assigned",
                _fset({"officer"}),
                OFFICER_ROLES,
                _fset({"monitoring"}),
                _fset({"assign"}),
                required_evidence=("officer_id",),
                four_eyes="no",
            )
        )
    for source in ("open", "triaged", "assigned"):
        rules.append(
            TransitionRule(
                f"{source}_to_in_review",
                source,
                "in_review",
                _fset({"officer"}),
                OFFICER_ROLES,
                _fset({"monitoring"}),
                _fset(
                    {
                        "review_started",
                        "risk_profile_update_required",
                        "further_information_requested",
                    }
                ),
                any_evidence=(("source_reference", "case_identifier"),),
                four_eyes="no",
            )
        )
    rules.append(
        TransitionRule(
            "escalated_to_in_review",
            "escalated",
            "in_review",
            _fset({"officer"}),
            SENIOR_ROLES,
            _fset({"monitoring"}),
            _fset({"senior_acknowledged"}),
            required_evidence=("officer_rationale",),
            four_eyes="no",
        )
    )
    for source in ("open", "triaged", "assigned", "in_review"):
        rules.append(
            TransitionRule(
                f"{source}_to_escalated",
                source,
                "escalated",
                _fset({"officer"}),
                OFFICER_ROLES,
                _fset({"monitoring"}),
                _fset({"escalated_to_sco"}),
                required_evidence=("officer_rationale",),
                four_eyes="no",
            )
        )
        rules.append(
            TransitionRule(
                f"{source}_to_overdue_escalated",
                source,
                "escalated",
                _fset({"officer"}),
                OFFICER_ROLES,
                _fset({"monitoring"}),
                _fset({"overdue_escalation"}),
                required_evidence=("officer_rationale", "escalation_id"),
                four_eyes="no",
            )
        )
    for target, evidence_key, reason_prefix in (
        ("routed_to_review", "periodic_review_id", "periodic_review"),
        ("routed_to_edd", "edd_case_id", "edd"),
    ):
        for source in ("open", "triaged", "assigned", "in_review", "escalated"):
            rules.append(
                TransitionRule(
                    f"{source}_to_{reason_prefix}",
                    source,
                    target,
                    _fset({"officer"}),
                    OFFICER_ROLES,
                    _fset({"monitoring"}),
                    _fset(
                        {
                            "route_to_periodic_review"
                            if target == "routed_to_review"
                            else "route_to_edd"
                        }
                    ),
                    required_evidence=(evidence_key,),
                    four_eyes="no",
                )
            )
    for source in ("open", "triaged", "assigned", "in_review", "escalated"):
        rules.append(
            TransitionRule(
                f"{source}_to_dismissed",
                source,
                "dismissed",
                _fset({"officer"}),
                OFFICER_ROLES,
                _fset({"monitoring"}),
                _fset(
                    {
                        "dismiss_false_positive",
                        "dismiss_duplicate",
                        "dismiss_no_action_needed",
                        "dismiss_resolved_externally",
                        "dismiss_other",
                    }
                ),
                required_evidence=("dismissal_reason", "officer_rationale"),
                allowed_owners=_fset(
                    {"manual", "documents", "screening_review"}
                ),
                four_eyes="risk_tiered",
            )
        )
    for source in ("in_review", "escalated"):
        rules.append(
            TransitionRule(
                f"{source}_to_resolved_manual",
                source,
                "resolved",
                _fset({"officer"}),
                OFFICER_ROLES,
                _fset({"monitoring"}),
                _fset({"no_material_impact"}),
                required_evidence=("officer_rationale",),
                allowed_owners=_fset({"manual"}),
                four_eyes="risk_tiered",
            )
        )
    for source in ("open", "triaged", "assigned", "in_review", "escalated"):
        rules.append(
            TransitionRule(
                f"{source}_to_resolved_documents",
                source,
                "resolved",
                _fset({"officer"}),
                OFFICER_ROLES,
                _fset({"kyc_documents"}),
                _fset({"document_accepted", "document_already_updated"}),
                required_evidence=("document_request_id", "document_id"),
                allowed_owners=_fset({"documents"}),
                four_eyes="document_control",
            )
        )
        rules.append(
            TransitionRule(
                f"{source}_to_waived_documents",
                source,
                "waived",
                _fset({"officer"}),
                SENIOR_ROLES,
                _fset({"kyc_documents"}),
                _fset({"document_waived"}),
                required_evidence=("document_request_id", "waiver_reason"),
                allowed_owners=_fset({"documents"}),
                four_eyes="senior_or_downstream",
            )
        )

    # Defined for the next synchronization PRs, but deliberately disabled here.
    for source, owner, workflow, evidence_key in (
        ("routed_to_edd", "edd", "edd", "edd_case_id"),
        ("routed_to_review", "periodic_review", "periodic_review", "periodic_review_id"),
    ):
        rules.append(
            TransitionRule(
                f"{source}_to_resolved_future",
                source,
                "resolved",
                _fset({"downstream"}),
                _fset({"system"}),
                _fset({workflow}),
                _fset({"downstream_outcome"}),
                required_evidence=(evidence_key, "downstream_outcome"),
                allowed_owners=_fset({owner}),
                future_only=True,
                four_eyes="downstream_control",
            )
        )
    rules.append(
        TransitionRule(
            "screening_in_review_to_resolved_future",
            "in_review",
            "resolved",
            _fset({"downstream"}),
            _fset({"system"}),
            _fset({"screening_review"}),
            _fset({"downstream_outcome"}),
            required_evidence=("screening_case_id", "downstream_outcome"),
            allowed_owners=_fset({"screening_review"}),
            future_only=True,
            four_eyes="downstream_control",
        )
    )

    return tuple(rules)


TRANSITION_RULES: Tuple[TransitionRule, ...] = _build_transition_rules()
ENABLED_TRANSITION_RULES: Tuple[TransitionRule, ...] = tuple(
    rule for rule in TRANSITION_RULES if not rule.future_only
)
FUTURE_TRANSITION_RULES: Tuple[TransitionRule, ...] = tuple(
    rule for rule in TRANSITION_RULES if rule.future_only
)
TRANSITION_COUNT = len(ENABLED_TRANSITION_RULES)


class MonitoringTransitionError(ValueError):
    """Safe, user-facing base class for controlled transition failures."""

    status_code = 400
    error_code = "monitoring_transition_error"

    def __init__(self, message: str):
        super().__init__(message)


class AlertNotFound(MonitoringTransitionError):
    status_code = 404
    error_code = "alert_not_found"


class InvalidStatus(MonitoringTransitionError):
    error_code = "invalid_status"


class InvalidTransition(MonitoringTransitionError):
    status_code = 409
    error_code = "invalid_transition"


class FutureTransitionDisabled(InvalidTransition):
    error_code = "future_transition_disabled"


class StaleCurrentState(InvalidTransition):
    error_code = "stale_current_state"


class AlreadyInTargetState(InvalidTransition):
    error_code = "already_in_target_state"


class TerminalStateError(InvalidTransition):
    error_code = "terminal_state"


class InsufficientRole(MonitoringTransitionError):
    status_code = 403
    error_code = "insufficient_role"


class MissingEvidence(MonitoringTransitionError):
    error_code = "missing_evidence"


class WrongEvidenceType(MonitoringTransitionError):
    error_code = "wrong_evidence_type"


class WrongAlertType(InvalidTransition):
    error_code = "wrong_alert_type"


class AmbiguousAlertOwner(InvalidTransition):
    error_code = "ambiguous_alert_owner"


class LinkedObjectMissing(MonitoringTransitionError):
    status_code = 404
    error_code = "linked_object_missing"


class EvidenceLinkMismatch(InvalidTransition):
    error_code = "evidence_link_mismatch"


class FourEyesRequired(InsufficientRole):
    error_code = "four_eyes_required"


class AuditInfrastructureError(RuntimeError):
    """Internal failure. API handlers must return a sanitized 500."""

    status_code = 500
    error_code = "audit_infrastructure_failure"


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        value = row.get(key, default)
        return default if value is None else value
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _token(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9_]+",
        "",
        str(value or "").strip().lower().replace("-", "_").replace(" ", "_"),
    )


def _safe_actor(actor: Optional[Mapping[str, Any]], actor_type: Optional[str]) -> Dict[str, str]:
    raw = dict(actor or {})
    normalized_type = _token(actor_type or raw.get("actor_type") or "officer")
    if normalized_type not in ACTOR_TYPES:
        raise InsufficientRole("The transition actor type is not authorized.")
    return {
        "type": normalized_type,
        "id": str(raw.get("sub") or raw.get("id") or "").strip(),
        "name": str(raw.get("name") or raw.get("full_name") or "").strip(),
        "role": _token(raw.get("role")),
    }


def _normalize_status(value: Any, *, field: str) -> str:
    status = str(value or "").strip()
    if status not in CANONICAL_STATUS_SET:
        raise InvalidStatus(f"{field} is not a canonical Monitoring Alert status.")
    return status


def _normalize_evidence(evidence: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if evidence is None:
        return {}
    if not isinstance(evidence, Mapping):
        raise WrongEvidenceType("Transition evidence must be a keyed object.")
    normalized: Dict[str, Any] = {}
    for raw_key, value in evidence.items():
        key = str(raw_key or "").strip()
        if key not in EVIDENCE_TYPES:
            raise WrongEvidenceType(f"Unsupported transition evidence type: {key or '<blank>'}.")
        if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
            raise WrongEvidenceType(f"Evidence {key} must be a scalar identifier or text value.")
        if value is None or not str(value).strip():
            raise MissingEvidence(f"Evidence {key} must not be blank.")
        normalized[key] = str(value).strip()[:1000] if key in TEXT_EVIDENCE_TYPES else value
    return normalized


def _validate_actor_identity(db: Any, actor: Mapping[str, str]) -> None:
    if not actor["id"]:
        raise InsufficientRole("The transition actor identity is required.")
    if actor["type"] != "officer":
        return
    row = _query_one(
        db,
        "SELECT id, role, status FROM users WHERE id = ?",
        (actor["id"],),
    )
    if not row:
        raise InsufficientRole("The transition officer does not exist.")
    if _token(row.get("status") or "active") != "active":
        raise InsufficientRole("The transition officer is not active.")
    if _token(row.get("role")) != actor["role"]:
        raise InsufficientRole(
            "The authenticated officer role does not match the authoritative user record."
        )


def _validate_assignment_authority(
    db: Any,
    actor: Mapping[str, str],
    officer_id: Any,
) -> Dict[str, Any]:
    """Validate both the assignee and the actor-to-assignee relationship.

    Compliance Officers may self-assign only. Senior Compliance Officers and
    Administrators may assign any active officer in the governed role set.
    Keeping this rule in the canonical lifecycle layer prevents a non-HTTP
    caller from bypassing the endpoint-only RBAC check.
    """

    assignee_id = str(officer_id or "").strip()
    row = _query_one(
        db,
        "SELECT id, role, status FROM users WHERE id = ?",
        (assignee_id,),
    )
    if not row:
        raise LinkedObjectMissing("The assigned officer does not exist.")
    if _token(row.get("status") or "active") != "active":
        raise EvidenceLinkMismatch("The assigned officer is not active.")
    if _token(row.get("role")) not in OFFICER_ROLES:
        raise InsufficientRole(
            "Monitoring Alerts may only be assigned to an active CO, SCO, or Administrator."
        )
    if (
        actor.get("type") == "officer"
        and actor.get("role") == "co"
        and str(actor.get("id") or "") != assignee_id
    ):
        raise InsufficientRole(
            "A Compliance Officer may only self-assign a Monitoring Alert."
        )
    return row


def validate_assignment_authority(
    db: Any,
    actor: Mapping[str, Any],
    officer_id: Any,
    *,
    actor_type: str = "officer",
) -> Dict[str, Any]:
    """Public guard for assignment-only updates that do not change status."""

    actor_info = _safe_actor(actor, actor_type)
    _validate_actor_identity(db, actor_info)
    return _validate_assignment_authority(db, actor_info, officer_id)


def _alert_owner(alert: Mapping[str, Any]) -> str:
    if _row_get(alert, "linked_edd_case_id") not in (None, ""):
        return "edd"
    if _row_get(alert, "linked_periodic_review_id") not in (None, ""):
        return "periodic_review"

    alert_type = _token(_row_get(alert, "alert_type"))
    source_reference = _token(_row_get(alert, "source_reference"))
    detected_by = _token(_row_get(alert, "detected_by"))
    if (
        alert_type in DOCUMENT_ALERT_TYPES
        or alert_type.startswith("document_")
        or source_reference.startswith("document_")
        or "document_health" in detected_by
    ):
        return "documents"
    if (
        _row_get(alert, "provider") not in (None, "")
        or _row_get(alert, "case_identifier") not in (None, "")
        or any(part in alert_type for part in SCREENING_ALERT_TOKENS)
    ):
        return "screening_review"
    return "manual"


def _query_all(
    db: Any,
    sql: str,
    params: Sequence[Any],
) -> Tuple[Dict[str, Any], ...]:
    return tuple(dict(row) for row in db.execute(sql, tuple(params)).fetchall())


def _authoritative_alert_owner(
    db: Any,
    alert: Mapping[str, Any],
    *,
    transition_target: Optional[str] = None,
    evidence: Optional[Mapping[str, Any]] = None,
) -> str:
    """Resolve ownership from the same explicit links audited by PR #900.

    Reverse workflow links are authoritative even when the alert-side pointer
    is absent.  Multiple explicit workflow owners or impossible cross-
    application links fail closed; falling back to alert semantics is permitted
    only when no explicit workflow link exists.
    """

    alert_id = _row_get(alert, "id")
    application_id = str(_row_get(alert, "application_id") or "")
    explicit_owners = set()

    def _validate_rows(
        owner: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        object_label: str,
        id_key: str = "id",
        reverse_alert_key: Optional[str] = None,
        allow_many: bool = False,
    ) -> None:
        if not rows:
            return
        explicit_owners.add(owner)
        linked_ids = {
            str(_row_get(linked, id_key))
            for linked in rows
            if _row_get(linked, id_key) not in (None, "")
        }
        if len(linked_ids) > 1 and not allow_many:
            raise AmbiguousAlertOwner(
                f"The Monitoring Alert has multiple linked {object_label} "
                "records and requires linkage reconciliation before transition."
            )
        for linked in rows:
            linked_application = str(_row_get(linked, "application_id") or "")
            if not linked_application or linked_application != application_id:
                raise EvidenceLinkMismatch(
                    f"The linked {object_label} has missing or conflicting "
                    "application linkage."
                )
            if reverse_alert_key:
                reverse_alert_id = _row_get(linked, reverse_alert_key)
                if (
                    reverse_alert_id not in (None, "")
                    and str(reverse_alert_id) != str(alert_id)
                ):
                    raise EvidenceLinkMismatch(
                        f"The linked {object_label} points to another "
                        "Monitoring Alert."
                    )

    direct_edd_id = _row_get(alert, "linked_edd_case_id")
    direct_edd_rows: Tuple[Dict[str, Any], ...] = ()
    if direct_edd_id not in (None, ""):
        direct_edd_rows = _query_all(
            db,
            "SELECT id, application_id, linked_monitoring_alert_id "
            "FROM edd_cases WHERE id = ?",
            (direct_edd_id,),
        )
        if not direct_edd_rows:
            raise LinkedObjectMissing("The alert's linked EDD case does not exist.")
    reverse_edd_rows = _query_all(
        db,
        "SELECT id, application_id, linked_monitoring_alert_id FROM edd_cases "
        "WHERE linked_monitoring_alert_id = ?",
        (alert_id,),
    )
    _validate_rows(
        "edd",
        direct_edd_rows + reverse_edd_rows,
        object_label="EDD case",
        reverse_alert_key="linked_monitoring_alert_id",
    )

    direct_review_id = _row_get(alert, "linked_periodic_review_id")
    direct_review_rows: Tuple[Dict[str, Any], ...] = ()
    if direct_review_id not in (None, ""):
        direct_review_rows = _query_all(
            db,
            "SELECT id, application_id, linked_monitoring_alert_id "
            "FROM periodic_reviews WHERE id = ?",
            (direct_review_id,),
        )
        if not direct_review_rows:
            raise LinkedObjectMissing(
                "The alert's linked periodic review does not exist."
            )
    reverse_review_rows = _query_all(
        db,
        "SELECT id, application_id, linked_monitoring_alert_id "
        "FROM periodic_reviews "
        "WHERE linked_monitoring_alert_id = ?",
        (alert_id,),
    )
    _validate_rows(
        "periodic_review",
        direct_review_rows + reverse_review_rows,
        object_label="periodic review",
        reverse_alert_key="linked_monitoring_alert_id",
    )

    change_rows = _query_all(
        db,
        """
        SELECT cr.id, cr.application_id
          FROM change_alerts ca
          JOIN change_requests cr ON cr.source_alert_id = ca.id
         WHERE ca.source_reference = ?
        """,
        (f"monitoring_alert:{alert_id}",),
    )
    _validate_rows(
        "change_management",
        change_rows,
        object_label="change request",
    )

    # Document refresh reuses one active requirement for an alert. Unlike the
    # display helper, ownership must not pick the newest row and conceal an
    # older duplicate or cross-application link: multiple alert-level request
    # links are corrupt/ambiguous and require reconciliation before transition.
    document_rows = _query_all(
        db,
        "SELECT id, application_id FROM application_enhanced_requirements "
        "WHERE monitoring_alert_id = ?",
        (alert_id,),
    )
    _validate_rows(
        "documents",
        document_rows,
        object_label="document requirement",
    )

    screening_evidence_rows = _query_all(
        db,
        "SELECT id, application_id, provider, case_identifier "
        "FROM monitoring_alert_evidence WHERE monitoring_alert_id = ?",
        (alert_id,),
    )
    _validate_rows(
        "screening_review",
        screening_evidence_rows,
        object_label="screening evidence",
        allow_many=True,
    )
    alert_provider = _token(_row_get(alert, "provider"))
    alert_case_identifier = str(
        _row_get(alert, "case_identifier") or ""
    ).strip()
    evidence_providers = {
        _token(_row_get(row, "provider"))
        for row in screening_evidence_rows
        if _row_get(row, "provider") not in (None, "")
    }
    evidence_cases = {
        str(_row_get(row, "case_identifier") or "").strip()
        for row in screening_evidence_rows
        if _row_get(row, "case_identifier") not in (None, "")
    }
    if len(evidence_providers) > 1 or len(evidence_cases) > 1:
        raise EvidenceLinkMismatch(
            "The linked screening evidence has conflicting provider or case "
            "identifiers."
        )
    if (
        alert_provider
        and evidence_providers
        and alert_provider not in evidence_providers
    ):
        raise EvidenceLinkMismatch(
            "The linked screening evidence provider conflicts with the alert."
        )
    if (
        alert_case_identifier
        and evidence_cases
        and alert_case_identifier not in evidence_cases
    ):
        raise EvidenceLinkMismatch(
            "The linked screening evidence case conflicts with the alert."
        )

    if len(explicit_owners) > 1:
        # Routing creates and links the target workflow in the same transaction
        # before the canonical status transition.  A real Document, Screening,
        # or Change Management signal therefore has exactly two explicit
        # owners for that brief handoff window: its authoritative source owner
        # and the newly linked target owner.  Permit only that exact,
        # evidence-bound transfer shape; every other multi-owner condition
        # remains a reconciliation error.
        target_owner_by_status = {
            "routed_to_edd": ("edd", "edd_case_id", "linked_edd_case_id"),
            "routed_to_review": (
                "periodic_review",
                "periodic_review_id",
                "linked_periodic_review_id",
            ),
        }
        effective_target = str(
            transition_target or _row_get(alert, "status") or ""
        ).strip()
        transfer = target_owner_by_status.get(effective_target)
        supplied_evidence = dict(evidence or {})
        if transfer:
            target_owner, evidence_key, alert_link_key = transfer
            source_owners = explicit_owners - {target_owner}
            linked_target_id = _row_get(alert, alert_link_key)
            supplied_target_id = supplied_evidence.get(
                evidence_key,
                linked_target_id if effective_target in HANDOFF_STATUSES else None,
            )
            if (
                target_owner in explicit_owners
                and len(source_owners) == 1
                and source_owners
                <= {
                    "documents",
                    "screening_review",
                    "change_management",
                }
                and supplied_target_id not in (None, "")
                and linked_target_id not in (None, "")
                and str(supplied_target_id) == str(linked_target_id)
            ):
                if effective_target in HANDOFF_STATUSES:
                    return target_owner
                return next(iter(source_owners))
        raise AmbiguousAlertOwner(
            "The Monitoring Alert has multiple explicit workflow owners and "
            "requires linkage reconciliation before transition."
        )
    if explicit_owners:
        return next(iter(explicit_owners))
    return _alert_owner(alert)


def _requires_review_control(
    alert: Mapping[str, Any],
    owner: str,
    target: str,
    evidence: Mapping[str, Any],
) -> bool:
    """Apply the existing protected M2.2 risk-tier contract without drift."""

    if target == "dismissed":
        from monitoring_dismissal_control import requires_control

        return requires_control(
            alert,
            action="dismiss",
            dismissal_reason=evidence.get("dismissal_reason"),
        )
    if target == "resolved" and owner == "manual":
        from monitoring_dismissal_control import requires_control

        return requires_control(
            alert,
            action="save_decision",
            outcome="no_material_impact",
        )
    # Document acceptance is governed by its exact accepted requirement and
    # linked document; waiver has its separate senior/downstream rule below.
    return False


def _matching_rules(
    source: str,
    target: str,
    actor: Mapping[str, str],
    source_workflow: str,
    owner: str,
) -> Tuple[TransitionRule, ...]:
    edge_rules = tuple(
        rule for rule in TRANSITION_RULES
        if rule.source == source and rule.target == target
    )
    if not edge_rules:
        return ()
    enabled = tuple(rule for rule in edge_rules if not rule.future_only)
    if not enabled:
        raise FutureTransitionDisabled(
            "This transition is defined for a future governed consumer and is not active."
        )
    return tuple(
        rule
        for rule in enabled
        if actor["type"] in rule.actor_types
        and actor["role"] in rule.roles
        and source_workflow in rule.source_workflows
        and ("any" in rule.allowed_owners or owner in rule.allowed_owners)
    )


def _missing_required_evidence(
    rules: Sequence[TransitionRule],
    evidence: Mapping[str, Any],
) -> Tuple[str, ...]:
    missing_sets = []
    for rule in rules:
        missing = [key for key in rule.required_evidence if key not in evidence]
        for options in rule.any_evidence:
            if not any(key in evidence for key in options):
                missing.append(" or ".join(options))
        if not missing:
            return ()
        missing_sets.append(tuple(missing))
    return min(missing_sets, key=len) if missing_sets else ()


def _select_rule(
    rules: Sequence[TransitionRule],
    evidence: Mapping[str, Any],
    reason_code: str,
) -> TransitionRule:
    reason_rules = tuple(
        rule for rule in rules if reason_code in rule.reason_codes
    )
    if not reason_rules:
        raise InvalidTransition(
            "reason_code is not approved for the requested transition edge."
        )
    for rule in reason_rules:
        if any(key not in evidence for key in rule.required_evidence):
            continue
        if any(not any(key in evidence for key in options) for options in rule.any_evidence):
            continue
        return rule
    missing = _missing_required_evidence(reason_rules, evidence)
    raise MissingEvidence(
        "Missing required transition evidence: " + ", ".join(missing)
    )


def _query_one(db: Any, sql: str, params: Sequence[Any]) -> Optional[Dict[str, Any]]:
    row = db.execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def _require_same_application(
    key: str,
    row: Mapping[str, Any],
    alert: Mapping[str, Any],
) -> None:
    alert_app = str(_row_get(alert, "application_id") or "")
    evidence_app = str(_row_get(row, "application_id") or "")
    if not alert_app or not evidence_app or alert_app != evidence_app:
        raise EvidenceLinkMismatch(
            f"{key} does not belong to the Monitoring Alert application."
        )


def _validate_linked_evidence(
    db: Any,
    alert: Mapping[str, Any],
    target: str,
    evidence: Mapping[str, Any],
    reason_code: str,
    actor: Mapping[str, str],
) -> None:
    alert_id = _row_get(alert, "id")
    document_request: Optional[Dict[str, Any]] = None

    if "source_reference" in evidence:
        stored = str(_row_get(alert, "source_reference") or "").strip()
        if not stored or str(evidence["source_reference"]).strip() != stored:
            raise EvidenceLinkMismatch("source_reference does not match the alert.")
    if "case_identifier" in evidence:
        stored = str(_row_get(alert, "case_identifier") or "").strip()
        if not stored or str(evidence["case_identifier"]).strip() != stored:
            raise EvidenceLinkMismatch("case_identifier does not match the alert.")

    if "officer_id" in evidence:
        row = _query_one(
            db,
            "SELECT id, role, status FROM users WHERE id = ?",
            (evidence["officer_id"],),
        )
        if not row:
            raise LinkedObjectMissing("The assigned officer does not exist.")
        if _token(row.get("status") or "active") != "active":
            raise EvidenceLinkMismatch("The assigned officer is not active.")
        if _token(row.get("role")) not in OFFICER_ROLES:
            raise InsufficientRole(
                "Monitoring Alerts may only be assigned to an active CO, SCO, or Administrator."
            )

    if "document_request_id" in evidence:
        document_request = _query_one(
            db,
            "SELECT id, application_id, monitoring_alert_id, linked_document_id, "
            "status, waiver_reason FROM application_enhanced_requirements WHERE id = ?",
            (evidence["document_request_id"],),
        )
        if not document_request:
            raise LinkedObjectMissing("The document request does not exist.")
        _require_same_application("document_request_id", document_request, alert)
        linked_alert = document_request.get("monitoring_alert_id")
        if str(linked_alert or "") != str(alert_id):
            raise EvidenceLinkMismatch(
                "The document request is not linked to this Monitoring Alert."
            )
        if (
            target == "resolved"
            and _token(document_request.get("status")) != "accepted"
        ):
            raise EvidenceLinkMismatch("The document request has no accepted outcome.")
        if (
            target == "waived"
            and _token(document_request.get("status")) != "waived"
        ):
            raise EvidenceLinkMismatch("The document request has no waived outcome.")
        if (
            target == "waived"
            and str(document_request.get("waiver_reason") or "").strip()
            != str(evidence.get("waiver_reason") or "").strip()
        ):
            raise EvidenceLinkMismatch(
                "waiver_reason does not match the approved document-request waiver."
            )
        if (
            "document_id" in evidence
            and str(document_request.get("linked_document_id") or "")
            != str(evidence["document_id"])
        ):
            raise EvidenceLinkMismatch(
                "The document request is not linked to the supplied document."
            )

    if "document_id" in evidence:
        document = _query_one(
            db,
            "SELECT id, application_id FROM documents WHERE id = ?",
            (evidence["document_id"],),
        )
        if not document:
            raise LinkedObjectMissing("The document does not exist.")
        _require_same_application("document_id", document, alert)
        if document_request is None:
            expected_source = f"document:{evidence['document_id']}"
            if str(_row_get(alert, "source_reference") or "") != expected_source:
                raise EvidenceLinkMismatch(
                    "The document is not the alert's exact source document."
                )

    if "verification_execution_id" in evidence:
        execution = _query_one(
            db,
            "SELECT id, application_id FROM agent_executions WHERE id = ?",
            (evidence["verification_execution_id"],),
        )
        if not execution:
            raise LinkedObjectMissing("The verification execution does not exist.")
        _require_same_application("verification_execution_id", execution, alert)

    if "screening_case_id" in evidence:
        screening = _query_one(
            db,
            "SELECT id, application_id, disposition FROM screening_reviews WHERE id = ?",
            (evidence["screening_case_id"],),
        )
        if not screening:
            raise LinkedObjectMissing("The screening review does not exist.")
        _require_same_application("screening_case_id", screening, alert)
        # ``screening_reviews`` is an application/subject roll-up and currently
        # has no alert-level foreign key.  PR #900 proved that same-application
        # review rows are contextual, not causal linkage.  Keep the identifier
        # typed for the disabled future consumer, but refuse to treat it as
        # transition evidence until an exact linkage contract is introduced.
        raise EvidenceLinkMismatch(
            "The screening review has no exact linkage to this Monitoring Alert; "
            "use the alert's stored provider case_identifier for an enabled "
            "screening dismissal."
        )

    if "edd_case_id" in evidence:
        edd = _query_one(
            db,
            "SELECT id, application_id, stage, linked_monitoring_alert_id "
            "FROM edd_cases WHERE id = ?",
            (evidence["edd_case_id"],),
        )
        if not edd:
            raise LinkedObjectMissing("The EDD case does not exist.")
        _require_same_application("edd_case_id", edd, alert)
        linked = _row_get(alert, "linked_edd_case_id")
        reverse_link = edd.get("linked_monitoring_alert_id")
        if not (
            str(linked or "") == str(evidence["edd_case_id"])
            or str(reverse_link or "") == str(alert_id)
        ):
            raise EvidenceLinkMismatch(
                "The EDD case has no exact linkage to this Monitoring Alert."
            )
        if target == "resolved" and _token(edd.get("stage")) not in {
            "edd_approved",
            "edd_rejected",
        }:
            raise EvidenceLinkMismatch("The EDD case has no terminal outcome.")

    if "periodic_review_id" in evidence:
        review = _query_one(
            db,
            "SELECT id, application_id, status, closed_at, "
            "linked_monitoring_alert_id FROM periodic_reviews WHERE id = ?",
            (evidence["periodic_review_id"],),
        )
        if not review:
            raise LinkedObjectMissing("The periodic review does not exist.")
        _require_same_application("periodic_review_id", review, alert)
        linked = _row_get(alert, "linked_periodic_review_id")
        reverse_link = review.get("linked_monitoring_alert_id")
        if not (
            str(linked or "") == str(evidence["periodic_review_id"])
            or str(reverse_link or "") == str(alert_id)
        ):
            raise EvidenceLinkMismatch(
                "The periodic review has no exact linkage to this Monitoring Alert."
            )
        if target == "resolved" and (
            _token(review.get("status")) != "completed"
            and review.get("closed_at") in (None, "")
        ):
            raise EvidenceLinkMismatch("The periodic review has no terminal outcome.")

    if "change_request_id" in evidence:
        change_request = _query_one(
            db,
            """
            SELECT cr.id, cr.application_id, ca.source_reference
              FROM change_requests cr
              JOIN change_alerts ca ON ca.id = cr.source_alert_id
             WHERE cr.id = ?
            """,
            (evidence["change_request_id"],),
        )
        if not change_request:
            raise LinkedObjectMissing("The change request does not exist.")
        _require_same_application("change_request_id", change_request, alert)
        if change_request.get("source_reference") != f"monitoring_alert:{alert_id}":
            raise EvidenceLinkMismatch(
                "The change request has no exact linkage to this Monitoring Alert."
            )

    if "review_request_id" in evidence:
        review_request = _query_one(
            db,
            "SELECT id, alert_id, state, initiated_by, approved_by, "
            "source_alert_status, requested_outcome, dismissal_reason, "
            "rationale, evidence_ref, transition_evidence, tier, "
            "second_review_bypassed FROM "
            "monitoring_alert_review_requests WHERE id = ?",
            (evidence["review_request_id"],),
        )
        if not review_request:
            raise LinkedObjectMissing("The review-control record does not exist.")
        if str(review_request.get("alert_id")) != str(alert_id):
            raise EvidenceLinkMismatch("The review-control record belongs to another alert.")
        if _token(review_request.get("state")) not in {"approved", "senior_cleared"}:
            raise EvidenceLinkMismatch("The review-control record is not an approved disposition.")
        approved_by = str(review_request.get("approved_by") or "").strip()
        initiated_by = str(review_request.get("initiated_by") or "").strip()
        if not approved_by or not initiated_by:
            raise FourEyesRequired(
                "The review-control record has no authoritative maker/checker identity."
            )
        initiating_user = _query_one(
            db,
            "SELECT id, role, status FROM users WHERE id = ?",
            (initiated_by,),
        )
        if (
            not initiating_user
            or _token(initiating_user.get("status") or "active") != "active"
            or _token(initiating_user.get("role")) not in OFFICER_ROLES
        ):
            raise FourEyesRequired(
                "The review-control record has no authoritative active "
                "initiating officer."
            )
        approving_user = _query_one(
            db,
            "SELECT id, role, status FROM users WHERE id = ?",
            (approved_by,),
        )
        if (
            not approving_user
            or _token(approving_user.get("status") or "active") != "active"
            or _token(approving_user.get("role")) not in SENIOR_ROLES
        ):
            raise FourEyesRequired(
                "The review-control record has no authoritative active senior "
                "approval."
            )
        review_state = _token(review_request.get("state"))
        if review_state == "approved" and approved_by == initiated_by:
            raise FourEyesRequired(
                "The review-control record violates maker-checker separation."
            )
        if review_state == "senior_cleared" and (
            approved_by != initiated_by
            or not bool(review_request.get("second_review_bypassed"))
        ):
            raise FourEyesRequired(
                "The senior-clear record has an invalid direct-clear identity."
            )
        if actor.get("id") != approved_by:
            raise FourEyesRequired(
                "Only the recorded approving officer may execute this "
                "review-controlled transition."
            )
        from monitoring_dismissal_control import (
            DismissalControlError,
            assert_evidence_current,
        )
        try:
            assert_evidence_current(alert, review_request.get("evidence_ref"))
        except DismissalControlError as exc:
            raise EvidenceLinkMismatch(str(exc)) from exc
        source_alert_status = str(
            review_request.get("source_alert_status") or ""
        ).strip()
        if source_alert_status != str(_row_get(alert, "status") or "").strip():
            raise EvidenceLinkMismatch(
                "The review-control record is not bound to the alert's exact "
                "current source status."
            )

        requested_outcome = _token(review_request.get("requested_outcome"))
        expected_outcomes: FrozenSet[str]
        if target == "dismissed":
            expected_outcomes = frozenset(
                {
                    "dismiss",
                    *(
                        ("false_positive",)
                        if reason_code == "dismiss_false_positive"
                        else ()
                    ),
                }
            )
            if (
                _token(review_request.get("dismissal_reason"))
                != _token(evidence.get("dismissal_reason"))
            ):
                raise EvidenceLinkMismatch(
                    "The review-control dismissal reason does not match the "
                    "requested transition."
                )
        elif target == "resolved" and reason_code == "no_material_impact":
            expected_outcomes = frozenset({"no_material_impact"})
        elif target == "resolved" and reason_code == "document_already_updated":
            expected_outcomes = frozenset({"mark_already_updated"})
        elif target == "waived" and reason_code == "document_waived":
            expected_outcomes = frozenset({"waive_with_reason"})
        else:
            expected_outcomes = frozenset()
        if requested_outcome not in expected_outcomes:
            raise EvidenceLinkMismatch(
                "The review-control record was approved for a different "
                "terminal outcome."
            )
        if (
            str(review_request.get("rationale") or "").strip()
            != str(evidence.get("officer_rationale") or "").strip()
        ):
            raise EvidenceLinkMismatch(
                "The review-control rationale does not match the requested "
                "terminal transition."
            )

        raw_control_evidence = review_request.get("transition_evidence")
        try:
            stored_control_evidence = json.loads(
                raw_control_evidence or "{}"
            )
        except (TypeError, ValueError) as exc:
            raise EvidenceLinkMismatch(
                "The review-control record has invalid typed evidence."
            ) from exc
        if not isinstance(stored_control_evidence, dict):
            raise EvidenceLinkMismatch(
                "The review-control record has invalid typed evidence."
            )
        if any(
            key not in REVIEW_CONTROL_EVIDENCE_TYPES
            or value is None
            or isinstance(value, (bool, dict, list, tuple, set))
            or not str(value).strip()
            for key, value in stored_control_evidence.items()
        ):
            raise EvidenceLinkMismatch(
                "The review-control record has invalid typed evidence."
            )
        requested_control_evidence = {
            key: value
            for key, value in evidence.items()
            if key in REVIEW_CONTROL_EVIDENCE_TYPES
        }
        if {
            key: str(value).strip()
            for key, value in stored_control_evidence.items()
        } != {
            key: str(value).strip()
            for key, value in requested_control_evidence.items()
        }:
            raise EvidenceLinkMismatch(
                "The review-control typed evidence does not match the "
                "requested terminal transition."
            )

    if "escalation_id" in evidence:
        escalation = _query_one(
            db,
            "SELECT id, alert_id FROM monitoring_alert_escalations WHERE id = ?",
            (evidence["escalation_id"],),
        )
        if not escalation:
            raise LinkedObjectMissing("The escalation record does not exist.")
        if str(escalation.get("alert_id")) != str(alert_id):
            raise EvidenceLinkMismatch("The escalation record belongs to another alert.")


def _enforce_owner_evidence(
    owner: str,
    target: str,
    evidence: Mapping[str, Any],
) -> None:
    if target == "dismissed":
        if owner == "screening_review" and "case_identifier" not in evidence:
            raise MissingEvidence(
                "Screening-owned dismissal evidence requires the alert's exact "
                "provider case_identifier."
            )
        if owner == "documents" and not (
            "document_id" in evidence or "document_request_id" in evidence
        ):
            raise MissingEvidence(
                "Document-owned dismissal evidence requires document_id or "
                "document_request_id."
            )


def _enforce_reason_evidence(
    target: str,
    reason_code: str,
    evidence: Mapping[str, Any],
) -> None:
    if target != "dismissed":
        return
    dismissal_reason = _token(evidence.get("dismissal_reason"))
    if reason_code != f"dismiss_{dismissal_reason}":
        raise InvalidTransition(
            "dismissal reason_code does not match the structured dismissal_reason."
        )


def _enforce_four_eyes(
    alert: Mapping[str, Any],
    owner: str,
    target: str,
    actor: Mapping[str, str],
    evidence: Mapping[str, Any],
) -> None:
    if target == "waived":
        if actor["type"] == "officer" and actor["role"] not in SENIOR_ROLES:
            raise FourEyesRequired("Only a senior officer may directly approve a waiver.")
        return
    if target not in {"dismissed", "resolved"}:
        return
    if not _requires_review_control(alert, owner, target, evidence):
        return
    if "review_request_id" not in evidence:
        raise FourEyesRequired(
            "This material clearance requires an approved review-control record."
        )


def _lock_alert(db: Any, alert_id: Any) -> Dict[str, Any]:
    if getattr(db, "is_postgres", False):
        row = db.execute(
            "SELECT * FROM monitoring_alerts WHERE id = ? FOR UPDATE",
            (alert_id,),
        ).fetchone()
    else:
        conn = getattr(db, "conn", None)
        if conn is not None and not getattr(conn, "in_transaction", False):
            db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    if row is None:
        raise AlertNotFound("Monitoring Alert not found.")
    return dict(row)


def lock_alert_for_transition(db: Any, alert_id: Any) -> Dict[str, Any]:
    """Lock and return an alert for a broader caller-owned atomic operation.

    Routing helpers use this before creating/linking a downstream row. The final
    status change must still call :func:`transition_alert_status`.
    """

    return _lock_alert(db, alert_id)


def _rollback_quietly(db: Any) -> None:
    try:
        db.rollback()
    except Exception:
        logger.exception("Monitoring transition rollback failed")


def _log_denied(
    exc: Exception,
    *,
    alert_id: Any,
    expected_status: Any,
    target_status: Any,
    source_workflow: Any,
) -> None:
    logger.warning(
        "monitoring_transition_denied error=%s alert_id=%s expected=%s "
        "target=%s source_workflow=%s",
        getattr(exc, "error_code", type(exc).__name__),
        alert_id,
        str(expected_status or "")[:40],
        str(target_status or "")[:40],
        str(source_workflow or "")[:80],
    )


def _canonical_audit_append() -> Callable[..., str]:
    from db import append_audit_log

    return append_audit_log


def transition_alert_status(
    db: Any,
    alert_id: Any,
    *,
    expected_status: str,
    target_status: str,
    actor: Mapping[str, Any],
    actor_type: str = "officer",
    source_workflow: str,
    reason_code: str,
    reason: str,
    evidence: Optional[Mapping[str, Any]] = None,
    request_id: Optional[str] = None,
    commit: bool = True,
) -> Dict[str, Any]:
    """Validate and atomically change one Monitoring Alert status.

    The caller must supply the exact expected current state. When ``commit`` is
    false, the caller owns the surrounding transaction and must commit promptly;
    any exception still rolls back the entire connection so partial mutations
    cannot survive.
    """

    expected = target = workflow = ""
    try:
        expected = _normalize_status(expected_status, field="expected_status")
        target = _normalize_status(target_status, field="target_status")
        workflow = _token(source_workflow)
        if not workflow:
            raise InvalidTransition("source_workflow is required.")
        actor_info = _safe_actor(actor, actor_type)
        normalized_evidence = _normalize_evidence(evidence)
        correlation_id = str(request_id or "").strip()
        if not correlation_id:
            try:
                from observability import get_request_id as _get_request_id

                correlation_id = str(_get_request_id() or "").strip()
            except Exception:
                correlation_id = ""
        if not correlation_id:
            correlation_id = f"monitoring-transition-{uuid.uuid4()}"

        allowed_reason_codes = ALLOWED_REASON_CODES.get(target, frozenset())
        normalized_reason_code = _token(reason_code)
        if normalized_reason_code not in allowed_reason_codes:
            raise InvalidTransition(
                "reason_code is not approved for the requested target status."
            )
        human_reason = str(reason or "").strip()
        if not human_reason:
            human_reason = normalized_reason_code.replace("_", " ")
        human_reason = human_reason[:1000]

        alert = _lock_alert(db, alert_id)
        _validate_actor_identity(db, actor_info)
        current = str(alert.get("status") or "").strip()
        if current not in CANONICAL_STATUS_SET:
            raise InvalidStatus(
                "The stored Monitoring Alert status is not canonical; manual reconciliation is required."
            )
        if current == target:
            raise AlreadyInTargetState(
                "The Monitoring Alert is already in the requested target state."
            )
        if current != expected:
            raise StaleCurrentState(
                "The Monitoring Alert changed since it was read; refresh and retry."
            )
        if current in TERMINAL_STATUSES:
            raise TerminalStateError(
                "Terminal Monitoring Alerts cannot be reopened or transitioned."
            )
        if alert.get("application_id") in (None, ""):
            raise EvidenceLinkMismatch(
                "The Monitoring Alert has no application linkage and requires "
                "manual reconciliation before transition."
            )

        owner = _authoritative_alert_owner(
            db,
            alert,
            transition_target=target,
            evidence=normalized_evidence,
        )
        edge_rules = tuple(
            rule
            for rule in TRANSITION_RULES
            if rule.source == current and rule.target == target
        )
        if not edge_rules:
            raise InvalidTransition(
                f"Transition {current} → {target} is prohibited by {STATE_MACHINE_VERSION}."
            )
        rules = _matching_rules(current, target, actor_info, workflow, owner)
        if not rules:
            actor_edge = any(
                actor_info["type"] in rule.actor_types
                and workflow in rule.source_workflows
                for rule in edge_rules
                if not rule.future_only
            )
            if actor_edge:
                if not any(actor_info["role"] in rule.roles for rule in edge_rules):
                    raise InsufficientRole(
                        "The actor role is not authorized for this transition."
                    )
                raise WrongAlertType(
                    "The transition is not permitted for this alert owner/type."
                )
            raise InsufficientRole(
                "The actor or source workflow is not authorized for this transition."
            )
        rule = _select_rule(
            rules,
            normalized_evidence,
            normalized_reason_code,
        )

        if target == "assigned":
            _validate_assignment_authority(
                db,
                actor_info,
                normalized_evidence["officer_id"],
            )
        _enforce_reason_evidence(
            target,
            normalized_reason_code,
            normalized_evidence,
        )
        _enforce_owner_evidence(owner, target, normalized_evidence)
        _validate_linked_evidence(
            db,
            alert,
            target,
            normalized_evidence,
            normalized_reason_code,
            actor_info,
        )
        _enforce_four_eyes(alert, owner, target, actor_info, normalized_evidence)

        # The expected-state predicate is the structural backstop after the row
        # lock and protects tests/fakes that cannot model FOR UPDATE precisely.
        terminal = target in TERMINAL_STATUSES
        db.execute(
            """
            UPDATE monitoring_alerts
               SET status = ?,
                   triaged_at = CASE
                       WHEN ? IN ('triaged','in_review') THEN COALESCE(triaged_at, CURRENT_TIMESTAMP)
                       ELSE triaged_at
                   END,
                   assigned_at = CASE
                       WHEN ? = 'assigned' THEN COALESCE(assigned_at, CURRENT_TIMESTAMP)
                       ELSE assigned_at
                   END,
                   resolved_at = CASE
                       WHEN ? THEN CURRENT_TIMESTAMP
                       ELSE resolved_at
                   END
             WHERE id = ? AND status = ?
            """,
            (target, target, target, terminal, alert_id, current),
        )
        rowcount = getattr(getattr(db, "_cursor", None), "rowcount", None)
        if rowcount not in (None, -1, 1):
            raise StaleCurrentState(
                "The Monitoring Alert changed during the transition."
            )

        updated = _query_one(
            db,
            "SELECT * FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        )
        if not updated or str(updated.get("status")) != target:
            raise StaleCurrentState(
                "The Monitoring Alert did not reach the requested target state."
            )

        audit_fn = _canonical_audit_append()
        if not callable(audit_fn):
            raise AuditInfrastructureError(
                "Monitoring transition audit infrastructure is unavailable."
            )

        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        audit_detail = {
            "alert_id": alert_id,
            "application_id": alert.get("application_id"),
            "previous_status": current,
            "new_status": target,
            "actor_type": actor_info["type"],
            "actor_id": actor_info["id"],
            "actor_role": actor_info["role"],
            "source_workflow": workflow,
            "reason_code": normalized_reason_code,
            "reason": human_reason,
            "evidence": normalized_evidence,
            "transition_rule_id": rule.rule_id,
            "transition_matrix_version": STATE_MACHINE_VERSION,
            "request_id": correlation_id,
            "timestamp": timestamp,
            "originating_pr": ORIGINATING_PR,
        }
        try:
            entry_hash = audit_fn(
                db,
                action=CANONICAL_AUDIT_ACTION,
                user_id=actor_info["id"],
                user_name=actor_info["name"],
                user_role=actor_info["role"],
                target=f"monitoring_alert:{alert_id}",
                detail=json.dumps(audit_detail, default=str, sort_keys=True),
                before_state={"status": current},
                after_state={"status": target},
                application_id=alert.get("application_id"),
                request_id=correlation_id,
                commit=False,
            )
        except AuditInfrastructureError:
            raise
        except Exception as exc:
            raise AuditInfrastructureError(
                "Monitoring transition audit write failed."
            ) from exc
        if not isinstance(entry_hash, str) or not entry_hash.strip():
            raise AuditInfrastructureError(
                "Monitoring transition audit writer returned no verifiable entry hash."
            )
        persisted_audit = _query_one(
            db,
            """
            SELECT action, target, application_id, request_id,
                   before_state, after_state
              FROM audit_log
             WHERE entry_hash = ?
            """,
            (entry_hash,),
        )
        expected_before = {"status": current}
        expected_after = {"status": target}
        try:
            recorded_before = json.loads(
                str((persisted_audit or {}).get("before_state") or "")
            )
            recorded_after = json.loads(
                str((persisted_audit or {}).get("after_state") or "")
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuditInfrastructureError(
                "Monitoring transition audit row could not be verified."
            ) from exc
        if (
            not persisted_audit
            or persisted_audit.get("action") != CANONICAL_AUDIT_ACTION
            or persisted_audit.get("target")
            != f"monitoring_alert:{alert_id}"
            or str(persisted_audit.get("application_id") or "")
            != str(alert.get("application_id") or "")
            or persisted_audit.get("request_id") != correlation_id
            or recorded_before != expected_before
            or recorded_after != expected_after
        ):
            raise AuditInfrastructureError(
                "Monitoring transition audit row did not match the transition."
            )

        if commit:
            db.commit()
        return {
            "changed": True,
            "alert_id": alert_id,
            "application_id": alert.get("application_id"),
            "previous_status": current,
            "status": target,
            "state_machine_version": STATE_MACHINE_VERSION,
            "transition_rule_id": rule.rule_id,
            "audit_entry_hash": entry_hash,
            "alert": updated,
        }
    except Exception as exc:
        _rollback_quietly(db)
        if isinstance(exc, MonitoringTransitionError):
            _log_denied(
                exc,
                alert_id=alert_id,
                expected_status=expected or expected_status,
                target_status=target or target_status,
                source_workflow=workflow or source_workflow,
            )
        raise


def is_canonical_status(value: Any) -> bool:
    return str(value or "").strip() in CANONICAL_STATUS_SET


def is_terminal_status(value: Any) -> bool:
    return str(value or "").strip() in TERMINAL_STATUSES


def is_handoff_status(value: Any) -> bool:
    return str(value or "").strip() in HANDOFF_STATUSES


def alert_owner(db: Any, alert: Mapping[str, Any]) -> str:
    """Return the authoritative downstream ownership boundary for an alert."""

    return _authoritative_alert_owner(db, alert)


__all__ = [
    "ACTIVE_STATUSES",
    "ACTOR_TYPES",
    "ALLOWED_REASON_CODES",
    "AlertNotFound",
    "AlreadyInTargetState",
    "AmbiguousAlertOwner",
    "AuditInfrastructureError",
    "CANONICAL_AUDIT_ACTION",
    "CANONICAL_STATUSES",
    "CANONICAL_STATUS_SET",
    "ENABLED_TRANSITION_RULES",
    "EVIDENCE_TYPES",
    "EvidenceLinkMismatch",
    "FUTURE_TRANSITION_RULES",
    "FourEyesRequired",
    "FutureTransitionDisabled",
    "HANDOFF_STATUSES",
    "InsufficientRole",
    "InvalidStatus",
    "InvalidTransition",
    "LinkedObjectMissing",
    "MissingEvidence",
    "MonitoringTransitionError",
    "ORIGINATING_PR",
    "STATE_MACHINE_VERSION",
    "StaleCurrentState",
    "TERMINAL_STATUSES",
    "TRANSITION_COUNT",
    "TRANSITION_RULES",
    "TerminalStateError",
    "TransitionRule",
    "WrongAlertType",
    "WrongEvidenceType",
    "alert_owner",
    "is_canonical_status",
    "is_handoff_status",
    "is_terminal_status",
    "lock_alert_for_transition",
    "transition_alert_status",
    "validate_assignment_authority",
]
