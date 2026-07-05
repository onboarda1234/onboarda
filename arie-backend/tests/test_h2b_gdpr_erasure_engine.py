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
    from datetime import datetime, timezone
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-ret", "a-h2b-ret", 4000)
    # give the subject a supervisor_audit_log row (retained-required, app-linked).
    # supervisor_audit_log NOT NULL cols: id, timestamp, event_type, action.
    db.execute(
        "INSERT INTO supervisor_audit_log "
        "(id, timestamp, event_type, action, application_id, entry_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("sup-h2b-ret", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
         "decision", "approve", "a-h2b-ret", "deadbeef"),
    )
    db.commit()
    ledger = ge.build_erasure_ledger(db, "c-h2b-ret")
    sup = next((e for e in ledger["entries"] if e["table"] == "supervisor_audit_log"), None)
    assert sup is not None, "supervisor_audit_log must appear in the ledger"
    assert sup["rows"] > 0
    assert sup["disposition"] == "retained_under_legal_obligation"
    # must cite an actual basis, never a bare "required"
    assert sup.get("legal_basis")
    assert sup["legal_basis"].strip().lower() != "required"


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


# ── Adversarial-review regressions (F1 / F2) ─────────────────────────────────

def test_client_sessions_draft_pii_blocks_live_completion(db):
    """A save-and-resume client_sessions row holds draft PII (contact email,
    names, DOB, nationality, ownership). It must NOT be excluded from discovery;
    it must surface as deferred and BLOCK a live erasure from reporting
    'executed' while that PII survives (adversarial F1)."""
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-cs", "a-h2b-cs", 4000)  # out of retention
    db.execute(
        "INSERT INTO client_sessions (id, client_id, application_id, form_data) VALUES (?, ?, ?, ?)",
        ("cs-h2b-1", "c-h2b-cs", "a-h2b-cs",
         '{"contact_email": "jane@doe.com", "director_name": "Jane Doe", '
         '"dob": "1980-01-01", "nationality": "GB"}'),
    )
    db.commit()

    ledger = ge.build_erasure_ledger(db, "c-h2b-cs")
    cs = next((e for e in ledger["entries"] if e["table"] == "client_sessions"), None)
    assert cs is not None, "client_sessions must not be excluded from discovery"
    assert cs["disposition"] == "deferred_not_implemented"
    assert "client_sessions" in ledger["deferred_tables"]

    res = ge.execute_subject_erasure(db, "c-h2b-cs", requested_by="admin", dry_run=False)
    db.commit()
    assert res["action"] == "refused_incomplete"
    assert res["changes_made"] is False
    assert res.get("erasure_executed") in (None, False)
    # draft PII and the director PII must both remain intact
    fd = db.execute("SELECT form_data FROM client_sessions WHERE id = 'cs-h2b-1'").fetchone()
    assert "Jane Doe" in fd["form_data"]
    d = db.execute("SELECT full_name FROM directors WHERE application_id = 'a-h2b-cs'").fetchone()
    assert d["full_name"] == "John Doe"


def test_application_id_only_row_is_counted(db):
    """A subject row linked ONLY by application_id (client_id NULL) must be
    counted. The old elif made the application_id branch unreachable whenever a
    client_id column existed, undercounting to 0 and letting a live erasure
    falsely 'complete' (adversarial F2)."""
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-aid", "a-h2b-aid", 4000)
    # client_sessions has BOTH client_id and application_id; insert a row whose
    # client_id is NULL but whose application_id points at the subject's app.
    db.execute(
        "INSERT INTO client_sessions (id, client_id, application_id, form_data) VALUES (?, NULL, ?, ?)",
        ("cs-h2b-aid", "a-h2b-aid", '{"director_name": "Anon Linked"}'),
    )
    db.commit()
    app_ids = ge._subject_app_ids(db, "c-h2b-aid")
    n = ge._subject_row_count(db, "client_sessions", "c-h2b-aid", app_ids)
    assert n == 1, "application_id-only row must be counted, not undercounted to 0"
    # and it therefore surfaces in the ledger (as deferred), blocking completion
    ledger = ge.build_erasure_ledger(db, "c-h2b-aid")
    assert "client_sessions" in ledger["deferred_tables"]


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
    cid = "c-h2b-ev"

    # A dry-run completion marker must NOT satisfy verification.
    ge._log_erasure(db, client_id=cid, application_id="a1", requested_by="admin",
                    action="erasure_completed", outcome="completed", dry_run=True, dsar_request_id=corr)
    db.commit()
    assert gdpr.verify_dsar_erasure_evidence(db, corr, cid) is False

    # A per-application 'erased' row (not the completion marker) must NOT satisfy
    # it — a partial run writes these but never truly completes (F4).
    ge._log_erasure(db, client_id=cid, application_id="a1", requested_by="admin",
                    action="erased", outcome="erased", dry_run=False, dsar_request_id=corr)
    db.commit()
    assert gdpr.verify_dsar_erasure_evidence(db, corr, cid) is False

    # A qualifying non-dry-run completion marker satisfies it — but ONLY for the
    # bound client_id; a different subject sharing the corr id must stay False (F5).
    ge._log_erasure(db, client_id=cid, application_id="a1", requested_by="admin",
                    action="erasure_completed", outcome="completed", dry_run=False, dsar_request_id=corr)
    db.commit()
    assert gdpr.verify_dsar_erasure_evidence(db, corr, cid) is True
    assert gdpr.verify_dsar_erasure_evidence(db, corr, "c-h2b-other") is False
    assert gdpr.verify_dsar_erasure_evidence(db, corr, None) is False


def test_mark_dsar_refuses_without_evidence(db):
    import gdpr
    import gdpr_erasure as ge
    ge._ensure_erasure_log_table(db)
    cid = "c-h2b-mark"
    created = gdpr.create_dsar(db, "erasure", "m@example.com", "M", cid, "erase")
    dsar_id = created["id"]

    # No evidence at all → refuse.
    assert gdpr.mark_dsar_erasure_executed(db, dsar_id, "no-such-corr") is False
    assert db.execute("SELECT erasure_executed FROM data_subject_requests WHERE id = ?", (dsar_id,)).fetchone()["erasure_executed"] in (False, 0, None)

    # A per-app 'erased' row (no completion marker) is NOT enough → still refuse.
    corr = f"corr-{dsar_id}"
    ge._log_erasure(db, client_id=cid, application_id="a9", requested_by="admin",
                    action="erased", outcome="erased", dry_run=False, dsar_request_id=corr)
    db.commit()
    assert gdpr.mark_dsar_erasure_executed(db, dsar_id, corr) is False

    # The completion marker bound to this subject flips it.
    ge._log_erasure(db, client_id=cid, application_id="a9", requested_by="admin",
                    action="erasure_completed", outcome="completed", dry_run=False, dsar_request_id=corr)
    db.commit()
    assert gdpr.mark_dsar_erasure_executed(db, dsar_id, corr) is True
    db.commit()
    assert db.execute("SELECT erasure_executed FROM data_subject_requests WHERE id = ?", (dsar_id,)).fetchone()["erasure_executed"] in (True, 1)


def test_partial_erasure_does_not_satisfy_verification(db):
    """A subject with one erasable + one retained application yields a PARTIAL
    run: per-app 'erased' rows are written but NO 'erasure_completed' marker, so
    DSAR verification must stay False — a partial run can never be mistaken for a
    completed erasure (adversarial F4)."""
    import gdpr
    import gdpr_erasure as ge
    _seed_subject(db, "c-h2b-part", "a-h2b-part-old", 4000)   # out of retention -> erasable
    _seed_subject(db, "c-h2b-part", "a-h2b-part-new", 30)     # in window -> retained
    corr = "corr-partial"
    res = ge.execute_subject_erasure(db, "c-h2b-part", requested_by="admin",
                                     dry_run=False, dsar_request_id=corr)
    db.commit()
    assert res["action"] == "partial"
    assert res["erasure_executed"] is False
    assert "a-h2b-part-new" in res["retained_refused_application_ids"]
    # a per-app 'erased' row exists for the out-of-retention app...
    erased = db.execute(
        "SELECT COUNT(*) AS c FROM gdpr_erasure_log WHERE dsar_request_id = ? AND action = 'erased'",
        (corr,),
    ).fetchone()
    assert (erased["c"] if not isinstance(erased, tuple) else erased[0]) >= 1
    # ...but NO completion marker, so verification stays False.
    assert gdpr.verify_dsar_erasure_evidence(db, corr, "c-h2b-part") is False
    # the retained app's PII is intact
    d = db.execute("SELECT full_name FROM directors WHERE application_id = 'a-h2b-part-new'").fetchone()
    assert d["full_name"] == "John Doe"


def test_cross_subject_correlation_cannot_mark_another_dsar(db):
    """A completion marker bound to subject A must not let subject B's DSAR that
    happens to reference the same correlation id be marked executed. The evidence
    check binds on the DSAR's OWN client_id, looked up server-side (F5)."""
    import gdpr
    import gdpr_erasure as ge
    ge._ensure_erasure_log_table(db)
    shared = "shared-corr-xsub"
    # Subject A's genuine completion marker under the shared correlation id.
    ge._log_erasure(db, client_id="c-h2b-xA", application_id="a-xA", requested_by="admin",
                    action="erasure_completed", outcome="completed", dry_run=False, dsar_request_id=shared)
    db.commit()
    # Subject B's DSAR references the SAME correlation id but is a different client.
    created = gdpr.create_dsar(db, "erasure", "b@example.com", "B", "c-h2b-xB", "erase")
    dsar_b = created["id"]
    assert gdpr.mark_dsar_erasure_executed(db, dsar_b, shared) is False
    assert db.execute("SELECT erasure_executed FROM data_subject_requests WHERE id = ?", (dsar_b,)).fetchone()["erasure_executed"] in (False, 0, None)


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
