from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE = ROOT / "arie-backoffice.html"


def _html():
    return BACKOFFICE.read_text(encoding="utf-8")


def test_risk_reassessment_panel_is_visible_to_officer_workspace():
    html = _html()
    assert "Risk Reassessment & Memo Addendum" in html
    assert "renderPeriodicReviewWorkspaceRiskReassessment" in html
    assert "Officer-confirmed risk decision" in html
    assert "Risk reassessment rationale" in html
    assert "Senior review required for this risk reassessment" in html
    assert "Generate memo addendum" in html
    assert "View memo addendum" in html
    assert "Finalize addendum" in html


def test_risk_reassessment_uses_authoritative_periodic_review_endpoints():
    html = _html()
    assert "/monitoring/reviews/" in html
    assert "/risk-reassessment" in html
    assert "/periodic-reviews/" in html
    assert "/memo/finalize" in html
    assert "savePeriodicReviewRiskReassessment" in html
    assert "generatePeriodicReviewMemoAddendum" in html
    assert "finalizePeriodicReviewMemoAddendum" in html


def test_backoffice_copy_preserves_human_control_and_no_auto_risk_update():
    html = _html()
    assert "It does not automatically change the application risk rating." in html
    assert "Officer decision required; application risk is not changed automatically." not in html


def test_prs6b_findings_consolidation_is_not_reintroduced():
    html = _html()
    assert "Officer Findings Draft" not in html
    assert "renderPeriodicReviewWorkspaceFindingsDraft" not in html
    assert "savePeriodicReviewWorkspaceFindings" not in html

