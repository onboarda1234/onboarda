-- Async application screening queue.
--
-- Submit-time ComplyAdvantage screening is deferred to the worker so the
-- client submit path can persist durable pricing/submitted state before any
-- long provider polling.  Fresh schemas include this table in db.py; this
-- migration repairs existing deployments.

CREATE TABLE IF NOT EXISTS screening_jobs (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    submit_attempt_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'complyadvantage',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','in_progress','retrying','succeeded','failed','cancelled')),
    priority INTEGER NOT NULL DEFAULT 100,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    run_after TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    locked_by TEXT,
    locked_at TIMESTAMP,
    last_error TEXT,
    job_metadata JSONB DEFAULT '{}',
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_screening_jobs_status_run_after
    ON screening_jobs(status, run_after, priority, created_at);

CREATE INDEX IF NOT EXISTS idx_screening_jobs_application
    ON screening_jobs(application_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_screening_jobs_active_application
    ON screening_jobs(application_id)
    WHERE status IN ('pending','retrying','in_progress');
