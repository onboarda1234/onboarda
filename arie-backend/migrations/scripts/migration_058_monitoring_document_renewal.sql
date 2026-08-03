-- Migration 058: canonical Monitoring document-renewal request records.
-- =====================================================================
-- This migration is additive and deliberately contains no INSERT, UPDATE,
-- DELETE, or backfill.  The request identifies the canonical KYC document,
-- while uploads remain staged evidence: no row here replaces or verifies a
-- documents row and no Monitoring Alert status is changed.
--
-- The DBConnection dialect markers preserve TEXT on SQLite and install native
-- TIMESTAMP / JSONB columns on PostgreSQL.  All parent references use RESTRICT
-- so regulated renewal evidence cannot disappear through cascade cleanup.

CREATE TABLE IF NOT EXISTS monitoring_document_renewal_requests (
    request_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE RESTRICT,
    customer_id TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    person_id TEXT,
    person_type TEXT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    document_type TEXT NOT NULL,
    document_slot_key TEXT NOT NULL,
    document_version INTEGER NOT NULL CHECK(document_version >= 1),
    monitoring_alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id) ON DELETE RESTRICT,
    request_reason TEXT NOT NULL CHECK(request_reason IN ('expired','expiring_soon','manual')),
    request_status TEXT NOT NULL CHECK(request_status IN ('created','awaiting_upload','upload_received','cancelled')),
    created_at TEXT /* TIMESTAMP_ON_POSTGRES */ NOT NULL DEFAULT (datetime('now')),
    due_date TEXT /* TIMESTAMP_ON_POSTGRES */ NOT NULL,
    created_by TEXT NOT NULL,
    sent_at TEXT /* TIMESTAMP_ON_POSTGRES */,
    cancelled_at TEXT /* TIMESTAMP_ON_POSTGRES */,
    cancelled_by TEXT,
    cancel_reason TEXT,
    updated_at TEXT /* TIMESTAMP_ON_POSTGRES */ NOT NULL DEFAULT (datetime('now')),
    updated_by TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    contract_version TEXT NOT NULL,
    eligibility_fingerprint TEXT NOT NULL,
    CHECK(
        (person_id IS NULL AND person_type IS NULL)
        OR (person_id IS NOT NULL AND person_type IS NOT NULL)
    ),
    CHECK(
        request_status NOT IN ('awaiting_upload','upload_received')
        OR sent_at IS NOT NULL
    ),
    CHECK(
        (
            request_status = 'cancelled'
            AND cancelled_at IS NOT NULL
            AND cancelled_by IS NOT NULL
            AND cancel_reason IS NOT NULL
            AND length(trim(cancel_reason)) > 0
        )
        OR (
            request_status <> 'cancelled'
            AND cancelled_at IS NULL
            AND cancelled_by IS NULL
            AND cancel_reason IS NULL
        )
    ),
    CHECK(length(trim(document_type)) > 0),
    CHECK(length(trim(document_slot_key)) > 0),
    CHECK(length(trim(created_by)) > 0),
    CHECK(length(trim(contract_version)) > 0),
    CHECK(length(eligibility_fingerprint) = 64),
    CHECK(eligibility_fingerprint = lower(eligibility_fingerprint))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_active_identity
    ON monitoring_document_renewal_requests(
        application_id,
        COALESCE(person_id, ''),
        document_slot_key
    )
    WHERE request_status <> 'cancelled';

CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_active_alert
    ON monitoring_document_renewal_requests(monitoring_alert_id)
    WHERE request_status <> 'cancelled';

CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_alert
    ON monitoring_document_renewal_requests(monitoring_alert_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_application
    ON monitoring_document_renewal_requests(application_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_customer
    ON monitoring_document_renewal_requests(customer_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_status
    ON monitoring_document_renewal_requests(request_status);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_due
    ON monitoring_document_renewal_requests(due_date);

CREATE TABLE IF NOT EXISTS monitoring_document_renewal_events (
    event_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES monitoring_document_renewal_requests(request_id) ON DELETE RESTRICT,
    event_sequence INTEGER NOT NULL CHECK(event_sequence > 0),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'request_created','request_sent','reminder_generated','request_resent',
        'due_date_changed','upload_received','request_cancelled'
    )),
    event_key TEXT NOT NULL UNIQUE,
    payload TEXT /* JSONB_ON_POSTGRES */ NOT NULL DEFAULT '{}',
    created_at TEXT /* TIMESTAMP_ON_POSTGRES */ NOT NULL DEFAULT (datetime('now')),
    created_by TEXT NOT NULL,
    due_date_snapshot TEXT /* TIMESTAMP_ON_POSTGRES */,
    reminder_interval_days INTEGER CHECK(reminder_interval_days IS NULL OR reminder_interval_days > 0),
    CHECK(
        (event_type = 'reminder_generated' AND reminder_interval_days IS NOT NULL)
        OR (event_type <> 'reminder_generated' AND reminder_interval_days IS NULL)
    ),
    CHECK(length(trim(created_by)) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_event_sequence
    ON monitoring_document_renewal_events(request_id, event_sequence);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_events_request_time
    ON monitoring_document_renewal_events(request_id, created_at);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_events_time
    ON monitoring_document_renewal_events(created_at);

CREATE TABLE IF NOT EXISTS monitoring_document_renewal_uploads (
    upload_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES monitoring_document_renewal_requests(request_id) ON DELETE RESTRICT,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE RESTRICT,
    customer_id TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    original_filename TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    file_size INTEGER NOT NULL CHECK(file_size >= 0),
    mime_type TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    uploaded_at TEXT /* TIMESTAMP_ON_POSTGRES */ NOT NULL DEFAULT (datetime('now')),
    uploaded_by TEXT NOT NULL,
    CHECK(length(trim(original_filename)) > 0),
    CHECK(length(trim(storage_key)) > 0),
    CHECK(length(file_sha256) = 64),
    CHECK(file_sha256 = lower(file_sha256)),
    CHECK(length(trim(uploaded_by)) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_uploads_request
    ON monitoring_document_renewal_uploads(request_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_uploads_application
    ON monitoring_document_renewal_uploads(application_id);

-- Durable lifecycle and retry evidence reserved before candidate bytes are
-- written. It covers interrupted upload/attachment as well as post-store
-- recording failures, and never points at canonical KYC documents.
CREATE TABLE IF NOT EXISTS monitoring_document_renewal_upload_cleanup (
    cleanup_id TEXT PRIMARY KEY,
    upload_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL REFERENCES monitoring_document_renewal_requests(request_id) ON DELETE RESTRICT,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE RESTRICT,
    customer_id TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    backend TEXT NOT NULL CHECK(backend IN ('s3','local')),
    storage_key TEXT NOT NULL,
    file_extension TEXT NOT NULL,
    local_path TEXT,
    s3_version_id TEXT,
    artifact_state TEXT NOT NULL DEFAULT 'reserved' CHECK(artifact_state IN (
        'reserved','stored','attached','cleanup_pending','cleaned'
    )),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    last_error_code TEXT,
    correlation_id TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT /* TIMESTAMP_ON_POSTGRES */,
    next_retry_at TEXT /* TIMESTAMP_ON_POSTGRES */,
    created_at TEXT /* TIMESTAMP_ON_POSTGRES */ NOT NULL DEFAULT (datetime('now')),
    stored_at TEXT /* TIMESTAMP_ON_POSTGRES */,
    attached_at TEXT /* TIMESTAMP_ON_POSTGRES */,
    cleaned_at TEXT /* TIMESTAMP_ON_POSTGRES */,
    updated_at TEXT /* TIMESTAMP_ON_POSTGRES */ NOT NULL DEFAULT (datetime('now')),
    CHECK(length(trim(storage_key)) > 0),
    CHECK(file_extension = '' OR substr(file_extension, 1, 1) = '.'),
    CHECK(length(file_extension) <= 21),
    CHECK(file_extension = lower(file_extension)),
    CHECK(
        (backend = 'local' AND local_path IS NOT NULL AND length(trim(local_path)) > 0 AND s3_version_id IS NULL)
        OR (backend = 's3' AND local_path IS NULL)
    ),
    CHECK(
        (artifact_state = 'reserved' AND stored_at IS NULL AND attached_at IS NULL AND cleaned_at IS NULL)
        OR (artifact_state = 'stored' AND stored_at IS NOT NULL AND attached_at IS NULL AND cleaned_at IS NULL)
        OR (artifact_state = 'cleanup_pending' AND attached_at IS NULL AND cleaned_at IS NULL)
        OR (artifact_state = 'attached' AND stored_at IS NOT NULL AND attached_at IS NOT NULL AND cleaned_at IS NULL)
        OR (artifact_state = 'cleaned' AND attached_at IS NULL AND cleaned_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_doc_renewal_cleanup_storage
    ON monitoring_document_renewal_upload_cleanup(backend, storage_key);
CREATE INDEX IF NOT EXISTS idx_monitoring_doc_renewal_cleanup_pending
    ON monitoring_document_renewal_upload_cleanup(artifact_state, next_retry_at, created_at);

-- Operational fairness checkpoint. This contains no customer data and lets a
-- bounded scheduler advance past permanently blocked rows instead of starving
-- later eligible work.
CREATE TABLE IF NOT EXISTS monitoring_document_renewal_scheduler_state (
    scheduler_key TEXT PRIMARY KEY CHECK(scheduler_key IN ('eligibility','reminders')),
    cursor_primary TEXT,
    cursor_secondary TEXT,
    updated_at TEXT /* TIMESTAMP_ON_POSTGRES */ NOT NULL DEFAULT (datetime('now'))
);

-- Narrow operator rollback (only before runtime rows exist):
--
--   DROP TABLE IF EXISTS monitoring_document_renewal_upload_cleanup;
--   DROP TABLE IF EXISTS monitoring_document_renewal_uploads;
--   DROP TABLE IF EXISTS monitoring_document_renewal_events;
--   DROP TABLE IF EXISTS monitoring_document_renewal_requests;
--   DROP TABLE IF EXISTS monitoring_document_renewal_scheduler_state;
--
-- Once a request exists these regulated records must be preserved through the
-- approved retention / erasure process; rollback must not hard-delete them.
