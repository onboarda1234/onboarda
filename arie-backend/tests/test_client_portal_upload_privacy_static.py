"""Client portal upload privacy boundary.

The client portal (``arie-portal.html``) must show upload-related messages only.
Every internal document verification finding — verification status, pass/fail,
warnings, model findings and confidence, extracted values, name / company /
registration-number / expiry mismatches, authenticity findings, quality scores,
rule failures, check identifiers and risk indicators — must stay out of the
client surface while remaining fully visible to compliance officers in the
RegMind back office (``arie-backoffice.html``).

These are presentation-layer guards only. The companion assertions at the bottom
of this module pin the parts that must NOT have changed: the verification
pipeline still runs, its results are still persisted and polled, and the
submission gate still reads the same machine-readable document state.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PORTAL_PATH = ROOT / "arie-portal.html"
BACKOFFICE_PATH = ROOT / "arie-backoffice.html"


def _portal_html():
    return PORTAL_PATH.read_text(encoding="utf-8")


def _backoffice_html():
    return BACKOFFICE_PATH.read_text(encoding="utf-8")


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


# Client-facing surfaces that render or describe an uploaded document.
CLIENT_UPLOAD_FUNCTIONS = (
    "renderPersistedVerification",
    "handleUpload",
    "handleKYCUpload",
    "buildReviewScreen",
    "updateDocsReviewMessaging",
    "finalSubmitFromReview",
)

# Verification-result fields the backend persists and returns. None of them may
# be read anywhere in the client upload surfaces.
FORBIDDEN_RESULT_FIELDS = (
    "results.checks",
    "results.red_flags",
    "results.warnings",
    "results.confidence",
    "results.ai_source",
    "results.verified_at",
    "check.result",
    "check.message",
    "check.classification",
    "check.extracted_value",
    "check.ps_value",
    "check.ps_field",
)

# Finding vocabulary that must never appear anywhere in the client bundle.
FORBIDDEN_CLIENT_FINDING_COPY = (
    "Red flags:",
    "Warnings:",
    "Confidence:",
    "Verification failed",
    "Verification in progress",
    "Verification skipped",
    "Pending verification",
    "Verification Required",
    "Verification Gate",
    "Verification Still Pending",
    "Not verified",
    "extracted:",
)


def _string_literals(body):
    """Every single-quoted literal in a JS function body."""
    return re.findall(r"'((?:[^'\\]|\\.)*)'", body)


def test_client_upload_surfaces_read_no_verification_result_fields():
    html = _portal_html()
    for function_name in CLIENT_UPLOAD_FUNCTIONS:
        body = _extract_js_function(html, function_name)
        for field in FORBIDDEN_RESULT_FIELDS:
            assert field not in body, f"{function_name} reads {field}"


def test_verification_status_labels_are_plumbed_but_never_rendered():
    """The upload record still mirrors the API payload; nothing displays it."""
    html = _portal_html()

    # Still carried through so the record stays a faithful mirror of the API.
    assert "verification_status_label: resp.verification_status_label || ''," in html
    assert "verification_status_tone: resp.verification_status_tone || 'pending'," in html

    # But never read by anything that renders.
    render_body = _extract_js_function(html, "renderPersistedVerification")
    for field in ("verification_status_label", "verification_status_tone"):
        assert field not in render_body, field

    for statement in re.findall(r"showToast\((.*?)\);", html, flags=re.S):
        assert "verification_status_label" not in statement, statement
        assert "verification_status_tone" not in statement, statement


def test_client_upload_receipt_renders_upload_outcome_only():
    html = _portal_html()
    body = _extract_js_function(html, "renderPersistedVerification")

    # Upload-related confirmation only.
    assert "CLIENT_UPLOAD_RECEIPT_STATUS" in body
    assert "CLIENT_UPLOAD_RECEIPT_NOTE" in body
    assert "var CLIENT_UPLOAD_RECEIPT_STATUS = '✓ Upload successful';" in html
    assert (
        "var CLIENT_UPLOAD_RECEIPT_NOTE = "
        "'Stored securely. Our team will review this document.';"
    ) in html

    # No status summary, banner or per-check row survives in the client card.
    assert "getVerificationSummary" not in html
    assert "doc-verify-row" not in html
    assert "summary.text" not in body
    assert "banners" not in body
    assert "checksHtml" not in body


def test_portal_bundle_contains_no_client_finding_vocabulary():
    html = _portal_html()
    for phrase in FORBIDDEN_CLIENT_FINDING_COPY:
        assert phrase not in html, phrase


def test_upload_toasts_report_upload_outcome_only():
    html = _portal_html()
    for function_name in ("handleUpload", "handleKYCUpload"):
        body = _extract_js_function(html, function_name)
        toasts = re.findall(r"showToast\((.*?)\);", body)
        assert toasts, f"{function_name} raises no toast"
        for toast in toasts:
            for phrase in ("Verified", "Verification", "verified", "verification"):
                assert phrase not in toast, f"{function_name}: {toast}"

    assert "showToast('success', 'Upload Successful', names + ' uploaded.');" in html
    assert "showToast('success', 'Upload Successful', fileName + ' uploaded.');" in html
    # A stalled verification poll no longer tells the client anything about it.
    assert "'Document verification still running after portal poll window'" in html


def test_submission_gate_copy_is_free_of_verification_vocabulary():
    html = _portal_html()
    messaging = _extract_js_function(html, "updateDocsReviewMessaging")
    review = _extract_js_function(html, "buildReviewScreen")

    assert "still being processed" in messaging
    assert "Submission Blocked — Outstanding Items" in messaging
    assert "⏳ Stored — processing" in review
    assert "⏳ ID + PoA stored — processing" in review

    # No client-visible string in either surface uses verification vocabulary.
    for name, body in (("updateDocsReviewMessaging", messaging), ("buildReviewScreen", review)):
        for literal in _string_literals(body):
            assert "verif" not in literal.lower(), f"{name}: {literal}"


# ── Guards on what must NOT have changed ────────────────────────────────────


def test_verification_pipeline_still_runs_and_is_polled_by_the_portal():
    html = _portal_html()

    # Upload still posts to the same endpoint and the portal still polls the
    # authoritative verification-status endpoint until a terminal state.
    assert "'/applications/' + currentApplicationId + '/documents?doc_type='" in html
    assert (
        "apiCall('GET', '/documents/' + encodeURIComponent(docId) + '/verification-status')"
        in html
    )
    assert "function startVerificationStatusPolling(options)" in html
    assert "verificationRecordIsTerminal(latestRecord)" in html
    assert "state === 'verified' || state === 'flagged' || state === 'failed'" in html


def test_submission_gate_still_reads_the_same_document_state():
    html = _portal_html()
    render_body = _extract_js_function(html, "renderPersistedVerification")
    gate_body = _extract_js_function(html, "slotVerificationIsSuccessful")
    review_body = _extract_js_function(html, "buildReviewScreen")

    # The receipt card still carries every machine-readable attribute the gate,
    # the KYC state helper and the browser smoke harness depend on.
    for attribute in (
        "data-doc-id",
        "data-doc-slot",
        "data-verification-success",
        "data-verification-state",
        "data-reliance-state",
    ):
        assert attribute in render_body, attribute

    assert "card.getAttribute('data-verification-success') === 'true'" in gate_body
    assert "slotVerificationIsSuccessful(item, getDocumentSlotKey(" in review_body
    assert "kycDocumentVerificationState(personId, 'passport', personType)" in review_body

    # Gate arithmetic and the submit condition are untouched.
    assert (
        "var canSubmit = missingCount === 0 &&\n    warningCount === 0 &&"
        in review_body.replace("\r\n", "\n")
    )


def test_backoffice_still_shows_every_verification_finding_to_officers():
    html = _backoffice_html()

    # Officer surfaces keep reading the full persisted verification payload.
    for field in (
        "verification_results",
        "verification_status",
        "verification_state",
        "verification_status_label",
        "verification_status_tone",
        "verification_success",
        "document_reliance_state",
    ):
        assert field in html, field

    # Per-check findings the client no longer sees stay rendered for officers.
    for finding in ("red_flags", "confidence", "classification", "check.result", "check.message"):
        assert finding in html, finding

    assert "Risk Rating" in html
    assert "Enhanced Due Diligence" in html
