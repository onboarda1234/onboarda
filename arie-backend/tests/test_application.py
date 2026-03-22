"""
Tests for application workflow.
"""
import pytest
import json


class TestApplicationWorkflow:
    def test_create_application(self, db):
        """Application can be created with required fields."""
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("app_test_1", "ARF-2026-999", "testclient001", "Test Corp", "Mauritius", "draft"))
        db.commit()

        row = db.execute("SELECT * FROM applications WHERE id='app_test_1'").fetchone()
        assert row is not None
        assert row["status"] == "draft"
        assert row["company_name"] == "Test Corp"

    def test_application_status_transitions(self, db):
        """Application status transitions are valid."""
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status)
            VALUES (?, ?, ?, ?, ?)
        """, ("app_trans_1", "ARF-2026-TR1", "testclient001", "Trans Corp", "draft"))
        db.commit()

        # Draft -> prescreening_submitted (valid status per CHECK constraint)
        db.execute("UPDATE applications SET status='prescreening_submitted' WHERE id='app_trans_1'")
        db.commit()
        row = db.execute("SELECT status FROM applications WHERE id='app_trans_1'").fetchone()
        assert row["status"] == "prescreening_submitted"

    def test_directors_linked_to_application(self, db, sample_application):
        """Directors are properly linked to applications."""
        db.execute("""
            INSERT INTO directors (id, application_id, full_name, nationality)
            VALUES (?, ?, ?, ?)
        """, ("dir001", sample_application, "John Smith", "Mauritius"))
        db.commit()

        dirs = db.execute(
            "SELECT * FROM directors WHERE application_id=?", (sample_application,)
        ).fetchall()
        assert len(dirs) == 1
        assert dirs[0]["full_name"] == "John Smith"

    def test_ubos_linked_to_application(self, db, sample_application):
        """UBOs are properly linked to applications."""
        db.execute("""
            INSERT INTO ubos (id, application_id, full_name, nationality, ownership_pct)
            VALUES (?, ?, ?, ?, ?)
        """, ("ubo001", sample_application, "Jane Doe", "UK", 75.0))
        db.commit()

        ubos = db.execute(
            "SELECT * FROM ubos WHERE application_id=?", (sample_application,)
        ).fetchall()
        assert len(ubos) == 1
        assert ubos[0]["ownership_pct"] == 75.0

    def test_audit_trail_created(self, db):
        """Audit log entries are created properly."""
        db.execute("""
            INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("admin001", "Test Admin", "admin", "Test Action", "test_target", "Test detail"))
        db.commit()

        logs = db.execute("SELECT * FROM audit_log WHERE action='Test Action'").fetchall()
        assert len(logs) == 1
        assert logs[0]["user_name"] == "Test Admin"


class TestDocuments:
    def test_document_record_creation(self, db, sample_application):
        """Document records can be created."""
        db.execute("""
            INSERT INTO documents (id, application_id, doc_type, doc_name, file_path)
            VALUES (?, ?, ?, ?, ?)
        """, ("doc001", sample_application, "passport", "passport.pdf", "/uploads/test.pdf"))
        db.commit()

        docs = db.execute(
            "SELECT * FROM documents WHERE application_id=?", (sample_application,)
        ).fetchall()
        assert len(docs) == 1
        assert docs[0]["verification_status"] == "pending"
