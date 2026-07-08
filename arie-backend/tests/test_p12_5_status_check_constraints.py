"""P12-5 (audit DCI-006) — enum CHECK constraints for workflow status columns.

Several status/source columns were raw text with no CHECK constraint, so
bugs, scripts, or admin SQL could insert invalid workflow states.  Fresh
schemas now carry inline CHECKs (both engines); long-lived PostgreSQL
databases are repaired by the v2.47 startup helper
(db._ensure_status_enum_constraints): NULL/blank backfill, off-canon
detection (rows are never rewritten — constraint skipped with a loud ERROR
instead), then constraint installation via
_replace_postgres_column_check_constraint.

Also covered here: the Severity.WARNING enum fix — six supervisor audit
paths (ai_override, escalation_created, override human reviews,
schema_validation_failed, contradiction/rule fallbacks) referenced
Severity.WARNING, which did not exist, so every one of them raised
AttributeError BEFORE its INSERT and silently lost override/escalation
audit evidence.  'warning' is therefore part of the severity canon.

PostgreSQL-gated tests run when TEST_POSTGRES_DSN / DATABASE_URL_TEST is set.
"""

import os
import re
import sqlite3
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _canon_specs():
    import db as db_module
    return db_module.STATUS_ENUM_CONSTRAINT_SPECS


# ---------------------------------------------------------------------------
# Lockstep: canon constants ↔ DDL CHECK clauses ↔ supervisor enums
# ---------------------------------------------------------------------------

class TestCanonLockstep:

    def _ddl_check_values(self, src, table, column):
        """Extract the CHECK(column IN (...)) value set for a column from a
        CREATE TABLE block in db.py source. Returns a list of sets (one per
        schema occurrence)."""
        results = []
        for m in re.finditer(
            rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n    \);", src, re.S
        ):
            block = m.group(1)
            cm = re.search(
                rf"{column}[^,]*?CHECK\({column} IN \((.*?)\)\)", block, re.S
            )
            if cm:
                values = set(re.findall(r"'([^']*)'", cm.group(1)))
                # Drop the column DEFAULT literal if the lazy match caught it
                results.append(values)
        return results

    def test_every_spec_column_has_matching_ddl_checks_in_both_schemas(self):
        with open(os.path.join(BACKEND, "db.py"), encoding="utf-8") as fh:
            src = fh.read()
        for table, column, allowed, _fill, _nn in _canon_specs():
            occurrences = self._ddl_check_values(src, table, column)
            assert len(occurrences) >= 2, (
                f"{table}.{column}: expected CHECK in BOTH main schemas "
                f"(PG + SQLite), found {len(occurrences)}"
            )
            for values in occurrences:
                assert values == set(allowed), (
                    f"{table}.{column}: DDL CHECK {sorted(values)} diverged "
                    f"from canon constant {sorted(allowed)} — update both "
                    f"together"
                )

    def test_supervisor_event_type_canon_matches_enum(self):
        import db as db_module
        from supervisor.schemas import AuditEventType
        assert set(db_module.SUPERVISOR_AUDIT_EVENT_TYPE_VALUES) == {
            e.value for e in AuditEventType
        }

    def test_supervisor_severity_canon_matches_enum(self):
        import db as db_module
        from supervisor.schemas import Severity
        assert set(db_module.SUPERVISOR_AUDIT_SEVERITY_VALUES) == {
            s.value for s in Severity
        }

    def test_pipeline_status_canon_matches_writer_literals(self):
        """The single writer funnel lives in supervisor/supervisor.py — every
        literal assigned to result.status must be in the canon."""
        import db as db_module
        with open(os.path.join(BACKEND, "supervisor", "supervisor.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        assigned = set(re.findall(r'\.status\s*=\s*"([a-z_]+)"', src))
        assert assigned, "expected status literals in supervisor.py"
        missing = assigned - set(db_module.SUPERVISOR_PIPELINE_STATUS_VALUES)
        assert not missing, (
            f"supervisor.py assigns pipeline status(es) {sorted(missing)} "
            f"outside the canon — widen SUPERVISOR_PIPELINE_STATUS_VALUES"
        )


# ---------------------------------------------------------------------------
# Fresh-install enforcement (SQLite DDL CHECKs)
# ---------------------------------------------------------------------------

class TestFreshSqliteEnforcement:

    def _sqlite(self, temp_db):
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        return conn

    def test_clients_status_rejects_off_canon(self, temp_db):
        conn = self._sqlite(temp_db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO clients (id, email, password_hash, status) "
                    "VALUES ('p125-c1', 'p125c1@test.com', 'h', 'suspended')"
                )
            conn.execute(
                "INSERT INTO clients (id, email, password_hash, status) "
                "VALUES ('p125-c2', 'p125c2@test.com', 'h', 'inactive')"
            )
            # NOT NULL now enforced (mirrors users.status)
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO clients (id, email, password_hash, status) "
                    "VALUES ('p125-c3', 'p125c3@test.com', 'h', NULL)"
                )
            conn.rollback()
        finally:
            conn.close()

    def test_agent_executions_status_and_source_reject_off_canon(self, temp_db):
        conn = self._sqlite(temp_db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO agent_executions (application_id, agent_name, status) "
                    "VALUES ('a', 'agent1', 'exploded')"
                )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO agent_executions (application_id, agent_name, status, source) "
                    "VALUES ('a', 'agent1', 'verified', 'claude')"
                )
            conn.execute(
                "INSERT INTO agent_executions (application_id, agent_name, status, source) "
                "VALUES ('a', 'agent1', 'verified', 'stored_screening_results')"
            )
            conn.execute(
                "INSERT INTO agent_executions (application_id, agent_name, status) "
                "VALUES ('a', 'agent1', 'error')"  # legacy value stays legal
            )
            conn.rollback()
        finally:
            conn.close()

    def test_supervisor_pipeline_status_rejects_off_canon(self, temp_db):
        conn = self._sqlite(temp_db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO supervisor_pipeline_results "
                    "(id, pipeline_id, application_id, status) "
                    "VALUES ('p1', 'p1', 'a', 'in_flight')"
                )
            conn.execute(
                "INSERT INTO supervisor_pipeline_results "
                "(id, pipeline_id, application_id, status) "
                "VALUES ('p2', 'p2', 'a', 'awaiting_review')"
            )
            conn.rollback()
        finally:
            conn.close()

    def test_supervisor_audit_log_event_type_and_severity(self, temp_db):
        conn = self._sqlite(temp_db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO supervisor_audit_log (id, timestamp, event_type, action) "
                    "VALUES ('s1', '2026-01-01T00:00:00Z', 'made_up_event', 'x')"
                )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO supervisor_audit_log "
                    "(id, timestamp, event_type, severity, action) "
                    "VALUES ('s2', '2026-01-01T00:00:00Z', 'ai_override', 'debug', 'x')"
                )
            conn.execute(
                "INSERT INTO supervisor_audit_log "
                "(id, timestamp, event_type, severity, action) "
                "VALUES ('s3', '2026-01-01T00:00:00Z', 'ai_override', 'warning', 'x')"
            )
            conn.rollback()
        finally:
            conn.close()

    def test_compliance_memos_statuses_reject_off_canon(self, temp_db):
        conn = self._sqlite(temp_db)
        try:
            conn.execute(
                "INSERT INTO applications (id, ref, company_name) VALUES ('p125-app', 'P125-REF', 'C')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO compliance_memos (application_id, memo_data, supervisor_status) "
                    "VALUES ('p125-app', '{}', 'consistent')"  # wrong case
                )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO compliance_memos (application_id, memo_data, rule_engine_status) "
                    "VALUES ('p125-app', '{}', 'CLEAN')"  # memo-JSON vocabulary, not the column canon
                )
            conn.execute(
                "INSERT INTO compliance_memos (application_id, memo_data, supervisor_status, rule_engine_status) "
                "VALUES ('p125-app', '{}', 'CONSISTENT_WITH_WARNINGS', 'pass')"
            )
            conn.rollback()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# v2.47 repair helper semantics (engine-neutral parts, run on SQLite)
# ---------------------------------------------------------------------------

class TestV247RepairHelper:

    def test_null_backfill_fills_defaults(self, temp_db):
        """NULL/blank values in backfillable columns are normalised to the
        column's semantic default (on both engines)."""
        from db import get_db, _ensure_status_enum_constraints

        db = get_db()
        try:
            db.execute(
                "INSERT INTO applications (id, ref, company_name) VALUES ('p125-bf', 'P125-BF', 'C')"
            )
            # CHECK constraints pass NULL by SQL semantics — seed NULLs.
            db.execute(
                "INSERT INTO compliance_memos (application_id, memo_data, supervisor_status, rule_engine_status) "
                "VALUES ('p125-bf', '{}', NULL, NULL)"
            )
            db.commit()
            _ensure_status_enum_constraints(db)
            row = dict(db.execute(
                "SELECT supervisor_status, rule_engine_status FROM compliance_memos "
                "WHERE application_id = 'p125-bf'"
            ).fetchone())
            assert row["supervisor_status"] == "pending"
            assert row["rule_engine_status"] == "pending"
            db.execute("DELETE FROM compliance_memos WHERE application_id = 'p125-bf'")
            db.execute("DELETE FROM applications WHERE id = 'p125-bf'")
            db.commit()
        finally:
            db.close()

    def test_helper_is_idempotent_across_boots(self, temp_db):
        from db import get_db, _ensure_status_enum_constraints
        db = get_db()
        try:
            _ensure_status_enum_constraints(db)
            _ensure_status_enum_constraints(db)  # second boot — no error
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Severity.WARNING regression (the six previously-crashing audit paths)
# ---------------------------------------------------------------------------

class TestSeverityWarningFix:

    def test_warning_member_exists(self):
        from supervisor.schemas import Severity
        assert Severity.WARNING.value == "warning"

    def test_failed_validation_audit_persists_with_warning(self, temp_db):
        """schema_validation_failed previously raised AttributeError before
        its INSERT — override/validation-failure evidence was silently lost."""
        from supervisor.audit import AuditLogger

        logger_ = AuditLogger(db_path=temp_db)
        entry = logger_.log_validation(
            run_id="p125-run", agent_type="document_verification",
            application_id="p125-app-w", is_valid=False, errors=["bad schema"],
        )
        assert entry.severity.value == "warning"
        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute(
                "SELECT severity, event_type FROM supervisor_audit_log WHERE run_id = 'p125-run'"
            ).fetchone()
            assert row is not None, "audit row must persist (previously lost)"
            assert row[0] == "warning"
            assert row[1] == "schema_validation_failed"
        finally:
            conn.close()

    def test_ai_override_audit_persists_with_warning(self, temp_db):
        from supervisor.audit import AuditLogger

        logger_ = AuditLogger(db_path=temp_db)
        entry = logger_.log_override(
            override_id="p125-ovr", application_id="p125-app-o",
            officer_name="Officer", officer_role="sco",
            override_type="decision", original_value="reject",
            override_value="approve", reason="test",
        )
        assert entry.severity.value == "warning"
        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute(
                "SELECT severity FROM supervisor_audit_log WHERE event_type = 'ai_override'"
            ).fetchone()
            assert row is not None and row[0] == "warning"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# PostgreSQL semantics (DSN-gated): constraint install, off-canon skip
# ---------------------------------------------------------------------------

def _pg_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


@pytest.fixture()
def pg_db():
    dsn = _pg_dsn()
    if not dsn:
        pytest.skip("No PostgreSQL DSN (TEST_POSTGRES_DSN / DATABASE_URL_TEST) available")
    import psycopg2
    from db import DBConnection
    conn = psycopg2.connect(dsn)
    db = DBConnection(conn, is_postgres=True)
    yield db
    try:
        db.rollback()
    except Exception:
        pass
    db.close()


class TestPostgresConstraintRepair:

    def _fresh_table(self, db, name):
        db.execute(f"DROP TABLE IF EXISTS {name}")
        db.execute(
            f"CREATE TABLE {name} (id TEXT PRIMARY KEY, status TEXT DEFAULT 'active')"
        )
        db.commit()

    def test_replace_helper_installs_and_enforces(self, pg_db):
        """_replace_postgres_column_check_constraint on a legacy-shaped table:
        constraint installs, off-canon INSERT rejected, stale constraint on
        the same column is replaced."""
        from db import _replace_postgres_column_check_constraint
        table = f"p125_probe_{uuid.uuid4().hex[:8]}"
        self._fresh_table(pg_db, table)
        try:
            # Simulate a stale historical CHECK with the WRONG canon.
            pg_db.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {table}_status_check "
                f"CHECK (status IN ('critical','error','warning','info','debug'))"
            )
            pg_db.commit()
            dropped = _replace_postgres_column_check_constraint(
                pg_db, table=table, column="status",
                constraint_name=f"{table}_status_check",
                allowed_values=("active", "inactive"),
            )
            pg_db.commit()
            assert f"{table}_status_check" in dropped, "stale CHECK must be replaced"
            pg_db.execute(
                f"INSERT INTO {table} (id, status) VALUES ('ok', 'inactive')"
            )
            pg_db.commit()
            with pytest.raises(Exception):
                pg_db.execute(
                    f"INSERT INTO {table} (id, status) VALUES ('bad', 'debug')"
                )
            pg_db.rollback()
        finally:
            pg_db.execute(f"DROP TABLE IF EXISTS {table}")
            pg_db.commit()

    def test_off_canon_rows_skip_constraint_and_are_preserved(self, pg_db, monkeypatch):
        """A legacy table holding off-canon values: the repair must NOT
        rewrite the rows and must NOT install the constraint (loud skip)."""
        import db as db_module
        table = f"p125_skip_{uuid.uuid4().hex[:8]}"
        self._fresh_table(pg_db, table)
        try:
            pg_db.execute(
                f"INSERT INTO {table} (id, status) VALUES ('legacy', 'suspended')"
            )
            pg_db.commit()
            monkeypatch.setattr(
                db_module, "STATUS_ENUM_CONSTRAINT_SPECS",
                ((table, "status", ("active", "inactive"), None, False),),
            )
            db_module._ensure_status_enum_constraints(pg_db)
            row = dict(pg_db.execute(
                f"SELECT status FROM {table} WHERE id = 'legacy'"
            ).fetchone())
            assert row["status"] == "suspended", "off-canon rows must be preserved"
            # No constraint installed → off-canon INSERT still possible.
            pg_db.execute(
                f"INSERT INTO {table} (id, status) VALUES ('legacy2', 'suspended')"
            )
            pg_db.commit()
        finally:
            pg_db.execute(f"DROP TABLE IF EXISTS {table}")
            pg_db.commit()

    def test_steady_state_boot_is_a_no_op(self, pg_db, monkeypatch):
        """Adversarial review M2: once the canonical constraint is installed,
        subsequent boots must NOT drop+recreate it (ACCESS EXCLUSIVE lock +
        full validation scan on unbounded tables). The constraint's oid must
        survive a second run unchanged."""
        import db as db_module
        table = f"p125_ss_{uuid.uuid4().hex[:8]}"
        self._fresh_table(pg_db, table)
        try:
            monkeypatch.setattr(
                db_module, "STATUS_ENUM_CONSTRAINT_SPECS",
                ((table, "status", ("active", "inactive"), "inactive", True),),
            )
            db_module._ensure_status_enum_constraints(pg_db)
            oid_before = dict(pg_db.execute(
                "SELECT oid FROM pg_constraint WHERE conname = ?",
                (f"{table}_status_check",),
            ).fetchone())["oid"]
            db_module._ensure_status_enum_constraints(pg_db)
            row = pg_db.execute(
                "SELECT oid FROM pg_constraint WHERE conname = ?",
                (f"{table}_status_check",),
            ).fetchone()
            assert row is not None, "constraint must still exist after second run"
            assert dict(row)["oid"] == oid_before, (
                "steady-state boot must not DROP+ADD the constraint"
            )
        finally:
            pg_db.execute(f"DROP TABLE IF EXISTS {table}")
            pg_db.commit()

    def test_clients_null_backfill_is_fail_closed(self, pg_db, monkeypatch):
        """Adversarial review M1: a NULL-status client cannot log in today
        (login filters status='active'); the backfill must NOT silently
        re-enable access — NULL goes to 'inactive', not 'active'."""
        import db as db_module
        table = f"p125_fc_{uuid.uuid4().hex[:8]}"
        self._fresh_table(pg_db, table)
        try:
            pg_db.execute(f"INSERT INTO {table} (id, status) VALUES ('anom', NULL)")
            pg_db.commit()
            # Use the REAL clients spec shape: null_fill comes from the
            # module constant tuple — assert the module-level policy first.
            clients_spec = next(
                s for s in db_module.STATUS_ENUM_CONSTRAINT_SPECS if s[0] == "clients"
            )
            assert clients_spec[3] == "inactive", (
                "clients.status NULL backfill must be fail-closed ('inactive')"
            )
            monkeypatch.setattr(
                db_module, "STATUS_ENUM_CONSTRAINT_SPECS",
                ((table, "status", ("active", "inactive"), clients_spec[3], True),),
            )
            db_module._ensure_status_enum_constraints(pg_db)
            row = dict(pg_db.execute(
                f"SELECT status FROM {table} WHERE id = 'anom'"
            ).fetchone())
            assert row["status"] == "inactive"
        finally:
            pg_db.execute(f"DROP TABLE IF EXISTS {table}")
            pg_db.commit()

    def test_severity_is_not_backfilled_on_the_hash_chained_table(self):
        """Adversarial review m4: severity participates in the supervisor
        audit chain hash — the repair must never rewrite it."""
        import db as db_module
        spec = next(
            s for s in db_module.STATUS_ENUM_CONSTRAINT_SPECS
            if s[0] == "supervisor_audit_log" and s[1] == "severity"
        )
        assert spec[3] is None, (
            "supervisor_audit_log.severity must not be backfilled (hash-chained)"
        )

    def test_clean_table_gains_constraint_and_null_backfill(self, pg_db, monkeypatch):
        import db as db_module
        table = f"p125_ok_{uuid.uuid4().hex[:8]}"
        self._fresh_table(pg_db, table)
        try:
            pg_db.execute(f"INSERT INTO {table} (id, status) VALUES ('n', NULL)")
            pg_db.commit()
            monkeypatch.setattr(
                db_module, "STATUS_ENUM_CONSTRAINT_SPECS",
                ((table, "status", ("active", "inactive"), "active", True),),
            )
            db_module._ensure_status_enum_constraints(pg_db)
            row = dict(pg_db.execute(
                f"SELECT status FROM {table} WHERE id = 'n'"
            ).fetchone())
            assert row["status"] == "active", "NULL must be backfilled"
            with pytest.raises(Exception):
                pg_db.execute(
                    f"INSERT INTO {table} (id, status) VALUES ('bad', 'zombie')"
                )
            pg_db.rollback()
            # NOT NULL applied
            with pytest.raises(Exception):
                pg_db.execute(f"INSERT INTO {table} (id, status) VALUES ('n2', NULL)")
            pg_db.rollback()
        finally:
            pg_db.execute(f"DROP TABLE IF EXISTS {table}")
            pg_db.commit()
