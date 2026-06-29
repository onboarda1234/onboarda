import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"
PORTAL_HTML = REPO_ROOT / "arie-portal.html"


def _html(path: Path = BACKOFFICE_HTML) -> str:
    return path.read_text(encoding="utf-8")


def _extract_js_function(html: str, function_name: str) -> str:
    marker = f"function {function_name}"
    start = html.index(marker)
    brace = html.index("{", start)
    depth = 0
    for pos in range(brace, len(html)):
        char = html[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[start:pos + 1]
    raise AssertionError(f"Could not extract function {function_name}")


HELPER_BEHAVIOUR_SCRIPT = r"""
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[1], 'utf8');

function extractFunction(functionName) {
  const marker = `function ${functionName}`;
  const start = html.indexOf(marker);
  if (start < 0) throw new Error(`Missing function ${functionName}`);
  const brace = html.indexOf('{', start);
  let depth = 0;
  for (let pos = brace; pos < html.length; pos += 1) {
    const ch = html[pos];
    if (ch === '{') depth += 1;
    if (ch === '}') {
      depth -= 1;
      if (depth === 0) return html.slice(start, pos + 1);
    }
  }
  throw new Error(`Could not extract ${functionName}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const sandbox = {
  escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  },
};

vm.createContext(sandbox);
[
  'formatNestedObject',
  'normalizeDetailValue',
  'registryComparableValue',
  'registrySourceDisplayValue',
  'registryValueIsStructured',
  'getFieldProvenanceConflictState',
  'renderFieldProvenanceConflictBadge',
  'renderFieldProvenanceSourceNote',
].forEach((fn) => vm.runInContext(extractFunction(fn), sandbox));

assert(sandbox.getFieldProvenanceConflictState('Current', undefined) === null, 'no source data should not create an indicator');
assert(sandbox.getFieldProvenanceConflictState('Company Ltd', 'company ltd') === null, 'case-insensitive match should not create mismatch');
assert(sandbox.getFieldProvenanceConflictState('  Company   Ltd  ', 'company ltd') === null, 'space-normalized match should not create mismatch');

const missing = sandbox.getFieldProvenanceConflictState('', 'Registry Company Ltd');
assert(missing && missing.state === 'missing', 'blank current value with source should be missing');
assert(sandbox.renderFieldProvenanceConflictBadge(missing).includes('>Missing<'), 'missing badge should render');
assert(sandbox.renderFieldProvenanceSourceNote(missing).includes('Source: Registry Company Ltd'), 'source note should render for missing source conflict');

const mismatch = sandbox.getFieldProvenanceConflictState('Current Company Ltd', 'Registry Company Ltd');
assert(mismatch && mismatch.state === 'mismatch', 'plain text source difference should be mismatch');
assert(sandbox.renderFieldProvenanceConflictBadge(mismatch).includes('>Mismatch<'), 'mismatch badge should render');

const needsReview = sandbox.getFieldProvenanceConflictState(
  '1 Current Street',
  { full_address: '1 Registry Street, London' }
);
assert(needsReview && needsReview.state === 'needs_review', 'structured source comparison should be needs review');
assert(sandbox.renderFieldProvenanceConflictBadge(needsReview).includes('>Needs review<'), 'needs review badge should render');
assert(sandbox.renderFieldProvenanceSourceNote(needsReview).includes('Source: 1 Registry Street, London'), 'source note should prefer full_address');

const override = sandbox.getFieldProvenanceConflictState('Current', 'Registry', { hasOverride: true });
assert(override && override.state === 'needs_review', 'overridden source/current difference should be needs review, not green');

console.log('field provenance helper behaviour ok');
"""


def test_field_provenance_conflict_helper_states_are_conservative():
    assert shutil.which("node"), "Node.js is required for helper behaviour validation"

    result = subprocess.run(
        ["node", "-e", HELPER_BEHAVIOUR_SCRIPT, str(BACKOFFICE_HTML)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_prescreen_summary_renders_conflict_badge_and_source_note_locally():
    html = _html()
    summary_body = _extract_js_function(html, "renderPrescreenSummary")

    assert "companiesHouseFieldProvenanceConflictState(app, field, currentRaw)" in summary_body
    assert "renderFieldProvenanceConflictBadge(registryConflict)" in summary_body
    assert "renderFieldProvenanceSourceNote(registryConflict)" in summary_body
    assert "registryBadges + registryConflictBadge" in summary_body
    assert "registrySourceNote" in summary_body


def test_conflict_indicators_do_not_expand_green_verified_coverage():
    html = _html()
    green_body = _extract_js_function(html, "renderCompaniesHouseFieldBadges")
    conflict_badge_body = _extract_js_function(html, "renderFieldProvenanceConflictBadge")
    conflict_state_body = _extract_js_function(html, "getFieldProvenanceConflictState")

    assert "renderCompaniesHouseRegistryBadge('verified'" in green_body
    assert "renderFieldProvenanceConflictBadge" not in green_body
    assert "✓ Verified" not in conflict_badge_body
    assert "ch-registry-badge" not in conflict_badge_body
    assert "field-provenance-conflict-badge" in conflict_badge_body
    assert "currentComparable === sourceComparable) return null" in conflict_state_body


def test_no_provenance_data_remains_implicit_without_client_declared_badge():
    html = _html()
    conflict_state_body = _extract_js_function(html, "getFieldProvenanceConflictState")
    conflict_app_body = _extract_js_function(html, "companiesHouseFieldProvenanceConflictState")
    conflict_badge_body = _extract_js_function(html, "renderFieldProvenanceConflictBadge")

    assert "sourceValue === undefined" in conflict_state_body
    assert "return null" in conflict_state_body
    assert "if (!isCompaniesHouseRegistryApp(app)) return null" in conflict_app_body
    assert "Client declared" not in conflict_badge_body
    assert "client-declared" not in conflict_badge_body
    assert "Client declared" not in _extract_js_function(html, "renderPrescreenSummary")


def test_supported_registry_fields_only_reuse_existing_source_mapping():
    html = _html()
    app_conflict_body = _extract_js_function(html, "companiesHouseFieldProvenanceConflictState")
    candidate_body = _extract_js_function(html, "registryFieldCandidateKeys")

    assert "registrySourcedValueForField(app, field)" in app_conflict_body
    assert "registered_entity_name" in candidate_body
    assert "entity_type" in candidate_body
    assert "registered_address" in candidate_body
    assert "registered_office_address" in candidate_body
    assert "incorporation_date" in candidate_body
    assert "country_of_incorporation" in candidate_body
    assert "registration_number" in candidate_body
    assert "brn" in candidate_body


def test_conflict_indicators_are_backoffice_only_and_do_not_touch_kyc_reverify_strings():
    backoffice = _html(BACKOFFICE_HTML)
    portal = _html(PORTAL_HTML)

    assert "field-provenance-conflict-badge" in backoffice
    assert "field-provenance-conflict-badge" not in portal
    assert "field-provenance-source-note" not in portal
    assert "Re-verify" in backoffice
    assert "verifyBackofficeDocument" in backoffice
