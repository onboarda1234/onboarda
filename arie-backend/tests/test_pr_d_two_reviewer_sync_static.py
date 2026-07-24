"""PR-D #5 — per-hit dispositions must stay in sync across two reviewers.

The per-hit disposition hydration single-flights on 'done' and was only re-armed
on a save failure, so a second reviewer's clearances/undos were invisible to the
other reviewer until a hard page reload. Fix: re-arm the per-app hydration on
case re-open (openAppDetail) and on Screening-tab re-entry (switchDetailTab), and
make the hydrate authoritative — clear the app's cached per-hit state before
applying the server rows so removals/undos are reflected, not only additions.

No change to the server-authoritative finalize gate (PR-B). These guards pin the
wiring (revert-sensitive) and execute the prefix-boundary clear.
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

def test_authoritative_refresh_clears_before_applying():
    src = _html()
    assert "function screeningClearAppHitDispositionState(appRef)" in src
    hydrate = _extract_function(src, "ensureScreeningHitDispositionsHydrated")
    assert "screeningClearAppHitDispositionState(appRef)" in hydrate, (
        "hydrate no longer clears app state — removals/undos would not be reflected"
    )


def test_case_reopen_rearms_hydration():
    fn = _extract_function(_html(), "openAppDetail")
    assert "SCREENING_HIT_DISPOSITION_HYDRATED[detailApp.ref] = null" in fn


def test_screening_tab_reentry_rehydrates():
    fn = _extract_function(_html(), "switchDetailTab")
    assert "tab === 'screening'" in fn
    assert "SCREENING_HIT_DISPOSITION_HYDRATED[currentApp.ref] = null" in fn
    assert "ensureScreeningHitDispositionsHydrated(currentApp.ref)" in fn


# ---------------------------------------------------------------------------
# Prefix-boundary clear (executed)
# ---------------------------------------------------------------------------

def test_clear_is_scoped_to_the_app_and_respects_the_delimiter():
    src = _html()
    script = f"""
const assert = require('assert');
var SCREENING_HIT_DISPOSITION_STATE = {{
  'APP-1|entity|Acme': {{ h1: {{ status: 'cleared' }} }},
  'APP-1|director|Jane': {{ h2: {{ status: 'match' }} }},
  'APP-12|entity|Other': {{ h3: {{ status: 'cleared' }} }},
}};
{_extract_function(src, 'screeningClearAppHitDispositionState')}
screeningClearAppHitDispositionState('APP-1');
assert.strictEqual('APP-1|entity|Acme' in SCREENING_HIT_DISPOSITION_STATE, false, 'APP-1 entity not cleared');
assert.strictEqual('APP-1|director|Jane' in SCREENING_HIT_DISPOSITION_STATE, false, 'APP-1 director not cleared');
// The trailing '|' must stop APP-1 from clearing APP-12.
assert.strictEqual('APP-12|entity|Other' in SCREENING_HIT_DISPOSITION_STATE, true, 'APP-12 wrongly cleared (prefix boundary bug)');
// A no-op appRef is safe.
screeningClearAppHitDispositionState('');
screeningClearAppHitDispositionState(null);
console.log('ok');
"""
    assert "ok" in _run_node(script)
