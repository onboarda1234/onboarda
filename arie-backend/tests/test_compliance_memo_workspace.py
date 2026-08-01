"""Compliance Memo document-workspace regression coverage.

The lifecycle is a projection of existing memo governance fields.  These tests
intentionally guard against adding a second persisted workflow or an HTML memo
that can diverge from the generated PDF.
"""

from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))


def _read(relative_path):
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_projected_memo_lifecycle_covers_document_states():
    from server import _memo_lifecycle_status

    consistent = {"supervisor": {"verdict": "CONSISTENT", "can_approve": True}}

    assert _memo_lifecycle_status(None) == "NOT_GENERATED"
    assert _memo_lifecycle_status({"review_status": "draft", "validation_status": "pending"}) == "DRAFT"
    assert _memo_lifecycle_status(
        {"review_status": "draft", "validation_status": "pass"},
        consistent,
    ) == "AWAITING_OFFICER_SIGNOFF"
    assert _memo_lifecycle_status(
        {"review_status": "approved", "validation_status": "pass"},
        consistent,
    ) == "FINAL"
    assert _memo_lifecycle_status(
        {"review_status": "approved", "validation_status": "pass"},
        consistent,
        is_current=False,
    ) == "SUPERSEDED"


def test_stale_projection_precedes_draft_or_final_state():
    from server import _memo_lifecycle_status

    row = {
        "review_status": "approved",
        "validation_status": "pass",
        "is_stale": 1,
    }
    assert _memo_lifecycle_status(row) == "STALE"
    assert _memo_lifecycle_status({**row, "is_stale": 0}, is_stale=True) == "STALE"


def test_timestamp_staleness_is_projected_without_a_new_lifecycle_column():
    from server import _memo_lifecycle_status, _memo_staleness_view

    row = {
        "review_status": "approved",
        "validation_status": "pass",
        "is_stale": 0,
        "created_at": "2026-07-31T10:00:00+00:00",
        "memo_data": "{}",
    }
    view = _memo_staleness_view(
        {"inputs_updated_at": "2026-07-31T10:01:00+00:00"},
        row,
    )

    assert view["is_stale"] is True
    assert view["trigger"] == "application_inputs_changed_after_memo"
    assert _memo_lifecycle_status(row, is_stale=view["is_stale"]) == "STALE"


def test_workspace_uses_actual_pdf_and_only_final_can_download():
    html = _read("arie-backoffice.html")

    for control_id in (
        "btn-generate-memo",
        "btn-preview-memo",
        "btn-regenerate-memo",
        "btn-save-memo-rationale",
        "btn-approve-memo",
        "btn-download-memo",
        "btn-memo-history",
        "memo-pdf-preview",
        "memo-approval-reason",
    ):
        assert f'id="{control_id}"' in html

    runtime = html[html.index("// ── Compliance Memo document workspace") :]
    assert "?preview=1" in runtime
    assert "return installMemoPdfResponse(response);" in runtime
    assert "var cached = await fetchMemoPdfResponse(false);" in runtime
    assert "link.href = cached.url;" in runtime
    assert "frame.src = url" in runtime
    assert "if (lifecycle !== 'FINAL')" in runtime
    assert "function renderMemoSections()" in runtime
    assert "return '';" in runtime


def test_csp_allows_only_same_origin_and_blob_pdf_frames():
    base_handler = _read("arie-backend/base_handler.py")

    assert base_handler.count("frame-src 'self' blob:;") == 2
    assert "frame-src *" not in base_handler


def test_workspace_has_no_dashboard_or_governance_panels():
    html = _read("arie-backoffice.html")

    for removed_id in (
        "memo-governance-summary",
        "memo-validation-panel",
        "memo-quality-gauge",
        "memo-val-issues",
        "memo-val-scores",
    ):
        assert f'id="{removed_id}"' not in html


def test_existing_memo_resource_adds_history_and_rationale_without_new_route():
    server_source = _read("arie-backend/server.py")
    handler = server_source[
        server_source.index("class ComplianceMemoHandler") :
        server_source.index("class MemoValidateHandler")
    ]

    assert "def get(self, app_id):" in handler
    assert '"versions": versions' in handler
    assert '"lifecycle_status"' in handler
    assert "def patch(self, app_id):" in handler
    assert "UPDATE compliance_memos SET review_notes = ?, reviewed_by = ?" in handler
    assert '"Save Memo Officer Rationale"' in handler
    assert "def post(self, app_id):" in handler


def test_preview_and_download_share_one_pdf_generator_and_differ_only_in_disposition():
    server_source = _read("arie-backend/server.py")
    handler = server_source[
        server_source.index("class MemoPDFDownloadHandler") :
        server_source.index("class ScreeningReportPDFDownloadHandler")
    ]

    assert handler.count("generate_memo_pdf(") == 1
    assert 'inline_preview = str(self.get_query_argument("preview", ""))' in handler
    assert 'disposition = "inline" if inline_preview else "attachment"' in handler
    assert '"X-PDF-SHA256"' in handler
    assert '"X-Memo-Lifecycle"' in handler
    assert "_ensure_memo_fresh_or_mark_stale(" in handler


def test_approval_contract_and_gates_remain_in_existing_handler():
    server_source = _read("arie-backend/server.py")
    handler = server_source[
        server_source.index("class MemoApproveHandler") :
        server_source.index("class ApplicationDecisionHandler")
    ]

    assert 'self.require_auth(roles=["admin", "sco"])' in handler
    assert '_validate_officer_signoff(body.get("officer_signoff"), "memo")' in handler
    assert "latest_compliance_memo_row_for_identifier" in handler
    assert "_ensure_memo_fresh_or_mark_stale(" in handler
    assert 'body.get("approval_reason")' in handler
    assert 'SET review_status = \'approved\'' in handler


def test_lifecycle_reuses_existing_database_columns():
    schema = _read("arie-backend/db.py")
    first_table = schema[
        schema.index("CREATE TABLE IF NOT EXISTS compliance_memos") :
        schema.index("CREATE TABLE IF NOT EXISTS edd_findings")
    ]

    for existing_column in (
        "version INTEGER",
        "review_status TEXT",
        "reviewed_by TEXT",
        "review_notes TEXT",
        "validation_status TEXT",
        "approved_by TEXT",
        "approved_at TIMESTAMP",
        "is_stale BOOLEAN",
        "stale_reason TEXT",
        "pdf_generated_at TIMESTAMP",
    ):
        assert existing_column in first_table
    assert "lifecycle_status" not in first_table


def test_pdf_template_has_draft_and_final_locked_formats(monkeypatch):
    import pdf_generator

    memo = {
        "application_ref": "ARF-WORKSPACE-001",
        "company_name": "Workspace Test Ltd",
        "memo_generated": "2026-07-31T10:00:00+00:00",
        "sections": {
            "executive_summary": {"content": "Concise compliance opinion."},
            "risk_assessment": {"content": "Residual risk is medium."},
            "red_flags_and_mitigants": {
                "red_flags": ["One material concern"],
                "mitigants": ["One mitigating control"],
                "approval_blockers": ["Resolve the outstanding condition"],
            },
            "compliance_decision": {"decision": "REVIEW", "content": "Officer review is required."},
            "ongoing_monitoring": {"content": "Apply standard ongoing monitoring."},
            "audit_and_governance": {"content": "Authoritative evidence remains in source modules."},
        },
        "metadata": {
            "memo_version": "1.0",
            "risk_rating": "MEDIUM",
            "risk_model_version": "rm-2026.07",
            "approval_recommendation": "REVIEW",
            "screening_snapshot": "No confirmed matches",
        },
    }
    application = {
        "ref": "ARF-WORKSPACE-001",
        "company_name": "Workspace Test Ltd",
        "inputs_updated_at": "2026-07-31T09:30:00+00:00",
    }
    captured = []

    class FakeHTML:
        def __init__(self, string):
            captured.append(string)

        def write_pdf(self):
            return b"%PDF-workspace"

    class FakeWeasyPrint:
        HTML = FakeHTML

    monkeypatch.setattr(pdf_generator, "_get_weasyprint", lambda: FakeWeasyPrint)

    pdf_generator.generate_memo_pdf(memo, application)
    pdf_generator.generate_memo_pdf(
        memo,
        application,
        approved_by="Senior Officer",
        approved_at="2026-07-31T11:00:00+00:00",
        approval_reason="Evidence supports the recorded opinion.",
    )

    draft_html, final_html = captured
    assert "DRAFT - NOT APPROVED" in draft_html
    assert "FINAL - APPROVED / LOCKED" not in draft_html
    assert "FINAL - APPROVED / LOCKED" in final_html
    assert "DRAFT - NOT APPROVED" not in final_html
    for heading in (
        "Document Control",
        "Memo Basis",
        "Compliance Opinion",
        "Decision Rationale",
        "Material Concerns and Mitigating Factors",
        "Conditions Before Approval",
        "Residual Risk and Monitoring Position",
        "Officer Decision and Sign-Off",
        "Audit and Governance Metadata",
    ):
        assert heading in draft_html
        assert heading in final_html
    assert "Current Application Profile" in draft_html
    assert "Subsequent material changes require a new memo version." in draft_html
    assert "Mandatory Conditions" in draft_html
    assert "Operational Conditions" in draft_html
    assert "RegMind Compliance Memorandum" in draft_html
    assert "recommendation-value" in draft_html
    assert "decision-badge" not in draft_html.split("<body>", 1)[1]
    assert "Risk Classification" in draft_html
    assert "Authoritative Risk Score" in draft_html
    assert '<ol class="condition-list">' in draft_html
    assert "Generated by RegMind Compliance OS" in draft_html
    assert "Evidence Pack Reference" in draft_html
    assert "Content Hash:" in draft_html
    assert "Officer Rationale - Professional Judgement" in final_html
    assert "Evidence supports the recorded opinion." in final_html


def test_validation_sample_uses_professional_opinion_and_expanded_rationale():
    from scripts.qa.generate_compliance_memo_validation_samples import _sample_memo

    memo = _sample_memo()
    opinion = memo["sections"]["compliance_decision"]["content"]
    rationale = memo["sections"]["executive_summary"]["content"]

    assert 120 <= len(opinion.split()) <= 180
    assert "no unresolved fact currently supports" not in opinion.lower()
    assert "identified and verified" in opinion
    assert "no confirmed sanctions, PEP or adverse-media matches" in opinion
    assert "proportionate" in opinion
    assert 55 <= len(rationale.split()) <= 80
    assert "no unexplained control layer" in rationale
    assert "medium residual risk" in rationale
    assert "conditional approval proportionate" in rationale
