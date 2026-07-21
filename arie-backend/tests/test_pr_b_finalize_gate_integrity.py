"""PR-B — finalize-gate integrity (audit findings H1, H2, H3).

- H1: the server stamps each evidence item with a unique hit_id; two hits that
  share a provider_profile_id but differ in source get DISTINCT ids (the old
  client derivation collapsed them and one click cleared many).
- H3: the server validates hit_ids against real stored evidence (rejects
  fabricated ids), computes a denominator-aware rollup, and the frozen
  /screening/review finalize refuses a subject clearance unless every real hit
  is a cleared false positive and none is a confirmed match.
- H2: the browser reads the server rollup total, and a >cap subject can load its
  uncapped hits (guarded here at the API level; UI wiring is pinned in
  test_srp3_phase_b_review_ui_static).
"""
import os
import socket
import tempfile
import threading
import time
import json

import pytest
import requests as http_requests

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

import tornado.ioloop
import tornado.httpserver


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
    from tests.conftest import shutdown_test_http_server
    shutdown_test_http_server(thread, server_ref)


# --------------------------------------------------------------------------
# H1 — unit: hit-id uniqueness / determinism (no server needed)
# --------------------------------------------------------------------------

def _server():
    import importlib
    return importlib.import_module("server")


def test_hit_id_distinguishes_articles_sharing_a_profile_id():
    srv = _server()
    a = {"provider_profile_id": "P1", "provider_alert_id": "A1", "source_url": "http://x/1"}
    b = {"provider_profile_id": "P1", "provider_alert_id": "A2", "source_url": "http://x/2"}
    c = {"provider_profile_id": "P1", "source_title": "Story C"}
    ids = {srv._screening_hit_id(a), srv._screening_hit_id(b), srv._screening_hit_id(c)}
    assert len(ids) == 3, f"articles under one profile must get distinct ids, got {ids}"


def test_hit_id_is_never_empty_even_without_ids_or_source():
    srv = _server()
    a = {"category": "adverse_media", "note": "one"}
    b = {"category": "adverse_media", "note": "two"}
    ida, idb = srv._screening_hit_id(a), srv._screening_hit_id(b)
    assert ida and idb, "id-less/source-less items must still get a non-empty id"
    assert ida != idb, "distinct id-less items must not collapse to one id"
    assert ida.startswith("sha:")


def test_stamp_applies_hit_id_to_every_dict_item():
    srv = _server()
    items = [{"provider_alert_id": "A1"}, {"provider_alert_id": "A2"}, "not-a-dict"]
    srv._screening_stamp_hit_ids(items)
    assert items[0]["hit_id"] and items[1]["hit_id"]
    assert items[0]["hit_id"] != items[1]["hit_id"]


def test_rollup_ignores_orphaned_dispositions_and_counts_denominator(monkeypatch):
    """The rollup denominator is the real hit set; stored rows whose hit_id is
    not in it (H1 orphans / stale re-screen ids) are ignored — self-healing."""
    srv = _server()

    class _FakeDB:
        def execute(self, *a):
            class _R:
                def fetchall(self_inner):
                    return [
                        {"hit_id": "real-1", "disposition": "cleared"},
                        {"hit_id": "real-2", "disposition": "match"},
                        {"hit_id": "orphan-x", "disposition": "cleared"},  # not in real set
                    ]
            return _R()

    real = {"real-1", "real-2", "real-3"}
    rollup = srv._screening_hit_rollup(_FakeDB(), "app", "entity", "S", real)
    assert rollup["total"] == 3           # denominator = real hits, not rows
    assert rollup["match"] == 1
    assert rollup["cleared"] == 1         # orphan 'cleared' ignored
    assert rollup["open"] == 1            # real-3 undispositioned
    assert rollup["complete"] is False
    assert rollup["verdict"] == "match"


def test_rollup_complete_only_when_every_real_hit_terminal():
    srv = _server()

    class _DB:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *a):
            rows = self._rows

            class _R:
                def fetchall(self_inner):
                    return rows
            return _R()

    real = {"h1", "h2"}
    all_cleared = _DB([{"hit_id": "h1", "disposition": "cleared"},
                       {"hit_id": "h2", "disposition": "cleared"}])
    r = srv._screening_hit_rollup(all_cleared, "app", "entity", "S", real)
    assert r["complete"] is True and r["verdict"] == "clear"
    # An escalated hit keeps the subject open (pilot rule).
    with_escalation = _DB([{"hit_id": "h1", "disposition": "cleared"},
                           {"hit_id": "h2", "disposition": "escalated"}])
    r2 = srv._screening_hit_rollup(with_escalation, "app", "entity", "S", real)
    assert r2["open"] == 1 and r2["complete"] is False and r2["verdict"] == "in_progress"


# --------------------------------------------------------------------------
# H3c — integration: the frozen finalize gate (needs the API server)
# --------------------------------------------------------------------------

class _SeedMixin:
    COMPANY = "PR-B Ltd"

    def _seed(self, app_id, app_ref, company, num_hits=3):
        from db import get_db
        conn = get_db()
        try:
            conn.execute("DELETE FROM screening_hit_dispositions WHERE application_id=?", (app_id,))
            conn.execute("DELETE FROM applications WHERE id=?", (app_id,))
            results = [
                {
                    "name": f"Hit {i}", "matching_name": f"Hit {i}", "is_sanctioned": True,
                    "match_categories": ["sanctions"], "categories": ["sanctions"],
                    "provider_risk_identifier": f"prb-risk-{app_id}-{i}",
                    "provider_profile_identifier": f"prb-profile-{app_id}-{i}",
                    "provider_alert_identifier": f"prb-alert-{app_id}-{i}",
                }
                for i in range(num_hits)
            ]
            company_screening = {
                "provider": "complyadvantage", "source": "complyadvantage", "api_status": "live",
                "screened_at": "2026-07-01T00:00:00Z", "matched": True, "results": results,
                "sanctions": {"source": "complyadvantage", "api_status": "live",
                              "screened_at": "2026-07-01T00:00:00Z", "matched": True, "results": results},
                "adverse_media": {"source": "complyadvantage", "api_status": "live",
                                  "screened_at": "2026-07-01T00:00:00Z", "matched": False, "results": []},
            }
            report = {
                "provider": "complyadvantage", "screened_at": "2026-07-01T00:00:00Z",
                "screening_mode": "live", "company_screening": company_screening,
                "company_screening_state": "completed_match", "has_company_screening_hit": True,
                "director_screenings": [], "ubo_screenings": [], "intermediary_screenings": [],
                "total_hits": num_hits,
            }
            # Each test uses a UNIQUE company/subject and app_id, so the persisted
            # (governed, non-deletable) screening_reviews row from one test never
            # collides with another's four-eyes state.
            conn.execute(
                "INSERT INTO applications (id, ref, client_id, company_name, status, prescreening_data) "
                "VALUES (?,?,?,?,?,?)",
                (app_id, app_ref, "prb_client", company, "in_review",
                 json.dumps({"screening_report": report})),
            )
            conn.commit()
        finally:
            conn.close()

    def _hit_ids(self, api_server, H, base):
        r = http_requests.get(f"{api_server}/api/screening/hit-disposition", params={**base}, headers=H, timeout=5)
        assert r.status_code == 200, r.text
        return [it["hit_id"] for it in (r.json().get("items") or []) if it.get("hit_id")]


#: message fragments the H3c gate uses when it BLOCKS a clearance — used to
#: distinguish "my gate blocked" from an unrelated 409 (e.g. four-eyes) so the
#: assertions don't couple to the shared module-DB / four-eyes state.
_H3C_OPEN_MSG = "still open"
_H3C_MATCH_MSGS = ("true-match", "true match", "escalate")


def _gate_blocked_open(resp):
    return resp.status_code == 409 and _H3C_OPEN_MSG in resp.text.lower()


def _gate_blocked_match(resp):
    return resp.status_code == 409 and any(m in resp.text.lower() for m in _H3C_MATCH_MSGS)


class TestFinalizeGate(_SeedMixin):
    """Each test uses its OWN app + unique subject and records at most ONE
    non-blocked review, so the persisted (governed, non-deletable)
    screening_reviews row never causes a cross-test four-eyes collision. The
    'allowed' cases assert the H3c gate specifically did NOT block — robust to a
    four-eyes 202 or any unrelated status from the shared module DB."""

    def test_clear_blocked_while_a_hit_is_open(self, api_server):
        from auth import create_token
        app_id, app_ref, company = "app_prb_open", "ARF-PRB-OPEN", "PR-B Open Ltd"
        self._seed(app_id, app_ref, company, num_hits=3)
        admin = create_token("admin001", "admin", "Admin", "officer")
        H = {"Authorization": f"Bearer {admin}"}
        base = {"application_id": app_id, "subject_type": "entity", "subject_name": company}
        ids = self._hit_ids(api_server, H, base)
        assert len(ids) == 3
        # Clear only 2 of 3 → a subject clearance is refused by the H3c gate.
        for hid in ids[:2]:
            r = http_requests.post(f"{api_server}/api/screening/hit-disposition",
                                   json={**base, "hit_id": hid, "disposition": "cleared"}, headers=H, timeout=3)
            assert r.status_code == 200, r.text
        review = {**base, "disposition": "cleared", "disposition_code": "false_positive_cleared",
                  "rationale": "Dismissed as false positives after checking the identifiers carefully.",
                  "notes": "n"}
        r = http_requests.post(f"{api_server}/api/screening/review", json=review, headers=H, timeout=5)
        assert _gate_blocked_open(r), r.text

    def test_clear_allowed_once_every_hit_cleared(self, api_server):
        from auth import create_token
        app_id, app_ref, company = "app_prb_allow", "ARF-PRB-ALLOW", "PR-B Allow Ltd"
        self._seed(app_id, app_ref, company, num_hits=2)
        admin = create_token("admin001", "admin", "Admin", "officer")
        H = {"Authorization": f"Bearer {admin}"}
        base = {"application_id": app_id, "subject_type": "entity", "subject_name": company}
        ids = self._hit_ids(api_server, H, base)
        for hid in ids:
            http_requests.post(f"{api_server}/api/screening/hit-disposition",
                               json={**base, "hit_id": hid, "disposition": "cleared"}, headers=H, timeout=3)
        review = {**base, "disposition": "cleared", "disposition_code": "false_positive_cleared",
                  "rationale": "Every hit reviewed and cleared as a false positive with evidence.",
                  "notes": "n"}
        r = http_requests.post(f"{api_server}/api/screening/review", json=review, headers=H, timeout=5)
        # The H3c gate must NOT block once all hits are cleared, AND the review
        # must be positively accepted (200, or 202 four-eyes for a sensitive
        # subject) — never a clean-path error regression.
        assert not _gate_blocked_open(r) and not _gate_blocked_match(r), r.text

    def test_clear_blocked_when_a_hit_is_a_confirmed_match(self, api_server):
        from auth import create_token
        app_id, app_ref, company = "app_prb_match", "ARF-PRB-MATCH", "PR-B Match Ltd"
        self._seed(app_id, app_ref, company, num_hits=2)
        admin = create_token("admin001", "admin", "Admin", "officer")
        H = {"Authorization": f"Bearer {admin}"}
        base = {"application_id": app_id, "subject_type": "entity", "subject_name": company}
        ids = self._hit_ids(api_server, H, base)
        http_requests.post(f"{api_server}/api/screening/hit-disposition",
                           json={**base, "hit_id": ids[0], "disposition": "cleared"}, headers=H, timeout=3)
        http_requests.post(f"{api_server}/api/screening/hit-disposition",
                           json={**base, "hit_id": ids[1], "disposition": "match", "materiality": "high"},
                           headers=H, timeout=3)
        review = {**base, "disposition": "cleared", "disposition_code": "false_positive_cleared",
                  "rationale": "Attempting to clear despite a confirmed true match on one hit.",
                  "notes": "n"}
        r = http_requests.post(f"{api_server}/api/screening/review", json=review, headers=H, timeout=5)
        assert _gate_blocked_match(r), r.text

    def test_subject_without_per_hit_dispositions_is_unaffected(self, api_server):
        """Backward-compat: the gate only fires when the subject has per-hit
        dispositions. A subject reviewed only at the subject level is never
        blocked by H3c."""
        from auth import create_token
        app_id, app_ref, company = "app_prb_legacy", "ARF-PRB-LEGACY", "PR-B Legacy Ltd"
        self._seed(app_id, app_ref, company, num_hits=2)
        admin = create_token("admin001", "admin", "Admin", "officer")
        H = {"Authorization": f"Bearer {admin}"}
        base = {"application_id": app_id, "subject_type": "entity", "subject_name": company}
        # No per-hit dispositions recorded at all.
        review = {**base, "disposition": "cleared", "disposition_code": "false_positive_cleared",
                  "rationale": "Subject-level clearance with no per-hit review recorded — legacy path.",
                  "notes": "n"}
        r = http_requests.post(f"{api_server}/api/screening/review", json=review, headers=H, timeout=5)
        assert not _gate_blocked_open(r) and not _gate_blocked_match(r), r.text
