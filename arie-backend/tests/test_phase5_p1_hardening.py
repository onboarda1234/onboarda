import json
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
import tornado.httpserver
import tornado.ioloop


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def phase5_api_server():
    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_phase5_test_{os.getpid()}.db")
    os.environ["DB_PATH"] = db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass

    from db import get_db, init_db

    init_db()
    conn = get_db()
    conn.commit()
    conn.close()

    import server as server_module

    server_module.HAS_S3 = False
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


def _officer_headers(role="admin", sub="admin001"):
    from auth import create_token

    token = create_token(sub, role, "Phase 5 Officer", "officer")
    return {"Authorization": f"Bearer {token}"}


def _client_headers(client_id):
    from auth import create_token

    token = create_token(client_id, "client", "Phase 5 Client", "client")
    return {"Authorization": f"Bearer {token}"}


def _seed_app(app_id="phase5_app", ref="ARF-2026-P5-001", status="in_review"):
    from db import get_db

    client_id = f"client_{app_id}"
    conn = get_db()
    conn.execute("DELETE FROM edd_findings WHERE edd_case_id IN (SELECT id FROM edd_cases WHERE application_id=?)", (app_id,))
    conn.execute("DELETE FROM edd_cases WHERE application_id=?", (app_id,))
    conn.execute("DELETE FROM rmi_request_items WHERE request_id IN (SELECT id FROM rmi_requests WHERE application_id=?)", (app_id,))
    conn.execute("DELETE FROM rmi_requests WHERE application_id=?", (app_id,))
    conn.execute("DELETE FROM documents WHERE application_id=?", (app_id,))
    conn.execute("DELETE FROM client_notifications WHERE application_id=?", (app_id,))
    conn.execute("DELETE FROM applications WHERE id=?", (app_id,))
    conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"{client_id}@example.com", "hash", "Phase 5 Ltd"),
    )
    conn.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, ref, client_id, "Phase 5 Ltd", "Mauritius", "Technology", "SME", status, "MEDIUM", 42),
    )
    conn.commit()
    conn.close()
    return client_id, app_id, ref


def _create_edd_case(app_id, *, stage="analysis", sla_due_at=None, senior_reviewer="sco001"):
    from db import get_db

    conn = get_db()
    conn.execute(
        """INSERT INTO edd_cases
           (application_id, client_name, risk_level, risk_score, stage, assigned_officer, senior_reviewer, sla_due_at, priority, edd_notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, "Phase 5 Ltd", "HIGH", 80, stage, "admin001", senior_reviewer, sla_due_at, "high", "[]"),
    )
    case_id = conn.execute("SELECT id FROM edd_cases WHERE application_id=? ORDER BY id DESC LIMIT 1", (app_id,)).fetchone()["id"]
    conn.commit()
    conn.close()
    return case_id


def _create_rmi_item(app_id, *, doc_type="reg_sh", label="Replacement required for entity:reg_sh",
                     item_status="requested", request_status="open", document_id=None):
    from db import get_db

    conn = get_db()
    app = conn.execute("SELECT id, client_id FROM applications WHERE id=?", (app_id,)).fetchone()
    request_id = uuid.uuid4().hex[:16]
    item_id = uuid.uuid4().hex[:16]
    conn.execute(
        """INSERT INTO rmi_requests
           (id, application_id, client_id, status, reason, deadline, created_by, created_by_name, fulfilled_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            request_id,
            app_id,
            app["client_id"] if app else "",
            request_status,
            "Missing required document",
            "2026-12-31",
            "admin001",
            "Phase 5 Officer",
            datetime.now(timezone.utc).isoformat() if request_status == "fulfilled" else None,
        ),
    )
    conn.execute(
        """INSERT INTO rmi_request_items
           (id, request_id, doc_type, label, description, status, document_id, uploaded_at, reviewed_at)
           VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)""",
        (
            item_id,
            request_id,
            doc_type,
            label,
            item_status,
            document_id,
            datetime.now(timezone.utc).isoformat() if document_id else None,
            datetime.now(timezone.utc).isoformat() if item_status in ("accepted", "rejected") else None,
        ),
    )
    conn.commit()
    conn.close()
    return request_id, item_id


def _insert_rmi_document(app_id, item_id, *, doc_type="reg_sh", slot_key=None, status="verified"):
    from db import get_db

    conn = get_db()
    doc_id = uuid.uuid4().hex[:16]
    verified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        """INSERT INTO documents
           (id, application_id, doc_type, doc_name, file_path, slot_key,
            verification_status, verification_results, verified_at, review_status,
            review_comment, reviewed_by, reviewer_role, reviewed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted',
                   'Accepted RMI replacement for workflow continuation.', 'admin001', 'admin', datetime('now'))""",
        (
            doc_id,
            app_id,
            doc_type,
            f"{doc_type}.pdf",
            f"/tmp/{app_id}/{doc_type}.pdf",
            slot_key or f"rmi:{item_id}",
            status,
            json.dumps({"overall": status, "checks": [{"result": "pass"}], "verified_at": verified_at}),
            verified_at,
        ),
    )
    conn.execute(
        """INSERT INTO agent_executions
           (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
           VALUES (?, ?, 'verify_document', 1, ?, ?, 0)""",
        (app_id, doc_id, status, json.dumps([{"result": "pass"}])),
    )
    conn.commit()
    conn.close()
    return doc_id


def _insert_edd_findings(case_id):
    from db import get_db

    conn = get_db()
    conn.execute(
        """INSERT INTO edd_findings
           (edd_case_id, findings_summary, key_concerns, mitigating_evidence, rationale, recommended_outcome)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            case_id,
            "EDD findings support senior review.",
            json.dumps(["PEP exposure reviewed"]),
            json.dumps(["Source of wealth evidence obtained"]),
            "Findings reviewed and residual risk is acceptable.",
            "approve_with_conditions",
        ),
    )
    conn.commit()
    conn.close()


def test_document_type_validation_rejects_unknown_upload(phase5_api_server):
    client_id, app_id, _ = _seed_app(app_id="phase5_doc_type", ref="ARF-2026-P5-002", status="rmi_sent")

    resp = requests.post(
        f"{phase5_api_server}/api/applications/{app_id}/documents?doc_type=totally_unknown_type",
        headers=_client_headers(client_id),
        files={"file": ("evidence.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=5,
    )

    assert resp.status_code == 400
    assert "Invalid doc_type" in resp.text


def test_legacy_general_doc_type_maps_to_supporting_document(phase5_api_server):
    client_id, app_id, _ = _seed_app(app_id="phase5_doc_general", ref="ARF-2026-P5-012", status="rmi_sent")

    resp = requests.post(
        f"{phase5_api_server}/api/applications/{app_id}/documents?doc_type=general",
        headers=_client_headers(client_id),
        files={"file": ("general.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=5,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["doc_type"] == "supporting_document"


def test_rmi_replacement_upload_maps_to_canonical_required_slot(phase5_api_server):
    from db import get_db

    client_id, app_id, _ = _seed_app(app_id="phase5_rmi_upload_slot", ref="ARF-2026-P5-020", status="rmi_sent")
    request_id, item_id = _create_rmi_item(app_id)

    resp = requests.post(
        f"{phase5_api_server}/api/applications/{app_id}/documents?doc_type=reg_sh&rmi_item_id={item_id}",
        headers=_client_headers(client_id),
        files={"file": ("shareholder-register.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=5,
    )

    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["slot_key"] == "entity:reg_sh"
    assert payload["canonical_slot_key"] == "entity:reg_sh"
    assert payload["rmi_trace_slot_key"] == f"rmi:{item_id}"
    assert payload["rmi_item_id"] == item_id

    conn = get_db()
    item = conn.execute("SELECT status, document_id FROM rmi_request_items WHERE id=?", (item_id,)).fetchone()
    doc = conn.execute("SELECT slot_key FROM documents WHERE id=?", (payload["id"],)).fetchone()
    audit = conn.execute(
        "SELECT detail FROM audit_log WHERE target=? AND action='RMI Replacement Uploaded' ORDER BY timestamp DESC, id DESC LIMIT 1",
        ("ARF-2026-P5-020",),
    ).fetchone()
    conn.close()
    assert item["status"] == "uploaded"
    assert item["document_id"] == payload["id"]
    assert doc["slot_key"] == "entity:reg_sh"
    assert audit and item_id in audit["detail"] and request_id in audit["detail"]


def test_unfulfilled_rmi_sent_cannot_continue(phase5_api_server):
    _, app_id, _ = _seed_app(app_id="phase5_rmi_unfulfilled", ref="ARF-2026-P5-021", status="rmi_sent")
    _create_rmi_item(app_id)

    resp = requests.patch(
        f"{phase5_api_server}/api/applications/{app_id}",
        json={"status": "kyc_documents"},
        headers=_officer_headers(),
        timeout=5,
    )

    assert resp.status_code == 409
    assert "All active RMI items must be uploaded and officer-accepted" in resp.text


def test_fulfilled_rmi_sent_can_continue_to_kyc_documents_and_audits(phase5_api_server):
    from db import get_db

    _, app_id, ref = _seed_app(app_id="phase5_rmi_continue", ref="ARF-2026-P5-022", status="rmi_sent")
    request_id, item_id = _create_rmi_item(app_id, item_status="accepted", request_status="fulfilled")
    doc_id = _insert_rmi_document(app_id, item_id)
    conn = get_db()
    conn.execute("UPDATE rmi_request_items SET document_id=? WHERE id=?", (doc_id, item_id))
    conn.commit()
    conn.close()

    resp = requests.patch(
        f"{phase5_api_server}/api/applications/{app_id}",
        json={"status": "kyc_documents"},
        headers=_officer_headers(),
        timeout=5,
    )

    assert resp.status_code == 200, resp.text
    conn = get_db()
    app = conn.execute("SELECT status FROM applications WHERE id=?", (app_id,)).fetchone()
    audit = conn.execute(
        "SELECT detail, before_state, after_state FROM audit_log WHERE target=? AND action='RMI Continuation' ORDER BY timestamp DESC, id DESC LIMIT 1",
        (ref,),
    ).fetchone()
    conn.close()
    assert app["status"] == "kyc_documents"
    assert audit and request_id in audit["detail"]
    assert '"status": "rmi_sent"' in audit["before_state"]
    assert '"status": "kyc_documents"' in audit["after_state"]


def test_rmi_sent_direct_kyc_submitted_still_requires_document_gate(phase5_api_server):
    _, app_id, _ = _seed_app(app_id="phase5_rmi_direct_gate", ref="ARF-2026-P5-023", status="rmi_sent")
    _, item_id = _create_rmi_item(app_id, item_status="accepted", request_status="fulfilled")
    doc_id = _insert_rmi_document(app_id, item_id)
    from db import get_db

    conn = get_db()
    conn.execute("UPDATE rmi_request_items SET document_id=? WHERE id=?", (doc_id, item_id))
    conn.commit()
    conn.close()

    resp = requests.patch(
        f"{phase5_api_server}/api/applications/{app_id}",
        json={"status": "kyc_submitted"},
        headers=_officer_headers(),
        timeout=5,
    )

    assert resp.status_code == 409
    assert "Document evidence gate failed for RMI continuation" in resp.text


def test_rmi_sent_rejects_arbitrary_status_transition(phase5_api_server):
    _, app_id, _ = _seed_app(app_id="phase5_rmi_arbitrary", ref="ARF-2026-P5-024", status="rmi_sent")

    # Terminal targets (approved/rejected) are intercepted earlier by the
    # can_decide terminal guard (PR-APPROVAL-AUTHORITY-MATRIX-1), so use a
    # non-terminal invalid target to exercise the transition-map validation path.
    resp = requests.patch(
        f"{phase5_api_server}/api/applications/{app_id}",
        json={"status": "pricing_review"},
        headers=_officer_headers(),
        timeout=5,
    )

    assert resp.status_code == 400
    assert "Invalid workflow transition" in resp.text


def test_rmi_custom_items_use_supporting_document_and_invalid_explicit_type_rejected():
    import server

    items = server._normalize_rmi_items({"documents_list": ["Custom source of wealth narrative"]})
    assert items == [{
        "doc_type": "supporting_document",
        "label": "Custom source of wealth narrative",
        "description": "",
    }]

    errors = []
    assert server._normalize_rmi_items(
        {"rmi_items": [{"doc_type": "bad<script>", "label": "Bad type"}]},
        errors=errors,
    ) == []
    assert errors and "Invalid RMI doc_type" in errors[0]


def test_provider_errors_are_sanitized_before_storage():
    from provider_errors import sanitize_provider_error
    from screening import _safe_future_result

    class FailingFuture:
        def result(self, timeout=None):
            raise RuntimeError(
                "https://provider.example/check?token=super-secret&api_key=abc failed Authorization=Bearer abc.def"
            )

    degraded, error = _safe_future_result(FailingFuture(), 1, "sumsub", "Phase 5 Ltd")

    assert degraded["error"] == "Provider temporarily unavailable"
    assert "super-secret" not in degraded["provider_error"]
    assert "abc.def" not in degraded["provider_error"]
    assert "api_key=[redacted]" in sanitize_provider_error("api_key=abc")
    assert "super-secret" not in error
    assert "/check" not in degraded["provider_error"]

    class BadStr:
        def __str__(self):
            raise RuntimeError("cannot stringify")

    assert sanitize_provider_error(BadStr()) == "unprintable provider error"


def test_edd_requires_sla_and_findings_before_senior_review(phase5_api_server):
    _, app_id, _ = _seed_app(app_id="phase5_edd_review", ref="ARF-2026-P5-003")
    future_due = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    case_id = _create_edd_case(app_id, stage="analysis", sla_due_at=future_due)

    missing_findings = requests.patch(
        f"{phase5_api_server}/api/edd/cases/{case_id}",
        json={"stage": "pending_senior_review"},
        headers=_officer_headers(),
        timeout=5,
    )
    assert missing_findings.status_code == 400
    assert "Structured EDD findings" in missing_findings.text

    _insert_edd_findings(case_id)
    accepted = requests.patch(
        f"{phase5_api_server}/api/edd/cases/{case_id}",
        json={"stage": "pending_senior_review", "senior_reviewer": "sco001"},
        headers=_officer_headers(),
        timeout=5,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["stage"] == "pending_senior_review"


def test_edd_findings_require_recommendation_plus_evidence(phase5_api_server):
    _, app_id, _ = _seed_app(app_id="phase5_edd_sparse", ref="ARF-2026-P5-013")
    future_due = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    case_id = _create_edd_case(app_id, stage="analysis", sla_due_at=future_due)

    from db import get_db

    conn = get_db()
    conn.execute(
        """INSERT INTO edd_findings
           (edd_case_id, findings_summary, key_concerns, mitigating_evidence, rationale, recommended_outcome)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (case_id, "", "[]", "[]", "", "approve"),
    )
    conn.commit()
    conn.close()

    sparse = requests.patch(
        f"{phase5_api_server}/api/edd/cases/{case_id}",
        json={"stage": "pending_senior_review"},
        headers=_officer_headers(),
        timeout=5,
    )
    assert sparse.status_code == 400
    assert "Structured EDD findings" in sparse.text

    conn = get_db()
    conn.execute(
        "UPDATE edd_findings SET key_concerns=? WHERE edd_case_id=?",
        (json.dumps(["Source of wealth gap reviewed"]), case_id),
    )
    conn.commit()
    conn.close()

    accepted = requests.patch(
        f"{phase5_api_server}/api/edd/cases/{case_id}",
        json={"stage": "pending_senior_review", "senior_reviewer": "sco001"},
        headers=_officer_headers(),
        timeout=5,
    )
    assert accepted.status_code == 200, accepted.text


def test_edd_closure_requires_sla_breach_reason_when_overdue(phase5_api_server):
    _, app_id, _ = _seed_app(app_id="phase5_edd_overdue", ref="ARF-2026-P5-004")
    past_due = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    case_id = _create_edd_case(app_id, stage="pending_senior_review", sla_due_at=past_due)
    _insert_edd_findings(case_id)

    blocked = requests.patch(
        f"{phase5_api_server}/api/edd/cases/{case_id}",
        json={"stage": "edd_approved", "decision_reason": "EDD approved after senior review."},
        headers=_officer_headers("sco", "sco001"),
        timeout=5,
    )
    assert blocked.status_code == 400
    assert "sla_breach_reason" in blocked.text

    accepted = requests.patch(
        f"{phase5_api_server}/api/edd/cases/{case_id}",
        json={
            "stage": "edd_approved",
            "decision_reason": "EDD approved after senior review.",
            "sla_breach_reason": "Delay caused by late bank evidence and reviewed by SCO.",
        },
        headers=_officer_headers("sco", "sco001"),
        timeout=5,
    )
    assert accepted.status_code == 200, accepted.text


def test_risk_labels_and_final_memo_status_are_canonical(phase5_api_server):
    _, app_id, ref = _seed_app(app_id="phase5_labels", ref="ARF-2026-P5-005")
    from db import get_db

    conn = get_db()
    conn.execute(
        """UPDATE applications SET risk_level='HIGH', final_risk_level='VERY_HIGH'
           WHERE id=?""",
        (app_id,),
    )
    conn.execute(
        """INSERT INTO compliance_memos
           (application_id, version, memo_data, review_status, validation_status, blocked, quality_score, memo_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            app_id,
            1,
            json.dumps({"sections": {}, "metadata": {}}),
            "approved",
            "pass_with_fixes",
            0,
            8.1,
            "v1",
        ),
    )
    conn.commit()
    conn.close()

    resp = requests.get(
        f"{phase5_api_server}/api/applications/{ref}",
        headers=_officer_headers(),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["risk_level_label"] == "High Risk"
    assert body["final_risk_level_label"] == "Very High Risk"
    assert body["latest_memo"]["final_status"] == "approved_with_findings"
    assert body["latest_memo_data"]["final_status"] == "approved_with_findings"


def test_memo_final_status_whitelists_unknown_values():
    import server

    assert server._memo_final_status({
        "review_status": "approved",
        "validation_status": "fail",
        "blocked": 0,
    }) == "approved"
    assert server._memo_final_status({
        "review_status": "draft",
        "validation_status": "fail",
        "blocked": 0,
    }) == "validation_failed"
    assert server._memo_final_status({
        "review_status": "pending_review",
        "validation_status": "pending",
        "blocked": 0,
    }) == "draft"


def test_upload_accept_attributes_match_server_allowlist():
    from security_hardening import FileUploadValidator

    assert FileUploadValidator.ALLOWED_EXTENSIONS == {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"}
    allowed = set(FileUploadValidator.ALLOWED_EXTENSIONS)
    repo_root = Path(__file__).resolve().parents[2]
    for path in (repo_root / "arie-portal.html", repo_root / "arie-backoffice.html"):
        html = path.read_text()
        for accept in __import__("re").findall(r'accept="([^"]+)"', html):
            for token in [part.strip() for part in accept.split(",") if part.strip().startswith(".")]:
                assert token in allowed, f"{path}:{token} is accepted by UI but rejected by server"
