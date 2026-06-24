"""
Tests for ApprovalGateValidator memo ordering fix (EX-06).

When multiple compliance_memos rows share the same version value,
the validator must select the newest row (by created_at DESC, id DESC)
— the same row that MemoApproveHandler approves.
"""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone


def _make_prescreening():
    now = datetime.now(timezone.utc)
    return json.dumps({
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
    })


def _insert_app(db):
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-memo-order-{suffix}"
    app_ref = f"ARF-MEMO-ORDER-{suffix}"
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            app_ref,
            f"client-memo-order-{suffix}",
            "Memo Ordering Test Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "submitted_to_compliance",
            "MEDIUM",
            45,
            _make_prescreening(),
        ),
    )
    verified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    for doc_type in (
        "cert_inc",
        "memarts",
        "reg_sh",
        "reg_dir",
        "fin_stmt",
        "poa",
        "board_res",
        "structure_chart",
    ):
        doc_id = f"doc-memo-order-{suffix}-{doc_type}"
        db.execute(
            """
            INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path, slot_key,
             verification_status, verification_results, verified_at)
            VALUES (?, ?, ?, ?, ?, ?, 'verified', ?, ?)
            """,
            (
                doc_id,
                app_id,
                doc_type,
                f"{doc_type}.pdf",
                f"/tmp/{doc_type}.pdf",
                f"entity:{doc_type}",
                json.dumps({"overall": "verified", "checks": [{"result": "pass"}], "verified_at": verified_at}),
                verified_at,
            ),
        )
        db.execute(
            """
            INSERT INTO agent_executions
            (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
            VALUES (?, ?, 'verify_document', 1, 'verified', ?, 0)
            """,
            (app_id, doc_id, json.dumps([{"result": "pass"}])),
        )
    db.commit()
    return app_id


def _insert_memo(db, app_id, *, review_status, version="v1.0",
                 validation_status="pass", supervisor_status="CONSISTENT",
                 approval_reason=None):
    """Insert a compliance memo. Each call sleeps 0.01s to guarantee distinct created_at."""
    time.sleep(0.01)
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation,
         review_status, quality_score, validation_status, supervisor_status,
         memo_version, approval_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            json.dumps({"ai_source": "deterministic", "metadata": {"ai_source": "deterministic"}}),
            "system",
            "APPROVE_WITH_CONDITIONS",
            review_status,
            8.5,
            validation_status,
            supervisor_status,
            version,
            approval_reason,
        ),
    )
    db.commit()


def test_newer_approved_memo_allows_decision(db):
    """
    Two memos with same version.  Older = draft, newer = approved.
    ApprovalGateValidator must pick the newer approved memo → allow.
    """
    from security_hardening import ApprovalGateValidator

    app_id = _insert_app(db)

    # Older memo — draft
    _insert_memo(db, app_id, review_status="draft", version="v1.0")
    # Newer memo — approved
    _insert_memo(
        db,
        app_id,
        review_status="approved",
        version="v1.0",
        approval_reason="Canonical memo approved for ordering test",
    )

    app = dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, f"Expected approval to pass, got: {msg}"


def test_newer_draft_memo_blocks_decision(db):
    """
    Two memos with same version.  Older = approved, newer = draft.
    ApprovalGateValidator must pick the newer draft memo → block.
    """
    from security_hardening import ApprovalGateValidator

    app_id = _insert_app(db)

    # Older memo — approved
    _insert_memo(
        db,
        app_id,
        review_status="approved",
        version="v1.0",
        approval_reason="Older memo approved for ordering test",
    )
    # Newer memo — draft
    _insert_memo(db, app_id, review_status="draft", version="v1.0")

    app = dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "draft" in msg.lower()
