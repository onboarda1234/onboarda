import json
import uuid
from datetime import datetime, timedelta, timezone


REQUIRED_DOC_TYPES = (
    "cert_inc",
    "memarts",
    "reg_sh",
    "reg_dir",
    "fin_stmt",
    "poa",
    "board_res",
    "structure_chart",
)


def _screening_payload():
    now = datetime.now(timezone.utc)
    return {
        "screening_report": {
            "screening_mode": "live",
            "screened_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "sanctions": {"api_status": "live"},
            "company_registry": {"api_status": "live"},
            "ip_geolocation": {"api_status": "live"},
            "kyc": {"api_status": "live"},
        },
        "screening_valid_until": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
        "screening_validity_days": 90,
    }


def _insert_app(db):
    suffix = uuid.uuid4().hex[:8]
    app_id = f"doc-reliance-{suffix}"
    ref = f"ARF-DOC-RELIANCE-{suffix}"
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status,
         risk_level, final_risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, 'Document Reliance Ltd', 'Mauritius', 'Technology',
                'SME', 'compliance_review', 'MEDIUM', 'MEDIUM', 42, ?)
        """,
        (app_id, ref, f"client-doc-reliance-{suffix}", json.dumps(_screening_payload())),
    )
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation, review_status,
         quality_score, validation_status, supervisor_status, approval_reason)
        VALUES (?, ?, 'system', 'APPROVE_WITH_CONDITIONS', 'approved',
                9.0, 'pass', 'CONSISTENT', 'Fixture approval reason')
        """,
        (
            app_id,
            json.dumps({
                "ai_source": "deterministic",
                "metadata": {"ai_source": "deterministic"},
                "supervisor": {"verdict": "CONSISTENT", "can_approve": True},
            }),
        ),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())


def _insert_document(
    db,
    app_id,
    doc_type,
    *,
    status="verified",
    slot_key=None,
    results=True,
    verified_at=None,
    agent_execution=True,
    manual_acceptance=False,
    reviewer_role="admin",
    review_comment="Manual acceptance reason",
    reviewed_at="2026-06-01T12:00:00",
):
    doc_id = f"doc-{app_id}-{doc_type}-{uuid.uuid4().hex[:6]}"
    if verified_at is None and status == "verified":
        verified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    result_payload = {}
    if results:
        result_payload = {
            "overall": "verified" if status == "verified" else status,
            "checks": [{"result": "pass"}] if status == "verified" else [{"result": "warn"}],
            "verified_at": verified_at,
        }
    db.execute(
        """
        INSERT INTO documents
        (id, application_id, doc_type, doc_name, file_path, slot_key,
         verification_status, verification_results, verified_at,
         review_status, review_comment, reviewed_by, reviewer_role, reviewed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            app_id,
            doc_type,
            f"{doc_type}.pdf",
            f"/tmp/{doc_type}.pdf",
            slot_key or f"entity:{doc_type}",
            status,
            json.dumps(result_payload),
            verified_at,
            "accepted" if manual_acceptance else "pending",
            review_comment if manual_acceptance else "",
            "admin001" if manual_acceptance else "",
            reviewer_role if manual_acceptance else "",
            reviewed_at if manual_acceptance else None,
        ),
    )
    if agent_execution:
        db.execute(
            """
            INSERT INTO agent_executions
            (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
            VALUES (?, ?, 'verify_document', 1, ?, ?, ?)
            """,
            (
                app_id,
                doc_id,
                "verified" if status == "verified" else status,
                json.dumps([{"result": "pass"}] if status == "verified" else [{"result": "warn"}]),
                0 if status == "verified" else 1,
            ),
        )
    return doc_id


def _insert_required_documents(db, app_id, **overrides):
    target_type = overrides.pop("target_type", None)
    target_doc_id = None
    for doc_type in REQUIRED_DOC_TYPES:
        kwargs = dict(overrides) if doc_type == target_type else {}
        doc_id = _insert_document(db, app_id, doc_type, **kwargs)
        if doc_type == target_type:
            target_doc_id = doc_id
    db.commit()
    return target_doc_id


def _insert_rmi_item(db, app_id, *, item_id, request_id, doc_id, doc_type="reg_sh",
                     label="Replacement required for entity:reg_sh", item_status="accepted",
                     request_status="fulfilled"):
    app = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    db.execute(
        """
        INSERT INTO rmi_requests
        (id, application_id, client_id, status, reason, deadline, created_by, created_by_name, fulfilled_at)
        VALUES (?, ?, ?, ?, 'Missing evidence replacement', '2026-12-31', 'admin001', 'Admin', datetime('now'))
        """,
        (request_id, app_id, app["client_id"], request_status),
    )
    db.execute(
        """
        INSERT INTO rmi_request_items
        (id, request_id, doc_type, label, description, status, document_id, uploaded_at, reviewed_at)
        VALUES (?, ?, ?, ?, '', ?, ?, datetime('now'), datetime('now'))
        """,
        (item_id, request_id, doc_type, label, item_status, doc_id),
    )


def _gate(db, app):
    from document_reliance_gate import evaluate_document_reliance_gate

    return evaluate_document_reliance_gate(db, app, stage="unit_test")


def test_verified_required_documents_pass_with_agent_execution_proof(db):
    app = _insert_app(db)
    _insert_required_documents(db, app["id"])

    gate = _gate(db, app)

    assert gate["passed"] is True
    assert gate["reliance_status"] == "ready"
    assert gate["satisfied_required_count"] == gate["required_count"]
    assert all(item["agent_execution_id"] for item in gate["documents"])


def test_optional_pending_document_does_not_block_required_reliance(db):
    app = _insert_app(db)
    _insert_required_documents(db, app["id"])
    _insert_document(db, app["id"], "supporting_document", status="pending", agent_execution=False)
    db.commit()

    gate = _gate(db, app)

    assert gate["passed"] is True


def test_manual_accepted_required_document_passes_with_governance(db):
    app = _insert_app(db)
    doc_id = _insert_required_documents(
        db,
        app["id"],
        target_type="cert_inc",
        status="flagged",
        agent_execution=False,
        manual_acceptance=True,
    )

    gate = _gate(db, app)
    accepted = [item for item in gate["documents"] if item.get("document_id") == doc_id][0]

    assert gate["passed"] is True
    assert accepted["reliance_state"] == "manual_accepted"
    assert accepted["manual_acceptance"]["reason"]


def test_manual_accepted_without_reason_or_role_is_rejected(db):
    app = _insert_app(db)
    _insert_required_documents(
        db,
        app["id"],
        target_type="cert_inc",
        status="flagged",
        agent_execution=False,
        manual_acceptance=True,
        reviewer_role="co",
        review_comment="",
    )

    gate = _gate(db, app)

    assert gate["passed"] is False
    assert any(blocker["code"] == "document_flagged" for blocker in gate["blockers"])


def test_gate_blocks_pending_failed_skipped_and_stale_documents(db):
    cases = [
        ("pending", "document_pending_verification"),
        ("failed", "document_verification_failed"),
        ("skipped", "document_verification_skipped"),
    ]
    for status, code in cases:
        app = _insert_app(db)
        _insert_required_documents(db, app["id"], target_type="cert_inc", status=status, agent_execution=False)

        gate = _gate(db, app)

        assert gate["passed"] is False
        assert any(blocker["code"] == code for blocker in gate["blockers"])

    app = _insert_app(db)
    stale_at = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%S")
    _insert_required_documents(db, app["id"], target_type="cert_inc", verified_at=stale_at)

    gate = _gate(db, app)

    assert gate["passed"] is False
    assert any(blocker["code"] == "stale_verification" for blocker in gate["blockers"])


def test_gate_blocks_missing_results_verified_at_agent_execution_and_unsupported_type(db):
    app = _insert_app(db)
    _insert_required_documents(db, app["id"], target_type="cert_inc", results=False)
    gate = _gate(db, app)
    assert any(blocker["code"] == "missing_verification_results" for blocker in gate["blockers"])

    app = _insert_app(db)
    _insert_required_documents(db, app["id"], target_type="cert_inc", verified_at="")
    gate = _gate(db, app)
    assert any(blocker["code"] == "missing_verified_at" for blocker in gate["blockers"])

    app = _insert_app(db)
    _insert_required_documents(db, app["id"], target_type="cert_inc", agent_execution=False)
    gate = _gate(db, app)
    assert any(blocker["code"] == "missing_agent_execution_proof" for blocker in gate["blockers"])

    app = _insert_app(db)
    for doc_type in REQUIRED_DOC_TYPES:
        if doc_type == "cert_inc":
            _insert_document(db, app["id"], "unknown_policy_doc", slot_key="entity:cert_inc")
        else:
            _insert_document(db, app["id"], doc_type)
    db.commit()
    gate = _gate(db, app)
    assert any(blocker["code"] == "unsupported_document_type" for blocker in gate["blockers"])


def test_gate_payload_is_json_serializable_with_postgres_datetime_rows(db):
    from document_reliance_gate import evaluate_document_reliance_gate

    app = _insert_app(db)
    _insert_required_documents(db, app["id"])
    docs = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM documents WHERE application_id=?",
            (app["id"],),
        ).fetchall()
    ]
    pg_timestamp = datetime.now(timezone.utc)
    for doc in docs:
        if doc.get("doc_type") == "cert_inc":
            doc["verified_at"] = pg_timestamp
            doc["verification_results"] = {
                "overall": "verified",
                "checks": [{"result": "pass"}],
                "verified_at": pg_timestamp,
            }

    gate = evaluate_document_reliance_gate(db, app, stage="postgres_datetime_unit", documents=docs)

    json.dumps(gate)
    cert_snapshot = next(
        item for item in gate["documents"]
        if item.get("required_document_type") == "cert_inc"
    )
    assert isinstance(cert_snapshot["verified_at"], str)
    assert isinstance(cert_snapshot["verification_results"]["verified_at"], str)


def test_approval_gate_blocks_document_evidence_and_passes_when_fixed(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_app(db)
    _insert_required_documents(db, app["id"], target_type="cert_inc", status="failed", agent_execution=False)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "Document evidence gate failed" in message

    app = _insert_app(db)
    _insert_required_documents(db, app["id"])
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, message


def test_rmi_replacement_alias_satisfies_canonical_required_slot(db):
    app = _insert_app(db)
    for doc_type in REQUIRED_DOC_TYPES:
        if doc_type != "reg_sh":
            _insert_document(db, app["id"], doc_type)
    item_id = f"rmi-item-{uuid.uuid4().hex[:8]}"
    doc_id = _insert_document(
        db,
        app["id"],
        "reg_sh",
        slot_key=f"rmi:{item_id}",
    )
    _insert_rmi_item(db, app["id"], item_id=item_id, request_id=f"rmi-{uuid.uuid4().hex[:8]}", doc_id=doc_id)
    db.commit()

    gate = _gate(db, app)
    shareholder = next(item for item in gate["documents"] if item["slot_key"] == "entity:reg_sh")

    assert gate["passed"] is True
    assert shareholder["document_id"] == doc_id
    assert shareholder["canonical_slot_satisfied_by_rmi"] is True
    assert shareholder["rmi_trace"]["rmi_item_id"] == item_id
    assert gate["rmi_slot_aliases"][0]["canonical_slot_key"] == "entity:reg_sh"


def test_rejected_rmi_replacement_does_not_satisfy_canonical_slot(db):
    app = _insert_app(db)
    for doc_type in REQUIRED_DOC_TYPES:
        if doc_type != "reg_sh":
            _insert_document(db, app["id"], doc_type)
    item_id = f"rmi-item-{uuid.uuid4().hex[:8]}"
    doc_id = _insert_document(
        db,
        app["id"],
        "reg_sh",
        slot_key=f"rmi:{item_id}",
    )
    _insert_rmi_item(
        db,
        app["id"],
        item_id=item_id,
        request_id=f"rmi-{uuid.uuid4().hex[:8]}",
        doc_id=doc_id,
        item_status="rejected",
        request_status="pending_review",
    )
    db.commit()

    gate = _gate(db, app)

    assert gate["passed"] is False
    assert any(
        blocker["code"] == "missing_required_document" and blocker["slot_key"] == "entity:reg_sh"
        for blocker in gate["blockers"]
    )


def test_memo_generation_stage_sees_rmi_replacement_alias(db):
    from document_reliance_gate import evaluate_document_reliance_gate

    app = _insert_app(db)
    for doc_type in REQUIRED_DOC_TYPES:
        if doc_type != "reg_sh":
            _insert_document(db, app["id"], doc_type)
    item_id = f"rmi-item-{uuid.uuid4().hex[:8]}"
    doc_id = _insert_document(db, app["id"], "reg_sh", slot_key=f"rmi:{item_id}")
    _insert_rmi_item(db, app["id"], item_id=item_id, request_id=f"rmi-{uuid.uuid4().hex[:8]}", doc_id=doc_id)
    db.commit()

    gate = evaluate_document_reliance_gate(db, app, stage="memo_generation")

    assert gate["passed"] is True
    assert not any(
        blocker["code"] == "missing_required_document" and blocker["slot_key"] == "entity:reg_sh"
        for blocker in gate["blockers"]
    )
