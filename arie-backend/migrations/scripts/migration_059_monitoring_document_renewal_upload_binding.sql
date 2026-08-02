-- Migration 059: authoritative Monitoring document-renewal upload binding.
-- ========================================================================
-- The staged upload remains separate from KYC & Documents.  This table binds
-- one staged candidate to the exact audited renewal-request identity without
-- inserting, replacing, verifying, or otherwise mutating a documents row.
--
-- This migration is additive and deliberately performs no backfill.  A legacy
-- upload_received request without a binding is therefore surfaced as manual
-- review by the application service instead of being guessed into this
-- contract.

CREATE TABLE IF NOT EXISTS monitoring_document_renewal_upload_bindings (
    upload_id TEXT PRIMARY KEY
        REFERENCES monitoring_document_renewal_uploads(upload_id) ON DELETE RESTRICT,
    renewal_request_id TEXT NOT NULL UNIQUE
        REFERENCES monitoring_document_renewal_requests(request_id) ON DELETE RESTRICT,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE RESTRICT,
    customer_id TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    person_id TEXT,
    person_type TEXT,
    original_document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    original_document_version INTEGER NOT NULL CHECK(original_document_version >= 1),
    uploaded_document_id TEXT NOT NULL UNIQUE,
    document_type TEXT NOT NULL,
    upload_timestamp TEXT /* TIMESTAMP_ON_POSTGRES */ NOT NULL,
    uploaded_by TEXT NOT NULL,
    binding_status TEXT NOT NULL CHECK(binding_status = 'bound'),
    contract_version TEXT NOT NULL,
    binding_fingerprint TEXT NOT NULL,
    CHECK(
        (person_id IS NULL AND person_type IS NULL)
        OR (person_id IS NOT NULL AND person_type IS NOT NULL)
    ),
    CHECK(length(trim(original_document_id)) > 0),
    CHECK(length(trim(uploaded_document_id)) > 0),
    CHECK(uploaded_document_id = 'renewal-candidate:' || upload_id),
    CHECK(length(trim(document_type)) > 0),
    CHECK(length(trim(uploaded_by)) > 0),
    CHECK(contract_version = 'monitoring_document_renewal_upload_binding_v1'),
    CHECK(length(binding_fingerprint) = 64),
    CHECK(binding_fingerprint = lower(binding_fingerprint)),
    CHECK(length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(binding_fingerprint, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0)
);

CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_binding_application
    ON monitoring_document_renewal_upload_bindings(application_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_binding_customer
    ON monitoring_document_renewal_upload_bindings(customer_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_binding_document
    ON monitoring_document_renewal_upload_bindings(original_document_id);

-- Rollback is schema-only and is permitted only while this table is empty:
--
--   DROP TABLE IF EXISTS monitoring_document_renewal_upload_bindings;
--
-- Once a binding exists it is regulated evidence and must be retained through
-- the approved retention / erasure process.  It must never be broadly deleted
-- to roll back application code.
