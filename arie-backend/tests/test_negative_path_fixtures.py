"""Item 36 persisted negative-path fixture and protection-manifest coverage."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db as db_module
from fixtures.cleanup import cleanup_registered_fixture
from fixtures.cli import _enforce_apply_gates
from fixtures.registry import APP_ID, NEGATIVE_PATH_FIXTURES
from fixtures.seeder import seed_all
from regulated_deletion import RegulatedDeleteDenied
from security_hardening import collect_approval_gate_blockers


TESTS = Path(__file__).resolve().parent
PROTECTION_MANIFEST = {
    "test_regulated_record_deletion_protection.py": "test_unsafe_regulated_delete_is_denied_and_structured",
    "test_supervisor_security_pr_a.py": "test_supervisor_basehandler_collision_decisions_use_common_implementations",
    "test_supervisor_persistence_bsa003.py": "test_fresh_schema_and_repeated_init_create_three_portable_tables",
    "test_r8_cross_tenant_ownership.py": "test_nonowned_existing_app_raises_permission_error",
    "test_idv_approval_gate.py": "test_pending_idv_blocks_final_approval",
    "test_portal_to_approval_e2e.py": "test_approval_checklist_is_documented_from_current_code",
    "test_audit_writer_application_request_ids.py": "test_append_audit_log_backward_compatible_and_metadata_enriched",
}
PROTECTION_FAILURE = (
    "This is a registered compliance protection.\n\n"
    "Removal or rename requires founder sign-off and updating the protection manifest."
)


def _row(db, sql, params=()):
    return db.execute(sql, params).fetchone()


def test_item36_manifest_is_complete_and_capped():
    assert len(NEGATIVE_PATH_FIXTURES) == 12
    expected_http = {
        "active-approval-blockers": 400,
        "stale-memo": 400,
        "stale-risk-provenance": 409,
        "missing-idv": 400,
        "missing-required-documents": 400,
        "pending-rmi": 409,
        "synthetic-sanctions-hit": 400,
        "outstanding-pep-review": 400,
        "periodic-review-blockers": 409,
        "completed-periodic-review": 200,
        "similar-reference-cross-client": 403,
        "already-consumed-approval": 409,
    }
    required = {
        "fixture_key", "synthetic_ref", "marker", "expected_state",
        "tables_written", "regulated_tables_written", "cleanup_order",
        "expected_control", "expected_HTTP_result", "safe_to_retain_in_staging",
    }
    for key, manifest in NEGATIVE_PATH_FIXTURES.items():
        assert required <= manifest.keys(), key
        assert manifest["fixture_key"] == key
        assert set(manifest["regulated_tables_written"]) <= set(manifest["tables_written"])
        assert set(manifest["cleanup_order"]) == set(manifest["tables_written"])
        assert isinstance(manifest["expected_HTTP_result"], int)
        assert manifest["expected_HTTP_result"] == expected_http[key]


@pytest.mark.parametrize("fixture_key", tuple(NEGATIVE_PATH_FIXTURES))
def test_seed_control_and_sanctioned_cleanup_round_trip(temp_db, fixture_key):
    manifest = NEGATIVE_PATH_FIXTURES[fixture_key]
    code = manifest["scenario_code"]
    app_id = APP_ID[code]
    result = seed_all(dry_run=False, only=[code])[0]
    assert result["fixture_key"] == fixture_key
    # Deterministic upsert: reseeding the same logical key must retain the same
    # application and state identifiers rather than creating a second fixture.
    repeated = seed_all(dry_run=False, only=[code])[0]
    assert repeated["application_id"] == result["application_id"]

    db = db_module.get_db()
    app = _row(db, "SELECT * FROM applications WHERE id=? AND is_fixture=?", (app_id, True))
    assert app, fixture_key

    state = manifest["expected_state"]
    if state == "pending_rmi":
        from server import _rmi_continuation_readiness
        control = _rmi_continuation_readiness(db, app)
        assert control["can_continue"] is False
    elif state in {"periodic_blocked", "periodic_completed"}:
        review = _row(
            db,
            "SELECT * FROM periodic_reviews WHERE application_id=?",
            (app_id,),
        )
        assert review
        assert (review["status"] == "completed") is (state == "periodic_completed")
        if state == "periodic_blocked":
            from periodic_review_blockers import evaluate_review_readiness
            readiness = evaluate_review_readiness(db, review)
            labels = {item["label"] for item in readiness["operational_blockers"]}
            assert "FIX-SCEN20 Provide refreshed ownership evidence" in labels
    elif state == "similar_reference_pair":
        pair = _row(db, "SELECT client_id FROM applications WHERE id=? AND is_fixture=?", ("f1xed0000000022b", True))
        assert pair and pair["client_id"] != app["client_id"]
        import change_management
        with pytest.raises(PermissionError, match="do not own"):
            change_management.create_change_request(
                db,
                "f1xed0000000022b",
                "portal_client",
                "portal",
                "FIX-SCEN22 cross-client denial",
                [{
                    "change_type": "company_details",
                    "field_name": "company_name",
                    "old_value": "Pair B",
                    "new_value": "Forged Pair B",
                }],
                {"sub": "fix-scen22-client-a", "name": "FIX-SCEN22 Client A", "role": "client"},
            )
    elif state == "consumed_approval":
        assert app["status"] == "approved"
        assert _row(db, "SELECT id FROM decision_records WHERE id='fix-scen23-decision'")
    else:
        # One call into the existing backend approval-summary helper; no gate
        # logic is recreated in this fixture test.
        if state == "stale_memo":
            from server import _memo_staleness_view
            memo = _row(db, "SELECT * FROM compliance_memos WHERE application_id=? ORDER BY id DESC LIMIT 1", (app_id,))
            assert _memo_staleness_view(app, memo)["is_stale"] is True
        elif state == "stale_risk":
            from server import _application_risk_staleness_error
            assert _application_risk_staleness_error(db, app)
        else:
            blockers = collect_approval_gate_blockers(app, db)
            assert blockers, fixture_key
            if state == "missing_idv":
                assert any(
                    item.get("blocker_group") == "identity_verification"
                    for item in blockers
                )
            elif state == "missing_documents":
                assert any(
                    item.get("blocker_group") == "document_evidence"
                    for item in blockers
                )
            elif state == "sanctions_hit":
                assert any(item.get("id") == "screening_adverse_truth" for item in blockers)
            elif state == "pep_review":
                from security_hardening import classify_approval_route
                route = classify_approval_route(app, db)
                assert "declared_pep_present" in route["escalation_reasons"]

    cleanup_registered_fixture(db, fixture_key, actor_id="pytest:item36")
    assert not _row(db, "SELECT id FROM applications WHERE id=?", (app_id,))
    if state == "similar_reference_pair":
        assert not _row(db, "SELECT id FROM applications WHERE id='f1xed0000000022b'")
        assert not _row(db, "SELECT id FROM clients WHERE id LIKE 'fix-scen22-client-%'")
    for table in manifest["tables_written"]:
        if table == "audit_log":
            residue = _row(db, "SELECT count(*) AS n FROM audit_log WHERE user_id='fixture_seed' AND detail LIKE ?", (f"%{code}%",))
        elif table == "decision_records":
            residue = _row(db, "SELECT count(*) AS n FROM decision_records WHERE id='fix-scen23-decision'")
        elif table == "rmi_request_items":
            residue = _row(db, "SELECT count(*) AS n FROM rmi_request_items WHERE request_id='fix-scen17-rmi'")
        elif table == "clients":
            residue = _row(db, "SELECT count(*) AS n FROM clients WHERE id LIKE 'fix-scen22-client-%'")
        elif table == "directors":
            residue = _row(db, "SELECT count(*) AS n FROM directors WHERE application_id=?", (app_id,))
        elif table == "applications":
            ids = (app_id, "f1xed0000000022b") if state == "similar_reference_pair" else (app_id,)
            placeholders = ",".join("?" for _ in ids)
            residue = _row(db, f"SELECT count(*) AS n FROM applications WHERE id IN ({placeholders})", ids)
        else:
            residue = _row(db, f"SELECT count(*) AS n FROM {table} WHERE application_id=?", (app_id,))
        assert residue["n"] == 0, (fixture_key, table)
    db.close()


@pytest.mark.parametrize("table", ["compliance_memos", "rmi_requests", "periodic_reviews", "decision_records", "audit_log"])
def test_representative_regulated_fixture_rows_deny_direct_delete(table):
    import sqlite3
    from db import DBConnection
    raw = sqlite3.connect(":memory:")
    guarded = DBConnection(raw, is_postgres=False, database_identity="/runtime/onboarda.db")
    guarded.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
    guarded.execute(f"INSERT INTO {table} VALUES ('fixture-row')")
    with pytest.raises(RegulatedDeleteDenied):
        guarded.execute(f"DELETE FROM {table} WHERE id='fixture-row'")
    guarded.close()


def test_fixture_apply_refuses_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOW_FIXTURE_SEED", "1")
    with pytest.raises(SystemExit, match="REFUSED.*staging"):
        _enforce_apply_gates("I-UNDERSTAND-FIXTURE-WRITE")


def test_interim_compliance_protection_manifest():
    """Interim filename/function guard; this is intentionally not a framework."""
    for filename, function_name in PROTECTION_MANIFEST.items():
        path = TESTS / filename
        assert path.exists(), f"{filename} missing. {PROTECTION_FAILURE}"
        names = {
            node.name
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert function_name in names, (
            f"{filename}::{function_name} missing. {PROTECTION_FAILURE}"
        )
