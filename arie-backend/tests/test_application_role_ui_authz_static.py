"""Applications role/UI alignment contracts.

The backend remains the authorization boundary. These tests execute the real
front-end state helpers to ensure restricted roles are not offered active
terminal controls or sent to officer-only Periodic Reviews endpoints.
"""

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
SECURITY_HARDENING = ROOT / "arie-backend" / "security_hardening.py"
SERVER = ROOT / "arie-backend" / "server.py"


def _html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _function_region(html: str, start_name: str, next_name: str) -> str:
    start = html.index(f"function {start_name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def _run_node(script: str) -> dict:
    assert shutil.which("node"), "Node.js is required for Applications UI role contracts"
    completed = subprocess.run(
        ["node", "-e", script, str(BACKOFFICE_HTML)],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def test_terminal_decision_state_has_a_role_direct_analyst_block():
    html = _html()
    role_helper = _function_region(
        html, "terminalDecisionRoleBlockReason", "canAccessApplicationPeriodicReviews"
    )
    approve_reason = _function_region(
        html, "approveBackendBlockReason", "setDetailActionVisibility"
    )
    action_state = _function_region(
        html, "buildApplicationActionState", "disabledActionReason"
    )

    assert "Analyst role cannot make final decisions" in role_helper
    assert "['admin', 'sco', 'co'].indexOf(role) < 0" in role_helper
    assert approve_reason.index("terminalDecisionRoleBlockReason()") < approve_reason.index(
        "rolePermissionsLoaded()"
    )
    assert "terminalAuthorityReason" in action_state
    assert "rejectReason = terminalAuthorityReason" in action_state
    assert "helper: terminalMessage || terminalAuthorityReason" in action_state


def test_disabled_terminal_controls_keep_accessibility_and_click_guards():
    html = _html()
    state_setter = _function_region(html, "setDetailActionState", "buildApplicationActionState")
    approve_handler = _function_region(html, "approveApplication", "rejectApplication")

    assert "el.disabled = !!state.disabled" in state_setter
    assert "el.setAttribute('aria-disabled', state.disabled ? 'true' : 'false')" in state_setter
    assert "data-disabled-reason" in state_setter
    assert approve_handler.index("buildApplicationActionState(currentApp)") < approve_handler.index(
        "assertPermission('approve_low_medium')"
    )
    assert "if (actionState.approve.disabled)" in approve_handler


def test_periodic_reviews_role_preflight_precedes_loading_and_api_calls():
    html = _html()
    access_helper = _function_region(
        html, "canAccessApplicationPeriodicReviews", "syncApplicationPeriodicReviewRoleAccessState"
    )
    summary_loader = _function_region(
        html, "loadLifecycleApplicationSummary", "fetchLifecycleApplicationSummary"
    )
    fetcher = _function_region(
        html, "fetchLifecycleApplicationSummary", "renderLifecycleApplicationSummary"
    )
    detail_loader = html[
        html.index("async function loadLifecycleDetailTab(force)") :
        html.index("// PR-C — Per-detail operator actions", html.index("async function loadLifecycleDetailTab(force)"))
    ]

    assert "['admin', 'sco', 'co'].indexOf(role) >= 0" in access_helper
    assert "Periodic Reviews are not available for your role." in html
    assert summary_loader.index("syncApplicationPeriodicReviewRoleAccessState()") < summary_loader.index(
        "fetchLifecycleApplicationSummary(applicationId)"
    )
    assert fetcher.index("!canAccessApplicationPeriodicReviews()") < fetcher.index("boApiCall(")
    assert detail_loader.index("syncApplicationPeriodicReviewRoleAccessState()") < detail_loader.index(
        "Loading periodic reviews workspace"
    )
    assert "if (err && err.status === 403)" in detail_loader
    forbidden_branch = detail_loader.split("if (err && err.status === 403)", 1)[1].split(
        "console.warn('Lifecycle detail tab load failed:'", 1
    )[0]
    assert "syncApplicationPeriodicReviewRoleAccessState(true)" in forbidden_branch
    assert "console.warn" not in forbidden_branch


def test_frontend_role_mirror_matches_unchanged_backend_policy():
    security_source = SECURITY_HARDENING.read_text(encoding="utf-8")
    server_source = SERVER.read_text(encoding="utf-8")

    assert 'DECISION_AUTHORITY_ROLES = ("admin", "sco", "co")' in security_source
    lifecycle_handler = server_source.split(
        "class LifecycleApplicationSummaryHandler", 1
    )[1].split("class AIAssistantHandler", 1)[0]
    assert 'require_auth(roles=["admin", "sco", "co"])' in lifecycle_handler
    assert '"analyst"' not in lifecycle_handler


def test_runtime_analyst_terminal_controls_are_disabled_without_opening_decision_modal():
    script = r"""
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const elements = {};
function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id, style: {}, attributes: {}, disabled: false, title: '', textContent: '', value: '',
      classList: { addCalls: 0, add() { this.addCalls += 1; } },
      setAttribute(name, value) { this.attributes[name] = String(value); },
      getAttribute(name) { return this.attributes[name] ?? null; },
      removeAttribute(name) { delete this.attributes[name]; }
    };
  }
  return elements[id];
}
const toastCalls = [];
let permissionChecks = 0;
let apiCalls = 0;
const context = {
  currentUser: { role: 'analyst' }, BO_AUTH_USER: { role: 'analyst' }, ROLE_PERMISSIONS: null,
  currentApp: { id: 'synthetic-app', statusRaw: 'compliance_review', risk: 'LOW', approvalRoute: { route: 'direct_low_medium' } },
  window: {}, document: { getElementById: element },
  getApprovalReadiness() { return { ready: true, blockers: [] }; },
  showToast(message, level) { toastCalls.push({ message, level }); },
  assertPermission() { permissionChecks += 1; return true; },
  focusCaseCommandCentre() {},
  boApiCall() { apiCalls += 1; return Promise.resolve({}); },
  console
};
vm.createContext(context);
const helpersStart = html.indexOf('function currentUserRole()');
const helpersEnd = html.indexOf('var currentUser =', helpersStart);
vm.runInContext(html.slice(helpersStart, helpersEnd), context);
const approveStart = html.indexOf('function approveApplication()');
const approveEnd = html.indexOf('\nfunction rejectApplication()', approveStart);
vm.runInContext(html.slice(approveStart, approveEnd), context);

const analystState = context.buildApplicationActionState(context.currentApp);
context.setDetailActionState('btn-approve', analystState.approve);
context.setDetailActionState('btn-reject', analystState.reject);
context.approveApplication();

const allowed = {};
for (const role of ['admin', 'sco', 'co']) {
  context.currentUser = { role };
  const state = context.buildApplicationActionState(context.currentApp);
  allowed[role] = { approveDisabled: state.approve.disabled, rejectDisabled: state.reject.disabled };
}
context.currentUser = { role: 'client' };
const clientState = context.buildApplicationActionState(context.currentApp);

process.stdout.write(JSON.stringify({
  analyst: {
    approveDisabled: analystState.approve.disabled,
    approveReason: analystState.approve.reason,
    rejectDisabled: analystState.reject.disabled,
    rejectReason: analystState.reject.reason,
    approveAriaDisabled: element('btn-approve').attributes['aria-disabled'],
    rejectAriaDisabled: element('btn-reject').attributes['aria-disabled'],
    decisionModalOpenCalls: element('modal-decision-reason').classList.addCalls,
    toastCalls,
    permissionChecks,
    apiCalls
  },
  allowed,
  client: { approveDisabled: clientState.approve.disabled, rejectDisabled: clientState.reject.disabled }
}));
"""
    result = _run_node(script)

    assert result["analyst"] == {
        "approveDisabled": True,
        "approveReason": "Analyst role cannot make final decisions",
        "rejectDisabled": True,
        "rejectReason": "Analyst role cannot make final decisions",
        "approveAriaDisabled": "true",
        "rejectAriaDisabled": "true",
        "decisionModalOpenCalls": 0,
        "toastCalls": [
            {"message": "Analyst role cannot make final decisions", "level": "warn"}
        ],
        "permissionChecks": 0,
        "apiCalls": 0,
    }
    assert result["allowed"] == {
        "admin": {"approveDisabled": False, "rejectDisabled": False},
        "sco": {"approveDisabled": False, "rejectDisabled": False},
        "co": {"approveDisabled": False, "rejectDisabled": False},
    }
    assert result["client"] == {"approveDisabled": True, "rejectDisabled": True}


def test_runtime_periodic_reviews_denial_never_spins_retries_or_logs_warning():
    script = r"""
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const elements = {};
function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id, style: {}, attributes: {}, innerHTML: '', title: '',
      setAttribute(name, value) { this.attributes[name] = String(value); },
      getAttribute(name) { return this.attributes[name] ?? null; },
      removeAttribute(name) { delete this.attributes[name]; }
    };
  }
  return elements[id];
}
let apiCalls = [];
let warningCalls = 0;
let mode = 'success';
const context = {
  currentUser: { role: 'analyst' }, BO_AUTH_USER: { role: 'analyst' }, ROLE_PERMISSIONS: null,
  currentApp: { id: 'synthetic-app', ref: 'ROLEAUDIT-UI-1' },
  window: {}, LIFECYCLE_SUMMARY_CACHE: {}, detailLifecycleSummaryOverview: null,
  document: { getElementById: element },
  boApiCall(method, path) {
    apiCalls.push({ method, path });
    if (mode === 'forbidden') return Promise.reject(Object.assign(new Error('Insufficient permissions'), { status: 403 }));
    return Promise.resolve({ active: { items: [] }, historical: { items: [] } });
  },
  renderLifecycleApplicationSummary() {}, loadOverviewPeriodicReviewBaseline() {},
  renderLifecycleDetailTab() { element('detail-tab-lifecycle').innerHTML = 'rendered'; },
  focusLifecycleDeepLinkTarget() {}, refreshPeriodicReviewDecisionActions() {},
  refreshPeriodicReviewRiskActions() {}, refreshLifecycleMemoActions() {},
  isPendingMemoState() { return false; },
  escapeHtml(value) { return String(value); },
  console: { warn() { warningCalls += 1; }, log() {}, error() {} }
};
vm.createContext(context);
const helpersStart = html.indexOf('function currentUserRole()');
const helpersEnd = html.indexOf('var currentUser =', helpersStart);
vm.runInContext(html.slice(helpersStart, helpersEnd), context);
const summaryStart = html.indexOf('async function loadLifecycleApplicationSummary(applicationId)');
const summaryEnd = html.indexOf('function renderLifecycleApplicationSummary(resp)', summaryStart);
vm.runInContext(html.slice(summaryStart, summaryEnd), context);
const detailStart = html.indexOf('async function loadLifecycleDetailTab(force)');
const detailEnd = html.indexOf('// PR-C — Per-detail operator actions', detailStart);
vm.runInContext(html.slice(detailStart, detailEnd), context);

(async () => {
  await context.loadLifecycleApplicationSummary('synthetic-app');
  await context.loadLifecycleDetailTab();
  const analystFetch = await context.fetchLifecycleApplicationSummary('synthetic-app');
  const analyst = {
    apiCalls: apiCalls.slice(),
    fetchResult: analystFetch,
    message: element('detail-tab-lifecycle').innerHTML,
    restricted: element('tab-lifecycle').attributes['data-role-restricted'],
    warningCalls
  };

  context.currentUser = { role: 'co' };
  context.window._detailLifecycleTabCache = null;
  context.LIFECYCLE_SUMMARY_CACHE = {};
  apiCalls = [];
  mode = 'forbidden';
  await context.loadLifecycleDetailTab(true);
  const forbidden = {
    apiCalls: apiCalls.slice(),
    message: element('detail-tab-lifecycle').innerHTML,
    warningCalls,
    hasSpinner: element('detail-tab-lifecycle').innerHTML.includes('Loading periodic reviews workspace')
  };

  context.window._detailLifecycleTabCache = null;
  context.LIFECYCLE_SUMMARY_CACHE = {};
  apiCalls = [];
  mode = 'success';
  await context.loadLifecycleDetailTab(true);
  const allowed = { apiCalls: apiCalls.slice(), content: element('detail-tab-lifecycle').innerHTML };

  process.stdout.write(JSON.stringify({ analyst, forbidden, allowed }));
})().catch((err) => { console.error(err); process.exit(1); });
"""
    result = _run_node(script)

    assert result["analyst"]["apiCalls"] == []
    assert result["analyst"]["fetchResult"] is None
    assert "Periodic Reviews are not available for your role." in result["analyst"]["message"]
    assert result["analyst"]["restricted"] == "true"
    assert result["analyst"]["warningCalls"] == 0

    assert result["forbidden"]["apiCalls"] == [
        {"method": "GET", "path": "/lifecycle/applications/synthetic-app/summary"}
    ]
    assert "Periodic Reviews are not available for your role." in result["forbidden"]["message"]
    assert result["forbidden"]["warningCalls"] == 0
    assert result["forbidden"]["hasSpinner"] is False

    assert result["allowed"] == {
        "apiCalls": [
            {"method": "GET", "path": "/lifecycle/applications/synthetic-app/summary"}
        ],
        "content": "rendered",
    }
