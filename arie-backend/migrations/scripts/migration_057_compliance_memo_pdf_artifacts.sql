-- Migration 057: immutable canonical Compliance Memorandum PDF artefacts.
-- ======================================================================
-- Preview and download must serve the same regulated byte sequence.  This
-- additive table stores one draft and one final PDF per canonical memo row,
-- together with the SHA-256 and byte length used for response verification.
-- The unique key prevents a second artefact from being inserted for the same
-- memo lifecycle state.  Application code never updates these rows and fails
-- closed if stored bytes do not match their persisted hash or length.
--
-- Fresh schemas create the same table directly in db.py.  DBConnection
-- rewrites AUTOINCREMENT to SERIAL for PostgreSQL; BYTEA and TIMESTAMP are
-- accepted by both supported databases.

CREATE TABLE IF NOT EXISTS compliance_memo_pdf_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memo_id INTEGER NOT NULL REFERENCES compliance_memos(id) ON DELETE CASCADE,
    artifact_state TEXT NOT NULL CHECK(artifact_state IN ('draft','final')),
    pdf_bytes BYTEA NOT NULL,
    pdf_sha256 TEXT NOT NULL,
    byte_length INTEGER NOT NULL,
    renderer_build_sha TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(memo_id, artifact_state)
);

CREATE INDEX IF NOT EXISTS idx_compliance_memo_pdf_artifacts_memo
    ON compliance_memo_pdf_artifacts(memo_id);

-- Narrow rollback (operator-run only, after preserving any regulated memo
-- artefacts in the evidence pack and confirming no deployed code reads them):
--
--   DROP TABLE IF EXISTS compliance_memo_pdf_artifacts;
--
-- Rollback does not modify compliance_memos or any application workflow row.
