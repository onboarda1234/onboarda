"""PR-27 / audit-log-tamper-evidence-1 — general audit_log hash chain (CORE).

Locks the decision-independent core: the v2.46 schema, the canonical hash, the
append_audit_log chokepoint, and verify_audit_log_chain's legacy/coverage-gap +
retention-tolerant model — on SQLite AND live PostgreSQL, because a chain that
verifies on SQLite but not on PostgreSQL (where TIMESTAMP columns return
datetimes, not strings) would be useless in production.

Nothing is wired through append_audit_log yet (that is a separate, decision-gated
step); these tests exercise the primitive directly.
"""
import importlib
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── engine-agnostic scenario bodies (run against both SQLite and PostgreSQL) ──

def _reset_audit_log(conn):
    """Start from an empty audit_log so `chained` == the whole table and the
    legacy-count assertions are exact (init/seed may insert legacy rows)."""
    conn.execute("DELETE FROM audit_log")
    conn.commit()


def _scenario_links_and_clean_verify(conn, dbmod):
    _reset_audit_log(conn)
    h0 = dbmod.append_audit_log(conn, action="A0", user_id="u1", detail="first", commit=True)
    h1 = dbmod.append_audit_log(conn, action="A1", user_id="u2", target="app1",
                                before_state={"status": "x"}, after_state={"status": "y"}, commit=True)
    h2 = dbmod.append_audit_log(conn, action="A2", user_id="u1", commit=True)
    rows = [dict(r) for r in conn.execute(
        "SELECT id, previous_hash, entry_hash FROM audit_log ORDER BY id").fetchall()]
    assert len(rows) == 3
    # genesis has no predecessor; each subsequent entry links to the prior hash
    assert rows[0]["previous_hash"] is None
    assert rows[0]["entry_hash"] == h0
    assert rows[1]["previous_hash"] == h0 and rows[1]["entry_hash"] == h1
    assert rows[2]["previous_hash"] == h1 and rows[2]["entry_hash"] == h2
    res = dbmod.verify_audit_log_chain(conn)
    assert res["verified"] is True
    assert res["chained_rows"] == 3
    assert res["legacy_rows"] == 0
    assert res["coverage_gaps"] == 0
    assert res["coverage_complete"] is True
    assert res["broken_links"] == []


def _scenario_tamper_detected(conn, dbmod):
    _reset_audit_log(conn)
    for i in range(4):
        dbmod.append_audit_log(conn, action=f"T{i}", user_id="u", detail=f"d{i}", commit=True)
    # Mutate a stored field on a committed row — the recomputed hash no longer matches.
    conn.execute("UPDATE audit_log SET detail = 'TAMPERED' WHERE action = 'T2'")
    conn.commit()
    res = dbmod.verify_audit_log_chain(conn)
    assert res["verified"] is False
    assert any(b.get("issue") == "content_tampered" for b in res["broken_links"])


def _scenario_legacy_tolerated(conn, dbmod):
    _reset_audit_log(conn)
    # A raw (hash-less) row written BEFORE the chain starts is legacy, not a gap.
    conn.execute("INSERT INTO audit_log (action, user_id) VALUES ('LEGACY', 'u')")
    conn.commit()
    dbmod.append_audit_log(conn, action="C0", user_id="u", commit=True)
    dbmod.append_audit_log(conn, action="C1", user_id="u", commit=True)
    res = dbmod.verify_audit_log_chain(conn)
    assert res["verified"] is True
    assert res["legacy_rows"] == 1
    assert res["coverage_gaps"] == 0


def _scenario_coverage_gap_reported_not_integrity_failure(conn, dbmod):
    """A raw insert AFTER the chain started is a COVERAGE gap — reported via
    coverage_complete/coverage_gaps but it must NOT flip integrity `verified`
    to False. During the deferred wiring rollout such gaps are EXPECTED; forcing
    verified:False would train operators to ignore the flag and bury a real
    tamper."""
    _reset_audit_log(conn)
    for i in range(3):
        dbmod.append_audit_log(conn, action=f"G{i}", user_id="u", commit=True)
    conn.execute("INSERT INTO audit_log (action, user_id) VALUES ('RAW_BYPASS', 'u')")
    conn.commit()
    res = dbmod.verify_audit_log_chain(conn)
    assert res["coverage_gaps"] == 1
    assert res["coverage_complete"] is False
    assert res["verified"] is True, res["broken_links"]  # integrity intact
    # coverage_gap is NOT an integrity break
    assert not any(b.get("issue") == "coverage_gap" for b in res["broken_links"])


def _scenario_tamper_caught_despite_coverage_gap(conn, dbmod):
    """The load-bearing anti-alert-fatigue property: a genuine tamper is still
    reported verified:False even when the table also has (expected) coverage
    gaps — the gap must not mask the tamper."""
    _reset_audit_log(conn)
    for i in range(4):
        dbmod.append_audit_log(conn, action=f"M{i}", user_id="u", detail=f"d{i}", commit=True)
    # an expected rollout gap
    conn.execute("INSERT INTO audit_log (action, user_id) VALUES ('RAW_BYPASS', 'u')")
    # AND a real tamper on a chained row
    conn.execute("UPDATE audit_log SET detail = 'TAMPERED' WHERE action = 'M2'")
    conn.commit()
    res = dbmod.verify_audit_log_chain(conn)
    assert res["coverage_gaps"] == 1 and res["coverage_complete"] is False
    assert res["verified"] is False
    assert any(b.get("issue") == "content_tampered" for b in res["broken_links"])


def _scenario_limit_windowed_integrity(conn, dbmod):
    """A positive `limit` bounds verification to the most recent N chained rows
    (integrity-only recency check) without scanning the whole table; coverage
    classification is skipped (coverage_complete is None)."""
    _reset_audit_log(conn)
    for i in range(8):
        dbmod.append_audit_log(conn, action=f"W{i}", user_id="u", commit=True)
    res = dbmod.verify_audit_log_chain(conn, limit=3)
    assert res["verified"] is True, res["broken_links"]
    assert res["entries_checked"] == 3  # bounded, not all 8
    assert res["coverage_complete"] is None  # not evaluated in windowed mode
    # tampering a row inside the window is still caught
    conn.execute("UPDATE audit_log SET action = 'ZZ' WHERE action = 'W7'")
    conn.commit()
    res2 = dbmod.verify_audit_log_chain(conn, limit=3)
    assert res2["verified"] is False
    assert any(b.get("issue") == "content_tampered" for b in res2["broken_links"])


def _scenario_genesis_after_retention_delete(conn, dbmod):
    _reset_audit_log(conn)
    for i in range(4):
        dbmod.append_audit_log(conn, action=f"R{i}", user_id="u", commit=True)
    # GDPR retention purge of the oldest (genesis) chained row: the earliest
    # surviving row now carries a non-NULL previous_hash pointing at a deleted
    # predecessor. That must verify (anchored head), NOT be flagged as a broken
    # genesis...
    gid = conn.execute("SELECT MIN(id) AS m FROM audit_log").fetchone()["m"]
    conn.execute("DELETE FROM audit_log WHERE id = ?", (gid,))
    conn.commit()
    res = dbmod.verify_audit_log_chain(conn)
    assert res["verified"] is True, res["broken_links"]
    assert res["chained_rows"] == 3
    # ...and tampering a surviving row is STILL caught after the retention delete
    # (retention must not blind tamper detection).
    conn.execute("UPDATE audit_log SET action = 'X' WHERE action = 'R2'")
    conn.commit()
    res2 = dbmod.verify_audit_log_chain(conn)
    assert res2["verified"] is False
    assert any(b.get("issue") == "content_tampered" for b in res2["broken_links"])


def _scenario_cross_engine_hash_reproducible(conn, dbmod):
    """The stored entry_hash must equal a recompute from the row read back out of
    the DB — this is the cross-engine guarantee (PostgreSQL returns the timestamp
    as a datetime, SQLite as a string; the normalizer must reconcile them)."""
    _reset_audit_log(conn)
    stored = dbmod.append_audit_log(conn, action="RT", user_id="u", target="t1",
                                    detail="d", before_state={"a": 1}, commit=True)
    row = dict(conn.execute(
        "SELECT id, user_id, user_name, user_role, action, target, detail, ip_address, "
        "timestamp, before_state, after_state, previous_hash, entry_hash "
        "FROM audit_log WHERE action = 'RT'").fetchone())
    recomputed = dbmod._compute_audit_log_entry_hash(row)
    assert recomputed == stored == row["entry_hash"]


_SCENARIOS = [
    _scenario_links_and_clean_verify,
    _scenario_tamper_detected,
    _scenario_legacy_tolerated,
    _scenario_coverage_gap_reported_not_integrity_failure,
    _scenario_tamper_caught_despite_coverage_gap,
    _scenario_limit_windowed_integrity,
    _scenario_genesis_after_retention_delete,
    _scenario_cross_engine_hash_reproducible,
]


# ── SQLite ──

@pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda s: s.__name__)
def test_audit_log_chain_sqlite(temp_db, scenario):
    import db as dbmod
    conn = dbmod.get_db()
    try:
        scenario(conn, dbmod)
    finally:
        conn.close()


# ── live PostgreSQL (fresh throwaway database per run) ──

def _pg_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


@pytest.fixture()
def fresh_pg(monkeypatch):
    base_dsn = _pg_dsn()
    if not base_dsn:
        pytest.skip("No PostgreSQL DSN available")
    import psycopg2
    from urllib.parse import urlsplit, urlunsplit
    db_name = f"onboarda_test_pr27_{uuid.uuid4().hex[:12]}"
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
    orig_environment = os.environ.get("ENVIRONMENT")
    orig_test_postgres_dsn = os.environ.get("TEST_POSTGRES_DSN")
    try:
        monkeypatch.setenv("DATABASE_URL", fresh_dsn)
        monkeypatch.setenv("TEST_POSTGRES_DSN", fresh_dsn)
        monkeypatch.setenv("ENVIRONMENT", "testing")
        import config as config_module
        import db as db_module
        importlib.reload(config_module)
        importlib.reload(db_module)
        db_module.init_db()
        conn = db_module.get_db()
        db_module.seed_initial_data(conn)
        conn.commit()
        conn.close()
        yield db_module
    finally:
        if orig is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = orig
        # Restore ENVIRONMENT BEFORE the reloads: monkeypatch's own undo runs
        # only after this finally block, so without this the reloaded config/db
        # modules re-derive their constants under ENVIRONMENT=development and
        # every later test in the process runs against development-flavoured
        # module state while os.environ says testing.
        if orig_environment is None:
            os.environ.pop("ENVIRONMENT", None)
        else:
            os.environ["ENVIRONMENT"] = orig_environment
        if orig_test_postgres_dsn is None:
            os.environ.pop("TEST_POSTGRES_DSN", None)
        else:
            os.environ["TEST_POSTGRES_DSN"] = orig_test_postgres_dsn
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


@pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda s: s.__name__)
def test_audit_log_chain_postgres(fresh_pg, scenario):
    from regulated_deletion import test_database_teardown_context

    dbmod = fresh_pg
    conn = dbmod.get_db()
    try:
        assert conn.is_postgres is True
        with test_database_teardown_context(conn, reason=f"reset audit-chain scenario {scenario.__name__}"):
            scenario(conn, dbmod)
    finally:
        conn.close()


# ── schema guard: the migration must add the columns + anti-fork index ──

def test_v2_46_columns_and_antifork_index_present_sqlite(temp_db):
    import db as dbmod
    conn = dbmod.get_db()
    try:
        assert dbmod._safe_column_exists(conn, "audit_log", "previous_hash")
        assert dbmod._safe_column_exists(conn, "audit_log", "entry_hash")
        # the partial unique index on previous_hash must exist (anti-fork backstop)
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_audit_log_prev_hash'"
        ).fetchone()
        assert idx is not None
    finally:
        conn.close()


def test_antifork_index_blocks_duplicate_previous_hash_sqlite(temp_db):
    """Two entries may never share a previous_hash (structural fork prevention)."""
    import db as dbmod
    import sqlite3
    conn = dbmod.get_db()
    try:
        _reset_audit_log(conn)
        dbmod.append_audit_log(conn, action="F0", user_id="u", commit=True)
        h = conn.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()["entry_hash"]
        # first successor referencing h — fine
        conn.execute("INSERT INTO audit_log (action, previous_hash, entry_hash) VALUES ('F1', ?, 'hh1')", (h,))
        conn.commit()
        # a SECOND row referencing the same predecessor must be rejected by the index
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO audit_log (action, previous_hash, entry_hash) VALUES ('F1b', ?, 'hh2')", (h,))
            conn.commit()
    finally:
        conn.close()
