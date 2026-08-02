"""Database guards for the document-renewal upload-binding contract.

The binding identity is enforced by one dialect-aware trigger contract rather
than redundant composite indexes on the protected Applications and Documents
tables.  This keeps migration 059 from blocking writes while still rejecting
every cross-wired binding at the database boundary.
"""


_POSTGRES_GUARD_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION monitoring_document_renewal_binding_identity_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM monitoring_document_renewal_uploads u
          JOIN monitoring_document_renewal_requests r
            ON r.request_id = u.request_id
           AND r.application_id = u.application_id
           AND r.customer_id = u.customer_id
          JOIN applications a
            ON a.id = r.application_id
           AND a.client_id = r.customer_id
          JOIN documents d
            ON d.id = r.document_id
           AND d.application_id = r.application_id
         WHERE u.upload_id = NEW.upload_id
           AND r.request_id = NEW.renewal_request_id
           AND r.application_id = NEW.application_id
           AND r.customer_id = NEW.customer_id
           AND r.person_id IS NOT DISTINCT FROM NEW.person_id
           AND r.person_type IS NOT DISTINCT FROM NEW.person_type
           AND r.document_id = NEW.original_document_id
           AND r.document_version = NEW.original_document_version
           AND r.document_type = NEW.document_type
           AND d.person_id IS NOT DISTINCT FROM NEW.person_id
           AND d.person_type IS NOT DISTINCT FROM NEW.person_type
           AND d.doc_type = NEW.document_type
           AND d.version = NEW.original_document_version
           AND u.uploaded_at = NEW.upload_timestamp
           AND u.uploaded_by = NEW.uploaded_by
    ) THEN
        RAISE EXCEPTION 'monitoring renewal upload binding identity mismatch'
            USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_POSTGRES_GUARD_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS trg_monitoring_doc_renewal_binding_identity
    ON monitoring_document_renewal_upload_bindings;
CREATE TRIGGER trg_monitoring_doc_renewal_binding_identity
BEFORE INSERT OR UPDATE ON monitoring_document_renewal_upload_bindings
FOR EACH ROW
EXECUTE FUNCTION monitoring_document_renewal_binding_identity_guard()
"""

_SQLITE_GUARD_SQL = """
DROP TRIGGER IF EXISTS trg_monitoring_doc_renewal_binding_identity_insert;
DROP TRIGGER IF EXISTS trg_monitoring_doc_renewal_binding_identity_update;
CREATE TRIGGER trg_monitoring_doc_renewal_binding_identity_insert
BEFORE INSERT ON monitoring_document_renewal_upload_bindings
WHEN NOT EXISTS (
    SELECT 1
      FROM monitoring_document_renewal_uploads u
      JOIN monitoring_document_renewal_requests r
        ON r.request_id = u.request_id
       AND r.application_id = u.application_id
       AND r.customer_id = u.customer_id
      JOIN applications a
        ON a.id = r.application_id
       AND a.client_id = r.customer_id
      JOIN documents d
        ON d.id = r.document_id
       AND d.application_id = r.application_id
     WHERE u.upload_id = NEW.upload_id
       AND r.request_id = NEW.renewal_request_id
       AND r.application_id = NEW.application_id
       AND r.customer_id = NEW.customer_id
       AND r.person_id IS NEW.person_id
       AND r.person_type IS NEW.person_type
       AND r.document_id = NEW.original_document_id
       AND r.document_version = NEW.original_document_version
       AND r.document_type = NEW.document_type
       AND d.person_id IS NEW.person_id
       AND d.person_type IS NEW.person_type
       AND d.doc_type = NEW.document_type
       AND d.version = NEW.original_document_version
       AND u.uploaded_at = NEW.upload_timestamp
       AND u.uploaded_by = NEW.uploaded_by
)
BEGIN
    SELECT RAISE(ABORT, 'monitoring renewal upload binding identity mismatch');
END;

CREATE TRIGGER trg_monitoring_doc_renewal_binding_identity_update
BEFORE UPDATE ON monitoring_document_renewal_upload_bindings
WHEN NOT EXISTS (
    SELECT 1
      FROM monitoring_document_renewal_uploads u
      JOIN monitoring_document_renewal_requests r
        ON r.request_id = u.request_id
       AND r.application_id = u.application_id
       AND r.customer_id = u.customer_id
      JOIN applications a
        ON a.id = r.application_id
       AND a.client_id = r.customer_id
      JOIN documents d
        ON d.id = r.document_id
       AND d.application_id = r.application_id
     WHERE u.upload_id = NEW.upload_id
       AND r.request_id = NEW.renewal_request_id
       AND r.application_id = NEW.application_id
       AND r.customer_id = NEW.customer_id
       AND r.person_id IS NEW.person_id
       AND r.person_type IS NEW.person_type
       AND r.document_id = NEW.original_document_id
       AND r.document_version = NEW.original_document_version
       AND r.document_type = NEW.document_type
       AND d.person_id IS NEW.person_id
       AND d.person_type IS NEW.person_type
       AND d.doc_type = NEW.document_type
       AND d.version = NEW.original_document_version
       AND u.uploaded_at = NEW.upload_timestamp
       AND u.uploaded_by = NEW.uploaded_by
)
BEGIN
    SELECT RAISE(ABORT, 'monitoring renewal upload binding identity mismatch');
END;
"""


def ensure_monitoring_document_renewal_upload_binding_guard(db):
    """Install the version-1 tuple guard without committing the caller's work.

    The helper is intentionally shared by consolidated startup and the
    migration-059 runner hook. A failure leaves version 059 unrecorded and
    runtime startup fail-closed. PostgreSQL rolls the migration back atomically;
    SQLite may retain idempotent DDL from ``executescript`` and converges on a
    retry. Callers own the final commit/rollback.
    """

    if getattr(db, "is_postgres", False):
        db.executescript(_POSTGRES_GUARD_FUNCTION_SQL)
        # Recreate instead of trusting the name of a pre-existing trigger. A
        # disabled, statement-level or INSERT-only trigger must never be
        # accepted as the authoritative INSERT+UPDATE row guard. This locks
        # only the additive binding table, not Applications or Documents.
        db.executescript(_POSTGRES_GUARD_TRIGGER_SQL)
        return

    db.executescript(_SQLITE_GUARD_SQL)
    rows = db.execute(
        """SELECT name, sql
             FROM sqlite_master
            WHERE type = 'trigger'
              AND name IN (?, ?)""",
        (
            "trg_monitoring_doc_renewal_binding_identity_insert",
            "trg_monitoring_doc_renewal_binding_identity_update",
        ),
    ).fetchall()
    found = {str(row.get("name") or ""): str(row.get("sql") or "") for row in rows}
    expected = {
        "trg_monitoring_doc_renewal_binding_identity_insert": "before insert",
        "trg_monitoring_doc_renewal_binding_identity_update": "before update",
    }
    for name, operation in expected.items():
        normalized = " ".join(found.get(name, "").lower().split())
        required = (
            operation,
            "monitoring_document_renewal_uploads",
            "monitoring_document_renewal_requests",
            "join applications",
            "join documents",
            "u.uploaded_at = new.upload_timestamp",
            "u.uploaded_by = new.uploaded_by",
        )
        if not all(token in normalized for token in required):
            raise RuntimeError(
                "monitoring renewal binding trigger is missing or unexpected: "
                + name
            )
