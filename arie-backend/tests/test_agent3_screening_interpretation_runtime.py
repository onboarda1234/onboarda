import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests as http_requests
import tornado.httpserver
import tornado.ioloop


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"


def _screening_timestamps():
    now = datetime.now(timezone.utc)
    return (
        (now - timedelta(days=1)).isoformat(),
        (now + timedelta(days=89)).isoformat(),
    )


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture()
def agent3_api_server(temp_db):
    from server import make_app

    app = make_app()
    port = _find_free_port()
    server_ref = {}
    started = threading.Event()

    def run_server():
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        io_loop = tornado.ioloop.IOLoop.current()
        srv = tornado.httpserver.HTTPServer(app)
        srv.listen(port, "127.0.0.1")
        server_ref["server"] = srv
        server_ref["loop"] = io_loop
        started.set()
        io_loop.start()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    started.wait(timeout=3)
    time.sleep(0.2)

    yield f"http://127.0.0.1:{port}"

    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


def _headers(user_id="agent3-officer", role="analyst", token_type="officer"):
    from server import create_token

    name = "Agent 3 Officer" if token_type == "officer" else "Agent 3 Client"
    token = create_token(user_id, role, name, token_type)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _stored_screening_prescreening():
    screened_at, valid_until = _screening_timestamps()
    return {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": screened_at,
            "total_hits": 2,
            "overall_flags": ["pep", "adverse_media"],
            "company_screening": {
                "company_name": "Agent Three Holdings Ltd",
                "matched": True,
                "results": [
                    {
                        "name": "Agent Three Holdings Ltd",
                        "match_score": 0.92,
                        "category": "pep",
                    },
                    {
                        "name": "Agent Three Holdings adverse media article",
                        "match_score": 74,
                        "category": "adverse_media",
                    },
                ],
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
        "screening_valid_until": valid_until,
    }


def _screening_prescreening(*, total_hits=0, company_results=None, director_screenings=None, overall_flags=None):
    company_results = company_results or []
    director_screenings = director_screenings or []
    screened_at, valid_until = _screening_timestamps()
    return {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": screened_at,
            "total_hits": total_hits,
            "overall_flags": overall_flags or [],
            "company_screening": {
                "company_name": "Agent Three Holdings Ltd",
                "matched": bool(company_results),
                "results": company_results,
            },
            "director_screenings": director_screenings,
            "ubo_screenings": [],
        },
        "screening_valid_until": valid_until,
    }


def _clean_no_hit_prescreening():
    return _screening_prescreening(total_hits=0)


def _declared_pep_no_hit_prescreening():
    return _screening_prescreening(
        total_hits=0,
        director_screenings=[{
            "person_name": "Declared PEP Director",
            "person_type": "director",
            "declared_pep": "Yes",
            "screening": {
                "matched": False,
                "results": [],
                "source": "complyadvantage",
                "api_status": "live",
            },
        }],
    )


def _provider_hit_prescreening(category, *, score=0.91):
    return _screening_prescreening(
        total_hits=1,
        overall_flags=[category],
        company_results=[{
            "name": f"Agent Three Holdings {category} hit",
            "match_score": score,
            "category": category,
        }],
    )


def _insert_declared_pep_director(db, app_id):
    db.execute(
        """
        INSERT INTO directors (application_id, full_name, nationality, is_pep, pep_declaration)
        VALUES (?, 'Declared PEP Director', 'Mauritius', 'Yes', ?)
        """,
        (
            app_id,
            json.dumps({
                "declared_pep": True,
                "client_declared_pep": True,
                "pep_status": "declared_yes",
            }, sort_keys=True),
        ),
    )
    db.commit()


def _insert_application(db, *, prescreening_data=None, status="compliance_review", risk_level="MEDIUM", risk_score=55):
    suffix = uuid.uuid4().hex[:8]
    app_id = f"agent3_app_{suffix}"
    app_ref = f"AG3-{suffix}"
    client_id = f"agent3_client_{suffix}"
    db.execute(
        """
        INSERT OR IGNORE INTO clients (id, email, password_hash, company_name, status)
        VALUES (?, ?, 'test-token-only', 'Agent 3 Client Ltd', 'active')
        """,
        (client_id, f"{client_id}@test.local"),
    )
    db.execute(
        """
        INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type,
             status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            app_ref,
            client_id,
            "Agent Three Holdings Ltd",
            "Mauritius",
            "Technology",
            "Company",
            status,
            risk_level,
            risk_score,
            json.dumps(prescreening_data or {}),
        ),
    )
    db.commit()
    return app_id, app_ref


def _fail_provider_call(*args, **kwargs):
    raise AssertionError("Agent 3 interpretation endpoint must not call providers")


def _block_provider_calls(monkeypatch):
    import server

    monkeypatch.setattr(server, "run_full_screening", _fail_provider_call)
    monkeypatch.setattr(server, "screen_sumsub_aml", _fail_provider_call)
    monkeypatch.setattr(server, "lookup_opencorporates", _fail_provider_call)
    monkeypatch.setattr(server.BaseHandler, "check_rate_limit", lambda *_args, **_kwargs: True)


def _post_agent3(agent3_api_server, app_id, *, user_id="agent3-officer"):
    return http_requests.post(
        f"{agent3_api_server}/api/applications/{app_id}/agent3/screening-interpretation",
        headers=_headers(user_id=user_id),
        json={},
        timeout=5,
    )


def _assert_persisted_and_audited(db, app_id, app_ref, expected_recommendation):
    execution = db.execute(
        """
        SELECT * FROM agent_executions
         WHERE application_id=? AND agent_number=3 AND status='completed'
         ORDER BY id DESC LIMIT 1
        """,
        (app_id,),
    ).fetchone()
    assert execution is not None
    assert execution["source"] == "stored_screening_results"
    persisted = json.loads(execution["flags_json"])
    assert persisted["recommended_disposition"] == expected_recommendation
    assert persisted["provider_call_made"] is False
    assert persisted["risk_or_decision_mutation"] is False
    checks = json.loads(execution["checks_json"])
    assert any(item["check"] == "provider_call_made" and item["provider_call_made"] is False for item in checks)

    audit = db.execute(
        """
        SELECT detail, before_state, after_state FROM audit_log
         WHERE target=? AND action='agent3_screening_interpretation.generated'
         ORDER BY id DESC LIMIT 1
        """,
        (app_ref,),
    ).fetchone()
    assert audit is not None
    audit_detail = json.loads(audit["detail"])
    assert audit_detail["provider_call_made"] is False
    assert audit_detail["recommendation"] == expected_recommendation
    assert json.loads(audit["before_state"]) == json.loads(audit["after_state"])
    return persisted


@pytest.mark.parametrize(
    (
        "scenario",
        "prescreening_factory",
        "insert_db_declared_pep",
        "expected_recommendation",
        "expected_severity",
        "expected_text",
    ),
    [
        (
            "clean_no_hit",
            _clean_no_hit_prescreening,
            False,
            "No reportable provider hit recorded",
            "Low",
            "No provider hits found in stored screening results",
        ),
        (
            "declared_pep_prescreening_only",
            _declared_pep_no_hit_prescreening,
            False,
            "Officer review required",
            "High",
            "Stored provider screening may show no external PEP match, but the subject is marked as Declared PEP. Officer review remains required.",
        ),
        (
            "declared_pep_db_only",
            _clean_no_hit_prescreening,
            True,
            "Officer review required",
            "High",
            "Stored provider screening may show no external PEP match, but the subject is marked as Declared PEP. Officer review remains required.",
        ),
        (
            "declared_pep_combined_dedup",
            _declared_pep_no_hit_prescreening,
            True,
            "Officer review required",
            "High",
            "Stored provider screening may show no external PEP match, but the subject is marked as Declared PEP. Officer review remains required.",
        ),
        (
            "provider_pep_hit",
            lambda: _provider_hit_prescreening("pep"),
            False,
            "EDD recommended",
            "High",
            "stored PEP hit(s) require EDD consideration",
        ),
        (
            "sanctions_hit",
            lambda: _provider_hit_prescreening("sanctions"),
            False,
            "Reject recommended",
            "Critical",
            "stored sanctions/watchlist hit(s) require senior officer review",
        ),
        (
            "adverse_media_hit",
            lambda: _provider_hit_prescreening("adverse_media"),
            False,
            "Officer review required",
            "Medium",
            "stored adverse media hit(s) require relevance and materiality review",
        ),
    ],
)
def test_agent3_screening_interpretation_scenarios_are_safe_and_clear(
    agent3_api_server,
    db,
    monkeypatch,
    scenario,
    prescreening_factory,
    insert_db_declared_pep,
    expected_recommendation,
    expected_severity,
    expected_text,
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _block_provider_calls(monkeypatch)
    app_id, app_ref = _insert_application(db, prescreening_data=prescreening_factory())
    if insert_db_declared_pep:
        _insert_declared_pep_director(db, app_id)
    before = dict(db.execute(
        "SELECT status, risk_level, risk_score FROM applications WHERE id=?",
        (app_id,),
    ).fetchone())

    resp = _post_agent3(agent3_api_server, app_id, user_id=f"agent3-{scenario}")

    assert resp.status_code == 200, resp.text
    output = resp.json()["interpretation"]
    assert output["recommended_disposition"] == expected_recommendation
    assert output["severity"] == expected_severity
    assert output["provider_call_made"] is False
    assert output["risk_or_decision_mutation"] is False
    assert output["ai_notice"] == "Deterministic interpretation generated from stored screening results. No provider call was made."
    assert output["officer_notice"] == "Officer decision required. Agent 3 provides an advisory interpretation only."
    rendered_text = json.dumps(output, sort_keys=True)
    assert expected_text in rendered_text
    assert "No compliance risk exists" not in rendered_text
    assert "risk-free" not in rendered_text.lower()
    if scenario.startswith("declared_pep_"):
        assert output["hit_counts"]["total"] == 0
        assert output["hit_counts"]["pep"] == 0
        assert output["hit_counts"]["declared_pep"] == 1
        assert "Clear" != output["recommended_disposition"]

    after = dict(db.execute(
        "SELECT status, risk_level, risk_score FROM applications WHERE id=?",
        (app_id,),
    ).fetchone())
    assert after == before
    _assert_persisted_and_audited(db, app_id, app_ref, expected_recommendation)


def test_agent3_generates_from_stored_screening_without_provider_calls(
    agent3_api_server,
    db,
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "would-be-live-ai-key")
    _block_provider_calls(monkeypatch)

    app_id, app_ref = _insert_application(db, prescreening_data=_stored_screening_prescreening())
    before = dict(db.execute(
        "SELECT status, risk_level, risk_score FROM applications WHERE id=?",
        (app_id,),
    ).fetchone())

    resp = http_requests.post(
        f"{agent3_api_server}/api/applications/{app_id}/agent3/screening-interpretation",
        headers=_headers(),
        json={},
        timeout=5,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    output = body["interpretation"]
    assert output["application_id"] == app_id
    assert output["application_ref"] == app_ref
    assert output["source"] == "stored_screening_results"
    assert output["provider_call_made"] is False
    assert output["risk_or_decision_mutation"] is False
    assert output["ai_mode"] == "deterministic_fallback"
    assert output["ai_notice"] == "Deterministic interpretation generated from stored screening results. No provider call was made."
    assert output["summary"]
    assert output["key_concerns"]
    assert output["false_positive_assessment"]
    assert output["adverse_media_relevance"]
    assert output["severity"] == "High"
    assert output["recommended_disposition"] == "EDD recommended"
    assert output["draft_audit_note"]
    assert output["evidence_used"]
    assert output["output_hash"]

    after = dict(db.execute(
        "SELECT status, risk_level, risk_score FROM applications WHERE id=?",
        (app_id,),
    ).fetchone())
    assert after == before

    execution = db.execute(
        """
        SELECT * FROM agent_executions
         WHERE application_id=? AND agent_number=3 AND status='completed'
         ORDER BY id DESC LIMIT 1
        """,
        (app_id,),
    ).fetchone()
    assert execution is not None
    assert execution["source"] == "stored_screening_results"
    assert execution["requires_review"] in (1, True)
    persisted = json.loads(execution["flags_json"])
    assert persisted["recommended_disposition"] == "EDD recommended"
    checks = json.loads(execution["checks_json"])
    assert any(item["check"] == "provider_call_made" and item["provider_call_made"] is False for item in checks)

    audit = db.execute(
        """
        SELECT detail, before_state, after_state FROM audit_log
         WHERE target=? AND action='agent3_screening_interpretation.generated'
         ORDER BY id DESC LIMIT 1
        """,
        (app_ref,),
    ).fetchone()
    assert audit is not None
    audit_detail = json.loads(audit["detail"])
    assert audit_detail["officer_id"] == "agent3-officer"
    assert audit_detail["provider_call_made"] is False
    assert audit_detail["recommendation"] == "EDD recommended"
    assert json.loads(audit["before_state"]) == json.loads(audit["after_state"])


def test_agent3_read_endpoint_returns_latest_persisted_output(agent3_api_server, db):
    app_id, _app_ref = _insert_application(db, prescreening_data=_stored_screening_prescreening())
    headers = _headers(user_id="agent3-reader")

    first = http_requests.post(
        f"{agent3_api_server}/api/applications/{app_id}/agent3/screening-interpretation",
        headers=headers,
        json={},
        timeout=5,
    )
    assert first.status_code == 200, first.text

    second = http_requests.post(
        f"{agent3_api_server}/api/applications/{app_id}/agent3/screening-interpretation",
        headers=headers,
        json={},
        timeout=5,
    )
    assert second.status_code == 200, second.text

    read = http_requests.get(
        f"{agent3_api_server}/api/applications/{app_id}/agent3/screening-interpretation",
        headers=headers,
        timeout=5,
    )
    assert read.status_code == 200, read.text
    output = read.json()["interpretation"]
    assert output["recommended_disposition"] == "EDD recommended"
    assert output["agent_execution_id"] is not None

    completed_count = db.execute(
        """
        SELECT COUNT(*) AS c FROM agent_executions
         WHERE application_id=? AND agent_number=3 AND status='completed'
        """,
        (app_id,),
    ).fetchone()["c"]
    assert completed_count == 2


def test_agent3_no_stored_screening_data_returns_clear_message_without_completed_output(
    agent3_api_server,
    db,
    monkeypatch,
):
    _block_provider_calls(monkeypatch)
    app_id, _app_ref = _insert_application(db, prescreening_data={})

    resp = http_requests.post(
        f"{agent3_api_server}/api/applications/{app_id}/agent3/screening-interpretation",
        headers=_headers(user_id="agent3-no-data"),
        json={},
        timeout=5,
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == (
        "No stored screening results available for interpretation. "
        "Run screening first through the existing screening workflow."
    )
    assert body["interpretation"] is None

    skipped = db.execute(
        """
        SELECT * FROM agent_executions
         WHERE application_id=? AND agent_number=3 AND status='skipped'
         ORDER BY id DESC LIMIT 1
        """,
        (app_id,),
    ).fetchone()
    assert skipped is not None
    assert skipped["source"] == "stored_screening_results"
    assert skipped["error_message"] == body["error"]

    read = http_requests.get(
        f"{agent3_api_server}/api/applications/{app_id}/agent3/screening-interpretation",
        headers=_headers(user_id="agent3-no-data-reader"),
        timeout=5,
    )
    assert read.status_code == 200, read.text
    assert read.json()["interpretation"] is None


def test_agent3_declared_pep_read_failure_fails_closed_without_clear_output(
    agent3_api_server,
    db,
    monkeypatch,
):
    import server

    _block_provider_calls(monkeypatch)
    monkeypatch.setattr(
        server,
        "_agent3_collect_declared_pep_from_db",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("pep declaration read failed")),
    )
    app_id, app_ref = _insert_application(db, prescreening_data=_clean_no_hit_prescreening())
    before = dict(db.execute(
        "SELECT status, risk_level, risk_score FROM applications WHERE id=?",
        (app_id,),
    ).fetchone())

    resp = _post_agent3(agent3_api_server, app_id, user_id="agent3-pep-read-failure")

    assert resp.status_code == 500
    assert resp.json()["error"] == "Agent 3 screening interpretation failed"
    completed = db.execute(
        """
        SELECT COUNT(*) AS c FROM agent_executions
         WHERE application_id=? AND agent_number=3 AND status='completed'
        """,
        (app_id,),
    ).fetchone()["c"]
    assert completed == 0
    failed_audit = db.execute(
        """
        SELECT detail, before_state, after_state FROM audit_log
         WHERE target=? AND action='agent3_screening_interpretation.failed'
         ORDER BY id DESC LIMIT 1
        """,
        (app_ref,),
    ).fetchone()
    assert failed_audit is not None
    detail = json.loads(failed_audit["detail"])
    assert detail["provider_call_made"] is False
    assert detail["error_type"] == "RuntimeError"
    assert json.loads(failed_audit["before_state"]) == json.loads(failed_audit["after_state"])
    after = dict(db.execute(
        "SELECT status, risk_level, risk_score FROM applications WHERE id=?",
        (app_id,),
    ).fetchone())
    assert after == before


def test_agent3_endpoint_is_officer_only(agent3_api_server, db):
    app_id, _app_ref = _insert_application(db, prescreening_data=_stored_screening_prescreening())

    unauthenticated = http_requests.get(
        f"{agent3_api_server}/api/applications/{app_id}/agent3/screening-interpretation",
        timeout=5,
    )
    assert unauthenticated.status_code == 401

    client = http_requests.post(
        f"{agent3_api_server}/api/applications/{app_id}/agent3/screening-interpretation",
        headers=_headers(user_id="agent3-client-user", role="client", token_type="client"),
        json={},
        timeout=5,
    )
    assert client.status_code == 403

    officer = http_requests.get(
        f"{agent3_api_server}/api/applications/{app_id}/agent3/screening-interpretation",
        headers=_headers(user_id="agent3-authorized-officer"),
        timeout=5,
    )
    assert officer.status_code == 200


def _extract_function(source, name):
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for idx in range(brace, len(source)):
        char = source[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:idx + 1]
    raise AssertionError(f"Could not extract function {name}")


def test_backoffice_agent3_screening_panel_static_contract():
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")

    assert "Agent 3 Screening Interpretation" in html
    assert "Generate an AI-assisted interpretation from stored screening results." in html
    assert "This does not re-run screening or change the officer decision." in html
    assert "Generate interpretation" in html
    assert "Officer decision required. Agent 3 provides an advisory interpretation only." in html
    assert "Collapse Agent 3" in html
    assert "Expand Agent 3" in html
    assert "AGENT3_SCREENING_INTERPRETATION_COLLAPSED" in html
    assert "agent3ScreeningInterpretationAppKey" in html
    assert "isAgent3ScreeningInterpretationCollapsed" in html
    assert "Plain-English summary" in html
    assert "False-positive assessment" in html
    assert "Adverse media relevance" in html
    assert "Evidence used" in html
    assert "Provider evidence" in html
    assert "Strict pass" in html
    assert "Relaxed pass" in html
    assert "Strict + relaxed" in html
    assert "Provider score unavailable" in html
    assert "Stronger provider match signal; officer verification required." in html
    assert "Broader provider match; review identifiers before clearing." in html
    assert "Surfaced in both provider passes; high review priority." in html
    assert "Provider did not supply numeric score or pass evidence." in html
    assert "No article URL supplied by provider payload." in html
    assert "Provider reference" in html
    assert "Audit trace" in html
    assert "No reportable provider hit recorded" in html
    assert "This is an advisory screening interpretation, not an approval decision." in html
    assert "Draft audit note" not in _extract_function(html, "renderAgent3ScreeningInterpretationPanel")

    panel_body = _extract_function(html, "renderAgent3ScreeningInterpretationPanel")
    render_body = _extract_function(html, "renderScreeningReviewPanel")
    fetch_detail_body = _extract_function(html, "fetchApplicationDetail")
    generate_body = _extract_function(html, "generateAgent3ScreeningInterpretation")
    toggle_body = _extract_function(html, "toggleAgent3ScreeningInterpretation")
    collapsed_body = _extract_function(html, "isAgent3ScreeningInterpretationCollapsed")

    assert "/agent3/screening-interpretation" not in render_body
    assert "/agent3/screening-interpretation" not in fetch_detail_body
    assert "/agent3/screening-interpretation" not in toggle_body
    assert "boApiCall(" not in toggle_body
    assert "boApiCall(" not in collapsed_body
    assert "boApiCall('POST', '/applications/' + appKey + '/agent3/screening-interpretation'" in generate_body
    assert "boApiCall('GET', '/applications/' + appKey + '/agent3/screening-interpretation'" not in generate_body
    assert "Agent recommendation:" in html
    assert "Decision:" not in render_body
    assert panel_body.count("agent3RecommendationBadge(output.recommended_disposition)") == 1
    assert "agent3ScreeningFieldHtml('Recommended disposition'" not in panel_body
    assert panel_body.count("This is an advisory screening interpretation, not an approval decision.") == 1
    assert panel_body.count("agent3ProviderHitsHtml(output)") == 1
    assert panel_body.count("agent3HitStatusCountsHtml(output.hit_row_status_counts)") == 1
    assert "Show full detail" in panel_body
    assert "Stored provider result contains zero reportable hit rows." in panel_body

    # PR-AGENT3-HIT-LEVEL-UI-1: hit-level rendering binds to the backend output
    # (hit_rows / hit_row_status_counts) and stays advisory + display-only.
    assert "agent3HitRowsTableHtml" in html
    assert "agent3HitStatusCountsHtml" in html
    assert "output.hit_rows" in html
    assert "output.hit_row_status_counts" in html
    assert "Hit-by-hit review" in html
    assert "surfaced_by_pass" in html
    assert "agent3ProviderEvidenceCellHtml" in html
    assert "agent3HitEvidenceDetailsHtml" in html
    assert "agent3AuditTraceHtml" in html
    for _status_label in ("Needs review", "Likely false positive", "High-confidence match", "Unavailable"):
        assert _status_label in html
    table_body = _extract_function(html, "agent3HitRowsTableHtml")
    assert "boApiCall(" not in table_body
    assert "/agent3/screening-interpretation" not in table_body
    assert "agent3HitActionButtonsHtml" not in html
    assert "False Positive" not in table_body
    evidence_body = _extract_function(html, "agent3HitEvidenceDetailsHtml")
    assert "Audit trace" not in evidence_body
    comparison_body = _extract_function(html, "buildScreeningComparisonPanel")
    assert "Declared vs Provider Match" in comparison_body
    assert "Provider profile attributes unavailable. Raw provider reference is retained in Audit trace." in comparison_body


def test_backoffice_agent3_provider_evidence_helpers_render_expected_copy():
    if not shutil.which("node"):
        pytest.skip("node is not available for helper rendering check")
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    script = "\n".join([
        textwrap.dedent(
            """
            function escapeHtml(value) {
              return String(value == null ? '' : value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
            }
            const failures = [];
            function assertIncludes(name, haystack, needle) {
              if (!haystack.includes(needle)) failures.push(name + ' missing ' + needle);
            }
            function assertExcludes(name, haystack, needle) {
              if (haystack.includes(needle)) failures.push(name + ' unexpectedly contained ' + needle);
            }
            """
        ),
        _extract_function(html, "agent3SurfacedByPassMeta"),
        _extract_function(html, "agent3ProviderEvidenceCellHtml"),
        _extract_function(html, "agent3TraceRowsHtml"),
        _extract_function(html, "agent3HitEvidenceDetailsHtml"),
        textwrap.dedent(
            """
            assertIncludes('strict', agent3ProviderEvidenceCellHtml({match_score:null, surfaced_by_pass:'strict'}), 'Strict pass');
            assertIncludes('strict help', agent3ProviderEvidenceCellHtml({match_score:null, surfaced_by_pass:'strict'}), 'Stronger provider match signal; officer verification required.');
            assertIncludes('relaxed', agent3ProviderEvidenceCellHtml({match_score:null, surfaced_by_pass:'relaxed'}), 'Relaxed pass');
            assertIncludes('both', agent3ProviderEvidenceCellHtml({match_score:null, surfaced_by_pass:'both'}), 'Strict + relaxed');
            assertIncludes('unavailable', agent3ProviderEvidenceCellHtml({match_score:null}), 'Provider score unavailable');
            assertIncludes('numeric', agent3ProviderEvidenceCellHtml({match_score:88}), '88%');
            const evidence = agent3HitEvidenceDetailsHtml({
              evidence_url:'https://news.example/article',
              evidence_title:'Article title',
              evidence_source:'Example News',
              evidence_snippet:'Article snippet',
              audit_trace:{provider:'complyadvantage', surfaced_by_pass:'strict'}
            });
            assertIncludes('evidence link', evidence, 'href="https://news.example/article"');
            assertIncludes('evidence title', evidence, 'Article title');
            assertExcludes('per-row audit trace', evidence, 'Audit trace');
            assertIncludes('missing url', agent3HitEvidenceDetailsHtml({}), 'No article URL supplied by provider payload.');
            if (failures.length) {
              console.error(failures.join('\\n'));
              process.exit(1);
            }
            """
        ),
    ])
    subprocess.run(["node", "-e", script], check=True, text=True, capture_output=True)


def test_backoffice_agent3_panel_render_dedupes_recommendation_and_advisory():
    if not shutil.which("node"):
        pytest.skip("node is not available for panel rendering check")
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    script = "\n".join([
        textwrap.dedent(
            """
            function escapeHtml(value) {
              return String(value == null ? '' : value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
            }
            const AGENT3_SCREENING_INTERPRETATION_BUSY = false;
            function getAgent3ScreeningInterpretation(app) { return app.output; }
            function isAgent3ScreeningInterpretationCollapsed(app) { return !!app.collapsed; }
            function agent3SeverityBadge(value) { return '<span>Severity: ' + escapeHtml(value) + '</span>'; }
            function agent3ProviderHitsHtml(output) { return '<span>Provider hits: ' + escapeHtml(output.hit_counts.total) + ' total</span>'; }
            function agent3HitStatusCountsHtml(counts) { return counts && counts.needs_review ? '<span>Needs review: ' + counts.needs_review + '</span>' : ''; }
            function agent3ScreeningFieldHtml(label, value) { return '<section><h4>' + escapeHtml(label) + '</h4><p>' + escapeHtml(Array.isArray(value) ? value.join('; ') : value) + '</p></section>'; }
            function agent3EvidenceHtml(_evidence) { return '<div>Evidence used body</div>'; }
            function agent3HitRowsTableHtml(rows) { return '<table><tbody><tr><td>hit rows ' + rows.length + '</td></tr></tbody></table>'; }
            function agent3AuditTraceHtml(_output) { return '<div>Audit trace body</div>'; }
            const failures = [];
            function count(haystack, needle) { return (haystack.match(new RegExp(needle.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'), 'g')) || []).length; }
            function assertEquals(name, actual, expected) {
              if (actual !== expected) failures.push(name + ' expected ' + expected + ' got ' + actual);
            }
            function assertIncludes(name, haystack, needle) {
              if (!haystack.includes(needle)) failures.push(name + ' missing ' + needle);
            }
            function assertExcludes(name, haystack, needle) {
              if (haystack.includes(needle)) failures.push(name + ' unexpectedly contained ' + needle);
            }
            """
        ),
        _extract_function(html, "agent3DisplayRecommendation"),
        _extract_function(html, "agent3RecommendationBadge"),
        _extract_function(html, "agent3ScreeningResultTerminal"),
        _extract_function(html, "renderAgent3ScreeningInterpretationPanel"),
        textwrap.dedent(
            """
            const advisory = 'This is an advisory screening interpretation, not an approval decision.';
            const hitOutput = {
              recommended_disposition: 'Officer review required',
              severity: 'Medium',
              generated_at: '2026-07-03T12:00:00Z',
              ai_notice: '',
              hit_counts: {total: 1},
              hit_rows: [{index: 1}],
              hit_row_status_counts: {needs_review: 1},
              summary: 'Stored screening result contains one provider hit.',
              key_concerns: ['Review the provider evidence.'],
              false_positive_assessment: 'Officer disambiguation required.',
              adverse_media_relevance: 'No adverse media hit.',
              evidence_used: [{source: 'prescreening_data.screening_report'}],
              screening_result_terminal: true
            };
            const hitHtml = renderAgent3ScreeningInterpretationPanel({id: 'app-hit', output: hitOutput});
            assertEquals('hit recommendation once', count(hitHtml, 'Officer review required'), 1);
            assertEquals('hit advisory once', count(hitHtml, advisory), 1);
            assertEquals('hit provider counts once', count(hitHtml, 'Provider hits: 1 total'), 1);
            assertExcludes('hit lower disposition', hitHtml, 'Recommended disposition');
            assertIncludes('hit table rendered', hitHtml, 'hit rows 1');

            const noHitOutput = {
              recommended_disposition: 'No reportable provider hit recorded',
              severity: 'Low',
              generated_at: '2026-07-03T12:00:00Z',
              ai_notice: '',
              hit_counts: {total: 0},
              hit_rows: [],
              hit_row_status_counts: {},
              summary: 'Stored screening result contains zero provider hit rows.',
              key_concerns: ['No provider hits found in stored screening results.'],
              false_positive_assessment: 'No provider false positives to clear.',
              adverse_media_relevance: 'No provider adverse media hits found.',
              evidence_used: [{source: 'prescreening_data.screening_report'}],
              screening_result_terminal: true
            };
            const noHitHtml = renderAgent3ScreeningInterpretationPanel({id: 'app-clean', output: noHitOutput});
            assertEquals('no-hit recommendation once', count(noHitHtml, 'No reportable provider hit recorded'), 1);
            assertEquals('no-hit advisory once', count(noHitHtml, advisory), 1);
            assertEquals('no-hit provider counts once', count(noHitHtml, 'Provider hits: 0 total'), 1);
            assertIncludes('no-hit compact line', noHitHtml, 'Stored provider result contains zero reportable hit rows.');
            assertIncludes('no-hit full detail toggle', noHitHtml, 'Show full detail');
            assertExcludes('no-hit empty hit table omitted', noHitHtml, 'hit rows 0');

            const pendingNoHitOutput = Object.assign({}, noHitOutput, {
              screening_result_terminal: false,
              screening_result_state: 'pending_provider'
            });
            const pendingNoHitHtml = renderAgent3ScreeningInterpretationPanel({id: 'app-pending', output: pendingNoHitOutput});
            assertIncludes('pending no-hit warning', pendingNoHitHtml, 'Stored provider result is not a terminal clean result');
            assertExcludes('pending no-hit not green', pendingNoHitHtml, '#ecfdf5');
            if (failures.length) {
              console.error(failures.join('\\n'));
              process.exit(1);
            }
            """
        ),
    ])
    subprocess.run(["node", "-e", script], check=True, text=True, capture_output=True)
