from __future__ import annotations

import json
from datetime import datetime, timezone

from enhanced_requirements import (
    _actor_user_fk_value,
    _application_target,
    _audit_user,
    _insert_audit,
    decorate_application_requirements_for_backoffice,
    serialize_application_requirement,
    serialize_portal_application_requirement,
)
from periodic_review_attestation import QUESTION_INDEX


CONDITIONAL_DOCUMENT_MAPPING_VERSION = "prs3_v1"
PERIODIC_REVIEW_DOCUMENT_GENERATION_SOURCE = "portal_periodic_review_attestation_submit"
PERIODIC_REVIEW_TRIGGER_CATEGORY = "periodic_review_attestation"
PERIODIC_REVIEW_PORTAL_VISIBLE_STATUSES = (
    "requested",
    "uploaded",
    "under_review",
    "accepted",
    "rejected",
)


QUESTION_REQUIREMENT_MAP = {
    "directors_changed": [
        {
            "requirement_key": "updated_register_of_directors",
            "requirement_label": "Updated Register of Directors",
            "requirement_description": "Upload the latest register of directors reflecting the declared director changes.",
            "subject_scope": "director",
            "mandatory": True,
        },
        {
            "requirement_key": "new_director_id_document",
            "requirement_label": "ID document for new director",
            "requirement_description": "Upload an identification document for each newly appointed director, where applicable.",
            "subject_scope": "director",
            "mandatory": False,
        },
        {
            "requirement_key": "new_director_proof_of_address",
            "requirement_label": "Proof of address for new director",
            "requirement_description": "Upload proof of address for each newly appointed director, where applicable.",
            "subject_scope": "director",
            "mandatory": False,
        },
    ],
    "shareholders_changed": [
        {
            "requirement_key": "updated_register_of_shareholders",
            "requirement_label": "Updated Register of Shareholders",
            "requirement_description": "Upload the latest register of shareholders reflecting the declared ownership changes.",
            "subject_scope": "company",
            "mandatory": True,
        },
        {
            "requirement_key": "updated_cap_table",
            "requirement_label": "Updated cap table or shareholding structure",
            "requirement_description": "Upload the updated cap table or shareholding structure.",
            "subject_scope": "company",
            "mandatory": True,
        },
        {
            "requirement_key": "share_transfer_or_allotment_evidence",
            "requirement_label": "Share transfer or allotment evidence",
            "requirement_description": "Upload share transfer, allotment, or equivalent supporting evidence where applicable.",
            "subject_scope": "company",
            "mandatory": False,
        },
    ],
    "ubos_changed": [
        {
            "requirement_key": "updated_ownership_chart",
            "requirement_label": "Updated ownership chart",
            "requirement_description": "Upload the latest ownership chart reflecting the declared UBO or control changes.",
            "subject_scope": "ubo",
            "mandatory": True,
        },
        {
            "requirement_key": "ubo_identification_document",
            "requirement_label": "UBO identification document",
            "requirement_description": "Upload an identification document for each new or changed UBO/controller.",
            "subject_scope": "ubo",
            "mandatory": True,
        },
        {
            "requirement_key": "ubo_proof_of_address",
            "requirement_label": "UBO proof of address",
            "requirement_description": "Upload proof of address for each new or changed UBO/controller.",
            "subject_scope": "ubo",
            "mandatory": True,
        },
        {
            "requirement_key": "proof_of_ownership_or_control",
            "requirement_label": "Proof of ownership or control",
            "requirement_description": "Upload documents evidencing the updated ownership or control position.",
            "subject_scope": "ubo",
            "mandatory": True,
        },
    ],
    "business_activity_changed": [
        {
            "requirement_key": "updated_business_activity_description",
            "requirement_label": "Updated business activity description",
            "requirement_description": "Upload a document describing the updated business activity.",
            "subject_scope": "company",
            "mandatory": True,
        },
        {
            "requirement_key": "website_product_operating_evidence",
            "requirement_label": "Website, product, or operating evidence",
            "requirement_description": "Upload website screenshots, product materials, or operating evidence supporting the updated activity.",
            "subject_scope": "company",
            "mandatory": False,
        },
        {
            "requirement_key": "contracts_invoices_or_commercial_evidence",
            "requirement_label": "Contracts, invoices, or commercial evidence",
            "requirement_description": "Upload contracts, invoices, or commercial evidence supporting the updated activity where relevant.",
            "subject_scope": "company",
            "mandatory": False,
        },
        {
            "requirement_key": "regulated_activity_licence_or_approval",
            "requirement_label": "Licence or approval for regulated activity",
            "requirement_description": "Upload the relevant licence or approval if the updated activity is regulated.",
            "subject_scope": "company",
            "mandatory": False,
        },
    ],
    "jurisdictions_changed": [
        {
            "requirement_key": "jurisdiction_rationale",
            "requirement_label": "Jurisdiction rationale",
            "requirement_description": "Upload a document explaining the rationale for the updated operating or target jurisdictions.",
            "subject_scope": "application",
            "mandatory": True,
        },
        {
            "requirement_key": "operating_countries_target_markets_list",
            "requirement_label": "Operating countries or target markets list",
            "requirement_description": "Upload the updated list of operating countries, client countries, or target markets.",
            "subject_scope": "application",
            "mandatory": True,
        },
        {
            "requirement_key": "market_operations_supporting_evidence",
            "requirement_label": "Supporting evidence for new market operations",
            "requirement_description": "Upload evidence supporting the updated market operations where relevant.",
            "subject_scope": "application",
            "mandatory": False,
        },
    ],
    "transaction_volume_changed": [
        {
            "requirement_key": "updated_transaction_volume_rationale",
            "requirement_label": "Updated transaction volume rationale",
            "requirement_description": "Upload a document explaining the updated transaction volume or value expectations.",
            "subject_scope": "application",
            "mandatory": True,
        },
        {
            "requirement_key": "expected_transaction_flow_explanation",
            "requirement_label": "Expected transaction flow explanation",
            "requirement_description": "Upload an explanation of the expected transaction flows after the declared change.",
            "subject_scope": "application",
            "mandatory": True,
        },
        {
            "requirement_key": "financials_bank_statements_or_projections",
            "requirement_label": "Bank statements, management accounts, or projections",
            "requirement_description": "Upload recent bank statements, management accounts, or business projections where relevant.",
            "subject_scope": "company",
            "mandatory": False,
        },
    ],
    "licence_regulatory_status_changed": [
        {
            "requirement_key": "licence_or_registration_certificate",
            "requirement_label": "Licence or registration certificate",
            "requirement_description": "Upload the updated licence or registration certificate.",
            "subject_scope": "company",
            "mandatory": True,
        },
        {
            "requirement_key": "regulator_approval_or_correspondence",
            "requirement_label": "Regulator approval letter or correspondence",
            "requirement_description": "Upload regulator approval letters or correspondence relevant to the declared change.",
            "subject_scope": "company",
            "mandatory": False,
        },
        {
            "requirement_key": "updated_regulatory_disclosure",
            "requirement_label": "Updated regulatory disclosure",
            "requirement_description": "Upload the updated regulatory disclosure.",
            "subject_scope": "company",
            "mandatory": True,
        },
    ],
    "company_contact_details_correct": [
        {
            "requirement_key": "updated_company_extract",
            "requirement_label": "Updated company extract",
            "requirement_description": "Upload the updated company extract if the company details have changed.",
            "subject_scope": "company",
            "mandatory": False,
        },
        {
            "requirement_key": "updated_registered_office_proof",
            "requirement_label": "Updated proof of registered office or address",
            "requirement_description": "Upload proof of the updated registered office or address where applicable.",
            "subject_scope": "company",
            "mandatory": False,
        },
        {
            "requirement_key": "updated_authorised_contact_confirmation",
            "requirement_label": "Updated authorised contact confirmation",
            "requirement_description": "Upload confirmation of the updated authorised contact where applicable.",
            "subject_scope": "company",
            "mandatory": False,
        },
    ],
}


def _row_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return row


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value):
    try:
        return int(value)
    except Exception:
        return None


def _review_trigger_key(review_id, question_key):
    return f"periodic_review_{review_id}_{question_key}"


def _requirement_sort_tuple(item):
    return (
        str(item.get("trigger_question_key") or ""),
        str(item.get("requirement_label") or ""),
        str(item.get("id") or ""),
    )


def _build_trigger_reason(question, answer_comment):
    label = str((question or {}).get("label") or "Periodic review attestation change").strip()
    comment = str(answer_comment or "").strip()
    if comment:
        return f"{label} Client comment: {comment}"
    return label


def _build_trigger_context(app, review, question, answer_entry, generation_source):
    question = question or {}
    answer_entry = answer_entry or {}
    return {
        "application_id": app.get("id"),
        "application_ref": app.get("ref"),
        "client_id": app.get("client_id"),
        "periodic_review_id": review.get("id"),
        "review_reference": f"PR-{review.get('id')}",
        "timeline": "Periodic Review",
        "lifecycle_stage": "Periodic Review",
        "source_surface": generation_source,
        "generation_source": generation_source,
        "trigger_question_key": question.get("key"),
        "trigger_question_label": question.get("label"),
        "attestation_status": "submitted",
        "attestation_answer": answer_entry.get("answer"),
        "attestation_comment": answer_entry.get("comment") or "",
        "mapping_version": CONDITIONAL_DOCUMENT_MAPPING_VERSION,
    }


def _build_requirement_payload(app, review, question, answer_entry, template, *, actor_fk, now, generation_source):
    trigger_key = _review_trigger_key(review.get("id"), question.get("key"))
    trigger_context = _build_trigger_context(app, review, question, answer_entry, generation_source)
    return {
        "application_id": app.get("id"),
        "trigger_key": trigger_key,
        "trigger_label": question.get("label") or question.get("key"),
        "trigger_category": PERIODIC_REVIEW_TRIGGER_CATEGORY,
        "requirement_key": template["requirement_key"],
        "requirement_label": template["requirement_label"],
        "requirement_description": template["requirement_description"],
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": template.get("subject_scope") or "application",
        "blocking_approval": 0,
        "waivable": 1,
        "waiver_roles": json.dumps([]),
        "mandatory": 1 if template.get("mandatory") else 0,
        "status": "requested",
        "generation_source": generation_source,
        "trigger_reason": _build_trigger_reason(question, answer_entry.get("comment")),
        "trigger_context": json.dumps(trigger_context, sort_keys=True),
        "linked_periodic_review_id": review.get("id"),
        "requested_at": now,
        "requested_by": actor_fk,
        "created_at": now,
        "created_by": actor_fk,
        "updated_at": now,
        "updated_by": actor_fk,
    }


def _select_existing_requirement(db, application_id, trigger_key, requirement_key):
    row = db.execute(
        """
        SELECT *
        FROM application_enhanced_requirements
        WHERE application_id = ?
          AND trigger_key = ?
          AND requirement_key = ?
        LIMIT 1
        """,
        (application_id, trigger_key, requirement_key),
    ).fetchone()
    return serialize_application_requirement(row)


def _insert_periodic_review_requirement(db, payload):
    db.execute(
        """
        INSERT INTO application_enhanced_requirements
        (
            application_id,
            trigger_key,
            trigger_label,
            trigger_category,
            requirement_key,
            requirement_label,
            requirement_description,
            audience,
            requirement_type,
            subject_scope,
            blocking_approval,
            waivable,
            waiver_roles,
            mandatory,
            status,
            generation_source,
            trigger_reason,
            trigger_context,
            linked_periodic_review_id,
            requested_at,
            requested_by,
            created_at,
            created_by,
            updated_at,
            updated_by
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload["application_id"],
            payload["trigger_key"],
            payload["trigger_label"],
            payload["trigger_category"],
            payload["requirement_key"],
            payload["requirement_label"],
            payload["requirement_description"],
            payload["audience"],
            payload["requirement_type"],
            payload["subject_scope"],
            payload["blocking_approval"],
            payload["waivable"],
            payload["waiver_roles"],
            payload["mandatory"],
            payload["status"],
            payload["generation_source"],
            payload["trigger_reason"],
            payload["trigger_context"],
            payload["linked_periodic_review_id"],
            payload["requested_at"],
            payload["requested_by"],
            payload["created_at"],
            payload["created_by"],
            payload["updated_at"],
            payload["updated_by"],
        ),
    )
    return _select_existing_requirement(
        db,
        payload["application_id"],
        payload["trigger_key"],
        payload["requirement_key"],
    )


def _list_review_rows(db, application_id, review_id):
    placeholders = ",".join(["?"] * len(PERIODIC_REVIEW_PORTAL_VISIBLE_STATUSES))
    return db.execute(
        f"""
        SELECT aer.*, err.active AS source_rule_active
        FROM application_enhanced_requirements aer
        LEFT JOIN enhanced_requirement_rules err ON err.id = aer.source_rule_id
        WHERE aer.application_id = ?
          AND aer.linked_periodic_review_id = ?
          AND aer.active = 1
          AND aer.status IN ({placeholders})
        ORDER BY aer.requested_at DESC, aer.updated_at DESC, aer.requirement_label, aer.id
        """,
        (application_id, review_id, *PERIODIC_REVIEW_PORTAL_VISIBLE_STATUSES),
    ).fetchall()


def _serialize_portal_review_requirement(db, row):
    safe = serialize_portal_application_requirement(db, row)
    if not safe:
        return None
    requirement = serialize_application_requirement(row)
    if not requirement:
        return None
    safe.update({
        "linked_periodic_review_id": requirement.get("linked_periodic_review_id"),
        "trigger_question_key": requirement.get("trigger_question_key"),
        "trigger_question_label": requirement.get("trigger_question_label") or requirement.get("trigger_label"),
        "trigger_reason": requirement.get("trigger_reason") or "",
        "mandatory": bool(requirement.get("mandatory")),
    })
    return safe


def list_portal_periodic_review_document_requests(db, application_id, review_id):
    rows = _list_review_rows(db, application_id, review_id)
    items = []
    for row in rows:
        safe = _serialize_portal_review_requirement(db, row)
        if safe:
            items.append(safe)
    return items


def list_backoffice_periodic_review_document_requests(db, app, review_id):
    rows = db.execute(
        """
        SELECT aer.*, err.active AS source_rule_active
        FROM application_enhanced_requirements aer
        LEFT JOIN enhanced_requirement_rules err ON err.id = aer.source_rule_id
        WHERE aer.application_id = ?
          AND aer.linked_periodic_review_id = ?
          AND aer.active = 1
        ORDER BY aer.requested_at DESC, aer.updated_at DESC, aer.requirement_label, aer.id
        """,
        (app.get("id"), review_id),
    ).fetchall()
    requirements = [serialize_application_requirement(row) for row in rows]
    requirements = [item for item in requirements if item]
    return decorate_application_requirements_for_backoffice(db, app, requirements)


def generate_periodic_review_document_requests(
    db,
    review,
    app,
    attestation_snapshot,
    *,
    actor=None,
    generation_source=PERIODIC_REVIEW_DOCUMENT_GENERATION_SOURCE,
):
    review = _row_dict(review) or {}
    app = _row_dict(app) or {}
    attestation_snapshot = dict(attestation_snapshot or {})
    actor_meta = _audit_user(actor)
    actor_fk = _actor_user_fk_value(db, actor)
    now = _now_iso()
    answers_by_key = {
        str(item.get("key")): item
        for item in (attestation_snapshot.get("questions") or [])
        if isinstance(item, dict) and item.get("key")
    }
    material_keys = [
        key for key in (attestation_snapshot.get("material_change_question_keys") or [])
        if key in QUESTION_REQUIREMENT_MAP
    ]
    created = []
    deduped = []
    target = _application_target(app)

    for question_key in material_keys:
        question = QUESTION_INDEX.get(question_key) or {"key": question_key, "label": question_key}
        answer_entry = answers_by_key.get(question_key) or {}
        for template in QUESTION_REQUIREMENT_MAP.get(question_key, []):
            payload = _build_requirement_payload(
                app,
                review,
                question,
                answer_entry,
                template,
                actor_fk=actor_fk,
                now=now,
                generation_source=generation_source,
            )
            existing = _select_existing_requirement(
                db,
                app.get("id"),
                payload["trigger_key"],
                payload["requirement_key"],
            )
            if existing:
                existing["trigger_question_key"] = question_key
                deduped.append(existing)
                _insert_audit(
                    db,
                    "periodic_review_document_request_deduped",
                    target,
                    {
                        "event": "periodic_review_document_request_deduped",
                        "periodic_review_id": review.get("id"),
                        "application_id": app.get("id"),
                        "application_ref": app.get("ref"),
                        "client_id": app.get("client_id"),
                        "source_attestation_status": attestation_snapshot.get("status"),
                        "source_attestation_submitted_at": attestation_snapshot.get("submitted_at"),
                        "trigger_question_key": question_key,
                        "trigger_question_label": question.get("label"),
                        "requirement_id": existing.get("id"),
                        "requirement_key": existing.get("requirement_key"),
                        "requirement_label": existing.get("requirement_label"),
                        "requirement_status": existing.get("status"),
                        "actor_user_id": actor_meta.get("sub"),
                        "actor_role": actor_meta.get("role"),
                        "source_surface": generation_source,
                        "mapping_version": CONDITIONAL_DOCUMENT_MAPPING_VERSION,
                    },
                    actor=actor,
                    after_state=existing,
                )
                continue

            inserted = _insert_periodic_review_requirement(db, payload)
            inserted["trigger_question_key"] = question_key
            created.append(inserted)
            _insert_audit(
                db,
                "periodic_review_document_request_created",
                target,
                {
                    "event": "periodic_review_document_request_created",
                    "periodic_review_id": review.get("id"),
                    "application_id": app.get("id"),
                    "application_ref": app.get("ref"),
                    "client_id": app.get("client_id"),
                    "source_attestation_status": attestation_snapshot.get("status"),
                    "source_attestation_submitted_at": attestation_snapshot.get("submitted_at"),
                    "trigger_question_key": question_key,
                    "trigger_question_label": question.get("label"),
                    "requirement_id": inserted.get("id"),
                    "requirement_key": inserted.get("requirement_key"),
                    "requirement_label": inserted.get("requirement_label"),
                    "actor_user_id": actor_meta.get("sub"),
                    "actor_role": actor_meta.get("role"),
                    "source_surface": generation_source,
                    "mapping_version": CONDITIONAL_DOCUMENT_MAPPING_VERSION,
                },
                actor=actor,
                after_state=inserted,
            )

    generation_detail = {
        "event": "periodic_review_document_requests_generated",
        "periodic_review_id": review.get("id"),
        "application_id": app.get("id"),
        "application_ref": app.get("ref"),
        "client_id": app.get("client_id"),
        "source_attestation_status": attestation_snapshot.get("status"),
        "source_attestation_submitted_at": attestation_snapshot.get("submitted_at"),
        "triggering_question_keys": material_keys,
        "created_request_ids": [item.get("id") for item in created],
        "created_requirement_keys": [item.get("requirement_key") for item in created],
        "deduped_request_ids": [item.get("id") for item in deduped],
        "deduped_requirement_keys": [item.get("requirement_key") for item in deduped],
        "actor_user_id": actor_meta.get("sub"),
        "actor_role": actor_meta.get("role"),
        "source_surface": generation_source,
        "mapping_version": CONDITIONAL_DOCUMENT_MAPPING_VERSION,
        "generated_count": len(created),
        "deduped_count": len(deduped),
    }
    _insert_audit(
        db,
        "periodic_review_document_requests_generated",
        target,
        generation_detail,
        actor=actor,
        after_state={
            "created_request_ids": generation_detail["created_request_ids"],
            "deduped_request_ids": generation_detail["deduped_request_ids"],
        },
    )

    created.sort(key=_requirement_sort_tuple)
    deduped.sort(key=_requirement_sort_tuple)
    return {
        "created": created,
        "deduped": deduped,
        "created_request_ids": generation_detail["created_request_ids"],
        "deduped_request_ids": generation_detail["deduped_request_ids"],
        "triggering_question_keys": material_keys,
        "generated_count": len(created),
        "deduped_count": len(deduped),
        "mapping_version": CONDITIONAL_DOCUMENT_MAPPING_VERSION,
    }


def emit_periodic_review_document_uploaded_audit(
    db,
    app,
    requirement,
    document_id,
    *,
    actor=None,
    source_surface,
    before_state=None,
    after_state=None,
):
    requirement = serialize_application_requirement(requirement) if requirement and not isinstance(requirement, dict) else dict(requirement or {})
    linked_review_id = _safe_int(requirement.get("linked_periodic_review_id"))
    if linked_review_id is None:
        return False
    target = _application_target(app)
    actor_meta = _audit_user(actor)
    _insert_audit(
        db,
        "periodic_review_document_uploaded",
        target,
        {
            "event": "periodic_review_document_uploaded",
            "periodic_review_id": linked_review_id,
            "application_id": app.get("id"),
            "application_ref": app.get("ref"),
            "client_id": app.get("client_id"),
            "requirement_id": requirement.get("id"),
            "requirement_key": requirement.get("requirement_key"),
            "requirement_label": requirement.get("requirement_label"),
            "document_id": document_id,
            "requirement_status": requirement.get("status"),
            "actor_user_id": actor_meta.get("sub"),
            "actor_role": actor_meta.get("role"),
            "source_surface": source_surface,
        },
        actor=actor,
        before_state=before_state,
        after_state=after_state or requirement,
    )
    return True
