"""PR-A — screening-review injection hardening (audit findings C1 + C2).

C1: subject names / provider hit IDs were interpolated into single-quoted JS
strings inside on*="fn('...')" handlers using escapeHtml. escapeHtml is wrong
for that context — the value is HTML-entity-decoded before the JS parses, so
&#039; becomes a real quote that closes the string. An applicant-supplied name
like  x'),evil(),('  executed code in the officer session, and a benign name
like O'Brien broke the handler. Fixed with escapeJsAttr.

C2: provider-supplied source/case URLs went into href with escapeHtml only, no
scheme check — a javascript:/data: URL executed on click. Fixed with safeUrl.

These guards pin both the helper behaviour (executed via node) and the fact
that the screening-review call sites route through the safe helpers.
"""
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _slice_between(source: str, start: str, end: str) -> str:
    start_idx = source.index(start)
    end_idx = source.index(end, start_idx)
    return source[start_idx:end_idx]


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
# Helper behaviour (executed)
# ---------------------------------------------------------------------------

def test_escape_js_attr_neutralises_breakout_and_preserves_clean_values():
    src = _html()
    script = f"""
const assert = require('assert');
{_extract_function(src, 'escapeJsAttr')}
// The single quote is JS-escaped (backslash survives HTML entity decoding),
// so it can never close the surrounding single-quoted JS string.
const evil = "x'),evil(),('";
const out = escapeJsAttr(evil);
assert(!/(^|[^\\\\])'/.test(out), 'unescaped single quote survived: ' + out);
assert(out.indexOf("\\\\'") !== -1, 'expected backslash-escaped quote');
// HTML-structural chars are entity-encoded so they cannot close the attribute
// or open a tag.
const tag = escapeJsAttr('</script><img src=x onerror=alert(1)>');
assert(tag.indexOf('<') === -1 && tag.indexOf('>') === -1, 'raw angle brackets: ' + tag);
// Byte-identical for ordinary alphanumeric values — no frozen-output drift.
assert.strictEqual(escapeJsAttr('Wirecard AG'), 'Wirecard AG');
assert.strictEqual(escapeJsAttr('ARF-QAFIX-001'), 'ARF-QAFIX-001');
assert.strictEqual(escapeJsAttr('director'), 'director');
// Backslash and newlines are escaped, never left bare.
assert.strictEqual(escapeJsAttr('a\\\\b'), 'a\\\\\\\\b');
assert(escapeJsAttr('a\\nb').indexOf('\\\\n') !== -1);
console.log('ok');
"""
    assert "ok" in _run_node(script)


def test_escape_js_attr_blocks_execution_through_the_real_render_path():
    """The definitive test: build the handler the way the app does (string
    concat → innerHTML), click it, and assert the payload does NOT run while a
    benign apostrophe name still fires."""
    src = _html()
    script = f"""
const assert = require('assert');
{_extract_function(src, 'escapeJsAttr')}
// Minimal DOM shim via jsdom is unavailable here; emulate the browser's two
// stage decode: HTML-attribute entity decode, then JS string parse.
function htmlAttrDecode(s) {{
  return s.replace(/&amp;/g, '&').replace(/&quot;/g, '"')
          .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&#0?39;/g, "'");
}}
function evaluatesTo(rawName) {{
  const attr = "fnTest('" + escapeJsAttr(rawName) + "')";   // what we emit into onclick="..."
  const jsSource = htmlAttrDecode(attr);                    // what the JS engine actually sees
  let fired = false, xss = false;
  const fnTest = function(){{ fired = true; }};
  // eslint-disable-next-line no-new-func
  new Function('fnTest', 'markXss', jsSource.replace('fnTest(', 'fnTest(') )(fnTest, () => {{ xss = true; }});
  return {{ fired, xss }};
}}
// Payload that in the OLD code closed the string and injected a call.
const evil = "x'),(markXss()),('";
const r1 = evaluatesTo(evil);
assert.strictEqual(r1.xss, false, 'payload executed');
assert.strictEqual(r1.fired, true, 'handler did not run');
// Benign apostrophe name must still call the handler (old code threw).
const r2 = evaluatesTo("O'Brien Holdings");
assert.strictEqual(r2.fired, true, 'apostrophe name broke the handler');
console.log('ok');
"""
    assert "ok" in _run_node(script)


def test_safe_url_allows_web_targets_and_blocks_dangerous_schemes():
    src = _html()
    script = f"""
const assert = require('assert');
{_extract_function(src, 'safeUrl')}
assert.strictEqual(safeUrl('javascript:alert(1)'), '');
assert.strictEqual(safeUrl('  JavaScript:alert(1)'), '');
assert.strictEqual(safeUrl('java\\tscript:alert(1)'), '');
assert.strictEqual(safeUrl('data:text/html,<script>'), '');
assert.strictEqual(safeUrl('vbscript:msgbox(1)'), '');
assert.strictEqual(safeUrl(''), '');
assert.strictEqual(safeUrl(null), '');
assert.strictEqual(safeUrl('https://complyadvantage.example/case/1'), 'https://complyadvantage.example/case/1');
assert.strictEqual(safeUrl('http://x.test/a'), 'http://x.test/a');
assert.strictEqual(safeUrl('mailto:x@y.test'), 'mailto:x@y.test');
assert.strictEqual(safeUrl('/reports/x'), '/reports/x');
assert.strictEqual(safeUrl('#anchor'), '#anchor');
console.log('ok');
"""
    assert "ok" in _run_node(script)


# ---------------------------------------------------------------------------
# Call-site wiring (static) — the screening-review surface must use the helpers
# ---------------------------------------------------------------------------

def test_per_hit_and_inline_disposition_args_use_escape_js_attr():
    src = _html()
    # Per-hit action + group toggle args.
    per_hit = _extract_function(src, "screeningTriageHitActions")
    assert "escapeJsAttr(appRef)" in per_hit
    assert "escapeJsAttr(hid)" in per_hit
    assert "escapeHtml(hid)" not in per_hit
    # Inline disposition panel choice/field/submit handlers.
    inline = _slice_between(src, "function renderInlineScreeningDispositionPanel", "function screeningModeBadge")
    assert "escapeJsAttr(app.ref)" in inline
    assert "escapeJsAttr(subjectType)" in inline
    assert "escapeJsAttr(subjectName)" in inline
    # No subject/ref/hit identifier is still fed to a handler through escapeHtml.
    for bad in (
        "escapeHtml(app.ref) + '\\',\\'' + escapeHtml(subjectType)",
        "escapeHtml(subjectType)",
        "escapeHtml(subjectName)",
    ):
        assert bad not in inline, f"unsafe JS-arg escaping remains: {bad!r}"


def test_subject_focus_and_rescreen_args_use_escape_js_attr():
    src = _html()
    focus = _extract_function(src, "screeningTriageSubjectListItem")
    assert "escapeJsAttr(subject.subject_type)" in focus
    assert "escapeJsAttr(subject.subject_name)" in focus
    # The visible subject-name span stays HTML-escaped (text context).
    assert "escapeHtml(subject.subject_name)" in focus


def test_provider_urls_route_through_safe_url():
    src = _html()
    # Every screening-review provider link is guarded AND value-wrapped by safeUrl.
    for token in (
        "safeUrl(mediaUrl)",
        "safeUrl(sourceUrl)",
        "safeUrl(item.source_url)",
        "safeUrl(caseUrl)",
        "safeUrl(e.item.source_url)",
        "safeUrl(url)",  # providerIndicatorDetails — legacy-report fallback link
    ):
        assert token in src, f"missing safeUrl wrap: {token}"
    # None of these are still placed raw into an href through escapeHtml alone.
    for bad in (
        'href="\' + escapeHtml(mediaUrl)',
        'href="\' + escapeHtml(sourceUrl) + \'" target="_blank" rel="noopener">Open source ↗',
        'href="\' + escapeHtml(item.source_url)',
        'href="\' + escapeHtml(caseUrl)',
        'href="\' + escapeHtml(e.item.source_url)',
        'href="\' + escapeHtml(url) + \'" target="_blank" rel="noopener">source</a>',
    ):
        assert bad not in src, f"unsanitised provider href remains: {bad!r}"

    # providerIndicatorDetails is a live screening-review sink (reached via
    # providerResultHighlights on the stored legacy-report fallback path); its
    # provider-controlled url must be scheme-checked.
    indicator = _extract_function(src, "providerIndicatorDetails")
    assert "safeUrl(url)" in indicator
    assert "escapeHtml(safeUrl(url))" in indicator
