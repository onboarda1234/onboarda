"""
Tests for AI Agent Standardisation (Improvements 1-9).
Validates control IDs, output structure, escalation, traceability, and no-result-no-pass.
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ═══════════════════════════════════════════════════════════
# Improvement 1: Control IDs
# ═══════════════════════════════════════════════════════════

class TestControlIDs:
    """Every check definition must have a unique control ID."""

    def test_all_doc_check_definitions_have_ids(self):
        """Every check in the derived check definitions must have an 'id' field."""
        from claude_client import ClaudeClient
        for doc_type, checks in ClaudeClient._get_check_definitions().items():
            for check in checks:
                assert "id" in check, f"Check '{check.get('label')}' in doc_type '{doc_type}' missing 'id'"
                assert (check["id"].startswith("DOC-") or check["id"].startswith("CERT-")
                        or check["id"].startswith("LIC-") or check["id"].startswith("GATE-")), \
                    f"Check ID '{check['id']}' must start with 'DOC-', 'CERT-', 'LIC-', or 'GATE-'"

    def test_control_ids_are_unique(self):
        """All control IDs across all doc types must be unique (CERT-01 is an allowed cross-cutting exception)."""
        from claude_client import ClaudeClient
        # CERT-01 is intentionally shared across all document types as the cross-cutting
        # Certification check — exclude it from the uniqueness check.
        ALLOWED_CROSS_CUTTING = {"CERT-01"}
        all_ids = []
        for doc_type, checks in ClaudeClient._get_check_definitions().items():
            for check in checks:
                if check["id"] not in ALLOWED_CROSS_CUTTING:
                    all_ids.append(check["id"])
        assert len(all_ids) == len(set(all_ids)), f"Duplicate control IDs found: {[x for x in all_ids if all_ids.count(x) > 1]}"

    def test_control_id_format(self):
        """Control IDs must follow the canonical format (e.g. DOC-01, DOC-06A, DOC-MA-01, LIC-GATE, GATE-01)."""
        from claude_client import ClaudeClient
        import re
        # Canonical formats in use:
        #   DOC-01     (numeric, e.g. DOC-05)
        #   DOC-06A    (numeric + letter suffix, e.g. DOC-06A, DOC-49A)
        #   DOC-MA-01  (text infix + numeric, e.g. DOC-MA-01)
        #   LIC-GATE   (prefix + text word, e.g. LIC-GATE)
        #   GATE-01    (prefix + numeric, e.g. GATE-01)
        #   CERT-01    (prefix + numeric, e.g. CERT-01)
        # Pattern: PREFIX-SEGMENT with up to one additional -SEGMENT (DOC-MA-01).
        pattern = re.compile(r'^(DOC|CERT|LIC|GATE)-[A-Z0-9]+(?:-[A-Z0-9]+)?$')
        for doc_type, checks in ClaudeClient._get_check_definitions().items():
            for check in checks:
                assert pattern.match(check["id"]), \
                    f"Control ID '{check['id']}' in '{doc_type}' doesn't match canonical ID format"

    def test_mock_verify_document_has_ids(self):
        """Mock verify_document response must include control IDs."""
        from claude_client import _mock_verify_document
        result = _mock_verify_document()
        for check in result["checks"]:
            assert "id" in check, f"Mock check '{check.get('label')}' missing 'id'"

    def test_ai_checks_seed_has_ids(self, temp_db):
        """AI checks in the database must include control IDs."""
        from db import get_db
        db = get_db()
        rows = db.execute("SELECT doc_type, checks FROM ai_checks").fetchall()
        db.close()
        for row in rows:
            checks = json.loads(row["checks"]) if row["checks"] else []
            for check in checks:
                assert "id" in check, f"DB check '{check.get('label')}' for doc_type '{row['doc_type']}' missing 'id'"


# ═══════════════════════════════════════════════════════════
# Improvement 2: Standardised Output Structure
# ═══════════════════════════════════════════════════════════

class TestStandardisedOutput:
    """Every agent output must conform to the standard structure."""

    def test_standardise_agent_output_pass(self):
        """PASS output structure is correct."""
        from claude_client import standardise_agent_output
        checks = [
            {"id": "DOC-01", "label": "Test", "result": "pass", "message": "OK"},
        ]
        output = standardise_agent_output(checks=checks, summary="All good")
        assert output["status"] == "PASS"
        assert output["checks"] == checks
        assert output["summary"] == "All good"
        assert output["flags"] == []
        assert output["requires_review"] is False
        assert output["validated"] is True
        assert output["rejected"] is False

    def test_standardise_agent_output_fail(self):
        """FAIL output triggers requires_review."""
        from claude_client import standardise_agent_output
        checks = [
            {"id": "DOC-01", "label": "Test", "result": "fail", "message": "Bad"},
        ]
        output = standardise_agent_output(checks=checks, summary="Failed")
        assert output["status"] == "FAIL"
        assert output["requires_review"] is True
        assert output["validated"] is False
        assert len(output["flags"]) == 1

    def test_standardise_agent_output_warn(self):
        """WARN output structure is correct."""
        from claude_client import standardise_agent_output
        checks = [
            {"id": "DOC-01", "label": "Test", "result": "warn", "message": "Caution"},
        ]
        output = standardise_agent_output(checks=checks, summary="Warn")
        assert output["status"] == "WARN"
        assert output["validated"] is True

    def test_standardise_agent_output_error(self):
        """ERROR output structure is correct."""
        from claude_client import standardise_agent_output
        output = standardise_agent_output(checks=[], error_message="AI unavailable")
        assert output["status"] == "ERROR"
        assert output["requires_review"] is True
        assert output["validated"] is False

    def test_standardise_agent_output_not_run(self):
        """NOT_RUN when no checks."""
        from claude_client import standardise_agent_output
        output = standardise_agent_output(checks=[], summary="No checks")
        assert output["status"] == "NOT_RUN"

    def test_document_id_enrichment(self):
        """Document ID and type are added to checks when provided."""
        from claude_client import standardise_agent_output
        checks = [{"id": "DOC-01", "label": "Test", "result": "pass", "message": "OK"}]
        output = standardise_agent_output(
            checks=checks, document_id="doc123", document_type="passport"
        )
        assert output["checks"][0]["document_id"] == "doc123"
        assert output["checks"][0]["document_type"] == "passport"


# ═══════════════════════════════════════════════════════════
# Improvement 3 & 4: Rule-Based + Explicit PASS/WARN/FAIL
# ═══════════════════════════════════════════════════════════

class TestRuleBasedChecks:
    """Check definitions must contain explicit PASS/WARN/FAIL criteria."""

    def test_rules_contain_pass_warn_fail(self):
        """AI and hybrid check definitions must contain explicit PASS, WARN, and FAIL criteria
        in their ai_prompt_hint / rule text so Claude can make the right decision.
        Pure rule-only checks are excluded as they run deterministically without AI."""
        from claude_client import ClaudeClient
        from verification_matrix import CheckClassification
        for doc_type, checks in ClaudeClient._get_check_definitions().items():
            for check in checks:
                # Only AI and hybrid checks require PASS/WARN/FAIL in their rule guidance.
                if check.get("classification") not in (CheckClassification.AI, CheckClassification.HYBRID):
                    continue
                rule = check.get("rule", "")
                assert "PASS" in rule, f"Check '{check['id']}' ({doc_type}) rule missing PASS criteria"
                assert "WARN" in rule or "warn" in rule.lower(), \
                    f"Check '{check['id']}' ({doc_type}) rule missing WARN criteria"
                assert "FAIL" in rule, f"Check '{check['id']}' ({doc_type}) rule missing FAIL criteria"

    def test_compute_overall_status_logic(self):
        """Overall status computation: FAIL > WARN > PASS."""
        from claude_client import compute_overall_status

        # No checks → NOT_RUN
        assert compute_overall_status([]) == "NOT_RUN"

        # All pass → PASS
        assert compute_overall_status([
            {"result": "pass"}, {"result": "pass"}
        ]) == "PASS"

        # Any warn → WARN
        assert compute_overall_status([
            {"result": "pass"}, {"result": "warn"}
        ]) == "WARN"

        # Any fail → FAIL (even with passes)
        assert compute_overall_status([
            {"result": "pass"}, {"result": "fail"}
        ]) == "FAIL"

        # Fail + warn → FAIL
        assert compute_overall_status([
            {"result": "warn"}, {"result": "fail"}
        ]) == "FAIL"


# ═══════════════════════════════════════════════════════════
# Improvement 5: Agent-to-Risk-Dimension Mapping
# ═══════════════════════════════════════════════════════════

class TestRiskDimensionMapping:
    """Agent-to-risk-dimension mapping must be complete."""

    def test_all_five_agents_have_dimensions(self):
        """Agents 1-5 must have risk dimension mappings."""
        from claude_client import AGENT_RISK_DIMENSIONS
        for agent_num in range(1, 6):
            assert agent_num in AGENT_RISK_DIMENSIONS, f"Agent {agent_num} missing from AGENT_RISK_DIMENSIONS"
            assert len(AGENT_RISK_DIMENSIONS[agent_num]) > 0, f"Agent {agent_num} has empty risk dimensions"

    def test_agent5_covers_all_dimensions(self):
        """Agent 5 (Compliance Memo) must cover all 5 dimensions."""
        from claude_client import AGENT_RISK_DIMENSIONS
        assert set(AGENT_RISK_DIMENSIONS[5]) == {"D1", "D2", "D3", "D4", "D5"}

    def test_risk_dimensions_in_database(self, temp_db):
        """Risk dimensions should be stored in the ai_agents table."""
        from db import get_db
        db = get_db()
        agents = db.execute("SELECT agent_number, risk_dimensions FROM ai_agents WHERE agent_number <= 5 ORDER BY agent_number").fetchall()
        db.close()
        for agent in agents:
            dims = json.loads(agent["risk_dimensions"]) if agent["risk_dimensions"] else []
            assert len(dims) > 0, f"Agent {agent['agent_number']} has no risk dimensions in DB"


# ═══════════════════════════════════════════════════════════
# Improvement 6: Escalation Triggers
# ═══════════════════════════════════════════════════════════

class TestEscalationTriggers:
    """Escalation logic must be deterministic and rule-based."""

    def test_fail_always_escalates(self):
        """Any FAIL check must trigger requires_review."""
        from claude_client import compute_escalation
        checks = [{"id": "DOC-01", "result": "fail", "message": "Bad"}]
        assert compute_escalation(checks) is True

    def test_pass_does_not_escalate(self):
        """All PASS checks must not escalate."""
        from claude_client import compute_escalation
        checks = [
            {"id": "DOC-01", "result": "pass"},
            {"id": "DOC-02", "result": "pass"},
        ]
        assert compute_escalation(checks) is False

    def test_empty_checks_no_escalation(self):
        """Empty checks list must not escalate."""
        from claude_client import compute_escalation
        assert compute_escalation([]) is False

    def test_always_escalate_check_ids(self):
        """Checks in ALWAYS_ESCALATE_CHECK_IDS must always escalate on FAIL."""
        from claude_client import compute_escalation, ALWAYS_ESCALATE_CHECK_IDS
        for check_id in ALWAYS_ESCALATE_CHECK_IDS:
            checks = [{"id": check_id, "result": "fail"}]
            assert compute_escalation(checks) is True, f"Check {check_id} should escalate on FAIL"

    def test_high_risk_dimension_warn_escalates(self):
        """WARN on high-risk dimension with score >= 3 must escalate."""
        from claude_client import compute_escalation
        checks = [{"id": "DOC-01", "result": "warn"}]
        risk_dims = {"D1": {"score": 3}}
        assert compute_escalation(checks, agent_number=1, risk_dimensions=risk_dims) is True

    def test_low_risk_dimension_warn_no_escalation(self):
        """WARN on low-risk dimension (score < 3) should not escalate."""
        from claude_client import compute_escalation
        checks = [{"id": "DOC-01", "result": "warn"}]
        risk_dims = {"D1": {"score": 1}}
        assert compute_escalation(checks, agent_number=1, risk_dimensions=risk_dims) is False


# ═══════════════════════════════════════════════════════════
# Improvement 7: Document-to-Check Mapping
# ═══════════════════════════════════════════════════════════

class TestDocumentToCheckMapping:
    """verify_document must include document_id and document_type."""

    def test_standardise_adds_document_metadata(self):
        """standardise_agent_output must inject document_id/document_type."""
        from claude_client import standardise_agent_output
        checks = [{"id": "DOC-01", "label": "Test", "result": "pass", "message": "OK"}]
        output = standardise_agent_output(
            checks=checks, document_id="d1", document_type="passport"
        )
        assert output["checks"][0]["document_id"] == "d1"
        assert output["checks"][0]["document_type"] == "passport"

    def test_existing_document_metadata_not_overwritten(self):
        """If check already has document_id, it should not be overwritten."""
        from claude_client import standardise_agent_output
        checks = [{"id": "DOC-01", "label": "Test", "result": "pass", "document_id": "existing"}]
        output = standardise_agent_output(
            checks=checks, document_id="new_id", document_type="passport"
        )
        assert output["checks"][0]["document_id"] == "existing"


# ═══════════════════════════════════════════════════════════
# Improvement 8: Traceability (agent_executions table)
# ═══════════════════════════════════════════════════════════

class TestTraceability:
    """Agent execution must be logged to the agent_executions table."""

    def test_agent_executions_table_exists(self, temp_db):
        """agent_executions table must exist in the schema."""
        import sqlite3
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_executions'"
        )
        result = cursor.fetchone()
        conn.close()
        assert result is not None, "agent_executions table does not exist"

    def test_log_agent_execution(self, temp_db):
        """log_agent_execution must successfully insert a row."""
        from db import log_agent_execution, get_db
        log_agent_execution(
            application_id="test-app-001",
            agent_name="verify_document",
            agent_number=1,
            status="verified",
            checks=[{"id": "DOC-01", "result": "pass"}],
            flags=[],
            requires_review=False,
            document_id="doc-001",
        )
        db = get_db()
        rows = db.execute(
            "SELECT * FROM agent_executions WHERE application_id='test-app-001'"
        ).fetchall()
        db.close()
        assert len(rows) >= 1
        row = rows[0]
        assert row["agent_name"] == "verify_document"
        assert row["agent_number"] == 1
        assert row["status"] == "verified"
        assert row["document_id"] == "doc-001"

    def test_log_agent_execution_with_error(self, temp_db):
        """log_agent_execution must handle error messages."""
        from db import log_agent_execution, get_db
        log_agent_execution(
            application_id="test-app-err",
            agent_name="verify_document",
            agent_number=1,
            status="error",
            error_message="AI timeout",
        )
        db = get_db()
        rows = db.execute(
            "SELECT * FROM agent_executions WHERE application_id='test-app-err'"
        ).fetchall()
        db.close()
        assert len(rows) >= 1
        assert rows[0]["error_message"] == "AI timeout"


# ═══════════════════════════════════════════════════════════
# Improvement 9: No Result = No Pass
# ═══════════════════════════════════════════════════════════

class TestNoResultNoPass:
    """Empty checks must never produce a PASS status."""

    def test_compute_overall_status_empty_is_not_run(self):
        """compute_overall_status([]) must return NOT_RUN."""
        from claude_client import compute_overall_status
        assert compute_overall_status([]) == "NOT_RUN"
        assert compute_overall_status(None) == "NOT_RUN"

    def test_standardise_empty_checks_not_run(self):
        """standardise_agent_output with no checks must return NOT_RUN."""
        from claude_client import standardise_agent_output
        output = standardise_agent_output(checks=[])
        assert output["status"] == "NOT_RUN"

    def test_safe_verification_status_empty(self):
        """_safe_verification_status with empty checks returns not_run."""
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from server import _safe_verification_status
        assert _safe_verification_status([], "verified") == "not_run"
        assert _safe_verification_status([], None) == "not_run"
        assert _safe_verification_status(None, "verified") == "not_run"

    def test_safe_verification_status_prevents_false_pass(self):
        """_safe_verification_status must not return 'verified' if checks have fails."""
        from server import _safe_verification_status
        checks = [{"result": "fail", "label": "Test"}]
        assert _safe_verification_status(checks, "verified") == "flagged"

    def test_safe_verification_status_preserves_real_pass(self):
        """_safe_verification_status returns 'verified' when checks genuinely pass."""
        from server import _safe_verification_status
        checks = [{"result": "pass", "label": "Test"}]
        assert _safe_verification_status(checks, "verified") == "verified"

    def test_safe_verification_status_warn_is_flagged(self):
        """_safe_verification_status must flag warns even if raw status is verified."""
        from server import _safe_verification_status
        checks = [{"result": "warn", "label": "Test"}]
        assert _safe_verification_status(checks, "verified") == "flagged"
