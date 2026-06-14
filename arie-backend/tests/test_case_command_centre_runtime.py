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
                const eddCalls = [];
                const toastCalls = [];

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
                function openEDDCaseFromApplication(caseId, applicationId, applicationRef) {{ eddCalls.push({{ type:'case', caseId, applicationId, applicationRef }}); }}
                function openEDDQueueForApplication(applicationId, applicationRef) {{ eddCalls.push({{ type:'queue', applicationId, applicationRef }}); }}
                function showToast(message, level) {{ toastCalls.push({{ message, level }}); }}
                function screeningTruthBlockedReasons(screeningTruth) {{
                  if (!screeningTruth) return [];
                  if (Array.isArray(screeningTruth.approval_blocked_reasons)) return screeningTruth.approval_blocked_reasons;
                  if (Array.isArray(screeningTruth.blocking_reasons)) return screeningTruth.blocking_reasons;
                  return [];
                }}
                function screeningTruthBlocksApproval(screeningTruth) {{
                  if (!screeningTruth) return false;
                  if (screeningTruth.approval_blocking === true) return true;
                  if (screeningTruth.screening_gate_ready === false) return true;
                  if (screeningTruth.approval_gate_ready === false) return true;
                  if (screeningTruth.approval_ready === false) return true;
                  return screeningTruthBlockedReasons(screeningTruth).length > 0;
                }}
                function getApplicationScreeningSummary() {{ return CONFIG.screeningSummary || {{}}; }}
                function computeDocumentReadinessSummary() {{ return CONFIG.documentSummary || {{ missingCount:0, issueCount:0, pepIncompleteCount:0 }}; }}
                function getEnhancedReviewSummary() {{ return CONFIG.enhancedSummary || {{}}; }}
                function memoSupervisorBlock(memoData, memoMeta) {{
                  if (CONFIG.memoSupervisor) return CONFIG.memoSupervisor;
                  return (memoData && memoData.supervisor) || (memoMeta && memoMeta.supervisor) || {{}};
                }}
                function getApprovalReadiness() {{ return CONFIG.approvalReadiness || {{ ready:false, blockers:['Blocked'] }}; }}
                function isTerminalGatePresentation(app) {{
                  var presentation = app && (app.approvalGatePresentation || app.approval_gate_presentation);
                  return !!(presentation && presentation.mode === 'terminal_decision_context');
                }}
                function terminalGatePresentation(app) {{
                  return (app && (app.approvalGatePresentation || app.approval_gate_presentation)) || null;
                }}
                function terminalGateDiagnostics(app) {{
                  return (app && (app.currentGateDiagnostics || app.current_gate_diagnostics)) || null;
                }}
                function terminalDecisionBasis(app) {{
                  return (app && (app.decisionBasis || app.decision_basis)) || null;
                }}
                function formatDetailDate(value) {{ return String(value || ''); }}

                var detailLifecycleSummaryOverview = CONFIG.lifecycleSummaryOverview || null;
                var SCREENING_QUEUE = CONFIG.screeningQueue || {{ metrics:null, rows:[], generated_at:null, load_error:null }};

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
                let actionRunResult = null;
                if (CONFIG.runActionKey) {
                  const actionId = Object.keys(CASE_COMMAND_RENDERED_ACTIONS).find(id => CASE_COMMAND_RENDERED_ACTIONS[id].action_key === CONFIG.runActionKey);
                  actionRunResult = actionId ? runCaseCommandAction(actionId) : false;
                }
                if (CONFIG.runDirectActionKey) {
                  actionRunResult = runCaseCommandAction(caseCommandActionTarget(CONFIG.runDirectActionKey, {
                    target_application_id: app && app.id,
                    filter_application_ref: app && app.ref
                  }));
                }
                if (CONFIG.resolveTarget) {
                  activateCaseCommandTarget(CONFIG.resolveTarget.tab, CONFIG.resolveTarget.anchorId);
                }
                console.log(JSON.stringify({
                  blockers,
                  html: document.getElementById('detail-case-command-centre').innerHTML,
                  actionTargets: CASE_COMMAND_RENDERED_ACTIONS,
                  actionRunResult,
                  visibleBlockerCardCount: (document.getElementById('detail-case-command-centre').innerHTML.match(/case-command-group-row/g) || []).length,
                  switchTabCalls,
                  eddCalls,
                  toastCalls,
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
        "screeningReviews": [],
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
        assert 'Compact Review Ltd' in result["html"]
        assert 'ARF-COMPACT-202 · Compact Review Ltd' not in result["html"]
        assert 'case-command-centre-meta' in result["html"]
        assert 'Decision stage' in result["html"]
        assert 'Activation status' in result["html"]
        assert 'Pricing Under Review' in result["html"]
        assert 'Risk' in result["html"]
        assert 'HIGH' in result["html"]
        assert 'Officer' in result["html"]
        assert 'Unassigned' in result["html"]
        assert 'Blocked —' in result["html"]
        assert 'case-command-centre-status' not in result["html"]

    def test_terminal_record_renders_decision_context_not_approval_blockers(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(
                        ref="ARF-TERMINAL-001",
                        company="Terminal Client Ltd",
                        status="Approved",
                        statusRaw="approved",
                        approvalGatePresentation={
                            "mode": "terminal_decision_context",
                            "is_terminal": True,
                            "terminal_status": "approved",
                            "legacy_evidence_incomplete": True,
                            "current_gate_blocker_count": 2,
                            "current_gate_diagnostics_label": "Current-state diagnostics only; not the historical approval basis.",
                        },
                        currentGateDiagnostics={
                            "applies_to": "current_state_only",
                            "label": "Current-state diagnostics only; not the historical approval basis.",
                            "blocker_count": 2,
                            "blockers": [
                                {
                                    "title": "Identity verification unresolved",
                                    "description": "Current IDV state is unresolved under today's gate.",
                                },
                                {
                                    "title": "Compliance memo is stale",
                                    "description": "Current memo state changed after the historical decision.",
                                },
                            ],
                        },
                        decisionBasis={
                            "available": False,
                            "decision_record_count": 0,
                            "evidence_warning": "No matching terminal decision record was found for this application status.",
                        },
                    ),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Current gate blocker"]},
                },
            )
        )
        assert "Terminal Client Ltd" in result["html"]
        assert "Legacy evidence incomplete" in result["html"]
        assert "Decision evidence incomplete" in result["html"]
        assert "Current-state diagnostics only" in result["html"]
        assert "Not historical basis" in result["html"]
        assert "Current gate blocker" not in result["html"]
        assert "Blocked —" not in result["html"]

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
                    "runActionKey": "screening.resolve",
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert "screening-missing" in blocker_ids
        assert "Screening review is still required." in result["html"]
        assert result["switchTabCalls"] == ["screening"]

    def test_screening_review_blocker_uses_application_screening_reviews(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(
                        screeningReviews=[
                            {
                                "subject_name": "John Harbor",
                                "subject_type": "ubo",
                                "review_required": True,
                                "review_disposition": None,
                            }
                        ]
                    ),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Screening review pending."]},
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert "screening-review" in blocker_ids
        assert "A screening result still needs officer review." in result["html"]
        assert "Resolve screening" in result["html"]

    def test_uncleared_terminal_match_still_blocks_case_command_centre(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {
                            "canonical_state": "completed_match",
                            "screening_terminal": True,
                            "screening_result": "match",
                            "defensible_clear": False,
                            "screening_gate_ready": False,
                            "approval_ready": False,
                            "approval_blocking": True,
                            "approval_blocked_reasons": ["company_watchlist:live_terminal_match"],
                        },
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Screening review pending."]},
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert "screening-review" in blocker_ids
        assert "A screening result still needs officer review." in result["html"]
        assert "company_watchlist:live_terminal_match" in result["html"]
        assert "ready for approval" not in result["html"].lower()
        assert "approval ready" not in result["html"].lower()

    def test_non_review_screening_gate_blocker_uses_generic_copy(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {
                            "canonical_state": "not_configured",
                            "screening_terminal": False,
                            "defensible_clear": False,
                            "screening_gate_ready": False,
                            "approval_ready": False,
                            "approval_blocking": True,
                            "approval_blocked_reasons": ["screening:provider_not_configured"],
                        },
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Screening not configured."]},
                },
            )
        )
        assert "A screening result is blocking approval." in result["html"]
        assert "Resolve the screening gate blocker before approval." in result["html"]
        assert "A screening result still needs officer review." not in result["html"]

    def test_screening_review_blocker_uses_authoritative_queue_rows(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(ref="ARF-SCREEN-QUEUE-1"),
                    "screeningQueue": {
                        "rows": [
                            {
                                "application_ref": "ARF-SCREEN-QUEUE-1",
                                "subject_name": "Queue Match Person",
                                "subject_type": "director",
                                "review_required": True,
                                "review_actionable": True,
                            }
                        ]
                    },
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Screening review pending."]},
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert "screening-review" in blocker_ids
        assert "Resolve screening" in result["html"]

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

    def test_memo_blockers_are_grouped_into_single_memo_package_row(self):
        html = _read_backoffice()
        app = _base_app(
            latestMemo={
                "sections": {"summary": {"content": "ok"}},
                "validation_status": "pending",
                "review_status": "draft",
                "supervisor": {},
            },
            latestMemoMeta={"supervisor_status": "PENDING"},
            memoIsStale=True,
            memoStaleReason="Screening changed after memo generation.",
        )
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
                    "approvalReadiness": {"ready": False, "blockers": ["Memo package blocked"]},
                    "runActionKey": "memo.open",
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert {"memo-stale", "memo-approval", "supervisor-failed"}.issubset(set(blocker_ids))
        assert result["visibleBlockerCardCount"] == 1
        assert "Memo Package" in result["html"]
        assert "Memo package has 3 unresolved controls" in result["html"]
        assert "validation_status" not in result["html"]
        assert "supervisor_status" not in result["html"]
        assert result["switchTabCalls"] == ["overview"]

    def test_backend_idv_blockers_are_grouped_and_route_to_idv_panel(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(
                        gateBlockers=[
                            {
                                "id": "idv-dir-1",
                                "category": "Identity Verification",
                                "title": "Identity verification unresolved",
                                "description": "Priya Declared PEP — identity verification is pending. Sumsub has not produced a final verification result yet.",
                                "ctaLabel": "Review IDV",
                                "tab": "kyc-docs",
                                "anchorId": "individual-identity-verification",
                                "blocker_group": "identity_verification",
                                "action_key": "idv.review",
                                "person_name": "Priya Declared PEP",
                            },
                            {
                                "id": "idv-ubo-1",
                                "category": "Identity Verification",
                                "title": "Identity verification failed and unresolved",
                                "description": "Jane UBO — identity verification is failed. Sumsub returned a failed identity verification result.",
                                "ctaLabel": "Review IDV",
                                "tab": "kyc-docs",
                                "anchorId": "individual-identity-verification",
                                "blocker_group": "identity_verification",
                                "action_key": "idv.review",
                                "person_name": "Jane UBO",
                            },
                        ]
                    ),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": True, "blockers": []},
                    "runActionKey": "idv.review",
                },
            )
        )
        assert result["visibleBlockerCardCount"] == 1
        assert "2 people need IDV attention" in result["html"]
        assert "Review IDV" in result["html"]
        assert "provider=" not in result["html"]
        assert "review_answer=" not in result["html"]
        assert "source=derived" not in result["html"]
        assert result["switchTabCalls"] == ["kyc-docs"]
        idv_targets = [
            target for target in result["actionTargets"].values()
            if target.get("action_key") == "idv.review"
        ]
        assert idv_targets
        assert idv_targets[0]["target_section"] == "section-b-identity-verification"
        assert idv_targets[0]["scroll_anchor"] == "individual-identity-verification"

    def test_backend_group_priority_keeps_screening_before_idv(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(
                        gateBlockers=[
                            {
                                "id": "screening-stale",
                                "category": "Screening",
                                "title": "Screening is stale",
                                "description": "Re-run screening before approval.",
                                "ctaLabel": "Resolve screening",
                                "tab": "screening",
                                "anchorId": "detail-screening-review",
                                "blocker_group": "screening",
                                "action_key": "screening.resolve",
                            },
                            {
                                "id": "idv-priya",
                                "category": "Identity Verification",
                                "title": "Identity verification unresolved",
                                "description": "Priya — identity verification is pending. Sumsub has not produced a final verification result yet.",
                                "ctaLabel": "Review IDV",
                                "tab": "kyc-docs",
                                "anchorId": "individual-identity-verification",
                                "blocker_group": "identity_verification",
                                "action_key": "idv.review",
                                "person_name": "Priya",
                            },
                        ]
                    ),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": True, "blockers": []},
                },
            )
        )
        assert "Next: Re-run or resolve screening." in result["html"]
        assert result["html"].index("Screening needs attention") < result["html"].index("1 person needs IDV attention")

    def test_enhanced_review_blocker_is_primary_and_deep_links_to_kyc(self):
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
                        "next_action": "Resolve outstanding enhanced review requirements.",
                        "next_action_code": "resolve_blockers",
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Enhanced review required"]},
                    "runActionKey": "evidence.review",
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert "enhanced-review" in blocker_ids
        assert result["blockers"][0]["id"] == "enhanced-review"
        assert result["blockers"][0]["category"] == "KYC / Enhanced Evidence"
        assert result["blockers"][0]["title"] == "Enhanced Evidence Requirements are still outstanding."
        assert "Resolve required onboarding evidence before approval." in result["html"]
        assert "Enhanced due diligence is still in progress." not in result["html"]
        assert 'data-action-key="evidence.review"' in result["html"]
        assert "Next: Review missing documents and enhanced evidence." in result["html"]
        assert result["switchTabCalls"] == ["kyc-docs"]

    def test_formal_investigation_blocker_targets_edd_owner_workflow(self):
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
                    "enhancedSummary": {},
                    "approvalReadiness": {"ready": False, "blockers": ["Formal investigation is open"]},
                    "runActionKey": "edd.open",
                },
            )
        )
        investigation = [item for item in result["blockers"] if item["id"] == "edd"][0]
        assert investigation["category"] == "EDD / Investigation"
        assert investigation["title"] == "EDD / Investigation case is open."
        assert investigation["ctaLabel"] == "Open EDD"
        assert investigation["tab"] == "alerts"
        assert investigation["action"] == "openEDDQueueForApplication(101,\"ARF-TEST-101\")"
        assert 'data-action-key="edd.open"' in result["html"]
        assert result["eddCalls"] == [{"type": "queue", "applicationId": 101, "applicationRef": "ARF-TEST-101"}]

    def test_ccc1_mixed_owner_workflows_are_split_and_routed(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(
                        id="13cabbdf214542ea",
                        ref="ARF-2026-900289",
                        status="EDD Required",
                        statusRaw="edd_required",
                        monitoringAlerts=[
                            {"id": 185, "status": "in_review", "summary": "Active monitoring alert"},
                            {"id": 184, "status": "routed_to_review", "linked_periodic_review_id": 48},
                            {"id": 183, "status": "routed_to_review", "recommended_destination_module": "periodic_review"},
                            {"id": 182, "status": "routed_to_edd", "linked_edd_case_id": 254},
                        ],
                    ),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "enhancedSummary": {
                        "approval_blocked": True,
                        "enhanced_review_active": True,
                        "next_action": "Resolve outstanding enhanced review requirements.",
                    },
                    "lifecycleSummaryOverview": {
                        "applicationId": "13cabbdf214542ea",
                        "summary": {
                            "active": {
                                "items": [
                                    {"type": "review", "id": 48, "state": "pending", "next_action": "Start review"},
                                    {"type": "edd", "id": 254, "stage": "triggered", "next_action": "Begin information gathering"},
                                ]
                            }
                        },
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Enhanced review required"]},
                },
            )
        )
        by_id = {item["id"]: item for item in result["blockers"]}
        assert {"monitoring-alert", "periodic-review", "edd"}.issubset(by_id)
        assert by_id["monitoring-alert"]["title"] == "One monitoring alert is still open."
        assert "3 monitoring alerts are still open" not in result["html"]
        assert by_id["monitoring-alert"]["category"] == "Monitoring Alerts"
        assert by_id["monitoring-alert"]["tab"] == "alerts"
        assert by_id["monitoring-alert"]["anchorId"] == "detail-tab-alerts"
        assert by_id["periodic-review"]["category"] == "Periodic Review"
        assert by_id["periodic-review"]["tab"] == "lifecycle"
        assert by_id["edd"]["category"] == "EDD / Investigation"
        assert by_id["edd"]["action"] == 'openEDDCaseFromApplication(254,"13cabbdf214542ea","ARF-2026-900289")'
        assert by_id["edd"]["id"] in [item["id"] for item in result["blockers"]]

    def test_ccc1_monitoring_only_case_shows_alerts_card_only_for_owner_alerts(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(
                        monitoringAlerts=[
                            {"id": "mon-1", "status": "open"},
                            {"id": "pr-1", "status": "routed_to_review", "linked_periodic_review_id": 12},
                        ]
                    ),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": True, "blockers": []},
                },
            )
        )
        ids = [item["id"] for item in result["blockers"]]
        assert "monitoring-alert" in ids
        assert "periodic-review" not in ids
        assert "edd" not in ids
        assert [item for item in result["blockers"] if item["id"] == "monitoring-alert"][0]["tab"] == "alerts"

    def test_ccc1_periodic_review_only_case_does_not_show_monitoring(self):
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
                    "lifecycleSummaryOverview": {
                        "applicationId": 101,
                        "summary": {"active": {"items": [{"type": "review", "id": 77, "state": "awaiting_client"}]}},
                    },
                    "approvalReadiness": {"ready": True, "blockers": []},
                },
            )
        )
        ids = [item["id"] for item in result["blockers"]]
        assert "periodic-review" in ids
        assert "monitoring-alert" not in ids
        assert "edd" not in ids

    def test_ccc1_edd_only_case_is_not_hidden_by_enhanced_review(self):
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
                    },
                    "lifecycleSummaryOverview": {
                        "applicationId": 101,
                        "summary": {"active": {"items": [{"type": "edd", "id": 254, "stage": "triggered"}]}},
                    },
                    "approvalReadiness": {"ready": False, "blockers": ["Enhanced review required", "EDD required"]},
                },
            )
        )
        ids = [item["id"] for item in result["blockers"]]
        assert "enhanced-review" in ids
        assert "edd" in ids
        edd = [item for item in result["blockers"] if item["id"] == "edd"][0]
        assert edd["action"] == 'openEDDCaseFromApplication(254,101,"ARF-TEST-101")'

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
                    "runActionKey": "documents.review",
                },
            )
        )
        blocker_ids = [item["id"] for item in result["blockers"]]
        assert "documents" in blocker_ids
        assert "Document evidence is not reliance-ready." in result["html"]
        assert 'data-action-key="documents.review"' in result["html"]
        assert result["visibleBlockerCardCount"] == len(result["blockers"])
        assert "1 mandatory blocker" not in result["html"]
        assert "Blocked — 1 unresolved controls" in result["html"]
        assert result["switchTabCalls"] == ["kyc-docs"]

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
        assert "No grouped blockers detected." in result["html"]
        assert "Final approval remains subject to backend approval gates." in result["html"]

    def test_backend_gate_blockers_render_as_authoritative_primary_list(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(
                        gateBlockers=[
                            {
                                "id": "idv-dir-1",
                                "category": "Identity Verification",
                                "title": "Identity verification unresolved",
                                "description": "Jane Director: Pending. Approval is blocked until IDV is resolved.",
                                "ctaLabel": "Resolve IDV",
                                "tab": "kyc-docs",
                                "anchorId": "individual-identity-verification",
                            }
                        ]
                    ),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": True, "blockers": []},
                },
            )
        )

        assert "Backend approval gate blockers are authoritative" not in result["html"]
        assert "Identity verification unresolved" in result["html"]
        assert "Resolve IDV" in result["html"]
        assert "1 mandatory blocker" not in result["html"]
        assert "Activation status" in result["html"]
        assert "Blocked — 1 unresolved controls" in result["html"]
        assert result["visibleBlockerCardCount"] == 1
        assert 'data-action-key="idv.review"' in result["html"]

    def test_backend_gate_payload_clear_renders_backend_clear_message(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(gateBlockers=[]),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "approvalReadiness": {"ready": True, "blockers": []},
                },
            )
        )

        assert "No backend approval blockers returned." in result["html"]
        assert "Backend approval gate payload is clear." in result["html"]
        assert "0 mandatory blockers" not in result["html"]

    def test_backend_approval_gates_remain_unchanged(self):
        html = _read_backoffice()
        assert "function getApprovalReadiness(app)" in html
        assert "getApplicationApprovalBlockers(app)" in html
        assert "confirmBtn.disabled = true" in html
        assert "Open approval decision modal to review blockers." in html
        assert "Backend gates still perform final validation." in html

    def test_case_command_action_map_and_top_actions_are_wired(self):
        html = _read_backoffice()
        required_actions = [
            "screening.resolve",
            "idv.review",
            "documents.review",
            "evidence.review",
            "edd.open",
            "memo.open",
            "memo.validate",
            "supervisor.run",
            "periodic_review.open",
            "override.open",
            "escalate.open",
            "reassign.open",
        ]
        for action in required_actions:
            assert action in html
        assert 'onclick="approveApplication()"' in html
        assert 'onclick="rejectApplication()"' in html
        assert 'onclick="requestMoreInfo()"' in html
        topbar = html[html.index("<!-- Top bar: back button + action buttons (horizontal) -->"):html.index('<div id="detail-case-command-centre">')]
        assert 'onclick="openOfficerCorrectionModal()"' not in topbar
        assert "Add correction" in html
        assert 'onclick="openOverrideModal()"' in html
        assert 'onclick="escalateCase()"' in html
        assert 'onclick="reassignCase()"' in html
        assert 'onclick="openExportPackModal()"' in html
        assert "function runCaseCommandAction(actionId)" in html

    def test_default_edd_action_map_routes_to_current_application_edd_queue(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "app": _base_app(status="Blocked", statusRaw="blocked"),
                    "screeningSummary": {
                        "screening_run_recorded": True,
                        "screening_truth_summary": {"approval_ready": True},
                        "screening_freshness": {"status": "valid"},
                    },
                    "enhancedSummary": {},
                    "approvalReadiness": {"ready": False, "blockers": ["Formal investigation is open"]},
                    "runDirectActionKey": "edd.open",
                },
            )
        )
        assert result["actionRunResult"] is True
        assert result["eddCalls"] == [{"type": "queue", "applicationId": 101, "applicationRef": "ARF-TEST-101"}]

    def test_ccc1_cold_cache_edd_open_loads_queue_before_error(self):
        html = _read_backoffice()
        edd_region = _extract_between(
            html,
            "async function openEDDDetail(caseId) {",
            "async function saveEDDFindings(caseId) {",
        )
        assert "await ensureEDDCasesLoaded({ force: true })" in edd_region
        assert "findEDDCaseById(caseId)" in edd_region
        assert "Investigation case not found" not in edd_region
        assert "could not be loaded" in edd_region

    def test_pr2d_overview_and_kyc_ui_cleanup_markup_is_present(self):
        html = _read_backoffice()
        overview_start = html.index('id="detail-tab-overview"')
        overview_end = html.index('id="detail-tab-kyc-docs"', overview_start)
        overview_region = html[overview_start:overview_end]
        supervisor_start = html.index('id="detail-tab-supervisor"')
        supervisor_end = html.index('id="detail-tab-lifecycle"', supervisor_start)
        supervisor_region = html[supervisor_start:supervisor_end]

        assert "AI Risk Assessment" not in html
        assert "AI Agent Pipeline Results" not in overview_region
        assert "Business Profile" not in html
        assert 'id="detail-application-data" style="display:none;"' in html
        assert 'details id="detail-ai-governance-evidence-details"' in supervisor_region
        assert 'AI Governance &amp; Evidence Trail' in supervisor_region
        assert 'AI Explainability Layer' not in overview_region
        assert 'AI Explainability Layer' in supervisor_region
        assert 'Risk drivers, AI reasoning, validation context, and supervisor signals.' in html
        assert 'details id="detail-prescreen-summary-details" open' in html
        assert 'Pre-Screening Summary' in html
        assert 'overview-top-layout' not in html
        assert 'prescreen-summary-card' in html
        assert 'details id="detail-kyc-documents-details"' in html
        assert 'id="detail-kyc-documents-summary-copy"' in html
        assert 'details id="detail-enhanced-requirements-details"' in html
        assert 'id="detail-enhanced-requirements-summary-copy"' in html
        assert 'Enhanced Review Requirements are onboarding evidence requirements. Formal investigation cases are managed in Lifecycle.' in html
        assert 'Corporate documents, identity evidence, enhanced evidence, portal disclosures, and verification results in one onboarding flow.' in html

    def test_pr5a_investigation_queue_copy_is_distinct_from_onboarding_evidence(self):
        html = _read_backoffice()
        edd_view = _extract_between(
            html,
            '<div class="view" id="view-edd">',
            '<div class="view" id="view-periodic-review-signals">',
        )
        assert "Formal investigation cases are managed in Lifecycle" in edd_view
        assert "Use KYC Documents for onboarding Enhanced Review Requirements." in edd_view
        assert "Investigation Case Detail" in edd_view
        assert "Investigation Case Workspace" in html
        assert "Formal narrative investigation. Routine onboarding Enhanced Review Requirements remain in KYC Documents." in html
        assert "Linked source object" in html
        assert "Maintain relationship" in html
        assert "Officer Notes / Rationale" in html
        assert "source_surface: 'investigation_case_workspace'" in html
        assert "Investigation Cases Active" in edd_view
        assert "Enhanced Review cases are now managed from Applications" not in edd_view
        assert "EDD Cases Active" not in edd_view

    def test_pr2d_enhanced_requirements_table_is_slimmed_without_upload_controls(self):
        html = _read_backoffice()
        assert "<th>Source / Reason</th>" not in html
        assert "<th>Timeline</th>" not in html
        assert "<th>Workflow / Evidence</th>" not in html
        assert "Enhanced Review Advanced Tracker" not in html
        assert 'id="detail-enhanced-evidence-documents-group"' in html
        assert "Show advanced requirement details" in html
        assert "Triggered by" in html
        assert "No upload controls were added to Enhanced Review Requirements." not in html
        enhanced_section = _extract_between(
            html,
            '<div class="detail-collapsible-card" id="detail-enhanced-requirements-section"',
            '<div class="detail-collapsible-card" id="detail-document-history-panel"',
        )
        assert "Upload Document" not in enhanced_section
        assert "Document Type" not in enhanced_section

    def test_pr2d_existing_kyc_upload_ui_is_still_present(self):
        html = _read_backoffice()
        kyc_section = _extract_between(
            html,
            '<div class="detail-collapsible-card" id="detail-kyc-documents-panel"',
            '<div class="detail-collapsible-card" id="detail-enhanced-requirements-section"',
        )
        assert "Upload Document" in kyc_section
        assert "Document Type" in kyc_section
        assert "Upload to Record" in kyc_section
