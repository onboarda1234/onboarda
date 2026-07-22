"""Monitoring-alert evidence — provider-URL injection hardening (C2 twin).

PR-A (#829) closed the ``javascript:``/``data:`` URL-injection class on the
screening-review surface with ``safeUrl``, but explicitly deferred the identical
sink on the periodic-monitoring alert surface: ``monitoringAlertProviderEvidenceRows``
and ``monitoringAlertEvidenceHtml`` each put a provider-supplied ``source_url`` /
``provider_case_url`` straight into an ``href`` with ``escapeHtml`` only and no
scheme check, so a stored ``javascript:`` URL executed on an officer's click.

These guards pin that both sinks now route the provider URL through ``safeUrl``
(revert-sensitive: reverting either to the raw provider value fails the test),
and that a dangerous scheme renders as "not available" instead of a live link.
"""
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _extract_function(source: str, name: str) -> str:
    start = source.index("function " + name)
    brace = source.index("{", start)
    depth = 0
    for idx in range(brace, len(source)):
        if source[idx] == "{":
            depth += 1
        elif source[idx] == "}":
            depth -= 1
            if depth == 0:
                return source[start:idx + 1]
    raise AssertionError("could not extract " + name)


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-"], input=script, cwd=ROOT, text=True, capture_output=True
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout


# ---------------------------------------------------------------------------
# Call-site wiring (static)
# ---------------------------------------------------------------------------

def test_monitoring_provider_evidence_rows_route_source_url_through_safe_url():
    src = _html()
    fn = _extract_function(src, "monitoringAlertProviderEvidenceRows")
    # The provider URL is wrapped by safeUrl before it can reach an href.
    assert "safeUrl(item.source_url || item.provider_case_url" in fn, (
        "monitoringAlertProviderEvidenceRows no longer guards source_url with safeUrl"
    )
    # Revert-sensitive: the old raw assignment must be gone.
    assert "var sourceUrl = item.source_url || item.provider_case_url || '';" not in fn


def test_monitoring_alert_evidence_html_routes_source_url_through_safe_url():
    src = _html()
    fn = _extract_function(src, "monitoringAlertEvidenceHtml")
    assert "safeUrl(monitoringAlertSourceUrl(ref))" in fn, (
        "monitoringAlertEvidenceHtml no longer guards source_url with safeUrl"
    )
    # Revert-sensitive: the old raw assignment must be gone.
    assert "var sourceUrl = monitoringAlertSourceUrl(ref);" not in fn


# ---------------------------------------------------------------------------
# Render behaviour (executed) — a dangerous scheme never reaches an href
# ---------------------------------------------------------------------------

def test_dangerous_scheme_never_rendered_into_monitoring_evidence_href():
    src = _html()
    script = f"""
const assert = require('assert');
{_extract_function(src, 'safeUrl')}
// Minimal stubs for the render helpers this function calls; safeUrl is the real
// (extracted) implementation — it is the thing under test.
function escapeHtml(s) {{
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}}
function monitoringTextValue(v, d) {{ var s = (v == null ? '' : String(v)); return s ? s : d; }}
function monitoringInfoGrid(rows) {{ return JSON.stringify(rows); }}
{_extract_function(src, 'monitoringAlertProviderEvidenceRows')}

// A stored javascript: URL must NOT survive into the output; it renders as
// "Not supplied" because safeUrl returns '' for a dangerous scheme.
const evil = monitoringAlertProviderEvidenceRows({{ provider_evidence: [
  {{ source_url: 'javascript:alert(document.cookie)' }},
  {{ provider_case_url: 'data:text/html,<script>alert(1)</script>' }}
]}});
assert(evil.indexOf('javascript:') === -1, 'javascript: URL reached the output');
assert(evil.indexOf('data:text/html') === -1, 'data: URL reached the output');
assert(evil.indexOf('Not supplied') !== -1, 'blocked URL did not fall back to Not supplied');

// A legitimate https URL still renders as a real link (no behaviour change).
const good = monitoringAlertProviderEvidenceRows({{ provider_evidence: [
  {{ source_url: 'https://complyadvantage.example/case/42' }}
]}});
assert(good.indexOf('https://complyadvantage.example/case/42') !== -1, 'valid URL was dropped');
assert(good.indexOf('Open source') !== -1, 'valid URL did not render a link');
assert(good.indexOf('Not supplied') === -1, 'valid URL wrongly fell back to Not supplied');
console.log('ok');
"""
    assert "ok" in _run_node(script)
