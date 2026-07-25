from __future__ import annotations

import json
import sqlite3

import pytest

import scripts.repair_kyc_party_document_links as repair_tool
from scripts.repair_kyc_party_document_links import (
    APPLY_CONFIRMATION,
    RepairSafetyError,
    database_identity_fingerprint,
    diagnose_repair,
    run_repair,
)


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE applications (
            id TEXT PRIMARY KEY,
            ref TEXT,
            company_name TEXT,
            status TEXT,
            is_fixture INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE directors (
            id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            person_key TEXT
        );
        CREATE TABLE ubos (
            id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            person_key TEXT
        );
        CREATE TABLE intermediaries (
            id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            person_key TEXT
        );
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            person_id TEXT,
            person_type TEXT,
            doc_type TEXT NOT NULL,
            slot_key TEXT,
            is_current INTEGER DEFAULT 1,
            version INTEGER DEFAULT 1
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            user_id TEXT,
            user_name TEXT,
            user_role TEXT,
            action TEXT NOT NULL,
            target TEXT,
            application_id TEXT,
            detail TEXT,
            ip_address TEXT,
            before_state TEXT,
            after_state TEXT,
            previous_hash TEXT,
            entry_hash TEXT,
            request_id TEXT
        );
        CREATE UNIQUE INDEX uq_audit_log_previous_hash
            ON audit_log(previous_hash)
            WHERE previous_hash IS NOT NULL;
        """
    )
    return db


@pytest.fixture
def sqlite_apply_unit(monkeypatch):
    """Let SQLite exercise atomic apply logic after identity is tested alone."""
    monkeypatch.setattr(
        repair_tool,
        "_assert_staging_database_identity",
        lambda db, **kwargs: None,
    )


class _IdentityResult:
    def __init__(self, database_name):
        self.database_name = database_name

    def fetchone(self):
        return {"database_name": self.database_name}


class _PostgresIdentityProbe:
    is_postgres = True

    def __init__(
        self,
        database_name,
        *,
        host="regmind-staging.example.test",
        port="5432",
        hostaddr="",
    ):
        self.database_name = database_name
        self.queries = []
        self.conn = self
        self.dsn_parameters = {
            "host": host,
            "hostaddr": hostaddr,
            "port": port,
            "dbname": database_name,
        }

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        return _IdentityResult(self.database_name)

    def get_dsn_parameters(self):
        return dict(self.dsn_parameters)


def _insert_app(
    db,
    *,
    app_id="app-s01",
    ref="ARF-2026-100421",
    company_name="E2E-20260724-150642-S01-Low-Risk",
    is_fixture=1,
):
    db.execute(
        """
        INSERT INTO applications (id, ref, company_name, status, is_fixture)
        VALUES (?, ?, ?, 'pricing_accepted', ?)
        """,
        (app_id, ref, company_name, is_fixture),
    )
    db.commit()


def _insert_director(
    db,
    *,
    app_id="app-s01",
    party_id="director-stable-1",
    person_key="dir1",
):
    db.execute(
        "INSERT INTO directors (id, application_id, person_key) VALUES (?, ?, ?)",
        (party_id, app_id, person_key),
    )
    db.commit()


def _insert_doc(
    db,
    *,
    doc_id="doc-1",
    app_id="app-s01",
    person_id="dir1",
    person_type=None,
    doc_type="poa",
    slot_key="person:dir1:poa",
    is_current=1,
    version=1,
):
    db.execute(
        """
        INSERT INTO documents
            (id, application_id, person_id, person_type, doc_type, slot_key,
             is_current, version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            app_id,
            person_id,
            person_type,
            doc_type,
            slot_key,
            is_current,
            version,
        ),
    )
    db.commit()


def test_dry_run_reports_exact_fixture_and_document_changes_without_writes():
    db = _db()
    _insert_app(db)
    _insert_director(db)
    _insert_doc(db)

    report = diagnose_repair(db, ["ARF-2026-100421"])

    assert report["outcome"] == "ready"
    assert report["summary"]["application_changes"] == 1
    assert report["summary"]["document_changes"] == 1
    app = report["applications"][0]
    assert app["fixture"]["change"]["to"] is False
    assert app["parties"] == [
        {
            "person_type": "director",
            "party_id": "director-stable-1",
            "person_key": "dir1",
            "change": None,
            "reference_aliases": ["director-stable-1", "dir1"],
        }
    ]
    document = app["documents"][0]
    assert document["action"] == "repair"
    assert document["proposed"] == {
        "person_id": "director-stable-1",
        "person_type": "director",
        "slot_key": "person:director:director-stable-1:poa",
    }

    stored_app = db.execute(
        "SELECT is_fixture FROM applications WHERE id='app-s01'"
    ).fetchone()
    stored_doc = db.execute(
        "SELECT person_id, person_type, slot_key, doc_type FROM documents WHERE id='doc-1'"
    ).fetchone()
    assert stored_app["is_fixture"] == 1
    assert dict(stored_doc) == {
        "person_id": "dir1",
        "person_type": None,
        "slot_key": "person:dir1:poa",
        "doc_type": "poa",
    }
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_noncanonical_stored_document_type_refuses_atomic_apply_without_writes(
    sqlite_apply_unit,
):
    db = _db()
    _insert_app(db)
    _insert_director(db)
    _insert_doc(
        db,
        doc_type="proof_of_address",
        slot_key="person:dir1:proof_of_address",
    )

    report = run_repair(
        db,
        ["ARF-2026-100421"],
        apply=True,
        environment="staging",
        confirmation=APPLY_CONFIRMATION,
    )

    assert report["outcome"] == "refused"
    refusal = report["refusals"][0]
    assert refusal == {
        "scope": "document",
        "application_ref": "ARF-2026-100421",
        "document_id": "doc-1",
        "code": "noncanonical_stored_document_type",
        "stored_doc_type": "proof_of_address",
        "canonical_doc_type": "poa",
        "reason": (
            "document category aliases require a separately reviewed migration; "
            "this repair does not mutate doc_type"
        ),
    }
    document = report["applications"][0]["documents"][0]
    assert document["action"] == "refused"
    assert document["proposed"] is None
    stored_app = db.execute(
        "SELECT is_fixture FROM applications WHERE id='app-s01'"
    ).fetchone()
    stored_doc = db.execute(
        "SELECT person_id, person_type, doc_type, slot_key "
        "FROM documents WHERE id='doc-1'"
    ).fetchone()
    assert stored_app["is_fixture"] == 1
    assert dict(stored_doc) == {
        "person_id": "dir1",
        "person_type": None,
        "doc_type": "proof_of_address",
        "slot_key": "person:dir1:proof_of_address",
    }
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_repair_preserves_cross_surface_document_type_slot_invariant(
    sqlite_apply_unit,
):
    db = _db()
    _insert_app(db, is_fixture=0)
    _insert_director(db)
    _insert_doc(db, doc_type="poa", slot_key="person:dir1:poa")

    diagnosed = diagnose_repair(db, ["ARF-2026-100421"])

    document = diagnosed["applications"][0]["documents"][0]
    assert diagnosed["outcome"] == "ready"
    assert document["doc_type"] == document["normalized_doc_type"] == "poa"
    assert (
        document["proposed"]["slot_key"].rsplit(":", 1)[-1]
        == document["doc_type"]
    )
    assert "doc_type" not in document["proposed"]

    applied = run_repair(
        db,
        ["ARF-2026-100421"],
        apply=True,
        environment="staging",
        confirmation=APPLY_CONFIRMATION,
    )

    assert applied["outcome"] == "applied"
    stored = db.execute(
        "SELECT doc_type, slot_key FROM documents WHERE id='doc-1'"
    ).fetchone()
    assert dict(stored) == {
        "doc_type": "poa",
        "slot_key": "person:director:director-stable-1:poa",
    }


@pytest.mark.parametrize(
    ("party_table", "person_type", "doc_type"),
    [
        ("directors", "director", "cert_inc"),
        ("ubos", "ubo", "unknown_category"),
        ("intermediaries", "intermediary", "passport"),
    ],
)
def test_invalid_document_scope_refuses_atomic_apply_without_writes(
    sqlite_apply_unit,
    party_table,
    person_type,
    doc_type,
):
    db = _db()
    _insert_app(db)
    db.execute(
        f"INSERT INTO {party_table} (id, application_id, person_key) "
        "VALUES ('party-stable-1', 'app-s01', 'legacy-party-1')"
    )
    _insert_doc(
        db,
        person_id="legacy-party-1",
        person_type=None,
        doc_type=doc_type,
        slot_key=f"person:legacy-party-1:{doc_type}",
    )

    report = run_repair(
        db,
        ["ARF-2026-100421"],
        apply=True,
        environment="staging",
        confirmation=APPLY_CONFIRMATION,
    )

    assert report["outcome"] == "refused"
    refusal = next(
        item
        for item in report["refusals"]
        if item.get("code") == "invalid_document_scope"
    )
    assert refusal["document_id"] == "doc-1"
    assert refusal["doc_type"] == doc_type
    assert refusal["person_type"] == person_type
    assert "ordinary upload policy would reject" in refusal["reason"]
    document = report["applications"][0]["documents"][0]
    assert document["action"] == "refused"
    assert document["proposed"] is None

    stored_app = db.execute(
        "SELECT is_fixture FROM applications WHERE id='app-s01'"
    ).fetchone()
    stored_doc = db.execute(
        "SELECT person_id, person_type, doc_type, slot_key "
        "FROM documents WHERE id='doc-1'"
    ).fetchone()
    assert stored_app["is_fixture"] == 1
    assert dict(stored_doc) == {
        "person_id": "legacy-party-1",
        "person_type": None,
        "doc_type": doc_type,
        "slot_key": f"person:legacy-party-1:{doc_type}",
    }
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("environment", "confirmation"),
    [
        ("production", APPLY_CONFIRMATION),
        ("demo", APPLY_CONFIRMATION),
        ("staging", "yes"),
        (None, APPLY_CONFIRMATION),
        ("staging", APPLY_CONFIRMATION),
    ],
)
def test_apply_requires_exact_staging_environment_and_confirmation(
    environment,
    confirmation,
):
    db = _db()
    _insert_app(db, is_fixture=0)
    _insert_director(db)
    _insert_doc(db)

    database_identity = (
        "postgresql://db/regmind_production"
        if environment == "staging" and confirmation == APPLY_CONFIRMATION
        else None
    )
    with pytest.raises(RepairSafetyError):
        run_repair(
            db,
            ["ARF-2026-100421"],
            apply=True,
            environment=environment,
            confirmation=confirmation,
            database_identity=database_identity,
        )

    assert db.execute(
        "SELECT person_id FROM documents WHERE id='doc-1'"
    ).fetchone()["person_id"] == "dir1"


def test_dry_run_refuses_production_environment_before_querying():
    db = _db()
    _insert_app(db, is_fixture=0)

    with pytest.raises(RepairSafetyError, match="production access"):
        run_repair(
            db,
            ["ARF-2026-100421"],
            environment="production",
        )


def test_positive_staging_database_fingerprint_ignores_credentials():
    identity = (
        "postgresql://staging_user:not-a-real-secret@"
        "regmind-staging.example.test:5432/regmind_staging"
    )
    expected = database_identity_fingerprint(
        "postgresql://another_user:another_value@"
        "regmind-staging.example.test/regmind_staging"
    )
    db = _PostgresIdentityProbe("regmind_staging")

    repair_tool._assert_staging_database_identity(
        db,
        database_identity=identity,
        expected_fingerprint=expected,
    )

    assert db.queries == [
        ("SELECT current_database() AS database_name", ())
    ]


def test_positive_staging_database_fingerprint_allows_non_target_tls_parameters():
    plain = database_identity_fingerprint(
        "postgresql://regmind-staging.example.test/regmind_staging"
    )
    tls = database_identity_fingerprint(
        "postgresql://regmind-staging.example.test/regmind_staging"
        "?sslmode=require&connect_timeout=10&application_name=kyc-repair"
    )

    assert tls == plain


@pytest.mark.parametrize(
    "database_identity",
    [
        "postgresql://regmind-staging.example.test/regmind_staging?host=opaque-prod",
        "postgresql://regmind-staging.example.test/regmind_staging?hostaddr=10.0.0.8",
        "postgresql://regmind-staging.example.test/regmind_staging?port=6432",
        "postgresql://regmind-staging.example.test/regmind_staging?dbname=production",
        "postgresql://regmind-staging.example.test/regmind_staging?service=production",
        "postgresql://regmind-staging.example.test/regmind_staging?%68ost=opaque-prod",
        "postgresql://staging-a.example.test,opaque-prod/regmind_staging",
        "postgresql://regmind-staging.example.test/regmind_staging#ignored-target",
    ],
)
def test_positive_staging_database_fingerprint_rejects_target_override_bypasses(
    database_identity,
):
    with pytest.raises(RepairSafetyError):
        database_identity_fingerprint(database_identity)


@pytest.mark.parametrize(
    ("database_identity", "expected_fingerprint", "database_name", "message"),
    [
        (
            "postgresql://opaque.example.test/regmind_staging",
            None,
            "regmind_staging",
            "pre-approved",
        ),
        (
            "postgresql://opaque.example.test/regmind_staging",
            "0" * 64,
            "regmind_staging",
            "pre-approved staging fingerprint",
        ),
        (
            "postgresql://opaque.example.test/regmind_staging",
            None,
            "regmind_production",
            "pre-approved",
        ),
    ],
)
def test_positive_staging_database_fingerprint_refuses_missing_or_mismatch(
    database_identity,
    expected_fingerprint,
    database_name,
    message,
):
    if expected_fingerprint is None and database_name == "regmind_production":
        expected_fingerprint = database_identity_fingerprint(database_identity)
        message = "host, port, or database"
    db = _PostgresIdentityProbe(
        database_name,
        host="opaque.example.test",
    )

    with pytest.raises(RepairSafetyError, match=message):
        repair_tool._assert_staging_database_identity(
            db,
            database_identity=database_identity,
            expected_fingerprint=expected_fingerprint,
        )


def test_positive_staging_database_fingerprint_refuses_non_postgres():
    with pytest.raises(RepairSafetyError, match="PostgreSQL staging"):
        repair_tool._assert_staging_database_identity(
            _db(),
            database_identity="postgresql://opaque.example.test/regmind_staging",
            expected_fingerprint="0" * 64,
        )


def test_preconnect_guard_requires_positive_staging_uri_fingerprint():
    identity = "postgresql://regmind-staging.example.test/regmind_staging"
    fingerprint = database_identity_fingerprint(identity)

    repair_tool._assert_staging_preconnect_identity(
        environment="staging",
        database_identity=identity,
        expected_fingerprint=fingerprint,
    )

    with pytest.raises(RepairSafetyError, match="pre-approved"):
        repair_tool._assert_staging_preconnect_identity(
            environment="staging",
            database_identity=identity,
            expected_fingerprint=None,
        )
    with pytest.raises(RepairSafetyError, match="does not match"):
        repair_tool._assert_staging_preconnect_identity(
            environment="staging",
            database_identity=identity,
            expected_fingerprint="0" * 64,
        )
    with pytest.raises(RepairSafetyError, match="ENVIRONMENT=staging"):
        repair_tool._assert_staging_preconnect_identity(
            environment="development",
            database_identity=identity,
            expected_fingerprint=fingerprint,
        )


@pytest.mark.parametrize(
    "variable_name",
    repair_tool.TARGET_AFFECTING_LIBPQ_ENVIRONMENT,
)
def test_apply_guard_refuses_ambient_libpq_target_redirects(
    monkeypatch,
    variable_name,
):
    monkeypatch.setenv(variable_name, "opaque-redirect")

    with pytest.raises(RepairSafetyError, match=variable_name):
        repair_tool._assert_apply_guard(
            environment="staging",
            confirmation=APPLY_CONFIRMATION,
            database_identity=(
                "postgresql://regmind-staging.example.test/regmind_staging"
            ),
        )


@pytest.mark.parametrize(
    ("probe", "message"),
    [
        (
            _PostgresIdentityProbe(
                "regmind_staging",
                host="opaque-production.example.test",
            ),
            "host, port, or database",
        ),
        (
            _PostgresIdentityProbe("regmind_staging", port="65432"),
            "host, port, or database",
        ),
        (
            _PostgresIdentityProbe("another_database"),
            "host, port, or database",
        ),
        (
            _PostgresIdentityProbe(
                "regmind_staging",
                hostaddr="127.0.0.2",
            ),
            "hostaddr",
        ),
    ],
)
def test_positive_staging_database_fingerprint_rejects_effective_target_mismatch(
    probe,
    message,
):
    identity = "postgresql://regmind-staging.example.test/regmind_staging"
    with pytest.raises(RepairSafetyError, match=message):
        repair_tool._assert_staging_database_identity(
            probe,
            database_identity=identity,
            expected_fingerprint=database_identity_fingerprint(identity),
        )


def test_apply_is_atomic_audited_and_idempotent(sqlite_apply_unit):
    db = _db()
    _insert_app(db)
    _insert_director(db)
    _insert_doc(db)

    applied = run_repair(
        db,
        ["ARF-2026-100421"],
        apply=True,
        environment="staging",
        confirmation=APPLY_CONFIRMATION,
    )

    assert applied["outcome"] == "applied"
    assert applied["summary"]["applied_changes"] == 2
    assert applied["applications"][0]["documents"][0]["action"] == "applied"
    assert db.execute(
        "SELECT is_fixture FROM applications WHERE id='app-s01'"
    ).fetchone()["is_fixture"] == 0
    stored = db.execute(
        "SELECT person_id, person_type, slot_key, doc_type FROM documents WHERE id='doc-1'"
    ).fetchone()
    assert dict(stored) == {
        "person_id": "director-stable-1",
        "person_type": "director",
        "slot_key": "person:director:director-stable-1:poa",
        "doc_type": "poa",
    }
    audit = db.execute(
        "SELECT before_state, after_state, entry_hash FROM audit_log"
    ).fetchone()
    assert len(audit["entry_hash"]) == 64
    assert json.loads(audit["before_state"])["documents"][0]["person_type"] is None
    assert json.loads(audit["after_state"])["documents"][0]["person_type"] == "director"

    second = run_repair(db, ["ARF-2026-100421"])
    assert second["outcome"] == "no_changes"
    assert second["summary"]["application_changes"] == 0
    assert second["summary"]["document_changes"] == 0
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1


def test_duplicate_party_reference_refuses_entire_apply_without_partial_writes(
    sqlite_apply_unit,
):
    db = _db()
    _insert_app(db, is_fixture=0)
    _insert_director(db, party_id="director-stable-1", person_key="owner1")
    db.execute(
        "INSERT INTO ubos (id, application_id, person_key) VALUES (?, ?, ?)",
        ("ubo-stable-1", "app-s01", "owner1"),
    )
    db.commit()
    _insert_doc(db, person_id="owner1", slot_key=None)

    report = run_repair(
        db,
        ["ARF-2026-100421"],
        apply=True,
        environment="staging",
        confirmation=APPLY_CONFIRMATION,
    )

    assert report["outcome"] == "refused"
    codes = {refusal["code"] for refusal in report["refusals"]}
    assert "duplicate_party_reference" in codes
    assert "ambiguous_document_party_reference" in codes
    assert db.execute(
        "SELECT person_id, slot_key FROM documents WHERE id='doc-1'"
    ).fetchone()["person_id"] == "owner1"
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_unresolved_reference_and_person_slot_without_owner_are_refused():
    db = _db()
    _insert_app(db, is_fixture=0)
    _insert_director(db)
    _insert_doc(db, doc_id="doc-unresolved", person_id="missing", slot_key=None)
    _insert_doc(
        db,
        doc_id="doc-missing-owner",
        person_id=None,
        slot_key="person:director:director-stable-1:passport",
    )

    report = diagnose_repair(db, ["ARF-2026-100421"])

    assert report["outcome"] == "refused"
    codes = {refusal["code"] for refusal in report["refusals"]}
    assert "unresolved_document_party_reference" in codes
    assert "missing_document_person_reference" in codes


def test_conflicting_slot_and_document_party_refuses_displacement():
    db = _db()
    _insert_app(db, is_fixture=0)
    _insert_director(db)
    db.execute(
        "INSERT INTO ubos (id, application_id, person_key) VALUES (?, ?, ?)",
        ("ubo-stable-1", "app-s01", "ubo1"),
    )
    db.commit()
    _insert_doc(
        db,
        person_id="dir1",
        slot_key="person:ubo:ubo1:poa",
    )

    report = diagnose_repair(db, ["ARF-2026-100421"])

    assert report["outcome"] == "refused"
    assert "conflicting_document_and_slot_party" in {
        refusal["code"] for refusal in report["refusals"]
    }


@pytest.mark.parametrize(
    ("slot_key", "expected_code"),
    [
        (
            "person:ubo:dir1:poa",
            "conflicting_slot_person_type",
        ),
        (
            "person:director:dir1:passport",
            "conflicting_slot_document_type",
        ),
        (
            "person:director:obsolete-party-id:poa",
            "unresolved_conflicting_slot_party_reference",
        ),
    ],
)
def test_conflicting_slot_metadata_is_refused_instead_of_silently_corrected(
    slot_key,
    expected_code,
):
    db = _db()
    _insert_app(db, is_fixture=0)
    _insert_director(db)
    _insert_doc(
        db,
        person_id="dir1",
        person_type="director",
        slot_key=slot_key,
    )

    report = diagnose_repair(db, ["ARF-2026-100421"])

    assert report["outcome"] == "refused"
    assert expected_code in {
        refusal["code"] for refusal in report["refusals"]
    }
    assert db.execute(
        "SELECT person_id, person_type, slot_key FROM documents WHERE id='doc-1'"
    ).fetchone()["slot_key"] == slot_key


@pytest.mark.parametrize(
    ("stored_person_type", "expected_code"),
    [
        ("ubo", "conflicting_stored_document_person_type"),
        ("shareholder", "invalid_document_person_type"),
    ],
)
def test_invalid_or_conflicting_stored_person_type_is_refused(
    stored_person_type,
    expected_code,
):
    db = _db()
    _insert_app(db, is_fixture=0)
    _insert_director(db)
    _insert_doc(db, person_type=stored_person_type, slot_key=None)

    report = diagnose_repair(db, ["ARF-2026-100421"])

    assert report["outcome"] == "refused"
    assert expected_code in {
        refusal["code"] for refusal in report["refusals"]
    }


def test_apply_refuses_schema_without_explicit_document_person_type_column(
    sqlite_apply_unit,
):
    db = _db()
    db.execute("ALTER TABLE documents DROP COLUMN person_type")
    _insert_app(db, is_fixture=0)
    _insert_director(db)
    _insert_doc_sql = (
        "INSERT INTO documents "
        "(id, application_id, person_id, doc_type, slot_key, is_current, version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    db.execute(
        _insert_doc_sql,
        (
            "doc-1",
            "app-s01",
            "dir1",
            "poa",
            None,
            1,
            1,
        ),
    )
    db.commit()

    report = run_repair(
        db,
        ["ARF-2026-100421"],
        apply=True,
        environment="staging",
        confirmation=APPLY_CONFIRMATION,
    )

    assert report["outcome"] == "refused"
    assert report["refusals"] == [
        {
            "scope": "schema",
            "code": "missing_required_columns",
            "table": "documents",
            "columns": ["person_type"],
        }
    ]
    assert db.execute(
        "SELECT person_id FROM documents WHERE id='doc-1'"
    ).fetchone()["person_id"] == "dir1"
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_duplicate_projected_current_slot_is_refused_but_version_history_is_safe():
    db = _db()
    _insert_app(db, is_fixture=0)
    _insert_director(db)
    _insert_doc(db, doc_id="doc-current-1", slot_key=None, is_current=1)
    _insert_doc(db, doc_id="doc-current-2", slot_key=None, is_current=1, version=2)

    duplicate = diagnose_repair(db, ["ARF-2026-100421"])
    assert duplicate["outcome"] == "refused"
    assert "duplicate_projected_current_slot" in {
        refusal["code"] for refusal in duplicate["refusals"]
    }

    db.execute("UPDATE documents SET is_current=0 WHERE id='doc-current-1'")
    db.commit()
    historical = diagnose_repair(db, ["ARF-2026-100421"])
    assert historical["outcome"] == "ready"
    assert historical["summary"]["document_changes"] == 2


def test_fixture_marker_correction_refuses_unknown_reuse_identity_and_reserved_id():
    db = _db()
    _insert_app(
        db,
        app_id="unknown-reuse",
        company_name="Some Other Company",
        is_fixture=1,
    )
    _insert_app(
        db,
        app_id="f1xed00000000001",
        ref="ARF-FIXTURE-1",
        company_name="Fixture Corp",
        is_fixture=1,
    )

    report = diagnose_repair(
        db,
        ["ARF-2026-100421", "ARF-FIXTURE-1"],
    )

    codes = {refusal["code"] for refusal in report["refusals"]}
    assert "unapproved_fixture_ref_reuse_identity" in codes
    assert "reserved_fixture_application" in codes
    assert report["summary"]["application_changes"] == 0


@pytest.mark.parametrize(
    ("ref", "company_name"),
    [
        (
            "ARF-2026-100428",
            "E2E-20260724-150642-S03-Geographic-Risk",
        ),
        (
            "ARF-2026-100424",
            "E2E-20260724-150642-S07-Higher-Risk-Sector",
        ),
    ],
)
def test_fixture_marker_correction_authorizes_mandated_enhanced_ref_reuse(
    ref,
    company_name,
):
    db = _db()
    _insert_app(
        db,
        app_id=f"app-{ref[-6:]}",
        ref=ref,
        company_name=company_name,
        is_fixture=1,
    )

    report = diagnose_repair(db, [ref])

    assert report["outcome"] == "ready"
    assert report["summary"]["application_changes"] == 1
    assert report["applications"][0]["fixture"]["change"] == {
        "field": "is_fixture",
        "from": True,
        "to": False,
        "action": "repair",
        "reason": "exact approved July 2026 staging ref-reuse identity",
    }


def test_missing_application_ref_and_empty_scope_fail_closed():
    db = _db()
    with pytest.raises(RepairSafetyError):
        diagnose_repair(db, [])

    report = diagnose_repair(db, ["ARF-DOES-NOT-EXIST"])
    assert report["outcome"] == "refused"
    assert report["refusals"] == [
        {
            "scope": "application",
            "code": "application_ref_not_found",
            "application_ref": "ARF-DOES-NOT-EXIST",
        }
    ]
