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

function extractVar(varName) {
  const marker = `var ${varName} = `;
  const start = html.indexOf(marker);
  if (start < 0) throw new Error(`Missing var ${varName}`);
  const end = html.indexOf('};', start);
  if (end < 0) throw new Error(`Could not extract var ${varName}`);
  return html.slice(start, end + 2);
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
vm.runInContext(extractVar('CH_REGISTRY_FIELD_ALIASES'), sandbox);
[
  'formatNestedObject',
  'normalizeDetailValue',
  'registryPrescreeningData',
  'registryProviderIsCompaniesHouse',
  'registryValueForKey',
  'registryFieldCandidateKeys',
  'registrySourcedValueForField',
  'registryCompanyStatus',
  'registryCompanyStatusHasMaterialIssue',
  'registrySourceDisplayValue',
  'registryComparableValue',
  'registryPad2',
  'registryDateComparableValue',
  'registryNumberComparableValue',
  'registryFieldComparableValue',
  'renderCompaniesHouseRegistryBadge',
  'isCompaniesHouseRegistryApp',
  'renderCompaniesHouseFieldBadges',
].forEach((fn) => vm.runInContext(extractFunction(fn), sandbox));

const app = {
  prescreeningData: {
    registry_provenance: { provider: 'companies_house' },
    registry_sourced_values: {
      registered_entity_name: 'Registry Company Ltd',
      registered_office_address: { full_address: '1 Registry Street, London' },
      incorporation_date: '2024-01-02',
    },
  },
};

assert(
  sandbox.renderCompaniesHouseFieldBadges(app, { key: 'registered_entity_name', label: 'Registered Entity Name' }, '  registry   company ltd  ').includes('>✓<'),
  'case-insensitive whitespace-normalized match should show tick'
);
assert(
  sandbox.renderCompaniesHouseFieldBadges(app, { key: 'registered_entity_name', label: 'Registered Entity Name' }, 'Current Company Ltd').includes('>Edited<'),
  'source/current difference should show neutral Edited'
);
assert(
  sandbox.renderCompaniesHouseFieldBadges(app, { key: 'registered_address', label: 'Registered Address' }, '1 Registry Street, London').includes('>✓<'),
  'structured address with a safe display value should compare'
);
assert(
  sandbox.renderCompaniesHouseFieldBadges(app, { key: 'incorporation_date', label: 'Incorporation Date' }, '2024-1-2').includes('>✓<'),
  'safe date normalization should compare dates'
);
assert(
  sandbox.renderCompaniesHouseFieldBadges(app, { key: 'registered_entity_name', label: 'Registered Entity Name' }, '') === '',
  'missing current values should remain unbadged'
);
assert(
  sandbox.renderCompaniesHouseFieldBadges({}, { key: 'registered_entity_name', label: 'Registered Entity Name' }, 'Registry Company Ltd') === '',
  'non-registry applications should remain unbadged'
);

console.log('field provenance neutral indicator behaviour ok');
"""


def test_registry_field_indicator_helper_states_are_neutral():
    assert shutil.which("node"), "Node.js is required for helper behaviour validation"

    result = subprocess.run(
        ["node", "-e", HELPER_BEHAVIOUR_SCRIPT, str(BACKOFFICE_HTML)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_prescreen_summary_renders_registry_field_badges_without_warning_notes():
    html = _html()
    summary_body = _extract_js_function(html, "renderPrescreenSummary")

    assert "renderCompaniesHouseFieldBadges(app, field, currentRaw)" in summary_body
    assert "companiesHouseFieldProvenanceConflictState" not in summary_body
    assert "renderFieldProvenanceConflictBadge" not in summary_body
    assert "renderFieldProvenanceSourceNote" not in summary_body
    assert "registryBadges +" in summary_body


def test_conflict_warning_indicators_removed_from_registry_badge_surface():
    html = _html()
    green_body = _extract_js_function(html, "renderCompaniesHouseFieldBadges")
    badge_body = _extract_js_function(html, "renderCompaniesHouseRegistryBadge")

    assert "renderCompaniesHouseRegistryBadge('verified'" in green_body
    assert "renderCompaniesHouseRegistryBadge('edited'" in green_body
    assert "renderFieldProvenanceConflictBadge" not in green_body
    assert "✓ Verified" not in badge_body
    assert "field-provenance-conflict-badge" not in html
    assert "getFieldProvenanceConflictState" not in html
    assert "Mismatch" not in badge_body
    assert "Needs review" not in badge_body


def test_no_provenance_data_remains_implicit_without_client_declared_badge():
    html = _html()
    field_body = _extract_js_function(html, "renderCompaniesHouseFieldBadges")
    party_badge_body = _extract_js_function(html, "renderPartyRegistryFieldBadge")

    assert "registryValue === undefined" in field_body
    assert "return ''" in field_body
    assert "originalValue === undefined" in party_badge_body
    assert "Client declared" not in field_body
    assert "client-declared" not in field_body
    assert "Client declared" not in party_badge_body


def test_supported_registry_fields_only_reuse_existing_source_mapping():
    html = _html()
    field_body = _extract_js_function(html, "renderCompaniesHouseFieldBadges")
    candidate_body = _extract_js_function(html, "registryFieldCandidateKeys")

    assert "registrySourcedValueForField(app, field)" in field_body
    assert "registered_entity_name" in candidate_body
    assert "entity_type" in candidate_body
    assert "registered_address" in candidate_body
    assert "registered_office_address" in candidate_body
    assert "incorporation_date" in candidate_body
    assert "country_of_incorporation" in candidate_body
    assert "registration_number" in candidate_body
    assert "brn" in candidate_body


def test_registry_indicators_are_backoffice_only_and_do_not_touch_kyc_reverify_strings():
    backoffice = _html(BACKOFFICE_HTML)
    portal = _html(PORTAL_HTML)

    assert "ch-registry-badge" in backoffice
    assert "field-provenance-conflict-badge" not in backoffice
    assert "ch-registry-badge" not in portal
    assert "field-provenance-conflict-badge" not in portal
    assert "field-provenance-source-note" not in portal
    assert "Re-verify" in backoffice
    assert "verifyBackofficeDocument" in backoffice
