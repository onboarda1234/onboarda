from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs" / "qa" / "PR13-full-lifecycle-e2e-validation.md"


def test_pr13_full_lifecycle_e2e_report_records_required_evidence():
    text = REPORT.read_text(encoding="utf-8")

    for expected in [
        "33ae6e3371dc3a253df3ecc14180560a1c3eb83f",
        "regmind-staging:301",
        "/api/version",
        "/api/health",
        "/api/liveness",
        "review `27`",
        "application `e6d43e0424fd4d51`",
        "EDD case `217`",
        "memo_status=generated",
        "periodic_review_memo_id=10",
        "decision=null",
        "/tmp/pr13-e2e-final-report.json",
        "/tmp/pr13-browser-smoke/report.json",
        "Blocking console errors: 0",
        "Unexpected API responses: 0",
    ]:
        assert expected in text


def test_pr13_full_lifecycle_e2e_report_confirms_audit_and_no_blockers():
    text = REPORT.read_text(encoding="utf-8")

    for action in [
        "periodic_review.legacy_import_saved",
        "periodic_review.assignment_updated",
        "periodic_review.state_changed",
        "periodic_review.material_change_attested",
        "periodic_review.risk_rerated",
        "periodic_review.evidence_link_added",
        "periodic_review.escalated_to_edd",
        "periodic_review.officer_rationale_saved",
        "periodic_review.outcome_recorded",
    ]:
        assert action in text

    assert "No P0/P1/P2 defects were found" in text
    assert "Production blocker:" in text
    assert "None." in text


def test_pr13_full_lifecycle_e2e_report_does_not_store_credentials():
    text = REPORT.read_text(encoding="utf-8")

    assert "StagingQa2026" not in text
    assert "m.dubois@ariefinance.mu" not in text
    assert "Authorization: Bearer" not in text
