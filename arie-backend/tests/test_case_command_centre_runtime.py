"""
Runtime checks for the PR 2 Case Command Centre panel.

These tests execute the real front-end blocker derivation and deep-link helpers
with a minimal DOM shim so the workflow-guidance behavior is pinned without a
browser deployment.
"""
import json
import os
import shutil
import subprocess
import textwrap


BACKOFFICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "arie-backoffice.html",
)


def _read_backoffice():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_between(html, start_marker, end_marker):
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def _runtime_js(html, config):
    region = _extract_between(
        html,
        "function caseCommandOpenLifecycleItems(app) {",
        "function renderApprovalBlockersPanel(app) {",
    )
    return "\n".join(
        [
            textwrap.dedent(
                f"""
                const CONFIG = {json.dumps(config)};
                const elements = {{}};
                const switchTabCalls = [];

                function makeElement(id) {{
                  return {{
                    id,
                    innerHTML: '',
                    textContent: '',
                    hidden: false,
                    attributes: {{}},
                    style: {{}},
                    scrollCalls: 0,
                    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
                    getAttribute(name) {{ return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }},
                    scrollIntoView() {{ this.scrollCalls += 1; }}
                  }};
                }}

                const document = {{
                  getElementById(id) {{
                    if (!elements[id]) elements[id] = makeElement(id);
                    return elements[id];
                  }}
                }};

                function escapeHtml(value) {{
                  return String(value == null ? '' : value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
                }}

                function switchDetailTab(tab) {{ switchTabCalls.push(tab); }}
                function getApplicationScreeningSummary() {{ return CONFIG.screeningSummary || {{}}; }}
                function computeDocumentReadinessSummary() {{ return CONFIG.documentSummary || {{ missingCount:0, issueCount:0, pepIncompleteCount:0 }}; }}
                function getEnhancedReviewSummary() {{ return CONFIG.enhancedSummary || {{}}; }}
                function memoSupervisorBlock(memoData, memoMeta) {{
                  if (CONFIG.memoSupervisor) return CONFIG.memoSupervisor;
                  return (memoData && memoData.supervisor) || (memoMeta && memoMeta.supervisor) || {{}};
                }}
                function getApprovalReadiness() {{ return CONFIG.approvalReadiness || {{ ready:false, blockers:['Blocked'] }}; }}

                var detailLifecycleSummaryOverview = CONFIG.lifecycleSummaryOverview || null;

                document.getElementById('detail-case-command-centre');
                document.getElementById('detail-activity').textContent = CONFIG.auditFailureMessage || '';
                (CONFIG.targetIds || []).forEach(id => document.getElementById(id));
                """
            ),
            region,
            textwrap.dedent(
                """
                const app = CONFIG.app;
                const blockers = getCaseCommandBlockers(app);
                renderCaseCommandCentre(app);
                if (CONFIG.resolveTarget) {
                  activateCaseCommandTarget(CONFIG.resolveTarget.tab, CONFIG.resolveTarget.anchorId);
                }
                console.log(JSON.stringify({
                  blockers,
                  html: document.getElementById('detail-case-command-centre').innerHTML,
                  visibleBlockerCardCount: (document.getElementById('detail-case-command-centre').innerHTML.match(/case-command-blocker-card/g) || []).length,
                  switchTabCalls,
                  targetScrollCalls: CONFIG.resolveTarget ? document.getElementById(CONFIG.resolveTarget.anchorId).scrollCalls : 0
                }));
                """
            ),
        ]
    )


def _run_node(script):
    assert shutil.which("node"), "Node.js is required for back-office runtime tests"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=os.path.dirname(BACKOFFICE_PATH),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _base_app(**overrides):
    app = {
        "id": 101,
        "ref": "ARF-TEST-101",
        "company": "RegMind Test Ltd",
        "status": "Compliance Review",
        "statusRaw": "compliance_review",
        "risk": "MEDIUM",
        "finalRiskLevel": "MEDIUM",
        "assigned": "Case Officer",
        "monitoringAlerts": [],
        "_documents": [],
        "prescreeningData": {},
        "latestMemo": {
            "sections": {"summary": {"content": "ok"}},
            "validation_status": "pass",
            "review_status": "approved",
            "supervisor": {"verdict": "CONSISTENT"},
        },
        "latestMemoMeta": {},
        "memoIsStale": False,
        "memoRequiresRegeneration": False,
        "memoStaleReason": "",
        "supervisorRequiresRerun": False,
        "enhancedReviewSummary": {},
    }
    app.update(overrides)
    return app


class TestCaseCommandCentreRuntime:
    def test_compact_summary_strip_shows_core_case_context(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(
                        ref="ARF-COMPACT-202",
                        company="Compact Review Ltd",
                        status="Pricing Under Review",
                        risk="HIGH",
                        finalRiskLevel="HIGH",
                        assigned="Unassigned",
                    ),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Memo missing"]},
                },
            )
        )
        assert 'ARF-COMPACT-202 · Compact Review Ltd' in result["html"]
        assert 'case-command-centre-meta' in result["html"]
        assert 'Stage' in result["html"]
        assert 'Pricing Under Review' in result["html"]
        assert 'Risk' in result["html"]
        assert 'HIGH' in result["html"]
        assert 'Officer' in result["html"]
        assert 'Unassigned' in result["html"]
        assert '>Blocked<' in result["html"]

    def test_screening_blocker_is_shown(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(),
                    "screeningSummary": {
                        "screening_run_recorded": False,
                        "screening_truth_summary": None,
                        "screening_freshness": None,
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Screening has not been run."]},
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert "screening-missing" in blocker_ids
        assert "Screening review is still required." in result["html"]

    def test_memo_missing_blocker_is_shown(self):
        html = _read_backoffice()
        app = _base_app(latestMemo=None)
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": app,
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Compliance memo has not been generated."]},
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert "memo-missing" in blocker_ids
        assert "Compliance memo has not been generated." in result["html"]

    def test_edd_blocker_is_shown_when_enhanced_review_is_active(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(status="EDD Required", statusRaw="edd_required"),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "enhancedSummary": {
                        "approval_blocked": True,
                        "enhanced_review_active": True,
                        "next_action": "Enhanced due diligence is required before approval.",
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["EDD required"]},
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert "edd" in blocker_ids
        assert "Enhanced due diligence is still in progress." in result["html"]

    def test_document_blocker_is_shown_when_document_issues_exist(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "documentSummary": {"missingCount": 2, "issueCount": 1, "pepIncompleteCount": 0},
                    "approvalReadiness": {"ready": False, "blockers": ["Document issues"]},
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert "documents" in blocker_ids
        assert "Document review still needs attention." in result["html"]
        assert 'onclick=\'activateCaseCommandTarget("kyc-docs","detail-documents")\'' in result["html"]
        assert result["visibleBlockerCardCount"] == len(result["blockers"])
        assert "1 blocker" in result["html"]

    def test_resolve_cta_helper_opens_the_requested_tab(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": True, "blockers": []},
                    "targetIds": ["detail-screening-review"],
                    "resolveTarget": {"tab": "screening", "anchorId": "detail-screening-review"},
                },
            )
        )
        assert result["switchTabCalls"] == ["screening"]
        assert result["targetScrollCalls"] == 1

    def test_ready_state_only_shows_positive_message_when_safe(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": True, "blockers": []},
                },
            )
        )
        assert result["blockers"] == []
        assert "No guidance blockers detected." in result["html"]
        assert "Final approval remains subject to backend approval gates." in result["html"]

    def test_backend_approval_gates_remain_unchanged(self):
        html = _read_backoffice()
        assert "function getApprovalReadiness(app)" in html
        assert "getApplicationApprovalBlockers(app)" in html
        assert "approveBtn.disabled = true" in html
        assert "Backend gates still perform final validation." in html
