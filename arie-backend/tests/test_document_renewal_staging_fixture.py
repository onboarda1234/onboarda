"""Focused safety contract for the governed Document Renewal fixture."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from fixtures.document_renewal_staging import (
    DocumentRenewalFixtureValidationError,
    FIXTURE_KEY,
    FIXTURE_MARKER,
    FIXTURE_VERSION,
    fixture_spec,
    load_manifest,
    manifest_sha256,
    validate_manifest,
)
from fixtures.document_renewal_staging_cli import (
    REQUIRED_ALLOW_VARIABLE,
    REQUIRED_CONFIRM_TOKEN,
    _enforce_apply_gates,
)
from fixtures.document_renewal_staging_seeder import (
    DocumentRenewalFixtureSeedError,
    STAGING_DATABASE_FINGERPRINT_ENV,
    _database_identity_fingerprint,
    _require_database_identity,
    seed_document_renewal_staging_fixture,
)
import monitoring_document_renewal as renewal


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
SCHEDULER_ACTOR = {
    "sub": "system:document-renewal-staging-fixture",
    "name": "Document Renewal Staging Fixture",
    "role": "system",
    "type": "system",
}


class Flags:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def is_enabled(self, name: str) -> bool:
        return self.enabled and name == renewal.FEATURE_FLAG


@pytest.fixture
def harness_db(tmp_path, monkeypatch):
    path = str(tmp_path / "document_renewal_harness.db")
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import config
    import db as db_module

    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATABASE_URL", "", raising=False)
    monkeypatch.setattr(config, "USE_POSTGRES", False, raising=False)
    monkeypatch.setattr(db_module, "DB_PATH", path)
    monkeypatch.setattr(db_module, "DATABASE_URL", "", raising=False)
    monkeypatch.setattr(db_module, "USE_POSTGRESQL", False)
    db_module.init_db()
    connection = db_module.get_db()
    try:
        yield connection
    finally:
        connection.close()


def _count(db, table: str, where: str = "1=1", params=()) -> int:
    row = db.execute(
        f"SELECT COUNT(*) AS count FROM {table} WHERE {where}", params
    ).fetchone()
    return int(row["count"])


def _seed(harness_db):
    return seed_document_renewal_staging_fixture(
        dry_run=False,
        db=harness_db,
    )


def test_manifest_is_exact_registered_and_safe():
    result = validate_manifest()
    spec = fixture_spec()
    assert result == {
        "valid": True,
        "fixture_key": FIXTURE_KEY,
        "fixture_version": FIXTURE_VERSION,
        "manifest_sha256": manifest_sha256(),
    }
    assert spec["fixture_key"] == "document-renewal-expired-v1"
    assert spec["fixture_marker"] == FIXTURE_MARKER
    assert spec["fixture_contract_version"] == FIXTURE_VERSION
    assert spec["application_id"].startswith("f1xed")
    assert spec["customer_id"].startswith("f1xed")
    assert spec["person_id"].startswith("f1xed")
    assert spec["document_id"].startswith("f1xed")
    assert spec["alert_source_reference"] == f"document:{spec['document_id']}"
    assert spec["request_id"] == "fxrnwreq00000001"
    assert load_manifest()["client"]["status"] == "inactive"

    from fixtures.registry import GOVERNED_STAGING_FIXTURE_MANIFESTS

    assert GOVERNED_STAGING_FIXTURE_MANIFESTS == {
        FIXTURE_KEY: "document_renewal_staging_fixture_v1.json"
    }

    with pytest.raises(
        DocumentRenewalFixtureValidationError,
        match="manifest sections",
    ):
        validate_manifest({})


def test_apply_gate_is_staging_only_and_hash_pinned(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv(REQUIRED_ALLOW_VARIABLE, "1")
    _enforce_apply_gates(
        confirm=REQUIRED_CONFIRM_TOKEN,
        reviewed_hash=manifest_sha256(),
    )
    with pytest.raises(SystemExit, match="reviewed-hash"):
        _enforce_apply_gates(
            confirm=REQUIRED_CONFIRM_TOKEN,
            reviewed_hash="0" * 64,
        )
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(SystemExit, match="ENVIRONMENT=staging"):
        _enforce_apply_gates(
            confirm=REQUIRED_CONFIRM_TOKEN,
            reviewed_hash=manifest_sha256(),
        )


def test_staging_seed_is_bound_to_approved_database_identity(monkeypatch):
    identity = "postgresql://ignored-user:ignored-password@db.internal:5432/regmind_staging"
    db = SimpleNamespace(is_postgres=True, database_identity=identity)
    approved = _database_identity_fingerprint(identity)

    monkeypatch.delenv(STAGING_DATABASE_FINGERPRINT_ENV, raising=False)
    with pytest.raises(DocumentRenewalFixtureSeedError, match="requires"):
        _require_database_identity(db, "staging")

    monkeypatch.setenv(STAGING_DATABASE_FINGERPRINT_ENV, "0" * 64)
    with pytest.raises(DocumentRenewalFixtureSeedError, match="does not match"):
        _require_database_identity(db, "staging")

    monkeypatch.setenv(STAGING_DATABASE_FINGERPRINT_ENV, approved)
    _require_database_identity(db, "staging")
    _require_database_identity(SimpleNamespace(), "testing")

    with pytest.raises(DocumentRenewalFixtureSeedError, match="complete PostgreSQL"):
        _database_identity_fingerprint(
            identity + "?host=production-db.internal&dbname=regmind_production"
        )


def test_seed_dry_run_is_repeatable_and_writes_nothing(harness_db):
    first = seed_document_renewal_staging_fixture(
        dry_run=True,
        db=harness_db,
    )
    second = seed_document_renewal_staging_fixture(
        dry_run=True,
        db=harness_db,
    )
    assert first == second
    assert first["dry_run"] is True
    assert first["read_only"] is True
    assert first["would_create"] is True
    assert first["created"] is False
    assert first["rolled_back"] is False
    assert first["alert_id"] is None
    assert first["linkage_status"] == "planned"
    spec = fixture_spec()
    assert _count(harness_db, "clients", "id=?", (spec["customer_id"],)) == 0
    assert _count(harness_db, "applications", "id=?", (spec["application_id"],)) == 0
    assert _count(harness_db, "directors", "id=?", (spec["person_id"],)) == 0
    assert _count(harness_db, "documents", "id=?", (spec["document_id"],)) == 0
    assert _count(
        harness_db,
        "monitoring_alerts",
        "source_reference=?",
        (spec["alert_source_reference"],),
    ) == 0
    assert _count(
        harness_db,
        "audit_log",
        "action LIKE 'fixture.document_renewal_staging_%'",
    ) == 0


def test_operator_dry_run_never_initializes_or_migrates_schema(
    harness_db,
    monkeypatch,
):
    import db as db_module

    operator_connection = db_module.get_db()

    def forbidden_init_db(*_args, **_kwargs):
        raise AssertionError("fixture dry-run must not initialize or migrate schema")

    monkeypatch.setattr(db_module, "init_db", forbidden_init_db)
    monkeypatch.setattr(db_module, "get_db", lambda: operator_connection)

    result = seed_document_renewal_staging_fixture(dry_run=True)

    assert result["read_only"] is True
    assert result["would_create"] is True
    assert _count(harness_db, "applications") == 0


def test_seed_is_idempotent_linked_and_login_disabled(harness_db):
    first = _seed(harness_db)
    audit_count = _count(
        harness_db,
        "audit_log",
        "action LIKE 'fixture.document_renewal_staging_%'",
    )
    second = _seed(harness_db)
    assert first["created"] is True
    assert second["created"] is False
    assert second["alert_id"] == first["alert_id"]
    assert audit_count == 5
    assert _count(
        harness_db,
        "audit_log",
        "action LIKE 'fixture.document_renewal_staging_%'",
    ) == audit_count
    fixture_audit = harness_db.execute(
        """
        SELECT entry_hash, previous_hash FROM audit_log
         WHERE action LIKE 'fixture.document_renewal_staging_%'
         ORDER BY id
        """
    ).fetchall()
    assert len(fixture_audit) == 5
    assert all(str(row["entry_hash"] or "") for row in fixture_audit)
    assert all(
        fixture_audit[index]["previous_hash"]
        == fixture_audit[index - 1]["entry_hash"]
        for index in range(1, len(fixture_audit))
    )
    client = harness_db.execute(
        "SELECT status,password_hash FROM clients WHERE id=?",
        (first["customer_id"],),
    ).fetchone()
    assert dict(client) == {
        "status": "inactive",
        "password_hash": "fixture-no-login",
    }
    assert _count(
        harness_db,
        "clients",
        "id=? AND status='active'",
        (first["customer_id"],),
    ) == 0

    from monitoring_alert_linkage import resolve_alert_linkage

    linked = resolve_alert_linkage(harness_db, first["alert_id"])
    assert linked["linkage_status"] == "linked"
    assert linked["application"]["id"] == first["application_id"]
    assert linked["application"]["customer_id"] == first["customer_id"]
    assert linked["document"]["canonical_document_id"] == first["document_id"]
    assert linked["document"]["person_id"] == first["person_id"]
    assert linked["document"]["source_is_current"] is True


def test_partial_or_drifted_reserved_identity_fails_without_repair(harness_db):
    seeded = _seed(harness_db)
    harness_db.execute(
        "UPDATE applications SET is_fixture=0 WHERE id=?",
        (seeded["application_id"],),
    )
    harness_db.commit()
    before = _count(harness_db, "audit_log")
    with pytest.raises(DocumentRenewalFixtureSeedError, match="baseline drift"):
        _seed(harness_db)
    assert _count(harness_db, "audit_log") == before
    assert _count(harness_db, "applications", "id=?", (seeded["application_id"],)) == 1


def test_seeder_refuses_extra_same_application_footprint(harness_db):
    seeded = _seed(harness_db)
    harness_db.execute(
        """
        INSERT INTO client_notifications
            (application_id,client_id,notification_type,title,message)
        VALUES (?,?,'unexpected_fixture_state','Unexpected','Unexpected')
        """,
        (seeded["application_id"], seeded["customer_id"]),
    )
    harness_db.commit()

    with pytest.raises(
        DocumentRenewalFixtureSeedError,
        match="partial or colliding",
    ):
        _seed(harness_db)

    assert _count(
        harness_db,
        "client_notifications",
        "application_id=?",
        (seeded["application_id"],),
    ) == 1


def test_seeder_refuses_production_before_db_access(monkeypatch):
    class NoDatabaseAccess:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("database must not be queried")

    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(DocumentRenewalFixtureSeedError, match="only in staging or testing"):
        seed_document_renewal_staging_fixture(
            dry_run=False,
            db=NoDatabaseAccess(),
        )


def test_fixture_only_scheduler_creates_exactly_once_and_ignores_nonfixture(
    harness_db,
):
    fixture = _seed(harness_db)
    harness_db.execute(
        "INSERT INTO clients(id,email,password_hash,company_name,status) "
        "VALUES ('real-client','real@example.invalid','x','Real Ltd','active')"
    )
    harness_db.execute(
        "INSERT INTO applications(id,ref,client_id,company_name,status,is_fixture) "
        "VALUES ('real-app','REAL-APP','real-client','Real Ltd','approved',0)"
    )
    harness_db.execute(
        """
        INSERT INTO documents
            (id,application_id,doc_type,doc_name,file_path,slot_key,is_current,
             version,expiry_date,verification_status,review_status)
        VALUES ('real-doc','real-app','passport','real.pdf','real://doc',
                'entity:passport',1,1,'2025-01-01','verified','accepted')
        """
    )
    harness_db.execute(
        """
        INSERT INTO monitoring_alerts
            (application_id,client_name,alert_type,severity,detected_by,summary,
             source_reference,status,discovered_via)
        VALUES ('real-app','Real Ltd','document_expired','high',
                'document_health_monitor','passport expired','document:real-doc',
                'open','document_health')
        """
    )
    harness_db.commit()
    fixture_document_before = dict(
        harness_db.execute(
            "SELECT * FROM documents WHERE id=?", (fixture["document_id"],)
        ).fetchone()
    )
    fixture_alert_before = dict(
        harness_db.execute(
            "SELECT * FROM monitoring_alerts WHERE id=?", (fixture["alert_id"],)
        ).fetchone()
    )

    first = renewal.run_fixture_only_renewal_eligibility(
        harness_db,
        actor=SCHEDULER_ACTOR,
        now=NOW,
        feature_flags=Flags(True),
    )
    second = renewal.run_fixture_only_renewal_eligibility(
        harness_db,
        actor=SCHEDULER_ACTOR,
        now=NOW,
        feature_flags=Flags(True),
    )

    assert first["mode"] == "fixture_only"
    assert first["scanned"] == 1
    assert first["created"] == 1
    assert first["non_fixture_scanned"] == 0
    assert first["request_id"] == fixture["request_id"]
    assert second["created"] == 0
    assert second["already_active"] == 1
    assert _count(harness_db, "monitoring_document_renewal_requests") == 1
    assert _count(
        harness_db,
        "monitoring_document_renewal_requests",
        "application_id='real-app'",
    ) == 0
    assert _count(harness_db, "monitoring_document_renewal_scheduler_state") == 0
    assert dict(
        harness_db.execute(
            "SELECT * FROM documents WHERE id=?", (fixture["document_id"],)
        ).fetchone()
    ) == fixture_document_before
    assert dict(
        harness_db.execute(
            "SELECT * FROM monitoring_alerts WHERE id=?", (fixture["alert_id"],)
        ).fetchone()
    ) == fixture_alert_before


@pytest.mark.parametrize("drift", ["fixture_marker", "is_fixture", "duplicate_alert"])
def test_fixture_only_scheduler_scope_drift_fails_before_writes(
    harness_db, drift
):
    fixture = _seed(harness_db)
    if drift == "fixture_marker":
        data = json.loads(
            harness_db.execute(
                "SELECT prescreening_data FROM applications WHERE id=?",
                (fixture["application_id"],),
            ).fetchone()["prescreening_data"]
        )
        data["fixture_marker"] = "WRONG"
        harness_db.execute(
            "UPDATE applications SET prescreening_data=? WHERE id=?",
            (json.dumps(data), fixture["application_id"]),
        )
    elif drift == "is_fixture":
        harness_db.execute(
            "UPDATE applications SET is_fixture=0 WHERE id=?",
            (fixture["application_id"],),
        )
    else:
        manifest = load_manifest()["alert"]
        harness_db.execute(
            """
            INSERT INTO monitoring_alerts
                (application_id,client_name,alert_type,severity,detected_by,
                 summary,source_reference,status,discovered_via)
            VALUES (?,?,?,?,?,?,?,'open',?)
            """,
            (
                fixture["application_id"],
                "FIX Document Renewal Harness Ltd",
                manifest["alert_type"],
                manifest["severity"],
                manifest["detected_by"],
                manifest["summary"],
                manifest["source_reference"],
                manifest["discovered_via"],
            ),
        )
    harness_db.commit()
    before_audit = _count(harness_db, "audit_log")
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.run_fixture_only_renewal_eligibility(
            harness_db,
            actor=SCHEDULER_ACTOR,
            now=NOW,
            feature_flags=Flags(True),
        )
    assert exc.value.code == "fixture_scope_invalid"
    assert _count(harness_db, "monitoring_document_renewal_requests") == 0
    assert _count(harness_db, "client_notifications") == 0
    assert _count(harness_db, "audit_log") == before_audit


def test_fixture_only_scheduler_requires_flag_and_nonproduction_environment(
    harness_db, monkeypatch
):
    _seed(harness_db)
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.run_fixture_only_renewal_eligibility(
            harness_db,
            actor=SCHEDULER_ACTOR,
            now=NOW,
            feature_flags=Flags(False),
        )
    assert exc.value.code == "feature_disabled"
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.run_fixture_only_renewal_eligibility(
            harness_db,
            actor=SCHEDULER_ACTOR,
            now=NOW,
            feature_flags=Flags(True),
        )
    assert exc.value.code == "fixture_scope_forbidden"
    assert _count(harness_db, "monitoring_document_renewal_requests") == 0
