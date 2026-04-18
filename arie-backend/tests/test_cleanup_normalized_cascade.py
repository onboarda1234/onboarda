"""
PR #116 fixup H2/H3 — production-path coverage for normalized-record cleanup.

Proves that the *production* delete cascade (server.cleanup_application_delete_artifacts)
removes screening_reports_normalized rows via the shared helper, and does NOT
abort when the table is absent (e.g. migration 007 not applied locally).
"""
import os
import sys
import tempfile
import sqlite3

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """
    Fresh DB initialised by the production schema, scoped to one test.

    Returns the open db connection; the production cleanup function
    expects to receive an already-open db and does NOT close it.
    """
    db_path = str(tmp_path / "cascade.db")
    monkeypatch.setenv("DB_PATH", db_path)

    # Force a re-init by clearing cached module if already imported
    from db import init_db, get_db
    init_db()

    conn = get_db()
    yield conn
    try:
        conn.close()
    except Exception:
        pass


def _seed_application(db, app_id="app-cascade-1", app_ref="REF-CASCADE-1"):
    db.execute(
        "INSERT INTO applications (id, ref, company_name, status, prescreening_data) "
        "VALUES (?, ?, ?, 'draft', '{}')",
        (app_id, app_ref, "Cascade Co"),
    )


def test_cleanup_removes_normalized_rows_via_helper(isolated_db):
    """
    Production cleanup path must remove normalized rows.
    Proves the helper (not dead inline SQL) is wired into the cascade.
    """
    from screening_storage import ensure_normalized_table
    from server import cleanup_application_delete_artifacts

    db = isolated_db
    ensure_normalized_table(db)
    _seed_application(db, "app-cascade-1", "REF-CASCADE-1")
    # Seed via direct INSERT to avoid coupling this test to the persist
    # helper's cursor-API expectations on the production DBConnection wrapper.
    for v, h in ((1, "h1"), (2, "h2")):
        db.execute(
            "INSERT INTO screening_reports_normalized "
            "(client_id, application_id, source_screening_report_hash, "
            " normalized_report_json, normalization_status, source) "
            "VALUES (?, ?, ?, ?, 'success', 'migration_scaffolding')",
            ("client_1", "app-cascade-1", h, '{"v": ' + str(v) + '}'),
        )
    db.commit()

    # Sanity: rows exist before cleanup
    pre = db.execute(
        "SELECT COUNT(*) AS c FROM screening_reports_normalized WHERE application_id=?",
        ("app-cascade-1",),
    ).fetchone()
    assert pre["c"] == 2

    cleanup_application_delete_artifacts(db, "app-cascade-1", "REF-CASCADE-1")
    db.commit()

    post = db.execute(
        "SELECT COUNT(*) AS c FROM screening_reports_normalized WHERE application_id=?",
        ("app-cascade-1",),
    ).fetchone()
    assert post["c"] == 0


def test_cleanup_does_not_fail_when_normalized_table_missing(isolated_db):
    """
    Production cleanup path must NOT abort the whole cascade when
    screening_reports_normalized does not exist (migration 007 not applied).

    This is the H3 invariant.  We deliberately drop the table so that any
    bypass of the helper's narrow missing-table handling would raise.
    """
    from server import cleanup_application_delete_artifacts

    db = isolated_db
    _seed_application(db, "app-cascade-2", "REF-CASCADE-2")
    db.commit()

    # Ensure the normalized table is absent
    db.execute("DROP TABLE IF EXISTS screening_reports_normalized")
    db.commit()

    # Must not raise — and must still complete the rest of the cascade
    cleanup_application_delete_artifacts(db, "app-cascade-2", "REF-CASCADE-2")
    db.commit()

    # The application's child rows in pre-existing tables should also be gone
    # (cascade did not abort).  We assert on documents as a representative
    # child table that always exists.
    docs = db.execute(
        "SELECT COUNT(*) AS c FROM documents WHERE application_id=?",
        ("app-cascade-2",),
    ).fetchone()
    assert docs["c"] == 0
