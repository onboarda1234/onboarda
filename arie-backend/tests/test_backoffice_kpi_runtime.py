"""
Runtime checks for back-office KPI rendering.

These tests execute the real JavaScript helpers used by the KPI dashboard with a
small DOM shim. They complement the static HTML tests by asserting rendered card
values for the Day 4/5 status-contract cases.
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

PENDING_STATUSES = [
    "draft",
    "pending",
    "submitted",
    "prescreening_submitted",
    "pre_approval_review",
    "pre_approved",
    "pricing_review",
    "pricing_accepted",
    "in_review",
    "under_review",
    "compliance_review",
    "kyc_submitted",
    "kyc_documents",
    "rmi_sent",
]


def _read_backoffice():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _runtime_js(html, scenario):
    helpers_start = html.index("function normalizeStatusKey(status)")
    helpers_end = html.index(
        "// ═══════════════════════════════════════════════════════════\n"
        "// DATA ARRAYS",
        helpers_start,
    )
    render_start = html.index("function renderKPIDashboard()")
    render_end = html.index("\nfunction buildMonthBars", render_start)
    return "\n".join([
        html[helpers_start:helpers_end],
        textwrap.dedent(
            """
            const elements = {};
            const document = {
              getElementById(id) {
                if (!elements[id]) elements[id] = { value: '', innerHTML: '', textContent: '' };
                return elements[id];
              }
            };
            document.getElementById('kpi-period').value = 'all';
            var APPLICATIONS = [];
            var USERS = [];
            var ROLE_LABELS = {};
            function getPersonScreeningResult() { return null; }

            function app(statusRaw, status) {
              return {
                statusRaw,
                status,
                date: '2026-05-01',
                directors: [],
                ubos: [],
                pepCount: 0,
                risk: 'LOW',
                assignedId: ''
              };
            }

            function extractCard(sectionHtml, label) {
              const marker = '<div class="kpi-card-label">' + label + '</div>';
              const start = sectionHtml.indexOf(marker);
              if (start < 0) return null;
              const region = sectionHtml.slice(start, start + 900);
              const valueMatch = region.match(/<div class="kpi-card-value"[^>]*>(.*?)<\\/div>/);
              const subMatch = region.match(/<div class="kpi-card-sub"[^>]*>(.*?)<\\/div>/);
              return {
                value: valueMatch ? valueMatch[1].replace(/<[^>]+>/g, '').trim() : '',
                sub: subMatch ? subMatch[1].replace(/<[^>]+>/g, '').trim() : ''
              };
            }
            """
        ),
        html[render_start:render_end],
        scenario,
    ])


def _run_node(script):
    assert shutil.which("node"), "Node.js is required for back-office KPI runtime tests"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=os.path.dirname(BACKOFFICE_PATH),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _staging_like_apps_js():
    return textwrap.dedent(
        """
        APPLICATIONS = [
          app('edd_required', 'Enhanced Due Diligence Required'),
          app('draft', 'Application Started'),
          app('draft', 'Application Started'),
          app('draft', 'Application Started'),
          app('in_review', 'Verification Ongoing'),
          app('kyc_submitted', 'KYC Documents Submitted'),
          app('kyc_documents', 'KYC Documents Required'),
          app('kyc_documents', 'KYC Documents Required'),
          app('kyc_documents', 'KYC Documents Required')
        ];
        for (let i = 0; i < 13; i++) {
          APPLICATIONS.push(app('pricing_review', 'Pricing Under Review'));
        }
        """
    )


class TestBackofficeKPIRuntime:
    def test_kpi_cards_render_backend_contract_counts(self):
        html = _read_backoffice()
        scenario = "\n".join([
            _staging_like_apps_js(),
            "setDashboardStatusContract({ pending_statuses: __PENDING__, canonical_view: 'applications_report_v1' });",
            textwrap.dedent(
                """
                renderKPIDashboard();
                const ops = document.getElementById('kpi-section-ops').innerHTML;
                const risk = document.getElementById('kpi-section-risk').innerHTML;
                console.log(JSON.stringify({
                  contractCount: getDashboardPendingStatuses().length,
                  canonicalView: DASHBOARD_STATUS_CONTRACT.canonicalView,
                  inProgress: extractCard(ops, 'In Progress Applications'),
                  edd: extractCard(risk, 'EDD Routing Rate')
                }));
                """
            ),
        ]).replace("__PENDING__", json.dumps(PENDING_STATUSES))

        result = _run_node(_runtime_js(html, scenario))

        assert result["contractCount"] == 14
        assert result["canonicalView"] == "applications_report_v1"
        assert result["inProgress"] == {
            "value": "21",
            "sub": "21 applications in the canonical in-progress bucket for all time",
        }
        assert result["edd"] == {
            "value": "4.5%",
            "sub": "1 applications routed to EDD of 22 total",
        }

    def test_kpi_missing_status_contract_renders_unavailable_not_zero(self):
        html = _read_backoffice()
        scenario = "\n".join([
            _staging_like_apps_js(),
            textwrap.dedent(
                """
                renderKPIDashboard();
                const ops = document.getElementById('kpi-section-ops').innerHTML;
                const risk = document.getElementById('kpi-section-risk').innerHTML;
                console.log(JSON.stringify({
                  contractCount: getDashboardPendingStatuses().length,
                  inProgress: extractCard(ops, 'In Progress Applications'),
                  edd: extractCard(risk, 'EDD Routing Rate')
                }));
                """
            ),
        ])

        result = _run_node(_runtime_js(html, scenario))

        assert result["contractCount"] == 0
        assert result["inProgress"] == {
            "value": "—",
            "sub": "In-progress status contract unavailable; reload dashboard data",
        }
        assert result["edd"] == {
            "value": "4.5%",
            "sub": "1 applications routed to EDD of 22 total",
        }

    def test_pending_helper_uses_runtime_backend_contract(self):
        html = _read_backoffice()
        scenario = textwrap.dedent(
            """
            setDashboardStatusContract({
              pending_statuses: ['Pricing Review', 'pricing-review', '', 'pricing_review'],
              canonical_view: 'applications_report_v1'
            });
            console.log(JSON.stringify({
              statuses: getDashboardPendingStatuses(),
              pricing: isDashboardPendingApplication(app('pricing_review', 'Pricing Under Review')),
              draft: isDashboardPendingApplication(app('draft', 'Application Started'))
            }));
            """
        )

        result = _run_node(_runtime_js(html, scenario))

        assert result == {
            "statuses": ["pricing_review"],
            "pricing": True,
            "draft": False,
        }
