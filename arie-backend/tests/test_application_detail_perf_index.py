"""Regression guard for the application-detail open-latency fix.

Opening an application in the back office issues one blocking GET
`/api/applications/:id`. That handler runs the document-reliance gate, which
resolves the latest ``verify_document`` agent execution per document via
``document_reliance_gate._latest_agent_execution`` — a query filtering
``agent_executions`` by ``document_id``. The table shipped with no indexes, so
that lookup was a full table scan for every document (compounded because the
gate is computed twice per open), which was the dominant cost when opening a
case.

These guard that ``idx_agent_executions_document_id`` is created by the schema
migrations and that SQLite's planner uses it for that lookup. Behaviour of the
handler is unchanged — this is purely an index, not a logic change.
"""
import sqlite3


def test_agent_executions_document_id_index_exists(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'index' AND tbl_name = 'agent_executions'"
    ).fetchall()
    names = {row["name"] for row in rows}
    assert "idx_agent_executions_document_id" in names, names


def test_index_survives_later_migration_rollback(tmp_path):
    """Staging regression: the index must commit in its own transaction.

    On staging, a later migration step (v2.11 CHECK constraints vs an
    off-canon legacy row) failed and issued db.rollback(), silently discarding
    the uncommitted index. _ensure_agent_executions_document_index must commit
    before returning so a subsequent rollback cannot undo it.
    """
    import db as dbmod

    path = tmp_path / "rollback-guard.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    wrapper = dbmod.DBConnection(conn, is_postgres=False)
    try:
        wrapper.execute(
            "CREATE TABLE agent_executions ("
            "id INTEGER PRIMARY KEY, application_id TEXT, document_id TEXT, "
            "agent_name TEXT, agent_number INTEGER, status TEXT, "
            "requires_review INTEGER, started_at TEXT, completed_at TEXT, "
            "error_message TEXT)"
        )
        wrapper.commit()

        assert dbmod._ensure_agent_executions_document_index(wrapper) is True

        # Simulate the failing later migration step: v2.11's except handler
        # calls db.rollback(). The committed index must survive it.
        wrapper.rollback()

        row = wrapper.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_agent_executions_document_id'"
        ).fetchone()
        assert row, "index was rolled back — it must be committed independently"
    finally:
        wrapper.close()


def test_missing_index_is_reported_at_error_level(tmp_path, caplog):
    """An absent index must be loud (ERROR), never a debug whisper."""
    import logging

    import db as dbmod

    path = tmp_path / "loud-failure.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    wrapper = dbmod.DBConnection(conn, is_postgres=False)
    try:
        # No agent_executions table → CREATE INDEX fails → verify finds nothing.
        with caplog.at_level(logging.ERROR, logger=dbmod.logger.name):
            assert dbmod._ensure_agent_executions_document_index(wrapper) is False
        assert any(
            "idx_agent_executions_document_id" in record.getMessage()
            and record.levelno >= logging.ERROR
            for record in caplog.records
        ), caplog.text
    finally:
        wrapper.close()


def test_agent_executions_document_lookup_uses_index(db):
    # Mirror the hot query in document_reliance_gate._latest_agent_execution.
    plan = db.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT id FROM agent_executions "
        "WHERE document_id = ? AND agent_number = 1 "
        "AND LOWER(COALESCE(agent_name, '')) = 'verify_document' "
        "ORDER BY completed_at DESC, id DESC LIMIT 1",
        ("doc-any",),
    ).fetchall()
    plan_text = " ".join(str(row["detail"]) for row in plan).lower()
    # The document_id equality predicate must resolve through the index, not a
    # full table scan.
    assert "idx_agent_executions_document_id" in plan_text, plan_text
    assert "scan agent_executions" not in plan_text, plan_text
