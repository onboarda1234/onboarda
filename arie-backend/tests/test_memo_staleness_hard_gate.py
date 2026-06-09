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
         raw_output_hash, created_at)
        VALUES (?, ?, 'approved', 'pass', 'CONSISTENT', 9.1, 'admin001',
                ?, ?, ?)
        """,
        (
            app_id,
            json.dumps(memo_data),
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            raw_output_hash,
            created_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
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
