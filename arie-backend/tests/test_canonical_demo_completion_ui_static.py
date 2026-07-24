import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _region(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def _run_node(script: str) -> dict:
    assert shutil.which("node"), "Node.js is required for canonical UI contract tests"
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def test_periodic_review_queue_surfaces_fixture_badge_and_priority_without_layout_churn():
    html = _html()
    normalizer = _region(
        html,
        "function normalizePeriodicReview(raw)",
        "function mergePeriodicReviewRowsInto",
    )
    renderer = _region(
        html,
        "function renderPeriodicReviews()",
        "// ── Monitoring pilot source catalog",
    )
    completed_loader = _region(
        html,
        "async function onPeriodicReviewStatusFilterChange",
        "function periodicReviewNotificationTone",
    )
    detail = _region(
        html,
        "function renderPrcReviewDetailSection",
        "async function refreshOpenPeriodicReview",
    )

    assert "priority: projection.priority || raw.priority || ''" in normalizer
    assert "fixtureRecordBadgeHtml(review)" in renderer
    assert "Priority: " in renderer
    assert "<th>Priority</th>" not in html
    assert "status=completed" in completed_loader
    assert "PERIODIC_REVIEWS_COMPLETED_LOADED" in completed_loader
    assert "fixtureRecordBadgeHtml(detail || {})" in detail
    assert 'data-periodic-review-fixture-identity="true"' in detail
    notification = _region(
        html,
        "function periodicReviewNotificationTone",
        "function eddTriggerText",
    )
    workspace = _region(
        html,
        "function renderPeriodicReviewWorkspaceNotifications",
        "function renderPeriodicReviewWorkspaceReadiness",
    )
    assert "Suppressed" not in notification
    assert "Synthetic delivery disabled" in notification
    assert "Delivery is intentionally suppressed for this synthetic fixture" in workspace


def test_explicit_canonical_fixture_flag_gets_truthful_label_at_runtime():
    html = _html()
    fixture_truthy = _region(html, "function fixtureTruthy", "function canToggleTestSmokeRecords")
    fixture_info = _region(html, "function fixtureRecordInfo", "function fixtureRecordBadgeHtml")
    script = f"""
const TEST_SMOKE_REF_PATTERNS = [];
const TEST_SMOKE_TEXT_PATTERNS = [];
{fixture_truthy}
{fixture_info}
const canonical = fixtureRecordInfo({{is_fixture:true, application_ref:'RM-PILOT-024'}});
const otherFixture = fixtureRecordInfo({{is_fixture:true, application_ref:'ARF-2026-900099'}});
const unmarked = fixtureRecordInfo({{application_ref:'RM-PILOT-024'}});
process.stdout.write(JSON.stringify({{canonical, otherFixture, unmarked}}));
"""
    result = _run_node(script)
    assert result["canonical"] == {
        "isFixture": True,
        "label": "Pilot Canonical / Synthetic",
    }
    assert result["otherFixture"] == {"isFixture": True, "label": "Test / Smoke"}
    assert result["unmarked"]["isFixture"] is False


def test_ai_supervisor_scope_marker_is_display_only_and_preserves_unmarked_governance():
    html = _html()
    scope_helper = _region(
        html,
        "function memoAiSupervisorExcludedFromPilot",
        "function memoSupervisorBlock",
    )
    governance = _region(
        html,
        "function memoGovernanceStatus",
        "function renderMemoGovernanceSummary",
    )
    script = f"""
const window = {{_currentMemoData:null}};
function getMemoApprovalBlockers() {{ return []; }}
function memoCanonicalBlockers() {{ return []; }}
function currentMemoApprovalReason() {{ return ''; }}
{scope_helper}
{governance}
const excludedMeta = {{ai_supervisor_scope:'excluded_from_controlled_pilot', application_ref:'RM-PILOT-040'}};
const canonicalIdentity = {{synthetic:true, non_production:true, dataset:'Pilot Canonical Dataset', application_ref:'RM-PILOT-040'}};
const approvedMemo = {{...canonicalIdentity, sections:{{}}, metadata:excludedMeta, validation_status:'pass', review_status:'approved'}};
window._currentMemoData = approvedMemo;
const approved = memoGovernanceStatus({{}}, {{}});
const draftMemo = {{...canonicalIdentity, sections:{{}}, metadata:excludedMeta, validation_status:'pass', review_status:'draft'}};
window._currentMemoData = draftMemo;
const excluded = memoGovernanceStatus({{}}, {{}});
const markerOnlyMemo = {{sections:{{}}, metadata:excludedMeta, validation_status:'pass', review_status:'draft'}};
window._currentMemoData = markerOnlyMemo;
const markerOnly = memoGovernanceStatus({{}}, {{}});
const normalMemo = {{sections:{{}}, metadata:{{}}, validation_status:'pass', review_status:'draft'}};
window._currentMemoData = normalMemo;
const normal = memoGovernanceStatus({{}}, {{}});
process.stdout.write(JSON.stringify({{approved, excluded, markerOnly, normal}}));
"""
    result = _run_node(script)
    assert result["approved"]["status"] == "APPROVED"
    assert result["approved"]["ready"] is True
    assert result["excluded"]["status"] == "AI SUPERVISOR EXCLUDED"
    assert result["excluded"]["ready"] is False
    assert "Run supervisor" not in result["excluded"]["next"]
    assert result["markerOnly"]["status"] == "NEEDS SUPERVISOR CHECK"
    assert result["normal"]["status"] == "NEEDS SUPERVISOR CHECK"


def test_canonical_memo_hides_active_supervisor_verdict_but_retains_normal_renderer():
    html = _html()
    scope_helper = _region(
        html,
        "function memoAiSupervisorExcludedFromPilot",
        "function memoSupervisorBlock",
    )
    renderer = _region(
        html,
        "function renderMemoSections(data)",
        "async function generateComplianceMemo",
    )
    script = f"""
const window = {{_currentDetailApp:{{}}}};
const RISK_UNAVAILABLE_TEXT = 'Risk unavailable';
function escapeHtml(value) {{ return String(value == null ? '' : value); }}
function buildRiskDisplayState() {{ return {{hasRisk:false, level:'', score:0}}; }}
function memoRiskBadgeClass() {{ return 'medium'; }}
function memoDecisionBadgeClass() {{ return 'medium'; }}
function renderMemoDecisionSnapshot() {{ return ''; }}
function existingBadgeClass() {{ return 'draft'; }}
{scope_helper}
{renderer}
const supervisor = {{
  verdict:'CONSISTENT', supervisor_confidence:0.9,
  recommendation:'SHOULD_NOT_RENDER', contradictions:[], warnings:[]
}};
const excludedHtml = renderMemoSections({{
  synthetic:true, non_production:true, dataset:'Pilot Canonical Dataset', application_ref:'RM-PILOT-040',
  sections:{{}}, metadata:{{ai_supervisor_scope:'excluded_from_controlled_pilot', application_ref:'RM-PILOT-040'}}, supervisor
}});
const normalHtml = renderMemoSections({{sections:{{}}, metadata:{{}}, supervisor}});
process.stdout.write(JSON.stringify({{excludedHtml, normalHtml}}));
"""
    result = _run_node(script)
    assert "AI Supervisor excluded from controlled pilot" in result["excludedHtml"]
    assert "SUPERVISOR VERDICT" not in result["excludedHtml"]
    assert "SHOULD_NOT_RENDER" not in result["excludedHtml"]
    assert "SUPERVISOR VERDICT" in result["normalHtml"]
    assert "SHOULD_NOT_RENDER" in result["normalHtml"]


def test_memo_renderer_normalizes_legacy_wrapped_detail_projection():
    html = _html()
    helper = _region(html, "function normalizeMemoPayload", "var PRESCREEN_CORRECTION_ALLOWED_FIELDS")
    script = f"""
{helper}
const direct = {{sections:{{executive_summary:{{content:'ok'}}}}, metadata:{{}}}};
const wrapped = {{id:572, memo_data:direct, review_status:'approved'}};
const normalizedDirect = normalizeMemoPayload(direct);
const normalizedWrapped = normalizeMemoPayload(wrapped);
process.stdout.write(JSON.stringify({{
  directSame: normalizedDirect === direct,
  wrappedSections: Object.keys(normalizedWrapped.sections),
  wrappedStatus: normalizedWrapped.review_status,
  missing: normalizeMemoPayload(null)
}}));
"""
    result = _run_node(script)
    assert result == {
        "directSame": True,
        "wrappedSections": ["executive_summary"],
        "wrappedStatus": "approved",
        "missing": None,
    }


def test_risk_assessment_uses_compact_executive_dashboard_and_collapsed_technical_details():
    html = _html()
    region = _region(html, "function riskExecutiveStoredOutcomeCodes", "function setMemoDownloadState")
    assert "risk-executive-dashboard" in region
    assert "Primary Risk Drivers" in region
    assert "Persisted risk factors only" in region
    assert "Executive Recommendation" not in region
    assert "risk-score-adjustment" in region
    assert "Risk breakdown" in region
    assert "Rule outcomes" in region
    assert "Evidence at a glance" in region
    assert "<details style=\"margin-top:12px" in region
    assert "applications.risk_dimensions" not in region
    pdf = _region(
        html,
        "function buildAuthoritativeRiskPdfHtml",
        "function downloadRiskPDF",
    )
    assert "Executive Summary" in pdf
    assert "Executive Recommendation" in pdf
    assert "Primary Risk Drivers" in pdf
    assert "Rule Outcomes" in pdf
    assert "Detailed Dimension Computation" in pdf
    assert "Decision Eligibility" in pdf
    assert "Risk Rating" in pdf
    assert "<th>Evidence</th>" not in pdf
    assert "<th>Explanation</th>" not in pdf
    assert "Evidence Status" not in pdf
    assert "Runtime Subcriteria Configuration Reference" not in pdf
    assert "Evidence Source" not in pdf


def test_supervisor_scope_guard_is_confined_to_canonical_ui_projection():
    html = _html()
    application_blockers = _region(
        html,
        "function getApplicationApprovalBlockers",
        "function isTerminalGatePresentation",
    )
    case_blockers = _region(
        html,
        "function getCaseCommandBlockers",
        "function renderTerminalCaseCommandCentre",
    )
    explainability = _region(
        html,
        "function explainabilityNeedsAttention",
        "function updateExplainabilityPanelState",
    )

    assert "memoAiSupervisorExcludedFromPilot(memoData)" in application_blockers
    assert "if (!memoSupervisorExcluded)" in application_blockers
    assert "memoAiSupervisorExcludedFromPilot(memoData)" in case_blockers
    assert "if (!supervisorExcluded)" in case_blockers
    assert "!supervisorExcluded && supervisor" in explainability
    assert "ENABLE_AI_SUPERVISOR" not in application_blockers
    assert "ENABLE_AI_SUPERVISOR" not in case_blockers
