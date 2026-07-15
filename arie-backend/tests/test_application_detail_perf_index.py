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


def test_agent_executions_document_id_index_exists(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'index' AND tbl_name = 'agent_executions'"
    ).fetchall()
    names = {row["name"] for row in rows}
    assert "idx_agent_executions_document_id" in names, names


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
