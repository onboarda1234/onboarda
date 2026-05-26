"""
Phase 1 Remediation Tests — Verify fixes for audit findings 1, 2, 3, 6, 7.

Finding 1: ClaudeClient.generate() method must exist
Finding 2: Validation engine must return category_scores aligned with frontend keys
Finding 3: SupervisorPipelineResult.to_dict() must include agent_results
Finding 6: memo_version must be insertable
Finding 7: Validation issues must include 'fix' field
"""
import os
import sys
import json
import pytest

# ── Finding 1: ClaudeClient.generate() exists and is callable ──

class TestFinding1_GenerateMethod:
    """Verify ClaudeClient has a generate() method that the monitoring agents can call."""

    def test_generate_method_exists(self):
        """ClaudeClient must have a public generate() method."""
        from claude_client import ClaudeClient
        assert hasattr(ClaudeClient, "generate"), "ClaudeClient is missing generate() method"
        assert callable(getattr(ClaudeClient, "generate")), "generate is not callable"

    def test_generate_method_signature(self):
        """generate() must accept (prompt, max_tokens) as monitoring agents call it."""
        import inspect
        from claude_client import ClaudeClient
        sig = inspect.signature(ClaudeClient.generate)
        params = list(sig.parameters.keys())
        assert "prompt" in params, "generate() missing 'prompt' parameter"
        assert "max_tokens" in params, "generate() missing 'max_tokens' parameter"

    def test_generate_returns_string_in_mock_mode(self):
        """In mock mode, generate() should return an empty string (not raise)."""
        from claude_client import ClaudeClient
        client = ClaudeClient(api_key=None, mock_mode=True)
        result = client.generate("test prompt", max_tokens=100)
        assert isinstance(result, str), f"Expected str, got {type(result)}"

    def test_generate_does_not_raise_attribute_error(self):
        """This was the original bug — calling .generate() raised AttributeError."""
        from claude_client import ClaudeClient
        client = ClaudeClient(api_key=None, mock_mode=True)
        # Must not raise AttributeError
        try:
            result = client.generate("Summarise compliance findings.", max_tokens=300)
        except AttributeError:
            pytest.fail("ClaudeClient.generate() raised AttributeError — Finding 1 NOT fixed")


# ── Finding 2: Validation scores field alignment ──

class TestFinding2_ValidationScores:
    """Verify validation_engine returns category_scores with frontend-expected keys."""

    FRONTEND_EXPECTED_KEYS = {
        "structural_completeness",
        "risk_consistency",
        "decision_alignment",
        "ownership_quality",
        "screening_defensibility",
        "document_verification",
        "red_flags_mitigants",
        "confidence_explainability",
        "missing_data_handling",
    }

    def _make_test_memo(self):
        """Create a minimal memo that passes basic validation."""
        return {
            "metadata": {
                "risk_score": 45,
                "risk_rating": "MEDIUM",
                "approval_recommendation": "REVIEW",
                "confidence_level": 0.82,
                "blocked": False,
            },
            "sections": {
                "executive_summary": {"title": "Executive Summary", "content": "Test summary."},
                "client_overview": {"title": "Client Overview", "content": "Entity overview."},
                "ownership_and_control": {
                    "title": "Ownership",
                    "content": "Test ownership. 50% shareholding.",
                    "structure_complexity": "Simple",
                    "control_statement": "John Doe exercises effective control via majority shareholding.",
                },
                "risk_assessment": {
                    "content": "Risk assessment.",
                    "sub_sections": {
                        "jurisdiction_risk": {"title": "Jurisdiction", "content": "Low risk.", "rating": "LOW"},
                        "business_risk": {"title": "Business", "content": "Medium.", "rating": "MEDIUM"},
                        "transaction_risk": {"title": "Transaction", "content": "Low.", "rating": "LOW"},
                        "ownership_risk": {"title": "Ownership", "content": "Low.", "rating": "LOW"},
                        "financial_crime_risk": {"title": "FinCrime", "content": "Low.", "rating": "LOW"},
                    },
                },
                "screening_results": {"title": "Screening", "content": "OpenSanctions screening. No matches."},
                "document_verification": {"title": "Documents", "content": "All verified and consistent."},
                "ai_explainability": {
                    "content": "Explainability.",
                    "risk_increasing_factors": ["Limited trading history", "New entity"],
                    "risk_decreasing_factors": ["Clean screening", "Verified ownership"],
                },
                "red_flags_and_mitigants": {
                    "red_flags": ["Limited history", "Data gaps"],
                    "mitigants": ["Clean screening", "Verified documents"],
                },
                "compliance_decision": {"decision": "REVIEW", "content": "Recommend review."},
                "ongoing_monitoring": {"title": "Monitoring", "content": "Annual review."},
                "audit_and_governance": {"title": "Audit", "content": "Audit trail."},
            },
        }

    def test_category_scores_field_exists_in_result(self):
        """Backend must return 'category_scores' in addition to 'scores_breakdown'."""
        from validation_engine import validate_compliance_memo
        result = validate_compliance_memo(self._make_test_memo())
        assert "category_scores" in result, "Missing 'category_scores' field in validation result"
        assert "scores_breakdown" in result, "Missing 'scores_breakdown' (backwards compat)"

    def test_category_scores_has_frontend_keys(self):
        """category_scores must contain keys the frontend JS expects."""
        from validation_engine import validate_compliance_memo
        result = validate_compliance_memo(self._make_test_memo())
        category_scores = result["category_scores"]
        for key in self.FRONTEND_EXPECTED_KEYS:
            assert key in category_scores, f"Frontend-expected key '{key}' missing from category_scores"

    def test_scores_breakdown_preserved(self):
        """Original scores_breakdown must still be present for backend consumers."""
        from validation_engine import validate_compliance_memo
        result = validate_compliance_memo(self._make_test_memo())
        assert isinstance(result["scores_breakdown"], dict)
        assert len(result["scores_breakdown"]) > 0


# ── Finding 3: Supervisor to_dict() includes agent_results ──

class TestFinding3_SupervisorAgentResults:
    """Verify SupervisorPipelineResult.to_dict() includes agent_results."""

    def test_to_dict_has_agent_results_key(self):
        """to_dict() output must contain 'agent_results' for GET endpoint."""
        try:
            from supervisor.supervisor import SupervisorPipelineResult
        except ImportError:
            pytest.skip("Supervisor module not available")

        result = SupervisorPipelineResult(pipeline_id="test-001", application_id="app-001")
        d = result.to_dict()
        assert "agent_results" in d, "to_dict() missing 'agent_results' — Finding 3 NOT fixed"
        assert isinstance(d["agent_results"], list)

    def test_to_dict_has_contradictions_detail(self):
        """to_dict() should include contradictions_detail for frontend compatibility."""
        try:
            from supervisor.supervisor import SupervisorPipelineResult
        except ImportError:
            pytest.skip("Supervisor module not available")

        result = SupervisorPipelineResult(pipeline_id="test-002", application_id="app-002")
        d = result.to_dict()
        assert "contradictions_detail" in d, "to_dict() missing 'contradictions_detail'"

    def test_to_dict_has_failed_agent_details(self):
        """to_dict() should include failed_agent_details."""
        try:
            from supervisor.supervisor import SupervisorPipelineResult
        except ImportError:
            pytest.skip("Supervisor module not available")

        result = SupervisorPipelineResult(pipeline_id="test-003", application_id="app-003")
        d = result.to_dict()
        assert "failed_agent_details" in d, "to_dict() missing 'failed_agent_details'"


# ── Finding 6: memo_version insertable ──

class TestFinding6_MemoVersion:
    """Verify memo INSERT SQL includes memo_version column."""

    def test_insert_sql_contains_memo_version(self):
        """The primary INSERT INTO compliance_memos must include memo_version."""
        import inspect
        # Read server.py source to verify SQL statement
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path, "r", encoding="utf-8") as f:
            source = f.read()

        # Find the primary INSERT statement
        idx = source.find("INSERT INTO compliance_memos (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score")
        assert idx > 0, "Could not find primary memo INSERT statement"

        # Extract the INSERT statement (next 500 chars)
        snippet = source[idx:idx + 500]
        assert "memo_version" in snippet, "memo_version not in primary INSERT statement — Finding 6 NOT fixed"


# ── Finding 7: Validation issues contain fix field ──

class TestFinding7_ValidationFixField:
    """Verify validation issues contain 'fix' field and frontend surfaces it."""

    def test_validation_issues_have_fix_field(self):
        """Each validation issue from the engine should have a 'fix' key."""
        from validation_engine import validate_compliance_memo
        # Use a memo that will trigger issues
        bad_memo = {
            "metadata": {"risk_score": 10, "risk_rating": "LOW", "approval_recommendation": "REJECT"},
            "sections": {
                "executive_summary": {"title": "ES", "content": "Summary."},
                "client_overview": {"title": "CO", "content": "Overview."},
            },
        }
        result = validate_compliance_memo(bad_memo)
        issues = result.get("issues", [])
        assert len(issues) > 0, "Expected validation issues for incomplete memo"

        for issue in issues:
            assert "fix" in issue, f"Issue missing 'fix' field: {issue.get('description', '')[:60]}"
            assert isinstance(issue["fix"], str) and len(issue["fix"]) > 0, \
                f"Issue 'fix' field is empty for: {issue.get('description', '')[:60]}"

    def test_backoffice_renders_fix_field(self):
        """Backoffice HTML must reference issue.fix for display."""
        bo_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-backoffice.html")
        if not os.path.exists(bo_path):
            bo_path = os.path.join(os.path.dirname(__file__), "..", "arie-backoffice.html")
        with open(bo_path, "r", encoding="utf-8") as f:
            html = f.read()
        assert "issue.fix" in html, "Backoffice HTML does not reference issue.fix — Finding 7 NOT fixed"
