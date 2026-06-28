import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
SERVER_PY = ROOT / "arie-backend" / "server.py"


def _canonical_final_approval_anchor(app):
    if str(app.get("status") or "").strip().lower() != "approved":
        return None
    return app.get("decided_at") or None


def _date_only(value):
    return str(value or "")[:10] if value else None


def _is_fixture_like(app):
    text = " ".join(
        str(app.get(key) or "")
        for key in ("ref", "company_name", "email", "source")
    ).lower()
    return bool(app.get("is_fixture")) or any(
        marker in text
        for marker in (
            "fixture",
            "smoke",
            "e2e",
            "prs-a1",
            "test",
            "@example.test",
        )
    )


def test_canonical_contract_uses_decided_at_not_first_approved_at():
    app = {
        "status": "approved",
        "risk_level": "HIGH",
        "first_approved_at": "2026-06-01T09:00:00Z",
        "decided_at": "2026-06-25T16:15:17Z",
    }

    assert _canonical_final_approval_anchor(app) == "2026-06-25T16:15:17Z"

    from periodic_review_policy import add_months

    assert add_months(_canonical_final_approval_anchor(app), 12) == "2027-06-25"
    assert add_months(app["first_approved_at"], 12) == "2027-06-01"


def test_initial_enrollment_payload_uses_final_decision_timestamp_when_supplied():
    from monitoring_enrollment import _review_payload

    app = {
        "id": "app-canonical-anchor",
        "status": "approved",
        "risk_level": "HIGH",
        "final_risk_level": "HIGH",
        "first_approved_at": "2026-06-01T09:00:00Z",
        "decided_at": "2026-06-25T16:15:17Z",
    }

    payload = _review_payload(
        app,
        previous_status="compliance_review",
        approved_at=app["decided_at"],
        review_cycle_number=1,
    )

    assert payload["frequency_months"] == 12
    assert payload["due_date"] == "2027-06-25"
    assert payload["next_review_date"] == "2027-06-25"
    assert payload["due_date"] != "2027-06-01"


def test_missing_decided_at_is_not_rule_verifiable_even_when_fallback_dates_exist():
    app = {
        "status": "approved",
        "risk_level": "MEDIUM",
        "first_approved_at": "2026-06-01T09:00:00Z",
        "completed_at": "2026-06-10T09:00:00Z",
        "created_at": "2026-05-15T09:00:00Z",
        "periodic_review_next_review_due": "2028-06-01",
    }

    assert _canonical_final_approval_anchor(app) is None
    assert app["periodic_review_next_review_due"] == "2028-06-01"
    assert _date_only(app["first_approved_at"]) == "2026-06-01"


@pytest.mark.parametrize("status", ["draft", "kyc_documents", "kyc_submitted", "submitted_to_compliance"])
def test_non_approved_applications_do_not_have_approval_anchor(status):
    app = {
        "status": status,
        "decided_at": "2026-06-25T16:15:17Z",
        "risk_level": "MEDIUM",
    }

    assert _canonical_final_approval_anchor(app) is None


@pytest.mark.parametrize("status", ["rejected", "declined", "cancelled", "withdrawn"])
def test_rejected_or_cancelled_applications_have_no_scheduled_review_anchor(status):
    app = {
        "status": status,
        "decided_at": "2026-06-25T16:15:17Z",
        "risk_level": "MEDIUM",
    }

    assert _canonical_final_approval_anchor(app) is None


def test_periodic_review_due_date_is_derived_output_not_approval_anchor():
    app = {
        "status": "approved",
        "decided_at": None,
        "risk_level": "MEDIUM",
    }
    periodic_review = {
        "created_at": "2026-06-26T09:23:14Z",
        "due_date": "2026-12-31",
        "next_review_date": "2026-12-31",
    }

    assert _canonical_final_approval_anchor(app) is None
    assert periodic_review["due_date"] == periodic_review["next_review_date"]
    assert _date_only(periodic_review["created_at"]) == "2026-06-26"


def test_fixture_missing_anchor_is_classified_as_internal_gap_not_pilot_anchor_failure():
    app = {
        "status": "approved",
        "ref": "ARF-PRS-A1-TERM-20260626092314_3e86ff",
        "company_name": "PRS A1 Terminal Smoke Ltd",
        "risk_level": "MEDIUM",
        "decided_at": None,
        "periodic_review_due_values": ["2026-12-31"],
    }

    assert _is_fixture_like(app) is True
    assert _canonical_final_approval_anchor(app) is None
    assert app["periodic_review_due_values"] == ["2026-12-31"]


def test_application_detail_serializer_exposes_fields_needed_for_reconciliation():
    source = SERVER_PY.read_text()
    detail_start = source.index("class ApplicationDetailHandler")
    detail_end = source.index("class ApplicationDecisionHandler")
    detail_source = source[detail_start:detail_end]

    assert "SELECT * FROM applications WHERE id = ? OR ref = ?" in detail_source
    assert "result = dict(app)" in detail_source
    assert '"periodic_review_baseline"' in detail_source
    assert '"periodic_review_baseline_eligibility"' in detail_source
    assert '"periodic_reviews"' in detail_source
    assert '"periodic_review"' in detail_source
    assert '"decided_at"' in source
    assert '"first_approved_at"' in source
    assert '"periodic_review_next_review_due"' in source


def test_pr605_frontend_display_still_preserves_backend_due_for_null_anchor():
    assert shutil.which("node"), "Node.js is required for back-office baseline preview checks"

    js = r"""
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('function periodicReviewBaselineAddMonths');
const end = html.indexOf('function renderOverviewPeriodicReviewBaseline');
if (start < 0 || end < 0 || end <= start) {
  throw new Error('Could not locate periodic review baseline preview functions');
}
const context = { console, Date, Number, String, window: {}, elements: {} };
context.document = { getElementById(id) { return context.elements[id] || null; } };
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
const id = 'app-1';
context.window._overviewPeriodicReviewBaselineDetail = {
  status: 'approved',
  risk_level: 'MEDIUM',
  periodic_review_baseline: {
    legacy_file: 'no',
    anchor_date: null,
    next_review_due: '2026-12-31',
    derived_cadence_months: 6
  }
};
context.elements = {
  ['overview-periodic-review-baseline-legacy-' + id]: { value: 'no' },
  ['overview-periodic-review-baseline-last-review-' + id]: { value: '' },
};
const result = context.periodicReviewBaselinePreview(id);
console.log(JSON.stringify(result));
"""

    result = subprocess.run(
        ["node", "-e", js, str(BACKOFFICE_HTML)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    actual = json.loads(result.stdout)
    assert actual["value"] == "2026-12-31"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known design gap from PRS-APPROVAL-ANCHOR-UNIFICATION-DESIGN-1: "
        "Application detail baseline fallback still prefers first_approved_at over decided_at."
    ),
)
def test_application_detail_baseline_helper_should_prefer_decided_at_over_first_approved_at():
    from server import _application_periodic_review_baseline_source

    app = {
        "id": "app-baseline-gap",
        "status": "approved",
        "risk_level": "HIGH",
        "final_risk_level": "HIGH",
        "first_approved_at": "2026-06-01T09:00:00Z",
        "decided_at": "2026-06-25T16:15:17Z",
    }

    baseline = _application_periodic_review_baseline_source(app)

    assert baseline["baseline_date"] == "2026-06-25"
    assert baseline["due_date"] == "2027-06-25"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known design gap from PRS-APPROVAL-ANCHOR-UNIFICATION-DESIGN-1: "
        "frontend fallback still checks first_approved_at before decided_at."
    ),
)
def test_frontend_fallback_preview_should_prefer_decided_at_over_first_approved_at():
    assert shutil.which("node"), "Node.js is required for back-office baseline preview checks"

    js = r"""
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('function periodicReviewBaselineAddMonths');
const end = html.indexOf('function renderOverviewPeriodicReviewBaseline');
if (start < 0 || end < 0 || end <= start) {
  throw new Error('Could not locate periodic review baseline preview functions');
}
const context = { console, Date, Number, String, window: {}, elements: {} };
context.document = { getElementById(id) { return context.elements[id] || null; } };
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
const id = 'app-1';
context.window._overviewPeriodicReviewBaselineDetail = {
  status: 'approved',
  risk_level: 'HIGH',
  first_approved_at: '2026-06-01T09:00:00Z',
  decided_at: '2026-06-25T16:15:17Z',
  periodic_review_baseline: {
    legacy_file: 'no',
    next_review_due: '',
    derived_cadence_months: 12
  }
};
context.elements = {
  ['overview-periodic-review-baseline-legacy-' + id]: { value: 'no' },
  ['overview-periodic-review-baseline-last-review-' + id]: { value: '' },
};
const result = context.periodicReviewBaselinePreview(id);
console.log(JSON.stringify(result));
"""

    result = subprocess.run(
        ["node", "-e", js, str(BACKOFFICE_HTML)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    actual = json.loads(result.stdout)
    assert actual["value"] == "2027-06-25"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known design gap from PRS-APPROVAL-ANCHOR-UNIFICATION-DESIGN-1: "
        "next-cycle scheduling helper still prefers first_approved_at over decided_at."
    ),
)
def test_next_cycle_anniversary_helper_should_prefer_decided_at_over_first_approved_at():
    from periodic_review_engine import _approval_anniversary_anchor

    app = {
        "status": "approved",
        "first_approved_at": "2026-06-01T09:00:00Z",
        "decided_at": "2026-06-25T16:15:17Z",
    }
    review = {"created_at": "2026-07-01T00:00:00Z"}

    assert _approval_anniversary_anchor(app, review) == "2026-06-25"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known design gap from PRS-APPROVAL-ANCHOR-UNIFICATION-DESIGN-1: "
        "management baseline helper still prefers first_approved_at/completed_at over decided_at."
    ),
)
def test_management_baseline_anchor_helper_should_prefer_decided_at():
    from periodic_review_management import _application_approval_anchor

    app = {
        "status": "approved",
        "first_approved_at": "2026-06-01T09:00:00Z",
        "completed_at": "2026-06-10T09:00:00Z",
        "decided_at": "2026-06-25T16:15:17Z",
    }

    assert _application_approval_anchor(app) == "2026-06-25T16:15:17Z"
