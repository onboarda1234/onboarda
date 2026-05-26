"""
under_review State Consistency Tests
=====================================
Validates that the 'under_review' application status is correctly supported
across the DB schema, state machine transitions, and business rules.

Root cause: 'under_review' was referenced in server.py state transitions
but was missing from all DB CHECK constraints, causing IntegrityError
when any application was transitioned to that state.
"""
import os
import sys
import json
import sqlite3
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


class TestUnderReviewDBConstraint:
    """Verify that the DB CHECK constraint allows 'under_review' status."""

    def test_under_review_allowed_in_sqlite_schema(self, db):
        """under_review must be accepted by the applications.status CHECK constraint."""
        db.execute("""
            INSERT INTO applications (id, ref, company_name, country, sector, entity_type, status)
            VALUES ('ur-test-1', 'ARF-UR-001', 'Test Corp', 'Mauritius', 'Technology', 'SME', 'under_review')
        """)
        db.commit()
        row = db.execute("SELECT status FROM applications WHERE id='ur-test-1'").fetchone()
        assert row["status"] == "under_review"

    def test_invalid_status_rejected(self, db):
        """A status not in the CHECK constraint must be rejected."""
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("""
                INSERT INTO applications (id, ref, company_name, country, sector, entity_type, status)
                VALUES ('ur-test-2', 'ARF-UR-002', 'Test Corp', 'Mauritius', 'Technology', 'SME', 'bogus_status')
            """)


class TestUnderReviewTransitions:
    """Verify that all state transitions involving under_review are correct."""

    def test_transition_map_includes_under_review_as_target(self):
        """submitted -> under_review must be a valid transition in server.py."""
        src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py"), encoding="utf-8").read()
        assert '"under_review"' in src or "'under_review'" in src

    def test_submitted_to_under_review_transition(self, db):
        """An application in 'submitted' can transition to 'under_review'."""
        db.execute("""
            INSERT INTO applications (id, ref, company_name, country, sector, entity_type, status)
            VALUES ('tr-test-1', 'ARF-TR-001', 'Test Corp', 'Mauritius', 'Technology', 'SME', 'submitted')
        """)
        db.commit()
        db.execute("UPDATE applications SET status='under_review' WHERE id='tr-test-1'")
        db.commit()
        row = db.execute("SELECT status FROM applications WHERE id='tr-test-1'").fetchone()
        assert row["status"] == "under_review"

    def test_under_review_to_edd_required(self, db):
        """under_review -> edd_required transition works at DB level."""
        db.execute("""
            INSERT INTO applications (id, ref, company_name, country, sector, entity_type, status)
            VALUES ('tr-test-2', 'ARF-TR-002', 'Test Corp', 'Mauritius', 'Technology', 'SME', 'under_review')
        """)
        db.commit()
        db.execute("UPDATE applications SET status='edd_required' WHERE id='tr-test-2'")
        db.commit()
        row = db.execute("SELECT status FROM applications WHERE id='tr-test-2'").fetchone()
        assert row["status"] == "edd_required"

    def test_under_review_to_approved(self, db):
        """under_review -> approved transition works at DB level."""
        db.execute("""
            INSERT INTO applications (id, ref, company_name, country, sector, entity_type, status)
            VALUES ('tr-test-3', 'ARF-TR-003', 'Test Corp', 'Mauritius', 'Technology', 'SME', 'under_review')
        """)
        db.commit()
        db.execute("UPDATE applications SET status='approved' WHERE id='tr-test-3'")
        db.commit()
        row = db.execute("SELECT status FROM applications WHERE id='tr-test-3'").fetchone()
        assert row["status"] == "approved"

    def test_under_review_to_rejected(self, db):
        """under_review -> rejected transition works at DB level."""
        db.execute("""
            INSERT INTO applications (id, ref, company_name, country, sector, entity_type, status)
            VALUES ('tr-test-4', 'ARF-TR-004', 'Test Corp', 'Mauritius', 'Technology', 'SME', 'under_review')
        """)
        db.commit()
        db.execute("UPDATE applications SET status='rejected' WHERE id='tr-test-4'")
        db.commit()
        row = db.execute("SELECT status FROM applications WHERE id='tr-test-4'").fetchone()
        assert row["status"] == "rejected"

    def test_edd_required_to_under_review(self, db):
        """edd_required -> under_review return transition works at DB level."""
        db.execute("""
            INSERT INTO applications (id, ref, company_name, country, sector, entity_type, status)
            VALUES ('tr-test-5', 'ARF-TR-005', 'Test Corp', 'Mauritius', 'Technology', 'SME', 'edd_required')
        """)
        db.commit()
        db.execute("UPDATE applications SET status='under_review' WHERE id='tr-test-5'")
        db.commit()
        row = db.execute("SELECT status FROM applications WHERE id='tr-test-5'").fetchone()
        assert row["status"] == "under_review"


class TestUnderReviewBusinessRules:
    """Verify business rules that reference under_review."""

    def test_under_review_in_immutable_party_states(self):
        """under_review must be in the immutable party states set."""
        import re
        src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py"), encoding="utf-8").read()
        m = re.search(r'immutable_party_states\s*=\s*\(([^)]+)\)', src)
        assert m, "immutable_party_states tuple not found in server.py"
        assert "under_review" in m.group(1)

    def test_under_review_in_non_draft_statuses(self):
        """under_review must be in the non-draft statuses guard."""
        import re
        src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py"), encoding="utf-8").read()
        m = re.search(r'non_draft_statuses\s*=\s*\(([^)]+)\)', src)
        assert m, "non_draft_statuses tuple not found in server.py"
        assert "under_review" in m.group(1)

    def test_under_review_in_review_states(self):
        """under_review must be in the review_states for H-05 risk gate."""
        import re
        src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py"), encoding="utf-8").read()
        m = re.search(r'review_states\s*=\s*\(([^)]+)\)', src)
        assert m, "review_states tuple not found in server.py"
        assert "under_review" in m.group(1)

    def test_valid_transitions_includes_under_review_source(self):
        """under_review must appear as a source state in valid_transitions."""
        src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py"), encoding="utf-8").read()
        assert '"under_review":' in src or "'under_review':" in src

    def test_resilience_valid_statuses_includes_under_review(self):
        """Resilience workflow_rules.py VALID_STATUSES must include under_review."""
        from resilience.workflow_rules import WorkflowEnforcer
        assert "under_review" in WorkflowEnforcer.VALID_STATUSES


class TestSchemaConsistency:
    """Verify DB schema and state machine are consistent."""

    def test_all_transition_targets_in_db_constraint(self, db):
        """Every status reachable via valid_transitions must be allowed by the DB."""
        all_states = [
            "draft", "submitted", "prescreening_submitted", "pricing_review",
            "pricing_accepted", "pre_approval_review", "pre_approved",
            "kyc_documents", "kyc_submitted", "compliance_review", "in_review",
            "under_review", "edd_required", "approved", "rejected",
            "rmi_sent", "withdrawn",
        ]

        for i, status in enumerate(all_states):
            app_id = f"schema-test-{i}"
            ref = f"ARF-SCH-{i:04d}"
            db.execute("""
                INSERT INTO applications (id, ref, company_name, country, sector, entity_type, status)
                VALUES (?, ?, 'Test Corp', 'Mauritius', 'Technology', 'SME', ?)
            """, (app_id, ref, status))
        db.commit()

        count = db.execute("SELECT COUNT(*) FROM applications WHERE id LIKE 'schema-test-%'").fetchone()[0]
        assert count == len(all_states)

    def test_db_constraint_rejects_unknown_status(self, db):
        """A status not in the known set must be rejected by the CHECK constraint."""
        invalid_statuses = ["pending", "active", "inactive", "complete", "foo"]
        for i, bad in enumerate(invalid_statuses):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("""
                    INSERT INTO applications (id, ref, company_name, country, sector, entity_type, status)
                    VALUES (?, ?, 'Test Corp', 'Mauritius', 'Technology', 'SME', ?)
                """, (f"bad-{i}", f"ARF-BAD-{i:04d}", bad))
            db.rollback()
