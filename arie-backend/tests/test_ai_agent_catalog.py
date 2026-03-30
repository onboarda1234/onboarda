from pathlib import Path


def test_canonical_ai_agent_catalog_has_expected_ids_and_names():
    from ai_agent_catalog import AI_AGENT_CATALOG, AI_AGENT_BY_ID

    ids = [agent["id"] for agent in AI_AGENT_CATALOG]
    names = [agent["name"] for agent in AI_AGENT_CATALOG]

    assert ids == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert len(set(names)) == 10
    assert AI_AGENT_BY_ID[5]["name"] == "Compliance Memo & Risk Recommendation Agent"
    assert AI_AGENT_BY_ID[9]["name"] == "Regulatory Impact Agent"
    assert AI_AGENT_BY_ID[9]["implementation_mode"] == "future_phase"


def test_seeded_ai_agents_match_canonical_catalog(temp_db):
    from ai_agent_catalog import AI_AGENT_CATALOG
    from db import get_db

    db = get_db()
    rows = db.execute(
        """
        SELECT agent_number, name, stage, supervisor_agent_type
        FROM ai_agents
        ORDER BY agent_number
        """
    ).fetchall()
    db.close()

    assert len(rows) == len(AI_AGENT_CATALOG)

    for row, expected in zip(rows, AI_AGENT_CATALOG):
        assert row["agent_number"] == expected["id"]
        assert row["name"] == expected["name"]
        assert row["stage"] == expected["stage"]
        assert row["supervisor_agent_type"] == expected["supervisor_type"]


def test_supervisor_agent_types_match_canonical_numbered_model():
    from supervisor.schemas import AgentType

    assert len(AgentType) == 10
    assert AgentType.REGULATORY_IMPACT.value == "regulatory_impact"
    assert "business_model_plausibility" not in {agent_type.value for agent_type in AgentType}


def test_no_scoped_ui_or_memo_surfaces_use_stale_agent_labels():
    # Resolve repo root relative to this test file (tests/ -> arie-backend/ -> repo root)
    root = Path(__file__).resolve().parent.parent.parent
    backend = root / "arie-backend"
    scoped_files = [
        root / "arie-backoffice.html",
        backend / "arie-backoffice.html",
        root / "arie-portal.html",
        backend / "arie-portal.html",
        backend / "memo_handler.py",
        backend / "claude_client.py",
        backend / "supervisor" / "agent_executors.py",
        backend / "supervisor" / "schemas.py",
        backend / "db.py",
        backend / "IMPLEMENTATION_SUMMARY.md",
    ]
    banned_strings = [
        "Compliance " + "Memo " + "Agent",
        "Agent 9: " + "Compliance " + "Memo " + "Agent",
        "66 " + "automated " + "checks",
        "66 " + "checks",
        "10-agent " + "verification " + "pipeline",
        "AI " + "Compliance " + "Engine",
        "fully automated through our " + "10-agent " + "pipeline",
        "external_" + "db_" + "verification",
        "Regulatory Impact Agent has no " + "supervisor equivalent",
        "Agent 2a: ",
        "Agent 2: " + "Corporate Structure & UBO Mapping",
        "Agent 4: " + "FinCrime Screening Interpretation",
    ]

    for path in scoped_files:
        if not path.exists():
            continue
        text = path.read_text()
        for banned in banned_strings:
            assert banned not in text, f"Found stale string '{banned}' in {path}"
