"""
S3 Client Helper Module for ARIE Finance Platform

Provides convenient methods for S3 operations:
- upload_document: Upload with metadata tags
- download_document: Retrieve a document
- list_client_documents: List all docs for a client
- delete_document: Soft delete with versioning
- get_presigned_url: Generate temporary download URLs
"""
from __future__ import annotations

import base64
import hashlib
import os
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple, Union
from urllib.parse import quote as _url_quote
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger(__name__)

_S3_TAG_VALUE_UNSAFE_RE = re.compile(r"[^A-Za-z0-9 +\-=._:/@]")
_S3_TAG_KEY_UNSAFE_RE = re.compile(r"[^A-Za-z0-9 +\-=._:/@]")
_S3_KEY_COMPONENT_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._=-]")
_S3_METADATA_VALUE_UNSAFE_RE = re.compile(r"[^A-Za-z0-9 +\-=._:/@]")
_S3_MAX_OBJECT_TAGS = 10
_S3_MAX_USER_METADATA_BYTES = 2048
_S3_ORIGINAL_FILENAME_METADATA_KEY = "original-filename-b64"
_MONITORING_RENEWAL_CANDIDATE_SEGMENT = "monitoring_renewal_candidate"
_MONITORING_RENEWAL_KEY_PART_RE = re.compile(r"^[A-Za-z0-9._=-]{1,120}$")
_MONITORING_RENEWAL_FILE_RE = re.compile(r"^[a-f0-9]{32}(?:\.[A-Za-z0-9]{1,20})?$")


def _strip_control_chars(value: str) -> str:
    return "".join(ch if ch.isprintable() and ch not in "\r\n\t" else "_" for ch in value)


def _sanitize_s3_tag_value(value, max_length: int = 255, fallback: str = "unknown") -> str:
    text = _strip_control_chars(str(value or ""))
    text = _S3_TAG_VALUE_UNSAFE_RE.sub("_", text).strip()
    return (text or fallback)[:max_length]


def _sanitize_s3_tag_key(value, max_length: int = 128) -> str:
    text = _strip_control_chars(str(value or "metadata"))
    text = _S3_TAG_KEY_UNSAFE_RE.sub("_", text).strip()
    return (text or "metadata")[:max_length]


def _sanitize_s3_metadata_value(value, max_length: int = 1024) -> str:
    text = _strip_control_chars(str(value or ""))
    text = _S3_METADATA_VALUE_UNSAFE_RE.sub("_", text)
    return text[:max_length]


def _safe_s3_path_component(value, fallback: str) -> str:
    raw = _strip_control_chars(str(value or "")).strip()
    if not raw:
        return fallback
    normalized = raw.replace("\\", "_").replace("/", "_")
    text = normalized.strip()
    text = _S3_KEY_COMPONENT_UNSAFE_RE.sub("_", text).strip("._/")
    if not text:
        text = fallback
    if normalized != raw or text != raw.strip("._/"):
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        text = f"{text[:111]}_{digest}"
    return text[:120]


def _safe_s3_filename(filename: str) -> str:
    text = str(filename or "").replace("\\", "/")
    text = os.path.basename(text).strip()
    text = _S3_KEY_COMPONENT_UNSAFE_RE.sub("_", text).strip("._/")
    if not text:
        return "document"
    if len(text) <= 180:
        return text
    root, ext = os.path.splitext(text)
    ext = ext[:20]
    root_limit = max(1, 180 - len(ext))
    return f"{root[:root_limit]}{ext}"


def _encode_s3_metadata_text(value, max_length: int = 1024) -> str:
    """Return URL-safe base64 ASCII metadata, truncating only for S3 limits."""
    text = _strip_control_chars(str(value or ""))
    while text:
        encoded = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")
        if len(encoded) <= max_length:
            return encoded
        text = text[: max(1, int(len(text) * 0.75))]
    return ""


def _decode_s3_metadata_text(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def _s3_user_metadata_size(metadata: Dict[str, str]) -> int:
    return sum(
        len(str(key).encode("ascii", errors="ignore")) + len(str(value).encode("utf-8"))
        for key, value in metadata.items()
    )


def _build_s3_metadata(client_id: str, doc_type: str, original_filename: str) -> Dict[str, str]:
    metadata = {
        'client-id': _sanitize_s3_metadata_value(client_id, 256),
        'doc-type': _sanitize_s3_metadata_value(doc_type, 256),
        'original-filename': _sanitize_s3_metadata_value(original_filename, 512),
    }
    encoded_original = _encode_s3_metadata_text(original_filename, 1024)
    if encoded_original:
        metadata[_S3_ORIGINAL_FILENAME_METADATA_KEY] = encoded_original

    if _s3_user_metadata_size(metadata) > _S3_MAX_USER_METADATA_BYTES:
        metadata.pop(_S3_ORIGINAL_FILENAME_METADATA_KEY, None)

    if _s3_user_metadata_size(metadata) > _S3_MAX_USER_METADATA_BYTES:
        metadata['original-filename'] = metadata['original-filename'][:128]
        metadata['client-id'] = metadata['client-id'][:128]
        metadata['doc-type'] = metadata['doc-type'][:128]

    if _s3_user_metadata_size(metadata) > _S3_MAX_USER_METADATA_BYTES:
        raise ValueError("S3 user metadata exceeds 2KB limit after sanitization")
    return metadata


def _append_s3_tag(tags_list: List[Dict[str, str]], seen_keys: set[str], key, value) -> None:
    tag_key = _sanitize_s3_tag_key(key)
    if tag_key in seen_keys:
        return
    if len(tags_list) >= _S3_MAX_OBJECT_TAGS:
        raise ValueError("Too many S3 object tags after sanitization")
    seen_keys.add(tag_key)
    tags_list.append({'Key': tag_key, 'Value': _sanitize_s3_tag_value(value)})


def _original_filename_from_metadata(metadata: Dict[str, str], fallback: str) -> str:
    encoded = metadata.get(_S3_ORIGINAL_FILENAME_METADATA_KEY)
    decoded = _decode_s3_metadata_text(encoded)
    if decoded:
        return decoded
    return metadata.get('original-filename', fallback)


def monitoring_renewal_candidate_key_allowed(key: str) -> bool:
    """Allow only the dedicated, deterministic renewal-candidate namespace."""

    parts = str(key or "").split("/")
    return bool(
        len(parts) == 5
        and parts[0] == "clients"
        and _MONITORING_RENEWAL_KEY_PART_RE.fullmatch(parts[1] or "")
        and parts[2] == _MONITORING_RENEWAL_CANDIDATE_SEGMENT
        and _MONITORING_RENEWAL_KEY_PART_RE.fullmatch(parts[3] or "")
        and _MONITORING_RENEWAL_FILE_RE.fullmatch(parts[4] or "")
        and all(part not in (".", "..") for part in parts)
    )


def build_monitoring_renewal_candidate_key(
    customer_id: str,
    request_id: str,
    upload_id: str,
    filename: str,
) -> str:
    """Build the exact deterministic key used by reservation and cleanup."""

    safe_customer = _safe_s3_path_component(customer_id, "client")
    safe_request = _safe_s3_path_component(request_id, "request")
    normalized_upload = str(upload_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}", normalized_upload):
        raise ValueError("upload_id must be a 32-character lowercase hex value")
    extension = os.path.splitext(_safe_s3_filename(filename))[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,20}", extension or ""):
        extension = ""
    key = (
        f"clients/{safe_customer}/{_MONITORING_RENEWAL_CANDIDATE_SEGMENT}/"
        f"{safe_request}/{normalized_upload}{extension}"
    )
    if not monitoring_renewal_candidate_key_allowed(key):
        raise ValueError("monitoring renewal candidate key is outside the allowlist")
    return key


class S3Client:
    """S3 client wrapper with document management functionality"""

    def __init__(self, bucket_name: Optional[str] = None, region: Optional[str] = None):
        """
        Initialize S3 client.

        Uses explicit AWS credentials if set, otherwise falls back to boto3
        default credential chain (IAM role, instance profile, etc.).

        Args:
            bucket_name (str): S3 bucket name (resolved via get_s3_bucket() by default)
            region (str): AWS region (default: AWS_REGION/AWS_DEFAULT_REGION env var or af-south-1)
        """
        if bucket_name:
            self.bucket_name = bucket_name
        else:
            # Use environment-aware bucket resolution from environment.py,
            # falling back to S3_BUCKET env var for backward compatibility.
            try:
                from environment import get_s3_bucket
                self.bucket_name = get_s3_bucket()
            except ImportError:
                self.bucket_name = os.getenv('S3_BUCKET', 'arie-documents')
        self.region = region or os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION', 'af-south-1')
        self.endpoint_url = (
            os.getenv('S3_ENDPOINT_URL')
            or os.getenv('AWS_S3_ENDPOINT_URL')
            or f"https://s3.{self.region}.amazonaws.com"
        )
        self.s3_config = Config(
            signature_version='s3v4',
            s3={'addressing_style': 'virtual'},
        )

        # Use explicit credentials if available, otherwise boto3 default chain (IAM role)
        access_key = os.getenv('AWS_ACCESS_KEY_ID')
        secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
        client_kwargs = {
            'region_name': self.region,
            'endpoint_url': self.endpoint_url,
            'config': self.s3_config,
        }

        if access_key and secret_key:
            client_kwargs['aws_access_key_id'] = access_key
            client_kwargs['aws_secret_access_key'] = secret_key
            self.s3_client = boto3.client('s3', **client_kwargs)
        else:
            # Fall back to IAM role / default credential chain (ECS Fargate, EC2, etc.)
            self.s3_client = boto3.client('s3', **client_kwargs)
        self._client_kwargs = dict(client_kwargs)

    def probe_client(self):
        """Return a client for readiness probing (P12-9 / DCI-029).

        Botocore's defaults (60s connect/read, retries) would block the
        caller for minutes during exactly the outage the probe exists to
        detect — and the readiness handler runs on the IOLoop thread, so
        that would stall the whole API. Tight timeouts, one attempt.
        """
        kwargs = dict(self._client_kwargs)
        kwargs['config'] = Config(
            signature_version='s3v4',
            s3={'addressing_style': 'virtual'},
            connect_timeout=2,
            read_timeout=3,
            retries={'max_attempts': 1},
        )
        return boto3.client('s3', **kwargs)

    def monitoring_renewal_candidate_client(self):
        """Use bounded I/O for request staging and always-on cleanup retries."""

        kwargs = dict(self._client_kwargs)
        kwargs['config'] = Config(
            signature_version='s3v4',
            s3={'addressing_style': 'virtual'},
            connect_timeout=3,
            read_timeout=10,
            retries={'max_attempts': 2, 'mode': 'standard'},
        )
        return boto3.client('s3', **kwargs)

    def upload_document(
        self,
        file_data: bytes,
        client_id: str,
        doc_type: str,
        filename: str,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None
    ) -> Tuple[bool, str]:
        """
        Upload a document to S3 with metadata tags.

        Args:
            file_data (bytes): Document content as bytes
            client_id (str): Client identifier for organizing documents
            doc_type (str): Type of document (kyc, identity, proof_of_address, etc.)
            filename (str): Original filename
            content_type (str, optional): MIME type (default: application/octet-stream)
            metadata (dict, optional): Additional metadata key-value pairs

        Returns:
            Tuple[bool, str]: (success, key_or_error_message)

        Example:
            success, key = s3.upload_document(
                file_data=b"...",
                client_id="client_123",
                doc_type="kyc",
                filename="kyc_form.pdf",
                content_type="application/pdf",
                metadata={"source": "web_upload"}
            )
        """
        try:
            # Build S3 key: clients/{client_id}/{doc_type}/{timestamp}_{upload_id}_{filename}
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            upload_id = uuid.uuid4().hex[:12]
            safe_client_id = _safe_s3_path_component(client_id, "client")
            safe_doc_type = _safe_s3_path_component(doc_type, "document")
            safe_filename = _safe_s3_filename(filename)
            key = f"clients/{safe_client_id}/{safe_doc_type}/{timestamp}_{upload_id}_{safe_filename}"

            # Prepare tags
            tags_list = [
                {'Key': 'client_id', 'Value': _sanitize_s3_tag_value(client_id)},
                {'Key': 'doc_type', 'Value': _sanitize_s3_tag_value(doc_type)},
                {'Key': 'uploaded_at', 'Value': datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")},
                {'Key': 'filename', 'Value': _sanitize_s3_tag_value(safe_filename)}
            ]
            seen_tag_keys = {tag['Key'] for tag in tags_list}

            if metadata:
                for meta_key, meta_value in metadata.items():
                    _append_s3_tag(tags_list, seen_tag_keys, meta_key, meta_value)

            # URL-encode tag values for S3 Tagging header format
            tagging_str = '&'.join([
                f"{_url_quote(str(tag['Key']), safe='')}={_url_quote(str(tag['Value']), safe='')}"
                for tag in tags_list
            ])

            resolved_content_type = content_type or "application/octet-stream"
            original_filename_for_storage = metadata.get("original_name", filename) if metadata else filename
            s3_metadata = _build_s3_metadata(client_id, doc_type, original_filename_for_storage)

            # Upload with tags and encryption
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=file_data,
                ContentType=resolved_content_type,
                ServerSideEncryption='AES256',
                Tagging=tagging_str,
                Metadata=s3_metadata
            )

            return True, key

        except ClientError as e:
            error_msg = f"Failed to upload {filename} to bucket '{self.bucket_name}': {e.response['Error']['Message']}"
            logger.error(f"S3 ClientError during upload: {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected error uploading document to bucket '{self.bucket_name}': {str(e)}"
            logger.error(f"S3 upload exception: {error_msg}")
            return False, error_msg

    @staticmethod
    def build_monitoring_renewal_candidate_key(
        customer_id: str,
        request_id: str,
        upload_id: str,
        filename: str,
    ) -> str:
        """Build the exact key reserved before a staged candidate is written."""

        return build_monitoring_renewal_candidate_key(
            customer_id,
            request_id,
            upload_id,
            filename,
        )

    def upload_monitoring_renewal_candidate(
        self,
        *,
        key: str,
        file_data: bytes,
        customer_id: str,
        request_id: str,
        upload_id: str,
        cleanup_id: str,
        original_filename: str,
        content_type: str,
        file_sha256: str,
    ) -> Tuple[bool, Union[Dict[str, Optional[str]], str]]:
        """Write one pre-reserved candidate and return its exact object version."""

        try:
            expected = self.build_monitoring_renewal_candidate_key(
                customer_id,
                request_id,
                upload_id,
                original_filename,
            )
        except (TypeError, ValueError):
            return False, "invalid_reserved_key"
        if key != expected or not monitoring_renewal_candidate_key_allowed(key):
            return False, "invalid_reserved_key"
        try:
            metadata = _build_s3_metadata(
                customer_id,
                _MONITORING_RENEWAL_CANDIDATE_SEGMENT,
                original_filename,
            )
            metadata.update(
                {
                    "renewal-request-id": _sanitize_s3_metadata_value(request_id, 256),
                    "renewal-upload-id": _sanitize_s3_metadata_value(upload_id, 64),
                    "renewal-cleanup-id": _sanitize_s3_metadata_value(cleanup_id, 64),
                    "file-sha256": _sanitize_s3_metadata_value(file_sha256, 64),
                }
            )
        except Exception:
            logger.exception("S3 monitoring renewal candidate metadata failed")
            return False, "candidate_upload_failed"
        if _s3_user_metadata_size(metadata) > _S3_MAX_USER_METADATA_BYTES:
            return False, "metadata_limit_exceeded"

        def reconcile_conditional_write():
            inspected, result = self.inspect_monitoring_renewal_candidate(key)
            if not inspected or not isinstance(result, dict):
                return None
            if result.get("exists") is not True:
                return False
            identity_matches = all(
                (
                    str(result.get("request_id") or "") == str(request_id),
                    str(result.get("upload_id") or "") == str(upload_id),
                    str(result.get("cleanup_id") or "") == str(cleanup_id),
                    str(result.get("file_sha256") or "") == str(file_sha256),
                    result.get("content_length") == len(file_data),
                )
            )
            if not identity_matches:
                return False
            return {
                "key": key,
                "version_id": str(result.get("version_id") or "").strip() or None,
                "etag": str(result.get("etag") or "").strip() or None,
                "reconciled": True,
            }

        for attempt in range(2):
            try:
                response = self.monitoring_renewal_candidate_client().put_object(
                    Bucket=self.bucket_name,
                    Key=key,
                    Body=file_data,
                    ContentType=content_type or "application/octet-stream",
                    ServerSideEncryption="AES256",
                    IfNoneMatch="*",
                    Metadata=metadata,
                    Tagging=(
                        "doc_type=monitoring_renewal_candidate&"
                        f"client_id={_url_quote(_sanitize_s3_tag_value(customer_id), safe='')}"
                    ),
                )
                return True, {
                    "key": key,
                    "version_id": str(response.get("VersionId") or "").strip() or None,
                    "etag": str(response.get("ETag") or "").strip() or None,
                }
            except ClientError as exc:
                code = str((exc.response.get("Error") or {}).get("Code") or "")
                if code in ("PreconditionFailed", "412"):
                    reconciled = reconcile_conditional_write()
                    if isinstance(reconciled, dict):
                        return True, reconciled
                    if reconciled is None:
                        return False, "candidate_ownership_unconfirmed"
                    return False, "candidate_already_exists"
                if code in ("ConditionalRequestConflict", "409"):
                    if attempt == 0:
                        continue
                    reconciled = reconcile_conditional_write()
                    if isinstance(reconciled, dict):
                        return True, reconciled
                    if reconciled is None:
                        return False, "candidate_ownership_unconfirmed"
                    return False, "candidate_upload_conflict"
                logger.exception("S3 monitoring renewal candidate upload failed")
                return False, "candidate_upload_failed"
            except Exception:
                logger.exception("S3 monitoring renewal candidate upload failed")
                return False, "candidate_upload_failed"
        return False, "candidate_upload_conflict"

    def inspect_monitoring_renewal_candidate(
        self,
        key: str,
    ) -> Tuple[bool, Union[Dict[str, Optional[str]], str]]:
        """Inspect only the allowlisted candidate key without exposing payloads."""

        if not monitoring_renewal_candidate_key_allowed(key):
            return False, "invalid_reserved_key"
        try:
            response = self.monitoring_renewal_candidate_client().head_object(
                Bucket=self.bucket_name,
                Key=key,
            )
            metadata = response.get("Metadata") or {}
            try:
                content_length = int(response.get("ContentLength"))
            except (TypeError, ValueError):
                content_length = None
            return True, {
                "exists": True,
                "version_id": str(response.get("VersionId") or "").strip() or None,
                "etag": str(response.get("ETag") or "").strip() or None,
                "request_id": str(metadata.get("renewal-request-id") or ""),
                "upload_id": str(metadata.get("renewal-upload-id") or ""),
                "cleanup_id": str(metadata.get("renewal-cleanup-id") or ""),
                "file_sha256": str(metadata.get("file-sha256") or ""),
                "content_length": content_length,
            }
        except ClientError as exc:
            code = str((exc.response.get("Error") or {}).get("Code") or "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return True, {"exists": False, "version_id": None}
            return False, "candidate_inspection_failed"
        except Exception:
            return False, "candidate_inspection_failed"

    def delete_monitoring_renewal_candidate(
        self,
        key: str,
        *,
        version_id: Optional[str] = None,
        expected_request_id: Optional[str] = None,
        expected_upload_id: Optional[str] = None,
        expected_cleanup_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Delete the exact uncommitted object version, never a general document."""

        if not monitoring_renewal_candidate_key_allowed(key):
            return False, "invalid_reserved_key"
        exact_version = str(version_id or "").strip() or None
        if exact_version is None:
            inspected, result = self.inspect_monitoring_renewal_candidate(key)
            if not inspected or not isinstance(result, dict):
                return False, "candidate_inspection_failed"
            if result.get("exists") is not True:
                return True, "candidate_absent"
            expected_identity = {
                "request_id": str(expected_request_id or ""),
                "upload_id": str(expected_upload_id or ""),
                "cleanup_id": str(expected_cleanup_id or ""),
            }
            if any(expected_identity.values()) and any(
                str(result.get(field) or "") != value
                for field, value in expected_identity.items()
                if value
            ):
                return False, "candidate_ownership_mismatch"
            exact_version = str(result.get("version_id") or "").strip() or None
        try:
            kwargs = {"Bucket": self.bucket_name, "Key": key}
            if exact_version:
                kwargs["VersionId"] = exact_version
            self.monitoring_renewal_candidate_client().delete_object(**kwargs)
            return True, "candidate_deleted"
        except Exception:
            return False, "candidate_delete_failed"

    def download_document(self, key: str) -> Tuple[bool, bytes | str]:
        """
        Retrieve a document from S3.

        Args:
            key (str): S3 object key

        Returns:
            Tuple[bool, bytes|str]: (success, file_data_or_error_message)

        Example:
            success, data = s3.download_document("clients/client_123/kyc/20260316_120000_kyc_form.pdf")
            if success:
                with open("downloaded.pdf", "wb") as f:
                    f.write(data)
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=key
            )
            file_data = response['Body'].read()
            return True, file_data

        except self.s3_client.exceptions.NoSuchKey:
            return False, f"Document not found: {key}"
        except ClientError as e:
            error_msg = f"Failed to download document: {e.response['Error']['Message']}"
            return False, error_msg
        except Exception as e:
            return False, f"Unexpected error downloading document: {str(e)}"

    def list_client_documents(
        self,
        client_id: str,
        doc_type: Optional[str] = None
    ) -> Tuple[bool, List[Dict] | str]:
        """
        List all documents for a specific client.

        Args:
            client_id (str): Client identifier
            doc_type (str, optional): Filter by document type (kyc, identity, etc.)

        Returns:
            Tuple[bool, List[Dict]|str]: (success, documents_list_or_error)

            Each document dict contains:
            - key: S3 object key
            - filename: Original filename
            - uploaded_at: Upload timestamp
            - size: File size in bytes
            - etag: Object ETag
            - version_id: Version ID (if versioning enabled)

        Example:
            success, docs = s3.list_client_documents("client_123", doc_type="kyc")
            if success:
                for doc in docs:
                    print(f"{doc['filename']} - {doc['size']} bytes")
        """
        try:
            prefix = f"clients/{_safe_s3_path_component(client_id, 'client')}/"
            if doc_type:
                prefix += f"{_safe_s3_path_component(doc_type, 'document')}/"

            documents = []
            paginator = self.s3_client.get_paginator('list_objects_v2')

            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                if 'Contents' not in page:
                    continue

                for obj in page['Contents']:
                    # Skip if it's a directory marker
                    if obj['Key'].endswith('/'):
                        continue

                    # Try to extract original filename from metadata
                    try:
                        metadata_response = self.s3_client.head_object(
                            Bucket=self.bucket_name,
                            Key=obj['Key']
                        )
                        original_filename = _original_filename_from_metadata(
                            metadata_response.get('Metadata', {}),
                            obj['Key'].split('/')[-1],
                        )
                    except:
                        original_filename = obj['Key'].split('/')[-1]

                    document = {
                        'key': obj['Key'],
                        'filename': original_filename,
                        'size': obj['Size'],
                        'etag': obj['ETag'].strip('"'),
                        'uploaded_at': obj['LastModified'].isoformat() if 'LastModified' in obj else None
                    }

                    # Include version ID if available
                    if 'VersionId' in obj:
                        document['version_id'] = obj['VersionId']

                    documents.append(document)

            return True, documents

        except ClientError as e:
            error_msg = f"Failed to list documents: {e.response['Error']['Message']}"
            return False, error_msg
        except Exception as e:
            return False, f"Unexpected error listing documents: {str(e)}"

    def delete_document(self, key: str) -> Tuple[bool, str]:
        """
        Soft delete a document (adds deletion marker, versioning preserves history).

        With versioning enabled, this creates a delete marker instead of permanently
        removing the object, preserving the document history for recovery.

        Args:
            key (str): S3 object key

        Returns:
            Tuple[bool, str]: (success, message_or_error)

        Example:
            success, msg = s3.delete_document("clients/client_123/kyc/20260316_120000_kyc_form.pdf")
        """
        try:
            response = self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=key
            )

            message = f"Document deleted: {key}"
            if 'DeleteMarker' in response and response['DeleteMarker']:
                message += " (soft delete - previous versions preserved)"

            return True, message

        except ClientError as e:
            error_msg = f"Failed to delete document: {e.response['Error']['Message']}"
            return False, error_msg
        except Exception as e:
            return False, f"Unexpected error deleting document: {str(e)}"

    def get_presigned_url_with_ownership(
        self,
        key: str,
        requesting_user_id: str,
        requesting_user_role: str,
        db_connection=None,
        expiry: int = 900,
        response_filename: Optional[str] = None,
        content_disposition: str = "attachment",
    ) -> Tuple[bool, str]:
        """
        H-04 FIX: Generate presigned URL with ownership validation.

        Officers (admin, sco, co) can access any document.
        Clients can only access documents belonging to their applications.

        Args:
            key: S3 object key (format: documents/{app_id}/{filename} or clients/{client_id}/...)
            requesting_user_id: ID of the user requesting access
            requesting_user_role: Role of the requesting user
            db_connection: Database connection for ownership lookup
            expiry: URL expiry in seconds
            response_filename: Optional filename for Content-Disposition
            content_disposition: Browser handling for the response; "attachment" or "inline"

        Returns:
            Tuple[bool, str]: (success, url_or_error_message)
        """
        # Officers have full access
        officer_roles = ("admin", "sco", "co")
        if requesting_user_role in officer_roles:
            logger.info(
                f"H-04 AUDIT: Officer {requesting_user_id} ({requesting_user_role}) "
                f"accessed document: {key}"
            )
            return self.get_presigned_url(key, expiry, response_filename, content_disposition)

        # Clients: validate ownership through application_id
        # Extract application_id from key path (format: documents/{app_id}/...)
        key_parts = key.split("/")
        if len(key_parts) < 2:
            logger.warning(
                f"H-04 SECURITY: Access denied — invalid key format: {key} "
                f"by user {requesting_user_id}"
            )
            return False, "Access denied: invalid document path"

        # Determine app_id from key structure
        app_id = key_parts[1] if key_parts[0] == "documents" else None
        client_id = key_parts[1] if key_parts[0] == "clients" else None

        if db_connection:
            if app_id:
                # Verify the application belongs to this client
                app = db_connection.execute(
                    "SELECT client_id FROM applications WHERE id = ?", (app_id,)
                ).fetchone()
                if not app or app["client_id"] != requesting_user_id:
                    logger.warning(
                        f"H-04 SECURITY: Document access DENIED — user {requesting_user_id} "
                        f"attempted to access document for application {app_id} "
                        f"(owner: {app['client_id'] if app else 'not found'})"
                    )
                    return False, "Access denied: you do not own this document"
            elif client_id and client_id != requesting_user_id:
                logger.warning(
                    f"H-04 SECURITY: Document access DENIED — user {requesting_user_id} "
                    f"attempted to access documents for client {client_id}"
                )
                return False, "Access denied: you do not own this document"
        else:
            # No DB connection provided — fail closed for clients
            logger.warning(
                f"H-04 SECURITY: Access denied — no DB connection for ownership check. "
                f"User {requesting_user_id}, key {key}"
            )
            return False, "Access denied: unable to verify document ownership"

        logger.info(
            f"H-04 AUDIT: Client {requesting_user_id} accessed owned document: {key}"
        )
        return self.get_presigned_url(key, expiry, response_filename, content_disposition)

    def get_presigned_url(
        self,
        key: str,
        expiry: int = 900,
        response_filename: Optional[str] = None,
        content_disposition: str = "attachment",
    ) -> Tuple[bool, str]:
        """
        Generate a temporary presigned URL for document download.

        The URL expires after the specified duration (default 15 minutes).
        Useful for allowing external access without permanent credentials.

        Args:
            key (str): S3 object key
            expiry (int): URL expiry time in seconds (default: 900 = 15 min, max: 3600 = 1 hour)
            response_filename (str, optional): Filename for Content-Disposition header
            content_disposition (str): "attachment" for downloads or "inline" for browser preview

        Returns:
            Tuple[bool, str]: (success, url_or_error_message)

        Example:
            success, url = s3.get_presigned_url(
                "clients/client_123/kyc/20260316_120000_kyc_form.pdf",
                expiry=7200,
                response_filename="kyc_form.pdf"
            )
            if success:
                # Share this URL (expires in 2 hours)
                print(url)
        """
        try:
            # Ensure expiry is within valid range (1 second to 1 hour max)
            expiry = max(1, min(expiry, 3600))

            params = {
                'Bucket': self.bucket_name,
                'Key': key,
            }

            # H-04: Sanitize filename to prevent header injection.
            # P11-7 (BSA-008): ALWAYS emit a Content-Disposition override. A
            # falsy response_filename previously skipped the override entirely,
            # letting S3 serve the object without any disposition (inline-capable
            # in browsers). Fall back to a neutral filename instead. The class
            # allows plain spaces only — \s would readmit CR/LF (header injection).
            safe_filename = re.sub(r'[^\w \-.]', '_', response_filename or "document")[:255] or "document"
            disposition = "inline" if content_disposition == "inline" else "attachment"
            params['ResponseContentDisposition'] = f'{disposition}; filename="{safe_filename}"'

            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params=params,
                ExpiresIn=expiry
            )

            return True, url

        except ClientError as e:
            error_msg = f"Failed to generate presigned URL: {e.response['Error']['Message']}"
            return False, error_msg
        except Exception as e:
            return False, f"Unexpected error generating URL: {str(e)}"

    def get_document_metadata(self, key: str) -> Tuple[bool, Dict | str]:
        """
        Retrieve metadata and tags for a document.

        Args:
            key (str): S3 object key

        Returns:
            Tuple[bool, Dict|str]: (success, metadata_dict_or_error)

            Metadata dict contains:
            - size: File size in bytes
            - etag: Object ETag
            - last_modified: Last modification timestamp
            - content_type: MIME type
            - metadata: Custom metadata key-value pairs
            - tags: Object tags
            - version_id: Version ID (if versioning enabled)

        Example:
            success, meta = s3.get_document_metadata("clients/client_123/kyc/...")
            if success:
                print(f"Size: {meta['size']} bytes")
                print(f"Tags: {meta['tags']}")
        """
        try:
            head_response = self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=key
            )

            # Get tags separately
            tags = {}
            try:
                tags_response = self.s3_client.get_object_tagging(
                    Bucket=self.bucket_name,
                    Key=key
                )
                tags = {tag['Key']: tag['Value'] for tag in tags_response.get('TagSet', [])}
            except:
                pass

            metadata = {
                'size': head_response['ContentLength'],
                'etag': head_response['ETag'].strip('"'),
                'last_modified': head_response['LastModified'].isoformat(),
                'content_type': head_response.get('ContentType', 'unknown'),
                'metadata': head_response.get('Metadata', {}),
                'tags': tags
            }

            if 'VersionId' in head_response:
                metadata['version_id'] = head_response['VersionId']

            return True, metadata

        except self.s3_client.exceptions.NoSuchKey:
            return False, f"Document not found: {key}"
        except ClientError as e:
            error_msg = f"Failed to get metadata: {e.response['Error']['Message']}"
            return False, error_msg
        except Exception as e:
            return False, f"Unexpected error getting metadata: {str(e)}"

    def copy_document(
        self,
        source_key: str,
        destination_key: str,
        metadata: Optional[Dict[str, str]] = None
    ) -> Tuple[bool, str]:
        """
        Copy a document within the S3 bucket.

        Args:
            source_key (str): Source object key
            destination_key (str): Destination object key
            metadata (dict, optional): Updated metadata for destination

        Returns:
            Tuple[bool, str]: (success, message_or_error)
        """
        try:
            copy_source = {'Bucket': self.bucket_name, 'Key': source_key}

            kwargs = {
                'Bucket': self.bucket_name,
                'Key': destination_key,
                'CopySource': copy_source,
                'ServerSideEncryption': 'AES256'
            }

            if metadata:
                kwargs['Metadata'] = metadata

            self.s3_client.copy_object(**kwargs)
            return True, f"Document copied to {destination_key}"

        except ClientError as e:
            error_msg = f"Failed to copy document: {e.response['Error']['Message']}"
            return False, error_msg
        except Exception as e:
            return False, f"Unexpected error copying document: {str(e)}"


# Convenience function for quick initialization
def get_s3_client(bucket_name: Optional[str] = None, region: Optional[str] = None) -> S3Client:
    """
    Create and return an S3 client instance.

    Args:
        bucket_name (str): S3 bucket name (default: S3_BUCKET env var)
        region (str): AWS region (default: AWS_DEFAULT_REGION env var or af-south-1)

    Returns:
        S3Client: Configured S3 client instance

    Example:
        from s3_client import get_s3_client

        s3 = get_s3_client()
        success, key = s3.upload_document(...)
    """
    return S3Client(bucket_name=bucket_name, region=region)


if __name__ == '__main__':
    # Simple test/demo
    print("S3 Client Module for ARIE Finance Platform")
    print("=" * 50)
    print("\nUsage:")
    print("  from s3_client import get_s3_client")
    print("  s3 = get_s3_client()")
    print("  success, key = s3.upload_document(...)")
    print("\nAvailable methods:")
    print("  - upload_document()")
    print("  - download_document()")
    print("  - list_client_documents()")
    print("  - delete_document()")
    print("  - get_presigned_url()")
    print("  - get_document_metadata()")
    print("  - copy_document()")
