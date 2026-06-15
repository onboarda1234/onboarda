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
    end = html.index("function buildStoredRiskComputation", start)
    return html[start:end]


def test_doc2a_default_document_verification_ui_is_decision_first():
    html = _backoffice_html()
    renderer = _verification_renderer(html)

    assert "Reliance status" in renderer
    assert "Why review is required" in renderer
    assert "Material issues" in renderer
    assert "Technical checks and audit details" in renderer
    assert "Full check result list" in renderer
    assert "Agent execution ID" in renderer
    assert "Evidence hash" in renderer
    assert "Verification timestamp" in renderer
    assert "Policy ID/version" in renderer
    assert "if (result !== 'pass') visibleCheckItems.push(checkHtml);" in renderer
    assert "allCheckItems.push(checkHtml);" in renderer
    assert "Failed or warning checks" not in renderer
    assert "Overall Result" not in renderer
    assert "Show technical verification checks" not in renderer


def test_doc2a_evidence_control_card_surfaces_required_reliance_fields():
    html = _backoffice_html()

    assert "function renderUnifiedKycDocumentCard(app, doc, linkedRequirement)" in html
    assert "evidence-control-card" in html
    assert "Lifecycle context" in html
    assert "Policy ID/version" in html
    assert "Last verified" in html
    assert "Uploaded by" in html
    assert "Required action" in html
    assert "Material issues and reliance evidence" in html
    assert "Verified" in html
    assert "Review required" in html
    assert "Failed" in html
    assert "Pending verification" in html
    assert "Stale" in html
    assert "Manual accepted" in html
    assert "Rejected" in html
    assert "Request info" in html
    assert "warnings.length > 0 || issues.length > 0 || hasWarningCheck" in html
    assert "hasFailedCheck" in html
    assert "Pilot Evidence Classification" not in html
    assert "Pilot evidence classification" not in html
    assert "Evidence Classification" in html


def test_doc2a_agent1_settings_is_lifecycle_policy_registry():
    html = _backoffice_html()

    assert "Agent 1 Evidence Control Layer" in html
    assert "Document Policy Registry" in html
    assert "DOC-POLICY-REGISTRY-v1" in html
    assert "agent1-policy-search" in html
    assert "agent1-policy-lifecycle-filter" in html
    assert "agent1-policy-gate-filter" in html
    assert "agent1-policy-status-filter" in html
    assert "Total document policies" in html
    assert "Active policies" in html
    assert "Manual review only" in html
    assert "Future / enterprise" in html
    assert "Policy families covered" in html
    assert "Policies that block decisions" in html
    assert "Unknown documents require review" in html
    assert "DOC-POLICY-CANONICAL-v1" in html

    for section in [
        "Entity Documents",
        "Person / KYC Documents",
        "EDD Evidence",
        "Change Management Evidence",
        "Periodic Review Evidence",
        "Monitoring Evidence",
        "Regulatory / Resource Evidence",
        "Supporting Evidence",
    ]:
        assert section in html


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


def test_doc2a_policy_cards_show_required_controls():
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
        "Used in",
        "Blocks / gate behaviour",
        "Manual acceptance",
        "Triggers",
        "Material checks",
        "Technical checks",
    ]:
        assert expected in html


def test_doc2a_unclassified_documents_are_not_auto_reliable():
    html = _backoffice_html()

    assert "DOC-UNKNOWN-UNCLASSIFIED-v1" in html
    assert "Unclassified / supporting document" in html
    assert "automated reliance blocked" in html
    assert "officer classification required" in html
    assert "blocked from automated reliance until classified and verified or manually accepted with reason" in html
    assert "Officer must classify this document before it can be relied on automatically." in html


def test_doc2a_resource_documents_are_library_only_unless_relied_on():
    html = _backoffice_html()

    assert "Library-only by default" in html
    assert "source/date/version review is required" in html
    assert "source/date/version required" in html


def test_doc_policy_canonical_ui_renames_and_pipeline_boundary():
    html = _backoffice_html()

    assert "Document Verification Policies" in html
    assert "Agent 1 verifies each document type consistently" in html
    assert "Checks are document-type based, not lifecycle duplicated" in html
    assert "Agent 1 does not approve, reject, waive, or override compliance decisions" in html
    assert "does not perform sanctions, PEP, or adverse-media screening" in html
    assert "Pipeline Check Labels" in html


def test_doc_policy_canonical_ui_status_and_scope_are_honest():
    html = _backoffice_html()

    assert "Runtime verified" in html
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
