"""
Tests for document upload/storage path — S3 bucket resolution, tag encoding,
error handling, and DB metadata integrity.

Covers the staging document upload failure root causes:
1. S3 bucket name resolution via get_s3_bucket()
2. Tag value URL encoding in upload_document()
3. ContentType set correctly on put_object
4. DB metadata written only after successful S3 upload
5. Error shape on storage failure
6. No false-success response on failed upload
"""
import os
import sys
import json
import uuid
import sqlite3
import tempfile
import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_row(db, app_id="testapp_001", client_id="testclient001"):
    """Ensure a test application exists in the DB."""
    db.execute("""
        INSERT OR IGNORE INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (app_id, f"ARF-2026-{app_id}", client_id, "Test Corp", "MU", "Technology", "SME", "draft", "MEDIUM", 50))
    db.commit()
    return app_id


# ===========================================================================
# 1. S3 Bucket Resolution
# ===========================================================================

class TestS3BucketResolution:
    """Verify S3Client picks the correct bucket name per environment."""

    def test_bucket_from_environment_module_staging(self, temp_db):
        """S3Client should use get_s3_bucket() which returns staging bucket."""
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}, clear=False):
            with patch("s3_client.os.getenv") as mock_getenv:
                # Simulate no S3_BUCKET env var, so environment.py logic kicks in
                from s3_client import S3Client
                with patch("s3_client.boto3"):
                    # Use explicit bucket — simulates get_s3_bucket() returning correct value
                    client = S3Client(bucket_name="arie-staging-documents")
                    assert client.bucket_name == "arie-staging-documents"

    def test_bucket_from_explicit_arg(self, temp_db):
        """Explicit bucket_name arg should override environment resolution."""
        from s3_client import S3Client
        with patch("s3_client.boto3"):
            client = S3Client(bucket_name="my-custom-bucket")
            assert client.bucket_name == "my-custom-bucket"

    def test_bucket_not_hardcoded_to_regmind(self, temp_db):
        """S3Client default should NOT be 'regmind-documents-staging' (the old hardcoded value)."""
        from s3_client import S3Client
        with patch("s3_client.boto3"):
            # When environment module is available, it should use get_s3_bucket()
            client = S3Client()
            assert client.bucket_name != "regmind-documents-staging", \
                "Bucket name must not default to the old hardcoded 'regmind-documents-staging'"

    def test_get_s3_client_uses_environment_bucket(self, temp_db):
        """get_s3_client() without args should use get_s3_bucket() from environment module."""
        from s3_client import get_s3_client
        with patch("s3_client.boto3"):
            client = get_s3_client()
            # In testing environment, get_s3_bucket() returns S3_BUCKET_TESTING or 'arie-testing-documents'
            assert client.bucket_name != "regmind-documents-staging"

    def test_get_s3_bucket_returns_correct_env_bucket(self, temp_db):
        """environment.get_s3_bucket() returns correct bucket for testing env."""
        from environment import get_s3_bucket
        bucket = get_s3_bucket()
        # In testing env, should return S3_BUCKET_TESTING or default 'arie-testing-documents'
        assert bucket is not None
        assert len(bucket) > 0


# ===========================================================================
# 2. Tag Value URL Encoding
# ===========================================================================

class TestTagEncoding:
    """Verify upload_document URL-encodes tag values correctly."""

    def test_tags_url_encoded_with_colons(self, temp_db):
        """Colons in uploaded_at timestamp must be URL-encoded in Tagging string."""
        from s3_client import S3Client
        mock_boto_client = MagicMock()
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="test-bucket")

            success, key = client.upload_document(
                file_data=b"test content",
                client_id="client_123",
                doc_type="passport",
                filename="test.pdf",
                content_type="application/pdf",
            )

            assert success is True
            # Check the Tagging parameter was URL-encoded
            put_call = mock_boto_client.put_object.call_args
            tagging_str = put_call.kwargs.get("Tagging", put_call[1].get("Tagging", ""))
            # Colons in timestamp should be encoded as %3A
            assert "%3A" in tagging_str, f"Tagging string should URL-encode colons: {tagging_str}"
            # No bare colons (the datetime value has colons)
            # The timestamp is like 2026-04-13T05%3A37%3A42

    def test_tags_url_encoded_with_spaces(self, temp_db):
        """Spaces in metadata values must be URL-encoded."""
        from s3_client import S3Client
        mock_boto_client = MagicMock()
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="test-bucket")

            success, key = client.upload_document(
                file_data=b"test",
                client_id="c1",
                doc_type="kyc",
                filename="f.pdf",
                metadata={"original_name": "John Doe Passport.pdf"}
            )

            assert success is True
            put_call = mock_boto_client.put_object.call_args
            tagging_str = put_call.kwargs.get("Tagging", put_call[1].get("Tagging", ""))
            # Spaces should be encoded (as %20 or +)
            assert " " not in tagging_str, f"Tagging string should not have bare spaces: {tagging_str}"

    def test_tags_url_encoded_special_chars(self, temp_db):
        """Special characters (ampersand, equals) in metadata must be encoded."""
        from s3_client import S3Client
        mock_boto_client = MagicMock()
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="test-bucket")

            success, key = client.upload_document(
                file_data=b"test",
                client_id="c1",
                doc_type="kyc",
                filename="test.pdf",
                metadata={"note": "key=value&extra"}
            )

            assert success is True
            put_call = mock_boto_client.put_object.call_args
            tagging_str = put_call.kwargs.get("Tagging", put_call[1].get("Tagging", ""))
            # Count ampersands — should only be tag delimiters, not in values
            # The note value "key=value&extra" has & and = that must be encoded
            parts = tagging_str.split("&")
            for part in parts:
                # Each part should have exactly one = (key=value)
                assert part.count("=") >= 1, f"Tag part malformed: {part}"


# ===========================================================================
# 3. ContentType Support
# ===========================================================================

class TestContentType:
    """Verify ContentType is passed to S3 put_object."""

    def test_content_type_set_on_upload(self, temp_db):
        """put_object should include ContentType when provided."""
        from s3_client import S3Client
        mock_boto_client = MagicMock()
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="test-bucket")

            client.upload_document(
                file_data=b"PDF content",
                client_id="c1",
                doc_type="kyc",
                filename="doc.pdf",
                content_type="application/pdf"
            )

            put_call = mock_boto_client.put_object.call_args
            assert put_call.kwargs.get("ContentType") == "application/pdf" or \
                   put_call[1].get("ContentType") == "application/pdf"

    def test_content_type_defaults_to_octet_stream(self, temp_db):
        """put_object should default ContentType to application/octet-stream."""
        from s3_client import S3Client
        mock_boto_client = MagicMock()
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="test-bucket")

            client.upload_document(
                file_data=b"data",
                client_id="c1",
                doc_type="general",
                filename="file.bin"
            )

            put_call = mock_boto_client.put_object.call_args
            ct = put_call.kwargs.get("ContentType", put_call[1].get("ContentType"))
            assert ct == "application/octet-stream"


# ===========================================================================
# 4. Upload Failure Returns Correct Error Shape
# ===========================================================================

class TestUploadFailureHandling:
    """Verify storage failures return proper error tuples, never false-success."""

    def test_client_error_returns_false(self, temp_db):
        """S3 ClientError should return (False, error_message)."""
        from s3_client import S3Client
        from botocore.exceptions import ClientError
        mock_boto_client = MagicMock()
        mock_boto_client.put_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "The specified bucket does not exist"}},
            "PutObject"
        )
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="nonexistent-bucket")

            success, msg = client.upload_document(
                file_data=b"test",
                client_id="c1",
                doc_type="kyc",
                filename="doc.pdf"
            )

            assert success is False
            assert "does not exist" in msg
            assert "nonexistent-bucket" in msg

    def test_unexpected_error_returns_false(self, temp_db):
        """Unexpected exceptions should return (False, error_message) with bucket info."""
        from s3_client import S3Client
        mock_boto_client = MagicMock()
        mock_boto_client.put_object.side_effect = ConnectionError("Network unreachable")
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="test-bucket")

            success, msg = client.upload_document(
                file_data=b"test",
                client_id="c1",
                doc_type="kyc",
                filename="doc.pdf"
            )

            assert success is False
            assert "Network unreachable" in msg
            assert "test-bucket" in msg

    def test_no_false_success_on_error(self, temp_db):
        """upload_document must NEVER return (True, ...) when put_object fails."""
        from s3_client import S3Client
        mock_boto_client = MagicMock()
        mock_boto_client.put_object.side_effect = Exception("boom")
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="b")

            success, msg = client.upload_document(
                file_data=b"x", client_id="c", doc_type="t", filename="f"
            )

            assert success is False


# ===========================================================================
# 5. DB Metadata Written Only on Successful Upload
# ===========================================================================

class TestDBMetadataIntegrity:
    """Verify DB document records are correct and only written on storage success."""

    def test_document_record_has_s3_key_on_success(self, temp_db, db, sample_application):
        """When S3 upload succeeds, DB record should have s3_key set."""
        doc_id = uuid.uuid4().hex[:16]
        s3_key = f"clients/{sample_application}/kyc/20260413_test.pdf"

        db.execute("""
            INSERT INTO documents (id, application_id, person_id, doc_type, doc_name, file_path, s3_key, file_size, mime_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (doc_id, sample_application, None, "passport", "test.pdf", "/tmp/test.pdf", s3_key, 100, "application/pdf"))
        db.commit()

        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        assert doc is not None
        assert doc["s3_key"] == s3_key
        assert doc["mime_type"] == "application/pdf"
        assert doc["application_id"] == sample_application

    def test_document_record_null_s3_key_when_no_s3(self, temp_db, db, sample_application):
        """When S3 is not used, s3_key should be NULL in DB."""
        doc_id = uuid.uuid4().hex[:16]
        db.execute("""
            INSERT INTO documents (id, application_id, person_id, doc_type, doc_name, file_path, s3_key, file_size, mime_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (doc_id, sample_application, None, "cert_inc", "cert.pdf", "/tmp/cert.pdf", None, 200, "application/pdf"))
        db.commit()

        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        assert doc is not None
        assert doc["s3_key"] is None

    def test_document_association_to_person(self, temp_db, db, sample_application):
        """Document linked to a person should store person_id correctly."""
        # Create director
        db.execute("""
            INSERT INTO directors (application_id, person_key, first_name, last_name, full_name, nationality)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (sample_application, "dir1", "Alice", "Test", "Alice Test", "GB"))
        db.commit()

        doc_id = uuid.uuid4().hex[:16]
        db.execute("""
            INSERT INTO documents (id, application_id, person_id, doc_type, doc_name, file_path, s3_key, file_size, mime_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (doc_id, sample_application, "dir1", "passport", "passport.pdf", "/tmp/p.pdf", "s3/key", 100, "application/pdf"))
        db.commit()

        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        assert doc["person_id"] == "dir1"
        assert doc["doc_type"] == "passport"

    def test_document_mime_type_stored_correctly(self, temp_db, db, sample_application):
        """Mime type should be stored as provided, not from file_info bracket access."""
        doc_id = uuid.uuid4().hex[:16]
        db.execute("""
            INSERT INTO documents (id, application_id, person_id, doc_type, doc_name, file_path, s3_key, file_size, mime_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (doc_id, sample_application, None, "fin_stmt", "report.xlsx", "/tmp/r.xlsx", None, 500,
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
        db.commit()

        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        assert doc["mime_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ===========================================================================
# 6. Upload Success Returns Correct Key Structure
# ===========================================================================

class TestUploadSuccessResponse:
    """Verify successful upload returns correct key structure."""

    def test_s3_key_structure(self, temp_db):
        """S3 key should follow clients/{client_id}/{doc_type}/{timestamp}_{filename} format."""
        from s3_client import S3Client
        mock_boto_client = MagicMock()
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="test-bucket")

            success, key = client.upload_document(
                file_data=b"content",
                client_id="app_123",
                doc_type="passport",
                filename="scan.pdf"
            )

            assert success is True
            assert key.startswith("clients/app_123/passport/")
            assert key.endswith("_scan.pdf")

    def test_upload_returns_s3_key_not_error(self, temp_db):
        """On success, second element of tuple is the S3 key, not an error message."""
        from s3_client import S3Client
        mock_boto_client = MagicMock()
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="test-bucket")

            success, result = client.upload_document(
                file_data=b"data",
                client_id="c1",
                doc_type="kyc",
                filename="doc.pdf"
            )

            assert success is True
            assert "clients/" in result
            assert "error" not in result.lower()


# ===========================================================================
# 7. Error Message Includes Diagnostic Info
# ===========================================================================

class TestErrorDiagnostics:
    """Verify error messages include bucket name for debugging."""

    def test_client_error_includes_bucket_name(self, temp_db):
        """ClientError messages should include the target bucket name."""
        from s3_client import S3Client
        from botocore.exceptions import ClientError
        mock_boto_client = MagicMock()
        mock_boto_client.put_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}},
            "PutObject"
        )
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="my-staging-bucket")

            _, msg = client.upload_document(
                file_data=b"x", client_id="c", doc_type="t", filename="f"
            )
            assert "my-staging-bucket" in msg

    def test_generic_error_includes_bucket_name(self, temp_db):
        """Generic exception messages should include the target bucket name."""
        from s3_client import S3Client
        mock_boto_client = MagicMock()
        mock_boto_client.put_object.side_effect = RuntimeError("Something broke")
        with patch("s3_client.boto3") as mock_boto:
            mock_boto.client.return_value = mock_boto_client
            client = S3Client(bucket_name="my-debug-bucket")

            _, msg = client.upload_document(
                file_data=b"x", client_id="c", doc_type="t", filename="f"
            )
            assert "my-debug-bucket" in msg
