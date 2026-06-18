import importlib
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _sync_db_path(path):
    os.environ["DATABASE_URL"] = ""
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


@pytest.fixture
def memo_enhanced_db(tmp_path):
    db_path = str(tmp_path / "enhanced_memo.db")
    _sync_db_path(db_path)
    import config as config_module
    import db as db_module

    importlib.reload(config_module)
    importlib.reload(db_module)
    db_module.init_db()
    conn = db_module.get_db()
    db_module.seed_initial_data(conn)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def _insert_application(db, *, risk_level="HIGH", prescreening=None):
    suffix = uuid.uuid4().hex[:10]
    app_id = "app_memo_" + suffix
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, brn, country, sector, entity_type,
         ownership_structure, prescreening_data, risk_score, risk_level,
         base_risk_level, final_risk_level, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            app_id,
            "ARF-MEMO-" + suffix,
            "client001",
            "Enhanced Memo Ltd",
            "BRN-" + suffix,
            "United Kingdom",
            "Technology",
            "SME",
            "Simple",
            json.dumps(prescreening or {"screening_report": {"screening_mode": "not_configured"}}),
            20 if risk_level == "LOW" else 72,
            risk_level,
            risk_level,
            risk_level,
            "compliance_review",
        ),
    )
    db.commit()
    return app_id


def _app_for_memo(db, app_id, summary):
    app = dict(db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    app.update({
        "source_of_funds": "Operating revenue",
        "expected_volume": "USD 100,000 monthly",
        "operating_countries": "United Kingdom",
        "incorporation_date": "2021-01-01",
        "business_activity": "Technology services",
        "enhanced_review_summary": summary,
    })
    return app


def _generate(db, app_id):
    from enhanced_requirements import generate_application_enhanced_requirements

    result = generate_application_enhanced_requirements(
        db,
        app_id,
        actor={"sub": "admin001", "name": "Test Admin", "role": "admin"},
        generation_source="memo_test",
    )
    db.commit()
    return result


def _set_requirement(db, app_id, requirement_key, **updates):
    set_clause = ", ".join(f"{key}=?" for key in updates)
    db.execute(
        f"""
        UPDATE application_enhanced_requirements
        SET {set_clause}
        WHERE application_id=? AND requirement_key=?
        """,
        tuple(updates.values()) + (app_id, requirement_key),
    )
    db.commit()


def test_enhanced_review_memo_section_says_not_triggered_when_no_requirements(memo_enhanced_db):
    from enhanced_requirements import build_enhanced_review_memo_summary
    from memo_handler import build_compliance_memo

    app_id = _insert_application(memo_enhanced_db, risk_level="LOW")
    summary = build_enhanced_review_memo_summary(memo_enhanced_db, app_id)

    assert summary["triggered"] is False
    assert summary["overall_status"] == "not_triggered"

    memo, _, _, _ = build_compliance_memo(_app_for_memo(memo_enhanced_db, app_id, summary), [], [], [])
    section = memo["sections"]["enhanced_review_edd"]

    assert section["title"] == "Onboarding Enhanced Review"
    assert "Not triggered based on the current application data" in section["content"]
    assert "Enhanced Review / EDD" not in section["title"]
    assert memo["metadata"]["enhanced_review_status"] == "not_triggered"


def test_enhanced_review_memo_summary_redacts_client_text_and_raw_context(memo_enhanced_db):
    from enhanced_requirements import build_enhanced_review_memo_summary
    from memo_handler import build_compliance_memo

    app_id = _insert_application(
        memo_enhanced_db,
        risk_level="HIGH",
        prescreening={
            "screening_report": {"screening_mode": "not_configured"},
            "expected_volume": "Over USD 5,000,000 per month",
            "existing_bank_account": "Yes",
        },
    )
    generated = _generate(memo_enhanced_db, app_id)
    assert generated["generated_count"] > 0

    secret_response = "SECRET client narrative that must not be copied into the memo"
    _set_requirement(
        memo_enhanced_db,
        app_id,
        "major_counterparties_explanation",
        status="uploaded",
        client_response_text=secret_response,
        client_response_at="2026-05-07T08:00:00Z",
        client_response_by="client001",
        uploaded_at="2026-05-07T08:00:00Z",
        trigger_reason='{"raw_screening_payload": "do not render"}',
        trigger_context=json.dumps({"raw_screening_payload": "do not render"}),
    )
    _set_requirement(
        memo_enhanced_db,
        app_id,
        "company_bank_reference",
        status="requested",
        requested_at="2026-05-07T07:00:00Z",
        requested_by="co001",
    )
    _set_requirement(
        memo_enhanced_db,
        app_id,
        "contracts_invoices",
        status="accepted",
        reviewed_at="2026-05-07T09:00:00Z",
        reviewed_by="co001",
    )
    _set_requirement(
        memo_enhanced_db,
        app_id,
        "company_sof_evidence",
        status="waived",
        waived_at="2026-05-07T10:00:00Z",
        waived_by="sco001",
        waiver_reason="Officer-approved documentary alternative reviewed",
    )
    _set_requirement(
        memo_enhanced_db,
        app_id,
        "volume_rationale_vs_business_size",
        status="rejected",
        reviewed_at="2026-05-07T11:00:00Z",
        reviewed_by="co001",
    )

    summary = build_enhanced_review_memo_summary(memo_enhanced_db, app_id)
    serialized = json.dumps(summary, sort_keys=True)
    assert summary["triggered"] is True
    assert summary["text_responses_count"] == 1
    assert summary["waiver_count"] == 1
    assert summary["mandatory_outstanding_count"] > 0
    assert summary["overall_status"] == "incomplete"
    assert "client_response_text" not in serialized
    assert secret_response not in serialized
    assert "raw_screening_payload" not in serialized
    assert "trigger_context" not in serialized

    memo, _, _, _ = build_compliance_memo(
        _app_for_memo(memo_enhanced_db, app_id, summary),
        [],
        [],
        [{"id": "doc1", "doc_type": "cert_inc", "verification_status": "verified"}],
    )
    section_text = memo["sections"]["enhanced_review_edd"]["content"]
    memo_blob = json.dumps(memo, sort_keys=True)

    assert "Triggered: Yes" in section_text
    assert memo["sections"]["enhanced_review_edd"]["title"] == "Onboarding Enhanced Review"
    assert "Requested from client" in section_text
    assert "client response submitted" in section_text
    assert "Officer-approved documentary alternative reviewed" in section_text
    assert "Enhanced Review remains incomplete" in section_text
    assert secret_response not in memo_blob
    assert "raw_screening_payload" not in memo_blob


def test_enhanced_review_memo_complete_only_after_mandatory_items_resolved(memo_enhanced_db):
    from enhanced_requirements import build_enhanced_review_memo_summary
    from memo_handler import build_compliance_memo

    app_id = _insert_application(memo_enhanced_db, risk_level="HIGH")
    _generate(memo_enhanced_db, app_id)

    initial = build_enhanced_review_memo_summary(memo_enhanced_db, app_id)
    assert initial["overall_status"] == "incomplete"
    assert initial["mandatory_outstanding_count"] > 0

    memo_enhanced_db.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='accepted', reviewed_at='2026-05-07T12:00:00Z', reviewed_by='sco001'
        WHERE application_id=?
        """,
        (app_id,),
    )
    memo_enhanced_db.commit()

    resolved = build_enhanced_review_memo_summary(memo_enhanced_db, app_id)
    assert resolved["overall_status"] == "complete"
    assert resolved["mandatory_outstanding_count"] == 0
    assert resolved["blocking_outstanding_count"] == 0

    memo, _, _, _ = build_compliance_memo(
        _app_for_memo(memo_enhanced_db, app_id, resolved),
        [],
        [],
        [{"id": "doc1", "doc_type": "cert_inc", "verification_status": "verified"}],
    )
    assert "requirements have been resolved" in memo["sections"]["enhanced_review_edd"]["content"]


def test_enhanced_review_memo_includes_backoffice_only_v5_internal_controls(memo_enhanced_db):
    from enhanced_requirements import build_enhanced_review_memo_summary
    from memo_handler import build_compliance_memo

    app_id = _insert_application(
        memo_enhanced_db,
        risk_level="LOW",
        prescreening={"screening_report": {"screening_mode": "not_configured"}},
    )
    memo_enhanced_db.execute(
        "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
        (app_id, "Declared PEP Director", "Yes"),
    )
    memo_enhanced_db.commit()
    _generate(memo_enhanced_db, app_id)

    summary = build_enhanced_review_memo_summary(memo_enhanced_db, app_id)
    assert summary["backoffice_only_count"] > 0
    assert not summary["senior_review_items"]

    memo, _, _, _ = build_compliance_memo(
        _app_for_memo(memo_enhanced_db, app_id, summary),
        [],
        [],
        [{"id": "doc1", "doc_type": "cert_inc", "verification_status": "verified"}],
    )
    section_text = memo["sections"]["enhanced_review_edd"]["content"]
    assert "back-office/internal" in section_text
    assert "Senior review tasks" not in section_text


def test_enhanced_review_memo_copy_does_not_blend_onboarding_with_edd(memo_enhanced_db):
    from enhanced_requirements import build_enhanced_review_memo_summary
    from memo_handler import build_compliance_memo

    app_id = _insert_application(memo_enhanced_db, risk_level="HIGH")
    _generate(memo_enhanced_db, app_id)

    summary = build_enhanced_review_memo_summary(memo_enhanced_db, app_id)
    memo, _, _, _ = build_compliance_memo(
        _app_for_memo(memo_enhanced_db, app_id, summary),
        [],
        [],
        [],
    )

    section = memo["sections"]["enhanced_review_edd"]
    assert section["title"] == "Onboarding Enhanced Review"
    assert "Enhanced Review / EDD" not in json.dumps(section)
    assert "Onboarding Enhanced Review" in json.dumps(section)
