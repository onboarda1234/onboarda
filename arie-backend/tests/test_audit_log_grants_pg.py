"""P10-7 ops half — PostgreSQL proof of the append-only grants pack.

Codex validation 2026-07-25 item 5(b) found the maintenance role's privilege
set incomplete: under ``SET ROLE regmind_audit_maint`` the sanctioned purge
(``gdpr.purge_expired_data("audit_logs")``) needs, on the SAME connection,
the retention-policy read, the maintenance-window marker INSERT/DELETE (plus
its sequence), SELECT on audit_log (count query, DELETE's WHERE, and the
trigger guard's own lookup, all checked as the invoking role), and the
data_purge_log evidence INSERT (plus its sequence). This suite runs the
LITERAL ``scripts/apply_audit_log_append_only_grants.sql`` through psql
against a scratch database whose schema is owned by a scratch app role
(mirroring staging), then proves:

  1. the script applies cleanly in one transaction (runbook invocation);
  2. the app role keeps INSERT/SELECT and loses UPDATE/DELETE/TRUNCATE;
  3. the boot path (db._ensure_audit_log_append_only) still succeeds as the
     app role — TRIGGER retained (5(a) boot safety);
  4. the runbook's exact purge procedure — admin connection + SET ROLE —
     completes end-to-end: expired row deleted, evidence row written,
     window marker removed. This test FAILS against the pre-fix script.

Skips outside CI: needs TEST_POSTGRES_DSN (superuser) and the psql client.
Roles are cluster-wide, so names are uniqued per run and dropped in teardown.
"""

import importlib
import os
import shutil
import subprocess
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_DSN") or not shutil.which("psql"),
    reason="requires TEST_POSTGRES_DSN and the psql client (CI lint-and-test job)",
)

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "apply_audit_log_append_only_grants.sql"
)


@pytest.fixture(scope="module")
def grants_env():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    base_dsn = os.environ["TEST_POSTGRES_DSN"]
    parts = parse_dsn(base_dsn)
    host, port = parts.get("host", "localhost"), parts.get("port", "5432")
    admin_user = parts["user"]
    admin_password = parts.get("password", "")

    suffix = uuid.uuid4().hex[:8]
    app_role = f"grants_app_{suffix}"
    maint_role = f"grants_maint_{suffix}"
    dbname = f"grants_check_{suffix}"
    app_password = uuid.uuid4().hex

    root = psycopg2.connect(base_dsn)
    root.autocommit = True
    with root.cursor() as cur:
        cur.execute(f"CREATE ROLE \"{app_role}\" LOGIN PASSWORD %s", (app_password,))
        cur.execute(f'CREATE DATABASE "{dbname}" OWNER "{app_role}"')

    app_dsn = f"postgresql://{app_role}:{app_password}@{host}:{port}/{dbname}"
    admin_scratch_dsn = (
        f"postgresql://{admin_user}:{admin_password}@{host}:{port}/{dbname}"
    )

    # Build the full schema AS the app role — staging's ownership layout —
    # via the real boot path (init_db creates tables, seeds retention
    # policies, and installs the append-only triggers).
    prev_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = app_dsn
    import db as db_module
    importlib.reload(db_module)
    try:
        db_module.init_db()
        yield {
            "db_module": db_module,
            "app_role": app_role,
            "maint_role": maint_role,
            "admin_user": admin_user,
            "app_dsn": app_dsn,
            "admin_scratch_dsn": admin_scratch_dsn,
        }
    finally:
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        importlib.reload(db_module)
        with root.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
            cur.execute(f'DROP ROLE IF EXISTS "{maint_role}"')
            cur.execute(f'DROP ROLE IF EXISTS "{app_role}"')
        root.close()


@pytest.fixture(scope="module")
def applied(grants_env):
    """Run the literal grants script exactly as the runbook prescribes."""
    proc = subprocess.run(
        [
            "psql", grants_env["admin_scratch_dsn"], "-1", "-X",
            "-v", f"app_role={grants_env['app_role']}",
            "-v", f"maint_role={grants_env['maint_role']}",
            "-v", f"admin_role={grants_env['admin_user']}",
            "-f", SCRIPT,
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"grants script failed (runbook §3 invocation):\n{proc.stdout}\n{proc.stderr}"
    )
    return proc


def _wrapped(grants_env, dsn, identity):
    import psycopg2
    conn = psycopg2.connect(dsn)
    return grants_env["db_module"].DBConnection(
        conn, is_postgres=True, database_identity=identity
    )


def _close(dbw):
    # Close the RAW connection: DBConnection.close() would putconn() into the
    # module pool, which belongs to the fixture's app-DSN — these direct
    # connections were never checked out of it.
    try:
        dbw.conn.rollback()
    except Exception:
        pass
    dbw.conn.close()


def test_script_applies_and_privileges_land(grants_env, applied):
    import psycopg2
    conn = psycopg2.connect(grants_env["admin_scratch_dsn"])
    try:
        with conn.cursor() as cur:
            checks = {
                "app_update": ("has_table_privilege(%s,'audit_log','UPDATE')", grants_env["app_role"], False),
                "app_delete": ("has_table_privilege(%s,'audit_log','DELETE')", grants_env["app_role"], False),
                "app_truncate": ("has_table_privilege(%s,'audit_log','TRUNCATE')", grants_env["app_role"], False),
                "app_insert": ("has_table_privilege(%s,'audit_log','INSERT')", grants_env["app_role"], True),
                "app_select": ("has_table_privilege(%s,'audit_log','SELECT')", grants_env["app_role"], True),
                "maint_delete": ("has_table_privilege(%s,'audit_log','DELETE')", grants_env["maint_role"], True),
                "maint_select": ("has_table_privilege(%s,'audit_log','SELECT')", grants_env["maint_role"], True),
                "maint_policy": ("has_table_privilege(%s,'data_retention_policies','SELECT')", grants_env["maint_role"], True),
                "maint_win_ins": ("has_table_privilege(%s,'audit_maintenance_window','INSERT')", grants_env["maint_role"], True),
                "maint_win_del": ("has_table_privilege(%s,'audit_maintenance_window','DELETE')", grants_env["maint_role"], True),
                "maint_win_sel": ("has_table_privilege(%s,'audit_maintenance_window','SELECT')", grants_env["maint_role"], True),
                "maint_ev_ins": ("has_table_privilege(%s,'data_purge_log','INSERT')", grants_env["maint_role"], True),
                "maint_win_seq": ("has_sequence_privilege(%s,'audit_maintenance_window_id_seq','USAGE')", grants_env["maint_role"], True),
                "maint_ev_seq": ("has_sequence_privilege(%s,'data_purge_log_id_seq','USAGE')", grants_env["maint_role"], True),
            }
            for name, (expr, role, expected) in checks.items():
                cur.execute(f"SELECT {expr}", (role,))
                assert cur.fetchone()[0] is expected, f"{name}: expected {expected}"
    finally:
        conn.close()


def test_boot_path_recreates_triggers_as_app_role(grants_env, applied):
    """5(a): redeploy boot must survive the grants (TRIGGER retained)."""
    dbw = _wrapped(grants_env, grants_env["app_dsn"], "app-boot-recheck")
    try:
        grants_env["db_module"]._ensure_audit_log_append_only(dbw)
        dbw.commit()
    finally:
        _close(dbw)


def test_app_role_is_append_only(grants_env, applied):
    import psycopg2
    dbw = _wrapped(grants_env, grants_env["app_dsn"], "app-append-only")
    try:
        dbw.execute(
            "INSERT INTO audit_log (action, user_name) VALUES (?, ?)",
            ("grants-pg-test-insert", "pg-grants-test"),
        )
        dbw.commit()
        # Raw cursor: the in-process regulated-deletion guard would refuse this
        # DELETE before PostgreSQL sees it — the point here is the DB-layer
        # privilege itself (defence in depth beneath that guard).
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            with dbw.conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM audit_log WHERE action = %s", ("grants-pg-test-insert",)
                )
        dbw.rollback()
    finally:
        _close(dbw)


def test_sanctioned_purge_under_maintenance_role(grants_env, applied):
    """The 5(b) regression: the runbook's purge procedure must work verbatim.

    Fails against the pre-fix script: the window-marker INSERT (or, if that
    were granted alone, the data_purge_log evidence INSERT) raises permission
    denied under the maintenance role and the purge cannot commit.
    """
    import gdpr

    # Seed as the app role: the real retention-policy seed (the boot path runs
    # _ensure_retention_policies at server start, not init_db), one row past
    # the 3650-day audit_logs retention, one recent row that must survive.
    app = _wrapped(grants_env, grants_env["app_dsn"], "app-seed")
    try:
        grants_env["db_module"]._ensure_retention_policies(app)
        # Under pytest the boot helper seeds a standing "nonprod_test_default"
        # window marker (legacy fixture support). Staging boots ARMED — remove
        # it so this purge runs against the real armed-trigger state.
        app.execute("DELETE FROM audit_maintenance_window")
        app.execute(
            "INSERT INTO audit_log (timestamp, action, user_name) "
            "VALUES (NOW() - INTERVAL '4000 days', ?, ?)",
            ("grants-pg-test-expired", "pg-grants-test"),
        )
        app.execute(
            "INSERT INTO audit_log (action, user_name) VALUES (?, ?)",
            ("grants-pg-test-recent", "pg-grants-test"),
        )
        app.commit()
    finally:
        _close(app)

    # The runbook procedure: admin connection, SET ROLE, one connection for
    # the whole purge.
    dbw = _wrapped(grants_env, grants_env["admin_scratch_dsn"], "admin-maint")
    try:
        dbw.execute(f"SET ROLE \"{grants_env['maint_role']}\"")
        result = gdpr.purge_expired_data(
            dbw, "audit_logs", purged_by="pg-grants-test", dry_run=False
        )
        assert result.get("error") is None, result
        assert result["records_deleted"] >= 1, result
        batch_id = result["purge_batch_id"]

        survivors = dbw.execute(
            "SELECT action FROM audit_log WHERE user_name = ?", ("pg-grants-test",)
        ).fetchall()
        actions = {row["action"] for row in survivors}
        assert "grants-pg-test-expired" not in actions
        assert "grants-pg-test-recent" in actions

        evidence = dbw.execute(
            "SELECT * FROM data_purge_log WHERE purge_batch_id = ?", (batch_id,)
        ).fetchone()
        assert evidence is not None, "DCI-021: purge must not commit without evidence"
        assert evidence["data_category"] == "audit_logs"

        window = dbw.execute("SELECT COUNT(*) AS c FROM audit_maintenance_window").fetchone()
        assert window["c"] == 0, "maintenance-window marker must not survive the purge"
    finally:
        _close(dbw)
