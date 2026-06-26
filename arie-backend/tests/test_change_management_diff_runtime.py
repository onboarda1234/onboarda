"""
Runtime checks for PR 4 Change Management diff rendering.

These tests execute the real front-end change-management helpers with a minimal
DOM shim so the review UI stays pinned without requiring a browser deploy.
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


def _runtime_js(html, config, action_js):
    region = _extract_between(
        html,
        "function apiFetch(url, opts) {",
        "var activeCaseTab = 'my-assigned';",
    )
    return "\n".join(
        [
            textwrap.dedent(
                f"""
                const CONFIG = {json.dumps(config)};
                const elements = {{}};
                const viewCalls = [];
                const toastCalls = [];
                const boCalls = [];

                function makeElement(id) {{
                  return {{
                    id,
                    innerHTML: '',
                    textContent: '',
                    value: '',
                    hidden: false,
                    style: {{}},
                    attributes: {{}},
                    className: '',
                    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
                    getAttribute(name) {{ return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }},
                    appendChild(child) {{ this.children = this.children || []; this.children.push(child); return child; }}
                  }};
                }}

                const document = {{
                  getElementById(id) {{
                    if (!elements[id]) elements[id] = makeElement(id);
                    return elements[id];
                  }},
                  createElement(tag) {{
                    return makeElement(tag);
                  }}
                }};

                function escapeHtml(value) {{
                  return String(value == null ? '' : value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
                }}

                function showView(name) {{ viewCalls.push(name); }}
                function showToast(message, tone) {{ toastCalls.push({{ message, tone: tone || 'info' }}); }}
                function boApiCall(method, path, body) {{
                  boCalls.push({{ method, path, body }});
                  if (path.indexOf('/change-management/requests/') === 0) {{
                    return Promise.resolve(CONFIG.requestDetail || {{}});
                  }}
                  if (path.indexOf('/change-management/requests') === 0) {{
                    return Promise.resolve({{ requests: CONFIG.requestList || [] }});
                  }}
                  if (path.indexOf('/change-management/alerts') === 0) {{
                    return Promise.resolve({{ alerts: CONFIG.alertList || [] }});
                  }}
                  if (path.indexOf('/change-management/stats') === 0) {{
                    return Promise.resolve(CONFIG.stats || {{ alerts: {{ total: 0, by_status: {{}} }}, requests: {{ total: 0, by_status: {{}} }} }});
                  }}
                  if (path.indexOf('/applications') === 0) {{
                    return Promise.resolve({{ applications: [] }});
                  }}
                  throw new Error('Unhandled path ' + path);
                }}

                var BO_AUTH_USER = CONFIG.authUser || {{ role: 'co' }};

                document.getElementById('detail-change-management');
                document.getElementById('cm-request-detail-content');
                document.getElementById('cm-request-detail-modal');
                document.getElementById('cm-request-status-filter').value = '';
                document.getElementById('cm-request-materiality-filter').value = '';
                document.getElementById('cm-alert-status-filter').value = '';
                document.getElementById('cm-workspace-dashboard');
                document.getElementById('cm-work-queue-tbody');
                document.getElementById('cm-work-queue-empty').style.display = 'none';
                """
            ),
            region,
            textwrap.dedent(
                f"""
                (async function() {{
                  {action_js}
                }})().catch(err => {{
                  console.error(err);
                  process.exit(1);
                }});
                """
            ),
        ]
    )


def _run_node(script):
    assert shutil.which("node"), "Node.js is required for change-management runtime tests"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=os.path.dirname(BACKOFFICE_PATH),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


class TestChangeManagementDiffRuntime:
    def test_application_detail_panel_renders_collapsed_summary_for_informational_request(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "requestList": [],
                    "requestDetail": {},
                },
                """
                renderApplicationChangeManagementPanel({
                  changeRequests: [{
                    id: 'CR-TEST-001',
                    materiality: 'tier3',
                    status: 'approved',
                    source: 'portal_client',
                    created_at: '2026-05-30 10:00:00',
                    changed_fields_count: 1,
                    preview_items: [{
                      field_name: 'company_name',
                      old_value: 'Old Name Ltd',
                      new_value: 'New Name Ltd',
                      materiality: 'tier3'
                    }]
                  }]
                });
                console.log(JSON.stringify({
                  html: document.getElementById('detail-change-management').innerHTML
                }));
                """,
            )
        )
        assert '1 linked request' in result["html"]
        assert 'Tier 3 — Administrative' in result["html"]
        assert 'Approved' in result["html"]
        assert '1 field changed' in result["html"]
        assert 'Expand' in result["html"]
        assert 'Open change request' in result["html"]
        assert 'Old Name Ltd' not in result["html"]

    def test_application_detail_panel_hides_when_no_requests_exist(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {},
                """
                renderApplicationChangeManagementPanel({ changeRequests: [] });
                console.log(JSON.stringify({
                  html: document.getElementById('detail-change-management').innerHTML,
                  display: document.getElementById('detail-change-management').style.display || ''
                }));
                """,
            )
        )
        assert result["html"] == ""
        assert result["display"] == "none"

    def test_application_detail_panel_expands_for_active_material_request_and_toggle_works(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "requestDetail": {}
                },
                """
                currentApp = {
                  ref: 'ARF-2026-100455',
                  changeRequests: [{
                    id: 'CR-TEST-EXPAND',
                    materiality: 'tier2',
                    status: 'submitted',
                    changed_fields_count: 2,
                    screening_required: true,
                    risk_review_required: true,
                    preview_items: [{
                      field_name: 'company_name',
                      old_value: null,
                      new_value: 'Portal QA R11 mnyvqzto',
                      materiality: 'tier2'
                    }]
                  }]
                };
                renderApplicationChangeManagementPanel(currentApp);
                const expandedHtml = document.getElementById('detail-change-management').innerHTML;
                toggleApplicationChangeManagementPanel('ARF-2026-100455');
                const collapsedHtml = document.getElementById('detail-change-management').innerHTML;
                console.log(JSON.stringify({
                  expandedHtml,
                  collapsedHtml
                }));
                """,
            )
        )
        assert 'CR-TEST-EXPAND' in result["expandedHtml"]
        assert 'Open full change request' in result["expandedHtml"]
        assert 'Legal name' in result["expandedHtml"]
        assert 'Portal QA R11 mnyvqzto' in result["expandedHtml"]
        assert 'Collapse' in result["expandedHtml"]
        assert 'Portal QA R11 mnyvqzto' not in result["collapsedHtml"]
        assert 'Expand' in result["collapsedHtml"]

    def test_request_detail_renders_readable_before_after_diff(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "requestDetail": {
                        "id": "CR-TEST-002",
                        "application_id": "app-1",
                        "application_ref": "ARF-2026-100455",
                        "company_name": "HighRisk Dual Approval Test Ltd",
                        "source": "portal_client",
                        "materiality": "tier2",
                        "status": "submitted",
                        "changed_fields_count": 1,
                        "risk_review_required": True,
                        "screening_required": True,
                        "items": [
                            {
                                "change_type": "company_details",
                                "field_name": "company_name",
                                "old_value": None,
                                "new_value": "Portal QA R11 mnyvqzto",
                                "materiality": "tier2"
                            }
                        ],
                        "documents": [],
                        "reviews": []
                    }
                },
                """
                await Promise.resolve(viewRequestDetail('CR-TEST-002'));
                await new Promise(resolve => setTimeout(resolve, 0));
                console.log(JSON.stringify({
                  html: document.getElementById('cm-request-detail-content').innerHTML,
                  modalDisplay: document.getElementById('cm-request-detail-modal').style.display || ''
                }));
                """,
            )
        )
        assert 'Old value vs requested new value' in result["html"]
        assert 'Legal name' in result["html"]
        assert 'Unavailable — legacy request' in result["html"]
        assert 'Portal QA R11 mnyvqzto' in result["html"]
        assert 'Tier 2 — Operational' in result["html"]
        assert 'Risk review required' in result["html"]
        assert '<pre' not in result["html"]

    def test_overview_cta_navigates_without_mutation(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "requestList": [
                        {"id": "CR-TEST-003", "status": "submitted"}
                    ],
                    "requestDetail": {
                        "id": "CR-TEST-003",
                        "application_id": "app-1",
                        "source": "portal_client",
                        "materiality": "tier1",
                        "status": "submitted",
                        "changed_fields_count": 1,
                        "preview_items": [],
                        "items": [],
                        "documents": [],
                        "reviews": []
                    }
                },
                """
                openChangeRequestFromApplication('CR-TEST-003');
                await new Promise(resolve => setTimeout(resolve, 0));
                console.log(JSON.stringify({
                  viewCalls,
                  boCalls,
                  modalDisplay: document.getElementById('cm-request-detail-modal').style.display || ''
                }));
                """,
            )
        )
        assert result["viewCalls"] == ["change-mgmt"]
        assert result["modalDisplay"] == ""
        assert all(call["method"] == "GET" for call in result["boCalls"])
