"""C0 — a document whose checks were ALL skipped must not present as verified.

Section A audit P0 finding: a Regulatory Licence uploaded when the client
declared no licence (LIC-GATE short-circuit) — or a retired document type —
aggregated to overall "verified" with zero executed checks and rendered as
"✓ Verified" / reliance-ready in the back office.

Fix under test:
  * engine: all-skip aggregation → overall "skipped" + not_applicable marker
  * server: persists STATE_SKIPPED, verified_at NULL, results carry skipped_at
  * worker: "skipped" accepted as a terminal job outcome (previously the
    completion allow-list rejected it and the failure path overwrote the
    correct document row as "failed")
  * startup backfill: historical all-skip "verified" rows re-marked "skipped"
  * back office: renders "Not applicable" (never Verified, never Failed),
    optional group, blocks "None", coverage "Not applicable"
  * memo: not-applicable documents excluded from the documentation calculus

The back-office assertions EXECUTE the extracted functions in node against
fixtures — the earlier string-anchored guards were shown vacuous under
sabotage by the adversarial review.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from document_verification import (  # noqa: E402
    CheckClassification,
    _aggregate,
    _pass,
    _skip,
    to_legacy_result,
    verify_document_layered,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKOFFICE = os.path.join(REPO_ROOT, "arie-backoffice.html")
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(BACKEND_DIR, "server.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ── Aggregation semantics ─────────────────────────────────────────


class TestAggregateAllSkip:
    def test_all_skip_is_skipped_not_verified(self):
        result = _aggregate([
            _skip("LIC-GATE", "Licence Applicability Gate", CheckClassification.RULE,
                  "Regulatory licence checks skipped — client declared no licence"),
        ])
        assert result["overall"] == "skipped"
        assert result["not_applicable"] is True
        assert "declared no licence" in result["skipped_reason"]

    def test_mixed_case_external_skip_strings_count(self):
        """The engine predicate must agree with the server's case-insensitive
        one — 'SKIP'/'Skipped' strings from an external producer included."""
        result = _aggregate([
            {"id": "X-1", "label": "A", "classification": "rule", "type": "rule",
             "result": "SKIP", "message": "skipped", "source": "rule"},
            {"id": "X-2", "label": "B", "classification": "rule", "type": "rule",
             "result": "Skipped", "message": "skipped", "source": "rule"},
        ])
        assert result["overall"] == "skipped"
        assert result["not_applicable"] is True

    def test_mixed_skip_and_pass_remains_verified(self):
        result = _aggregate([
            _skip("X-1", "Gate", CheckClassification.RULE, "gate skipped"),
            _pass("DOC-05", "Entity Name Match", CheckClassification.RULE, "match"),
        ])
        assert result["overall"] == "verified"
        assert "not_applicable" not in result

    def test_empty_checks_stay_flagged(self):
        """P0-5 parity: no checks at all is an error state, not not-applicable."""
        result = _aggregate([])
        assert result["overall"] == "flagged"
        assert "not_applicable" not in result

    def test_legacy_result_preserves_marker(self):
        layered = _aggregate([
            _skip("LIC-GATE", "Licence Applicability Gate", CheckClassification.RULE,
                  "Regulatory licence checks skipped — client declared no licence"),
        ])
        legacy = to_legacy_result(layered)
        assert legacy["overall"] == "skipped"
        assert legacy["not_applicable"] is True
        assert legacy["skipped_reason"]


# ── Engine entry point: real short-circuit paths ──────────────────


class TestLayeredEngineSkipPaths:
    def _run(self, doc_type, prescreening):
        return verify_document_layered(
            doc_type=doc_type,
            category="entity",
            file_path=None,
            file_size=0,
            mime_type="application/pdf",
            prescreening_data=prescreening,
            risk_level="LOW",
            existing_hashes=[],
        )

    def test_licence_not_applicable_is_skipped(self):
        result = self._run("licence", {"regulatory_licences": "None"})
        assert result["overall"] == "skipped"
        assert result["not_applicable"] is True
        checks = result["checks"]
        assert len(checks) == 1
        assert checks[0]["id"] == "LIC-GATE"
        assert checks[0]["result"] == "skip"

    def test_retired_doc_type_is_skipped(self):
        result = self._run("cert_reg", {"registered_entity_name": "Acme Ltd"})
        assert result["overall"] == "skipped"
        assert result["not_applicable"] is True

    def test_applicable_licence_is_not_short_circuited(self):
        result = self._run("licence", {"regulatory_licences": "FSC Investment Dealer Licence"})
        assert result["overall"] != "skipped"


# ── Server mapping (source-level guards; runtime covered below) ───


class TestServerStatusMapping:
    def test_all_skipped_maps_to_state_skipped(self):
        src = _read(SERVER)
        assert "_all_checks_skipped" in src
        block = src.split("_all_checks_skipped = bool(checks)", 1)[1][:700]
        assert "status = STATE_SKIPPED" in block
        assert "STATE_VERIFIED if all_passed else STATE_FLAGGED" in block

    def test_skipped_status_carries_no_verified_at_column(self):
        src = _read(SERVER)
        assert '_verified_at_sql = "NULL" if status == STATE_SKIPPED' in src

    def test_skipped_results_json_swaps_verified_at_for_skipped_at(self):
        src = _read(SERVER)
        assert '"verified_at": None if status == STATE_SKIPPED else _completed_at' in src
        assert '"skipped_at": _completed_at if status == STATE_SKIPPED else None' in src


# ── Worker terminal-state contract (the H1 corruption path) ───────


class TestWorkerTerminalContract:
    def test_job_success_allow_list_accepts_skipped(self):
        """mark_verification_job_succeeded must accept every state in the
        worker's TERMINAL_DOCUMENT_STATES — rejecting 'skipped' routed the
        worker into its failure path, which overwrote an already-correct
        document row as 'failed' with a false system-failure record."""
        from verification_jobs import mark_verification_job_succeeded  # noqa: F401
        import inspect
        src = inspect.getsource(mark_verification_job_succeeded)
        assert '"skipped"' in src.split("raise ValueError", 1)[0]

    def test_worker_terminal_states_and_allow_list_agree(self):
        from verification_worker import TERMINAL_DOCUMENT_STATES
        from verification_jobs import mark_verification_job_succeeded
        import inspect
        src = inspect.getsource(mark_verification_job_succeeded)
        allow = re.search(r"status not in \(([^)]*)\)", src)
        assert allow, "allow-list not found"
        allowed = {token.strip().strip('"\'') for token in allow.group(1).split(",") if token.strip()}
        assert set(TERMINAL_DOCUMENT_STATES) <= allowed, (
            f"worker terminal states {set(TERMINAL_DOCUMENT_STATES)} not all accepted "
            f"by the job success allow-list {allowed}"
        )


# ── Back office rendering: EXECUTED in node against fixtures ──────


def _extract_js_functions(html, names):
    out = []
    for name in names:
        marker = "function " + name + "("
        idx = html.index(marker)
        end = html.index("\nfunction ", idx + 1)
        out.append(html[idx:end])
    return "\n".join(out)


BO_FUNCTIONS = [
    "documentChecksAllSkipped",
    "documentReadyDisplayState",
    "documentRelianceDisplayState",
    "documentReviewGroupKey",
    "documentBlocksDisplay",
    "firstMeaningfulDetailValue",
    "normalizeCoverageCheckLabel",
    "coverageLabelsMatch",
    "documentCoverageStatusBucket",
    "documentPolicyExpectedChecks",
    "buildDocumentVerificationCoverage",
]

BO_FIXTURES = {
    "na_licence": {
        "doc": {
            "doc_type": "licence",
            "verification_status": "skipped",
            "verification_results": {
                "overall": "skipped", "not_applicable": True,
                "skipped_reason": "Regulatory licence checks skipped — client declared no licence",
                "checks": [{"id": "LIC-GATE", "label": "Licence Applicability Gate",
                            "result": "skip", "message": "client declared no licence"}],
            },
        },
        "policy": {"backend_executable": True, "gateBehavior": "Blocks reliance where a regulated/licensed business activity is declared.",
                   "materialChecks": ["Entity Name Match", "Licence Validity", "Licence Scope", "Certification"]},
    },
    "agent_disabled": {
        "doc": {
            "doc_type": "cert_inc",
            "verification_status": "skipped",
            "verification_results": {"overall": "skipped", "checks": [],
                                     "system_warning": "agent1_disabled"},
        },
        "policy": {"backend_executable": True, "materialChecks": ["Entity Name Match"]},
    },
    "historical_unswept": {
        "doc": {
            "doc_type": "licence",
            "verification_status": "verified",
            "verified_at": "2026-05-01 10:00:00",
            "verification_results": {
                "overall": "verified",
                "checks": [{"id": "LIC-GATE", "label": "Licence Applicability Gate",
                            "result": "skip", "message": "client declared no licence"}],
            },
        },
        "policy": {"backend_executable": True, "materialChecks": ["Entity Name Match"]},
    },
    "rejected_precedence": {
        "doc": {
            "doc_type": "licence",
            "review_status": "rejected",
            "verification_status": "skipped",
            "verification_results": {"overall": "skipped", "not_applicable": True,
                                     "checks": [{"result": "skip", "label": "g"}]},
        },
        "policy": {"backend_executable": True, "materialChecks": []},
    },
    "manual_accept_precedence": {
        "doc": {
            "doc_type": "licence",
            "review_status": "accepted",
            "verification_status": "skipped",
            "verification_results": {"overall": "skipped", "not_applicable": True,
                                     "checks": [{"result": "skip", "label": "g"}]},
        },
        "policy": {"backend_executable": True, "materialChecks": []},
    },
    "genuine_verified": {
        "doc": {
            "doc_type": "cert_inc",
            "verification_status": "verified",
            "verified_at": "2026-08-01 10:00:00",
            "verification_results": {
                "overall": "verified",
                "checks": [{"id": "DOC-05", "label": "Entity Name Match",
                            "result": "pass", "message": "match"}],
            },
        },
        "policy": {"backend_executable": True, "materialChecks": ["Entity Name Match"]},
    },
    "flagged_all_pending_buckets": {
        # F3 regression: bucketed counts fold 'pending' into skipped — the
        # coverage state must NOT claim Not applicable for these.
        "doc": {
            "doc_type": "fin_stmt",
            "verification_status": "flagged",
            "verification_results": {
                "overall": "flagged",
                "checks": [{"id": "DOC-20", "label": "Financial Period",
                            "result": "pending", "message": "queued"},
                           {"id": "DOC-21", "label": "Entity Name Match",
                            "result": "pending", "message": "queued"}],
            },
        },
        "policy": {"backend_executable": True, "materialChecks": ["Financial Period"]},
    },
}


@pytest.fixture(scope="module")
def bo_runtime_results():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    html = _read(BACKOFFICE)
    js = _extract_js_functions(html, BO_FUNCTIONS)
    harness = (
        "function documentNeedsTypeSelection() { return false; }\n"
        + js + "\n"
        + "var fixtures = " + json.dumps(BO_FIXTURES) + ";\n"
        + """
var out = {};
Object.keys(fixtures).forEach(function(key) {
  var doc = fixtures[key].doc;
  var policy = fixtures[key].policy;
  var state = documentRelianceDisplayState(doc, policy);
  var coverage = buildDocumentVerificationCoverage(doc, policy);
  out[key] = {
    label: state && state.label,
    tone: state && state.tone,
    group: documentReviewGroupKey(doc, policy, state, null, null),
    blocks: documentBlocksDisplay(doc, policy, state, null),
    coverage_state: coverage.runState,
    all_skipped: documentChecksAllSkipped(doc)
  };
});
out.__group_for_na_label = documentReviewGroupKey({id: 'x'}, null, {label: 'Not applicable'}, null, null);
console.log(JSON.stringify(out));
"""
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(harness)
        path = fh.name
    try:
        proc = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    assert proc.returncode == 0, f"node harness failed: {proc.stderr[:800]}"
    return json.loads(proc.stdout.strip())


class TestBackofficeRuntimeRendering:
    def test_new_na_licence(self, bo_runtime_results):
        r = bo_runtime_results["na_licence"]
        assert r["label"] == "Not applicable"
        assert r["tone"] == "skipped"
        assert r["group"] == "optional"
        assert r["blocks"] == "None"
        assert r["coverage_state"] == "not_applicable"

    def test_agent_disabled_skip_keeps_existing_treatment(self, bo_runtime_results):
        r = bo_runtime_results["agent_disabled"]
        assert r["all_skipped"] is False
        assert r["label"] == "Failed"

    def test_historical_unswept_row_no_longer_verified(self, bo_runtime_results):
        """Pre-backfill rows stored as 'verified' with an all-skip check list
        must render Not applicable — the display keys on evidence, not the
        stale status column."""
        r = bo_runtime_results["historical_unswept"]
        assert r["label"] == "Not applicable"
        assert r["group"] == "optional"

    def test_officer_decisions_outrank_not_applicable(self, bo_runtime_results):
        assert bo_runtime_results["rejected_precedence"]["label"] == "Rejected"
        assert bo_runtime_results["manual_accept_precedence"]["label"] == "Manual accepted"

    def test_genuine_verified_doc_unchanged(self, bo_runtime_results):
        r = bo_runtime_results["genuine_verified"]
        assert r["label"] == "Verified"
        assert r["group"] == "verified"
        assert r["blocks"] == "None"

    def test_pending_buckets_do_not_fake_not_applicable(self, bo_runtime_results):
        r = bo_runtime_results["flagged_all_pending_buckets"]
        assert r["all_skipped"] is False
        assert r["coverage_state"] != "not_applicable"
        assert r["label"] == "Failed"

    def test_na_label_never_routes_to_verified_group(self, bo_runtime_results):
        assert bo_runtime_results["__group_for_na_label"] == "optional"


class TestBackofficeStaticGuards:
    @pytest.fixture(scope="class")
    def html(self):
        return _read(BACKOFFICE)

    def test_not_applicable_return_precedes_failed_mapping(self, html):
        fn = html.split("function documentRelianceDisplayState(doc, policy) {", 1)[1]
        fn = fn.split("\nfunction ", 1)[0]
        na_return = fn.index("return { label: 'Not applicable', tone: 'skipped' };")
        failed_map = fn.index("['failed', 'flagged', 'skipped'].indexOf(verificationState)")
        assert na_return < failed_map

    def test_ready_state_guards_all_skip_evidence(self, html):
        fn = html.split("function documentReadyDisplayState(doc) {", 1)[1]
        fn = fn.split("\nfunction ", 1)[0]
        assert "documentChecksAllSkipped(doc)" in fn
        assert "&& !allSkipped)" in fn

    def test_required_action_copy(self, html):
        assert "No verification checks apply to this document" in html

    def test_badge_css_exists(self, html):
        assert ".reliance-badge.skipped" in html

    def test_coverage_badge_mapping(self, html):
        assert "coverage.runState === 'not_applicable' ? 'Not applicable'" in html

    def test_panel_counts_exclude_not_applicable(self, html):
        assert "label !== 'not applicable'" in html


# ── Runtime end-to-end: verify endpoint, backfill, memo ───────────


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
def c0_api_server():
    import requests  # noqa: F401  (fail early if missing)
    import tornado.httpserver
    import tornado.ioloop

    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_c0_{os.getpid()}.db")
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

    from tests.conftest import shutdown_test_http_server
    shutdown_test_http_server(thread, server_ref)


def _admin_headers():
    from auth import create_token
    token = create_token("admin001", "admin", "Test Admin", "officer")
    return {"Authorization": f"Bearer {token}"}


def _insert_c0_app_and_doc(doc_id, doc_type, prescreening):
    from db import get_db
    conn = get_db()
    app_id = f"app-c0-{doc_id}"
    conn.execute(
        "INSERT INTO applications (id, ref, company_name, country, sector, entity_type, "
        "status, risk_level, risk_score, prescreening_data) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (app_id, f"C0-{doc_id}", "C0 Test Ltd", "Mauritius", "Consulting",
         "Private Limited Company", "under_review", "LOW", 20,
         json.dumps(prescreening)),
    )
    conn.execute(
        "INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, "
        "verification_status, is_current, version) VALUES (?,?,?,?,?,?,1,1)",
        (doc_id, app_id, doc_type, f"{doc_type}.pdf", f"/nonexistent/{doc_id}.pdf",
         "pending"),
    )
    conn.commit()
    conn.close()
    return app_id


class TestVerifyEndpointRuntime:
    def test_licence_not_applicable_persists_skipped(self, c0_api_server):
        import requests
        from db import get_db

        _insert_c0_app_and_doc("lic1", "licence", {
            "registered_entity_name": "C0 Test Ltd",
            "regulatory_licences": "None",
        })
        resp = requests.post(
            f"{c0_api_server}/api/documents/lic1/verify",
            headers=_admin_headers(), timeout=30,
        )
        assert resp.status_code == 200, resp.text

        conn = get_db()
        row = conn.execute(
            "SELECT verification_status, verified_at, verification_results "
            "FROM documents WHERE id='lic1'"
        ).fetchone()
        conn.close()
        assert row["verification_status"] == "skipped"
        assert row["verified_at"] in (None, "")
        results = json.loads(row["verification_results"])
        assert results["overall"] == "skipped"
        assert results["not_applicable"] is True
        assert results.get("skipped_at")
        assert results.get("verified_at") in (None, "")

    def test_retired_doc_type_persists_skipped(self, c0_api_server):
        import requests
        from db import get_db

        _insert_c0_app_and_doc("reg1", "cert_reg", {
            "registered_entity_name": "C0 Test Ltd",
        })
        resp = requests.post(
            f"{c0_api_server}/api/documents/reg1/verify",
            headers=_admin_headers(), timeout=30,
        )
        assert resp.status_code == 200, resp.text
        conn = get_db()
        row = conn.execute(
            "SELECT verification_status, verified_at FROM documents WHERE id='reg1'"
        ).fetchone()
        conn.close()
        assert row["verification_status"] == "skipped"
        assert row["verified_at"] in (None, "")


class TestWorkerJobSuccessRuntime:
    def test_mark_succeeded_accepts_skipped_terminal(self, c0_api_server):
        from db import get_db
        from verification_jobs import (
            claim_next_verification_job,
            enqueue_verification_job,
            mark_verification_job_succeeded,
        )

        app_id = _insert_c0_app_and_doc("wrk1", "licence", {
            "registered_entity_name": "C0 Test Ltd",
            "regulatory_licences": "None",
        })
        conn = get_db()
        doc = dict(conn.execute("SELECT * FROM documents WHERE id='wrk1'").fetchone())
        app_row = dict(conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
        enqueue_verification_job(conn, doc, app_row, None)
        conn.commit()
        job = claim_next_verification_job(conn, worker_id="c0-test-worker")
        assert job is not None
        conn.commit()

        updated = mark_verification_job_succeeded(
            conn,
            job["id"],
            worker_id="c0-test-worker",
            verification_status="skipped",
            verification_results={"overall": "skipped", "not_applicable": True,
                                  "checks": [{"result": "skip", "label": "gate"}]},
            transition_document=False,
        )
        conn.commit()
        assert updated["status"] == "succeeded"

        with pytest.raises(ValueError):
            mark_verification_job_succeeded(
                conn, job["id"], worker_id="c0-test-worker",
                verification_status="pending",
                verification_results={}, transition_document=False,
            )
        conn.close()


class TestStartupBackfill:
    def test_repair_flips_historical_all_skip_verified_rows(self, c0_api_server):
        from db import get_db, repair_all_skip_verified_documents

        legacy_results = json.dumps({
            "overall": "verified",
            "checks": [{"id": "LIC-GATE", "label": "Licence Applicability Gate",
                        "result": "skip",
                        "message": "Regulatory licence checks skipped — client declared no licence"}],
        })
        genuine_results = json.dumps({
            "overall": "verified",
            "checks": [{"id": "DOC-05", "label": "Entity Name Match",
                        "result": "pass", "message": "skip nothing here"}],
        })
        conn = get_db()
        app_id = _insert_c0_app_and_doc("bf-seed", "cert_inc", {"registered_entity_name": "C0"})
        for doc_id, results in (("bf1", legacy_results), ("bf2", genuine_results)):
            conn.execute(
                "INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, "
                "verification_status, verification_results, verified_at, is_current, version) "
                "VALUES (?,?,?,?,?,?,?,datetime('now'),1,1)",
                (doc_id, app_id, "licence", "L.pdf", "/x.pdf", "verified", results),
            )
        conn.commit()

        repaired = repair_all_skip_verified_documents(conn)
        conn.commit()
        assert repaired >= 1

        rows = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, verification_status, verified_at, verification_results "
            "FROM documents WHERE id IN ('bf1','bf2')"
        ).fetchall()}
        assert rows["bf1"]["verification_status"] == "skipped"
        assert rows["bf1"]["verified_at"] in (None, "")
        # stored JSON left verbatim for audit
        assert json.loads(rows["bf1"]["verification_results"])["overall"] == "verified"
        # genuine verified row untouched (message merely contains 'skip')
        assert rows["bf2"]["verification_status"] == "verified"
        assert rows["bf2"]["verified_at"] not in (None, "")

        audit = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action='documents.all_skip_verified_repair'"
        ).fetchone()["c"]
        assert audit >= 1

        # idempotent
        assert repair_all_skip_verified_documents(conn) == 0
        conn.close()


class TestMemoExcludesNotApplicable:
    def _memo_for(self, docs):
        from memo_handler import build_compliance_memo
        app = {
            "id": "app-c0-memo", "ref": "C0-MEMO", "company_name": "C0 Test Ltd",
            "brn": "C0-12345", "country": "Mauritius", "sector": "Consulting",
            "entity_type": "Private Limited Company", "status": "under_review",
            "risk_level": "LOW", "risk_score": 20, "risk_dimensions": "{}",
            "risk_escalations": "[]",
            "prescreening_data": json.dumps({"registered_entity_name": "C0 Test Ltd"}),
        }
        memo, *_ = build_compliance_memo(app, [], [], docs)
        return memo

    def test_not_applicable_doc_excluded_from_documentation_calculus(self, c0_api_server):
        docs = [
            {"id": "d1", "doc_type": "cert_inc", "doc_name": "COI.pdf",
             "verification_status": "verified",
             "verification_results": json.dumps({
                 "overall": "verified",
                 "checks": [{"result": "pass", "label": "Entity Name Match"}]})},
            {"id": "d2", "doc_type": "licence", "doc_name": "Licence.pdf",
             "verification_status": "skipped",
             "verification_results": json.dumps({
                 "overall": "skipped", "not_applicable": True,
                 "checks": [{"result": "skip", "label": "Licence Applicability Gate"}]})},
        ]
        memo = self._memo_for(docs)
        dumped = json.dumps(memo)
        # the NA licence is not an outstanding document
        assert "1 of 1 document(s) verified" in dumped or "(1/1)" in dumped, dumped[:400]
        assert "14 business days" not in dumped
        # raw upload count preserved in metadata
        assert '"document_count": 2' in dumped

    def test_operational_skip_still_counts_as_outstanding(self, c0_api_server):
        docs = [
            {"id": "d1", "doc_type": "cert_inc", "doc_name": "COI.pdf",
             "verification_status": "skipped",
             "verification_results": json.dumps({
                 "overall": "skipped", "checks": [],
                 "system_warning": "agent1_disabled"})},
        ]
        memo = self._memo_for(docs)
        dumped = json.dumps(memo)
        assert '"document_count": 1' in dumped
        assert "Documentation gap" in dumped or "outstanding" in dumped
