"""PostgreSQL persistence contract for Pilot Canonical Dataset v1."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
import uuid

import pytest


EXPECTED_MANIFEST_SHA256 = (
    "45ceaa32d592f754289fb888bbb6d6a863349cf9bde406e7d7055b6c7dc23d25"
)


def _postgres_dsn() -> str | None:
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


@pytest.fixture()
def canonical_postgres(monkeypatch):
    """Return runtime modules bound to an isolated PostgreSQL database."""
    base_dsn = _postgres_dsn()
    if not base_dsn:
        pytest.skip("No PostgreSQL DSN available")

    import psycopg2
    from psycopg2 import sql

    database_name = f"pilot_canonical_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(base_dsn)
    admin = psycopg2.connect(base_dsn)
    admin.autocommit = True
    database_dsn = urlunsplit(
        (parts.scheme, parts.netloc, f"/{database_name}", parts.query, parts.fragment)
    )
    database_created = False
    db_module = None
    flag_managers = {}
    flag_snapshots = {}
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )
        database_created = True

        monkeypatch.setenv("DATABASE_URL", database_dsn)
        monkeypatch.setenv("ENVIRONMENT", "testing")
        monkeypatch.setenv("ENABLE_RSMP_TIER0A_MAPPING_FIDELITY", "true")

        import db as loaded_db_module
        import environment
        import risk_controlled_values
        import fixtures.seeder as fixture_seeder_module
        import fixtures.pilot_canonical as canonical_module
        import fixtures.pilot_canonical_seeder as seeder_module

        db_module = loaded_db_module
        db_module.close_pg_pool()
        monkeypatch.setattr(db_module, "DATABASE_URL", database_dsn)
        monkeypatch.setattr(db_module, "USE_POSTGRESQL", True)
        monkeypatch.setattr(fixture_seeder_module, "USE_POSTGRESQL", True)
        monkeypatch.setattr(seeder_module, "USE_POSTGRESQL", True)

        flag_name = risk_controlled_values.ACTIVATION_FLAG
        flag_managers = {
            id(manager): manager
            for manager in (environment.flags, risk_controlled_values.flags)
        }
        flag_snapshots = {
            key: dict(manager._cache) for key, manager in flag_managers.items()
        }
        for manager in flag_managers.values():
            manager._cache[flag_name] = True

        db_module.init_db()
        connection = db_module.get_db()
        try:
            db_module.seed_initial_data(connection)
            connection.commit()
        finally:
            connection.close()

        yield SimpleNamespace(
            canonical=canonical_module,
            db=db_module,
            seeder=seeder_module,
        )
    finally:
        if db_module is not None:
            with suppress(Exception):
                db_module.close_pg_pool()
        for key, manager in flag_managers.items():
            manager._cache.clear()
            manager._cache.update(flag_snapshots[key])
        if database_created:
            with admin.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )
        admin.close()


def _public_table_counts(db_module) -> dict[str, int]:
    connection = db_module.get_db()
    try:
        tables = [
            row["table_name"]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' "
                "ORDER BY table_name"
            ).fetchall()
        ]
        return {
            table: int(
                connection.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
            )
            for table in tables
        }
    finally:
        connection.close()


def _canonical_application_count(db_module) -> int:
    connection = db_module.get_db()
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM applications WHERE ref LIKE ?",
                ("RM-PILOT-%",),
            ).fetchone()["n"]
        )
    finally:
        connection.close()


def _canonical_document_snapshot(db_module):
    connection = db_module.get_db()
    try:
        return [
            (row["id"], row["doc_type"])
            for row in connection.execute(
                "SELECT d.id,d.doc_type FROM documents d "
                "JOIN applications a ON a.id=d.application_id "
                "WHERE a.ref LIKE ? ORDER BY d.id",
                ("RM-PILOT-%",),
            ).fetchall()
        ]
    finally:
        connection.close()


def _canonical_document_row_version_snapshot(db_module):
    connection = db_module.get_db()
    try:
        return [
            (row["id"], row["doc_type"], row["xmin"])
            for row in connection.execute(
                "SELECT d.id,d.doc_type,d.xmin::text AS xmin FROM documents d "
                "JOIN applications a ON a.id=d.application_id "
                "WHERE a.ref LIKE ? ORDER BY d.id",
                ("RM-PILOT-%",),
            ).fetchall()
        ]
    finally:
        connection.close()


def _canonical_application_risk_snapshot(db_module):
    connection = db_module.get_db()
    try:
        return [
            (
                row["id"], row["ref"], row["risk_score"], row["risk_level"],
                row["status"], row["onboarding_lane"],
                row["risk_config_version"], row["risk_dimensions"],
                row["risk_escalations"],
            )
            for row in connection.execute(
                "SELECT a.id,a.ref,a.risk_score,a.risk_level,a.status,"
                "a.onboarding_lane,a.risk_config_version,"
                "a.risk_dimensions::text AS risk_dimensions,"
                "a.risk_escalations::text AS risk_escalations "
                "FROM applications a WHERE a.ref LIKE ? ORDER BY a.ref",
                ("RM-PILOT-%",),
            ).fetchall()
        ]
    finally:
        connection.close()


def _canonical_correction_request_snapshot(db_module):
    connection = db_module.get_db()
    try:
        return [
            (row["id"], row["doc_type"], row["document_id"])
            for row in connection.execute(
                "SELECT i.id,i.doc_type,i.document_id "
                "FROM rmi_request_items i "
                "JOIN rmi_requests r ON r.id=i.request_id "
                "JOIN applications a ON a.id=r.application_id "
                "WHERE a.ref LIKE ? ORDER BY i.id",
                ("RM-PILOT-%",),
            ).fetchall()
        ]
    finally:
        connection.close()


def test_rm_pilot_037_persists_integer_override_flag(canonical_postgres):
    runtime = canonical_postgres
    first_decision_scenario = next(
        row
        for row in runtime.canonical.scenarios()
        if row.get("decision_evidence")
    )
    assert first_decision_scenario["reference"] == "RM-PILOT-037"

    results = runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=False,
        references=["RM-PILOT-037"],
    )

    assert [row["reference"] for row in results] == ["RM-PILOT-037"]
    after_first = _public_table_counts(runtime.db)
    repeated = runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=False,
        references=["RM-PILOT-037"],
    )
    after_second = _public_table_counts(runtime.db)

    assert [row["reference"] for row in repeated] == ["RM-PILOT-037"]
    assert after_first == after_second

    connection = runtime.db.get_db()
    try:
        rows = connection.execute(
            "SELECT d.override_flag, pg_typeof(d.override_flag)::text AS override_type, "
            "a.is_fixture, pg_typeof(a.is_fixture)::text AS fixture_type "
            "FROM decision_records d "
            "JOIN applications a ON a.ref=d.application_ref "
            "WHERE d.application_ref=?",
            ("RM-PILOT-037",),
        ).fetchall()
    finally:
        connection.close()

    assert len(rows) == 1
    row = rows[0]
    assert row["override_flag"] == 0
    assert row["override_type"] == "integer"
    assert row["is_fixture"] is True
    assert row["fixture_type"] == "boolean"


def test_complete_postgres_dry_run_is_repeatable_and_zero_residue(
    canonical_postgres,
):
    runtime = canonical_postgres
    before = _public_table_counts(runtime.db)
    assert _canonical_application_count(runtime.db) == 0

    first = runtime.seeder.seed_pilot_canonical_dataset(dry_run=True)
    after_first = _public_table_counts(runtime.db)
    assert _canonical_application_count(runtime.db) == 0
    second = runtime.seeder.seed_pilot_canonical_dataset(dry_run=True)
    after_second = _public_table_counts(runtime.db)
    assert _canonical_application_count(runtime.db) == 0

    expected_references = [f"RM-PILOT-{number:03d}" for number in range(1, 42)]
    assert [row["reference"] for row in first] == expected_references
    assert [row["reference"] for row in second] == expected_references
    assert len({row["reference"] for row in first}) == 41
    assert before == after_first == after_second
    assert runtime.canonical.manifest_sha256() == EXPECTED_MANIFEST_SHA256
    assert runtime.canonical.validate_runtime_alignment() == {
        "scenario_count": 41,
        "aligned": True,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
    }


def test_tier0c_b_routes_and_decision_eligibility_reconcile_on_postgres(
    canonical_postgres,
):
    runtime = canonical_postgres
    runtime.seeder.seed_pilot_canonical_dataset(dry_run=False)

    connection = runtime.db.get_db()
    try:
        applications = connection.execute(
            "SELECT * FROM applications WHERE ref LIKE ? ORDER BY ref",
            ("RM-PILOT-%",),
        ).fetchall()
        result = runtime.canonical.validate_tier0c_b_approval_routes(
            applications,
            db=connection,
        )
    finally:
        connection.close()

    assert result["scenario_count"] == 41
    assert result["approval_routes_valid"] is True
    assert result["decision_eligibility_valid"] is True
    by_ref = {row["reference"]: row for row in result["results"]}
    for reference in (
        "RM-PILOT-006", "RM-PILOT-007", "RM-PILOT-008", "RM-PILOT-009",
        "RM-PILOT-010", "RM-PILOT-011", "RM-PILOT-013", "RM-PILOT-039",
        "RM-PILOT-040",
    ):
        assert by_ref[reference]["approval_route"] == "compliance_required"
    for reference in ("RM-PILOT-014", "RM-PILOT-025", "RM-PILOT-026"):
        assert by_ref[reference]["approval_route"] == "dual_control_required"
    for reference in (
        "RM-PILOT-024", "RM-PILOT-031", "RM-PILOT-032", "RM-PILOT-033",
        "RM-PILOT-034", "RM-PILOT-035", "RM-PILOT-036",
    ):
        assert by_ref[reference]["decision_eligibility"] == "blocked"
    assert {
        reference: by_ref[reference]["eligibility_reason"]
        for reference in (
            "RM-PILOT-024", "RM-PILOT-031", "RM-PILOT-032",
            "RM-PILOT-033", "RM-PILOT-034", "RM-PILOT-035",
            "RM-PILOT-036", "RM-PILOT-038",
        )
    } == {
        "RM-PILOT-024": "terminal_state",
        "RM-PILOT-031": "case_stage_not_decisionable",
        "RM-PILOT-032": "case_stage_not_decisionable",
        "RM-PILOT-033": "unresolved_risk_mapping",
        "RM-PILOT-034": "unresolved_risk_mapping",
        "RM-PILOT-035": "unresolved_risk_mapping",
        "RM-PILOT-036": "screening_pending_or_unresolved",
        "RM-PILOT-038": "rejected",
    }


def test_postgres_converges_only_reviewed_prior_manifest_identity(
    canonical_postgres,
):
    runtime = canonical_postgres
    reference = "RM-PILOT-001"
    runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=False, references=[reference]
    )
    prior_version, prior_hash = next(
        identity
        for identity, successor in runtime.seeder.APPROVED_MANIFEST_LINEAGE.items()
        if successor == ("v1", EXPECTED_MANIFEST_SHA256)
    )

    connection = runtime.db.get_db()
    try:
        application = connection.execute(
            "SELECT id,prescreening_data::text AS prescreening_data "
            "FROM applications WHERE ref=?",
            (reference,),
        ).fetchone()
        identity = json.loads(application["prescreening_data"])
        original_id = application["id"]
        identity["dataset_version"] = prior_version
        identity["dataset_hash"] = prior_hash
        connection.execute(
            "UPDATE applications SET prescreening_data=? WHERE id=?",
            (json.dumps(identity, sort_keys=True), original_id),
        )
        connection.commit()
    finally:
        connection.close()

    runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=False, references=[reference]
    )
    connection = runtime.db.get_db()
    try:
        converged = connection.execute(
            "SELECT id,prescreening_data::text AS prescreening_data "
            "FROM applications WHERE ref=?",
            (reference,),
        ).fetchone()
    finally:
        connection.close()

    assert converged["id"] == original_id
    assert json.loads(converged["prescreening_data"])["dataset_hash"] == (
        EXPECTED_MANIFEST_SHA256
    )


def test_rm_pilot_029_persists_manual_edd_provenance_without_manifest_drift(
    canonical_postgres,
):
    runtime = canonical_postgres
    before = _public_table_counts(runtime.db)

    dry_run = runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=True,
        references=["RM-PILOT-029"],
    )
    assert [row["reference"] for row in dry_run] == ["RM-PILOT-029"]
    assert _public_table_counts(runtime.db) == before

    runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=False,
        references=["RM-PILOT-029"],
    )
    connection = runtime.db.get_db()
    try:
        application = connection.execute(
            "SELECT id,ref,risk_score,risk_level,onboarding_lane,risk_dimensions,"
            "risk_escalations,risk_config_version "
            "FROM applications WHERE ref=?",
            ("RM-PILOT-029",),
        ).fetchone()
        edd_case = connection.execute(
            "SELECT trigger_source,origin_context FROM edd_cases WHERE application_id=?",
            (application["id"],),
        ).fetchone()
    finally:
        connection.close()

    expected = next(
        row["expected"]
        for row in runtime.canonical.scenarios()
        if row["reference"] == "RM-PILOT-029"
    )
    assert application["id"] == "pcdv100000000029"
    assert application["risk_score"] == expected["score"]
    assert application["risk_level"] == expected["tier"]
    assert application["onboarding_lane"] == expected["lane"]
    assert edd_case["trigger_source"] == "officer_escalate_edd"
    assert edd_case["origin_context"] == "manual_onboarding_escalation"

    stored_snapshot = dict(application)
    repeated_dry_run = runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=True,
        references=["RM-PILOT-029"],
    )
    assert [row["reference"] for row in repeated_dry_run] == ["RM-PILOT-029"]
    connection = runtime.db.get_db()
    try:
        after_dry_run = connection.execute(
            "SELECT id,ref,risk_score,risk_level,onboarding_lane,risk_dimensions,"
            "risk_escalations,risk_config_version "
            "FROM applications WHERE ref=?",
            ("RM-PILOT-029",),
        ).fetchone()
    finally:
        connection.close()
    assert dict(after_dry_run) == stored_snapshot


def test_rm_pilot_030_persists_manual_compliance_handoff(canonical_postgres):
    runtime = canonical_postgres
    before = _public_table_counts(runtime.db)

    dry_run = runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=True,
        references=["RM-PILOT-030"],
    )
    assert [row["reference"] for row in dry_run] == ["RM-PILOT-030"]
    assert _public_table_counts(runtime.db) == before

    runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=False,
        references=["RM-PILOT-030"],
    )
    connection = runtime.db.get_db()
    try:
        application = connection.execute(
            "SELECT id,ref,status,risk_score,risk_level,onboarding_lane,"
            "submitted_to_compliance_at,submitted_to_compliance_by,"
            "prescreening_data::text AS prescreening_data "
            "FROM applications WHERE ref=?",
            ("RM-PILOT-030",),
        ).fetchone()
    finally:
        connection.close()

    scenario = next(
        row
        for row in runtime.canonical.scenarios()
        if row["reference"] == "RM-PILOT-030"
    )
    manual_metadata = json.loads(application["prescreening_data"])[
        "scenario_evidence"
    ]["manual_compliance_escalation"]
    assert application["id"] == "pcdv100000000030"
    assert application["status"] == scenario["expected"]["application_status"]
    assert application["risk_score"] == scenario["expected"]["score"]
    assert application["risk_level"] == scenario["expected"]["tier"]
    assert application["onboarding_lane"] == scenario["expected"]["lane"]
    assert application["submitted_to_compliance_at"]
    assert application["submitted_to_compliance_by"] == "co001"
    assert manual_metadata["trigger_source"] == "officer_submitted_to_compliance"
    assert manual_metadata["origin_context"] == "manual_onboarding_escalation"

    stored_snapshot = dict(application)
    repeated = runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=True,
        references=["RM-PILOT-030"],
    )
    assert [row["reference"] for row in repeated] == ["RM-PILOT-030"]
    connection = runtime.db.get_db()
    try:
        after_dry_run = connection.execute(
            "SELECT id,ref,status,risk_score,risk_level,onboarding_lane,"
            "submitted_to_compliance_at,submitted_to_compliance_by,"
            "prescreening_data::text AS prescreening_data "
            "FROM applications WHERE ref=?",
            ("RM-PILOT-030",),
        ).fetchone()
    finally:
        connection.close()
    assert dict(after_dry_run) == stored_snapshot
    assert runtime.canonical.manifest_sha256() == EXPECTED_MANIFEST_SHA256


def test_canonical_runtime_inputs_survive_persistence_and_dry_run(
    canonical_postgres,
):
    from party_utils import get_application_parties
    from prescreening.risk_inputs import build_prescreening_risk_input
    from rule_engine import compute_risk_score

    runtime = canonical_postgres
    manifest = {
        row["reference"]: row for row in runtime.canonical.scenarios()
    }
    runtime.seeder.seed_pilot_canonical_dataset(dry_run=False)
    before = _canonical_application_risk_snapshot(runtime.db)

    dry_run = runtime.seeder.seed_pilot_canonical_dataset(dry_run=True)
    assert len(dry_run) == 41
    assert _canonical_application_risk_snapshot(runtime.db) == before

    connection = runtime.db.get_db()
    try:
        applications = connection.execute(
            "SELECT * FROM applications WHERE ref LIKE ? ORDER BY ref",
            ("RM-PILOT-%",),
        ).fetchall()
        assert len(applications) == 41
        for application in applications:
            app = dict(application)
            directors, ubos, intermediaries = get_application_parties(
                connection, app["id"]
            )
            scorer_input = build_prescreening_risk_input(
                application=app,
                prescreening_data=app["prescreening_data"],
                directors=directors,
                ubos=ubos,
                intermediaries=intermediaries,
            )
            expected_inputs = manifest[app["ref"]]["risk_inputs"]
            assert scorer_input["monthly_volume"] == expected_inputs["monthly_volume"]
            assert (
                scorer_input["payment_corridors"]
                == expected_inputs["transaction_complexity"]
            )
            assert (
                scorer_input["introduction_method"]
                == expected_inputs["introduction_method"]
            )
            assert (
                scorer_input["services_required"]
                == expected_inputs["business"]["services"]["primary_services"]
            )
            assert scorer_input["_prescreening_mapping_corrections"] == [
                "primary_service_from_services_required"
            ]

            result = compute_risk_score(scorer_input)
            expected = manifest[app["ref"]]["expected"]
            assert result["score"] == expected["score"]
            assert result["level"] == expected["tier"]
            stale = {
                str(reason).split(":", 2)[1]
                for reason in result.get("escalations", [])
                if str(reason).startswith("stale:unmapped_")
            }
            assert "unmapped_monthly_volume" not in stale
            assert "unmapped_complexity" not in stale
            assert "unmapped_introduction" not in stale
            expected_unresolved = {
                "RM-PILOT-033": {"unmapped_sector"},
                "RM-PILOT-034": {"unmapped_entity_type"},
                "RM-PILOT-035": {"unmapped_incorporation_country"},
            }.get(app["ref"], set())
            assert stale == expected_unresolved
    finally:
        connection.close()

    assert runtime.canonical.manifest_sha256() == EXPECTED_MANIFEST_SHA256


def test_canonical_document_types_are_canonical_and_startup_stable(
    canonical_postgres,
):
    runtime = canonical_postgres

    runtime.seeder.seed_pilot_canonical_dataset(dry_run=False)
    first_documents = _canonical_document_snapshot(runtime.db)
    first_applications = _canonical_application_risk_snapshot(runtime.db)
    first_requests = _canonical_correction_request_snapshot(runtime.db)

    assert len(first_documents) == 131
    assert all(
        doc_type not in {"certificate_of_incorporation", "proof_of_address"}
        for _, doc_type in first_documents
    )
    assert sum(doc_type == "cert_inc" for _, doc_type in first_documents) == 40
    assert sum(doc_type == "poa" for _, doc_type in first_documents) == 1
    assert any(
        document_id == "pcdv100000000037x04" and doc_type == "poa"
        for document_id, doc_type in first_documents
    )
    assert first_requests == [
        ("pcdv100000000037:correction-item", "poa", "pcdv100000000037x04")
    ]

    runtime.seeder.seed_pilot_canonical_dataset(dry_run=False)
    second_documents = _canonical_document_snapshot(runtime.db)
    assert second_documents == first_documents
    assert _canonical_application_risk_snapshot(runtime.db) == first_applications
    assert _canonical_correction_request_snapshot(runtime.db) == first_requests
    second_document_versions = _canonical_document_row_version_snapshot(runtime.db)

    # This is the same normalization invoked during backend startup. A
    # canonical seed must leave it with no legacy values to rewrite, including
    # no PostgreSQL row-version changes.
    connection = runtime.db.get_db()
    try:
        runtime.db.normalize_legacy_doc_types(connection)
    finally:
        connection.close()

    assert _canonical_document_snapshot(runtime.db) == first_documents
    assert _canonical_document_row_version_snapshot(runtime.db) == second_document_versions
    assert _canonical_application_risk_snapshot(runtime.db) == first_applications
    assert _canonical_correction_request_snapshot(runtime.db) == first_requests
    assert runtime.canonical.manifest_sha256() == EXPECTED_MANIFEST_SHA256


def test_canonical_memo_and_periodic_ui_contracts_persist_on_postgres(
    canonical_postgres,
):
    runtime = canonical_postgres
    references = [
        "RM-PILOT-001",
        "RM-PILOT-005",
        "RM-PILOT-006",
        "RM-PILOT-008",
        "RM-PILOT-014",
        "RM-PILOT-017",
        "RM-PILOT-039",
        "RM-PILOT-040",
        "RM-PILOT-041",
    ]
    runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=False,
        references=references,
    )

    connection = runtime.db.get_db()
    try:
        memos = connection.execute(
            "SELECT a.ref,a.risk_score,a.risk_level,a.risk_config_version,cm.memo_data "
            "FROM compliance_memos cm JOIN applications a ON a.id=cm.application_id "
            "WHERE a.ref IN (?,?,?,?,?,?,?) ORDER BY a.ref",
            (
                "RM-PILOT-001", "RM-PILOT-006", "RM-PILOT-017",
                "RM-PILOT-039", "RM-PILOT-040", "RM-PILOT-041",
                "RM-PILOT-005",
            ),
        ).fetchall()
        assert len(memos) == 7
        for row in memos:
            memo = row["memo_data"]
            if isinstance(memo, str):
                memo = json.loads(memo)
            assert len(memo["sections"]) >= 11
            assert memo["metadata"]["risk_score"] == row["risk_score"]
            assert memo["metadata"]["risk_rating"] == row["risk_level"]
            assert memo["metadata"]["risk_config_version"] == row["risk_config_version"]
            assert memo["metadata"]["ai_supervisor_scope"] == "excluded_from_controlled_pilot"

        reviews = connection.execute(
            "SELECT a.ref,pr.last_review_date,pr.next_review_date,pr.due_date,pr.priority "
            "FROM periodic_reviews pr JOIN applications a ON a.id=pr.application_id "
            "WHERE a.ref IN (?,?,?,?) ORDER BY a.ref",
            ("RM-PILOT-005", "RM-PILOT-008", "RM-PILOT-014", "RM-PILOT-041"),
        ).fetchall()
        assert [row["ref"] for row in reviews] == [
            "RM-PILOT-005", "RM-PILOT-008", "RM-PILOT-014", "RM-PILOT-041"
        ]
        assert [row["priority"] for row in reviews] == ["low", "normal", "high", "low"]
        assert all(row["last_review_date"] for row in reviews)
        assert all(row["next_review_date"] == row["due_date"] for row in reviews)

        suppression_audits = connection.execute(
            "SELECT COUNT(*) AS n FROM audit_log "
            "WHERE action='fixture.pilot_canonical_notification_suppressed'"
        ).fetchone()["n"]
        assert suppression_audits == 4
    finally:
        connection.close()

    before = _public_table_counts(runtime.db)
    runtime.seeder.seed_pilot_canonical_dataset(
        dry_run=False,
        references=references,
    )
    assert _public_table_counts(runtime.db) == before
