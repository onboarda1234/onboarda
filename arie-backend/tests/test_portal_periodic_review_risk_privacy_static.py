from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "arie-portal.html"


def _html():
    return PORTAL.read_text(encoding="utf-8")


def test_prs7_risk_reassessment_is_not_client_visible():
    html = _html()
    forbidden = [
        "Risk Reassessment & Memo Addendum",
        "renderPeriodicReviewWorkspaceRiskReassessment",
        "periodic-review-risk-",
        "/risk-reassessment",
        "Officer-confirmed risk decision",
        "Risk reassessment rationale",
        "Senior review required for this risk reassessment",
        "Generate memo addendum",
        "View memo addendum",
        "Finalize addendum",
        "memo/finalize",
    ]
    for text in forbidden:
        assert text not in html


def test_periodic_review_portal_does_not_expose_officer_memo_or_rationale_copy():
    html = _html()
    assert "periodic_review_memo_addendum" not in html
    assert "officer_confirmed_risk_decision" not in html
    assert "senior_review_required_before_risk_change" not in html
    assert "backoffice_periodic_review_risk_reassessment" not in html

