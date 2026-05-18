from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def phase1_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "phase1.db"))

    import importlib
    import config as config_module
    import db as db_module

    importlib.reload(config_module)
    importlib.reload(db_module)
    db_module.DB_PATH = str(tmp_path / "phase1.db")
    db_module.init_db()
    conn = db_module.get_db()
    for user_id, role in (("admin001", "admin"), ("sco001", "sco"), ("co001", "co"), ("agent001", "agent")):
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (user_id, f"{user_id}@example.com", "x", user_id.upper(), role),
        )
    conn.execute(
        "INSERT OR IGNORE INTO applications (id, ref, company_name, risk_level, final_risk_level, status, risk_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("app-phase1", "APP-PHASE1", "Phase1 Test Co", "MEDIUM", "MEDIUM", "approved", 42),
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def audit_sink():
    events = []

    def writer(user, action, target, detail, db=None, before_state=None, after_state=None, commit=True):
        events.append({
            "user": dict(user or {}),
            "action": action,
            "target": target,
            "detail": detail,
            "before_state": before_state,
            "after_state": after_state,
        })

    writer.events = events
    return writer


ADMIN = {"sub": "admin001", "name": "Admin", "role": "admin"}
SCO = {"sub": "sco001", "name": "SCO", "role": "sco"}
CO = {"sub": "co001", "name": "Officer", "role": "co"}



def _insert_review(conn, **overrides):
    payload = {
        "application_id": "app-phase1",
        "client_name": "Phase1 Test Co",
        "risk_level": "MEDIUM",
        "status": "pending",
        "trigger_type": "time_based",
        "review_cycle_number": 1,
    }
    payload.update(overrides)
    cols = ",".join(payload.keys())
    placeholders = ",".join("?" for _ in payload)
    conn.execute(f"INSERT INTO periodic_reviews ({cols}) VALUES ({placeholders})", tuple(payload.values()))
    conn.commit()
    return conn.execute("SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1").fetchone()["id"]



def _insert_document(conn, *, document_id="doc-phase1"):
    conn.execute(
        "INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
        (document_id, "app-phase1", "passport", "passport.pdf", f"/tmp/{document_id}.pdf", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return document_id



def _insert_edd(conn, *, linked_periodic_review_id=None, stage="analysis"):
    conn.execute(
        "INSERT INTO edd_cases (application_id, client_name, stage, linked_periodic_review_id) VALUES (?, ?, ?, ?)",
        ("app-phase1", "Phase1 Test Co", stage, linked_periodic_review_id),
    )
    conn.commit()
    return conn.execute("SELECT id FROM edd_cases ORDER BY id DESC LIMIT 1").fetchone()["id"]



def _review_row(conn, review_id):
    return conn.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()



def test_phase1_schema_helper_is_idempotent(phase1_db):
    import db as db_module

    db_module._ensure_periodic_review_phase1_schema(phase1_db)
    db_module._ensure_periodic_review_phase1_schema(phase1_db)
    cols = {row["name"] for row in phase1_db.execute("PRAGMA table_info(periodic_reviews)").fetchall()}
    for column in {
        "assigned_officer",
        "assigned_by",
        "review_cycle_number",
        "policy_version",
        "frequency_months",
        "calculation_basis",
        "legacy_import",
        "legacy_source_type",
        "legacy_confidence",
        "legacy_sco_acknowledged_at",
        "import_requires_ack",
        "material_change_attestation",
        "material_change_categories",
        "risk_change_attestation",
        "risk_rerate_reason",
        "officer_rationale",
        "memo_status",
        "periodic_review_memo_id",
        "last_review_date",
        "next_review_date",
    }:
        assert column in cols
    link_cols = {row["name"] for row in phase1_db.execute("PRAGMA table_info(periodic_review_evidence_links)").fetchall()}
    assert {"periodic_review_id", "requirement_id", "document_id", "link_type", "linked_by", "linked_at", "note"} <= link_cols



def test_assignment_is_stored_and_not_inferred_from_decided_by(phase1_db, audit_sink):
    from periodic_review_management import assign_review
    import lifecycle_queue as lq

    review_id = _insert_review(phase1_db, decided_by="sco001")
    result = assign_review(phase1_db, review_id, assigned_officer="co001", user=ADMIN, audit_writer=audit_sink)
    phase1_db.commit()

    row = _review_row(phase1_db, review_id)
    assert row["assigned_officer"] == "co001"
    assert row["decided_by"] == "sco001"
    assert result["assigned_officer"] == "co001"

    queue = lq.build_lifecycle_queue(phase1_db, types=["review"])
    review_item = queue["items"][0]
    assert review_item["owner_id"] == "co001"
    assert all(event["after_state"]["assigned_officer"] == "co001" for event in audit_sink.events)



def test_reassignment_requires_reason_and_audits_before_after(phase1_db, audit_sink):
    from periodic_review_management import InvalidPeriodicReviewInput, assign_review

    review_id = _insert_review(phase1_db, assigned_officer="co001", assigned_by="admin001", assigned_at="2026-01-01T00:00:00Z")
    with pytest.raises(InvalidPeriodicReviewInput):
        assign_review(phase1_db, review_id, assigned_officer="sco001", user=ADMIN, audit_writer=audit_sink)

    result = assign_review(
        phase1_db,
        review_id,
        assigned_officer="sco001",
        reassigned_reason="Escalated workload",
        user=ADMIN,
        audit_writer=audit_sink,
    )
    phase1_db.commit()
    assert result["assigned_officer"] == "sco001"
    assert audit_sink.events[-1]["before_state"]["assigned_officer"] == "co001"
    assert audit_sink.events[-1]["after_state"]["assigned_officer"] == "sco001"



def test_import_setup_saves_last_review_date_and_source_metadata(phase1_db, audit_sink):
    from periodic_review_management import save_legacy_import_setup

    review_id = _insert_review(phase1_db, risk_level="LOW")
    result = save_legacy_import_setup(
        phase1_db,
        review_id,
        last_review_date="2024-01-15",
        source_type="system_export",
        source_note="Imported from ARIE register",
        confidence="high",
        assigned_officer="co001",
        user=CO,
        audit_writer=audit_sink,
    )
    phase1_db.commit()
    row = _review_row(phase1_db, review_id)
    assert row["last_review_date"] == "2024-01-15"
    assert row["legacy_source_type"] == "system_export"
    assert row["legacy_source_note"] == "Imported from ARIE register"
    assert row["legacy_confidence"] == "high"
    assert row["assigned_officer"] == "co001"
    assert result["next_review_date"] == "2027-01-15"



def test_high_import_requires_acknowledgement_and_low_medium_does_not(phase1_db, audit_sink):
    from periodic_review_management import acknowledge_legacy_import, save_legacy_import_setup

    high_review = _insert_review(phase1_db, risk_level="HIGH")
    low_review = _insert_review(phase1_db, risk_level="LOW")

    save_legacy_import_setup(
        phase1_db,
        high_review,
        last_review_date="2025-01-01",
        source_type="internal_register",
        confidence="medium",
        user=CO,
        audit_writer=audit_sink,
    )
    save_legacy_import_setup(
        phase1_db,
        low_review,
        last_review_date="2025-01-01",
        source_type="internal_register",
        confidence="medium",
        user=CO,
        audit_writer=audit_sink,
    )
    ack = acknowledge_legacy_import(phase1_db, high_review, user=SCO, audit_writer=audit_sink)
    phase1_db.commit()

    assert _review_row(phase1_db, high_review)["import_requires_ack"] == 1
    assert _review_row(phase1_db, low_review)["import_requires_ack"] == 0
    assert ack["legacy_sco_acknowledged_by"] == "sco001"
    assert _review_row(phase1_db, high_review)["legacy_sco_acknowledged_at"] is not None


@pytest.mark.parametrize(
    ("risk_level", "expected_months", "expected_next_review_date"),
    [
        ("LOW", 36, "2027-01-15"),
        ("MEDIUM", 24, "2026-01-15"),
        ("HIGH", 12, "2025-01-15"),
        ("VERY_HIGH", 6, "2024-07-15"),
    ],
)
def test_policy_snapshot_is_stored_from_import_setup(phase1_db, audit_sink, risk_level, expected_months, expected_next_review_date):
    from periodic_review_management import save_legacy_import_setup

    review_id = _insert_review(phase1_db, risk_level=risk_level)
    save_legacy_import_setup(
        phase1_db,
        review_id,
        last_review_date="2024-01-15",
        source_type="prior_file_note",
        confidence="high",
        user=CO,
        audit_writer=audit_sink,
    )
    phase1_db.commit()
    row = _review_row(phase1_db, review_id)
    assert row["policy_version"] == "v1"
    assert row["frequency_months"] == expected_months
    assert row["next_review_date"] == expected_next_review_date



def test_material_change_attestation_validation(phase1_db, audit_sink):
    from periodic_review_management import InvalidPeriodicReviewInput, save_material_change_attestation

    review_id = _insert_review(phase1_db)
    with pytest.raises(InvalidPeriodicReviewInput):
        save_material_change_attestation(
            phase1_db,
            review_id,
            attestation="no_material_change",
            categories=["directors"],
            user=CO,
            audit_writer=audit_sink,
        )
    with pytest.raises(InvalidPeriodicReviewInput):
        save_material_change_attestation(
            phase1_db,
            review_id,
            attestation="material_change_identified",
            categories=[],
            user=CO,
            audit_writer=audit_sink,
        )



def test_risk_change_records_audit_and_recalculates_next_review_date_without_application_write(phase1_db, audit_sink):
    from periodic_review_management import record_risk_change, RISK_WRITE_GAP_MESSAGE

    review_id = _insert_review(phase1_db, risk_level="MEDIUM", last_review_date="2024-01-15")
    result = record_risk_change(
        phase1_db,
        review_id,
        new_risk_level="HIGH",
        reason_code="material_change",
        officer_note="Ownership changed",
        user=CO,
        audit_writer=audit_sink,
    )
    phase1_db.commit()
    row = _review_row(phase1_db, review_id)
    app = phase1_db.execute("SELECT risk_level, final_risk_level FROM applications WHERE id = ?", ("app-phase1",)).fetchone()
    assert row["previous_risk_level"] == "MEDIUM"
    assert row["new_risk_level"] == "HIGH"
    assert row["next_review_date"] == "2025-01-15"
    assert result["application_risk_write_status"] == "unsafe_gap"
    assert result["application_risk_write_message"] == RISK_WRITE_GAP_MESSAGE
    assert app["risk_level"] == "MEDIUM"
    assert audit_sink.events[-1]["after_state"]["new_risk_level"] == "HIGH"



def test_evidence_links_reference_existing_documents_without_duplicate_document_rows(phase1_db, audit_sink):
    from periodic_review_management import add_evidence_link

    review_id = _insert_review(phase1_db)
    document_id = _insert_document(phase1_db)
    before_count = phase1_db.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
    result = add_evidence_link(
        phase1_db,
        review_id,
        requirement_id="req-1",
        document_id=document_id,
        link_type="requirement_evidence",
        note="Linked existing KYC document",
        user=CO,
        audit_writer=audit_sink,
    )
    phase1_db.commit()
    after_count = phase1_db.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
    link_row = phase1_db.execute("SELECT * FROM periodic_review_evidence_links WHERE id = ?", (result["id"],)).fetchone()
    assert after_count == before_count
    assert link_row["document_id"] == document_id



def test_shared_projection_returns_canonical_state_across_surfaces(phase1_db):
    import lifecycle_queue as lq
    import monitoring_enrollment as me
    import server
    from periodic_review_projection_service import get_review_projection

    review_id = _insert_review(
        phase1_db,
        risk_level="HIGH",
        assigned_officer="co001",
        import_requires_ack=1,
        required_items=json.dumps([
            {"id": "req-1", "item_type": "screening_refresh", "label": "Refresh screening", "severity": "high", "status": "open"}
        ]),
        officer_rationale="",
    )
    edd_id = _insert_edd(phase1_db, linked_periodic_review_id=review_id, stage="analysis")
    phase1_db.execute("UPDATE periodic_reviews SET linked_edd_case_id = ? WHERE id = ?", (edd_id, review_id))
    phase1_db.commit()

    projection = get_review_projection(phase1_db, review_id)
    monitoring_summary = me.latest_active_review_summary(phase1_db, "app-phase1")
    queue = lq.build_lifecycle_queue(phase1_db, types=["review"], application_id="app-phase1")
    edd_case = server._materialise_edd_case(phase1_db, phase1_db.execute("SELECT * FROM edd_cases WHERE id = ?", (edd_id,)).fetchone())

    assert projection["status"] == "pending"
    assert projection["status_label"] == "Blocked"
    assert monitoring_summary["status_label"] == "Blocked"
    assert queue["items"][0]["status_label"] == "Blocked"
    assert edd_case["linked_periodic_review"]["status_label"] == "Blocked"



def test_new_flow_writes_outcome_and_not_legacy_decision(phase1_db, audit_sink):
    import periodic_review_engine as pre
    from periodic_review_management import save_officer_rationale

    phase1_db.execute(
        "UPDATE applications SET prescreening_data = ? WHERE id = ?",
        (
            json.dumps({
                "screening_report": {"screened_at": "2026-01-01T00:00:00Z"},
                "screening_valid_until": "2099-12-31T00:00:00Z",
            }),
            "app-phase1",
        ),
    )
    review_id = _insert_review(
        phase1_db,
        risk_level="LOW",
        required_items=json.dumps([
            {"id": "outcome", "item_type": "review_outcome_recorded", "label": "Record outcome", "severity": "medium", "status": "open"}
        ]),
        legacy_import=0,
        import_requires_ack=0,
    )
    save_officer_rationale(phase1_db, review_id, rationale="Officer completed review.", user=CO, audit_writer=audit_sink)
    result = pre.record_review_outcome(
        phase1_db,
        review_id,
        outcome=pre.OUTCOME_NO_CHANGE,
        outcome_reason="No material issues detected",
        user=CO,
        audit_writer=audit_sink,
    )
    row = _review_row(phase1_db, review_id)
    assert result["outcome"] == pre.OUTCOME_NO_CHANGE
    assert row["outcome"] == pre.OUTCOME_NO_CHANGE
    assert row["decision"] is None



def test_locked_setup_change_requires_override_reason(phase1_db, audit_sink):
    from periodic_review_management import ImmutablePeriodicReviewFieldError, save_legacy_import_setup

    review_id = _insert_review(phase1_db, risk_level="MEDIUM")
    save_legacy_import_setup(
        phase1_db,
        review_id,
        last_review_date="2025-01-01",
        source_type="internal_register",
        confidence="medium",
        user=CO,
        audit_writer=audit_sink,
    )
    phase1_db.commit()
    with pytest.raises(ImmutablePeriodicReviewFieldError):
        save_legacy_import_setup(
            phase1_db,
            review_id,
            last_review_date="2025-02-01",
            source_type="internal_register",
            confidence="medium",
            user=ADMIN,
            audit_writer=audit_sink,
        )
    save_legacy_import_setup(
        phase1_db,
        review_id,
        last_review_date="2025-02-01",
        source_type="internal_register",
        confidence="medium",
        override_reason="Correcting imported register date",
        user=ADMIN,
        audit_writer=audit_sink,
    )
