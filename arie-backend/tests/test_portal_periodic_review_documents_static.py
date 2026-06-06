from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_HTML = REPO_ROOT / "arie-portal.html"


def test_portal_periodic_review_modal_surfaces_post_submit_document_request_summary():
    html = PORTAL_HTML.read_text()
    submit_start = html.index("async function submitPeriodicReviewAttestation()")
    submit_end = html.index("async function loadMyApplications()", submit_start)
    submit_section = html[submit_start:submit_end]
    assert "function periodicReviewDocumentRequestSummaryHtml(task)" in html
    assert "Documents required for this Periodic Review" in html
    assert "Only Periodic Review follow-up documents triggered by your answers are shown here." in html
    assert "function uploadPeriodicReviewDocumentRequest(requirementId, inputId, button)" in html
    assert "await refreshPeriodicReviewTask();" in html
    assert "?exclude_periodic_review=1" in html
    assert "await resumeApplication(targetRef, targetStage);" not in submit_section
    assert "showView('onboarding');" not in submit_section
    assert "applyApplicationViewState('onboarding');" not in submit_section
    assert "if (!data.application_ref && taskMeta.application_ref) data.application_ref = taskMeta.application_ref;" in html
