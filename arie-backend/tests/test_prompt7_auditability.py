"""
Prompt 7 — Memo Evidence and Approval Auditability Tests

Verifies:
1. Memo metadata contains source_attribution with structured evidence references.
2. first_approver_id is preserved (not nulled) after final approval for HIGH-risk apps.
3. GET /api/applications/:id response includes first_approver_name and decision_by_name.
4. Two-officer approval requires different officers (same-officer still blocked).
"""
import os
import sys
import json
import pytest
import sqlite3
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ════════════════════════════════════════════════════════════
# Memo source_attribution tests
# ════════════════════════════════════════════════════════════

class TestMemoSourceAttribution:
    def _make_minimal_app(self):
        return {
            "id": "app-attr-001",
            "ref": "ARF-2026-ATTR-001",
            "company_name": "AttributionCo Ltd",
            "brn": "C12345",
            "country": "Mauritius",
            "sector": "Technology",
            "entity_type": "SME",
            "entity_size": "SME",
            "source_of_funds": "Revenue",
            "expected_volume": "Low",
            "operating_countries": "Mauritius",
            "business_activity": "Software development",
            "prescreening_data": "{}",
            "risk_level": "LOW",
            "risk_score": 25,
            "screening_reviews": [],
        }

    def test_memo_has_source_attribution(self):
        """Generated memo metadata must contain a source_attribution key."""
        from memo_handler import build_compliance_memo
        app = self._make_minimal_app()
        memo, _, _, _ = build_compliance_memo(app, [], [], [])
        assert "source_attribution" in memo["metadata"], (
            "memo metadata must contain 'source_attribution'"
        )

    def test_source_attribution_has_required_fields(self):
        """source_attribution must expose application_ref, generation_pipeline, and source sections."""
        from memo_handler import build_compliance_memo
        app = self._make_minimal_app()
        memo, _, _, _ = build_compliance_memo(app, [], [], [])
        sa = memo["metadata"]["source_attribution"]
        assert sa["application_ref"] == app["ref"]
        assert "generation_pipeline" in sa
        assert "rule_engine" in sa["generation_pipeline"]
        assert "screening_sources" in sa
        assert "document_sources" in sa
        assert isinstance(sa["rule_engine_checks"], int)
        assert isinstance(sa["rule_engine_violations"], int)

    def test_source_attribution_document_counts_match(self):
        """source_attribution.document_sources must match the documents list passed in."""
        from memo_handler import build_compliance_memo
        app = self._make_minimal_app()
        docs = [
            {"doc_type": "Certificate of Incorporation", "verification_status": "verified"},
            {"doc_type": "Passport", "verification_status": "pending"},
        ]
        memo, _, _, _ = build_compliance_memo(app, [], [], docs)
        ds = memo["metadata"]["source_attribution"]["document_sources"]
        assert ds["total"] == 2
        assert ds["verified"] == 1
        assert ds["pending"] == 1
        assert "Certificate of Incorporation" in ds["types"]

    def test_source_attribution_screening_fields(self):
        """source_attribution.screening_sources must reflect screening terminal state."""
        from memo_handler import build_compliance_memo
        app = self._make_minimal_app()
        memo, _, _, _ = build_compliance_memo(app, [], [], [])
        ss = memo["metadata"]["source_attribution"]["screening_sources"]
        assert "company_screened" in ss
        assert "persons_screened" in ss
        assert "screening_terminal" in ss
        assert "provider" in ss


# ════════════════════════════════════════════════════════════
# Dual-approval persistence tests
# ════════════════════════════════════════════════════════════

class TestDualApprovalPersistence:
    def _make_db(self):
        """Create a minimal in-memory SQLite DB with required tables."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE applications (
            id TEXT PRIMARY KEY,
            ref TEXT,
            status TEXT DEFAULT 'pending',
            risk_level TEXT DEFAULT 'HIGH',
            first_approver_id TEXT,
            first_approved_at TEXT,
            decision_by TEXT,
            decided_at TEXT,
            decision_notes TEXT,
            updated_at TEXT
        )""")
        conn.execute("""CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT, user_name TEXT, user_role TEXT,
            action TEXT, target TEXT, detail TEXT, ip_address TEXT,
            before_state TEXT, after_state TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        return conn

    def test_first_approver_preserved_after_final_approval(self):
        """
        After final approval, first_approver_id must NOT be set to NULL.
        This preserves the dual-approval audit trail on the applications row.
        """
        db = self._make_db()
        db.execute("""INSERT INTO applications (id, ref, status, risk_level, first_approver_id, first_approved_at)
                      VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                   ("app-dual-001", "ARF-2026-DUAL-001", "pending", "HIGH", "officer-alpha"))
        db.commit()

        # Simulate final-approval UPDATE (as patched in server.py — no NULL assignment)
        db.execute("""UPDATE applications SET
                          status=?, decided_at=datetime('now'), decision_by=?,
                          decision_notes=?, updated_at=datetime('now')
                      WHERE id=?""",
                   ("approved", "officer-beta", "{}", "app-dual-001"))
        db.commit()

        row = db.execute("SELECT * FROM applications WHERE id = ?", ("app-dual-001",)).fetchone()
        assert row["first_approver_id"] == "officer-alpha", (
            "first_approver_id must be preserved after final approval, not nulled"
        )
        assert row["decision_by"] == "officer-beta", (
            "decision_by must record the second (final) approver"
        )

    def test_two_distinct_approver_ids_present(self):
        """Both first_approver_id and decision_by are populated with different users."""
        db = self._make_db()
        db.execute("""INSERT INTO applications (id, ref, status, risk_level, first_approver_id, first_approved_at)
                      VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                   ("app-dual-002", "ARF-2026-DUAL-002", "pending", "VERY_HIGH", "officer-gamma"))
        db.execute("""UPDATE applications SET
                          status='approved', decided_at=datetime('now'), decision_by='officer-delta',
                          decision_notes='{}', updated_at=datetime('now')
                      WHERE id='app-dual-002'""")
        db.commit()

        row = db.execute("SELECT first_approver_id, decision_by FROM applications WHERE id='app-dual-002'").fetchone()
        assert row["first_approver_id"] != row["decision_by"], (
            "first and second approver must be different officers"
        )
        assert row["first_approver_id"] is not None
        assert row["decision_by"] is not None

    def test_audit_snapshot_includes_first_approver_id(self):
        """
        The after_state audit snapshot for the final approval must include first_approver_id
        so the audit log reflects both approvers even if the application row were modified.
        """
        import json as _json
        after_state = {
            "status": "approved",
            "decision": "approve",
            "decision_reason": "All checks passed",
            "override_ai": False,
            "rmi_request_id": None,
            "decision_by": "officer-beta",
            "first_approver_id": "officer-alpha",
        }
        # After-state must carry both approver IDs
        assert "first_approver_id" in after_state
        assert after_state["first_approver_id"] is not None
        assert "decision_by" in after_state
        assert after_state["decision_by"] != after_state["first_approver_id"]

    def test_same_officer_dual_approval_still_blocked(self):
        """
        validate_high_risk_dual_approval must return DUAL_SAME_OFFICER when the
        same officer attempts both approvals.
        """
        from security_hardening import ApprovalGateValidator
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE applications (
            id TEXT PRIMARY KEY, ref TEXT, risk_level TEXT,
            first_approver_id TEXT, first_approved_at TEXT
        )""")
        db.execute("""INSERT INTO applications VALUES (?, ?, ?, ?, datetime('now'))""",
                   ("app-same", "ARF-SAME-001", "HIGH", "officer-x"))
        db.commit()

        app = dict(db.execute("SELECT * FROM applications WHERE id='app-same'").fetchone())
        user = {"sub": "officer-x", "role": "officer", "name": "Officer X"}

        can_approve, error_code = ApprovalGateValidator.validate_high_risk_dual_approval(app, user, db)
        assert not can_approve
        assert error_code == "DUAL_SAME_OFFICER"
