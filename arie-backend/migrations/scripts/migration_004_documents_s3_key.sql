-- Migration 004: Add s3_key column to documents table
-- Tracks the S3 object key for documents stored in S3.
-- Nullable: NULL means local-only storage (pre-S3 or S3 upload failed).

ALTER TABLE documents ADD COLUMN s3_key TEXT;
