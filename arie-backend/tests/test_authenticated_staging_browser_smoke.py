from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "arie-backend" / "scripts" / "qa" / "staging_browser_smoke.js"
ROLE_MATRIX_HARNESS = (
    ROOT / "arie-backend" / "scripts" / "qa" / "application_role_matrix_harness.py"
)
RUNBOOK = ROOT / "docs" / "DEPLOYMENT_RUNBOOK.md"


def _script_text():
    return SCRIPT.read_text(encoding="utf-8")


def test_authenticated_staging_browser_smoke_requires_approved_login_env():
    text = _script_text()

    assert "STAGING_QA_EMAIL" in text
    assert "STAGING_QA_PASSWORD" in text
    assert "Missing required environment variables" in text
    assert "ui-form" in text
    assert "Credential values must be supplied via environment variables" in text


def test_authenticated_staging_browser_smoke_does_not_embed_credentials_or_bypass_auth():
    text = _script_text()

    assert "StagingQa2026" not in text
    assert "m.dubois@ariefinance.mu" not in text
    assert "localStorage.setItem" not in text
    assert "sessionStorage.setItem" not in text
    assert "Authorization: Bearer" not in text
    assert "BACKOFFICE_TOKEN" not in text
    assert "tokenInjectionUsed: false" in text
    assert "authBypassUsed: false" in text


def test_authenticated_staging_browser_smoke_covers_required_backoffice_surfaces():
    text = _script_text()

    for expected in [
        "Applications",
        "Application Detail",
        "KYC Documents",
        "Screening Review",
        "Screening Queue",
        "Screening Queue Filters and Pagination",
        "Screening Evidence",
        "Four-Eyes Screening Controls",
        "Risk Scoring Model",
        "Authoritative Risk Evidence",
        "RegMind AI Compliance Supervisor",
        "Lifecycle Tab",
        "Case Management",
        "RegMind Monitoring",
        "Monitoring Alerts",
        "Monitoring Pilot Scope",
        "Lifecycle Queue",
        "EDD",
        "Change Management",
    ]:
        assert expected in text

    for check_name in [
        "applicationsPageLoads",
        "applicationDetailLoads",
        "overviewTabLoads",
        "lifecycleTabLoads",
        "kycDocumentsTabLoads",
        "kycDocumentEvidenceRenders",
        "screeningReviewTabLoads",
        "applicationScreeningEvidenceRenders",
        "complianceSupervisorTabLoads",
        "alertsTabLoads",
        "screeningQueueLoads",
        "screeningQueueFiltersRender",
        "screeningQueuePaginationRenders",
        "fixturesExcludedFromDefaultScreeningQueue",
        "pendingOrErroredScreenNeverAppearsClear",
        "screeningReviewOpens",
        "erroredScreenHasNoDispositionAction",
        "populatedScreeningEvidenceRenders",
        "fourEyesStateRenders",
        "fourEyesQueueOffersExactSecondReviewAction",
        "screeningDispositionControlsRespectRoleAndState",
        "riskModelBackendProjectionLoads",
        "riskModelPageLoads",
        "riskModelRemainsReadOnly",
        "riskModelUsesBackendProjection",
        "riskModelAdminBoundaryEnforced",
        "authoritativeRiskEvidenceRenders",
        "riskExportControlsRender",
        "memoControlsRetainBackendDerivedState",
        "applicationDecisionControlsRetainBackendDerivedState",
        "caseManagementLoads",
        "ongoingMonitoringLoads",
        "monitoringAlertsLoad",
        "monitoringPilotScopeLoad",
        "lifecycleQueueLoads",
        "eddWorkflowLoads",
        "changeManagementLoads",
    ]:
        assert check_name in text


def test_authenticated_staging_browser_smoke_records_browser_evidence():
    text = _script_text()

    assert "page.on(\"console\"" in text
    assert "page.on(\"pageerror\"" in text
    assert "page.on(\"requestfailed\"" in text
    assert "page.on(\"response\"" in text
    assert "badResponses" in text
    assert "failedRequests" in text
    assert "blockingConsoleErrors" in text
    assert "nonBlockingConsoleErrors" in text
    assert "noBlockingConsoleErrors" in text
    assert "screenshots" in text
    assert "report.json" in text
    assert "screenshot(page" in text
    assert "providerLabelFindings" in text
    assert "scanRemovedProviderLabels" in text
    assert "noRemovedProviderLabels" in text
    assert "applicationStatusTokenFindings" in text
    assert "scanApplicationStatusTokens" in text
    assert "noRawApplicationStatusTokenStatusSurfaces" in text
    assert "noRawApplicationStatusTokenFixtureNames" in text
    assert "noVisibleInternalApplicationStatusReasonCodes" in text
    assert "memoControlState" in text
    assert "applicationDecisionControlState" in text
    assert "expectedCanonicalControlState" in text
    assert "kycDocumentSemanticEvidence" in text
    assert "applicationScreeningSemanticEvidence" in text
    assert "populatedScreeningSemanticEvidence" in text
    assert "authoritativeRiskDisplay" in text
    assert "providerErrorScreeningState" in text
    assert "fourEyesQueueState" in text
    assert "fourEyesQueueActionState" in text
    assert "riskRuntimeProjection" in text


def test_authenticated_staging_browser_smoke_is_read_only_on_protected_records():
    text = _script_text()

    assert "const appId = roleMatrixProfile" in text
    assert ': "RM-PILOT-039";' in text
    assert "STAGING_SMOKE_APP_ID is required for the role-matrix profile." in text
    assert "requestedApplicationOpened" in text
    assert 'fill("ARF-QAFIX-004")' in text
    assert 'fill("ARF-QAFIX-006")' in text
    assert "submitScreeningDisposition" not in text
    assert "approveApplication()" not in text
    assert "approveMemo()" not in text
    assert "downloadRiskCSV()" in text
    assert "downloadRiskPDF()" in text
    assert 'button:has-text("View")' in text


def test_browser_profiles_keep_canonical_smoke_and_role_matrix_targets_distinct():
    browser = _script_text()
    role_matrix = ROLE_MATRIX_HARNESS.read_text(encoding="utf-8")

    assert 'const PROTECTED_CANONICAL_PROFILE = "protected-canonical"' in browser
    assert 'const ROLE_MATRIX_PROFILE = "role-matrix"' in browser
    assert "if (!roleMatrixProfile)" in browser
    assert "openedApplication" in browser
    assert "authenticatedRoleMatchesExpected" in browser
    assert '"STAGING_SMOKE_PROFILE": "role-matrix"' in role_matrix
    assert '"STAGING_SMOKE_EXPECTED_ROLE": role' in role_matrix
    assert '"STAGING_SMOKE_APP_ID": expected_application["id"]' in role_matrix
    assert "browser_report.get(\"applicationId\") == expected_application[\"id\"]" in role_matrix
    assert "opened_application.get(\"id\") == expected_application[\"id\"]" in role_matrix
    assert '"opened_application_id": opened_application["id"]' in role_matrix


def test_authenticated_staging_browser_smoke_asserts_exact_backend_derived_controls():
    text = _script_text()

    assert 'applicationStatus: String(app.statusRaw' in text
    assert 'memoReviewStatus: String(memo.review_status' in text
    assert 'approveMemo?.disabled === true' in text
    assert "await page.waitForFunction(() => {" in text
    assert 'applicationRef === "RM-PILOT-039"' in text
    assert 'applicationStatus === "approved"' in text
    assert 'memoReviewStatus === "approved"' in text
    assert "controlIsEnabled(report.observations.memoControlState.generate)" in text
    assert "controlIsEnabled(report.observations.memoControlState.validate)" in text
    assert "controlIsDisabled(" in text
    assert "/already been approved|already approved/i" in text
    assert "/already approved|terminal/i" in text


def test_authenticated_staging_browser_smoke_rejects_weak_content_and_four_eyes_checks():
    text = _script_text()

    assert "renderedIdentityCount > 0" in text
    assert "reviewStatuses.length > 0" in text
    assert "verificationStatuses.length > 0" in text
    assert "evidenceItemCount > 0" in text
    assert "providerReferenceCount > 0" in text
    assert "renderedHitCount > 0" in text
    assert "hasLoadingOrErrorState === false" in text
    assert '"Clear as False Positive"' in text
    assert '"pending_second_review"' in text
    assert "detailDispositionActions" in text
    assert '"Confirm True Match"' in text
    assert '"Escalate"' in text
    assert '"Request More Information"' in text
    assert ".detailDispositionActions.length === 1" in text
    assert '.detailDispositionActions[0].text === "Clear as False Positive"' in text
    assert ".detailDispositionActions[0].disabled === false" in text


def test_authenticated_staging_browser_smoke_categorizes_application_status_tokens():
    text = _script_text()

    assert "submitted_to_compliance" in text
    assert "officer_submitted_to_compliance" in text
    assert "officerStatusSurfaces" in text
    assert "fixturePartyNames" in text
    assert "visibleInternalMachineCodes" in text
    assert "storageMachineCodes" in text
    assert "submitted_to_compliance\\s+(director|owner|ubo|beneficial owner|fixture)" in text
    assert "category: \"storageMachineCodes\"" in text


def test_authenticated_staging_browser_smoke_classifies_known_role_denials_as_non_blocking():
    text = _script_text()

    assert "isNonBlockingConsoleError" in text
    assert "BO API Error: GET /users Error: Insufficient permissions" in text
    assert "BO API Error: GET /audit?limit=100 Error: Insufficient permissions" in text
    assert "Failed to load resource: the server responded with a status of 403" in text
    assert "knownRoleDeniedResponses.length > 0" in text


def test_deployment_runbook_documents_authenticated_browser_smoke_securely():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    section = runbook.split("### Authenticated staging browser smoke", 1)[1].split("### Manual validation", 1)[0]

    assert "arie-backend/scripts/qa/staging_browser_smoke.js" in section
    assert "STAGING_QA_EMAIL" in section
    assert "STAGING_QA_PASSWORD" in section
    assert "STAGING_SMOKE_OUT_DIR" in section
    assert "PLAYWRIGHT_NODE_MODULES" in section
    assert "real back-office login form" in section
    assert "Do not paste credentials" in section
    assert "Do not inject tokens" in section
    assert "StagingQa2026" not in section
    assert "m.dubois@ariefinance.mu" not in section
