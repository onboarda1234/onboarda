"""
Runtime checks for canonical back-office dashboard metrics.

These tests execute the real dashboard rendering helpers with a DOM shim and
prove the audited cards are rendered from the backend dashboard payload rather
than silently recomputed from APPLICATIONS / EDD_CASES in the browser.
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


def _dashboard_runtime_js(html, scenario):
    helpers_start = html.index("function normalizeStatusKey(status)")
    helpers_end = html.index("function updateDashboardMonitoringStats()", helpers_start)
    lanes_start = html.index("function updateOnboardingLanes()")
    lanes_end = html.index("\nfunction getEnhancedReviewSummary", lanes_start)
    return "\n".join([
        html[helpers_start:helpers_end],
        textwrap.dedent(
            """
            const elements = {};
            const document = {
              getElementById(id) {
                if (!elements[id]) elements[id] = { textContent: '', innerHTML: '', style: {} };
                return elements[id];
              }
            };
            var APPLICATIONS = [];
            var EDD_CASES = [];
            function updateDashboardMonitoringStats() {}
            """
        ),
        html[lanes_start:lanes_end],
        scenario,
    ])


def _run_node(script):
    assert shutil.which("node"), "Node.js is required for back-office dashboard runtime tests"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=os.path.dirname(BACKOFFICE_PATH),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


class TestBackofficeDashboardRuntime:
    def test_dashboard_cards_render_backend_metrics_not_local_fallbacks(self):
        html = _read_backoffice()
        scenario = textwrap.dedent(
            """
            APPLICATIONS = [{ status: 'approved', date: '2026-01-01', risk: 'LOW' }];
            EDD_CASES = [{ stage: 'triggered' }];
            setDashboardData({
              canonical_view: 'dashboard_metrics_v2',
              metrics: {
                total_applications: { value: 42, kind: 'applications' },
                in_progress_applications: { value: 9, kind: 'applications' },
                approved_this_month: { value: 7, kind: 'applications', timestamp_field: 'decided_at' },
                rejected_declined: { value: 3, kind: 'applications' },
                edd_in_progress: { value: 11, kind: 'edd_cases', pending_senior_review: 2 },
                avg_processing_time: { available: true, display: '4.2 h', sample_size: 10 }
              },
              risk_distribution: { LOW: 8, MEDIUM: 12, HIGH: 9, VERY_HIGH: 5, UNKNOWN: 8, total: 42 },
              lane_distribution: { fast_lane: 6, standard_review: 14, enhanced_due_diligence: 18, unknown: 4, total: 42 }
            });
            updateDashboardStats();
            updateOnboardingLanes();
            console.log(JSON.stringify({
              total: document.getElementById('dash-stat-total').textContent,
              inProgress: document.getElementById('dash-stat-early-stage').textContent,
              approved: document.getElementById('dash-stat-approved').textContent,
              rejected: document.getElementById('dash-stat-rejected').textContent,
              edd: document.getElementById('dash-stat-edd').textContent,
              avgTime: document.getElementById('dash-stat-avgtime').textContent,
              eddNote: document.getElementById('dash-stat-edd-change').textContent,
              approvedNote: document.getElementById('dash-stat-approved-change').textContent,
              riskUnknown: document.getElementById('dash-risk-unknown').textContent,
              laneUnknownCount: document.getElementById('lane-unknown-count').textContent,
              laneUnknownWidth: document.getElementById('lane-unknown-bar').style.width
            }));
            """
        )
        result = _run_node(_dashboard_runtime_js(html, scenario))

        assert result == {
            "total": "42",
            "inProgress": "9",
            "approved": "7",
            "rejected": "3",
            "edd": "11",
            "avgTime": "4.2 h",
            "eddNote": "Active EDD case count",
            "approvedNote": "Approved by decided date this month",
            "riskUnknown": "8 (19%)",
            "laneUnknownCount": 4,
            "laneUnknownWidth": "10%",
        }

    def test_dashboard_labels_are_truthful_for_cases_and_unknown_lane_bucket(self):
        html = _read_backoffice()
        assert "Active EDD Cases" in html
        assert "Unset / Unknown" in html
