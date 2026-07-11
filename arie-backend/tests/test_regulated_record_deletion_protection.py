from __future__ import annotations

import json
import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from db import DBConnection  # noqa: E402
from fixtures.cleanup import FixtureCleanupDenied, fixture_cleanup_context  # noqa: E402
from gdpr import purge_expired_data  # noqa: E402
from regulated_deletion import (  # noqa: E402
    FIXTURE_CLEANUP_CONFIRMATION,
    RegulatedDeleteDenied,
    assert_sql_delete_allowed,
    is_verified_disposable_postgres_test_db,
    sanctioned_delete_context,
    test_database_teardown_context,
)


def _guarded_memory_db() -> DBConnection:
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    # A non-temporary identity intentionally exercises the same deny-by-default
    # path as an unverified runtime connection.
    return DBConnection(raw, is_postgres=False, database_identity="/runtime/onboarda.db")


def test_unsafe_regulated_delete_is_denied_and_structured(caplog):
    db = _guarded_memory_db()
    db.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, detail TEXT)")
    db.execute("INSERT INTO audit_log (id, detail) VALUES (?, ?)", (1, "evidence"))
    db.commit()

    with caplog.at_level("WARNING", logger="arie.regulated_deletion"):
        with pytest.raises(RegulatedDeleteDenied):
            db.execute("DELETE FROM audit_log WHERE id=?", (1,))

    assert db.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"] == 1
    event = json.loads(caplog.records[-1].message)
    assert event["event"] == "regulated_delete_denied"
    assert event["table"] == "audit_log"
    assert "evidence" not in caplog.records[-1].message
    db.close()


def test_sanctioned_retention_context_is_table_scoped():
    db = _guarded_memory_db()
    db.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE decision_records (id TEXT PRIMARY KEY)")
    db.execute("INSERT INTO audit_log (id) VALUES (1)")
    db.execute("INSERT INTO decision_records (id) VALUES ('d1')")

    with sanctioned_delete_context(
        "retention_purge",
        actor_id="system:test",
        role="system",
        reason="approved test retention cutoff",
        allowed_tables=("audit_log",),
        confirmed=True,
    ):
        db.execute("DELETE FROM audit_log WHERE id=1")
        with pytest.raises(RegulatedDeleteDenied):
            db.execute("DELETE FROM decision_records WHERE id='d1'")

    assert db.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"] == 0
    assert db.execute("SELECT COUNT(*) AS c FROM decision_records").fetchone()["c"] == 1
    db.close()


def test_unknown_invalid_and_future_gdpr_contexts_fail_closed():
    with pytest.raises(ValueError, match="unknown sanctioned"):
        with sanctioned_delete_context(
            "not_approved",
            actor_id="x",
            role="system",
            reason="x",
            allowed_tables=("audit_log",),
            confirmed=True,
        ):
            pass

    with pytest.raises(ValueError, match="remains disabled"):
        with sanctioned_delete_context(
            "future_gdpr_erasure_dual_control",
            actor_id="legal-1",
            role="legal",
            reason="approved subject request",
            allowed_tables=("audit_log",),
            confirmed=True,
            second_approver_id="legal-2",
            feature_enabled=False,
        ):
            pass

    # The context remains technically possible for the future dual-control
    # workflow, without wiring or enabling the dormant erasure engine.
    with sanctioned_delete_context(
        "future_gdpr_erasure_dual_control",
        actor_id="legal-1",
        role="legal",
        reason="approved subject request",
        allowed_tables=("audit_log",),
        confirmed=True,
        second_approver_id="legal-2",
        feature_enabled=True,
    ):
        pass


def test_ephemeral_delete_allowed_and_multistatement_denial_has_no_partial_mutation():
    db = _guarded_memory_db()
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
    db.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)")
    db.execute("INSERT INTO sessions (id) VALUES ('s1')")
    db.execute("INSERT INTO audit_log (id) VALUES (1)")
    db.execute("DELETE FROM sessions WHERE id='s1'")
    assert db.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"] == 0

    db.execute("INSERT INTO sessions (id) VALUES ('s2')")
    with pytest.raises(RegulatedDeleteDenied):
        db.executescript("DELETE FROM sessions WHERE id='s2'; DELETE FROM audit_log WHERE id=1;")
    assert db.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"] == 1
    assert db.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"] == 1
    db.close()


def test_multi_table_truncate_cannot_hide_regulated_target_after_ephemeral_table():
    with pytest.raises(RegulatedDeleteDenied):
        assert_sql_delete_allowed("TRUNCATE TABLE sessions, public.audit_log CASCADE")
    assert_sql_delete_allowed("SELECT 'DELETE FROM audit_log' AS harmless_text -- DELETE FROM decision_records")


def _clear_deployment_markers(monkeypatch):
    for key in (
        "PRODUCTION", "IS_PRODUCTION", "STAGING", "IS_STAGING",
        "APP_ENV", "DEPLOYMENT_ENVIRONMENT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_disposable_postgres_teardown_context_allows_exact_local_test_db(monkeypatch, caplog):
    _clear_deployment_markers(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    expected = "postgresql://postgres:secret@localhost:5432/onboarda_test_ci_123?sslmode=disable"
    # Scheme alias and query parsing are safely normalized before exact match.
    active = "postgres://postgres:secret@localhost/onboarda_test_ci_123?sslmode=disable"
    monkeypatch.setenv("TEST_POSTGRES_DSN", expected)
    db = SimpleNamespace(is_postgres=True, database_identity=active)

    with caplog.at_level("WARNING", logger="arie.regulated_deletion"):
        with test_database_teardown_context(db, reason="reset audit-chain test rows"):
            assert_sql_delete_allowed(active and "DELETE FROM audit_log", database_identity=active, is_postgres=True)

    assert not any("regulated_delete_denied" in record.message for record in caplog.records)


@pytest.mark.parametrize(
    ("environment", "active_dsn", "test_dsn", "marker_key", "marker_value"),
    (
        ("staging", "postgresql://u:p@localhost/onboarda_test_ci", "postgresql://u:p@localhost/onboarda_test_ci", None, None),
        ("production", "postgresql://u:p@localhost/onboarda_test_ci", "postgresql://u:p@localhost/onboarda_test_ci", None, None),
        (None, "postgresql://u:p@localhost/onboarda_test_ci", "postgresql://u:p@localhost/onboarda_test_ci", None, None),
        ("testing", "postgresql://u:p@localhost/onboarda_test_ci_a", "postgresql://u:p@localhost/onboarda_test_ci_b", None, None),
        ("testing", "postgresql://u:p@10.0.0.8/onboarda_test_ci", "postgresql://u:p@10.0.0.8/onboarda_test_ci", None, None),
        ("testing", "postgresql://u:p@localhost/onboarda_ci", "postgresql://u:p@localhost/onboarda_ci", None, None),
        ("testing", "postgresql://u:p@db.cluster.us-east-1.rds.amazonaws.com/onboarda_test_ci", "postgresql://u:p@db.cluster.us-east-1.rds.amazonaws.com/onboarda_test_ci", None, None),
        ("testing", "postgresql://u:p@localhost/onboarda_test_production", "postgresql://u:p@localhost/onboarda_test_production", None, None),
        ("testing", "postgresql://u:p@localhost/onboarda_test_ci", "postgresql://u:p@localhost/onboarda_test_ci", "IS_PRODUCTION", "true"),
        ("testing", "postgresql://u:p@localhost/onboarda_test_ci", "postgresql://u:p@localhost/onboarda_test_ci", "STAGING", "1"),
    ),
)
def test_disposable_postgres_teardown_predicates_fail_closed(
    monkeypatch, environment, active_dsn, test_dsn, marker_key, marker_value
):
    _clear_deployment_markers(monkeypatch)
    if environment is None:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("TEST_POSTGRES_DSN", test_dsn)
    if marker_key:
        monkeypatch.setenv(marker_key, marker_value)
    assert not is_verified_disposable_postgres_test_db(active_dsn, True)
    with pytest.raises(ValueError, match="verified disposable"):
        with test_database_teardown_context(
            SimpleNamespace(is_postgres=True, database_identity=active_dsn),
            reason="attempted test reset",
        ):
            pass


def test_disposable_postgres_teardown_requires_test_dsn_context_and_postgres(monkeypatch):
    _clear_deployment_markers(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    dsn = "postgresql://u:p@127.0.0.1/onboarda_test_ci"
    monkeypatch.delenv("TEST_POSTGRES_DSN", raising=False)
    monkeypatch.setenv("DATABASE_URL", dsn)
    assert not is_verified_disposable_postgres_test_db(dsn, True)
    assert not is_verified_disposable_postgres_test_db(dsn, False, test_postgres_dsn=dsn)
    assert not is_verified_disposable_postgres_test_db(None, True, test_postgres_dsn=dsn)

    with pytest.raises(RegulatedDeleteDenied):
        assert_sql_delete_allowed("DELETE FROM audit_log", database_identity=dsn, is_postgres=True)
    with pytest.raises(ValueError, match="unknown sanctioned"):
        with sanctioned_delete_context(
            "application_delete",
            actor_id="runtime-user",
            role="client",
            reason="ordinary application delete",
            allowed_tables=("audit_log",),
            confirmed=True,
            database_identity=dsn,
            is_postgres=True,
        ):
            pass

    monkeypatch.setenv("TEST_POSTGRES_DSN", dsn)
    with pytest.raises(RegulatedDeleteDenied):
        assert_sql_delete_allowed("DELETE FROM audit_log", database_identity=dsn, is_postgres=True)
    with pytest.raises(ValueError, match="verified disposable"):
        with test_database_teardown_context(
            SimpleNamespace(is_postgres=False, database_identity=":memory:"),
            reason="PostgreSQL-only bypass attempted from SQLite",
        ):
            pass


def test_disposable_postgres_local_socket_is_allowed_but_remote_override_is_denied(monkeypatch):
    _clear_deployment_markers(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    socket_dsn = "postgresql:///onboarda_test_socket?host=%2Fvar%2Frun%2Fpostgresql"
    monkeypatch.setenv("TEST_POSTGRES_DSN", socket_dsn)
    assert is_verified_disposable_postgres_test_db(socket_dsn, True)

    remote_override = "postgresql://localhost/onboarda_test_socket?host=remote.internal"
    monkeypatch.setenv("TEST_POSTGRES_DSN", remote_override)
    assert not is_verified_disposable_postgres_test_db(remote_override, True)


def test_disposable_postgres_teardown_context_is_not_wired_into_runtime_paths():
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    for runtime_file in (
        "server.py", "base_handler.py", "db.py", "gdpr.py",
        "production_controls.py", "gdpr_erasure.py",
    ):
        source = (backend / runtime_file).read_text(encoding="utf-8")
        assert "test_database_teardown_context" not in source, runtime_file


def test_fixture_cleanup_requires_nonprod_marker_confirmation_and_isolated_test_db(monkeypatch):
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db = DBConnection(raw, is_postgres=False, database_identity=":memory:")
    db.execute("CREATE TABLE applications (id TEXT PRIMARY KEY, is_fixture INTEGER NOT NULL)")
    db.execute("INSERT INTO applications VALUES ('fixture-1', 1)")
    db.execute("INSERT INTO applications VALUES ('real-1', 0)")

    monkeypatch.setenv("ENVIRONMENT", "testing")
    with fixture_cleanup_context(
        db,
        "fixture-1",
        actor_id="system:test",
        confirmation=FIXTURE_CLEANUP_CONFIRMATION,
        reason="test fixture cleanup",
        allowed_tables=("audit_log",),
    ):
        pass

    with pytest.raises(FixtureCleanupDenied, match="not explicitly marked"):
        with fixture_cleanup_context(
            db,
            "real-1",
            actor_id="system:test",
            confirmation=FIXTURE_CLEANUP_CONFIRMATION,
            reason="test fixture cleanup",
            allowed_tables=("audit_log",),
        ):
            pass

    with pytest.raises(FixtureCleanupDenied, match="confirmation"):
        with fixture_cleanup_context(
            db,
            "fixture-1",
            actor_id="system:test",
            confirmation="wrong",
            reason="test fixture cleanup",
            allowed_tables=("audit_log",),
        ):
            pass

    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(FixtureCleanupDenied, match="testing or staging"):
        with fixture_cleanup_context(
            db,
            "fixture-1",
            actor_id="system:test",
            confirmation=FIXTURE_CLEANUP_CONFIRMATION,
            reason="test fixture cleanup",
            allowed_tables=("audit_log",),
        ):
            pass
    db.close()


def test_retention_engine_purges_only_eligible_row_and_writes_evidence():
    db = _guarded_memory_db()
    db.executescript(
        """
        CREATE TABLE data_retention_policies (
            id TEXT PRIMARY KEY, data_category TEXT, retention_days INTEGER,
            auto_purge INTEGER, requires_review INTEGER
        );
        CREATE TABLE audit_log (id INTEGER PRIMARY KEY, timestamp TEXT, detail TEXT);
        CREATE TABLE data_purge_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_category TEXT, record_count INTEGER, oldest_record_date TEXT,
            newest_record_date TEXT, retention_policy_id TEXT, purge_reason TEXT,
            purged_by TEXT, subject_id TEXT, application_id TEXT,
            tables_affected TEXT, per_table_counts TEXT, purge_batch_id TEXT,
            evidence_json TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO data_retention_policies VALUES (?,?,?,?,?)",
        ("policy-audit", "audit_logs", 30, 0, 1),
    )
    db.execute("INSERT INTO audit_log VALUES (?,?,?)", (1, "2020-01-01T00:00:00", "expired"))
    db.execute("INSERT INTO audit_log VALUES (?,?,?)", (2, "2999-01-01T00:00:00", "current"))
    db.commit()

    with pytest.raises(RegulatedDeleteDenied):
        db.execute("DELETE FROM audit_log WHERE id=1")

    result = purge_expired_data(db, "audit_logs", purged_by="admin-test", dry_run=False)
    assert result["records_deleted"] == 1
    assert db.execute("SELECT id FROM audit_log ORDER BY id").fetchall() == [{"id": 2}]
    log_row = db.execute("SELECT * FROM data_purge_log").fetchone()
    assert log_row["record_count"] == 1
    evidence = json.loads(log_row["evidence_json"])
    assert evidence["sanctioned_context"] == "retention_purge"
    assert evidence["deleted_rowcount"] == 1
    db.close()


def test_legacy_company_cleanup_execute_mode_is_refused_without_mutation(monkeypatch):
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_named_application.py"
    spec = importlib.util.spec_from_file_location("cleanup_named_application_p12_1", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    class FakeDB:
        closed = False

        def execute(self, sql, params=()):
            assert sql == "SELECT * FROM applications"
            return self

        def fetchall(self):
            return [{
                "id": "app-1",
                "ref": "ARF-1",
                "company_name": "1947 OIL & GAS PLC",
                "status": "draft",
                "created_at": "2020-01-01",
            }]

        def close(self):
            self.closed = True

    fake = FakeDB()
    monkeypatch.setattr(module, "_get_db", lambda: fake)
    assert module.run_cleanup("1947 OIL & GAS PLC", execute=True) is False
    assert fake.closed is True
