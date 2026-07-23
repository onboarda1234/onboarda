import json
import os
import shutil
import subprocess
import textwrap


BACKOFFICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "arie-backoffice.html",
)


def _read_backoffice():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_function(html, name):
    start = html.index(f"function {name}")
    brace = html.index("{", start)
    depth = 0
    for idx in range(brace, len(html)):
        char = html[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[start : idx + 1]
    raise AssertionError(f"Could not extract function {name}")


def _officer_runtime_js(html, scenario):
    # Start at the shared pepDeclaredTriState classifier (defined just above
    # normalizePepDisplay) so the extracted PEP block carries the helper that
    # normalizePepDisplay / personHasDeclaredOrVerifiedPep now delegate to.
    pep_start = html.index("function pepDeclaredTriState(value)")
    pep_end = html.index("\nfunction buildPartyDisplayName", pep_start)
    correction_start = html.index("function safeParseJson(value, fallback)")
    correction_end = html.index("\nasync function submitOfficerCorrection", correction_start)
    return "\n".join(
        [
            textwrap.dedent(
                """
                function assert(condition, message) {
                  if (!condition) throw new Error(message);
                }
                function escapeHtml(value) {
                  return String(value == null ? '' : value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
                }
                function normalizeDetailValue(value) {
                  if (value == null || value === '') return '—';
                  if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
                  return String(value);
                }
                function firstMeaningfulDetailValue() {
                  for (let i = 0; i < arguments.length; i++) {
                    const value = arguments[i];
                    if (value != null && String(value).trim() !== '' && String(value).trim() !== '—') {
                      return String(value);
                    }
                  }
                  return '—';
                }
                var APPLICATIONS = [
                  {
                    country: 'Mauritius',
                    sector: 'Professional Services',
                    entityType: 'SME / Private Company',
                    ownershipStructure: 'Simple — direct identifiable UBOs'
                  }
                ];
                var currentApp = {
                  country: 'Mauritius',
                  sector: 'Professional Services',
                  entityType: 'SME / Private Company',
                  ownershipStructure: 'Simple — direct identifiable UBOs',
                  directors: [{ id: 'dir-1', name: 'Jane Meridian' }],
                  ubos: [{ id: 'ubo-1', name: 'John Harbor' }],
                  intermediaries: []
                };
                var RUNTIME_RISK_MODEL = { catalogs: {
                  country: [{label:'Mauritius'},{label:'United Kingdom'},{label:'Singapore'}],
                  sector: [{label:'Professional Services'},{label:'Technology / SaaS'}],
                  entity_type: [{label:'SME / Private Company'},{label:'Trust'}]
                }};
                var document = {
                  elements: {},
                  getElementById(id) { return this.elements[id] || null; }
                };
                """
            ),
            html[pep_start:pep_end],
            html[correction_start:correction_end],
            scenario,
        ]
    )


def _run_node(script):
    assert shutil.which("node"), "Node.js is required for officer correction UI tests"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=os.path.dirname(BACKOFFICE_PATH),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_officer_correction_history_safe_parsing_and_pep_rendering():
    html = _read_backoffice()
    scenario = textwrap.dedent(
        """
        assert(safeParseJson({ a: 1 }, {}).a === 1, 'object input should pass through');
        assert(Array.isArray(safeParseJson('[1,2]', [])), 'JSON array string should parse');
        assert(safeParseJson('{"field":"value"}', {}).field === 'value', 'JSON object string should parse');
        assert(safeParseJson('{bad json', { fallback: true }).fallback === true, 'invalid JSON should use fallback');

        document.elements['detail-officer-corrections'] = { innerHTML: '' };
        const app = {
          ubos: currentApp.ubos,
          directors: currentApp.directors,
          intermediaries: [],
          officerCorrections: [{
            target_type: 'pep_status',
            subject_type: 'ubo',
            target_id: 'ubo-1',
            field_changes: null,
            before_state: {
              client_declared_pep: false,
              declared_pep: false,
              officer_verified_pep: null,
              verified_pep: null,
              pep_status: 'declared_no'
            },
            after_state: JSON.stringify({
              client_declared_pep: false,
              declared_pep: false,
              officer_verified_pep: true,
              verified_pep: true,
              pep_status: 'confirmed_pep',
              pep_declaration: { internal: 'do not render' }
            }),
            corrected_by_name: 'Aisha Sudally',
            corrected_at: '2026-05-12 14:13:21',
            correction_reason: 'Screening review confirmed PEP exposure',
            evidence_source: 'Screening review confirmed PEP exposure'
          }]
        };
        renderOfficerCorrectionHistory(app);
        assert(document.elements['detail-officer-corrections'].innerHTML.includes('Expand'), 'history should collapse by default');
        currentApp = app;
        toggleOfficerCorrectionHistory();
        const rendered = document.elements['detail-officer-corrections'].innerHTML;
        assert(rendered.includes('Field corrected:'), 'history should label the corrected field');
        assert(rendered.includes('John Harbor - Verified PEP status'), 'history should include readable person and field');
        assert(rendered.includes('Before:</strong> Client declared: No | Officer verified: Not verified yet'), 'before PEP state should be readable');
        assert(rendered.includes('After:</strong> Client declared: No | Officer verified: Confirmed PEP'), 'after PEP state should be readable');
        assert(rendered.includes('Reason / Evidence:'), 'history should show reason/evidence');
        assert(!rendered.includes('target_type'), 'history must not expose backend target_type');
        assert(!rendered.includes('materiality'), 'history must not expose materiality');
        assert(!rendered.includes('tier1') && !rendered.includes('tier2') && !rendered.includes('tier3'), 'history must not expose internal tiers');
        assert(!rendered.includes('raw JSON'), 'history must not expose raw JSON labels');
        assert(!rendered.includes('{&quot;') && !rendered.includes('internal'), 'history must not render nested JSON internals');
        console.log(JSON.stringify({ ok: true }));
        """
    )
    assert _run_node(_officer_runtime_js(html, scenario))["ok"] is True


def test_officer_correction_history_collapsed_summary_toggle_and_secure_values():
    html = _read_backoffice()
    encrypted_blob = "gAAAAABqLongEncryptedValueThatMustNeverRenderInOfficerCorrectionHistory0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    scenario = textwrap.dedent(
        f"""
        document.elements['detail-officer-corrections'] = {{ innerHTML: '' }};
        currentApp = {{
          ubos: currentApp.ubos,
          directors: currentApp.directors,
          intermediaries: [],
          officerCorrections: [
            {{
              id: 10,
              target_type: 'ubo',
              subject_type: 'ubo',
              target_id: 'ubo-1',
              field_scope: 'nationality',
              before_state: {{ nationality: 'Mauritius' }},
              after_state: {{ nationality: 'Singapore' }},
              corrected_by_name: 'Older Officer',
              corrected_at: '2026-05-12 14:13:21',
              correction_reason: 'Older correction',
              downstream_state: {{ risk_impact: 'Risk recomputed: no change' }}
            }},
            {{
              id: 11,
              target_type: 'ubo',
              subject_type: 'ubo',
              target_id: 'ubo-1',
              field_scope: 'nationality',
              before_state: {{ nationality: '{encrypted_blob}' }},
              after_state: {{ nationality: 'Iran' }},
              corrected_by_name: 'Aisha Sudally',
              corrected_at: '2026-06-08 09:20:00',
              correction_reason: 'Passport evidence confirms nationality',
              downstream_state: {{ risk_impact: 'Risk recomputed: 55.0 / HIGH -> 70.0 / VERY_HIGH' }}
            }}
          ]
        }};

        OFFICER_CORRECTION_HISTORY_EXPANDED = false;
        renderOfficerCorrectionHistory(currentApp);
        let rendered = document.elements['detail-officer-corrections'].innerHTML;
        assert(rendered.includes('Corrections'), 'collapsed summary should label correction count');
        assert(rendered.includes('>2<'), 'collapsed summary should show correction count');
        assert(rendered.includes('John Harbor - Nationality'), 'summary should show latest corrected field / party');
        assert(rendered.includes('Aisha Sudally'), 'summary should show latest officer');
        assert(rendered.includes('2026-06-08 09:20:00'), 'summary should show latest date');
        assert(rendered.includes('Risk recomputed: 55.0 / HIGH -&gt; 70.0 / VERY_HIGH'), 'summary should show latest risk impact');
        assert(rendered.includes('Expand'), 'collapsed summary should expose Expand toggle');
        assert(!rendered.includes('Before:'), 'collapsed view must hide full before/after detail');
        assert(!rendered.includes('{encrypted_blob}'), 'collapsed view must not show encrypted values');

        toggleOfficerCorrectionHistory();
        rendered = document.elements['detail-officer-corrections'].innerHTML;
        assert(rendered.includes('Collapse'), 'expanded view should expose Collapse toggle');
        assert(rendered.includes('Before:</strong> Previous value unavailable / securely stored'), 'encrypted before value should be redacted');
        assert(rendered.includes('After:</strong> Iran'), 'expanded view should preserve readable after value');
        assert(!rendered.includes('{encrypted_blob}'), 'expanded view must not show encrypted values');
        assert(rendered.indexOf('Aisha Sudally') < rendered.indexOf('Older Officer'), 'expanded history should remain newest-first');

        toggleOfficerCorrectionHistory();
        rendered = document.elements['detail-officer-corrections'].innerHTML;
        assert(rendered.includes('Expand'), 'collapsed view should restore Expand toggle');
        assert(!rendered.includes('Before:'), 'collapsed view should hide details after collapsing');
        console.log(JSON.stringify({{ ok: true }}));
        """
    )
    assert _run_node(_officer_runtime_js(html, scenario))["ok"] is True


def test_officer_correction_field_specific_controls_are_configured():
    html = _read_backoffice()
    scenario = textwrap.dedent(
        """
        function cfg(target, field, current) {
          return officerCorrectionValueControlConfig(target, field, current || '');
        }
        assert(cfg('application', 'country', 'Mauritius').type === 'select', 'country must use select');
        assert(cfg('application', 'sector', 'Professional Services').type === 'select', 'sector must use select');
        assert(cfg('application', 'entity_type', 'SME / Private Company').type === 'select', 'entity type must use select');
        assert(cfg('application', 'ownership_structure', 'Simple — direct identifiable UBOs').type === 'select', 'ownership structure must use select');
        assert(cfg('risk_field', 'source_of_funds', '').type === 'select', 'source of funds must use select');
        assert(cfg('risk_field', 'source_of_wealth', '').type === 'select', 'source of wealth must use select');
        assert(cfg('risk_field', 'expected_volume', '').type === 'select', 'expected volume must use select');
        assert(cfg('risk_field', 'monthly_volume', '').type === 'select', 'monthly volume must use select');
        assert(cfg('ubo', 'ownership_pct', '').type === 'number', 'ownership percentage must use number input');
        assert(cfg('ubo', 'ownership_pct', '').min === 0 && cfg('ubo', 'ownership_pct', '').max === 100, 'ownership percentage must be bounded 0-100');
        assert(cfg('director', 'date_of_birth', '').type === 'date', 'date of birth must use date input');
        assert(cfg('director', 'nationality', 'Mauritius').type === 'select', 'director nationality must use select');
        assert(cfg('director', 'is_pep', 'No').type === 'select', 'client-declared PEP must use select');
        assert(cfg('intermediary', 'jurisdiction', 'Mauritius').type === 'select', 'intermediary jurisdiction must use select');
        assert(cfg('application', 'country_of_incorporation', 'Mauritius').type === 'select', 'country of incorporation must use select');
        assert(cfg('application', 'introduction_method', '').type === 'select', 'introduction method must use select');
        assert(cfg('pep_status', 'verified_pep', '').type === 'pep_status', 'PEP verification must use PEP status control');
        assert(cfg('director', 'nationality', 'Mauritius').options.includes('United Kingdom'), 'country options should include portal country list');
        assert(cfg('application', 'sector', 'Software / SaaS').options.includes('Crypto / Digital Assets Exchange'), 'sector options should include portal sector list');
        console.log(JSON.stringify({ ok: true }));
        """
    )
    assert _run_node(_officer_runtime_js(html, scenario))["ok"] is True


def test_party_residence_and_appointment_fields_are_correctable():
    """Option (a) approved 2026-07-17: the party card displays country of
    residence, residential address and (directors) date of appointment, so the
    correction form must offer them. Backend whitelist/columns already existed;
    this pins the UI side."""
    html = _read_backoffice()
    scenario = textwrap.dedent(
        """
        function cfg(target, field, current) {
          return officerCorrectionValueControlConfig(target, field, current || '');
        }
        var directorFields = officerCorrectionFieldOptions('director').map(function(o) { return o.value; });
        var uboFields = officerCorrectionFieldOptions('ubo').map(function(o) { return o.value; });
        ['country_of_residence', 'residential_address', 'date_of_appointment'].forEach(function(field) {
          assert(directorFields.includes(field), 'director must offer ' + field);
        });
        assert(uboFields.includes('country_of_residence'), 'ubo must offer country_of_residence');
        assert(uboFields.includes('residential_address'), 'ubo must offer residential_address');
        assert(!uboFields.includes('date_of_appointment'), 'ubo must NOT offer date_of_appointment');
        assert(cfg('director', 'country_of_residence', 'Mauritius').type === 'select', 'country of residence must use country select');
        assert(cfg('director', 'country_of_residence', 'Mauritius').options.includes('United Kingdom'), 'country of residence options must come from portal country list');
        assert(cfg('director', 'date_of_appointment', '').type === 'date', 'date of appointment must use date input');
        assert(cfg('director', 'residential_address', '').type === 'text', 'residential address must use free text');
        console.log(JSON.stringify({ ok: true }));
        """
    )
    assert _run_node(_officer_runtime_js(html, scenario))["ok"] is True


def test_party_card_correction_modal_locks_to_clicked_person():
    """Party-card entry must not re-ask what the officer already answered by
    clicking the card: the target/person/subject dropdowns are hidden behind a
    locked context chip. The generic Overview launcher keeps the full form
    (openOfficerCorrectionModal clears the lock)."""
    html = _read_backoffice()
    assert 'id="officer-correction-context"' in html
    assert 'id="officer-correction-context-name"' in html
    assert 'id="officer-correction-context-role"' in html
    assert 'id="officer-correction-target-group"' in html
    assert "Locked to this person" in html
    assert "function applyOfficerCorrectionLockedContext(" in html
    # openPartyCorrectionModal must set the lock; the generic opener must clear it.
    party_fn = html.split("function openPartyCorrectionModal(", 1)[1].split("function applyOfficerCorrectionLockedContext(", 1)[0]
    assert "window._officerCorrectionLockedParty = {" in party_fn
    assert "applyOfficerCorrectionLockedContext();" in party_fn
    generic_fn = html.split("function openOfficerCorrectionModal(", 1)[1].split("function renderOfficerCorrectionWarning(", 1)[0]
    assert "window._officerCorrectionLockedParty = null;" in generic_fn
    # Re-renders must re-apply the lock so a dropdown change can never unhide.
    update_fn = html.split("function updateOfficerCorrectionForm(", 1)[1].split("function openOfficerCorrectionModal(", 1)[0]
    assert "applyOfficerCorrectionLockedContext();" in update_fn


def test_officer_correction_static_guards():
    html = _read_backoffice()
    assert "parseJson(" not in html, "back-office must not reference undefined parseJson"
    assert 'id="officer-correction-new-value-control"' in html
    assert 'id="officer-correction-pep-status"' in html
    assert '<option value="false_positive">False positive</option>' in html
    assert "safeParseJson(value, fallback)" in html
    assert "function renderOfficerCorrectionValueControl()" in html
    assert "type=\"number\"" in html and "max=\"' + escapeHtml(String(cfg.max))" in html


def test_unknown_pep_values_do_not_render_as_no():
    html = _read_backoffice()
    scenario = textwrap.dedent(
        """
        assert(normalizePepDisplay(null) !== 'No', 'null PEP must not render as No');
        assert(normalizePepDisplay(undefined) !== 'No', 'undefined PEP must not render as No');
        assert(normalizePepDisplay('unknown') !== 'No', 'unknown PEP must not render as No');
        assert(officerCorrectionHistoryPepValue({}).includes('Client declared: Not captured'), 'missing client declaration should be Not captured');
        assert(officerCorrectionHistoryPepValue({}).includes('Officer verified: Not verified yet'), 'missing officer verification should be Not verified yet');
        console.log(JSON.stringify({ ok: true }));
        """
    )
    assert _run_node(_officer_runtime_js(html, scenario))["ok"] is True


def test_pr410b_prescreening_correction_mode_static_contract():
    html = _read_backoffice()
    assert "btn-prescreen-correction-mode" in html
    assert "Edit in Correction Mode" in html
    assert "Officer Correction Mode" in html
    assert "savePrescreenCorrectionMode" in html
    assert "cancelPrescreenCorrectionMode" in html
    assert "Save Correction" in html
    assert "Correction reason is required before saving." in html
    assert "/officer-corrections" in html
    assert "loadActivityLog(currentApp)" in html
    assert "await refreshCurrentAppDetail()" in html
    assert "Risk recomputed and memo controls refreshed" in html

    allowed_region = html[
        html.index("var PRESCREEN_CORRECTION_ALLOWED_FIELDS"):
        html.index("var PRESCREEN_CORRECTION_MODE_ACTIVE")
    ]
    assert "registered_entity_name" in allowed_region
    assert "trading_name" in allowed_region
    assert "referrer_name" in allowed_region
    for risk_field in (
        "country_of_incorporation",
        "sector",
        "entity_type",
        "ownership_structure",
        "introduction_method",
        "monthly_volume",
    ):
        assert risk_field in allowed_region
    for deferred_field in (
        "source_of_funds",
        "source_of_wealth",
        "pep_status",
        "ownership_pct",
        "nationality",
    ):
        assert deferred_field not in allowed_region

    assert "this risk-relevant correction type is deferred to a later controlled release." in html
    assert "No risk recomputation required" in html
    assert "Memo impact" in html


def test_pr410c_party_card_and_dropdown_static_contract():
    html = _read_backoffice()
    party_card = _extract_function(html, "renderPartyCard")
    assert "function renderPartyCard" in html
    assert "function renderPartySection" in html
    assert "Correct party details" in html
    assert "Review screening" in html
    assert "PEP / Screening Status" not in party_card
    assert "renderPartyFact('PEP', partyClientDeclaredPepDisplay(party), 'Not captured')" in party_card
    assert "Client-declared PEP" in html
    assert "Officer-verified PEP" not in party_card
    assert "Screening-confirmed PEP" not in party_card
    assert "Relationship type" not in party_card
    assert "Missing" in html
    assert "Not captured" in html
    assert "Not verified yet" in html
    assert "N/A" in html
    assert "Corrected" in party_card
    assert "No correction" in party_card
    assert "openPartyCorrectionModal" in html
    assert "application_overview_party_correction_mode" in html
    assert "OFFICER_PORTAL_COUNTRY_OPTIONS" in html
    assert "OFFICER_PORTAL_SECTOR_OPTIONS" in html
    assert "Crypto / Digital Assets Exchange" in html
    assert "Introduced by non-regulated intermediary" in html
    assert "{ value: 'is_pep', label: 'Client-declared PEP status' }" in html
    assert "renderPrescreenCorrectionControl" in html


def test_party_card_pep_display_is_consolidated_without_structured_duplicate():
    html = _read_backoffice()
    party_card = _extract_function(html, "renderPartyCard")

    assert "PEP / Screening Status" not in party_card
    assert "Structured PEP declaration" not in html
    assert "renderPepDeclarationDetailsHtml" not in party_card
    assert "renderPartyFact('PEP', partyClientDeclaredPepDisplay(party), 'Not captured')" in party_card
    assert "Client-declared PEP" not in party_card
    assert "Officer-verified PEP" not in party_card
    assert "Screening-confirmed PEP" not in party_card
    assert "Relationship type" not in party_card
    assert "renderPartyFact('Role'" in party_card
    assert "renderPartyFact(nationalityLabel" in party_card
    assert "renderPartyFact('Date of Birth'" in party_card
    assert "renderPartyFact('Ownership'" in party_card
    assert "openPartyCorrectionModal" in party_card
    assert "Correct party details" in party_card


def test_party_section_groups_party_types_in_default_open_accordion():
    html = _read_backoffice()
    party_section = _extract_function(html, "renderPartySection")

    assert 'id="party-review-accordion" open' in party_section
    assert "Directors: " in party_section
    assert "UBOs: " in party_section
    assert "Intermediaries: " in party_section
    assert "Directors, UBOs, and intermediary shareholders." in party_section
    assert "renderPartyCard(item, partyType, app)" in party_section
