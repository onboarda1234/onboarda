"""M3.1 DOCUMENT-HEALTH-SCHEDULER-STAGED-ROLLOUT tests.

Covers: dry-run writes nothing, strict cap with counted remainder, segment
and fixtures-only scoping, idempotent re-run, off-by-default scheduler
config, status-row reporting, source purity (no providers/emails/Agent 1),
and the API endpoint contract (dry-run default, role gating).
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── DB fixture (mirrors test_document_health_monitor.py) ────────────────────

@pytest.fixture
def sched_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr("config.DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr("db.DB_PATH", str(tmp_path / "test.db"))
    import db as db_module

    db_module.init_db()
    conn = db_module.get_db()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT UNIQUE NOT NULL, "
        "filename TEXT NOT NULL, description TEXT DEFAULT '', "
        "applied_at TEXT DEFAULT (datetime('now')), checksum TEXT)"
    )
    for v, fn in [
        ("001", "migration_001_initial.sql"),
        ("002", "migration_002_supervisor_tables.sql"),
        ("003", "migration_003_monitoring_indexes.sql"),
        ("004", "migration_004_documents_s3_key.sql"),
        ("005", "migration_005_applications_truth_schema.sql"),
        ("006", "migration_006_person_dob.sql"),
        ("007", "migration_007_screening_reports_normalized.sql"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, filename) VALUES (?, ?)",
            (v, fn),
        )
    conn.commit()
    from migrations.runner import run_all_migrations_with_connection

    run_all_migrations_with_connection(conn)
    yield conn
    conn.close()


def _seed_app(conn, app_id, *, status="approved", is_fixture=0):
    conn.execute(
        "INSERT OR REPLACE INTO applications (id, ref, company_name, risk_level, status, is_fixture) "
        "VALUES (?, ?, ?, 'MEDIUM', ?, ?)",
        (app_id, f"REF-{app_id}", f"{app_id} Ltd", status, is_fixture),
    )
    conn.commit()


def _seed_expired_doc(conn, app_id, doc_id, doc_type="passport"):
    conn.execute(
        """
        INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path, uploaded_at,
             expiry_date, is_current)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            doc_id,
            app_id,
            doc_type,
            f"{doc_type}.pdf",
            f"/tmp/{doc_type}.pdf",
            datetime.now(timezone.utc).isoformat(),
            (datetime.now(timezone.utc) - timedelta(days=5)).date().isoformat(),
        ),
    )
    conn.commit()


def _alert_count(conn):
    return conn.execute("SELECT COUNT(*) AS c FROM monitoring_alerts").fetchone()["c"]


def _audit_count(conn):
    return conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]


# ── Dry run ──────────────────────────────────────────────────────────────────

def test_dry_run_reports_counts_and_writes_nothing(sched_db):
    import document_health_scheduler as dhs

    _seed_app(sched_db, "app-dry-1")
    _seed_expired_doc(sched_db, "app-dry-1", "doc-dry-1")
    _seed_expired_doc(sched_db, "app-dry-1", "doc-dry-2", doc_type="licence")

    alerts_before, audits_before = _alert_count(sched_db), _audit_count(sched_db)
    summary = dhs.run_document_health_sweep(sched_db, dry_run=True)

    assert summary["dry_run"] is True
    assert summary["created"] == 2
    assert summary["by_type"] == {"document_expired": 2}
    assert summary["by_severity"] == {"high": 1, "critical": 1}
    assert _alert_count(sched_db) == alerts_before
    assert _audit_count(sched_db) == audits_before  # zero writes, zero audit


def test_dry_run_is_the_default(sched_db):
    import document_health_scheduler as dhs

    _seed_app(sched_db, "app-default-dry")
    _seed_expired_doc(sched_db, "app-default-dry", "doc-default-dry")
    before = _alert_count(sched_db)
    summary = dhs.run_document_health_sweep(sched_db)
    assert summary["dry_run"] is True
    assert _alert_count(sched_db) == before


# ── Live sweep: create, idempotency, status row ──────────────────────────────

def test_live_sweep_creates_then_second_run_is_idempotent(sched_db):
    import document_health_scheduler as dhs

    _seed_app(sched_db, "app-live-1")
    _seed_expired_doc(sched_db, "app-live-1", "doc-live-1")

    first = dhs.run_document_health_sweep(sched_db, dry_run=False)
    assert first["created"] == 1
    assert first["failed"] == 0

    second = dhs.run_document_health_sweep(sched_db, dry_run=False)
    assert second["created"] == 0  # dedup key (document_id, alert_type)

    row = sched_db.execute(
        "SELECT * FROM monitoring_agent_status WHERE agent_type = 'document_health_scheduler'"
    ).fetchone()
    assert row is not None
    assert row["last_run"]
    assert row["alerts_generated"] == 1
    # Scheduler flag is off in tests → status row reports disabled, no next_run.
    assert row["status"] == "disabled"
    assert row["next_run"] in (None, "")

    audit_actions = [
        r["action"] for r in sched_db.execute(
            "SELECT action FROM audit_log WHERE target = 'document_health_scheduler'"
        ).fetchall()
    ]
    assert "monitoring.document_health.run_started" in audit_actions
    assert "monitoring.document_health.run_completed" in audit_actions


# ── Cap ──────────────────────────────────────────────────────────────────────

def test_cap_is_strict_and_remainder_is_counted(sched_db):
    import document_health_scheduler as dhs

    for i in range(3):
        app = f"app-cap-{i}"
        _seed_app(sched_db, app)
        _seed_expired_doc(sched_db, app, f"doc-cap-{i}-a")
        _seed_expired_doc(sched_db, app, f"doc-cap-{i}-b", doc_type="national_id")

    summary = dhs.run_document_health_sweep(sched_db, dry_run=False, max_alerts=3)

    assert summary["created"] <= 3  # never exceeds the cap
    assert summary["created"] == 2  # first app fits (2), second would breach (2+2>3)
    assert summary["capped"] is True
    assert summary["applications_skipped_by_cap"] == 2
    assert summary["remainder_would_create"] == 4  # counted, not silent
    assert _alert_count(sched_db) == 2


# ── Scope: segment + fixtures ────────────────────────────────────────────────

def test_segment_limits_sweep_to_named_applications(sched_db):
    import document_health_scheduler as dhs

    for app in ("app-seg-in", "app-seg-out"):
        _seed_app(sched_db, app)
        _seed_expired_doc(sched_db, app, f"doc-{app}")

    summary = dhs.run_document_health_sweep(
        sched_db, dry_run=False, segment=["app-seg-in"],
    )
    assert summary["created"] == 1
    rows = sched_db.execute(
        "SELECT application_id FROM monitoring_alerts"
    ).fetchall()
    assert {r["application_id"] for r in rows} == {"app-seg-in"}


def test_default_scope_excludes_fixtures_and_non_approved(sched_db):
    import document_health_scheduler as dhs

    _seed_app(sched_db, "app-scope-ok")
    _seed_app(sched_db, "app-scope-fixture", is_fixture=1)
    _seed_app(sched_db, "app-scope-draft", status="draft")
    # NULL is_fixture must be treated as non-fixture (the case the removed
    # COALESCE guarded). `is_fixture IS NOT TRUE` keeps this row in scope.
    sched_db.execute(
        "INSERT OR REPLACE INTO applications (id, ref, company_name, risk_level, status, is_fixture) "
        "VALUES (?, ?, ?, 'MEDIUM', 'approved', NULL)",
        ("app-scope-nullfix", "REF-app-scope-nullfix", "app-scope-nullfix Ltd"),
    )
    sched_db.commit()
    for app in ("app-scope-ok", "app-scope-fixture", "app-scope-draft", "app-scope-nullfix"):
        _seed_expired_doc(sched_db, app, f"doc-{app}")

    ids = dhs.sweep_candidate_application_ids(sched_db)
    assert "app-scope-ok" in ids
    assert "app-scope-nullfix" in ids
    assert "app-scope-fixture" not in ids
    assert "app-scope-draft" not in ids

    fixture_ids = dhs.sweep_candidate_application_ids(sched_db, fixtures_only=True)
    assert fixture_ids == ["app-scope-fixture"]

    summary = dhs.run_document_health_sweep(sched_db, dry_run=False, fixtures_only=True)
    assert summary["created"] == 1
    rows = sched_db.execute("SELECT application_id FROM monitoring_alerts").fetchall()
    assert {r["application_id"] for r in rows} == {"app-scope-fixture"}


def test_sweep_scope_sql_is_dialect_neutral_bool():
    """Sweep scope must not use COALESCE(is_fixture, 0): `is_fixture` is BOOLEAN on
    PostgreSQL, and COALESCE(boolean, integer) raises DatatypeMismatch (the Phase A
    dry-run 500). Guards the dialect-neutral `IS TRUE`/`IS NOT TRUE` form. This is a
    source-level regression that SQLite-only functional tests cannot catch, since
    SQLite silently evaluates the boolean/integer mix."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "document_health_scheduler.py"
    text = src.read_text(encoding="utf-8")
    assert "COALESCE(is_fixture, 0)" not in text, (
        "Sweep scope must use a dialect-neutral fixture filter; "
        "COALESCE(boolean, 0) raises DatatypeMismatch on PostgreSQL."
    )
    assert "IN (1, TRUE)" not in text, (
        "Mixed integer/boolean IN-list (1, TRUE) is not PostgreSQL-safe."
    )
    assert "is_fixture IS TRUE" in text and "is_fixture IS NOT TRUE" in text, (
        "Sweep scope should filter fixtures with `is_fixture IS TRUE` / "
        "`is_fixture IS NOT TRUE` (valid on PG BOOLEAN and SQLite INTEGER)."
    )


# ── Config gating ────────────────────────────────────────────────────────────

def test_scheduler_is_off_by_default_everywhere(monkeypatch):
    import document_health_scheduler as dhs

    monkeypatch.delenv("DOCUMENT_HEALTH_SCHEDULER_ENABLED", raising=False)
    for env in ("development", "testing", "staging", "production"):
        monkeypatch.setenv("ENVIRONMENT", env)
        assert dhs.scheduler_enabled() is False, env
    monkeypatch.setenv("DOCUMENT_HEALTH_SCHEDULER_ENABLED", "true")
    assert dhs.scheduler_enabled() is True
    monkeypatch.setenv("DOCUMENT_HEALTH_SCHEDULER_ENABLED", "false")
    assert dhs.scheduler_enabled() is False


def test_interval_and_cap_env_parsing_with_clamps(monkeypatch):
    import document_health_scheduler as dhs

    monkeypatch.delenv("DOCUMENT_HEALTH_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_HEALTH_MAX_ALERTS_PER_RUN", raising=False)
    assert dhs.scheduler_interval_seconds() == 21600
    assert dhs.max_alerts_per_run() == 50

    monkeypatch.setenv("DOCUMENT_HEALTH_INTERVAL_SECONDS", "10")
    assert dhs.scheduler_interval_seconds() == 300  # min clamp
    monkeypatch.setenv("DOCUMENT_HEALTH_INTERVAL_SECONDS", "garbage")
    assert dhs.scheduler_interval_seconds() == 21600

    monkeypatch.setenv("DOCUMENT_HEALTH_MAX_ALERTS_PER_RUN", "0")
    assert dhs.max_alerts_per_run() == 1
    monkeypatch.setenv("DOCUMENT_HEALTH_MAX_ALERTS_PER_RUN", "99999")
    assert dhs.max_alerts_per_run() == 500

    monkeypatch.setenv("DOCUMENT_HEALTH_SEGMENT", " app-1 , app-2 ")
    assert dhs.configured_segment() == ["app-1", "app-2"]
    monkeypatch.setenv("DOCUMENT_HEALTH_SEGMENT", "")
    assert dhs.configured_segment() is None


# ── Purity ───────────────────────────────────────────────────────────────────

def test_scheduler_source_has_no_provider_email_or_agent_references():
    src = open(os.path.join(BACKEND_DIR, "document_health_scheduler.py")).read().lower()
    for banned in (
        "send_portal_email", "smtp", "email_sender",
        "sumsub", "complyadvantage", "opencorporates",
        "claude_client", "anthropic",
        "client_notification",
        "/verify", "verification_jobs",
    ):
        assert banned not in src, banned


def test_plan_is_pure_readonly(sched_db):
    from document_health_monitor import compute_document_health_plan

    _seed_app(sched_db, "app-plan-1")
    _seed_expired_doc(sched_db, "app-plan-1", "doc-plan-1")
    alerts_before, audits_before = _alert_count(sched_db), _audit_count(sched_db)

    plan = compute_document_health_plan(sched_db, "app-plan-1")
    assert len(plan["would_create"]) == 1
    assert plan["would_create"][0]["alert_type"] == "document_expired"
    assert _alert_count(sched_db) == alerts_before
    assert _audit_count(sched_db) == audits_before


# ── API endpoint contract (live tornado, isolated sqlite) ────────────────────

import socket
import tempfile
import threading
import time

import requests
import tornado.httpserver
import tornado.ioloop


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _patch_attr(module, name, value, restore):
    sentinel = object()
    old_value = getattr(module, name, sentinel)
    restore.append((module, name, old_value, sentinel))
    setattr(module, name, value)


def _restore_attrs(restore):
    for module, name, old_value, sentinel in reversed(restore):
        if old_value is sentinel:
            try:
                delattr(module, name)
            except AttributeError:
                pass
        else:
            setattr(module, name, old_value)


@pytest.fixture(scope="module")
def sweep_api_server():
    db_path = os.path.join(
        tempfile.gettempdir(),
        f"onboarda_dhs_api_{os.getpid()}_{time.time_ns()}.db",
    )
    restore = []
    thread = None
    server_ref = {}
    previous_env = {
        "DB_PATH": os.environ.get("DB_PATH"),
        "DATABASE_URL": os.environ.get("DATABASE_URL"),
    }
    os.environ["DB_PATH"] = db_path
    os.environ["DATABASE_URL"] = ""

    import config as config_module
    import db as db_module

    _patch_attr(config_module, "DATABASE_URL", "", restore)
    _patch_attr(config_module, "DB_PATH", db_path, restore)
    _patch_attr(config_module, "ENVIRONMENT", "testing", restore)
    _patch_attr(db_module, "DATABASE_URL", "", restore)
    _patch_attr(db_module, "DB_PATH", db_path, restore)
    _patch_attr(db_module, "USE_POSTGRESQL", False, restore)
    _patch_attr(db_module, "_CFG_ENVIRONMENT", "testing", restore)

    db_module.init_db()
    conn = db_module.get_db()
    for user_id, email, name, role in [
        ("admin_dhs", "admin-dhs@example.test", "Admin DHS", "admin"),
        ("co_dhs", "co-dhs@example.test", "CO DHS", "co"),
    ]:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (user_id, email, "unused", name, role),
        )
    conn.execute(
        "INSERT OR REPLACE INTO applications (id, ref, company_name, status, is_fixture) VALUES (?, ?, ?, 'approved', 0)",
        ("app-api-dhs", "API-DHS", "API DHS Ltd"),
    )
    conn.execute(
        """
        INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path, uploaded_at,
             expiry_date, is_current)
        VALUES ('doc-api-dhs', 'app-api-dhs', 'passport', 'passport.pdf',
                '/tmp/passport.pdf', ?, ?, 1)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    import server as server_module

    _patch_attr(server_module, "DATABASE_URL", "", restore)
    _patch_attr(server_module, "DB_PATH", db_path, restore)
    _patch_attr(server_module, "USE_POSTGRES", False, restore)
    _patch_attr(server_module, "USE_POSTGRESQL", False, restore)
    _patch_attr(server_module, "db_get_db", db_module.get_db, restore)
    _patch_attr(server_module, "db_init_db", db_module.init_db, restore)
    from server import make_app

    app = make_app()
    port = _free_port()
    started = threading.Event()

    def run_server():
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        io_loop = tornado.ioloop.IOLoop.current()
        http_server = tornado.httpserver.HTTPServer(app)
        http_server.listen(port, "127.0.0.1")
        server_ref["server"] = http_server
        server_ref["loop"] = io_loop
        started.set()
        io_loop.start()

    try:
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        started.wait(timeout=3)
        time.sleep(0.2)
        yield f"http://127.0.0.1:{port}", db_module
    finally:
        if thread:
            from tests.conftest import shutdown_test_http_server
            shutdown_test_http_server(thread, server_ref)
        if previous_env["DB_PATH"] is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = previous_env["DB_PATH"]
        if previous_env["DATABASE_URL"] is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_env["DATABASE_URL"]
        _restore_attrs(restore)
        try:
            os.unlink(db_path)
        except FileNotFoundError:
            pass


def _api_token(user_id, role, name):
    from auth import create_token

    return create_token(user_id, role, name, "officer")


def _api_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_api_empty_body_defaults_to_dry_run_and_writes_nothing(sweep_api_server):
    base_url, db_module = sweep_api_server
    token = _api_token("admin_dhs", "admin", "Admin DHS")

    conn = db_module.get_db()
    before = conn.execute("SELECT COUNT(*) AS c FROM monitoring_alerts").fetchone()["c"]
    conn.close()

    resp = requests.post(
        f"{base_url}/api/monitoring/document-health/sweep",
        headers=_api_headers(token), json={}, timeout=10,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["created"] == 1  # would-create, reported only

    conn = db_module.get_db()
    after = conn.execute("SELECT COUNT(*) AS c FROM monitoring_alerts").fetchone()["c"]
    conn.close()
    assert after == before


def test_api_live_sweep_requires_explicit_flag_and_creates(sweep_api_server):
    base_url, db_module = sweep_api_server
    token = _api_token("admin_dhs", "admin", "Admin DHS")

    resp = requests.post(
        f"{base_url}/api/monitoring/document-health/sweep",
        headers=_api_headers(token),
        json={"dry_run": False, "max_alerts": 10},
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is False
    assert body["created"] == 1

    conn = db_module.get_db()
    try:
        alert = conn.execute(
            "SELECT * FROM monitoring_alerts WHERE application_id = 'app-api-dhs'"
        ).fetchone()
        assert alert is not None
        assert alert["alert_type"] == "document_expired"
        assert alert["detected_by"] == "document_health_monitor"
        manual_audit = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'monitoring.document_health.manual_sweep'"
        ).fetchone()["c"]
        assert manual_audit == 1
    finally:
        conn.close()


def test_api_sweep_is_admin_sco_only(sweep_api_server):
    base_url, _db_module = sweep_api_server
    co_token = _api_token("co_dhs", "co", "CO DHS")

    resp = requests.post(
        f"{base_url}/api/monitoring/document-health/sweep",
        headers=_api_headers(co_token), json={}, timeout=10,
    )
    assert resp.status_code == 403


def test_api_status_endpoint_reports_config(sweep_api_server):
    base_url, _db_module = sweep_api_server
    token = _api_token("admin_dhs", "admin", "Admin DHS")

    resp = requests.get(
        f"{base_url}/api/monitoring/document-health/sweep",
        headers=_api_headers(token), timeout=10,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is False  # off by default
    assert body["enabled_default"] == "explicit_opt_in_only"
    assert body["interval_seconds"] >= 300
    assert body["max_alerts_per_run"] >= 1
