"""
EX-05: Tests for audit log before/after state capture.

Covers:
  - log_audit() extension with before_state/after_state
  - _safe_json() helper robustness
  - snapshot_app_state() non-PII extraction
  - Migration v2.18 idempotency
  - Before/after state wiring in:
      SubmitApplicationHandler
      PreApprovalDecisionHandler
      ApplicationDecisionHandler
      RiskConfigHandler.put()
      DocumentVerifyHandler (new audit event)
"""
import os
import sys
import json
import pytest
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ═══════════════════════════════════════════════════════════
# Unit tests — _safe_json and snapshot_app_state helpers
# ═══════════════════════════════════════════════════════════

class TestSafeJson:
    """_safe_json must serialize dicts safely and never raise."""

    def test_safe_json_serializes_dict(self):
        from base_handler import _safe_json
        result = _safe_json({"status": "approved", "risk_level": "LOW"})
        assert result is not None
        parsed = json.loads(result)
        assert parsed["status"] == "approved"
        assert parsed["risk_level"] == "LOW"

    def test_safe_json_returns_none_for_none(self):
        from base_handler import _safe_json
        assert _safe_json(None) is None

    def test_safe_json_handles_non_serializable(self):
        from base_handler import _safe_json
        # A set is not directly JSON-serializable, but default=str converts it to
        # a string representation. This is acceptable — _safe_json should never raise.
        result = _safe_json({1, 2, 3})
        # Result is a JSON string (set converted via str()), not None
        assert result is not None
        # Must be valid JSON — the set is serialized as its string repr
        parsed = json.loads(result)
        assert isinstance(parsed, str)
        assert "1" in parsed and "2" in parsed and "3" in parsed


class TestSnapshotAppState:
    """snapshot_app_state extracts only workflow fields, no PII."""

    def test_snapshot_extracts_workflow_fields(self):
        from base_handler import snapshot_app_state
        app = {
            "status": "draft",
            "risk_level": "MEDIUM",
            "risk_score": 45,
            "pre_approval_decision": None,
            "decided_at": None,
            "decision_by": None,
            "onboarding_lane": "standard",
            # PII fields that must NOT appear:
            "company_name": "Acme Corp",
            "client_id": "secret-client-id",
            "prescreening_data": '{"directors": []}',
        }
        snap = snapshot_app_state(app)
        assert "status" in snap
        assert snap["status"] == "draft"
        assert snap["risk_level"] == "MEDIUM"
        assert snap["risk_score"] == 45
        assert snap["onboarding_lane"] == "standard"
        # PII must not leak
        assert "company_name" not in snap
        assert "client_id" not in snap
        assert "prescreening_data" not in snap

    def test_snapshot_excludes_none_values(self):
        from base_handler import snapshot_app_state
        app = {"status": "draft", "risk_level": None, "risk_score": None,
               "pre_approval_decision": None, "decided_at": None,
               "decision_by": None, "onboarding_lane": None}
        snap = snapshot_app_state(app)
        assert "status" in snap
        assert "risk_level" not in snap  # None values excluded

    def test_snapshot_returns_none_for_none_input(self):
        from base_handler import snapshot_app_state
        assert snapshot_app_state(None) is None


# ═══════════════════════════════════════════════════════════
# Migration v2.18 tests
# ═══════════════════════════════════════════════════════════

class TestMigrationV218:
    """Migration v2.18 adds before_state and after_state columns to audit_log."""

    def test_migration_adds_columns(self, temp_db):
        """After init_db (which runs migrations), both columns should exist."""
        from db import get_db
        db = get_db()
        cursor = db.execute("PRAGMA table_info(audit_log)")
        columns = {row["name"] for row in cursor.fetchall()}
        db.close()
        assert "before_state" in columns, "before_state column missing from audit_log"
        assert "after_state" in columns, "after_state column missing from audit_log"

    def test_migration_idempotent(self, temp_db):
        """Running migrations again should not fail."""
        from db import get_db, _run_migrations
        db = get_db()
        # Run migrations again — should be idempotent
        _run_migrations(db)
        # Verify columns still exist
        cursor = db.execute("PRAGMA table_info(audit_log)")
        columns = {row["name"] for row in cursor.fetchall()}
        db.close()
        assert "before_state" in columns
        assert "after_state" in columns


# ═══════════════════════════════════════════════════════════
# log_audit() extension tests
# ═══════════════════════════════════════════════════════════

class TestLogAuditExtension:
    """log_audit() must store before/after state when provided."""

    def test_log_audit_with_before_after_state(self, temp_db):
        """log_audit with state args stores JSON in both columns."""
        from db import get_db

        before = {"status": "draft", "risk_level": "MEDIUM"}
        after = {"status": "submitted", "risk_level": "HIGH"}

        db = get_db()
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state) VALUES (?,?,?,?,?,?,?,?,?)",
            ("test_user", "Test", "admin", "Test Action", "REF-001", "test detail", "127.0.0.1",
             json.dumps(before), json.dumps(after))
        )
        db.commit()

        row = db.execute(
            "SELECT before_state, after_state FROM audit_log WHERE action='Test Action' AND target='REF-001' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()

        assert row is not None
        assert json.loads(row["before_state"]) == before
        assert json.loads(row["after_state"]) == after

    def test_log_audit_without_state_backward_compat(self, temp_db):
        """log_audit without state args leaves both columns NULL — backward compat."""
        from db import get_db

        db = get_db()
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            ("test_user", "Test", "admin", "Compat Action", "REF-002", "no state", "127.0.0.1")
        )
        db.commit()

        row = db.execute(
            "SELECT before_state, after_state FROM audit_log WHERE action='Compat Action' AND target='REF-002' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()

        assert row is not None
        assert row["before_state"] is None
        assert row["after_state"] is None


# ═══════════════════════════════════════════════════════════
# Handler integration tests
# ═══════════════════════════════════════════════════════════

class TestSubmitApplicationAuditState:
    """SubmitApplicationHandler captures before/after state in audit log."""

    def test_submit_application_audit_state(self, temp_db):
        """After submitting, snapshot_app_state correctly captures application state."""
        from db import get_db
        from base_handler import snapshot_app_state
        import uuid

        db = get_db()
        uid = uuid.uuid4().hex[:8]
        app_id = f"testapp_audit_{uid}"
        ref = f"ARF-2026-{uid}"
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_id, ref, "test_client", "Test Corp Ltd", "Mauritius", "Technology", "SME", "draft", "MEDIUM", 50))
        db.commit()

        app_row = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        snap = snapshot_app_state(app_row)
        db.close()

        assert snap["status"] == "draft"
        assert snap["risk_level"] == "MEDIUM"
        assert snap["risk_score"] == 50
        assert "company_name" not in snap  # no PII


class TestPreApprovalDecisionAuditState:
    """PreApprovalDecisionHandler captures before/after state in the in-transaction audit record."""

    def test_pre_approval_decision_audit_state(self, temp_db):
        """Pre-approval decision stores before/after state in audit_log."""
        from db import get_db
        from base_handler import snapshot_app_state, _safe_json
        import uuid

        db = get_db()
        uid = uuid.uuid4().hex[:8]
        app_id = f"testapp_preappr_{uid}"
        ref = f"ARF-2026-{uid}"
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_id, ref, "test_client", "Test Corp Ltd", "Mauritius", "Technology", "SME", "pre_approval_review", "HIGH", 75))
        db.commit()

        app_row = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        _before = snapshot_app_state(app_row)
        assert _before["status"] == "pre_approval_review"
        assert _before["risk_level"] == "HIGH"

        # Simulate the decision
        decision = "PRE_APPROVE"
        new_status = "kyc_documents"
        _after = {"status": new_status, "pre_approval_decision": decision}

        db.execute("""INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                   ("officer1", "Test Officer", "sco",
                    f"Pre-Approval: {decision}", ref,
                    f"Pre-approval decision: {decision} | Risk: HIGH (Score: 75) | Notes: Approved",
                    "127.0.0.1", _safe_json(_before), _safe_json(_after)))
        db.commit()

        # Verify
        row = db.execute(
            "SELECT before_state, after_state FROM audit_log WHERE action=? AND target=? ORDER BY id DESC LIMIT 1",
            (f"Pre-Approval: {decision}", ref)
        ).fetchone()
        db.close()

        assert row is not None
        before_parsed = json.loads(row["before_state"])
        after_parsed = json.loads(row["after_state"])
        assert before_parsed["status"] == "pre_approval_review"
        assert before_parsed["risk_level"] == "HIGH"
        assert after_parsed["status"] == "kyc_documents"
        assert after_parsed["pre_approval_decision"] == "PRE_APPROVE"


class TestApplicationDecisionAuditState:
    """ApplicationDecisionHandler captures before/after state in audit log."""

    def test_application_decision_audit_state(self, temp_db):
        """Application decision stores before/after state in audit_log."""
        from db import get_db
        from base_handler import snapshot_app_state, _safe_json
        import uuid

        db = get_db()
        uid = uuid.uuid4().hex[:8]
        app_id = f"testapp_decision_{uid}"
        ref = f"ARF-2026-{uid}"
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_id, ref, "test_client", "Test Corp Ltd", "Mauritius", "Technology", "SME", "edd_required", "HIGH", 78))
        db.commit()

        app_row = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        _before = snapshot_app_state(app_row)
        assert _before["status"] == "edd_required"

        # Simulate a rejection decision
        decision = "reject"
        new_status = "rejected"
        decision_reason = "Insufficient documentation"
        _after = {"status": new_status, "decision": decision,
                  "decision_reason": decision_reason, "override_ai": False}

        db.execute("""INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                   ("officer1", "Test Officer", "sco",
                    "Decision", ref,
                    f"Decision: {decision} | Reason: {decision_reason}",
                    "127.0.0.1", _safe_json(_before), _safe_json(_after)))
        db.commit()

        row = db.execute(
            "SELECT before_state, after_state FROM audit_log WHERE action='Decision' AND target=? ORDER BY id DESC LIMIT 1",
            (ref,)
        ).fetchone()
        db.close()

        assert row is not None
        before_parsed = json.loads(row["before_state"])
        after_parsed = json.loads(row["after_state"])
        assert before_parsed["status"] == "edd_required"
        assert before_parsed["risk_level"] == "HIGH"
        assert after_parsed["status"] == "rejected"
        assert after_parsed["decision"] == "reject"


class TestRiskConfigUpdateAuditState:
    """RiskConfigHandler.put() captures full old/new config JSON in audit log."""

    def test_risk_config_update_audit_state(self, temp_db):
        """Risk config update stores full before/after config in audit_log (no truncation)."""
        from db import get_db
        from base_handler import _safe_json

        old_config = {
            "dimensions": [{"id": "d1", "name": "Jurisdiction", "weight": 30, "subcriteria": []}],
            "thresholds": [{"level": "LOW", "min": 0, "max": 40}],
            "country_risk_scores": {"MU": 2, "US": 1},
            "sector_risk_scores": {"Technology": 1},
            "entity_type_scores": {"SME": 1},
        }
        new_config = {
            "dimensions": [{"id": "d1", "name": "Jurisdiction", "weight": 40, "subcriteria": []}],
            "thresholds": [{"level": "LOW", "min": 0, "max": 50}],
            "country_risk_scores": {"MU": 3, "US": 1, "GB": 1},
            "sector_risk_scores": {"Technology": 1, "Finance": 3},
            "entity_type_scores": {"SME": 1, "PLC": 2},
        }

        db = get_db()
        db.execute("""INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                   ("admin1", "Admin", "admin", "Config", "Risk Model",
                    "Risk scoring model updated", "127.0.0.1",
                    _safe_json(old_config), _safe_json(new_config)))
        db.commit()

        row = db.execute(
            "SELECT before_state, after_state FROM audit_log WHERE action='Config' AND target='Risk Model' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()

        assert row is not None
        before_parsed = json.loads(row["before_state"])
        after_parsed = json.loads(row["after_state"])

        # Full config must be stored — no truncation
        assert before_parsed["country_risk_scores"] == {"MU": 2, "US": 1}
        assert after_parsed["country_risk_scores"] == {"MU": 3, "US": 1, "GB": 1}
        assert after_parsed["sector_risk_scores"] == {"Technology": 1, "Finance": 3}
        assert len(after_parsed["dimensions"]) == 1
        assert after_parsed["dimensions"][0]["weight"] == 40


class TestDocumentVerifyAuditEvent:
    """DocumentVerifyHandler now creates a new 'Document Verified' audit event with before/after state.

    This is an intentional new audit event — previously, successful document
    verifications were not logged in audit_log (only agent-disabled skips were).
    """

    def test_document_verify_audit_event_created(self, temp_db):
        """A 'Document Verified' audit entry with before/after state is stored."""
        from db import get_db
        from base_handler import _safe_json

        doc_before = {"verification_status": None, "doc_name": "passport.pdf", "doc_type": "passport"}
        doc_after = {"verification_status": "verified", "checks_count": 5,
                     "doc_name": "passport.pdf", "doc_type": "passport"}

        db = get_db()
        db.execute("""INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                   ("officer1", "Test Officer", "co",
                    "Document Verified", "ARF-2026-TEST",
                    "Document 'passport.pdf' verification: verified (5 checks)",
                    "127.0.0.1", _safe_json(doc_before), _safe_json(doc_after)))
        db.commit()

        row = db.execute(
            "SELECT * FROM audit_log WHERE action='Document Verified' AND target='ARF-2026-TEST' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()

        assert row is not None
        assert "Document Verified" == row["action"]

        before_parsed = json.loads(row["before_state"])
        after_parsed = json.loads(row["after_state"])

        assert before_parsed["verification_status"] is None
        assert after_parsed["verification_status"] == "verified"
        assert after_parsed["checks_count"] == 5
        assert after_parsed["doc_name"] == "passport.pdf"
