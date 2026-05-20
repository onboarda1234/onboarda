"""
Shared verification state contract.

Backends remain authoritative for both the stored state and the client-facing
language. Frontends should render these labels instead of inventing independent
status wording.
"""

STATE_PENDING = "pending"
STATE_IN_PROGRESS = "in_progress"
STATE_VERIFIED = "verified"
STATE_FLAGGED = "flagged"
STATE_FAILED = "failed"

VERIFICATION_STATES = (
    STATE_PENDING,
    STATE_IN_PROGRESS,
    STATE_VERIFIED,
    STATE_FLAGGED,
    STATE_FAILED,
)

_ALIASES = {
    "": STATE_PENDING,
    "not_run": STATE_PENDING,
    "queued": STATE_PENDING,
    "processing": STATE_IN_PROGRESS,
    "running": STATE_IN_PROGRESS,
    "pass": STATE_VERIFIED,
    "passed": STATE_VERIFIED,
    "approved": STATE_VERIFIED,
    "warn": STATE_FLAGGED,
    "warning": STATE_FLAGGED,
    "review": STATE_FLAGGED,
    "review_required": STATE_FLAGGED,
    "manual_review": STATE_FLAGGED,
    "fail": STATE_FAILED,
    "error": STATE_FAILED,
}

_STATE_META = {
    STATE_PENDING: {
        "label": "Pending verification",
        "tone": "pending",
        "success": False,
        "terminal": False,
    },
    STATE_IN_PROGRESS: {
        "label": "Verification in progress",
        "tone": "pending",
        "success": False,
        "terminal": False,
    },
    STATE_VERIFIED: {
        "label": "Verified",
        "tone": "success",
        "success": True,
        "terminal": True,
    },
    STATE_FLAGGED: {
        "label": "Review required",
        "tone": "warning",
        "success": False,
        "terminal": True,
    },
    STATE_FAILED: {
        "label": "Verification failed",
        "tone": "error",
        "success": False,
        "terminal": True,
    },
}


def normalize_verification_state(value) -> str:
    """Return a canonical verification state for storage and rendering."""
    raw = str(value or "").strip().lower()
    normalized = _ALIASES.get(raw, raw)
    if normalized in VERIFICATION_STATES:
        return normalized
    return STATE_PENDING


def verification_state_payload(value) -> dict:
    """Client-safe state metadata controlled by the backend."""
    state = normalize_verification_state(value)
    meta = _STATE_META[state]
    return {
        "verification_state": state,
        "verification_status_label": meta["label"],
        "verification_status_tone": meta["tone"],
        "verification_success": meta["success"],
        "verification_terminal": meta["terminal"],
    }


def decorate_document_verification_state(document: dict) -> dict:
    """Add backend-owned verification state metadata to a document payload."""
    if document is None:
        return document
    status = document.get("verification_status")
    document.update(verification_state_payload(status))
    document["verification_status"] = normalize_verification_state(status)
    return document
