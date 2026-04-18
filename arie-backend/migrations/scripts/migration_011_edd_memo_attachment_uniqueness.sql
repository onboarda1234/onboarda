-- Migration 011: EDD memo-attachment uniqueness backstop (PR-04a)
-- ================================================================
-- PR-04a hardening: enforce one ACTIVE attachment per
-- (edd_case_id, memo_context_kind, memo_id, periodic_review_id) identity
-- as a schema-level backstop in addition to the application-layer rule
-- in arie-backend/edd_memo_integration.py
-- (`attach_edd_findings_to_memo_context`).
--
-- WHY
-- ---
-- PR-04 validation surfaced an artifact-model gap: nothing physically
-- stopped two ACTIVE rows for the same EDD under the same context key.
-- This index closes the door at the schema level so that even under
-- concurrent calls only one active row can exist per identity.
--
-- NULL-SAFE IDENTITY
-- ------------------
-- SQLite and PostgreSQL both treat NULLs in unique indexes as
-- distinct (PG <15 does, and PG >=15 also keeps the default behaviour
-- unless NULLS NOT DISTINCT is specified). To get portable NULL-safe
-- uniqueness we COALESCE the nullable identity columns to a sentinel
-- value (0). Both ``memo_id`` and ``periodic_review_id`` are AUTOINCREMENT
-- / SERIAL surrogate ids that start at 1, so 0 is a safe NULL sentinel.
--
-- PARTIAL INDEX
-- -------------
-- ``WHERE detached_at IS NULL`` scopes the constraint to ACTIVE rows
-- only. Detached (soft-deleted) attachments are intentionally allowed
-- to coexist for audit history -- they are not considered for the
-- "one active attachment per identity" rule.
--
-- DIALECT
-- -------
-- * SQLite >= 3.9 supports expression indexes and partial indexes.
-- * PostgreSQL has supported both for a long time.
-- * No ALTER TABLE; no existing column changed; no row mutated.
--
-- SCOPE
-- -----
-- * Additive only.
-- * No protected file is modified by this migration.
-- * No EX-01..EX-13 control critical file is touched.
-- * `compliance_memos`, `edd_cases`, `periodic_reviews` are unchanged.

CREATE UNIQUE INDEX IF NOT EXISTS uix_edd_memo_attachments_active_identity
    ON edd_memo_attachments (
        edd_case_id,
        memo_context_kind,
        COALESCE(memo_id, 0),
        COALESCE(periodic_review_id, 0)
    )
    WHERE detached_at IS NULL;
