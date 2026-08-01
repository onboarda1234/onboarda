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


def test_memo_workspace_is_isolated_from_supervisor_scope_markers():
    html = _html()
    start = html.index("id=\"compliance-memo-workspace\"")
    end = html.index("</section>", start)
    workspace = html[start:end]

    assert "memo-pdf-preview" in workspace
    assert "Supervisor" not in workspace
    assert "memoAiSupervisorExcludedFromPilot" not in workspace
    assert "memoGovernanceStatus" not in workspace


def test_canonical_memo_uses_pdf_only_renderer():
    html = _html()
    renderer = _region(
        html,
        "function renderMemoSections()",
        "function renderMemoGovernanceSummary",
    )

    assert "return '';" in renderer
    assert "SUPERVISOR VERDICT" not in renderer
    assert "enhanced_review_edd" not in renderer


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
    # Removed: hardcoded evidence ticks and the literal "Verified" status tile.
    assert "Evidence at a glance" not in region
    assert "'Status', 'Verified'" not in region
    # Zero contribution must render as "0", never as an empty cell.
    assert "riskExecutiveNumber(contribution)" in region
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
