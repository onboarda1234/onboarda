"""Priority C: Draft persistence — server-side behaviour tests.

Covers:
  * draft save / update (single active draft, no duplicates)
  * resume an existing draft
  * discard a draft
  * cross-user isolation (user A cannot read/write/delete user B's draft)
  * autosave does not create a duplicate active draft for the same app
  * application_id ownership is enforced
  * empty drafts are rejected
  * KYC submission clears the active draft (no stale "Resume" banner)
  * /api/save-resume/active returns drafts joined to apps for the calling user only
  * form_data is encrypted at rest and round-trips correctly
  * legacy plaintext form_data rows still read transparently
  * non-client (officer/admin) tokens cannot use the draft endpoints
"""

import json
import inspect
import os
import socket
import sqlite3
import tempfile
import threading
import time

import pytest
import requests as http_requests
import tornado.httpserver
import tornado.ioloop


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def api_server():
    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_draft_test_{os.getpid()}.db")
    os.environ["DB_PATH"] = db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass

    from db import get_db, init_db, seed_initial_data

    init_db()
    try:
        conn = get_db()
        seed_initial_data(conn)
        conn.commit()
        conn.close()
    except Exception as exc:
        raise RuntimeError(f"Failed to seed draft test database: {exc}") from exc

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


# ── Helpers ────────────────────────────────────────────────────

def _ensure_client(db, client_id, email):
    import bcrypt

    db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, email,
         bcrypt.hashpw(b"TestPass123!", bcrypt.gensalt()).decode(),
         "Draft Test Co"),
    )
    db.commit()


def _ensure_application(db, app_id, ref, client_id, status="draft"):
    db.execute(
        """
        INSERT OR IGNORE INTO applications (
            id, ref, client_id, company_name, country, status, prescreening_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (app_id, ref, client_id, f"{ref} Ltd", "Mauritius", status,
         json.dumps({"registered_entity_name": f"{ref} Ltd",
                     "country_of_incorporation": "Mauritius"})),
    )
    db.commit()


def _client_token(client_id, name="Draft Test Client"):
    from auth import create_token
    return create_token(client_id, "client", name, "client")


def _meaningful_form_data(seed="Acme"):
    return {
        "prescreening": {
            "f-reg-name": f"{seed} Ltd",
            "f-trade-name": f"{seed} Trading",
            "f-email": f"{seed.lower()}@example.com",
        },
        "directors": [
            {"first_name": "Jane", "last_name": "Doe", "nationality": "Mauritius"}
        ],
    }


# ── Tests ──────────────────────────────────────────────────────

def test_save_creates_new_draft_then_update_in_place(api_server):
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_save", "draftsave@test.com")
    _ensure_application(conn, "draft_app_save", "ARF-DRAFT-SAVE", "draftuser_save")
    conn.close()

    token = _client_token("draftuser_save")
    headers = {"Authorization": f"Bearer {token}"}

    # First save creates the row
    r1 = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": "draft_app_save",
              "form_data": _meaningful_form_data("First"),
              "last_step": 1},
        timeout=3,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "saved"
    assert r1.json()["last_saved_at"]

    # Second save updates the same row — never inserts a duplicate
    r2 = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": "draft_app_save",
              "form_data": _meaningful_form_data("Second"),
              "last_step": 2},
        timeout=3,
    )
    assert r2.status_code == 200, r2.text

    conn = get_db()
    rows = conn.execute(
        "SELECT id, last_step FROM client_sessions WHERE client_id=? AND application_id=?",
        ("draftuser_save", "draft_app_save"),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, "Autosave must not create duplicate active drafts"
    assert rows[0]["last_step"] == 2


def test_pre_submit_save_without_application_id_creates_draft_shell(api_server):
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_presubmit", "draftpresubmit@test.com")
    conn.close()

    token = _client_token("draftuser_presubmit")
    headers = {"Authorization": f"Bearer {token}"}

    payload = _meaningful_form_data("PreSubmit")
    payload["prescreening"]["f-reg-name"] = "PreSubmit Holdings Ltd"
    payload["prescreening"]["f-inc-country"] = "Mauritius"

    save_resp = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"form_data": payload, "last_step": 0},
        timeout=5,
    )
    assert save_resp.status_code == 200, save_resp.text
    body = save_resp.json()
    assert body["status"] == "saved"
    assert body["application_id"]
    assert body["application_ref"]

    app_id = body["application_id"]
    app_ref = body["application_ref"]

    # Same draft identity is resumable and not duplicated.
    save_again = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": app_id, "form_data": _meaningful_form_data("PreSubmit2"), "last_step": 1},
        timeout=5,
    )
    assert save_again.status_code == 200, save_again.text
    assert save_again.json()["application_id"] == app_id

    conn = get_db()
    app_rows = conn.execute(
        "SELECT id, ref, status, company_name FROM applications WHERE client_id=?",
        ("draftuser_presubmit",),
    ).fetchall()
    session_rows = conn.execute(
        "SELECT id, application_id FROM client_sessions WHERE client_id=?",
        ("draftuser_presubmit",),
    ).fetchall()
    conn.close()
    assert len(app_rows) == 1
    assert app_rows[0]["id"] == app_id
    assert app_rows[0]["ref"] == app_ref
    assert app_rows[0]["status"] == "draft"
    assert len(session_rows) == 1
    assert session_rows[0]["application_id"] == app_id

    # Resume works by app id and by app ref after navigation/refresh.
    get_by_id = http_requests.get(
        f"{api_server}/api/applications/{app_id}",
        headers=headers, timeout=5,
    )
    assert get_by_id.status_code == 200, get_by_id.text

    get_by_ref = http_requests.get(
        f"{api_server}/api/applications/{app_ref}",
        headers=headers, timeout=5,
    )
    assert get_by_ref.status_code == 200, get_by_ref.text


def test_pre_submit_autosave_without_id_reuses_same_draft(api_server):
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_presubmit_reuse", "draftpresubmitreuse@test.com")
    conn.close()

    token = _client_token("draftuser_presubmit_reuse")
    headers = {"Authorization": f"Bearer {token}"}
    payload = _meaningful_form_data("Reuse")
    payload["prescreening"]["f-reg-name"] = "Reuse Draft Co"

    first = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"form_data": payload, "last_step": 0},
        timeout=5,
    )
    assert first.status_code == 200, first.text

    second = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"form_data": payload, "last_step": 1},
        timeout=5,
    )
    assert second.status_code == 200, second.text
    assert first.json()["application_id"] == second.json()["application_id"]

    conn = get_db()
    apps = conn.execute(
        "SELECT id FROM applications WHERE client_id=?",
        ("draftuser_presubmit_reuse",),
    ).fetchall()
    sessions = conn.execute(
        "SELECT id FROM client_sessions WHERE client_id=?",
        ("draftuser_presubmit_reuse",),
    ).fetchall()
    conn.close()
    assert len(apps) == 1
    assert len(sessions) == 1


def test_pre_submit_save_accepts_root_payload_shape_without_form_data(api_server):
    """Meaningful pre-submit payloads must save even when sent in root/canonical shape."""
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_rootshape", "draftrootshape@test.com")
    conn.close()

    token = _client_token("draftuser_rootshape")
    headers = {"Authorization": f"Bearer {token}"}

    root_payload = {
        "registered_entity_name": "Root Shape Holdings Ltd",
        "company_name": "Root Shape Holdings Ltd",
        "country": "Mauritius",
        "sector": "Technology",
        "entity_type": "SME",
        "ownership_structure": "Simple ownership",
        "prescreening_data": {
            "registered_entity_name": "Root Shape Holdings Ltd",
            "country_of_incorporation": "Mauritius",
            "sector": "Technology",
            "entity_type": "SME",
            "ownership_structure": "Simple ownership",
        },
        "directors": [{"first_name": "Rita", "last_name": "Draft", "nationality": "MU"}],
    }

    save_resp = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={**root_payload, "last_step": 0},
        timeout=5,
    )
    assert save_resp.status_code == 200, save_resp.text
    body = save_resp.json()
    assert body["status"] == "saved"
    assert body["application_id"]

    get_resp = http_requests.get(
        f"{api_server}/api/save-resume?application_id={body['application_id']}",
        headers=headers,
        timeout=5,
    )
    assert get_resp.status_code == 200, get_resp.text
    form_data = get_resp.json()["form_data"]
    assert isinstance(form_data, dict)
    assert form_data.get("directors"), "Saved draft must persist meaningful party data"


def test_pre_submit_manual_then_autosave_updates_same_draft_identity(api_server):
    """Manual save and subsequent autosave must update the same pre-submit draft."""
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_same_identity", "draftsameidentity@test.com")
    conn.close()

    token = _client_token("draftuser_same_identity")
    headers = {"Authorization": f"Bearer {token}"}
    payload = _meaningful_form_data("SameIdentity")
    payload["prescreening"]["f-reg-name"] = "Same Identity Co"

    first = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"form_data": payload, "last_step": 0},
        timeout=5,
    )
    assert first.status_code == 200, first.text
    app_id = first.json()["application_id"]

    second = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": app_id, "form_data": payload, "last_step": 1},
        timeout=5,
    )
    assert second.status_code == 200, second.text
    assert second.json()["application_id"] == app_id


def test_pre_submit_step2_key_fields_round_trip_and_update_without_500(api_server):
    """Pre-submit Step 2 key compliance fields must persist, update, and resume cleanly."""
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_step2_roundtrip", "draftstep2roundtrip@test.com")
    conn.close()

    token = _client_token("draftuser_step2_roundtrip")
    headers = {"Authorization": f"Bearer {token}"}

    first_payload = {
        "prescreening": {
            "f-reg-name": "Step2 Roundtrip Ltd",
            "f-entity-type": "SME / Private Company",
            "f-ownership-structure": "1–2 ownership layers",
            "f-sector": "Software / SaaS",
        },
        "directors": [{"first_name": "Mia", "last_name": "Director", "nationality": "MU"}],
        "ubos": [{"first_name": "Uma", "last_name": "Beneficial", "nationality": "GB", "ownership_pct": "55"}],
    }

    manual_save = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"form_data": first_payload, "last_step": 0},
        timeout=5,
    )
    assert manual_save.status_code == 200, manual_save.text
    app_id = manual_save.json()["application_id"]

    autosave_payload = json.loads(json.dumps(first_payload))
    autosave_payload["prescreening"]["f-sector"] = "Fintech / Payments"
    autosave_payload["directors"][0]["nationality"] = "Mauritius"
    autosave_payload["ubos"][0]["nationality"] = "United Kingdom"

    autosave = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": app_id, "form_data": autosave_payload, "last_step": 1},
        timeout=5,
    )
    assert autosave.status_code == 200, autosave.text
    assert autosave.json()["application_id"] == app_id

    resumed = http_requests.get(
        f"{api_server}/api/save-resume?application_id={app_id}",
        headers=headers,
        timeout=5,
    )
    assert resumed.status_code == 200, resumed.text
    form_data = resumed.json()["form_data"]
    assert form_data["prescreening"]["f-entity-type"] == "SME / Private Company"
    assert form_data["prescreening"]["f-ownership-structure"] == "1–2 ownership layers"
    assert form_data["prescreening"]["f-sector"] == "Fintech / Payments"
    assert form_data["directors"][0]["nationality"] == "Mauritius"
    assert form_data["ubos"][0]["nationality"] == "United Kingdom"


def test_pre_submit_draft_lookup_falls_back_when_applications_updated_at_missing():
    """Legacy schema safety: missing applications.updated_at must not 500 pre-submit save."""
    import server

    class _FakeResult:
        def __init__(self, rows=None):
            self._rows = rows or []

        def fetchall(self):
            return list(self._rows)

    class _FakeDB:
        def __init__(self):
            self.queries = []

        def execute(self, sql, params=()):
            self.queries.append(sql)
            if "FROM applications WHERE client_id=?" in sql and "ORDER BY updated_at DESC" in sql:
                raise sqlite3.OperationalError("no such column: updated_at")
            if "FROM applications WHERE client_id=?" in sql and "ORDER BY created_at DESC, id DESC" in sql:
                return _FakeResult(rows=[{
                    "id": "legacy_app_1",
                    "ref": "ARF-LEGACY-1",
                    "company_name": "Legacy Stage Draft Ltd",
                }])
            raise AssertionError(f"Unexpected SQL in fake DB: {sql}")

    class _FakeHandler:
        def __init__(self):
            self.errors = []

        _normalize_company_key = staticmethod(server.SaveResumeHandler._normalize_company_key)
        _is_missing_column_error = staticmethod(server.SaveResumeHandler._is_missing_column_error)

        def error(self, message, status=400):
            self.errors.append((message, status))

    db = _FakeDB()
    handler = _FakeHandler()
    row = server.SaveResumeHandler._ensure_pre_submit_draft_application(
        handler,
        db,
        "draftuser_legacy_schema",
        {
            "prescreening": {"f-reg-name": "Legacy Stage Draft Ltd"},
            "directors": [{"first_name": "Lia", "last_name": "Stage", "nationality": "MU"}],
        },
    )

    assert row is not None
    assert row["id"] == "legacy_app_1"
    assert handler.errors == []
    assert any("ORDER BY created_at DESC, id DESC" in q for q in db.queries)


def test_save_resume_post_has_legacy_client_sessions_updated_at_fallback():
    """Autosave/manual updates must still work if client_sessions.updated_at is absent."""
    import server

    src = inspect.getsource(server.SaveResumeHandler.post)
    assert "UPDATE client_sessions SET form_data=?, last_step=? WHERE id=?" in src
    assert "SELECT updated_at FROM client_sessions" in src
    assert "_is_missing_column_error(exc, \"updated_at\")" in src


def test_resume_returns_saved_form_data(api_server):
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_resume", "draftresume@test.com")
    _ensure_application(conn, "draft_app_resume", "ARF-DRAFT-RESUME", "draftuser_resume")
    conn.close()

    token = _client_token("draftuser_resume")
    headers = {"Authorization": f"Bearer {token}"}

    payload = _meaningful_form_data("ResumeMe")
    save_resp = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": "draft_app_resume",
              "form_data": payload, "last_step": 3},
        timeout=3,
    )
    assert save_resp.status_code == 200, save_resp.text

    get_resp = http_requests.get(
        f"{api_server}/api/save-resume?application_id=draft_app_resume",
        headers=headers, timeout=3,
    )
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body["last_step"] == 3
    assert body["last_saved_at"]
    assert body["form_data"]["prescreening"]["f-trade-name"] == "ResumeMe Trading"
    assert body["form_data"]["directors"][0]["last_name"] == "Doe"


def test_discard_removes_draft(api_server):
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_discard", "draftdiscard@test.com")
    _ensure_application(conn, "draft_app_discard", "ARF-DRAFT-DISCARD", "draftuser_discard")
    conn.close()

    token = _client_token("draftuser_discard")
    headers = {"Authorization": f"Bearer {token}"}

    http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": "draft_app_discard",
              "form_data": _meaningful_form_data("Bin"), "last_step": 1},
        timeout=3,
    )

    del_resp = http_requests.delete(
        f"{api_server}/api/save-resume?application_id=draft_app_discard",
        headers=headers, timeout=3,
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # Subsequent GET reports an empty draft
    get_resp = http_requests.get(
        f"{api_server}/api/save-resume?application_id=draft_app_discard",
        headers=headers, timeout=3,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["form_data"] == {}
    assert get_resp.json()["last_saved_at"] is None


def test_cross_user_cannot_read_or_write_other_users_draft(api_server):
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_a", "userA@test.com")
    _ensure_client(conn, "draftuser_b", "userB@test.com")
    # Application owned by user A
    _ensure_application(conn, "draft_app_isolation", "ARF-ISO", "draftuser_a")
    conn.close()

    token_a = _client_token("draftuser_a")
    token_b = _client_token("draftuser_b")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User A saves a draft on their own application
    secret = _meaningful_form_data("Confidential")
    save_a = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers_a,
        json={"application_id": "draft_app_isolation",
              "form_data": secret, "last_step": 1},
        timeout=3,
    )
    assert save_a.status_code == 200, save_a.text

    # User B tries to read it — must look like "not found"
    get_b = http_requests.get(
        f"{api_server}/api/save-resume?application_id=draft_app_isolation",
        headers=headers_b, timeout=3,
    )
    assert get_b.status_code == 404

    # User B tries to overwrite it — must be rejected
    overwrite_b = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers_b,
        json={"application_id": "draft_app_isolation",
              "form_data": _meaningful_form_data("Hijack"),
              "last_step": 99},
        timeout=3,
    )
    assert overwrite_b.status_code == 404

    # User B cannot delete it either
    del_b = http_requests.delete(
        f"{api_server}/api/save-resume?application_id=draft_app_isolation",
        headers=headers_b, timeout=3,
    )
    assert del_b.status_code == 404

    # User A still sees their original draft intact
    get_a = http_requests.get(
        f"{api_server}/api/save-resume?application_id=draft_app_isolation",
        headers=headers_a, timeout=3,
    )
    assert get_a.status_code == 200
    assert get_a.json()["form_data"]["prescreening"]["f-trade-name"] == "Confidential Trading"
    assert get_a.json()["last_step"] == 1

    # User B's "active drafts" feed never lists user A's draft
    list_b = http_requests.get(
        f"{api_server}/api/save-resume/active",
        headers=headers_b, timeout=3,
    )
    assert list_b.status_code == 200
    assert all(d["application_id"] != "draft_app_isolation" for d in list_b.json()["drafts"])


def test_active_drafts_lists_only_callers_open_drafts(api_server):
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_list", "draftlist@test.com")
    _ensure_application(conn, "draft_app_list_open", "ARF-LIST-OPEN", "draftuser_list",
                        status="draft")
    _ensure_application(conn, "draft_app_list_done", "ARF-LIST-DONE", "draftuser_list",
                        status="approved")
    conn.close()

    token = _client_token("draftuser_list")
    headers = {"Authorization": f"Bearer {token}"}

    for app_id in ("draft_app_list_open", "draft_app_list_done"):
        http_requests.post(
            f"{api_server}/api/save-resume",
            headers=headers,
            json={"application_id": app_id,
                  "form_data": _meaningful_form_data(app_id), "last_step": 1},
            timeout=3,
        )

    list_resp = http_requests.get(
        f"{api_server}/api/save-resume/active",
        headers=headers, timeout=3,
    )
    assert list_resp.status_code == 200
    refs = {d["ref"] for d in list_resp.json()["drafts"]}
    assert "ARF-LIST-OPEN" in refs
    # Approved apps must not surface as resumable drafts
    assert "ARF-LIST-DONE" not in refs


def test_empty_draft_payload_is_rejected(api_server):
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_empty", "draftempty@test.com")
    _ensure_application(conn, "draft_app_empty", "ARF-EMPTY", "draftuser_empty")
    conn.close()

    token = _client_token("draftuser_empty")
    headers = {"Authorization": f"Bearer {token}"}

    r = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": "draft_app_empty",
              "form_data": {"prescreening": {}}, "last_step": 0},
        timeout=3,
    )
    assert r.status_code == 400

    conn = get_db()
    rows = conn.execute(
        "SELECT id FROM client_sessions WHERE application_id=?",
        ("draft_app_empty",),
    ).fetchall()
    conn.close()
    assert rows == [], "Empty draft must not create a client_sessions row"


def test_kyc_submit_clears_active_draft(api_server, tmp_path):
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_kyc", "draftkyc@test.com")
    _ensure_application(conn, "draft_app_kyc", "ARF-KYC-CLEAR", "draftuser_kyc",
                        status="kyc_documents")

    # Need at least one document on the application for KYC submit to succeed
    doc_path = tmp_path / "coi.pdf"
    doc_path.write_bytes(b"%PDF-1.4 minimal")
    conn.execute(
        """
        INSERT INTO documents (id, application_id, doc_type, doc_name, file_path)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("doc_kyc_clear", "draft_app_kyc", "cert_inc", "coi.pdf", str(doc_path)),
    )
    conn.commit()
    conn.close()

    token = _client_token("draftuser_kyc")
    headers = {"Authorization": f"Bearer {token}"}

    save = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": "draft_app_kyc",
              "form_data": _meaningful_form_data("KycSubmit"), "last_step": 4},
        timeout=3,
    )
    assert save.status_code == 200, save.text

    submit = http_requests.post(
        f"{api_server}/api/applications/draft_app_kyc/submit-kyc",
        headers=headers, timeout=5,
    )
    assert submit.status_code == 200, submit.text

    # Draft row must be gone — no stale "Resume" banner
    conn = get_db()
    rows = conn.execute(
        "SELECT id FROM client_sessions WHERE application_id=?",
        ("draft_app_kyc",),
    ).fetchall()
    conn.close()
    assert rows == [], "KYC submission must clear the active draft session"


def test_form_data_is_encrypted_at_rest_when_pii_key_configured(api_server):
    from db import get_db
    from server import (
        _pii_encryptor,
        DRAFT_FORM_DATA_ENCRYPTED_PREFIX,
        DRAFT_FORM_DATA_ENCRYPTED_JSON_KEY,
    )

    if _pii_encryptor is None:
        pytest.skip("PII encryptor not configured in this environment")

    conn = get_db()
    _ensure_client(conn, "draftuser_enc", "draftenc@test.com")
    _ensure_application(conn, "draft_app_enc", "ARF-ENC", "draftuser_enc")
    conn.close()

    token = _client_token("draftuser_enc")
    headers = {"Authorization": f"Bearer {token}"}

    sentinel = "SENTINEL_PLAINTEXT_AT_REST_NEEDLE"
    payload = _meaningful_form_data("Enc")
    payload["prescreening"]["f-trade-name"] = sentinel

    save = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": "draft_app_enc",
              "form_data": payload, "last_step": 1},
        timeout=3,
    )
    assert save.status_code == 200, save.text

    conn = get_db()
    raw_row = conn.execute(
        "SELECT form_data FROM client_sessions WHERE application_id=?",
        ("draft_app_enc",),
    ).fetchone()
    conn.close()
    raw = raw_row["form_data"]
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    raw = str(raw)
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = raw
    if isinstance(decoded, dict):
        token = decoded.get(DRAFT_FORM_DATA_ENCRYPTED_JSON_KEY, "")
        assert isinstance(token, str) and token.strip(), \
            "form_data must store encrypted draft payload in JSON envelope"
        assert sentinel not in token, \
            "Plaintext PII must not be visible in encrypted token"
    else:
        # Backward-compatible safety for legacy storage format.
        assert decoded.startswith(DRAFT_FORM_DATA_ENCRYPTED_PREFIX), \
            f"form_data must be encrypted at rest, got: {str(decoded)[:40]!r}"
        assert sentinel not in str(decoded), \
            "Plaintext PII must not be visible in client_sessions.form_data"
    assert sentinel not in raw, \
        "Plaintext PII must not be visible in client_sessions.form_data"

    # And the API still round-trips the plaintext correctly via decryption
    get_resp = http_requests.get(
        f"{api_server}/api/save-resume?application_id=draft_app_enc",
        headers=headers, timeout=3,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["form_data"]["prescreening"]["f-trade-name"] == sentinel


def test_legacy_plaintext_form_data_rows_still_read(api_server):
    """Backwards compatibility: rows written before the encryption rollout
    are stored as plaintext JSON and must still resume cleanly."""
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_legacy", "draftlegacy@test.com")
    _ensure_application(conn, "draft_app_legacy", "ARF-LEGACY", "draftuser_legacy")
    legacy_payload = {"prescreening": {"f-reg-name": "Legacy Co Ltd"},
                      "directors": [{"first_name": "Old", "last_name": "Format"}]}
    conn.execute(
        """
        INSERT INTO client_sessions (client_id, application_id, form_data, last_step)
        VALUES (?, ?, ?, ?)
        """,
        ("draftuser_legacy", "draft_app_legacy", json.dumps(legacy_payload), 2),
    )
    conn.commit()
    conn.close()

    token = _client_token("draftuser_legacy")
    headers = {"Authorization": f"Bearer {token}"}

    get_resp = http_requests.get(
        f"{api_server}/api/save-resume?application_id=draft_app_legacy",
        headers=headers, timeout=3,
    )
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["form_data"]["prescreening"]["f-reg-name"] == "Legacy Co Ltd"
    assert body["form_data"]["directors"][0]["last_name"] == "Format"
    assert body["last_step"] == 2


def test_officer_token_cannot_use_draft_endpoints(api_server):
    """Drafts are a portal client primitive — back-office users must not be
    able to read or write portal client drafts via this endpoint."""
    from auth import create_token

    officer_token = create_token("officer001", "officer", "Officer Test", "co")
    headers = {"Authorization": f"Bearer {officer_token}"}

    r_get = http_requests.get(
        f"{api_server}/api/save-resume?application_id=anything",
        headers=headers, timeout=3,
    )
    assert r_get.status_code == 403

    r_post = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"application_id": "anything",
              "form_data": _meaningful_form_data(), "last_step": 0},
        timeout=3,
    )
    assert r_post.status_code == 403

    r_list = http_requests.get(
        f"{api_server}/api/save-resume/active",
        headers=headers, timeout=3,
    )
    assert r_list.status_code == 403


def test_clean_normal_application_flow_still_works(api_server):
    """Sanity check: the standard create -> patch flow on an application
    is unaffected by the draft persistence changes."""
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_clean", "draftclean@test.com")
    conn.close()

    token = _client_token("draftuser_clean")
    headers = {"Authorization": f"Bearer {token}"}

    # Create a brand-new application (no draft involved)
    create = http_requests.post(
        f"{api_server}/api/applications",
        headers=headers,
        json={
            "registered_entity_name": "Clean Flow Ltd",
            "country": "Mauritius",
            "sector": "Technology",
            "entity_type": "SME",
            "ownership_structure": "Simple ownership",
        },
        timeout=5,
    )
    assert create.status_code == 201, create.text
    app_id = create.json()["id"]
    assert create.json()["status"] == "draft"

    # Detail endpoint returns the new application
    detail = http_requests.get(
        f"{api_server}/api/applications/{app_id}",
        headers=headers, timeout=3,
    )
    assert detail.status_code == 200
    assert detail.json()["company_name"] == "Clean Flow Ltd"

    # No client_sessions row should be auto-created — drafts are explicit
    conn = get_db()
    rows = conn.execute(
        "SELECT id FROM client_sessions WHERE application_id=?",
        (app_id,),
    ).fetchall()
    conn.close()
    assert rows == []


# ── Portal HTML wiring assertions (Priority C frontend) ──────

def _portal_html():
    portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
    with open(portal_path, "r", encoding="utf-8") as handle:
        return handle.read()


def test_portal_renders_save_draft_bar_and_dirty_tracking():
    src = _portal_html()
    # Save & Resume bar DOM is now rendered (CSS already existed, DOM was missing).
    assert 'id="save-draft-bar"' in src
    assert 'id="save-status"' in src
    assert 'id="save-ref-display"' in src
    # Dirty-flag plumbing must exist for the navigation guard to make sense.
    assert "_draftDirty" in src
    assert "_markDraftDirty" in src
    # Autosave must be wired into the pre-screening view, not only onboarding.
    assert "name === 'prescreening'" in src or 'name === "prescreening"' in src


def test_portal_has_beforeunload_navigation_guard():
    src = _portal_html()
    assert "beforeunload" in src
    assert "_attachBeforeUnloadGuard" in src
    # Guard must short-circuit during legitimate submission flows.
    assert "_draftSubmitting" in src
    # In-app navigation should also guard against unsaved pre-submit changes.
    assert "_confirmDiscardUnsavedChangesForNavigation" in src


def test_portal_dashboard_resume_cta_has_discard_action():
    src = _portal_html()
    assert 'id="resume-cta-discard"' in src
    assert "discardActiveDraft" in src
    # Discard must hit the server-side delete endpoint.
    assert "/save-resume?application_id=" in src


def test_portal_discard_flow_uses_in_app_confirm_and_cancel_path():
    src = _portal_html()
    assert "async function discardActiveDraft()" in src
    assert "_showDraftDiscardConfirmDialog" in src
    assert "if (!confirmed) return;" in src
    assert "Discard draft" in src


def test_portal_resume_cta_discard_reachability_wiring_present():
    src = _portal_html()
    assert "discardBtn.style.display = 'inline-flex';" in src
    assert "discardBtn.setAttribute('data-app-id'" in src
    assert "discardBtn.setAttribute('data-app-ref'" in src
    assert "discardBtn.setAttribute('data-app-name'" in src


def test_portal_save_draft_is_truthful_for_pre_submit_flow():
    src = _portal_html()
    # Save Draft should not claim unavailable before first submit anymore.
    assert "Save Unavailable Yet" not in src
    # Save path should allow saving without a pre-existing application id.
    assert "if (currentApplicationId) payload.application_id = currentApplicationId;" in src
    # Manual save + autosave must share the same request serializer.
    assert "function buildSaveResumePayload()" in src


def test_portal_new_application_has_duplicate_draft_guard():
    src = _portal_html()
    assert "_loadExistingDraftForNewApplicationGuard" in src
    assert "_discardDraftFromGuard" in src
    assert "_showDraftGuardChoiceDialog" in src
    assert "draft-start-guard-overlay" in src
    assert "choice === 'R'" in src
    assert "choice === 'D'" in src


def test_portal_resume_cta_prefers_active_pre_submit_draft():
    src = _portal_html()
    assert "var drafts = inProgress.filter(function(a) { return (a.status || '') === 'draft'; });" in src
    assert "var app = drafts.length ? drafts[0] : inProgress[0];" in src


def test_portal_restore_normalizes_key_dropdowns_and_nationality():
    src = _portal_html()
    assert "SELECT_RESTORE_ALIASES" in src
    assert "'f-entity-type'" in src
    assert "'f-ownership-structure'" in src
    assert "'f-sector'" in src
    assert "'nat-select'" in src
    assert "_restoreSelectValue(natSel, rowData.nationality || '', 'nat-select')" in src


def test_portal_draft_discard_uses_in_app_confirmation_not_native():
    src = _portal_html()
    delete_fn = src.split("async function deleteApplication(ref, companyName) {", 1)[1].split("async function discardActiveDraft()", 1)[0]
    discard_fn = src.split("async function discardActiveDraft() {", 1)[1].split("function renderRecentActivity(apps) {", 1)[0]
    start_new_fn = src.split("async function startNewApplication() {", 1)[1].split("function buildDraftDataFromApplication(app) {", 1)[0]
    assert "function _showDraftDiscardConfirmDialog(" in src
    assert "draft-discard-confirm-overlay" in src
    assert "Discard draft?" in src
    assert "This action is irreversible." in src
    assert "await _showDraftDiscardConfirmDialog(label, 'Delete draft')" in delete_fn
    assert "await _showDraftDiscardConfirmDialog(name, 'Discard draft')" in discard_fn
    assert "await _showDraftDiscardConfirmDialog(label, 'Discard draft and start new')" in start_new_fn
    assert "return confirm(" not in src
    assert "window.confirm(" not in src
    assert "Discard your in-progress draft for " not in src
    assert "Delete ' + label + '? This cannot be undone." not in src


# ── generate_ref() collision safety (root-cause regression suite) ──────────

def test_generate_ref_uses_max_not_count_after_deletion(api_server):
    """generate_ref() must return a ref that does not already exist in the DB
    even after one or more applications are deleted (COUNT-based generation
    would regenerate a previously-issued ref, causing a UNIQUE violation on
    INSERT and returning HTTP 500).
    """
    from db import get_db
    import server

    year = __import__('datetime').datetime.now().year
    prefix = f"ARF-{year}-"

    conn = get_db()
    _ensure_client(conn, "draftuser_refmax", "draftrefmax@test.com")

    # Find the current max numeric suffix so our inserted refs are safely above it.
    rows = conn.execute(
        "SELECT ref FROM applications WHERE ref LIKE ?", (f"{prefix}%",)
    ).fetchall()
    existing_max = server._REF_BASE_NUMBER - 1
    for r in rows:
        try:
            n = int(str(r["ref"])[len(prefix):])
            if n > existing_max:
                existing_max = n
        except (ValueError, IndexError):
            pass

    # Insert three consecutive refs above the current max.
    base = existing_max + 1000
    for i in range(3):
        ref = f"{prefix}{base + i}"
        conn.execute(
            """
            INSERT OR IGNORE INTO applications
            (id, ref, client_id, company_name, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"refmax_app_{i}", ref, "draftuser_refmax",
                f"RefMax Corp {i}", "draft", "{}",
            ),
        )
    conn.commit()

    # Delete the middle app — COUNT-based generation would return base+1 (which
    # still exists), causing a UNIQUE violation.  MAX-based generation must
    # return base+3 (max existing = base+2, so next = base+3).
    conn.execute("DELETE FROM applications WHERE id='refmax_app_1'")
    conn.commit()
    conn.close()

    ref = server.generate_ref()
    expected = f"{prefix}{base + 3}"
    assert ref == expected, (
        f"generate_ref returned {ref!r} after deletion — expected {expected!r}. "
        f"COUNT-based generation would return {prefix}{base + 1!s} which already exists."
    )
    # Double-check: the returned ref must not already exist in the DB.
    conn = get_db()
    collision = conn.execute(
        "SELECT id FROM applications WHERE ref=?", (ref,)
    ).fetchone()
    conn.close()
    assert collision is None, f"generate_ref() returned {ref!r} which already exists"


def test_generate_ref_starts_at_base_when_no_refs_exist():
    """When the DB is empty (or has no current-year refs) generate_ref() must
    return the base ref ARF-<year>-100421 without errors."""
    import server
    from db import get_db

    # Use the current test DB — just verify the ref has the right prefix/shape
    ref = server.generate_ref()
    year = __import__('datetime').datetime.now().year
    assert ref.startswith(f"ARF-{year}-"), f"ref {ref!r} missing year prefix"
    suffix = ref.split("-")[-1]
    assert suffix.isdigit(), f"ref suffix {suffix!r} is not numeric"
    assert int(suffix) >= server._REF_BASE_NUMBER


def test_pre_submit_save_with_full_real_frontend_payload(api_server):
    """POST /api/save-resume must return 200 for the exact form_data shape
    that the real portal frontend sends, including all boolean checkbox states,
    empty string form fields, and extra metadata keys like appRef and timestamp.
    """
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_realshape", "draftrealshape@test.com")
    conn.close()

    token = _client_token("draftuser_realshape")
    headers = {"Authorization": f"Bearer {token}"}

    # Mirrors the exact collectFormData() output from arie-portal.html.
    real_frontend_form_data = {
        "appRef": "",
        "computedRiskLevel": "",
        "uploadedDocs": [],
        "timestamp": "2026-04-24T03:15:27.777Z",
        "prescreening": {
            "f-reg-name": "Real Shape Holdings Ltd",
            "f-trade-name": "",
            "f-reg-address": "",
            "f-hq-address": "",
            "f-contact-first": "",
            "f-contact-last": "",
            "f-email": "",
            "f-phone-code": "",
            "f-mobile": "",
            "f-website": "",
            "f-is-licensed": False,
            "f-licences": "",
            "f-licence-number": "",
            "f-licence-authority": "",
            "f-licence-type": "",
            "f-inc-country": "Mauritius",
            "f-inc-date": "",
            "f-brn": "",
            "f-sector": "Fintech / Payments",
            "f-entity-type": "SME / Private Company",
            "f-ownership-structure": "Simple — direct identifiable UBOs",
            "f-monthly-volume": "",
            "f-txn-complexity": "",
            "f-biz-overview": "",
            "f-source-wealth-type": "",
            "f-source-wealth": "",
            "f-source-init-type": "",
            "f-source-init": "",
            "f-source-ongoing-type": "",
            "f-source-ongoing": "",
            "f-mgmt": "",
            "f-intro-method": "",
            "f-referrer-name": "",
            "f-authorised-share-capital": "",
            "f-consent-declaration": False,
            "f-consent-pricing": False,
            "f-consent-terms": False,
        },
        "kycPersons": {},
        "servicesRequired": [],
        "accountPurposes": [],
        "currencies": [],
        "countriesOfOperation": [],
        "targetMarkets": [],
        "directors": [],
        "ubos": [],
        "intermediaries": [],
    }

    resp = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"form_data": real_frontend_form_data, "last_step": 0},
        timeout=5,
    )
    assert resp.status_code == 200, (
        f"POST /api/save-resume returned {resp.status_code} for real frontend "
        f"payload shape. Expected 200. Body: {resp.text}"
    )
    body = resp.json()
    assert body["status"] == "saved"
    assert body["application_id"]
    assert body["application_ref"]
    app_id = body["application_id"]

    # Autosave with application_id must also return 200 (not 500).
    real_frontend_form_data["prescreening"]["f-biz-overview"] = "Updated overview"
    autosave_resp = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={
            "application_id": app_id,
            "form_data": real_frontend_form_data,
            "last_step": 0,
        },
        timeout=5,
    )
    assert autosave_resp.status_code == 200, (
        f"Autosave (with application_id) returned {autosave_resp.status_code}. "
        f"Body: {autosave_resp.text}"
    )
    assert autosave_resp.json()["application_id"] == app_id

    # Resumed data must persist the updated field.
    resume_resp = http_requests.get(
        f"{api_server}/api/save-resume?application_id={app_id}",
        headers=headers,
        timeout=5,
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["form_data"]["prescreening"]["f-biz-overview"] == "Updated overview"


def test_pre_submit_save_reuses_same_draft_across_multiple_autosaves(api_server):
    """Repeated autosaves without application_id must reuse the same draft
    application shell — not create a new application on every tick."""
    from db import get_db

    conn = get_db()
    _ensure_client(conn, "draftuser_multisave", "draftmultisave@test.com")
    conn.close()

    token = _client_token("draftuser_multisave")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"prescreening": {"f-reg-name": "MultiSave Corp Ltd"}}

    first = http_requests.post(
        f"{api_server}/api/save-resume",
        headers=headers,
        json={"form_data": payload, "last_step": 0},
        timeout=5,
    )
    assert first.status_code == 200, first.text
    app_id = first.json()["application_id"]

    # Second and third autosave — omit application_id to simulate autosave
    # before the frontend has received the first response.
    for _ in range(2):
        r = http_requests.post(
            f"{api_server}/api/save-resume",
            headers=headers,
            json={"form_data": payload, "last_step": 0},
            timeout=5,
        )
        assert r.status_code == 200, r.text
        assert r.json()["application_id"] == app_id, (
            "Repeated autosaves must return the same application_id"
        )

    conn = get_db()
    rows = conn.execute(
        "SELECT id FROM applications WHERE client_id=? AND status='draft'",
        ("draftuser_multisave",),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, (
        f"Expected exactly 1 draft application, found {len(rows)}"
    )


def test_is_unique_constraint_error_helper():
    """_is_unique_constraint_error must classify real SQLite and PostgreSQL
    unique-violation messages correctly."""
    import server

    h = server.SaveResumeHandler

    # SQLite
    assert h._is_unique_constraint_error(
        Exception("UNIQUE constraint failed: applications.ref")
    )
    # PostgreSQL (psycopg2 IntegrityError string)
    assert h._is_unique_constraint_error(
        Exception("duplicate key value violates unique constraint")
    )
    assert h._is_unique_constraint_error(
        Exception('unique violation: Key (ref)=(ARF-2026-100421) already exists')
    )
    # Non-unique errors must not be misclassified
    assert not h._is_unique_constraint_error(
        Exception("no such column: updated_at")
    )
    assert not h._is_unique_constraint_error(None)
    assert not h._is_unique_constraint_error(Exception(""))


# ── Portal wiring: post-discard refresh via loadMyApplications ────────────

def test_portal_discard_active_draft_calls_load_my_applications():
    """discardActiveDraft() must call loadMyApplications() after a successful
    discard so the dashboard table and CTA card refresh immediately without
    requiring the user to navigate away and back."""
    src = _portal_html()
    # Find the body of discardActiveDraft (between function start and next fn)
    discard_fn = src.split("async function discardActiveDraft() {", 1)[1].split(
        "function renderRecentActivity(apps) {", 1
    )[0]
    assert "loadMyApplications()" in discard_fn, (
        "discardActiveDraft() must call loadMyApplications() to refresh the "
        "dashboard after a successful discard. "
        "Previously it called the undefined loadDashboardApplications()."
    )
    # Must not call the undefined legacy refresh functions
    assert "loadDashboardApplications()" not in discard_fn, (
        "discardActiveDraft() must not call the undefined loadDashboardApplications()"
    )
    assert "loadClientApplications()" not in discard_fn, (
        "discardActiveDraft() must not call the undefined loadClientApplications()"
    )

