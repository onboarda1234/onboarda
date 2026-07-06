from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "arie-backend" / "scripts" / "qa" / "staging_browser_smoke.js"
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
        "AI Compliance Supervisor",
        "Lifecycle Tab",
        "Case Management",
        "Ongoing Monitoring",
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
        "lifecycleTabLoads",
        "kycDocumentsTabLoads",
        "screeningReviewTabLoads",
        "complianceSupervisorTabLoads",
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
