"""Client upload failures are reported with fixed, sanitised copy.

The client portal must tell the client an upload failed and what to do about
it, without ever rendering the backend's error text. Backend strings are used
to classify the failure; only the three constants below are displayed.

These are presentation guards. The upload request, polling, the submission gate
and the verification pipeline are untouched by the code they pin.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PORTAL_PATH = ROOT / "arie-portal.html"

TOO_LARGE = "File exceeds the maximum allowed size. Please upload a smaller file."
UNSUPPORTED = "This file type is not supported. Please upload one of the supported file formats."
GENERIC = "We couldn’t upload your document. Please try again."


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


# Every client-facing surface that reports an upload failure.
UPLOAD_FAILURE_FUNCTIONS = (
    "handleUpload",
    "handleKYCUpload",
    "uploadRMIItem",
    "uploadPortalEnhancedRequirement",
    "uploadPeriodicReviewDocumentRequest",
    "uploadPortalDocumentRenewal",
)


def test_the_three_client_upload_failure_messages_are_defined():
    html = _portal_html()
    assert f"too_large: '{TOO_LARGE}'" in html
    assert f"unsupported_type: '{UNSUPPORTED}'" in html
    assert f"generic: '{GENERIC}'" in html


def test_every_upload_failure_path_uses_the_sanitised_reporter():
    html = _portal_html()
    for function_name in UPLOAD_FAILURE_FUNCTIONS:
        body = _extract_js_function(html, function_name)
        assert "showClientUploadFailure(err)" in body, function_name


def test_no_upload_path_renders_backend_error_text():
    html = _portal_html()
    for function_name in UPLOAD_FAILURE_FUNCTIONS:
        body = _extract_js_function(html, function_name)
        # err.message must never reach a toast or the DOM from these paths.
        for toast in re.findall(r"showToast\((.*?)\);", body, flags=re.S):
            assert "err.message" not in toast, f"{function_name}: {toast}"
        for assignment in re.findall(r"\.textContent\s*=\s*([^;]+);", body):
            assert "err.message" not in assignment, f"{function_name}: {assignment}"
        assert "err.message ||" not in body, function_name


def test_classifier_reads_backend_text_but_never_displays_it():
    html = _portal_html()
    classifier = _extract_js_function(html, "classifyClientUploadError")
    message = _extract_js_function(html, "clientUploadErrorMessage")
    reporter = _extract_js_function(html, "showClientUploadFailure")

    # Classification is the only use of the backend string.
    assert "err.message" in classifier
    assert "return 'too_large';" in classifier
    assert "return 'unsupported_type';" in classifier
    assert "return 'generic';" in classifier

    # Display comes from the fixed table only.
    assert "CLIENT_UPLOAD_ERROR_COPY[classifyClientUploadError(err)]" in message
    assert "err.message" not in message

    # The reporter shows a fixed title plus the mapped body, and stays quiet
    # when apiCall has already handled an expired session.
    assert "showToast('error', 'Upload Failed', clientUploadErrorMessage(err))" in reporter
    assert "=== 'Session expired'" in reporter


def test_failed_upload_leaves_the_slot_ready_for_another_file():
    html = _portal_html()

    entity = _extract_js_function(html, "handleUpload")
    catch_block = entity.split(".catch(function(err)", 1)[1]
    assert "label.textContent = 'Click to upload';" in catch_block
    assert "item.classList.remove('uploaded');" in catch_block
    assert "input.value = '';" in catch_block

    person = _extract_js_function(html, "handleKYCUpload")
    person_catch = person.split(".catch(function(err)", 1)[1]
    assert "resetUploadUI();" in person_catch

    reset = _extract_js_function(html, "resetUploadUI")
    assert "lbl.textContent = 'Click to upload'" in reset
    assert "input.value = ''" in reset


def test_upload_success_and_gate_copy_are_unchanged_by_this_hotfix():
    """Scenarios 1, 2 and 5 must be untouched."""
    html = _portal_html()

    # Scenario 1 — successful upload receipt.
    assert "var CLIENT_UPLOAD_RECEIPT_STATUS = '✓ Upload successful';" in html
    assert (
        "var CLIENT_UPLOAD_RECEIPT_NOTE = "
        "'Stored securely. Our team will review this document.';"
    ) in html
    assert "showToast('success', 'Upload Successful', names + ' uploaded.');" in html
    assert "showToast('success', 'Upload Successful', fileName + ' uploaded.');" in html

    # Scenario 2 — neutral blocked-state notice.
    assert (
        "var PORTAL_INTERNAL_REVIEW_NOTICE = 'Some uploaded documents are still "
        "being processed or require internal review. You will be able to "
        "continue once the review is complete.';"
    ) in html

    # Scenario 5 — retry remains available; the slot reset is asserted above.
    assert "function addClearButton(docId)" in html
