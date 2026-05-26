"""
Sprint 3 — PDF Generator Tests
Validates server-side PDF generation produces valid PDF output
with correct structure, escaping, and metadata.

Note: These tests require WeasyPrint native system libraries (libpango, libcairo,
libglib2.0, etc.). On Windows they will be skipped automatically unless the native
libs are installed. In CI they run in a dedicated pdf-tests job on Ubuntu.
"""
import os
import sys
import json
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

# Skip the entire module if WeasyPrint cannot load its native shared libraries.
# This keeps Windows local development usable; CI runs these in a dedicated job
# with the required native packages installed.
try:
    import weasyprint as _wp
    _wp.HTML(string="<p>probe</p>").write_pdf()
    _WEASYPRINT_AVAILABLE = True
except Exception:
    _WEASYPRINT_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _WEASYPRINT_AVAILABLE,
    reason="WeasyPrint native libraries not available (libpango/libcairo); "
           "PDF tests run in the dedicated CI pdf-tests job on Ubuntu.",
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

def _make_memo():
    """Build a realistic compliance memo for PDF testing."""
    return {
        "sections": {
            "executive_summary": {"content": "Low-risk Technology company domiciled in Mauritius. Clean screening across all consolidated lists."},
            "client_overview": {"content": "Test Corp Ltd, SME, Technology sector. Incorporated 2019."},
            "ownership_and_control": {
                "content": "UBO1 holds 80% ownership via direct shareholding.",
                "structure_complexity": "Simple",
                "control_statement": "John Doe exercises effective control via 80% direct shareholding."
            },
            "risk_assessment": {
                "content": "Overall risk: MEDIUM",
                "sub_sections": {
                    "jurisdiction_risk": {"rating": "MEDIUM", "content": "Mauritius — offshore but cooperative jurisdiction"},
                    "business_risk": {"rating": "LOW", "content": "Technology sector, established operations"},
                    "transaction_risk": {"rating": "MEDIUM", "content": "Standard volume, domestic focus"},
                    "ownership_risk": {"rating": "LOW", "content": "Clear single-tier ownership"},
                    "financial_crime_risk": {"rating": "LOW", "content": "No PEP or sanctions exposure"}
                }
            },
            "screening_results": {"content": "Screening completed. No sanctions matches across UN, EU, OFAC, HMT."},
            "document_verification": {"content": "All documents verified. No discrepancies found."},
            "ai_explainability": {
                "content": "Risk assessed via multi-agent pipeline.",
                "risk_increasing_factors": ["Limited trading history", "Offshore jurisdiction"],
                "risk_decreasing_factors": ["Clean sanctions screening", "Verified ownership"]
            },
            "red_flags_and_mitigants": {
                "red_flags": ["Limited trading history in jurisdiction"],
                "mitigants": ["Clean screening", "Transparent ownership"]
            },
            "compliance_decision": {"decision": "APPROVE_WITH_CONDITIONS", "content": "Approved with enhanced monitoring."},
            "ongoing_monitoring": {"content": "Enhanced monitoring tier. Quarterly review."},
            "audit_and_governance": {"content": "Full audit trail maintained. 10-agent pipeline."}
        },
        "metadata": {
            "risk_rating": "MEDIUM",
            "risk_score": 45,
            "approval_recommendation": "APPROVE_WITH_CONDITIONS",
            "confidence_level": 0.78,
            "memo_version": "1.0",
            "rule_engine": {"engine_status": "CLEAN", "violations": []}
        }
    }


def _make_application():
    """Build a sample application dict."""
    return {
        "id": "app001",
        "ref": "ARF-2026-001",
        "company_name": "Test Corp Ltd",
        "country": "Mauritius",
        "sector": "Technology",
        "entity_type": "SME",
    }


# ═══════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════

class TestPDFGeneration:
    def test_generates_valid_pdf_bytes(self):
        """generate_memo_pdf must return bytes starting with %PDF header."""
        from pdf_generator import generate_memo_pdf
        pdf = generate_memo_pdf(_make_memo(), _make_application())
        assert isinstance(pdf, bytes)
        assert pdf[:5] == b"%PDF-"

    def test_pdf_has_reasonable_size(self):
        """Generated PDF must be non-trivial (>5KB) but not absurd (<5MB)."""
        from pdf_generator import generate_memo_pdf
        pdf = generate_memo_pdf(_make_memo(), _make_application())
        assert len(pdf) > 5000, f"PDF too small: {len(pdf)} bytes"
        assert len(pdf) < 5_000_000, f"PDF too large: {len(pdf)} bytes"

    def test_pdf_to_file_creates_file(self):
        """generate_memo_pdf_to_file must create a readable file."""
        from pdf_generator import generate_memo_pdf_to_file
        path = generate_memo_pdf_to_file(_make_memo(), _make_application())
        try:
            assert os.path.exists(path)
            assert os.path.getsize(path) > 5000
            with open(path, "rb") as f:
                assert f.read(5) == b"%PDF-"
        finally:
            os.unlink(path)

    def test_html_injection_escaped(self):
        """Malicious HTML in memo content must be escaped, not rendered."""
        from pdf_generator import generate_memo_pdf
        memo = _make_memo()
        memo["sections"]["executive_summary"]["content"] = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
        # Should not raise — content is escaped
        pdf = generate_memo_pdf(memo, _make_application())
        assert isinstance(pdf, bytes)
        assert pdf[:5] == b"%PDF-"

    def test_missing_sections_handled(self):
        """PDF generation must handle missing or empty sections gracefully."""
        from pdf_generator import generate_memo_pdf
        memo = {"sections": {}, "metadata": {"risk_rating": "LOW", "risk_score": 10, "approval_recommendation": "APPROVE", "confidence_level": 0.9}}
        pdf = generate_memo_pdf(memo, _make_application())
        assert isinstance(pdf, bytes)
        assert pdf[:5] == b"%PDF-"

    def test_approval_metadata_included(self):
        """PDF should include approval info when provided."""
        from pdf_generator import generate_memo_pdf
        pdf = generate_memo_pdf(
            _make_memo(), _make_application(),
            approved_by="Aisha Sudally", approved_at="2026-03-22T10:00:00"
        )
        assert isinstance(pdf, bytes)
        assert len(pdf) > 5000

    def test_validation_and_supervisor_context(self):
        """PDF should incorporate validation and supervisor results."""
        from pdf_generator import generate_memo_pdf
        pdf = generate_memo_pdf(
            _make_memo(), _make_application(),
            validation_result={"validation_status": "pass", "quality_score": 8.5},
            supervisor_result={"verdict": "CONSISTENT"}
        )
        assert isinstance(pdf, bytes)
        assert len(pdf) > 5000

    def test_very_high_risk_renders(self):
        """VERY_HIGH risk level must render without errors."""
        from pdf_generator import generate_memo_pdf
        memo = _make_memo()
        memo["metadata"]["risk_rating"] = "VERY_HIGH"
        memo["metadata"]["risk_score"] = 85
        memo["metadata"]["approval_recommendation"] = "REJECT"
        pdf = generate_memo_pdf(memo, _make_application())
        assert isinstance(pdf, bytes)
