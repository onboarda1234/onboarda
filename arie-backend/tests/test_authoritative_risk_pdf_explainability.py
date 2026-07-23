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
        function evidence(tier, score, route, eligibility, edd) {
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
                reasons:[],
                escalation_reasons:tier === 'HIGH' || tier === 'VERY_HIGH' ? ['high_or_very_high_risk'] : []
              },
              floor_reasons:tier === 'HIGH' || tier === 'VERY_HIGH' ? ['floor_rule_declared_pep'] : [],
              escalations:tier === 'HIGH' || tier === 'VERY_HIGH' ? ['floor_rule_declared_pep'] : [],
              dimensions
            },
            factor_evidence:factors,
            dimension_computation_evidence:dimensionRows,
            computation_evidence:{
              schema_version:'risk-factor-evidence-v1',
              base_composite_score:35.6667,
              policy_adjustment:score - 35.6667,
              final_composite_score:score
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
        "LOW": "Onboarding Officer approval",
        "MEDIUM": "Compliance approval",
        "HIGH": "Dual control - SCO and Admin",
        "VERY_HIGH": "Dual control - SCO and Admin",
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
        assert "Detailed Dimension Computation" in rendered
        assert all(dimension in rendered for dimension in ("D1 -", "D2 -", "D3 -", "D4 -", "D5 -"))
        assert factor_labels <= {label for label in factor_labels if label in rendered}
        assert "Rule Score" in rendered
        assert "Weighted Contribution" in rendered
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
        assert "Runtime Subcriteria Configuration Reference" not in rendered
        assert "<th>Source</th>" not in rendered
        assert "applications.risk_dimensions" not in rendered
        assert "rule_engine." not in rendered


def test_pdf_renderer_has_no_scoring_or_contribution_recalculation_and_no_runtime_dependency():
    source = _pdf_source()
    assert "RUNTIME_RISK_MODEL" not in source
    assert "compute_risk_score" not in source
    assert "weighted_factor_contribution =" not in source
    assert "rule_score *" not in source
    assert "factor_weight /" not in source
    assert "composite_contribution =" not in source
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
    assert "Compliance approval" in rendered
    assert "Blocked from decision" in rendered
    assert "Terminal State" in rendered
    assert "The underlying approval route remains Compliance approval" in rendered


def test_pdf_print_layout_is_explicit_and_dimension_tables_are_not_split():
    source = _pdf_source()
    assert "@page{size:A4 landscape" in source
    assert "page-break-before:always" in source
    assert "break-inside:avoid" in source
    assert "thead{display:table-header-group}" in source
    assert 'data-pdf-page="executive"' in source
    assert 'data-pdf-page="detailed"' in source
    assert 'data-pdf-page="report-information"' in source
