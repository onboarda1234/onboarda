"""
Change Management Module for Onboarda / RegMind Platform.

Implements formal Change Requests, Change Alerts, and controlled
implementation of approved changes into the live approved client profile.

This module provides:
- Change Alert lifecycle (external/internal signal → review → convert/dismiss)
- Change Request lifecycle (creation → triage → screening → risk → approval → implementation)
- Entity profile versioning (approved profile snapshots)
- Status transition guards
- Materiality classification
- Downstream action routing (screening, risk, EDD, memo hooks)
- Audit logging at every meaningful step
"""

import json
import logging
import secrets
from datetime import datetime, date, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# Constants & Enums
# ============================================================================

# --- Change Alert Statuses ---
CHANGE_ALERT_STATUSES = (
    "new",
    "under_review",
    "awaiting_client_confirmation",
    "converted_to_change_request",
    "dismissed",
    "resolved_no_change",
    "escalated",
)

# Valid transitions: from_status -> allowed_to_statuses
CHANGE_ALERT_TRANSITIONS = {
    "new": ("under_review", "dismissed", "escalated"),
    "under_review": (
        "awaiting_client_confirmation",
        "converted_to_change_request",
        "dismissed",
        "resolved_no_change",
        "escalated",
    ),
    "awaiting_client_confirmation": (
        "under_review",
        "converted_to_change_request",
        "dismissed",
        "resolved_no_change",
    ),
    "escalated": (
        "under_review",
        "converted_to_change_request",
        "dismissed",
        "resolved_no_change",
    ),
    # Terminal states — no further transitions
    "converted_to_change_request": (),
    "dismissed": (),
    "resolved_no_change": (),
}

# --- Change Request Statuses ---
CHANGE_REQUEST_STATUSES = (
    "draft",
    "submitted",
    "triage_in_progress",
    "pending_information",
    "ready_for_review",
    "screening_in_progress",
    "risk_review_required",
    "approval_pending",
    "approved",
    "rejected",
    "partially_approved",
    "implemented",
    "cancelled",
    "superseded",
)

CHANGE_REQUEST_TRANSITIONS = {
    "draft": ("submitted", "cancelled"),
    "submitted": ("triage_in_progress", "cancelled"),
    "triage_in_progress": (
        "pending_information",
        "ready_for_review",
        "screening_in_progress",
        "cancelled",
    ),
    "pending_information": (
        "triage_in_progress",
        "ready_for_review",
        "cancelled",
    ),
    "ready_for_review": (
        "screening_in_progress",
        "risk_review_required",
        "approval_pending",
        "cancelled",
    ),
    "screening_in_progress": (
        "ready_for_review",
        "risk_review_required",
        "approval_pending",
        "cancelled",
    ),
    "risk_review_required": (
        "approval_pending",
        "ready_for_review",
        "cancelled",
    ),
    "approval_pending": (
        "approved",
        "rejected",
        "partially_approved",
        "pending_information",
        "cancelled",
    ),
    "approved": ("implemented", "superseded"),
    "partially_approved": ("implemented", "approved", "superseded", "cancelled"),
    "rejected": (),  # Terminal
    "implemented": (),  # Terminal
    "cancelled": (),  # Terminal
    "superseded": (),  # Terminal
}

# --- Materiality Tiers ---
MATERIALITY_TIERS = ("tier1", "tier2", "tier3")

MATERIALITY_DEFAULTS = {
    # Tier 1 — High-impact structural changes
    "legal_name_change": "tier1",
    "director_change": "tier1",
    "ubo_change": "tier1",
    "shareholding_change": "tier1",
    "control_change": "tier1",
    "registered_address_country_change": "tier1",
    "business_activity_change": "tier1",
    "countries_of_operation_change": "tier1",
    "licensing_status_change": "tier1",
    # Tier 2 — Moderate operational changes
    "same_country_address_change": "tier2",
    "signatory_change": "tier2",
    "operational_change": "tier2",
    # Tier 3 — Administrative/cosmetic changes
    "contact_detail_update": "tier3",
    "website_update": "tier3",
    "typo_correction": "tier3",
    "formatting_correction": "tier3",
}

# --- Change Sources / Origins ---
CHANGE_SOURCES = (
    "portal_client",
    "backoffice_manual",
    "periodic_review",
    "ongoing_monitoring",
    "external_alert_conversion",
    "system_admin",
)

CHANGE_CHANNELS = (
    "portal",
    "backoffice",
    "email",
    "phone",
    "relationship_manager",
    "companies_house",
    "open_corporates",
    "registry_api",
    "other",
)

# --- Change Types ---
CHANGE_TYPES = (
    "company_details",
    "director_add",
    "director_remove",
    "director_update",
    "ubo_add",
    "ubo_remove",
    "ubo_update",
    "intermediary_add",
    "intermediary_remove",
    "intermediary_update",
    "signatory_add",
    "signatory_remove",
    "signatory_update",
    "address_change",
    "business_activity_change",
    "licensing_change",
    "contact_update",
    "other",
)

# --- Person change actions ---
PERSON_ACTIONS = ("add", "remove", "update")
PERSON_STATUSES = ("active", "ceased", "pending")

# --- Downstream action flags ---
DOWNSTREAM_ACTION_MAP = {
    "tier1": {
        "screening_required": True,
        "risk_review_required": True,
        "edd_review_required": False,  # Depends on resulting risk level
        "memo_addendum_hook": True,
        "periodic_review_acceleration_hook": True,
    },
    "tier2": {
        "screening_required": True,
        "risk_review_required": True,
        "edd_review_required": False,
        "memo_addendum_hook": False,
        "periodic_review_acceleration_hook": False,
    },
    "tier3": {
        "screening_required": False,
        "risk_review_required": False,
        "edd_review_required": False,
        "memo_addendum_hook": False,
        "periodic_review_acceleration_hook": False,
    },
}

# --- Roles allowed for each action ---
ROLE_PERMISSIONS = {
    "create_request": ("admin", "sco", "co", "client"),
    "submit_request": ("admin", "sco", "co", "client"),
    "triage_request": ("admin", "sco", "co"),
    "request_info": ("admin", "sco", "co"),
    "review_request": ("admin", "sco", "co"),
    "reject_request": ("admin", "sco", "co"),
    "approve_tier3": ("admin", "sco", "co"),
    "approve_tier2": ("admin", "sco", "co"),
    "approve_tier1": ("admin", "sco"),
    "implement_change": ("admin", "sco"),
    "upload_document": ("admin", "sco", "co"),
    "create_alert": ("admin", "sco", "co"),
    "review_alert": ("admin", "sco", "co"),
    "dismiss_alert": ("admin", "sco", "co"),
    "convert_alert": ("admin", "sco", "co"),
}

# Whitelists for person change operations — validated before any SQL construction
_ALLOWED_PERSON_TABLES = {"directors", "ubos"}
_PERSON_SAFE_FIELDS = {
    "directors": {"full_name", "first_name", "last_name", "nationality",
                  "date_of_birth", "is_pep", "pep_declaration"},
    "ubos": {"full_name", "first_name", "last_name", "nationality",
             "date_of_birth", "is_pep", "pep_declaration", "ownership_pct"},
}


# ============================================================================
# ID Generation
# ============================================================================

def generate_change_alert_id() -> str:
    """Generate a unique Change Alert identifier."""
    ts = datetime.now(timezone.utc).strftime("%y%m%d")
    token = secrets.token_hex(4).upper()
    return f"CA-{ts}-{token}"


def generate_change_request_id() -> str:
    """Generate a unique Change Request identifier."""
    ts = datetime.now(timezone.utc).strftime("%y%m%d")
    token = secrets.token_hex(4).upper()
    return f"CR-{ts}-{token}"


def generate_profile_version_id() -> str:
    """Generate a unique entity profile version identifier."""
    ts = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    token = secrets.token_hex(3).upper()
    return f"PV-{ts}-{token}"


# ============================================================================
# Validation Helpers
# ============================================================================

def validate_alert_transition(current_status: str, new_status: str) -> Tuple[bool, str]:
    """Validate a change alert status transition.

    Returns (is_valid, error_message).
    """
    if current_status not in CHANGE_ALERT_STATUSES:
        return False, f"Unknown current alert status: {current_status}"
    if new_status not in CHANGE_ALERT_STATUSES:
        return False, f"Unknown target alert status: {new_status}"
    allowed = CHANGE_ALERT_TRANSITIONS.get(current_status, ())
    if new_status not in allowed:
        return False, (
            f"Invalid alert transition: {current_status} → {new_status}. "
            f"Allowed: {', '.join(allowed) if allowed else 'none (terminal state)'}"
        )
    return True, ""


def validate_request_transition(current_status: str, new_status: str) -> Tuple[bool, str]:
    """Validate a change request status transition.

    Returns (is_valid, error_message).
    """
    if current_status not in CHANGE_REQUEST_STATUSES:
        return False, f"Unknown current request status: {current_status}"
    if new_status not in CHANGE_REQUEST_STATUSES:
        return False, f"Unknown target request status: {new_status}"
    allowed = CHANGE_REQUEST_TRANSITIONS.get(current_status, ())
    if new_status not in allowed:
        return False, (
            f"Invalid request transition: {current_status} → {new_status}. "
            f"Allowed: {', '.join(allowed) if allowed else 'none (terminal state)'}"
        )
    return True, ""


def classify_materiality(change_type: str) -> str:
    """Return materiality tier for a given change type.

    Falls back to tier2 for unknown change types (safe default).
    """
    return MATERIALITY_DEFAULTS.get(change_type, "tier2")


def get_downstream_actions(materiality: str) -> Dict[str, bool]:
    """Return downstream action flags for a materiality tier."""
    return DOWNSTREAM_ACTION_MAP.get(materiality, DOWNSTREAM_ACTION_MAP["tier2"]).copy()


def check_role_permission(user_role: str, action: str) -> Tuple[bool, str]:
    """Check if a user role is allowed to perform an action.

    Returns (is_allowed, error_message).
    """
    allowed_roles = ROLE_PERMISSIONS.get(action)
    if allowed_roles is None:
        return False, f"Unknown action: {action}"
    if user_role not in allowed_roles:
        return False, f"Role '{user_role}' not permitted for '{action}'. Requires: {', '.join(allowed_roles)}"
    return True, ""


# ============================================================================
# Entity Profile Snapshot
# ============================================================================

def _json_safe_value(val: Any) -> Any:
    """Convert a value to a JSON-safe type.

    Handles datetime/date objects that SQLite may return as Python objects.
    """
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return val


def _json_safe_dict(d: Dict) -> Dict:
    """Return a copy of dict with all values converted to JSON-safe types."""
    return {k: _json_safe_value(v) for k, v in d.items()}


def snapshot_entity_profile(db, application_id: str) -> Dict[str, Any]:
    """Capture a full snapshot of the current entity/company profile.

    Reads from applications, directors, ubos, intermediaries tables.
    Returns a dict that can be stored as JSON in entity_profile_versions.
    All values are JSON-safe (no raw datetime objects).
    """
    app = db.execute(
        "SELECT * FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    if not app:
        return {}

    # Convert row to dict
    app_dict = dict(app) if app else {}

    # Get related parties — ensure all values are JSON-serializable
    directors = [
        _json_safe_dict(dict(r)) for r in db.execute(
            "SELECT * FROM directors WHERE application_id = ?", (application_id,)
        ).fetchall()
    ]
    ubos = [
        _json_safe_dict(dict(r)) for r in db.execute(
            "SELECT * FROM ubos WHERE application_id = ?", (application_id,)
        ).fetchall()
    ]
    intermediaries = [
        _json_safe_dict(dict(r)) for r in db.execute(
            "SELECT * FROM intermediaries WHERE application_id = ?", (application_id,)
        ).fetchall()
    ]

    # Extract profile-relevant fields only (no internal workflow fields)
    profile_fields = [
        "company_name", "brn", "country", "sector", "entity_type",
        "ownership_structure", "prescreening_data",
        "risk_score", "risk_level", "risk_dimensions",
    ]
    profile = {}
    for f in profile_fields:
        if f in app_dict:
            profile[f] = _json_safe_value(app_dict[f])

    profile["directors"] = directors
    profile["ubos"] = ubos
    profile["intermediaries"] = intermediaries
    profile["snapshot_at"] = datetime.now(timezone.utc).isoformat()

    return profile


# ============================================================================
# Change Alert Operations
# ============================================================================

def create_change_alert(
    db,
    application_id: str,
    alert_type: str,
    source_channel: str,
    summary: str,
    detected_changes: Dict[str, Any],
    confidence: Optional[float] = None,
    source_reference: Optional[str] = None,
    source_payload: Optional[Dict] = None,
    detected_by: Optional[str] = None,
    user: Optional[Dict] = None,
    log_audit_fn=None,
) -> Dict[str, Any]:
    """Create a new Change Alert.

    Args:
        db: Database connection.
        application_id: ID of the application/entity.
        alert_type: Type of change detected.
        source_channel: Where the alert originated (companies_house, etc.).
        summary: Human-readable description.
        detected_changes: JSON-serializable dict of detected deltas.
        confidence: Optional confidence score (0.0 - 1.0).
        source_reference: Optional URL or reference to source.
        source_payload: Optional raw evidence/payload from source.
        detected_by: Optional identifier of detection system/agent.
        user: Current user dict (for audit).
        log_audit_fn: Audit logging function.

    Returns:
        Dict with the created alert data.
    """
    alert_id = generate_change_alert_id()
    materiality = classify_materiality(alert_type)
    now = datetime.now(timezone.utc).isoformat()

    db.execute(
        """INSERT INTO change_alerts
           (id, application_id, alert_type, source_channel, summary,
            detected_changes, materiality, confidence, source_reference,
            source_payload, detected_by, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)""",
        (
            alert_id, application_id, alert_type, source_channel, summary,
            json.dumps(detected_changes) if detected_changes else "{}",
            materiality,
            confidence,
            source_reference,
            json.dumps(source_payload) if source_payload else None,
            detected_by or "system",
            now, now,
        ),
    )
    db.commit()

    if log_audit_fn and user:
        log_audit_fn(
            user, "Change Alert Created", alert_id,
            f"Alert type={alert_type}, channel={source_channel}, materiality={materiality}",
            db=db,
        )

    return {
        "id": alert_id,
        "application_id": application_id,
        "alert_type": alert_type,
        "source_channel": source_channel,
        "summary": summary,
        "detected_changes": detected_changes,
        "materiality": materiality,
        "confidence": confidence,
        "source_reference": source_reference,
        "detected_by": detected_by or "system",
        "status": "new",
        "created_at": now,
    }


def update_change_alert_status(
    db,
    alert_id: str,
    new_status: str,
    user: Dict,
    notes: Optional[str] = None,
    log_audit_fn=None,
) -> Tuple[bool, str]:
    """Update a Change Alert's status with transition guard.

    Returns (success, error_message).
    """
    row = db.execute(
        "SELECT id, status FROM change_alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    if not row:
        return False, f"Alert not found: {alert_id}"

    current_status = row["status"]
    valid, err = validate_alert_transition(current_status, new_status)
    if not valid:
        return False, err

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """UPDATE change_alerts
           SET status = ?, reviewer_id = ?, reviewer_notes = ?,
               reviewed_at = ?, updated_at = ?
           WHERE id = ?""",
        (new_status, user.get("sub"), notes, now, now, alert_id),
    )
    db.commit()

    if log_audit_fn:
        log_audit_fn(
            user, "Change Alert Status Updated", alert_id,
            f"Status: {current_status} → {new_status}" + (f". Notes: {notes}" if notes else ""),
            db=db,
            before_state={"status": current_status},
            after_state={"status": new_status},
        )

    return True, ""


def convert_alert_to_request(
    db,
    alert_id: str,
    user: Dict,
    additional_notes: Optional[str] = None,
    items: Optional[List[Dict]] = None,
    log_audit_fn=None,
) -> Tuple[Optional[Dict], str]:
    """Convert a Change Alert into a formal Change Request.

    If items are provided explicitly, they are used directly.
    Otherwise, items are derived from the alert's detected_changes.
    Returns (request_dict_or_none, error_message).
    """
    alert = db.execute(
        "SELECT * FROM change_alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    if not alert:
        return None, f"Alert not found: {alert_id}"

    alert = dict(alert)
    current_status = alert["status"]

    # Validate transition
    valid, err = validate_alert_transition(current_status, "converted_to_change_request")
    if not valid:
        return None, err

    # Use explicitly provided items or derive from alert
    if items and len(items) > 0:
        request_items = items
    else:
        detected_changes = alert.get("detected_changes")
        if isinstance(detected_changes, str):
            try:
                detected_changes = json.loads(detected_changes)
            except (json.JSONDecodeError, TypeError):
                detected_changes = {}
        request_items = _alert_changes_to_items(detected_changes, alert.get("alert_type", "other"))

    request = create_change_request(
        db=db,
        application_id=alert["application_id"],
        source="external_alert_conversion",
        source_channel=alert.get("source_channel", "other"),
        reason=f"Converted from alert {alert_id}: {alert.get('summary', '')}",
        items=request_items,
        user=user,
        source_alert_id=alert_id,
        log_audit_fn=log_audit_fn,
    )

    # Mark the alert as converted
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """UPDATE change_alerts
           SET status = 'converted_to_change_request',
               converted_request_id = ?, reviewer_id = ?,
               reviewer_notes = ?, reviewed_at = ?, updated_at = ?
           WHERE id = ?""",
        (request["id"], user.get("sub"), additional_notes, now, now, alert_id),
    )
    db.commit()

    if log_audit_fn:
        log_audit_fn(
            user, "Change Alert Converted", alert_id,
            f"Converted to change request {request['id']}",
            db=db,
            before_state={"status": current_status},
            after_state={"status": "converted_to_change_request", "request_id": request["id"]},
        )

    return request, ""


def _alert_changes_to_items(detected_changes: Dict, alert_type: str) -> List[Dict]:
    """Convert detected_changes dict from alert into change request items."""
    items = []
    if not detected_changes:
        return items
    for field, delta in detected_changes.items():
        old_value = delta.get("old") if isinstance(delta, dict) else None
        new_value = delta.get("new") if isinstance(delta, dict) else str(delta)
        items.append({
            "change_type": alert_type,
            "field_name": field,
            "old_value": json.dumps(old_value) if old_value is not None else None,
            "new_value": json.dumps(new_value) if new_value is not None else None,
            "materiality": classify_materiality(alert_type),
        })
    return items


# ============================================================================
# Change Request Operations
# ============================================================================

def create_change_request(
    db,
    application_id: str,
    source: str,
    source_channel: str,
    reason: str,
    items: List[Dict],
    user: Dict,
    source_alert_id: Optional[str] = None,
    log_audit_fn=None,
) -> Dict[str, Any]:
    """Create a new Change Request with items.

    Args:
        db: Database connection.
        application_id: Application/entity being changed.
        source: Origin of request (portal_client, backoffice_manual, etc.).
        source_channel: Channel (portal, backoffice, email, etc.).
        reason: Human-readable reason for the change.
        items: List of dicts describing each field/person change.
        user: Current user dict.
        source_alert_id: Optional ID of originating alert.
        log_audit_fn: Audit logging function.

    Returns:
        Dict with created request data.
    """
    # --- Service-layer role guard ---
    allowed, role_err = check_role_permission(user.get("role", ""), "create_request")
    if not allowed:
        raise PermissionError(role_err)

    request_id = generate_change_request_id()
    now = datetime.now(timezone.utc).isoformat()

    # Determine overall materiality (highest of all items)
    item_materialities = [
        i.get("materiality", classify_materiality(i.get("change_type", "other")))
        for i in items
    ] if items else ["tier3"]
    overall_materiality = _highest_materiality(item_materialities)

    # Capture base profile version for conflict detection
    base_version_id = _get_current_profile_version_id(db, application_id)

    # If no profile version exists yet, create an initial baseline snapshot
    if not base_version_id:
        initial_snapshot = snapshot_entity_profile(db, application_id)
        if initial_snapshot:
            base_version_id = _create_profile_version(
                db, application_id, None, {}, initial_snapshot, user
            )

    # Downstream action flags based on materiality
    downstream = get_downstream_actions(overall_materiality)

    db.execute(
        """INSERT INTO change_requests
           (id, application_id, source, source_channel, source_alert_id,
            reason, materiality, status, base_profile_version_id,
            screening_required, risk_review_required, edd_review_required,
            memo_addendum_hook, periodic_review_acceleration_hook,
            created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            request_id, application_id, source, source_channel, source_alert_id,
            reason, overall_materiality, base_version_id,
            downstream["screening_required"],
            downstream["risk_review_required"],
            downstream["edd_review_required"],
            downstream["memo_addendum_hook"],
            downstream["periodic_review_acceleration_hook"],
            user.get("sub"), now, now,
        ),
    )

    # Insert items
    for idx, item in enumerate(items):
        item_id = f"{request_id}-I{idx + 1:03d}"
        item_materiality = item.get("materiality", classify_materiality(item.get("change_type", "other")))
        person_snapshot = item.get("person_snapshot")

        db.execute(
            """INSERT INTO change_request_items
               (id, request_id, change_type, field_name,
                old_value, new_value, materiality,
                person_action, person_snapshot, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id, request_id, item.get("change_type", "other"),
                item.get("field_name"),
                item.get("old_value"),
                item.get("new_value"),
                item_materiality,
                item.get("person_action"),
                json.dumps(person_snapshot) if person_snapshot else None,
                now,
            ),
        )

    db.commit()

    if log_audit_fn:
        log_audit_fn(
            user, "Change Request Created", request_id,
            f"Source={source}, materiality={overall_materiality}, items={len(items)}",
            db=db,
        )

    return {
        "id": request_id,
        "application_id": application_id,
        "source": source,
        "source_channel": source_channel,
        "source_alert_id": source_alert_id,
        "reason": reason,
        "materiality": overall_materiality,
        "status": "draft",
        "base_profile_version_id": base_version_id,
        "items": items,
        "downstream_actions": downstream,
        "created_by": user.get("sub"),
        "created_at": now,
    }


def update_change_request_status(
    db,
    request_id: str,
    new_status: str,
    user: Dict,
    notes: Optional[str] = None,
    log_audit_fn=None,
) -> Tuple[bool, str]:
    """Update a Change Request's status with transition guard.

    Returns (success, error_message).
    """
    row = db.execute(
        "SELECT id, status, materiality FROM change_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if not row:
        return False, f"Request not found: {request_id}"

    current_status = row["status"]
    valid, err = validate_request_transition(current_status, new_status)
    if not valid:
        return False, err

    # Role-based approval checks
    materiality = row["materiality"]
    if new_status in ("approved", "partially_approved"):
        action = f"approve_{materiality}"
        allowed, role_err = check_role_permission(user.get("role", ""), action)
        if not allowed:
            return False, role_err

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """UPDATE change_requests
           SET status = ?, updated_at = ?
           WHERE id = ?""",
        (new_status, now, request_id),
    )
    db.commit()

    if log_audit_fn:
        log_audit_fn(
            user, "Change Request Status Updated", request_id,
            f"Status: {current_status} → {new_status}" + (f". Notes: {notes}" if notes else ""),
            db=db,
            before_state={"status": current_status},
            after_state={"status": new_status},
        )

    return True, ""


def submit_change_request(
    db,
    request_id: str,
    user: Dict,
    log_audit_fn=None,
) -> Tuple[bool, str]:
    """Submit a draft change request for processing.

    Returns (success, error_message).
    """
    # --- Service-layer role guard ---
    allowed, role_err = check_role_permission(user.get("role", ""), "submit_request")
    if not allowed:
        return False, role_err

    row = db.execute(
        "SELECT id, status FROM change_requests WHERE id = ?", (request_id,)
    ).fetchone()
    if not row:
        return False, f"Request not found: {request_id}"
    if row["status"] != "draft":
        return False, f"Request must be in draft status to submit (current: {row['status']})"

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """UPDATE change_requests
           SET status = 'submitted', submitted_at = ?, updated_at = ?
           WHERE id = ?""",
        (now, now, request_id),
    )
    db.commit()

    if log_audit_fn:
        log_audit_fn(
            user, "Change Request Submitted", request_id,
            "Request submitted for processing",
            db=db,
            before_state={"status": "draft"},
            after_state={"status": "submitted"},
        )

    return True, ""


# ============================================================================
# Approval & Implementation
# ============================================================================

def approve_change_request(
    db,
    request_id: str,
    user: Dict,
    decision_notes: Optional[str] = None,
    log_audit_fn=None,
) -> Tuple[bool, str]:
    """Approve a change request (does NOT implement — separate step).

    Returns (success, error_message).
    """
    row = db.execute(
        "SELECT id, status, materiality FROM change_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if not row:
        return False, f"Request not found: {request_id}"

    materiality = row["materiality"]
    action = f"approve_{materiality}"
    allowed, role_err = check_role_permission(user.get("role", ""), action)
    if not allowed:
        return False, role_err

    valid, err = validate_request_transition(row["status"], "approved")
    if not valid:
        return False, err

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """UPDATE change_requests
           SET status = 'approved', approved_by = ?, approved_at = ?,
               decision_notes = ?, updated_at = ?
           WHERE id = ?""",
        (user.get("sub"), now, decision_notes, now, request_id),
    )

    # Record review
    review_id = f"{request_id}-RV-{secrets.token_hex(3).upper()}"
    db.execute(
        """INSERT INTO change_request_reviews
           (id, request_id, reviewer_id, reviewer_role, decision,
            decision_notes, reviewed_at)
           VALUES (?, ?, ?, ?, 'approved', ?, ?)""",
        (review_id, request_id, user.get("sub"), user.get("role"), decision_notes, now),
    )

    db.commit()

    if log_audit_fn:
        log_audit_fn(
            user, "Change Request Approved", request_id,
            f"Approved by {user.get('name', user.get('sub'))}. "
            f"Materiality: {materiality}. Notes: {decision_notes or 'none'}",
            db=db,
            before_state={"status": row["status"]},
            after_state={"status": "approved"},
        )

    return True, ""


def reject_change_request(
    db,
    request_id: str,
    user: Dict,
    decision_notes: Optional[str] = None,
    log_audit_fn=None,
) -> Tuple[bool, str]:
    """Reject a change request.

    Returns (success, error_message).
    """
    # --- Service-layer role guard ---
    allowed, role_err = check_role_permission(user.get("role", ""), "reject_request")
    if not allowed:
        return False, role_err

    row = db.execute(
        "SELECT id, status, materiality FROM change_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if not row:
        return False, f"Request not found: {request_id}"

    valid, err = validate_request_transition(row["status"], "rejected")
    if not valid:
        return False, err

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """UPDATE change_requests
           SET status = 'rejected', approved_by = ?, approved_at = ?,
               decision_notes = ?, updated_at = ?
           WHERE id = ?""",
        (user.get("sub"), now, decision_notes, now, request_id),
    )

    review_id = f"{request_id}-RV-{secrets.token_hex(3).upper()}"
    db.execute(
        """INSERT INTO change_request_reviews
           (id, request_id, reviewer_id, reviewer_role, decision,
            decision_notes, reviewed_at)
           VALUES (?, ?, ?, ?, 'rejected', ?, ?)""",
        (review_id, request_id, user.get("sub"), user.get("role"), decision_notes, now),
    )

    db.commit()

    if log_audit_fn:
        log_audit_fn(
            user, "Change Request Rejected", request_id,
            f"Rejected by {user.get('name', user.get('sub'))}. Notes: {decision_notes or 'none'}",
            db=db,
            before_state={"status": row["status"]},
            after_state={"status": "rejected"},
        )

    return True, ""


def implement_change_request(
    db,
    request_id: str,
    user: Dict,
    log_audit_fn=None,
    recompute_risk_fn=None,
) -> Tuple[bool, str, Optional[str]]:
    """Implement an approved change request into the live profile.

    This is the controlled implementation step:
    1. Validates request is approved
    2. Detects stale version conflicts
    3. Snapshots current profile (before_state)
    4. Applies approved changes to live data
    5. Creates new profile version (after_state)
    6. Triggers risk recomputation if needed
    7. Writes audit evidence
    8. Rolls back on failure

    Returns (success, error_message, new_version_id).
    """
    # Permission check FIRST — before any DB queries (defense in depth)
    allowed, role_err = check_role_permission(user.get("role", ""), "implement_change")
    if not allowed:
        return False, role_err, None

    row = db.execute(
        "SELECT * FROM change_requests WHERE id = ?", (request_id,)
    ).fetchone()
    if not row:
        return False, f"Request not found: {request_id}", None

    request = dict(row)
    if request["status"] != "approved":
        return False, f"Request must be approved before implementation (current: {request['status']})", None

    application_id = request["application_id"]

    # Stale version check
    current_version_id = _get_current_profile_version_id(db, application_id)
    base_version_id = request.get("base_profile_version_id")
    if base_version_id and current_version_id and base_version_id != current_version_id:
        return False, (
            f"Profile has been updated since this request was created. "
            f"Base version: {base_version_id}, current: {current_version_id}. "
            f"Please rebase or create a new request."
        ), None

    # Snapshot current profile (before)
    before_snapshot = snapshot_entity_profile(db, application_id)

    # Get items to apply
    items = db.execute(
        "SELECT * FROM change_request_items WHERE request_id = ?",
        (request_id,),
    ).fetchall()

    try:
        # Apply changes
        for item in items:
            item = dict(item)
            _apply_change_item(db, application_id, item)

        # Snapshot after applying
        after_snapshot = snapshot_entity_profile(db, application_id)

        # Create new profile version
        new_version_id = _create_profile_version(
            db, application_id, request_id, before_snapshot, after_snapshot, user
        )

        # Mark request as implemented
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            """UPDATE change_requests
               SET status = 'implemented', implemented_at = ?,
                   implemented_by = ?, result_profile_version_id = ?,
                   updated_at = ?
               WHERE id = ?""",
            (now, user.get("sub"), new_version_id, now, request_id),
        )

        db.commit()

        # Trigger risk recomputation if needed (after commit)
        if request.get("risk_review_required") and recompute_risk_fn:
            try:
                recompute_risk_fn(db, application_id, f"Change request {request_id} implemented", user, log_audit_fn)
            except Exception as e:
                logger.warning("Risk recomputation after change %s failed: %s", request_id, e)

        if log_audit_fn:
            log_audit_fn(
                user, "Change Request Implemented", request_id,
                f"Profile version: {new_version_id}. Items applied: {len(items)}",
                db=db,
                before_state=_safe_snapshot_summary(before_snapshot),
                after_state=_safe_snapshot_summary(after_snapshot),
            )

        return True, "", new_version_id

    except Exception as e:
        logger.error("Implementation of change request %s failed: %s", request_id, e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return False, f"Implementation failed: {str(e)}", None


def _apply_change_item(db, application_id: str, item: Dict) -> None:
    """Apply a single change request item to the live database.

    Handles both field-level changes and person (director/UBO/intermediary) changes.
    """
    change_type = item.get("change_type", "")
    field_name = item.get("field_name")
    new_value = item.get("new_value")
    person_action = item.get("person_action")
    person_snapshot = item.get("person_snapshot")

    if isinstance(person_snapshot, str):
        try:
            person_snapshot = json.loads(person_snapshot)
        except (json.JSONDecodeError, TypeError):
            person_snapshot = None

    # Person changes (director/UBO/intermediary)
    if change_type.startswith("director_"):
        _apply_person_change(db, application_id, "directors", person_action, person_snapshot, field_name, new_value)
    elif change_type.startswith("ubo_"):
        _apply_person_change(db, application_id, "ubos", person_action, person_snapshot, field_name, new_value)
    elif change_type.startswith("intermediary_"):
        _apply_intermediary_change(db, application_id, person_action, person_snapshot, field_name, new_value)
    elif change_type in ("company_details", "address_change", "business_activity_change",
                         "licensing_change", "contact_update", "other"):
        # Field-level changes on applications table
        if field_name and new_value is not None:
            _apply_field_change(db, application_id, field_name, new_value)


def _apply_person_change(db, application_id: str, table: str, action: str,
                         snapshot: Optional[Dict], field_name: Optional[str],
                         new_value: Optional[str]) -> None:
    """Apply a director or UBO change.

    Table and field names are validated against module-level whitelists
    (_ALLOWED_PERSON_TABLES, _PERSON_SAFE_FIELDS) before any SQL construction.
    """
    if table not in _ALLOWED_PERSON_TABLES:
        logger.warning("Blocked person change to unknown table: %s", table)
        return
    now = datetime.now(timezone.utc).isoformat()

    if action == "add" and snapshot:
        person_key = snapshot.get("person_key", f"cr_{secrets.token_hex(3)}")
        if table == "directors":
            db.execute(
                """INSERT INTO directors
                   (id, application_id, person_key, full_name, first_name, last_name,
                    nationality, date_of_birth, is_pep, pep_declaration, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    secrets.token_hex(8), application_id, person_key,
                    snapshot.get("full_name", ""),
                    snapshot.get("first_name", ""),
                    snapshot.get("last_name", ""),
                    snapshot.get("nationality"),
                    snapshot.get("date_of_birth"),
                    snapshot.get("is_pep", False),
                    json.dumps(snapshot.get("pep_declaration")) if snapshot.get("pep_declaration") else None,
                    now,
                ),
            )
        elif table == "ubos":
            db.execute(
                """INSERT INTO ubos
                   (id, application_id, person_key, full_name, first_name, last_name,
                    nationality, date_of_birth, ownership_pct, is_pep, pep_declaration, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    secrets.token_hex(8), application_id, person_key,
                    snapshot.get("full_name", ""),
                    snapshot.get("first_name", ""),
                    snapshot.get("last_name", ""),
                    snapshot.get("nationality"),
                    snapshot.get("date_of_birth"),
                    snapshot.get("ownership_pct"),
                    snapshot.get("is_pep", False),
                    json.dumps(snapshot.get("pep_declaration")) if snapshot.get("pep_declaration") else None,
                    now,
                ),
            )

    elif action == "remove" and snapshot:
        person_key = snapshot.get("person_key")
        if person_key:
            db.execute(
                f"DELETE FROM {table} WHERE application_id = ? AND person_key = ?",
                (application_id, person_key),
            )

    elif action == "update" and snapshot:
        person_key = snapshot.get("person_key")
        if person_key and field_name and new_value is not None:
            safe_fields = _PERSON_SAFE_FIELDS.get(table, set())
            if field_name in safe_fields:
                db.execute(
                    f"UPDATE {table} SET {field_name} = ? WHERE application_id = ? AND person_key = ?",
                    (new_value, application_id, person_key),
                )


def _apply_intermediary_change(db, application_id: str, action: str,
                               snapshot: Optional[Dict], field_name: Optional[str],
                               new_value: Optional[str]) -> None:
    """Apply an intermediary shareholder change."""
    now = datetime.now(timezone.utc).isoformat()

    if action == "add" and snapshot:
        person_key = snapshot.get("person_key", f"int_{secrets.token_hex(3)}")
        db.execute(
            """INSERT INTO intermediaries
               (id, application_id, person_key, entity_name, jurisdiction,
                ownership_pct, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                secrets.token_hex(8), application_id, person_key,
                snapshot.get("entity_name", ""),
                snapshot.get("jurisdiction"),
                snapshot.get("ownership_pct"),
                now,
            ),
        )
    elif action == "remove" and snapshot:
        person_key = snapshot.get("person_key")
        if person_key:
            db.execute(
                "DELETE FROM intermediaries WHERE application_id = ? AND person_key = ?",
                (application_id, person_key),
            )
    elif action == "update" and snapshot:
        person_key = snapshot.get("person_key")
        if person_key and field_name and new_value is not None:
            safe_fields = {"entity_name", "jurisdiction", "ownership_pct"}
            if field_name in safe_fields:
                db.execute(
                    f"UPDATE intermediaries SET {field_name} = ? WHERE application_id = ? AND person_key = ?",
                    (new_value, application_id, person_key),
                )


def _apply_field_change(db, application_id: str, field_name: str, new_value: str) -> None:
    """Apply a field-level change to the applications table.

    Only allows changes to known safe fields.
    """
    SAFE_ENTITY_FIELDS = {
        "company_name", "brn", "country", "sector", "entity_type",
        "ownership_structure",
    }
    if field_name not in SAFE_ENTITY_FIELDS:
        logger.warning("Blocked unsafe field change attempt: %s on application %s", field_name, application_id)
        return

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        f"UPDATE applications SET {field_name} = ?, updated_at = ? WHERE id = ?",
        (new_value, now, application_id),
    )


# ============================================================================
# Profile Versioning
# ============================================================================

def _get_current_profile_version_id(db, application_id: str) -> Optional[str]:
    """Get the ID of the current (most recent) profile version for an application."""
    try:
        row = db.execute(
            """SELECT id FROM entity_profile_versions
               WHERE application_id = ? AND is_current = ?
               ORDER BY version_number DESC LIMIT 1""",
            (application_id, True),
        ).fetchone()
        return row["id"] if row else None
    except Exception:
        return None


def _create_profile_version(
    db,
    application_id: str,
    request_id: str,
    before_snapshot: Dict,
    after_snapshot: Dict,
    user: Dict,
) -> str:
    """Create a new entity profile version record."""
    version_id = generate_profile_version_id()
    now = datetime.now(timezone.utc).isoformat()

    # Get next version number
    try:
        row = db.execute(
            "SELECT MAX(version_number) as max_v FROM entity_profile_versions WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        next_version = (row["max_v"] or 0) + 1 if row else 1
    except Exception:
        next_version = 1

    # Mark all existing versions as not current
    # Use parameterized boolean for PostgreSQL BOOLEAN / SQLite INTEGER compatibility
    try:
        db.execute(
            "UPDATE entity_profile_versions SET is_current = ? WHERE application_id = ?",
            (False, application_id),
        )
    except Exception:
        pass

    db.execute(
        """INSERT INTO entity_profile_versions
           (id, application_id, version_number, is_current,
            profile_snapshot, change_request_id,
            created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            version_id, application_id, next_version,
            True,
            json.dumps(after_snapshot, default=str),
            request_id,
            user.get("sub"),
            now,
        ),
    )

    return version_id


def get_profile_versions(db, application_id: str) -> List[Dict]:
    """Get all profile versions for an application, newest first."""
    try:
        rows = db.execute(
            """SELECT id, application_id, version_number, is_current,
                      change_request_id, created_by, created_at
               FROM entity_profile_versions
               WHERE application_id = ?
               ORDER BY version_number DESC""",
            (application_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_profile_version_detail(db, version_id: str) -> Optional[Dict]:
    """Get a specific profile version with full snapshot."""
    try:
        row = db.execute(
            "SELECT * FROM entity_profile_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        if isinstance(result.get("profile_snapshot"), str):
            try:
                result["profile_snapshot"] = json.loads(result["profile_snapshot"])
            except (json.JSONDecodeError, TypeError):
                pass
        return result
    except Exception:
        return None


# ============================================================================
# Query / List Operations
# ============================================================================

def list_change_alerts(
    db,
    application_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict]:
    """List change alerts with optional filters."""
    query = "SELECT * FROM change_alerts WHERE 1=1"
    params = []

    if application_id:
        query += " AND application_id = ?"
        params.append(application_id)
    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    try:
        rows = db.execute(query, tuple(params)).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("detected_changes"), str):
                try:
                    d["detected_changes"] = json.loads(d["detected_changes"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if isinstance(d.get("source_payload"), str):
                try:
                    d["source_payload"] = json.loads(d["source_payload"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(d)
        return results
    except Exception as e:
        logger.error("Failed to list change alerts: %s", e)
        return []


def list_change_requests(
    db,
    application_id: Optional[str] = None,
    status: Optional[str] = None,
    materiality: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict]:
    """List change requests with optional filters."""
    query = "SELECT * FROM change_requests WHERE 1=1"
    params = []

    if application_id:
        query += " AND application_id = ?"
        params.append(application_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    if materiality:
        query += " AND materiality = ?"
        params.append(materiality)
    if source:
        query += " AND source = ?"
        params.append(source)

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    try:
        rows = db.execute(query, tuple(params)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Failed to list change requests: %s", e)
        return []


def get_change_request_detail(db, request_id: str) -> Optional[Dict]:
    """Get full details of a change request including items and reviews."""
    try:
        row = db.execute(
            "SELECT * FROM change_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if not row:
            return None

        result = dict(row)

        # Get items
        items = db.execute(
            "SELECT * FROM change_request_items WHERE request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
        result["items"] = []
        for item in items:
            item_dict = dict(item)
            if isinstance(item_dict.get("person_snapshot"), str):
                try:
                    item_dict["person_snapshot"] = json.loads(item_dict["person_snapshot"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result["items"].append(item_dict)

        # Get documents
        try:
            docs = db.execute(
                "SELECT * FROM change_request_documents WHERE request_id = ? ORDER BY uploaded_at",
                (request_id,),
            ).fetchall()
            result["documents"] = [dict(d) for d in docs]
        except Exception:
            result["documents"] = []

        # Get reviews
        try:
            reviews = db.execute(
                "SELECT * FROM change_request_reviews WHERE request_id = ? ORDER BY reviewed_at",
                (request_id,),
            ).fetchall()
            result["reviews"] = [dict(r) for r in reviews]
        except Exception:
            result["reviews"] = []

        return result
    except Exception as e:
        logger.error("Failed to get change request detail: %s", e)
        return None


def get_change_alert_detail(db, alert_id: str) -> Optional[Dict]:
    """Get full details of a change alert."""
    try:
        row = db.execute(
            "SELECT * FROM change_alerts WHERE id = ?", (alert_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        if isinstance(result.get("detected_changes"), str):
            try:
                result["detected_changes"] = json.loads(result["detected_changes"])
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(result.get("source_payload"), str):
            try:
                result["source_payload"] = json.loads(result["source_payload"])
            except (json.JSONDecodeError, TypeError):
                pass
        return result
    except Exception as e:
        logger.error("Failed to get change alert detail: %s", e)
        return None


# ============================================================================
# Document Attachment
# ============================================================================

def attach_document_to_request(
    db,
    request_id: str,
    doc_name: str,
    doc_type: str,
    file_path: str,
    item_id: Optional[str] = None,
    uploaded_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Attach a supporting document to a change request.

    Uses a separate linking table (change_request_documents) to avoid polluting
    the main documents table with non-application documents.
    """
    doc_id = secrets.token_hex(8)
    now = datetime.now(timezone.utc).isoformat()

    db.execute(
        """INSERT INTO change_request_documents
           (id, request_id, item_id, doc_name, doc_type, file_path,
            uploaded_by, uploaded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (doc_id, request_id, item_id, doc_name, doc_type, file_path, uploaded_by, now),
    )
    db.commit()

    return {
        "id": doc_id,
        "request_id": request_id,
        "item_id": item_id,
        "doc_name": doc_name,
        "doc_type": doc_type,
        "file_path": file_path,
        "uploaded_by": uploaded_by,
        "uploaded_at": now,
    }


# ============================================================================
# Statistics / Dashboard
# ============================================================================

def get_change_management_stats(db) -> Dict[str, Any]:
    """Get summary statistics for change management dashboard."""
    stats = {
        "alerts": {"total": 0, "new": 0, "under_review": 0, "escalated": 0, "by_status": {}},
        "requests": {"total": 0, "draft": 0, "submitted": 0, "approval_pending": 0,
                      "approved": 0, "implemented": 0, "by_status": {}},
    }
    try:
        # Alert counts — single GROUP BY query
        for row in db.execute(
            "SELECT status, COUNT(*) as cnt FROM change_alerts GROUP BY status"
        ).fetchall():
            s = row["status"]
            stats["alerts"]["by_status"][s] = row["cnt"]
            stats["alerts"]["total"] += row["cnt"]
            if s in stats["alerts"]:
                stats["alerts"][s] = row["cnt"]

        # Request counts — single GROUP BY query
        for row in db.execute(
            "SELECT status, COUNT(*) as cnt FROM change_requests GROUP BY status"
        ).fetchall():
            s = row["status"]
            stats["requests"]["by_status"][s] = row["cnt"]
            stats["requests"]["total"] += row["cnt"]
            if s in stats["requests"]:
                stats["requests"][s] = row["cnt"]

    except Exception as e:
        logger.error("Failed to get change management stats: %s", e)

    return stats


# ============================================================================
# Internal Helpers
# ============================================================================

def _highest_materiality(tiers: List[str]) -> str:
    """Return the highest (most impactful) materiality tier from a list."""
    priority = {"tier1": 1, "tier2": 2, "tier3": 3}
    if not tiers:
        return "tier2"
    return min(tiers, key=lambda t: priority.get(t, 2))


def _safe_snapshot_summary(snapshot: Dict) -> Dict:
    """Return a safe summary of a profile snapshot for audit logging.

    Avoids storing full PII in audit_log.
    """
    if not snapshot:
        return {}
    return {
        "company_name": snapshot.get("company_name"),
        "country": snapshot.get("country"),
        "sector": snapshot.get("sector"),
        "entity_type": snapshot.get("entity_type"),
        "risk_score": snapshot.get("risk_score"),
        "risk_level": snapshot.get("risk_level"),
        "directors_count": len(snapshot.get("directors", [])),
        "ubos_count": len(snapshot.get("ubos", [])),
        "intermediaries_count": len(snapshot.get("intermediaries", [])),
        "snapshot_at": snapshot.get("snapshot_at"),
    }
