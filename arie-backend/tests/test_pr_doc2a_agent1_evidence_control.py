import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "arie-backend"
sys.path.insert(0, str(BACKEND_ROOT))


def _backoffice_html() -> str:
    return (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")


def _backend_db_py() -> str:
    return (BACKEND_ROOT / "db.py").read_text(encoding="utf-8")


def _verification_renderer(html: str) -> str:
    start = html.index("function buildVerificationResultsHtml")
    end = html.index("function renderDocumentAuditDetails", start)
    return html[start:end]


def test_doc2a_default_document_verification_ui_is_decision_first():
    html = _backoffice_html()
    renderer = _verification_renderer(html)

    assert "Technical audit details" in renderer
    assert "Checks requiring attention" in renderer
    assert "Passed technical checks" in renderer
    assert "Agent execution ID" in renderer
    assert "Evidence hash" in renderer
    assert "Verification timestamp" in renderer
    assert "Policy ID/version" in renderer
    assert "Material findings" in renderer
    assert "Expected checks missing" in renderer
    assert "Failed or warning checks" not in renderer
    assert "Overall Result" not in renderer
    assert "Show technical verification checks" not in renderer


def test_doc2a_evidence_control_card_surfaces_required_reliance_fields():
    html = _backoffice_html()

    assert "function renderUnifiedKycDocumentCard(app, doc, linkedRequirement" in html
    assert "document-review-row" in html
    assert "Lifecycle context" in html
    assert "Policy ID/version" in html
    assert "Verification timestamp" in html
    assert "Uploaded by" in html
    assert "Next:" in html
    assert "Issue" in html
    assert "Verification details" in html
    assert "Verified" in html
    assert "Review required" in html
    assert "Failed" in html
    assert "Pending verification" in html
    assert "Stale" in html
    assert "Manual accepted" in html
    assert "Rejected" in html
    assert "Request replacement" in html
    assert "warnings.length > 0 || issues.length > 0 || hasWarningCheck" in html
    assert "hasFailedCheck" in html
    assert "Pilot Evidence Classification" not in html
    assert "Pilot evidence classification" not in html
    assert "Document type" in html


def test_doc2a_agent1_settings_keeps_simple_check_configuration():
    html = _backoffice_html()

    settings_view = html.split('<div class="view" id="view-ai-checks">', 1)[1]
    settings_view = settings_view.split('<!-- ═══════════════ AI AGENTS VIEW', 1)[0]

    assert "Document Verification Policies" in settings_view
    assert "Underlying Verification Check Configuration" in settings_view
    assert "Entity Documents" in settings_view
    assert "Person / KYC Documents" in settings_view
    assert "Enhanced / EDD Documents" in settings_view

    assert "Agent 1 Evidence Control Layer" not in settings_view
    assert "Document Policy Registry" not in settings_view
    assert "Canonical Policy Coverage" not in settings_view
    assert "agent1-policy-search" not in settings_view
    assert "agent1-policy-lifecycle-filter" not in settings_view
    assert "agent1-policy-gate-filter" not in settings_view
    assert "agent1-policy-status-filter" not in settings_view
    assert "Total document policies" not in settings_view
    assert "Policy families covered" not in settings_view


def test_doc2a_policy_registry_lists_required_document_families():
    html = _backoffice_html()

    required_families = [
        "Certificate of Incorporation",
        "Certificate of Registration / Business registration",
        "Memorandum of Association",
        "Register of Directors",
        "Register of Shareholders",
        "UBO Declaration",
        "Ownership chart / structure chart",
        "Board resolution / authorised signatory resolution",
        "Proof of registered address",
        "Financial statements / management accounts",
        "AML/compliance policy",
        "Contracts / invoices / business activity evidence",
        "Passport",
        "National ID / government ID",
        "Proof of address",
        "CV / LinkedIn profile",
        "Bank reference",
        "PEP declaration support",
        "Source of Wealth",
        "Source of Funds",
        "Bank statements",
        "Tax return",
        "Payslip / employment income proof",
        "Dividend / investment income proof",
        "Sale agreement",
        "Inheritance evidence",
        "Loan agreement",
        "Adverse media response",
        "Senior management approval evidence",
        "Periodic review attestation / no-change confirmation",
        "Certificate of Name Change",
        "Monitoring alert support evidence",
        "SAR/STR support document",
        "regulatory intelligence source document",
        "Unclassified / supporting document",
    ]

    for family in required_families:
        assert family in html


def test_doc2a_change_management_policy_baselines_are_explicit():
    html = _backoffice_html()

    for expected in [
        "Director change",
        "director extraction",
        "new/changed director re-screening required",
        "UBO change",
        "ownership percentage validation",
        "new/changed UBO re-screening required",
        "before/after comparison",
        "total ownership validation",
        "DOB correction",
        "Nationality correction",
        "identity continuity",
        "risk recalculation if material",
    ]:
        assert expected in html


def test_doc2a_policy_metadata_remains_available_without_registry_dashboard():
    html = _backoffice_html()

    for expected in [
        "policyId: policyId",
        "materialChecks",
        "technicalChecks",
        "gateBehavior",
        "manualAcceptance",
        "manualAcceptanceAllowed",
        "rescreeningTrigger",
        "coverageStatus",
        "usedIn: usedIn",
        "triggers: triggers",
        "backend_executable",
        "AGENT1_POLICY_SUMMARY",
    ]:
        assert expected in html

    view = html.split('<div class="view" id="view-ai-checks">', 1)[1].split(
        '<!-- ═══════════════ AI AGENTS VIEW', 1
    )[0]
    assert "Underlying Verification Check Configuration" in view
    assert "Blocks / gate behaviour" not in view
    assert "agent1-policy-registry" not in view


def test_doc2a_unclassified_documents_are_not_auto_reliable():
    html = _backoffice_html()

    assert "DOC-UNKNOWN-UNCLASSIFIED-v1" in html
    assert "Unclassified / supporting document" in html
    assert "automated reliance blocked" in html
    assert "officer classification required" in html
    assert "blocked from automated reliance until classified and verified or manually accepted with reason" in html
    assert "Document type required before this evidence can support approval." in html


def test_doc2a_resource_documents_are_library_only_unless_relied_on():
    html = _backoffice_html()

    assert "Library-only by default" in html
    assert "source/date/version review is required" in html
    assert "source/date/version required" in html


def test_doc_policy_canonical_ui_renames_and_pipeline_boundary():
    html = _backoffice_html()

    assert "Document Verification Policies" in html
    assert "Agent 1 verifies uploaded onboarding and requested evidence documents using the checks configured in Document Verification Policies." in html
    assert "It cannot approve, reject, waive, or perform sanctions/PEP/adverse-media screening." in html
    assert "configured checks" in html
    assert "Underlying Verification Check Configuration" in html


def test_doc_policy_canonical_ui_status_and_scope_are_honest():
    html = _backoffice_html()

    assert "backend_executable" in html
    assert "Manual review only" in html
    assert "Future / enterprise" in html
    assert "Future / enterprise. SAR/STR implementation is not active in pilot scope." in html
    assert "sar_str_active: false" in html
    assert "Manual review only; not presented as runtime verified" in html


def test_approval_blocked_chip_is_not_green_when_not_blocked():
    html = _backoffice_html()

    assert "var blockedTone = summary.approval_blocked ? 'red' : '';" in html


def test_doc2a_document_upload_attribution_schema_is_migrated(tmp_path, monkeypatch):
    db_source = _backend_db_py()

    assert "uploaded_by TEXT REFERENCES users(id)" in db_source
    assert "def _ensure_document_upload_audit_schema" in db_source
    assert "_ensure_document_upload_audit_schema(db)" in db_source

    from tests._migration_idempotency_helpers import fresh_migration_db

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        columns = db.execute("PRAGMA table_info(documents)").fetchall()
        assert "uploaded_by" in {row["name"] for row in columns}
