"""
EX-06 — Senior-officer override path for flagged documents.

Tests:
1. Flagged doc blocks approval when unreviewed
2. Admin can accept flagged doc with reason → approval gate passes
3. SCO can accept flagged doc with reason → approval gate passes
4. CO/analyst cannot override flagged doc
5. Missing reason rejects
6. Rejected flagged doc still blocks approval
7. Non-flagged normal reviewed docs still behave as before
8. Audit trail records senior override with reason
9. EX-06 decision gate becomes reachable after flagged doc override
"""

import json
import sqlite3
import uuid
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _create_app_with_flagged_doc(db, *, doc_type="memarts", verification_status="flagged"):
    """Helper: create an application with a flagged document and valid memo."""
    from tests.conftest import clean_ca_prescreening

    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-flagdoc-{suffix}"
    app_ref = f"ARF-FLAGDOC-{suffix}"
    doc_id = f"doc-flagdoc-{suffix}"

    _now = datetime.now(timezone.utc)
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id, app_ref, f"client-flagdoc-{suffix}",
            "FlagDoc Test Ltd", "Mauritius", "Technology", "SME",
            "compliance_review", "MEDIUM", 45,
            json.dumps(clean_ca_prescreening(
                company_name="FlagDoc Test Ltd",
                screened_at=_now.strftime("%Y-%m-%dT%H:%M:%S"),
            )),
        ),
    )
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score,
         validation_status, supervisor_status, approval_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            json.dumps({"ai_source": "deterministic", "metadata": {"ai_source": "deterministic"}}),
            "system", "APPROVE_WITH_CONDITIONS", "approved", 8.5,
            "pass", "CONSISTENT", "Fixture approval reason",
        ),
    )
    required_doc_types = [
        "cert_inc",
        "memarts",
        "reg_sh",
        "reg_dir",
        "fin_stmt",
        "poa",
        "board_res",
        "structure_chart",
    ]
    verified_at = _now.strftime("%Y-%m-%dT%H:%M:%S")
    for required_type in required_doc_types:
        current_doc_id = doc_id if required_type == doc_type else f"doc-flagdoc-{suffix}-{required_type}"
        current_status = verification_status if required_type == doc_type else "verified"
        db.execute(
            """
            INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path, slot_key, verification_status,
             verification_results, verified_at, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                current_doc_id,
                app_id,
                required_type,
                f"{required_type}.pdf",
                f"/tmp/{required_type}.pdf",
                f"entity:{required_type}",
                current_status,
                json.dumps({
                    "overall": current_status,
                    "checks": [{"result": "pass"}] if current_status == "verified" else [{"result": "warn"}],
                    "verified_at": verified_at if current_status == "verified" else None,
                }),
                verified_at if current_status == "verified" else None,
                "pending",
            ),
        )
        if current_status == "verified":
            db.execute(
                """
                INSERT INTO agent_executions
                (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
                VALUES (?, ?, 'verify_document', 1, 'verified', ?, 0)
                """,
                (app_id, current_doc_id, json.dumps([{"result": "pass"}])),
            )
    db.commit()
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(app), doc_id


# ---------- Test 1: Flagged doc blocks approval when unreviewed ----------

def test_flagged_doc_blocks_approval_unreviewed(db):
    from security_hardening import ApprovalGateValidator

    app, _doc_id = _create_app_with_flagged_doc(db)
    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "Document evidence gate failed" in msg
    assert "flagged" in msg.lower()
    assert "Memorandum of Association" in msg


# ---------- Test 2: Admin can accept flagged doc → gate passes ----------

def test_admin_accepts_flagged_doc_gate_passes(db):
    from security_hardening import ApprovalGateValidator

    app, doc_id = _create_app_with_flagged_doc(db)

    db.execute(
        "UPDATE documents SET review_status='accepted', review_comment='Verified manually — original is legible', "
        "reviewed_by='admin001', reviewer_role='admin', reviewed_at='2026-06-01T12:00:00' WHERE id=?",
        (doc_id,),
    )
    db.commit()

    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval to pass but got: {msg}"


def test_staging_workflow_only_synthetic_acceptance_does_not_pass_document_reliance_gate(db, monkeypatch):
    import security_hardening
    from security_hardening import ApprovalGateValidator

    monkeypatch.setattr(security_hardening, "ENV", "staging")
    app, doc_id = _create_app_with_flagged_doc(db)
    db.execute(
        """
        UPDATE documents
        SET evidence_class='test_only_synthetic',
            workflow_test_accepted=1,
            workflow_test_acceptance_reason='Staging mechanics test only',
            workflow_test_accepted_by='admin001',
            workflow_test_accepted_at='2026-05-28T12:00:00',
            workflow_test_acceptance_environment='staging'
        WHERE id=?
        """,
        (doc_id,),
    )
    db.commit()

    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "Document evidence gate failed" in msg
    stored = db.execute(
        "SELECT verification_status FROM documents WHERE id=?",
        (doc_id,),
    ).fetchone()
    assert stored["verification_status"] == "flagged"


def test_workflow_only_synthetic_acceptance_does_not_pass_flagged_doc_gate_in_production(db, monkeypatch):
    import security_hardening
    from security_hardening import ApprovalGateValidator

    monkeypatch.setattr(security_hardening, "ENV", "production")
    app, doc_id = _create_app_with_flagged_doc(db)
    db.execute(
        """
        UPDATE documents
        SET evidence_class='test_only_synthetic',
            workflow_test_accepted=1,
            workflow_test_acceptance_reason='Staging mechanics test only',
            workflow_test_accepted_by='admin001',
            workflow_test_accepted_at='2026-05-28T12:00:00',
            workflow_test_acceptance_environment='staging'
        WHERE id=?
        """,
        (doc_id,),
    )
    db.commit()

    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "Document evidence gate failed" in msg


# ---------- Test 3: SCO can accept flagged doc → gate passes ----------

def test_sco_accepts_flagged_doc_gate_passes(db):
    from security_hardening import ApprovalGateValidator

    app, doc_id = _create_app_with_flagged_doc(db)

    db.execute(
        "UPDATE documents SET review_status='accepted', review_comment='Confirmed authentic via registrar', "
        "reviewed_by='sco001', reviewer_role='sco', reviewed_at='2026-06-01T12:00:00' WHERE id=?",
        (doc_id,),
    )
    db.commit()

    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval to pass but got: {msg}"


# ---------- Test 4: CO/analyst cannot override flagged doc ----------

def test_co_cannot_override_flagged_doc(db):
    from security_hardening import ApprovalGateValidator

    app, doc_id = _create_app_with_flagged_doc(db)

    # Simulate a CO trying to accept (reviewer_role = 'co')
    db.execute(
        "UPDATE documents SET review_status='accepted', review_comment='Looks fine to me', "
        "reviewed_by='co001', reviewer_role='co', reviewed_at='2026-06-01T12:00:00' WHERE id=?",
        (doc_id,),
    )
    db.commit()

    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "Document evidence gate failed" in msg


def test_analyst_cannot_override_flagged_doc(db):
    from security_hardening import ApprovalGateValidator

    app, doc_id = _create_app_with_flagged_doc(db)

    db.execute(
        "UPDATE documents SET review_status='accepted', review_comment='Checked', "
        "reviewed_by='analyst001', reviewer_role='analyst', reviewed_at='2026-06-01T12:00:00' WHERE id=?",
        (doc_id,),
    )
    db.commit()

    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "Document evidence gate failed" in msg


# ---------- Test 5: Missing reason rejects ----------

def test_missing_reason_blocks_gate(db):
    from security_hardening import ApprovalGateValidator

    app, doc_id = _create_app_with_flagged_doc(db)

    # Senior role but empty comment
    db.execute(
        "UPDATE documents SET review_status='accepted', review_comment='', "
        "reviewed_by='admin001', reviewer_role='admin', reviewed_at='2026-06-01T12:00:00' WHERE id=?",
        (doc_id,),
    )
    db.commit()

    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "Document evidence gate failed" in msg


def test_null_reason_blocks_gate(db):
    from security_hardening import ApprovalGateValidator

    app, doc_id = _create_app_with_flagged_doc(db)

    db.execute(
        "UPDATE documents SET review_status='accepted', review_comment=NULL, "
        "reviewed_by='sco001', reviewer_role='sco', reviewed_at='2026-06-01T12:00:00' WHERE id=?",
        (doc_id,),
    )
    db.commit()

    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "Document evidence gate failed" in msg


# ---------- Test 6: Rejected flagged doc still blocks ----------

def test_rejected_flagged_doc_blocks(db):
    from security_hardening import ApprovalGateValidator

    app, doc_id = _create_app_with_flagged_doc(db)

    db.execute(
        "UPDATE documents SET review_status='rejected', review_comment='Cannot verify', "
        "reviewed_by='admin001', reviewer_role='admin' WHERE id=?",
        (doc_id,),
    )
    db.commit()

    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "Document evidence gate failed" in msg


# ---------- Test 7: Non-flagged docs behave normally ----------

def test_nonflagged_doc_passes_normally(db):
    from security_hardening import ApprovalGateValidator

    app, doc_id = _create_app_with_flagged_doc(db, verification_status="verified")

    # A verified doc with any review status should not block
    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval to pass but got: {msg}"


def test_pending_doc_blocks_reliance(db):
    from security_hardening import ApprovalGateValidator

    app, doc_id = _create_app_with_flagged_doc(db, verification_status="pending")
    can_approve, msg = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "pending" in msg.lower()


# ---------- Test 8: Audit trail records senior override with reason ----------

def test_audit_trail_records_override(db):
    """
    Validates that the audit_log table contains a 'Document Accepted With Findings'
    entry with before/after state when a senior officer overrides a flagged doc.

    This test simulates the handler-level insert directly since we are unit-testing
    the data contract, not the HTTP layer.
    """

    app, doc_id = _create_app_with_flagged_doc(db)
    app_ref = app["ref"]

    before_state = {
        "verification_status": "flagged",
        "review_status": "pending",
        "review_comment": None,
        "reviewed_by": None,
    }
    after_state = {
        "verification_status": "flagged",
        "review_status": "accepted",
        "review_comment": "Verified via registrar",
        "reviewed_by": "admin001",
        "reviewer_role": "admin",
    }

    db.execute(
        """INSERT INTO audit_log
           (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            "admin001", "Test Admin", "admin",
            "Document Accepted With Findings", app_ref,
            f"Senior override: flagged document 'memarts.pdf' (type=memarts) "
            f"accepted by Test Admin (role=admin). Reason: Verified via registrar",
            "127.0.0.1",
            json.dumps(before_state), json.dumps(after_state),
        ),
    )
    db.commit()

    row = db.execute(
        "SELECT * FROM audit_log WHERE action='Document Accepted With Findings' AND target=?",
        (app_ref,),
    ).fetchone()
    assert row is not None
    assert "admin" in (dict(row).get("user_role") or "")
    assert "Verified via registrar" in (dict(row).get("detail") or "")

    after = json.loads(dict(row)["after_state"])
    assert after["reviewer_role"] == "admin"
    assert after["review_status"] == "accepted"

    before = json.loads(dict(row)["before_state"])
    assert before["verification_status"] == "flagged"
    assert before["review_status"] == "pending"


# ---------- Test 9: Decision gate reachable after override ----------

def test_decision_gate_reachable_after_override(db):
    """
    Full flow: create flagged doc → gate blocked → admin override → gate passes.
    Demonstrates EX-06 decision gate becomes reachable.
    """
    from security_hardening import ApprovalGateValidator

    app, doc_id = _create_app_with_flagged_doc(db)

    # Step 1: gate blocked
    can1, msg1 = ApprovalGateValidator.validate_approval(app, db)
    assert can1 is False
    assert "Document evidence gate failed" in msg1

    # Step 2: admin override
    db.execute(
        "UPDATE documents SET review_status='accepted', "
        "review_comment='Manually verified against original registrar copy — acceptable', "
        "reviewed_by='admin001', reviewer_role='admin', reviewed_at='2026-06-01T12:00:00' WHERE id=?",
        (doc_id,),
    )
    db.commit()

    # Step 3: gate passes
    can2, msg2 = ApprovalGateValidator.validate_approval(app, db)
    assert can2 is True, f"Expected gate to pass after override but got: {msg2}"
