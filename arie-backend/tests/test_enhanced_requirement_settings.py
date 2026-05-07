import json
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import requests
import tornado.httpserver
import tornado.ioloop
import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _sync_db_path(path):
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def enhanced_req_api_server():
    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_enhanced_req_{os.getpid()}.db")
    _sync_db_path(db_path)
    try:
        os.unlink(db_path)
    except OSError:
        pass

    from db import get_db, init_db, seed_initial_data

    init_db()
    conn = get_db()
    seed_initial_data(conn)
    conn.commit()
    conn.close()

    import server as server_module

    app = server_module.make_app()
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


def _headers(role="admin", token_type="officer"):
    from auth import create_token

    user_id = {
        "admin": "admin001",
        "sco": "sco001",
        "co": "co001",
        "analyst": "analyst001",
        "client": "client001",
    }.get(role, role)
    token = create_token(user_id, role, f"Test {role}", token_type)
    return {"Authorization": f"Bearer {token}"}


def _new_rule_payload(suffix=None):
    suffix = suffix or uuid.uuid4().hex[:8]
    return {
        "trigger_key": f"custom_trigger_{suffix}",
        "trigger_label": "Custom Trigger",
        "trigger_category": "custom",
        "requirement_key": f"custom_requirement_{suffix}",
        "requirement_label": "Custom Enhanced Requirement",
        "requirement_description": "Collect a custom enhanced requirement.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "application",
        "blocking_approval": True,
        "waivable": True,
        "waiver_roles": ["admin", "sco"],
        "mandatory": True,
        "active": True,
        "sort_order": 900,
        "client_safe_label": "Additional evidence",
        "client_safe_description": "Please provide additional evidence.",
        "internal_notes": "Test rule",
    }


def test_default_rules_seed_idempotently(enhanced_req_api_server):
    from db import get_db
    from enhanced_requirements import default_rule_rows, seed_default_enhanced_requirement_rules

    conn = get_db()
    before = conn.execute("SELECT COUNT(*) as c FROM enhanced_requirement_rules").fetchone()["c"]
    assert before >= len(default_rule_rows())

    inserted = seed_default_enhanced_requirement_rules(conn)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) as c FROM enhanced_requirement_rules").fetchone()["c"]
    dupes = conn.execute("""
        SELECT trigger_key, requirement_key, COUNT(*) as c
        FROM enhanced_requirement_rules
        GROUP BY trigger_key, requirement_key
        HAVING COUNT(*) > 1
    """).fetchall()
    conn.close()

    assert inserted == 0
    assert after == before
    assert dupes == []


def test_list_endpoint_returns_seeded_rules_and_read_roles(enhanced_req_api_server):
    admin_resp = requests.get(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements",
        headers=_headers("admin"),
        timeout=5,
    )
    assert admin_resp.status_code == 200, admin_resp.text
    body = admin_resp.json()
    keys = {(r["trigger_key"], r["requirement_key"]) for r in body["rules"]}
    assert ("high_or_very_high_risk", "company_bank_reference") in keys
    assert ("pep", "mandatory_senior_review") in keys
    assert "high_or_very_high_risk" in body["grouped"]

    co_resp = requests.get(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements",
        headers=_headers("co"),
        timeout=5,
    )
    assert co_resp.status_code == 200


def test_co_can_read_but_cannot_modify_enhanced_requirements(enhanced_req_api_server):
    read_resp = requests.get(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements",
        headers=_headers("co"),
        timeout=5,
    )
    assert read_resp.status_code == 200, read_resp.text
    rule_id = read_resp.json()["rules"][0]["id"]

    create_resp = requests.post(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements",
        json=_new_rule_payload(),
        headers=_headers("co"),
        timeout=5,
    )
    assert create_resp.status_code == 403

    update_resp = requests.patch(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements/{rule_id}",
        json={"requirement_label": "CO must not update policy"},
        headers=_headers("co"),
        timeout=5,
    )
    assert update_resp.status_code == 403

    disable_resp = requests.post(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements/{rule_id}/disable",
        headers=_headers("co"),
        timeout=5,
    )
    assert disable_resp.status_code == 403

    enable_resp = requests.post(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements/{rule_id}/enable",
        headers=_headers("co"),
        timeout=5,
    )
    assert enable_resp.status_code == 403


def test_rule_serialization_accepts_text_or_native_json_fields():
    from enhanced_requirements import serialize_rule

    base = {
        "id": 1,
        "trigger_key": "pep",
        "trigger_label": "PEP",
        "trigger_category": "screening",
        "requirement_key": "pep_sow_evidence",
        "requirement_label": "Source of Wealth evidence",
        "requirement_description": "",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "screening_subject",
        "blocking_approval": 1,
        "waivable": 1,
        "mandatory": 1,
        "active": 1,
        "sort_order": 10,
    }

    text_backed = dict(base, waiver_roles='["admin", "sco"]', applies_when='{"risk_level":"high"}')
    native_backed = dict(base, waiver_roles=["admin", "sco"], applies_when={"risk_level": "high"})

    assert serialize_rule(text_backed)["waiver_roles"] == ["admin", "sco"]
    assert serialize_rule(text_backed)["applies_when"] == {"risk_level": "high"}
    assert serialize_rule(native_backed)["waiver_roles"] == ["admin", "sco"]
    assert serialize_rule(native_backed)["applies_when"] == {"risk_level": "high"}


def test_admin_can_create_update_disable_enable_and_audit(enhanced_req_api_server):
    payload = _new_rule_payload()
    create_resp = requests.post(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements",
        json=payload,
        headers=_headers("admin"),
        timeout=5,
    )
    assert create_resp.status_code == 201, create_resp.text
    rule = create_resp.json()["rule"]
    rule_id = rule["id"]

    update_resp = requests.patch(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements/{rule_id}",
        json={"requirement_label": "Updated Enhanced Requirement", "audience": "both"},
        headers=_headers("sco"),
        timeout=5,
    )
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["rule"]["requirement_label"] == "Updated Enhanced Requirement"
    assert update_resp.json()["rule"]["audience"] == "both"

    disable_resp = requests.post(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements/{rule_id}/disable",
        headers=_headers("admin"),
        timeout=5,
    )
    assert disable_resp.status_code == 200, disable_resp.text
    assert disable_resp.json()["rule"]["active"] is False

    enable_resp = requests.post(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements/{rule_id}/enable",
        headers=_headers("admin"),
        timeout=5,
    )
    assert enable_resp.status_code == 200, enable_resp.text
    assert enable_resp.json()["rule"]["active"] is True

    from db import get_db

    conn = get_db()
    rows = conn.execute(
        """
        SELECT action, detail, before_state, after_state
        FROM audit_log
        WHERE action LIKE 'enhanced_requirement_rule.%'
        ORDER BY id
        """
    ).fetchall()
    conn.close()

    actions = [row["action"] for row in rows]
    assert "enhanced_requirement_rule.created" in actions
    assert "enhanced_requirement_rule.updated" in actions
    assert "enhanced_requirement_rule.disabled" in actions
    assert "enhanced_requirement_rule.enabled" in actions
    stateful = [row for row in rows if row["action"] in (
        "enhanced_requirement_rule.updated",
        "enhanced_requirement_rule.disabled",
        "enhanced_requirement_rule.enabled",
    )]
    assert stateful
    assert all(row["before_state"] and row["after_state"] for row in stateful)
    detail = json.loads(rows[-1]["detail"])
    assert detail["rule_id"] is not None
    assert detail["actor"] == "admin001"


def test_analyst_cannot_modify_and_client_cannot_access(enhanced_req_api_server):
    analyst_resp = requests.post(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements",
        json=_new_rule_payload(),
        headers=_headers("analyst"),
        timeout=5,
    )
    assert analyst_resp.status_code == 403

    client_resp = requests.get(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements",
        headers=_headers("client", token_type="client"),
        timeout=5,
    )
    assert client_resp.status_code == 403


def test_invalid_enum_and_duplicate_keys_are_rejected(enhanced_req_api_server):
    invalid = _new_rule_payload()
    invalid["audience"] = "external"
    resp = requests.post(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements",
        json=invalid,
        headers=_headers("admin"),
        timeout=5,
    )
    assert resp.status_code == 400
    assert "audience" in resp.text

    duplicate = _new_rule_payload()
    duplicate["trigger_key"] = "pep"
    duplicate["requirement_key"] = "mandatory_senior_review"
    dup_resp = requests.post(
        f"{enhanced_req_api_server}/api/settings/enhanced-requirements",
        json=duplicate,
        headers=_headers("admin"),
        timeout=5,
    )
    assert dup_resp.status_code == 409


def test_backoffice_enhanced_requirements_view_is_wired():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "arie-backoffice.html").read_text(encoding="utf-8")

    assert 'id="view-enhanced-requirements"' in html
    assert "loadEnhancedRequirementRules" in html
    assert "renderEnhancedRequirementRules" in html
    assert "showEnhancedRequirementForm" in html
    assert "role-enhanced-settings" in html
    assert "/settings/enhanced-requirements" in html


def test_backoffice_application_enhanced_requirements_visibility_is_wired():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "arie-backoffice.html").read_text(encoding="utf-8")
    portal_html = (repo_root / "arie-portal.html").read_text(encoding="utf-8")

    assert "Enhanced Review Requirements" in html
    assert 'id="detail-enhanced-requirements-section"' in html
    assert 'id="detail-enhanced-requirements-container"' in html
    assert "loadApplicationEnhancedRequirements" in html
    assert "renderApplicationEnhancedRequirements" in html
    assert "refreshApplicationEnhancedRequirements" in html
    assert "saveApplicationEnhancedRequirement" in html
    assert "waiveApplicationEnhancedRequirement" in html
    assert "requestApplicationEnhancedRequirementFromClient" in html
    assert "canViewApplicationEnhancedRequirements" in html
    assert "canUpdateApplicationEnhancedRequirements" in html
    assert "canWaiveApplicationEnhancedRequirements" in html
    assert "canRequestApplicationEnhancedRequirementsFromClient" in html
    assert "enhancedRequirementRequestEligible" in html
    assert "canManageEnhancedRequirements()" in html
    assert "/applications/' + appKey + '/enhanced-requirements" in html
    assert "/applications/' + encodeURIComponent(currentApp.id) + '/enhanced-requirements/generate" in html
    assert "/applications/' + encodeURIComponent(currentApp.id) + '/enhanced-requirements/' + encodeURIComponent(requirementId)" in html
    assert "/applications/' + encodeURIComponent(currentApp.id) + '/enhanced-requirements/' + encodeURIComponent(requirementId) + '/request" in html
    assert "boApiCall('PATCH'" in html
    assert "boApiCall('POST'" in html
    assert "generation_source: 'manual_backoffice_refresh'" in html
    assert "Back-office actions" in html
    assert "Internal review notes" in html
    assert "Waiver reason" in html
    assert "Save update" in html
    assert "Request from client" in html
    assert "Portal and RMI exposure are deferred to a later step." in html
    assert "['client','both'].indexOf(audience) >= 0" in html
    assert "['generated','under_review','rejected'].indexOf(status) >= 0" in html
    assert "Only admins and senior compliance officers can waive enhanced requirements" in html
    assert "No enhanced requirements generated for this application." in html
    assert "Enhanced requirement configuration is incomplete. Requirements may not be fully generated." in html

    block = html.split("// APPLICATION ENHANCED REVIEW REQUIREMENTS — back-office display/actions", 1)[1]
    block = block.split("function renderUsers()", 1)[0]
    assert "/enhanced-requirements" in block
    assert "/rmi" not in block.lower()
    assert "/notify" not in block
    assert "/decision" not in block
    assert "/memo" not in block
    assert "/edd/" not in block.lower()
    assert "/documents" not in block
    assert "/supervisor" not in block

    assert "/portal/applications/' + encodeURIComponent(currentApplicationId) + '/enhanced-requirements" in portal_html
    assert "Additional Information Required" in portal_html
    assert "renderPortalEnhancedRequirements" in portal_html
    assert "loadPortalEnhancedRequirements" in portal_html

    portal_section = portal_html.split('id="additional-info-required-card"', 1)[1]
    portal_section = portal_section.split("<!-- Section A: Corporate Documents -->", 1)[0]
    assert "To complete your review, please provide the additional information below." in portal_section
    for forbidden in (
        "EDD",
        "Enhanced Due Diligence",
        "high risk",
        "screening concern",
        "sanctions concern",
        "PEP concern",
    ):
        assert forbidden.lower() not in portal_section.lower()

    portal_logic = portal_html.split("function portalEnhancedRequirementTone", 1)[1]
    portal_logic = portal_logic.split("function rmiItemTone", 1)[0]
    assert "/portal/applications/" in portal_logic
    assert "apiCall('GET', '/applications/" not in portal_logic
    assert "apiCall('POST', '/applications/" not in portal_logic
    assert "apiCall('PATCH', '/applications/" not in portal_logic
    assert "/rmi" not in portal_logic.lower()
    assert "/notify" not in portal_logic
    assert "/decision" not in portal_logic
    assert "/memo" not in portal_logic
    assert "/edd/" not in portal_logic.lower()
    assert "/screening" not in portal_logic
    assert "Required" in portal_logic
    assert "Submitted" in portal_logic
    assert "Under review" in portal_logic
    assert "Additional information needed" in portal_logic
