"""PR-D — screening-review collapsible sections keep their open-state on action.

Audit finding (PR-D reliability set): the weak-tail and bucket-overflow
``<details>`` sections on the Screening Review page had no persisted open-state,
so any disposition / bulk-clear action (which re-renders the whole panel via
renderScreeningReviewPanel) silently collapsed a section the officer had
expanded, and the panel jumped back to the top.

Fix: persist per-section open-state in SCREENING_TRIAGE_SECTION_OPEN (mirroring
SCREENING_HIT_OPEN for individual cards) and restore scroll across in-place
re-renders. These guards pin the wiring (revert-sensitive) and execute the
toggle handler.
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
    result = subprocess.run(["node", "-"], input=script, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout


# ---------------------------------------------------------------------------
# Wiring (static, revert-sensitive)
# ---------------------------------------------------------------------------

def test_weak_tail_section_persists_open_state():
    fn = _extract_function(_html(), "screeningTriageWeakTailSection")
    assert "screeningTriageSectionKey(row, 'weak')" in fn
    assert 'data-section-open-key="' in fn
    assert 'ontoggle="screeningTriageSectionToggled(this)"' in fn
    assert "(weakOpen ? ' open' : '')" in fn


def test_bucket_overflow_section_persists_open_state():
    src = _html()
    # The overflow <details> is emitted inside the buckets renderer.
    marker = 'data-screening-triage-bucket-overflow="'
    seg = src[src.index(marker) - 400: src.index(marker) + 400]
    assert "screeningTriageSectionKey(row, 'overflow|' + meta.key)" in seg
    assert 'data-section-open-key="' in seg
    assert 'ontoggle="screeningTriageSectionToggled(this)"' in seg
    assert "(overflowOpen ? ' open' : '')" in seg


def test_panel_restores_scroll_across_rerender():
    fn = _extract_function(_html(), "renderScreeningReviewPanel")
    assert "screeningReviewScrollElement(host)" in fn
    assert "data-screening-rendered" in fn
    assert "scroller.scrollTop = prevScrollTop" in fn


# ---------------------------------------------------------------------------
# Toggle behaviour (executed)
# ---------------------------------------------------------------------------

def test_section_toggle_sets_and_clears_persisted_state():
    src = _html()
    script = f"""
const assert = require('assert');
var SCREENING_TRIAGE_SECTION_OPEN = {{}};
{_extract_function(src, 'screeningTriageSectionToggled')}
function fakeEl(key, open) {{
  return {{ open: open, getAttribute: function(k) {{ return k === 'data-section-open-key' ? key : null; }} }};
}}
// Expanding a section persists it; collapsing clears it.
screeningTriageSectionToggled(fakeEl('subjectA|weak', true));
assert.strictEqual(SCREENING_TRIAGE_SECTION_OPEN['subjectA|weak'], true, 'open not persisted');
screeningTriageSectionToggled(fakeEl('subjectA|weak', false));
assert.strictEqual('subjectA|weak' in SCREENING_TRIAGE_SECTION_OPEN, false, 'close did not clear');
// A missing key is a harmless no-op (never throws).
screeningTriageSectionToggled({{ open: true, getAttribute: function() {{ return null; }} }});
screeningTriageSectionToggled(null);
console.log('ok');
"""
    assert "ok" in _run_node(script)
