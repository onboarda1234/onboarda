import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_HTML = REPO_ROOT / "arie-portal.html"
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"


def _portal_html() -> str:
    return PORTAL_HTML.read_text(encoding="utf-8")


def _backoffice_html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


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


def _extract_table_headers(html: str, table_id: str) -> list[str]:
    marker = f'id="{table_id}"'
    marker_index = html.index(marker)
    table_start = html.rfind("<table", 0, marker_index)
    thead_end = html.index("</thead>", marker_index)
    return [
        re.sub(r"<.*?>", "", match).strip()
        for match in re.findall(r"<th>(.*?)</th>", html[table_start:thead_end], flags=re.S)
    ]


def _extract_select_restore_aliases(html: str, alias_key: str) -> dict[str, str]:
    marker = f"'{alias_key}': {{"
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
                body = html[brace + 1:pos]
                return dict(re.findall(r"'([^']+)': '([^']+)'", body))
    raise AssertionError(f"Could not extract aliases for {alias_key}")


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
    assert "companyIntakeState.imported_director_rows" in confirm_body
    assert "companyIntakeState.imported_officers = companyIntakeState.imported_director_rows" in confirm_body
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
    assert "delete payload.directors" not in submit_body
    assert "delete payload.ubos" not in submit_body
    assert "country_of_residence" in submit_body
    assert "residential_address" in submit_body
    assert "date_of_appointment" in submit_body
    assert "registration_number" in submit_body
    assert "registered_address" in submit_body
    assert "owned_or_controlled_by" in submit_body

    preserve_directors_body = _extract_js_function(html, "companyIntakeShouldPreserveImportedDirectors")
    preserve_ubos_body = _extract_js_function(html, "companyIntakeShouldPreserveImportedUBOs")
    assert "companyIntakeIsCurrentDraft()" in preserve_directors_body
    assert "officers_confirmed === true" in preserve_directors_body
    assert "companyIntakeIsCurrentDraft()" in preserve_ubos_body
    assert "pscs_confirmed === true" in preserve_ubos_body


def test_application_layout_is_wider_for_related_party_tables():
    html = _portal_html()
    assert ".page.application-page { max-width: 1280px; }" in html
    assert '<div class="page application-page">' in html
    assert "#directors-container," in html
    assert "overflow-x: auto" in html
    assert "related-party-table" in html


def test_related_party_tables_have_approved_columns_only():
    html = _portal_html()
    assert _extract_table_headers(html, "directors-table") == [
        "First Name",
        "Last Name",
        "Nationality",
        "Date of Birth",
        "Country of Residence",
        "Residential Address",
        "Date of Appointment",
        "PEP",
        "",
    ]
    assert _extract_table_headers(html, "intermediaries-table") == [
        "Company Name",
        "Country of Incorporation",
        "Registration Number",
        "Registered Address",
        "% Ownership in Applicant",
        "Owned / Controlled By",
        "",
    ]
    assert _extract_table_headers(html, "ubos-table") == [
        "First Name",
        "Last Name",
        "Nationality",
        "Date of Birth",
        "Country of Residence",
        "Residential Address",
        "% Ownership",
        "PEP",
        "",
    ]

    for forbidden in ("Role", "capacity", "Entity type", "Nature of Control", "Notes"):
        assert forbidden not in _extract_table_headers(html, "directors-table")
        assert forbidden not in _extract_table_headers(html, "intermediaries-table")
        assert forbidden not in _extract_table_headers(html, "ubos-table")


def test_assisted_handoff_prefills_editable_tables_without_bulky_client_cards():
    html = _portal_html()
    assert 'id="company-intake-imported-directors"' not in html
    assert 'id="company-intake-imported-pscs"' not in html
    assert "Imported directors / officers / members from Companies House" not in html
    assert "Imported PSC / beneficial-owner candidates" not in html
    assert "Some details may have been pre-filled from registry data. Please complete any missing fields." in html

    prefill_body = _extract_js_function(html, "applyCompanyIntakePrefillToForm")
    assert "renderCompanyIntakeImportedPartySummaries()" in prefill_body

    table_apply_body = _extract_js_function(html, "applyCompanyIntakeImportedPartiesToTables")
    assert "companyIntakeImportedDirectorRowsForTable()" in table_apply_body
    assert "companyIntakeImportedUboRowsForTable()" in table_apply_body
    assert "companyIntakeImportedIntermediaryRowsForTable()" in table_apply_body

    director_map_body = _extract_js_function(html, "companyIntakeOfficerToDirectorRow")
    assert "companyIntakeIsCorporatePartyCandidate(officer)" in director_map_body
    assert "date_of_appointment" in director_map_body
    assert "country_of_residence" in director_map_body

    ubo_map_body = _extract_js_function(html, "companyIntakeOwnerToUboRow")
    assert "companyIntakeIsCorporatePartyCandidate(owner)" in ubo_map_body
    assert "country_of_residence" in ubo_map_body

    intermediary_map_body = _extract_js_function(html, "companyIntakeOwnerToIntermediaryRow")
    assert "registration_number" in intermediary_map_body
    assert "registered_address" in intermediary_map_body
    assert "owned_or_controlled_by" in intermediary_map_body

    collect_body = _extract_js_function(html, "collectFormData")
    for field in (
        "country_of_residence",
        "residential_address",
        "date_of_appointment",
        "registration_number",
        "registered_address",
        "owned_or_controlled_by",
    ):
        assert field in collect_body


def test_country_of_residence_restore_aliases_cover_uk_constituent_countries():
    html = _portal_html()
    aliases = _extract_select_restore_aliases(html, "nat-select")

    for value in (
        "england",
        "wales",
        "scotland",
        "northern ireland",
        "northern-ireland",
        "gb",
        "gbr",
        "uk",
        "british",
    ):
        assert aliases[value] == "United Kingdom"

    assert "_normalizeSelectToken(raw)" in _extract_js_function(html, "_restoreSelectValue")
    assert "restoreSelectAlias || 'nat-select'" in _extract_js_function(html, "setPartyRowValue")

    restore_body = _extract_js_function(html, "restorePartyRows")
    assert "setPartyRowValue(lastRow, 'country_of_residence', rowData.country_of_residence || '')" in restore_body

    for table_id in ("directors-table", "ubos-table"):
        marker = f'id="{table_id}"'
        table_start = html.rfind("<table", 0, html.index(marker))
        table_end = html.index("</table>", html.index(marker))
        table_html = html[table_start:table_end]
        assert 'class="nat-select" data-field="country_of_residence"' in table_html


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


def test_draft_company_documents_section_is_compact_and_draft_worded():
    html = _portal_html()
    section = _extract_div_by_id(html, "draft-company-documents-section")

    assert "Draft company documents" in section
    assert (
        "Generate draft company documents from the information entered in this application. "
        "Review, sign where required, and upload signed copies later in KYC &amp; Documents."
    ) in section
    assert section.count('id="draft-documents-trigger"') == 1
    assert "Generate drafts ▾" in section
    for option in (
        "Register of Directors",
        "Register of Members / Shareholders",
        "Ownership Structure Chart",
        "Download all drafts",
    ):
        assert option in section

    assert "btn-submit" not in section
    assert "document dashboard" not in section.lower()
    assert html.index('id="draft-company-documents-section"') > html.index('id="ubos-table"')
    assert html.index('id="draft-company-documents-section"') < html.index("<!-- 14. Consent & Data Protection -->")


def test_draft_company_documents_are_generated_client_side_from_form_data():
    html = _portal_html()
    draft_region = html[
        html.index("function toggleDraftDocumentsMenu"):
        html.index("function isDraftValueMeaningful")
    ]

    assert "function collectDraftCompanyDocumentData" in draft_region
    assert "collectFormData()" in draft_region
    assert "buildDraftRegisterOfDirectorsSection" in draft_region
    assert "buildDraftMembersRegisterSection" in draft_region
    assert "buildDraftOwnershipStructureSection" in draft_region
    assert "new Blob([html], { type: 'text/html;charset=utf-8' })" in draft_region
    assert "link.download = filename" in draft_region
    assert "apiCall(" not in draft_region
    assert "/api/" not in draft_region
    assert "handleKYCUpload" not in draft_region
    assert "submitDocuments" not in draft_region
    assert "checklist" not in draft_region.lower()
    assert "evidence" not in draft_region.lower()
    assert "document_status" not in draft_region

    for field in (
        "date_of_birth",
        "nationality",
        "country_of_residence",
        "residential_address",
        "date_of_appointment",
        "ownership_pct",
        "registration_number",
        "jurisdiction",
        "owned_or_controlled_by",
    ):
        assert field in draft_region


def test_draft_company_document_output_is_marked_draft_and_sanitized():
    html = _portal_html()
    draft_region = html[
        html.index("function toggleDraftDocumentsMenu"):
        html.index("function isDraftValueMeaningful")
    ]

    assert "DRAFT" in draft_region
    assert "Generated on:" in draft_region
    assert "Application reference:" in draft_region
    assert "Draft documents are generated from the information entered in this application." in draft_region
    assert "KYC &amp; Documents" in draft_region
    assert "Draft only. UBO data may not equal legal member/shareholder data." in draft_region
    assert "Review shareholder and member status before using this draft as a legal register." in draft_region

    for forbidden in (
        "raw_response_json",
        "source_metadata_json",
        "COMPANIES_HOUSE_API_KEY",
        "provider credentials",
        "ciphertext",
        "officially verified",
        "accepted evidence",
        "RegMind-certified",
        "approved register",
    ):
        assert forbidden not in draft_region


def test_draft_company_document_dynamic_values_are_escaped_before_html_output():
    html = _portal_html()
    esc_body = _extract_js_function(html, "draftCompanyDocumentEsc")
    assert "escapeHtml(draftCompanyDocumentValue(value))" in esc_body

    table_body = _extract_js_function(html, "draftCompanyDocumentTable")
    assert "draftCompanyDocumentEsc(header)" in table_body
    assert "draftCompanyDocumentEsc(cell)" in table_body
    assert "'<td>' + cell" not in table_body
    assert "'<th>' + header" not in table_body

    shell_body = _extract_js_function(html, "buildDraftCompanyDocumentHtml")
    assert "draftCompanyDocumentEsc(titleMap[type] || titleMap.all)" in shell_body
    assert "draftCompanyDocumentEsc(data.appRef)" in shell_body
    assert "draftCompanyDocumentEsc(generatedAt)" in shell_body
    assert "draftCompanyDocumentTable(['Company field', 'Value']" in shell_body

    structure_body = _extract_js_function(html, "buildDraftOwnershipStructureSection")
    assert "draftCompanyDocumentEsc(companyName + companyNumber)" in structure_body
    assert "draftCompanyDocumentEsc(member.owned_or_controlled_by)" in structure_body
    assert "draftCompanyDocumentEsc(member.entity_name + pct)" in structure_body
    assert "draftCompanyDocumentEsc(draftCompanyDocumentFullName(owner) + pct)" in structure_body


def test_draft_company_documents_do_not_add_backoffice_or_registry_badge_surface():
    portal_html = _portal_html()
    backoffice_html = _backoffice_html()

    for forbidden in (
        "draft-company-documents-section",
        "Generate drafts ▾",
        "Draft company documents",
    ):
        assert forbidden not in backoffice_html

    draft_section = _extract_div_by_id(portal_html, "draft-company-documents-section")
    for forbidden in (
        "registry-badge",
        "ch-registry-badge",
        "source_metadata_json",
        "raw_response_json",
        "response_hash",
    ):
        assert forbidden not in draft_section
