"""Schema contracts for the staged Monitoring document-renewal workflow.

The request, event, upload, and cleanup tables are additive regulated-evidence
stores; the fifth table is an operational fairness cursor. They never backfill
or mutate ``documents`` / ``monitoring_alerts`` and their parent foreign keys
are deliberately RESTRICT rather than cascade-delete.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from tests._migration_idempotency_helpers import fresh_migration_db


MIGRATION_PATH = (
    BACKEND_ROOT
    / "migrations"
    / "scripts"
    / "migration_058_monitoring_document_renewal.sql"
)

REQUEST_COLUMNS = """
    request_id, application_id, customer_id, person_id, person_type,
    document_id, document_type, document_slot_key, document_version, monitoring_alert_id,
    request_reason, request_status, due_date, created_by, sent_at,
    cancelled_at, cancelled_by, cancel_reason, contract_version,
    eligibility_fingerprint
"""


@pytest.fixture
def renewal_db(tmp_path, monkeypatch):
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        # The application deliberately leaves SQLite FK enforcement to test /
        # development callers; production PostgreSQL enforces it natively.
        # Enable it here so the declared RESTRICT contract is exercised.
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            """INSERT INTO clients (id, email, password_hash, company_name)
               VALUES (?, ?, ?, ?)""",
            ("renewal-client", "renewal-schema@example.test", "hash", "Renewal Schema Ltd"),
        )
        db.execute(
            """INSERT INTO applications (id, ref, client_id, company_name)
               VALUES (?, ?, ?, ?)""",
            ("renewal-app", "ARF-RENEWAL-SCHEMA", "renewal-client", "Renewal Schema Ltd"),
        )
        for document_id, document_type in (
            ("renewal-doc-a", "passport"),
            ("renewal-doc-b", "proof_of_address"),
        ):
            db.execute(
                """INSERT INTO documents
                       (id, application_id, person_id, person_type, doc_type,
                        doc_name, file_path, version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document_id,
                    "renewal-app",
                    "director-1",
                    "director",
                    document_type,
                    f"{document_type}.pdf",
                    f"staged/{document_id}.pdf",
                    1,
                ),
            )
        for alert_id, source_reference in (
            (58001, "renewal-doc-a"),
            (58002, "renewal-doc-b"),
            (58003, "renewal-doc-b-manual"),
        ):
            db.execute(
                """INSERT INTO monitoring_alerts
                       (id, application_id, alert_type, severity, detected_by,
                        summary, source_reference)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    alert_id,
                    "renewal-app",
                    "document_expired",
                    "medium",
                    "schema_test",
                    "Document renewal schema test",
                    source_reference,
                ),
            )
        db.commit()
        yield db


def _insert_request(
    db,
    *,
    request_id="renewal-request-1",
    document_id="renewal-doc-a",
    document_type="passport",
    alert_id=58001,
    status="created",
    person_id="director-1",
    person_type="director",
    sent_at=None,
    cancelled_at=None,
    cancelled_by=None,
    cancel_reason=None,
    document_version=1,
    revision=1,
    fingerprint=None,
):
    db.execute(
        f"""INSERT INTO monitoring_document_renewal_requests ({REQUEST_COLUMNS}, revision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            request_id,
            "renewal-app",
            "renewal-client",
            person_id,
            person_type,
            document_id,
            document_type,
            f"person:director:director-1:{document_type}",
            document_version,
            alert_id,
            "expired",
            status,
            "2030-01-31T00:00:00Z",
            "officer-1",
            sent_at,
            cancelled_at,
            cancelled_by,
            cancel_reason,
            "monitoring_document_renewal_request_v1",
            fingerprint or ("a" * 64),
            revision,
        ),
    )


def _table_types(db, table):
    return {
        row["name"]: (row["type"] or "").upper()
        for row in db.execute(f"PRAGMA table_info({table})").fetchall()
    }


def test_fresh_sqlite_schema_has_exact_tables_types_and_empty_inventory(renewal_db):
    expected = {
        "monitoring_document_renewal_requests",
        "monitoring_document_renewal_events",
        "monitoring_document_renewal_uploads",
        "monitoring_document_renewal_upload_cleanup",
        "monitoring_document_renewal_scheduler_state",
    }
    present = {
        row["name"]
        for row in renewal_db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert expected <= present
    assert _table_types(renewal_db, "monitoring_document_renewal_requests")["created_at"] == "TEXT"
    assert _table_types(renewal_db, "monitoring_document_renewal_events")["payload"] == "TEXT"
    assert _table_types(renewal_db, "monitoring_document_renewal_uploads")["uploaded_at"] == "TEXT"
    assert _table_types(renewal_db, "monitoring_document_renewal_upload_cleanup")["lease_expires_at"] == "TEXT"
    assert _table_types(renewal_db, "monitoring_document_renewal_scheduler_state")["updated_at"] == "TEXT"
    for table in expected:
        assert renewal_db.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"] == 0


def test_request_constraints_and_person_pair_fail_closed(renewal_db):
    _insert_request(renewal_db)

    bad_cases = (
        {"request_id": "bad-version", "document_id": "renewal-doc-b", "document_type": "proof_of_address", "alert_id": 58002, "document_version": 0},
        {"request_id": "bad-revision", "document_id": "renewal-doc-b", "document_type": "proof_of_address", "alert_id": 58002, "revision": 0},
        {"request_id": "bad-person", "document_id": "renewal-doc-b", "document_type": "proof_of_address", "alert_id": 58002, "person_type": None},
        {"request_id": "bad-fingerprint", "document_id": "renewal-doc-b", "document_type": "proof_of_address", "alert_id": 58002, "fingerprint": "ABC"},
        {
            "request_id": "bad-status",
            "document_id": "renewal-doc-b",
            "document_type": "proof_of_address",
            "alert_id": 58002,
            "status": "verified",
        },
        {
            "request_id": "bad-awaiting",
            "document_id": "renewal-doc-b",
            "document_type": "proof_of_address",
            "alert_id": 58002,
            "status": "awaiting_upload",
        },
        {
            "request_id": "bad-cancelled",
            "document_id": "renewal-doc-b",
            "document_type": "proof_of_address",
            "alert_id": 58002,
            "status": "cancelled",
        },
    )
    for case in bad_cases:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_request(renewal_db, **case)

    with pytest.raises(sqlite3.IntegrityError):
        renewal_db.execute(
            f"""INSERT INTO monitoring_document_renewal_requests ({REQUEST_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "bad-reason",
                "renewal-app",
                "renewal-client",
                "director-1",
                "director",
                "renewal-doc-b",
                "proof_of_address",
                "person:director:director-1:proof_of_address",
                1,
                58002,
                "approximately_expired",
                "created",
                "2030-01-31",
                "officer-1",
                None,
                None,
                None,
                None,
                "monitoring_document_renewal_request_v1",
                "b" * 64,
            ),
        )


def test_partial_unique_active_identity_allows_new_request_only_after_cancel(renewal_db):
    _insert_request(renewal_db)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_request(
            renewal_db,
            request_id="renewal-request-duplicate",
            alert_id=58002,
        )

    renewal_db.execute(
        """UPDATE monitoring_document_renewal_requests
              SET request_status = 'cancelled',
                  cancelled_at = ?, cancelled_by = ?, cancel_reason = ?
            WHERE request_id = ?""",
        ("2029-12-01T00:00:00Z", "officer-2", "Superseded request", "renewal-request-1"),
    )
    _insert_request(
        renewal_db,
        request_id="renewal-request-after-cancel",
        alert_id=58002,
    )
    assert renewal_db.execute(
        "SELECT COUNT(*) AS c FROM monitoring_document_renewal_requests"
    ).fetchone()["c"] == 2


def test_partial_unique_active_alert_is_independent_of_document_identity(renewal_db):
    _insert_request(renewal_db)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_request(
            renewal_db,
            request_id="renewal-request-same-alert",
            document_id="renewal-doc-b",
            document_type="proof_of_address",
            alert_id=58001,
        )


def test_events_are_idempotent_typed_and_request_restricted(renewal_db):
    _insert_request(renewal_db)
    renewal_db.execute(
        """INSERT INTO monitoring_document_renewal_events
               (event_id, request_id, event_sequence, event_type, event_key, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("event-1", "renewal-request-1", 1, "request_created", "request-1:created", "officer-1"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        renewal_db.execute(
            """INSERT INTO monitoring_document_renewal_events
                   (event_id, request_id, event_sequence, event_type, event_key, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("event-2", "renewal-request-1", 2, "request_sent", "request-1:created", "officer-1"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        renewal_db.execute(
            """INSERT INTO monitoring_document_renewal_events
                   (event_id, request_id, event_sequence, event_type, event_key, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("event-3", "renewal-request-1", 2, "reminder_generated", "request-1:reminder", "scheduler"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        renewal_db.execute(
            """INSERT INTO monitoring_document_renewal_events
                   (event_id, request_id, event_sequence, event_type, event_key, created_by,
                    reminder_interval_days)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("event-4", "renewal-request-1", 3, "request_sent", "request-1:bad-reminder", "officer-1", 7),
        )
    with pytest.raises(sqlite3.IntegrityError):
        renewal_db.execute(
            """INSERT INTO monitoring_document_renewal_events
                   (event_id, request_id, event_sequence, event_type, event_key, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("event-5", "missing-request", 1, "request_created", "missing:created", "officer-1"),
        )


def test_uploads_are_staged_evidence_with_no_document_fk_or_verification_fields(renewal_db):
    _insert_request(renewal_db)
    renewal_db.execute(
        """INSERT INTO monitoring_document_renewal_uploads
               (upload_id, request_id, application_id, customer_id,
                original_filename, storage_key, file_size, mime_type,
                file_sha256, uploaded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "upload-1",
            "renewal-request-1",
            "renewal-app",
            "renewal-client",
            "replacement.pdf",
            "monitoring-renewals/renewal-request-1/upload-1.pdf",
            1234,
            "application/pdf",
            "b" * 64,
            "renewal-client",
        ),
    )
    columns = _table_types(renewal_db, "monitoring_document_renewal_uploads")
    assert "document_id" not in columns
    assert "verification_status" not in columns
    with pytest.raises(sqlite3.IntegrityError):
        renewal_db.execute(
            """INSERT INTO monitoring_document_renewal_uploads
                   (upload_id, request_id, application_id, customer_id,
                    original_filename, storage_key, file_size, mime_type,
                    file_sha256, uploaded_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "upload-negative",
                "renewal-request-1",
                "renewal-app",
                "renewal-client",
                "replacement.pdf",
                "monitoring-renewals/negative.pdf",
                -1,
                "application/pdf",
                "c" * 64,
                "renewal-client",
            ),
        )


def test_upload_cleanup_ledger_is_identity_bound_and_state_constrained(renewal_db):
    _insert_request(renewal_db)
    renewal_db.execute(
        """
        INSERT INTO monitoring_document_renewal_upload_cleanup
            (cleanup_id, upload_id, request_id, application_id, customer_id,
             backend, storage_key, file_extension, local_path)
        VALUES (?, ?, ?, ?, ?, 'local', ?, '.pdf', ?)
        """,
        (
            "cleanup-1",
            "a" * 32,
            "renewal-request-1",
            "renewal-app",
            "renewal-client",
            "monitoring-renewal-candidate/" + "a" * 32 + ".pdf",
            "/tmp/monitoring-renewal-candidates/" + "a" * 32 + ".pdf",
        ),
    )
    with pytest.raises(sqlite3.IntegrityError):
        renewal_db.execute(
            """
            INSERT INTO monitoring_document_renewal_upload_cleanup
                (cleanup_id, upload_id, request_id, application_id, customer_id,
                 backend, storage_key, file_extension, local_path,
                 artifact_state)
            VALUES (?, ?, ?, ?, ?, 'local', ?, '.PDF', ?, 'verified')
            """,
            (
                "cleanup-bad",
                "b" * 32,
                "renewal-request-1",
                "renewal-app",
                "renewal-client",
                "monitoring-renewal-candidate/" + "b" * 32 + ".PDF",
                "/tmp/monitoring-renewal-candidates/" + "b" * 32 + ".PDF",
            ),
        )
    foreign_keys = renewal_db.execute(
        "PRAGMA foreign_key_list(monitoring_document_renewal_upload_cleanup)"
    ).fetchall()
    assert {row["table"] for row in foreign_keys} == {
        "applications",
        "clients",
        "monitoring_document_renewal_requests",
    }
    assert {row["on_delete"].upper() for row in foreign_keys} == {"RESTRICT"}


def test_parent_foreign_keys_restrict_hard_delete(renewal_db):
    _insert_request(renewal_db)
    foreign_keys = renewal_db.execute(
        "PRAGMA foreign_key_list(monitoring_document_renewal_requests)"
    ).fetchall()
    assert {row["table"] for row in foreign_keys} == {
        "applications",
        "clients",
        "documents",
        "monitoring_alerts",
    }
    assert {row["on_delete"].upper() for row in foreign_keys} == {"RESTRICT"}
    with pytest.raises(sqlite3.IntegrityError):
        renewal_db.execute("DELETE FROM documents WHERE id = ?", ("renewal-doc-a",))


def test_migration_is_idempotent_preserves_rows_and_contains_no_backfill(renewal_db):
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    executable_sql = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    assert re.search(r"^\s*(INSERT|UPDATE|DELETE)\b", executable_sql, re.MULTILINE | re.IGNORECASE) is None

    # Force the migration path instead of relying on init_db's inline DDL.
    renewal_db.execute("DROP TABLE monitoring_document_renewal_upload_cleanup")
    renewal_db.execute("DROP TABLE monitoring_document_renewal_uploads")
    renewal_db.execute("DROP TABLE monitoring_document_renewal_events")
    renewal_db.execute("DROP TABLE monitoring_document_renewal_requests")
    renewal_db.execute("DROP TABLE monitoring_document_renewal_scheduler_state")
    renewal_db.executescript(sql)
    assert _table_types(renewal_db, "monitoring_document_renewal_events")["payload"] == "TEXT"
    assert _table_types(renewal_db, "monitoring_document_renewal_requests")["created_at"] == "TEXT"

    _insert_request(renewal_db)
    renewal_db.executescript(sql)
    renewal_db.executescript(sql)
    row = renewal_db.execute(
        """SELECT request_id, request_status, document_id
             FROM monitoring_document_renewal_requests"""
    ).fetchone()
    assert dict(row) == {
        "request_id": "renewal-request-1",
        "request_status": "created",
        "document_id": "renewal-doc-a",
    }


def test_renewal_tables_are_regulated_and_not_unattended_purge_targets():
    from regulated_deletion import EPHEMERAL_TABLES, REGULATED_TABLES

    tables = {
        "monitoring_document_renewal_requests",
        "monitoring_document_renewal_events",
        "monitoring_document_renewal_uploads",
        "monitoring_document_renewal_upload_cleanup",
    }
    assert tables <= REGULATED_TABLES
    assert tables.isdisjoint(EPHEMERAL_TABLES)


def test_live_postgres_schema_and_file_migration_types(monkeypatch):
    if not (os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")):
        pytest.skip("Set TEST_POSTGRES_DSN or DATABASE_URL_TEST for PostgreSQL schema validation")

    from tests.test_migration_chain_full import _fresh_pg

    db_module, db = _fresh_pg(monkeypatch)
    try:
        db.execute("DROP TABLE monitoring_document_renewal_upload_cleanup")
        db.execute("DROP TABLE monitoring_document_renewal_uploads")
        db.execute("DROP TABLE monitoring_document_renewal_events")
        db.execute("DROP TABLE monitoring_document_renewal_requests")
        db.execute("DROP TABLE monitoring_document_renewal_scheduler_state")
        db.commit()
        db.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
        db.commit()

        rows = db.execute(
            """SELECT table_name, column_name, data_type
                 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'monitoring_document_renewal_requests',
                      'monitoring_document_renewal_events',
                      'monitoring_document_renewal_uploads',
                      'monitoring_document_renewal_upload_cleanup',
                      'monitoring_document_renewal_scheduler_state'
                  )
                  AND column_name IN (
                      'created_at', 'payload', 'uploaded_at', 'stored_at',
                      'attached_at', 'cleaned_at', 'updated_at',
                      'lease_expires_at', 'next_retry_at'
                  )"""
        ).fetchall()
        types = {
            (row["table_name"], row["column_name"]): row["data_type"]
            for row in rows
        }
        assert types[("monitoring_document_renewal_events", "payload")] == "jsonb"
        assert types[("monitoring_document_renewal_requests", "created_at")].startswith("timestamp")
        assert types[("monitoring_document_renewal_uploads", "uploaded_at")].startswith("timestamp")
        for column in (
            "created_at",
            "stored_at",
            "attached_at",
            "cleaned_at",
            "updated_at",
            "lease_expires_at",
            "next_retry_at",
        ):
            assert types[("monitoring_document_renewal_upload_cleanup", column)].startswith(
                "timestamp"
            )
        assert types[("monitoring_document_renewal_scheduler_state", "updated_at")].startswith(
            "timestamp"
        )

        # CREATE TABLE / INDEX IF NOT EXISTS remains a no-op on rerun.
        db.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
        db.commit()
    finally:
        db.close()
        db_module.close_pg_pool()
