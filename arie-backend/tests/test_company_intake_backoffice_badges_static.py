from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"
PORTAL_HTML = REPO_ROOT / "arie-portal.html"


def _html(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _backoffice_html() -> str:
    return _html(BACKOFFICE_HTML)


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


def test_registry_badge_legend_is_single_compact_prescreen_legend():
    html = _backoffice_html()
    summary_body = _extract_js_function(html, "renderPrescreenSummary")
    badge_body = _extract_js_function(html, "renderCompaniesHouseRegistryBadge")
    legend_body = _extract_js_function(html, "renderCompaniesHouseIndicatorLegend")

    assert html.count("Registry indicators:") == 1
    assert summary_body.count("renderCompaniesHouseIndicatorLegend(app)") == 1
    assert "✓ Verified" in badge_body
    assert "Review" in badge_body
    assert "Issue" in badge_body
    assert "✅ Verified" not in badge_body
    assert "⚠️ Review" not in badge_body
    assert "🔴 Registry issue" not in badge_body
    assert "ch-indicator-legend-separator" in legend_body


def test_registry_badge_css_is_compact_and_not_a_large_notice_panel():
    html = _backoffice_html()

    assert ".ch-registry-badge" in html
    assert "font-size:9px" in html
    assert "padding:1px 5px" in html
    assert "min-height:16px" in html
    assert ".ch-party-review-note" not in html


def test_company_profile_badge_logic_uses_registry_values_overrides_and_status_issue():
    html = _backoffice_html()
    field_body = _extract_js_function(html, "renderCompaniesHouseFieldBadges")
    override_body = _extract_js_function(html, "registryFieldOverride")
    sourced_body = _extract_js_function(html, "registrySourcedValueForField")
    status_body = _extract_js_function(html, "registryCompanyStatusHasMaterialIssue")

    assert "registry_sourced_values" in sourced_body
    assert "registry_field_overrides" in override_body
    assert "registryCompanyStatusHasMaterialIssue(app)" in field_body
    assert "registryComparableValue(currentRaw)" in field_body
    assert "renderCompaniesHouseRegistryBadge('verified'" in field_body
    assert "renderCompaniesHouseRegistryBadge('review'" in field_body
    assert "renderCompaniesHouseRegistryBadge('issue'" in field_body
    for status in ("inactive", "dissolved", "liquidation", "administration", "receivership", "insolvency", "removed", "closed"):
        assert status in status_body


def test_company_profile_target_fields_are_wired_for_registry_badges():
    html = _backoffice_html()
    detail_body = _extract_js_function(html, "renderAuthoritativeAppDetail")

    assert "key: 'registered_entity_name'" in detail_body
    assert "key: 'entity_type'" in detail_body
    assert "key: 'registered_address'" in detail_body
    assert "registered_office_address" in detail_body
    assert "key: 'incorporation_date'" in detail_body
    assert "key: 'country_of_incorporation'" in detail_body
    assert "key: 'registration_number'" in detail_body
    assert "company_number" in detail_body


def test_party_mapping_preserves_pr570_fields_and_sanitized_registry_provenance():
    html = _backoffice_html()
    fetch_body = _extract_js_function(html, "fetchApplicationDetail")

    for field in (
        "first_name",
        "last_name",
        "nationality",
        "date_of_birth",
        "country_of_residence",
        "residential_address",
        "date_of_appointment",
        "ownership_pct",
        "registered_address",
        "registration_number",
        "owned_or_controlled_by",
        "source",
        "officer_role",
        "officer_entity_type",
        "requires_individual_kyc",
        "requires_corporate_structure_review",
        "registry_lookup_id",
        "response_hash",
        "imported_at",
        "imported_by",
        "psc_state",
        "registry_statement_type",
        "psc_status_reason",
        "psc_kind",
        "is_candidate_ubo",
    ):
        assert field in fetch_body
    assert "source_metadata_json" not in fetch_body


def test_imported_individual_directors_members_and_corporate_members_have_distinct_badges():
    html = _backoffice_html()
    party_badge_body = _extract_js_function(html, "renderCompaniesHousePartyBadge")
    party_card_body = _extract_js_function(html, "renderPartyCard")

    assert "partyType === 'director'" in party_badge_body
    assert "officer_entity_type" in party_badge_body
    assert "requires_corporate_structure_review" in party_badge_body
    assert "requires_individual_kyc" in party_badge_body
    assert "Corporate director — corporate structure review required." in party_badge_body
    assert "Corporate LLP member — corporate structure review required." in party_badge_body
    assert "Director, officer, or member imported from Companies House." in party_badge_body
    assert "role.indexOf('secretary')" in party_badge_body
    assert "renderCompaniesHouseRegistryBadge('review'" in party_badge_body
    assert "renderCompaniesHouseRegistryBadge('verified'" in party_badge_body
    assert "registryBadge" in party_card_body


def test_psc_candidate_no_psc_exempt_and_corporate_intermediary_states_have_badges():
    html = _backoffice_html()
    party_badge_body = _extract_js_function(html, "renderCompaniesHousePartyBadge")
    psc_section_body = _extract_js_function(html, "renderCompaniesHousePscSectionBadge")
    party_section_body = _extract_js_function(html, "renderPartySection")

    assert "partyType === 'ubo'" in party_badge_body
    assert "partyType === 'intermediary'" in party_badge_body
    assert "psc_found" in party_badge_body
    assert "corporate_psc" in party_badge_body
    assert "is_candidate_ubo" in party_badge_body
    assert "PSC candidate imported from Companies House." in party_badge_body
    assert "Corporate PSC — ownership structure review required." in party_badge_body
    assert "Corporate PSC or intermediary ownership structure review required." in party_badge_body
    assert "no_psc" in psc_section_body
    assert "psc_exempt" in psc_section_body
    assert "No active PSC returned — ownership confirmation required." in psc_section_body
    assert "PSC information exempt or unavailable — officer review required." in psc_section_body
    assert "renderCompaniesHousePscSectionBadge(app, 'ubo')" in party_section_body
    assert "renderCompaniesHousePscSectionBadge(app, 'intermediary')" in party_section_body
    assert "final approved UBO" not in party_badge_body


def test_no_badge_rendered_when_party_has_no_registry_evidence():
    html = _backoffice_html()
    evidence_body = _extract_js_function(html, "partyHasRegistryEvidence")
    party_badge_body = _extract_js_function(html, "renderCompaniesHousePartyBadge")

    assert "partySourceIsCompaniesHouse(party) || !!party.psc_state" in evidence_body
    assert "registry_lookup_id" not in evidence_body
    assert "if (!partyHasRegistryEvidence(party)) return ''" in party_badge_body


def test_badges_are_backoffice_only_not_rendered_in_client_portal():
    portal = _html(PORTAL_HTML)

    assert "ch-registry-badge" not in portal
    assert "ch-indicator-legend" not in portal
    assert "renderCompaniesHouseRegistryBadge" not in portal
    assert "Registry indicators:" not in portal


def test_no_unavailable_badge_approval_language_or_raw_registry_payload_surface_added():
    html = _backoffice_html()
    badge_body = _extract_js_function(html, "renderCompaniesHouseRegistryBadge")
    legend_body = _extract_js_function(html, "renderCompaniesHouseIndicatorLegend")
    party_badge_body = _extract_js_function(html, "renderCompaniesHousePartyBadge")
    party_card_body = _extract_js_function(html, "renderPartyCard")

    for forbidden in (
        "Registry unavailable",
        "Not verified",
        "Missing registry data",
        "Approved",
        "Cleared",
        "KYC passed",
        "raw_response_json",
        "COMPANIES_HOUSE_API_KEY",
        "api.company-information.service.gov.uk",
        "source_metadata_json",
    ):
        assert forbidden not in badge_body
        assert forbidden not in legend_body
        assert forbidden not in party_badge_body
        assert forbidden not in party_card_body
    assert "raw_response_json" not in html
    assert "COMPANIES_HOUSE_API_KEY" not in html
