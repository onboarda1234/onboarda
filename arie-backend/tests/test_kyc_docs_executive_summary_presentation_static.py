import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
PORTAL_HTML = ROOT / "arie-portal.html"


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


def _function_region(src: str, start_name: str, next_name: str) -> str:
    start = src.index(f"function {start_name}")
    end = src.index(f"function {next_name}", start)
    return src[start:end]


def _run_node(script: str) -> None:
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


def _executive_summary_functions(html: str) -> str:
    return "\n\n".join(
        _extract_function(html, name)
        for name in [
            "collapseDocumentReviewFindingText",
            "normalizeDocumentReviewFindingText",
            "documentVerificationCheckResultBucket",
            "documentVerificationCheckDisplayResult",
            "documentExecutiveSummarySentence",
            "documentExecutiveSummarySource",
            "documentExecutiveSummaryFindings",
            "documentPersistedRecommendedAction",
            "renderDocumentExecutiveSummary",
        ]
    )


def _verification_check_functions(html: str) -> str:
    return "\n\n".join(
        _extract_function(html, name)
        for name in [
            "documentVerificationCheckResultBucket",
            "documentVerificationCheckDisplayResult",
            "documentVerificationCheckMethodMeta",
            "renderDocumentVerificationCheckRow",
            "documentVerificationCheckHiddenWhenPassing",
            "buildVerificationResultsHtml",
        ]
    )


def test_summary_is_one_line_deterministic_escaped_and_non_mutating_without_operational_expansion():
    html = _backoffice_html()
    functions = _executive_summary_functions(html)
    script = f"""
{_js_prelude()}
{functions}
var narrative = 'Document is blank. No memorandum content was detected. The submitted PDF contains <script>alert(1)</script> and no constitutional clauses, so completeness cannot be verified.';
var doc = {{ verification_results: {{
  recommended_action: 'Request replacement document <img src=x onerror=alert(1)>',
  checks: [
    {{ id: 'DOC-11', label: 'Visible Text Detection', result: 'fail', message: 'No visible text detected.' }},
    {{ id: 'DOC-12', label: 'Constitutional Clauses', result: 'fail', message: 'No constitutional clauses identified.' }},
    {{ id: 'DOC-13', label: 'Document Completeness', result: 'inconclusive', message: 'Completeness could not be verified.' }}
  ]
}} }};
var before = JSON.stringify(doc);
var rendered = renderDocumentExecutiveSummary(doc, narrative, {{ label: 'Failed' }});
assert(rendered.indexOf('Executive Summary') < 0, 'executive summary heading should not render');
assert(rendered.indexOf('document-executive-summary-outcome">Document is blank.</div>') >= 0, 'only the first concise conclusion should be prominent');
assert(rendered.indexOf('No visible text detected.') < 0, 'structured findings should remain in technical audit details');
assert(rendered.indexOf('No constitutional clauses identified.') < 0, 'summary should not render additional findings');
assert(rendered.indexOf('Completeness could not be verified.') < 0, 'summary should remain one line');
assert(rendered.indexOf('<ul') < 0 && rendered.indexOf('<li') < 0, 'summary should not render bullets');
assert(rendered.indexOf('<details') < 0 && rendered.indexOf('<summary') < 0, 'summary should not render an accordion');
assert(rendered.indexOf('Show more') < 0, 'summary should not render Show more');
assert(rendered.indexOf('document-executive-narrative') < 0, 'full narrative should not render on the operational card');
assert(rendered.indexOf('&lt;script&gt;alert(1)&lt;/script&gt;') < 0, 'hidden full narrative should not leak into rendered markup');
assert(rendered.indexOf('<script>alert(1)</script>') < 0, 'raw narrative HTML must not render');
assert(rendered.indexOf('Recommended action') < 0, 'summary should contain only the persisted conclusion');
assert(rendered.indexOf('&lt;img src=x onerror=alert(1)&gt;') < 0, 'hidden recommendation should not leak into rendered markup');
assert(rendered.indexOf('<img src=x onerror=alert(1)>') < 0, 'recommendation HTML must not render');
assert(JSON.stringify(doc) === before, 'presentation rendering must not mutate verification outcomes');
"""
    _run_node(script)


def test_summary_falls_back_to_existing_conclusion_and_omits_unstored_recommendation():
    html = _backoffice_html()
    functions = _executive_summary_functions(html)
    script = f"""
{_js_prelude()}
{functions}
var rendered = renderDocumentExecutiveSummary(
  {{ verification_results: {{ checks: [] }} }},
  'Existing persisted conclusion without structured findings.',
  {{ label: 'Review required' }}
);
assert(rendered.indexOf('Existing persisted conclusion without structured findings.') >= 0, 'existing conclusion should be the fallback');
assert(rendered.indexOf('document-executive-findings') < 0, 'findings list should be omitted when none are safely available');
assert(rendered.indexOf('Recommended action') < 0, 'recommendation block should be omitted when no explicit value is stored');
assert(rendered.indexOf('Show more') < 0, 'fallback summary should not render Show more');
assert(documentPersistedRecommendedAction({{ verification_results: {{}} }}) === '', 'missing recommendation must not be inferred from status');
"""
    _run_node(script)


def test_check_rows_show_dot_name_result_and_reason_without_accordion_or_metadata():
    html = _backoffice_html()
    functions = _verification_check_functions(html)
    script = f"""
{_js_prelude()}
{functions}
var results = {{
  ai_source: 'live',
  overall: 'flagged',
  checks: [
    {{ id: 'GATE-01', label: 'File Format', result: 'pass', classification: 'rule', source: 'rule', message: 'PDF accepted.' }},
    {{ id: 'DOC-10', label: 'Document Integrity', result: 'pass', classification: 'hybrid', source: 'rule', message: 'Document integrity checks passed.' }},
    {{ id: 'DOC-11', label: 'Entity Name Match', result: 'warn', classification: 'hybrid', source: 'ai', confidence: 0.81, ps_field: 'entity_name', ps_value: 'Meridian Ltd', extracted_value: 'Meridian Limited', message: 'Name requires officer review.' }},
    {{ id: 'DOC-12', label: 'Minimum Page Count', result: 'fail', classification: 'rule', source: 'rule', message: 'Minimum page count not met.' }},
    {{ id: 'DOC-13', label: 'Objects Clause', result: 'inconclusive', classification: 'ai', source: 'ai', message: 'Clause could not be assessed.' }},
    {{ id: 'DOC-14', label: 'Seal Check', result: 'skipped', classification: 'rule', source: 'rule', message: 'Not applicable.' }}
  ]
}};
var before = JSON.stringify(results);
var rendered = buildVerificationResultsHtml(results, {{}}, {{ uploadedBy: 'Officer', stateLabel: 'Failed' }});
assert(rendered.indexOf('File Format') < 0, 'passed File Format should be hidden from the officer UI');
['Document Integrity', 'Entity Name Match', 'Minimum Page Count', 'Objects Clause', 'Seal Check'].forEach(function(label) {{
  assert(rendered.indexOf(label) >= 0, 'every stored check should remain available: ' + label);
}});
['Pass', 'Warning', 'Fail', 'Inconclusive', 'Skipped'].forEach(function(result) {{
  assert(rendered.indexOf(result) >= 0, 'canonical result label should render: ' + result);
}});
assert((rendered.match(/<div class="verification-check-row /g) || []).length === 5, 'each visible check should render as a static row');
assert((rendered.match(/verification-check-method-dot/g) || []).length === 5, 'every visible check should show its methodology dot');
assert(rendered.indexOf('<details class="verification-check-row') < 0, 'check rows should not be accordions');
assert(rendered.indexOf('Name requires officer review.') >= 0, 'message should render immediately');
assert(rendered.indexOf('Document integrity checks passed.') >= 0, 'passed substantive check reason should render immediately');
assert(rendered.indexOf('verification-check-meta') < 0, 'per-check metadata container should not render');
['Method', 'Confidence', 'Check ID', 'Classification', 'Pre-screening field', 'Declared value', 'Extracted value'].forEach(function(label) {{
  assert(rendered.indexOf(label) < 0, 'technical metadata should be absent: ' + label);
}});
assert(rendered.indexOf('<strong>Source:</strong>') < 0, 'source metadata should be absent');
assert(rendered.indexOf('Checks unavailable / not run') < 0, 'unavailable checks should stay in the attention group');
assert(JSON.stringify(results) === before, 'check rendering must not mutate verification outcomes');
"""
    _run_node(script)


def test_check_rows_have_no_disclosure_affordance():
    html = _backoffice_html()
    check_row = _extract_function(html, "renderDocumentVerificationCheckRow")

    assert 'grid-template-columns:minmax(0,1fr) auto' in html
    assert ".verification-check-row > summary" not in html
    assert ".verification-check-row[open]" not in html
    assert '<div class="verification-check-row ' in check_row
    assert "<details" not in check_row
    assert "<summary>" not in check_row
    assert "onclick=" not in check_row


def test_actions_handlers_collapsed_audit_and_client_portal_remain_unchanged_in_scope():
    html = _backoffice_html()
    portal = PORTAL_HTML.read_text(encoding="utf-8")
    actions = _function_region(html, "renderDocumentSecondaryActions", "renderDocumentAuditToggleAction")
    primary = _function_region(html, "renderDocumentPrimaryAction", "renderDocumentDirectActions")
    audit = _function_region(html, "renderDocumentAuditDetails", "documentReviewContextLine")

    assert "viewBackofficeDocument" in primary
    assert "downloadBackofficeDocument" in actions
    assert "reviewBackofficeDocument" in actions
    assert "verifyBackofficeDocument" in actions
    assert r"\'accepted\'" in actions
    assert r"\'info_requested\'" in actions
    assert r"\'rejected\'" in actions
    assert '<div class="document-review-audit-panel" hidden>' in audit
    assert "Executive Summary" not in portal
    assert "document-executive-summary" not in portal
