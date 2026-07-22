"""PR-C — honesty & audit integrity (audit H4, H5, H6 + undo role gate).

- H5: triage buckets by the MOST MATERIAL category, so a sanctions+adverse hit
  counts under Sanctions and the Sanctions tile can never read 0 while a
  sanctions hit exists. The SHARED categoriser (which feeds evidence linking)
  is left unchanged.
- H4: the subject rollup strip states only the adverse-media count and never
  asserts the hits are "near-identical copies of one story".
- H6: the per-hit audit detail records the officer rationale and what each
  hit's decision superseded (prior_dispositions), in the append-only audit_log.
- Undo gate: undoing/overwriting a recorded false-positive clearance requires a
  clear-authorised role.
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


def _server():
    import importlib
    return importlib.import_module("server")


# --------------------------------------------------------------------------
# H5 — unit: most-material bucketing, shared categoriser untouched
# --------------------------------------------------------------------------

def test_bucket_categoriser_is_most_material_first():
    srv = _server()
    bc = srv._screening_evidence_bucket_category
    # A hit carrying BOTH sanctions and adverse-media signals buckets under
    # Sanctions (most material) — never Adverse Media.
    assert bc("sanctions", "adverse media") == "Sanctions"
    assert bc("adverse media", "sanction list") == "Sanctions"
    assert bc("PEP", "adverse media") == "PEP"           # pep over adverse
    assert bc("watchlist", "adverse media") == "Watchlist"
    assert bc("adverse media") == "Adverse Media"        # adverse-only unchanged
    assert bc("something else") == "Unclassified Provider Risk"


def test_shared_categoriser_order_is_unchanged_for_linking():
    """The shared _screening_evidence_category MUST keep its original order so
    evidence-to-subject linking is byte-identical (a multi-category item still
    contributes 'Adverse Media' to _screening_row_categories)."""
    srv = _server()
    c = srv._screening_evidence_category
    assert c("sanctions", "adverse media") == "Adverse Media"  # original order preserved
    assert c("pep", "adverse media") == "Adverse Media"
    assert c("sanction") == "Sanctions"


def test_bucket_map_maps_returned_labels_to_buckets():
    srv = _server()
    m = srv._TRIAGE_BUCKET_BY_CATEGORY
    assert m.get("sanctions") == "sanctions"
    assert m.get("watchlist") == "watchlist"
    assert m.get("pep") == "pep"
    assert m.get("adverse media") == "adverse_media"


# --------------------------------------------------------------------------
# H4 — static: strip claims only the count
# --------------------------------------------------------------------------

def test_rollup_strip_does_not_fabricate_one_story_claim():
    import re
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "arie-backoffice.html").read_text(encoding="utf-8")
    start = html.index("function screeningSubjectRollupStrip")
    strip = html[start:html.index("function ", start + 10)]
    # Assert against the RENDERED strings only — strip JS line-comments first, so
    # the explanatory comment (which legitimately names the retired phrase) can't
    # produce a false pass or false fail.
    rendered = "\n".join(re.sub(r"//.*$", "", line) for line in strip.splitlines())
    assert "copies of one story" not in rendered
    assert "near-identical" not in rendered        # no duplication claim in any rendered string
    assert "adverse-media hits" in rendered        # count still shown
    assert "review the ranked list" in rendered    # honest, count-only framing


def test_h5_reorder_is_isolated_to_the_triage_bucket_site():
    """Lock the H5 isolation: the two evidence-LINKING categorisers must keep
    calling the unchanged shared _screening_evidence_category, never the new
    most-material bucket categoriser (which would detach evidence)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")

    def _region(name, nxt):
        return src[src.index(f"def {name}"):src.index(f"def {nxt}", src.index(f"def {name}") + 1)]

    row_cats = _region("_screening_row_categories", "_screening_evidence_subject_matches")
    assert "_screening_evidence_category(" in row_cats
    assert "_screening_evidence_bucket_category(" not in row_cats
    # The bucket categoriser is used by exactly one caller (the triage-bucket
    # site) besides its own definition.
    assert src.count("_screening_evidence_bucket_category(") == 2


# --------------------------------------------------------------------------
# H5 + H6 + undo gate — integration
# --------------------------------------------------------------------------

def _find_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
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
        asyncio.set_event_loop(asyncio.new_event_loop())
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


def _seed(app_id, app_ref, company, *, sanctions_and_adverse=False, num_hits=2):
    from db import get_db
    conn = get_db()
    try:
        conn.execute("DELETE FROM screening_hit_dispositions WHERE application_id=?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id=?", (app_id,))
        results = []
        for i in range(num_hits):
            cats = ["sanctions", "adverse_media"] if sanctions_and_adverse else ["sanctions"]
            results.append({
                "name": f"Hit {i}", "matching_name": f"Hit {i}", "is_sanctioned": True,
                "match_categories": cats, "categories": cats,
                "provider_risk_identifier": f"prc-risk-{app_id}-{i}",
                "provider_profile_identifier": f"prc-profile-{app_id}-{i}",
                "provider_alert_identifier": f"prc-alert-{app_id}-{i}",
            })
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
        conn.execute(
            "INSERT INTO applications (id, ref, client_id, company_name, status, prescreening_data) "
            "VALUES (?,?,?,?,?,?)",
            (app_id, app_ref, "prc_client", company, "in_review",
             json.dumps({"screening_report": report})),
        )
        conn.commit()
    finally:
        conn.close()


def _hit_ids(api_server, H, base):
    r = http_requests.get(f"{api_server}/api/screening/hit-disposition", params={**base}, headers=H, timeout=5)
    assert r.status_code == 200, r.text
    return [it["hit_id"] for it in (r.json().get("items") or []) if it.get("hit_id")]


class TestHonestyAndAudit:
    def test_sanctions_plus_adverse_hit_counts_under_sanctions_tile(self, api_server):
        """H5 end-to-end: a hit flagged sanctions AND adverse buckets under
        Sanctions, so the Sanctions tile is non-zero (never 0-with-a-hit)."""
        from auth import create_token
        _seed("app_prc_h5", "ARF-PRC-H5", "PR-C H5 Ltd", sanctions_and_adverse=True, num_hits=2)
        H = {"Authorization": f"Bearer {create_token('admin001','admin','Admin','officer')}"}
        r = http_requests.get(f"{api_server}/api/screening/queue",
                              params={"include_evidence": 1, "application_ref": "ARF-PRC-H5", "show_fixtures": "true"},
                              headers=H, timeout=8)
        assert r.status_code == 200, r.text
        rows = [x for x in r.json().get("rows", []) if x.get("subject_type") == "entity"]
        assert rows, r.text
        buckets = (rows[0].get("triage") or {}).get("buckets") or {}
        assert buckets.get("sanctions", 0) >= 1, f"sanctions tile must not be 0 with a sanctions hit: {buckets}"

    def test_per_hit_audit_records_rationale_and_prior_disposition(self, api_server):
        """H6: the audit detail carries rationale + prior_dispositions so a
        superseded clearance's reasoning survives the row being overwritten."""
        from auth import create_token
        from db import get_db
        _seed("app_prc_h6", "ARF-PRC-H6", "PR-C H6 Ltd", num_hits=2)
        H = {"Authorization": f"Bearer {create_token('admin001','admin','Admin','officer')}"}
        base = {"application_id": "app_prc_h6", "subject_type": "entity", "subject_name": "PR-C H6 Ltd"}
        hid = _hit_ids(api_server, H, base)[0]
        # First clearance with a rationale.
        http_requests.post(f"{api_server}/api/screening/hit-disposition",
                           json={**base, "hit_id": hid, "disposition": "cleared",
                                 "rationale": "DOB mismatch confirmed against passport; false positive."},
                           headers=H, timeout=3)
        # Overwrite to a true match — the prior 'cleared' must be captured.
        http_requests.post(f"{api_server}/api/screening/hit-disposition",
                           json={**base, "hit_id": hid, "disposition": "match", "materiality": "high",
                                 "rationale": "Reassessed — genuine match after further review."},
                           headers=H, timeout=3)
        conn = get_db()
        rows = conn.execute(
            "SELECT detail FROM audit_log WHERE target=? AND action='Screening Hit Disposition' ORDER BY id",
            ("ARF-PRC-H6",)).fetchall()
        conn.close()
        details = [json.loads(dict(r)["detail"]) for r in rows]
        assert any("DOB mismatch confirmed" in (d.get("rationale") or "") for d in details), \
            "first clearance rationale not recorded in audit_log"
        overwrite = [d for d in details if d.get("disposition") == "match"]
        assert overwrite and overwrite[-1].get("prior_dispositions", {}).get(hid) == "cleared", \
            f"prior 'cleared' disposition not captured on overwrite: {overwrite}"

    def test_analyst_cannot_undo_or_overwrite_a_clearance(self, api_server):
        """Undo gate: reversing a recorded false-positive clearance needs a
        clear-authorised role — an analyst cannot undo or overwrite it."""
        from auth import create_token
        _seed("app_prc_gate", "ARF-PRC-GATE", "PR-C Gate Ltd", num_hits=2)
        admin = {"Authorization": f"Bearer {create_token('admin001','admin','Admin','officer')}"}
        analyst = {"Authorization": f"Bearer {create_token('analyst001','analyst','Analyst','officer')}"}
        base = {"application_id": "app_prc_gate", "subject_type": "entity", "subject_name": "PR-C Gate Ltd"}
        hid = _hit_ids(api_server, admin, base)[0]
        # Admin clears the hit.
        r = http_requests.post(f"{api_server}/api/screening/hit-disposition",
                               json={**base, "hit_id": hid, "disposition": "cleared"}, headers=admin, timeout=3)
        assert r.status_code == 200, r.text
        # Analyst tries to UNDO the clearance → 403.
        r = http_requests.post(f"{api_server}/api/screening/hit-disposition",
                               json={**base, "hit_id": hid, "disposition": "pending"}, headers=analyst, timeout=3)
        assert r.status_code == 403, r.text
        # Analyst tries to OVERWRITE the clearance with escalated → 403.
        r = http_requests.post(f"{api_server}/api/screening/hit-disposition",
                               json={**base, "hit_id": hid, "disposition": "escalated"}, headers=analyst, timeout=3)
        assert r.status_code == 403, r.text
        # An SCO/admin can undo it.
        r = http_requests.post(f"{api_server}/api/screening/hit-disposition",
                               json={**base, "hit_id": hid, "disposition": "pending"}, headers=admin, timeout=3)
        assert r.status_code == 200, r.text

    def test_analyst_can_still_confirm_a_pending_hit(self, api_server):
        """The gate only guards reversing a CLEARED hit — an analyst can still
        confirm a true match on a pending hit (unchanged behaviour)."""
        from auth import create_token
        _seed("app_prc_pending", "ARF-PRC-PENDING", "PR-C Pending Ltd", num_hits=2)
        analyst = {"Authorization": f"Bearer {create_token('analyst001','analyst','Analyst','officer')}"}
        base = {"application_id": "app_prc_pending", "subject_type": "entity", "subject_name": "PR-C Pending Ltd"}
        hid = _hit_ids(api_server, analyst, base)[0]
        r = http_requests.post(f"{api_server}/api/screening/hit-disposition",
                               json={**base, "hit_id": hid, "disposition": "match", "materiality": "high"},
                               headers=analyst, timeout=3)
        assert r.status_code == 200, r.text
