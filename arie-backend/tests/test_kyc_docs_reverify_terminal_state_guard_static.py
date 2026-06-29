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


def _reverify_rendering_functions() -> str:
    html = _backoffice_html()
    return "\n\n".join(
        _extract_function(html, name)
        for name in [
            "documentReadyDisplayState",
            "documentHasUploadedFile",
            "documentCanShowReverifyAction",
            "renderDocumentSecondaryActions",
        ]
    )


def test_terminal_manual_accepted_row_hides_reverify_even_with_historical_failed_state():
    script = f"""
{_js_prelude()}
function verifyBackofficeDocument() {{}}
function downloadBackofficeDocument() {{}}
function reviewBackofficeDocument() {{}}
function requestMoreInfo() {{}}
{_reverify_rendering_functions()}
var app = {{ ref: 'ARF-TEST' }};
var historicalFailure = {{
  id: 'doc-manual',
  doc_name: 'manual-accepted.pdf',
  review_status: 'accepted',
  document_reliance_state: 'manual_accepted',
  verification_state: 'failed',
  verification_status: 'failed'
}};
var rendered = renderDocumentSecondaryActions(app, historicalFailure, {{ label: 'Manual accepted' }});
assert(rendered.indexOf('Re-verify') < 0, 'manual accepted terminal row must not expose Re-verify');
assert(rendered.indexOf('Accept with reason') < 0, 'manual accepted row should not expose review actions');
assert(rendered.indexOf('Request replacement') < 0, 'manual accepted row should not expose replacement action');
assert(rendered.indexOf('Download') >= 0, 'non-mutating download action should remain');
"""
    _run_node(script)


def test_terminal_verified_row_hides_reverify_even_with_historical_flagged_state():
    script = f"""
{_js_prelude()}
function verifyBackofficeDocument() {{}}
function downloadBackofficeDocument() {{}}
function reviewBackofficeDocument() {{}}
function requestMoreInfo() {{}}
{_reverify_rendering_functions()}
var app = {{ ref: 'ARF-TEST' }};
var historicalFailure = {{
  id: 'doc-verified',
  doc_name: 'verified.pdf',
  document_reliance_status: 'ready',
  verification_state: 'flagged',
  verification_status: 'failed'
}};
var rendered = renderDocumentSecondaryActions(app, historicalFailure, {{ label: 'Verified' }});
assert(rendered.indexOf('Re-verify') < 0, 'verified terminal row must not expose Re-verify');
assert(rendered.indexOf('Accept with reason') < 0, 'verified row should not expose review actions');
assert(rendered.indexOf('Request replacement') < 0, 'verified row should not expose replacement action');
assert(rendered.indexOf('Download') >= 0, 'non-mutating download action should remain');
"""
    _run_node(script)


def test_reverify_remains_visible_for_eligible_uploaded_non_terminal_rows():
    script = f"""
{_js_prelude()}
function verifyBackofficeDocument() {{}}
function downloadBackofficeDocument() {{}}
function reviewBackofficeDocument() {{}}
function requestMoreInfo() {{}}
{_reverify_rendering_functions()}
var app = {{ ref: 'ARF-TEST' }};
var pending = renderDocumentSecondaryActions(app, {{ id: 'doc-pending', doc_name: 'pending.pdf', verification_state: 'pending' }}, {{ label: 'Pending verification' }});
var failed = renderDocumentSecondaryActions(app, {{ id: 'doc-failed', doc_name: 'failed.pdf', verification_state: 'failed' }}, {{ label: 'Failed' }});
var stale = renderDocumentSecondaryActions(app, {{ id: 'doc-stale', doc_name: 'stale.pdf', verification_state: 'stale' }}, {{ label: 'Stale' }});
var system = renderDocumentSecondaryActions(app, {{ id: 'doc-system', doc_name: 'system.pdf', verification_state: 'failed' }}, {{ label: 'System issue' }});
assert(pending.indexOf('Re-verify') >= 0, 'pending uploaded row should still expose Re-verify');
assert(failed.indexOf('Re-verify') >= 0, 'failed uploaded row should still expose Re-verify');
assert(stale.indexOf('Re-verify') >= 0, 'stale uploaded row should still expose Re-verify');
assert(system.indexOf('Re-verify') >= 0, 'system issue uploaded row should still expose Re-verify');
assert(failed.indexOf('Accept with reason') >= 0, 'failed row review action should remain');
assert(failed.indexOf('Request replacement') >= 0, 'failed row replacement action should remain');
assert(failed.indexOf('Reject') >= 0, 'failed row reject action should remain');
"""
    _run_node(script)


def test_missing_or_no_file_rows_keep_reverify_hidden():
    script = f"""
{_js_prelude()}
function verifyBackofficeDocument() {{}}
function downloadBackofficeDocument() {{}}
function reviewBackofficeDocument() {{}}
function requestMoreInfo() {{}}
{_reverify_rendering_functions()}
var app = {{ ref: 'ARF-TEST' }};
var noFile = renderDocumentSecondaryActions(app, {{ verification_state: 'failed' }}, {{ label: 'Failed' }});
var missing = renderDocumentSecondaryActions(app, null, {{ label: 'Missing' }});
assert(noFile.indexOf('Re-verify') < 0, 'row without uploaded file should not expose Re-verify');
assert(missing.indexOf('Re-verify') < 0, 'missing slot should not expose Re-verify');
assert(missing.indexOf('Request from client') >= 0, 'missing slot follow-up action should remain');
"""
    _run_node(script)


def test_pr_622_document_detail_rendering_strings_remain_intact():
    html = _backoffice_html()

    assert "Expected checks missing" not in html[
        html.index("function buildVerificationResultsHtml") : html.index("function renderDocumentAuditDetails")
    ]
    assert "Completed checks" in html
    assert "Verification completed. No failed or warning checks were stored. Detailed passed-check evidence is not available for this document." in html
    assert ".verification-check-method-dot.rule" in html
    assert ".verification-check-method-dot.hybrid" in html
    assert ".verification-check-method-dot.ai" in html
    assert "verification-check-method-dot ' + methodMeta.key" in html
    assert "Accepted with reason." in html
    assert "Verification policy missing. Admin setup is required before automated verification can run. Manual review is required before relying on this document." in html
    assert "Required document missing. Request from client or upload document before approval." in html
