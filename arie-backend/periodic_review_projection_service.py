from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from periodic_review_blockers import (
    decode_required_items,
    evaluate_review_readiness,
    load_evidence_links,
)

ACTIVE_REVIEW_STATES = (
    "pending",
    "in_progress",
    "awaiting_information",
    "pending_senior_review",
)
COMPLETED_REVIEW_STATE = "completed"
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
def _table_columns(db, table: str) -> Set[str]:
    if table not in {"periodic_reviews"}:
        return set()
    try:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        if rows:
            return {str(row["name"]) for row in rows}
    except Exception:
        pass
    try:
        rows = db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        return {str(row["column_name"]) for row in rows}
    except Exception:
        return set()


def _effective_risk_level(review) -> Optional[str]:
    return _row_get(review, "new_risk_level") or _row_get(review, "risk_level")
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


def _is_legacy_completed_review(review, raw_status: str) -> bool:
    if raw_status != COMPLETED_REVIEW_STATE:
        return False
    if _row_get(review, "outcome"):
        return False
    return bool(_row_get(review, "decision"))


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
    evidence_links = evidence_links if evidence_links is not None else load_evidence_links(db, review_id)
    readiness = evaluate_review_readiness(
        db,
        review,
        required_items=decode_required_items(_row_get(review, "required_items")),
        evidence_links=evidence_links,
    )
    blockers = readiness["operational_blockers"]
    raw_status = str(_row_get(review, "status") or "pending").strip().lower() or "pending"
    completion_readiness_applicable = not _is_legacy_completed_review(review, raw_status)
    if completion_readiness_applicable:
        completion_blocker_count = readiness["completion_blocker_count"]
        completion_blockers = readiness["completion_blockers"]
        completion_ready = readiness["completion_ready"]
    else:
        completion_blocker_count = 0
        completion_blockers = []
        completion_ready = None
    return {
        "review_id": review_id,
        "application_id": app_id,
        "client_name": _row_get(application, "company_name") or _row_get(review, "client_name") or "",
        "status": raw_status,
        "status_label": _status_label(raw_status, len(blockers), _row_get(review, "linked_edd_case_id")),
        "assigned_officer": _row_get(review, "assigned_officer"),
        "linked_edd_case_id": _row_get(review, "linked_edd_case_id"),
        "due_date": _row_get(review, "due_date"),
        "priority": _row_get(review, "priority"),
        "trigger_source": _row_get(review, "trigger_source") or _row_get(review, "trigger_type"),
        "trigger_reason": _row_get(review, "trigger_reason") or _row_get(review, "review_reason"),
        "last_review_date": _row_get(review, "last_review_date"),
        "next_review_date": _row_get(review, "next_review_date") or _row_get(review, "due_date"),
        "risk_level": _effective_risk_level(review) or _row_get(application, "final_risk_level") or _row_get(application, "risk_level"),
        "blocker_count": len(blockers),
        "blocker_summary": [blocker["label"] for blocker in blockers],
        "completion_blocker_count": completion_blocker_count,
        "completion_blocker_summary": [blocker["label"] for blocker in completion_blockers],
        "completion_ready": completion_ready,
        "completion_readiness_applicable": completion_readiness_applicable,
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
    application_ids: Optional[Iterable[str]] = None,
    review_ids: Optional[Iterable[int]] = None,
    statuses: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM periodic_reviews WHERE 1=1"
    params: List[Any] = []
    if application_id:
        sql += " AND application_id = ?"
        params.append(application_id)
    if application_ids:
        app_ids = [app_id for app_id in dict.fromkeys(application_ids) if app_id]
        if app_ids:
            sql += " AND application_id IN (" + ",".join("?" for _ in app_ids) + ")"
            params.extend(app_ids)
    if review_ids:
        cleaned_review_ids = [int(review_id) for review_id in dict.fromkeys(review_ids)]
        if cleaned_review_ids:
            sql += " AND id IN (" + ",".join("?" for _ in cleaned_review_ids) + ")"
            params.extend(cleaned_review_ids)
    if statuses:
        cleaned = [str(status).strip().lower() for status in statuses if str(status).strip()]
        if cleaned:
            sql += " AND LOWER(COALESCE(status, 'pending')) IN (" + ",".join("?" for _ in cleaned) + ")"
            params.extend(cleaned)
    columns = _table_columns(db, "periodic_reviews")
    order_parts = []
    if "due_date" in columns:
        order_parts.append("due_date ASC")
    if "created_at" in columns:
        order_parts.append("created_at DESC")
    order_parts.append("id DESC")
    sql += " ORDER BY " + ", ".join(order_parts)
    rows = db.execute(sql, tuple(params)).fetchall()
    review_ids = [row["id"] for row in rows]
    evidence_by_review: Dict[int, List[Dict[str, Any]]] = {rid: [] for rid in review_ids}
    if review_ids:
        placeholders = ",".join("?" for _ in review_ids)
        link_rows = db.execute(
            f"SELECT l.id, l.periodic_review_id, l.requirement_id, l.document_id, l.link_type, l.linked_by, l.linked_at, l.note, "
            "d.doc_type AS document_type, d.doc_name AS document_name, d.verification_status AS document_verification_status, "
            "d.review_status AS document_review_status, d.review_comment AS document_review_comment, "
            "d.reviewer_role AS document_reviewer_role, d.reviewed_at AS document_reviewed_at, "
            "d.verified_at AS document_verified_at, d.is_current AS document_is_current "
            f"FROM periodic_review_evidence_links l "
            "LEFT JOIN documents d ON d.id = l.document_id "
            f"WHERE l.periodic_review_id IN ({placeholders}) ORDER BY l.id ASC",
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
