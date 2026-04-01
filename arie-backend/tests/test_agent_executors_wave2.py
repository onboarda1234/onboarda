"""
Wave 2 — Agent 5 (Compliance Memo & Risk Recommendation)
Unit tests for the unified executor bridge.

Tests cover:
  - Bridge to build_compliance_memo() when available
  - Fallback to summary mode when memo_handler fails
  - Classification tags on all output sections
  - Risk-model divergence cross-check
  - Backward compatibility (all fields consumed by contradictions detector)
  - Rule enforcement surfacing
  - Escalation logic
  - Data quality assessment
"""
import os
import sys
import sqlite3
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from supervisor.agent_executors import (
    execute_compliance_memo,
    _compute_risk_divergence,
    _classify_memo_sections,
    _build_business_model_summary,
    _RISK_TIER_RANK,
)


# ═══════════════════════════════════════════════════════════
# Test DB Setup
# ═══════════════════════════════════════════════════════════

def _create_test_db(app_data=None, directors=None, ubos=None, documents=None):
    """Create a temporary SQLite database with test data."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    db = sqlite3.connect(db_path)

    db.execute("""CREATE TABLE applications (
        id TEXT PRIMARY KEY, ref TEXT, company_name TEXT, country TEXT,
        registration_number TEXT, entity_type TEXT, ownership_structure TEXT,
        sector TEXT, risk_level TEXT, risk_score REAL, source_of_funds TEXT,
        expected_volume TEXT, client_id TEXT, status TEXT DEFAULT 'submitted',
        brn TEXT, assigned_to TEXT, prescreening_data TEXT DEFAULT '{}'
    )""")

    db.execute("""CREATE TABLE directors (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        first_name TEXT, last_name TEXT, full_name TEXT, nationality TEXT,
        position TEXT, is_pep TEXT DEFAULT 'No', pep_declaration TEXT DEFAULT '{}'
    )""")

    db.execute("""CREATE TABLE ubos (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        first_name TEXT, last_name TEXT, full_name TEXT, nationality TEXT,
        ownership_pct REAL, is_pep TEXT DEFAULT 'No', pep_declaration TEXT DEFAULT '{}'
    )""")

    db.execute("""CREATE TABLE intermediaries (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        entity_name TEXT, jurisdiction TEXT, ownership_pct REAL
    )""")

    db.execute("""CREATE TABLE documents (
        id TEXT PRIMARY KEY, application_id TEXT, document_type TEXT,
        filename TEXT, verification_status TEXT DEFAULT 'pending'
    )""")

    app_id = "test-app-w2"
    defaults = {
        "id": app_id, "ref": "APP-W2-001",
        "company_name": "MemoTest Ltd", "country": "Mauritius",
        "registration_number": "C99999", "entity_type": "Private Limited Company",
        "ownership_structure": "", "sector": "Financial Services",
        "risk_level": "MEDIUM", "risk_score": 45.0,
        "brn": "BRN-99999", "assigned_to": "admin001",
    }
    if app_data:
        defaults.update(app_data)

    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    db.execute(f"INSERT INTO applications ({cols}) VALUES ({placeholders})", list(defaults.values()))

    if directors:
        for i, d in enumerate(directors):
            d.setdefault("id", f"dir-w2-{i}")
            d.setdefault("application_id", app_id)
            d.setdefault("full_name", f"Director {i}")
            cols = ", ".join(d.keys())
            placeholders = ", ".join(["?"] * len(d))
            db.execute(f"INSERT INTO directors ({cols}) VALUES ({placeholders})", list(d.values()))

    if ubos:
        for i, u in enumerate(ubos):
            u.setdefault("id", f"ubo-w2-{i}")
            u.setdefault("application_id", app_id)
            u.setdefault("full_name", f"UBO {i}")
            u.setdefault("ownership_pct", 50.0)
            cols = ", ".join(u.keys())
            placeholders = ", ".join(["?"] * len(u))
            db.execute(f"INSERT INTO ubos ({cols}) VALUES ({placeholders})", list(u.values()))

    if documents:
        for i, doc in enumerate(documents):
            doc.setdefault("id", f"doc-w2-{i}")
            doc.setdefault("application_id", app_id)
            doc.setdefault("document_type", f"doc_type_{i}")
            doc.setdefault("filename", f"file_{i}.pdf")
            cols = ", ".join(doc.keys())
            placeholders = ", ".join(["?"] * len(doc))
            db.execute(f"INSERT INTO documents ({cols}) VALUES ({placeholders})", list(doc.values()))

    db.commit()
    db.close()
    return db_path, app_id


# ═══════════════════════════════════════════════════════════
# Risk Divergence Cross-Check Tests
# ═══════════════════════════════════════════════════════════

class TestRiskDivergence:
    def test_aligned_risks(self):
        result = _compute_risk_divergence("MEDIUM", 45.0, {"aggregated_risk": "MEDIUM"})
        assert result["divergence_detected"] is False
        assert result["tier_gap"] == 0

    def test_one_tier_gap_not_divergent(self):
        result = _compute_risk_divergence("MEDIUM", 45.0, {"aggregated_risk": "HIGH"})
        assert result["divergence_detected"] is False
        assert result["tier_gap"] == 1

    def test_two_tier_gap_is_divergent(self):
        result = _compute_risk_divergence("LOW", 20.0, {"aggregated_risk": "HIGH"})
        assert result["divergence_detected"] is True
        assert result["tier_gap"] == 2

    def test_three_tier_gap(self):
        result = _compute_risk_divergence("LOW", 10.0, {"aggregated_risk": "VERY_HIGH"})
        assert result["divergence_detected"] is True
        assert result["tier_gap"] == 3

    def test_missing_memo_risk(self):
        result = _compute_risk_divergence("MEDIUM", 45.0, {})
        assert result["divergence_detected"] is False
        assert "missing" in result["note"].lower()

    def test_classification_tag(self):
        result = _compute_risk_divergence("MEDIUM", 45.0, {"aggregated_risk": "MEDIUM"})
        assert result["classification"] == "rule"


# ═══════════════════════════════════════════════════════════
# Memo Section Classification Tests
# ═══════════════════════════════════════════════════════════

class TestMemoSectionClassification:
    def test_all_sections_tagged(self):
        memo = {"sections": {
            "executive_summary": "summary text",
            "client_overview": {"company": "Test"},
            "risk_assessment": {"level": "MEDIUM"},
            "compliance_decision": {"decision": "APPROVE"},
        }}
        tagged = _classify_memo_sections(memo)
        assert len(tagged) == 4
        for section in tagged:
            assert "classification" in section
            assert section["classification"] in ("rule", "hybrid", "ai")

    def test_executive_summary_is_hybrid(self):
        memo = {"sections": {"executive_summary": "text"}}
        tagged = _classify_memo_sections(memo)
        assert tagged[0]["classification"] == "hybrid"

    def test_risk_assessment_is_rule(self):
        memo = {"sections": {"risk_assessment": {}}}
        tagged = _classify_memo_sections(memo)
        assert tagged[0]["classification"] == "rule"

    def test_empty_memo(self):
        tagged = _classify_memo_sections({})
        assert tagged == []


# ═══════════════════════════════════════════════════════════
# Agent 5 Executor Tests — Unified Bridge
# ═══════════════════════════════════════════════════════════

class TestAgent5Executor:
    def test_basic_execution(self):
        """Executor runs without crashing and returns expected structure."""
        db_path, app_id = _create_test_db(
            directors=[{"full_name": "Alice Smith"}],
            ubos=[{"full_name": "Bob Jones", "ownership_pct": 100, "nationality": "UK"}],
            documents=[
                {"document_type": "passport", "verification_status": "verified"},
                {"document_type": "cert_inc", "verification_status": "verified"},
                {"document_type": "poa", "verification_status": "verified"},
            ],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})

        # Core fields must exist
        assert result["status"] in ("clean", "issues_found")
        assert result["confidence_score"] > 0
        assert result["recommended_risk_level"] is not None
        assert result["recommended_action"] is not None
        assert result["overall_risk_score"] is not None
        assert result["memo_source"] in ("unified", "fallback")
        os.unlink(db_path)

    def test_backward_compatible_fields(self):
        """All fields consumed by contradictions detector must be present."""
        db_path, app_id = _create_test_db(
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
            documents=[{"verification_status": "verified"}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})

        # Fields read by contradictions.py
        assert "recommendation" in result
        assert "recommended_action" in result
        assert "confidence_score" in result
        assert "recommended_risk_level" in result
        assert "status" in result
        assert "data_quality_assessment" in result
        assert "risk_indicators" in result or "risk_indicators_summary" in result
        os.unlink(db_path)

    def test_memo_source_unified_when_memo_succeeds(self):
        """When build_compliance_memo succeeds, source should be 'unified'."""
        db_path, app_id = _create_test_db(
            app_data={"risk_level": "MEDIUM", "risk_score": 45.0},
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
            documents=[{"verification_status": "verified"}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        assert result["memo_source"] == "unified"
        # Memo sections should be populated (not empty)
        assert len(result["memo_sections"]) > 0
        os.unlink(db_path)

    def test_classification_tags_on_findings(self):
        """All findings should have classification field."""
        db_path, app_id = _create_test_db(
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
            documents=[{"verification_status": "verified"}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        for f in result["findings"]:
            assert "classification" in f, f"Finding missing classification: {f.get('title')}"
        os.unlink(db_path)

    def test_risk_divergence_present(self):
        """Risk model divergence cross-check should be in output."""
        db_path, app_id = _create_test_db(
            app_data={"risk_level": "LOW", "risk_score": 20.0},
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
            documents=[{"verification_status": "verified"}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        assert "risk_model_divergence" in result
        div = result["risk_model_divergence"]
        assert "divergence_detected" in div
        assert "stored_risk_level" in div
        assert div["classification"] == "rule"
        os.unlink(db_path)

    def test_high_risk_escalates(self):
        """HIGH risk level should trigger escalation."""
        db_path, app_id = _create_test_db(
            app_data={"risk_level": "HIGH", "risk_score": 65.0},
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
            documents=[{"verification_status": "verified"}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        assert result["escalation_flag"] is True
        assert result["escalation_reason"] is not None
        os.unlink(db_path)

    def test_pep_exposure_in_screening_summary(self):
        """PEP directors/UBOs should be reflected in screening summary."""
        db_path, app_id = _create_test_db(
            directors=[{"full_name": "PEP Director", "is_pep": "Yes"}],
            ubos=[{"full_name": "Normal UBO", "ownership_pct": 100}],
            documents=[{"verification_status": "verified"}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        assert "1" in result["screening_summary"] or "PEP" in result["screening_summary"]
        os.unlink(db_path)

    def test_rule_enforcements_present(self):
        """Rule enforcements from memo_handler should surface."""
        db_path, app_id = _create_test_db(
            app_data={"risk_level": "MEDIUM", "risk_score": 45.0},
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
            documents=[{"verification_status": "verified"}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        assert "rule_enforcements" in result
        os.unlink(db_path)

    def test_validation_result_present(self):
        """Validation result from validation_engine should surface."""
        db_path, app_id = _create_test_db(
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
            documents=[{"verification_status": "verified"}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        # validation_result may be None if validation_engine not importable in test,
        # but the key should exist
        assert "validation_result" in result
        os.unlink(db_path)

    def test_supervisor_verdict_present(self):
        """Supervisor verdict from memo-level supervisor should surface."""
        db_path, app_id = _create_test_db(
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
            documents=[{"verification_status": "verified"}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        assert "supervisor_verdict" in result
        os.unlink(db_path)

    def test_new_fields_present(self):
        """Wave 2 new fields should all be present."""
        db_path, app_id = _create_test_db(
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
            documents=[{"verification_status": "verified"}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        assert "memo_source" in result
        assert "risk_model_divergence" in result
        assert "risk_dimensions" in result
        assert "rule_enforcements" in result
        assert "validation_result" in result
        assert "supervisor_verdict" in result
        os.unlink(db_path)

    def test_client_overview_populated(self):
        """Client overview should contain company details."""
        db_path, app_id = _create_test_db(
            app_data={"company_name": "TestCorp Ltd", "country": "UK", "sector": "Technology"},
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        co = result["client_overview"]
        assert co["company_name"] == "TestCorp Ltd"
        assert co["country"] == "UK"
        assert co["sector"] == "Technology"
        os.unlink(db_path)

    def test_overall_risk_score_capped(self):
        """Overall risk score should be 0.0-1.0."""
        db_path, app_id = _create_test_db(
            app_data={"risk_score": 85.0},
            directors=[{"full_name": "Alice"}],
            ubos=[{"full_name": "Bob", "ownership_pct": 100}],
        )
        result = execute_compliance_memo(app_id, {"db_path": db_path})
        assert 0.0 <= result["overall_risk_score"] <= 1.0
        os.unlink(db_path)


# ═══════════════════════════════════════════════════════════
# Business Model Summary Tests (preserved from Wave 1)
# ═══════════════════════════════════════════════════════════

class TestBusinessModelSummary:
    def test_high_risk_sector(self):
        result = _build_business_model_summary({"sector": "Cryptocurrency", "country": "US"})
        assert result["industry_risk_level"] == "HIGH"
        assert len(result["red_flags"]) > 0

    def test_low_risk_with_sof(self):
        result = _build_business_model_summary({
            "sector": "Technology", "country": "UK",
            "source_of_funds": "Revenue from SaaS", "expected_volume": "100000",
        })
        assert result["industry_risk_level"] == "LOW"
        assert result["plausibility"] == 0.80

    def test_missing_sof_flags(self):
        result = _build_business_model_summary({"sector": "Technology", "country": "UK"})
        assert "Source of funds not declared" in result["red_flags"]
        assert result["plausibility"] == 0.55
