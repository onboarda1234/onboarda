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

import os
import json
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple, Union
import boto3
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger(__name__)


class S3Client:
    """S3 client wrapper with document management functionality"""

    def __init__(self, bucket_name: Optional[str] = None, region: Optional[str] = None):
        """
        Initialize S3 client.

        Uses explicit AWS credentials if set, otherwise falls back to boto3
        default credential chain (IAM role, instance profile, etc.).

        Args:
            bucket_name (str): S3 bucket name (default: S3_BUCKET env var)
            region (str): AWS region (default: AWS_DEFAULT_REGION env var or af-south-1)
        """
        self.bucket_name = bucket_name or os.getenv('S3_BUCKET', 'regmind-documents-staging')
        self.region = region or os.getenv('AWS_DEFAULT_REGION', 'af-south-1')

        # Use explicit credentials if available, otherwise boto3 default chain (IAM role)
        access_key = os.getenv('AWS_ACCESS_KEY_ID')
        secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')

        if access_key and secret_key:
            self.s3_client = boto3.client(
                's3',
                region_name=self.region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key
            )
        else:
            # Fall back to IAM role / default credential chain (ECS Fargate, EC2, etc.)
            self.s3_client = boto3.client('s3', region_name=self.region)

    def upload_document(
        self,
        file_data: bytes,
        client_id: str,
        doc_type: str,
        filename: str,
        metadata: Optional[Dict[str, str]] = None
    ) -> Tuple[bool, str]:
        """
        Upload a document to S3 with metadata tags.

        Args:
            file_data (bytes): Document content as bytes
            client_id (str): Client identifier for organizing documents
            doc_type (str): Type of document (kyc, identity, proof_of_address, etc.)
            filename (str): Original filename
            metadata (dict, optional): Additional metadata key-value pairs

        Returns:
            Tuple[bool, str]: (success, key_or_error_message)

        Example:
            success, key = s3.upload_document(
                file_data=b"...",
                client_id="client_123",
                doc_type="kyc",
                filename="kyc_form.pdf",
                metadata={"source": "web_upload"}
            )
        """
        try:
            # Build S3 key: clients/{client_id}/{doc_type}/{timestamp}_{filename}
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            key = f"clients/{client_id}/{doc_type}/{timestamp}_{filename}"

            # Prepare tags
            tags_list = [
                {'Key': 'client_id', 'Value': client_id},
                {'Key': 'doc_type', 'Value': doc_type},
                {'Key': 'uploaded_at', 'Value': datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")},
                {'Key': 'filename', 'Value': filename}
            ]

            if metadata:
                for meta_key, meta_value in metadata.items():
                    tags_list.append({'Key': meta_key, 'Value': str(meta_value)[:255]})

            # Upload with tags and encryption
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=file_data,
                ServerSideEncryption='AES256',
                Tagging='&'.join([f"{tag['Key']}={tag['Value']}" for tag in tags_list]),
                Metadata={
                    'client-id': client_id,
                    'doc-type': doc_type,
                    'original-filename': filename
                }
            )

            return True, key

        except ClientError as e:
            error_msg = f"Failed to upload {filename}: {e.response['Error']['Message']}"
            return False, error_msg
        except Exception as e:
            return False, f"Unexpected error uploading document: {str(e)}"

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
            prefix = f"clients/{client_id}/"
            if doc_type:
                prefix += f"{doc_type}/"

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
                        original_filename = metadata_response['Metadata'].get(
                            'original-filename',
                            obj['Key'].split('/')[-1]
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
        response_filename: Optional[str] = None
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
            return self.get_presigned_url(key, expiry, response_filename)

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
        return self.get_presigned_url(key, expiry, response_filename)

    def get_presigned_url(
        self,
        key: str,
        expiry: int = 900,
        response_filename: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Generate a temporary presigned URL for document download.

        The URL expires after the specified duration (default 15 minutes).
        Useful for allowing external access without permanent credentials.

        Args:
            key (str): S3 object key
            expiry (int): URL expiry time in seconds (default: 900 = 15 min, max: 3600 = 1 hour)
            response_filename (str, optional): Filename for Content-Disposition header

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

            if response_filename:
                # H-04: Sanitize filename to prevent header injection
                safe_filename = re.sub(r'[^\w\s\-.]', '_', response_filename)[:255]
                params['ResponseContentDisposition'] = f'attachment; filename="{safe_filename}"'

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
