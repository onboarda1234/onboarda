import re
from html import unescape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _backoffice_html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _region(src: str, start_marker: str, end_marker: str) -> str:
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


def _upload_doc_type_select(html: str) -> str:
    return _region(html, '<select class="form-select" id="bo-upload-doc-type">', "</select>")


def _upload_options(html: str) -> dict[str, str]:
    select = _upload_doc_type_select(html)
    return {
        value: unescape(label.strip())
        for value, label in re.findall(r'<option value="([^"]*)">([^<]*)</option>', select)
    }


def test_officer_upload_dropdown_includes_supported_person_identity_types():
    options = _upload_options(_backoffice_html())

    assert options["passport"] == "Passport / Government ID"
    assert options["national_id"] == "National ID / Government ID"


def test_expected_slot_preselection_has_matching_select_options():
    html = _backoffice_html()
    helper = _region(html, "function openBoDocUploadForExpectedSlot", "async function refreshCurrentKycDocumentsDetail")
    options = _upload_options(html)

    assert "document.getElementById('bo-upload-doc-type').value = docType || '';" in helper
    assert "passport" in options
    assert "national_id" in options


def test_existing_document_type_values_remain_available():
    options = _upload_options(_backoffice_html())

    for value in [
        "cert_inc",
        "memarts",
        "cert_reg",
        "reg_sh",
        "reg_dir",
        "fin_stmt",
        "board_res",
        "structure_chart",
        "poa",
        "bankref",
        "source_wealth",
        "source_funds",
        "supporting_document",
    ]:
        assert value in options


def test_upload_handler_and_person_scope_submission_remain_unchanged():
    html = _backoffice_html()
    panel = _region(html, '<div id="bo-doc-upload-panel"', '<div id="detail-docs-with-verification">')
    upload = _region(html, "async function submitBoDocUpload", "async function viewBackofficeDocument")

    assert 'onclick="submitBoDocUpload()"' in panel
    assert "var docType = document.getElementById('bo-upload-doc-type').value;" in upload
    assert "var personId = document.getElementById('bo-upload-person-id').value.trim();" in upload
    assert "var personType = document.getElementById('bo-upload-person-type').value.trim();" in upload
    assert "var query = '?doc_type=' + encodeURIComponent(docType);" in upload
    assert "query += '&person_id=' + encodeURIComponent(personId);" in upload
    assert "query += '&person_type=' + encodeURIComponent(personType);" in upload
    assert "boApiCall('POST', '/applications/' + uploadAppTarget + '/documents' + query, formData)" in upload


def test_recent_kyc_document_ui_controls_remain_intact():
    html = _backoffice_html()

    assert "Verification policy missing. Admin setup is required before automated verification can run. Manual review is required before relying on this document." in html
    assert "Required document missing. Request from client or upload document before approval." in html
    assert "Completed checks" in html
    assert "Verification completed. No failed or warning checks were stored. Detailed passed-check evidence is not available for this document." in html
    assert "verification-check-method-dot ' + methodMeta.key" in html
    assert "Accepted with reason." in html
    assert "function documentCanShowReverifyAction" in html
    assert "manual_accepted" in html
    assert "renderDocumentCompactSummary" in html
    assert "collapseDocumentReviewFindingText" in html
