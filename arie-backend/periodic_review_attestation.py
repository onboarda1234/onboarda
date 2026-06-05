from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional


QUESTIONNAIRE_VERSION = "prs2_v1"
ATTESTATION_STATUS_NOT_STARTED = "not_started"
ATTESTATION_STATUS_DRAFT = "draft"
ATTESTATION_STATUS_SUBMITTED = "submitted"
VALID_ANSWER_VALUES = {"yes", "no", "unanswered"}

ATTESTATION_QUESTIONS = [
    {
        "key": "directors_changed",
        "label": "Have there been any changes in the company's directors?",
        "comment_prompt": "Please briefly explain what changed.",
        "material_change_answer": "yes",
    },
    {
        "key": "shareholders_changed",
        "label": "Have there been any changes in the company's shareholders or shareholding structure?",
        "comment_prompt": "Please briefly explain what changed.",
        "material_change_answer": "yes",
    },
    {
        "key": "ubos_changed",
        "label": "Have there been any changes in beneficial owners, controllers, or persons exercising control over the company?",
        "comment_prompt": "Please briefly explain what changed.",
        "material_change_answer": "yes",
    },
    {
        "key": "business_activity_changed",
        "label": "Has the nature of the company's business activity changed?",
        "comment_prompt": "Please briefly explain what changed.",
        "material_change_answer": "yes",
    },
    {
        "key": "jurisdictions_changed",
        "label": "Have the countries where the company operates, serves clients, or targets markets changed?",
        "comment_prompt": "Please briefly explain what changed.",
        "material_change_answer": "yes",
    },
    {
        "key": "transaction_volume_changed",
        "label": "Has the expected transaction volume or transaction value changed materially?",
        "comment_prompt": "Please briefly explain what changed.",
        "material_change_answer": "yes",
    },
    {
        "key": "licence_regulatory_status_changed",
        "label": "Has the company obtained, lost, changed, or become subject to any licence, registration, or regulatory approval?",
        "comment_prompt": "Please briefly explain what changed.",
        "material_change_answer": "yes",
    },
    {
        "key": "company_contact_details_correct",
        "label": "Are the company details and contact details currently held by us still correct?",
        "comment_prompt": "Please provide the updated details.",
        "material_change_answer": "no",
    },
]

QUESTION_INDEX = {question["key"]: question for question in ATTESTATION_QUESTIONS}


class PeriodicReviewAttestationError(ValueError):
    pass


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


def _parse_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_temporal(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            try:
                return date.fromisoformat(text[:10]).isoformat()
            except ValueError:
                return text
    return str(value)


def _normalize_answer(value: Any) -> str:
    answer = str(value or "").strip().lower()
    if answer in {"", "null", "none"}:
        return "unanswered"
    if answer not in VALID_ANSWER_VALUES:
        raise PeriodicReviewAttestationError("answers must be yes, no, or unanswered")
    return answer


def _normalize_comment(value: Any) -> str:
    return str(value or "").strip()


def question_definitions() -> List[Dict[str, Any]]:
    return [dict(question) for question in ATTESTATION_QUESTIONS]


def _question_payload(question: Dict[str, Any], answer: str, comment: str) -> Dict[str, Any]:
    material_change = answer == question["material_change_answer"]
    return {
        "key": question["key"],
        "label": question["label"],
        "answer": answer,
        "comment": comment,
        "material_change": material_change,
        "comment_required": material_change,
        "comment_prompt": question["comment_prompt"],
    }


def build_attestation_snapshot(
    payload: Optional[Dict[str, Any]],
    *,
    status: str,
    saved_at: Optional[Any],
    submitted_at: Optional[Any],
    submitted_by: Optional[str],
) -> Dict[str, Any]:
    raw_payload = dict(payload or {})
    answers_by_key = raw_payload.get("answers") if isinstance(raw_payload.get("answers"), dict) else {}
    questions = []
    material_change_keys = []
    for question in ATTESTATION_QUESTIONS:
        raw_answer = answers_by_key.get(question["key"]) if isinstance(answers_by_key, dict) else {}
        raw_answer = raw_answer if isinstance(raw_answer, dict) else {}
        answer = _normalize_answer(raw_answer.get("answer"))
        comment = _normalize_comment(raw_answer.get("comment"))
        entry = _question_payload(question, answer, comment)
        if entry["material_change"]:
            material_change_keys.append(question["key"])
        questions.append(entry)
    declaration_accepted = bool(raw_payload.get("declaration_accepted"))
    return {
        "questionnaire_version": str(raw_payload.get("questionnaire_version") or QUESTIONNAIRE_VERSION),
        "status": status or ATTESTATION_STATUS_NOT_STARTED,
        "saved_at": _serialize_temporal(saved_at),
        "submitted_at": _serialize_temporal(submitted_at),
        "submitted_by": submitted_by,
        "declaration_accepted": declaration_accepted,
        "questions": questions,
        "material_change_question_keys": material_change_keys,
        "has_material_changes": bool(material_change_keys),
    }


def attestation_snapshot_from_review(review_row) -> Dict[str, Any]:
    payload = _parse_json_dict(_row_get(review_row, "client_attestation_payload"))
    status = str(_row_get(review_row, "client_attestation_status") or ATTESTATION_STATUS_NOT_STARTED)
    saved_at = _row_get(review_row, "client_attestation_saved_at")
    submitted_at = _row_get(review_row, "client_attestation_submitted_at")
    submitted_by = _row_get(review_row, "client_attestation_submitted_by")
    return build_attestation_snapshot(
        payload,
        status=status,
        saved_at=saved_at,
        submitted_at=submitted_at,
        submitted_by=submitted_by,
    )


def task_status_label(snapshot: Dict[str, Any], *, is_overdue: bool) -> str:
    status = str(snapshot.get("status") or ATTESTATION_STATUS_NOT_STARTED)
    if status == ATTESTATION_STATUS_SUBMITTED:
        return "Submitted"
    if is_overdue:
        return "Overdue"
    if status == ATTESTATION_STATUS_DRAFT:
        return "Draft saved"
    return "Not started"


def task_primary_action_label(snapshot: Dict[str, Any]) -> str:
    status = str(snapshot.get("status") or ATTESTATION_STATUS_NOT_STARTED)
    if status == ATTESTATION_STATUS_SUBMITTED:
        return "View submitted attestation"
    if status == ATTESTATION_STATUS_DRAFT:
        return "Continue review"
    return "Start review"


def portal_task_summary_from_review(review_row, projection: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    snapshot = attestation_snapshot_from_review(review_row)
    projection = projection or {}
    return {
        "review_id": _row_get(review_row, "id"),
        "review_reference": projection.get("review_reference") or f"PR-{_row_get(review_row, 'id')}",
        "application_id": _row_get(review_row, "application_id"),
        "company_name": projection.get("client_name") or _row_get(review_row, "client_name") or "",
        "due_date": projection.get("due_date") or _row_get(review_row, "due_date") or _row_get(review_row, "next_review_date"),
        "is_overdue": bool(projection.get("is_overdue")),
        "task_status": snapshot["status"],
        "task_status_label": task_status_label(snapshot, is_overdue=bool(projection.get("is_overdue"))),
        "primary_action_label": task_primary_action_label(snapshot),
        "attestation_status": snapshot["status"],
        "attestation_status_label": {
            ATTESTATION_STATUS_NOT_STARTED: "Not started",
            ATTESTATION_STATUS_DRAFT: "Draft saved",
            ATTESTATION_STATUS_SUBMITTED: "Submitted",
        }.get(snapshot["status"], "Not started"),
        "saved_at": snapshot.get("saved_at"),
        "submitted_at": snapshot.get("submitted_at"),
    }


def _validated_answers_from_payload(raw_payload: Dict[str, Any], *, final_submit: bool) -> Dict[str, Dict[str, str]]:
    answers = raw_payload.get("answers")
    if not isinstance(answers, dict):
        answers = {}
    normalized: Dict[str, Dict[str, str]] = {}
    for question in ATTESTATION_QUESTIONS:
        entry = answers.get(question["key"])
        entry = entry if isinstance(entry, dict) else {}
        answer = _normalize_answer(entry.get("answer"))
        comment = _normalize_comment(entry.get("comment"))
        normalized[question["key"]] = {"answer": answer, "comment": comment}
        if final_submit and answer == "unanswered":
            raise PeriodicReviewAttestationError("All attestation questions must be answered before submission")
        if answer == question["material_change_answer"] and not comment:
            raise PeriodicReviewAttestationError("Comments are required for every declared material change")
    extra_keys = [key for key in answers.keys() if key not in QUESTION_INDEX]
    if extra_keys:
        raise PeriodicReviewAttestationError("Unknown attestation question key")
    return normalized


def prepare_attestation_draft_update(review_row, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
    existing = attestation_snapshot_from_review(review_row)
    if existing["status"] == ATTESTATION_STATUS_SUBMITTED:
        raise PeriodicReviewAttestationError("Submitted attestations are read-only")
    answers = _validated_answers_from_payload(raw_payload or {}, final_submit=False)
    now = _now_iso()
    payload = {
        "questionnaire_version": QUESTIONNAIRE_VERSION,
        "answers": answers,
        "declaration_accepted": bool(raw_payload.get("declaration_accepted")),
        "saved_at": now,
        "submitted_at": existing.get("submitted_at"),
    }
    return {
        "status": ATTESTATION_STATUS_DRAFT,
        "saved_at": now,
        "submitted_at": _serialize_temporal(_row_get(review_row, "client_attestation_submitted_at")),
        "submitted_by": _row_get(review_row, "client_attestation_submitted_by"),
        "payload_json": json.dumps(payload, sort_keys=True),
        "snapshot": build_attestation_snapshot(
            payload,
            status=ATTESTATION_STATUS_DRAFT,
            saved_at=now,
            submitted_at=_serialize_temporal(_row_get(review_row, "client_attestation_submitted_at")),
            submitted_by=_row_get(review_row, "client_attestation_submitted_by"),
        ),
    }


def prepare_attestation_submission_update(review_row, raw_payload: Dict[str, Any], *, submitted_by: str) -> Dict[str, Any]:
    existing = attestation_snapshot_from_review(review_row)
    if existing["status"] == ATTESTATION_STATUS_SUBMITTED:
        raise PeriodicReviewAttestationError("Submitted attestations are read-only")
    answers = _validated_answers_from_payload(raw_payload or {}, final_submit=True)
    declaration_accepted = bool(raw_payload.get("declaration_accepted"))
    if not declaration_accepted:
        raise PeriodicReviewAttestationError("Declaration must be accepted before submission")
    now = _now_iso()
    saved_at = _serialize_temporal(existing.get("saved_at")) or now
    payload = {
        "questionnaire_version": QUESTIONNAIRE_VERSION,
        "answers": answers,
        "declaration_accepted": True,
        "saved_at": saved_at,
        "submitted_at": now,
    }
    return {
        "status": ATTESTATION_STATUS_SUBMITTED,
        "saved_at": saved_at,
        "submitted_at": now,
        "submitted_by": submitted_by,
        "payload_json": json.dumps(payload, sort_keys=True),
        "snapshot": build_attestation_snapshot(
            payload,
            status=ATTESTATION_STATUS_SUBMITTED,
            saved_at=saved_at,
            submitted_at=now,
            submitted_by=submitted_by,
        ),
    }


def material_change_question_keys(snapshot: Dict[str, Any]) -> List[str]:
    keys = snapshot.get("material_change_question_keys")
    if isinstance(keys, list):
        return [str(key) for key in keys]
    return []
