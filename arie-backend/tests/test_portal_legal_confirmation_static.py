import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_HTML = REPO_ROOT / "arie-portal.html"


def _portal_html():
    return PORTAL_HTML.read_text(encoding="utf-8")


def _extract_final_confirmation(html):
    marker = 'id="final-confirmation-title"'
    marker_index = html.index(marker)
    start = html.rfind('<div class="consent-section"', 0, marker_index)
    end = html.index('<button type="submit" class="btn-submit">', marker_index)
    return html[start:end]


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


def _extract_js_assignment_function(html, assignment):
    start = html.index(assignment)
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
    raise AssertionError(f"Could not extract assignment {assignment}")


def test_final_confirmation_renders_one_required_legal_checkbox():
    html = _portal_html()
    section = _extract_final_confirmation(html)

    assert "Final confirmation" in section
    assert "Please review the legal terms before submitting your application." in section
    assert 'id="f-consent-legal-confirmation" required' in section
    assert 'for="f-consent-legal-confirmation"' in section
    assert (
        "I confirm that I have read and agree to the Data Protection Notice, "
        "Information Sharing, Data Retention, Ongoing Monitoring, and Declaration terms."
    ) in section

    required_checkboxes = re.findall(r'<input[^>]+type="checkbox"[^>]+required', section)
    assert len(required_checkboxes) == 1

    for removed_id in (
        "f-consent-dpa",
        "f-consent-sharing",
        "f-consent-retention",
        "f-consent-monitoring",
        "f-consent-declaration",
    ):
        assert f'id="{removed_id}"' not in section
    assert "Data Processing Consent" not in section


def test_marketing_checkbox_remains_separate_and_optional():
    section = _extract_final_confirmation(_portal_html())

    marketing_input = re.search(r'<input[^>]+id="f-consent-marketing"[^>]*>', section)
    assert marketing_input is not None
    assert "required" not in marketing_input.group(0)
    assert 'for="f-consent-marketing"' in section
    assert (
        "I agree to receive marketing communications and understand I may opt out "
        "at any time. (Optional)"
    ) in section


def test_submit_validation_blocks_only_when_legal_confirmation_unchecked():
    html = _portal_html()
    wrapper = _extract_js_assignment_function(html, "submitPrescreening = function(e)")

    assert "var required = ['f-consent-legal-confirmation'];" in wrapper
    assert "f-consent-marketing" not in wrapper
    assert "Legal Confirmation Required" in wrapper
    assert "Please confirm that you have read and agree to the legal terms before submitting." in wrapper
    assert "origSubmitPrescreening(e);" in wrapper


def test_submit_payload_preserves_existing_consent_fields_from_grouped_checkbox():
    html = _portal_html()
    submit_body = _extract_js_function(html, "submitPrescreening")

    assert "var legalConsentConfirmed = isChecked('f-consent-legal-confirmation');" in submit_body
    for payload_field in (
        "consent_data_processing",
        "consent_information_sharing",
        "consent_data_retention",
        "consent_ongoing_monitoring",
        "consent_declaration",
    ):
        assert f"{payload_field}: legalConsentConfirmed" in submit_body
    assert "consent_marketing: isChecked('f-consent-marketing')" in submit_body


def test_legal_terms_link_is_configurable_and_safe_for_new_tab():
    html = _portal_html()
    section = _extract_final_confirmation(html)

    assert "const LEGAL_TERMS_URL = window.PORTAL_LEGAL_TERMS_URL || '/legal/data-protection-declaration-terms';" in html
    assert 'id="legal-terms-link"' in section
    assert 'href="/legal/data-protection-declaration-terms"' in section
    assert 'target="_blank"' in section
    assert 'rel="noopener noreferrer"' in section
    assert "View Data Protection &amp; Declaration Terms" in section

    configure_body = _extract_js_function(html, "configureLegalTermsLink")
    assert "link.href = LEGAL_TERMS_URL" in configure_body


def test_legacy_consent_restore_maps_old_fields_without_api_or_workflow_changes():
    html = _portal_html()
    restore_body = _extract_js_function(html, "restoreDraftFromData")
    legacy_helper = _extract_js_function(html, "legalConsentFromStoredFields")
    build_body = _extract_js_function(html, "buildDraftDataFromApplication")
    submit_body = _extract_js_function(html, "submitPrescreening")

    assert "prescreeningData['f-consent-legal-confirmation'] = legalConsentFromStoredFields(prescreeningData);" in restore_body
    for legacy_id in (
        "f-consent-dpa",
        "f-consent-sharing",
        "f-consent-retention",
        "f-consent-monitoring",
        "f-consent-declaration",
    ):
        assert legacy_id in legacy_helper
    assert "data.prescreening['f-consent-legal-confirmation']" in build_body

    assert "apiCall('PUT', '/applications/' + currentApplicationId, payload)" in submit_body
    assert "apiCall('POST', '/applications', payload)" in submit_body
    assert "apiCall('POST', '/applications/' + currentApplicationId + '/submit')" in submit_body
