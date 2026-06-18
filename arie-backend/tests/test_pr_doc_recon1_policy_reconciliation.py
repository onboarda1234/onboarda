from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _backoffice_html() -> str:
    return (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")


def _settings_view(html: str) -> str:
    return html.split('<div class="view" id="view-ai-checks">', 1)[1].split(
        "<!-- ═══════════════ AI AGENTS VIEW", 1
    )[0]


def test_document_verification_policies_page_removes_registry_dashboard():
    view = _settings_view(_backoffice_html())

    assert "Document Verification Policies" in view
    assert "Underlying Verification Check Configuration" in view
    assert "Entity Documents" in view
    assert "Person / KYC Documents" in view
    assert "Enhanced Evidence Documents" in view

    assert "Agent 1 Evidence Control Layer" not in view
    assert "Document Policy Registry" not in view
    assert "Canonical Policy Coverage" not in view
    assert "agent1-policy-search" not in view
    assert "agent1-policy-summary" not in view
    assert "Policy families covered" not in view


def test_edd_verification_checks_are_in_simple_configuration_section():
    html = _backoffice_html()

    assert "var EDD_DOC_CHECKS" in html
    for label in (
        "Source of Wealth Documentation",
        "Source of Funds Documentation",
        "Bank Statements",
        "Bank Reference Letter",
    ):
        assert label in html
    assert "switchCheckTab('edd'" in html
    assert "payloads.push({ category:'entity', doc_type:doc.docId" in html


def test_agent1_wording_is_aligned_to_current_verification_scope():
    html = _backoffice_html()

    assert (
        "Agent 1 verifies uploaded onboarding and requested evidence documents using the checks configured "
        "in Document Verification Policies."
    ) in html
    assert "It cannot approve, reject, waive, or perform sanctions/PEP/adverse-media screening." in html
    assert "configured checks" in html
    assert "active policies" not in html.split("function renderAgentsPipeline()", 1)[1].split(
        "function toggleAgentPanel", 1
    )[0]


def test_enhanced_requirement_policy_mapping_classifies_runtime_and_manual_docs():
    from enhanced_requirements import enhanced_requirement_document_policy

    active = enhanced_requirement_document_policy("company_sof_evidence")
    assert active["document_type"] == "source_funds"
    assert active["verification_mode"] == "active_runtime_verified"
    assert active["runtime_executable"] is True

    bankref = enhanced_requirement_document_policy("company_bank_reference")
    assert bankref["document_type"] == "bankref"
    assert bankref["policy_id"] == "DOC-EVIDENCE-BANK-REFERENCE-v1"

    manual = enhanced_requirement_document_policy("trust_nominee_foundation_documents")
    assert manual["document_type"] == "trust_deed"
    assert manual["verification_mode"] == "manual_review_only"
    assert manual["runtime_executable"] is False

    unknown = enhanced_requirement_document_policy("custom_requested_document")
    assert unknown["document_type"] == "supporting_document"
    assert unknown["verification_mode"] == "manual_review_only"
