from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
PORTAL_HTML = ROOT / "arie-portal.html"


INLINE_SCRIPT_PARSE = r"""
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
let found = 0;
let match;
while ((match = re.exec(html)) !== null) {
  found += 1;
  new vm.Script(match[1], {filename: process.argv[1] + '.inline-' + found + '.js'});
}
if (!found) throw new Error('No inline script found');
"""

BINDING_STATUS_BEHAVIOUR = r"""
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('function monitoringDocumentRenewalBindingStatusLabel');
const end = html.indexOf('function renderMonitoringDocumentRenewalCreateControls', start);
if (start < 0 || end < 0) throw new Error('binding status helper not found');
const sandbox = {
  monitoringCanonicalToken: value => String(value || '').trim().toLowerCase(),
};
vm.createContext(sandbox);
vm.runInContext(html.slice(start, end), sandbox);
const label = sandbox.monitoringDocumentRenewalBindingStatusLabel;
const valid = {
  upload_id: 'upload-1',
  renewal_request_id: 'request-1',
  application_id: 'app-1',
  customer_id: 'customer-1',
  person_id: 'person-1',
  person_type: 'director',
  original_document_id: 'document-1',
  original_document_version: 1,
  uploaded_document_id: 'renewal-candidate:upload-1',
  document_type: 'passport',
  upload_timestamp: '2030-01-01T00:00:00Z',
  uploaded_by: 'customer-1',
  binding_status: 'bound',
  contract_version: 'monitoring_document_renewal_upload_binding_v1',
};
if (label(valid, 'upload_received') !== 'Bound') throw new Error('valid binding not bound');
for (const status of ['created', 'awaiting_upload', 'cancelled']) {
  if (label(null, status) !== 'Not bound') throw new Error('valid unbound state failed');
}
for (const status of ['upload_received', '', 'unknown']) {
  if (!label(null, status).includes('manual review')) {
    throw new Error('missing binding did not fail closed');
  }
}
for (const invalid of [
  {...valid, application_id: ''},
  {...valid, uploaded_document_id: 'document-1'},
  {...valid, original_document_version: 0},
  {...valid, upload_timestamp: 'not-a-timestamp'},
  {...valid, person_type: ''},
  {...valid, binding_status: 'verified'},
  {...valid, contract_version: 'monitoring_document_renewal_upload_binding_v0'},
]) {
  if (!label(invalid, 'upload_received').includes('manual review')) {
    throw new Error('malformed binding did not fail closed');
  }
}
"""


def _region(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    return source[start_index : source.index(end, start_index)]


def test_backoffice_renewal_card_is_exact_type_and_flag_gated():
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    contract = _region(
        html,
        "var MONITORING_DOCUMENT_RENEWAL_ALERT_TYPES",
        "function monitoringDocumentRenewalMilestones",
    )
    assert "'document_expired'" in contract
    assert "'document_expiring_soon'" in contract
    assert "'document_expiry_missing'" in contract
    assert "raw.renewal_feature_enabled === true" in contract
    assert ".indexOf(monitoringDocumentRenewalAlertType(alert)) >= 0" in contract
    assert "source_reference" not in contract
    assert "summary" not in contract
    exact_type = _region(
        html,
        "function monitoringDocumentRenewalAlertType",
        "function monitoringDocumentRenewalSupportedAlert",
    )
    assert "return String(raw.alert_type || '');" in exact_type
    assert "monitoringCanonicalToken" not in exact_type
    assert "raw.type" not in exact_type
    assert "raw.alertType" not in exact_type

    legacy = _region(
        html,
        "function renderMonitoringDocumentDecisionSection",
        "function renderMonitoringDecisionSection",
    )
    assert "if (monitoringDocumentRenewalOwnsAlert(alert)) return '';" in legacy
    assert "if (monitoringDocumentRenewalBlocksLegacy(alert))" in legacy
    assert 'data-monitoring-renewal-manual-review="true"' in legacy
    assert "renderMonitoringReplacementUploadControl(uploaded)" in legacy
    assert "monitoring-accept-updated-document-btn" in legacy

    legacy_gate = _region(
        html,
        "function monitoringDocumentRenewalBlocksLegacy",
        "function monitoringDocumentRenewalStatusLabel",
    )
    assert "monitoringDocumentRenewalOwnsAlert(alert)" in legacy_gate
    assert "monitoringDocumentRenewalFlagEnabled(alert)" in legacy_gate
    assert "monitoringDocumentLegacyRequestActive(alert)" in legacy_gate


def test_backoffice_renewal_controls_are_orchestration_only():
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    renewal_ui = _region(
        html,
        "function monitoringDocumentRenewalStatusLabel",
        "function renderMonitoringDocumentDecisionSection",
    )
    card = _region(
        html,
        "function renderMonitoringDocumentRenewalCreateControls",
        "function renderMonitoringDocumentDecisionSection",
    )
    for label in (
        "Document Renewal Request",
        "Renewal Requested",
        "Request Sent",
        "Awaiting Upload",
        "Upload Received",
        "Create Renewal Request",
        "Resend Portal Request",
        "Change Due Date",
        "Cancel Request",
    ):
        assert label in renewal_ui
    for prohibited in (
        "monitoring-accept-updated-document-btn",
        "monitoring-reject-updated-document-btn",
        "monitoring-waive-updated-document-btn",
        "monitoring-replacement-upload",
        "reviewMonitoringUpdatedDocument(",
        "uploadMonitoringReplacementDocument(",
    ):
        assert prohibited not in card
    assert "does not close the Monitoring Alert" in card
    assert "never replace a canonical document" in card
    assert "status !== 'upload_received'" in card
    assert "Cancelling retains the staged upload and audit evidence" in card
    render_card = _region(
        html,
        "function renderMonitoringDocumentRenewalCard",
        "function renderMonitoringDocumentDecisionSection",
    )
    assert "raw.renewal_projection_error" in render_card
    assert 'data-monitoring-renewal-unavailable="true"' in render_card
    assert render_card.index("raw.renewal_projection_error") < render_card.index(
        "renderMonitoringDocumentRenewalCreateControls"
    )

    binding_label = _region(
        html,
        "function monitoringDocumentRenewalBindingStatusLabel",
        "function renderMonitoringDocumentRenewalCreateControls",
    )
    for required in (
        "renewal_request_id",
        "application_id",
        "customer_id",
        "original_document_id",
        "original_document_version",
        "uploaded_document_id",
        "document_type",
        "upload_timestamp",
        "uploaded_by",
        "monitoring_document_renewal_upload_binding_v1",
        "renewal-candidate:",
        "Binding unavailable — manual review required",
    ):
        assert required in binding_label
    assert "Number.isInteger(version) && version >= 1" in binding_label
    assert "!Number.isNaN(Date.parse(timestamp))" in binding_label
    assert "return complete ? 'Bound'" in binding_label
    assert "normalizeAlertStatusLabel(status)" not in binding_label


def test_backoffice_renewal_actions_use_only_dedicated_endpoints():
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    actions = _region(
        html,
        "async function createMonitoringDocumentRenewalRequest",
        "async function requestMonitoringUpdatedDocument",
    )
    for endpoint in (
        "'/monitoring/alerts/' + encodeURIComponent(alert.id) + '/renewal-request'",
        "'/monitoring/renewal-requests/' + encodeURIComponent(request.request_id) + '/resend'",
        "'/monitoring/renewal-requests/' + encodeURIComponent(request.request_id) + '/due-date'",
        "'/monitoring/renewal-requests/' + encodeURIComponent(request.request_id) + '/cancel'",
    ):
        assert endpoint in actions
    assert "reason: reason" in actions
    assert "due_date: dueDate" in actions
    assert "manual_reason: manualReason" in actions
    assert "expected_revision: monitoringDocumentRenewalRevision(request)" in actions
    for prohibited in (
        "/replacement-upload",
        "accept_updated_document",
        "reject_updated_document",
        "waive_updated_document",
        "/documents/",
        "/verify",
    ):
        assert prohibited not in actions


def test_portal_renewal_cards_are_application_bound_and_upload_only():
    html = PORTAL_HTML.read_text(encoding="utf-8")
    renderer = _region(
        html,
        "function clearPortalDocumentRenewalRequests",
        "function portalEnhancedRequirementTone",
    )
    assert "portalDocumentRenewalApplicationId = ''" in renderer
    assert "boundApplicationId !== String(currentApplicationId || '')" in renderer
    assert "String(request.application_id || '') === boundApplicationId" in renderer
    assert "String(currentApplicationId || '') !== requestedApplicationId" in renderer
    assert "response.application_id" in renderer
    assert "data-renewal-application-id" in renderer
    assert "data-renewal-request-id" in renderer
    assert "request.request_status || '') !== 'awaiting_upload'" in renderer
    assert "Upload Renewal Document" in renderer
    assert "upload_received: 'Uploaded'" in renderer
    assert "Awaiting Verification" in renderer
    assert "Compliance has not verified or accepted it" in renderer
    assert "canonical document has not been replaced" in renderer
    assert "status === 'awaiting_upload' && featureEnabled === true" in renderer
    assert "portalDocumentRenewalFeatureEnabled = featureEnabled === true" in renderer
    assert "Renewal uploads are currently read-only while the governed workflow is OFF" in renderer
    assert (
        "This renewal request has expired. Contact Compliance if a new due date "
        "or replacement request is required."
    ) in renderer

    for prohibited in (
        "Approve",
        "Accept Document",
        "Reject",
        "Waive",
        "Close Alert",
        "replace-canonical",
        "Invoke Agent 1",
        "Verify Renewal Document",
    ):
        assert prohibited not in renderer


def test_portal_renewal_uses_dedicated_read_and_staging_upload_contract():
    html = PORTAL_HTML.read_text(encoding="utf-8")
    loader = _region(
        html,
        "async function loadPortalDocumentRenewalRequests",
        "function selectPortalDocumentRenewalFile",
    )
    upload = _region(
        html,
        "async function uploadPortalDocumentRenewal",
        "function portalEnhancedRequirementTone",
    )
    assert (
        "apiCall('GET', '/portal/applications/' + encodeURIComponent(requestedApplicationId) + '/renewal-requests?limit=' + PORTAL_DOCUMENT_RENEWAL_PAGE_LIMIT + '&offset=0')"
        in loader
    )
    assert (
        "'/portal/applications/' + encodeURIComponent(applicationId) + '/renewal-requests/' + encodeURIComponent(requestId) + '/upload'"
        in upload
    )
    assert "new FormData()" in upload
    assert "formData.append('file', file, file.name)" in upload
    assert "if (!portalDocumentRenewalFeatureEnabled)" in upload
    assert (
        "showToast('success', 'Renewal Upload Bound', 'Uploaded and awaiting "
        "verification. Your canonical document has not been replaced.')"
    ) in upload
    for prohibited in (
        "/enhanced-requirements/",
        "/documents?doc_type=",
        "/verify",
        "verification_status",
        "review_status",
        "document_id",
    ):
        assert prohibited not in upload


def test_portal_renders_renewal_requests_on_kyc_and_approved_surfaces():
    html = PORTAL_HTML.read_text(encoding="utf-8")
    assert 'id="portal-renewal-onboarding-card"' in html
    assert 'id="portal-renewal-approved-card"' in html
    resume = _region(html, "async function resumeApplication", "async function deleteApplication")
    # Renewal loading must not suspend resumeApplication before its final KYC
    # card rebuild; doing so can discard a client upload selected in that gap.
    assert "loadPortalDocumentRenewalRequests()" not in resume
    assert resume.count("loadPortalEnhancedRequirements()") == 1
    switcher = _region(html, "function showView(name, options)", "function setSidebarActive")
    assert "name === 'onboarding' || name === 'approved'" in switcher
    assert switcher.count("loadPortalDocumentRenewalRequests()") == 1
    assert switcher.count("loadPortalEnhancedRequirements()") == 1
    enhanced_ready = "await loadPortalEnhancedRequirements()"
    first_card_sync = "syncDirectorsUBOsToKYC"
    final_view = "showView(target, { skipEnhancedRequirementsLoad: true })"
    final_rebuild = "applyApplicationViewState(target)"
    document_hydration = "syncPersistedApplicationDocuments(resumedApplication)"
    assert resume.index(enhanced_ready) < resume.index(first_card_sync)
    assert resume.index(first_card_sync) < resume.index(final_view)
    assert resume.index(final_view) < resume.index(final_rebuild)
    assert resume.index(final_rebuild) < resume.index(document_hydration)
    assert "await " not in resume[
        resume.index(first_card_sync) : resume.index(document_hydration)
    ]
    autosave_wrapper = _region(html, "var origShowView = showView", "var SECTION_A_DOCS")
    assert "showView = async function(name, options)" in autosave_wrapper
    assert "origShowView(name, options)" in autosave_wrapper
    assert "return enhancedRequirementsLoad" in autosave_wrapper
    assert "return continueNavigation()" in autosave_wrapper
    wrapper = _region(html, "var _originalShowView = showView", "function togglePwd")
    assert "showView = function(name, options)" in wrapper
    assert "_originalShowView(name, options)" in wrapper
    assert "return enhancedRequirementsLoad" in wrapper

    clear_renewals = _region(
        html,
        "function clearPortalDocumentRenewalRequests",
        "function renderPortalDocumentRenewalUnavailable",
    )
    upload_renewal = _region(
        html,
        "async function uploadPortalDocumentRenewal",
        "function portalEnhancedRequirementTone",
    )
    load_more = _region(
        html,
        "async function loadMorePortalDocumentRenewalRequests",
        "function selectPortalDocumentRenewalFile",
    )
    render_renewals = _region(
        html,
        "function renderPortalDocumentRenewalRequests",
        "async function loadPortalDocumentRenewalRequests",
    )
    assert "portalDocumentRenewalBusy = {}" not in clear_renewals
    assert "portalDocumentRenewalPageBusy = {}" not in clear_renewals
    assert "portalDocumentRenewalBusy[busyKey] = true" in upload_renewal
    assert "delete portalDocumentRenewalBusy[busyKey]" in upload_renewal
    assert "pagination.has_more === true" in render_renewals
    assert "pagination && pagination.next_offset" in render_renewals
    assert "loadMorePortalDocumentRenewalRequests(this)" in render_renewals
    assert "Number(response.offset) !== offset" in load_more
    assert "apiCall('GET'" in load_more
    assert "apiCall('POST'" not in load_more
    assert "generation === portalDocumentRenewalGeneration" in load_more
    assert "portalDocumentRenewalPageBusy[applicationId] = true" in load_more
    assert "delete portalDocumentRenewalPageBusy[applicationId]" in load_more
    initial_loader = _region(
        html,
        "async function loadPortalDocumentRenewalRequests",
        "async function loadMorePortalDocumentRenewalRequests",
    )
    assert "portalDocumentRenewalGeneration !== requestedGeneration" in initial_loader
    assert "portalDocumentRenewalGeneration === requestedGeneration" in initial_loader


def test_changed_ui_inline_scripts_parse():
    node = shutil.which("node")
    assert node, "Node.js is required for inline JavaScript parsing"
    for path in (BACKOFFICE_HTML, PORTAL_HTML):
        result = subprocess.run(
            [node, "-e", INLINE_SCRIPT_PARSE, str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr or result.stdout


def test_backoffice_binding_status_helper_fails_closed_on_public_shapes():
    node = shutil.which("node")
    assert node, "Node.js is required for binding-status behavior validation"
    result = subprocess.run(
        [node, "-e", BINDING_STATUS_BEHAVIOUR, str(BACKOFFICE_HTML)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
