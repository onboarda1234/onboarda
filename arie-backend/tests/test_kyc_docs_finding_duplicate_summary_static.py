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


def test_long_finding_is_split_without_repeating_visible_summary_under_show_more():
    html = _backoffice_html()
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
function normalizeDetailValue(value) {{ return String(value || '').trim() || '—'; }}
function escapeHtml(value) {{
  return String(value || '').replace(/[&<>"']/g, function(ch) {{
    return {{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }}[ch];
  }});
}}
function isStandaloneKycDocumentIssue(issueText) {{
  return issueText === KYC_MISSING_REQUIRED_DOCUMENT_MESSAGE ||
    issueText === KYC_VERIFICATION_POLICY_MISSING_MESSAGE;
}}
{functions}
function assert(condition, message) {{
  if (!condition) throw new Error(message);
}}
var finding = [
  'The uploaded document contains no residential or postal address whatsoever.',
  'The image shows a project phase and timeline table with columns for Phase, Timeline, Scope, and Outcome.',
  'There is no address present to compare against the associated person or entity.',
  'Address similarity cannot be computed and officer review is required.'
].join(' ');
var rendered = renderDocumentCompactSummary(finding, 'Approval blocked', 'Request replacement', {{ label: 'Failed' }});
assert(rendered.indexOf('Show more') >= 0, 'long finding with extra text should keep Show more');
assert((rendered.match(/The uploaded document contains no residential/g) || []).length === 1, 'visible finding prefix should not repeat under Show more');
assert(rendered.indexOf('document-review-issue-more-copy issue') >= 0, 'expanded non-duplicate copy should render');
assert(rendered.indexOf('Approval blocked') >= 0, 'blocker detail should remain available');
assert(rendered.indexOf('Next: Request replacement') >= 0, 'next action detail should remain available');
assert(documentReviewNonDuplicateFindingDetail('Same finding.', 'Same finding.') === '', 'identical detail should be suppressed');
assert(documentReviewNonDuplicateFindingDetail('Same finding', 'Same finding. Additional non-duplicative detail.') === 'Additional non-duplicative detail.', 'prefix duplicate should be removed from detail');
"""
    _run_node(script)


def test_show_more_is_hidden_when_expansion_would_only_repeat_short_finding():
    html = _backoffice_html()
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
function normalizeDetailValue(value) {{ return String(value || '').trim() || '—'; }}
function escapeHtml(value) {{ return String(value || '').replace(/[&<>"']/g, function(ch) {{ return {{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }}[ch]; }}); }}
function isStandaloneKycDocumentIssue(issueText) {{ return issueText === KYC_MISSING_REQUIRED_DOCUMENT_MESSAGE || issueText === KYC_VERIFICATION_POLICY_MISSING_MESSAGE; }}
{functions}
function assert(condition, message) {{ if (!condition) throw new Error(message); }}
var rendered = renderDocumentCompactSummary('Document image is unreadable.', 'None', 'No action required', {{ label: 'Failed' }});
assert(rendered.indexOf('Document image is unreadable.') >= 0, 'short finding should render visibly');
assert(rendered.indexOf('Show more') < 0, 'Show more should be hidden when there is no additional detail');
assert(rendered.indexOf('document-review-issue-more-copy') < 0, 'duplicate expanded body should not render');
"""
    _run_node(script)


def test_compact_summary_no_longer_renders_same_issue_line_as_visible_and_expanded_copy():
    html = _backoffice_html()
    summary = _function_region(html, "renderDocumentCompactSummary", "renderDocumentSecondaryActions")
    finding_helpers = _function_region(html, "collapseDocumentReviewFindingText", "renderDocumentSecondaryActions")

    assert "renderDocumentFindingSummaryHtml(issueLine, issueClass, 'Show more')" in summary
    assert "documentReviewNonDuplicateFindingDetail" in finding_helpers
    assert "compactDocumentReviewFindingSummary" in finding_helpers
    assert "escapeHtml(issueLine) + '</div></details>'" not in summary
    assert "'<details class=\"document-review-issue-more\"><summary>Show more</summary><div class=\"document-review-issue-more-copy ' + issueClass + '\">' + escapeHtml(issueLine)" not in summary


def test_technical_audit_details_and_document_actions_remain_available():
    html = _backoffice_html()
    actions = _function_region(html, "renderDocumentDirectActions", "renderDocumentAuditDetails")
    primary = _function_region(html, "renderDocumentPrimaryAction", "renderDocumentDirectActions")
    secondary = _function_region(html, "renderDocumentSecondaryActions", "renderDocumentAuditToggleAction")
    audit_toggle = _function_region(html, "renderDocumentAuditToggleAction", "toggleDocumentTechnicalAudit")
    audit_panel = _function_region(html, "renderDocumentAuditDetails", "documentReviewContextLine")

    assert "renderDocumentAuditToggleAction(doc)" in actions
    assert "Technical audit details" in audit_toggle
    assert 'aria-expanded="false"' in audit_toggle
    assert '<div class="document-review-audit-panel" hidden>' in audit_panel
    assert "buildVerificationResultsHtml(doc.verification_results, coverage, auditContext)" in audit_panel
    assert "viewBackofficeDocument" in primary
    assert ">View</button>" in primary
    assert "downloadBackofficeDocument" in secondary
    assert ">Download</button>" in secondary
    assert "Accept with reason" in secondary
    assert "Request replacement" in secondary
    assert "Reject" in secondary
    assert "Re-verify" in secondary
    assert ">Upload</button>" in primary


def test_missing_policy_warning_copy_and_page_level_actions_are_unchanged():
    html = _backoffice_html()
    detail_view = html[html.index('id="view-app-detail"'):html.index('<div id="detail-case-command-centre">')]

    assert (
        "Verification policy missing. Admin setup is required before automated verification can run. "
        "Manual review is required before relying on this document."
    ) in html
    assert "System setup issue: verification policy missing." not in html
    assert "Required document missing. Request from client or upload document before approval." in html
    assert "Approve" in detail_view
    assert "More Info" in detail_view
    assert "More ▾" in detail_view
