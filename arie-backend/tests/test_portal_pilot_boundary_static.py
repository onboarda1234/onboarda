import re
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_PATH = REPO_ROOT / "arie-portal.html"
BACKOFFICE_PATH = REPO_ROOT / "arie-backoffice.html"


CLIENT_VIEW_IDS = (
    "view-pending",
    "view-risk-scoring",
    "view-pre-approval-hold",
    "view-compliance-hold",
    "view-pricing",
    "view-onboarding",
    "view-submission-review",
    "view-docs-review",
    "view-approved",
    "view-client-notifications",
)

WORDING_CLEANUP_PATTERNS = (
    r"Pre-Screening",
    r"bank officer",
    r"Officer review may be required",
)

FORBIDDEN_CLIENT_PATTERNS = (
    r"\bLOW RISK\b",
    r"\bMEDIUM RISK\b",
    r"\bHIGH RISK\b",
    r"\bVERY HIGH RISK\b",
    r"Medium-Low",
    r"Risk Assessment",
    r"Risk Rating",
    r"Next Review",
    r"All Checks Cleared",
    r"Sanctions Screening",
    r"Document Authenticity",
    r"Document Validity",
    r"Composite Risk Score",
    r"AI Transparency",
    r"AI-powered risk scoring",
    r"5-dimension risk scoring",
    r"AI risk scoring engine",
    r"Risk unavailable\s*[\u2014-]\s*recalculation required",
    r"Enhanced Due Diligence",
    r"\bEDD\b",
    r"elevated risk profile",
    r"high risk",
    *WORDING_CLEANUP_PATTERNS,
)


class VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._hidden_stack = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        style = attrs_dict.get("style", "")
        parent_hidden = any(self._hidden_stack)
        if "display:none" in style.replace(" ", "").lower():
            self._hidden_stack.append(True)
        else:
            self._hidden_stack.append(parent_hidden)

    def handle_endtag(self, tag):
        if self._hidden_stack:
            self._hidden_stack.pop()

    def handle_data(self, data):
        if not any(self._hidden_stack):
            text = data.strip()
            if text:
                self.parts.append(text)


def _portal_html():
    return PORTAL_PATH.read_text(encoding="utf-8")


def _visible_text(markup):
    parser = VisibleTextParser()
    parser.feed(markup)
    return " ".join(parser.parts)


def _extract_div_by_id(html, element_id):
    marker = f'id="{element_id}"'
    marker_index = html.index(marker)
    start = html.rfind("<div", 0, marker_index)
    pos = start
    depth = 0
    while pos < len(html):
        next_open = html.find("<div", pos)
        next_close = html.find("</div>", pos)
        if next_close == -1:
            raise AssertionError(f"Could not find end of {element_id}")
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 4
            continue
        depth -= 1
        pos = next_close + len("</div>")
        if depth == 0:
            return html[start:pos]
    raise AssertionError(f"Could not extract {element_id}")


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


def _extract_js_var_object(html, variable_name):
    marker = f"var {variable_name} = {{"
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
    raise AssertionError(f"Could not extract object {variable_name}")


def _extract_js_object_property(object_source, property_name):
    marker = f"  {property_name}: {{"
    start = object_source.index(marker)
    brace = object_source.index("{", start)
    depth = 0
    for pos in range(brace, len(object_source)):
        char = object_source[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return object_source[start:pos + 1]
    raise AssertionError(f"Could not extract object property {property_name}")


def _assert_client_safe_text(text):
    for pattern in FORBIDDEN_CLIENT_PATTERNS:
        assert not re.search(pattern, text, flags=re.IGNORECASE), pattern


def test_client_portal_rendered_views_hide_risk_and_ai_wording():
    html = _portal_html()
    for view_id in CLIENT_VIEW_IDS:
        text = _visible_text(_extract_div_by_id(html, view_id))
        _assert_client_safe_text(text)


def test_approved_state_is_activation_only():
    html = _portal_html()
    approved_markup = _extract_div_by_id(html, "view-approved")
    text = _visible_text(approved_markup)
    _assert_client_safe_text(text)
    assert "Application Approved" in text
    assert "Account Approved" not in text
    assert "Activation is in progress" in text
    assert "No Further Action Required" not in text
    assert "Account status" not in text
    assert "Submitted documents are complete" not in text
    assert "Our team will contact you" in text
    approved_copy = (
        "Your application has been approved. Activation is in progress and our team "
        "will contact you with next steps."
    )
    assert approved_copy in text
    assert "Confirm Receipt" not in text
    assert "Confirm Receipt" not in approved_markup
    assert "ARF-2024-XXXXXX" not in approved_markup


def test_approved_state_reference_uses_real_ref_or_hides_block():
    html = _portal_html()
    approved_markup = _extract_div_by_id(html, "view-approved")
    assert "ARF-2024-XXXXXX" not in html
    assert 'id="approved-ref-wrap" style="display:none;"' in approved_markup
    assert '<div class="ref-number" id="approved-ref"></div>' in approved_markup

    ref_body = _extract_js_function(html, "setPortalReferenceDisplay")
    assert "var value = String(ref || '').trim();" in ref_body
    assert "wrapper.style.display = 'none'" in ref_body
    assert "refEl.textContent = value;" in ref_body
    assert "wrapper.style.display = ''" in ref_body

    approved_ref_body = _extract_js_function(html, "setApprovedApplicationRef")
    assert "setPortalReferenceDisplay('approved-ref', ref, 'approved-ref-wrap')" in approved_ref_body

    view_state_body = _extract_js_function(html, "applyApplicationViewState")
    assert "target === 'approved'" in view_state_body
    assert "setApprovedApplicationRef(appRef)" in view_state_body

    route_body = _extract_js_function(html, "routePortalByBackendStatus")
    approved_branch = route_body.split("nextStatus === 'approved'", 1)[1].split("nextStatus === 'rmi_sent'", 1)[0]
    assert "setApprovedApplicationRef(appRef)" in approved_branch
    assert "textContent = appRef ||" not in approved_branch


def test_approved_state_has_no_fake_receipt_button():
    html = _portal_html()
    approved_markup = _extract_div_by_id(html, "view-approved")
    assert "Confirm Receipt" not in html
    assert "Acknowledge" not in approved_markup
    assert "Welcome aboard!" not in approved_markup


def test_no_recalculation_or_ai_risk_wording_in_portal_bundle():
    html = _portal_html()
    forbidden = (
        "Risk unavailable \u2014 recalculation required",
        "Risk unavailable - recalculation required",
        "AI-powered risk scoring",
        "5-dimension risk scoring",
        "AI risk scoring engine",
        "AI Transparency",
    )
    for phrase in forbidden:
        assert phrase not in html


def test_portal_wording_cleanup_terms_removed_from_visible_client_views():
    html = _portal_html()
    visible_text = []
    for match in re.finditer(r'id="(view-[^"]+)"', html):
        element_id = match.group(1)
        visible_text.append(_visible_text(_extract_div_by_id(html, element_id)))
    combined = " ".join(visible_text)
    for pattern in WORDING_CLEANUP_PATTERNS:
        assert not re.search(pattern, combined, flags=re.IGNORECASE), pattern


def test_portal_status_lookup_uses_client_safe_status_mapping():
    html = _portal_html()
    lookup_body = _extract_js_function(html, "lookupApplication")
    assert "getClientPortalStatusLabel(app.status)" in lookup_body
    assert "app.status_label" not in lookup_body


def test_portal_sanitizes_backend_verification_copy_before_rendering():
    html = _portal_html()
    sanitizer_body = _extract_js_function(html, "sanitizeClientPortalCopy")
    assert "Pre-Screening" in sanitizer_body
    assert "Initial Review" in sanitizer_body
    assert "bank officer" in sanitizer_body
    assert "Officer review may be required" in sanitizer_body
    render_body = _extract_js_function(html, "renderPersistedVerification")
    assert "sanitizeClientPortalCopy(check.message)" in render_body
    assert "sanitizeClientPortalCopy(check.label || check.name || 'Check')" in render_body


def test_initial_review_submit_button_matches_reset_copy():
    html = _portal_html()
    prescreening_markup = _extract_div_by_id(html, "view-prescreening")
    assert "Submit Initial Review" in _visible_text(prescreening_markup)
    assert "Submit Application" not in _visible_text(prescreening_markup)


def test_left_application_badges_use_client_safe_labels():
    html = _portal_html()
    projection_source = _extract_js_var_object(html, "PORTAL_STATUS_PROJECTIONS")
    label_body = _extract_js_function(html, "getClientPortalStatusLabel")
    sidebar_body = _extract_js_function(html, "renderSidebarApps")

    for label in (
        "Documents Required",
        "Submitted",
        "Approved",
        "Declined",
        "Information Required",
        "Under Review",
        "Pricing Review",
        "Enhanced Review Required",
    ):
        assert label in projection_source

    assert "getPortalStatusProjection(status).badge" in label_body
    assert "getClientPortalStatusLabel(app.status)" in sidebar_body
    assert "getClientPortalStatusClass(app.status)" in sidebar_body
    assert "escapeHtml(label)" in sidebar_body
    assert "app.status_label" not in sidebar_body

    forbidden_badges = (
        "Info Req.",
        "Accepted",
        "Ready",
        "Screening",
        "Pricing",
        "Approved \\u2013 Ready for Activation",
        "Further Information Requested",
        "Compliance Review in Progress",
        "Under Compliance Review",
    )
    for label in forbidden_badges:
        assert label not in sidebar_body


def test_portal_status_projection_defines_required_status_contracts():
    html = _portal_html()
    projection_source = _extract_js_var_object(html, "PORTAL_STATUS_PROJECTIONS")

    draft = _extract_js_object_property(projection_source, "draft")
    assert "view: 'prescreening'" in draft
    assert "primaryCta: 'Continue application'" in draft
    assert "allowsDraftRestore: true" in draft
    assert "autosaveAllowed: true" in draft
    assert "prescreeningEditable: true" in draft
    assert "showSubmitInitialReview: true" in draft

    required_non_draft_statuses = (
        "submitted",
        "prescreening_submitted",
        "pricing_review",
        "pricing_accepted",
        "pre_approval_review",
        "pre_approved",
        "kyc_documents",
        "kyc_submitted",
        "compliance_review",
        "submitted_to_compliance",
        "in_review",
        "under_review",
        "edd_required",
        "approved",
        "rejected",
        "rmi_sent",
        "withdrawn",
    )
    for status in required_non_draft_statuses:
        block = _extract_js_object_property(projection_source, status)
        assert "allowsDraftRestore: false" in block, status
        assert "autosaveAllowed: false" in block, status
        assert "prescreeningEditable: false" in block, status
        assert "showSubmitInitialReview: false" in block, status

    assert "view: 'pricing'" in _extract_js_object_property(projection_source, "pricing_review")
    assert "primaryCta: 'Accept pricing'" in _extract_js_object_property(projection_source, "pricing_review")
    assert "view: 'pre-approval-hold'" in _extract_js_object_property(projection_source, "pre_approval_review")
    assert "progressFlow: 'enhanced'" in _extract_js_object_property(projection_source, "pre_approval_review")
    assert "view: 'onboarding'" in _extract_js_object_property(projection_source, "kyc_documents")
    assert "hydrateParties: true" in _extract_js_object_property(projection_source, "kyc_documents")
    assert "view: 'docs-review'" in _extract_js_object_property(projection_source, "kyc_submitted")
    assert "view: 'compliance-hold'" in _extract_js_object_property(projection_source, "submitted_to_compliance")
    assert "view: 'pre-approval-hold'" in _extract_js_object_property(projection_source, "edd_required")
    assert "Enhanced Review Required" in _extract_js_object_property(projection_source, "edd_required")
    assert "primaryCta: 'No resubmission'" in _extract_js_object_property(projection_source, "approved")
    assert "Change Management" in _extract_js_object_property(projection_source, "approved")


def test_portal_projection_drives_progress_editability_and_primary_cta():
    html = _portal_html()
    render_progress = _extract_js_function(html, "renderPortalProgress")
    apply_body = _extract_js_function(html, "applyPortalProjectionToView")
    editability_body = _extract_js_function(html, "updatePrescreeningEditability")
    dashboard_body = _extract_js_function(html, "loadMyApplications")

    assert "progressFlow === 'enhanced'" in render_progress
    assert "Pre-Approval" in render_progress
    assert "Compliance Review" in render_progress
    assert "Decision" in render_progress
    assert "steps.innerHTML = renderPortalProgress(projection)" in apply_body
    assert "updatePrescreeningEditability(projection)" in apply_body
    assert "updatePrescreeningSubmitVisibility(projection)" in apply_body
    assert "projection.prescreeningEditable === true" in editability_body
    assert "el.disabled = !editable" in editability_body
    assert "var primaryCta = projection.primaryCta || 'Open';" in dashboard_body
    assert "escapeHtml(primaryCta)" in dashboard_body


def test_portal_static_progress_trackers_match_canonical_flow_labels():
    html = _portal_html()
    for view_id in ("view-pending", "view-risk-scoring", "view-pricing", "view-onboarding", "view-docs-review", "view-compliance-hold"):
        markup = _extract_div_by_id(html, view_id)
        assert "Pricing" in markup, view_id
        assert "KYC & Documents" in markup, view_id
        assert "Compliance Review" in markup, view_id
        assert "Decision" in markup, view_id
        assert "Application Review" not in _visible_text(markup).split("Application Timeline")[0], view_id
        assert "Approved" not in _visible_text(markup).split("Application Timeline")[0], view_id

    pre_approval_markup = _extract_div_by_id(html, "view-pre-approval-hold")
    assert "Pre-Approval" in pre_approval_markup
    assert "Compliance Review" in pre_approval_markup
    assert "Decision" in pre_approval_markup


def test_pricing_acceptance_routes_by_backend_status_not_frontend_risk():
    html = _portal_html()
    accept_body = _extract_js_function(html, "acceptPricing")
    assert "routePortalByBackendStatus(result.status" in accept_body
    assert "result.risk_level" not in accept_body
    assert "computedRiskLevel" not in accept_body

    route_body = _extract_js_function(html, "routePortalByBackendStatus")
    assert "nextStatus === 'pre_approval_review'" in route_body
    assert "showPreApprovalHold()" in route_body
    assert "nextStatus === 'kyc_documents'" in route_body
    assert "continueToKYCDocuments" in route_body
    assert "nextStatus === 'approved'" in route_body
    assert "showView('approved')" in route_body
    assert "nextStatus === 'rmi_sent'" in route_body
    assert "showView('client-notifications')" in route_body

    pre_approval_branch = (
        route_body
        .split("nextStatus === 'pre_approval_review'", 1)[1]
        .split("nextStatus === 'kyc_documents'", 1)[0]
    )
    assert "showPreApprovalHold()" in pre_approval_branch
    assert "continueToKYCDocuments" not in pre_approval_branch
    assert "showView('onboarding')" not in pre_approval_branch


def test_prescreening_submit_routes_by_backend_status():
    html = _portal_html()
    body = _extract_js_function(html, "submitPrescreening")
    consent_wrapper = html.split("submitPrescreening = function(e) {", 1)[1].split("var required = ", 1)[0]
    assert body.index("!getCurrentPortalProjection().showSubmitInitialReview") < body.index("apiCall('PUT'")
    assert "blockNonDraftPrescreeningSubmit()" in body
    assert "!getCurrentPortalProjection().showSubmitInitialReview" in consent_wrapper
    assert "blockNonDraftPrescreeningSubmit()" in consent_wrapper
    assert "routePortalByBackendStatus(submitResp.status" in body
    assert "submitResp.risk_level" not in body
    assert "showView('risk-scoring')" not in body


def test_portal_resume_and_save_resume_are_draft_only():
    html = _portal_html()
    resume_body = _extract_js_function(html, "resumeApplication")
    cta_body = _extract_js_function(html, "renderResumeCTA")
    save_body = _extract_js_function(html, "saveDraft")
    autosave_body = _extract_js_function(html, "startAutoSave")

    assert "if (projection.allowsDraftRestore)" in resume_body
    assert "apiCall('GET', '/save-resume?application_id='" in resume_body
    assert resume_body.index("if (projection.allowsDraftRestore)") < resume_body.index("apiCall('GET', '/save-resume?application_id='")
    assert "projection.allowsDraftRestore || projection.hydrateParties === true" in resume_body
    assert "restoredFields && getCurrentPortalProjection().allowsDraftRestore" in resume_body
    assert "showToast('success', 'Draft Restored'" in resume_body
    assert "showToast('info', 'Application Loaded'" in resume_body

    assert "var draftApps = (apps || []).filter(function(a) {" in cta_body
    assert "var draftSessions = (activeDrafts || []).filter(function(a) {" in cta_body
    assert "allowsDraftRestore === true" in cta_body
    assert "app = drafts.length ? drafts[0] : inProgress[0];" not in cta_body
    assert "Draft ready to continue" in cta_body

    assert "!getCurrentPortalProjection().autosaveAllowed" in save_body
    assert "!getCurrentPortalProjection().autosaveAllowed" in autosave_body


def test_person_document_polling_rejects_invalid_person_identifiers():
    html = _portal_html()
    poll_body = _extract_js_function(html, "startVerificationStatusPolling")
    assert "isValidPortalPathId(options.docId)" in poll_body

    trigger_body = _extract_js_function(html, "triggerUploadKYC")
    assert "isValidPortalPathId(inputId)" in trigger_body

    upload_body = _extract_js_function(html, "handleKYCUpload")
    assert upload_body.index("isValidPortalPersonId(personId)") < upload_body.index("var docKey")
    assert "document.getElementById('kyc-person-' + personId)" in upload_body
    assert "encodeURIComponent(personId)" in upload_body

    link_body = _extract_js_function(html, "sendKYCLink")
    assert link_body.index("isValidPortalPersonId(personId)") < link_body.index(
        "document.getElementById('kyc-email-' + personId)"
    )
    assert "document.getElementById('kyc-person-' + personId)" in link_body

    sync_body = _extract_js_function(html, "syncPersistedApplicationDocuments")
    assert "var personId = String(rawPersonId).trim();" in sync_body
    assert "isValidPortalPersonId(personId)" in sync_body
    assert "document.getElementById('kyc-person-' + personId)" in sync_body
    assert "renderPersonVerification(personId" in sync_body
    assert "renderPersonVerification(personId, doc.doc_type, updatedRecord)" in sync_body


def test_backoffice_retains_internal_risk_visibility():
    text = BACKOFFICE_PATH.read_text(encoding="utf-8")
    assert "Risk Rating" in text
    assert "Stored Composite Score" in text
    assert "Enhanced Due Diligence" in text
