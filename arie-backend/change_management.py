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

# --- Valid Change Types (shared whitelist for create, convert, and implement) ---
VALID_CHANGE_TYPES = frozenset({
    # Entity-level field changes
    "company_details",
    "address_change",
    "business_activity_change",
    "contact_detail_update",
    "other",
    # Director changes
    "director_add",
    "director_remove",
    "director_change",
    # UBO changes
    "ubo_add",
    "ubo_remove",
    "ubo_change",
    # Intermediary changes
    "intermediary_add",
    "intermediary_remove",
    "intermediary_change",
})


def validate_change_types(items: list) -> tuple:
    """Validate that all items have a supported change_type.

    Returns (valid, error_message).
    """
    for idx, item in enumerate(items):
        ct = item.get("change_type", "")
        if ct not in VALID_CHANGE_TYPES:
            return False, (
                f"Unsupported change_type '{ct}' in item {idx}. "
                f"Valid types: {', '.join(sorted(VALID_CHANGE_TYPES))}"
            )
    return True, ""

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
    "create_request": ("admin", "sco", "co", "analyst", "client"),
    "submit_request": ("admin", "sco", "co", "analyst", "client"),
    "triage_request": ("admin", "sco", "co", "analyst"),
    "request_info": ("admin", "sco", "co", "analyst"),
    "review_request": ("admin", "sco", "co"),
    "reject_request": ("admin", "sco", "co"),
    "approve_tier3": ("admin", "sco", "co"),
    "approve_tier2": ("admin", "sco", "co"),
    "approve_tier1": ("admin", "sco"),
    "implement_change": ("admin", "sco"),
    "upload_document": ("admin", "sco", "co", "analyst"),
    "create_alert": ("admin", "sco", "co", "analyst"),
    "review_alert": ("admin", "sco", "co"),
    "dismiss_alert": ("admin", "sco", "co"),
    "convert_alert": ("admin", "sco", "co"),
}

# Terminal/final statuses that analyst must NOT be able to set via PATCH
ANALYST_BLOCKED_STATUSES = frozenset({
    "approved", "rejected", "partially_approved",
    "implemented", "cancelled", "superseded",
})

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
    """Convert detected_changes dict from alert into change request items.

    Alert types (e.g. legal_name_change, shareholding_change) may not match
    the VALID_CHANGE_TYPES whitelist used for request items.  When an alert
    type is not directly in the whitelist, we fall back to ``"other"`` so that
    auto-derived items pass create-time validation while still preserving the
    original materiality tier from the alert type.
    """
    items = []
    if not detected_changes:
        return items
    # Map alert_type to a valid change_type; fall back to "other" for unknown
    change_type = alert_type if alert_type in VALID_CHANGE_TYPES else "other"
    for field, delta in detected_changes.items():
        old_value = delta.get("old") if isinstance(delta, dict) else None
        new_value = delta.get("new") if isinstance(delta, dict) else str(delta)
        items.append({
            "change_type": change_type,
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

    # --- Defence-in-depth: portal ownership enforcement ---
    # When the request originates from the portal, verify that the
    # authenticated client actually owns the target application.
    # The handler layer SHOULD already enforce this, but this guard
    # provides a second barrier against cross-tenant mutations.
    if source_channel == "portal":
        client_id = user.get("sub")
        app_row = db.execute(
            "SELECT client_id FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        if not app_row:
            raise PermissionError(
                "Application not found. Cannot create portal change request."
            )
        if app_row["client_id"] != client_id:
            logger.warning(
                "Defence-in-depth: portal CR blocked | client=%s app=%s owner=%s",
                client_id, application_id, app_row["client_id"],
            )
            # Audit the denial via the caller-supplied audit function
            if log_audit_fn:
                try:
                    log_audit_fn(
                        user, "portal_cr_denied_not_owner",
                        application_id,
                        json.dumps({
                            "reason": "defence_in_depth",
                            "client_id": client_id,
                            "attempted_application_id": application_id,
                            "actual_owner": app_row["client_id"],
                        }),
                        db=db,
                    )
                except Exception:
                    logger.exception(
                        "Failed to write denial audit in defence-in-depth"
                    )
            raise PermissionError(
                "You do not own this application. Portal change request denied."
            )

    # --- Reject zero-item creates (prevents orphan requests with no actionable content) ---
    if not items:
        raise ValueError(
            "At least one change item is required. "
            "Provide an 'items' array with change_type, field_name, and new_value."
        )

    # --- Validate change_type values before creating anything ---
    valid, ct_err = validate_change_types(items)
    if not valid:
        raise ValueError(ct_err)

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
        "SELECT * FROM change_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if not row:
        return False, f"Request not found: {request_id}"

    request = dict(row)
    current_status = request["status"]
    valid, err = validate_request_transition(current_status, new_status)
    if not valid:
        return False, err

    # --- Analyst guard: block terminal/final statuses ---
    user_role = user.get("role", "")
    if user_role == "analyst" and new_status in ANALYST_BLOCKED_STATUSES:
        return False, (
            f"Role 'analyst' not permitted to set status '{new_status}'. "
            f"Analysts may only move requests through preparatory statuses."
        )

    # Role-based approval checks + precondition gate (PR-CM-APPROVAL-PRECONDITIONS-1).
    # The PATCH→approved path is gated identically to the dedicated approve endpoint;
    # overrides are only available through the approve endpoint, so this path blocks
    # outright on any outstanding blocker.
    materiality = request["materiality"]
    if new_status in ("approved", "partially_approved"):
        action = f"approve_{materiality}"
        allowed, role_err = check_role_permission(user.get("role", ""), action)
        if not allowed:
            return False, role_err
        blockers = approval_blockers(db, request, approver_user=user)
        if blockers:
            codes = ", ".join(b["code"] for b in blockers)
            if log_audit_fn:
                log_audit_fn(
                    user, "CM Approval Blocked", request_id,
                    f"Approval blocked by: {codes}", db=db,
                )
            return False, f"Approval blocked by preconditions: {codes}"

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
# Approval Preconditions (PR-CM-APPROVAL-PRECONDITIONS-1)
# ============================================================================

# Materiality tiers that require maker/checker (creator != approver).
# Non-waivable: segregation of duties has no break-glass.
MAKER_CHECKER_TIERS = frozenset({"tier1", "tier2"})

# Roles permitted to override (waivable) precondition blockers.
_OVERRIDE_ROLES = ("admin", "sco")

_RISK_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
VALID_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", "VERY_HIGH"})

# CR statuses on which precondition results may NOT be (re)recorded — the
# decision is already made or the request is closed.
_PRECONDITION_LOCKED_STATUSES = frozenset({
    "approved", "partially_approved", "rejected", "implemented", "cancelled", "superseded",
})


def _normalize_risk_level(v) -> Optional[str]:
    """Normalize a risk level to the canonical set, or None if unrecognized."""
    if not v:
        return None
    n = str(v).strip().upper().replace(" ", "_")
    return n if n in VALID_RISK_LEVELS else None


def _flag_true(v) -> bool:
    """Interpret a stored boolean flag (bool / sqlite int / pg bool / text)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("1", "true", "t", "yes")


def _risk_increased(pre, post):
    if not pre or not post:
        return None
    return _RISK_ORDER.get(str(post).upper(), 0) > _RISK_ORDER.get(str(pre).upper(), 0)


def _load_precondition_results(request: Dict) -> Dict[str, Any]:
    raw = request.get("precondition_results")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _request_content_signature(db, request_id: str) -> str:
    """Stable signature of a request's change items.

    Used for stale-clearance detection: a recorded precondition is invalidated
    only when the request's *content* (items) changes — NOT when its status
    transitions (status changes bump updated_at, which must not stale a result).
    """
    try:
        rows = db.execute(
            """SELECT change_type, field_name, old_value, new_value, person_action, person_snapshot
               FROM change_request_items WHERE request_id = ? ORDER BY id""",
            (request_id,),
        ).fetchall()
    except Exception:
        return ""
    import hashlib
    parts = []
    for r in rows:
        d = dict(r)
        parts.append("|".join(str(d.get(k) if d.get(k) is not None else "") for k in
                              ("change_type", "field_name", "old_value", "new_value",
                               "person_action", "person_snapshot")))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _app_screening_snapshot(db, application_id: str) -> Dict[str, Any]:
    """Capture EXISTING (persisted) screening evidence for an application.

    PR-2 references existing screening data — it does NOT run a fresh screen
    (that is PR-4). unresolved_match: True/False when determinable, else None.
    """
    snap = {"screening_ref": None, "screened_at": None, "unresolved_match": None}
    try:
        row = db.execute(
            "SELECT prescreening_data FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        if not row:
            return snap
        raw = row["prescreening_data"]
        data = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        report = (data or {}).get("screening_report") or {}
        if not report:
            return snap
        snap["screened_at"] = report.get("screened_at") or report.get("screening_date")
        snap["screening_ref"] = report.get("report_id") or report.get("id") or "application:screening_report"
        total_hits = report.get("total_hits")
        sanctions = report.get("sanctions") if isinstance(report.get("sanctions"), dict) else {}
        matched = sanctions.get("matched")
        unresolved = report.get("unresolved_matches")
        status = str(report.get("status") or report.get("result") or "").strip().lower()
        adverse_status = status in {"match", "hit", "unresolved", "escalate", "fail", "failed"}
        clean_status = status in {"clear", "cleared", "no_match", "completed_clear", "clean", "pass", "passed"}
        # Adverse signal → unresolved match.
        if (matched is True or (isinstance(total_hits, int) and total_hits > 0)
                or (isinstance(unresolved, int) and unresolved > 0) or adverse_status):
            snap["unresolved_match"] = True
        # Explicit clean signal → no unresolved match.
        elif (matched is False or (isinstance(total_hits, int) and total_hits == 0)
                or (isinstance(unresolved, int) and unresolved == 0) or clean_status):
            snap["unresolved_match"] = False
        else:
            # Report present but NO determinate clean/adverse signal → indeterminate.
            # Absence of match fields is NOT treated as clean (fail-safe).
            snap["unresolved_match"] = None
    except Exception:
        return snap
    return snap


def _app_risk_snapshot(db, application_id: str) -> Dict[str, Any]:
    try:
        row = db.execute(
            "SELECT risk_level, risk_score, risk_computed_at FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        if not row:
            return {}
        d = dict(row)
        return {
            "risk_level": d.get("risk_level"),
            "risk_score": d.get("risk_score"),
            "risk_computed_at": d.get("risk_computed_at"),
        }
    except Exception:
        return {}


def record_precondition_result(
    db, request_id, kind, user, result=None, note=None, log_audit_fn=None,
) -> Tuple[bool, str]:
    """Record an evidence-backed precondition result (screening|risk) on a CR.

    Does NOT run screening or recompute risk (that is PR-4). It references the
    existing persisted screening/risk data, the reviewer, and a content
    signature (for stale detection). Returns (ok, error_message).
    """
    kind = str(kind or "").strip().lower()
    if kind not in ("screening", "risk"):
        return False, f"Unsupported precondition kind: {kind}"
    allowed, role_err = check_role_permission(user.get("role", ""), "review_request")
    if not allowed:
        return False, role_err
    row = db.execute("SELECT * FROM change_requests WHERE id = ?", (request_id,)).fetchone()
    if not row:
        return False, f"Request not found: {request_id}"
    request = dict(row)
    # Do not allow recording precondition results on terminal/decided requests.
    if request.get("status") in _PRECONDITION_LOCKED_STATUSES:
        return False, (
            f"precondition_locked: cannot record a precondition result on a "
            f"'{request.get('status')}' request"
        )
    results = _load_precondition_results(request)
    now = datetime.now(timezone.utc).isoformat()
    sig = _request_content_signature(db, request_id)
    extra = result if isinstance(result, dict) else {}

    entry = {
        "result": "recorded",
        "recorded_by": user.get("sub"),
        "recorded_by_name": user.get("name"),
        "recorded_at": now,
        "note": ((note or extra.get("note") or "").strip() or None),
        "content_sig": sig,
    }
    if kind == "screening":
        snap = _app_screening_snapshot(db, request["application_id"])
        ref = extra.get("screening_ref") or snap.get("screening_ref")
        screened_at = extra.get("screened_at") or snap.get("screened_at")
        unresolved = extra.get("unresolved_match") if "unresolved_match" in extra else snap.get("unresolved_match")
        # Evidence-backed only: require a screening reference AND a determinate
        # match status. A blank "screening reviewed" marker is rejected.
        if not ref or unresolved is None:
            return False, (
                "screening_result_evidence_missing: a screening result requires a persisted "
                "screening report (or explicit screening_ref + unresolved_match evidence). "
                "A screening result cannot be recorded without underlying evidence."
            )
        entry["screening_ref"] = ref
        entry["screened_at"] = screened_at
        entry["unresolved_match"] = bool(unresolved)
    else:
        snap = _app_risk_snapshot(db, request["application_id"])
        risk_level_raw = extra.get("risk_level") or snap.get("risk_level")
        if not risk_level_raw:
            return False, (
                "risk_result_evidence_missing: a risk result requires a risk level "
                "(from the application's computed risk or an explicit risk_level)."
            )
        risk_level = _normalize_risk_level(risk_level_raw)
        if not risk_level:
            return False, (
                "risk_result_invalid_level: risk level must be one of "
                "LOW, MEDIUM, HIGH, VERY_HIGH (got: " + str(risk_level_raw) + ")"
            )
        entry["risk_level"] = risk_level
        entry["risk_computed_at"] = extra.get("risk_computed_at") or snap.get("risk_computed_at")
        entry["risk_increased"] = (
            extra.get("risk_increased") if "risk_increased" in extra
            else _risk_increased(request.get("pre_change_risk_level"), risk_level)
        )

    results[kind] = entry
    # NOTE: deliberately does NOT bump updated_at — staleness is keyed on the
    # request content signature, not on status-transition timestamps.
    db.execute(
        "UPDATE change_requests SET precondition_results = ? WHERE id = ?",
        (json.dumps(results), request_id),
    )
    db.commit()

    if log_audit_fn:
        log_audit_fn(
            user, "CM Precondition Recorded", request_id,
            f"kind={kind}; recorded_by={user.get('name', user.get('sub'))}; note={entry.get('note') or 'n/a'}",
            db=db,
        )
    return True, ""


def _blocker(code, label, next_action, waivable):
    return {"code": code, "label": label, "next_action": next_action, "waivable": waivable}


def approval_blockers(db, request: Dict, approver_user: Optional[Dict] = None) -> List[Dict]:
    """Return structured blockers preventing approval of a change request."""
    blockers: List[Dict] = []
    materiality = request.get("materiality")
    results = _load_precondition_results(request)
    sig = _request_content_signature(db, request["id"])

    # Maker/checker — non-waivable for tier1/tier2.
    if materiality in MAKER_CHECKER_TIERS and approver_user is not None:
        if approver_user.get("sub") and approver_user.get("sub") == request.get("created_by"):
            blockers.append(_blocker(
                "maker_checker_same_user",
                "Maker/checker: the request creator cannot approve their own change",
                "A different officer (SCO/Admin) must approve",
                False,
            ))

    # Screening precondition.
    if _flag_true(request.get("screening_required")):
        sc = results.get("screening")
        if not sc or sc.get("result") != "recorded":
            # No evidence-backed screening result recorded — non-waivable
            # (you cannot override a screening that was never performed).
            blockers.append(_blocker(
                "screening_required_uncleared",
                "Screening review required",
                "Record an evidence-backed screening result for this change",
                False,
            ))
        elif sc.get("content_sig") != sig:
            blockers.append(_blocker(
                "screening_clearance_stale",
                "Screening result is stale (the request changed after it was recorded)",
                "Re-record the screening result",
                sc.get("unresolved_match") is False,  # waivable only if the prior result was clean
            ))
        elif sc.get("unresolved_match") is True:
            blockers.append(_blocker(
                "screening_unresolved_match",
                "Screening shows an unresolved match — cannot approve",
                "Resolve/disposition the screening match before approval",
                False,
            ))
        elif sc.get("unresolved_match") is not False:
            blockers.append(_blocker(
                "screening_result_indeterminate",
                "Screening result is indeterminate — a clean result could not be confirmed",
                "Record a determinate screening result",
                False,
            ))

    # Risk precondition.
    if _flag_true(request.get("risk_review_required")):
        rk = results.get("risk")
        if not rk or rk.get("result") != "recorded":
            blockers.append(_blocker(
                "risk_review_required_uncleared",
                "Risk review required",
                "Record the risk review result for this change",
                True,
            ))
        elif rk.get("content_sig") != sig:
            blockers.append(_blocker(
                "risk_clearance_stale",
                "Risk review result is stale (the request changed after it was recorded)",
                "Re-record the risk review result",
                True,
            ))
    return blockers


def evaluate_approval(db, request: Dict) -> Dict[str, Any]:
    """Neutral approval *readiness* for UI/detail.

    Excludes approver-specific maker/checker (no current user here), so this
    reports whether PRECONDITIONS are met — NOT whether a given user may approve.
    The field is intentionally named ``preconditions_met`` (not ``can_approve``)
    so the UI never tells a creator they can approve their own tier1/tier2 change.
    """
    blockers = approval_blockers(db, request, approver_user=None)
    notes = []
    if request.get("materiality") in MAKER_CHECKER_TIERS:
        notes.append({
            "code": "maker_checker_required",
            "label": "Approval requires a different officer than the creator (maker/checker)",
        })
    return {"preconditions_met": len(blockers) == 0, "blockers": blockers, "approval_notes": notes}


def _apply_overrides(blockers: List[Dict], override_codes, override_reason, user: Dict):
    """Return (remaining_blockers, applied_codes, error_message).

    SCO/Admin only; reason mandatory; non-waivable blockers can never be overridden.
    """
    codes = set(override_codes or [])
    if not codes:
        return blockers, [], ""
    if user.get("role", "") not in _OVERRIDE_ROLES:
        return blockers, [], f"Role '{user.get('role','')}' may not override approval blockers"
    if not (override_reason and str(override_reason).strip()):
        return blockers, [], "override_reason is required to override an approval blocker"
    remaining, applied = [], []
    for b in blockers:
        if b["code"] in codes and b.get("waivable"):
            applied.append(b["code"])
        else:
            remaining.append(b)
    return remaining, applied, ""


# ============================================================================
# Approval & Implementation
# ============================================================================

def approve_change_request(
    db,
    request_id: str,
    user: Dict,
    decision_notes: Optional[str] = None,
    log_audit_fn=None,
    override_codes=None,
    override_reason: Optional[str] = None,
) -> Tuple[bool, str]:
    """Approve a change request (does NOT implement — separate step).

    Enforces approval preconditions (PR-CM-APPROVAL-PRECONDITIONS-1): maker/checker
    (non-waivable for tier1/tier2) and screening/risk precondition results. SCO/Admin
    may override waivable blockers with a mandatory reason.

    Returns (success, error_message).
    """
    row = db.execute(
        "SELECT * FROM change_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if not row:
        return False, f"Request not found: {request_id}"

    request = dict(row)
    materiality = request["materiality"]
    action = f"approve_{materiality}"
    allowed, role_err = check_role_permission(user.get("role", ""), action)
    if not allowed:
        return False, role_err

    valid, err = validate_request_transition(request["status"], "approved")
    if not valid:
        return False, err

    # --- Approval precondition gate (maker/checker + screening/risk) ---
    blockers = approval_blockers(db, request, approver_user=user)
    remaining, applied, ov_err = _apply_overrides(blockers, override_codes, override_reason, user)
    if ov_err:
        return False, ov_err
    if remaining:
        codes = ", ".join(b["code"] for b in remaining)
        if log_audit_fn:
            log_audit_fn(
                user, "CM Approval Blocked", request_id,
                f"Approval blocked by: {codes}", db=db,
            )
        return False, f"Approval blocked by preconditions: {codes}"
    if applied and log_audit_fn:
        log_audit_fn(
            user, "CM Approval Override", request_id,
            f"Overrode {', '.join(applied)}; reason: {override_reason}", db=db,
        )

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

    if not items:
        return False, f"No change items found for request {request_id}", None

    try:
        # Apply changes — track what was applied vs skipped
        applied_details = []
        skipped_details = []

        for item in items:
            item = dict(item)
            applied, detail = _apply_change_item(db, application_id, item)
            if applied:
                applied_details.append(detail)
            else:
                skipped_details.append(detail)

        # Fail if NO items were actually applied to live tables
        if not applied_details:
            raise ValueError(
                f"No items could be applied to live profile. "
                f"Skipped: {'; '.join(skipped_details)}"
            )

        # Snapshot after applying — captures the post-change live profile
        after_snapshot = snapshot_entity_profile(db, application_id)

        # Create new profile version (errors propagate — no silent swallow)
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

        # Single atomic commit — live update, profile version, and status
        # all succeed or all fail together
        db.commit()

        # Trigger risk recomputation if needed (after commit)
        if request.get("risk_review_required") and recompute_risk_fn:
            try:
                recompute_risk_fn(db, application_id, f"Change request {request_id} implemented", user, log_audit_fn)
            except Exception as e:
                logger.warning("Risk recomputation after change %s failed: %s", request_id, e)

        audit_msg = f"Profile version: {new_version_id}. Items applied: {len(applied_details)}"
        if skipped_details:
            audit_msg += f". Items skipped: {len(skipped_details)} ({'; '.join(skipped_details)})"

        if log_audit_fn:
            log_audit_fn(
                user, "Change Request Implemented", request_id,
                audit_msg,
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


def _apply_change_item(db, application_id: str, item: Dict) -> Tuple[bool, str]:
    """Apply a single change request item to the live database.

    Handles both field-level changes and person (director/UBO/intermediary) changes.

    Returns (applied, detail) where applied is True if the item was
    successfully applied to a live table, and detail is a human-readable
    note (empty on success, descriptive on skip/failure).
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
        return True, f"director change applied ({person_action})"
    elif change_type.startswith("ubo_"):
        _apply_person_change(db, application_id, "ubos", person_action, person_snapshot, field_name, new_value)
        return True, f"ubo change applied ({person_action})"
    elif change_type.startswith("intermediary_"):
        _apply_intermediary_change(db, application_id, person_action, person_snapshot, field_name, new_value)
        return True, f"intermediary change applied ({person_action})"
    elif change_type in ("company_details", "address_change", "business_activity_change",
                         "licensing_change", "contact_update", "contact_detail_update", "other"):
        # Field-level changes on applications table
        if field_name and new_value is not None:
            _apply_field_change(db, application_id, field_name, new_value)
            return True, f"field '{field_name}' updated"
        else:
            return False, f"skipped: field_name={field_name!r}, new_value={'None' if new_value is None else repr(new_value)}"
    else:
        return False, f"skipped: unrecognised change_type={change_type!r}"


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


SAFE_ENTITY_FIELDS = {
    "company_name", "brn", "country", "sector", "entity_type",
    "ownership_structure",
}


def _apply_field_change(db, application_id: str, field_name: str, new_value: str) -> None:
    """Apply a field-level change to the applications table.

    Only allows changes to known safe fields.
    Raises ValueError for unsupported fields so the caller can fail or
    record an explicit audit note — never silently claims success.
    """
    if field_name not in SAFE_ENTITY_FIELDS:
        raise ValueError(
            f"Unsupported/unsafe field '{field_name}' on application {application_id}. "
            f"Allowed fields: {', '.join(sorted(SAFE_ENTITY_FIELDS))}"
        )

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        f"UPDATE applications SET {field_name} = ?, updated_at = ?, inputs_updated_at = ? WHERE id = ?",
        (new_value, now, now, application_id),
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
    """Create a new entity profile version record.

    All SQL operations run within the caller's transaction.  Errors are
    propagated — never swallowed — so that the caller can roll back the
    entire transaction (including any live-profile mutations that preceded
    this call).
    """
    version_id = generate_profile_version_id()
    now = datetime.now(timezone.utc).isoformat()

    # Get next version number — errors propagate to caller for full rollback
    row = db.execute(
        "SELECT MAX(version_number) as max_v FROM entity_profile_versions WHERE application_id = ?",
        (application_id,),
    ).fetchone()
    next_version = (row["max_v"] or 0) + 1 if row else 1

    # Mark all existing versions as not current
    # Use parameterized boolean for PostgreSQL BOOLEAN / SQLite INTEGER compatibility
    db.execute(
        "UPDATE entity_profile_versions SET is_current = ? WHERE application_id = ?",
        (False, application_id),
    )

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

def _change_request_downstream_obligations(request: Dict[str, Any]) -> List[Dict[str, Any]]:
    obligations: List[Dict[str, Any]] = []
    if request.get("screening_required"):
        obligations.append({"code": "screening_required", "label": "Screening review required"})
    if request.get("risk_review_required"):
        obligations.append({"code": "risk_review_required", "label": "Risk review required"})
    if request.get("edd_review_required"):
        obligations.append({"code": "edd_review_required", "label": "EDD review may be required"})
    if request.get("memo_addendum_hook"):
        obligations.append({"code": "memo_addendum_required", "label": "Memo addendum required"})
    if request.get("periodic_review_acceleration_hook"):
        obligations.append({"code": "periodic_review_acceleration", "label": "Periodic review acceleration hook"})

    if obligations:
        return obligations

    materiality = request.get("materiality")
    if materiality == "tier1":
        return [{"code": "tier1_advisory", "label": "Tier 1 structural change — downstream compliance review may be required", "advisory": True}]
    if materiality == "tier2":
        return [{"code": "tier2_advisory", "label": "Tier 2 operational change — compliance review may be required", "advisory": True}]
    if materiality == "tier3":
        return [{"code": "tier3_advisory", "label": "Tier 3 administrative change — fast-track review may be available", "advisory": True}]
    return []


def _enrich_change_request_records(db, requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not requests:
        return requests

    app_ids = sorted({req.get("application_id") for req in requests if req.get("application_id")})
    request_ids = [req.get("id") for req in requests if req.get("id")]

    app_lookup: Dict[str, Dict[str, Any]] = {}
    if app_ids:
        placeholders = ",".join(["?"] * len(app_ids))
        app_rows = db.execute(
            f"SELECT id, ref, company_name FROM applications WHERE id IN ({placeholders})",
            tuple(app_ids),
        ).fetchall()
        app_lookup = {row["id"]: dict(row) for row in app_rows}

    item_counts: Dict[str, int] = {}
    preview_items: Dict[str, List[Dict[str, Any]]] = {}
    if request_ids:
        placeholders = ",".join(["?"] * len(request_ids))
        item_rows = db.execute(
            f"""SELECT request_id, COUNT(*) AS item_count
                FROM change_request_items
                WHERE request_id IN ({placeholders})
                GROUP BY request_id""",
            tuple(request_ids),
        ).fetchall()
        item_counts = {
            row["request_id"]: int(row["item_count"] or 0)
            for row in item_rows
        }
        preview_rows = db.execute(
            f"""SELECT id, request_id, change_type, field_name, old_value, new_value, materiality,
                       person_action, person_snapshot, created_at
                FROM change_request_items
                WHERE request_id IN ({placeholders})
                ORDER BY created_at ASC, id ASC""",
            tuple(request_ids),
        ).fetchall()
        for row in preview_rows:
            bucket = preview_items.setdefault(row["request_id"], [])
            if len(bucket) < 3:
                bucket.append(dict(row))

    enriched: List[Dict[str, Any]] = []
    for req in requests:
        record = dict(req)
        app_meta = app_lookup.get(record.get("application_id")) or {}
        record["application_ref"] = app_meta.get("ref")
        record["company_name"] = app_meta.get("company_name")
        record["changed_fields_count"] = item_counts.get(record.get("id"), len(record.get("items") or []))
        record["preview_items"] = preview_items.get(record.get("id"), [])
        record["downstream_obligations"] = _change_request_downstream_obligations(record)
        enriched.append(record)
    return enriched

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
        return _enrich_change_request_records(db, [dict(r) for r in rows])
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

        result["changed_fields_count"] = len(result["items"])
        result["downstream_obligations"] = _change_request_downstream_obligations(result)
        app_meta = db.execute(
            "SELECT ref, company_name FROM applications WHERE id = ?",
            (result.get("application_id"),),
        ).fetchone()
        if app_meta:
            result["application_ref"] = app_meta["ref"]
            result["company_name"] = app_meta["company_name"]

        # Approval readiness (PR-CM-APPROVAL-PRECONDITIONS-1) for officer UI.
        try:
            result["approval"] = evaluate_approval(db, result)
        except Exception:
            result["approval"] = {"can_approve": None, "blockers": [], "approval_notes": []}

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
# Approved-Profile Locking & Auto-Draft (PR-CM-LOCK-AND-AUTO-DRAFT-1)
# ============================================================================

# Application statuses in which the entity profile is considered approved and
# locked. Material edits to records in these states must NOT mutate live data;
# they are staged as a Change Request instead.
LOCKED_PROFILE_STATUSES = frozenset({
    "approved",
})

# Non-terminal Change Request statuses. An attempted edit that matches an
# already-open request of one of these statuses is reused rather than spawning
# a duplicate (idempotency). Terminal statuses (approved/rejected/implemented/
# cancelled/superseded) are intentionally excluded so a fresh edit after a
# closed request starts a new draft.
OPEN_CHANGE_REQUEST_STATUSES = frozenset({
    "draft",
    "submitted",
    "triage_in_progress",
    "pending_information",
    "ready_for_review",
    "screening_in_progress",
    "risk_review_required",
    "approval_pending",
})

# Application-table fields that remain directly editable on a locked profile
# (cosmetic / contact-only). Everything else is treated as material and routed
# through Change Management.
MINOR_DIRECT_EDIT_FIELDS = frozenset({
    "website",
    "contact_email",
    "contact_phone",
    "phone",
    "email",
})

# Core entity-identity fields that must route through Change Management on a
# locked profile. This is a clearer-named alias of the pre-existing
# SAFE_ENTITY_FIELDS (defined earlier in this module from the apply side); a
# change to any of these on an approved profile is staged as a CR regardless of
# the officer-correction heuristic tier (e.g. BRN, which the heuristic tiers as
# tier3, is protected here).
LOCKED_ENTITY_FIELDS = SAFE_ENTITY_FIELDS

# Map an application field to the canonical CM change_type used for items.
_FIELD_TO_CHANGE_TYPE = {
    "company_name": "company_details",
    "brn": "company_details",
    "entity_type": "company_details",
    "ownership_structure": "company_details",
    "country": "address_change",
    "sector": "business_activity_change",
}


def is_profile_locked(status: Optional[str]) -> bool:
    """Return True if an application status represents an approved/locked profile."""
    return (status or "") in LOCKED_PROFILE_STATUSES


def _field_change_type(field_name: str) -> str:
    """Map an application field to the canonical CM change_type (safe default)."""
    return _FIELD_TO_CHANGE_TYPE.get(field_name, "company_details")


def diff_application_fields(app: Dict, proposed: Dict) -> List[Dict]:
    """Compute material field changes between a live app row and a proposed update.

    Returns CM-ready change items for fields that (a) are present in
    ``proposed``, (b) differ from the current stored value, and (c) are not in
    the minor-direct-edit whitelist. None and "" are treated as equal. Returns
    an empty list when nothing material changed.
    """
    items: List[Dict] = []
    for field, new_value in proposed.items():
        if field in MINOR_DIRECT_EDIT_FIELDS:
            continue
        current = app.get(field)
        cur_norm = "" if current is None else str(current).strip()
        new_norm = "" if new_value is None else str(new_value).strip()
        if cur_norm == new_norm:
            continue
        ct = _field_change_type(field)
        items.append({
            "change_type": ct,
            "field_name": field,
            "old_value": None if current is None else str(current),
            "new_value": None if new_value is None else str(new_value),
            "materiality": classify_materiality(ct),
        })
    return items


def find_open_draft_for_items(db, application_id: str, items: List[Dict]) -> Optional[str]:
    """Find an existing open (non-terminal) change request covering the same items.

    Idempotency guard: an attempted edit whose (change_type, field_name) set
    exactly matches an already-open request returns that request id rather than
    creating a duplicate. Returns the request id or None.
    """
    target_keys = {(i.get("change_type"), i.get("field_name")) for i in items}
    if not target_keys:
        return None
    statuses = sorted(OPEN_CHANGE_REQUEST_STATUSES)
    placeholders = ",".join(["?"] * len(statuses))
    try:
        rows = db.execute(
            f"""SELECT id FROM change_requests
                WHERE application_id = ? AND status IN ({placeholders})
                ORDER BY created_at DESC""",
            (application_id, *statuses),
        ).fetchall()
    except Exception as e:
        logger.error("find_open_draft_for_items failed: %s", e)
        return None
    for row in rows:
        rid = row["id"]
        item_rows = db.execute(
            "SELECT change_type, field_name FROM change_request_items WHERE request_id = ?",
            (rid,),
        ).fetchall()
        existing_keys = {(r["change_type"], r["field_name"]) for r in item_rows}
        if existing_keys == target_keys:
            return rid
    return None


def stage_locked_profile_edit(
    db,
    app: Dict,
    items: List[Dict],
    user: Dict,
    source_channel: str = "backoffice",
    source: str = "backoffice_manual",
    reason: Optional[str] = None,
    log_audit_fn=None,
) -> Dict[str, Any]:
    """Stage an attempted edit to a locked/approved profile as a draft Change Request.

    This NEVER mutates the live profile — the proposed values are recorded as
    change request items only. Idempotent: if an open draft already covers the
    same change set, that draft is returned instead of creating a duplicate.

    Returns a structured payload for the handler to return to the client
    (intended HTTP 409).
    """
    application_id = app["id"]
    app_ref = app.get("ref", application_id)

    existing_id = find_open_draft_for_items(db, application_id, items)
    if existing_id:
        detail = get_change_request_detail(db, existing_id)
        if log_audit_fn:
            log_audit_fn(
                user, "Change Request Draft Reused", app_ref,
                f"Attempted edit on locked profile matched existing open request {existing_id}",
                db=db,
            )
        return {
            "action": "change_request_exists",
            "request_id": existing_id,
            "request": detail,
            "prefilled_items": (detail or {}).get("items", items),
            "recommended_next_action": "open_existing_change_request",
            "message": (
                "A draft Change Request already exists for this change. "
                "Add evidence and submit it for approval."
            ),
        }

    request = create_change_request(
        db=db,
        application_id=application_id,
        source=source,
        source_channel=source_channel,
        reason=reason or "Auto-drafted from a blocked edit on an approved profile.",
        items=items,
        user=user,
        log_audit_fn=log_audit_fn,
    )

    if log_audit_fn:
        log_audit_fn(
            user, "Change Request Auto-Drafted", app_ref,
            f"Blocked edit on approved profile auto-drafted as {request['id']} "
            f"({len(items)} item(s), materiality={request['materiality']})",
            db=db,
        )

    return {
        "action": "change_request_drafted",
        "request_id": request["id"],
        "request": request,
        "prefilled_items": items,
        "recommended_next_action": "complete_change_request",
        "message": (
            "Approved profile is protected. We've started a Change Request from "
            "your edit — add supporting evidence and submit it for approval."
        ),
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
