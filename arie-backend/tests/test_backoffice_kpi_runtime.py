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

EDD_ROUTED_STATUSES = ["edd_required"]


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


def _export_runtime_js(html, scenario):
    report_start = html.index("var REPORT_EXPORT_FIELD_LIST")
    report_end = html.index("function updateOnboardingLanes", report_start)
    kpi_start = html.index("function exportKPIReport()")
    kpi_end = html.index("\n// ═══════════════════════════════════════════════════════════\n// RISK SCORING", kpi_start)
    return "\n".join([
        textwrap.dedent(
            """
            const fetchCalls = [];
            const clickedDownloads = [];
            const appendedDownloads = [];
            const removedDownloads = [];
            const objectUrls = [];
            const revokedUrls = [];
            const toasts = [];
            let blobCalls = 0;
            let loginShown = false;

            const document = {
              body: {
                appendChild(el) { appendedDownloads.push(el.download || ''); },
                removeChild(el) { removedDownloads.push(el.download || ''); }
              },
              createElement(tag) {
                return {
                  tagName: tag,
                  href: '',
                  download: '',
                  click() { clickedDownloads.push({ href: this.href, download: this.download }); }
                };
              }
            };
            const URL = {
              createObjectURL(blob) {
                objectUrls.push(blob);
                return 'blob://download-' + objectUrls.length;
              },
              revokeObjectURL(url) { revokedUrls.push(url); }
            };

            var BO_API_BASE = '/api';
            var BO_AUTH_TOKEN = 'runtime-token';
            function showToast(message, type) { toasts.push({ message, type }); }
            function showLoginScreen() { loginShown = true; }
            function getReportFilters() { return { jurisdiction: 'Mauritius', risk_level: '' }; }

            function makeCsvResponse(recordCount) {
              return {
                status: 200,
                ok: true,
                headers: {
                  get(name) {
                    const headers = {
                      'X-Report-Record-Count': recordCount,
                      'Content-Disposition': 'attachment; filename=\"regmind_applications_report_2026-05-06.csv\"'
                    };
                    return headers[name] || '';
                  }
                },
                async blob() {
                  blobCalls += 1;
                  return 'csv-blob-' + blobCalls;
                },
                async json() { return {}; }
              };
            }
            """
        ),
        html[report_start:report_end],
        html[kpi_start:kpi_end],
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
            "setDashboardStatusContract({ pending_statuses: __PENDING__, edd_routed_statuses: __EDD__, canonical_view: 'applications_report_v1' });",
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
        ]).replace("__PENDING__", json.dumps(PENDING_STATUSES)).replace("__EDD__", json.dumps(EDD_ROUTED_STATUSES))

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
            "setDashboardStatusContract({ edd_routed_statuses: __EDD__ });",
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
        ]).replace("__EDD__", json.dumps(EDD_ROUTED_STATUSES))

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

    def test_report_and_kpi_exports_download_server_csv_blobs(self):
        html = _read_backoffice()
        scenario = textwrap.dedent(
            """
            globalThis.fetch = async function(url, opts) {
              fetchCalls.push({ url, headers: opts.headers || {} });
              return makeCsvResponse('22');
            };

            exportReportsCSV();
            exportKPIReport();

            setTimeout(function() {
              console.log(JSON.stringify({
                fetchCalls,
                clickedDownloads,
                appendedDownloads,
                removedDownloads,
                objectUrls,
                revokedUrls,
                toasts,
                blobCalls,
                loginShown
              }));
            }, 0);
            """
        )

        result = _run_node(_export_runtime_js(html, scenario))

        assert result["blobCalls"] == 2
        assert result["loginShown"] is False
        assert len(result["fetchCalls"]) == 2
        report_call, kpi_call = result["fetchCalls"]
        assert report_call["url"].startswith("/api/reports/generate?format=csv&fields=")
        assert "risk_level,risk_score,sector" in report_call["url"]
        assert "jurisdiction=Mauritius" in report_call["url"]
        assert kpi_call["url"].startswith("/api/reports/generate?format=csv&fields=")
        assert "risk_level,risk_score,sector" in kpi_call["url"]
        assert "jurisdiction=Mauritius" not in kpi_call["url"]
        assert report_call["headers"]["Authorization"] == "Bearer runtime-token"
        assert kpi_call["headers"]["Authorization"] == "Bearer runtime-token"
        assert result["clickedDownloads"] == [
            {"href": "blob://download-1", "download": "regmind_applications_report_2026-05-06.csv"},
            {"href": "blob://download-2", "download": "regmind_applications_report_2026-05-06.csv"},
        ]
        assert result["appendedDownloads"] == [
            "regmind_applications_report_2026-05-06.csv",
            "regmind_applications_report_2026-05-06.csv",
        ]
        assert result["removedDownloads"] == result["appendedDownloads"]
        assert result["revokedUrls"] == ["blob://download-1", "blob://download-2"]
        assert result["toasts"] == [
            {"message": "📥 Report exported: 22 records", "type": "success"},
            {"message": "📥 KPI report exported: 22 records", "type": "success"},
        ]

    def test_kpi_export_zero_record_response_does_not_download_blob(self):
        html = _read_backoffice()
        scenario = textwrap.dedent(
            """
            globalThis.fetch = async function(url, opts) {
              fetchCalls.push({ url, headers: opts.headers || {} });
              return makeCsvResponse('0');
            };

            exportKPIReport();

            setTimeout(function() {
              console.log(JSON.stringify({
                fetchCalls,
                clickedDownloads,
                toasts,
                blobCalls
              }));
            }, 0);
            """
        )

        result = _run_node(_export_runtime_js(html, scenario))

        assert result["blobCalls"] == 0
        assert result["clickedDownloads"] == []
        assert len(result["fetchCalls"]) == 1
        assert result["fetchCalls"][0]["url"].startswith("/api/reports/generate?format=csv&fields=")
        assert result["toasts"] == [{"message": "No data to export", "type": "warning"}]
