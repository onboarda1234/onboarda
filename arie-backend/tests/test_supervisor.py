"""
Sprint 1 — Supervisor Tests (Framework + Memo Contradiction Detection)
Tests for supervisor framework (schemas, confidence, etc.) and
all 11 memo supervisor checks + verdict computation.
16 framework tests + 16 contradiction tests = 32 total.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tests.conftest import make_base_memo


# ═══════════════════════════════════════════════════════════════
# PART A: Supervisor Framework Tests (original)
# ═══════════════════════════════════════════════════════════════

class TestSupervisorSchemas:
    def test_agent_types_defined(self):
        from supervisor.schemas import AgentType
        assert len(AgentType) == 10
        assert AgentType.IDENTITY_DOCUMENT_INTEGRITY is not None
        assert AgentType.FINCRIME_SCREENING is not None

    def test_confidence_routing_thresholds(self):
        from supervisor.schemas import ConfidenceRouting
        assert ConfidenceRouting.NORMAL is not None
        assert ConfidenceRouting.HUMAN_REVIEW is not None
        assert ConfidenceRouting.MANDATORY_ESCALATION is not None


class TestValidator:
    def test_validator_initializes(self):
        from supervisor.validator import SchemaValidator
        v = SchemaValidator()
        assert v is not None

    def test_basic_validation(self):
        from supervisor.validator import SchemaValidator
        from supervisor.schemas import AgentType
        v = SchemaValidator()
        output = {
            "agent_type": "identity_document_integrity",
            "status": "completed",
            "confidence_score": 0.85,
            "summary": "Document verified",
            "findings": [],
            "evidence": [],
            "risk_indicators": [],
            "requires_escalation": False,
        }
        result = v.validate(output, AgentType.IDENTITY_DOCUMENT_INTEGRITY)
        assert result is not None


class TestConfidence:
    def test_evaluator_initializes(self):
        from supervisor.confidence import ConfidenceEvaluator
        ce = ConfidenceEvaluator()
        assert ce is not None
        assert ce.normal_threshold == 0.85
        assert ce.review_threshold == 0.65

    def test_routing_decision(self):
        from supervisor.confidence import ConfidenceEvaluator
        from supervisor.schemas import ConfidenceRouting
        ce = ConfidenceEvaluator()
        assert ce.route_confidence(0.90) == ConfidenceRouting.NORMAL
        assert ce.route_confidence(0.75) == ConfidenceRouting.HUMAN_REVIEW
        assert ce.route_confidence(0.50) == ConfidenceRouting.MANDATORY_ESCALATION


class TestContradictions:
    def test_detector_initializes(self):
        from supervisor.contradictions import ContradictionDetector
        cd = ContradictionDetector()
        assert cd is not None


class TestRulesEngine:
    def test_engine_initializes(self):
        from supervisor.rules_engine import RulesEngine
        re = RulesEngine()
        re.load_default_rules()
        assert re is not None
        assert len(re.rules) > 0

    def test_rules_have_priority(self):
        from supervisor.rules_engine import RulesEngine
        re = RulesEngine()
        re.load_default_rules()
        priorities = [r.priority for r in re.rules]
        assert priorities == sorted(priorities), "Rules should be priority-ordered"


class TestAuditLogger:
    def test_logger_initializes(self, temp_db):
        from supervisor.audit import AuditLogger
        al = AuditLogger(db_path=temp_db)
        assert al is not None

    def test_hash_chain_integrity(self, temp_db):
        from supervisor.audit import AuditLogger
        from supervisor.schemas import AuditEventType
        al = AuditLogger(db_path=temp_db)
        al.log(
            event_type=AuditEventType.AGENT_RUN_STARTED,
            action="test",
            application_id="app1",
            data={"key": "value"}
        )
        al.log(
            event_type=AuditEventType.AGENT_RUN_COMPLETED,
            action="test2",
            application_id="app1",
            data={"key": "value2"}
        )
        result = al.verify_chain_integrity(limit=10)
        assert result["verified"] is True


# ═══════════════════════════════════════════════════════════════
# PART B: Memo Supervisor Contradiction Tests (Sprint 1)
# ═══════════════════════════════════════════════════════════════

class TestMemoCheck1_RiskVsDecision:
    """Check 1: Risk rating vs decision consistency."""

    def test_high_risk_approve_contradiction(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "approval_recommendation": "APPROVE"}
        })
        result = run_memo_supervisor(memo)
        cats = [c["category"] for c in result["contradictions"]]
        assert "risk_vs_decision" in cats

    def test_low_risk_reject_contradiction(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "LOW", "approval_recommendation": "REJECT"}
        })
        result = run_memo_supervisor(memo)
        cats = [c["category"] for c in result["contradictions"]]
        assert "risk_vs_decision" in cats

    def test_medium_conditions_no_contradiction(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo()
        result = run_memo_supervisor(memo)
        risk_c = [c for c in result["contradictions"] if c["category"] == "risk_vs_decision"]
        assert len(risk_c) == 0


class TestMemoCheck2_Ownership:
    """Check 2: Ownership gaps vs LOW rating."""

    def test_low_ownership_with_gaps(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "sections": {
                "ownership_and_control": {"content": "UBO data not provided. Cannot be determined.", "structure_complexity": "Simple", "control_statement": "Unknown."},
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "MEDIUM"},
                    "business_risk": {"rating": "LOW"},
                    "transaction_risk": {"rating": "MEDIUM"},
                    "ownership_risk": {"rating": "LOW"},
                    "financial_crime_risk": {"rating": "LOW"}
                }}
            }
        })
        result = run_memo_supervisor(memo)
        own_c = [c for c in result["contradictions"] if c["category"] == "ownership_inconsistency"]
        assert len(own_c) >= 1


class TestMemoCheck3_PEP:
    """Check 3: PEP findings vs screening results."""

    def test_pep_match_denied_in_exec(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "sections": {
                "screening_results": {"content": "PEP confirmed match identified in screening results."},
                "executive_summary": {"content": "No PEP exposure. Low risk entity."}
            }
        })
        result = run_memo_supervisor(memo)
        pep_issues = [c for c in result["contradictions"] if c["category"] in ("pep_inconsistency", "pep_advisory")]
        assert len(pep_issues) >= 1

    def test_pep_handled_not_critical(self, temp_db):
        """PEP identified AND flagged for enhanced measures → not a critical contradiction."""
        from server import run_memo_supervisor
        memo = make_base_memo({
            "sections": {
                "screening_results": {"content": "PEP confirmed match identified and flagged for enhanced due diligence. Enhanced measures applied."},
                "executive_summary": {"content": "No PEP exposure in executive summary."}
            }
        })
        result = run_memo_supervisor(memo)
        pep_critical = [c for c in result["contradictions"] if c["category"] == "pep_inconsistency"]
        assert len(pep_critical) == 0, "Properly handled PEP should not trigger critical contradiction"

    def test_clean_screening_no_pep_issue(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo()
        result = run_memo_supervisor(memo)
        pep_issues = [c for c in result["contradictions"] if c["category"] in ("pep_inconsistency", "pep_advisory")]
        assert len(pep_issues) == 0


class TestMemoCheck4_Docs:
    """Check 4: Outstanding docs vs APPROVE."""

    def test_outstanding_docs_approve(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"approval_recommendation": "APPROVE"},
            "sections": {
                "document_verification": {"content": "2 documents outstanding and pending."},
                "compliance_decision": {"decision": "APPROVE"}
            }
        })
        result = run_memo_supervisor(memo)
        doc_c = [c for c in result["contradictions"] if c["category"] == "doc_vs_decision"]
        assert len(doc_c) >= 1

    def test_no_documents_blocks_approval(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {
                "approval_recommendation": "APPROVE_WITH_CONDITIONS",
                "document_count": 0,
                "documentation_complete": False
            },
            "sections": {
                "document_verification": {"content": "No documents have been uploaded. Entity verification cannot be completed."},
                "compliance_decision": {"decision": "APPROVE_WITH_CONDITIONS"}
            }
        })
        result = run_memo_supervisor(memo)
        assert result["can_approve"] is False
        assert result["requires_sco_review"] is True
        warning_cats = [w["category"] for w in result["warnings"]]
        assert "missing_documents" in warning_cats


class TestMemoCheck5_RedFlags:
    """Check 5: Red flags without mitigants."""

    def test_flags_no_mitigants(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH"},
            "sections": {"red_flags_and_mitigants": {
                "red_flags": ["High risk jurisdiction", "Complex ownership"],
                "mitigants": []
            }}
        })
        result = run_memo_supervisor(memo)
        rf_c = [c for c in result["contradictions"] if c["category"] == "rf_mitigant_imbalance"]
        assert len(rf_c) >= 1


class TestMemoCheck6_Factors:
    """Check 6: Misclassified factors."""

    def test_decreasing_in_increasing_list(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "sections": {"ai_explainability": {
                "content": "Analysis.",
                "risk_increasing_factors": ["No PEP exposure", "Clean sanctions"],
                "risk_decreasing_factors": []
            }}
        })
        result = run_memo_supervisor(memo)
        factor_c = [c for c in result["contradictions"] if c["category"] == "factor_misclassification"]
        assert len(factor_c) >= 1


class TestMemoCheck8_JurisdictionMonitoring:
    """Check 8: HIGH jurisdiction + Standard monitoring."""

    def test_high_jur_standard_monitoring(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "HIGH"},
                    "business_risk": {"rating": "LOW"},
                    "transaction_risk": {"rating": "MEDIUM"},
                    "ownership_risk": {"rating": "MEDIUM"},
                    "financial_crime_risk": {"rating": "LOW"}
                }},
                "ongoing_monitoring": {"content": "Standard monitoring tier applied."}
            }
        })
        result = run_memo_supervisor(memo)
        jur_c = [c for c in result["contradictions"] if c["category"] == "jurisdiction_vs_monitoring"]
        assert len(jur_c) >= 1


class TestMemoVerdict:
    """Verdict computation logic."""

    def test_clean_memo_consistent(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo()
        result = run_memo_supervisor(memo)
        assert result["verdict"] in ("CONSISTENT", "CONSISTENT_WITH_WARNINGS")

    def test_critical_contradiction_inconsistent(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "approval_recommendation": "APPROVE"}
        })
        result = run_memo_supervisor(memo)
        assert result["verdict"] == "INCONSISTENT"

    def test_confidence_penalised(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "approval_recommendation": "APPROVE"}
        })
        result = run_memo_supervisor(memo)
        assert result["supervisor_confidence"] < 1.0

    def test_result_fields(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo()
        result = run_memo_supervisor(memo)
        for field in ["verdict", "contradictions", "warnings", "recommendation", "supervisor_confidence"]:
            assert field in result, f"Missing field: {field}"



# ═══════════════════════════════════════════════════════════════
# PART C: Supervisor Audit Hash-Chain Hardening Tests (Priority E)
# ═══════════════════════════════════════════════════════════════

class TestAuditChainEntry:
    """Tests for append_verdict_chain_entry and the hash-chain guarantee.

    All tests use the shared temp_db fixture.  Tests that need a clean
    supervisor_audit_log table call _clear_chain() at the start so they
    do not observe entries written by earlier tests.
    """

    def _clear_chain(self, temp_db):
        """Delete all rows from supervisor_audit_log for a clean slate."""
        import sqlite3
        conn = sqlite3.connect(temp_db)
        conn.execute("DELETE FROM supervisor_audit_log")
        conn.commit()
        conn.close()

    def _get_chain_rows(self, temp_db, application_id=None):
        """Return supervisor_audit_log rows ordered by timestamp ASC."""
        import sqlite3
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        if application_id:
            rows = conn.execute(
                "SELECT * FROM supervisor_audit_log WHERE application_id = ? ORDER BY timestamp ASC",
                (application_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM supervisor_audit_log ORDER BY timestamp ASC"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def test_append_verdict_chain_entry_creates_row(self, temp_db):
        """A single verdict write produces exactly one supervisor_audit_log row."""
        from db import get_db
        from supervisor.audit import append_verdict_chain_entry
        db = get_db()
        entry_hash = append_verdict_chain_entry(
            db=db,
            application_id="app-ce-001",
            verdict="CONSISTENT",
            contradiction_count=0,
            supervisor_confidence=0.95,
            memo_id="memo-ce-001",
            actor_id="co-1",
            actor_name="Test Officer",
            actor_role="co",
        )
        db.commit()
        db.close()

        rows = self._get_chain_rows(temp_db, application_id="app-ce-001")
        assert len(rows) == 1
        row = rows[0]
        assert row["event_type"] == "supervisor_verdict"
        assert row["application_id"] == "app-ce-001"
        assert row["entry_hash"] == entry_hash
        assert row["entry_hash"] is not None
        assert len(row["entry_hash"]) == 64  # SHA-256 hex

    def test_genesis_entry_has_null_previous_hash(self, temp_db):
        """The first chain entry has previous_hash = NULL (genesis)."""
        self._clear_chain(temp_db)
        from db import get_db
        from supervisor.audit import append_verdict_chain_entry
        db = get_db()
        append_verdict_chain_entry(
            db=db,
            application_id="app-genesis",
            verdict="CONSISTENT",
            contradiction_count=0,
            supervisor_confidence=1.0,
            memo_id="memo-genesis",
        )
        db.commit()
        db.close()

        rows = self._get_chain_rows(temp_db)
        assert len(rows) == 1
        assert rows[0]["previous_hash"] is None

    def test_multiple_verdicts_form_linked_chain(self, temp_db):
        """Multiple verdict writes form a properly hash-linked chain."""
        self._clear_chain(temp_db)
        from db import get_db
        from supervisor.audit import append_verdict_chain_entry
        for i in range(3):
            db = get_db()
            append_verdict_chain_entry(
                db=db,
                application_id=f"app-chain-{i}",
                verdict="CONSISTENT",
                contradiction_count=i,
                supervisor_confidence=0.9 - i * 0.05,
                memo_id=f"memo-chain-{i}",
            )
            db.commit()
            db.close()

        rows = self._get_chain_rows(temp_db)
        assert len(rows) == 3
        assert rows[0]["previous_hash"] is None
        assert rows[1]["previous_hash"] == rows[0]["entry_hash"]
        assert rows[2]["previous_hash"] == rows[1]["entry_hash"]

    def test_verify_chain_succeeds_on_intact_chain(self, temp_db):
        """verify_chain_integrity() returns verified=True for an intact chain."""
        self._clear_chain(temp_db)
        from db import get_db
        from supervisor.audit import append_verdict_chain_entry, AuditLogger
        for i in range(4):
            db = get_db()
            append_verdict_chain_entry(
                db=db,
                application_id="app-verify",
                verdict="CONSISTENT_WITH_WARNINGS",
                contradiction_count=1,
                supervisor_confidence=0.85,
                memo_id=f"memo-v{i}",
            )
            db.commit()
            db.close()

        al = AuditLogger(db_path=temp_db)
        result = al.verify_chain_integrity(limit=100)
        assert result["verified"] is True
        assert result["entries_checked"] == 4
        assert result.get("broken_links", []) == []

    def test_deliberate_hash_tampering_detected(self, temp_db):
        """verify_chain_integrity() detects a tampered entry_hash."""
        import sqlite3
        self._clear_chain(temp_db)
        from db import get_db
        from supervisor.audit import append_verdict_chain_entry, AuditLogger
        db = get_db()
        append_verdict_chain_entry(
            db=db,
            application_id="app-tamper",
            verdict="CONSISTENT",
            contradiction_count=0,
            supervisor_confidence=1.0,
            memo_id="memo-tamper",
        )
        db.commit()
        db.close()

        # Tamper: overwrite entry_hash with garbage
        conn = sqlite3.connect(temp_db)
        conn.execute("UPDATE supervisor_audit_log SET entry_hash = 'deadbeef'")
        conn.commit()
        conn.close()

        al = AuditLogger(db_path=temp_db)
        result = al.verify_chain_integrity(limit=100)
        assert result["verified"] is False
        assert len(result.get("broken_links", [])) >= 1

    def test_deliberate_chain_link_tampering_detected(self, temp_db):
        """verify_chain_integrity() detects a broken chain link (previous_hash mismatch)."""
        import sqlite3
        self._clear_chain(temp_db)
        from db import get_db
        from supervisor.audit import append_verdict_chain_entry, AuditLogger
        for i in range(2):
            db = get_db()
            append_verdict_chain_entry(
                db=db,
                application_id="app-linkbreak",
                verdict="CONSISTENT",
                contradiction_count=0,
                supervisor_confidence=1.0,
                memo_id=f"memo-linkbreak-{i}",
            )
            db.commit()
            db.close()

        # Tamper: corrupt the previous_hash of the second entry
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "UPDATE supervisor_audit_log SET previous_hash = 'badhash' "
            "WHERE previous_hash IS NOT NULL"
        )
        conn.commit()
        conn.close()

        al = AuditLogger(db_path=temp_db)
        result = al.verify_chain_integrity(limit=100)
        assert result["verified"] is False
        assert len(result.get("broken_links", [])) >= 1

    def test_chain_append_failure_prevents_commit(self, temp_db):
        """If append_verdict_chain_entry raises, the verdict UPDATE is not committed."""
        from db import get_db
        from supervisor.audit import append_verdict_chain_entry

        app_id = "app-fail-test"
        # Insert a real compliance_memo row using auto-increment id
        db = get_db()
        try:
            db.execute(
                "INSERT OR IGNORE INTO compliance_memos "
                "(application_id, memo_data, review_status) VALUES (?, '{}', 'draft')",
                (app_id,),
            )
            db.commit()
            memo_row = db.execute(
                "SELECT id FROM compliance_memos WHERE application_id = ? ORDER BY id DESC LIMIT 1",
                (app_id,),
            ).fetchone()
            memo_id = str(memo_row["id"])
        finally:
            db.close()

        import unittest.mock as mock
        import supervisor.audit as sa

        verdict_committed = []
        db2 = get_db()
        try:
            db2.execute(
                "UPDATE compliance_memos SET supervisor_status = 'CONSISTENT' WHERE id = ?",
                (memo_id,),
            )
            # Simulate a chain write failure — exception must propagate
            with mock.patch.object(
                sa,
                "append_verdict_chain_entry",
                side_effect=RuntimeError("Simulated chain write failure"),
            ):
                sa.append_verdict_chain_entry(
                    db=db2,
                    application_id=app_id,
                    verdict="CONSISTENT",
                    contradiction_count=0,
                    supervisor_confidence=1.0,
                    memo_id=memo_id,
                )
            db2.commit()
            verdict_committed.append(True)
        except RuntimeError:
            pass  # Expected — do NOT commit
        finally:
            db2.close()

        db3 = get_db()
        row = db3.execute(
            "SELECT supervisor_status FROM compliance_memos WHERE id = ?", (memo_id,)
        ).fetchone()
        db3.close()
        assert verdict_committed == [], "commit must not have been reached"
        # supervisor_status must still be the default (never updated to 'CONSISTENT')
        assert row["supervisor_status"] != "CONSISTENT"

    def test_verdict_event_type_is_supervisor_verdict(self, temp_db):
        """Entries written by append_verdict_chain_entry use SUPERVISOR_VERDICT event type."""
        from db import get_db
        from supervisor.audit import append_verdict_chain_entry
        db = get_db()
        append_verdict_chain_entry(
            db=db,
            application_id="app-evtype",
            verdict="INCONSISTENT",
            contradiction_count=2,
            supervisor_confidence=0.7,
            memo_id="memo-evtype",
        )
        db.commit()
        db.close()

        rows = self._get_chain_rows(temp_db, application_id="app-evtype")
        assert len(rows) == 1
        assert rows[0]["event_type"] == "supervisor_verdict"

    def test_get_entries_returns_verdict_entries(self, temp_db):
        """AuditLogger.get_entries() returns rows written by append_verdict_chain_entry."""
        from db import get_db
        from supervisor.audit import append_verdict_chain_entry, AuditLogger
        db = get_db()
        append_verdict_chain_entry(
            db=db,
            application_id="app-getentries",
            verdict="CONSISTENT",
            contradiction_count=0,
            supervisor_confidence=0.99,
            memo_id="memo-ge",
        )
        db.commit()
        db.close()

        al = AuditLogger(db_path=temp_db)
        entries = al.get_entries(application_id="app-getentries", limit=10)
        assert len(entries) == 1
        assert entries[0]["application_id"] == "app-getentries"
        assert entries[0]["event_type"] == "supervisor_verdict"

    def test_verify_chain_on_empty_table_returns_verified(self, temp_db):
        """verify_chain_integrity() on an empty table returns verified=True, entries_checked=0."""
        self._clear_chain(temp_db)
        from supervisor.audit import AuditLogger
        al = AuditLogger(db_path=temp_db)
        result = al.verify_chain_integrity(limit=100)
        assert result["verified"] is True
        assert result["entries_checked"] == 0
