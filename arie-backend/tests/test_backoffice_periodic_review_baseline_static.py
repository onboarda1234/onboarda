import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
PORTAL_HTML = ROOT / "arie-portal.html"


NODE_BASELINE_PREVIEW_CASES = r"""
const fs = require('fs');
const vm = require('vm');

const htmlPath = process.argv[1];
const cases = JSON.parse(process.argv[2]);
const html = fs.readFileSync(htmlPath, 'utf8');
const start = html.indexOf('function periodicReviewBaselineAddMonths');
const end = html.indexOf('function renderOverviewPeriodicReviewBaseline');
if (start < 0 || end < 0 || end <= start) {
  throw new Error('Could not locate periodic review baseline preview functions');
}

const context = {
  console,
  Date,
  Number,
  String,
  window: {},
  elements: {},
};
context.document = {
  getElementById(id) {
    return context.elements[id] || null;
  },
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);

function preview(caseDef) {
  const id = 'app-1';
  context.window._overviewPeriodicReviewBaselineDetail = caseDef.detail;
  context.elements = {
    ['overview-periodic-review-baseline-legacy-' + id]: { value: caseDef.legacy || 'no' },
    ['overview-periodic-review-baseline-last-review-' + id]: { value: caseDef.lastReview || '' },
  };
  const result = context.periodicReviewBaselinePreview(id);
  return result.value || result.placeholder || 'Not scheduled yet';
}

const actual = {};
for (const caseDef of cases) {
  actual[caseDef.name] = preview(caseDef);
}
console.log(JSON.stringify(actual));
"""


def test_overview_contains_compact_periodic_review_baseline_box():
    html = BACKOFFICE_HTML.read_text()
    baseline_region = html[
        html.index("function renderOverviewPeriodicReviewBaseline"):
        html.index("async function loadOverviewPeriodicReviewBaseline")
    ]

    assert 'id="detail-periodic-review-baseline"' in html
    assert "Periodic Review Baseline" not in html
    assert "Officer-only setup metadata" not in html
    assert "periodic-baseline-compact" in baseline_region
    assert "periodic-baseline-row" in baseline_region
    assert "periodic-baseline-actions" in baseline_region
    assert "overview-periodic-review-baseline-legacy-" in baseline_region
    assert "overview-periodic-review-baseline-last-review-" in baseline_region
    assert "overview-periodic-review-baseline-next-due-" in baseline_region
    assert "Save baseline" in baseline_region
    assert "Is this a legacy file?" in baseline_region
    assert "['n/a', 'N/A']" in html
    assert "Not applicable - no periodic-review baseline will be scheduled" in html
    assert "Derived cadence" in baseline_region
    assert "Cadence is derived from the current officer-visible risk level." not in baseline_region
    assert "Officer note" not in baseline_region
    assert 'type="hidden" value="' in baseline_region
    assert "/periodic-review-baseline" in html
    assert "Periodic review baseline can be configured after onboarding approval." in html
    assert "No periodic review case is available yet for baseline setup on this application." not in html


def test_overview_periodic_review_baseline_preview_uses_backend_due_date_and_status_copy():
    assert shutil.which("node"), "Node.js is required for back-office baseline preview checks"
    cases = [
        {
            "name": "approved_backend_due_null_anchor",
            "legacy": "no",
            "detail": {
                "status": "approved",
                "risk_level": "MEDIUM",
                "periodic_review_baseline": {
                    "legacy_file": "no",
                    "anchor_date": None,
                    "next_review_due": "2026-12-31",
                    "derived_cadence_months": 6,
                },
            },
        },
        {
            "name": "approved_anchor_no_backend_due",
            "legacy": "no",
            "detail": {
                "status": "approved",
                "risk_level": "HIGH",
                "first_approved_at": "2026-06-30T09:00:00Z",
                "periodic_review_baseline": {
                    "legacy_file": "no",
                    "next_review_due": "",
                    "derived_cadence_months": 12,
                },
            },
        },
        {
            "name": "approved_missing_anchor_no_due",
            "legacy": "no",
            "detail": {
                "status": "approved",
                "risk_level": "MEDIUM",
                "periodic_review_baseline": {"legacy_file": "no"},
            },
        },
        {
            "name": "non_approved_scheduled_after_approval",
            "legacy": "no",
            "detail": {
                "status": "kyc_submitted",
                "risk_level": "MEDIUM",
                "periodic_review_baseline": {"legacy_file": "no"},
            },
        },
        {
            "name": "rejected_no_review_scheduled",
            "legacy": "no",
            "detail": {
                "status": "rejected",
                "risk_level": "MEDIUM",
                "periodic_review_baseline": {"legacy_file": "no"},
            },
        },
        {
            "name": "legacy_valid_date",
            "legacy": "yes",
            "lastReview": "2025-01-15",
            "detail": {
                "status": "approved",
                "risk_level": "HIGH",
                "periodic_review_baseline": {
                    "legacy_file": "yes",
                    "derived_cadence_months": 12,
                },
            },
        },
        {
            "name": "legacy_missing_date",
            "legacy": "yes",
            "detail": {
                "status": "approved",
                "risk_level": "HIGH",
                "periodic_review_baseline": {
                    "legacy_file": "yes",
                    "derived_cadence_months": 12,
                },
            },
        },
    ]
    result = subprocess.run(
        ["node", "-e", NODE_BASELINE_PREVIEW_CASES, str(BACKOFFICE_HTML), json.dumps(cases)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    actual = json.loads(result.stdout)
    assert actual["approved_backend_due_null_anchor"] == "2026-12-31"
    assert actual["approved_anchor_no_backend_due"] == "2027-06-30"
    assert actual["approved_missing_anchor_no_due"] == "Approval date missing - cannot calculate next review due"
    assert actual["non_approved_scheduled_after_approval"] == "Scheduled after onboarding approval"
    assert actual["rejected_no_review_scheduled"] == "No review scheduled"
    assert actual["legacy_valid_date"] == "2026-01-15"
    assert actual["legacy_missing_date"] == "Last review date required"
    assert all("Awaiting onboarding approval" not in value for value in actual.values())


def test_portal_does_not_render_officer_baseline_box():
    html = PORTAL_HTML.read_text()

    assert "Periodic Review Baseline" not in html
    assert "overview-periodic-review-baseline" not in html
