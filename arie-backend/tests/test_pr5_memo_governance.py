"""PR-5 regression tests for canonical memo governance and approval reason UX."""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone


def _live_prescreening():
    now = datetime.now(timezone.utc)
    return json.dumps(
        {
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
    )


def _insert_app(db):
    from tests.conftest import insert_verified_required_documents

    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-pr5-{suffix}"
    app_ref = f"ARF-PR5-{suffix}"
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type,
         status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            app_ref,
            f"client-pr5-{suffix}",
            "PR5 Memo Governance Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "submitted_to_compliance",
            "MEDIUM",
            42,
            _live_prescreening(),
        ),
    )
    insert_verified_required_documents(db, app_id)
    db.commit()
    return app_id, app_ref


def _insert_memo(
    db,
    app_id,
    *,
    version,
    created_at,
    review_status="draft",
    validation_status="pass",
    supervisor_status="CONSISTENT",
    approval_reason=None,
):
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, version, memo_data, generated_by, ai_recommendation,
         review_status, quality_score, validation_status, supervisor_status,
         approval_reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            version,
            json.dumps(
                {
                    "sections": {"executive_summary": {"content": f"Memo version {version}"}},
                    "metadata": {"ai_source": "deterministic"},
                    "supervisor": {"verdict": supervisor_status, "can_approve": True},
                }
            ),
            "system",
            "APPROVE_WITH_CONDITIONS",
            review_status,
            8.5,
            validation_status,
            supervisor_status,
            approval_reason,
            created_at,
        ),
    )
    db.commit()
    return db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(path):
    with open(os.path.join(_repo_root(), path), "r", encoding="utf-8") as handle:
        return handle.read()


def test_canonical_latest_memo_orders_by_version_then_created_at_then_id(db):
    from memo_governance import latest_compliance_memo_row, memo_selection_metadata

    app_id, _ = _insert_app(db)
    _insert_memo(
        db,
        app_id,
        version=1,
        created_at="2026-06-13T11:00:00",
        review_status="approved",
        approval_reason="Older lower-version memo approved",
    )
    canonical_id = _insert_memo(
        db,
        app_id,
        version=2,
        created_at="2026-06-13T10:00:00",
        review_status="draft",
    )

    latest = latest_compliance_memo_row(db, app_id)

    assert latest["id"] == canonical_id
    assert latest["version"] == 2
    assert memo_selection_metadata(latest)["selector"] == "pr5_canonical_v1"
    assert memo_selection_metadata(latest)["selection_order"] == "version DESC, created_at DESC, id DESC"


def test_canonical_latest_memo_selector_rejects_unsafe_columns(db):
    from memo_governance import latest_compliance_memo_row

    app_id, _ = _insert_app(db)
    _insert_memo(
        db,
        app_id,
        version=1,
        created_at="2026-06-13T11:00:00",
    )

    latest = latest_compliance_memo_row(db, app_id, columns="id, memo_data")
    assert latest["memo_data"]

    try:
        latest_compliance_memo_row(db, app_id, columns="id; DROP TABLE compliance_memos")
    except ValueError as exc:
        assert "Invalid compliance memo column selector" in str(exc)
    else:
        raise AssertionError("unsafe memo selector columns should be rejected")


def test_approval_gate_uses_canonical_memo_not_newer_lower_version(db):
    from security_hardening import ApprovalGateValidator

    app_id, _ = _insert_app(db)
    _insert_memo(
        db,
        app_id,
        version=1,
        created_at="2026-06-13T12:00:00",
        review_status="approved",
        approval_reason="Approved lower-version memo",
    )
    _insert_memo(
        db,
        app_id,
        version=2,
        created_at="2026-06-13T09:00:00",
        review_status="draft",
    )
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()

    can_approve, message = ApprovalGateValidator.validate_approval(dict(app), db)

    assert can_approve is False
    assert "draft" in message.lower()


def test_approval_gate_requires_reason_for_canonical_approved_memo(db):
    from security_hardening import ApprovalGateValidator

    app_id, _ = _insert_app(db)
    _insert_memo(
        db,
        app_id,
        version=1,
        created_at="2026-06-13T09:00:00",
        review_status="approved",
        approval_reason="   ",
    )
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()

    can_approve, message = ApprovalGateValidator.validate_approval(dict(app), db)

    assert can_approve is False
    assert "approval_reason" in message


def test_memo_approval_handler_requires_and_persists_approval_reason_static():
    server_py = _read("arie-backend/server.py")
    handler_start = server_py.index("class MemoApproveHandler")
    handler_region = server_py[handler_start : handler_start + 16000]

    assert 'body.get("approval_reason")' in handler_region
    assert "approval_reason is required" in handler_region
    assert "UPDATE compliance_memos SET review_status = 'approved'" in handler_region
    assert "approval_reason = ?" in handler_region
    assert "Approval reason:" in handler_region
    assert '"canonical_memo_id"' in handler_region


def test_export_and_api_consumers_use_canonical_selector_static():
    server_py = _read("arie-backend/server.py")
    security_py = _read("arie-backend/security_hardening.py")
    export_py = _read("arie-backend/evidence_pack_export.py")
    edd_py = _read("arie-backend/edd_memo_integration.py")

    assert "latest_compliance_memo_row(" in server_py
    assert "CANONICAL_MEMO_ORDER_SQL" in server_py
    assert "latest_compliance_memo_row(" in security_py
    assert "latest_compliance_memo_row(db, app_id)" in export_py
    assert '("Approval reason", memo.get("approval_reason"))' in export_py
    assert "latest_compliance_memo_row(db, application_id, columns=\"id\")" in edd_py


def test_memo_ui_captures_reason_and_collapses_diagnostics_static():
    html = _read("arie-backoffice.html")

    assert 'id="memo-governance-summary"' in html
    assert 'id="memo-approval-reason"' in html
    assert "approval_reason: approvalReason" in html
    assert "acknowledged: !!memoSignoff.checked" in html
    assert "Enter the approval reason before submitting memo approval." in html
    assert "Full Memo / Diagnostics" in html
    assert "Validation failed, but no issue detail was returned" in html
    assert "DETERMINISTIC (RULE-BASED) OUTPUT" not in html
    assert "this UI does not capture or submit that reason yet" not in html
