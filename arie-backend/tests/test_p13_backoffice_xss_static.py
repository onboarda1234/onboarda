import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _slice_between(source: str, start: str, end: str) -> str:
    start_idx = source.index(start)
    end_idx = source.index(end, start_idx)
    return source[start_idx:end_idx]


def _security_helpers(source: str) -> str:
    return _slice_between(source, "function escapeHtml", "var TEST_SMOKE_RECORD_TOGGLE_STORAGE_KEY")


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-"],
        input=script,
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout


def test_memo_renderer_escapes_malicious_section_finding_and_company_text():
    source = _html()
    memo_region = _slice_between(source, "function renderMemoDecisionSnapshot", "async function generateComplianceMemo")
    script = f"""
const assert = require('assert');
global.window = {{ _currentDetailApp: {{ statusRaw: 'submitted' }} }};
const RISK_UNAVAILABLE_TEXT = 'Risk unavailable';
function buildRiskDisplayState() {{ return {{ hasRisk: true, level: 'bad class injected', score: 42.5 }}; }}
function memoCanonicalBlockers() {{ return []; }}
function appendMemoCanonicalBlocker(items, blocker) {{ items.push(blocker); }}
{_security_helpers(source)}
{memo_region}
const rendered = renderMemoSections({{
  application_ref: '<svg onload=alert(1)>',
  memo_generated: '2026-07-09T12:00:00Z',
  metadata: {{
    risk_rating: 'high hacked',
    approval_recommendation: 'REVIEW injected',
    block_reason: '<script>alert(1)</script>',
    blocked: true,
    rule_engine: {{
      engine_status: 'ENFORCED',
      enforcements: [{{ rule: '<img src=x onerror=alert(1)>', reason: '<script>alert(1)</script>', original: '<svg onload=alert(1)>', enforced: 'safe' }}],
      violations: [{{ rule: '<script>alert(1)</script>', severity: 'bad class injected', detail: '<img src=x onerror=alert(1)>', action: 'javascript:alert(1)' }}]
    }}
  }},
  sections: {{
    client_overview: {{ title: 'Company <script>alert(1)</script>', content: '<img src=x onerror=alert(1)>' }},
    risk_assessment: {{
      title: 'Risk section',
      content: '<script>alert(1)</script>',
      sub_sections: {{
        jurisdiction_risk: {{ title: '<svg onload=alert(1)>', rating: 'very high injected', content: 'javascript:alert(1)' }}
      }}
    }},
    red_flags_and_mitigants: {{
      title: 'Findings',
      red_flags: ['<script>alert(1)</script>'],
      mitigants: ['<img src=x onerror=alert(1)>']
    }},
    compliance_decision: {{ decision: '"><img src=x onerror=alert(1)>', content: '<svg onload=alert(1)>' }},
    ownership_and_control: {{ structure_complexity: '<script>alert(1)</script>', control_statement: '<img src=x onerror=alert(1)>', content: '<svg onload=alert(1)>' }},
    ai_explainability: {{
      content: '<script>alert(1)</script>',
      risk_increasing_factors: ['<img src=x onerror=alert(1)>'],
      risk_decreasing_factors: ['<svg onload=alert(1)>']
    }}
  }},
  supervisor: {{
    verdict: 'CONSISTENT_WITH_WARNINGS',
    supervisor_confidence: 0.6,
    recommendation: '<script>alert(1)</script>',
    contradictions: [{{ severity: 'critical injected', description: '<img src=x onerror=alert(1)>', section_a: '<svg onload=alert(1)>', section_b: 'javascript:alert(1)' }}],
    warnings: [{{ severity: 'bad class injected', description: '<script>alert(1)</script>' }}]
  }}
}});
assert(rendered.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
assert(rendered.includes('&lt;img src=x onerror=alert(1)&gt;'));
assert(rendered.includes('&lt;svg onload=alert(1)&gt;'));
assert(!rendered.includes('<script'));
assert(!rendered.includes('<img'));
assert(!rendered.includes('<svg'));
assert(!/href\\s*=\\s*["']?\\s*javascript:/i.test(rendered));
assert(!/src\\s*=\\s*["']?\\s*data:/i.test(rendered));
assert(rendered.includes('class="memo-risk-badge medium"'));
assert(rendered.includes('class="memo-decision-box review"'));
"""
    _run_node(script)


def test_supervisor_renderer_escapes_contradictions_rules_and_recommendations():
    source = _html()
    supervisor_region = _slice_between(source, "function renderSupervisorResults", "// submitSupervisorReview")
    script = f"""
const assert = require('assert');
global.window = {{ _currentDetailApp: {{ updated_at: '2026-07-09T12:30:00Z' }} }};
const elements = {{}};
function element(id) {{
  if (!elements[id]) {{
    elements[id] = {{
      id,
      innerHTML: '',
      textContent: '',
      style: {{}},
      firstChild: null,
      insertBefore(child) {{ this.innerHTML = (child.innerHTML || '') + this.innerHTML; }},
      remove() {{ this.removed = true; }}
    }};
  }}
  return elements[id];
}}
global.document = {{
  getElementById: element,
  createElement(tag) {{ return element('created-' + tag + '-' + Object.keys(elements).length); }}
}};
{_security_helpers(source)}
{supervisor_region}
renderSupervisorResults({{
  status: 'completed',
  started_at: '<script>alert(1)</script>',
  completed_at: '<img src=x onerror=alert(1)>',
  pipeline_id: '<svg onload=alert(1)>',
  case_aggregate: {{
    aggregate_confidence: 0.5,
    confidence_routing: '<script>alert(1)</script>',
    successful_agents: 1,
    total_agents_run: 2,
    total_contradictions: 1,
    total_rules_triggered: 1,
    escalation_required: true,
    escalation_level: '<img src=x onerror=alert(1)>'
  }},
  agent_results: [{{ agent_name: '<script>alert(1)</script>', agent_type: '<img src=x onerror=alert(1)>', status: 'bad class injected', confidence: 0.2, findings_count: 1, issues_count: 1, escalation_flag: true }}],
  contradictions_detail: [{{ contradiction_category: '<svg onload=alert(1)>', severity: 'critical injected', description: '<img src=x onerror=alert(1)>', agent_a_type: '<script>alert(1)</script>', agent_b_type: 'javascript:alert(1)' }}],
  triggered_rules: [{{ rule_name: '<script>alert(1)</script>', action_taken: 'bad class injected', rule_recommendation: '<svg onload=alert(1)>' }}],
  requires_human_review: true,
  blocking_issues: ['<img src=x onerror=alert(1)>'],
  review_reasons: ['<script>alert(1)</script>']
}});
const rendered = Object.values(elements).map((el) => el.innerHTML || '').join('\\n');
assert(rendered.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
assert(rendered.includes('&lt;img src=x onerror=alert(1)&gt;'));
assert(rendered.includes('&lt;svg onload=alert(1)&gt;'));
assert(!rendered.includes('<script'));
assert(!rendered.includes('<img'));
assert(!rendered.includes('<svg'));
assert(!/href\\s*=\\s*["']?\\s*javascript:/i.test(rendered));
assert(!/src\\s*=\\s*["']?\\s*data:/i.test(rendered));
"""
    _run_node(script)


def test_audit_renderers_escape_detail_before_after_and_chain_errors():
    source = _html()
    audit_region = _slice_between(source, "function safeParseAuditDetail", "function setAuditTrailFilter")
    supervisor_audit_region = _slice_between(source, "async function refreshSupervisorAudit", "// Auto-load supervisor dashboard")
    script = f"""
const assert = require('assert');
function formatRoleLabel(value) {{ return String(value || ''); }}
function formatNestedObject(value) {{ return JSON.stringify(value); }}
function firstMeaningfulDetailValue() {{
  for (const value of arguments) {{
    if (value !== undefined && value !== null && String(value).trim() !== '') return value;
  }}
  return '';
}}
const elements = {{}};
function element(id) {{
  if (!elements[id]) elements[id] = {{ id, value: '', innerHTML: '', textContent: '', style: {{}} }};
  return elements[id];
}}
global.document = {{ getElementById: element }};
global.BO_API_BASE = '/api';
global.BO_AUTH_TOKEN = '';
{_security_helpers(source)}
{audit_region}
{supervisor_audit_region}
const card = renderAuditEventCard({{
  action: '<script>alert(1)</script>',
  target: '<img src=x onerror=alert(1)>',
  user_name: '<svg onload=alert(1)>',
  user_role: 'admin',
  timestamp: '2026-07-09T12:00:00Z',
  detail: JSON.stringify({{ status: 'unknown class injected', summary: '<script>alert(1)</script>' }}),
  before_state: JSON.stringify({{ value: '<img src=x onerror=alert(1)>' }}),
  after_state: JSON.stringify({{ value: '<svg onload=alert(1)>' }})
}}, 0);
assert(card.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
assert(card.includes('&lt;img src=x onerror=alert(1)&gt;'));
assert(card.includes('&lt;svg onload=alert(1)&gt;'));
assert(card.includes('audit-chip status pending'));
assert(!card.includes('<script'));
assert(!card.includes('<img'));
assert(!card.includes('<svg'));
assert.strictEqual(auditStatusClass('x\\" onclick=\\"alert(1)'), 'pending');
assert.strictEqual(auditCategoryClass('x\\" onclick=\\"alert(1)'), 'system');
assert.strictEqual(adminAuditActionBadgeClass('x\\" onclick=\\"alert(1)'), 'pending');
document.getElementById('sv-audit-filter-type').value = 'type&bad=<script>';
let calls = [];
global.fetch = async function(url) {{
  calls.push(url);
  return {{
    json: async () => ({{
      entries: [{{
        severity: 'critical injected',
        event_type: '<script>alert(1)</script>',
        timestamp: '2026-07-09T12:00:00Z',
        action: '<img src=x onerror=alert(1)>',
        detail: '<svg onload=alert(1)>',
        entry_hash: '<script>alert(1)</script>'
      }}]
    }})
  }};
}};
(async function() {{
  await refreshSupervisorAudit();
  const entriesHtml = document.getElementById('sv-audit-entries').innerHTML;
  const entriesLower = entriesHtml.toLowerCase();
  assert(calls[0].includes('event_type=type%26bad%3D%3Cscript%3E'));
  assert(entriesLower.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
  assert(entriesLower.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert(entriesLower.includes('&lt;svg onload=alert(1)&gt;'));
  assert(!entriesHtml.includes('<script'));
  assert(!entriesHtml.includes('<img'));
  assert(!entriesHtml.includes('<svg'));
  fetch = async function() {{ return {{ json: async () => ({{ error: '<svg onload=alert(1)>' }}) }}; }};
  await verifySupervisorAuditChain();
  const chainHtml = document.getElementById('sv-audit-chain-status').innerHTML;
  assert(chainHtml.includes('&lt;svg onload=alert(1)&gt;'));
  assert(!chainHtml.includes('<svg'));
}})().catch((err) => {{ console.error(err); process.exit(1); }});
"""
    _run_node(script)


def test_named_renderer_static_patterns_use_safe_mappings_and_escaping():
    source = _html()
    memo_region = _slice_between(source, "function renderMemoSections", "async function generateComplianceMemo")
    supervisor_region = _slice_between(source, "function renderSupervisorResults", "// Auto-load supervisor dashboard")
    audit_region = _slice_between(source, "function safeParseAuditDetail", "function setAuditTrailFilter")
    admin_audit_region = _slice_between(source, "function renderAudit()", "function getApplicationScreeningReport")

    assert "MEMO_RISK_BADGE_CLASS_MAP" in source
    assert "MEMO_DECISION_BOX_CLASS_MAP" in source
    assert "AUDIT_STATUS_CLASS_MAP" in source
    assert "ADMIN_AUDIT_ACTION_BADGE_CLASS_MAP" in source
    assert "(sub.rating||'medium').toLowerCase()" not in memo_region
    assert "(item.status || 'generated').replace(/_/g, '-')" not in memo_region
    assert "+ sec.content +" not in memo_region
    assert "+ c.description" not in memo_region
    assert "+ w.description" not in memo_region
    assert "+ data.error +" not in supervisor_region
    assert "+ e.detail +" not in supervisor_region
    assert "return normalized;" not in audit_region
    assert "category.toLowerCase()" not in audit_region
    assert "adminAuditActionBadgeClass(e.action)" in admin_audit_region
