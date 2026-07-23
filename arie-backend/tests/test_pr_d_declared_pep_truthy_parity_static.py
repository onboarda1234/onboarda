"""PR-D — declared-PEP truthiness must be identical across every surface.

Audit finding (PR-D, frozen Application Review surface — founder-approved): the
"is this a declared PEP?" decision was implemented four times with divergent
token sets, so the same person could read as a PEP on one surface and not on
another. The canonical example: only some paths recognised the 'declared_yes'
status string.

Fix: one canonical classifier `pepDeclaredTriState(value) -> 'Yes'|'No'|null`;
`normalizePepDisplay`, `pepYesNoFromValue`, and `personHasDeclaredOrVerifiedPep`
all route through it. These guards pin the delegation (revert-sensitive) and
execute the classifier + prove the three surfaces now agree token-for-token.
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
# Delegation wiring (static, revert-sensitive)
# ---------------------------------------------------------------------------

def test_all_pep_paths_delegate_to_the_canonical_classifier():
    src = _html()
    assert "function pepDeclaredTriState(value)" in src
    assert "pepDeclaredTriState(value)" in _extract_function(src, "pepYesNoFromValue")
    assert "pepDeclaredTriState(value)" in _extract_function(src, "normalizePepDisplay")
    assert ".map(pepDeclaredTriState)" in _extract_function(src, "personHasDeclaredOrVerifiedPep")


# ---------------------------------------------------------------------------
# Classifier + cross-surface parity (executed)
# ---------------------------------------------------------------------------

def test_classifier_matrix_and_surface_agreement():
    src = _html()
    script = f"""
const assert = require('assert');
{_extract_function(src, 'pepDeclaredTriState')}
{_extract_function(src, 'normalizePepDisplay')}
{_extract_function(src, 'pepYesNoFromValue')}
{_extract_function(src, 'personHasDeclaredOrVerifiedPep')}

// 1) Token matrix.
const YES = [true, 1, 'yes', 'y', 'true', '1', 'confirmed_pep', 'declared_yes', 'Client-declared PEP'];
const NO  = [false, 0, 'no', 'false', '0', 'not_pep', 'declared_no', 'No PEP'];
const UNK = [null, '', 'unknown', 'pending', 'not captured', 'Not verified yet'];
YES.forEach(v => assert.strictEqual(pepDeclaredTriState(v), 'Yes', 'expected Yes: ' + v));
NO.forEach(v  => assert.strictEqual(pepDeclaredTriState(v), 'No',  'expected No: ' + v));
UNK.forEach(v => assert.strictEqual(pepDeclaredTriState(v), null, 'expected null: ' + v));

// 2) The three surfaces must AGREE for every token (the core of the fix).
YES.forEach(function(v) {{
  assert.strictEqual(normalizePepDisplay(v), 'Yes', 'display disagrees (yes): ' + v);
  assert.strictEqual(pepYesNoFromValue(v), 'Yes', 'yesno disagrees (yes): ' + v);
  assert.strictEqual(personHasDeclaredOrVerifiedPep({{ client_declared_pep: v }}), true, 'truthiness disagrees (yes): ' + v);
}});
NO.forEach(function(v) {{
  assert.strictEqual(normalizePepDisplay(v), 'No', 'display disagrees (no): ' + v);
  assert.strictEqual(pepYesNoFromValue(v), 'No', 'yesno disagrees (no): ' + v);
}});

// 3) Regression anchor: 'declared_yes' was the token some paths missed.
assert.strictEqual(normalizePepDisplay('declared_yes'), 'Yes', 'declared_yes must display Yes');
assert.strictEqual(pepYesNoFromValue('declared_yes'), 'Yes');
assert.strictEqual(personHasDeclaredOrVerifiedPep({{ pep_status: 'declared_yes' }}), true);

// 4) false_positive keeps its distinct display but is not a declared PEP.
assert.strictEqual(normalizePepDisplay('false_positive'), 'False positive');
assert.strictEqual(personHasDeclaredOrVerifiedPep({{ client_declared_pep: 'false_positive' }}), false);

// 5) Conflicting-signal precedence (regression the review caught): a Yes-signal
//    on the label or is_pep MUST survive a string 'no' on a declared field —
//    the No-side is strict boolean === false only, so a string 'no' never
//    pre-empts a downstream Yes and hides a real PEP.
assert.strictEqual(personHasDeclaredOrVerifiedPep({{ client_declared_pep: 'no', pep_status_display: 'Officer-verified PEP' }}), true, 'string-no field hid a label PEP');
assert.strictEqual(personHasDeclaredOrVerifiedPep({{ client_declared_pep: 'no', is_pep: 'yes' }}), true, 'string-no field hid an is_pep PEP');
assert.strictEqual(personHasDeclaredOrVerifiedPep({{ officer_verified_pep: 'no', pep_status_display: 'client-declared pep' }}), true);
// A genuine boolean-false with no other Yes-signal stays not-a-PEP (unchanged).
assert.strictEqual(personHasDeclaredOrVerifiedPep({{ client_declared_pep: false }}), false);
console.log('ok');
"""
    assert "ok" in _run_node(script)
