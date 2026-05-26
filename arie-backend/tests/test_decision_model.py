"""
Tests for decision_model.py — Normalized decision record layer.
Covers: record construction, validation, persistence, and retrieval.
"""
import os
import sys
import json
import sqlite3
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from decision_model import (
    VALID_DECISION_TYPES,
    VALID_SOURCES,
    VALID_RISK_LEVELS,
    build_decision_record,
    build_from_application_decision,
    build_from_supervisor_verdict,
    save_decision_record,
    get_decision_records,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def decision_db(tmp_path):
    """Create a minimal SQLite DB with the decision_records table."""
    db_path = str(tmp_path / "test_decisions.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE decision_records (
            id TEXT PRIMARY KEY,
            application_ref TEXT NOT NULL,
            decision_type TEXT NOT NULL,
            risk_level TEXT,
            confidence_score REAL,
            source TEXT NOT NULL,
            actor_user_id TEXT,
            actor_role TEXT,
            timestamp TEXT NOT NULL,
            key_flags TEXT DEFAULT '[]',
            override_flag INTEGER DEFAULT 0,
            override_reason TEXT,
            extra_json TEXT DEFAULT '{}'
        )
    """)
    conn.commit()

    # Wrap in a minimal adapter that mimics DBConnection
    class _TestDB:
        def __init__(self, c):
            self._conn = c

        def execute(self, sql, params=None):
            if params:
                return self._conn.execute(sql, params)
            return self._conn.execute(sql)

        def commit(self):
            self._conn.commit()

        def close(self):
            self._conn.close()

    yield _TestDB(conn)
    conn.close()


@pytest.fixture
def sample_app():
    """Minimal application dict matching DB row structure."""
    return {
        "id": "app-001",
        "ref": "ARF-2026-0001",
        "company_name": "Test Corp",
        "risk_level": "HIGH",
        "risk_score": 55.0,
        "status": "in_review",
    }


@pytest.fixture
def sample_user():
    """Minimal authenticated user dict."""
    return {
        "sub": "user-abc",
        "name": "Test Officer",
        "role": "sco",
    }


# ============================================================================
# Test: build_decision_record
# ============================================================================

class TestBuildDecisionRecord:
    def test_basic_record(self):
        record = build_decision_record(
            application_ref="ARF-2026-0001",
            decision_type="approve",
            source="manual",
            actor={"user_id": "user-1", "role": "admin"},
        )
        assert record["application_ref"] == "ARF-2026-0001"
        assert record["decision_type"] == "approve"
        assert record["source"] == "manual"
        assert record["actor"]["user_id"] == "user-1"
        assert record["actor"]["role"] == "admin"
        assert record["decision_id"]  # UUID should be non-empty
        assert record["timestamp"]  # Should have a timestamp
        assert record["key_flags"] == []
        assert record["override_flag"] is False
        assert record["override_reason"] is None

    def test_with_all_fields(self):
        record = build_decision_record(
            application_ref="ARF-2026-0002",
            decision_type="reject",
            source="supervisor",
            actor={"user_id": "user-2", "role": "sco"},
            risk_level="VERY_HIGH",
            confidence_score=0.75,
            key_flags=["pep_match", "sanctions_hit"],
            override_flag=True,
            override_reason="Manual review required",
            extra={"contradiction_count": 3},
        )
        assert record["risk_level"] == "VERY_HIGH"
        assert record["confidence_score"] == 0.75
        assert record["key_flags"] == ["pep_match", "sanctions_hit"]
        assert record["override_flag"] is True
        assert record["override_reason"] == "Manual review required"
        assert record["extra"]["contradiction_count"] == 3

    def test_invalid_decision_type_raises(self):
        with pytest.raises(ValueError, match="Invalid decision_type"):
            build_decision_record(
                application_ref="X",
                decision_type="invalid_decision",
                source="manual",
                actor={"user_id": "u", "role": "r"},
            )

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="Invalid source"):
            build_decision_record(
                application_ref="X",
                decision_type="approve",
                source="unknown_source",
                actor={"user_id": "u", "role": "r"},
            )

    def test_invalid_risk_level_raises(self):
        with pytest.raises(ValueError, match="Invalid risk_level"):
            build_decision_record(
                application_ref="X",
                decision_type="approve",
                source="manual",
                actor={"user_id": "u", "role": "r"},
                risk_level="EXTREME",
            )

    def test_override_without_reason_raises(self):
        with pytest.raises(ValueError, match="override_reason is required"):
            build_decision_record(
                application_ref="X",
                decision_type="approve",
                source="manual",
                actor={"user_id": "u", "role": "r"},
                override_flag=True,
            )

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValueError, match="confidence_score must be"):
            build_decision_record(
                application_ref="X",
                decision_type="approve",
                source="manual",
                actor={"user_id": "u", "role": "r"},
                confidence_score=1.5,
            )

    def test_all_valid_decision_types(self):
        for dt in VALID_DECISION_TYPES:
            record = build_decision_record(
                application_ref="X",
                decision_type=dt,
                source="manual",
                actor={"user_id": "u", "role": "r"},
            )
            assert record["decision_type"] == dt

    def test_all_valid_sources(self):
        for src in VALID_SOURCES:
            record = build_decision_record(
                application_ref="X",
                decision_type="approve",
                source=src,
                actor={"user_id": "u", "role": "r"},
            )
            assert record["source"] == src

    def test_null_risk_level_allowed(self):
        record = build_decision_record(
            application_ref="X",
            decision_type="approve",
            source="manual",
            actor={"user_id": "u", "role": "r"},
            risk_level=None,
        )
        assert record["risk_level"] is None

    def test_null_confidence_allowed(self):
        record = build_decision_record(
            application_ref="X",
            decision_type="approve",
            source="manual",
            actor={"user_id": "u", "role": "r"},
            confidence_score=None,
        )
        assert record["confidence_score"] is None

    def test_override_reason_ignored_when_flag_false(self):
        record = build_decision_record(
            application_ref="X",
            decision_type="approve",
            source="manual",
            actor={"user_id": "u", "role": "r"},
            override_flag=False,
            override_reason="should be ignored",
        )
        assert record["override_reason"] is None


# ============================================================================
# Test: save_decision_record / get_decision_records
# ============================================================================

class TestPersistence:
    def test_save_and_retrieve(self, decision_db):
        record = build_decision_record(
            application_ref="ARF-2026-0001",
            decision_type="approve",
            source="manual",
            actor={"user_id": "user-1", "role": "admin"},
            risk_level="LOW",
            confidence_score=0.9,
        )
        save_decision_record(decision_db, record)
        decision_db.commit()

        records = get_decision_records(decision_db, "ARF-2026-0001")
        assert len(records) == 1
        assert records[0]["decision_id"] == record["decision_id"]
        assert records[0]["decision_type"] == "approve"
        assert records[0]["risk_level"] == "LOW"
        assert records[0]["confidence_score"] == 0.9
        assert records[0]["actor"]["user_id"] == "user-1"

    def test_multiple_records_ordered_by_timestamp(self, decision_db):
        for dt in ["reject", "escalate_edd", "approve"]:
            record = build_decision_record(
                application_ref="ARF-2026-0001",
                decision_type=dt,
                source="manual",
                actor={"user_id": "user-1", "role": "admin"},
            )
            save_decision_record(decision_db, record)
        decision_db.commit()

        records = get_decision_records(decision_db, "ARF-2026-0001")
        assert len(records) == 3

    def test_filter_by_decision_type(self, decision_db):
        for dt in ["approve", "reject", "approve"]:
            record = build_decision_record(
                application_ref="ARF-2026-0001",
                decision_type=dt,
                source="manual",
                actor={"user_id": "user-1", "role": "admin"},
            )
            save_decision_record(decision_db, record)
        decision_db.commit()

        approvals = get_decision_records(decision_db, "ARF-2026-0001", decision_type="approve")
        assert len(approvals) == 2
        rejections = get_decision_records(decision_db, "ARF-2026-0001", decision_type="reject")
        assert len(rejections) == 1

    def test_limit_parameter(self, decision_db):
        for _ in range(5):
            record = build_decision_record(
                application_ref="ARF-2026-0001",
                decision_type="approve",
                source="manual",
                actor={"user_id": "user-1", "role": "admin"},
            )
            save_decision_record(decision_db, record)
        decision_db.commit()

        records = get_decision_records(decision_db, "ARF-2026-0001", limit=3)
        assert len(records) == 3

    def test_no_records_returns_empty(self, decision_db):
        records = get_decision_records(decision_db, "ARF-NONEXISTENT")
        assert records == []

    def test_override_fields_persisted(self, decision_db):
        record = build_decision_record(
            application_ref="ARF-2026-0001",
            decision_type="approve",
            source="manual",
            actor={"user_id": "user-1", "role": "admin"},
            override_flag=True,
            override_reason="Officer override",
        )
        save_decision_record(decision_db, record)
        decision_db.commit()

        records = get_decision_records(decision_db, "ARF-2026-0001")
        assert records[0]["override_flag"] is True
        assert records[0]["override_reason"] == "Officer override"

    def test_extra_json_persisted(self, decision_db):
        record = build_decision_record(
            application_ref="ARF-2026-0001",
            decision_type="reject",
            source="supervisor",
            actor={"user_id": "user-1", "role": "admin"},
            extra={"verdict": "INCONSISTENT", "contradiction_count": 2},
        )
        save_decision_record(decision_db, record)
        decision_db.commit()

        records = get_decision_records(decision_db, "ARF-2026-0001")
        assert records[0]["extra"]["verdict"] == "INCONSISTENT"
        assert records[0]["extra"]["contradiction_count"] == 2

    def test_key_flags_persisted(self, decision_db):
        record = build_decision_record(
            application_ref="ARF-2026-0001",
            decision_type="reject",
            source="manual",
            actor={"user_id": "user-1", "role": "admin"},
            key_flags=["risk:HIGH", "ai_override"],
        )
        save_decision_record(decision_db, record)
        decision_db.commit()

        records = get_decision_records(decision_db, "ARF-2026-0001")
        assert records[0]["key_flags"] == ["risk:HIGH", "ai_override"]


# ============================================================================
# Test: build_from_application_decision
# ============================================================================

class TestBuildFromApplicationDecision:
    def test_basic_manual_decision(self, sample_app, sample_user):
        record = build_from_application_decision(
            app=sample_app,
            decision="approve",
            decision_reason="All checks passed",
            user=sample_user,
        )
        assert record["application_ref"] == "ARF-2026-0001"
        assert record["decision_type"] == "approve"
        assert record["source"] == "manual"
        assert record["actor"]["user_id"] == "user-abc"
        assert record["actor"]["role"] == "sco"
        assert record["risk_level"] == "HIGH"
        assert "risk:HIGH" in record["key_flags"]
        assert record["extra"]["decision_reason"] == "All checks passed"

    def test_override_flagged(self, sample_app, sample_user):
        record = build_from_application_decision(
            app=sample_app,
            decision="approve",
            decision_reason="Override needed",
            user=sample_user,
            override_ai=True,
            override_reason="Manual review completed",
        )
        assert record["override_flag"] is True
        assert record["override_reason"] == "Manual review completed"
        assert "ai_override" in record["key_flags"]

    def test_confidence_derived_from_supervisor(self, sample_app, sample_user):
        supervisor_result = {
            "verdict": "CONSISTENT",
            "supervisor_confidence": 0.85,
            "contradiction_count": 0,
            "can_approve": True,
        }
        record = build_from_application_decision(
            app=sample_app,
            decision="approve",
            decision_reason="Approved",
            user=sample_user,
            supervisor_result=supervisor_result,
        )
        assert record["confidence_score"] == 0.85

    def test_confidence_normalized_from_percentage(self, sample_app, sample_user):
        supervisor_result = {
            "verdict": "CONSISTENT",
            "supervisor_confidence": 85,
            "contradiction_count": 0,
            "can_approve": True,
        }
        record = build_from_application_decision(
            app=sample_app,
            decision="approve",
            decision_reason="Approved",
            user=sample_user,
            supervisor_result=supervisor_result,
        )
        assert record["confidence_score"] == 0.85

    def test_no_supervisor_gives_null_confidence(self, sample_app, sample_user):
        record = build_from_application_decision(
            app=sample_app,
            decision="approve",
            decision_reason="Approved",
            user=sample_user,
        )
        assert record["confidence_score"] is None

    def test_inconsistent_supervisor_adds_flag(self, sample_app, sample_user):
        supervisor_result = {
            "verdict": "INCONSISTENT",
            "supervisor_confidence": 0.40,
            "contradiction_count": 3,
            "can_approve": False,
        }
        record = build_from_application_decision(
            app=sample_app,
            decision="reject",
            decision_reason="Failed supervisor",
            user=sample_user,
            supervisor_result=supervisor_result,
        )
        assert "supervisor:inconsistent" in record["key_flags"]

    def test_warnings_supervisor_adds_flag(self, sample_app, sample_user):
        supervisor_result = {
            "verdict": "CONSISTENT_WITH_WARNINGS",
            "supervisor_confidence": 0.65,
            "contradiction_count": 2,
            "can_approve": True,
        }
        record = build_from_application_decision(
            app=sample_app,
            decision="approve",
            decision_reason="Approved with warnings",
            user=sample_user,
            supervisor_result=supervisor_result,
        )
        assert "supervisor:warnings" in record["key_flags"]

    def test_low_risk_no_risk_flag(self, sample_user):
        app = {"ref": "ARF-2026-0002", "risk_level": "LOW", "status": "in_review"}
        record = build_from_application_decision(
            app=app,
            decision="approve",
            decision_reason="Clean",
            user=sample_user,
        )
        assert not any(f.startswith("risk:") for f in record["key_flags"])


# ============================================================================
# Test: build_from_supervisor_verdict
# ============================================================================

class TestBuildFromSupervisorVerdict:
    def test_consistent_verdict(self, sample_app, sample_user):
        supervisor_result = {
            "verdict": "CONSISTENT",
            "supervisor_confidence": 0.92,
            "contradiction_count": 0,
            "can_approve": True,
            "recommendation": "Proceed with approval",
        }
        record = build_from_supervisor_verdict(sample_app, supervisor_result, sample_user)
        assert record["decision_type"] == "approve"
        assert record["source"] == "supervisor"
        assert record["confidence_score"] == 0.92
        assert "verdict:CONSISTENT" in record["key_flags"]
        assert record["extra"]["can_approve"] is True

    def test_inconsistent_verdict(self, sample_app, sample_user):
        supervisor_result = {
            "verdict": "INCONSISTENT",
            "supervisor_confidence": 0.35,
            "contradiction_count": 4,
            "can_approve": False,
            "recommendation": "Do not proceed",
        }
        record = build_from_supervisor_verdict(sample_app, supervisor_result, sample_user)
        assert record["decision_type"] == "reject"
        assert "verdict:INCONSISTENT" in record["key_flags"]
        assert "contradictions:4" in record["key_flags"]
        assert "approval_blocked" in record["key_flags"]

    def test_warnings_verdict_can_approve(self, sample_app, sample_user):
        supervisor_result = {
            "verdict": "CONSISTENT_WITH_WARNINGS",
            "supervisor_confidence": 0.72,
            "contradiction_count": 2,
            "can_approve": True,
            "recommendation": "Proceed with caution",
        }
        record = build_from_supervisor_verdict(sample_app, supervisor_result, sample_user)
        assert record["decision_type"] == "approve"
        assert "contradictions:2" in record["key_flags"]

    def test_warnings_verdict_cannot_approve(self, sample_app, sample_user):
        supervisor_result = {
            "verdict": "CONSISTENT_WITH_WARNINGS",
            "supervisor_confidence": 0.50,
            "contradiction_count": 3,
            "can_approve": False,
            "recommendation": "EDD required",
        }
        record = build_from_supervisor_verdict(sample_app, supervisor_result, sample_user)
        assert record["decision_type"] == "escalate_edd"
        assert "approval_blocked" in record["key_flags"]

    def test_confidence_normalized_from_percentage(self, sample_app, sample_user):
        supervisor_result = {
            "verdict": "CONSISTENT",
            "supervisor_confidence": 90,
            "contradiction_count": 0,
            "can_approve": True,
        }
        record = build_from_supervisor_verdict(sample_app, supervisor_result, sample_user)
        assert record["confidence_score"] == 0.9


# ============================================================================
# Test: Constants and enumerations
# ============================================================================

class TestConstants:
    def test_valid_decision_types_covers_all(self):
        expected = {"approve", "reject", "escalate_edd", "request_documents", "pre_approve", "request_info"}
        assert set(VALID_DECISION_TYPES) == expected

    def test_valid_sources(self):
        assert set(VALID_SOURCES) == {"manual", "supervisor", "rule_engine"}

    def test_valid_risk_levels(self):
        assert set(VALID_RISK_LEVELS) == {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}
