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

-- Composite parent identities make it impossible to assemble one binding from
-- otherwise valid IDs belonging to different uploads, requests, customers or
-- documents.  Each leading ID is already unique; these indexes expose the
-- complete authoritative tuples to both PostgreSQL and SQLite foreign keys.
CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_app_customer
    ON applications(id, client_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_upload_identity
    ON monitoring_document_renewal_uploads(
        upload_id, request_id, application_id, customer_id
    );
CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_request_identity
    ON monitoring_document_renewal_requests(
        request_id, application_id, customer_id, document_id,
        document_version, document_type
    );
CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_request_person
    ON monitoring_document_renewal_requests(request_id, person_id, person_type);
CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_document_identity
    ON documents(id, application_id, doc_type, version);
CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_document_person
    ON documents(id, application_id, person_id, person_type);

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
    CHECK(length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(binding_fingerprint, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    FOREIGN KEY(application_id, customer_id)
        REFERENCES applications(id, client_id) ON DELETE RESTRICT,
    FOREIGN KEY(upload_id, renewal_request_id, application_id, customer_id)
        REFERENCES monitoring_document_renewal_uploads(
            upload_id, request_id, application_id, customer_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(
        renewal_request_id, application_id, customer_id,
        original_document_id, original_document_version, document_type
    ) REFERENCES monitoring_document_renewal_requests(
        request_id, application_id, customer_id,
        document_id, document_version, document_type
    ) ON DELETE RESTRICT,
    FOREIGN KEY(renewal_request_id, person_id, person_type)
        REFERENCES monitoring_document_renewal_requests(
            request_id, person_id, person_type
        ) ON DELETE RESTRICT,
    FOREIGN KEY(
        original_document_id, application_id, document_type,
        original_document_version
    ) REFERENCES documents(id, application_id, doc_type, version)
        ON DELETE RESTRICT,
    FOREIGN KEY(original_document_id, application_id, person_id, person_type)
        REFERENCES documents(id, application_id, person_id, person_type)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_binding_application
    ON monitoring_document_renewal_upload_bindings(application_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_binding_customer
    ON monitoring_document_renewal_upload_bindings(customer_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_binding_document
    ON monitoring_document_renewal_upload_bindings(original_document_id);

-- The consolidated startup schema additionally installs an engine-specific,
-- null-safe INSERT/UPDATE identity trigger.  It proves the person/entity pair,
-- upload timestamp and uploader against the joined request/upload/document
-- records.  The trigger is required because SQL composite foreign keys use
-- MATCH SIMPLE semantics and therefore cannot prove a nullable person tuple.
-- Production startup always converges the PostgreSQL trigger before serving;
-- the SQLite schema installs the equivalent trigger for focused tests.

-- Rollback is schema-only and is permitted only while this table is empty:
--
--   DROP TABLE IF EXISTS monitoring_document_renewal_upload_bindings;
--
-- Once a binding exists it is regulated evidence and must be retained through
-- the approved retention / erasure process.  It must never be broadly deleted
-- to roll back application code.
