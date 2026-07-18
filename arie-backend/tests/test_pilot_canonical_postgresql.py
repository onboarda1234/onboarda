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
    "fee7436a6bf6ead1cc9a8090ceaa3de7071a9b745e43f2c69a445cf74efdf9c9"
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
