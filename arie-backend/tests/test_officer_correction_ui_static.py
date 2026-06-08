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


def _officer_runtime_js(html, scenario):
    pep_start = html.index("function normalizePepDisplay(value)")
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
                var COUNTRY_RISK_LISTS = {
                  FATF_BLACK: [],
                  FATF_GREY: [],
                  SANCTIONED: [],
                  LOW_RISK: ['Mauritius', 'United Kingdom', 'Singapore']
                };
                var SECTOR_RISK_CONFIG = [
                  { sector: 'Professional Services', score: 3 },
                  { sector: 'Technology / SaaS', score: 2 }
                ];
                var ENTITY_TYPE_SCORES = [
                  { type: 'SME / Private Company', score: 2 },
                  { type: 'Trust', score: 3 }
                ];
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
    assert "function renderPartyCard" in html
    assert "function renderPartySection" in html
    assert "Correct party details" in html
    assert "Review screening" in html
    assert "PEP / Screening Status" in html
    assert "Client-declared PEP" in html
    assert "Officer-verified PEP" in html
    assert "Screening-confirmed PEP" in html
    assert "Missing" in html
    assert "openPartyCorrectionModal" in html
    assert "application_overview_party_correction_mode" in html
    assert "OFFICER_PORTAL_COUNTRY_OPTIONS" in html
    assert "OFFICER_PORTAL_SECTOR_OPTIONS" in html
    assert "Crypto / Digital Assets Exchange" in html
    assert "Introduced by non-regulated intermediary" in html
    assert "{ value: 'is_pep', label: 'Client-declared PEP status' }" in html
    assert "renderPrescreenCorrectionControl" in html
