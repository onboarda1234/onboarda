import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"
PORTAL_HTML = REPO_ROOT / "arie-portal.html"


def _html(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _backoffice_html() -> str:
    return _html(BACKOFFICE_HTML)


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
  console,
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
  'CH_REGISTRY_FIELD_ALIASES',
  'PARTY_REGISTRY_FIELD_KEYS',
  'PARTY_REGISTRY_TOP_LEVEL_ORIGINAL_FIELDS',
  'PARTY_REGISTRY_SOURCE_ORIGINAL_FIELDS',
].forEach((name) => vm.runInContext(extractVar(name), sandbox));
[
  'formatNestedObject',
  'normalizeDetailValue',
  'firstMeaningfulDetailValue',
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
  'partySourceIsCompaniesHouse',
  'partySourceMetadata',
  'partyFirstValueFromKeys',
  'partyRegistrySourceContainers',
  'partyOriginalRegistryValue',
  'partyCurrentRegistryValue',
  'renderPartyRegistryFieldBadge',
  'renderCompaniesHouseFieldBadges',
].forEach((fn) => vm.runInContext(extractFunction(fn), sandbox));

const verifiedBadge = sandbox.renderCompaniesHouseRegistryBadge('verified');
assert(verifiedBadge.includes('>✓<'), 'verified indicator should be tick only');
assert(!verifiedBadge.includes('✓ Verified'), 'verified indicator must not render broad text');
assert(verifiedBadge.includes('aria-label="Verified from registry"'), 'verified indicator must be accessible');

const editedBadge = sandbox.renderCompaniesHouseRegistryBadge('edited');
assert(editedBadge.includes('>Edited<'), 'edited indicator should render neutral Edited text');
assert(editedBadge.includes('Client edited registry-imported value'), 'edited indicator must explain client edit');

const app = {
  prescreeningData: {
    registry_provenance: { provider: 'companies_house' },
    registry_profile: { provider: 'companies_house' },
    registry_sourced_values: {
      registered_entity_name: 'Registry Ltd',
      registration_number: '12345678',
      country_of_incorporation: 'United Kingdom',
      incorporation_date: '2020-01-02',
    },
  },
};
assert(
  sandbox.renderCompaniesHouseFieldBadges(app, { key: 'registered_entity_name', label: 'Registered Entity Name' }, ' registry ltd ').includes('>✓<'),
  'unchanged registry-backed company field should tick'
);
assert(
  sandbox.renderCompaniesHouseFieldBadges(app, { key: 'registered_entity_name', label: 'Registered Entity Name' }, 'Client Edited Ltd').includes('>Edited<'),
  'changed registry-backed company field should show Edited'
);
assert(
  sandbox.renderCompaniesHouseFieldBadges(app, { label: 'Contact Email' }, 'client@example.com') === '',
  'client-declared contact email should have no registry badge'
);
assert(
  sandbox.renderCompaniesHouseFieldBadges(app, { key: 'registration_number', label: 'Registration / BRN' }, '') === '',
  'missing current company value should stay unbadged'
);

const director = {
  source: 'companies_house',
  full_name: 'Jane Director',
  nat: 'British',
  dob: '1980-05-01',
  country_of_residence: 'United Kingdom',
  residential_address: '',
  date_of_appointment: '2020-01-02',
  source_metadata_json: JSON.stringify({
    registry_originals: {
      full_name: 'Jane Director',
      name: 'Jane Director',
      nationality: 'British',
      date_of_birth: '1980-05-01',
      country_of_residence: 'United Kingdom',
      appointed_on: '2020-01-02',
    },
  }),
};
assert(sandbox.renderPartyRegistryFieldBadge(director, 'director', 'name').includes('>✓<'), 'unchanged imported director name should tick');
assert(sandbox.renderPartyRegistryFieldBadge({ ...director, full_name: 'Jane Edited' }, 'director', 'name').includes('>Edited<'), 'edited imported director name should show Edited');
assert(sandbox.renderPartyRegistryFieldBadge(director, 'director', 'country_of_residence').includes('>✓<'), 'unchanged director residence should tick');
assert(sandbox.renderPartyRegistryFieldBadge(director, 'director', 'residential_address') === '', 'director residential address should not badge without explicit support');
assert(sandbox.renderPartyRegistryFieldBadge(director, 'director', 'pep') === '', 'director PEP should never badge');
assert(sandbox.renderPartyRegistryFieldBadge({ ...director, source: '' }, 'director', 'name') === '', 'manual director should not badge');
assert(sandbox.renderPartyRegistryFieldBadge({ ...director, full_name: '' }, 'director', 'name') === '', 'missing current director name should not become Edited');

const ubo = {
  source: 'companies_house',
  full_name: 'Alex PSC',
  nat: 'Mauritian',
  dob: '1975-03',
  country_of_residence: 'Mauritius',
  pct: 25,
  source_metadata_json: JSON.stringify({
    registry_originals: {
      full_name: 'Alex PSC',
      name: 'Alex PSC',
      nationality: 'Mauritian',
      date_of_birth: { year: 1975, month: 3 },
      country_of_residence: 'Mauritius',
      ownership_pct: 25,
    },
  }),
};
assert(sandbox.renderPartyRegistryFieldBadge(ubo, 'ubo', 'name').includes('>✓<'), 'unchanged imported UBO name should tick');
assert(sandbox.renderPartyRegistryFieldBadge({ ...ubo, country_of_residence: 'France' }, 'ubo', 'country_of_residence').includes('>Edited<'), 'edited UBO residence should show Edited');
assert(sandbox.renderPartyRegistryFieldBadge(ubo, 'ubo', 'pep') === '', 'UBO PEP should never badge');

const intermediary = {
  source: 'companies_house',
  entity_name: 'Registry Holdco Ltd',
  jurisdiction: 'United Kingdom',
  registration_number: 'SC123456',
  registered_address: '1 Registry Street',
  pct: 40,
  owned_or_controlled_by: 'Client statement',
  source_metadata_json: JSON.stringify({
    registry_originals: {
      company_name: 'Registry Holdco Ltd',
      country_of_incorporation: 'United Kingdom',
      registration_number: 'SC123456',
      registered_address: '1 Registry Street',
      ownership_pct: 40,
    },
  }),
};
assert(sandbox.renderPartyRegistryFieldBadge(intermediary, 'intermediary', 'entity_name').includes('>✓<'), 'unchanged corporate PSC entity name should tick');
assert(sandbox.renderPartyRegistryFieldBadge({ ...intermediary, entity_name: 'Edited Holdco Ltd' }, 'intermediary', 'entity_name').includes('>Edited<'), 'edited corporate PSC entity name should show Edited');
assert(sandbox.renderPartyRegistryFieldBadge(intermediary, 'intermediary', 'owned_or_controlled_by') === '', 'owned/controlled by should not badge without registry support');

console.log('registry field indicator helper behaviour ok');
"""


def test_registry_badge_legend_is_field_level_tick_and_edited_only():
    html = _backoffice_html()
    summary_body = _extract_js_function(html, "renderPrescreenSummary")
    badge_body = _extract_js_function(html, "renderCompaniesHouseRegistryBadge")
    legend_body = _extract_js_function(html, "renderCompaniesHouseIndicatorLegend")

    assert html.count("Registry field indicators:") == 1
    assert "Registry indicators:" not in html
    assert summary_body.count("renderCompaniesHouseIndicatorLegend(app)") == 1
    assert "✓ Verified" not in badge_body
    assert "label: '✓'" in badge_body
    assert "Verified from registry" in badge_body
    assert "Client edited registry-imported value" in badge_body
    assert "aria-label" in badge_body
    assert "Review" not in badge_body
    assert "Issue" not in badge_body
    assert "renderCompaniesHouseRegistryBadge('verified'" in legend_body
    assert "renderCompaniesHouseRegistryBadge('edited'" in legend_body
    assert "renderCompaniesHouseRegistryBadge('review'" not in legend_body
    assert "renderCompaniesHouseRegistryBadge('issue'" not in legend_body


def test_registry_badge_css_is_small_green_tick_and_neutral_edited():
    html = _backoffice_html()

    assert ".ch-registry-badge" in html
    assert ".ch-registry-badge.verified" in html
    assert "width:14px" in html
    assert "min-width:14px" in html
    assert "font-size:10px" in html
    assert ".ch-registry-badge.edited" in html
    assert "#f1f5f9" in html
    assert "#475569" in html
    assert ".ch-registry-badge.review" not in html
    assert ".ch-registry-badge.issue" not in html
    assert ".field-provenance-conflict-badge" not in html
    assert ".field-provenance-source-note" not in html
    assert ".ch-party-review-note" not in html


def test_company_profile_field_badge_logic_ticks_or_marks_edited_conservatively():
    html = _backoffice_html()
    field_body = _extract_js_function(html, "renderCompaniesHouseFieldBadges")
    sourced_body = _extract_js_function(html, "registrySourcedValueForField")
    status_body = _extract_js_function(html, "registryCompanyStatusHasMaterialIssue")

    assert "registry_sourced_values" in sourced_body
    assert "registryFieldOverride(app, field)" not in field_body
    assert "registryCompanyStatusHasMaterialIssue(app)" in field_body
    assert "registryFieldComparableValue(currentRaw, fieldKey)" in field_body
    assert "renderCompaniesHouseRegistryBadge('verified'" in field_body
    assert "renderCompaniesHouseRegistryBadge('edited'" in field_body
    assert "renderCompaniesHouseRegistryBadge('review'" not in field_body
    assert "renderCompaniesHouseRegistryBadge('issue'" not in field_body
    for status in ("inactive", "dissolved", "liquidation", "administration", "receivership", "insolvency", "removed", "closed"):
        assert status in status_body


def test_company_profile_target_fields_are_wired_for_registry_badges():
    html = _backoffice_html()
    detail_body = _extract_js_function(html, "renderAuthoritativeAppDetail")

    assert "key: 'registered_entity_name'" in detail_body
    assert "key: 'entity_type'" in detail_body
    assert "key: 'registered_address'" in detail_body
    assert "registered_office_address" in detail_body
    assert "key: 'incorporation_date'" in detail_body
    assert "key: 'country_of_incorporation'" in detail_body
    assert "key: 'registration_number'" in detail_body
    assert "company_number" in detail_body


def test_party_mapping_preserves_pr570_fields_and_internal_registry_metadata_for_comparison():
    html = _backoffice_html()
    fetch_body = _extract_js_function(html, "fetchApplicationDetail")

    for field in (
        "first_name",
        "last_name",
        "nationality",
        "date_of_birth",
        "country_of_residence",
        "residential_address",
        "date_of_appointment",
        "ownership_pct",
        "registered_address",
        "registration_number",
        "owned_or_controlled_by",
        "source",
        "source_metadata_json",
        "registry_originals",
        "officer_role",
        "officer_entity_type",
        "requires_individual_kyc",
        "requires_corporate_structure_review",
        "registry_lookup_id",
        "response_hash",
        "imported_at",
        "imported_by",
        "psc_state",
        "registry_statement_type",
        "psc_status_reason",
        "psc_kind",
        "is_candidate_ubo",
    ):
        assert field in fetch_body


def test_party_field_badges_are_wired_without_row_level_verified_claim():
    html = _backoffice_html()
    party_badge_body = _extract_js_function(html, "renderPartyRegistryFieldBadge")
    party_card_body = _extract_js_function(html, "renderPartyCard")
    party_section_body = _extract_js_function(html, "renderPartySection")

    assert "function renderCompaniesHousePartyBadge" not in html
    assert "function partyHasVerifiedRegistryBadge" not in html
    assert "renderCompaniesHousePscSectionBadge" not in html
    assert "registryBadge" not in party_card_body
    assert "nameBadge" in party_card_body
    assert "renderPartyRegistryFieldBadge(party, partyType, 'country_of_residence')" in party_card_body
    assert "renderPartyRegistryFieldBadge(party, partyType, 'date_of_appointment')" in party_card_body
    assert "renderPartyRegistryFieldBadge(party, partyType, 'registration_number')" in party_card_body
    assert "renderPartyRegistryFieldBadge(party, partyType, 'registered_address')" in party_card_body
    assert "renderPartyFact('PEP', partyClientDeclaredPepDisplay(party), 'Not captured')" in party_card_body
    assert "renderPartyRegistryFieldBadge(party, partyType, 'pep')" not in party_card_body
    assert "renderPartyRegistryFieldBadge(party, partyType, 'residential_address')" not in party_card_body
    assert "renderPartyRegistryFieldBadge(party, partyType, 'owned_or_controlled_by')" not in party_card_body
    assert "renderCompaniesHouseRegistryBadge('verified'" in party_badge_body
    assert "renderCompaniesHouseRegistryBadge('edited'" in party_badge_body
    assert "renderPartySubsection('Ultimate Beneficial Owners', ubos, 'ubo', 'No UBOs persisted.')" in party_section_body


def test_party_registry_field_allowlist_excludes_client_declared_fields():
    html = _backoffice_html()
    field_keys_region = html[html.index("var PARTY_REGISTRY_FIELD_KEYS"):html.index("var PARTY_REGISTRY_TOP_LEVEL_ORIGINAL_FIELDS")]

    for expected in (
        "name",
        "nationality",
        "date_of_birth",
        "country_of_residence",
        "date_of_appointment",
        "entity_name",
        "jurisdiction",
        "registration_number",
        "registered_address",
        "ownership_pct",
    ):
        assert expected in field_keys_region
    for forbidden in (
        "residential_address",
        "pep",
        "source_of_funds",
        "expected_transactions",
        "owned_or_controlled_by",
    ):
        assert forbidden not in field_keys_region


def test_registry_field_indicator_helper_behaviour():
    assert shutil.which("node"), "Node.js is required for registry helper behaviour validation"

    result = subprocess.run(
        ["node", "-e", HELPER_BEHAVIOUR_SCRIPT, str(BACKOFFICE_HTML)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_badges_are_backoffice_only_not_rendered_in_client_portal():
    portal = _html(PORTAL_HTML)

    assert "ch-registry-badge" not in portal
    assert "ch-indicator-legend" not in portal
    assert "renderCompaniesHouseRegistryBadge" not in portal
    assert "Registry field indicators:" not in portal


def test_no_review_issue_warning_or_raw_registry_payload_surface_added():
    html = _backoffice_html()
    badge_body = _extract_js_function(html, "renderCompaniesHouseRegistryBadge")
    legend_body = _extract_js_function(html, "renderCompaniesHouseIndicatorLegend")
    party_badge_body = _extract_js_function(html, "renderPartyRegistryFieldBadge")
    party_card_body = _extract_js_function(html, "renderPartyCard")

    for forbidden in (
        "Registry unavailable",
        "Missing registry data",
        "Approved",
        "Cleared",
        "KYC passed",
        "raw_response_json",
        "COMPANIES_HOUSE_API_KEY",
        "api.company-information.service.gov.uk",
        "ciphertext",
        "Review",
        "Issue",
        "Mismatch",
    ):
        assert forbidden not in badge_body
        assert forbidden not in legend_body
        assert forbidden not in party_badge_body
        if forbidden not in {"Review", "Issue"}:
            assert forbidden not in party_card_body
    assert "raw_response_json" not in html
    assert "COMPANIES_HOUSE_API_KEY" not in html
    assert ".field-provenance-conflict-badge" not in html
