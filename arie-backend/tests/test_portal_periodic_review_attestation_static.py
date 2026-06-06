from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_HTML = REPO_ROOT / "arie-portal.html"
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"


def _function_region(html: str, name: str, next_name: str) -> str:
    start = html.index(f"function {name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_dashboard_uses_client_safe_portal_applications_endpoint_and_task_surface():
    html = PORTAL_HTML.read_text()
    load_region = _function_region(html, "loadMyApplications", "generateRef")
    assert "apiCall('GET', '/portal/applications')" in load_region
    assert "renderPeriodicReviewTasks(" in load_region
    assert "application_ref: app.ref" in load_region
    assert "application_status: app.status" in load_region
    assert "app.risk_level" not in load_region
    assert "riskColor" not in load_region


def test_portal_periodic_review_modal_wires_fetch_save_submit_and_read_only_copy():
    html = PORTAL_HTML.read_text()
    lower = html.lower()
    assert 'id="periodic-review-tasks-container"' in html
    assert 'id="periodic-review-modal"' in html
    assert "function openPeriodicReviewTask(applicationId)" in html
    assert "/periodic-review/save-draft" in html
    assert "/periodic-review/submit" in html
    assert "This is not full onboarding." in html
    assert "This attestation has been submitted and is now read-only." in html
    assert "supporting documents may be requested separately" in lower
    assert "Thank you. Based on the changes declared, additional documents may be required." in html


def test_backoffice_lifecycle_surfaces_read_only_client_attestation_summary():
    html = BACKOFFICE_HTML.read_text()
    assert "function renderLifecycleClientAttestationPanel(reviewDetail, reviewProjection)" in html
    assert "Client Attestation Summary" in html
    assert "read-only in back office" in html
    assert "Highlighted answers indicate declared material changes or incorrect company/contact details." in html
    assert "Declared material-change keys:" in html
