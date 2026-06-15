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
    assert "Lifecycle stages covered" in html
    assert "Documents with blockers" in html
    assert "Unknown handling enabled" in html

    for section in [
        "Entity Documents",
        "Person / KYC Documents",
        "EDD Evidence",
        "Change Management Evidence",
        "Periodic Review Evidence",
        "Monitoring / SAR Evidence",
        "Regulatory / Resource Evidence",
        "Technical Checks",
    ]:
        assert section in html


def test_doc2a_policy_registry_lists_required_document_families():
    html = _backoffice_html()

    required_families = [
        "Certificate of Incorporation",
        "Business registration / licence",
        "Memorandum & Articles",
        "Register of Directors",
        "Register of Shareholders",
        "UBO declaration",
        "Ownership chart",
        "Board resolution / authorised signatory resolution",
        "Proof of registered address",
        "Financial statements",
        "AML/compliance policy where required",
        "Contracts / invoices where used as business activity evidence",
        "Passport",
        "National ID",
        "Proof of address",
        "CV",
        "Bank reference",
        "Signatory authority",
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
        "Enhanced due diligence memo support",
        "Director change evidence",
        "UBO change evidence",
        "Ownership percentage change evidence",
        "Registered address change evidence",
        "PEP status change evidence",
        "DOB correction evidence",
        "Nationality correction evidence",
        "Name correction evidence",
        "refreshed Certificate of Incorporation / registry extract",
        "updated registers",
        "updated proof of address",
        "expired document replacement",
        "periodic review attestation",
        "no-change confirmation",
        "refreshed SOW/SOF where high-risk",
        "updated financial/business activity evidence",
        "monitoring alert support document",
        "adverse media source capture",
        "transaction support evidence",
        "client explanation / response",
        "SAR/STR support document",
        "investigation closure evidence",
        "regulatory guidance",
        "laws/regulations",
        "internal policy document",
        "compliance resource file",
        "regulatory intelligence source document",
        "source document used in memo/reasoning",
    ]

    for family in required_families:
        assert family in html


def test_doc2a_change_management_policy_baselines_are_explicit():
    html = _backoffice_html()

    for expected in [
        "updated register of directors + person KYC",
        "director extraction",
        "appointment/removal detection",
        "person KYC present",
        "name/DOB/nationality match",
        "updated shareholder register + person KYC",
        "UBO extraction",
        "ownership percentage validation",
        "natural-person trace",
        "application consistency",
        "before/after comparison",
        "total ownership validation",
        "threshold/materiality detection",
        "DOB extraction",
        "nationality extraction",
        "identity continuity",
        "re-screening required",
        "Risk recalculation and re-screening if material",
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
        "When required",
        "Gate behaviour",
        "Manual acceptance",
        "Re-screening trigger",
        "Material checks",
        "Technical checks",
    ]:
        assert expected in html


def test_doc2a_unclassified_documents_are_not_auto_reliable():
    html = _backoffice_html()

    assert "DOC-UNKNOWN-UNCLASSIFIED-v1" in html
    assert "Unclassified document / unknown policy" in html
    assert "blocked from automated reliance" in html
    assert "routed for officer classification" in html
    assert "excluded from memo/approval reliance until classified and verified or manually accepted with reason" in html
    assert "Officer must classify this document before it can be relied on automatically." in html


def test_doc2a_resource_documents_are_library_only_unless_relied_on():
    html = _backoffice_html()

    assert "Library-only unless relied on in a case, memo, policy, or decision" in html
    assert "source/date/version verification is required" in html
    assert "Library only; blocks reliance if cited and source/date/version are unverified" in html


def test_doc2a_document_upload_attribution_schema_is_migrated(tmp_path, monkeypatch):
    db_source = _backend_db_py()

    assert "uploaded_by TEXT REFERENCES users(id)" in db_source
    assert "def _ensure_document_upload_audit_schema" in db_source
    assert "_ensure_document_upload_audit_schema(db)" in db_source

    from tests._migration_idempotency_helpers import fresh_migration_db

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        columns = db.execute("PRAGMA table_info(documents)").fetchall()
        assert "uploaded_by" in {row["name"] for row in columns}
