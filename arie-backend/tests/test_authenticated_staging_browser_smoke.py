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
        "Compliance Supervisor",
        "Lifecycle Tab",
        "Case Management",
        "Ongoing Monitoring",
        "Monitoring Alerts",
        "Monitoring Agents",
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
        "monitoringAgentsLoad",
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
    assert "noConsoleErrors" in text
    assert "screenshots" in text
    assert "report.json" in text
    assert "screenshot(page" in text


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
