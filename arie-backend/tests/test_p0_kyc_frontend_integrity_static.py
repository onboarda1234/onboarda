"""Static regression guards for the P0 KYC party/document hydration defect."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "arie-portal.html"
BACKOFFICE = ROOT / "arie-backoffice.html"
SERVER = ROOT / "arie-backend" / "server.py"
ENHANCED_REQUIREMENTS = ROOT / "arie-backend" / "enhanced_requirements.py"
DOCUMENT_RELIANCE_GATE = ROOT / "arie-backend" / "document_reliance_gate.py"
DOCUMENT_SCOPE_POLICY = ROOT / "arie-backend" / "document_scope_policy.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(source: str, name: str, *, async_function: bool = False) -> str:
    marker = f"{'async ' if async_function else ''}function {name}"
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for position in range(brace, len(source)):
        char = source[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : position + 1]
    raise AssertionError(f"Could not extract {name}")


def _python_function(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_pricing_review_hydrates_canonical_parties_before_acceptance():
    source = _read(PORTAL)
    pricing_projection = source.split("  pricing_review: {", 1)[1].split(
        "  pricing_accepted: {", 1
    )[0]
    accept_pricing = _function(source, "acceptPricing", async_function=True)

    assert "hydrateParties: true" in pricing_projection
    assert "apiCall('GET', '/applications/' + encodeURIComponent(currentApplicationId))" in accept_pricing
    assert "bindCanonicalPartyRecordsToRows(canonicalApplication)" in accept_pricing
    assert accept_pricing.index("bindCanonicalPartyRecordsToRows") < accept_pricing.index(
        "'/accept-pricing'"
    )
    assert "Pricing acceptance is blocked until ownership parties can be linked" in accept_pricing
    assert "renderPricingPartyIntegrityError([])" in accept_pricing
    assert "No pricing acceptance was recorded; retry or refresh" in accept_pricing
    render_error = _function(source, "renderPricingPartyIntegrityError")
    availability = _function(source, "updatePricingAcceptanceAvailability")
    assert "pricingPartyIntegrityErrors = normalizedErrors.slice()" in render_error
    assert "updatePricingAcceptanceAvailability()" in render_error
    assert "!!serverPricing" in availability
    assert "terms && terms.checked" in availability
    assert "pricingPartyIntegrityErrors.length === 0" in availability
    binder = _function(source, "bindCanonicalPartyRecordsToRows")
    assert "idMatches.length > 1 || keyMatches.length > 1" in binder
    assert "(personKey && keyMatches.length !== 1)" in binder
    assert "conflicting_or_ambiguous_party_identity" in binder
    assert "duplicate_canonical_party_binding" in binder
    assert "authoritativeIdentityTokens" in binder
    assert "authoritative_party_identity_namespace_collision" in binder
    assert "if (!errors.length) syncDirectorsUBOsToKYC()" in binder


def test_resume_hydrates_documents_after_the_final_card_rebuild():
    source = _read(PORTAL)
    resume = _function(source, "resumeApplication", async_function=True)

    assert resume.count("syncPersistedApplicationDocuments(resumedApplication)") == 1
    apply_index = resume.index("applyApplicationViewState(target)")
    document_index = resume.index("syncPersistedApplicationDocuments(resumedApplication)")
    review_index = resume.index("buildReviewScreen()", document_index)
    assert apply_index < document_index < review_index


def test_server_party_id_is_primary_and_legacy_person_key_is_only_an_alias():
    source = _read(PORTAL)
    restore = _function(source, "restorePartyRows")
    sync = _function(source, "syncDirectorsUBOsToKYC")
    upload = _function(source, "handleKYCUpload")
    matcher = _function(source, "findKYCPersonCard")

    assert "lastRow.dataset.partyId = String(rowData.id).trim()" in restore
    assert "lastRow.dataset.personKey = rowData.person_key" in restore
    assert "id: identity.id" in sync
    assert "card.dataset.partyId = p.partyId || ''" in sync
    assert "card.dataset.personKey = p.personKey || ''" in sync
    assert "card.dataset.personType = p.type" in sync
    assert "hasStablePartyId" in sync
    assert "Uploads are unavailable because this party is not linked" in sync
    assert "stablePartyId !== String(personId)" in upload
    assert "'&person_id=' + encodeURIComponent(stablePartyId)" in upload
    assert "identity.partyId === lookupId || identity.personKey === lookupId" in matcher
    assert "domPersonId === lookupId" in matcher
    assert "getElementById('kyc-person-' + lookupId)" not in matcher
    assert "if (matches.length === 1) return matches[0]" in matcher
    assert "KYC party alias is ambiguous; refusing to match" in matcher


def test_ad_hoc_kyc_person_cards_cannot_create_unresolved_upload_subjects():
    source = _read(PORTAL)
    add_person = _function(source, "addKYCPerson")

    assert 'onclick="addKYCPerson()"' not in source
    assert "createElement" not in add_person
    assert "handleKYCUpload" not in add_person
    assert "Ownership Update Required" in add_person


def test_professional_profile_is_hydrated_and_patched_by_stable_party_id():
    source = _read(PORTAL)
    upload_panel = _function(source, "buildUploadPanelHTML")
    save_profile = _function(source, "saveKYCProfessionalProfile", async_function=True)
    update_row = _function(source, "updateOwnershipRowProfessionalProfile")
    resume = _function(source, "resumeApplication", async_function=True)

    assert "value=\"' + escapeHtml(p.professionalProfileUrl || '')" in upload_panel
    assert "saveKYCProfessionalProfile" in upload_panel
    assert "card.dataset.partyId" in save_profile
    assert (
        "'/kyc/parties/' + encodeURIComponent(stablePartyId) + '/profile'"
        in save_profile
    )
    assert "person_type: normalizedPersonType" in save_profile
    assert "professional_profile_url: value" in save_profile
    assert "value === previousValue" in save_profile
    assert "input.dataset.profileSaveInFlight === 'true'" in save_profile
    assert "rowPartyId === partyId && rowPersonType === personType" in update_row
    assert "personKey" not in update_row
    assert "matchingRows.length === 1" in update_row
    assert resume.count("professional_profile_url:") >= 3
    review = _function(source, "buildReviewScreen")
    assert "professionalProfileValue === savedProfessionalProfileValue" in review
    assert "isValidPortalPathId(card.dataset && card.dataset.partyId)" in review
    assert "❌ Missing or not saved" in review


def test_unmatched_or_ambiguous_persisted_documents_fail_visibly_closed():
    source = _read(PORTAL)
    hydrate = _function(source, "syncPersistedApplicationDocuments")
    slot_validator = _function(source, "validatePersistedDocumentSlotIdentity")
    special_validator = _function(source, "validatePortalSpecialDocumentOwnership")
    render_person_verification = _function(source, "renderPersonVerification")
    submit = _function(source, "submitDocuments")
    final_submit = _function(source, "finalSubmitFromReview", async_function=True)

    assert 'id="portal-document-hydration-errors"' in source
    assert "findKYCPersonCard(storedPersonId, storedPersonType)" in hydrate
    assert "validatePersistedDocumentSlotIdentity(doc)" in hydrate
    assert "conflicting_document_slot_identity" in slot_validator
    assert "storedSlotKey !== expectedSlotKey" in slot_validator
    assert "'person:' + personType + ':' + personId + ':' + docType" in slot_validator
    assert "special_slot: true" in slot_validator
    assert "if (slotIdentity.special_slot) return" in hydrate
    assert "item.document_id" in special_validator
    assert "requirement.linked_document_id" in special_validator
    assert "ownerMatches.length !== 1" in special_validator
    assert "unresolved_or_ambiguous_special_document_owner" in special_validator
    assert "matches.length !== 1" in special_validator
    assert "unlinked_or_ambiguous_special_document_slot" in special_validator
    assert "duplicate_party_document_slot" in hydrate
    assert "document_category_unavailable" in hydrate
    assert "renderPortalDocumentHydrationErrors(hydrationErrors)" in hydrate
    assert "person_id: personId" in render_person_verification
    assert "slot_key: uiSlotKey" in render_person_verification
    assert "portalDocumentHydrationBlocked()" in submit
    assert "portalDocumentHydrationBlocked()" in final_submit


def test_intermediary_review_uses_explicit_type_and_only_corporate_requirements():
    source = _read(PORTAL)
    review = _function(source, "buildReviewScreen")

    assert "var isInter = personType === 'intermediary'" in review
    assert "startsWith('int')" not in review
    intermediary_branch = review.split("if (isInter) {", 1)[1].split("} else {", 1)[0]
    assert "['cert-inc', 'reg-dir', 'reg-sh', 'cert-gs', 'fin-stmt']" in intermediary_branch
    assert "'passport'" not in intermediary_branch
    assert "'poa'" not in intermediary_branch


def test_backoffice_retains_typed_document_ownership_and_displays_profiles():
    source = _read(BACKOFFICE)
    render_party = _function(source, "renderPartyCard")
    expected_slots = _function(source, "buildExpectedKycDocumentSlots")
    slot_matcher = _function(source, "documentMatchesExpectedSlot")
    readiness = _function(source, "computeDocumentReadinessSummary")
    integrity = _function(source, "validateBackofficeDocumentSlotIdentity")
    app_integrity = _function(source, "validateBackofficeDocumentIntegrity")
    party_matches = _function(source, "backofficePartyIdentityMatches")
    owner = _function(source, "findDocumentOwnerLabelForApp")
    taxonomy = _function(source, "renderStandardKycDocumentTaxonomy")

    assert "person_id: d.person_id" in source
    assert "person_type: d.person_type || ''" in source
    assert "person_id: doc.person_id || ''" in source
    assert "person_type: doc.person_type || ''" in source
    assert "professional_profile_url: d.professional_profile_url || ''" in source
    assert "professional_profile_url: u.professional_profile_url || ''" in source
    assert "renderPartyFact('Professional Profile', party.professional_profile_url, 'Not captured')" in render_party
    assert "partyDisplayValue(value, missingLabel)" in _function(source, "renderPartyFact")
    assert "person_id: person.id || person.person_key" in expected_slots
    assert "legacy_person_key: person.person_key || ''" in expected_slots
    assert "String(doc.person_type || '').toLowerCase()" in slot_matcher
    assert "expectedSlot.legacy_person_key" in slot_matcher
    assert "validateBackofficeDocumentIntegrity(app, doc)" in slot_matcher
    assert "storedSlotKey !== expectedSlotKey" in integrity
    assert "'person:' + personType + ':' + personId + ':' + docType" in integrity
    assert "special_slot: true" in integrity
    assert "person_id: person.id || person.person_key" in readiness
    assert "documentMatchesExpectedSlot(doc, expectation, app)" in readiness
    assert "documentIntegrityFailures.length > 0" in readiness
    assert "readinessUsedDocumentIds[documentId]" in readiness
    assert "app[collectionName]" in party_matches
    assert "ownerMatches.length !== 1" in app_integrity
    assert "item.document_id" in app_integrity
    assert "requirement.linked_document_id" in app_integrity
    assert "unlinked_or_ambiguous_special_document_slot" in app_integrity
    assert "validateBackofficeDocumentIntegrity(app, doc)" in owner
    assert "Unresolved document owner — integrity mismatch" in owner
    assert "matches.length === 1" in owner
    assert "renderBackofficeDocumentIntegrityBanner(documentIntegrityFailures)" in taxonomy
    assert "identity.valid && !identity.special_slot" in taxonomy


def test_kyc_applicant_registration_uses_exact_typed_canonical_party():
    source = _read(PORTAL)
    register = _function(source, "sendKYCLink", async_function=True)

    assert "card.dataset.partyId" in register
    assert "card.dataset.personType" in register
    assert "stablePartyId !== personId || !personType" in register
    assert "person_id.startsWith" not in register
    assert "external_user_id: personId" in register
    assert "person_type: personType" in register


def test_canonical_empty_party_collections_never_restore_stale_prescreening_rows():
    portal = _read(PORTAL)
    resume = _function(portal, "resumeApplication", async_function=True)
    review = _function(portal, "buildReviewScreen")
    submit = _function(portal, "submitDocuments")
    final_submit = _function(portal, "finalSubmitFromReview", async_function=True)

    canonical_branch = resume.split("var useCanonicalPartyRecords", 1)[1]
    assert "canonicalPartyCollectionIntegrityErrors(app)" in canonical_branch
    assert "Array.isArray(app.directors) ? app.directors : []" in canonical_branch
    assert "Array.isArray(app.ubos) ? app.ubos : []" in canonical_branch
    assert "Array.isArray(app.intermediaries) ? app.intermediaries : []" in canonical_branch
    assert "renderPortalPartyHydrationErrors(canonicalPartyErrors)" in canonical_branch
    assert "partyHydrationMismatchCount" in review
    assert "!portalPartyHydrationBlocked()" in review
    assert "portalPartyHydrationBlocked()" in submit
    assert "portalPartyHydrationBlocked()" in final_submit
    assert 'id="portal-party-hydration-errors"' in portal

    backoffice = _read(BACKOFFICE)
    detail = _function(backoffice, "fetchApplicationDetail", async_function=True)
    assert "Array.isArray(detail.directors) ? detail.directors : []" in detail
    assert "Array.isArray(detail.ubos) ? detail.ubos : []" in detail
    assert "Array.isArray(detail.intermediaries) ? detail.intermediaries : []" in detail
    assert "buildPrescreeningPartyFallback" not in detail
    assert "_partyIntegrityErrors: partyIntegrityErrors" in detail
    assert "backoffice-party-integrity-error" in _function(
        backoffice, "renderBackofficePartyIntegrityBanner"
    )
    assert "partyIntegrityErrors.length > 0" in _function(
        backoffice, "computeDocumentReadinessSummary"
    )


def test_cross_type_canonical_party_id_collision_blocks_all_portal_party_rendering():
    source = _read(PORTAL)
    collection_integrity = _function(
        source,
        "canonicalPartyCollectionIntegrityErrors",
    )
    sync = _function(source, "syncDirectorsUBOsToKYC")
    pricing = _function(source, "acceptPricing", async_function=True)
    render_errors = _function(source, "renderPortalPartyHydrationErrors")

    assert "canonical_party_id_cross_type_collision" in collection_integrity
    assert "involved_person_types" in collection_integrity
    assert "canonicalIdOwners[recordId] !== collection.type" in collection_integrity
    assert "portalPartyHydrationBlocked()" in sync
    assert "clearSyncedCanonicalKYCCards()" in sync
    assert sync.index("portalPartyHydrationBlocked()") < sync.index(
        "var persons = []"
    )
    assert "renderPortalPartyHydrationErrors(partyBindingErrors)" in pricing
    assert "clearSyncedCanonicalKYCCards()" in render_errors
    assert "reuse the same stable ID across different party types" in render_errors


def test_unknown_entity_document_category_fails_visible_instead_of_disappearing():
    portal = _read(PORTAL)
    hydrate = _function(portal, "syncPersistedApplicationDocuments")
    missing_slot_branch = hydrate.split(
        "var entitySlotId = findPortalDocumentSlot(doc.doc_type);", 1
    )[1].split("var entityUiSlotKey", 1)[0]

    assert "if (!entitySlotId)" in missing_slot_branch
    assert "hydrationErrors.push" in missing_slot_branch
    assert "document_category_unavailable" in missing_slot_branch
    assert "renderPortalDocumentHydrationErrors(hydrationErrors)" in hydrate


def test_enhanced_evidence_link_contract_is_server_enforced_and_ui_filtered():
    backoffice = _read(BACKOFFICE)
    options = _function(backoffice, "enhancedRequirementDocumentOptions")
    eligibility = _function(backoffice, "enhancedRequirementDocumentEligibleForLink")
    local_integrity = _function(backoffice, "enhancedRequirementDocumentLinkIntegrity")
    taxonomy = _function(backoffice, "renderStandardKycDocumentTaxonomy")
    readiness = _function(backoffice, "computeDocumentReadinessSummary")

    assert "enhancedRequirementDocumentEligibleForLink(req, doc)" in options
    assert "enhancedRequirementDocumentLinkIntegrity(req, doc).valid === true" in eligibility
    assert "'enhanced_requirement:' + String(req.id || '').trim()" in local_integrity
    assert "document_slot_mismatch" in local_integrity
    assert "Base KYC documents cannot be reused as enhanced evidence" in local_integrity
    assert "backoffice-enhanced-document-link-integrity-error" in backoffice
    assert "renderEnhancedRequirementDocumentIntegrityBanner" in taxonomy
    assert "enhancedDocumentLinkIntegrityCount" in readiness

    enhanced = _read(ENHANCED_REQUIREMENTS)
    validator = _python_function(enhanced, "validate_enhanced_requirement_document_link")
    update = _python_function(enhanced, "update_application_enhanced_requirement")
    fulfill = _python_function(enhanced, "fulfill_application_enhanced_requirement_document")
    approval = _python_function(enhanced, "validate_enhanced_requirements_for_approval")
    assert "document_not_current" in validator
    assert "document_type_mismatch" in validator
    assert "document_slot_mismatch" in validator
    assert "document_owner_mismatch" in validator
    assert "document_linked_to_other_requirement" in validator
    assert "validate_enhanced_requirement_document_link" in update
    assert "validate_enhanced_requirement_document_link" in fulfill
    assert "_approval_document_link_integrity_item" in approval


def test_backoffice_maps_server_accepted_missing_link_to_named_red_blocker():
    source = _read(BACKOFFICE)
    mapper = _function(
        source,
        "applyEnhancedRequirementServerDocumentIntegrity",
    )
    errors = _function(source, "enhancedRequirementDocumentIntegrityErrors")
    badge = _function(source, "enhancedRequirementStatusBadge")
    evidence_group = _function(source, "renderEnhancedEvidenceDocumentsGroupHtml")
    renderer = _function(source, "renderApplicationEnhancedRequirements")
    readiness = _function(source, "computeDocumentReadinessSummary")

    assert "summary.invalid_document_links" in mapper
    assert "invalid.document_integrity" in mapper
    assert "linked_document_integrity_valid = false" in mapper
    assert "_server_document_integrity_invalid = true" in mapper
    assert "enhancedRequirementHasServerIntegrityFailure(req)" in errors
    assert "enhancedRequirementLinkIntegrityForDisplay(req)" in errors
    assert "Accepted — evidence missing" in badge
    assert "enhanced-document-link-integrity-error" in evidence_group
    assert "Approval is blocked" in evidence_group
    assert "applyEnhancedRequirementServerDocumentIntegrity" in renderer
    assert "enhancedLinkDescriptions" in readiness
    assert "persisted evidence association is inconsistent" in readiness


def test_special_enhanced_slots_do_not_compete_with_base_reliance_and_full_view_is_typed():
    reliance = _python_function(
        _read(DOCUMENT_RELIANCE_GATE),
        "_select_expected_document",
    )
    server = _read(SERVER)
    pilot = _python_function(server, "_pilot_evidence_classification_summary")

    assert 'actual_slot.startswith("enhanced_requirement:")' in reliance
    assert "continue" in reliance.split(
        'actual_slot.startswith("enhanced_requirement:")', 1
    )[1][:100]
    assert "validate_enhanced_requirement_document_link" in pilot
    assert "document_link_integrity" in pilot
    full_projection = server.split("# Batch fetch documents (1 query)", 1)[1].split(
        "rmi_by_app =", 1
    )[0]
    assert "person_id, person_type, review_status" in full_projection


def test_ordinary_upload_scope_policy_rejects_entity_identity_but_preserves_high_risk_person_docs():
    server = _read(SERVER)
    scope_policy = _read(DOCUMENT_SCOPE_POLICY)
    scope = _python_function(server, "_base_document_scope_error")
    assert '"passport"' in scope_policy.split("INDIVIDUAL_BASE_DOCUMENT_TYPES", 1)[1].split(
        "}", 1
    )[0]
    assert '"bankref"' in scope_policy.split("INDIVIDUAL_BASE_DOCUMENT_TYPES", 1)[1].split(
        "}", 1
    )[0]
    assert '"source_wealth"' in scope_policy.split("INDIVIDUAL_BASE_DOCUMENT_TYPES", 1)[1].split(
        "}", 1
    )[0]
    assert "base_document_scope_error_for_canonical_type" in scope
    assert "_base_document_scope_error(requested_doc_type, person_type)" in server
    assert "requested_doc_type != canonical_base_type" in server
    assert '"noncanonical_doc_type"' in server
    assert "Non-canonical document type suffixes are not accepted" in server
