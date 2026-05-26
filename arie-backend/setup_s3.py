#!/usr/bin/env python3
"""
AWS S3 Bucket Setup Script for ARIE Finance Pilot Platform

This script configures an S3 bucket with:
- Server-side encryption (AES-256)
- Versioning enabled
- Public access blocked
- Lifecycle rules for KYC document retention and archival
- CORS configuration for the Render-hosted frontend

Environment Variables Required:
- AWS_ACCESS_KEY_ID: AWS access key
- AWS_SECRET_ACCESS_KEY: AWS secret key
- AWS_REGION: AWS region (default: eu-west-1)
"""

import os
import sys
import json
import boto3
from botocore.exceptions import ClientError


class S3BucketSetup:
    """Manages S3 bucket setup and configuration"""

    def __init__(self, region='eu-west-1'):
        """
        Initialize S3 bucket setup with AWS credentials from environment variables.

        Args:
            region (str): AWS region for the bucket
        """
        self.region = region
        self.bucket_name = 'arie-finance-documents'

        # Initialize S3 client with credentials from environment variables
        try:
            self.s3_client = boto3.client(
                's3',
                region_name=region,
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
            )
        except Exception as e:
            print(f"Error: Failed to initialize S3 client. Check AWS credentials.")
            print(f"Details: {e}")
            sys.exit(1)

    def bucket_exists(self):
        """Check if the bucket already exists"""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            raise

    def create_bucket(self):
        """Create S3 bucket in the specified region"""
        try:
            if self.bucket_exists():
                print(f"Bucket '{self.bucket_name}' already exists.")
                return True

            print(f"Creating bucket '{self.bucket_name}' in region '{self.region}'...")

            if self.region == 'us-east-1':
                # us-east-1 doesn't require LocationConstraint
                self.s3_client.create_bucket(Bucket=self.bucket_name)
            else:
                self.s3_client.create_bucket(
                    Bucket=self.bucket_name,
                    CreateBucketConfiguration={'LocationConstraint': self.region}
                )

            print(f"✓ Bucket '{self.bucket_name}' created successfully.")
            return True
        except ClientError as e:
            print(f"✗ Error creating bucket: {e}")
            return False

    def enable_versioning(self):
        """Enable versioning on the bucket"""
        try:
            print("Enabling versioning...")
            self.s3_client.put_bucket_versioning(
                Bucket=self.bucket_name,
                VersioningConfiguration={'Status': 'Enabled'}
            )
            print("✓ Versioning enabled.")
            return True
        except ClientError as e:
            print(f"✗ Error enabling versioning: {e}")
            return False

    def enable_server_side_encryption(self):
        """Enable server-side encryption (AES-256) by default"""
        try:
            print("Enabling server-side encryption (AES-256)...")
            self.s3_client.put_bucket_encryption(
                Bucket=self.bucket_name,
                ServerSideEncryptionConfiguration={
                    'Rules': [
                        {
                            'ApplyServerSideEncryptionByDefault': {
                                'SSEAlgorithm': 'AES256'
                            },
                            'BucketKeyEnabled': True
                        }
                    ]
                }
            )
            print("✓ Server-side encryption (AES-256) enabled.")
            return True
        except ClientError as e:
            print(f"✗ Error enabling encryption: {e}")
            return False

    def block_public_access(self):
        """Block all public access to the bucket"""
        try:
            print("Blocking all public access...")
            self.s3_client.put_public_access_block(
                Bucket=self.bucket_name,
                PublicAccessBlockConfiguration={
                    'BlockPublicAcls': True,
                    'IgnorePublicAcls': True,
                    'BlockPublicPolicy': True,
                    'RestrictPublicBuckets': True
                }
            )
            print("✓ Public access blocked.")
            return True
        except ClientError as e:
            print(f"✗ Error blocking public access: {e}")
            return False

    def configure_lifecycle_rules(self):
        """Configure lifecycle rules for KYC document retention and archival"""
        try:
            print("Configuring lifecycle rules...")

            lifecycle_config = {
                'Rules': [
                    {
                        'Id': 'kyc-document-retention',
                        'Status': 'Enabled',
                        'Filter': {
                            'And': {
                                'Tags': [
                                    {
                                        'Key': 'doc_type',
                                        'Value': 'kyc'
                                    }
                                ],
                                'Prefix': 'kyc/'
                            }
                        },
                        'Transitions': [
                            {
                                'Days': 90,
                                'StorageClass': 'STANDARD_IA'
                            },
                            {
                                'Days': 365,
                                'StorageClass': 'GLACIER'
                            }
                        ],
                        'Expiration': {
                            'Days': 2555  # 7 years
                        },
                        'NoncurrentVersionTransitions': [
                            {
                                'NoncurrentDays': 90,
                                'StorageClass': 'STANDARD_IA'
                            },
                            {
                                'NoncurrentDays': 365,
                                'StorageClass': 'GLACIER'
                            }
                        ],
                        'NoncurrentVersionExpiration': {
                            'NoncurrentDays': 2555  # 7 years
                        }
                    },
                    {
                        'Id': 'general-document-cleanup',
                        'Status': 'Enabled',
                        'Filter': {
                            'Prefix': 'temp/'
                        },
                        'Expiration': {
                            'Days': 30  # Clean up temporary files after 30 days
                        }
                    }
                ]
            }

            self.s3_client.put_bucket_lifecycle_configuration(
                Bucket=self.bucket_name,
                LifecycleConfiguration=lifecycle_config
            )
            print("✓ Lifecycle rules configured:")
            print("  - KYC documents: Standard-IA after 90 days, Glacier after 365 days, deleted after 2555 days (7 years)")
            print("  - Temporary files: deleted after 30 days")
            return True
        except ClientError as e:
            print(f"✗ Error configuring lifecycle rules: {e}")
            return False

    def configure_cors(self):
        """Configure CORS for the Render-hosted frontend"""
        try:
            print("Configuring CORS...")

            cors_config = {
                'CORSRules': [
                    {
                        'AllowedOrigins': [
                            'https://arie-finance.onrender.com',
                            'http://localhost:3000',  # For local development
                            'http://localhost:5000'   # For local development
                        ],
                        'AllowedMethods': ['GET', 'PUT', 'POST', 'DELETE', 'HEAD'],
                        'AllowedHeaders': ['*'],
                        'ExposeHeaders': [
                            'ETag',
                            'x-amz-version-id',
                            'x-amz-request-id'
                        ],
                        'MaxAgeSeconds': 3600
                    }
                ]
            }

            self.s3_client.put_bucket_cors(
                Bucket=self.bucket_name,
                CORSConfiguration=cors_config
            )
            print("✓ CORS configured for:")
            print("  - https://arie-finance.onrender.com (production)")
            print("  - http://localhost:3000 (dev)")
            print("  - http://localhost:5000 (dev)")
            return True
        except ClientError as e:
            print(f"✗ Error configuring CORS: {e}")
            return False

    def configure_bucket_policy(self):
        """Configure bucket policy to allow only encrypted uploads"""
        try:
            print("Configuring bucket policy...")

            bucket_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "DenyUnencryptedObjectUploads",
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:PutObject",
                        "Resource": f"arn:aws:s3:::{self.bucket_name}/*",
                        "Condition": {
                            "StringNotEquals": {
                                "s3:x-amz-server-side-encryption": "AES256"
                            }
                        }
                    },
                    {
                        "Sid": "DenyInsecureTransport",
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:*",
                        "Resource": [
                            f"arn:aws:s3:::{self.bucket_name}",
                            f"arn:aws:s3:::{self.bucket_name}/*"
                        ],
                        "Condition": {
                            "Bool": {
                                "aws:SecureTransport": "false"
                            }
                        }
                    }
                ]
            }

            self.s3_client.put_bucket_policy(
                Bucket=self.bucket_name,
                Policy=json.dumps(bucket_policy)
            )
            print("✓ Bucket policy configured:")
            print("  - Deny unencrypted uploads")
            print("  - Require HTTPS/TLS for all operations")
            return True
        except ClientError as e:
            print(f"✗ Error configuring bucket policy: {e}")
            return False

    def enable_logging(self):
        """Enable access logging for the bucket"""
        try:
            print("Configuring access logging...")

            # Note: This creates a logging bucket. In production, use a separate bucket
            log_target_bucket = f"{self.bucket_name}-logs"

            # Try to create the logging bucket if it doesn't exist
            try:
                if self.region == 'us-east-1':
                    self.s3_client.create_bucket(Bucket=log_target_bucket)
                else:
                    self.s3_client.create_bucket(
                        Bucket=log_target_bucket,
                        CreateBucketConfiguration={'LocationConstraint': self.region}
                    )
                print(f"  Created logging bucket '{log_target_bucket}'")
            except ClientError as e:
                if e.response['Error']['Code'] != 'BucketAlreadyExists':
                    print(f"  Warning: Could not create logging bucket: {e}")

            # Configure logging
            self.s3_client.put_bucket_logging(
                Bucket=self.bucket_name,
                BucketLoggingStatus={
                    'LoggingEnabled': {
                        'TargetBucket': log_target_bucket,
                        'TargetPrefix': 'logs/'
                    }
                }
            )
            print(f"✓ Access logging configured to '{log_target_bucket}'")
            return True
        except ClientError as e:
            print(f"✗ Error configuring logging: {e}")
            return False

    def display_bucket_info(self):
        """Display current bucket configuration"""
        try:
            print("\n" + "="*60)
            print("BUCKET CONFIGURATION SUMMARY")
            print("="*60)

            # Versioning
            versioning = self.s3_client.get_bucket_versioning(Bucket=self.bucket_name)
            status = versioning.get('Status', 'Not Set')
            print(f"Versioning: {status}")

            # Encryption
            try:
                encryption = self.s3_client.get_bucket_encryption(Bucket=self.bucket_name)
                rules = encryption['ServerSideEncryptionConfiguration']['Rules']
                print(f"Encryption: {rules[0]['ApplyServerSideEncryptionByDefault']['SSEAlgorithm']}")
            except:
                print("Encryption: Not configured")

            # Public Access
            pab = self.s3_client.get_public_access_block(Bucket=self.bucket_name)
            config = pab['PublicAccessBlockConfiguration']
            print(f"Public Access Blocked: {config['BlockPublicAcls']}")

            # Lifecycle
            try:
                lifecycle = self.s3_client.get_bucket_lifecycle_configuration(Bucket=self.bucket_name)
                print(f"Lifecycle Rules: {len(lifecycle['Rules'])} rule(s) configured")
            except:
                print("Lifecycle Rules: Not configured")

            print("="*60 + "\n")

        except ClientError as e:
            print(f"Error retrieving bucket information: {e}")

    def setup(self):
        """Execute full bucket setup"""
        print("\n" + "="*60)
        print("ARIE FINANCE S3 BUCKET SETUP")
        print("="*60 + "\n")

        steps = [
            ("Creating bucket", self.create_bucket),
            ("Enabling versioning", self.enable_versioning),
            ("Enabling encryption", self.enable_server_side_encryption),
            ("Blocking public access", self.block_public_access),
            ("Configuring lifecycle rules", self.configure_lifecycle_rules),
            ("Configuring CORS", self.configure_cors),
            ("Configuring bucket policy", self.configure_bucket_policy),
            ("Enabling access logging", self.enable_logging),
        ]

        results = []
        for step_name, step_func in steps:
            try:
                result = step_func()
                results.append((step_name, result))
            except Exception as e:
                print(f"✗ Unexpected error during {step_name}: {e}")
                results.append((step_name, False))

        # Display summary
        self.display_bucket_info()

        # Print final summary
        successful = sum(1 for _, result in results if result)
        total = len(results)

        print("\n" + "="*60)
        print(f"SETUP COMPLETE: {successful}/{total} steps successful")
        print("="*60)

        if successful == total:
            print("\n✓ S3 bucket fully configured and ready for use!")
            return 0
        else:
            print("\n✗ Some steps failed. Please review the errors above.")
            return 1


def main():
    """Main entry point"""
    region = os.getenv('AWS_REGION', 'eu-west-1')

    # Validate AWS credentials
    if not os.getenv('AWS_ACCESS_KEY_ID'):
        print("Error: AWS_ACCESS_KEY_ID environment variable not set")
        sys.exit(1)

    if not os.getenv('AWS_SECRET_ACCESS_KEY'):
        print("Error: AWS_SECRET_ACCESS_KEY environment variable not set")
        sys.exit(1)

    setup = S3BucketSetup(region=region)
    sys.exit(setup.setup())


if __name__ == '__main__':
    main()
