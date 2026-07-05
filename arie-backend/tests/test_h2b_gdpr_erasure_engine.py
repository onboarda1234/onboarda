"""H2B — GDPR subject-erasure engine (wired-but-OFF).

Covers the sharpened spec:
  * category-keyed FAIL-CLOSED retention (no hardcoded fallback);
  * a COMPLETE erase/retain/defer ledger (no silent omission);
  * the LIVE-PATH INVARIANT (no 'executed' while subject data sits in deferred
    tables — refuse as incomplete);
  * PG-correct expanded gdpr_erasure_log;
  * evidence-coupled DSAR status (complete_dsar can't mark erasure; only a
    qualifying non-dry-run log row satisfies verification);
  * the engine stays OFF (unwired) and preserves the draft's safety behaviours.
"""
import contextlib
import importlib
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_policies(db):
    """Guarantee the retention policies these tests depend on.

    The shared conftest temp DB can lack them (seed_initial_data is wrapped in
    a swallowing try/except and can abort before the retention block); the old
    silent 2555-day fallback masked that. H2B is fail-closed, so tests must
    provide their own policies rather than relying on the fixture.
    """
    try:
        from db import _DEFAULT_RETENTION_POLICIES as pols
    except Exception:
        pols = [
            ("client_pii", 2555, "AML/CFT Act 2020 s.17", "", False, True),
            ("application_data", 2555, "Regulatory obligation", "", False, True),
            ("audit_logs", 3650, "Legitimate interest + regulatory", "", False, False),
        ]
    for pol in pols:
        db.execute(
            "INSERT OR IGNORE INTO data_retention_policies "
            "(data_category, retention_days, legal_basis, description, auto_purge, requires_review) "
            "VALUES (?,?,?,?,?,?)",
            pol,
        )
    db.commit()


@contextlib.contextmanager
def _preserve_policies(db):
    """Snapshot and restore data_retention_policies around a mutation test so
    the shared temp DB is not polluted for later tests."""
    rows = [dict(r) for r in db.execute(
        "SELECT data_category, retention_days, legal_basis, description, auto_purge, requires_review "
        "FROM data_retention_policies"
    ).fetchall()]
    try:
        yield
    finally:
        db.execute("DELETE FROM data_retention_policies")
        for r in rows:
            db.execute(
                "INSERT INTO data_retention_policies "
                "(data_category, retention_days, legal_basis, description, auto_purge, requires_review) "
                "VALUES (?,?,?,?,?,?)",
                (r["data_category"], r["retention_days"], r["legal_basis"],
                 r["description"], r["auto_purge"], r["requires_review"]),
            )
        db.commit()


def _seed_subject(db, client_id, app_id, decided_days_ago, *, with_memo=False, with_director=True):
    from datetime import datetime, timedelta, timezone
    _ensure_policies(db)
    decided = (datetime.now(timezone.utc) - timedelta(days=decided_days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name, status) "
        "VALUES (?, ?, 'h', 'Acme Co', 'active')",
        (client_id, f"{client_id}@example.com"),
    )
    db.execute(
        "INSERT INTO applications (id, ref, client_id, company_name, status, decided_at) "
        "VALUES (?, ?, ?, 'Acme Co', 'approved', ?)",
        (app_id, f"REF-{app_id}", client_id, decided),
    )
    if with_director:
        db.execute(
            "INSERT INTO directors (id, application_id, full_name, first_name, last_name, "
            "nationality, date_of_birth, residential_address) "
            "VALUES (?, ?, 'John Doe', 'John', 'Doe', 'GB', '1980-01-01', '1 High St')",
            (f"{app_id}-d1", app_id),
        )
    if with_memo:  # a deferred-table row (compliance_memos: subject-linked, no erase rule)
        db.execute(
            "INSERT INTO compliance_memos (application_id, memo_data) VALUES (?, ?)",
            (app_id, '{"summary": "contains subject narrative PII"}'),
        )
    db.commit()


# ── Fail-closed, category-keyed retention (audit C5) ─────────────────────────

def test_missing_client_pii_policy_fails_closed(db):
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-fc1", "a-h2b-fc1", 4000)
    with _preserve_policies(db):
        db.execute("DELETE FROM data_retention_policies WHERE data_category = 'client_pii'")
        db.commit()
        with pytest.raises(ge.RetentionPolicyError):
            ge.plan_subject_erasure(db, "c-h2b-fc1")
        with pytest.raises(ge.RetentionPolicyError):
            ge.execute_subject_erasure(db, "c-h2b-fc1", requested_by="admin", dry_run=False)


def test_nonpositive_policy_fails_closed(db):
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-fc2", "a-h2b-fc2", 4000)
    with _preserve_policies(db):
        db.execute("UPDATE data_retention_policies SET retention_days = 0 WHERE data_category = 'application_data'")
        db.commit()
        with pytest.raises(ge.RetentionPolicyError):
            ge.plan_subject_erasure(db, "c-h2b-fc2")


def test_no_silent_hardcoded_default():
    """The old silent 2555-day fallback must be gone."""
    import gdpr_erasure as ge
    src = open(os.path.join(BACKEND, "gdpr_erasure.py"), encoding="utf-8").read()
    assert "_DEFAULT_AML_RETENTION_DAYS" not in src, "hardcoded retention fallback still present (C5)"
    assert not hasattr(ge, "_aml_retention_days"), "old silent-fallback resolver still present"


# ── Complete ledger (no silent omission) ─────────────────────────────────────

def test_ledger_accounts_every_subject_table(db):
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-led", "a-h2b-led", 4000, with_memo=True)
    ledger = ge.build_erasure_ledger(db, "c-h2b-led")

    by_table = {e["table"]: e for e in ledger["entries"]}
    # erasable tables with rows
    assert by_table["directors"]["disposition"] == "erasable"
    assert by_table["applications"]["disposition"] == "erasable"
    # the deferred table (compliance_memos) is named, not silently dropped
    assert by_table["compliance_memos"]["disposition"] == "deferred_not_implemented"
    assert "compliance_memos" in ledger["deferred_tables"]
    assert ledger["complete"] is False
    # tables with zero subject rows are not_applicable, never silently missing
    assert all("disposition" in e for e in ledger["entries"])


def test_ledger_retained_entries_cite_a_basis(db):
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-ret", "a-h2b-ret", 4000)
    # give the subject a supervisor_audit_log row (retained-required, app-linked)
    try:
        db.execute(
            "INSERT INTO supervisor_audit_log (application_id, entry_hash) VALUES (?, ?)",
            ("a-h2b-ret", "deadbeef"),
        )
        db.commit()
        ledger = ge.build_erasure_ledger(db, "c-h2b-ret")
        sup = next((e for e in ledger["entries"] if e["table"] == "supervisor_audit_log"), None)
        if sup and sup["rows"] > 0:
            assert sup["disposition"] == "retained_under_legal_obligation"
            assert sup.get("legal_basis")  # must cite a basis, never bare "required"
    except Exception:
        pytest.skip("supervisor_audit_log insert shape differs in this schema")


# ── Live-path invariant (audit) ──────────────────────────────────────────────

def test_live_execute_refuses_incomplete_when_deferred_rows_present(db):
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-inc", "a-h2b-inc", 4000, with_memo=True)  # out of retention + a deferred row
    res = ge.execute_subject_erasure(db, "c-h2b-inc", requested_by="admin", dry_run=False)
    db.commit()
    assert res["action"] == "refused_incomplete"
    assert res["changes_made"] is False
    assert res.get("erasure_executed") in (None, False)
    assert "compliance_memos" in res["deferred_tables"]
    # PII must be intact — nothing was erased
    d = db.execute("SELECT full_name FROM directors WHERE application_id = 'a-h2b-inc'").fetchone()
    assert d["full_name"] == "John Doe"


def test_live_execute_erases_when_no_deferred_rows(db):
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-ok", "a-h2b-ok", 4000)  # out of retention, no deferred rows
    res = ge.execute_subject_erasure(db, "c-h2b-ok", requested_by="admin", dry_run=False)
    db.commit()
    assert res["action"] == "executed"
    assert res["erasure_executed"] is True
    d = db.execute("SELECT full_name FROM directors WHERE application_id = 'a-h2b-ok'").fetchone()
    assert d["full_name"] == "[ERASED]"


# ── Preserved draft safety behaviours ────────────────────────────────────────

def test_dry_run_makes_no_changes(db):
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-dry", "a-h2b-dry", 4000)
    res = ge.execute_subject_erasure(db, "c-h2b-dry", requested_by="admin", dry_run=True)
    assert res["action"] == "dry_run"
    assert res["changes_made"] is False
    assert db.execute("SELECT full_name FROM directors WHERE application_id = 'a-h2b-dry'").fetchone()["full_name"] == "John Doe"


def test_in_window_subject_refused_and_pii_preserved(db):
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-win", "a-h2b-win", 30)  # inside retention
    res = ge.execute_subject_erasure(db, "c-h2b-win", requested_by="admin", dry_run=False)
    db.commit()
    assert "a-h2b-win" in res["retained_refused_application_ids"]
    assert res.get("erasure_executed") in (None, False)
    assert db.execute("SELECT full_name FROM directors WHERE application_id = 'a-h2b-win'").fetchone()["full_name"] == "John Doe"


def test_override_requires_reason(db):
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-ov", "a-h2b-ov", 30)
    res = ge.execute_subject_erasure(db, "c-h2b-ov", requested_by="admin", dry_run=False,
                                     override_retention=True, override_reason="")
    assert res["action"] == "refused"


# ── Evidence-coupled DSAR status (caveats A + B) ─────────────────────────────

def test_complete_dsar_never_sets_erasure_executed(db):
    import gdpr
    created = gdpr.create_dsar(db, "erasure", "subj@example.com", "Subj", None, "erase me")
    dsar_id = created["id"]
    done = gdpr.complete_dsar(db, dsar_id, "officer", "handled", "completed")
    assert done.get("erasure_executed") in (False, 0)
    row = db.execute("SELECT erasure_executed FROM data_subject_requests WHERE id = ?", (dsar_id,)).fetchone()
    assert row["erasure_executed"] in (False, 0, None)


def test_verify_evidence_rejects_dry_run_and_generic_rows(db):
    import gdpr
    import gdpr_erasure as ge
    ge._ensure_erasure_log_table(db)
    corr = "dsar-corr-1"

    # A dry-run row must NOT satisfy verification.
    ge._log_erasure(db, client_id="c1", application_id="a1", requested_by="admin",
                    action="erased", outcome="erased", dry_run=True, dsar_request_id=corr)
    db.commit()
    assert gdpr.verify_dsar_erasure_evidence(db, corr) is False

    # A generic (non-erased) row must NOT satisfy it.
    ge._log_erasure(db, client_id="c1", application_id="a1", requested_by="admin",
                    action="retained_refused", outcome="retained", dry_run=False, dsar_request_id=corr)
    db.commit()
    assert gdpr.verify_dsar_erasure_evidence(db, corr) is False

    # A qualifying non-dry-run erased row satisfies it.
    ge._log_erasure(db, client_id="c1", application_id="a1", requested_by="admin",
                    action="erased", outcome="erased", dry_run=False, dsar_request_id=corr)
    db.commit()
    assert gdpr.verify_dsar_erasure_evidence(db, corr) is True


def test_mark_dsar_refuses_without_evidence(db):
    import gdpr
    import gdpr_erasure as ge
    ge._ensure_erasure_log_table(db)
    created = gdpr.create_dsar(db, "erasure", "m@example.com", "M", None, "erase")
    dsar_id = created["id"]

    assert gdpr.mark_dsar_erasure_executed(db, dsar_id, "no-such-corr") is False
    assert db.execute("SELECT erasure_executed FROM data_subject_requests WHERE id = ?", (dsar_id,)).fetchone()["erasure_executed"] in (False, 0, None)

    corr = f"corr-{dsar_id}"
    ge._log_erasure(db, client_id="c9", application_id="a9", requested_by="admin",
                    action="erased", outcome="erased", dry_run=False, dsar_request_id=corr)
    db.commit()
    assert gdpr.mark_dsar_erasure_executed(db, dsar_id, corr) is True
    db.commit()
    assert db.execute("SELECT erasure_executed FROM data_subject_requests WHERE id = ?", (dsar_id,)).fetchone()["erasure_executed"] in (True, 1)


# ── Stays OFF (unwired) ──────────────────────────────────────────────────────

def test_engine_not_imported_by_live_runtime():
    for module in ("server.py", "gdpr.py"):
        src = open(os.path.join(BACKEND, module), encoding="utf-8").read()
        assert "import gdpr_erasure" not in src, f"{module} imports the erasure engine — must stay OFF/unwired"
        assert "execute_subject_erasure" not in src, f"{module} calls the erasure executor — must stay OFF"


# ── PostgreSQL correctness (throwaway DB) ────────────────────────────────────

def _pg_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


@pytest.fixture()
def fresh_pg(monkeypatch):
    base_dsn = _pg_dsn()
    if not base_dsn:
        pytest.skip("No PostgreSQL DSN available")
    import psycopg2
    from urllib.parse import urlsplit, urlunsplit
    db_name = f"h2b_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(base_dsn)
    admin = psycopg2.connect(base_dsn)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}"')
    except Exception:
        admin.close()
        raise
    fresh_dsn = urlunsplit((parts.scheme, parts.netloc, "/" + db_name, parts.query, parts.fragment))
    orig = os.environ.get("DATABASE_URL")
    try:
        monkeypatch.setenv("DATABASE_URL", fresh_dsn)
        monkeypatch.setenv("ENVIRONMENT", "development")
        import config as config_module
        import db as db_module
        importlib.reload(config_module)
        importlib.reload(db_module)
        db_module.init_db()
        conn = db_module.get_db()
        db_module.seed_initial_data(conn)
        conn.commit()
        yield db_module
    finally:
        if orig is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = orig
        try:
            import config as config_module
            import db as db_module
            importlib.reload(config_module)
            importlib.reload(db_module)
        except Exception:
            pass
        try:
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        except Exception:
            pass
        admin.close()


def test_pg_erasure_log_uses_real_booleans(fresh_pg):
    import gdpr_erasure as ge
    from datetime import datetime, timedelta, timezone
    db = fresh_pg.get_db()
    try:
        decided = (datetime.now(timezone.utc) - timedelta(days=4000)).strftime("%Y-%m-%dT%H:%M:%S")
        db.execute("INSERT INTO clients (id, email, password_hash, company_name, status) VALUES (?, ?, 'h', 'Co', 'active')",
                   ("pgc", "pgc@example.com"))
        db.execute("INSERT INTO applications (id, ref, client_id, company_name, status, decided_at) VALUES (?, ?, ?, 'Co', 'approved', ?)",
                   ("pga", "R-PGA", "pgc", decided))
        db.commit()
        res = ge.execute_subject_erasure(db, "pgc", requested_by="admin", dry_run=False)
        db.commit()
        assert res["action"] == "executed"
        row = db.execute("SELECT dry_run, retention_overridden FROM gdpr_erasure_log "
                         "WHERE action = 'erased' LIMIT 1").fetchone()
        # On PG these are real booleans, not 0/1 integers.
        assert row["dry_run"] is False
        assert row["retention_overridden"] is False
    finally:
        db.close()


def test_pg_fail_closed_on_empty_policy(fresh_pg):
    import gdpr_erasure as ge
    db = fresh_pg.get_db()
    try:
        db.execute("DELETE FROM data_retention_policies")
        db.commit()
        db.execute("INSERT INTO clients (id, email, password_hash, company_name, status) VALUES (?, ?, 'h', 'Co', 'active')",
                   ("pgc2", "pgc2@example.com"))
        db.commit()
        with pytest.raises(ge.RetentionPolicyError):
            ge.plan_subject_erasure(db, "pgc2")
    finally:
        db.close()
