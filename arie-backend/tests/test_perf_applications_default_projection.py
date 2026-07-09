"""perf-applications-default-list-projection — slim default list payload.

GET /api/applications previously defaulted to the FULL hydrated view: a.*
for up to 5000 rows plus batched child records (documents, parties,
screening reviews, RMI requests, periodic reviews) for every caller that
omitted ?view=. The back office already requested ?view=list; the default
now IS the slim paginated projection, and the full shape remains available
via explicit ?view=full (unchanged). Any unrecognised view value falls back
to the cheap projection.

The periodic_review projection stays a full/detail-surface field: building
it costs several queries per active review (document-request status, memo
status, blocker evaluation), the list UI never renders it, and attaching it
to the hot paginated list (which auto-refreshes) would make the "perf"
change a regression for the back office. test_periodic_review_phase1_handlers
guards the detail/full-view consistency instead.
"""

import os
import secrets
import socket
import sys
import tempfile
import threading
import time

import pytest
import tornado.httpserver
import tornado.ioloop

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests as http_requests


def _find_free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def api_server():
    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db")
    os.environ["DB_PATH"] = db_path

    from db import init_db, seed_initial_data, get_db
    init_db()
    try:
        conn = get_db()
        seed_initial_data(conn)
        conn.commit()
        conn.close()
    except Exception:
        pass

    # Seed a known application with a document so the full view has
    # something hydrated to show.
    from db import get_db as _get_db
    conn = _get_db()
    app_id = f"perf-proj-{secrets.token_hex(4)}"
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (f"{app_id}-cl", f"{app_id}@test.com", "hash", "Projection Ltd"),
    )
    conn.execute(
        "INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status) "
        "VALUES (?, ?, ?, ?, 'GB', 'Technology', 'Limited Company', 'submitted')",
        (app_id, f"PERF-{secrets.token_hex(4).upper()}", f"{app_id}-cl", "Projection Ltd"),
    )
    conn.commit()
    conn.close()

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

    yield f"http://127.0.0.1:{port}", app_id

    from tests.conftest import shutdown_test_http_server
    shutdown_test_http_server(thread, server_ref)


def _officer_headers():
    from auth import create_token
    token = create_token("admin001", "admin", "Test Admin", "officer")
    return {"Authorization": f"Bearer {token}"}


def _get(api, path):
    base, _app_id = api
    return http_requests.get(f"{base}{path}", headers=_officer_headers(), timeout=5)


class TestDefaultProjection:

    def test_bare_endpoint_returns_slim_paginated_list(self, api_server):
        resp = _get(api_server, "/api/applications")
        assert resp.status_code == 200
        body = resp.json()
        assert body["view"] == "list"
        assert "pagination" in body and "total" in body
        assert body["limit"] <= 20, "default list page size must be small"
        assert body["applications"], "seeded application expected"
        row = body["applications"][0]
        # Slim rows: no hydrated child records, no full a.* payload, and no
        # periodic_review projection (several queries per active review; the
        # list UI never renders it — it belongs to the detail/full surfaces).
        for heavy in ("documents", "directors", "ubos", "prescreening_data",
                      "screening_reviews", "rmi_requests", "periodic_review"):
            assert heavy not in row, f"slim row must not carry {heavy}"
        # The one summary key the back-office list actually renders
        assert "enhanced_review_summary" in row

    def test_view_full_still_returns_hydrated_shape(self, api_server):
        _base, app_id = api_server
        resp = _get(api_server, "/api/applications?view=full")
        assert resp.status_code == 200
        body = resp.json()
        row = next(a for a in body["applications"] if a["id"] == app_id)
        # The hydrated shape is unchanged for explicit opt-in
        assert "documents" in row
        assert "directors" in row
        assert "periodic_review" in row

    def test_unrecognised_view_falls_back_to_slim(self, api_server):
        resp = _get(api_server, "/api/applications?view=banana")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("view") == "list"

    def test_explicit_view_list_unchanged(self, api_server):
        resp = _get(api_server, "/api/applications?view=list&limit=5&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert body["view"] == "list"
        assert body["limit"] == 5
        assert body["pagination"]["has_prev"] is False


@pytest.fixture(scope="module")
def sort_filter_apps(api_server):
    """Seed a deterministic set of applications for sort/filter assertions."""
    from db import get_db
    conn = get_db()
    prefix = f"sortfx-{secrets.token_hex(3)}"
    # Nadir has a NULL risk_score on purpose: COALESCE(risk_score, -1) must
    # keep NULL placement identical on SQLite and PostgreSQL (PG's bare DESC
    # default is NULLS FIRST).
    # Company names carry the unique prefix as a suffix so ?q=<prefix> scopes
    # queries to exactly these rows: the api_server-style modules share one
    # per-pid DB file, it accumulates >100 rows across the full suite, and the
    # list view caps limit at 100 — exact-order assertions must therefore be
    # page-independent. The differing FIRST characters still drive the sort.
    rows = [
        (f"{prefix}-a", f"Zebra Holdings {prefix}", "approved", 80.0, "HIGH"),
        (f"{prefix}-b", f"alpha traders {prefix}", "rejected", 20.0, "LOW"),
        (f"{prefix}-c", f"Mango Corp {prefix}", "submitted", 50.0, "MEDIUM"),
        (f"{prefix}-d", f"Nadir Ventures {prefix}", "submitted", None, "VERY_HIGH"),
    ]
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (f"{prefix}-cl", f"{prefix}@test.com", "hash", "Sort Fixtures Ltd"),
    )
    for app_id, company, status, score, risk_level in rows:
        conn.execute(
            "INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, risk_score, risk_level) "
            "VALUES (?, ?, ?, ?, 'GB', 'Technology', 'Limited Company', ?, ?, ?)",
            (app_id, f"SORT-{secrets.token_hex(4).upper()}", f"{prefix}-cl", company, status, score, risk_level),
        )
    conn.commit()
    conn.close()
    return prefix, [r[0] for r in rows]


class TestStatusFilterAndSort:
    """ux-applications-list-sort-status-tabs: comma status lists + whitelisted sort."""

    def test_single_status_filter_unchanged(self, api_server, sort_filter_apps):
        prefix, _ids = sort_filter_apps
        resp = _get(api_server, f"/api/applications?status=rejected&q={prefix}&limit=100")
        assert resp.status_code == 200
        rows = resp.json()["applications"]
        assert all(r["status"] == "rejected" for r in rows)
        assert any(r["id"] == f"{prefix}-b" for r in rows)

    def test_comma_separated_status_filters_to_the_set(self, api_server, sort_filter_apps):
        prefix, _ids = sort_filter_apps
        resp = _get(api_server, f"/api/applications?status=approved,rejected&q={prefix}&limit=100")
        assert resp.status_code == 200
        rows = resp.json()["applications"]
        assert rows, "expected the seeded approved/rejected apps"
        assert all(r["status"] in ("approved", "rejected") for r in rows)
        ids = {r["id"] for r in rows}
        assert f"{prefix}-a" in ids and f"{prefix}-b" in ids
        assert f"{prefix}-c" not in ids

    def test_comma_status_tolerates_whitespace_and_empties(self, api_server, sort_filter_apps):
        prefix, _ids = sort_filter_apps
        resp = _get(api_server, f"/api/applications?status=%20approved%20,%20,rejected,&q={prefix}&limit=100")
        assert resp.status_code == 200
        rows = resp.json()["applications"]
        assert rows
        assert all(r["status"] in ("approved", "rejected") for r in rows)

    def test_sort_company_name_ascending_is_case_insensitive(self, api_server, sort_filter_apps):
        prefix, ids = sort_filter_apps
        resp = _get(api_server, f"/api/applications?sort=company_name&dir=asc&q={prefix}&limit=100")
        assert resp.status_code == 200
        rows = [r for r in resp.json()["applications"] if r["id"] in set(ids)]
        names = [r["company_name"] for r in rows]
        expected = [f"alpha traders {prefix}", f"Mango Corp {prefix}",
                    f"Nadir Ventures {prefix}", f"Zebra Holdings {prefix}"]
        assert names == expected

    def test_sort_risk_score_descending_nulls_last(self, api_server, sort_filter_apps):
        prefix, ids = sort_filter_apps
        resp = _get(api_server, f"/api/applications?sort=risk_score&dir=desc&q={prefix}&limit=100")
        assert resp.status_code == 200
        rows = [r for r in resp.json()["applications"] if r["id"] in set(ids)]
        scores = [r["risk_score"] for r in rows]
        # COALESCE(risk_score, -1): unscored rows sort BELOW every real
        # score on descending — never above the actual high-risk rows.
        assert scores == [80.0, 50.0, 20.0, None]

    def test_sort_risk_level_uses_severity_rank_not_alphabetical(self, api_server, sort_filter_apps):
        prefix, ids = sort_filter_apps
        resp = _get(api_server, f"/api/applications?sort=risk_level&dir=desc&q={prefix}&limit=100")
        assert resp.status_code == 200
        rows = [r for r in resp.json()["applications"] if r["id"] in set(ids)]
        levels = [r["risk_level"] for r in rows]
        # Alphabetical text ordering would give VERY_HIGH, MEDIUM, LOW, HIGH.
        assert levels == ["VERY_HIGH", "HIGH", "MEDIUM", "LOW"]

    def test_unknown_sort_key_falls_back_to_default_order(self, api_server, sort_filter_apps):
        resp = _get(api_server, "/api/applications?sort=;%20DROP%20TABLE%20applications;--&limit=100")
        assert resp.status_code == 200
        body = resp.json()
        created = [r["created_at"] for r in body["applications"]]
        assert created == sorted(created, reverse=True), "fallback must be created_at DESC"
        # And the table survived the attempt.
        assert _get(api_server, "/api/applications").status_code == 200

    def test_unknown_dir_value_coerces_to_desc(self, api_server, sort_filter_apps):
        prefix, ids = sort_filter_apps
        resp = _get(api_server, f"/api/applications?sort=risk_score&dir=sideways;--&q={prefix}&limit=100")
        assert resp.status_code == 200
        rows = [r for r in resp.json()["applications"] if r["id"] in set(ids)]
        scores = [r["risk_score"] for r in rows]
        assert scores == [80.0, 50.0, 20.0, None], "any dir other than asc must mean DESC"

    def test_sort_composes_with_comma_status_filter(self, api_server, sort_filter_apps):
        prefix, _ids = sort_filter_apps
        resp = _get(
            api_server,
            f"/api/applications?status=approved,rejected&sort=company_name&dir=asc&q={prefix}&limit=100",
        )
        assert resp.status_code == 200
        rows = [r for r in resp.json()["applications"] if r["id"].startswith(prefix)]
        assert [r["id"] for r in rows] == [f"{prefix}-b", f"{prefix}-a"]
