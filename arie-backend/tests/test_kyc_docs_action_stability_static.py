from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _region(src: str, start_marker: str, end_marker: str) -> str:
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


def test_kyc_document_actions_refresh_back_to_documents_tab():
    html = _read("arie-backoffice.html")

    helper = _region(html, "async function refreshCurrentKycDocumentsDetail", "async function submitBoDocUpload")
    upload = _region(html, "async function submitBoDocUpload", "async function viewBackofficeDocument")
    reverify = _region(html, "async function verifyBackofficeDocument", "async function reviewBackofficeDocument")
    review = _region(html, "async function confirmDocumentReview", "// ══════════════════════════════════════════════════════════")

    assert "openAppDetail(ref, { initialTab: 'kyc-docs' })" in helper
    assert "var uploadAppRef = currentApp.ref;" in upload
    assert "await refreshCurrentKycDocumentsDetail(uploadAppRef)" in upload
    assert "await refreshCurrentKycDocumentsDetail(appRef || (currentApp && currentApp.ref))" in reverify
    assert "await refreshCurrentKycDocumentsDetail(appRef || (currentApp && currentApp.ref))" in review

    for stale_refresh in [
        "openAppDetail(currentApp.ref);",
        "openAppDetail(appRef || (currentApp && currentApp.ref));",
    ]:
        assert stale_refresh not in upload + reverify + review


def test_backoffice_upload_has_progress_phases_and_duplicate_click_guard():
    html = _read("arie-backoffice.html")

    panel = _region(html, '<div id="bo-doc-upload-panel"', '<div id="detail-docs-with-verification">')
    helpers = _region(html, "function setBoDocUploadStatus", "async function refreshCurrentKycDocumentsDetail")
    upload = _region(html, "async function submitBoDocUpload", "async function viewBackofficeDocument")

    assert 'id="bo-upload-submit-btn"' in panel
    assert 'id="bo-upload-cancel-btn"' in panel
    assert "var BO_DOC_UPLOAD_IN_FLIGHT = false;" in html
    assert "var BO_DOC_UPLOAD_PHASE = 'idle';" in html
    assert "var BO_DOC_UPLOAD_RUN_ID = 0;" in html
    assert "function beginBoDocUploadInFlight" in helpers
    assert "function completeBoDocUploadPhase" in helpers
    assert "setBoDocUploadControlsDisabled(disabled)" in helpers
    assert "submitBtn.setAttribute('aria-busy', disabled ? 'true' : 'false')" in helpers

    assert "if (BO_DOC_UPLOAD_IN_FLIGHT)" in upload
    assert "Upload already in progress. Duplicate clicks are ignored." in upload
    assert "var uploadRunId = beginBoDocUploadInFlight('Uploading document...')" in upload
    assert "setBoDocUploadPhase('uploaded', 'Upload saved. Verifying document...')" in upload
    assert "setBoDocUploadPhase('verifying', 'Upload saved. Verifying document...')" in upload
    assert "setBoDocUploadPhase('refreshing_status'" in upload
    assert "completeBoDocUploadPhase('complete', 'Upload saved and document status refreshed.')" in upload
    assert "completeBoDocUploadPhase('upload_error'" in upload


def test_backoffice_upload_preserves_verification_but_separates_downstream_failures():
    html = _read("arie-backoffice.html")

    upload = _region(html, "async function submitBoDocUpload", "async function viewBackofficeDocument")

    assert "await boApiCall('POST', '/documents/' + uploadedDocId + '/verify')" in upload
    assert "postUploadWarning = 'Upload saved, but verification/status refresh could not complete. Refresh this section.'" in upload
    assert "completeBoDocUploadPhase('post_upload_warning'" in upload
    assert "showToast('Document uploaded. Verification/status refresh needs attention.', 'warn')" in upload
    assert "if (uploadAccepted)" in upload
    assert "resetBoDocUploadState({ reason: 'upload_failed' })" not in upload


def test_backoffice_upload_does_not_block_success_on_broad_application_refresh():
    html = _read("arie-backoffice.html")

    helpers = _region(html, "function refreshBackOfficeUploadBackgroundState", "function resetBoDocUploadState")
    upload = _region(html, "async function submitBoDocUpload", "async function viewBackofficeDocument")

    assert "refreshBackOfficeUploadBackgroundState();" in upload
    assert "loadFromAPI().catch(function(loadErr)" in helpers
    assert "refreshAdminAuditEvidence();" in helpers
    assert "await loadFromAPI();" not in upload


def test_rejection_reason_is_required_in_officer_modal():
    html = _read("arie-backoffice.html")
    modal = _region(html, '<div class="modal-overlay" id="modal-document-review">', '<div class="modal-overlay" id="modal-officer-correction">')
    review_fn = _region(html, "async function reviewBackofficeDocument", "async function confirmDocumentReview")
    confirm_fn = _region(html, "async function confirmDocumentReview", "// ══════════════════════════════════════════════════════════")

    assert "document-review-comment-error" in modal
    assert "Rejection reason (Optional)" not in html
    assert "label: 'Rejection reason'" in review_fn
    assert "commentEl.dataset.reasonRequired = status === 'rejected' ? 'true' : 'false';" in review_fn
    assert "status === 'rejected' && !comment" in confirm_fn
    assert "Rejection reason is required." in confirm_fn
    assert "boApiCall('POST', '/documents/' + docId + '/review'" in confirm_fn


def test_view_uses_inline_endpoint_and_download_keeps_attachment_path():
    html = _read("arie-backoffice.html")
    open_fn = _region(html, "async function openBackofficeDocument", "function getFilenameFromContentDisposition")

    assert "'/documents/' + docId + '/download' + (inlineView ? '?view=inline' : '')" in open_fn
    assert "window.open(data.download_url, '_blank', 'noopener')" in open_fn
    assert "window.open(blobUrl, '_blank', 'noopener')" in open_fn
    assert "a.download = getFilenameFromContentDisposition" in open_fn
    assert "downloadBackofficeDocument(docId)" not in _region(html, "async function viewBackofficeDocument", "async function downloadBackofficeDocument")


def test_more_dropdown_closes_on_outside_click_and_after_action():
    html = _read("arie-backoffice.html")
    closer = _region(html, "function closeDocumentReviewMoreMenus", "function renderDocumentAuditToggleAction")
    listener = _region(html, "document.addEventListener('click'", "document.addEventListener('keydown'")

    assert ".document-review-more[open]" in closer
    assert "menu.open = false" in closer
    assert "target.closest('.document-review-more')" in listener
    assert "target.closest('.document-review-more-item')" in listener
    assert "setTimeout(function() { closeDocumentReviewMoreMenus(); }, 0)" in listener
    assert "closeDocumentReviewMoreMenus(menu)" in listener


def test_s3_inline_preview_preserves_authorization_but_requests_inline_disposition():
    server = _read("arie-backend/server.py")
    s3_client = _read("arie-backend/s3_client.py")

    download_handler = _region(server, "class DocumentDownloadHandler", "# ══════════════════════════════════════════════════════════")
    assert 'inline_view = self.get_argument("view", "") == "inline"' in download_handler
    assert 'disposition = "inline" if (inline_view and is_previewable) else "attachment"' in download_handler
    assert "get_presigned_url_with_ownership(" in download_handler
    assert "content_disposition=disposition" in download_handler

    presign_with_ownership = _region(s3_client, "def get_presigned_url_with_ownership", "def get_presigned_url(")
    presign = _region(s3_client, "def get_presigned_url(", "def get_document_metadata")
    assert 'content_disposition: str = "attachment"' in presign_with_ownership
    assert 'content_disposition: str = "attachment"' in presign
    assert 'disposition = "inline" if content_disposition == "inline" else "attachment"' in presign
    assert "ResponseContentDisposition" in presign
