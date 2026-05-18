from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

ACTIVE_REVIEW_STATES = (
    "pending",
    "in_progress",
    "awaiting_information",
    "pending_senior_review",
)
COMPLETED_REVIEW_STATE = "completed"
RESOLVED_ITEM_STATUSES = {"cleared", "not_applicable"}
DOCUMENT_EVIDENCE_ITEM_TYPES = {
    "kyc_refresh",
    "ubo_confirmation",
    "document_expired",
    "document_expiring_soon",
    "document_stale",
    "document_expiry_missing",
    "licensing_refresh",
    "source_of_funds_refresh",
    "source_of_wealth_refresh",
}


def _row_get(row, key, default=None):
    if row is None:
        return default
    try:
        if key in row.keys():
            value = row[key]
            return default if value is None else value
    except Exception:
        pass
    if isinstance(row, dict):
        value = row.get(key, default)
        return default if value is None else value
    return default


def _decode_required_items(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _effective_risk_level(review) -> Optional[str]:
    return _row_get(review, "new_risk_level") or _row_get(review, "risk_level")


def _load_evidence_link_rows(db, review_id: int) -> List[Dict[str, Any]]:
    rows = db.execute(
        "SELECT id, requirement_id, document_id, link_type, linked_by, linked_at, note "
        "FROM periodic_review_evidence_links WHERE periodic_review_id = ? ORDER BY id ASC",
        (review_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_memo_status(db, review_id: int, review_row) -> Optional[str]:
    status = _row_get(review_row, "memo_status")
    if status:
        return status
    row = db.execute(
        "SELECT id, status FROM periodic_review_memos WHERE periodic_review_id = ? "
        "ORDER BY version DESC, id DESC LIMIT 1",
        (review_id,),
    ).fetchone()
    if row:
        return _row_get(row, "status")
    if _row_get(review_row, "outcome"):
        return "pending"
    return None


def _linked_edd_blocker(db, review_row) -> Optional[str]:
    linked_edd_case_id = _row_get(review_row, "linked_edd_case_id")
    if not linked_edd_case_id:
        return None
    row = db.execute(
        "SELECT stage FROM edd_cases WHERE id = ?",
        (linked_edd_case_id,),
    ).fetchone()
    stage = str(_row_get(row, "stage") or "").strip().lower()
    if stage and stage not in {"edd_approved", "edd_rejected"}:
        return "Active linked EDD case is still open"
    return None


def _blocking_summary(db, review_row, required_items: List[Dict[str, Any]], evidence_links: List[Dict[str, Any]]) -> List[str]:
    blockers: List[str] = []
    if _row_get(review_row, "import_requires_ack") and not _row_get(review_row, "legacy_sco_acknowledged_at"):
        blockers.append("SCO/admin acknowledgement required for imported high-risk review")

    linked_requirements = {
        str(link.get("requirement_id"))
        for link in evidence_links
        if link.get("requirement_id") is not None and str(link.get("requirement_id")).strip()
    }
    for item in required_items:
        status = str(item.get("status") or "open").strip().lower()
        if status in RESOLVED_ITEM_STATUSES:
            continue
        item_type = str(item.get("item_type") or item.get("code") or "").strip()
        label = str(item.get("label") or item_type or "Required review item").strip()
        if item_type == "screening_refresh":
            blockers.append("Screening evidence is missing or stale")
        elif item_type in DOCUMENT_EVIDENCE_ITEM_TYPES and str(item.get("id") or "") not in linked_requirements:
            blockers.append(f"Evidence outstanding: {label}")

    edd_blocker = _linked_edd_blocker(db, review_row)
    if edd_blocker:
        blockers.append(edd_blocker)
    if not str(_row_get(review_row, "officer_rationale") or "").strip():
        blockers.append("Officer rationale is required")
    if not str(_row_get(review_row, "outcome") or "").strip():
        blockers.append("Review outcome is required before completion")

    deduped: List[str] = []
    seen = set()
    for blocker in blockers:
        if blocker in seen:
            continue
        seen.add(blocker)
        deduped.append(blocker)
    return deduped


def _status_label(raw_status: str, blocker_count: int, linked_edd_case_id: Any) -> str:
    if raw_status == COMPLETED_REVIEW_STATE:
        return "Completed"
    if blocker_count:
        return "Blocked"
    if raw_status == "in_progress":
        return "In Review"
    if raw_status == "awaiting_information":
        return "Waiting for Info"
    if raw_status == "pending_senior_review":
        return "Senior Review"
    if linked_edd_case_id:
        return "Escalated to EDD"
    return "Due"


def build_review_projection(db, review_row, *, evidence_links: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    review = dict(review_row)
    review_id = _row_get(review, "id")
    app_id = _row_get(review, "application_id")
    application = None
    if app_id:
        application = db.execute(
            "SELECT id, ref, company_name, risk_level, final_risk_level FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
    required_items = _decode_required_items(_row_get(review, "required_items"))
    evidence_links = evidence_links if evidence_links is not None else _load_evidence_link_rows(db, review_id)
    blockers = _blocking_summary(db, review, required_items, evidence_links)
    raw_status = str(_row_get(review, "status") or "pending").strip().lower() or "pending"
    return {
        "review_id": review_id,
        "application_id": app_id,
        "client_name": _row_get(application, "company_name") or _row_get(review, "client_name") or "",
        "status": raw_status,
        "status_label": _status_label(raw_status, len(blockers), _row_get(review, "linked_edd_case_id")),
        "assigned_officer": _row_get(review, "assigned_officer"),
        "due_date": _row_get(review, "due_date"),
        "priority": _row_get(review, "priority"),
        "trigger_source": _row_get(review, "trigger_source") or _row_get(review, "trigger_type"),
        "trigger_reason": _row_get(review, "trigger_reason") or _row_get(review, "review_reason"),
        "last_review_date": _row_get(review, "last_review_date"),
        "next_review_date": _row_get(review, "next_review_date") or _row_get(review, "due_date"),
        "risk_level": _effective_risk_level(review) or _row_get(application, "final_risk_level") or _row_get(application, "risk_level"),
        "blocker_count": len(blockers),
        "blocker_summary": blockers,
        "outcome": _row_get(review, "outcome"),
        "memo_status": _load_memo_status(db, review_id, review),
        "lifecycle_link": {
            "type": "periodic_review",
            "review_id": review_id,
            "path": f"/api/monitoring/reviews/{review_id}",
        },
        "source": "periodic_reviews",
        "evidence_links": evidence_links,
    }


def get_review_projection(db, review_id: int) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        return None
    return build_review_projection(db, row)


def list_review_projections(
    db,
    *,
    application_id: Optional[str] = None,
    statuses: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM periodic_reviews WHERE 1=1"
    params: List[Any] = []
    if application_id:
        sql += " AND application_id = ?"
        params.append(application_id)
    if statuses:
        cleaned = [str(status).strip().lower() for status in statuses if str(status).strip()]
        if cleaned:
            sql += " AND LOWER(COALESCE(status, 'pending')) IN (" + ",".join("?" for _ in cleaned) + ")"
            params.extend(cleaned)
    sql += " ORDER BY due_date ASC, created_at DESC, id DESC"
    rows = db.execute(sql, tuple(params)).fetchall()
    review_ids = [row["id"] for row in rows]
    evidence_by_review: Dict[int, List[Dict[str, Any]]] = {rid: [] for rid in review_ids}
    if review_ids:
        placeholders = ",".join("?" for _ in review_ids)
        link_rows = db.execute(
            f"SELECT * FROM periodic_review_evidence_links WHERE periodic_review_id IN ({placeholders}) ORDER BY id ASC",
            tuple(review_ids),
        ).fetchall()
        for row in link_rows:
            evidence_by_review.setdefault(row["periodic_review_id"], []).append(dict(row))
    return [
        build_review_projection(db, row, evidence_links=evidence_by_review.get(row["id"], []))
        for row in rows
    ]


def latest_active_review_summary(db, application_id: str) -> Optional[Dict[str, Any]]:
    projections = list_review_projections(db, application_id=application_id, statuses=ACTIVE_REVIEW_STATES)
    return projections[0] if projections else None
