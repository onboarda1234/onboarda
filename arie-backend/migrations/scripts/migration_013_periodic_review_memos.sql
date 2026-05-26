-- Migration 013: Lightweight Periodic Review Memo Artifact (PR-D)
-- ================================================================
-- Adds a single additive table, ``periodic_review_memos``, capturing a
-- deterministic, template-driven reassessment artifact generated AFTER
-- a periodic review's outcome is recorded. The memo is distinct from
-- the onboarding memo (``compliance_memos``) and from the EDD memo
-- context resolver (``edd_memo_integration``); there is no FK and no
-- cross-write between this table and any existing table.
--
-- SCOPE: additive only.
--   * No column added to or altered on ``compliance_memos``.
--   * No column added to or altered on ``periodic_reviews``.
--   * No column added to or altered on ``edd_cases`` or
--     ``edd_memo_attachments``.
--   * No FKs (soft references only -- mirrors the PR-01/PR-02/PR-03/
--     PR-04 contract for SQLite/PostgreSQL parity and to avoid schema
--     ordering surprises).
--   * No CHECK constraints on enum-shaped columns (SQLite cannot add
--     CHECK via ALTER TABLE; the application layer enforces vocabulary
--     -- see arie-backend/periodic_review_memo.py).
--
-- DIALECT: CREATE TABLE / CREATE INDEX with IF NOT EXISTS is portable
-- across SQLite (>=3.2) and PostgreSQL (>=9.x). INTEGER PRIMARY KEY
-- AUTOINCREMENT is translated to SERIAL PRIMARY KEY by the migration
-- runner's ``_translate_query`` on PostgreSQL. No ALTER TABLE.
--
-- IDEMPOTENCY: provided by the migration runner via schema_version.
-- The CREATE TABLE IF NOT EXISTS clauses are additionally idempotent
-- for safety (mirrors the convention used elsewhere in this repo).
--
-- EX-CONTROL IMPACT: none. No file in PROTECTED_FILES is modified by
-- this migration. No existing column is altered. No existing row is
-- mutated. EX-01..EX-13 regressions are impossible by construction.
--
-- ARTIFACT-SEPARATION RATIONALE
-- -----------------------------
-- ``compliance_memos`` is the onboarding memo identity (per-application
-- per-version). A periodic review produces a different artifact shape
-- (9 structured sections summarising *why this review exists, what was
-- examined, what the outcome is*). Co-locating the two would conflate
-- two independent lifecycles and force onboarding memo history to
-- mutate when a review completes.
--
-- ``memo_context`` is always the literal JSON ``{"kind":
-- "periodic_review"}``. It is stored for semantic separation from
-- onboarding memos and future extensibility, NOT because PR-D extends
-- EDD memo-context resolution. The ``edd_memo_integration`` resolver
-- does not consult ``periodic_review_memos`` in PR-D.

-- ── Periodic review memos (lightweight reassessment artifact) ────
CREATE TABLE IF NOT EXISTS periodic_review_memos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    periodic_review_id INTEGER NOT NULL,      -- soft-ref periodic_reviews.id
    application_id TEXT,                      -- matches applications.id type
    version INTEGER NOT NULL DEFAULT 1,
    memo_data TEXT NOT NULL,                  -- JSON (9-section memo payload)
    memo_context TEXT NOT NULL,               -- JSON: {"kind":"periodic_review"}
    generated_at TEXT DEFAULT (datetime('now')),
    generated_by TEXT NOT NULL,               -- system actor string
    status TEXT NOT NULL DEFAULT 'generated', -- 'generated' | 'generation_failed'
    UNIQUE(periodic_review_id, version)
);

-- Lookup indexes --------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_prm_review
    ON periodic_review_memos(periodic_review_id);
CREATE INDEX IF NOT EXISTS idx_prm_app
    ON periodic_review_memos(application_id);
