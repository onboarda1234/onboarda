from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backoffice_reads_backend_document_reliance_summary():
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")

    assert "documentRelianceSummary" in html
    assert "document_evidence_gate" in html
    assert "document evidence blocker(s)" in html
    assert "Document evidence is not reliance-ready." in html


def test_portal_does_not_treat_blocked_reliance_as_verified():
    html = (ROOT / "arie-portal.html").read_text(encoding="utf-8")

    assert "data-reliance-state" in html
    assert "document_reliance_state" in html
    assert "Verification skipped - manual review required" in html
    assert "Blocked from use" in html
