"""
EDD Active-Memo Integration -- PR-04
=====================================

Provider-agnostic linkage layer that lets EDD findings feed the correct
*active decision artifact* (onboarding memo or periodic-review memo)
without:

* mutating ``compliance_memos`` history,
* overwriting an onboarding memo with periodic-review-lifecycle material,
* creating a third, disconnected EDD memo universe,
* touching any file in ``PROTECTED_FILES``.

What this module provides
-------------------------

* :func:`resolve_active_memo_context` — deterministic resolution of the
  memo context an EDD case belongs to, based on PR-01
  ``origin_context`` and the explicit linkage columns added by
  migration 008.

* :func:`set_edd_findings` / :func:`get_edd_findings` — structured
  findings payload, persisted on the new ``edd_findings`` table
  (one row per ``edd_case_id``, upsert semantics).

* :func:`attach_edd_findings_to_memo_context` — record an explicit,
  audited attachment row on ``edd_memo_attachments`` linking an EDD
  case (and therefore its findings) to either the onboarding memo or
  the periodic-review memo context resolved for that case.

* :func:`detach_edd_findings_from_memo_context` — break an existing
  attachment, with a structured ``.detached`` audit event. Idempotent
  no-op when there is nothing to detach.

* :func:`get_memo_context_attachments` /
  :func:`get_memo_context_findings` — read helpers for downstream memo
  assembly to consume findings attached to a given memo context.

Design constraints (preserved verbatim from PR-01..PR-03a contracts)
--------------------------------------------------------------------

* **Onboarding memo identity** stays per-application per-version on
  ``compliance_memos``. PR-04 does NOT alter that table.

* **Periodic-review memo identity** is the ``periodic_reviews.id`` row
  itself for now. PR-04 does NOT promote review memo to a separate
  ``periodic_review_memos`` row -- that is intentionally deferred.

* **Audit-writer is REQUIRED** for every mutating helper here, mirroring
  the PR-01 contract (raises :class:`MissingAuditWriter` BEFORE any DB
  mutation when the writer is None).

* **Provider-agnostic.** No screening provider, no Sumsub, no
  ComplyAdvantage. No reading of ``screening_reports_normalized`` as
  authoritative.

* **EX-01..EX-13 untouched.** No file in ``PROTECTED_FILES`` is
  imported, modified, or relied on for new behaviour.

PR-02 reverse-link displacement contract
----------------------------------------

PR-02 documented that ``edd_cases.linked_monitoring_alert_id`` and
``edd_cases.linked_periodic_review_id`` always point to the *most
recent* originator. PR-04 honors this: the resolver treats the
*explicit* linkage column on ``edd_cases`` as the authoritative source
of truth and never tries to enumerate every alert/review that ever
pointed at a given EDD.

PR-03a clarification (decision vs outcome)
------------------------------------------

PR-04 reads ``periodic_reviews.outcome`` (PR-03a authoritative outcome
field) where outcome matters -- never the legacy ``decision`` column --
and never co-writes both. This module does not alter either field.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

import lifecycle_linkage as ll
from lifecycle_linkage import (
    MissingAuditWriter,
    _row_get,  # internal but stable helper -- mirror PR-01/PR-03 conventions
)

logger = logging.getLogger("arie.edd_memo_integration")


# ─────────────────────────────────────────────────────────────────
# Vocabularies (application-layer source of truth -- DB has no CHECK)
# ─────────────────────────────────────────────────────────────────
MEMO_CONTEXT_ONBOARDING = "onboarding"
MEMO_CONTEXT_PERIODIC_REVIEW = "periodic_review"

VALID_MEMO_CONTEXT_KINDS = (
    MEMO_CONTEXT_ONBOARDING,
    MEMO_CONTEXT_PERIODIC_REVIEW,
)

# Recommended-outcome vocabulary for structured findings. Kept narrow
# and explicit. This is *not* the EDD case decision (that lives on
# ``edd_cases.decision``); it is the finding author's recommendation
# that a senior reviewer can accept or reject when approving the EDD.
RECOMMENDED_OUTCOME_APPROVE = "approve"
RECOMMENDED_OUTCOME_APPROVE_WITH_CONDITIONS = "approve_with_conditions"
RECOMMENDED_OUTCOME_REJECT = "reject"
RECOMMENDED_OUTCOME_ESCALATE = "escalate"

VALID_RECOMMENDED_OUTCOMES = (
    RECOMMENDED_OUTCOME_APPROVE,
    RECOMMENDED_OUTCOME_APPROVE_WITH_CONDITIONS,
    RECOMMENDED_OUTCOME_REJECT,
    RECOMMENDED_OUTCOME_ESCALATE,
)


# ─────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────
class EDDMemoIntegrationError(ValueError):
    """Base class for PR-04 integration validation failures."""


class EDDCaseNotFound(EDDMemoIntegrationError):
    pass


class FindingsValidationError(EDDMemoIntegrationError):
    pass


class MemoContextResolutionError(EDDMemoIntegrationError):
    """Raised when no memo context can be deterministically resolved
    from the recorded EDD lifecycle state. The caller is expected to
    surface this as a 4xx, never to silently guess."""


class AttachmentValidationError(EDDMemoIntegrationError):
    pass


# ─────────────────────────────────────────────────────────────────
# Internal utilities
# ─────────────────────────────────────────────────────────────────
AuditWriter = Callable[..., None]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_audit_writer(audit_writer):
    if audit_writer is None:
        raise MissingAuditWriter(
            "edd_memo_integration mutating helpers require a non-None "
            "audit_writer (canonical audit path). Refusing to mutate."
        )


def _detail(payload):
    try:
        return json.dumps(dict(payload), default=str, sort_keys=True)
    except (TypeError, ValueError):
        return json.dumps({"serialization_error": True})


def _emit_audit(audit_writer, user, action, target, detail_payload,
                db, before_state=None, after_state=None):
    user_dict = dict(user) if user else {}
    logger.info(
        "edd_memo_audit action=%s target=%s detail=%s",
        action, target, _detail(detail_payload),
    )
    if audit_writer is None:
        return
    try:
        audit_writer(
            user_dict, action, target, _detail(detail_payload),
            db=db, before_state=before_state, after_state=after_state,
        )
    except Exception:
        logger.exception("edd_memo audit write failed action=%s", action)


def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        try:
            return {k: row[k] for k in row.keys()}
        except Exception:
            return None


def _fetch_edd(db, edd_case_id) -> Dict[str, Any]:
    row = db.execute(
        "SELECT * FROM edd_cases WHERE id = ?", (edd_case_id,)
    ).fetchone()
    out = _row_to_dict(row)
    if out is None:
        raise EDDCaseNotFound(f"edd_case id={edd_case_id} not found")
    return out


def _fetch_periodic_review(db, review_id) -> Optional[Dict[str, Any]]:
    if review_id is None:
        return None
    row = db.execute(
        "SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)
    ).fetchone()
    return _row_to_dict(row)


def _fetch_alert(db, alert_id) -> Optional[Dict[str, Any]]:
    if alert_id is None:
        return None
    row = db.execute(
        "SELECT * FROM monitoring_alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    return _row_to_dict(row)


def _fetch_latest_onboarding_memo_id(db, application_id) -> Optional[int]:
    """Return the id of the latest compliance_memos row for an application,
    or None if no onboarding memo exists yet.

    PR-04 does NOT mutate this row; it is read-only here.
    """
    if application_id is None:
        return None
    row = db.execute(
        "SELECT id FROM compliance_memos "
        "WHERE application_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (application_id,),
    ).fetchone()
    return _row_get(row, "id") if row else None


# ─────────────────────────────────────────────────────────────────
# Active memo context resolution
# ─────────────────────────────────────────────────────────────────
def resolve_active_memo_context(db, edd_case_id) -> Dict[str, Any]:
    """Deterministically resolve the active memo context for an EDD case.

    Resolution rules (in order, first match wins):

    1. If ``edd_cases.linked_periodic_review_id`` is non-NULL, the
       active context is the periodic-review memo context for that
       review (regardless of what ``origin_context`` says). This is the
       strongest signal because it is the explicit linkage column
       managed by PR-01 ``lifecycle_linkage.set_edd_origin`` and by
       PR-03 ``periodic_review_engine.escalate_review_to_edd``.

    2. Else if ``origin_context == 'periodic_review'``, the EDD claims
       a review origin but has no explicit linkage. This is treated as
       a resolution failure (not a silent guess) and raises
       :class:`MemoContextResolutionError`.

    3. Else if ``origin_context == 'onboarding'``, the active context
       is the onboarding memo context for the application.

    4. Else if ``origin_context == 'monitoring_alert'`` AND the linked
       alert (via ``edd_cases.linked_monitoring_alert_id``) itself
       points at a periodic review, route to that review's context.
       If the alert is not linked to any review, the EDD is treated as
       a post-onboarding lifecycle EDD and routed to the onboarding
       memo context (matches the documented contract: EDD must not
       create a disconnected decision universe).

    5. Else (origin_context in {'change_request', 'manual', None}):
       fall back to the onboarding memo context for the application.
       PR-01 documented that ``origin_context`` may be NULL on legacy
       rows; this default keeps such rows operationally sane.

    Returns a dict shaped like::

        {
            "edd_case_id": int,
            "application_id": str,
            "kind": "onboarding" | "periodic_review",
            "periodic_review_id": int | None,
            "memo_id": int | None,           # latest compliance_memos.id
                                              # for kind='onboarding';
                                              # always None for review.
            "origin_context": str | None,    # mirrored for callers
            "resolution_reason": str,        # human-readable explanation
        }

    Never mutates DB. Raises :class:`EDDCaseNotFound` if the EDD does
    not exist, and :class:`MemoContextResolutionError` only in the
    documented under-specified case (rule 2).
    """
    edd = _fetch_edd(db, edd_case_id)
    application_id = _row_get(edd, "application_id")
    if application_id is None:
        raise MemoContextResolutionError(
            f"edd_case id={edd_case_id} has no application_id; "
            "cannot resolve memo context"
        )

    origin_context = _row_get(edd, "origin_context")
    linked_review_id = _row_get(edd, "linked_periodic_review_id")
    linked_alert_id = _row_get(edd, "linked_monitoring_alert_id")

    # Rule 1: explicit periodic-review linkage wins.
    if linked_review_id is not None:
        review = _fetch_periodic_review(db, linked_review_id)
        if review is None:
            # Linkage points at a non-existent review row. Refuse to
            # silently fall back -- this is a data-integrity issue the
            # caller should surface.
            raise MemoContextResolutionError(
                f"edd_case id={edd_case_id} linked_periodic_review_id="
                f"{linked_review_id} does not exist in periodic_reviews"
            )
        return {
            "edd_case_id": edd_case_id,
            "application_id": application_id,
            "kind": MEMO_CONTEXT_PERIODIC_REVIEW,
            "periodic_review_id": linked_review_id,
            "memo_id": None,
            "origin_context": origin_context,
            "resolution_reason": (
                "explicit linked_periodic_review_id on edd_cases"
            ),
        }

    # Rule 2: claims periodic_review origin but no explicit link.
    if origin_context == "periodic_review":
        raise MemoContextResolutionError(
            f"edd_case id={edd_case_id} has origin_context='periodic_review' "
            "but no linked_periodic_review_id; cannot resolve memo "
            "context deterministically. Set linked_periodic_review_id "
            "via lifecycle_linkage.set_edd_origin and retry."
        )

    # Rule 3: explicit onboarding origin.
    if origin_context == "onboarding":
        memo_id = _fetch_latest_onboarding_memo_id(db, application_id)
        return {
            "edd_case_id": edd_case_id,
            "application_id": application_id,
            "kind": MEMO_CONTEXT_ONBOARDING,
            "periodic_review_id": None,
            "memo_id": memo_id,
            "origin_context": origin_context,
            "resolution_reason": "origin_context='onboarding'",
        }

    # Rule 4: monitoring-alert origin -- inspect the alert.
    if origin_context == "monitoring_alert":
        alert = _fetch_alert(db, linked_alert_id) if linked_alert_id else None
        alert_review_id = (
            _row_get(alert, "linked_periodic_review_id") if alert else None
        )
        if alert_review_id is not None:
            review = _fetch_periodic_review(db, alert_review_id)
            if review is not None:
                return {
                    "edd_case_id": edd_case_id,
                    "application_id": application_id,
                    "kind": MEMO_CONTEXT_PERIODIC_REVIEW,
                    "periodic_review_id": alert_review_id,
                    "memo_id": None,
                    "origin_context": origin_context,
                    "resolution_reason": (
                        "monitoring_alert origin; alert linked to "
                        f"periodic_review id={alert_review_id}"
                    ),
                }
        # No review-side context -- treat as post-onboarding lifecycle
        # EDD against the onboarding memo. Documented contract: never
        # create a disconnected decision universe.
        memo_id = _fetch_latest_onboarding_memo_id(db, application_id)
        return {
            "edd_case_id": edd_case_id,
            "application_id": application_id,
            "kind": MEMO_CONTEXT_ONBOARDING,
            "periodic_review_id": None,
            "memo_id": memo_id,
            "origin_context": origin_context,
            "resolution_reason": (
                "monitoring_alert origin without review linkage; "
                "routed to onboarding memo context"
            ),
        }

    # Rule 5: change_request / manual / None -- default to onboarding.
    memo_id = _fetch_latest_onboarding_memo_id(db, application_id)
    return {
        "edd_case_id": edd_case_id,
        "application_id": application_id,
        "kind": MEMO_CONTEXT_ONBOARDING,
        "periodic_review_id": None,
        "memo_id": memo_id,
        "origin_context": origin_context,
        "resolution_reason": (
            f"origin_context={origin_context!r}; defaulted to onboarding "
            "memo context (no explicit review linkage)"
        ),
    }


# ─────────────────────────────────────────────────────────────────
# Structured findings -- read / upsert
# ─────────────────────────────────────────────────────────────────
_FINDINGS_LIST_FIELDS = (
    "key_concerns",
    "mitigating_evidence",
    "conditions",
    "supporting_notes",
)
_FINDINGS_TEXT_FIELDS = (
    "findings_summary",
    "rationale",
)


def _coerce_list_field(value, field_name: str) -> List[Any]:
    """Validate and normalise a list-typed findings field."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise FindingsValidationError(
            f"findings.{field_name} must be a list, got {type(value).__name__}"
        )
    # supporting_notes accepts dicts; the others are strings. We do not
    # coerce dict<->str -- the caller is expected to pass the right
    # shape. Validation here is light: every entry must be JSON-serialisable.
    for i, item in enumerate(value):
        try:
            json.dumps(item, default=str)
        except (TypeError, ValueError) as exc:
            raise FindingsValidationError(
                f"findings.{field_name}[{i}] is not JSON-serialisable: {exc}"
            )
    return value


def _coerce_text_field(value, field_name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FindingsValidationError(
            f"findings.{field_name} must be a string, got "
            f"{type(value).__name__}"
        )
    return value


def _decode_list(raw) -> List[Any]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def _materialise_findings_row(row) -> Dict[str, Any]:
    d = _row_to_dict(row) or {}
    return {
        "id": d.get("id"),
        "edd_case_id": d.get("edd_case_id"),
        "findings_summary": d.get("findings_summary"),
        "key_concerns": _decode_list(d.get("key_concerns")),
        "mitigating_evidence": _decode_list(d.get("mitigating_evidence")),
        "conditions": _decode_list(d.get("conditions")),
        "rationale": d.get("rationale"),
        "supporting_notes": _decode_list(d.get("supporting_notes")),
        "recommended_outcome": d.get("recommended_outcome"),
        "created_by": d.get("created_by"),
        "created_at": d.get("created_at"),
        "updated_by": d.get("updated_by"),
        "updated_at": d.get("updated_at"),
    }


def get_edd_findings(db, edd_case_id) -> Optional[Dict[str, Any]]:
    """Return the structured findings for an EDD case, or None.

    Never mutates DB. Returns ``None`` when no findings have been
    recorded yet (the EDD case may still exist).
    """
    row = db.execute(
        "SELECT * FROM edd_findings WHERE edd_case_id = ?",
        (edd_case_id,),
    ).fetchone()
    if row is None:
        return None
    return _materialise_findings_row(row)


def set_edd_findings(db, edd_case_id, *,
                     findings: Mapping[str, Any],
                     user=None,
                     audit_writer=None) -> Dict[str, Any]:
    """Upsert structured findings for an EDD case.

    ``findings`` is a dict that may contain any of:

    * ``findings_summary`` (str)
    * ``key_concerns`` (list[str])
    * ``mitigating_evidence`` (list[str])
    * ``conditions`` (list[str])
    * ``rationale`` (str)
    * ``supporting_notes`` (list[dict|str])
    * ``recommended_outcome`` (one of :data:`VALID_RECOMMENDED_OUTCOMES`)

    Validation:

    * The EDD case must exist (raises :class:`EDDCaseNotFound`).
    * List fields must be lists of JSON-serialisable items.
    * Text fields must be strings or None.
    * ``recommended_outcome``, when present, must be in
      :data:`VALID_RECOMMENDED_OUTCOMES`.

    Behaviour:

    * On first call for an EDD case, INSERTs a new row.
    * On subsequent calls, UPDATEs the existing row in place. A
      structured ``edd.findings.updated`` audit event is emitted with
      before/after state. The first call emits ``edd.findings.created``.

    Audit-writer is REQUIRED (raises :class:`MissingAuditWriter` BEFORE
    any DB write).

    Returns the materialised findings dict (post-write).
    """
    _require_audit_writer(audit_writer)
    if not isinstance(findings, Mapping):
        raise FindingsValidationError(
            "findings must be a mapping/dict"
        )
    # EDD must exist -- raises EDDCaseNotFound on miss.
    _fetch_edd(db, edd_case_id)

    # Validate and normalise every supplied field.
    norm: Dict[str, Any] = {}
    for f in _FINDINGS_TEXT_FIELDS:
        if f in findings:
            norm[f] = _coerce_text_field(findings[f], f)
    for f in _FINDINGS_LIST_FIELDS:
        if f in findings:
            norm[f] = _coerce_list_field(findings[f], f)
    if "recommended_outcome" in findings:
        rec = findings["recommended_outcome"]
        if rec is not None and rec not in VALID_RECOMMENDED_OUTCOMES:
            raise FindingsValidationError(
                f"recommended_outcome={rec!r} not one of "
                f"{VALID_RECOMMENDED_OUTCOMES}"
            )
        norm["recommended_outcome"] = rec

    existing = get_edd_findings(db, edd_case_id)
    ts = _utc_now_iso()
    actor = (user or {}).get("sub") if user else None

    if existing is None:
        # INSERT
        params = (
            edd_case_id,
            norm.get("findings_summary"),
            json.dumps(norm.get("key_concerns", []), default=str),
            json.dumps(norm.get("mitigating_evidence", []), default=str),
            json.dumps(norm.get("conditions", []), default=str),
            norm.get("rationale"),
            json.dumps(norm.get("supporting_notes", []), default=str),
            norm.get("recommended_outcome"),
            actor,
            ts,
            actor,
            ts,
        )
        db.execute(
            "INSERT INTO edd_findings "
            "(edd_case_id, findings_summary, key_concerns, "
            " mitigating_evidence, conditions, rationale, "
            " supporting_notes, recommended_outcome, "
            " created_by, created_at, updated_by, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            params,
        )
        db.commit()
        action = "edd.findings.created"
        before = None
    else:
        # UPDATE -- only overwrite supplied fields.
        merged = {
            "findings_summary": existing.get("findings_summary"),
            "key_concerns": existing.get("key_concerns") or [],
            "mitigating_evidence": existing.get("mitigating_evidence") or [],
            "conditions": existing.get("conditions") or [],
            "rationale": existing.get("rationale"),
            "supporting_notes": existing.get("supporting_notes") or [],
            "recommended_outcome": existing.get("recommended_outcome"),
        }
        merged.update(norm)
        params = (
            merged["findings_summary"],
            json.dumps(merged["key_concerns"], default=str),
            json.dumps(merged["mitigating_evidence"], default=str),
            json.dumps(merged["conditions"], default=str),
            merged["rationale"],
            json.dumps(merged["supporting_notes"], default=str),
            merged["recommended_outcome"],
            actor,
            ts,
            edd_case_id,
        )
        db.execute(
            "UPDATE edd_findings "
            "SET findings_summary = ?, "
            "    key_concerns = ?, "
            "    mitigating_evidence = ?, "
            "    conditions = ?, "
            "    rationale = ?, "
            "    supporting_notes = ?, "
            "    recommended_outcome = ?, "
            "    updated_by = ?, "
            "    updated_at = ? "
            "WHERE edd_case_id = ?",
            params,
        )
        db.commit()
        action = "edd.findings.updated"
        before = {
            "findings_summary": existing.get("findings_summary"),
            "key_concerns": existing.get("key_concerns"),
            "mitigating_evidence": existing.get("mitigating_evidence"),
            "conditions": existing.get("conditions"),
            "rationale": existing.get("rationale"),
            "supporting_notes": existing.get("supporting_notes"),
            "recommended_outcome": existing.get("recommended_outcome"),
        }

    after_row = get_edd_findings(db, edd_case_id)
    after_payload = {
        k: after_row.get(k) for k in (
            "findings_summary", "key_concerns", "mitigating_evidence",
            "conditions", "rationale", "supporting_notes",
            "recommended_outcome",
        )
    }
    _emit_audit(
        audit_writer, user, action,
        f"edd_case:{edd_case_id}",
        {"edd_case_id": edd_case_id, "fields": sorted(norm.keys())},
        db, before_state=before, after_state=after_payload,
    )
    return after_row


# ─────────────────────────────────────────────────────────────────
# Memo-context attachments
# ─────────────────────────────────────────────────────────────────
def _materialise_attachment_row(row) -> Dict[str, Any]:
    d = _row_to_dict(row) or {}
    return {
        "id": d.get("id"),
        "edd_case_id": d.get("edd_case_id"),
        "application_id": d.get("application_id"),
        "memo_context_kind": d.get("memo_context_kind"),
        "memo_id": d.get("memo_id"),
        "periodic_review_id": d.get("periodic_review_id"),
        "attached_by": d.get("attached_by"),
        "attached_at": d.get("attached_at"),
        "detached_by": d.get("detached_by"),
        "detached_at": d.get("detached_at"),
    }


def _find_active_attachment(db, edd_case_id, *,
                            kind: str,
                            memo_id: Optional[int],
                            periodic_review_id: Optional[int],
                            ) -> Optional[Dict[str, Any]]:
    """Return the active (non-detached) attachment matching the key, or None.

    "Active" means ``detached_at IS NULL``. We treat
    (edd_case_id, kind, memo_id, periodic_review_id) as the attachment
    identity and never create a duplicate active row for the same key.
    """
    sql = (
        "SELECT * FROM edd_memo_attachments "
        "WHERE edd_case_id = ? AND memo_context_kind = ? "
        "  AND detached_at IS NULL "
    )
    params: List[Any] = [edd_case_id, kind]
    if memo_id is None:
        sql += "  AND memo_id IS NULL "
    else:
        sql += "  AND memo_id = ? "
        params.append(memo_id)
    if periodic_review_id is None:
        sql += "  AND periodic_review_id IS NULL "
    else:
        sql += "  AND periodic_review_id = ? "
        params.append(periodic_review_id)
    sql += "ORDER BY id DESC LIMIT 1"
    row = db.execute(sql, params).fetchone()
    return _materialise_attachment_row(row) if row else None


def attach_edd_findings_to_memo_context(db, edd_case_id, *,
                                        user=None,
                                        audit_writer=None,
                                        ) -> Dict[str, Any]:
    """Attach an EDD case's findings to the resolved active memo context.

    Resolves the active memo context with
    :func:`resolve_active_memo_context`, then INSERTs (or reuses) an
    ``edd_memo_attachments`` row that records the linkage. The caller
    is expected to have populated structured findings via
    :func:`set_edd_findings` first; this is enforced (raises
    :class:`AttachmentValidationError` when no findings exist).

    Idempotency:

    * If an active attachment already exists for the same
      ``(edd_case_id, kind, memo_id, periodic_review_id)`` tuple, this
      is a no-op (no new row, no audit event), and the existing row is
      returned with ``"reused": True``.
    * If the resolved context has changed since a prior attachment
      (e.g. an EDD that was originally attached to onboarding has now
      been re-linked to a periodic review), a NEW attachment row is
      inserted for the new context. The old attachment is left intact
      so the audit history of the previous linkage is preserved -- use
      :func:`detach_edd_findings_from_memo_context` to explicitly
      detach the old one when that is the intent.

    Audit-writer is REQUIRED. Emits ``edd.memo_context.attached`` on
    create with full before/after state.

    Returns a dict::

        {
            "attachment": <materialised attachment row>,
            "context": <resolved memo context>,
            "created": bool,
            "reused": bool,
        }

    Raises:

    * :class:`EDDCaseNotFound` -- via the resolver.
    * :class:`MemoContextResolutionError` -- via the resolver.
    * :class:`AttachmentValidationError` -- when no findings exist yet
      for the EDD case.
    """
    _require_audit_writer(audit_writer)
    findings = get_edd_findings(db, edd_case_id)
    if findings is None:
        raise AttachmentValidationError(
            f"edd_case id={edd_case_id} has no structured findings; "
            "call set_edd_findings(...) before attaching"
        )

    context = resolve_active_memo_context(db, edd_case_id)
    kind = context["kind"]
    memo_id = context.get("memo_id")
    periodic_review_id = context.get("periodic_review_id")
    application_id = context["application_id"]

    existing = _find_active_attachment(
        db, edd_case_id, kind=kind,
        memo_id=memo_id, periodic_review_id=periodic_review_id,
    )
    if existing is not None:
        return {
            "attachment": existing,
            "context": context,
            "created": False,
            "reused": True,
        }

    actor = (user or {}).get("sub") if user else None
    ts = _utc_now_iso()
    db.execute(
        "INSERT INTO edd_memo_attachments "
        "(edd_case_id, application_id, memo_context_kind, memo_id, "
        " periodic_review_id, attached_by, attached_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (edd_case_id, application_id, kind, memo_id,
         periodic_review_id, actor, ts),
    )
    db.commit()
    new_row = _find_active_attachment(
        db, edd_case_id, kind=kind,
        memo_id=memo_id, periodic_review_id=periodic_review_id,
    )

    _emit_audit(
        audit_writer, user, "edd.memo_context.attached",
        f"edd_case:{edd_case_id}",
        {
            "edd_case_id": edd_case_id,
            "application_id": application_id,
            "memo_context_kind": kind,
            "memo_id": memo_id,
            "periodic_review_id": periodic_review_id,
            "resolution_reason": context["resolution_reason"],
            "findings_recommended_outcome": findings.get(
                "recommended_outcome"
            ),
        },
        db,
        before_state={"attachment": None},
        after_state={"attachment": new_row},
    )
    return {
        "attachment": new_row,
        "context": context,
        "created": True,
        "reused": False,
    }


def detach_edd_findings_from_memo_context(db, edd_case_id, *,
                                          memo_context_kind: Optional[str] = None,
                                          memo_id: Optional[int] = None,
                                          periodic_review_id: Optional[int] = None,
                                          user=None,
                                          audit_writer=None,
                                          ) -> List[Dict[str, Any]]:
    """Detach EDD-findings attachment(s) from a memo context.

    If ``memo_context_kind`` (and optionally ``memo_id`` /
    ``periodic_review_id``) is supplied, only the matching active
    attachment is detached. If only ``edd_case_id`` is supplied, every
    active attachment for that EDD case is detached.

    Detachment is a soft-update: ``detached_at`` and ``detached_by``
    are set; the row is preserved for audit history. Idempotent: if no
    active attachment matches, the call is a no-op and NO audit event
    is emitted (mirrors the PR-01 unlink-helper convention).

    Audit-writer is REQUIRED. On every actual detachment, emits a
    structured ``edd.memo_context.detached`` event.

    Returns the list of materialised attachment rows that were
    detached (empty list on no-op).
    """
    _require_audit_writer(audit_writer)
    if memo_context_kind is not None and memo_context_kind not in VALID_MEMO_CONTEXT_KINDS:
        raise AttachmentValidationError(
            f"memo_context_kind={memo_context_kind!r} not one of "
            f"{VALID_MEMO_CONTEXT_KINDS}"
        )

    sql = (
        "SELECT * FROM edd_memo_attachments "
        "WHERE edd_case_id = ? AND detached_at IS NULL"
    )
    params: List[Any] = [edd_case_id]
    if memo_context_kind is not None:
        sql += " AND memo_context_kind = ?"
        params.append(memo_context_kind)
    if memo_id is not None:
        sql += " AND memo_id = ?"
        params.append(memo_id)
    if periodic_review_id is not None:
        sql += " AND periodic_review_id = ?"
        params.append(periodic_review_id)
    rows = db.execute(sql, params).fetchall() or []
    if not rows:
        return []

    actor = (user or {}).get("sub") if user else None
    ts = _utc_now_iso()
    detached: List[Dict[str, Any]] = []
    for row in rows:
        before = _materialise_attachment_row(row)
        db.execute(
            "UPDATE edd_memo_attachments "
            "SET detached_at = ?, detached_by = ? "
            "WHERE id = ? AND detached_at IS NULL",
            (ts, actor, before["id"]),
        )
        after = dict(before)
        after["detached_at"] = ts
        after["detached_by"] = actor
        detached.append(after)
        _emit_audit(
            audit_writer, user, "edd.memo_context.detached",
            f"edd_case:{edd_case_id}",
            {
                "edd_case_id": edd_case_id,
                "attachment_id": before["id"],
                "memo_context_kind": before["memo_context_kind"],
                "memo_id": before["memo_id"],
                "periodic_review_id": before["periodic_review_id"],
            },
            db, before_state=before, after_state=after,
        )
    db.commit()
    return detached


# ─────────────────────────────────────────────────────────────────
# Read helpers for memo-side consumption
# ─────────────────────────────────────────────────────────────────
def get_memo_context_attachments(db, *,
                                 kind: str,
                                 memo_id: Optional[int] = None,
                                 periodic_review_id: Optional[int] = None,
                                 application_id: Optional[str] = None,
                                 include_detached: bool = False,
                                 ) -> List[Dict[str, Any]]:
    """List attachments belonging to a memo context.

    Used by downstream memo assembly / officer UI to ask:
    "what EDD cases feed this decision artifact?".

    At least one of ``memo_id``, ``periodic_review_id`` or
    ``application_id`` should be supplied; calling this with only
    ``kind`` returns every attachment of that kind across the system
    and is intentionally allowed for back-office tooling.
    """
    if kind not in VALID_MEMO_CONTEXT_KINDS:
        raise AttachmentValidationError(
            f"kind={kind!r} not one of {VALID_MEMO_CONTEXT_KINDS}"
        )
    sql = (
        "SELECT * FROM edd_memo_attachments "
        "WHERE memo_context_kind = ?"
    )
    params: List[Any] = [kind]
    if memo_id is not None:
        sql += " AND memo_id = ?"
        params.append(memo_id)
    if periodic_review_id is not None:
        sql += " AND periodic_review_id = ?"
        params.append(periodic_review_id)
    if application_id is not None:
        sql += " AND application_id = ?"
        params.append(application_id)
    if not include_detached:
        sql += " AND detached_at IS NULL"
    sql += " ORDER BY attached_at ASC, id ASC"
    rows = db.execute(sql, params).fetchall() or []
    return [_materialise_attachment_row(r) for r in rows]


def get_memo_context_findings(db, *,
                              kind: str,
                              memo_id: Optional[int] = None,
                              periodic_review_id: Optional[int] = None,
                              application_id: Optional[str] = None,
                              include_detached: bool = False,
                              ) -> List[Dict[str, Any]]:
    """List structured findings attached to a memo context.

    Convenience wrapper around :func:`get_memo_context_attachments` +
    :func:`get_edd_findings`: returns the materialised findings dicts
    for every attached EDD case, in attachment order. EDD cases that
    have an attachment but no recorded findings (which should not
    happen in normal flow because :func:`attach_edd_findings_to_memo_context`
    refuses) are skipped silently in the returned list to keep memo
    assembly defensive.

    Each returned dict is enriched with ``attachment`` (the linkage
    row) so downstream consumers can show provenance.
    """
    attachments = get_memo_context_attachments(
        db, kind=kind, memo_id=memo_id,
        periodic_review_id=periodic_review_id,
        application_id=application_id,
        include_detached=include_detached,
    )
    out: List[Dict[str, Any]] = []
    for att in attachments:
        f = get_edd_findings(db, att["edd_case_id"])
        if f is None:
            continue
        f = dict(f)
        f["attachment"] = att
        out.append(f)
    return out


__all__ = [
    # Vocabularies
    "MEMO_CONTEXT_ONBOARDING",
    "MEMO_CONTEXT_PERIODIC_REVIEW",
    "VALID_MEMO_CONTEXT_KINDS",
    "RECOMMENDED_OUTCOME_APPROVE",
    "RECOMMENDED_OUTCOME_APPROVE_WITH_CONDITIONS",
    "RECOMMENDED_OUTCOME_REJECT",
    "RECOMMENDED_OUTCOME_ESCALATE",
    "VALID_RECOMMENDED_OUTCOMES",
    # Exceptions
    "EDDMemoIntegrationError",
    "EDDCaseNotFound",
    "FindingsValidationError",
    "MemoContextResolutionError",
    "AttachmentValidationError",
    # Helpers
    "resolve_active_memo_context",
    "get_edd_findings",
    "set_edd_findings",
    "attach_edd_findings_to_memo_context",
    "detach_edd_findings_from_memo_context",
    "get_memo_context_attachments",
    "get_memo_context_findings",
]
