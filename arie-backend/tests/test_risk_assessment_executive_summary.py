"""Presentation-only contracts for the Risk Assessment Executive Summary."""

import json
from pathlib import Path
import shutil
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _source_region(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def _ui_source() -> str:
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    return _source_region(
        html,
        "function riskExecutiveStoredOutcomeCodes(risk)",
        "\nfunction setMemoDownloadState",
    )


def _risk_report_helpers() -> str:
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    return _source_region(
        html,
        "function riskReportDisplayValue(value)",
        "\nfunction buildAuthoritativeRiskPdfHtml",
    )


def _run_node(script: str) -> dict:
    assert shutil.which("node"), "Node.js is required for Risk Assessment UI tests"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
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
        function factor(dimensionId, key, label, raw, score, contribution) {
          return {
            dimension_id:dimensionId,
            factor_key:key,
            factor_label:label,
            raw_value:raw,
            normalized_value:raw,
            rule_score:score,
            factor_weight:25,
            weighted_factor_contribution:contribution,
            resolution_status:'resolved',
            rule_identifier:key + '_runtime_score',
            evidence_source:'rule_engine.' + key
          };
        }
        const baseFactors = [
          factor('D1','entity_type','Entity Type','Trust',3,0.6),
          factor('D1','ownership_structure','Ownership Structure','Simple — direct identifiable UBOs',1,0.2),
          factor('D1','pep_status','PEP Status','No declared PEP',1,0.25),
          factor('D1','adverse_media','Adverse Media','No adverse media',1,0.15),
          factor('D2','country_of_incorporation','Country of Incorporation','United Kingdom',1,0.25),
          factor('D2','ubo_nationalities','UBO / Director Nationalities',['British'],1,0.2),
          factor('D3','service_type','Service Type',['Cross-border payments'],3,1.2),
          factor('D3','monthly_volume','Monthly Volume','Over USD 5m',4,1.4),
          factor('D4','industry_sector','Industry Sector','Private Banking',4,4),
          factor('D5','introduction_method','Introduction Method','Direct application',1,0.5)
        ];
        const dimensions = [
          {id:'D1',name:'Customer / Entity Risk',stored_score:2.2,weight:30},
          {id:'D2',name:'Geographic Risk',stored_score:1,weight:25},
          {id:'D3',name:'Product / Service Risk',stored_score:3.5,weight:20},
          {id:'D4',name:'Industry / Sector Risk',stored_score:4,weight:15},
          {id:'D5',name:'Delivery Channel Risk',stored_score:1,weight:10}
        ];
        const dimensionEvidence = [
          {dimension_id:'D1',composite_contribution:91.1111},
          {dimension_id:'D2',composite_contribution:82.2222},
          {dimension_id:'D3',composite_contribution:73.3333},
          {dimension_id:'D4',composite_contribution:64.4444},
          {dimension_id:'D5',composite_contribution:55.5555}
        ];
        function evidence(options) {
          options = options || {};
          const score = Object.prototype.hasOwnProperty.call(options, 'score') ? options.score : 55;
          const factors = (options.factors || baseFactors).map((row) => ({...row}));
          return {
            available:true,
            authoritative:true,
            status:'ready',
            source:'stored runtime evidence',
            config_version:'risk_config:2026-07-17 11:16:03.481284',
            risk_computed_at:'2026-07-23T15:35:00Z',
            application:{
              score,
              tier:options.tier || 'HIGH',
              edd_route:options.edd || 'EDD',
              approval_route:{
                route:options.blocked ? 'blocked' : (options.route || 'dual_control_required'),
                approval_route:options.route || 'dual_control_required',
                decision_eligibility:options.blocked ? 'blocked' : 'eligible',
                escalation_reasons:options.routeEscalations || []
              },
              floor_reasons:options.floorReasons || [],
              escalations:options.escalations || [],
              dimensions:dimensions.map((row) => ({...row}))
            },
            factor_evidence:factors,
            dimension_computation_evidence:dimensionEvidence.map((row) => ({...row})),
            computation_evidence:{
              base_composite_score:Object.prototype.hasOwnProperty.call(options, 'base') ? options.base : score,
              policy_adjustment:Object.prototype.hasOwnProperty.call(options, 'adjustment') ? options.adjustment : 0,
              final_composite_score:Object.prototype.hasOwnProperty.call(options, 'final') ? options.final : score
            }
          };
        }
        const app = {
          sector:'STATIC APPLICATION SECTOR',
          country:'STATIC APPLICATION COUNTRY',
          entityType:'STATIC APPLICATION ENTITY',
          riskReportEvidence:null
        };
        function driversOnly(html) {
          return html.slice(html.indexOf('Primary Risk Drivers'), html.indexOf('Risk breakdown'));
        }
        """
    )
    return (
        prelude
        + "\n"
        + _risk_report_helpers()
        + "\n"
        + _ui_source()
        + "\n"
        + textwrap.dedent(assertion)
    )


def test_low_medium_high_and_very_high_use_persisted_factors_and_business_routes():
    result = _run_node(
        _fixture_script(
            """
            const cases = [
              ['LOW',12,'direct_low_medium','Fast Lane'],
              ['MEDIUM',40.4,'compliance_required','Standard Review'],
              ['HIGH',55,'dual_control_required','EDD'],
              ['VERY_HIGH',70,'dual_control_required','EDD']
            ];
            const output = {};
            cases.forEach(([tier, score, route, edd]) => {
              const stored = evidence({tier, score, route, edd});
              app.riskReportEvidence = stored;
              const before = JSON.stringify(stored);
              const html = renderStoredRiskComputationHtml(app);
              output[tier] = {
                html,
                drivers:driversOnly(html),
                unmodified:before === JSON.stringify(stored)
              };
            });
            process.stdout.write(JSON.stringify(output));
            """
        )
    )

    route_labels = {
        "LOW": "Onboarding Officer Approval",
        "MEDIUM": "Compliance Approval",
        "HIGH": "Dual Control — SCO and Admin",
        "VERY_HIGH": "Dual Control — SCO and Admin",
    }
    for tier, payload in result.items():
        rendered = payload["html"]
        drivers = payload["drivers"]
        assert payload["unmodified"] is True
        assert "Primary Risk Drivers" in rendered
        assert "Executive Recommendation" not in rendered
        assert route_labels[tier] in rendered
        assert "Industry Sector" in drivers
        assert "Private Banking" in drivers
        assert "High (4/4)" in drivers
        assert "STATIC APPLICATION SECTOR" not in rendered
        assert "STATIC APPLICATION COUNTRY" not in rendered
        assert "STATIC APPLICATION ENTITY" not in rendered
        assert "direct_low_medium" not in rendered
        assert "compliance_required" not in rendered
        assert "dual_control_required" not in rendered
        assert "_runtime_score" not in rendered


def test_primary_drivers_exclude_workflow_consequences_and_rule_only_outcomes():
    result = _run_node(
        _fixture_script(
            """
            const stored = evidence({
              route:'dual_control_required',
              blocked:true,
              floorReasons:['floor_rule_high_risk_sector'],
              escalations:[
                'sub_factor_score_4',
                'monthly_volume_score_4',
                'edd_required',
                'high_or_very_high_risk',
                'officer_submitted_to_compliance'
              ],
              routeEscalations:[
                'edd_required',
                'high_or_very_high_risk',
                'officer_submitted_to_compliance'
              ],
              base:17,
              final:55,
              adjustment:38
            });
            app.riskReportEvidence = stored;
            const html = renderStoredRiskComputationHtml(app);
            process.stdout.write(JSON.stringify({html, drivers:driversOnly(html)}));
            """
        )
    )
    drivers = result["drivers"]
    assert "Industry Sector" in drivers
    assert "High-Risk Sector" in drivers
    assert "Monthly Volume" in drivers
    assert "Over USD 5m" in drivers
    assert "Enhanced Due Diligence Required" not in drivers
    assert "High or Very High Risk Rating" not in drivers
    assert "Officer Submission to Compliance" not in drivers
    assert "Monthly Volume Requires Compliance Review" not in drivers
    assert "Blocked from decision" not in drivers
    assert "Stored rule outcome" not in drivers
    assert "!" not in drivers


def test_floor_factor_is_first_and_remaining_order_is_deterministic():
    result = _run_node(
        _fixture_script(
            """
            const changed = baseFactors.map((row) => ({...row}));
            changed.find((row) => row.factor_key === 'ubo_nationalities').raw_value = ['British','Afghanistan'];
            changed.find((row) => row.factor_key === 'ubo_nationalities').rule_score = 4;
            changed.find((row) => row.factor_key === 'ubo_nationalities').weighted_factor_contribution = 0.8;
            changed.find((row) => row.factor_key === 'service_type').weighted_factor_contribution = 1.4;
            const first = evidence({
              factors:changed,
              floorReasons:['floor_rule_sanctioned_nationality:afghanistan'],
              base:18,
              final:70
            });
            const second = evidence({
              factors:[...changed].reverse(),
              floorReasons:['floor_rule_sanctioned_nationality:afghanistan'],
              base:18,
              final:70
            });
            app.riskReportEvidence = first;
            const firstDrivers = driversOnly(renderStoredRiskComputationHtml(app));
            app.riskReportEvidence = second;
            const secondDrivers = driversOnly(renderStoredRiskComputationHtml(app));
            process.stdout.write(JSON.stringify({firstDrivers, secondDrivers}));
            """
        )
    )
    first = result["firstDrivers"]
    second = result["secondDrivers"]
    assert first == second
    assert first.index("UBO / Director Nationalities") < first.index("Industry Sector")
    assert first.index("Industry Sector") < first.index("Monthly Volume")
    assert first.index("Monthly Volume") < first.index("Service Type")
    assert "British, Afghanistan" in first
    assert "High (4/4)" in first
    assert "Sanctioned or FATF High-Risk UBO or Director Nationality - Afghanistan" in first


def test_pep_and_opaque_ownership_floor_causes_lead_the_driver_cards():
    result = _run_node(
        _fixture_script(
            """
            function renderFloor(factorKey, rawValue, floorReason) {
              const changed = baseFactors.map((row) => ({...row}));
              const factorRow = changed.find((row) => row.factor_key === factorKey);
              factorRow.raw_value = rawValue;
              factorRow.rule_score = 4;
              factorRow.weighted_factor_contribution = 0.9;
              const stored = evidence({factors:changed, floorReasons:[floorReason], base:14, final:55});
              app.riskReportEvidence = stored;
              return driversOnly(renderStoredRiskComputationHtml(app));
            }
            process.stdout.write(JSON.stringify({
              pep:renderFloor('pep_status','Domestic PEP','floor_rule_declared_pep'),
              opaque:renderFloor('ownership_structure','Opaque — UBOs cannot be fully identified','floor_rule_opaque_ownership')
            }));
            """
        )
    )
    pep = result["pep"]
    opaque = result["opaque"]
    assert pep.index("PEP Status") < pep.index("Industry Sector")
    assert "Domestic PEP" in pep
    assert "Declared PEP" in pep
    assert opaque.index("Ownership Structure") < opaque.index("Industry Sector")
    assert "Opaque — UBOs cannot be fully identified" in opaque
    assert "Opaque Ownership Structure" in opaque


def test_ambiguous_elevation_codes_do_not_infer_factor_causes():
    result = _run_node(
        _fixture_script(
            """
            const changed = baseFactors.map((row) => ({...row}));
            changed.find((row) => row.factor_key === 'country_of_incorporation').raw_value = 'United Kingdom';
            changed.find((row) => row.factor_key === 'country_of_incorporation').rule_score = 1;
            changed.find((row) => row.factor_key === 'country_of_incorporation').weighted_factor_contribution = 0.25;
            changed.find((row) => row.factor_key === 'industry_sector').raw_value = 'Software / SaaS';
            changed.find((row) => row.factor_key === 'industry_sector').rule_score = 2;
            changed.find((row) => row.factor_key === 'industry_sector').weighted_factor_contribution = 2;
            changed.find((row) => row.factor_key === 'adverse_media').raw_value = 'clear';
            changed.find((row) => row.factor_key === 'adverse_media').rule_score = 1;
            changed.find((row) => row.factor_key === 'adverse_media').weighted_factor_contribution = 0.15;
            const severe = evidence({
              factors:changed,
              escalations:['elevation_severe_combination'],
              base:48,
              final:70
            });
            app.riskReportEvidence = severe;
            const severeHtml = renderStoredRiskComputationHtml(app);
            const screening = evidence({
              factors:changed,
              escalations:['elevation_screening_concern'],
              base:48,
              final:55
            });
            app.riskReportEvidence = screening;
            const screeningHtml = renderStoredRiskComputationHtml(app);
            process.stdout.write(JSON.stringify({
              severeDrivers:driversOnly(severeHtml),
              severeHtml,
              screeningDrivers:driversOnly(screeningHtml),
              screeningHtml
            }));
            """
        )
    )
    assert "Industry Sector" in result["severeDrivers"]
    assert "Software / SaaS" in result["severeDrivers"]
    assert "Country of Incorporation" not in result["severeDrivers"]
    assert "Adverse Media" not in result["severeDrivers"]
    assert "Severe Combined Risk Factors" not in result["severeDrivers"]
    assert "Severe Combined Risk Factors" in result["severeHtml"]
    assert "Material Screening Concern" not in result["screeningDrivers"]
    assert "Material Screening Concern" in result["screeningHtml"]


def test_original_final_strip_is_conditional_and_uses_persisted_values_verbatim():
    result = _run_node(
        _fixture_script(
            """
            const unchanged = evidence({tier:'MEDIUM',score:40.4,route:'compliance_required',base:40.4,final:40.4,adjustment:-0.0166});
            app.riskReportEvidence = unchanged;
            const unchangedHtml = renderStoredRiskComputationHtml(app);
            const adjusted = evidence({
              tier:'HIGH',
              score:55,
              route:'dual_control_required',
              floorReasons:['floor_rule_high_risk_sector'],
              base:17,
              final:55,
              adjustment:38
            });
            app.riskReportEvidence = adjusted;
            const adjustedHtml = renderStoredRiskComputationHtml(app);
            process.stdout.write(JSON.stringify({unchangedHtml, adjustedHtml}));
            """
        )
    )
    assert "Original Score" not in result["unchangedHtml"]
    assert "risk-score-adjustment" not in result["unchangedHtml"]
    assert "Original Score" in result["adjustedHtml"]
    assert ">17<" in result["adjustedHtml"]
    assert ">55<" in result["adjustedHtml"]
    assert "High-Risk Sector" in result["adjustedHtml"]
    assert "91.1111" in result["adjustedHtml"]
    assert "82.2222" in result["adjustedHtml"]


def test_ui_source_has_no_score_reconstruction_and_pdf_contract_remains_separate():
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    ui = _ui_source()
    pdf = _source_region(
        html,
        "function buildAuthoritativeRiskPdfHtml",
        "\nfunction downloadRiskPDF",
    )

    assert "score * weight * 0.25" not in ui
    assert ".reduce(" not in ui
    assert "dimension_computation_evidence" in ui
    assert "composite_contribution" in ui
    assert "weighted_factor_contribution" in ui
    assert "computation_evidence" in ui
    assert "app.sector" not in ui
    assert "app.country" not in ui
    assert "app.entityType" not in ui
    assert "Executive Recommendation" not in ui
    assert "Executive Recommendation" in pdf
