"""PR-D — application screening-evidence load failure must surface, not silently
show a clean/empty panel (audit items: "stuck loading" + "data-outage flag").

`ensureApplicationScreeningEvidenceRows` latched `SCREENING_REVIEW_APP_EVIDENCE_FETCHES[appRef]='error'`
on failure but (a) never re-rendered, (b) was never read by the render, and
(c) never retried (the guard early-returns on 'error') — so a failed provider-
evidence load left the officer on the empty legacy view that reads as
"no matches / clear", with no way to reload it.

Fix: re-render on catch, an explicit outage banner + Retry in the panel, and a
`retryApplicationScreeningEvidenceRows` helper that clears the latched state and
re-fetches. These guards are revert-sensitive.
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

def test_evidence_load_failure_rerenders_the_panel():
    fn = _extract_function(_html(), "ensureApplicationScreeningEvidenceRows")
    # The catch must re-render so the failure is visible.
    catch_idx = fn.index("catch")
    assert "renderScreeningReviewPanel" in fn[catch_idx:], (
        "evidence-load failure no longer re-renders — outage would stay hidden"
    )


def test_retry_helper_clears_latched_error_and_refetches():
    fn = _extract_function(_html(), "retryApplicationScreeningEvidenceRows")
    assert "SCREENING_REVIEW_APP_EVIDENCE_FETCHES[appRef] = null" in fn
    assert "ensureApplicationScreeningEvidenceRows(appRef)" in fn


def test_render_shows_outage_banner_gated_on_error_state():
    fn = _extract_function(_html(), "renderScreeningReviewPanel")
    assert "SCREENING_REVIEW_APP_EVIDENCE_FETCHES[app.ref] === 'error'" in fn
    assert 'data-screening-evidence-outage="true"' in fn
    # Explicit "don't read empty as clear" wording + a retry affordance.
    assert "do not treat an empty" in fn
    assert "retryApplicationScreeningEvidenceRows(\\'' + escapeJsAttr(app.ref)" in fn


# ---------------------------------------------------------------------------
# Retry behaviour (executed)
# ---------------------------------------------------------------------------

def test_retry_resets_state_calls_ensure_and_rerenders():
    src = _html()
    script = f"""
const assert = require('assert');
var SCREENING_REVIEW_APP_EVIDENCE_FETCHES = {{ 'APP1': 'error' }};
var ensureCalled = null, renderCalled = null;
function ensureApplicationScreeningEvidenceRows(ref) {{ ensureCalled = ref; }}
var currentApp = {{ ref: 'APP1' }};
var currentScreeningReviewFocus = null;
function renderScreeningReviewPanel(a, f) {{ renderCalled = a && a.ref; }}
{_extract_function(src, 'retryApplicationScreeningEvidenceRows')}
retryApplicationScreeningEvidenceRows('APP1');
assert.strictEqual(SCREENING_REVIEW_APP_EVIDENCE_FETCHES['APP1'], null, 'latched error not cleared');
assert.strictEqual(ensureCalled, 'APP1', 're-fetch not triggered');
assert.strictEqual(renderCalled, 'APP1', 'panel not re-rendered');
// null appRef is a harmless no-op.
ensureCalled = null;
retryApplicationScreeningEvidenceRows(null);
assert.strictEqual(ensureCalled, null, 'null appRef should be a no-op');
console.log('ok');
"""
    assert "ok" in _run_node(script)
