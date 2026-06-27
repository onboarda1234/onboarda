from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_PATH = REPO_ROOT / "arie-portal.html"


def _portal_html():
    return PORTAL_PATH.read_text(encoding="utf-8")


def _extract_js_function(html, function_name):
    marker = f"function {function_name}"
    start = html.index(marker)
    brace = html.index("{", start)
    depth = 0
    for pos in range(brace, len(html)):
        char = html[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[start:pos + 1]
    raise AssertionError(f"Could not extract function {function_name}")


def test_sidebar_refresh_reuses_portal_applications_and_canonical_projection():
    html = _portal_html()
    refresh_body = _extract_js_function(html, "refreshPortalSidebarApplications")
    render_body = _extract_js_function(html, "renderSidebarApps")

    assert "apiCall('GET', '/portal/applications')" in refresh_body
    assert "renderSidebarApps(portalSidebarApplications)" in refresh_body
    assert "getClientPortalStatusLabel(app.status)" in render_body
    assert "getClientPortalStatusClass(app.status)" in render_body
    assert "getPortalStatusProjection(status).badge" in html
    assert "getPortalStatusProjection(status).statusClass" in html


def test_submit_transition_refreshes_sidebar_without_changing_submit_routing():
    html = _portal_html()
    submit_body = _extract_js_function(html, "submitPrescreening")

    assert "routePortalByBackendStatus(submitResp.status" in submit_body
    assert "refreshPortalSidebarApplications({" in submit_body
    assert "status: submitResp.status" in submit_body
    assert "applicationId: currentApplicationId" in submit_body
    assert "submitResp.risk_level" not in submit_body
    assert "showView('risk-scoring')" not in submit_body
    assert "blockNonDraftPrescreeningSubmit()" in submit_body


def test_pricing_accept_transition_refreshes_sidebar_without_changing_pricing_flow():
    html = _portal_html()
    accept_body = _extract_js_function(html, "acceptPricing")
    route_body = _extract_js_function(html, "routePortalByBackendStatus")

    assert "routePortalByBackendStatus(result.status" in accept_body
    assert "refreshPortalSidebarApplications({" in accept_body
    assert "status: result.status" in accept_body
    assert "result.risk_level" not in accept_body
    assert "computedRiskLevel" not in accept_body
    assert "nextStatus === 'kyc_documents'" in route_body
    assert "continueToKYCDocuments" in route_body


def test_kyc_upload_and_kyc_submit_refresh_sidebar_without_altering_upload_flow():
    html = _portal_html()
    upload_body = _extract_js_function(html, "handleUpload")
    submit_body = _extract_js_function(html, "finalSubmitFromReview")

    assert "apiCall('POST', '/applications/' + currentApplicationId + '/documents?doc_type='" in upload_body
    assert "refreshPortalSidebarApplications({" in upload_body
    assert "status: currentApplicationStatus || 'kyc_documents'" in upload_body
    assert "verification_status_label" in upload_body

    assert "apiCall('POST', '/applications/' + currentApplicationId + '/submit-kyc')" in submit_body
    assert "currentApplicationStatus = 'kyc_submitted'" in submit_body
    assert "refreshPortalSidebarApplications({" in submit_body
    assert "status: currentApplicationStatus" in submit_body
    assert "showView('docs-review')" in submit_body


def test_reload_and_open_existing_application_reconcile_sidebar_with_backend_status():
    html = _portal_html()
    show_view_body = _extract_js_function(html, "showView")
    load_body = _extract_js_function(html, "loadMyApplications")
    resume_body = _extract_js_function(html, "resumeApplication")

    assert "loadMyApplications();" in show_view_body
    assert "var resp = await apiCall('GET', '/portal/applications');" in load_body
    assert "portalSidebarApplications = apps.slice();" in load_body
    assert "var app = await apiCall('GET', '/applications/' + encodeURIComponent(ref));" in resume_body
    assert "currentApplicationStatus = normalizePortalStatus(app.status || stage || 'draft')" in resume_body
    assert "status: currentApplicationStatus" in resume_body
    assert "showToast('success', 'Draft Restored'" in resume_body
    assert "restoredFields && getCurrentPortalProjection().allowsDraftRestore" in resume_body


def test_sidebar_refresh_is_scoped_to_current_application_only():
    html = _portal_html()
    match_body = _extract_js_function(html, "portalAppIdentifiersMatch")
    patch_body = _extract_js_function(html, "patchPortalSidebarApplicationStatus")
    refresh_body = _extract_js_function(html, "refreshPortalSidebarApplications")

    assert "[app.id, app.ref].some" in match_body
    assert "if (!portalAppIdentifiersMatch(app, target)) return app;" in patch_body
    assert "return Object.assign({}, app, patch || {}, { status: normalizedStatus });" in patch_body
    assert "if (!portalAppIdentifiersMatch(app, applicationId)) return app;" in refresh_body
    assert "return Object.assign({}, app, patch, { status: normalizePortalStatus(status) });" in refresh_body


def test_sidebar_refresh_regression_no_draft_restore_or_invalid_submit_cta_for_non_draft():
    html = _portal_html()
    refresh_body = _extract_js_function(html, "refreshPortalSidebarApplications")
    submit_visibility_body = _extract_js_function(html, "updatePrescreeningSubmitVisibility")
    projection_source = html.split("var PORTAL_STATUS_PROJECTIONS = {", 1)[1].split("};", 1)[0]

    assert "Draft Restored" not in refresh_body
    assert "Submit Initial Review" not in refresh_body
    assert "projection.showSubmitInitialReview === true" in submit_visibility_body
    assert "pricing_review:" in projection_source
    assert "kyc_documents:" in projection_source
    assert "showSubmitInitialReview: false" in projection_source.split("pricing_review:", 1)[1].split("pricing_accepted:", 1)[0]
    assert "showSubmitInitialReview: false" in projection_source.split("kyc_documents:", 1)[1].split("kyc_submitted:", 1)[0]
