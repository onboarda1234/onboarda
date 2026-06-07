from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"

sys.path.insert(0, str(BACKEND_ROOT))

from periodic_review_projection_service import derive_operational_review_status


def test_officer_findings_draft_editor_is_removed_from_backoffice_workspace():
    html = BACKOFFICE_HTML.read_text()

    assert "Officer Findings Draft" not in html
    assert "Save draft findings" not in html
    assert "renderPeriodicReviewWorkspaceFindingsDraft" not in html
    assert "savePeriodicReviewWorkspaceFindings" not in html
    assert "Periodic Review Decision" in html
    assert "Review findings summary" in html
    assert "Rationale for decision" in html
    assert "Senior review note, if applicable" in html


def test_decision_section_prefills_legacy_draft_fields_without_duplicate_editor():
    html = BACKOFFICE_HTML.read_text()
    start = html.index("function renderPeriodicReviewWorkspaceDecision(reviewDetail)")
    end = html.index("async function completePeriodicReviewDecision(reviewId)", start)
    section = html[start:end]

    assert "decision.findings_summary || reviewDetail.officer_findings_note" in section
    assert "decision.follow_up_notes || reviewDetail.officer_deficiencies_note" in section
    assert "decision.senior_review_note || reviewDetail.officer_internal_review_note" in section


def test_clear_current_review_gates_project_ready_for_decision_without_legacy_draft():
    status = derive_operational_review_status(
        raw_status="in_progress",
        attestation_status="submitted",
        has_missing_documents=False,
        has_documents_pending_review=False,
        blocker_count=0,
        findings_present=False,
    )

    assert status["status_key"] == "ready_for_decision"
    assert status["status_label"] == "Ready for decision"
