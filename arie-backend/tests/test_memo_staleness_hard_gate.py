import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _valid_screening_payload():
    now = datetime.now(timezone.utc)
    return {
        "screening_report": {
            "screening_mode": "live",
            "screened_at": now.isoformat(),
            "sanctions": {"api_status": "live", "matched": False, "source": "sumsub"},
            "kyc": {"api_status": "live", "source": "sumsub"},
        },
        "screening_valid_until": (now + timedelta(days=30)).isoformat(),
    }


def _insert_gate_ready_app(db):
    suffix = uuid.uuid4().hex[:8]
    app_id = f"memo_stale_{suffix}"
    ref = f"ARF-MEMO-STALE-{suffix}"
    client_id = f"client_{suffix}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"{client_id}@example.test", "hash", "Memo Stale Ltd"),
    )
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type,
         status, risk_level, final_risk_level, risk_score, prescreening_data,
         submitted_at, updated_at, inputs_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            ref,
            client_id,
            "Memo Stale Ltd",
            "Mauritius",
            "Technology",
            "Limited Company",
            "in_review",
            "MEDIUM",
            "MEDIUM",
            42,
            json.dumps(_valid_screening_payload()),
            now,
            now,
            now,
        ),
    )
    db.execute(
        """
        INSERT INTO idv_resolutions
        (id, application_id, application_ref, person_id, person_type, person_name,
         prior_provider_status, prior_review_answer, resolution_status, resolution_outcome,
         reason_code, evidence_reviewed, rationale, confirmation_text, senior_approver_id,
         resolved_by, resolved_by_name, resolved_by_role, ip_address, user_agent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"idv_memo_stale_{suffix}",
            app_id,
            ref,
            client_id,
            "client",
            "Memo Stale Ltd",
            "pending",
            "",
            "manual_verified",
            "manual_verification_completed",
            "other",
            json.dumps(["corporate_documents"]),
            "Manual verification recorded for memo staleness gate fixture.",
            "I confirm I have reviewed the evidence and accept responsibility for this IDV resolution.",
            "",
            "admin001",
            "Admin",
            "admin",
            "127.0.0.1",
            "pytest",
            now,
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
        doc_id = f"doc_memo_stale_{suffix}_{doc_type}"
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
    return app_id, ref


def _insert_approved_memo(db, app_id, *, created_at=None, raw_output_hash=None, memo_metadata=None):
    memo_data = {
        "ai_source": "deterministic",
        "metadata": {"ai_source": "deterministic", **(memo_metadata or {})},
        "supervisor": {"verdict": "CONSISTENT", "can_approve": True},
    }
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, review_status, validation_status,
         supervisor_status, quality_score, approved_by, approved_at,
         raw_output_hash, approval_reason, created_at)
        VALUES (?, ?, 'approved', 'pass', 'CONSISTENT', 9.1, 'admin001',
                ?, ?, ?, ?)
        """,
        (
            app_id,
            json.dumps(memo_data),
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            raw_output_hash,
            "Fixture approval reason",
            created_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    db.commit()


def _set_ca_entity_screening(db, app_id):
    now = datetime.now(timezone.utc)
    prescreening = {
        "screening_report": {
            "screening_mode": "live",
            "screened_at": now.isoformat(),
            "provider": "complyadvantage",
            "source": "complyadvantage",
            "total_hits": 0,
            "adverse_media_coverage": "none",
            "company_screening": {
                "provider": "complyadvantage",
                "source": "complyadvantage",
                "matched": False,
                "sanctions": {
                    "api_status": "live",
                    "matched": False,
                    "source": "complyadvantage",
                    "provider": "complyadvantage",
                    "results": [],
                },
                "adverse_media": {
                    "api_status": "live",
                    "matched": False,
                    "source": "complyadvantage",
                    "provider": "complyadvantage",
                    "results": [],
                },
            },
        },
        "screening_valid_until": (now + timedelta(days=30)).isoformat(),
    }
    db.execute(
        "UPDATE applications SET prescreening_data = ?, inputs_updated_at = updated_at WHERE id = ?",
        (json.dumps(prescreening), app_id),
    )
    db.commit()


def _insert_ca_entity_adverse_media_evidence(db, app_id):
    suffix = app_id.replace("-", "_")
    source_reference = {
        "provider": "complyadvantage",
        "case_identifier": f"case-ca4b-{suffix}",
        "alert_identifier": f"alert-ca4b-{suffix}",
        "risk_identifier": f"risk-ca4b-{suffix}",
        "subject_scope": "entity",
    }
    db.execute(
        """
        INSERT INTO monitoring_alerts
            (application_id, client_name, alert_type, severity, detected_by,
             summary, source_reference, status, provider, case_identifier, discovered_via)
        VALUES (?, 'Memo Stale Ltd', 'media', 'High', 'complyadvantage',
                'ComplyAdvantage Mesh adverse-media match', ?, 'open',
                'complyadvantage', ?, 'manual')
        """,
        (app_id, json.dumps(source_reference), f"case-ca4b-{suffix}"),
    )
    alert_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    db.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, case_identifier, alert_identifier,
             risk_identifier, profile_identifier, evidence_type, matched_subject_name,
             relationship_to_client, match_category, risk_indicator, match_confidence,
             source_title, source_name, source_url, source_url_available, publication_date,
             snippet, evidence_json, raw_provider_reference, evidence_status, evidence_hash, fetched_at)
        VALUES (?, ?, 'complyadvantage', ?, ?,
                ?, ?, 'adverse_media', 'Memo Stale Ltd',
                'entity', 'Adverse Media', 'Adverse Media', '0.95',
                'Provider adverse-media article', 'Provider News',
                'https://provider.example.test/article', 1, '2026-06-01',
                'Provider adverse-media snippet for memo parity.',
                ?, ?, 'fetched', 'hash-ca4b-evidence', ?)
        """,
        (
            alert_id,
            app_id,
            f"case-ca4b-{suffix}",
            f"alert-ca4b-{suffix}",
            f"risk-ca4b-{suffix}",
            f"profile-ca4b-{suffix}",
            json.dumps({"title": "Provider adverse-media article", "source_name": "Provider News"}),
            json.dumps({"risk_identifier": f"risk-ca4b-{suffix}", "profile_identifier": f"profile-ca4b-{suffix}"}),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
    )
    db.commit()


def _app(db, app_id):
    return dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())


def _latest_memo(db, app_id):
    return dict(db.execute(
        "SELECT * FROM compliance_memos WHERE application_id = ? ORDER BY id DESC LIMIT 1",
        (app_id,),
    ).fetchone())


def test_fresh_schema_has_memo_staleness_columns(db):
    columns = {row["name"] for row in db.execute("PRAGMA table_info(compliance_memos)").fetchall()}
    assert {"is_stale", "stale_reason", "stale_reasons", "stale_trigger", "stale_marked_at"}.issubset(columns)


@pytest.mark.parametrize(
    ("trigger", "reason"),
    [
        ("screening_disposition_changed", "Screening disposition changed."),
        ("edd_status_or_requirements_changed", "EDD status changed."),
        ("risk_recomputed", "Final risk changed after recomputation."),
        ("enhanced_requirements_generated", "Enhanced requirement generation changed."),
    ],
)
def test_material_stale_triggers_reset_memo_and_block_final_approval(db, trigger, reason):
    from security_hardening import ApprovalGateValidator
    from server import _mark_latest_memo_stale

    app_id, ref = _insert_gate_ready_app(db)
    _insert_approved_memo(db, app_id)
    allowed_before, before_msg = ApprovalGateValidator.validate_approval(_app(db, app_id), db)
    assert allowed_before is True, before_msg

    result = _mark_latest_memo_stale(
        db,
        app_id,
        trigger=trigger,
        reason=reason,
        actor={"sub": "admin001", "name": "Admin", "role": "admin"},
        app_ref=ref,
        before_state={"before": True},
        after_state={"after": True},
    )
    db.commit()
    assert result["marked"] is True

    memo = _latest_memo(db, app_id)
    assert memo["is_stale"] in (1, True)
    assert memo["stale_trigger"] == trigger
    assert memo["review_status"] == "draft"
    assert memo["validation_status"] == "pending"
    assert memo["supervisor_status"] == "pending"
    assert memo["approved_by"] is None

    allowed_after, after_msg = ApprovalGateValidator.validate_approval(_app(db, app_id), db)
    assert allowed_after is False
    assert "stale" in after_msg.lower()

    audit = db.execute(
        "SELECT detail FROM audit_log WHERE target = ? AND action = 'Memo Marked Stale' ORDER BY id DESC LIMIT 1",
        (ref,),
    ).fetchone()
    assert audit is not None
    assert trigger in audit["detail"]


def test_regenerated_revalidated_supervisor_reapproved_memo_restores_approval_eligibility(db):
    from security_hardening import ApprovalGateValidator
    from server import _mark_latest_memo_stale

    app_id, ref = _insert_gate_ready_app(db)
    _insert_approved_memo(db, app_id)
    _mark_latest_memo_stale(
        db,
        app_id,
        trigger="risk_recomputed",
        reason="Risk recomputation changed final risk.",
        actor={"sub": "admin001", "name": "Admin", "role": "admin"},
        app_ref=ref,
    )
    db.commit()
    blocked, blocked_msg = ApprovalGateValidator.validate_approval(_app(db, app_id), db)
    assert blocked is False
    assert "stale" in blocked_msg.lower()

    # Simulates the required recovery path: regenerate a new memo, rerun
    # validation, rerun supervisor, and approve/sign off the fresh memo.
    _insert_approved_memo(db, app_id)
    allowed, msg = ApprovalGateValidator.validate_approval(_app(db, app_id), db)
    assert allowed is True, msg


def test_input_timestamp_staleness_is_persisted_when_memo_approval_checks_freshness(db):
    from server import _ensure_memo_fresh_or_mark_stale

    app_id, ref = _insert_gate_ready_app(db)
    old_memo_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    _insert_approved_memo(db, app_id, created_at=old_memo_time)
    db.execute(
        "UPDATE applications SET inputs_updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), app_id),
    )
    app = _app(db, app_id)
    memo = _latest_memo(db, app_id)
    stale = _ensure_memo_fresh_or_mark_stale(
        db,
        app,
        memo,
        actor={"sub": "admin001", "name": "Admin", "role": "admin"},
        ip_address="127.0.0.1",
        context="memo_approval",
    )
    db.commit()

    assert stale["is_stale"] is True
    memo_after = _latest_memo(db, app_id)
    assert memo_after["is_stale"] in (1, True)
    assert memo_after["stale_trigger"] == "application_inputs_changed_after_memo"
    audit = db.execute(
        "SELECT detail FROM audit_log WHERE target = ? AND action = 'Memo Marked Stale' ORDER BY id DESC LIMIT 1",
        (ref,),
    ).fetchone()
    assert audit is not None
    assert "application_inputs_changed_after_memo" in audit["detail"]


def test_risk_snapshot_mismatch_marks_memo_stale_and_blocks_approval(db):
    from security_hardening import ApprovalGateValidator
    from server import _ensure_memo_fresh_or_mark_stale

    app_id, ref = _insert_gate_ready_app(db)
    db.execute(
        """
        UPDATE applications
        SET risk_score = 70,
            risk_level = 'VERY_HIGH',
            final_risk_level = 'VERY_HIGH',
            risk_computed_at = '2026-06-09T08:00:00Z'
        WHERE id = ?
        """,
        (app_id,),
    )
    _insert_approved_memo(
        db,
        app_id,
        memo_metadata={
            "canonical_risk": {"available": True, "level": "MEDIUM", "score": 42},
            "display_risk_rating": "MEDIUM",
            "display_risk_score": 42,
            "risk_rating": "MEDIUM",
            "risk_score": 42,
        },
    )

    stale = _ensure_memo_fresh_or_mark_stale(
        db,
        _app(db, app_id),
        _latest_memo(db, app_id),
        actor={"sub": "admin001", "name": "Admin", "role": "admin"},
        ip_address="127.0.0.1",
        context="memo_approval",
    )
    db.commit()

    assert stale["is_stale"] is True
    assert stale["trigger"] == "memo_risk_snapshot_mismatch"
    memo_after = _latest_memo(db, app_id)
    assert memo_after["is_stale"] in (1, True)
    assert memo_after["stale_trigger"] == "memo_risk_snapshot_mismatch"

    allowed, msg = ApprovalGateValidator.validate_approval(_app(db, app_id), db)
    assert allowed is False
    assert "stale" in msg.lower()

    audit = db.execute(
        "SELECT detail FROM audit_log WHERE target = ? AND action = 'Memo Marked Stale' ORDER BY id DESC LIMIT 1",
        (ref,),
    ).fetchone()
    assert audit is not None
    assert "memo_risk_snapshot_mismatch" in audit["detail"]


def test_ca_adverse_media_current_truth_marks_old_no_media_memo_stale(db):
    from server import _attach_memo_screening_current_snapshot, _ensure_memo_fresh_or_mark_stale, _memo_staleness_view

    app_id, ref = _insert_gate_ready_app(db)
    _set_ca_entity_screening(db, app_id)
    _insert_ca_entity_adverse_media_evidence(db, app_id)
    _insert_approved_memo(
        db,
        app_id,
        memo_metadata={
            "adverse_media_state_summary": {
                "coverage": "none",
                "has_hit": False,
                "terminal": False,
            },
            "canonical_screening_current_summary": {
                "current_risk_count": 0,
                "current_unresolved_risk_count": 0,
                "has_adverse_media_hit": False,
                "adverse_media_coverage": "none",
            },
        },
    )

    app = _attach_memo_screening_current_snapshot(db, _app(db, app_id))
    snapshot = app["memo_screening_current_snapshot"]
    assert snapshot["has_adverse_media_hit"] is True
    assert snapshot["current_risk_count"] >= 1
    assert snapshot["current_unresolved_risk_count"] == snapshot["current_risk_count"]

    stale_view = _memo_staleness_view(app, _latest_memo(db, app_id))
    assert stale_view["is_stale"] is True
    assert stale_view["trigger"] == "memo_screening_adverse_media_truth_mismatch"
    assert "ComplyAdvantage Mesh evidence" in stale_view["reason"]

    persisted = _ensure_memo_fresh_or_mark_stale(
        db,
        _app(db, app_id),
        _latest_memo(db, app_id),
        actor={"sub": "admin001", "name": "Admin", "role": "admin"},
        ip_address="127.0.0.1",
        context="memo_approval",
    )
    db.commit()

    assert persisted["is_stale"] is True
    memo_after = _latest_memo(db, app_id)
    assert memo_after["is_stale"] in (1, True)
    assert memo_after["stale_trigger"] == "memo_screening_adverse_media_truth_mismatch"
    audit = db.execute(
        "SELECT before_state, after_state FROM audit_log WHERE target = ? AND action = 'Memo Marked Stale' ORDER BY id DESC LIMIT 1",
        (ref,),
    ).fetchone()
    assert audit is not None
    assert "memo_adverse_media_state_summary" in audit["before_state"]
    assert "current_canonical_screening_summary" in audit["after_state"]


def test_regenerated_memo_consumes_db_backed_ca_adverse_media_snapshot(db):
    from memo_handler import build_compliance_memo
    from server import _attach_memo_screening_current_snapshot

    app_id, _ = _insert_gate_ready_app(db)
    _set_ca_entity_screening(db, app_id)
    _insert_ca_entity_adverse_media_evidence(db, app_id)

    app = _attach_memo_screening_current_snapshot(db, _app(db, app_id))
    memo, _, _, _ = build_compliance_memo(
        app,
        [],
        [],
        [{"id": "doc-ca4b", "doc_type": "cert_inc", "verification_status": "verified"}],
    )

    adverse = memo["metadata"]["adverse_media_state_summary"]
    assert adverse["coverage"] == "provider_evidence"
    assert adverse["has_hit"] is True
    assert "adverse_media_hit" in memo["metadata"]["risk_evidence"]["financial_crime"]["triggers"]
