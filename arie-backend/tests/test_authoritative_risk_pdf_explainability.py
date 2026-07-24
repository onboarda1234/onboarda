"""Presentation-only contracts for the Authoritative Risk Evidence PDF."""

import json
from pathlib import Path
import shutil
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _pdf_source() -> str:
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    start = html.index("function requireAuthoritativeRiskPdfEvidence(app)")
    end = html.index("\nfunction downloadRiskPDF()", start)
    return html[start:end]


def _run_node(script: str) -> dict:
    assert shutil.which("node"), "Node.js is required for PDF explainability tests"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _fixture_script(assertion: str) -> str:
    prelude = textwrap.dedent(
        """
        function escapeHtml(value) {
          return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        }
        function getAuthoritativeRiskEvidence(app) {
          const evidence = app && app.riskReportEvidence;
          if (!evidence || evidence.available !== true || evidence.authoritative !== true || evidence.status !== 'ready') return null;
          return evidence;
        }
        function factor(dimensionId, key, label, raw, score, weight, contribution) {
          return {
            dimension_id:dimensionId, factor_key:key, factor_label:label,
            raw_value:raw, normalized_value:Array.isArray(raw) ? raw.map(String) : String(raw).toLowerCase(),
            rule_score:score, factor_weight:weight,
            weighted_factor_contribution:contribution,
            resolution_status:'resolved', rule_identifier:key + '_runtime_score',
            evidence_source:'rule_engine.' + key
          };
        }
        const factors = [
          factor('D1','entity_type','Entity Type','Family Office',3,20,0.6),
          factor('D1','ownership_structure','Ownership Structure','Simple - direct identifiable UBOs',1,20,0.2),
          factor('D1','pep_status','PEP Status','No declared PEP',1,25,0.25),
          factor('D1','adverse_media','Adverse Media','No adverse media',1,15,0.15),
          factor('D1','source_of_wealth','Source of Wealth','Business revenue',1,10,0.1),
          factor('D1','source_of_funds','Source of Funds','Company bank transfer',1,10,0.1),
          factor('D2','country_of_incorporation','Country of Incorporation','United Arab Emirates',2,25,0.5),
          factor('D2','ubo_nationalities','UBO / Director Nationalities',['United Arab Emirates'],2,20,0.4),
          factor('D2','intermediary_jurisdictions','Intermediary Shareholder Jurisdictions',[],1,20,0.2),
          factor('D2','countries_of_operation','Countries of Operation',['United Arab Emirates'],2,20,0.4),
          factor('D2','target_markets','Target Markets',['United Arab Emirates'],2,15,0.3),
          factor('D3','service_type','Service Type',['Cross-border payments'],3,40,1.2),
          factor('D3','monthly_volume','Monthly Volume','USD 500,000 to USD 5m per month',3,35,1.05),
          factor('D3','transaction_complexity','Transaction Complexity','Complex - multiple currencies',3,25,0.75),
          factor('D4','industry_sector','Industry Sector','Investment Management',3,100,3),
          factor('D5','introduction_method','Introduction Method','Direct application - client initiated',1,50,0.5),
          factor('D5','delivery_channel','Delivery Channel','Video',2,50,1)
        ];
        const dimensionRows = [
          {dimension_id:'D1',dimension_score:1.4,dimension_weight:30,rounding_adjustment:0,composite_contribution:4,factor_keys:['entity_type','ownership_structure','pep_status','adverse_media','source_of_wealth','source_of_funds']},
          {dimension_id:'D2',dimension_score:1.8,dimension_weight:25,rounding_adjustment:0,composite_contribution:6.6667,factor_keys:['country_of_incorporation','ubo_nationalities','intermediary_jurisdictions','countries_of_operation','target_markets']},
          {dimension_id:'D3',dimension_score:3,dimension_weight:20,rounding_adjustment:0,composite_contribution:13.3333,factor_keys:['service_type','monthly_volume','transaction_complexity']},
          {dimension_id:'D4',dimension_score:3,dimension_weight:15,rounding_adjustment:0,composite_contribution:10,factor_keys:['industry_sector']},
          {dimension_id:'D5',dimension_score:1.5,dimension_weight:10,rounding_adjustment:0,composite_contribution:1.6667,factor_keys:['introduction_method','delivery_channel']}
        ];
        const dimensions = dimensionRows.map((row) => ({
          id:row.dimension_id,
          name:{
            D1:'Customer / Entity Risk', D2:'Geographic Risk',
            D3:'Product / Service Risk', D4:'Industry / Sector Risk',
            D5:'Delivery Channel Risk'
          }[row.dimension_id],
          weight:row.dimension_weight,
          stored_score:row.dimension_score
        }));
        function evidence(tier, score, route, eligibility, edd, options) {
          options = options || {};
          const has = (key) => Object.prototype.hasOwnProperty.call(options, key);
          const storedFactors = factors.map((row) => ({...row}));
          const storedDimensions = dimensionRows.map((row) => ({...row, factor_keys:[...row.factor_keys]}));
          return {
            available:true, authoritative:true, read_only:true, status:'ready',
            config_version:'risk_config:2026-07-17 11:16:03.481284',
            risk_computed_at:'2026-07-23T15:35:00Z',
            application:{
              score, tier, edd_route:edd,
              approval_route:{
                route:eligibility === 'blocked' ? 'blocked' : route,
                approval_route:route,
                decision_eligibility:eligibility,
                eligibility_reason:eligibility === 'blocked' ? 'terminal_state' : '',
                reasons:options.approval_reasons || [],
                escalation_reasons:options.approval_escalations || []
              },
              floor_reasons:options.floor_reasons || [],
              escalations:options.escalations || [],
              dimensions
            },
            factor_evidence:storedFactors,
            dimension_computation_evidence:storedDimensions,
            computation_evidence:{
              schema_version:'risk-factor-evidence-v1',
              base_composite_score:has('base') ? options.base : score,
              policy_adjustment:has('adjustment') ? options.adjustment : 0,
              final_composite_score:has('final') ? options.final : score
            }
          };
        }
        const app = {
          ref:'RM-PILOT-PDF', company:'Regulator Evidence Ltd',
          country:'United Arab Emirates', brn:'BRN-PDF-001',
          sector:'Investment Management', entityType:'Family Office',
          dims:{canonical_dataset:{dataset_version:'v1',dataset_hash:'45ceaa32d592f754289fb888bbb6d6a863349cf9bde406e7d7055b6c7dc23d25'}},
          prescreeningData:{}
        };
        """
    )
    return prelude + "\n" + _pdf_source() + "\n" + textwrap.dedent(assertion)


def test_low_medium_high_and_very_high_reports_render_the_persisted_ledger_only():
    result = _run_node(
        _fixture_script(
            """
            const cases = [
              ['LOW',12,'direct_low_medium','eligible','Fast Lane'],
              ['MEDIUM',43.3,'compliance_required','eligible','Standard Review'],
              ['HIGH',55,'dual_control_required','eligible','EDD'],
              ['VERY_HIGH',70,'dual_control_required','blocked','EDD']
            ];
            const output = {};
            cases.forEach(([tier, score, route, eligibility, edd]) => {
              const stored = evidence(tier, score, route, eligibility, edd);
              app.riskReportEvidence = stored;
              const before = JSON.stringify(stored);
              const html = buildAuthoritativeRiskPdfHtml(app, stored, 'Test Officer', '2026-07-23T16:00:00Z');
              output[tier] = {html, unmodified:before === JSON.stringify(stored)};
            });
            process.stdout.write(JSON.stringify(output));
            """
        )
    )

    route_labels = {
        "LOW": "Onboarding Officer Approval",
        "MEDIUM": "Compliance Approval",
        "HIGH": "Dual Control - SCO and Admin",
        "VERY_HIGH": "Dual Control - SCO and Admin",
    }
    tier_labels = {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH", "VERY_HIGH": "VERY HIGH"}
    scores = {"LOW": "12", "MEDIUM": "43.3", "HIGH": "55", "VERY_HIGH": "70"}
    factor_labels = {
        "Entity Type",
        "Ownership Structure",
        "PEP Status",
        "Adverse Media",
        "Source of Wealth",
        "Source of Funds",
        "Country of Incorporation",
        "UBO / Director Nationalities",
        "Intermediary Shareholder Jurisdictions",
        "Countries of Operation",
        "Target Markets",
        "Service Type",
        "Monthly Volume",
        "Transaction Complexity",
        "Industry Sector",
        "Introduction Method",
        "Delivery Channel",
    }

    for tier, payload in result.items():
        rendered = payload["html"]
        assert payload["unmodified"] is True
        assert tier_labels[tier] in rendered
        assert scores[tier] in rendered
        assert route_labels[tier] in rendered
        assert "Executive Summary" in rendered
        assert "Decision Eligibility" in rendered
        assert "Executive Recommendation" in rendered
        assert "Primary Risk Drivers" in rendered
        assert "Detailed Dimension Computation" in rendered
        assert all(dimension in rendered for dimension in ("D1 -", "D2 -", "D3 -", "D4 -", "D5 -"))
        assert all(label in rendered for label in factor_labels)
        assert (
            "<th>Factor</th><th>Input Value</th><th>Rule Applied</th>"
            "<th>Risk Rating</th><th>Weight</th><th>Weighted Contribution</th>"
        ) in rendered
        assert all(rating in rendered for rating in ("Very Low (1/4)", "Low (2/4)", "Medium (3/4)"))
        assert "Rounding Adjustment" in rendered
        assert "Composite Score" in rendered
        assert "Policy Adjustment" in rendered
        assert "Final Score" in rendered
        assert "Risk Configuration Version" in rendered
        assert "RSMP Version" in rendered
        assert "Manifest Version" in rendered
        assert "Evidence Hash" in rendered
        assert "Computation Hash" in rendered
        assert "Not separately recorded" in rendered
        assert "Original Score" not in rendered
        assert "Evidence Status" not in rendered
        assert "Complete factor ledger" not in rendered
        assert "Dimension reconciliation verified" not in rendered
        assert "Authoritative and read-only" not in rendered
        assert "Key Risk Drivers" not in rendered
        assert "<th>Rule Score</th>" not in rendered
        assert "<th>Evidence</th>" not in rendered
        assert "<th>Explanation</th>" not in rendered
        assert "Stored evidence" not in rendered
        assert "Resolved as" not in rendered
        assert "Runtime Subcriteria Configuration Reference" not in rendered
        assert "<th>Source</th>" not in rendered
        assert "applications.risk_dimensions" not in rendered
        assert "rule_engine." not in rendered
        assert "direct_low_medium" not in rendered
        assert "compliance_required" not in rendered
        assert "dual_control_required" not in rendered


def test_pdf_renderer_has_no_scoring_or_contribution_recalculation_and_no_runtime_dependency():
    source = _pdf_source()
    assert "RUNTIME_RISK_MODEL" not in source
    assert "compute_risk_score" not in source
    assert "weighted_factor_contribution =" not in source
    assert "rule_score *" not in source
    assert "factor_weight /" not in source
    assert "composite_contribution =" not in source
    assert "policy_adjustment +" not in source
    assert "base_composite_score +" not in source
    assert ".reduce(" not in source
    assert "evidence.factor_evidence" in source
    assert "evidence.dimension_computation_evidence" in source
    assert "evidence.computation_evidence" in source


def test_pdf_export_fails_closed_when_the_persisted_factor_ledger_is_incomplete():
    result = _run_node(
        _fixture_script(
            """
            const stored = evidence('MEDIUM',43.3,'compliance_required','eligible','Standard Review');
            stored.factor_evidence = stored.factor_evidence.slice(0, 16);
            app.riskReportEvidence = stored;
            let message = '';
            try { requireAuthoritativeRiskPdfEvidence(app); }
            catch (error) { message = error.message; }
            process.stdout.write(JSON.stringify({message}));
            """
        )
    )
    assert result["message"] == (
        "Authoritative factor evidence is incomplete. "
        "Recompute risk before exporting."
    )


def test_pdf_export_fails_closed_when_dimension_factor_order_is_partial_or_malformed():
    result = _run_node(
        _fixture_script(
            """
            const variants = [
              'not-an-array',
              ['entity_type','ownership_structure','pep_status','adverse_media','source_of_wealth'],
              ['entity_type','ownership_structure','pep_status','adverse_media','source_of_wealth','source_of_wealth']
            ];
            const messages = variants.map((factorKeys) => {
              const stored = evidence('MEDIUM',43.3,'compliance_required','eligible','Standard Review');
              stored.dimension_computation_evidence[0].factor_keys = factorKeys;
              app.riskReportEvidence = stored;
              try { requireAuthoritativeRiskPdfEvidence(app); return ''; }
              catch (error) { return error.message; }
            });
            const duplicateDimension = evidence('MEDIUM',43.3,'compliance_required','eligible','Standard Review');
            duplicateDimension.dimension_computation_evidence[1] = {
              ...duplicateDimension.dimension_computation_evidence[0],
              factor_keys:[...duplicateDimension.dimension_computation_evidence[0].factor_keys]
            };
            app.riskReportEvidence = duplicateDimension;
            try { requireAuthoritativeRiskPdfEvidence(app); messages.push(''); }
            catch (error) { messages.push(error.message); }
            process.stdout.write(JSON.stringify({messages}));
            """
        )
    )
    assert set(result["messages"]) == {
        "Authoritative dimension evidence is incomplete. Recompute risk before exporting."
    }


def test_pdf_risk_rating_mapping_is_exact_and_malformed_scores_fail_closed():
    result = _run_node(
        _fixture_script(
            """
            const valid = evidence('MEDIUM',43.3,'compliance_required','eligible','Standard Review');
            [1,2,3,4].forEach((rating, index) => { valid.factor_evidence[index].rule_score = rating; });
            const html = buildAuthoritativeRiskPdfHtml(app, valid, 'Test Officer', '2026-07-23T16:00:00Z');
            const invalidValues = [null, '', 0, 5, 2.5, '4', 'high', NaN];
            const messages = invalidValues.map((value) => {
              const stored = evidence('MEDIUM',43.3,'compliance_required','eligible','Standard Review');
              stored.factor_evidence[0].rule_score = value;
              app.riskReportEvidence = stored;
              try { requireAuthoritativeRiskPdfEvidence(app); return ''; }
              catch (error) { return error.message; }
            });
            const missing = evidence('MEDIUM',43.3,'compliance_required','eligible','Standard Review');
            delete missing.factor_evidence[0].rule_score;
            app.riskReportEvidence = missing;
            let missingMessage = '';
            try { requireAuthoritativeRiskPdfEvidence(app); }
            catch (error) { missingMessage = error.message; }
            process.stdout.write(JSON.stringify({html, messages, missingMessage}));
            """
        )
    )
    assert all(
        rating in result["html"]
        for rating in ("Very Low (1/4)", "Low (2/4)", "Medium (3/4)", "High (4/4)")
    )
    assert set(result["messages"]) == {
        "Authoritative factor risk rating is invalid. Recompute risk before exporting."
    }
    assert result["missingMessage"] == (
        "Authoritative factor evidence is incomplete. Recompute risk before exporting."
    )


def test_original_score_is_conditional_and_uses_only_persisted_values():
    result = _run_node(
        _fixture_script(
            """
            const roundingOnly = evidence(
              'MEDIUM',43.3,'compliance_required','eligible','Standard Review',
              {base:43.3, adjustment:-0.0333, final:43.3}
            );
            const elevated = evidence(
              'HIGH',55,'dual_control_required','eligible','EDD',
              {
                base:13, adjustment:42, final:55,
                floor_reasons:['floor_rule_opaque_ownership'],
                escalations:['floor_rule_opaque_ownership']
              }
            );
            const before = JSON.stringify(elevated);
            const roundingHtml = buildAuthoritativeRiskPdfHtml(app, roundingOnly, 'Test Officer', '2026-07-23T16:00:00Z');
            const elevatedHtml = buildAuthoritativeRiskPdfHtml(app, elevated, 'Test Officer', '2026-07-23T16:00:00Z');
            process.stdout.write(JSON.stringify({
              roundingHtml, elevatedHtml, unmodified:before === JSON.stringify(elevated)
            }));
            """
        )
    )
    assert "Original Score" not in result["roundingHtml"]
    assert "Original Score" in result["elevatedHtml"]
    assert ">13<" in result["elevatedHtml"]
    assert ">55<" in result["elevatedHtml"]
    assert "Reason for Elevation or Floor" in result["elevatedHtml"]
    assert "Opaque Ownership Structure" in result["elevatedHtml"]
    assert "floor_rule_opaque_ownership" not in result["elevatedHtml"]
    assert result["unmodified"] is True


def test_rm_pilot_028_opaque_ownership_floor_driver_is_first_despite_lower_contribution():
    result = _run_node(
        _fixture_script(
            """
            app.ref = 'RM-PILOT-028';
            const stored = evidence(
              'HIGH',55,'dual_control_required','eligible','EDD',
              {
                base:13, adjustment:42, final:55,
                floor_reasons:['floor_rule_opaque_ownership'],
                escalations:['floor_rule_opaque_ownership']
              }
            );
            const ownership = stored.factor_evidence.find((item) => item.factor_key === 'ownership_structure');
            ownership.raw_value = '3+ ownership layers / nominee shareholders';
            ownership.normalized_value = '3+ ownership layers / nominee shareholders';
            ownership.rule_score = 3;
            ownership.weighted_factor_contribution = 0.6;
            const industry = stored.factor_evidence.find((item) => item.factor_key === 'industry_sector');
            industry.weighted_factor_contribution = 3;
            const html = buildAuthoritativeRiskPdfHtml(app, stored, 'Test Officer', '2026-07-23T16:00:00Z');
            const driverStart = html.indexOf('Primary Risk Drivers');
            const drivers = html.slice(driverStart, html.indexOf('<div class="report-footer"', driverStart));
            process.stdout.write(JSON.stringify({html, drivers}));
            """
        )
    )
    assert result["drivers"].index("Ownership Structure") < result["drivers"].index("Industry Sector")
    assert "3+ ownership layers / nominee shareholders" in result["drivers"]
    assert "Opaque Ownership Structure" in result["drivers"]
    assert "floor_rule_opaque_ownership" not in result["html"]


def test_persisted_afghanistan_nationality_floor_driver_is_prominent_without_canonical_rewrite():
    result = _run_node(
        _fixture_script(
            """
            app.ref = 'QA-PDF-GEOGRAPHY-FLOOR';
            const stored = evidence(
              'VERY_HIGH',70,'dual_control_required','eligible','EDD',
              {
                base:46.2, adjustment:23.8, final:70,
                floor_reasons:['floor_rule_sanctioned_nationality:afghanistan'],
                escalations:['floor_rule_sanctioned_nationality:afghanistan']
              }
            );
            const nationality = stored.factor_evidence.find((item) => item.factor_key === 'ubo_nationalities');
            nationality.raw_value = ['Afghanistan'];
            nationality.normalized_value = ['afghanistan'];
            nationality.rule_score = 4;
            nationality.weighted_factor_contribution = 0.8;
            const html = buildAuthoritativeRiskPdfHtml(app, stored, 'Test Officer', '2026-07-23T16:00:00Z');
            const driverStart = html.indexOf('Primary Risk Drivers');
            const drivers = html.slice(driverStart, html.indexOf('<div class="report-footer"', driverStart));
            process.stdout.write(JSON.stringify({html, drivers}));
            """
        )
    )
    assert result["drivers"].index("UBO / Director Nationalities") < result["drivers"].index("Industry Sector")
    assert "Afghanistan" in result["drivers"]
    assert "Sanctioned or FATF High-Risk UBO or Director Nationality - Afghanistan" in result["html"]
    assert "floor_rule_sanctioned_nationality" not in result["html"]


def test_all_floor_and_elevation_drivers_remain_visible_when_more_than_eight_are_recorded():
    result = _run_node(
        _fixture_script(
            """
            const stored = evidence(
              'VERY_HIGH',70,'dual_control_required','eligible','EDD',
              {
                base:32, adjustment:38, final:70,
                floor_reasons:[
                  'floor_rule_declared_pep',
                  'floor_rule_high_risk_sector',
                  'floor_rule_elevated_jurisdiction',
                  'floor_rule_opaque_ownership',
                  'floor_rule_sanctioned_nationality:afghanistan'
                ],
                escalations:[
                  'elevation_screening_concern',
                  'elevation_severe_combination',
                  'floor_rule_edd_routing',
                  'material_screening_disposition_floor'
                ]
              }
            );
            const html = buildAuthoritativeRiskPdfHtml(app, stored, 'Test Officer', '2026-07-23T16:00:00Z');
            const driverStart = html.indexOf('Primary Risk Drivers');
            const drivers = html.slice(driverStart, html.indexOf('<div class="report-footer"', driverStart));
            const driverCount = (drivers.match(/<div class="driver/g) || []).length;
            process.stdout.write(JSON.stringify({html, drivers, driverCount}));
            """
        )
    )
    assert result["driverCount"] > 8
    assert all(
        label in result["drivers"]
        for label in (
            "Declared PEP",
            "High-Risk Sector",
            "Elevated Jurisdiction",
            "Opaque Ownership Structure",
            "Sanctioned or FATF High-Risk UBO or Director Nationality - Afghanistan",
            "Material Screening Concern",
            "Severe Combined Risk Factors",
            "Enhanced Due Diligence Requirement",
            "Confirmed Material Screening Concern",
        )
    )
    assert "floor_rule_" not in result["html"]
    assert "elevation_" not in result["html"]


def test_rule_outcomes_use_business_labels_and_never_leak_internal_identifiers():
    result = _run_node(
        _fixture_script(
            """
            const stored = evidence(
              'HIGH',55,'dual_control_required','blocked','EDD',
              {
                base:13, adjustment:42, final:55,
                floor_reasons:['floor_rule_opaque_ownership'],
                escalations:['floor_rule_opaque_ownership'],
                approval_escalations:['high_or_very_high_risk','developer_only_internal_rule']
              }
            );
            const html = buildAuthoritativeRiskPdfHtml(app, stored, 'Test Officer', '2026-07-23T16:00:00Z');
            process.stdout.write(JSON.stringify({html}));
            """
        )
    )
    rendered = result["html"]
    assert all(
        label in rendered
        for label in ("Risk Floor Applied", "Reason for Elevation", "Approval Basis", "Decision Eligibility")
    )
    assert "Opaque Ownership Structure" in rendered
    assert "High or Very High Risk Rating" in rendered
    assert "Recorded Compliance Control" in rendered
    assert "Terminal Application State" in rendered
    assert "floor_rule_opaque_ownership" not in rendered
    assert "high_or_very_high_risk" not in rendered
    assert "developer_only_internal_rule" not in rendered
    assert "terminal_state" not in rendered
    assert "dual_control_required" not in rendered


def test_pdf_uses_underlying_policy_route_and_reports_lifecycle_eligibility_separately():
    result = _run_node(
        _fixture_script(
            """
            const stored = evidence('MEDIUM',43.3,'compliance_required','blocked','Standard Review');
            app.riskReportEvidence = stored;
            const html = buildAuthoritativeRiskPdfHtml(app, stored, 'Test Officer', '2026-07-23T16:00:00Z');
            process.stdout.write(JSON.stringify({html}));
            """
        )
    )
    rendered = result["html"]
    assert "Compliance Approval" in rendered
    assert "Blocked from decision" in rendered
    assert "Terminal Application State" in rendered
    assert "The underlying approval route remains Compliance Approval" in rendered
    assert "compliance_required" not in rendered
    assert "terminal_state" not in rendered


def test_pdf_print_layout_is_explicit_and_dimension_tables_are_not_split():
    source = _pdf_source()
    assert "@page{size:A4 landscape" in source
    assert "page-break-before:always" in source
    assert "break-inside:avoid" in source
    assert "thead{display:table-header-group}" in source
    assert "table.factor-table{table-layout:fixed}" in source
    assert ".dimension-page-break{break-before:page;page-break-before:always}" in source
    assert (
        '<col style="width:17%"><col style="width:29%"><col style="width:21%">'
        '<col style="width:14%"><col style="width:8%"><col style="width:11%">'
    ) in source
    assert 'data-pdf-page="executive"' in source
    assert 'data-pdf-page="detailed"' in source
    assert 'data-pdf-page="report-information"' in source
