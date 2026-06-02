"""
Runtime checks for resilient back-office login and background preload handling.

These tests execute the real front-end login/preload helpers with a DOM shim so
the PR 1 behavior is pinned without requiring a browser deployment.
"""
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


def _extract_between(html, start_marker, end_marker):
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def _login_runtime_js(html, scenario):
    load_region = _extract_between(
        html,
        "var BACKOFFICE_LAST_LOAD_FAILURES = [];",
        "function formatStatus",
    )
    login_region = _extract_between(
        html,
        "function showLoginScreen()",
        "// ═══════════════════════════════════════════════════════════\n// REVIEW SCHEDULE SETTINGS",
    )
    return "\n".join(
        [
            textwrap.dedent(
                """
                const toasts = [];
                const showViewCalls = [];
                const routeMatches = [];
                const elements = {};

                function makeClassList(initial) {
                  const set = new Set(initial || []);
                  return {
                    add(name) { set.add(name); },
                    remove(name) { set.delete(name); },
                    contains(name) { return set.has(name); },
                    toArray() { return Array.from(set); }
                  };
                }

                function makeElement(id) {
                  const attributes = {};
                  return {
                    id,
                    value: '',
                    textContent: '',
                    innerHTML: '',
                    disabled: false,
                    hidden: false,
                    focusCalled: false,
                    style: {},
                    attributes,
                    classList: makeClassList(id === 'login-error' || id === 'dashboard-load-warning' ? [] : []),
                    focus() { this.focusCalled = true; },
                    setAttribute(name, value) { this.attributes[name] = String(value); },
                    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; },
                    removeAttribute(name) { delete this.attributes[name]; }
                  };
                }

                const document = {
                  body: { className: '' },
                  getElementById(id) {
                    if (!elements[id]) elements[id] = makeElement(id);
                    return elements[id];
                  }
                };

                var window = {};
                var BO_API_BASE = '/api';
                var BO_AUTH_TOKEN = '';
                var BO_AUTH_USER = null;
                var BO_AUTH_SESSION_SEQ = 0;
                var BO_ACTIVE_SESSION_ID = 0;
                var USERS = [];
                var APPLICATIONS = [];
                var AUDIT_LOG = [];
                var AI_AGENTS = [];
                var ENTITY_DOC_CHECKS = [];
                var PERSON_DOC_CHECKS = [];
                var MONITORING_ALERTS = [];
                var PERIODIC_REVIEWS = [];
                var MONITORING_AGENTS = [];
                var MONITORING_DASHBOARD = null;
                var RESOURCES = [];
                var REG_DOCUMENTS = [];
                var REG_CURRENT_DOC_ID = '';
                var ROLE_PERMISSIONS = null;
                var EDD_CASES = [];
                var currentUser = { id: '', sub: '', name: 'System', email: '', role: 'admin', initials: 'SY', status: 'active' };
                var ROLE_LABELS = { admin:'Administrator', sco:'Senior Compliance Officer', co:'Compliance Officer', analyst:'Analyst' };
                var applicationsApiTotal = 0;
                var _applicationsLastRefreshed = null;
                var _applicationsRefreshInterval = null;
                var _applicationsRefreshMs = 30000;
                var _stalenessTickInterval = null;
                var RISK_DIMENSIONS = [{ id: 'dim-1', name: 'A', weight: 25, color: '#2563eb', subcriteria: [{ name: 'Sub', weight: 10 }] }];
                var RISK_THRESHOLDS = [{ min: 0, max: 24, level: 'LOW', label: 'Low Risk', color: 'var(--green)' }];

                document.getElementById('login-email').value = '';
                document.getElementById('login-password').value = '';
                document.getElementById('login-error-text').textContent = 'Invalid email or password. Please try again.';
                document.getElementById('login-overlay').style.display = 'flex';
                document.getElementById('login-overlay').style.pointerEvents = 'auto';
                document.getElementById('login-overlay').style.visibility = 'visible';
                document.getElementById('login-overlay').setAttribute('aria-hidden', 'false');

                function showToast(message, type) { toasts.push({ message, type }); }
                function mapApplicationFromApi(app) { return Object.assign({ ref: app.ref || 'ARF-TEST-1' }, app); }
                function normalizeAIAgentConfig(agent) { return agent; }
                function normalizeMonitoringAlert(alert) { return alert; }
                function normalizePeriodicReview(review) { return review; }
                function setDashboardStatusContract() {}
                function setDashboardData(data) { routeMatches.push({ type: 'dashboard-data', data }); }
                function renderDashboardRecent() { routeMatches.push({ type: 'renderDashboardRecent', count: APPLICATIONS.length }); }
                function populateOfficerDropdowns() { routeMatches.push({ type: 'populateOfficerDropdowns', count: USERS.length }); }
                function applyBackofficeHashRoute() { return false; }
                function showView(name) { showViewCalls.push(name); }
                function loadAuditTrail() { return Promise.resolve(true); }
                async function refreshDashboardData() {
                  var dashboardResp = await boApiCall('GET', '/dashboard');
                  setDashboardStatusContract(dashboardResp);
                  setDashboardData(dashboardResp);
                  return true;
                }
                function clearBoAuthState() {
                  BO_AUTH_TOKEN = '';
                  BO_AUTH_USER = null;
                  BO_ACTIVE_SESSION_ID = 0;
                }
                function getCurrentBoSessionId() { return BO_ACTIVE_SESSION_ID; }
                function _updateFreshnessIndicator() {}
                function _startApplicationsAutoRefresh() { _applicationsRefreshInterval = 1; }
                function _stopApplicationsAutoRefresh() { _applicationsRefreshInterval = null; }
                function setBoAuth(token, user) {
                  BO_AUTH_TOKEN = token;
                  BO_AUTH_USER = user;
                  BO_AUTH_SESSION_SEQ += 1;
                  BO_ACTIVE_SESSION_ID = BO_AUTH_SESSION_SEQ;
                  if (user) {
                    currentUser = {
                      id: user.id || user.sub || '',
                      sub: user.sub || user.id || '',
                      name: user.name || user.full_name || user.email || 'Signed-in user',
                      email: user.email || '',
                      role: user.role || '',
                      initials: 'SU',
                      status: user.status || 'active'
                    };
                  }
                }
                function syncCurrentUserUI() {}
                function resetBackofficeSessionData() {
                  DASHBOARD_DATA = null;
                  DASHBOARD_BOOTSTRAP_STATE = 'idle';
                  SUPPORT_DATA_STATE = 'idle';
                  SUPPORT_DATA_PROMISE = null;
                  APPLICATIONS = [];
                  applicationsApiTotal = 0;
                  APPLICATIONS_LOAD_STATE = 'idle';
                  APPLICATIONS_LOAD_PROMISE = null;
                  APPLICATIONS_LOAD_ERROR = '';
                  SCREENING_QUEUE = { metrics:null, rows:[], generated_at:null, load_error:null };
                  SCREENING_QUEUE_DIRTY = false;
                  SCREENING_QUEUE_LOAD_PROMISE = null;
                  MONITORING_ALERTS = [];
                  PERIODIC_REVIEWS = [];
                  MONITORING_AGENTS = [];
                  MONITORING_DASHBOARD = null;
                  MONITORING_DATA_STATE = 'idle';
                  MONITORING_DATA_PROMISE = null;
                  EDD_CASES = [];
                  EDD_DATA_PROMISE = null;
                  AUDIT_LOG = [];
                  AUDIT_DATA_PROMISE = null;
                  RESOURCES = [];
                  RESOURCES_DATA_PROMISE = null;
                  REG_DOCUMENTS = [];
                  REG_CURRENT_DOC_ID = null;
                  REG_INTEL_DATA_PROMISE = null;
                  ROLE_PERMISSIONS = null;
                  ROLE_PERMISSIONS_PROMISE = null;
                  BACKOFFICE_LAST_LOAD_FAILURES = [];
                }

                async function loadEDDCases(options) {
                  options = options || {};
                  try {
                    const resp = await boApiCall('GET', '/edd/cases');
                    if (resp.cases) EDD_CASES = resp.cases;
                    return true;
                  } catch (err) {
                    if (options.throwOnError) throw err;
                    return false;
                  }
                }
                """
            ),
            load_region,
            login_region,
            scenario,
        ]
    )


def _run_node(script):
    assert shutil.which("node"), "Node.js is required for back-office runtime tests"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=os.path.dirname(BACKOFFICE_PATH),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _scenario_script(fetch_behavior_js, api_behavior_js):
    return "\n".join(
        [
            f"const FETCH_BEHAVIOR = {fetch_behavior_js.strip()};",
            f"const API_BEHAVIOR = {api_behavior_js.strip()};",
            textwrap.dedent(
                """
                const apiCalls = [];
                const fetchCalls = [];

                function okJson(payload) {
                  return { ok: true, status: 200, async json() { return payload; } };
                }

                document.getElementById('login-email').value = 'officer@example.com';
                document.getElementById('login-password').value = 'Password123!';
                document.getElementById('login-error').classList = makeClassList([]);
                document.getElementById('login-overlay').classList = makeClassList([]);
                document.getElementById('dashboard-load-warning').classList = makeClassList([]);
                document.getElementById('login-overlay').hidden = false;
                document.getElementById('login-overlay').style.display = 'flex';
                document.getElementById('login-overlay').style.pointerEvents = 'auto';
                document.getElementById('login-overlay').style.visibility = 'visible';
                document.getElementById('login-overlay').setAttribute('aria-hidden', 'false');
                document.body.className = 'role-admin login-active';

                global.fetch = async function(url, options) {
                  fetchCalls.push({ url, method: (options || {}).method || 'GET' });
                  return FETCH_BEHAVIOR(url, options || {});
                };

                async function boApiCall(method, path) {
                  apiCalls.push({ method, path });
                  return API_BEHAVIOR(method, path);
                }

                async function runScenario() {
                  await handleLogin({ preventDefault() {} });
                  console.log(JSON.stringify({
                    overlayHidden: document.getElementById('login-overlay').classList.contains('hidden'),
                    overlayDisplay: document.getElementById('login-overlay').style.display || '',
                    overlayPointerEvents: document.getElementById('login-overlay').style.pointerEvents || '',
                    overlayVisibility: document.getElementById('login-overlay').style.visibility || '',
                    overlayAriaHidden: document.getElementById('login-overlay').getAttribute('aria-hidden'),
                    overlayHiddenAttr: !!document.getElementById('login-overlay').hidden,
                    bodyClassName: document.body.className,
                    loginErrorVisible: document.getElementById('login-error').classList.contains('show'),
                    loginErrorText: document.getElementById('login-error-text').textContent,
                    warningVisible: document.getElementById('dashboard-load-warning').classList.contains('show'),
                    warningText: document.getElementById('dashboard-load-warning-text').textContent,
                    token: BO_AUTH_TOKEN,
                    currentUser: currentUser,
                    applicationsCount: APPLICATIONS.length,
                    usersCount: USERS.length,
                    eddCount: EDD_CASES.length,
                    toasts,
                    showViewCalls,
                    apiCalls,
                    fetchCalls,
                    failures: BACKOFFICE_LAST_LOAD_FAILURES
                  }));
                }

                runScenario().catch((err) => {
                  console.error(err && err.stack ? err.stack : err);
                  process.exit(1);
                });
                """
            ),
        ]
    )


def _base_api_behavior(extra_cases=""):
    return textwrap.dedent(
        f"""
        async function(method, path) {{
          {extra_cases}
          if (path === '/applications?limit=5000') return {{ total: 1, applications: [{{ ref: 'ARF-1', company: 'Acme Ltd' }}] }};
          if (path === '/dashboard') return {{ metrics: [] }};
          if (path === '/users') return {{ users: [{{ id: 'u1', full_name: 'Officer Example', email: 'officer@example.com', role: 'co', status: 'active' }}] }};
          if (path === '/config/risk-model') return {{ dimensions: [], thresholds: [] }};
          if (path === '/config/ai-agents') return {{ agents: [] }};
          if (path === '/config/verification-checks') return {{ entity: [], person: [] }};
          if (path === '/monitoring/alerts') return {{ alerts: [] }};
          if (path === '/monitoring/reviews') return {{ reviews: [] }};
          if (path === '/monitoring/agents') return {{ agents: [] }};
          if (path === '/monitoring/dashboard') return {{ stats: [] }};
          if (path === '/edd/cases') return {{ cases: [{{ id: 1, client_name: 'Acme Ltd' }}] }};
          if (path === '/resources') return {{ resources: [] }};
          if (path === '/regulatory-intelligence') return {{ documents: [] }};
          if (path === '/config/roles-permissions') return {{ permissions: [] }};
          throw new Error('Unhandled API path: ' + path);
        }}
        """
    )


class TestBackofficeLoginResilienceRuntime:
    def test_successful_auth_bootstraps_dashboard_without_blocking_deferred_loaders(self):
        html = _read_backoffice()
        fetch_behavior = textwrap.dedent(
            """
            async function(url) {
              return okJson({
                token: 'token-123',
                user: { id: 'u1', email: 'officer@example.com', name: 'Officer Example', role: 'co' }
              });
            }
            """
        )
        scenario = _scenario_script(fetch_behavior, _base_api_behavior())
        result = _run_node(_login_runtime_js(html, scenario))

        assert result["overlayHidden"] is True
        assert result["overlayDisplay"] == "none"
        assert result["overlayPointerEvents"] == "none"
        assert result["overlayVisibility"] == "hidden"
        assert result["overlayAriaHidden"] == "true"
        assert result["overlayHiddenAttr"] is True
        assert "authenticated" in result["bodyClassName"]
        assert result["loginErrorVisible"] is False
        assert result["warningVisible"] is False
        assert result["token"] == "token-123"
        assert result["applicationsCount"] == 0
        assert result["usersCount"] == 0
        assert result["showViewCalls"] == ["dashboard", "dashboard"]
        assert result["failures"] == []
        assert [call["path"] for call in result["apiCalls"]] == ["/dashboard"]

    def test_successful_auth_does_not_block_on_deferred_users_loader_failure(self):
        html = _read_backoffice()
        fetch_behavior = textwrap.dedent(
            """
            async function(url) {
              return okJson({
                token: 'token-users-fail',
                user: { id: 'u1', email: 'officer@example.com', name: 'Officer Example', role: 'co' }
              });
            }
            """
        )
        api_behavior = _base_api_behavior("if (path === '/users') throw new Error('Users failed with 500');")
        scenario = _scenario_script(fetch_behavior, api_behavior)
        result = _run_node(_login_runtime_js(html, scenario))

        assert result["overlayHidden"] is True
        assert result["overlayDisplay"] == "none"
        assert result["overlayPointerEvents"] == "none"
        assert result["overlayVisibility"] == "hidden"
        assert result["overlayAriaHidden"] == "true"
        assert result["overlayHiddenAttr"] is True
        assert result["loginErrorVisible"] is False
        assert result["warningVisible"] is False
        assert result["warningText"] == "Signed in. Some dashboard data failed to load. You can continue working, or retry loading background data."
        assert result["applicationsCount"] == 0
        assert result["usersCount"] == 0
        assert result["token"] == "token-users-fail"
        assert result["failures"] == []
        assert [call["path"] for call in result["apiCalls"]] == ["/dashboard"]

    def test_successful_auth_does_not_block_on_deferred_edd_loader_failure(self):
        html = _read_backoffice()
        fetch_behavior = textwrap.dedent(
            """
            async function(url) {
              return okJson({
                token: 'token-edd-fail',
                user: { id: 'u1', email: 'officer@example.com', name: 'Officer Example', role: 'co' }
              });
            }
            """
        )
        api_behavior = _base_api_behavior("if (path === '/edd/cases') throw new Error('EDD preload failed');")
        scenario = _scenario_script(fetch_behavior, api_behavior)
        result = _run_node(_login_runtime_js(html, scenario))

        assert result["overlayHidden"] is True
        assert result["overlayDisplay"] == "none"
        assert result["overlayPointerEvents"] == "none"
        assert result["overlayVisibility"] == "hidden"
        assert result["overlayAriaHidden"] == "true"
        assert result["overlayHiddenAttr"] is True
        assert result["loginErrorVisible"] is False
        assert result["warningVisible"] is False
        assert result["warningText"] == "Signed in. Some dashboard data failed to load. You can continue working, or retry loading background data."
        assert result["applicationsCount"] == 0
        assert result["eddCount"] == 0
        assert result["failures"] == []
        assert [call["path"] for call in result["apiCalls"]] == ["/dashboard"]

    def test_invalid_credentials_keep_overlay_visible_and_show_auth_error(self):
        html = _read_backoffice()
        fetch_behavior = textwrap.dedent(
            """
            async function(url) {
              return { ok: false, status: 401, async json() { return { error: 'Invalid email or password.' }; } };
            }
            """
        )
        scenario = _scenario_script(fetch_behavior, _base_api_behavior())
        result = _run_node(_login_runtime_js(html, scenario))

        assert result["overlayHidden"] is False
        assert result["overlayDisplay"] == "flex"
        assert result["overlayPointerEvents"] == "auto"
        assert result["overlayVisibility"] == "visible"
        assert result["overlayAriaHidden"] == "false"
        assert result["overlayHiddenAttr"] is False
        assert "login-active" in result["bodyClassName"]
        assert result["loginErrorVisible"] is True
        assert result["loginErrorText"] == "Invalid email or password."
        assert result["token"] == ""
        assert result["showViewCalls"] == []

    def test_auth_network_failure_stays_on_login_and_does_not_create_session(self):
        html = _read_backoffice()
        fetch_behavior = textwrap.dedent(
            """
            async function(url) {
              throw new Error('network down');
            }
            """
        )
        scenario = _scenario_script(fetch_behavior, _base_api_behavior())
        result = _run_node(_login_runtime_js(html, scenario))

        assert result["overlayHidden"] is False
        assert result["overlayDisplay"] == "flex"
        assert result["overlayPointerEvents"] == "auto"
        assert result["overlayVisibility"] == "visible"
        assert result["overlayAriaHidden"] == "false"
        assert result["overlayHiddenAttr"] is False
        assert "login-active" in result["bodyClassName"]
        assert result["loginErrorVisible"] is True
        assert "authentication service is unavailable" in result["loginErrorText"].lower()
        assert result["token"] == ""
        assert result["warningVisible"] is False

    def test_static_banner_copy_and_retry_control_exist(self):
        html = _read_backoffice()
        assert 'id="dashboard-load-warning"' in html
        assert 'Signed in. Some dashboard data failed to load. You can continue working, or retry loading background data.' in html
        assert 'id="dashboard-load-warning-retry"' in html
        assert "async function retryDashboardLoad()" in html
