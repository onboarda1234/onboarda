"""
Tests for Sumsub applicant creation and screening integrity hardening.

Covers:
  1. Empty applicantId from 2xx response ⇒ api_status=error, not success
  2. create_applicant non-2xx failure ⇒ api_status=error, not pending
  3. Missing applicant row (no applicant_id) ⇒ screening api_status=error, not pending
  4. source=sumsub only when real applicant/check exists
  5. Simulated screening explicitly labeled as simulated
  6. Audit entries: "KYC Applicant Created" only on real ID; failure logged
  7. request_check structured logging (success, failure, 409 fallback)
  8. Gate 5 not weakened — still blocks pending/error/simulated
  9. Diagnostic endpoint returns per-person data
"""
import json
import os
import sys
import sqlite3
import tempfile
import uuid

# Ensure DB_PATH is set before production-module import triggers config.py.
if "DB_PATH" not in os.environ:
    os.environ["DB_PATH"] = os.path.join(
        tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db"
    )
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch
import pytest

# ══════════════════════════════════════════════════════════════════
# 1. create_applicant: empty applicantId ⇒ error
# ══════════════════════════════════════════════════════════════════

def test_create_applicant_empty_id_from_2xx():
    """If Sumsub returns 2xx but no applicantId, treat as error."""
    from sumsub_client import SumsubClient

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True
    client.level_name = "test-level"

    # Mock _request_with_retry to return 200 with empty id
    client._request_with_retry = MagicMock(return_value=(
        200,
        {"id": "", "review": {"reviewStatus": "init"}, "inspectionId": "abc"},
        ""
    ))

    result = client.create_applicant(external_user_id="user123")
    assert result["api_status"] == "error"
    assert result["applicant_id"] == ""
    assert "empty applicantId" in result.get("error", "").lower() or "empty" in result.get("note", "").lower()


def test_create_applicant_none_id_from_2xx():
    """If Sumsub returns 2xx with None id, treat as error."""
    from sumsub_client import SumsubClient

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True
    client.level_name = "test-level"

    client._request_with_retry = MagicMock(return_value=(
        201,
        {"id": None, "review": {"reviewStatus": "init"}},
        ""
    ))

    result = client.create_applicant(external_user_id="user456")
    assert result["api_status"] == "error"
    assert result["applicant_id"] == ""


def test_create_applicant_whitespace_id_from_2xx():
    """If Sumsub returns 2xx with whitespace-only id, treat as error."""
    from sumsub_client import SumsubClient

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True
    client.level_name = "test-level"

    client._request_with_retry = MagicMock(return_value=(
        200,
        {"id": "   ", "review": {"reviewStatus": "init"}},
        ""
    ))

    result = client.create_applicant(external_user_id="user789")
    assert result["api_status"] == "error"
    assert result["applicant_id"] == ""


def test_create_applicant_valid_id_success():
    """If Sumsub returns 2xx with a real applicantId, success."""
    from sumsub_client import SumsubClient

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True
    client.level_name = "test-level"

    client._request_with_retry = MagicMock(return_value=(
        200,
        {"id": "abc123real", "review": {"reviewStatus": "init"}, "inspectionId": "insp1"},
        ""
    ))

    result = client.create_applicant(external_user_id="user_ok")
    assert result["api_status"] == "live"
    assert result["applicant_id"] == "abc123real"
    assert result["source"] == "sumsub"


# ══════════════════════════════════════════════════════════════════
# 2. create_applicant non-2xx failure ⇒ error, not pending
# ══════════════════════════════════════════════════════════════════

def test_create_applicant_500_returns_error():
    """Non-2xx from Sumsub create_applicant ⇒ api_status=error when configured."""
    from sumsub_client import SumsubClient

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True
    client.level_name = "test-level"

    client._request_with_retry = MagicMock(return_value=(
        500,
        {},
        "Internal Server Error"
    ))

    result = client.create_applicant(external_user_id="user_fail")
    assert result["api_status"] == "error"
    assert "500" in result.get("error", "")


def test_create_applicant_403_returns_error():
    """403 from Sumsub create_applicant ⇒ api_status=error when configured."""
    from sumsub_client import SumsubClient

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True
    client.level_name = "test-level"

    client._request_with_retry = MagicMock(return_value=(
        403,
        {},
        "Forbidden"
    ))

    result = client.create_applicant(external_user_id="user_403")
    assert result["api_status"] == "error"
    assert result["applicant_id"] == ""


# ══════════════════════════════════════════════════════════════════
# 3. Missing applicant row ⇒ screening error, not pending
# ══════════════════════════════════════════════════════════════════

def test_screening_no_applicant_id_returns_error():
    """screen_sumsub_aml returns api_status=error when applicant creation fails."""
    import screening

    def fake_create_fail(**kwargs):
        return {
            "applicant_id": "",
            "api_status": "error",
            "source": "sumsub",
            "error": "create_applicant failed: API returned 500",
        }

    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create_fail
    try:
        result = screening.screen_sumsub_aml("John Doe", birth_date="1990-01-01")
        assert result["api_status"] == "error", \
            f"Expected error but got {result['api_status']}"
        assert result["source"] == "sumsub"
        assert not result["matched"]
    finally:
        screening.sumsub_create_applicant = original


def test_screening_no_applicant_id_no_pending():
    """screen_sumsub_aml must never return pending when no real applicant exists."""
    import screening

    def fake_create_no_id(**kwargs):
        return {
            "applicant_id": "",
            "api_status": "simulated",
            "source": "simulated",
        }

    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create_no_id
    try:
        result = screening.screen_sumsub_aml("Jane Doe", birth_date="1985-05-15")
        # Must not be pending — either error or simulated
        assert result["api_status"] != "pending", \
            f"Got api_status=pending with no real applicant — unacceptable"
        assert result["api_status"] in ("error", "simulated")
    finally:
        screening.sumsub_create_applicant = original


# ══════════════════════════════════════════════════════════════════
# 4. source=sumsub only when real applicant/check exists
# ══════════════════════════════════════════════════════════════════

def test_source_sumsub_requires_real_applicant():
    """source=sumsub should only appear when a real Sumsub applicant was created."""
    import screening

    def fake_create_simulated(**kwargs):
        return {
            "applicant_id": "",
            "api_status": "simulated",
            "source": "simulated",
        }

    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create_simulated
    try:
        result = screening.screen_sumsub_aml("Test Person")
        # source must not be "sumsub" when creation was simulated
        assert result["source"] != "sumsub" or result["api_status"] == "error", \
            f"Got source=sumsub without real applicant: {result}"
    finally:
        screening.sumsub_create_applicant = original


def test_source_sumsub_with_real_applicant():
    """source=sumsub is correct when a real applicant exists and review completes."""
    import screening

    def fake_create_ok(**kwargs):
        return {
            "applicant_id": "real_applicant_123",
            "external_user_id": kwargs.get("external_user_id", ""),
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create_ok
    try:
        mock_client = MagicMock()
        mock_client.request_check.return_value = {
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "real_applicant_123",
            "review_status": "completed",
            "review_answer": "GREEN",
            "source": "sumsub",
            "api_status": "live",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Real Person", birth_date="1990-01-01")
            assert result["source"] == "sumsub"
            assert result["api_status"] == "live"
    finally:
        screening.sumsub_create_applicant = original


# ══════════════════════════════════════════════════════════════════
# 5. Simulated screening explicitly labeled
# ══════════════════════════════════════════════════════════════════

def test_simulated_screening_labeled():
    """When screening falls back to simulation, source and api_status reflect it."""
    import screening

    def fake_create_simulated(**kwargs):
        return {
            "applicant_id": "",
            "api_status": "simulated",
            "source": "simulated",
        }

    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create_simulated
    try:
        result = screening.screen_sumsub_aml("Sim Person")
        assert result["api_status"] == "simulated"
        assert result["source"] == "simulated"
    finally:
        screening.sumsub_create_applicant = original


# ══════════════════════════════════════════════════════════════════
# 6. Audit entries for KYC Applicant creation (success/failure)
# ══════════════════════════════════════════════════════════════════

def _make_handler_and_call(applicant_result, external_user_id="ext_user_1"):
    """Create a SumsubApplicantHandler and call post() with mocked data."""
    from tornado.web import Application
    from tornado.httputil import HTTPServerRequest, HTTPHeaders
    from server import SumsubApplicantHandler

    app = Application()
    mock_conn = MagicMock()
    mock_conn.context = MagicMock()
    mock_conn.context.remote_ip = "127.0.0.1"

    body_data = json.dumps({
        "external_user_id": external_user_id,
        "first_name": "Test",
        "last_name": "User",
    }).encode()

    req = HTTPServerRequest(
        method="POST",
        uri="/api/kyc/applicant",
        version="HTTP/1.1",
        headers=HTTPHeaders({"Content-Type": "application/json", "Host": "localhost"}),
        body=body_data,
        connection=mock_conn,
    )

    handler = SumsubApplicantHandler(app, req)
    handler._transforms = []
    audit_calls = []
    handler.log_audit = lambda user, action, target, detail, **kw: audit_calls.append(
        {"action": action, "target": target, "detail": detail}
    )
    handler.require_auth = lambda *a, **kw: {"sub": "test", "name": "Test", "role": "admin"}

    with patch("server.sumsub_create_applicant", return_value=applicant_result):
        handler.post()

    return handler, audit_calls


def test_audit_entry_on_successful_creation():
    """Audit log writes 'KYC Applicant Created' only when applicantId is real."""
    result = {
        "applicant_id": "real_id_xyz",
        "external_user_id": "ext_user_1",
        "status": "init",
        "source": "sumsub",
        "api_status": "live",
    }
    handler, audits = _make_handler_and_call(result)
    assert len(audits) == 1
    assert audits[0]["action"] == "KYC Applicant Created"
    assert "real_id_xyz" in audits[0]["detail"]


def test_audit_entry_on_failed_creation():
    """Audit log writes 'KYC Applicant Creation Failed' when applicantId is empty."""
    result = {
        "applicant_id": "",
        "status": "error",
        "source": "sumsub",
        "api_status": "error",
        "error": "create_applicant failed: API returned 500",
    }
    handler, audits = _make_handler_and_call(result)
    assert len(audits) == 1
    assert audits[0]["action"] == "KYC Applicant Creation Failed"
    assert "error" in audits[0]["detail"].lower() or "failed" in audits[0]["detail"].lower()


def test_no_created_audit_on_empty_id():
    """Audit log must NOT write 'KYC Applicant Created' when applicantId is empty."""
    result = {
        "applicant_id": "",
        "status": "error",
        "source": "sumsub",
        "api_status": "error",
        "error": "empty applicantId",
    }
    handler, audits = _make_handler_and_call(result)
    for a in audits:
        assert a["action"] != "KYC Applicant Created", \
            "Must not write 'KYC Applicant Created' with empty applicantId"


# ══════════════════════════════════════════════════════════════════
# 7. request_check structured logging
# ══════════════════════════════════════════════════════════════════

def test_request_check_success_logging():
    """request_check logs success with structured info."""
    from sumsub_client import SumsubClient
    import logging

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True

    client._request_with_retry = MagicMock(return_value=(200, {}, ""))

    with patch.object(logging.getLogger("sumsub_client"), "info") as mock_info:
        result = client.request_check("applicant_abc")
        assert result["ok"] is True
        assert result["api_status"] == "live"
        # Check structured logging was called
        info_calls = [str(c) for c in mock_info.call_args_list]
        assert any("request_check success" in c for c in info_calls)


def test_request_check_409_fallback_logging():
    """request_check 409 logs fallback-to-poll behavior."""
    from sumsub_client import SumsubClient
    import logging

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True

    client._request_with_retry = MagicMock(return_value=(409, {}, "Conflict"))
    client.get_applicant_review_status = MagicMock(return_value={
        "applicant_id": "applicant_409",
        "review_status": "completed",
        "review_answer": "GREEN",
        "source": "sumsub",
        "api_status": "live",
    })

    with patch.object(logging.getLogger("sumsub_client"), "info") as mock_info:
        result = client.request_check("applicant_409")
        assert result["ok"] is True
        info_calls = [str(c) for c in mock_info.call_args_list]
        assert any("fallback-to-poll" in c for c in info_calls)
        assert any("poll result" in c for c in info_calls)


def test_request_check_failure_logging():
    """request_check non-2xx (not 409) logs failure."""
    from sumsub_client import SumsubClient
    import logging

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True

    client._request_with_retry = MagicMock(return_value=(500, {}, "Server Error"))

    with patch.object(logging.getLogger("sumsub_client"), "warning") as mock_warn:
        result = client.request_check("applicant_fail")
        assert result["api_status"] == "error"
        warn_calls = [str(c) for c in mock_warn.call_args_list]
        assert any("request_check failure" in c for c in warn_calls)


# ══════════════════════════════════════════════════════════════════
# 8. Gate 5 not weakened
# ══════════════════════════════════════════════════════════════════

def test_gate5_still_blocks_pending():
    """Gate 5 still blocks required screening with api_status=pending."""
    from security_hardening import _collect_screening_provider_evidence

    report = {
        "director_screenings": [{
            "person_name": "Test Director",
            "person_type": "director",
            "screening": {
                "api_status": "pending",
                "source": "sumsub",
            }
        }],
        "ubo_screenings": [],
        "kyc_applicants": [],
        "company_screening": {"sanctions": {"api_status": "live", "source": "sumsub"}},
    }

    evidence = _collect_screening_provider_evidence(report)
    required = [e for e in evidence if e.get("is_required")]
    pending_items = [e for e in required if e.get("api_status") == "pending"]
    assert len(pending_items) > 0, "Expected pending items in Gate 5 evidence"


def test_gate5_still_blocks_error():
    """Gate 5 still blocks required screening with api_status=error."""
    from security_hardening import _collect_screening_provider_evidence

    report = {
        "director_screenings": [{
            "person_name": "Failed Director",
            "person_type": "director",
            "screening": {
                "api_status": "error",
                "source": "sumsub",
            }
        }],
        "ubo_screenings": [],
        "kyc_applicants": [],
        "company_screening": {"sanctions": {"api_status": "live", "source": "sumsub"}},
    }

    evidence = _collect_screening_provider_evidence(report)
    required = [e for e in evidence if e.get("is_required")]
    error_items = [e for e in required if e.get("api_status") == "error"]
    assert len(error_items) > 0, "Expected error items in Gate 5 evidence"


def test_gate5_still_blocks_simulated():
    """Gate 5 still blocks required screening with api_status=simulated."""
    from security_hardening import _collect_screening_provider_evidence

    report = {
        "director_screenings": [{
            "person_name": "Simulated Director",
            "person_type": "director",
            "screening": {
                "api_status": "simulated",
                "source": "simulated",
            }
        }],
        "ubo_screenings": [],
        "kyc_applicants": [],
        "company_screening": {"sanctions": {"api_status": "live", "source": "sumsub"}},
    }

    evidence = _collect_screening_provider_evidence(report)
    required = [e for e in evidence if e.get("is_required")]
    simulated_items = [e for e in required if e.get("api_status") == "simulated"]
    assert len(simulated_items) > 0, "Expected simulated items in Gate 5 evidence"


def test_gate5_allows_live():
    """Gate 5 allows required screening with api_status=live."""
    from security_hardening import _collect_screening_provider_evidence

    report = {
        "director_screenings": [{
            "person_name": "Live Director",
            "person_type": "director",
            "screening": {
                "api_status": "live",
                "source": "sumsub",
            }
        }],
        "ubo_screenings": [],
        "kyc_applicants": [{
            "person_name": "Live Director",
            "person_type": "director",
            "api_status": "live",
            "source": "sumsub",
            "applicant_id": "real123",
        }],
        "company_screening": {"sanctions": {"api_status": "live", "source": "sumsub"}},
    }

    evidence = _collect_screening_provider_evidence(report)
    required = [e for e in evidence if e.get("is_required")]
    # All required should be live
    for e in required:
        assert e["api_status"] == "live", f"Expected live but got {e['api_status']} for {e['name']}"


# ══════════════════════════════════════════════════════════════════
# 9. Diagnostic endpoint
# ══════════════════════════════════════════════════════════════════

def _setup_diagnostic_db():
    """Create an in-memory DB with required tables for diagnostic tests."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE applications (
        id TEXT PRIMARY KEY, ref TEXT, company_name TEXT DEFAULT '',
        country TEXT DEFAULT '', sector TEXT DEFAULT '', entity_type TEXT DEFAULT '',
        prescreening_data TEXT DEFAULT '{}', client_id TEXT DEFAULT '',
        updated_at TEXT DEFAULT '', screening_mode TEXT DEFAULT 'live'
    )""")
    db.execute("""CREATE TABLE sumsub_applicant_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL,
        applicant_id TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        person_name TEXT DEFAULT '',
        person_type TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(applicant_id)
    )""")
    db.execute("""CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT DEFAULT '', user_name TEXT DEFAULT '', user_role TEXT DEFAULT '',
        action TEXT DEFAULT '', target TEXT DEFAULT '', detail TEXT DEFAULT '',
        ip_address TEXT DEFAULT '', before_state TEXT, after_state TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    return db


def test_diagnostic_handler_returns_data():
    """SumsubDiagnosticsHandler returns per-person applicant data."""
    from tornado.web import Application
    from tornado.httputil import HTTPServerRequest, HTTPHeaders
    from server import SumsubDiagnosticsHandler

    db = _setup_diagnostic_db()

    # Insert application
    app_id = str(uuid.uuid4())
    prescreening = json.dumps({
        "sumsub_applicant_ids": {"ext_tariq": "sumsub_tariq_123"},
        "screening_report": {
            "screening_mode": "live",
            "kyc_applicants": [{
                "person_name": "Tariq Test",
                "person_type": "director",
                "applicant_id": "sumsub_tariq_123",
                "api_status": "live",
                "source": "sumsub",
            }],
            "director_screenings": [{
                "person_name": "Tariq Test",
                "person_type": "director",
                "screening": {"api_status": "live", "source": "sumsub"},
            }],
            "ubo_screenings": [],
        },
        "last_screened_at": "2026-01-01T00:00:00",
    })
    db.execute("INSERT INTO applications (id, ref, prescreening_data) VALUES (?, ?, ?)",
               (app_id, "APP-001", prescreening))
    db.execute("""INSERT INTO sumsub_applicant_mappings
        (application_id, applicant_id, external_user_id, person_name, person_type)
        VALUES (?, ?, ?, ?, ?)""",
        (app_id, "sumsub_tariq_123", "ext_tariq", "Tariq Test", "director"))
    db.execute("""INSERT INTO audit_log (action, target, detail) VALUES (?, ?, ?)""",
        ("KYC Applicant Created", "ext_tariq", "Sumsub applicant created — ID: sumsub_tariq_123"))
    db.commit()

    app = Application()
    mock_conn = MagicMock()
    mock_conn.context = MagicMock()
    mock_conn.context.remote_ip = "127.0.0.1"

    req = HTTPServerRequest(
        method="GET",
        uri=f"/api/admin/sumsub-diagnostics?application_id={app_id}",
        version="HTTP/1.1",
        headers=HTTPHeaders({"Host": "localhost"}),
        connection=mock_conn,
    )

    handler = SumsubDiagnosticsHandler(app, req)
    handler._transforms = []
    handler.require_auth = lambda *a, **kw: {"sub": "admin", "name": "Admin", "role": "admin"}

    with patch("server.get_db", return_value=db):
        handler.get()

    # Parse the response
    body = b"".join(handler._write_buffer).decode()
    resp = json.loads(body)

    assert resp.get("status") == "success" or "data" in resp or "application_id" in resp.get("data", resp)
    data = resp.get("data", resp)
    assert data.get("application_id") == app_id or data.get("application_ref") == "APP-001"


def test_diagnostic_handler_requires_admin():
    """SumsubDiagnosticsHandler requires admin/sco role."""
    from tornado.web import Application
    from tornado.httputil import HTTPServerRequest, HTTPHeaders
    from server import SumsubDiagnosticsHandler

    app = Application()
    mock_conn = MagicMock()
    mock_conn.context = MagicMock()
    mock_conn.context.remote_ip = "127.0.0.1"

    req = HTTPServerRequest(
        method="GET",
        uri="/api/admin/sumsub-diagnostics?application_id=test",
        version="HTTP/1.1",
        headers=HTTPHeaders({"Host": "localhost"}),
        connection=mock_conn,
    )

    handler = SumsubDiagnosticsHandler(app, req)
    handler._transforms = []

    # Simulate auth failure
    handler.require_auth = lambda *a, **kw: None

    handler.get()

    # Should return error or no data
    body = b"".join(handler._write_buffer).decode()
    # With auth failure, handler returns nothing (require_auth handles the response)
    # Just verify it didn't crash and didn't return data
    if body:
        resp = json.loads(body)
        assert resp.get("status") != "success" or "error" in body.lower()


# ══════════════════════════════════════════════════════════════════
# 10. End-to-end screening integrity: error propagation
# ══════════════════════════════════════════════════════════════════

def test_screening_request_check_error_propagates():
    """If request_check fails, screen_sumsub_aml returns error, not pending."""
    import screening

    def fake_create_ok(**kwargs):
        return {
            "applicant_id": "real_applicant_for_check",
            "external_user_id": kwargs.get("external_user_id", ""),
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create_ok
    try:
        mock_client = MagicMock()
        mock_client.request_check.return_value = {
            "ok": False,
            "source": "sumsub",
            "api_status": "error",
            "error": "request_check failed: API returned 500",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Error Person", birth_date="1990-01-01")
            assert result["api_status"] == "error"
            assert result["source"] == "sumsub"
    finally:
        screening.sumsub_create_applicant = original


def test_screening_poll_error_propagates():
    """If poll fails, screen_sumsub_aml returns error, not pending."""
    import screening

    def fake_create_ok(**kwargs):
        return {
            "applicant_id": "real_applicant_for_poll",
            "external_user_id": kwargs.get("external_user_id", ""),
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create_ok
    try:
        mock_client = MagicMock()
        mock_client.request_check.return_value = {
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "real_applicant_for_poll",
            "api_status": "error",
            "source": "sumsub",
            "error": "get_applicant_review_status failed: API returned 503",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Poll Error Person")
            assert result["api_status"] == "error"
    finally:
        screening.sumsub_create_applicant = original


def test_create_applicant_failure_never_returns_pending():
    """create_applicant failure must never produce api_status=pending."""
    from sumsub_client import SumsubClient

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True
    client.level_name = "test-level"

    # Test various failure scenarios
    for status_code in [400, 401, 403, 500, 502, 503]:
        client._request_with_retry = MagicMock(return_value=(
            status_code, {}, f"Error {status_code}"
        ))
        result = client.create_applicant(external_user_id=f"user_{status_code}")
        assert result["api_status"] != "pending", \
            f"create_applicant returned pending for status {status_code}"
        assert result["api_status"] == "error"


# ══════════════════════════════════════════════════════════════════
# 11. create_applicant structured logging
# ══════════════════════════════════════════════════════════════════

def test_create_applicant_success_logging():
    """create_applicant logs success with structured info."""
    from sumsub_client import SumsubClient
    import logging

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True
    client.level_name = "test-level"

    client._request_with_retry = MagicMock(return_value=(
        200,
        {"id": "logged_id_123", "review": {"reviewStatus": "init"}},
        ""
    ))

    with patch.object(logging.getLogger("sumsub_client"), "info") as mock_info:
        result = client.create_applicant(external_user_id="log_user")
        assert result["applicant_id"] == "logged_id_123"
        info_calls = [str(c) for c in mock_info.call_args_list]
        assert any("create_applicant success" in c for c in info_calls)
        assert any("logged_id_123" in c for c in info_calls)


def test_create_applicant_failure_logging():
    """create_applicant logs failure with structured info."""
    from sumsub_client import SumsubClient
    import logging

    client = SumsubClient.__new__(SumsubClient)
    client.is_configured = True
    client.level_name = "test-level"

    client._request_with_retry = MagicMock(return_value=(
        500, {}, "Internal Server Error"
    ))

    with patch.object(logging.getLogger("sumsub_client"), "warning") as mock_warn:
        result = client.create_applicant(external_user_id="fail_user")
        assert result["api_status"] == "error"
        warn_calls = [str(c) for c in mock_warn.call_args_list]
        assert any("create_applicant failure" in c for c in warn_calls)
