import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
import requests as http_requests
import tornado.httpserver
import tornado.ioloop


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"


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
    return {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": "2026-06-30T10:00:00+00:00",
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
        "screening_valid_until": "2026-09-28T10:00:00+00:00",
    }


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


def test_agent3_generates_from_stored_screening_without_provider_calls(
    agent3_api_server,
    db,
    monkeypatch,
):
    import server

    monkeypatch.setenv("ANTHROPIC_API_KEY", "would-be-live-ai-key")
    monkeypatch.setattr(server, "run_full_screening", _fail_provider_call)
    monkeypatch.setattr(server, "screen_sumsub_aml", _fail_provider_call)
    monkeypatch.setattr(server, "lookup_opencorporates", _fail_provider_call)

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
    assert "AI narrative unavailable" in output["ai_notice"]
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
):
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
    assert "Officer decision required. Agent 3 does not approve, reject, or close screening reviews." in html
    assert "Plain-English summary" in html
    assert "False-positive assessment" in html
    assert "Adverse media relevance" in html
    assert "Recommended disposition" in html
    assert "Draft audit note" in html
    assert "Evidence used" in html

    render_body = _extract_function(html, "renderScreeningReviewPanel")
    fetch_detail_body = _extract_function(html, "fetchApplicationDetail")
    generate_body = _extract_function(html, "generateAgent3ScreeningInterpretation")

    assert "/agent3/screening-interpretation" not in render_body
    assert "/agent3/screening-interpretation" not in fetch_detail_body
    assert "boApiCall('POST', '/applications/' + appKey + '/agent3/screening-interpretation'" in generate_body
    assert "boApiCall('GET', '/applications/' + appKey + '/agent3/screening-interpretation'" not in generate_body
    assert "Agent recommendation:" in html
    assert "Decision:" not in render_body
