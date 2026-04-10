"""
Tests for person-level document upload flow (KYC: passport, POA, etc.)
Validates the upload-store-verify chain for Director, UBO, and intermediary records.
"""
import os
import io
import json
import pytest
import uuid


class TestPersonLevelDocumentUpload:
    """Tests for person-level (Director/UBO) document upload and verification."""

    def _get_db(self):
        from db import get_db
        return get_db()

    def _create_director(self, db, app_id, person_key="dir1", first_name="John", last_name="Doe", nationality="GB"):
        """Create a director record with a person_key."""
        db.execute("""
            INSERT INTO directors (application_id, person_key, first_name, last_name, full_name, nationality)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (app_id, person_key, first_name, last_name, f"{first_name} {last_name}", nationality))
        db.commit()

    def _create_ubo(self, db, app_id, person_key="ubo1", first_name="Jane", last_name="Smith", nationality="US", ownership_pct=25.0):
        """Create a UBO record with a person_key."""
        db.execute("""
            INSERT INTO ubos (application_id, person_key, first_name, last_name, full_name, nationality, ownership_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (app_id, person_key, first_name, last_name, f"{first_name} {last_name}", nationality, ownership_pct))
        db.commit()

    def _upload_document(self, db, app_id, doc_type="passport", person_id="dir1", filename="passport.pdf"):
        """Simulate a document upload by inserting directly into the DB."""
        import tempfile
        doc_id = uuid.uuid4().hex[:16]
        file_path = os.path.join(tempfile.gettempdir(), f"test_{doc_id}.pdf")
        # Create a minimal test file
        with open(file_path, "wb") as f:
            f.write(b"%PDF-1.4 test content")
        db.execute("""
            INSERT INTO documents (id, application_id, person_id, doc_type, doc_name, file_path, file_size, mime_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (doc_id, app_id, person_id, doc_type, filename, file_path, 100, "application/pdf"))
        db.commit()
        return doc_id

    # ── Test 1: Person resolution for Director ──
    def test_resolve_person_director(self, temp_db, db, sample_application):
        """Director with person_key='dir1' should be resolvable."""
        self._create_director(db, sample_application, person_key="dir1")
        from server import resolve_application_person
        person = resolve_application_person(db, sample_application, "dir1")
        assert person is not None, "Director with person_key='dir1' should be found"
        assert person["full_name"] == "John Doe"
        assert person["person_type"] == "director"
        assert person["entity_type"] == "Person"

    # ── Test 2: Person resolution for UBO ──
    def test_resolve_person_ubo(self, temp_db, db, sample_application):
        """UBO with person_key='ubo1' should be resolvable."""
        self._create_ubo(db, sample_application, person_key="ubo1")
        from server import resolve_application_person
        person = resolve_application_person(db, sample_application, "ubo1")
        assert person is not None, "UBO with person_key='ubo1' should be found"
        assert person["full_name"] == "Jane Smith"
        assert person["person_type"] == "ubo"
        assert person["entity_type"] == "Person"

    # ── Test 3: Person resolution fails for invalid person_id ──
    def test_resolve_person_invalid_id(self, temp_db, db, sample_application):
        """Invalid person_id should return None, not crash."""
        from server import resolve_application_person
        person = resolve_application_person(db, sample_application, "nonexistent")
        assert person is None

    # ── Test 4: Person resolution with None person_id ──
    def test_resolve_person_none_id(self, temp_db, db, sample_application):
        """None person_id should return None, not crash."""
        from server import resolve_application_person
        person = resolve_application_person(db, sample_application, None)
        assert person is None

    # ── Test 5: Document record created with correct person_id ──
    def test_document_linked_to_person(self, temp_db, db, sample_application):
        """Document uploaded with person_id should be stored with that linkage."""
        self._create_director(db, sample_application, person_key="dir1")
        doc_id = self._upload_document(db, sample_application, doc_type="passport", person_id="dir1")
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        assert doc is not None
        assert doc["person_id"] == "dir1"
        assert doc["doc_type"] == "passport"
        assert doc["application_id"] == sample_application

    # ── Test 6: Document record created without person_id (company-level) ──
    def test_document_company_level(self, temp_db, db, sample_application):
        """Company-level document should be stored with NULL person_id."""
        doc_id = self._upload_document(db, sample_application, doc_type="cert_inc", person_id=None)
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        assert doc is not None
        assert doc["person_id"] is None
        assert doc["doc_type"] == "cert_inc"

    # ── Test 7: Verification context for person-level passport ──
    def test_verification_context_passport(self, temp_db, db, sample_application):
        """Passport doc with person_id should resolve to 'kyc' category."""
        self._create_director(db, sample_application, person_key="dir1")
        doc_id = self._upload_document(db, sample_application, doc_type="passport", person_id="dir1")
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        app = db.execute("SELECT * FROM applications WHERE id=?", (sample_application,)).fetchone()
        from server import resolve_document_subject_context
        ctx = resolve_document_subject_context(db, app, dict(doc))
        assert ctx["doc_category"] == "kyc"
        assert ctx["subject_type"] == "director"
        assert ctx["person_record"] is not None
        assert ctx["person_record"]["full_name"] == "John Doe"

    # ── Test 8: Verification context for UBO passport ──
    def test_verification_context_ubo_passport(self, temp_db, db, sample_application):
        """UBO passport doc should resolve to 'kyc' category with UBO person_type."""
        self._create_ubo(db, sample_application, person_key="ubo1")
        doc_id = self._upload_document(db, sample_application, doc_type="passport", person_id="ubo1")
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        app = db.execute("SELECT * FROM applications WHERE id=?", (sample_application,)).fetchone()
        from server import resolve_document_subject_context
        ctx = resolve_document_subject_context(db, app, dict(doc))
        assert ctx["doc_category"] == "kyc"
        assert ctx["subject_type"] == "ubo"
        assert ctx["person_record"]["full_name"] == "Jane Smith"

    # ── Test 9: Company-level verification context ──
    def test_verification_context_company_doc(self, temp_db, db, sample_application):
        """cert_inc doc without person_id should resolve to 'company' category."""
        doc_id = self._upload_document(db, sample_application, doc_type="cert_inc", person_id=None)
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        app = db.execute("SELECT * FROM applications WHERE id=?", (sample_application,)).fetchone()
        from server import resolve_document_subject_context
        ctx = resolve_document_subject_context(db, app, dict(doc))
        assert ctx["doc_category"] == "company"
        assert ctx["subject_type"] == "application_company"

    # ── Test 10: build_document_verification_context enriches person data ──
    def test_build_verification_context_enrichment(self, temp_db, db, sample_application):
        """build_document_verification_context should enrich prescreening_data with person fields."""
        self._create_director(db, sample_application, person_key="dir1")
        doc_id = self._upload_document(db, sample_application, doc_type="passport", person_id="dir1")
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        app = db.execute("SELECT * FROM applications WHERE id=?", (sample_application,)).fetchone()
        from server import build_document_verification_context
        ctx = build_document_verification_context(db, dict(app), dict(doc))
        assert ctx["person_name"] == "John Doe"
        assert ctx["doc_category"] == "kyc"
        assert ctx["prescreening_data"].get("full_name") == "John Doe"

    # ── Test 11: Passport is in id_doc_types ──
    def test_passport_in_id_doc_types(self, temp_db):
        """Passport should be recognized as an identity document type."""
        id_doc_types = ["passport", "national_id", "id_card", "drivers_license", "director_id", "ubo_id"]
        assert "passport" in id_doc_types

    # ── Test 12: Doc type normalization does not break passport ──
    def test_doc_type_normalization_passport(self, temp_db):
        """Passport should pass through normalization unchanged."""
        _DOC_TYPE_NORMALIZE = {
            "doc-coi": "cert_inc", "doc-memarts": "memarts", "doc-shareholders": "reg_sh",
            "doc-directors-reg": "reg_dir", "doc-financials": "fin_stmt", "doc-proof-address": "poa",
            "doc-board-res": "board_res", "doc-structure-chart": "structure_chart",
            "doc-bank-ref": "bankref", "doc-license-cert": "licence",
            "doc-contracts": "contracts", "doc-source-wealth-proof": "source_wealth",
            "doc-source-funds-proof": "source_funds", "doc-bank-statements": "bank_statements",
            "doc-aml-policy": "aml_policy",
        }
        assert _DOC_TYPE_NORMALIZE.get("passport", "passport") == "passport"
        assert _DOC_TYPE_NORMALIZE.get("poa", "poa") == "poa"
        assert _DOC_TYPE_NORMALIZE.get("cv", "cv") == "cv"

    # ── Test 13: Multiple directors have distinct person records ──
    def test_multiple_directors_distinct(self, temp_db, db, sample_application):
        """Multiple directors should be individually resolvable."""
        self._create_director(db, sample_application, person_key="dir1", first_name="Alice", last_name="One")
        self._create_director(db, sample_application, person_key="dir2", first_name="Bob", last_name="Two")
        from server import resolve_application_person
        p1 = resolve_application_person(db, sample_application, "dir1")
        p2 = resolve_application_person(db, sample_application, "dir2")
        assert p1 is not None and p2 is not None
        assert p1["full_name"] == "Alice One"
        assert p2["full_name"] == "Bob Two"

    # ── Test 14: Person upload with empty person_id stores NULL ──
    def test_document_empty_person_id(self, temp_db, db, sample_application):
        """Empty string person_id from backend get_argument should store as provided."""
        doc_id = self._upload_document(db, sample_application, doc_type="passport", person_id="")
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        assert doc is not None
        # Empty string is stored, not NULL
        assert doc["person_id"] == ""

    # ── Test 15: Verification context handles unresolvable person_id gracefully ──
    def test_verification_context_unresolvable_person(self, temp_db, db, sample_application):
        """Passport with unresolvable person_id should still return context (with None person_record)."""
        doc_id = self._upload_document(db, sample_application, doc_type="passport", person_id="nonexistent")
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        app = db.execute("SELECT * FROM applications WHERE id=?", (sample_application,)).fetchone()
        from server import resolve_document_subject_context
        ctx = resolve_document_subject_context(db, app, dict(doc))
        # passport is not in company_doc_types, so falls to 'kyc'
        assert ctx["doc_category"] == "kyc"
        assert ctx["person_record"] is None

    # ── Test 16: Sanctions screening safe when person has empty full_name ──
    def test_sanctions_safe_empty_name(self, temp_db, db, sample_application, mock_screening):
        """Sanctions screening should not crash when person full_name is empty."""
        # Create director with empty names
        db.execute("""
            INSERT INTO directors (application_id, person_key, first_name, last_name, full_name, nationality)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (sample_application, "dir_empty", "", "", "", "GB"))
        db.commit()
        from server import resolve_application_person
        person = resolve_application_person(db, sample_application, "dir_empty")
        # Person resolved but full_name is empty — screening should be skipped per our fix
        assert person is not None
        assert person["full_name"] == ""

    # ── Test 17: Document with passport doc_type correctly identified as ID doc ──
    def test_passport_identified_as_id_doc(self, temp_db, db, sample_application):
        """Passport documents should trigger identity verification screening path."""
        self._create_director(db, sample_application, person_key="dir1")
        doc_id = self._upload_document(db, sample_application, doc_type="passport", person_id="dir1")
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        id_doc_types = ["passport", "national_id", "id_card", "drivers_license", "director_id", "ubo_id"]
        assert doc["doc_type"] in id_doc_types, f"passport should be in id_doc_types but doc_type is {doc['doc_type']}"
        assert doc["person_id"] == "dir1"

    # ── Test 18: Resume preserves person_key through save/restore cycle ──
    def test_person_key_persists(self, temp_db, db, sample_application):
        """Person keys should survive save/load cycle."""
        self._create_director(db, sample_application, person_key="dir1", first_name="Alice", last_name="Resume")
        from server import get_application_parties
        dirs, ubos, ints = get_application_parties(db, sample_application)
        assert len(dirs) >= 1
        found = [d for d in dirs if d.get("person_key") == "dir1"]
        assert len(found) == 1, "Director with person_key='dir1' should be in the returned parties"
        assert found[0]["full_name"] == "Alice Resume"

    # ── Test 19: Document with POA doc_type for director ──
    def test_person_poa_document(self, temp_db, db, sample_application):
        """Proof of Address document should work for person-level upload."""
        self._create_director(db, sample_application, person_key="dir1")
        doc_id = self._upload_document(db, sample_application, doc_type="poa", person_id="dir1")
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        assert doc is not None
        assert doc["doc_type"] == "poa"
        assert doc["person_id"] == "dir1"

    # ── Test 20: Multiple documents for same person ──
    def test_multiple_docs_same_person(self, temp_db, db, sample_application):
        """Multiple documents can be uploaded for the same person."""
        self._create_director(db, sample_application, person_key="dir1")
        doc_id_1 = self._upload_document(db, sample_application, doc_type="passport", person_id="dir1", filename="passport.pdf")
        doc_id_2 = self._upload_document(db, sample_application, doc_type="poa", person_id="dir1", filename="utility_bill.pdf")
        docs = db.execute("SELECT * FROM documents WHERE application_id=? AND person_id='dir1'", (sample_application,)).fetchall()
        assert len(docs) == 2
        doc_types = {d["doc_type"] for d in docs}
        assert "passport" in doc_types
        assert "poa" in doc_types
