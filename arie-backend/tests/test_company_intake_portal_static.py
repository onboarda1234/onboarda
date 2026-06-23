from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_HTML = REPO_ROOT / "arie-portal.html"


def _portal_html() -> str:
    return PORTAL_HTML.read_text(encoding="utf-8")


def _extract_div_by_id(html: str, element_id: str) -> str:
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


def _extract_js_function(html: str, function_name: str) -> str:
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


def test_company_intake_assistant_appears_before_long_application_form():
    html = _portal_html()
    lookup_view = _extract_div_by_id(html, "view-company-lookup")
    assert html.index('id="view-company-lookup"') < html.index('id="view-prescreening"')
    assert "Start your company application" in lookup_view
    assert "Search your company registry record to pre-fill your application and reduce manual entry." in lookup_view
    assert "Country of Incorporation" in lookup_view
    assert "Company Name or Registration Number" in lookup_view
    assert "I can’t find my company / enter manually" in lookup_view
    assert "Step 1 of 5 - Find your company" in lookup_view
    assert "Confirm profile" in lookup_view
    assert "Confirm directors / officers / members" in lookup_view
    assert "Confirm ownership" in lookup_view
    assert "Complete application" in lookup_view


def test_new_application_and_registration_enter_company_intake_first():
    html = _portal_html()
    start_body = _extract_js_function(html, "startNewApplication")
    assert "resetPortalApplicationState();" in start_body
    assert "showView('company-lookup')" in start_body
    assert "showView('prescreening')" not in start_body

    register_start = html.index("function submitRegister")
    register_end = html.index("// ─── Login", register_start)
    register_region = html[register_start:register_end]
    assert "showView('company-lookup')" in register_region


def test_uk_lookup_enabled_and_non_uk_manual_fallback_does_not_call_registry():
    html = _portal_html()
    lookup_view = _extract_div_by_id(html, "view-company-lookup")
    assert "Registry-assisted intake is currently available for UK companies. You can continue manually." in lookup_view
    assert 'onclick="searchCompanyIntakeRegistry()"' in lookup_view

    country_body = _extract_js_function(html, "handleCompanyIntakeCountryChange")
    assert "companyIntakeIsUkCountry(country)" in country_body
    assert "searchBtn.disabled = !isUk" in country_body
    assert "/company-intake/search" not in country_body

    search_body = _extract_js_function(html, "searchCompanyIntakeRegistry")
    guard_region = search_body.split("apiCall('GET', '/company-intake/search?q='", 1)[0]
    assert "!companyIntakeIsUkCountry(country)" in guard_region
    assert "return;" in guard_region


def test_company_intake_uses_existing_backend_endpoints_and_handles_session_reuse():
    html = _portal_html()
    assert "/company-intake/search?q=" in html
    assert "/company-intake/start" in html
    assert "/company-intake/confirm-profile" in html
    assert "/company-intake/confirm-officers" in html
    assert "/company-intake/confirm-pscs" in html
    assert "/company-intake/session/" in html
    assert "/company-intake/company/" in html

    start_body = _extract_js_function(html, "useCompanyIntakeResult")
    assert "selected_registry_result: selected" in start_body
    assert "country_of_incorporation: 'GB'" in start_body
    assert "provider: 'companies_house'" in start_body
    assert "session_reused" in start_body
    assert "currentApplicationId = companyIntakeState.application_id" in start_body
    assert "companyIntakeApplySessionStage(sessionStage)" in start_body
    assert "companyIntakeStepForStage(sessionStage)" in start_body


def test_profile_confirmation_requires_override_reason_and_prefills_application_form():
    html = _portal_html()
    lookup_view = _extract_div_by_id(html, "view-company-lookup")
    assert 'id="intake-override-reason"' in lookup_view
    assert "Required when you edit registry-sourced fields." in lookup_view

    override_body = _extract_js_function(html, "companyIntakeCollectProfileOverrides")
    assert "intake-override-reason" in override_body
    assert "override_reason" in override_body
    assert "Please add an override reason" in override_body

    confirm_body = _extract_js_function(html, "confirmCompanyIntakeProfile")
    assert "apiCall('POST', '/company-intake/confirm-profile'" in confirm_body
    assert "overrides: overrides" in confirm_body

    prefill_body = _extract_js_function(html, "applyCompanyIntakePrefillToForm")
    for field_id in (
        "f-reg-name",
        "f-entity-type",
        "f-reg-address",
        "f-inc-date",
        "f-inc-country",
        "f-brn",
        "f-biz-overview",
    ):
        assert field_id in prefill_body
    assert "prefill-notice" in prefill_body


def test_officer_confirmation_displays_corporate_director_review_not_individual_kyc():
    html = _portal_html()
    lookup_view = _extract_div_by_id(html, "view-company-lookup")
    assert "Confirm active directors, officers, or LLP member candidates imported from Companies House." in lookup_view
    assert "Loading officer candidates..." in lookup_view
    assert "Companies House did not return active director, officer, or member candidates for this company." in html
    assert "Companies House did not return active director candidates for this company." not in html

    render_body = _extract_js_function(html, "renderCompanyIntakeOfficers")
    assert "officer_entity_type" in render_body
    assert "requires_individual_kyc" in render_body
    assert "requires_corporate_structure_review" in render_body
    assert "Individual KYC required: " in render_body
    assert "Corporate structure review required: " in render_body
    assert "Corporate structure review applies" in render_body
    assert "This officer or member is not treated as an ordinary individual KYC candidate" in render_body

    confirm_body = _extract_js_function(html, "confirmCompanyIntakeOfficers")
    assert "apiCall('POST', '/company-intake/confirm-officers'" in confirm_body
    assert "companyIntakeState.imported_officers = selected" in confirm_body
    assert "imported_count" in confirm_body
    assert "skipped_count" in confirm_body


def test_psc_branches_render_clear_compliance_review_messages():
    html = _portal_html()
    render_body = _extract_js_function(html, "renderCompanyIntakePSCs")
    assert "psc_found" in render_body
    assert "no_psc" in render_body
    assert "psc_exempt" in render_body
    assert "corporate_psc" in render_body
    assert "These are candidates for compliance review, not final approved UBOs." in render_body
    assert "Companies House did not return an active PSC record. ARIE may still require ownership confirmation during compliance review." in render_body
    assert "Companies House indicates PSC information may be exempt or unavailable. This will be flagged for compliance review." in render_body
    assert "A corporate PSC candidate was returned. A structure chart / ownership explanation may be required later." in render_body

    confirm_body = _extract_js_function(html, "confirmCompanyIntakePSCs")
    assert "apiCall('POST', '/company-intake/confirm-pscs'" in confirm_body
    assert "pscs: pscResult" in confirm_body
    assert "companyIntakeState.imported_pscs = pscResult" in confirm_body


def test_manual_fallback_and_save_draft_paths_are_preserved():
    html = _portal_html()
    manual_body = _extract_js_function(html, "proceedManually")
    assert "resetPortalApplicationState();" in manual_body
    assert "showView('prescreening')" in manual_body
    assert "Manual Entry Mode" in manual_body

    assert "async function saveDraft()" in html
    assert "/save-resume" in html
    assert "btn-save-draft" in html

    continue_body = _extract_js_function(html, "continueCompanyIntakeToApplicationForm")
    assert "currentApplicationId = companyIntakeState.application_id" in continue_body
    assert "showView('prescreening')" in continue_body
    assert "applyCompanyIntakePrefillToForm()" in continue_body
    assert "_setSaveStatus('Registry prefill ready to review'" in continue_body


def test_assisted_form_submit_preserves_imported_directors_and_ubo_candidates():
    html = _portal_html()
    submit_body = _extract_js_function(html, "submitPrescreening")
    assert "companyIntakeShouldPreserveImportedDirectors()" in submit_body
    assert "companyIntakeShouldPreserveImportedUBOs()" in submit_body
    assert "if (!preserveImportedDirectors)" in submit_body
    assert "if (!preserveImportedUbos)" in submit_body
    assert "if (preserveImportedDirectors) delete payload.directors;" in submit_body
    assert "if (preserveImportedUbos) delete payload.ubos;" in submit_body

    preserve_directors_body = _extract_js_function(html, "companyIntakeShouldPreserveImportedDirectors")
    preserve_ubos_body = _extract_js_function(html, "companyIntakeShouldPreserveImportedUBOs")
    assert "companyIntakeIsCurrentDraft()" in preserve_directors_body
    assert "officers_confirmed === true" in preserve_directors_body
    assert "companyIntakeIsCurrentDraft()" in preserve_ubos_body
    assert "pscs_confirmed === true" in preserve_ubos_body


def test_assisted_handoff_renders_read_only_imported_party_summaries_above_long_form_tables():
    html = _portal_html()
    directors_marker = 'id="company-intake-imported-directors"'
    directors_table_marker = 'id="directors-container"'
    pscs_marker = 'id="company-intake-imported-pscs"'
    ubos_table_marker = 'id="ubos-container"'
    assert directors_marker in html
    assert pscs_marker in html
    assert html.index(directors_marker) < html.index(directors_table_marker)
    assert html.index(pscs_marker) < html.index(ubos_table_marker)

    summary_head_body = _extract_js_function(html, "companyIntakeImportedSummaryHead")
    assert "Source: Companies House" in summary_head_body

    directors_summary_body = _extract_js_function(html, "renderCompanyIntakeImportedDirectorsSummary")
    assert "Imported directors / officers / members from Companies House" in directors_summary_body
    assert "director, officer, or member candidates" in directors_summary_body
    assert "Missing fields requiring client completion" in directors_summary_body
    assert "Individual KYC required" in directors_summary_body
    assert "Corporate structure review required" in directors_summary_body

    officer_missing_body = _extract_js_function(html, "companyIntakeOfficerMissingFields")
    assert "Full date of birth" in officer_missing_body
    assert "PEP declaration" in officer_missing_body

    psc_summary_body = _extract_js_function(html, "renderCompanyIntakeImportedPscSummary")
    assert "Imported PSC / beneficial-owner candidates" in psc_summary_body
    assert "Missing fields requiring client completion" in psc_summary_body
    assert "candidate beneficial owners for compliance review, not final approved UBOs" in psc_summary_body

    psc_missing_body = _extract_js_function(html, "companyIntakePscMissingFields")
    assert "Full date of birth" in psc_missing_body
    assert "PEP declaration" in psc_missing_body
    assert "Exact ownership percentage" in psc_missing_body

    prefill_body = _extract_js_function(html, "applyCompanyIntakePrefillToForm")
    assert "renderCompanyIntakeImportedPartySummaries()" in prefill_body


def test_no_secret_or_raw_provider_surface_added_to_portal():
    html = _portal_html()
    lookup_view = _extract_div_by_id(html, "view-company-lookup")
    for forbidden in (
        "COMPANIES_HOUSE_API_KEY",
        "api.company-information.service.gov.uk",
        "raw_response_json",
        "chatbot",
        "OCR",
        "back-office panel",
    ):
        assert forbidden not in html
        assert forbidden not in lookup_view
