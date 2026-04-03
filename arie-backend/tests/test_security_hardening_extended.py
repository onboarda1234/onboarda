"""
Tests for security_hardening.py — PIIEncryptor, PasswordPolicy, ApplicationSchema,
FileUploadValidator, TokenRevocationList, MemoValidator, tag_ai_response, health endpoints.
"""
import os
import time
import pytest
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet


# ══════════════════════════════════════════════════════════
# PII ENCRYPTION
# ══════════════════════════════════════════════════════════

class TestPIIEncryptor:
    """Test PIIEncryptor field-level encryption."""

    @pytest.fixture
    def valid_key(self):
        """Generate a valid Fernet key for testing."""
        return Fernet.generate_key().decode()

    @pytest.fixture
    def encryptor(self, valid_key):
        from security_hardening import PIIEncryptor
        return PIIEncryptor(key=valid_key)

    def test_init_with_valid_key(self, valid_key):
        from security_hardening import PIIEncryptor
        enc = PIIEncryptor(key=valid_key)
        assert enc.cipher is not None

    def test_init_with_no_key_raises(self, monkeypatch):
        monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
        from security_hardening import PIIEncryptor
        with pytest.raises(RuntimeError, match="PII_ENCRYPTION_KEY"):
            PIIEncryptor(key=None)

    def test_init_with_invalid_key_raises(self):
        from security_hardening import PIIEncryptor
        with pytest.raises((ValueError, RuntimeError)):
            PIIEncryptor(key="not-a-valid-key")

    def test_init_with_env_var(self, monkeypatch, valid_key):
        monkeypatch.setenv("PII_ENCRYPTION_KEY", valid_key)
        from security_hardening import PIIEncryptor
        enc = PIIEncryptor(key=None)
        assert enc.cipher is not None

    def test_encrypt_returns_string(self, encryptor):
        result = encryptor.encrypt("test-passport-123")
        assert isinstance(result, str)
        assert result != "test-passport-123"

    def test_encrypt_decrypt_roundtrip(self, encryptor):
        plaintext = "GB123456789"
        encrypted = encryptor.encrypt(plaintext)
        decrypted = encryptor.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_empty_string(self, encryptor):
        result = encryptor.encrypt("")
        assert result == ""

    def test_decrypt_empty_string(self, encryptor):
        result = encryptor.decrypt("")
        assert result == ""

    def test_encrypt_unicode(self, encryptor):
        plaintext = "日本語パスポート"
        encrypted = encryptor.encrypt(plaintext)
        decrypted = encryptor.decrypt(encrypted)
        assert decrypted == plaintext

    def test_decrypt_with_wrong_key_fails(self, valid_key):
        from security_hardening import PIIEncryptor
        from cryptography.fernet import InvalidToken
        enc1 = PIIEncryptor(key=valid_key)
        enc2 = PIIEncryptor(key=Fernet.generate_key().decode())
        encrypted = enc1.encrypt("secret-data")
        with pytest.raises(InvalidToken):
            enc2.decrypt(encrypted)

    def test_encrypt_dict_fields(self, encryptor):
        data = {
            "name": "John Doe",
            "passport_number": "GB123456",
            "nationality": "British",
            "email": "john@example.com"
        }
        fields = ["passport_number", "nationality"]
        result = encryptor.encrypt_dict_fields(data, fields)
        # Encrypted fields should be different
        assert result["passport_number"] != "GB123456"
        assert result["nationality"] != "British"
        # Non-encrypted fields should remain
        assert result["name"] == "John Doe"
        assert result["email"] == "john@example.com"

    def test_decrypt_dict_fields(self, encryptor):
        data = {
            "name": "John Doe",
            "passport_number": "GB123456",
            "nationality": "British",
        }
        fields = ["passport_number", "nationality"]
        encrypted = encryptor.encrypt_dict_fields(data, fields)
        decrypted = encryptor.decrypt_dict_fields(encrypted, fields)
        assert decrypted["passport_number"] == "GB123456"
        assert decrypted["nationality"] == "British"
        assert decrypted["name"] == "John Doe"

    def test_encrypt_dict_skips_missing_fields(self, encryptor):
        data = {"name": "John", "email": "john@test.com"}
        result = encryptor.encrypt_dict_fields(data, ["passport_number"])
        assert "passport_number" not in result
        assert result["name"] == "John"

    def test_encrypt_dict_skips_none_fields(self, encryptor):
        data = {"passport_number": None, "name": "John"}
        result = encryptor.encrypt_dict_fields(data, ["passport_number"])
        assert result["passport_number"] is None

    def test_pii_fields_lists_defined(self):
        from security_hardening import PIIEncryptor
        assert len(PIIEncryptor.PII_FIELDS_DIRECTORS) > 0
        assert "passport_number" in PIIEncryptor.PII_FIELDS_DIRECTORS
        assert len(PIIEncryptor.PII_FIELDS_UBOS) > 0
        assert "passport_number" in PIIEncryptor.PII_FIELDS_UBOS


# ══════════════════════════════════════════════════════════
# PASSWORD POLICY
# ══════════════════════════════════════════════════════════

class TestPasswordPolicy:
    """Test PasswordPolicy validation and generation."""

    def test_valid_password(self):
        from security_hardening import PasswordPolicy
        valid, msg = PasswordPolicy.validate("StrongPass123!")
        assert valid is True
        assert msg == ""

    def test_empty_password(self):
        from security_hardening import PasswordPolicy
        valid, msg = PasswordPolicy.validate("")
        assert valid is False
        assert "required" in msg.lower()

    def test_too_short(self):
        from security_hardening import PasswordPolicy
        valid, msg = PasswordPolicy.validate("Sh0rt!")
        assert valid is False
        assert "12" in msg

    def test_no_uppercase(self):
        from security_hardening import PasswordPolicy
        valid, msg = PasswordPolicy.validate("nouppercase123!")
        assert valid is False
        assert "uppercase" in msg.lower()

    def test_no_lowercase(self):
        from security_hardening import PasswordPolicy
        valid, msg = PasswordPolicy.validate("NOLOWERCASE123!")
        assert valid is False
        assert "lowercase" in msg.lower()

    def test_no_digit(self):
        from security_hardening import PasswordPolicy
        valid, msg = PasswordPolicy.validate("NoDigitHere!!!")
        assert valid is False
        assert "digit" in msg.lower()

    def test_no_special_char(self):
        from security_hardening import PasswordPolicy
        valid, msg = PasswordPolicy.validate("NoSpecialChar123")
        assert valid is False
        assert "special" in msg.lower()

    def test_exactly_min_length(self):
        from security_hardening import PasswordPolicy
        # Exactly 12 chars with all requirements
        valid, msg = PasswordPolicy.validate("Abcdefgh12!!")
        assert valid is True

    def test_generate_temporary_meets_policy(self):
        from security_hardening import PasswordPolicy
        for _ in range(10):  # Test multiple generations for randomness
            temp = PasswordPolicy.generate_temporary()
            valid, msg = PasswordPolicy.validate(temp)
            assert valid is True, f"Generated password '{temp}' failed: {msg}"

    def test_generate_temporary_length(self):
        from security_hardening import PasswordPolicy
        temp = PasswordPolicy.generate_temporary()
        assert len(temp) >= 12

    def test_generate_temporary_unique(self):
        from security_hardening import PasswordPolicy
        passwords = set()
        for _ in range(20):
            passwords.add(PasswordPolicy.generate_temporary())
        # All should be unique (extremely unlikely to collide)
        assert len(passwords) == 20


# ══════════════════════════════════════════════════════════
# APPLICATION SCHEMA VALIDATION
# ══════════════════════════════════════════════════════════

class TestApplicationSchema:
    """Test ApplicationSchema request validation."""

    @pytest.fixture
    def valid_app_data(self):
        return {
            "entity_type": "company",
            "company_name": "Test Corp Ltd",
            "sector": "technology",
            "directors": [{
                "first_name": "John",
                "last_name": "Doe",
                "date_of_birth": "1990-01-01",
            }],
            "ubos": [{
                "name": "John Doe",
                "ownership_pct": 80.0,
            }],
        }

    def test_valid_application(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is True
        assert msg == ""

    def test_invalid_entity_type(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid_app_data["entity_type"] = "invalid_type"
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is False
        assert "entity_type" in msg.lower()

    def test_missing_company_name(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid_app_data["company_name"] = ""
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is False
        assert "company_name" in msg.lower()

    def test_company_name_too_long(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid_app_data["company_name"] = "A" * 300
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is False
        assert "max length" in msg.lower()

    def test_invalid_sector(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid_app_data["sector"] = "invalid_sector"
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is False

    def test_empty_sector_allowed(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid_app_data["sector"] = ""
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is True

    def test_directors_not_list(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid_app_data["directors"] = "not a list"
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is False
        assert "list" in msg.lower()

    def test_too_many_directors(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid_app_data["directors"] = [
            {"first_name": f"Dir{i}", "last_name": "Test", "date_of_birth": "1990-01-01"}
            for i in range(51)
        ]
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is False
        assert "too many" in msg.lower()

    def test_too_many_ubos(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid_app_data["ubos"] = [
            {"name": f"UBO{i}", "ownership_pct": 1.0}
            for i in range(51)
        ]
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is False

    def test_invalid_beneficial_owner(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid_app_data["beneficial_owner"] = "not a number"
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is False

    def test_negative_annual_revenue(self, valid_app_data):
        from security_hardening import ApplicationSchema
        valid_app_data["annual_revenue"] = -100
        valid, msg = ApplicationSchema.validate_application(valid_app_data)
        assert valid is False

    def test_non_dict_body(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_application("not a dict")
        assert valid is False
        assert "JSON object" in msg

    def test_all_valid_entity_types(self):
        from security_hardening import ApplicationSchema
        for et in ApplicationSchema.VALID_ENTITY_TYPES:
            data = {
                "entity_type": et,
                "company_name": "Test Corp",
                "directors": [],
                "ubos": [],
            }
            valid, msg = ApplicationSchema.validate_application(data)
            assert valid is True, f"Entity type '{et}' should be valid: {msg}"


class TestDirectorValidation:
    """Test ApplicationSchema.validate_director()."""

    def test_valid_director(self):
        from security_hardening import ApplicationSchema
        director = {
            "first_name": "John",
            "last_name": "Doe",
            "date_of_birth": "1990-01-01",
        }
        valid, msg = ApplicationSchema.validate_director(director)
        assert valid is True

    def test_missing_first_name(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_director({"last_name": "Doe", "date_of_birth": "1990-01-01"})
        assert valid is False
        assert "first_name" in msg

    def test_missing_last_name(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_director({"first_name": "John", "date_of_birth": "1990-01-01"})
        assert valid is False
        assert "last_name" in msg

    def test_missing_date_of_birth(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_director({"first_name": "John", "last_name": "Doe"})
        assert valid is False
        assert "date_of_birth" in msg

    def test_passport_number_too_long(self):
        from security_hardening import ApplicationSchema
        director = {
            "first_name": "John", "last_name": "Doe", "date_of_birth": "1990-01-01",
            "passport_number": "A" * 51,
        }
        valid, msg = ApplicationSchema.validate_director(director)
        assert valid is False

    def test_nationality_too_long(self):
        from security_hardening import ApplicationSchema
        director = {
            "first_name": "John", "last_name": "Doe", "date_of_birth": "1990-01-01",
            "nationality": "A" * 101,
        }
        valid, msg = ApplicationSchema.validate_director(director)
        assert valid is False

    def test_non_dict_raises(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_director("not a dict")
        assert valid is False


class TestUBOValidation:
    """Test ApplicationSchema.validate_ubo()."""

    def test_valid_ubo(self):
        from security_hardening import ApplicationSchema
        ubo = {"name": "John Doe", "ownership_pct": 50.0}
        valid, msg = ApplicationSchema.validate_ubo(ubo)
        assert valid is True

    def test_missing_name(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_ubo({"ownership_pct": 50.0})
        assert valid is False
        assert "name" in msg

    def test_missing_ownership(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_ubo({"name": "John"})
        assert valid is False
        assert "ownership_pct" in msg

    def test_ownership_too_high(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_ubo({"name": "John", "ownership_pct": 101.0})
        assert valid is False

    def test_ownership_negative(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_ubo({"name": "John", "ownership_pct": -1.0})
        assert valid is False

    def test_ownership_zero_allowed(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_ubo({"name": "John", "ownership_pct": 0.0})
        assert valid is True

    def test_ownership_hundred_allowed(self):
        from security_hardening import ApplicationSchema
        valid, msg = ApplicationSchema.validate_ubo({"name": "John", "ownership_pct": 100.0})
        assert valid is True


# ══════════════════════════════════════════════════════════
# FILE UPLOAD VALIDATOR
# ══════════════════════════════════════════════════════════

class TestFileUploadValidator:
    """Test FileUploadValidator MIME/magic byte validation."""

    def test_valid_pdf(self):
        from security_hardening import FileUploadValidator
        valid, msg = FileUploadValidator.validate(
            "document.pdf", "application/pdf", b"%PDF-1.4 test content"
        )
        assert valid is True
        assert msg == ""

    def test_valid_png(self):
        from security_hardening import FileUploadValidator
        valid, msg = FileUploadValidator.validate(
            "image.png", "image/png", b"\x89PNG\r\n\x1a\n test content"
        )
        assert valid is True

    def test_valid_jpeg(self):
        from security_hardening import FileUploadValidator
        valid, msg = FileUploadValidator.validate(
            "photo.jpg", "image/jpeg", b"\xff\xd8\xff test content"
        )
        assert valid is True

    def test_disallowed_extension(self):
        from security_hardening import FileUploadValidator
        valid, msg = FileUploadValidator.validate(
            "script.exe", "application/pdf", b"%PDF test"
        )
        assert valid is False
        assert "not allowed" in msg.lower()

    def test_disallowed_mime_type(self):
        from security_hardening import FileUploadValidator
        valid, msg = FileUploadValidator.validate(
            "document.pdf", "application/x-executable", b"%PDF test"
        )
        assert valid is False
        assert "not allowed" in msg.lower()

    def test_file_too_large(self):
        from security_hardening import FileUploadValidator
        # 26MB (over 25MB limit)
        large_data = b"%PDF" + b"x" * (26 * 1024 * 1024)
        valid, msg = FileUploadValidator.validate(
            "big.pdf", "application/pdf", large_data
        )
        assert valid is False
        assert "exceeds" in msg.lower()

    def test_magic_bytes_mismatch(self):
        from security_hardening import FileUploadValidator
        # PDF extension/mime but PNG magic bytes
        valid, msg = FileUploadValidator.validate(
            "document.pdf", "application/pdf", b"\x89PNG fake content"
        )
        assert valid is False

    def test_no_magic_bytes_match(self):
        from security_hardening import FileUploadValidator
        valid, msg = FileUploadValidator.validate(
            "document.pdf", "application/pdf", b"random data no magic"
        )
        assert valid is False
        assert "magic bytes" in msg.lower()

    def test_jpeg_jpg_variants_accepted(self):
        from security_hardening import FileUploadValidator
        # image/jpg with .jpeg extension
        valid, msg = FileUploadValidator.validate(
            "photo.jpeg", "image/jpeg", b"\xff\xd8\xff test"
        )
        assert valid is True

    def test_docx_accepted(self):
        from security_hardening import FileUploadValidator
        # DOCX files use PK ZIP magic bytes
        valid, msg = FileUploadValidator.validate(
            "document.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK\x03\x04 test content"
        )
        assert valid is True

    def test_xlsx_with_zip_magic(self):
        from security_hardening import FileUploadValidator
        # XLSX shares PK ZIP magic bytes
        valid, msg = FileUploadValidator.validate(
            "data.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK\x03\x04 test content"
        )
        assert valid is True

    def test_content_type_with_charset(self):
        from security_hardening import FileUploadValidator
        # Content-Type may include charset
        valid, msg = FileUploadValidator.validate(
            "document.pdf", "application/pdf; charset=utf-8", b"%PDF-1.4 test"
        )
        assert valid is True

    def test_allowed_extensions_set(self):
        from security_hardening import FileUploadValidator
        assert ".pdf" in FileUploadValidator.ALLOWED_EXTENSIONS
        assert ".jpg" in FileUploadValidator.ALLOWED_EXTENSIONS
        assert ".jpeg" in FileUploadValidator.ALLOWED_EXTENSIONS
        assert ".png" in FileUploadValidator.ALLOWED_EXTENSIONS
        assert ".docx" in FileUploadValidator.ALLOWED_EXTENSIONS
        assert ".xlsx" in FileUploadValidator.ALLOWED_EXTENSIONS

    def test_max_file_size_is_25mb(self):
        from security_hardening import FileUploadValidator
        assert FileUploadValidator.MAX_FILE_SIZE == 25 * 1024 * 1024


# ══════════════════════════════════════════════════════════
# TOKEN REVOCATION LIST
# ══════════════════════════════════════════════════════════

class TestTokenRevocationList:
    """Test TokenRevocationList in-memory functionality."""

    def test_new_token_not_revoked(self):
        from security_hardening import TokenRevocationList
        trl = TokenRevocationList()
        trl._db_loaded = True  # Skip DB loading
        assert trl.is_revoked("some-jti") is False

    def test_revoke_marks_token(self):
        from security_hardening import TokenRevocationList
        trl = TokenRevocationList()
        trl._db_loaded = True
        future_time = time.time() + 3600
        trl._revoked = {}  # Reset
        # Mock DB persist to avoid DB dependency
        trl._db_persist = lambda jti, exp: None
        trl.revoke("test-jti-123", future_time)
        assert trl.is_revoked("test-jti-123") is True

    def test_expired_revocation_removed(self):
        from security_hardening import TokenRevocationList
        trl = TokenRevocationList()
        trl._db_loaded = True
        trl._revoked = {}
        past_time = time.time() - 10  # Already expired
        trl._revoked["old-jti"] = past_time
        assert trl.is_revoked("old-jti") is False
        assert "old-jti" not in trl._revoked

    def test_cleanup_removes_expired(self):
        from security_hardening import TokenRevocationList
        trl = TokenRevocationList()
        trl._db_loaded = True
        trl._db_remove_expired = lambda: None  # Mock DB
        past = time.time() - 10
        future = time.time() + 3600
        trl._revoked = {"expired-jti": past, "active-jti": future}
        trl.cleanup()
        assert "expired-jti" not in trl._revoked
        assert "active-jti" in trl._revoked

    def test_stats(self):
        from security_hardening import TokenRevocationList
        trl = TokenRevocationList()
        trl._db_loaded = True
        trl._revoked = {"jti1": time.time() + 3600, "jti2": time.time() + 3600}
        stats = trl.stats()
        assert stats["revoked_count"] == 2
        assert "last_cleanup" in stats


# ══════════════════════════════════════════════════════════
# MEMO VALIDATOR
# ══════════════════════════════════════════════════════════

class TestMemoValidator:
    """Test MemoValidator cross-check logic."""

    def test_valid_memo_no_discrepancies(self):
        from security_hardening import MemoValidator
        memo = {
            "memo_text": "Risk assessment complete. Standard monitoring applied.",
            "risk_score": 45,
            "approval_recommendation": "approve",
        }
        agent_results = {
            "screening_hits": [],
            "risk_score": 45,
            "flagged_documents": [],
            "risk_level": "medium",
        }
        valid, issues = MemoValidator.validate_memo_against_results(memo, agent_results)
        assert valid is True
        assert issues == []

    def test_screening_hits_discrepancy(self):
        from security_hardening import MemoValidator
        memo = {
            "memo_text": "No screening hits found in sanctions databases.",
            "risk_score": 30,
        }
        agent_results = {
            "screening_hits": [{"name": "Match1"}],
            "risk_score": 30,
            "flagged_documents": [],
        }
        valid, issues = MemoValidator.validate_memo_against_results(memo, agent_results)
        assert valid is False
        assert any("screening hits" in d.lower() for d in issues)

    def test_risk_score_mismatch(self):
        from security_hardening import MemoValidator
        memo = {
            "memo_text": "Standard assessment.",
            "risk_score": 80,
        }
        agent_results = {
            "screening_hits": [],
            "risk_score": 30,
            "flagged_documents": [],
        }
        valid, issues = MemoValidator.validate_memo_against_results(memo, agent_results)
        assert valid is False
        assert any("risk score" in d.lower() for d in issues)

    def test_risk_score_within_tolerance(self):
        from security_hardening import MemoValidator
        memo = {"memo_text": "Assessment.", "risk_score": 47}
        agent_results = {"screening_hits": [], "risk_score": 45, "flagged_documents": []}
        valid, issues = MemoValidator.validate_memo_against_results(memo, agent_results)
        assert valid is True

    def test_document_verification_discrepancy(self):
        from security_hardening import MemoValidator
        memo = {
            "memo_text": "All documents verified and consistent.",
            "risk_score": 40,
        }
        agent_results = {
            "screening_hits": [],
            "risk_score": 40,
            "flagged_documents": [{"doc": "passport", "issue": "expired"}],
        }
        valid, issues = MemoValidator.validate_memo_against_results(memo, agent_results)
        assert valid is False
        assert any("document" in d.lower() for d in issues)

    def test_high_risk_approval_without_override(self):
        from security_hardening import MemoValidator
        memo = {
            "memo_text": "Recommend approval based on analysis.",
            "risk_score": 85,
            "approval_recommendation": "approve",
        }
        agent_results = {
            "screening_hits": [],
            "risk_score": 85,
            "flagged_documents": [],
            "risk_level": "high",
        }
        valid, issues = MemoValidator.validate_memo_against_results(memo, agent_results)
        assert valid is False
        assert any("override" in d.lower() for d in issues)

    def test_high_risk_approval_with_override_mentioned(self):
        from security_hardening import MemoValidator
        memo = {
            "memo_text": "Override justified due to exceptional circumstances.",
            "risk_score": 85,
            "approval_recommendation": "approve",
        }
        agent_results = {
            "screening_hits": [],
            "risk_score": 85,
            "flagged_documents": [],
            "risk_level": "high",
        }
        valid, issues = MemoValidator.validate_memo_against_results(memo, agent_results)
        assert valid is True


# ══════════════════════════════════════════════════════════
# AI SOURCE TRACKING
# ══════════════════════════════════════════════════════════

class TestTagAiResponse:
    """Test tag_ai_response() and is_mock_ai_response()."""

    def test_tag_valid_source(self):
        from security_hardening import tag_ai_response
        response = {"analysis": "test", "score": 75}
        tagged = tag_ai_response(response, "claude-sonnet-4-6")
        assert tagged["ai_source"] == "claude-sonnet-4-6"
        assert tagged["analysis"] == "test"

    def test_tag_opus_source(self):
        from security_hardening import tag_ai_response
        tagged = tag_ai_response({}, "claude-opus-4-6")
        assert tagged["ai_source"] == "claude-opus-4-6"

    def test_tag_mock_source(self):
        from security_hardening import tag_ai_response
        tagged = tag_ai_response({}, "mock")
        assert tagged["ai_source"] == "mock"

    def test_tag_invalid_source_raises(self):
        from security_hardening import tag_ai_response
        with pytest.raises(ValueError, match="Invalid AI source"):
            tag_ai_response({}, "gpt-4")

    def test_tag_preserves_original(self):
        from security_hardening import tag_ai_response
        original = {"key": "value"}
        tagged = tag_ai_response(original, "claude-sonnet-4-6")
        # Should not mutate original
        assert "ai_source" not in original
        assert tagged["key"] == "value"

    def test_is_mock_true(self):
        from security_hardening import is_mock_ai_response
        assert is_mock_ai_response({"ai_source": "mock"}) is True

    def test_is_mock_false(self):
        from security_hardening import is_mock_ai_response
        assert is_mock_ai_response({"ai_source": "claude-sonnet-4-6"}) is False

    def test_is_mock_missing_field(self):
        from security_hardening import is_mock_ai_response
        assert is_mock_ai_response({}) is False

    def test_is_mock_case_insensitive(self):
        from security_hardening import is_mock_ai_response
        assert is_mock_ai_response({"ai_source": "MOCK"}) is True
        assert is_mock_ai_response({"ai_source": "Mock"}) is True


# ══════════════════════════════════════════════════════════
# HEALTH ENDPOINTS
# ══════════════════════════════════════════════════════════

class TestHealthEndpoints:
    """Test get_safe_health_response() and get_detailed_health_response()."""

    def test_safe_health_response(self):
        from security_hardening import get_safe_health_response
        response = get_safe_health_response()
        assert response["status"] == "ok"
        assert "service" in response
        assert "version" in response
        assert "timestamp" in response

    def test_safe_health_no_sensitive_data(self):
        from security_hardening import get_safe_health_response
        response = get_safe_health_response()
        # Should NOT contain env vars, keys, or config
        assert "configuration" not in response
        assert "database" not in response

    def test_detailed_health_includes_database(self):
        from security_hardening import get_detailed_health_response
        response = get_detailed_health_response()
        assert "database" in response
        assert "status" in response["database"]

    def test_detailed_health_with_config(self):
        from security_hardening import get_detailed_health_response
        response = get_detailed_health_response(include_config=True)
        assert "configuration" in response
        assert "environment" in response["configuration"]

    def test_detailed_health_without_config(self):
        from security_hardening import get_detailed_health_response
        response = get_detailed_health_response(include_config=False)
        assert "configuration" not in response
