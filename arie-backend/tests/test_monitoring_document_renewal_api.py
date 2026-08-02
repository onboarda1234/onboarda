"""HTTP and static safety contracts for Monitoring document renewal.

The integration cases exercise the public handler boundary against an isolated
database.  The static guards complement those tests by proving that the staged
portal-upload handler cannot silently drift back to the legacy document-refresh
or canonical-document mutation paths.
"""

from __future__ import annotations

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

import pytest
import requests
import tornado.httpserver
import tornado.ioloop


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = BACKEND_ROOT / "server.py"
sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def renewal_api_server(tmp_path_factory):
    work = tmp_path_factory.mktemp("monitoring_document_renewal_api")
    db_path = str(work / "renewal-api.db")
    upload_dir = str(work / "uploads")
    old_environment = {
        key: os.environ.get(key)
        for key in (
            "DB_PATH",
            "DATABASE_URL",
            "UPLOAD_DIR",
            "ENABLE_DOCUMENT_RENEWAL_AUTOMATION",
        )
    }
    os.environ.update(
        {
            "DB_PATH": db_path,
            "DATABASE_URL": "",
            "UPLOAD_DIR": upload_dir,
            "ENABLE_DOCUMENT_RENEWAL_AUTOMATION": "true",
        }
    )

    import importlib
    config_module = None
    db_module = None
    environment_module = None
    renewal_module = None
    server_module = None
    thread = None
    server_ref = {}
    try:
        import config as config_module
        import db as db_module
        import environment as environment_module

        importlib.reload(environment_module)
        importlib.reload(config_module)
        importlib.reload(db_module)
        db_module._DB_PATH = db_path
        db_module.init_db()
        conn = db_module.get_db()
        try:
            _seed_actors(conn)
            conn.commit()
        finally:
            conn.close()

        import monitoring_document_renewal as renewal_module
        import server as server_module

        importlib.reload(renewal_module)
        importlib.reload(server_module)
        # A successful testing upload deliberately remains local.  Network-backed
        # object storage is neither required nor contacted by this API contract.
        server_module.HAS_S3 = False
        app = server_module.make_app()
        port = _free_port()
        started = threading.Event()

        def run_server():
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            io_loop = tornado.ioloop.IOLoop.current()
            server = tornado.httpserver.HTTPServer(app)
            server.listen(port, "127.0.0.1")
            server_ref["server"] = server
            server_ref["loop"] = io_loop
            started.set()
            io_loop.start()

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        assert started.wait(timeout=3), "renewal API test server did not start"
        time.sleep(0.2)
        yield f"http://127.0.0.1:{port}", db_module, server_module
    finally:
        try:
            if thread is not None:
                from tests.conftest import shutdown_test_http_server

                shutdown_test_http_server(thread, server_ref)
        finally:
            for key, value in old_environment.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            # This module-scoped fixture reloads runtime modules while the
            # renewal feature is enabled. Restore the process-wide module state
            # even when setup or server shutdown fails so later protected tests
            # cannot inherit the enabled flag, isolated database path, or local
            # upload backend through sys.modules.
            for module in (
                environment_module,
                config_module,
                db_module,
                renewal_module,
                server_module,
            ):
                if module is not None:
                    importlib.reload(module)


def _seed_actors(conn):
    for user_id, email, name, role in (
        ("renewal_admin", "renewal-admin@example.test", "Renewal Admin", "admin"),
        ("renewal_co", "renewal-co@example.test", "Renewal Officer", "co"),
        (
            "renewal_analyst",
            "renewal-analyst@example.test",
            "Renewal Analyst",
            "analyst",
        ),
    ):
        conn.execute(
            """INSERT OR REPLACE INTO users
                   (id, email, password_hash, full_name, role, status)
               VALUES (?, ?, 'unused', ?, ?, 'active')""",
            (user_id, email, name, role),
        )
    for client_id, email, company in (
        ("renewal_client_a", "renewal-client-a@example.test", "Renewal Client A"),
        ("renewal_client_b", "renewal-client-b@example.test", "Renewal Client B"),
    ):
        conn.execute(
            """INSERT OR REPLACE INTO clients
                   (id, email, password_hash, company_name, status)
               VALUES (?, ?, 'unused', ?, 'active')""",
            (client_id, email, company),
        )


def _seed_case(db_module, suffix, *, client_id="renewal_client_a"):
    application_id = f"renewal_app_{suffix}"
    application_ref = f"ARF-RENEWAL-{suffix.upper()}"
    person_id = f"renewal_director_{suffix}"
    document_id = f"renewal_doc_{suffix}"
    stable_number = int.from_bytes(
        hashlib.sha256(suffix.encode("utf-8")).digest()[:4], "big"
    ) % 100000
    alert_id = int(f"58{stable_number:05d}")
    conn = db_module.get_db()
    try:
        conn.execute(
            """INSERT INTO applications
                   (id, ref, client_id, company_name, status, risk_level)
               VALUES (?, ?, ?, ?, 'approved', 'MEDIUM')""",
            (application_id, application_ref, client_id, f"Renewal {suffix} Ltd"),
        )
        conn.execute(
            """INSERT INTO directors (id, application_id, person_key, full_name)
               VALUES (?, ?, ?, ?)""",
            (person_id, application_id, person_id, f"Director {suffix}"),
        )
        conn.execute(
            """INSERT INTO documents
                   (id, application_id, person_id, person_type, doc_type,
                    doc_name, file_path, file_size, mime_type, slot_key,
                    is_current, version, expiry_date, verification_status,
                    review_status)
               VALUES (?, ?, ?, 'director', 'passport', ?, ?, 64,
                       'application/pdf', ?, 1, 1, '2020-01-01',
                       'verified', 'accepted')""",
            (
                document_id,
                application_id,
                person_id,
                f"Expired passport {suffix}.pdf",
                f"fixtures/{document_id}.pdf",
                f"person:director:{person_id}:passport",
            ),
        )
        conn.execute(
            """INSERT INTO monitoring_alerts
                   (id, application_id, client_name, alert_type, severity,
                    detected_by, summary, source_reference, status,
                    discovered_via)
               VALUES (?, ?, ?, 'document_expired', 'medium',
                       'Document Health Monitor', ?, ?, 'open',
                       'document_health')""",
            (
                alert_id,
                application_id,
                f"Renewal {suffix} Ltd",
                f"Passport expired for {suffix}",
                f"document:{document_id}",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "application_id": application_id,
        "application_ref": application_ref,
        "client_id": client_id,
        "person_id": person_id,
        "document_id": document_id,
        "alert_id": alert_id,
    }


def _token(user_id, role, name, user_type="officer"):
    from auth import create_token

    return create_token(user_id, role, name, user_type)


def _admin_token():
    return _token("renewal_admin", "admin", "Renewal Admin")


def _analyst_token():
    return _token("renewal_analyst", "analyst", "Renewal Analyst")


def _client_token(client_id):
    return _token(client_id, "client", client_id, "client")


def _json_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _create(base_url, case, token=None):
    return requests.post(
        f"{base_url}/api/monitoring/alerts/{case['alert_id']}/renewal-request",
        headers=_json_headers(token or _admin_token()),
        json={
            "reason": "expired",
            "due_date": (date.today() + timedelta(days=21)).isoformat(),
        },
        timeout=10,
    )


@contextmanager
def _feature_state(server_module, enabled):
    renewal = server_module._monitoring_document_renewal
    original = renewal.renewal_feature_enabled
    renewal.renewal_feature_enabled = lambda _feature_flags=None: enabled
    try:
        yield
    finally:
        renewal.renewal_feature_enabled = original


def _db_counts(db_module, case):
    conn = db_module.get_db()
    try:
        return {
            "requests": conn.execute(
                """SELECT COUNT(*) AS c
                     FROM monitoring_document_renewal_requests
                    WHERE monitoring_alert_id = ?""",
                (case["alert_id"],),
            ).fetchone()["c"],
            "events": conn.execute(
                """SELECT COUNT(*) AS c
                     FROM monitoring_document_renewal_events e
                     JOIN monitoring_document_renewal_requests r
                       ON r.request_id = e.request_id
                    WHERE r.monitoring_alert_id = ?""",
                (case["alert_id"],),
            ).fetchone()["c"],
            "uploads": conn.execute(
                """SELECT COUNT(*) AS c
                     FROM monitoring_document_renewal_uploads u
                     JOIN monitoring_document_renewal_requests r
                       ON r.request_id = u.request_id
                    WHERE r.monitoring_alert_id = ?""",
                (case["alert_id"],),
            ).fetchone()["c"],
            "documents": conn.execute(
                "SELECT COUNT(*) AS c FROM documents WHERE application_id = ?",
                (case["application_id"],),
            ).fetchone()["c"],
            "executions": conn.execute(
                "SELECT COUNT(*) AS c FROM agent_executions WHERE application_id = ?",
                (case["application_id"],),
            ).fetchone()["c"],
            "legacy": conn.execute(
                """SELECT COUNT(*) AS c
                     FROM application_enhanced_requirements
                    WHERE monitoring_alert_id = ?""",
                (case["alert_id"],),
            ).fetchone()["c"],
            "alert_status": conn.execute(
                "SELECT status FROM monitoring_alerts WHERE id = ?",
                (case["alert_id"],),
            ).fetchone()["status"],
        }
    finally:
        conn.close()


def test_officer_create_resend_due_date_cancel_http_contract(renewal_api_server):
    base_url, db_module, _server_module = renewal_api_server
    case = _seed_case(db_module, "lifecycle")
    token = _admin_token()

    created = _create(base_url, case, token)
    assert created.status_code == 201, created.text
    request = created.json()["request"]
    assert request["application_id"] == case["application_id"]
    assert request["document_id"] == case["document_id"]
    assert str(request["monitoring_alert_id"]) == str(case["alert_id"])
    assert request["request_status"] == "awaiting_upload"
    # Creation and dispatch are two audited state changes in one transaction.
    assert request["revision"] == 2

    read = requests.get(
        f"{base_url}/api/monitoring/alerts/{case['alert_id']}/renewal-request",
        headers=_auth_headers(token),
        timeout=10,
    )
    assert read.status_code == 200, read.text
    assert read.json()["request"]["request_id"] == request["request_id"]

    resent = requests.post(
        f"{base_url}/api/monitoring/renewal-requests/{request['request_id']}/resend",
        headers=_json_headers(token),
        json={"expected_revision": 2},
        timeout=10,
    )
    assert resent.status_code == 200, resent.text
    assert resent.json()["request"]["revision"] == 3

    new_due = (date.today() + timedelta(days=30)).isoformat()
    due = requests.patch(
        f"{base_url}/api/monitoring/renewal-requests/{request['request_id']}/due-date",
        headers=_json_headers(token),
        json={
            "expected_revision": 3,
            "due_date": new_due,
            "reason": "Client requested an agreed extension.",
        },
        timeout=10,
    )
    assert due.status_code == 200, due.text
    assert due.json()["request"]["due_date"] == new_due
    assert due.json()["request"]["revision"] == 4

    cancelled = requests.post(
        f"{base_url}/api/monitoring/renewal-requests/{request['request_id']}/cancel",
        headers=_json_headers(token),
        json={"expected_revision": 4, "reason": "Request withdrawn by officer."},
        timeout=10,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["request"]["request_status"] == "cancelled"
    assert cancelled.json()["request"]["revision"] == 5

    counts = _db_counts(db_module, case)
    assert counts["requests"] == 1
    assert counts["events"] == 5
    assert counts["documents"] == 1
    assert counts["executions"] == 0
    assert counts["legacy"] == 0
    assert counts["alert_status"] == "open"


def test_officer_routes_enforce_rbac_required_fields_and_methods(renewal_api_server):
    base_url, db_module, _server_module = renewal_api_server
    case = _seed_case(db_module, "rbac")
    endpoint = f"{base_url}/api/monitoring/alerts/{case['alert_id']}/renewal-request"

    assert requests.post(endpoint, json={"reason": "expired"}, timeout=10).status_code == 401
    assert _create(base_url, case, _analyst_token()).status_code == 403
    assert _create(base_url, case, _client_token("renewal_client_a")).status_code == 403
    assert requests.patch(
        endpoint,
        headers=_json_headers(_admin_token()),
        json={"reason": "expired"},
        timeout=10,
    ).status_code == 405

    created = _create(base_url, case)
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["request_id"]
    action_base = f"{base_url}/api/monitoring/renewal-requests/{request_id}"
    assert requests.get(
        f"{action_base}/resend", headers=_auth_headers(_admin_token()), timeout=10
    ).status_code == 405
    assert requests.patch(
        f"{action_base}/due-date",
        headers=_json_headers(_admin_token()),
        json={"expected_revision": 1, "due_date": (date.today() + timedelta(days=22)).isoformat()},
        timeout=10,
    ).status_code == 400
    for action in ("resend", "cancel"):
        response = requests.post(
            f"{action_base}/{action}",
            headers=_json_headers(_admin_token()),
            json={},
            timeout=10,
        )
        assert response.status_code == 400


def test_portal_own_application_list_and_staged_upload_are_safe(renewal_api_server):
    base_url, db_module, _server_module = renewal_api_server
    case = _seed_case(db_module, "portal")
    created = _create(base_url, case)
    assert created.status_code == 201, created.text
    request = created.json()["request"]
    client_token = _client_token(case["client_id"])

    listed = requests.get(
        f"{base_url}/api/portal/applications/{case['application_id']}/renewal-requests",
        headers=_auth_headers(client_token),
        timeout=10,
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["application_id"] == case["application_id"]
    assert body["feature_enabled"] is True
    assert body["total"] == 1
    public_request = body["renewal_requests"][0]
    assert public_request["request_id"] == request["request_id"]
    subject_ref = hashlib.sha256(
        f"{case['application_id']}:director:{case['person_id']}".encode("utf-8")
    ).hexdigest()[:8].upper()
    assert public_request["subject_display_label"] == (
        f"Director — Director portal · ref {subject_ref}"
    )
    for private_field in (
        "customer_id",
        "person_id",
        "person_type",
        "document_id",
        "document_version",
        "monitoring_alert_id",
        "created_by",
        "cancel_reason",
        "storage_key",
        "file_sha256",
    ):
        assert private_field not in public_request

    before = _db_counts(db_module, case)
    uploaded = requests.post(
        f"{base_url}/api/portal/applications/{case['application_id']}"
        f"/renewal-requests/{request['request_id']}/upload",
        headers=_auth_headers(client_token),
        files={
            "file": (
                "renewed-passport.pdf",
                b"%PDF-1.4\n% staged renewal candidate\n%%EOF",
                "application/pdf",
            )
        },
        timeout=10,
    )
    assert uploaded.status_code == 201, uploaded.text
    upload_body = uploaded.json()
    assert upload_body["status"] == "upload_received"
    assert upload_body["request"]["request_status"] == "upload_received"
    serialized = json.dumps(upload_body)
    assert "storage_key" not in serialized
    assert "file_sha256" not in serialized

    after = _db_counts(db_module, case)
    assert after["uploads"] == before["uploads"] + 1
    assert after["documents"] == before["documents"] == 1
    assert after["executions"] == before["executions"] == 0
    assert after["legacy"] == before["legacy"] == 0
    assert after["alert_status"] == before["alert_status"] == "open"
    conn = db_module.get_db()
    try:
        artifact = dict(
            conn.execute(
                """
                SELECT artifact_state, backend, local_path, storage_key,
                       s3_version_id
                  FROM monitoring_document_renewal_upload_cleanup
                 WHERE request_id = ?
                """,
                (request["request_id"],),
            ).fetchone()
        )
        notification = dict(
            conn.execute(
                """
                SELECT documents_list, message FROM client_notifications
                 WHERE application_id = ?
                 ORDER BY id ASC LIMIT 1
                """,
                (case["application_id"],),
            ).fetchone()
        )
    finally:
        conn.close()
    assert artifact["artifact_state"] == "attached"
    assert artifact["backend"] == "local"
    assert artifact["s3_version_id"] is None
    assert artifact["storage_key"].startswith(
        "local://monitoring-renewal-candidates/"
    )
    assert (os.stat(artifact["local_path"]).st_mode & 0o777) == 0o600
    assert "Director portal" in notification["documents_list"]
    assert "Director portal" in notification["message"]

    officer_list = requests.get(
        f"{base_url}/api/portal/applications/{case['application_id']}/renewal-requests",
        headers=_auth_headers(_admin_token()),
        timeout=10,
    )
    assert officer_list.status_code == 403


def test_cross_client_routes_return_404_and_do_not_leak_request(renewal_api_server):
    base_url, db_module, _server_module = renewal_api_server
    owned = _seed_case(db_module, "cross-owned", client_id="renewal_client_a")
    foreign = _seed_case(db_module, "cross-foreign", client_id="renewal_client_b")
    owned_request = _create(base_url, owned).json()["request"]
    foreign_request = _create(base_url, foreign).json()["request"]
    token_a = _client_token("renewal_client_a")

    foreign_list = requests.get(
        f"{base_url}/api/portal/applications/{foreign['application_id']}/renewal-requests",
        headers=_auth_headers(token_a),
        timeout=10,
    )
    assert foreign_list.status_code == 404
    assert foreign_request["request_id"] not in foreign_list.text

    cross_request = requests.post(
        f"{base_url}/api/portal/applications/{owned['application_id']}"
        f"/renewal-requests/{foreign_request['request_id']}/upload",
        headers=_auth_headers(token_a),
        files={
            "file": (
                "cross-client.pdf",
                b"%PDF-1.4\n% cross client must fail\n%%EOF",
                "application/pdf",
            )
        },
        timeout=10,
    )
    assert cross_request.status_code == 404
    assert foreign_request["request_id"] not in cross_request.text

    assert _db_counts(db_module, owned)["uploads"] == 0
    assert _db_counts(db_module, foreign)["uploads"] == 0
    assert owned_request["request_id"] != foreign_request["request_id"]


def test_same_client_cannot_swap_request_between_applications(renewal_api_server):
    base_url, db_module, _server_module = renewal_api_server
    first = _seed_case(db_module, "same-client-first", client_id="renewal_client_a")
    second = _seed_case(db_module, "same-client-second", client_id="renewal_client_a")
    first_request = _create(base_url, first).json()["request"]
    second_request = _create(base_url, second).json()["request"]
    token = _client_token("renewal_client_a")

    swapped = requests.post(
        f"{base_url}/api/portal/applications/{first['application_id']}"
        f"/renewal-requests/{second_request['request_id']}/upload",
        headers=_auth_headers(token),
        files={
            "file": (
                "wrong-application.pdf",
                b"%PDF-1.4\n% exact application binding\n%%EOF",
                "application/pdf",
            )
        },
        timeout=10,
    )
    assert swapped.status_code == 404
    assert second_request["request_id"] not in swapped.text
    assert _db_counts(db_module, first)["uploads"] == 0
    assert _db_counts(db_module, second)["uploads"] == 0
    assert first_request["request_id"] != second_request["request_id"]


def test_portal_distinguishes_same_document_type_for_two_people(renewal_api_server):
    base_url, db_module, _server_module = renewal_api_server
    case = _seed_case(db_module, "two-people")
    second_person_id = "renewal_director_two_people_second"
    second_document_id = "renewal_doc_two_people_second"
    second_alert_id = case["alert_id"] + 10_000_000
    conn = db_module.get_db()
    try:
        conn.execute(
            "INSERT INTO directors (id, application_id, person_key, full_name) VALUES (?, ?, ?, ?)",
            (
                second_person_id,
                case["application_id"],
                second_person_id,
                "Director two-people",
            ),
        )
        conn.execute(
            """
            INSERT INTO documents
                (id, application_id, person_id, person_type, doc_type,
                 doc_name, file_path, file_size, mime_type, slot_key,
                 is_current, version, expiry_date, verification_status,
                 review_status)
            VALUES (?, ?, ?, 'director', 'passport', ?, ?, 64,
                    'application/pdf', ?, 1, 1, '2020-01-01',
                    'verified', 'accepted')
            """,
            (
                second_document_id,
                case["application_id"],
                second_person_id,
                "Second director passport.pdf",
                f"fixtures/{second_document_id}.pdf",
                f"person:director:{second_person_id}:passport",
            ),
        )
        conn.execute(
            """
            INSERT INTO monitoring_alerts
                (id, application_id, client_name, alert_type, severity,
                 detected_by, summary, source_reference, status,
                 discovered_via)
            VALUES (?, ?, 'Renewal two-people Ltd', 'document_expired',
                    'medium', 'Document Health Monitor',
                    'Second director passport expired', ?, 'open',
                    'document_health')
            """,
            (second_alert_id, case["application_id"], f"document:{second_document_id}"),
        )
        conn.commit()
    finally:
        conn.close()

    first = _create(base_url, case)
    second_case = {**case, "alert_id": second_alert_id, "document_id": second_document_id}
    second = _create(base_url, second_case)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    listed = requests.get(
        f"{base_url}/api/portal/applications/{case['application_id']}/renewal-requests",
        headers=_auth_headers(_client_token(case["client_id"])),
        timeout=10,
    )
    assert listed.status_code == 200, listed.text
    requests_by_label = {
        item["subject_display_label"]: item
        for item in listed.json()["renewal_requests"]
    }
    first_ref = hashlib.sha256(
        f"{case['application_id']}:director:{case['person_id']}".encode("utf-8")
    ).hexdigest()[:8].upper()
    second_ref = hashlib.sha256(
        f"{case['application_id']}:director:{second_person_id}".encode("utf-8")
    ).hexdigest()[:8].upper()
    assert set(requests_by_label) == {
        f"Director — Director two-people · ref {first_ref}",
        f"Director — Director two-people · ref {second_ref}",
    }
    for item in requests_by_label.values():
        assert item["document_type"] == "passport"
        assert "person_id" not in item
        assert "document_id" not in item


def test_feature_off_rejects_every_mutation_before_writes(renewal_api_server):
    base_url, db_module, server_module = renewal_api_server
    case = _seed_case(db_module, "feature-off")
    before = _db_counts(db_module, case)

    with _feature_state(server_module, False):
        create = _create(base_url, case)
        assert create.status_code == 409
        create_error = create.json()
        assert create_error["error_code"] == "feature_disabled"
        assert isinstance(create_error["error"], str)

        for method, path, payload in (
            (
                requests.post,
                "/api/monitoring/renewal-requests/missing/resend",
                {"expected_revision": 1},
            ),
            (
                requests.patch,
                "/api/monitoring/renewal-requests/missing/due-date",
                {
                    "expected_revision": 1,
                    "due_date": (date.today() + timedelta(days=21)).isoformat(),
                    "reason": "Controlled extension.",
                },
            ),
            (
                requests.post,
                "/api/monitoring/renewal-requests/missing/cancel",
                {"expected_revision": 1, "reason": "Controlled cancellation."},
            ),
        ):
            response = method(
                base_url + path,
                headers=_json_headers(_admin_token()),
                json=payload,
                timeout=10,
            )
            assert response.status_code == 409
            response_error = response.json()
            assert response_error["error_code"] == "feature_disabled"
            assert isinstance(response_error["error"], str)

        portal_upload = requests.post(
            f"{base_url}/api/portal/applications/{case['application_id']}"
            "/renewal-requests/missing/upload",
            headers=_auth_headers(_client_token(case["client_id"])),
            files={
                "file": (
                    "feature-off.pdf",
                    b"%PDF-1.4\n% feature off\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=10,
        )
        assert portal_upload.status_code == 409

    assert _db_counts(db_module, case) == before


def test_feature_off_keeps_existing_request_visible_but_read_only(
    renewal_api_server,
):
    base_url, db_module, server_module = renewal_api_server
    case = _seed_case(db_module, "feature-off-existing")
    request = _create(base_url, case).json()["request"]
    before = _db_counts(db_module, case)

    with _feature_state(server_module, False):
        listed = requests.get(
            f"{base_url}/api/portal/applications/{case['application_id']}/renewal-requests",
            headers=_auth_headers(_client_token(case["client_id"])),
            timeout=10,
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["feature_enabled"] is False
        assert listed.json()["renewal_requests"][0]["request_id"] == request["request_id"]
        upload = requests.post(
            f"{base_url}/api/portal/applications/{case['application_id']}"
            f"/renewal-requests/{request['request_id']}/upload",
            headers=_auth_headers(_client_token(case["client_id"])),
            files={
                "file": (
                    "feature-off-existing.pdf",
                    b"%PDF-1.4\n% feature off existing\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=10,
        )
        assert upload.status_code == 409

    assert _db_counts(db_module, case) == before


def test_stale_document_linkage_disables_portal_upload_before_storage(
    renewal_api_server,
):
    base_url, db_module, _server_module = renewal_api_server
    case = _seed_case(db_module, "stale-upload")
    request = _create(base_url, case).json()["request"]
    replacement_id = case["document_id"] + "-v2"
    conn = db_module.get_db()
    try:
        original = dict(
            conn.execute(
                "SELECT * FROM documents WHERE id = ?",
                (case["document_id"],),
            ).fetchone()
        )
        conn.execute(
            "UPDATE documents SET is_current = 0, superseded_by_document_id = ? WHERE id = ?",
            (replacement_id, case["document_id"]),
        )
        conn.execute(
            """
            INSERT INTO documents
                (id, application_id, person_id, person_type, doc_type,
                 doc_name, file_path, file_size, mime_type, slot_key,
                 is_current, version, expiry_date, verification_status,
                 review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 2, '2028-01-01',
                    'pending', 'pending')
            """,
            (
                replacement_id,
                case["application_id"],
                original["person_id"],
                original["person_type"],
                original["doc_type"],
                "Replacement passport.pdf",
                f"fixtures/{replacement_id}.pdf",
                64,
                "application/pdf",
                original["slot_key"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    listed = requests.get(
        f"{base_url}/api/portal/applications/{case['application_id']}/renewal-requests",
        headers=_auth_headers(_client_token(case["client_id"])),
        timeout=10,
    )
    projected = listed.json()["renewal_requests"][0]
    assert projected["manual_review_required"] is True
    assert projected["upload_allowed"] is False

    upload = requests.post(
        f"{base_url}/api/portal/applications/{case['application_id']}"
        f"/renewal-requests/{request['request_id']}/upload",
        headers=_auth_headers(_client_token(case["client_id"])),
        files={
            "file": (
                "stale.pdf",
                b"%PDF-1.4\n% stale must not store\n%%EOF",
                "application/pdf",
            )
        },
        timeout=10,
    )
    assert upload.status_code == 409
    conn = db_module.get_db()
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM monitoring_document_renewal_upload_cleanup WHERE request_id = ?",
            (request["request_id"],),
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM monitoring_document_renewal_uploads WHERE request_id = ?",
            (request["request_id"],),
        ).fetchone()["c"] == 0
    finally:
        conn.close()


def test_s3_success_without_exact_key_fails_closed_and_reconciles_reservation(
    renewal_api_server,
):
    base_url, db_module, server_module = renewal_api_server
    case = _seed_case(db_module, "empty-s3-key")
    request = _create(base_url, case).json()["request"]
    deleted = []

    class EmptyKeyS3:
        @staticmethod
        def build_monitoring_renewal_candidate_key(*args):
            return server_module.build_monitoring_renewal_candidate_key(*args)

        @staticmethod
        def upload_monitoring_renewal_candidate(**_kwargs):
            return True, {"key": "", "version_id": None}

        @staticmethod
        def delete_monitoring_renewal_candidate(key, *, version_id=None):
            deleted.append((key, version_id))
            return True, "candidate_deleted"

    original_has_s3 = server_module.HAS_S3
    original_get_s3 = server_module.get_s3_client
    server_module.HAS_S3 = True
    server_module.get_s3_client = lambda: EmptyKeyS3()
    try:
        upload = requests.post(
            f"{base_url}/api/portal/applications/{case['application_id']}"
            f"/renewal-requests/{request['request_id']}/upload",
            headers=_auth_headers(_client_token(case["client_id"])),
            files={
                "file": (
                    "empty-key.pdf",
                    b"%PDF-1.4\n% empty key must fail\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=10,
        )
    finally:
        server_module.HAS_S3 = original_has_s3
        server_module.get_s3_client = original_get_s3

    assert upload.status_code == 500
    assert deleted and deleted[0][0].startswith("clients/")
    conn = db_module.get_db()
    try:
        artifact = dict(
            conn.execute(
                """
                SELECT artifact_state, last_error_code
                  FROM monitoring_document_renewal_upload_cleanup
                 WHERE request_id = ?
                """,
                (request["request_id"],),
            ).fetchone()
        )
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM monitoring_document_renewal_uploads WHERE request_id = ?",
            (request["request_id"],),
        ).fetchone()["c"] == 0
    finally:
        conn.close()
    assert artifact["artifact_state"] == "cleaned"
    assert artifact["last_error_code"] is None


def _request_row(db_module, request_id):
    conn = db_module.get_db()
    try:
        return dict(
            conn.execute(
                "SELECT * FROM monitoring_document_renewal_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        )
    finally:
        conn.close()


def test_reserved_artifact_lease_blocks_cleanup_and_retry_runs_with_flag_off(
    renewal_api_server,
):
    base_url, db_module, server_module = renewal_api_server
    case = _seed_case(db_module, "artifact-lease")
    public = _create(base_url, case).json()["request"]
    request = _request_row(db_module, public["request_id"])
    upload_id = "a" * 32
    cleanup_id = "b" * 32
    candidate_dir = os.path.join(
        server_module.UPLOAD_DIR,
        "monitoring-renewal-candidates",
    )
    os.makedirs(candidate_dir, mode=0o700, exist_ok=True)
    local_path = os.path.join(candidate_dir, upload_id + ".pdf")
    storage_key = (
        server_module._MONITORING_RENEWAL_LOCAL_STORAGE_PREFIX
        + upload_id
        + ".pdf"
    )
    conn = db_module.get_db()
    try:
        server_module._reserve_monitoring_renewal_upload_artifact(
            conn,
            request=request,
            actor={"sub": case["client_id"], "name": "Client", "role": "client"},
            cleanup_id=cleanup_id,
            upload_id=upload_id,
            backend="local",
            storage_key=storage_key,
            file_extension=".pdf",
            local_path=local_path,
            correlation_id="artifact-lease-test",
        )
    finally:
        conn.close()

    with _feature_state(server_module, False):
        untouched = server_module.process_monitoring_renewal_upload_cleanup(limit=10)
    assert untouched["scanned"] == 0
    conn = db_module.get_db()
    try:
        assert conn.execute(
            "SELECT artifact_state FROM monitoring_document_renewal_upload_cleanup WHERE cleanup_id = ?",
            (cleanup_id,),
        ).fetchone()["artifact_state"] == "reserved"
    finally:
        conn.close()

    server_module._mark_monitoring_renewal_artifact_cleanup_pending(
        cleanup_id,
        error_code="simulated_process_exit",
    )
    with _feature_state(server_module, False):
        cleaned = server_module.process_monitoring_renewal_upload_cleanup(limit=10)
    assert cleaned["cleaned"] == 1
    conn = db_module.get_db()
    try:
        assert conn.execute(
            "SELECT artifact_state FROM monitoring_document_renewal_upload_cleanup WHERE cleanup_id = ?",
            (cleanup_id,),
        ).fetchone()["artifact_state"] == "cleaned"
    finally:
        conn.close()


def test_cleanup_claim_fences_concurrent_upload_attachment(
    renewal_api_server,
    monkeypatch,
):
    base_url, db_module, server_module = renewal_api_server
    case = _seed_case(db_module, "cleanup-race")
    public = _create(base_url, case).json()["request"]
    request = _request_row(db_module, public["request_id"])
    upload_id = "c" * 32
    cleanup_id = "d" * 32
    body = b"%PDF-1.4\n% cleanup race\n%%EOF"
    candidate_dir = os.path.join(
        server_module.UPLOAD_DIR,
        "monitoring-renewal-candidates",
    )
    os.makedirs(candidate_dir, mode=0o700, exist_ok=True)
    local_path = os.path.join(candidate_dir, upload_id + ".pdf")
    storage_key = (
        server_module._MONITORING_RENEWAL_LOCAL_STORAGE_PREFIX
        + upload_id
        + ".pdf"
    )
    conn = db_module.get_db()
    try:
        artifact = server_module._reserve_monitoring_renewal_upload_artifact(
            conn,
            request=request,
            actor={"sub": case["client_id"], "name": "Client", "role": "client"},
            cleanup_id=cleanup_id,
            upload_id=upload_id,
            backend="local",
            storage_key=storage_key,
            file_extension=".pdf",
            local_path=local_path,
        )
        descriptor = os.open(local_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
        server_module._mark_monitoring_renewal_artifact_stored(
            conn,
            artifact,
            actor={"sub": case["client_id"], "name": "Client", "role": "client"},
        )
    finally:
        conn.close()
    server_module._mark_monitoring_renewal_artifact_cleanup_pending(
        cleanup_id,
        error_code="simulated_record_failure",
    )

    entered_delete = threading.Event()
    release_delete = threading.Event()
    real_unlink = os.unlink

    def blocked_unlink(path, *, dir_fd=None):
        entered_delete.set()
        assert release_delete.wait(timeout=5)
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(server_module.os, "unlink", blocked_unlink)
    result = {}

    def run_cleanup():
        result.update(
            server_module._process_monitoring_renewal_upload_artifact(cleanup_id)
        )

    cleanup_thread = threading.Thread(target=run_cleanup, daemon=True)
    cleanup_thread.start()
    assert entered_delete.wait(timeout=5)
    conn = db_module.get_db()
    try:
        with pytest.raises(
            server_module._monitoring_document_renewal.RenewalError
        ) as exc:
            server_module._monitoring_document_renewal.record_renewal_upload(
                conn,
                request["request_id"],
                actor={
                    "sub": case["client_id"],
                    "name": "Client",
                    "role": "client",
                    "type": "client",
                },
                expected_revision=request["revision"],
                artifact_reservation_id=cleanup_id,
                upload_id=upload_id,
                original_filename="race.pdf",
                storage_key=storage_key,
                file_size=len(body),
                mime_type="application/pdf",
                file_sha256=hashlib.sha256(body).hexdigest(),
            )
        assert exc.value.code == "artifact_reservation_mismatch"
    finally:
        conn.close()
        release_delete.set()
    cleanup_thread.join(timeout=5)
    assert not cleanup_thread.is_alive()
    assert result["cleaned"] is True
    conn = db_module.get_db()
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM monitoring_document_renewal_uploads WHERE request_id = ?",
            (request["request_id"],),
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT artifact_state FROM monitoring_document_renewal_upload_cleanup WHERE cleanup_id = ?",
            (cleanup_id,),
        ).fetchone()["artifact_state"] == "cleaned"
    finally:
        conn.close()


def test_s3_artifact_cleanup_is_exact_idempotent_and_never_deletes_attached(
    renewal_api_server,
):
    base_url, db_module, server_module = renewal_api_server
    case = _seed_case(db_module, "s3-cleanup")
    public = _create(base_url, case).json()["request"]
    request = _request_row(db_module, public["request_id"])
    upload_id = "e" * 32
    cleanup_id = "f" * 32
    storage_key = server_module.build_monitoring_renewal_candidate_key(
        case["client_id"], request["request_id"], upload_id, "candidate.pdf"
    )
    conn = db_module.get_db()
    try:
        artifact = server_module._reserve_monitoring_renewal_upload_artifact(
            conn,
            request=request,
            actor={"sub": case["client_id"], "name": "Client", "role": "client"},
            cleanup_id=cleanup_id,
            upload_id=upload_id,
            backend="s3",
            storage_key=storage_key,
            file_extension=".pdf",
        )
        server_module._mark_monitoring_renewal_artifact_stored(
            conn,
            artifact,
            actor={"sub": case["client_id"], "name": "Client", "role": "client"},
            s3_version_id="exact-version-1",
        )
    finally:
        conn.close()
    server_module._mark_monitoring_renewal_artifact_cleanup_pending(
        cleanup_id,
        error_code="simulated_crash_after_put",
    )

    deletes = []

    class ExactDeleteS3:
        @staticmethod
        def delete_monitoring_renewal_candidate(key, *, version_id=None):
            deletes.append((key, version_id))
            return True, "candidate_deleted"

    original_has_s3 = server_module.HAS_S3
    original_get_s3 = server_module.get_s3_client
    server_module.HAS_S3 = True
    server_module.get_s3_client = lambda: ExactDeleteS3()
    try:
        with _feature_state(server_module, False):
            first = server_module.process_monitoring_renewal_upload_cleanup(limit=10)
            second = server_module.process_monitoring_renewal_upload_cleanup(limit=10)
    finally:
        server_module.HAS_S3 = original_has_s3
        server_module.get_s3_client = original_get_s3
    assert first["cleaned"] == 1
    assert second["scanned"] == 0
    assert deletes == [(storage_key, "exact-version-1")]

    attached_case = _seed_case(db_module, "s3-attached")
    attached_public = _create(base_url, attached_case).json()["request"]
    attached_request = _request_row(db_module, attached_public["request_id"])
    attached_upload_id = "1" * 32
    attached_cleanup_id = "2" * 32
    attached_key = server_module.build_monitoring_renewal_candidate_key(
        attached_case["client_id"],
        attached_request["request_id"],
        attached_upload_id,
        "candidate.pdf",
    )
    conn = db_module.get_db()
    try:
        attached_artifact = server_module._reserve_monitoring_renewal_upload_artifact(
            conn,
            request=attached_request,
            actor={"sub": attached_case["client_id"], "name": "Client", "role": "client"},
            cleanup_id=attached_cleanup_id,
            upload_id=attached_upload_id,
            backend="s3",
            storage_key=attached_key,
            file_extension=".pdf",
        )
        server_module._mark_monitoring_renewal_artifact_stored(
            conn,
            attached_artifact,
            actor={"sub": attached_case["client_id"], "name": "Client", "role": "client"},
            s3_version_id="exact-version-2",
        )
        conn.execute(
            """
            INSERT INTO monitoring_document_renewal_uploads
                (upload_id, request_id, application_id, customer_id,
                 original_filename, storage_key, file_size, mime_type,
                 file_sha256, uploaded_by)
            VALUES (?, ?, ?, ?, 'candidate.pdf', ?, 10,
                    'application/pdf', ?, ?)
            """,
            (
                attached_upload_id,
                attached_request["request_id"],
                attached_request["application_id"],
                attached_request["customer_id"],
                attached_key,
                "a" * 64,
                attached_case["client_id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    server_module._mark_monitoring_renewal_artifact_cleanup_pending(
        attached_cleanup_id,
        error_code="simulated_crash_after_commit",
    )
    delete_count = len(deletes)
    server_module.HAS_S3 = True
    server_module.get_s3_client = lambda: ExactDeleteS3()
    try:
        reconciled = server_module.process_monitoring_renewal_upload_cleanup(limit=10)
    finally:
        server_module.HAS_S3 = original_has_s3
        server_module.get_s3_client = original_get_s3
    assert reconciled["attached"] == 1
    assert len(deletes) == delete_count
    conn = db_module.get_db()
    try:
        assert conn.execute(
            "SELECT artifact_state FROM monitoring_document_renewal_upload_cleanup WHERE cleanup_id = ?",
            (attached_cleanup_id,),
        ).fetchone()["artifact_state"] == "attached"
    finally:
        conn.close()


def test_s3_artifact_cleanup_failure_backs_off_then_retries_exact_version(
    renewal_api_server,
):
    base_url, db_module, server_module = renewal_api_server
    case = _seed_case(db_module, "s3-cleanup-retry")
    public = _create(base_url, case).json()["request"]
    request = _request_row(db_module, public["request_id"])
    upload_id = "8" * 32
    cleanup_id = "9" * 32
    storage_key = server_module.build_monitoring_renewal_candidate_key(
        case["client_id"], request["request_id"], upload_id, "candidate.pdf"
    )
    conn = db_module.get_db()
    try:
        artifact = server_module._reserve_monitoring_renewal_upload_artifact(
            conn,
            request=request,
            actor={"sub": case["client_id"], "name": "Client", "role": "client"},
            cleanup_id=cleanup_id,
            upload_id=upload_id,
            backend="s3",
            storage_key=storage_key,
            file_extension=".pdf",
        )
        server_module._mark_monitoring_renewal_artifact_stored(
            conn,
            artifact,
            actor={"sub": case["client_id"], "name": "Client", "role": "client"},
            s3_version_id="retry-version-1",
        )
    finally:
        conn.close()
    server_module._mark_monitoring_renewal_artifact_cleanup_pending(
        cleanup_id,
        error_code="simulated_record_failure",
    )

    deletes = []

    class RetryDeleteS3:
        @staticmethod
        def delete_monitoring_renewal_candidate(key, *, version_id=None):
            deletes.append((key, version_id))
            if len(deletes) == 1:
                return False, "temporary_delete_failure"
            return True, "candidate_deleted"

    original_has_s3 = server_module.HAS_S3
    original_get_s3 = server_module.get_s3_client
    server_module.HAS_S3 = True
    server_module.get_s3_client = lambda: RetryDeleteS3()
    try:
        first = server_module.process_monitoring_renewal_upload_cleanup(limit=10)
        immediate = server_module.process_monitoring_renewal_upload_cleanup(limit=10)
        conn = db_module.get_db()
        try:
            deferred = dict(
                conn.execute(
                    """
                    SELECT artifact_state, attempts, last_error_code, next_retry_at
                      FROM monitoring_document_renewal_upload_cleanup
                     WHERE cleanup_id = ?
                    """,
                    (cleanup_id,),
                ).fetchone()
            )
            conn.execute(
                """
                UPDATE monitoring_document_renewal_upload_cleanup
                   SET next_retry_at = ?
                 WHERE cleanup_id = ?
                """,
                (
                    (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                    cleanup_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        retried = server_module.process_monitoring_renewal_upload_cleanup(limit=10)
    finally:
        server_module.HAS_S3 = original_has_s3
        server_module.get_s3_client = original_get_s3

    assert first == {"scanned": 1, "cleaned": 0, "attached": 0, "deferred": 1}
    assert immediate["scanned"] == 0
    assert deferred["artifact_state"] == "cleanup_pending"
    assert deferred["attempts"] == 1
    assert deferred["last_error_code"] == "temporary_delete_failure"
    assert deferred["next_retry_at"]
    assert retried["cleaned"] == 1
    assert deletes == [
        (storage_key, "retry-version-1"),
        (storage_key, "retry-version-1"),
    ]
    conn = db_module.get_db()
    try:
        final = dict(
            conn.execute(
                """
                SELECT artifact_state, attempts, last_error_code, next_retry_at
                  FROM monitoring_document_renewal_upload_cleanup
                 WHERE cleanup_id = ?
                """,
                (cleanup_id,),
            ).fetchone()
        )
    finally:
        conn.close()
    assert final == {
        "artifact_state": "cleaned",
        "attempts": 2,
        "last_error_code": None,
        "next_retry_at": None,
    }


def test_artifact_reservation_rejects_cross_identity_and_symlink_escape(
    renewal_api_server,
    tmp_path,
):
    base_url, db_module, server_module = renewal_api_server
    case = _seed_case(db_module, "artifact-identity")
    public = _create(base_url, case).json()["request"]
    request = _request_row(db_module, public["request_id"])
    upload_id = "3" * 32
    wrong_key = server_module.build_monitoring_renewal_candidate_key(
        "renewal_client_b",
        request["request_id"],
        upload_id,
        "candidate.pdf",
    )
    conn = db_module.get_db()
    try:
        with pytest.raises(RuntimeError, match="identity mismatch"):
            server_module._reserve_monitoring_renewal_upload_artifact(
                conn,
                request=request,
                actor={"sub": case["client_id"], "name": "Client", "role": "client"},
                cleanup_id="4" * 32,
                upload_id=upload_id,
                backend="s3",
                storage_key=wrong_key,
                file_extension=".pdf",
            )
        conn.rollback()
    finally:
        conn.close()

    candidate_dir = os.path.join(
        server_module.UPLOAD_DIR,
        "monitoring-renewal-candidates",
    )
    os.makedirs(candidate_dir, mode=0o700, exist_ok=True)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"must remain")
    symlink_path = os.path.join(candidate_dir, "5" * 32 + ".pdf")
    os.symlink(str(outside), symlink_path)
    try:
        assert not server_module._monitoring_renewal_local_artifact_allowed(
            server_module._MONITORING_RENEWAL_LOCAL_STORAGE_PREFIX
            + "5" * 32
            + ".pdf",
            symlink_path,
            "5" * 32,
            ".pdf",
        )
        assert outside.read_bytes() == b"must remain"
    finally:
        os.unlink(symlink_path)


def test_local_candidate_directory_symlink_is_never_followed_or_chmodded(
    renewal_api_server,
    tmp_path,
    monkeypatch,
):
    _base_url, _db_module, server_module = renewal_api_server
    upload_root = tmp_path / "local-upload-root"
    upload_root.mkdir(mode=0o700)
    outside = tmp_path / "outside-candidates"
    outside.mkdir(mode=0o755)
    outside_mode = os.stat(outside).st_mode & 0o777
    (upload_root / "monitoring-renewal-candidates").symlink_to(
        outside,
        target_is_directory=True,
    )
    monkeypatch.setattr(server_module, "UPLOAD_DIR", str(upload_root))

    with pytest.raises(OSError):
        server_module._open_monitoring_renewal_local_candidate_directory(
            create=True
        )
    with pytest.raises(OSError):
        server_module._open_monitoring_renewal_local_candidate_directory(
            create=False
        )
    assert (os.stat(outside).st_mode & 0o777) == outside_mode


def test_s3_candidate_put_is_conditional_and_delete_uses_exact_version():
    from s3_client import S3Client, build_monitoring_renewal_candidate_key

    calls = []

    class CandidateClient:
        def put_object(self, **kwargs):
            calls.append(("put", kwargs))
            return {"VersionId": "version-123", "ETag": "etag-123"}

        def delete_object(self, **kwargs):
            calls.append(("delete", kwargs))
            return {}

    client = object.__new__(S3Client)
    client.bucket_name = "renewal-test-bucket"
    candidate = CandidateClient()
    client.monitoring_renewal_candidate_client = lambda: candidate
    key = build_monitoring_renewal_candidate_key(
        "renewal-client",
        "renewal-request",
        "6" * 32,
        "candidate.pdf",
    )
    success, result = client.upload_monitoring_renewal_candidate(
        key=key,
        file_data=b"%PDF-1.4\n%%EOF",
        customer_id="renewal-client",
        request_id="renewal-request",
        upload_id="6" * 32,
        cleanup_id="7" * 32,
        original_filename="candidate.pdf",
        content_type="application/pdf",
        file_sha256="a" * 64,
    )
    assert success is True
    assert result["version_id"] == "version-123"
    assert calls[0][1]["IfNoneMatch"] == "*"
    deleted, _ = client.delete_monitoring_renewal_candidate(
        key,
        version_id="version-123",
    )
    assert deleted is True
    assert calls[1][1]["VersionId"] == "version-123"


def test_s3_upload_admission_is_bounded_and_released_after_completion(
    renewal_api_server,
):
    base_url, db_module, server_module = renewal_api_server
    cases = [
        _seed_case(db_module, f"admission-{index}")
        for index in range(3)
    ]
    request_rows = [_create(base_url, case).json()["request"] for case in cases]
    entered = 0
    entered_lock = threading.Lock()
    two_entered = threading.Event()
    release_uploads = threading.Event()

    class BlockingS3:
        @staticmethod
        def build_monitoring_renewal_candidate_key(*args):
            return server_module.build_monitoring_renewal_candidate_key(*args)

        @staticmethod
        def upload_monitoring_renewal_candidate(**kwargs):
            nonlocal entered
            with entered_lock:
                entered += 1
                if entered >= 2:
                    two_entered.set()
            assert release_uploads.wait(timeout=10)
            return True, {
                "key": kwargs["key"],
                "version_id": f"version-{kwargs['upload_id']}",
            }

    original_has_s3 = server_module.HAS_S3
    original_get_s3 = server_module.get_s3_client
    server_module.HAS_S3 = True
    server_module.get_s3_client = lambda: BlockingS3()
    results = [None, None]

    def upload(index):
        case = cases[index]
        request = request_rows[index]
        results[index] = requests.post(
            f"{base_url}/api/portal/applications/{case['application_id']}"
            f"/renewal-requests/{request['request_id']}/upload",
            headers=_auth_headers(_client_token(case["client_id"])),
            files={
                "file": (
                    f"admission-{index}.pdf",
                    b"%PDF-1.4\n% bounded admission\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=15,
        )

    workers = [threading.Thread(target=upload, args=(index,), daemon=True) for index in (0, 1)]
    try:
        for worker in workers:
            worker.start()
        assert two_entered.wait(timeout=8)
        started = time.monotonic()
        third = requests.post(
            f"{base_url}/api/portal/applications/{cases[2]['application_id']}"
            f"/renewal-requests/{request_rows[2]['request_id']}/upload",
            headers=_auth_headers(_client_token(cases[2]["client_id"])),
            files={
                "file": (
                    "admission-third.pdf",
                    b"%PDF-1.4\n% reject promptly\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=5,
        )
        assert third.status_code == 503
        assert time.monotonic() - started < 2
        conn = db_module.get_db()
        try:
            assert conn.execute(
                "SELECT COUNT(*) AS c FROM monitoring_document_renewal_upload_cleanup WHERE request_id = ?",
                (request_rows[2]["request_id"],),
            ).fetchone()["c"] == 0
        finally:
            conn.close()
        release_uploads.set()
        for worker in workers:
            worker.join(timeout=10)
        assert all(not worker.is_alive() for worker in workers)
        assert [response.status_code for response in results] == [201, 201]

        retry = requests.post(
            f"{base_url}/api/portal/applications/{cases[2]['application_id']}"
            f"/renewal-requests/{request_rows[2]['request_id']}/upload",
            headers=_auth_headers(_client_token(cases[2]["client_id"])),
            files={
                "file": (
                    "admission-third.pdf",
                    b"%PDF-1.4\n% admitted after release\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=10,
        )
        assert retry.status_code == 201, retry.text
    finally:
        release_uploads.set()
        for worker in workers:
            worker.join(timeout=2)
        server_module.HAS_S3 = original_has_s3
        server_module.get_s3_client = original_get_s3


def test_upload_admission_source_future_owns_capacity_after_waiter_cancel(
    renewal_api_server,
):
    _base_url, _db_module, server_module = renewal_api_server
    original_executor = server_module._DOCUMENT_RENEWAL_UPLOAD_EXECUTOR
    original_admission = server_module._DOCUMENT_RENEWAL_UPLOAD_ADMISSION
    test_executor = ThreadPoolExecutor(max_workers=1)
    test_admission = threading.BoundedSemaphore(value=1)
    server_module._DOCUMENT_RENEWAL_UPLOAD_EXECUTOR = test_executor
    server_module._DOCUMENT_RENEWAL_UPLOAD_ADMISSION = test_admission
    entered = threading.Event()
    release_worker = threading.Event()

    def blocking_upload():
        entered.set()
        assert release_worker.wait(timeout=10)
        return True, {"key": "test"}

    async def cancel_waiter_while_worker_owns_body():
        assert server_module._try_acquire_document_renewal_upload_capacity()
        source_future = server_module._submit_document_renewal_upload(
            blocking_upload
        )
        waiter = asyncio.ensure_future(asyncio.wrap_future(source_future))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not server_module._try_acquire_document_renewal_upload_capacity()
        release_worker.set()
        while not source_future.done():
            await asyncio.sleep(0.01)
        assert server_module._try_acquire_document_renewal_upload_capacity()
        server_module._DOCUMENT_RENEWAL_UPLOAD_ADMISSION.release()

    try:
        asyncio.run(cancel_waiter_while_worker_owns_body())
    finally:
        release_worker.set()
        test_executor.shutdown(wait=True)
        server_module._DOCUMENT_RENEWAL_UPLOAD_EXECUTOR = original_executor
        server_module._DOCUMENT_RENEWAL_UPLOAD_ADMISSION = original_admission


def test_upload_admission_references_are_scoped_only_to_renewal_handler():
    tree, source = _server_tree_and_source()
    handler = _class_node(tree, "PortalDocumentRenewalUploadHandler")
    handler_source = ast.get_source_segment(source, handler)
    acquire_helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_try_acquire_document_renewal_upload_capacity"
    )
    submit_helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_submit_document_renewal_upload"
    )
    acquire_source = ast.get_source_segment(source, acquire_helper)
    submit_source = ast.get_source_segment(source, submit_helper)
    assert handler_source.count("_try_acquire_document_renewal_upload_capacity") == 1
    assert handler_source.count("_submit_document_renewal_upload") == 1
    assert "_DOCUMENT_RENEWAL_UPLOAD_ADMISSION.acquire" not in handler_source
    assert handler_source.count("_DOCUMENT_RENEWAL_UPLOAD_ADMISSION.release") == 1
    assert source.count("_DOCUMENT_RENEWAL_UPLOAD_ADMISSION.acquire") == 1
    assert source.count("_DOCUMENT_RENEWAL_UPLOAD_ADMISSION.release") == 2
    assert "blocking=False" in acquire_source
    assert ".submit(" in submit_source
    assert ".add_done_callback(" in submit_source
    assert handler_source.index("db.close()") < handler_source.index(
        "_submit_document_renewal_upload"
    )


def test_legacy_monitoring_document_actions_fail_closed_only_when_feature_on(
    renewal_api_server,
):
    base_url, db_module, server_module = renewal_api_server
    enabled_case = _seed_case(db_module, "legacy-on")
    token = _admin_token()

    enabled = requests.patch(
        f"{base_url}/api/monitoring/alerts/{enabled_case['alert_id']}",
        headers=_json_headers(token),
        json={"action": "request_updated_document"},
        timeout=10,
    )
    assert enabled.status_code == 409
    assert _db_counts(db_module, enabled_case)["legacy"] == 0

    replacement = requests.post(
        f"{base_url}/api/monitoring/alerts/{enabled_case['alert_id']}/replacement-upload",
        headers=_auth_headers(token),
        data={"source_note": "Must remain on the legacy path only."},
        files={
            "file": (
                "legacy-blocked.pdf",
                b"%PDF-1.4\n% legacy blocked\n%%EOF",
                "application/pdf",
            )
        },
        timeout=10,
    )
    assert replacement.status_code == 409
    assert _db_counts(db_module, enabled_case)["documents"] == 1

    disabled_case = _seed_case(db_module, "legacy-off")
    with _feature_state(server_module, False):
        preserved = requests.patch(
            f"{base_url}/api/monitoring/alerts/{disabled_case['alert_id']}",
            headers=_json_headers(token),
            json={"action": "request_updated_document"},
            timeout=10,
        )
    assert preserved.status_code == 200, preserved.text
    counts = _db_counts(db_module, disabled_case)
    assert counts["legacy"] == 1
    assert counts["requests"] == 0
    assert counts["documents"] == 1
    assert counts["alert_status"] == "open"

    # An in-flight protected legacy request remains grandfathered if the flag
    # is enabled after creation; it is not duplicated or forcibly migrated.
    continued = requests.patch(
        f"{base_url}/api/monitoring/alerts/{disabled_case['alert_id']}",
        headers=_json_headers(token),
        json={"action": "request_updated_document"},
        timeout=10,
    )
    assert continued.status_code == 200, continued.text
    assert _db_counts(db_module, disabled_case)["legacy"] == 1

    canonical_case = _seed_case(db_module, "canonical-rollback")
    canonical = _create(base_url, canonical_case)
    assert canonical.status_code == 201, canonical.text
    with _feature_state(server_module, False):
        rollback_attempt = requests.patch(
            f"{base_url}/api/monitoring/alerts/{canonical_case['alert_id']}",
            headers=_json_headers(token),
            json={"action": "request_updated_document"},
            timeout=10,
        )
    assert rollback_attempt.status_code == 409
    assert _db_counts(db_module, canonical_case)["legacy"] == 0


def test_cross_alert_and_version_canonical_owner_fences_legacy_slot_only(
    renewal_api_server,
):
    base_url, db_module, server_module = renewal_api_server
    case = _seed_case(db_module, "legacy-cross-slot")
    canonical_alert_id = case["alert_id"] + 900000000
    other_alert_id = case["alert_id"] + 910000000
    replacement_id = case["document_id"] + "-v2"
    other_document_id = case["document_id"] + "-address"
    conn = db_module.get_db()
    try:
        original = dict(
            conn.execute(
                "SELECT * FROM documents WHERE id = ?",
                (case["document_id"],),
            ).fetchone()
        )
        conn.execute(
            "UPDATE documents SET is_current = 0, superseded_by_document_id = ? WHERE id = ?",
            (replacement_id, case["document_id"]),
        )
        conn.execute(
            """
            INSERT INTO documents
                (id, application_id, person_id, person_type, doc_type,
                 doc_name, file_path, file_size, mime_type, slot_key,
                 is_current, version, expiry_date, verification_status,
                 review_status)
            VALUES (?, ?, ?, ?, ?, 'Replacement passport.pdf', ?, 64,
                    'application/pdf', ?, 1, 2, '2020-01-01',
                    'verified', 'accepted')
            """,
            (
                replacement_id,
                case["application_id"],
                original["person_id"],
                original["person_type"],
                original["doc_type"],
                f"fixtures/{replacement_id}.pdf",
                original["slot_key"],
            ),
        )
        conn.execute(
            """
            INSERT INTO monitoring_alerts
                (id, application_id, client_name, alert_type, severity,
                 detected_by, summary, source_reference, status,
                 discovered_via)
            VALUES (?, ?, 'Renewal cross-slot Ltd', 'document_expired',
                    'medium', 'Document Health Monitor',
                    'Replacement passport expired', ?, 'open',
                    'document_health')
            """,
            (
                canonical_alert_id,
                case["application_id"],
                f"document:{replacement_id}",
            ),
        )
        conn.execute(
            """
            INSERT INTO documents
                (id, application_id, person_id, person_type, doc_type,
                 doc_name, file_path, file_size, mime_type, slot_key,
                 is_current, version, expiry_date, verification_status,
                 review_status)
            VALUES (?, ?, ?, ?, 'proof_of_address', 'Address.pdf', ?, 64,
                    'application/pdf', ?, 1, 1, '2020-01-01',
                    'verified', 'accepted')
            """,
            (
                other_document_id,
                case["application_id"],
                original["person_id"],
                original["person_type"],
                f"fixtures/{other_document_id}.pdf",
                f"person:director:{case['person_id']}:proof_of_address",
            ),
        )
        conn.execute(
            """
            INSERT INTO monitoring_alerts
                (id, application_id, client_name, alert_type, severity,
                 detected_by, summary, source_reference, status,
                 discovered_via)
            VALUES (?, ?, 'Renewal cross-slot Ltd', 'document_expired',
                    'medium', 'Document Health Monitor', 'Address expired',
                    ?, 'open', 'document_health')
            """,
            (
                other_alert_id,
                case["application_id"],
                f"document:{other_document_id}",
            ),
        )
        for alert_id, document_id, status in (
            (case["alert_id"], case["document_id"], "accepted"),
            (other_alert_id, other_document_id, "requested"),
        ):
            conn.execute(
                """
                INSERT INTO application_enhanced_requirements
                    (monitoring_alert_id, application_id, active, status,
                     monitoring_document_id, linked_document_id,
                     requirement_type, trigger_category, trigger_key,
                     trigger_label, requirement_key, requirement_label)
                VALUES (?, ?, 1, ?, ?, ?, 'document', 'monitoring',
                        ?, 'Monitoring renewal', ?, 'Renewed document')
                """,
                (
                    alert_id,
                    case["application_id"],
                    status,
                    document_id,
                    document_id,
                    f"monitoring:{alert_id}",
                    f"renewal:{document_id}",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    canonical_case = dict(case)
    canonical_case.update(
        {"alert_id": canonical_alert_id, "document_id": replacement_id}
    )
    canonical = _create(base_url, canonical_case)
    assert canonical.status_code == 201, canonical.text

    conn = db_module.get_db()
    try:
        assert server_module._monitoring_legacy_document_write_blocked(
            conn,
            case["alert_id"],
        ) is True
        conn.rollback()
        # The canonical passport owner must not block a distinct active legacy
        # proof-of-address slot in the same application.
        assert server_module._monitoring_legacy_document_write_blocked(
            conn,
            other_alert_id,
        ) is False
        conn.rollback()
        with _feature_state(server_module, False):
            assert server_module._monitoring_legacy_document_write_blocked(
                conn,
                other_alert_id,
            ) is False
        conn.rollback()
        for broken_reference in ("legacy:unproven", "document:missing-document"):
            conn.execute(
                "UPDATE monitoring_alerts SET source_reference = ? WHERE id = ?",
                (broken_reference, case["alert_id"]),
            )
            conn.commit()
            with _feature_state(server_module, False):
                assert server_module._monitoring_legacy_document_write_blocked(
                    conn,
                    case["alert_id"],
                ) is True
            conn.rollback()
    finally:
        conn.close()

    no_canonical = _seed_case(db_module, "legacy-broken-no-canonical")
    conn = db_module.get_db()
    try:
        conn.execute(
            "UPDATE monitoring_alerts SET source_reference = 'legacy:unproven' WHERE id = ?",
            (no_canonical["alert_id"],),
        )
        conn.commit()
        with _feature_state(server_module, False):
            assert server_module._monitoring_legacy_document_write_blocked(
                conn,
                no_canonical["alert_id"],
            ) is False
        conn.rollback()
    finally:
        conn.close()


def test_legacy_active_detection_uses_only_protected_exact_vocabulary(
    renewal_api_server,
):
    _base_url, db_module, server_module = renewal_api_server
    case = _seed_case(db_module, "legacy-status-vocabulary")
    conn = db_module.get_db()
    try:
        conn.execute(
            """
            INSERT INTO application_enhanced_requirements
                (monitoring_alert_id, application_id, active, status,
                 monitoring_document_id, linked_document_id, trigger_key,
                 trigger_label, trigger_category, requirement_key,
                 requirement_label)
            VALUES (?, ?, 1, 'generated', ?, ?, ?, 'Monitoring renewal',
                    'monitoring', ?, 'Renewed document')
            """,
            (
                case["alert_id"],
                case["application_id"],
                case["document_id"],
                case["document_id"],
                f"monitoring:{case['alert_id']}",
                f"renewal:{case['document_id']}",
            ),
        )
        conn.commit()
        for status in ("requested", "uploaded", "under_review", "rejected"):
            conn.execute(
                "UPDATE application_enhanced_requirements SET status = ? WHERE monitoring_alert_id = ?",
                (status, case["alert_id"]),
            )
            conn.commit()
            assert server_module._monitoring_legacy_document_request_active(
                conn,
                case["alert_id"],
            ) is True
        for status in (
            "accepted",
            "waived",
            "generated",
            "cancelled",
        ):
            conn.execute(
                "UPDATE application_enhanced_requirements SET status = ? WHERE monitoring_alert_id = ?",
                (status, case["alert_id"]),
            )
            conn.commit()
            assert server_module._monitoring_legacy_document_request_active(
                conn,
                case["alert_id"],
            ) is False
    finally:
        conn.close()


def _server_tree_and_source():
    source = SERVER_PATH.read_text(encoding="utf-8")
    return ast.parse(source), source


def _class_node(tree, name):
    return next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    )


def test_handler_method_and_route_contracts_are_narrow():
    tree, source = _server_tree_and_source()
    expected = {
        "MonitoringAlertRenewalRequestHandler": {"get", "post"},
        "MonitoringRenewalRequestResendHandler": {"post"},
        "MonitoringRenewalRequestDueDateHandler": {"patch"},
        "MonitoringRenewalRequestCancelHandler": {"post"},
        "PortalDocumentRenewalRequestsHandler": {"get"},
        "PortalDocumentRenewalUploadHandler": {"post"},
    }
    for class_name, methods in expected.items():
        node = _class_node(tree, class_name)
        implemented = {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert implemented == methods

    for route in (
        r'/api/monitoring/alerts/([0-9]+)/renewal-request',
        r'/api/monitoring/renewal-requests/([A-Za-z0-9_-]+)/resend',
        r'/api/monitoring/renewal-requests/([A-Za-z0-9_-]+)/due-date',
        r'/api/monitoring/renewal-requests/([A-Za-z0-9_-]+)/cancel',
        r'/api/portal/applications/([^/]+)/renewal-requests/([A-Za-z0-9_-]+)/upload',
        r'/api/portal/applications/([^/]+)/renewal-requests',
    ):
        # Count the exact raw-string route literal rather than a substring;
        # the list route is intentionally a prefix of the upload route.
        assert source.count(f'r"{route}"') == 1


def test_all_legacy_document_mutation_handlers_use_slot_ownership_fence():
    tree, source = _server_tree_and_source()
    for class_name in (
        "ApplicationEnhancedRequirementDetailHandler",
        "ApplicationEnhancedRequirementRequestHandler",
        "ApplicationEnhancedRequirementUploadHandler",
        "PortalApplicationEnhancedRequirementUploadHandler",
    ):
        region = ast.get_source_segment(source, _class_node(tree, class_name))
        assert region.count("_monitoring_legacy_document_write_blocked") == 1
        assert "_monitoring_canonical_renewal_active" not in region


def test_portal_upload_handler_calls_only_dedicated_renewal_record_service():
    tree, source = _server_tree_and_source()
    node = _class_node(tree, "PortalDocumentRenewalUploadHandler")
    region = ast.get_source_segment(source, node)
    renewal_calls = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "_monitoring_document_renewal"
    }
    assert renewal_calls == {
        "renewal_feature_enabled",
        "get_renewal_request",
        "record_renewal_upload",
    }
    for prohibited_call in (
        "_monitoring_document_refresh",
        "_request_monitoring_updated_document",
        "_prepare_document_slot_replacement",
        "_mark_monitoring_document_upload_received",
        "queue_verification_for_document",
        "invoke_agent",
        "transition_alert_status",
    ):
        assert prohibited_call not in region
    assert not __import__("re").search(
        r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
        r"(?:documents|monitoring_alerts|agent_executions)\b",
        region,
        __import__("re").IGNORECASE,
    )


def test_public_projection_cannot_serialize_storage_location_or_digest():
    tree, source = _server_tree_and_source()
    projection = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_monitoring_renewal_public_projection"
    )
    region = ast.get_source_segment(source, projection)
    assert "storage_key" not in region
    assert "file_sha256" not in region
    fields = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "_RENEWAL_REQUEST_PUBLIC_FIELDS"
            for target in node.targets
        )
    )
    public_fields = set(ast.literal_eval(fields.value))
    assert {"storage_key", "file_sha256", "eligibility_fingerprint"}.isdisjoint(
        public_fields
    )
