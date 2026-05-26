-- Migration 010: EDD Active-Memo Integration (PR-04)
-- ===================================================
-- Adds the minimum additive schema required to:
--
--   1. capture STRUCTURED EDD findings per edd_case (one row per
--      edd_case_id, upserted by the application layer), and
--   2. ATTACH those findings to an explicit memo context
--      (onboarding memo or periodic-review memo) without mutating
--      ``compliance_memos`` history and without creating a third,
--      disconnected EDD memo universe.
--
-- SCOPE: additive only.
--   * No column added to or altered on ``compliance_memos``.
--   * No column added to or altered on ``edd_cases``.
--   * No column added to or altered on ``periodic_reviews``.
--   * No FKs (soft references only -- mirrors the PR-01/PR-02/PR-03
--     contract for SQLite/PostgreSQL parity and to avoid schema
--     ordering surprises).
--   * No CHECK constraints on enum-shaped columns (SQLite cannot add
--     CHECK via ALTER TABLE; the application layer enforces vocabulary
--     -- see arie-backend/edd_memo_integration.py).
--
-- DIALECT: CREATE TABLE / CREATE INDEX with IF NOT EXISTS is portable
-- across SQLite (>=3.2) and PostgreSQL (>=9.x). No ALTER TABLE.
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
-- per-version). PR-01 and PR-03 deliberately did not add a memo
-- pointer to lifecycle rows because memo identity needed care. PR-04
-- adds an explicit ``edd_memo_attachments`` row to record which memo
-- context an EDD case feeds into:
--
--   * memo_context_kind = 'onboarding'      -> attaches to compliance_memos.id
--   * memo_context_kind = 'periodic_review' -> attaches to periodic_reviews.id
--
-- This keeps the onboarding memo history immutable (no rewrite, no
-- overwrite) while making the EDD-to-memo-context relationship
-- explicit, structured and auditable. Findings are stored separately
-- on ``edd_findings`` so the same structured payload can be referenced
-- from either context without duplication.

-- ── EDD findings (structured payload, one row per EDD case) ──────
CREATE TABLE IF NOT EXISTS edd_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edd_case_id INTEGER NOT NULL UNIQUE,
    findings_summary TEXT,
    key_concerns TEXT DEFAULT '[]',          -- JSON array of strings
    mitigating_evidence TEXT DEFAULT '[]',   -- JSON array of strings
    conditions TEXT DEFAULT '[]',            -- JSON array of strings
    rationale TEXT,
    supporting_notes TEXT DEFAULT '[]',      -- JSON array of {ref, note}
    recommended_outcome TEXT,                -- application-layer enum
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_edd_findings_edd_case_id
    ON edd_findings(edd_case_id);


-- ── EDD-to-memo-context attachments (linkage table) ──────────────
-- An attachment row asserts: "the structured findings for this
-- edd_case_id are part of the decision artifact identified by
-- (memo_context_kind, memo_id, periodic_review_id)".
--
-- For memo_context_kind='onboarding':
--   memo_id is the compliance_memos.id (may be NULL if no onboarding
--   memo exists yet -- the attachment still records intent and the
--   linkage will be resolvable as soon as the onboarding memo is
--   generated). periodic_review_id is NULL.
--
-- For memo_context_kind='periodic_review':
--   periodic_review_id is the periodic_reviews.id. memo_id is NULL
--   today (PR-04 deliberately does not introduce a separate
--   periodic_review_memos table -- the periodic review row IS the
--   review memo context). A future PR may promote the review memo to
--   its own row, at which point this column becomes the FK target.
--
-- application_id is denormalized for cheap lookups by application.
CREATE TABLE IF NOT EXISTS edd_memo_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edd_case_id INTEGER NOT NULL,
    application_id TEXT NOT NULL,
    memo_context_kind TEXT NOT NULL,        -- 'onboarding' | 'periodic_review'
    memo_id INTEGER,                        -- soft-ref compliance_memos.id (onboarding)
    periodic_review_id INTEGER,             -- soft-ref periodic_reviews.id (review)
    attached_by TEXT,
    attached_at TEXT DEFAULT (datetime('now')),
    detached_at TEXT,
    detached_by TEXT
);

-- Lookup indexes ---------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_edd_case
    ON edd_memo_attachments(edd_case_id);
CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_app
    ON edd_memo_attachments(application_id);
CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_kind
    ON edd_memo_attachments(memo_context_kind);
CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_memo
    ON edd_memo_attachments(memo_id);
CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_review
    ON edd_memo_attachments(periodic_review_id);
