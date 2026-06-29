import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _backoffice_html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    start = src.index(f"function {name}")
    brace = src.index("{", start)
    depth = 0
    for index in range(brace, len(src)):
        char = src[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return src[start : index + 1]
    raise AssertionError(f"could not extract function {name}")


def _function_region(html: str, start_name: str, next_name: str) -> str:
    start = html.index(f"function {start_name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def _run_node(script: str) -> str:
    assert shutil.which("node"), "Node.js is required for Back Office rendering checks"
    result = subprocess.run(
        ["node", "-"],
        input=script,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def _js_prelude() -> str:
    return r"""
function firstMeaningfulDetailValue() {
  for (var i = 0; i < arguments.length; i++) {
    var value = arguments[i];
    if (value === null || value === undefined) continue;
    var normalized = String(value).trim();
    if (normalized && normalized !== '—') return value;
  }
  return '';
}
function normalizeDetailValue(value) {
  if (value === null || value === undefined || String(value).trim() === '') return '—';
  return String(value).trim();
}
function escapeHtml(value) {
  return String(value || '').replace(/[&<>"']/g, function(ch) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
  });
}
function assert(condition, message) {
  if (!condition) throw new Error(message);
}
"""


def test_expected_checks_missing_is_not_rendered_in_technical_audit_details():
    html = _backoffice_html()
    technical = _function_region(html, "buildVerificationResultsHtml", "renderDocumentAuditDetails")

    assert "Expected checks missing" not in technical
    assert "pushAuditFact('Expected checks missing'" not in technical
    assert "missingExpectedChecks.join" not in technical


def test_verified_document_with_stored_passed_checks_shows_completed_checks_and_method_dots():
    html = _backoffice_html()
    functions = "\n\n".join(
        _extract_function(html, name)
        for name in [
            "documentVerificationCheckResultBucket",
            "documentVerificationCheckMethodMeta",
            "renderDocumentVerificationCheckRow",
            "buildVerificationResultsHtml",
        ]
    )
    script = f"""
{_js_prelude()}
{functions}
var rendered = buildVerificationResultsHtml({{
  ai_source: 'live',
  verified_at: '2026-06-29T08:00:00Z',
  overall: 'verified',
  checks: [
    {{ label: 'Hash generation', result: 'pass', classification: 'rule', id: 'HASH-01' }},
    {{ label: 'Document type match', status: 'passed', method: 'Hybrid', check_id: 'MAT-01' }},
    {{ label: 'Name match', result: 'success', method: 'AI-Generated', confidence: 0.91, key: 'NAME-01' }},
    {{ label: 'Clarity/readability', result: 'warn', method: 'Deterministic', message: 'Manual review recommended.' }}
  ]
}}, {{}}, {{ uploadedBy: 'Officer', stateLabel: 'Verified' }});
assert(rendered.indexOf('Completed checks') >= 0, 'completed checks heading should render');
assert(rendered.indexOf('Hash generation') >= 0, 'stored passed check should render');
assert(rendered.indexOf('Document type match') >= 0, 'stored passed hybrid check should render');
assert(rendered.indexOf('Name match') >= 0, 'stored passed AI check should render');
assert(rendered.indexOf('Checks requiring attention') >= 0, 'attention section should remain');
assert(rendered.indexOf('Clarity/readability') >= 0, 'warning check should remain visible');
assert(rendered.indexOf('check-type-legend') >= 0, 'legend should remain visible');
assert(rendered.indexOf('verification-check-method-dot rule') >= 0, 'rule method dot should render');
assert(rendered.indexOf('verification-check-method-dot hybrid') >= 0, 'hybrid method dot should render');
assert(rendered.indexOf('verification-check-method-dot ai') >= 0, 'AI method dot should render');
assert(rendered.indexOf('Expected checks missing') < 0, 'expected-checks-missing box should not render');
assert(rendered.indexOf('Detailed passed-check evidence is not available') < 0, 'fallback should not render when pass checks exist');
"""
    _run_node(script)


def test_verified_document_without_stored_pass_detail_shows_conservative_fallback():
    html = _backoffice_html()
    functions = "\n\n".join(
        _extract_function(html, name)
        for name in [
            "documentVerificationCheckResultBucket",
            "documentVerificationCheckMethodMeta",
            "renderDocumentVerificationCheckRow",
            "buildVerificationResultsHtml",
        ]
    )
    script = f"""
{_js_prelude()}
{functions}
var rendered = buildVerificationResultsHtml({{
  ai_source: 'live',
  verified_at: '2026-06-29T08:00:00Z',
  overall: 'verified',
  checks: []
}}, {{}}, {{ uploadedBy: 'Officer', stateLabel: 'Verified' }});
assert(rendered.indexOf('Completed checks') >= 0, 'completed checks heading should render');
assert(rendered.indexOf('Verification completed. No failed or warning checks were stored. Detailed passed-check evidence is not available for this document.') >= 0, 'conservative fallback should render');
assert(rendered.indexOf('Expected checks missing') < 0, 'expected-checks-missing box should not render');
"""
    _run_node(script)


def test_manual_accepted_summary_is_terminal_copy_without_next_prefix():
    html = _backoffice_html()
    short_next_action = _function_region(html, "documentShortNextAction", "normalizeCoverageCheckLabel")
    functions = "\n\n".join(
        _extract_function(html, name)
        for name in [
            "collapseDocumentReviewFindingText",
            "normalizeDocumentReviewFindingText",
            "compactDocumentReviewFindingSummary",
            "documentReviewNonDuplicateFindingDetail",
            "renderDocumentFindingSummaryHtml",
            "renderDocumentCompactSummary",
        ]
    )
    script = f"""
var KYC_MISSING_REQUIRED_DOCUMENT_MESSAGE = 'Required document missing. Request from client or upload document before approval.';
var KYC_VERIFICATION_POLICY_MISSING_MESSAGE = 'Verification policy missing. Admin setup is required before automated verification can run. Manual review is required before relying on this document.';
{_js_prelude()}
function isStandaloneKycDocumentIssue(issueText) {{
  return issueText === KYC_MISSING_REQUIRED_DOCUMENT_MESSAGE ||
    issueText === KYC_VERIFICATION_POLICY_MISSING_MESSAGE;
}}
{functions}
var rendered = renderDocumentCompactSummary('None', 'None', 'Accepted with reason', {{ label: 'Manual accepted' }});
    assert(rendered.indexOf('Accepted with reason.') >= 0, 'manual accepted terminal copy should render');
    assert(rendered.indexOf('Next: Accepted with reason') < 0, 'manual accepted copy should not be next-action copy');
"""
    _run_node(script)
    assert "Wait for verification or re-verify" in short_next_action
    assert "Wait or re-verify" not in short_next_action


def test_reverify_visibility_uses_existing_handler_for_eligible_uploaded_documents_only():
    html = _backoffice_html()
    functions = "\n\n".join(
        _extract_function(html, name)
        for name in [
            "documentHasUploadedFile",
            "documentCanShowReverifyAction",
            "renderDocumentSecondaryActions",
        ]
    )
    script = f"""
{_js_prelude()}
function verifyBackofficeDocument() {{}}
function downloadBackofficeDocument() {{}}
function reviewBackofficeDocument() {{}}
function requestMoreInfo() {{}}
{functions}
var app = {{ ref: 'ARF-TEST' }};
var pending = renderDocumentSecondaryActions(app, {{ id: 'doc-pending', verification_state: 'pending', doc_name: 'pending.pdf' }}, {{ label: 'Pending verification' }});
var failed = renderDocumentSecondaryActions(app, {{ id: 'doc-failed', verification_state: 'failed', doc_name: 'failed.pdf' }}, {{ label: 'Failed' }});
var stale = renderDocumentSecondaryActions(app, {{ id: 'doc-stale', verification_state: 'stale', doc_name: 'stale.pdf' }}, {{ label: 'Stale' }});
var verified = renderDocumentSecondaryActions(app, {{ id: 'doc-verified', verification_state: 'verified', doc_name: 'verified.pdf' }}, {{ label: 'Verified' }});
var manual = renderDocumentSecondaryActions(app, {{ id: 'doc-manual', review_status: 'accepted', doc_name: 'manual.pdf' }}, {{ label: 'Manual accepted' }});
var noFile = renderDocumentSecondaryActions(app, {{ verification_state: 'failed' }}, {{ label: 'Failed' }});
var missing = renderDocumentSecondaryActions(app, null, {{ label: 'Missing' }});
assert(pending.indexOf('Re-verify') >= 0, 'pending uploaded document should expose Re-verify');
assert(failed.indexOf('Re-verify') >= 0, 'failed uploaded document should expose Re-verify');
assert(stale.indexOf('Re-verify') >= 0, 'stale uploaded document should expose Re-verify');
assert(verified.indexOf('Re-verify') < 0, 'verified document should not expose Re-verify');
assert(manual.indexOf('Re-verify') < 0, 'manual accepted document should not expose Re-verify');
assert(noFile.indexOf('Re-verify') < 0, 'document without uploaded-file evidence should not expose Re-verify');
assert(missing.indexOf('Re-verify') < 0, 'missing slot should not expose Re-verify');
assert(failed.indexOf('Accept with reason') >= 0, 'document review action should remain');
assert(failed.indexOf('Request replacement') >= 0, 'replacement action should remain');
assert(failed.indexOf('Reject') >= 0, 'reject action should remain');
"""
    _run_node(script)


def test_missing_policy_duplicate_cleanup_and_action_surfaces_remain_intact():
    html = _backoffice_html()
    technical = _function_region(html, "buildVerificationResultsHtml", "renderDocumentAuditDetails")
    audit_panel = _function_region(html, "renderDocumentAuditDetails", "documentReviewContextLine")
    audit_toggle = _function_region(html, "renderDocumentAuditToggleAction", "toggleDocumentTechnicalAudit")
    actions = _function_region(html, "renderDocumentDirectActions", "buildVerificationResultsHtml")
    primary = _function_region(html, "renderDocumentPrimaryAction", "renderDocumentDirectActions")
    secondary = _function_region(html, "renderDocumentSecondaryActions", "renderDocumentAuditToggleAction")

    assert "Verification policy missing. Admin setup is required before automated verification can run. Manual review is required before relying on this document." in html
    assert "Required document missing. Request from client or upload document before approval." in html
    assert "documentReviewNonDuplicateFindingDetail" in html
    assert "buildVerificationResultsHtml(doc.verification_results, coverage, auditContext)" in audit_panel
    assert '<div class="document-review-audit-panel" hidden>' in audit_panel
    assert "renderDocumentAuditToggleAction(doc)" in actions
    assert "Technical audit details" in audit_toggle
    assert "viewBackofficeDocument" in primary
    assert ">View</button>" in primary
    assert ">Upload</button>" in primary
    assert "downloadBackofficeDocument" in secondary
    assert ">Download</button>" in secondary
    assert "Accept with reason" in secondary
    assert "Request replacement" in secondary
    assert "Reject" in secondary
    assert "Overall Result" not in technical
    assert "Portal slot/source" not in technical
    assert "Policy ID/version" not in technical
