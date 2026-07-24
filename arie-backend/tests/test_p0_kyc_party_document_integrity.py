"""Focused P0 regressions for post-pricing KYC persistence and ownership.

These tests exercise the production HTTP handlers for pricing, reload,
profile persistence, document authorization, logout, and Sumsub scoping. The
lower-level cases cover stable party synchronization and reliance integrity
without relying on browser-local state.
"""

import json
import os
import socket
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import requests
import tornado.httpserver
import tornado.ioloop


def _sync_db_path(path):
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def p0_api_server(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("p0-backend") / "regmind-p0.db")
    _sync_db_path(db_path)

    from db import get_db, init_db, seed_initial_data

    init_db()
    db = get_db()
    seed_initial_data(db)
    db.commit()
    db.close()

    import server as server_module

    server_module.HAS_S3 = False
    app = server_module.make_app()
    port = _free_port()
    server_ref = {}
    started = threading.Event()

    def run_server():
        import asyncio

        asyncio.set_event_loop(asyncio.new_event_loop())
        io_loop = tornado.ioloop.IOLoop.current()
        http_server = tornado.httpserver.HTTPServer(app)
        http_server.listen(port, "127.0.0.1")
        server_ref.update(server=http_server, loop=io_loop)
        started.set()
        io_loop.start()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    assert started.wait(timeout=3), "P0 backend test server did not start"
    time.sleep(0.15)

    yield f"http://127.0.0.1:{port}"

    from tests.conftest import shutdown_test_http_server

    shutdown_test_http_server(thread, server_ref)


def _token_headers(user_id, *, actor_type="client", role=None):
    from auth import create_token

    role = role or ("client" if actor_type == "client" else "admin")
    token = create_token(
        user_id,
        role,
        f"P0 {actor_type.title()} {user_id}",
        actor_type,
    )
    return {"Authorization": f"Bearer {token}"}


def _seed_case(*, status="kyc_documents", collision=False):
    from db import get_db

    suffix = uuid.uuid4().hex[:10]
    client_id = f"p0-client-{suffix}"
    attacker_id = f"p0-attacker-{suffix}"
    app_id = f"p0-app-{suffix}"
    ref = f"ARF-P0-{suffix.upper()}"
    director_a = f"p0-dir-a-{suffix}"
    director_b = f"p0-dir-b-{suffix}"
    ubo_id = f"p0-ubo-{suffix}"

    db = get_db()
    for actor_id in (client_id, attacker_id):
        db.execute(
            """
            INSERT INTO clients
                (id, email, password_hash, company_name, status)
            VALUES (?, ?, 'test-only', ?, 'active')
            """,
            (actor_id, f"{actor_id}@example.test", f"{actor_id} Ltd"),
        )
    db.execute(
        """
        INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type,
             status, risk_level, final_risk_level, risk_score, onboarding_lane,
             prescreening_data)
        VALUES (?, ?, ?, 'P0 Persistence Ltd', 'Mauritius', 'Technology',
                'SME', ?, 'MEDIUM', 'MEDIUM', 42, 'STP', '{}')
        """,
        (app_id, ref, client_id, status),
    )
    # Insert out of presentation order to prove reload ordering is
    # deterministic and never an ownership mechanism.
    db.execute(
        """
        INSERT INTO directors
            (id, application_id, person_key, first_name, last_name, full_name,
             date_of_birth, country_of_residence)
        VALUES (?, ?, 'zeta-director', 'Zara', 'Zulu', 'Zara Zulu',
                '1980-01-02', 'Mauritius')
        """,
        (director_b, app_id),
    )
    db.execute(
        """
        INSERT INTO directors
            (id, application_id, person_key, first_name, last_name, full_name,
             date_of_birth, country_of_residence)
        VALUES (?, ?, 'alpha-director', 'Alice', 'Alpha', 'Alice Alpha',
                '1979-03-04', 'United Kingdom')
        """,
        (director_a, app_id),
    )
    db.execute(
        """
        INSERT INTO ubos
            (id, application_id, person_key, first_name, last_name, full_name,
             ownership_pct, date_of_birth, country_of_residence)
        VALUES (?, ?, 'primary-ubo', 'Uma', 'Owner', 'Uma Owner', 37.5,
                '1985-05-06', 'Mauritius')
        """,
        (ubo_id, app_id),
    )
    documents = (
        (
            f"p0-doc-a-{suffix}",
            director_a,
            "director",
            "passport",
            "SYNTHETIC-A-PASSPORT.pdf",
            f"person:director:{director_a}:passport",
        ),
        (
            f"p0-doc-b-{suffix}",
            director_b,
            "director",
            "passport",
            "SYNTHETIC-B-PASSPORT.pdf",
            f"person:director:{director_b}:passport",
        ),
        (
            f"p0-doc-ubo-{suffix}",
            ubo_id,
            "ubo",
            "poa",
            "SYNTHETIC-UBO-POA.pdf",
            f"person:ubo:{ubo_id}:poa",
        ),
        (
            f"p0-doc-entity-{suffix}",
            None,
            None,
            "cert_inc",
            "SYNTHETIC-CERTIFICATE.pdf",
            "entity:cert_inc",
        ),
    )
    for doc_id, person_id, person_type, doc_type, name, slot_key in documents:
        db.execute(
            """
            INSERT INTO documents
                (id, application_id, person_id, person_type, doc_type, doc_name,
                 file_path, mime_type, slot_key, is_current, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'application/pdf', ?, 1, 1)
            """,
            (
                doc_id,
                app_id,
                person_id,
                person_type,
                doc_type,
                name,
                f"/tmp/{name}",
                slot_key,
            ),
        )

    collision_ids = None
    if collision:
        collision_a = f"p0-collision-a-{suffix}"
        collision_b = f"p0-collision-b-{suffix}"
        db.execute(
            """
            INSERT INTO directors
                (id, application_id, person_key, first_name, last_name, full_name)
            VALUES (?, ?, 'collision-key-a', 'First', 'Collision',
                    'First Collision')
            """,
            (collision_a, app_id),
        )
        db.execute(
            """
            INSERT INTO directors
                (id, application_id, person_key, first_name, last_name, full_name)
            VALUES (?, ?, ?, 'Second', 'Collision', 'Second Collision')
            """,
            (collision_b, app_id, collision_a),
        )
        collision_ids = (collision_a, collision_b)

    db.commit()
    db.close()
    return {
        "app_id": app_id,
        "ref": ref,
        "client_id": client_id,
        "attacker_id": attacker_id,
        "director_a": director_a,
        "director_b": director_b,
        "ubo_id": ubo_id,
        "documents": documents,
        "collision_ids": collision_ids,
    }


def _raw_integrity_snapshot(app_id):
    from db import get_db

    db = get_db()
    snapshot = {}
    for table in ("directors", "ubos", "intermediaries", "documents"):
        snapshot[table] = [
            dict(row)
            for row in db.execute(
                f"SELECT * FROM {table} WHERE application_id=? ORDER BY id",
                (app_id,),
            ).fetchall()
        ]
    app = dict(
        db.execute(
            "SELECT * FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()
    )
    db.close()
    return app, snapshot


def test_pricing_reload_and_logout_change_workflow_only(p0_api_server):
    case = _seed_case(status="pricing_review")
    headers = _token_headers(case["client_id"])
    before_app, before_rows = _raw_integrity_snapshot(case["app_id"])

    accepted = requests.post(
        f"{p0_api_server}/api/applications/{case['app_id']}/accept-pricing",
        headers=headers,
        timeout=8,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "kyc_documents"

    after_app, after_rows = _raw_integrity_snapshot(case["app_id"])
    assert after_rows == before_rows
    ignored = {"status", "updated_at"}
    assert {
        key: value for key, value in after_app.items() if key not in ignored
    } == {
        key: value for key, value in before_app.items() if key not in ignored
    }

    detail = requests.get(
        f"{p0_api_server}/api/applications/{case['app_id']}",
        headers=headers,
        timeout=12,
    )
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert [party["id"] for party in payload["directors"]] == [
        case["director_a"],
        case["director_b"],
    ]
    expected_links = {
        doc_id: (case["app_id"], person_id, person_type, doc_type)
        for doc_id, person_id, person_type, doc_type, _name, _slot in case["documents"]
    }
    _, db_rows = _raw_integrity_snapshot(case["app_id"])
    assert {
        row["id"]: (
            row["application_id"],
            row["person_id"],
            row["person_type"],
            row["doc_type"],
        )
        for row in db_rows["documents"]
    } == expected_links

    before_logout = _raw_integrity_snapshot(case["app_id"])
    logged_out = requests.post(
        f"{p0_api_server}/api/auth/logout",
        headers=headers,
        timeout=8,
    )
    assert logged_out.status_code == 200, logged_out.text
    assert logged_out.json()["status"] == "logged_out"
    assert _raw_integrity_snapshot(case["app_id"]) == before_logout

    clean_headers = _token_headers(case["client_id"])
    reloaded = requests.get(
        f"{p0_api_server}/api/applications/{case['app_id']}",
        headers=clean_headers,
        timeout=12,
    )
    assert reloaded.status_code == 200, reloaded.text
    assert [party["id"] for party in reloaded.json()["directors"]] == [
        case["director_a"],
        case["director_b"],
    ]
    assert {
        (doc["id"], doc["person_id"], doc["person_type"], doc["doc_type"])
        for doc in reloaded.json()["documents"]
    } == {
        (doc_id, person_id, person_type, doc_type)
        for doc_id, person_id, person_type, doc_type, _name, _slot in case["documents"]
    }


def test_concurrent_pricing_acceptance_has_one_winner_and_one_set_of_side_effects(
    p0_api_server,
):
    """Two workers that both read pricing_review still commit exactly once.

    The existing API fixture uses one synchronous Tornado IOLoop, so a second
    independent server thread models a second ECS worker. The DB wrapper pauses
    each request immediately after its initial application read. This
    deterministically gives both workers the same stale pricing_review snapshot
    before either can attempt the transition; the database compare-and-set must
    decide the sole winner.
    """
    case = _seed_case(status="pricing_review")
    headers = _token_headers(case["client_id"])

    import server as server_module
    from db import get_db as real_get_db

    db = real_get_db()
    db.execute(
        """
        UPDATE applications
           SET risk_level='HIGH', final_risk_level='HIGH', risk_score=82,
               onboarding_lane='EDD'
         WHERE id=?
        """,
        (case["app_id"],),
    )
    officer_count = db.execute(
        "SELECT COUNT(*) AS count FROM users WHERE role IN ('sco','co')"
    ).fetchone()["count"]
    assert officer_count > 0
    db.commit()
    db.close()

    second_app = server_module.make_app()
    second_port = _free_port()
    second_ref = {}
    second_started = threading.Event()

    def run_second_server():
        import asyncio

        asyncio.set_event_loop(asyncio.new_event_loop())
        io_loop = tornado.ioloop.IOLoop.current()
        http_server = tornado.httpserver.HTTPServer(second_app)
        http_server.listen(second_port, "127.0.0.1")
        second_ref.update(server=http_server, loop=io_loop)
        second_started.set()
        io_loop.start()

    second_thread = threading.Thread(target=run_second_server, daemon=True)
    second_thread.start()
    assert second_started.wait(timeout=3), "Second pricing worker did not start"
    time.sleep(0.05)

    read_barrier = threading.Barrier(2)

    class BarrierAfterInitialPricingRead:
        def __init__(self, inner):
            self._inner = inner
            self._wait_after_fetch = False
            self._waited = False

        def execute(self, sql, params=()):
            normalized = " ".join(str(sql).split()).lower()
            self._wait_after_fetch = (
                not self._waited
                and normalized.startswith(
                    "select * from applications where id = ? or ref = ?"
                )
                and tuple(params) == (case["app_id"], case["app_id"])
            )
            self._inner.execute(sql, params)
            return self

        def fetchone(self):
            row = self._inner.fetchone()
            if self._wait_after_fetch:
                self._wait_after_fetch = False
                self._waited = True
                read_barrier.wait(timeout=5)
            return row

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def synchronized_get_db():
        return BarrierAfterInitialPricingRead(real_get_db())

    def accept(base_url):
        return requests.post(
            f"{base_url}/api/applications/{case['app_id']}/accept-pricing",
            headers=headers,
            timeout=12,
        )

    try:
        with patch.object(server_module, "get_db", side_effect=synchronized_get_db):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(accept, p0_api_server),
                    executor.submit(accept, f"http://127.0.0.1:{second_port}"),
                ]
                responses = [future.result(timeout=15) for future in futures]
    finally:
        from tests.conftest import shutdown_test_http_server

        shutdown_test_http_server(second_thread, second_ref)

    assert sorted(response.status_code for response in responses) == [200, 400]
    winner = next(response for response in responses if response.status_code == 200)
    loser = next(response for response in responses if response.status_code == 400)
    assert winner.json()["status"] == "pre_approval_review"
    assert loser.json() == {
        "error": "Application is not in pricing review stage"
    }

    db = real_get_db()
    assert db.execute(
        "SELECT status FROM applications WHERE id=?",
        (case["app_id"],),
    ).fetchone()["status"] == "pre_approval_review"
    assert db.execute(
        """
        SELECT COUNT(*) AS count
          FROM audit_log
         WHERE application_id=? AND action='Pricing Accepted'
        """,
        (case["app_id"],),
    ).fetchone()["count"] == 1
    assert db.execute(
        """
        SELECT COUNT(*) AS count
          FROM notifications
         WHERE title=?
        """,
        (f"PRE-APPROVAL REQUIRED: {case['ref']}",),
    ).fetchone()["count"] == officer_count
    db.close()


def test_repeated_party_save_preserves_ids_order_ownership_and_documents():
    case = _seed_case()
    from db import get_db
    from server import get_application_parties, store_application_parties

    db = get_db()
    payload = [
        {
            "id": case["director_b"],
            "person_key": "zeta-director",
            "first_name": "Zara",
            "last_name": "Zulu",
        },
        {
            "id": case["director_a"],
            "person_key": "alpha-director",
            "first_name": "Alice",
            "last_name": "Alpha",
        },
    ]
    ubo_payload = [
        {
            "id": case["ubo_id"],
            "person_key": "primary-ubo",
            "first_name": "Uma",
            "last_name": "Owner",
        }
    ]
    store_application_parties(
        db,
        case["app_id"],
        directors=payload,
        ubos=ubo_payload,
    )
    store_application_parties(
        db,
        case["app_id"],
        directors=list(reversed(payload)),
        ubos=ubo_payload,
    )
    db.commit()

    directors, ubos, _ = get_application_parties(db, case["app_id"])
    assert [party["id"] for party in directors] == [
        case["director_a"],
        case["director_b"],
    ]
    assert len(directors) == 2
    assert len(ubos) == 1
    assert ubos[0]["id"] == case["ubo_id"]
    assert float(ubos[0]["ownership_pct"]) == 37.5
    assert db.execute(
        "SELECT COUNT(*) AS count FROM documents WHERE application_id=?",
        (case["app_id"],),
    ).fetchone()["count"] == len(case["documents"])

    with pytest.raises(ValueError, match="missing its required display name"):
        store_application_parties(
            db,
            case["app_id"],
            directors=[
                {
                    "id": case["director_a"],
                    "person_key": "alpha-director",
                    "first_name": "",
                    "last_name": "",
                }
            ],
        )
    assert db.execute(
        "SELECT COUNT(*) AS count FROM directors WHERE application_id=?",
        (case["app_id"],),
    ).fetchone()["count"] == 2
    db.close()


def test_profile_is_encrypted_rehydrated_clearable_and_authorized(p0_api_server):
    case = _seed_case()
    owner_headers = {
        **_token_headers(case["client_id"]),
        "Content-Type": "application/json",
    }
    endpoint = (
        f"{p0_api_server}/api/applications/{case['app_id']}/kyc/parties/"
        f"{case['director_a']}/profile"
    )
    profile_url = "https://profiles.example.test/alice-alpha"
    saved = requests.patch(
        endpoint,
        headers=owner_headers,
        json={
            "person_type": "director",
            "professional_profile_url": profile_url,
        },
        timeout=8,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["person_id"] == case["director_a"]
    assert saved.json()["professional_profile_url"] == profile_url

    from db import get_db

    db = get_db()
    raw_value = db.execute(
        "SELECT professional_profile_url FROM directors WHERE id=?",
        (case["director_a"],),
    ).fetchone()["professional_profile_url"]
    db.close()
    assert raw_value
    assert raw_value != profile_url

    malformed = requests.patch(
        endpoint,
        headers=owner_headers,
        json={
            "person_type": "director",
            "professional_profile_url": "javascript:alert(1)",
        },
        timeout=8,
    )
    assert malformed.status_code == 400

    officer_denied = requests.patch(
        endpoint,
        headers={
            **_token_headers(
                "admin001",
                actor_type="officer",
                role="admin",
            ),
            "Content-Type": "application/json",
        },
        json={
            "person_type": "director",
            "professional_profile_url": "https://officer.example.test/",
        },
        timeout=8,
    )
    assert officer_denied.status_code == 403

    reloaded = requests.get(
        f"{p0_api_server}/api/applications/{case['app_id']}",
        headers=_token_headers(case["client_id"]),
        timeout=12,
    )
    assert reloaded.status_code == 200, reloaded.text
    director = next(
        row for row in reloaded.json()["directors"]
        if row["id"] == case["director_a"]
    )
    assert director["professional_profile_url"] == profile_url

    db = get_db()
    audit = db.execute(
        """
        SELECT detail, before_state, after_state
          FROM audit_log
         WHERE application_id=?
           AND action='KYC Party Profile Updated'
         ORDER BY id DESC
         LIMIT 1
        """,
        (case["app_id"],),
    ).fetchone()
    db.close()
    assert audit is not None
    assert profile_url not in json.dumps(dict(audit), default=str)

    denied = requests.patch(
        endpoint,
        headers={
            **_token_headers(case["attacker_id"]),
            "Content-Type": "application/json",
        },
        json={
            "person_type": "director",
            "professional_profile_url": "https://attacker.example.test/",
        },
        timeout=8,
    )
    assert denied.status_code == 403

    cleared = requests.patch(
        endpoint,
        headers=owner_headers,
        json={
            "person_type": "director",
            "professional_profile_url": "",
        },
        timeout=8,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["professional_profile_url"] == ""

    db = get_db()
    assert db.execute(
        "SELECT professional_profile_url FROM directors WHERE id=?",
        (case["director_a"],),
    ).fetchone()["professional_profile_url"] == ""
    db.execute(
        "UPDATE applications SET status='compliance_review' WHERE id=?",
        (case["app_id"],),
    )
    db.commit()
    db.close()
    locked = requests.patch(
        endpoint,
        headers=owner_headers,
        json={
            "person_type": "director",
            "professional_profile_url": profile_url,
        },
        timeout=8,
    )
    assert locked.status_code == 409


def test_document_upload_missing_ambiguous_and_cross_tenant_refs_fail_closed(
    p0_api_server,
):
    case = _seed_case()
    owner_headers = _token_headers(case["client_id"])
    attacker_headers = _token_headers(case["attacker_id"])
    upload_url = f"{p0_api_server}/api/applications/{case['app_id']}/documents"
    synthetic = (
        "SYNTHETIC E2E TEST DOCUMENT — NOT A REAL IDENTITY OR CORPORATE RECORD"
    ).encode()

    denied = requests.get(upload_url, headers=attacker_headers, timeout=8)
    assert denied.status_code == 403
    denied_upload = requests.post(
        f"{upload_url}?doc_type=passport&person_id={case['director_a']}"
        "&person_type=director",
        headers=attacker_headers,
        files={"file": ("synthetic.pdf", synthetic, "application/pdf")},
        timeout=8,
    )
    assert denied_upload.status_code == 403

    missing_type = requests.post(
        f"{upload_url}?doc_type=passport&person_id={case['director_a']}",
        headers=owner_headers,
        files={"file": ("synthetic.pdf", synthetic, "application/pdf")},
        timeout=8,
    )
    assert missing_type.status_code == 400

    mismatched_type = requests.post(
        f"{upload_url}?doc_type=passport&person_id={case['director_a']}"
        "&person_type=ubo",
        headers=owner_headers,
        files={"file": ("synthetic.pdf", synthetic, "application/pdf")},
        timeout=8,
    )
    assert mismatched_type.status_code == 400

    from db import get_db
    from server import resolve_application_person

    db = get_db()
    db.execute(
        """
        INSERT INTO ubos
            (id, application_id, person_key, first_name, last_name, full_name,
             ownership_pct)
        VALUES (?, ?, 'alpha-director', 'Ambiguous', 'Alias',
                'Ambiguous Alias', 10)
        """,
        (f"ambiguous-ubo-{uuid.uuid4().hex[:8]}", case["app_id"]),
    )
    db.commit()
    assert resolve_application_person(
        db,
        case["app_id"],
        "alpha-director",
    ) is None
    assert db.execute(
        "SELECT COUNT(*) AS count FROM documents WHERE application_id=?",
        (case["app_id"],),
    ).fetchone()["count"] == len(case["documents"])
    db.close()


def test_replacement_rejects_linked_category_movement_without_mutation():
    case = _seed_case()
    from db import get_db
    from server import _prepare_document_slot_replacement

    poa_doc_id = f"linked-poa-{uuid.uuid4().hex[:8]}"
    db = get_db()
    db.execute(
        """
        INSERT INTO documents
            (id, application_id, person_id, person_type, doc_type, doc_name,
             file_path, slot_key, is_current, version)
        VALUES (?, ?, ?, 'director', 'poa', 'linked-poa.pdf',
                '/tmp/linked-poa.pdf', ?, 1, 1)
        """,
        (
            poa_doc_id,
            case["app_id"],
            case["director_a"],
            f"person:director:{case['director_a']}:poa",
        ),
    )
    db.commit()

    with pytest.raises(ValueError, match="document category"):
        _prepare_document_slot_replacement(
            db,
            application_id=case["app_id"],
            new_document_id="new-passport-refused",
            doc_type="passport",
            person_id=case["director_a"],
            person_type="director",
            extra_document_ids=[poa_doc_id],
        )
    db.rollback()
    row = db.execute(
        "SELECT is_current, superseded_at FROM documents WHERE id=?",
        (poa_doc_id,),
    ).fetchone()
    assert row["is_current"] in (1, True)
    assert row["superseded_at"] is None

    wrong_special_id = f"wrong-rmi-passport-{uuid.uuid4().hex[:8]}"
    db.execute(
        """
        INSERT INTO documents
            (id, application_id, person_id, person_type, doc_type, doc_name,
             file_path, slot_key, is_current, version)
        VALUES (?, ?, ?, 'director', 'passport', 'wrong-rmi-passport.pdf',
                '/tmp/wrong-rmi-passport.pdf', 'rmi:different-item', 1, 1)
        """,
        (wrong_special_id, case["app_id"], case["director_a"]),
    )
    db.commit()
    with pytest.raises(ValueError, match="conflicting slot"):
        _prepare_document_slot_replacement(
            db,
            application_id=case["app_id"],
            new_document_id="new-passport-wrong-rmi-refused",
            doc_type="passport",
            person_id=case["director_a"],
            person_type="director",
            slot_key=f"person:director:{case['director_a']}:passport",
            allowed_extra_slot_keys=["rmi:expected-item"],
            extra_document_ids=[wrong_special_id],
        )
    db.rollback()
    wrong_special = db.execute(
        "SELECT is_current, superseded_at FROM documents WHERE id=?",
        (wrong_special_id,),
    ).fetchone()
    assert wrong_special["is_current"] in (1, True)
    assert wrong_special["superseded_at"] is None

    allowed = _prepare_document_slot_replacement(
        db,
        application_id=case["app_id"],
        new_document_id="new-passport-exact-rmi-allowed",
        doc_type="passport",
        person_id=case["director_a"],
        person_type="director",
        slot_key=f"person:director:{case['director_a']}:passport",
        allowed_extra_slot_keys=["rmi:different-item"],
        extra_document_ids=[wrong_special_id],
    )
    assert wrong_special_id in {
        row["id"] for row in allowed["previous_documents"]
    }
    db.rollback()
    db.close()


def test_reliance_rejects_owner_type_category_and_cross_alias_collisions():
    from document_reliance_gate import (
        _evaluate_document,
        evaluate_document_reliance_gate,
    )
    from db import get_db

    expectation = {
        "doc_type": "passport",
        "label": "Alice passport",
        "person_id": "stable-alice",
        "person_type": "director",
        "slot_key": "person:director:stable-alice:passport",
    }
    clean_evidence = {
        "id": "reliance-doc",
        "is_current": True,
        "verification_status": "verified",
        "verification_results": json.dumps(
            {"overall": "verified", "checks": [{"result": "pass"}]}
        ),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    wrong_owner = _evaluate_document(
        None,
        expectation,
        {
            **clean_evidence,
            "doc_type": "passport",
            "person_id": "stable-bob",
            "person_type": "director",
            "slot_key": expectation["slot_key"],
        },
        require_agent_execution=False,
        stale_days=3650,
    )
    assert wrong_owner["blockers"][0]["code"] == (
        "document_party_association_integrity"
    )
    wrong_type = _evaluate_document(
        None,
        expectation,
        {
            **clean_evidence,
            "doc_type": "passport",
            "person_id": "stable-alice",
            "person_type": "ubo",
            "slot_key": expectation["slot_key"],
        },
        require_agent_execution=False,
        stale_days=3650,
    )
    assert wrong_type["blockers"][0]["code"] == (
        "document_party_association_integrity"
    )
    wrong_category = _evaluate_document(
        None,
        expectation,
        {
            **clean_evidence,
            "doc_type": "poa",
            "person_id": "stable-alice",
            "person_type": "director",
            "slot_key": expectation["slot_key"],
        },
        require_agent_execution=False,
        stale_days=3650,
    )
    assert wrong_category["blockers"][0]["code"] == "unsupported_document_type"

    case = _seed_case(collision=True)
    collision_a, collision_b = case["collision_ids"]
    db = get_db()
    app = dict(
        db.execute(
            "SELECT * FROM applications WHERE id=?",
            (case["app_id"],),
        ).fetchone()
    )
    gate = evaluate_document_reliance_gate(
        db,
        app,
        stage="p0_cross_alias_collision",
        documents=[
            {
                **clean_evidence,
                "id": "single-collision-document",
                "application_id": case["app_id"],
                "doc_type": "passport",
                "person_id": collision_a,
                "person_type": "director",
                "slot_key": f"person:director:{collision_a}:passport",
            }
        ],
        require_agent_execution=False,
        stale_days=3650,
    )
    db.close()
    collision_passports = [
        item
        for item in gate["documents"]
        if item["required_document_type"] == "passport"
        and item["person_id"] in {collision_a, collision_b}
    ]
    by_party = {item["person_id"]: item for item in collision_passports}
    assert by_party[collision_a]["document_id"] == "single-collision-document"
    assert by_party[collision_b]["document_id"] is None


def test_sumsub_uses_exact_typed_party_and_ignores_tampered_identity_fields(
    p0_api_server,
):
    case = _seed_case()
    captured = {}

    def fake_create_applicant(**kwargs):
        captured.update(kwargs)
        return {
            "applicant_id": f"sumsub-{uuid.uuid4().hex[:8]}",
            "status": "init",
            "source": "sumsub-test",
            "api_status": "mocked",
        }

    payload = {
        "application_id": case["app_id"],
        "external_user_id": "alpha-director",
        "person_type": "director",
        "first_name": "Mallory",
        "last_name": "Tampered",
        "dob": "2001-01-01",
        "country": "US",
    }
    with patch("server.sumsub_create_applicant", side_effect=fake_create_applicant):
        response = requests.post(
            f"{p0_api_server}/api/kyc/applicant",
            headers={
                **_token_headers(case["client_id"]),
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=8,
        )
    assert response.status_code == 200, response.text
    assert captured["external_user_id"] == case["director_a"]
    assert captured["first_name"] == "Alice"
    assert captured["last_name"] == "Alpha"
    assert captured["dob"] == "1979-03-04"
    assert captured["country"] == "United Kingdom"

    with patch("server.sumsub_create_applicant") as provider:
        denied = requests.post(
            f"{p0_api_server}/api/kyc/applicant",
            headers={
                **_token_headers(case["attacker_id"]),
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=8,
        )
        assert denied.status_code == 403
        provider.assert_not_called()

        mismatched = requests.post(
            f"{p0_api_server}/api/kyc/applicant",
            headers={
                **_token_headers(case["client_id"]),
                "Content-Type": "application/json",
            },
            json={**payload, "person_type": "ubo"},
            timeout=8,
        )
        assert mismatched.status_code == 400
        provider.assert_not_called()
