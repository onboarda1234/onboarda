"""Focused tests for the canonical document-renewal request workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import sys

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import DBConnection, append_audit_log
import monitoring_document_renewal as renewal


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
OFFICER = {
    "sub": "officer-1",
    "name": "Test Officer",
    "role": "co",
    "type": "officer",
}
CLIENT = {
    "sub": "client-1",
    "name": "Test Client",
    "role": "client",
    "type": "client",
}
SCHEDULER = {
    "id": "renewal-scheduler",
    "name": "Renewal Scheduler",
    "role": "system",
    "type": "system",
}


class Flags:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.seen = []

    def is_enabled(self, name: str) -> bool:
        self.seen.append(name)
        return self.enabled


@pytest.fixture
def renewal_db():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db = DBConnection(raw, is_postgres=False, database_identity=":memory:")
    db.executescript(
        """
        CREATE TABLE clients (id TEXT PRIMARY KEY);
        CREATE TABLE applications (
            id TEXT PRIMARY KEY,
            ref TEXT,
            client_id TEXT,
            company_name TEXT
        );
        CREATE TABLE monitoring_alerts (
            id INTEGER PRIMARY KEY,
            application_id TEXT,
            client_name TEXT,
            alert_type TEXT,
            source_reference TEXT,
            provider TEXT,
            case_identifier TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            resolved_at TEXT
        );
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            person_id TEXT,
            person_type TEXT,
            doc_type TEXT,
            slot_key TEXT,
            is_current BOOLEAN,
            version INTEGER,
            superseded_by_document_id TEXT,
            expiry_date TEXT,
            valid_until TEXT,
            verification_status TEXT,
            verification_results TEXT,
            verified_at TEXT,
            review_status TEXT,
            reviewer_role TEXT,
            review_comment TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            superseded_at TEXT
        );
        CREATE TABLE directors (
            id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT, full_name TEXT
        );
        CREATE TABLE ubos (
            id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT, full_name TEXT
        );
        CREATE TABLE intermediaries (
            id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT, entity_name TEXT
        );
        CREATE TABLE agent_executions (
            id INTEGER PRIMARY KEY, application_id TEXT, document_id TEXT,
            agent_name TEXT, agent_number INTEGER, status TEXT,
            requires_review BOOLEAN, started_at TEXT, completed_at TEXT,
            error_message TEXT
        );
        CREATE TABLE application_enhanced_requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitoring_alert_id INTEGER,
            application_id TEXT,
            active BOOLEAN,
            status TEXT,
            monitoring_document_id TEXT,
            linked_document_id TEXT
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT, user_name TEXT, user_role TEXT,
            action TEXT, target TEXT, detail TEXT, ip_address TEXT,
            timestamp TEXT, before_state TEXT, after_state TEXT,
            previous_hash TEXT, entry_hash TEXT,
            application_id TEXT, request_id TEXT
        );
        CREATE UNIQUE INDEX audit_log_previous_hash_unique
            ON audit_log(previous_hash) WHERE previous_hash IS NOT NULL;
        CREATE TABLE monitoring_document_renewal_requests (
            request_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            person_id TEXT,
            person_type TEXT,
            document_id TEXT NOT NULL,
            document_type TEXT NOT NULL,
            document_slot_key TEXT NOT NULL,
            document_version INTEGER NOT NULL,
            monitoring_alert_id INTEGER NOT NULL,
            request_reason TEXT NOT NULL,
            request_status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            due_date TEXT NOT NULL,
            created_by TEXT NOT NULL,
            sent_at TEXT,
            cancelled_at TEXT,
            cancelled_by TEXT,
            cancel_reason TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            contract_version TEXT NOT NULL,
            eligibility_fingerprint TEXT NOT NULL
        );
        CREATE UNIQUE INDEX renewal_active_document_unique
            ON monitoring_document_renewal_requests(
                application_id, COALESCE(person_id, ''), document_slot_key
            ) WHERE request_status <> 'cancelled';
        CREATE TABLE monitoring_document_renewal_events (
            event_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            event_sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_key TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            due_date_snapshot TEXT,
            reminder_interval_days INTEGER
        );
        CREATE TABLE monitoring_document_renewal_uploads (
            upload_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            application_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            mime_type TEXT NOT NULL,
            file_sha256 TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            uploaded_by TEXT NOT NULL
        );
        CREATE TABLE monitoring_document_renewal_upload_bindings (
            upload_id TEXT PRIMARY KEY,
            renewal_request_id TEXT NOT NULL UNIQUE,
            application_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            person_id TEXT,
            person_type TEXT,
            original_document_id TEXT NOT NULL,
            original_document_version INTEGER NOT NULL,
            uploaded_document_id TEXT NOT NULL UNIQUE,
            document_type TEXT NOT NULL,
            upload_timestamp TEXT NOT NULL,
            uploaded_by TEXT NOT NULL,
            binding_status TEXT NOT NULL CHECK (binding_status = 'bound'),
            contract_version TEXT NOT NULL,
            binding_fingerprint TEXT NOT NULL,
            CHECK (
                (person_id IS NULL AND person_type IS NULL)
                OR (person_id IS NOT NULL AND person_type IS NOT NULL)
            )
        );
        CREATE TABLE monitoring_document_renewal_upload_cleanup (
            cleanup_id TEXT PRIMARY KEY,
            upload_id TEXT NOT NULL UNIQUE,
            request_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            backend TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            file_extension TEXT NOT NULL,
            local_path TEXT,
            s3_version_id TEXT,
            artifact_state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error_code TEXT,
            correlation_id TEXT,
            lease_owner TEXT,
            lease_expires_at TEXT,
            next_retry_at TEXT,
            created_at TEXT NOT NULL,
            stored_at TEXT,
            attached_at TEXT,
            cleaned_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE monitoring_document_renewal_scheduler_state (
            scheduler_key TEXT PRIMARY KEY,
            cursor_primary TEXT,
            cursor_secondary TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE client_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT,
            client_id TEXT,
            notification_type TEXT,
            title TEXT NOT NULL,
            message TEXT,
            documents_list TEXT,
            read_status INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    db.execute("INSERT INTO clients(id) VALUES ('client-1')")
    db.execute(
        """
        INSERT INTO applications(id, ref, client_id, company_name)
        VALUES ('app-1', 'APP-1', 'client-1', 'Example Ltd')
        """
    )
    db.execute(
        """
        INSERT INTO documents(
            id, application_id, doc_type, slot_key, is_current, version,
            expiry_date, verification_status, review_status
        ) VALUES ('doc-1', 'app-1', 'passport', 'passport:entity', 1, 1,
                  '2026-07-01', 'flagged', 'pending')
        """
    )
    db.execute(
        """
        INSERT INTO monitoring_alerts(
            id, application_id, client_name, alert_type, source_reference, status
        ) VALUES (1, 'app-1', 'Example Ltd', 'document_expired',
                  'document:doc-1', 'open')
        """
    )
    db.commit()
    yield db
    db.close()


def _stored_upload_artifact(
    db,
    request,
    *,
    upload_id,
    storage_key,
    cleanup_id=None,
):
    artifact_id = cleanup_id or f"artifact-{upload_id}"
    timestamp = NOW.isoformat()
    db.execute(
        """
        INSERT INTO monitoring_document_renewal_upload_cleanup
            (cleanup_id, upload_id, request_id, application_id, customer_id,
             backend, storage_key, file_extension, artifact_state, created_at, stored_at,
             updated_at)
        VALUES (?, ?, ?, ?, ?, 's3', ?, '', 'stored', ?, ?, ?)
        """,
        (
            artifact_id,
            upload_id,
            request["request_id"],
            request["application_id"],
            request["customer_id"],
            storage_key,
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    db.commit()
    return artifact_id


def _create(db, **overrides):
    kwargs = {
        "reason": "expired",
        "actor": OFFICER,
        "now": NOW,
        "feature_flags": Flags(True),
    }
    kwargs.update(overrides)
    return renewal.create_renewal_request(db, 1, **kwargs)


def _scalar(db, sql, params=()):
    row = db.execute(sql, params).fetchone()
    return next(iter(row.values()))


def _upload_state(db, request_id):
    return {
        "request": dict(
            db.execute(
                "SELECT request_status, revision FROM monitoring_document_renewal_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        ),
        "uploads": _scalar(
            db,
            "SELECT COUNT(*) FROM monitoring_document_renewal_uploads WHERE request_id = ?",
            (request_id,),
        ),
        "bindings": _scalar(
            db,
            "SELECT COUNT(*) FROM monitoring_document_renewal_upload_bindings WHERE renewal_request_id = ?",
            (request_id,),
        ),
        "received_events": _scalar(
            db,
            "SELECT COUNT(*) FROM monitoring_document_renewal_events WHERE request_id = ? AND event_type = 'upload_received'",
            (request_id,),
        ),
        "bound_audits": _scalar(
            db,
            """SELECT COUNT(*)
                 FROM audit_log a
                 JOIN monitoring_document_renewal_upload_bindings b
                   ON a.target = 'monitoring_renewal_upload_binding:' || b.upload_id
                WHERE a.action = 'monitoring.document_renewal.upload_bound'
                  AND b.renewal_request_id = ?""",
            (request_id,),
        ),
    }


def _upload_failure_audits(db, request_id):
    return [
        dict(row)
        for row in db.execute(
            """
            SELECT action, detail
              FROM audit_log
             WHERE target = ?
               AND action IN (
                   'monitoring.document_renewal.upload_rejected',
                   'monitoring.document_renewal.wrong_document_type',
                   'monitoring.document_renewal.duplicate_upload',
                   'monitoring.document_renewal.binding_failed'
               )
             ORDER BY id
            """,
            (f"monitoring_renewal_request:{request_id}",),
        ).fetchall()
    ]


def _upload_kwargs(db, request, **overrides):
    values = {
        "actor": CLIENT,
        "expected_revision": request["revision"],
        "upload_id": "upload-1",
        "original_filename": "passport.pdf",
        "storage_key": f"renewals/{request['application_id']}/{request['request_id']}/upload-1",
        "file_size": 1024,
        "mime_type": "application/pdf",
        "file_sha256": "a" * 64,
        "now": NOW,
        "feature_flags": Flags(True),
        "expected_application_id": request["application_id"],
        "expected_customer_id": request["customer_id"],
        "expected_person_id": request.get("person_id"),
        "expected_original_document_id": request["document_id"],
        "expected_document_type": request["document_type"],
    }
    values.update(overrides)
    values["artifact_reservation_id"] = _stored_upload_artifact(
        db,
        request,
        upload_id=values["upload_id"],
        storage_key=values["storage_key"],
        cleanup_id=values.pop("cleanup_id", None),
    )
    return values


def _add_document_alert(
    db,
    *,
    alert_id: int,
    app_id: str,
    document_id: str,
    alert_type: str = "document_expired",
    expiry_date: str = "2026-07-01",
):
    db.execute(
        "INSERT INTO applications(id, ref, client_id, company_name) VALUES (?, ?, 'client-1', ?)",
        (app_id, app_id.upper(), f"{app_id} Ltd"),
    )
    db.execute(
        """
        INSERT INTO documents(
            id, application_id, doc_type, slot_key, is_current, version,
            expiry_date, verification_status, review_status
        ) VALUES (?, ?, 'passport', ?, 1, 1, ?, 'flagged', 'pending')
        """,
        (document_id, app_id, f"entity:{app_id}:passport", expiry_date),
    )
    db.execute(
        """
        INSERT INTO monitoring_alerts(
            id, application_id, client_name, alert_type, source_reference, status
        ) VALUES (?, ?, ?, ?, ?, 'open')
        """,
        (alert_id, app_id, f"{app_id} Ltd", alert_type, f"document:{document_id}"),
    )
    db.commit()


def _supersede_primary_document(db, *, alert_id=2):
    db.execute(
        """
        UPDATE documents
           SET is_current = 0, superseded_by_document_id = 'doc-2',
               superseded_at = ?
         WHERE id = 'doc-1'
        """,
        (NOW.isoformat(),),
    )
    db.execute(
        """
        INSERT INTO documents(
            id, application_id, doc_type, slot_key, is_current, version,
            expiry_date, verification_status, review_status
        ) VALUES ('doc-2', 'app-1', 'passport', 'passport:entity', 1, 2,
                  '2026-07-15', 'flagged', 'pending')
        """
    )
    db.execute(
        """
        INSERT INTO monitoring_alerts(
            id, application_id, client_name, alert_type, source_reference, status
        ) VALUES (?, 'app-1', 'Example Ltd', 'document_expired',
                  'document:doc-2', 'open')
        """,
        (alert_id,),
    )
    db.commit()
    return alert_id


def test_flag_is_the_only_write_gate_and_defaults_fail_closed(renewal_db):
    disabled = Flags(False)
    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db, feature_flags=disabled)
    assert exc.value.code == "feature_disabled"
    assert disabled.seen == ["ENABLE_DOCUMENT_RENEWAL_AUTOMATION"]
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_requests") == 0


def test_create_is_atomic_sent_awaiting_upload_and_preserves_owner_records(renewal_db):
    alert_before = dict(
        renewal_db.execute("SELECT * FROM monitoring_alerts WHERE id = 1").fetchone()
    )
    document_before = dict(
        renewal_db.execute("SELECT * FROM documents WHERE id = 'doc-1'").fetchone()
    )

    result = _create(renewal_db, request_id="renewal-1")

    assert result["contract_version"] == "monitoring_document_renewal_request_v1"
    assert result["request_status"] == "awaiting_upload"
    assert result["display_status"] == "Awaiting Upload"
    assert result["sent_at"] == "2026-08-02T12:00:00Z"
    assert result["revision"] == 2
    assert result["document_id"] == "doc-1"
    assert result["due_date"] == "2026-08-16"
    assert [row["event_type"] for row in renewal_db.execute(
        "SELECT event_type FROM monitoring_document_renewal_events ORDER BY event_sequence"
    ).fetchall()] == ["request_created", "request_sent"]
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM audit_log") == 2
    notification = dict(
        renewal_db.execute(
            "SELECT notification_type, documents_list FROM client_notifications"
        ).fetchone()
    )
    assert notification == {
        "notification_type": "document_renewal_request",
        "documents_list": '["passport — Entity — Example Ltd"]',
    }
    assert dict(renewal_db.execute("SELECT * FROM monitoring_alerts WHERE id = 1").fetchone()) == alert_before
    assert dict(renewal_db.execute("SELECT * FROM documents WHERE id = 'doc-1'").fetchone()) == document_before
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM application_enhanced_requirements") == 0


def test_create_is_idempotent_and_does_not_duplicate_events_or_audit(renewal_db):
    first = _create(renewal_db, request_id="renewal-1")
    second = _create(renewal_db, request_id="ignored-on-idempotent-retry")
    assert second["request_id"] == first["request_id"]
    assert second["idempotent"] is True
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_requests") == 1
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_events") == 2
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM audit_log") == 2


def test_exact_automatic_eligibility_and_no_semantic_aliases(renewal_db):
    renewal_db.execute(
        "UPDATE documents SET expiry_date = '2026-08-20' WHERE id = 'doc-1'"
    )
    renewal_db.execute(
        "UPDATE monitoring_alerts SET alert_type = 'document_expiring_soon' WHERE id = 1"
    )
    renewal_db.commit()
    result = _create(renewal_db, reason="expiring_soon")
    assert result["request_reason"] == "expiring_soon"

    renewal_db.execute("DELETE FROM monitoring_document_renewal_events")
    renewal_db.execute("DELETE FROM audit_log")
    renewal_db.execute("DELETE FROM monitoring_document_renewal_requests")
    renewal_db.execute(
        "UPDATE monitoring_alerts SET alert_type = 'Expired Document' WHERE id = 1"
    )
    renewal_db.commit()
    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db, reason="expired")
    assert exc.value.code == "linkage_not_authoritative"
    assert exc.value.details == {"linkage_code": "unsupported_alert_type"}


def test_creation_transition_requires_exactly_one_updated_row(renewal_db):
    renewal_db.execute(
        """
        CREATE TRIGGER ignore_renewal_creation_transition
        BEFORE UPDATE OF request_status
        ON monitoring_document_renewal_requests
        WHEN OLD.request_status = 'created'
         AND NEW.request_status = 'awaiting_upload'
        BEGIN
            SELECT RAISE(IGNORE);
        END
        """
    )
    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db, request_id="renewal-stale-transition")
    assert exc.value.code == "stale_request"
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_requests",
    ) == 0


def test_expiring_soon_outside_window_is_ineligible(renewal_db):
    renewal_db.execute(
        "UPDATE documents SET expiry_date = '2026-09-15' WHERE id = 'doc-1'"
    )
    renewal_db.execute(
        "UPDATE monitoring_alerts SET alert_type = 'document_expiring_soon' WHERE id = 1"
    )
    renewal_db.commit()
    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db, reason="expiring_soon")
    assert exc.value.code == "not_eligible"


def test_manual_request_requires_bounded_rationale(renewal_db):
    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db, reason="manual", manual_reason="")
    assert exc.value.code == "invalid_input"
    result = _create(
        renewal_db,
        reason="manual",
        manual_reason="Officer requested a fresh identity document.",
    )
    event = renewal_db.execute(
        "SELECT payload FROM monitoring_document_renewal_events WHERE event_type='request_created'"
    ).fetchone()["payload"]
    assert "Officer requested a fresh identity document." in event
    assert result["request_reason"] == "manual"


def test_active_exact_legacy_request_blocks_canonical_creation(renewal_db):
    renewal_db.execute(
        """
        INSERT INTO application_enhanced_requirements(
            monitoring_alert_id, application_id, active, status,
            monitoring_document_id, linked_document_id
        ) VALUES (1, 'app-1', 1, 'requested', 'doc-1', 'doc-1')
        """
    )
    renewal_db.commit()
    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db)
    assert exc.value.code == "legacy_request_conflict"
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_requests") == 0


def test_second_audit_failure_rolls_back_request_events_and_first_audit(renewal_db):
    calls = []

    def fail_second(db, **kwargs):
        calls.append(kwargs["action"])
        if len(calls) == 2:
            raise RuntimeError("audit unavailable")
        return append_audit_log(db, **kwargs)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        _create(renewal_db, audit_writer=fail_second)
    assert calls == [
        "monitoring.document_renewal.request_created",
        "monitoring.document_renewal.request_sent",
    ]
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_requests") == 0
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_events") == 0
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM audit_log") == 0


def test_resend_due_date_and_cancel_use_revision_preconditions(renewal_db):
    request = _create(renewal_db, request_id="renewal-1")
    request = renewal.resend_renewal_request(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=2,
        now=NOW,
        feature_flags=Flags(True),
    )
    assert request["revision"] == 3
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.update_renewal_due_date(
            renewal_db,
            request["request_id"],
            actor=OFFICER,
            expected_revision=2,
            due_date="2026-08-22",
            reason="Give the client additional time.",
            now=NOW,
            feature_flags=Flags(True),
        )
    assert exc.value.code == "stale_request"
    request = renewal.update_renewal_due_date(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=3,
        due_date="2026-08-22",
        reason="Give the client additional time.",
        now=NOW,
        feature_flags=Flags(True),
    )
    assert request["due_date"] == "2026-08-22"
    request = renewal.cancel_renewal_request(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=4,
        cancel_reason="Request entered in error.",
        now=NOW,
        feature_flags=Flags(True),
    )
    assert request["request_status"] == "cancelled"
    replacement = _create(renewal_db, request_id="renewal-2")
    assert replacement["request_id"] == "renewal-2"
    assert renewal.renewal_request_for_alert(
        renewal_db, 1, include_cancelled=True
    )["request_id"] == "renewal-2"


def test_upload_is_staged_only_and_idempotent(renewal_db):
    document_before = dict(
        renewal_db.execute("SELECT * FROM documents WHERE id='doc-1'").fetchone()
    )
    request = _create(renewal_db, request_id="renewal-1")
    kwargs = _upload_kwargs(renewal_db, request)
    uploaded = renewal.record_renewal_upload(
        renewal_db, request["request_id"], **kwargs
    )
    assert uploaded["request_status"] == "upload_received"
    assert {item["key"]: item["complete"] for item in uploaded["milestones"]} == {
        "renewal_requested": True,
        "request_sent": True,
        "awaiting_upload": True,
    }
    assert uploaded["verification_controls"] is False
    assert len(uploaded["uploads"]) == 1
    assert "storage_key" not in uploaded["uploads"][0]
    assert dict(renewal_db.execute("SELECT * FROM documents WHERE id='doc-1'").fetchone()) == document_before
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM agent_executions") == 0
    retried = renewal.record_renewal_upload(
        renewal_db, request["request_id"], **kwargs
    )
    assert retried["idempotent"] is True
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_uploads") == 1
    binding = dict(
        renewal_db.execute(
            "SELECT * FROM monitoring_document_renewal_upload_bindings WHERE upload_id = ?",
            (kwargs["upload_id"],),
        ).fetchone()
    )
    assert binding == {
        "upload_id": "upload-1",
        "renewal_request_id": request["request_id"],
        "application_id": request["application_id"],
        "customer_id": request["customer_id"],
        "person_id": request["person_id"],
        "person_type": request["person_type"],
        "original_document_id": request["document_id"],
        "original_document_version": request["document_version"],
        "uploaded_document_id": "renewal-candidate:upload-1",
        "document_type": request["document_type"],
        "upload_timestamp": "2026-08-02T12:00:00Z",
        "uploaded_by": CLIENT["sub"],
        "binding_status": "bound",
        "contract_version": "monitoring_document_renewal_upload_binding_v1",
        "binding_fingerprint": binding["binding_fingerprint"],
    }
    assert len(binding["binding_fingerprint"]) == 64
    assert binding["binding_fingerprint"] == binding["binding_fingerprint"].lower()
    assert binding["binding_fingerprint"] == renewal._upload_binding_fingerprint(
        upload_id=kwargs["upload_id"],
        renewal_request_id=request["request_id"],
        application_id=request["application_id"],
        customer_id=request["customer_id"],
        person_id=request["person_id"],
        person_type=request["person_type"],
        original_document_id=request["document_id"],
        original_document_version=request["document_version"],
        uploaded_document_id=binding["uploaded_document_id"],
        document_type=request["document_type"],
        file_sha256=kwargs["file_sha256"],
        file_size=kwargs["file_size"],
        mime_type=kwargs["mime_type"],
        uploaded_by=CLIENT["sub"],
        upload_timestamp=binding["upload_timestamp"],
    )
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_events WHERE event_type='upload_received'",
    ) == 1
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_upload_bindings",
    ) == 1
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM audit_log WHERE action='monitoring.document_renewal.upload_bound'",
    ) == 1

    cancelled = renewal.cancel_renewal_request(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=uploaded["revision"],
        cancel_reason="The staged file was not the requested evidence.",
        now=NOW,
        feature_flags=Flags(True),
    )
    assert cancelled["request_status"] == "cancelled"
    replacement = _create(renewal_db, request_id="renewal-2")
    assert replacement["request_status"] == "awaiting_upload"
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_uploads",
    ) == 1
    assert dict(
        renewal_db.execute("SELECT * FROM documents WHERE id='doc-1'").fetchone()
    ) == document_before


@pytest.mark.parametrize(
    "field,value,error_code",
    [
        ("expected_application_id", "app-other", "wrong_application"),
        ("expected_customer_id", "client-other", "wrong_customer"),
        ("expected_person_id", "person-other", "wrong_person"),
        ("expected_original_document_id", "doc-other", "wrong_document"),
        ("expected_document_type", "proof_of_address", "wrong_document_type"),
    ],
)
def test_upload_binding_rejects_every_stale_preflight_identity_without_partial_write(
    renewal_db, field, value, error_code
):
    request = _create(renewal_db, request_id="renewal-identity")
    before = _upload_state(renewal_db, request["request_id"])
    kwargs = _upload_kwargs(
        renewal_db,
        request,
        **{field: value, "upload_id": f"upload-{field}"},
    )

    with pytest.raises(renewal.RenewalError) as exc:
        renewal.record_renewal_upload(
            renewal_db,
            request["request_id"],
            **kwargs,
        )

    assert exc.value.code == error_code
    assert _upload_state(renewal_db, request["request_id"]) == before
    audits = _upload_failure_audits(renewal_db, request["request_id"])
    assert len(audits) == 1
    assert audits[0]["action"] == (
        "monitoring.document_renewal.wrong_document_type"
        if error_code == "wrong_document_type"
        else "monitoring.document_renewal.upload_rejected"
    )
    assert json.loads(audits[0]["detail"])["reason_code"] == error_code
    artifact = dict(
        renewal_db.execute(
            "SELECT artifact_state, attached_at FROM monitoring_document_renewal_upload_cleanup WHERE cleanup_id = ?",
            (kwargs["artifact_reservation_id"],),
        ).fetchone()
    )
    assert artifact == {"artifact_state": "stored", "attached_at": None}


def test_cancelled_request_rejects_upload_binding_without_partial_write(renewal_db):
    request = _create(renewal_db, request_id="renewal-cancelled-upload")
    cancelled = renewal.cancel_renewal_request(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=request["revision"],
        cancel_reason="The request is no longer required.",
        now=NOW,
        feature_flags=Flags(True),
    )
    before = _upload_state(renewal_db, request["request_id"])
    kwargs = _upload_kwargs(
        renewal_db,
        cancelled,
        upload_id="upload-cancelled",
    )

    with pytest.raises(renewal.RenewalError) as exc:
        renewal.record_renewal_upload(
            renewal_db,
            request["request_id"],
            **kwargs,
        )

    assert exc.value.code == "request_cancelled"
    assert _upload_state(renewal_db, request["request_id"]) == before


def test_overdue_request_rejects_but_due_date_is_inclusive_in_utc(renewal_db):
    due_today = _create(
        renewal_db,
        request_id="renewal-due-today",
        due_date=NOW.date().isoformat(),
    )
    today_kwargs = _upload_kwargs(
        renewal_db,
        due_today,
        upload_id="upload-due-today",
        now=NOW.replace(hour=23, minute=59, second=59),
    )
    accepted = renewal.record_renewal_upload(
        renewal_db,
        due_today["request_id"],
        **today_kwargs,
    )
    assert accepted["request_status"] == "upload_received"

    _add_document_alert(
        renewal_db,
        alert_id=2,
        app_id="app-expired-request",
        document_id="doc-expired-request",
    )
    overdue = renewal.create_renewal_request(
        renewal_db,
        2,
        reason="expired",
        actor=OFFICER,
        due_date=NOW.date().isoformat(),
        now=NOW,
        request_id="renewal-overdue",
        feature_flags=Flags(True),
    )
    before = _upload_state(renewal_db, overdue["request_id"])
    overdue_kwargs = _upload_kwargs(
        renewal_db,
        overdue,
        upload_id="upload-overdue",
        now=NOW + timedelta(days=1),
    )
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.record_renewal_upload(
            renewal_db,
            overdue["request_id"],
            **overdue_kwargs,
        )
    assert exc.value.code == "request_expired"
    assert _upload_state(renewal_db, overdue["request_id"]) == before


def test_distinct_duplicate_upload_is_rejected_and_first_binding_is_unchanged(
    renewal_db,
):
    request = _create(renewal_db, request_id="renewal-duplicate")
    first_kwargs = _upload_kwargs(
        renewal_db,
        request,
        upload_id="upload-first",
    )
    first = renewal.record_renewal_upload(
        renewal_db,
        request["request_id"],
        **first_kwargs,
    )
    before = _upload_state(renewal_db, request["request_id"])
    duplicate_kwargs = _upload_kwargs(
        renewal_db,
        first,
        upload_id="upload-second",
        file_sha256="b" * 64,
    )

    with pytest.raises(renewal.RenewalError) as exc:
        renewal.record_renewal_upload(
            renewal_db,
            request["request_id"],
            **duplicate_kwargs,
        )

    assert exc.value.code in {"upload_already_received", "duplicate_upload"}
    assert _upload_state(renewal_db, request["request_id"]) == before
    bound = renewal_db.execute(
        "SELECT upload_id, uploaded_document_id FROM monitoring_document_renewal_upload_bindings WHERE renewal_request_id = ?",
        (request["request_id"],),
    ).fetchall()
    assert [dict(row) for row in bound] == [
        {
            "upload_id": "upload-first",
            "uploaded_document_id": "renewal-candidate:upload-first",
        }
    ]


def test_second_binding_audit_failure_rolls_back_upload_binding_and_request(
    renewal_db,
):
    request = _create(renewal_db, request_id="renewal-binding-audit-failure")
    kwargs = _upload_kwargs(
        renewal_db,
        request,
        upload_id="upload-binding-audit-failure",
    )
    before = _upload_state(renewal_db, request["request_id"])
    calls = []

    def fail_bound_audit(db, **audit_kwargs):
        calls.append(audit_kwargs["action"])
        if audit_kwargs["action"] == "monitoring.document_renewal.upload_bound":
            raise RuntimeError("injected upload binding audit failure")
        return append_audit_log(db, **audit_kwargs)

    with pytest.raises(renewal.RenewalError) as exc:
        renewal.record_renewal_upload(
            renewal_db,
            request["request_id"],
            audit_writer=fail_bound_audit,
            **kwargs,
        )

    assert exc.value.code == "binding_failed"
    assert calls == [
        "monitoring.document_renewal.upload_received",
        "monitoring.document_renewal.upload_bound",
        "monitoring.document_renewal.binding_failed",
    ]
    assert _upload_state(renewal_db, request["request_id"]) == before
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM audit_log WHERE action IN ('monitoring.document_renewal.upload_received', 'monitoring.document_renewal.upload_bound')",
    ) == 0
    audits = _upload_failure_audits(renewal_db, request["request_id"])
    assert len(audits) == 1
    assert audits[0]["action"] == "monitoring.document_renewal.binding_failed"
    assert json.loads(audits[0]["detail"])["reason_code"] == "binding_failed"
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_upload_cleanup WHERE cleanup_id = ? AND artifact_state = 'stored'",
        (kwargs["artifact_reservation_id"],),
    ) == 1


def test_binding_insert_failure_rolls_back_upload_event_request_and_attachment(
    renewal_db,
):
    request = _create(renewal_db, request_id="renewal-binding-write-failure")
    kwargs = _upload_kwargs(
        renewal_db,
        request,
        upload_id="upload-binding-write-failure",
    )
    before = _upload_state(renewal_db, request["request_id"])
    renewal_db.execute(
        """
        CREATE TRIGGER fail_upload_binding_insert
        BEFORE INSERT ON monitoring_document_renewal_upload_bindings
        BEGIN
            SELECT RAISE(ABORT, 'injected upload binding write failure');
        END
        """
    )
    renewal_db.commit()

    with pytest.raises(renewal.RenewalError) as exc:
        renewal.record_renewal_upload(
            renewal_db,
            request["request_id"],
            **kwargs,
        )

    assert exc.value.code == "binding_failed"
    assert _upload_state(renewal_db, request["request_id"]) == before
    audits = _upload_failure_audits(renewal_db, request["request_id"])
    assert len(audits) == 1
    assert audits[0]["action"] == "monitoring.document_renewal.binding_failed"
    assert json.loads(audits[0]["detail"])["reason_code"] == "binding_failed"
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_upload_cleanup WHERE cleanup_id = ? AND artifact_state = 'stored'",
        (kwargs["artifact_reservation_id"],),
    ) == 1


def test_unbound_legacy_upload_projects_manual_review_instead_of_false_success(
    renewal_db,
):
    request = _create(renewal_db, request_id="renewal-unbound-legacy")
    renewal_db.execute(
        """
        INSERT INTO monitoring_document_renewal_uploads
            (upload_id, request_id, application_id, customer_id,
             original_filename, storage_key, file_size, mime_type,
             file_sha256, uploaded_at, uploaded_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-unbound-upload",
            request["request_id"],
            request["application_id"],
            request["customer_id"],
            "legacy.pdf",
            "renewals/legacy-unbound-upload",
            100,
            "application/pdf",
            "c" * 64,
            NOW.isoformat(),
            CLIENT["sub"],
        ),
    )
    renewal_db.execute(
        """
        UPDATE monitoring_document_renewal_requests
           SET request_status = 'upload_received', revision = revision + 1
         WHERE request_id = ?
        """,
        (request["request_id"],),
    )
    renewal_db.commit()

    projected = renewal.get_renewal_request(renewal_db, request["request_id"])

    assert projected["manual_review_required"] is True
    assert projected["upload_allowed"] is False
    assert projected["binding_current"] is False
    assert "upload_binding" not in projected
    assert projected["binding_error"]["code"] == "binding_missing"
    assert projected["request_status"] == "upload_received"
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_upload_bindings",
    ) == 0


def test_reminders_are_bounded_deterministic_and_atomic(renewal_db):
    request = _create(
        renewal_db,
        request_id="renewal-1",
        due_date="2026-08-16",
        now=NOW - timedelta(days=1),
    )
    first = renewal.generate_due_reminders(
        renewal_db,
        actor=SCHEDULER,
        now=NOW,
        feature_flags=Flags(True),
    )
    second = renewal.generate_due_reminders(
        renewal_db,
        actor=SCHEDULER,
        now=NOW,
        feature_flags=Flags(True),
    )
    assert first == {
        "scanned": 1,
        "generated": 1,
        "blocked": 0,
        "unchanged": 0,
        "request_ids": [request["request_id"]],
        "blocked_requests": [],
        "degraded": False,
        "degraded_failures": [],
    }
    assert second == {
        "scanned": 1,
        "generated": 0,
        "blocked": 0,
        "unchanged": 1,
        "request_ids": [],
        "blocked_requests": [],
        "degraded": False,
        "degraded_failures": [],
    }
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_events WHERE event_type='reminder_generated'",
    ) == 1
    projection = renewal.renewal_request_for_alert(renewal_db, 1)
    assert projection["reminder_count"] == 1
    assert projection["last_reminder_at"] == "2026-08-02T12:00:00Z"


def test_same_day_request_notification_suppresses_duplicate_reminder(renewal_db):
    request = _create(
        renewal_db,
        request_id="renewal-1",
        due_date="2026-08-16",
    )
    same_tick = renewal.generate_due_reminders(
        renewal_db,
        actor=SCHEDULER,
        now=NOW,
        feature_flags=Flags(True),
    )
    assert same_tick["generated"] == 0
    assert same_tick["unchanged"] == 1
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM client_notifications",
    ) == 1

    next_interval = renewal.generate_due_reminders(
        renewal_db,
        actor=SCHEDULER,
        now=NOW + timedelta(days=7),
        feature_flags=Flags(True),
    )
    assert next_interval["generated"] == 1
    assert next_interval["request_ids"] == [request["request_id"]]
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM client_notifications",
    ) == 2


def test_reads_remain_available_while_flag_is_off(renewal_db):
    request = _create(renewal_db, request_id="renewal-1")
    found = renewal.renewal_request_for_alert(renewal_db, 1)
    listed = renewal.list_renewal_requests(
        renewal_db, application_id="app-1", customer_id="client-1"
    )
    detailed = renewal.get_renewal_request(renewal_db, request["request_id"])
    assert found["request_id"] == request["request_id"]
    assert [item["request_id"] for item in listed] == [request["request_id"]]
    assert len(detailed["events"]) == 2
    assert detailed["uploads"] == []


def test_request_listing_defaults_to_active_rows_and_has_bounded_pages(renewal_db):
    first = _create(renewal_db, request_id="renewal-1")
    renewal.cancel_renewal_request(
        renewal_db,
        first["request_id"],
        actor=OFFICER,
        expected_revision=first["revision"],
        cancel_reason="Replace this request with a corrected request.",
        now=NOW,
        feature_flags=Flags(True),
    )
    second = _create(renewal_db, request_id="renewal-2")

    active = renewal.list_renewal_requests(
        renewal_db, application_id="app-1", customer_id="client-1"
    )
    first_page = renewal.list_renewal_requests(
        renewal_db,
        application_id="app-1",
        customer_id="client-1",
        include_cancelled=True,
        limit=1,
        offset=0,
    )
    second_page = renewal.list_renewal_requests(
        renewal_db,
        application_id="app-1",
        customer_id="client-1",
        include_cancelled=True,
        limit=1,
        offset=1,
    )

    assert [item["request_id"] for item in active] == [second["request_id"]]
    assert [item["request_id"] for item in first_page] == [second["request_id"]]
    assert [item["request_id"] for item in second_page] == [first["request_id"]]
    assert renewal.count_renewal_requests(
        renewal_db, application_id="app-1", customer_id="client-1"
    ) == 1
    assert renewal.count_renewal_requests(
        renewal_db,
        application_id="app-1",
        customer_id="client-1",
        include_cancelled=True,
    ) == 2
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.list_renewal_requests(renewal_db, limit=101)
    assert exc.value.code == "invalid_pagination"


def test_request_listing_exposes_every_row_across_page_boundary(renewal_db):
    first = _create(renewal_db, request_id="renewal-01")
    expected_ids = {first["request_id"]}
    for index in range(2, 52):
        document_id = f"doc-{index}"
        alert_id = index
        renewal_db.execute(
            """
            INSERT INTO documents(
                id, application_id, doc_type, slot_key, is_current, version,
                expiry_date, verification_status, review_status
            ) VALUES (?, 'app-1', 'passport', ?, 1, 1, '2026-07-01',
                      'flagged', 'pending')
            """,
            (document_id, f"passport:entity:{index}"),
        )
        renewal_db.execute(
            """
            INSERT INTO monitoring_alerts(
                id, application_id, client_name, alert_type,
                source_reference, status
            ) VALUES (?, 'app-1', 'Renewal Test Ltd', 'document_expired', ?, 'open')
            """,
            (alert_id, f"document:{document_id}"),
        )
        renewal_db.commit()
        request = renewal.create_renewal_request(
            renewal_db,
            alert_id,
            reason="expired",
            actor=OFFICER,
            now=NOW,
            request_id=f"renewal-{index:02d}",
            feature_flags=Flags(True),
        )
        expected_ids.add(request["request_id"])

    first_page = renewal.list_renewal_requests(
        renewal_db,
        application_id="app-1",
        customer_id="client-1",
        limit=50,
        offset=0,
    )
    second_page = renewal.list_renewal_requests(
        renewal_db,
        application_id="app-1",
        customer_id="client-1",
        limit=50,
        offset=50,
    )
    first_ids = {item["request_id"] for item in first_page}
    second_ids = {item["request_id"] for item in second_page}

    assert renewal.count_renewal_requests(
        renewal_db, application_id="app-1", customer_id="client-1"
    ) == 51
    assert len(first_ids) == 50
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == expected_ids


def test_stale_canonical_document_version_fails_closed(renewal_db):
    request = _create(renewal_db, request_id="renewal-1")
    renewal_db.execute("UPDATE documents SET version = 2 WHERE id = 'doc-1'")
    renewal_db.commit()
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.resend_renewal_request(
            renewal_db,
            request["request_id"],
            actor=OFFICER,
            expected_revision=2,
            now=NOW,
            feature_flags=Flags(True),
        )
    assert exc.value.code == "stale_linkage"
    assert renewal.get_renewal_request(
        renewal_db, request["request_id"], include_history=False
    )["revision"] == 2


def test_terminal_alert_is_ineligible_and_never_reopened(renewal_db):
    renewal_db.execute("UPDATE monitoring_alerts SET status='resolved' WHERE id=1")
    renewal_db.commit()
    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db)
    assert exc.value.code == "alert_not_active"
    assert _scalar(renewal_db, "SELECT status FROM monitoring_alerts WHERE id=1") == "resolved"


def test_service_enforces_officer_client_and_system_actor_boundaries(renewal_db):
    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db, actor=CLIENT)
    assert exc.value.code == "insufficient_role"

    request = _create(renewal_db, request_id="renewal-1")
    for operation in (
        lambda: renewal.resend_renewal_request(
            renewal_db,
            request["request_id"],
            actor=CLIENT,
            expected_revision=2,
            feature_flags=Flags(True),
        ),
        lambda: renewal.cancel_renewal_request(
            renewal_db,
            request["request_id"],
            actor=CLIENT,
            expected_revision=2,
            cancel_reason="Not authorised.",
            feature_flags=Flags(True),
        ),
        lambda: renewal.update_renewal_due_date(
            renewal_db,
            request["request_id"],
            actor=CLIENT,
            expected_revision=2,
            due_date="2026-08-20",
            reason="Not authorised.",
            feature_flags=Flags(True),
        ),
    ):
        with pytest.raises(renewal.RenewalError) as exc:
            operation()
        assert exc.value.code == "insufficient_role"

    with pytest.raises(renewal.RenewalError) as exc:
        renewal.generate_due_reminders(
            renewal_db,
            actor={**OFFICER, "type": "officer"},
            feature_flags=Flags(True),
        )
    assert exc.value.code == "insufficient_role"
    assert renewal.get_renewal_request(
        renewal_db, request["request_id"], include_history=False
    )["revision"] == 2


def test_upload_requires_exact_authenticated_customer_identity(renewal_db):
    request = _create(renewal_db, request_id="renewal-1")
    common = {
        "expected_revision": 2,
        "artifact_reservation_id": "artifact-upload-1",
        "upload_id": "upload-1",
        "original_filename": "passport.pdf",
        "storage_key": "renewals/app-1/renewal-1/upload-1",
        "file_size": 1024,
        "mime_type": "application/pdf",
        "file_sha256": "a" * 64,
        "feature_flags": Flags(True),
    }
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.record_renewal_upload(
            renewal_db,
            request["request_id"],
            actor={"sub": "other-client", "role": "client", "type": "client"},
            **common,
        )
    assert exc.value.code == "cross_customer_access"
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.record_renewal_upload(
            renewal_db,
            request["request_id"],
            actor=OFFICER,
            **common,
        )
    assert exc.value.code == "insufficient_role"
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_uploads") == 0


def test_upload_rejects_cross_customer_before_stale_linkage_resolution(
    renewal_db, monkeypatch
):
    request = _create(renewal_db, request_id="renewal-1")
    renewal_db.execute("UPDATE documents SET version=2 WHERE id='doc-1'")
    renewal_db.commit()

    def forbidden_after_auth(*_args, **_kwargs):
        raise AssertionError("cross-customer caller reached write locking or linkage")

    monkeypatch.setattr(renewal, "_begin_write", forbidden_after_auth)
    monkeypatch.setattr(renewal, "_validate_request_linkage", forbidden_after_auth)
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.record_renewal_upload(
            renewal_db,
            request["request_id"],
            actor={"sub": "other-client", "role": "client", "type": "client"},
            expected_revision=2,
            artifact_reservation_id="artifact-upload-1",
            upload_id="upload-1",
            original_filename="passport.pdf",
            storage_key="renewals/app-1/renewal-1/upload-1",
            file_size=1024,
            mime_type="application/pdf",
            file_sha256="a" * 64,
            feature_flags=Flags(True),
        )
    assert exc.value.code == "cross_customer_access"


def test_mutable_alert_lifecycle_status_is_not_part_of_eligibility_fingerprint(renewal_db):
    request = _create(renewal_db, request_id="renewal-1")
    renewal_db.execute("UPDATE monitoring_alerts SET status='triaged' WHERE id=1")
    renewal_db.commit()
    resent = renewal.resend_renewal_request(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=2,
        feature_flags=Flags(True),
    )
    assert resent["revision"] == 3
    assert _scalar(renewal_db, "SELECT status FROM monitoring_alerts WHERE id=1") == "triaged"


@pytest.mark.parametrize(
    "reason,alert_type,expiry_date,manual_reason",
    [
        ("expired", "document_expired", "2026-07-01", None),
        ("expiring_soon", "document_expiring_soon", "2026-08-20", None),
        ("manual", "document_expired", "2026-07-01", "Officer rationale."),
    ],
)
def test_superseded_trigger_document_fails_closed_for_every_reason(
    renewal_db, reason, alert_type, expiry_date, manual_reason
):
    renewal_db.execute(
        """
        UPDATE documents
           SET is_current=0, superseded_by_document_id='doc-2', superseded_at='2026-08-01'
         WHERE id='doc-1'
        """
    )
    renewal_db.execute(
        """
        INSERT INTO documents(
            id, application_id, doc_type, slot_key, is_current, version,
            expiry_date, verification_status, review_status
        ) VALUES ('doc-2', 'app-1', 'passport', 'passport:entity', 1, 2,
                  ?, 'pending', 'pending')
        """,
        (expiry_date,),
    )
    renewal_db.execute(
        "UPDATE monitoring_alerts SET alert_type=? WHERE id=1", (alert_type,)
    )
    renewal_db.commit()
    with pytest.raises(renewal.RenewalError) as exc:
        _create(
            renewal_db,
            reason=reason,
            manual_reason=manual_reason,
        )
    assert exc.value.code == "stale_document_alert"
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_requests") == 0


def test_empty_audit_evidence_fails_closed_and_rolls_back(renewal_db):
    def empty_evidence(_db, **_kwargs):
        return ""

    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db, audit_writer=empty_evidence)
    assert exc.value.code == "audit_unavailable"
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_requests") == 0
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_events") == 0


def test_domain_request_id_is_not_reused_as_audit_correlation_id(renewal_db):
    audit_calls = []

    def capture(_db, **kwargs):
        audit_calls.append(kwargs)
        return f"hash-{len(audit_calls)}"

    _create(renewal_db, request_id="domain-request-id", audit_writer=capture)
    assert len(audit_calls) == 2
    assert [call["request_id"] for call in audit_calls] == [None, None]


def test_upload_limit_is_exactly_ten_megabytes(renewal_db):
    request = _create(renewal_db, request_id="renewal-1")
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.record_renewal_upload(
            renewal_db,
            request["request_id"],
            actor=CLIENT,
            expected_revision=2,
            artifact_reservation_id="artifact-upload-1",
            upload_id="upload-1",
            original_filename="passport.pdf",
            storage_key="renewals/upload-1",
            file_size=(10 * 1024 * 1024) + 1,
            mime_type="application/pdf",
            file_sha256="a" * 64,
            feature_flags=Flags(True),
        )
    assert exc.value.code == "invalid_upload"
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_uploads") == 0


def test_postgres_date_values_and_jsonb_event_payloads_are_json_safe():
    projected = renewal._project(
        {
            "request_id": "r1",
            "request_status": "awaiting_upload",
            "due_date": NOW.date(),
            "created_at": NOW,
            "sent_at": NOW,
        }
    )
    assert projected["due_date"] == "2026-08-02"
    assert projected["created_at"] == "2026-08-02T12:00:00Z"

    class Cursor:
        def __init__(self, rows):
            self._items = rows

        def fetchall(self):
            return self._items

    class FakeDb:
        def __init__(self):
            self.calls = 0

        def execute(self, _sql, _params):
            self.calls += 1
            if self.calls == 1:
                return Cursor(
                    [
                        {
                            "event_id": "e1",
                            "request_id": "r1",
                            "event_type": "request_created",
                            "event_key": "created:r1",
                            "payload": {"due_date": "2026-08-02"},
                            "created_at": NOW,
                            "created_by": "officer-1",
                            "due_date_snapshot": NOW.date(),
                            "reminder_interval_days": None,
                        }
                    ]
                )
            return Cursor([])

    history = renewal._history(FakeDb(), "r1")
    assert history["events"][0]["payload"] == {"due_date": "2026-08-02"}
    assert history["events"][0]["created_at"] == "2026-08-02T12:00:00Z"
    assert renewal._json_dumps({"due": NOW.date(), "at": NOW}) == (
        '{"at":"2026-08-02T12:00:00Z","due":"2026-08-02"}'
    )


def test_automatic_eligibility_creates_once_and_never_undoes_cancellation(renewal_db):
    document_before = dict(
        renewal_db.execute("SELECT * FROM documents WHERE id='doc-1'").fetchone()
    )
    first = renewal.generate_eligible_renewal_requests(
        renewal_db,
        actor=SCHEDULER,
        limit=10,
        now=NOW,
        feature_flags=Flags(True),
    )
    assert first["created"] == 1
    request = renewal.renewal_request_for_alert(renewal_db, 1)
    assert request["created_by"] == SCHEDULER["id"]
    assert _scalar(renewal_db, "SELECT status FROM monitoring_alerts WHERE id=1") == "open"
    assert dict(
        renewal_db.execute("SELECT * FROM documents WHERE id='doc-1'").fetchone()
    ) == document_before
    cancelled = renewal.cancel_renewal_request(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=request["revision"],
        cancel_reason="Client confirmed the document is no longer applicable.",
        now=NOW,
        feature_flags=Flags(True),
    )
    assert cancelled["request_status"] == "cancelled"
    second = renewal.generate_eligible_renewal_requests(
        renewal_db,
        actor=SCHEDULER,
        limit=10,
        now=NOW,
        feature_flags=Flags(True),
    )
    assert second["scanned"] == 0
    assert second["created"] == 0
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_requests") == 1


def test_system_actor_cannot_create_manual_request(renewal_db):
    with pytest.raises(renewal.RenewalError) as exc:
        _create(
            renewal_db,
            reason="manual",
            manual_reason="System must not originate manual rationale.",
            actor=SCHEDULER,
        )
    assert exc.value.code == "insufficient_role"


@pytest.mark.parametrize("days_until_expiry", [0, 1, 7, 14, 30])
def test_expiring_soon_default_due_date_never_exceeds_expiry(
    renewal_db, days_until_expiry
):
    expiry = (NOW.date() + timedelta(days=days_until_expiry)).isoformat()
    renewal_db.execute(
        "UPDATE documents SET expiry_date=? WHERE id='doc-1'", (expiry,)
    )
    renewal_db.execute(
        "UPDATE monitoring_alerts SET alert_type='document_expiring_soon' WHERE id=1"
    )
    renewal_db.commit()
    request = _create(renewal_db, reason="expiring_soon")
    assert request["due_date"] == min(
        NOW.date() + timedelta(days=renewal.DEFAULT_DUE_DAYS),
        NOW.date() + timedelta(days=days_until_expiry),
    ).isoformat()


def test_expiring_due_date_change_after_expiry_rolls_back_everything(renewal_db):
    renewal_db.execute(
        "UPDATE documents SET expiry_date='2026-08-09' WHERE id='doc-1'"
    )
    renewal_db.execute(
        "UPDATE monitoring_alerts SET alert_type='document_expiring_soon' WHERE id=1"
    )
    renewal_db.commit()
    request = _create(renewal_db, reason="expiring_soon")
    audit_count = _scalar(renewal_db, "SELECT COUNT(*) FROM audit_log")
    notification_count = _scalar(
        renewal_db, "SELECT COUNT(*) FROM client_notifications"
    )
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.update_renewal_due_date(
            renewal_db,
            request["request_id"],
            actor=OFFICER,
            expected_revision=request["revision"],
            due_date="2026-08-10",
            reason="Unsafe extension.",
            now=NOW,
            feature_flags=Flags(True),
        )
    assert exc.value.code == "invalid_due_date"
    persisted = renewal.get_renewal_request(
        renewal_db, request["request_id"], include_history=False
    )
    assert persisted["due_date"] == "2026-08-09"
    assert persisted["revision"] == request["revision"]
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM audit_log") == audit_count
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM client_notifications") == notification_count


def test_stale_linkage_reads_truthfully_and_cancellation_remains_available(renewal_db):
    request = _create(renewal_db, request_id="renewal-stale")
    renewal_db.execute("UPDATE documents SET version=2 WHERE id='doc-1'")
    renewal_db.commit()
    projection = renewal.get_renewal_request(
        renewal_db, request["request_id"], include_history=False
    )
    assert projection["linkage_current"] is False
    assert projection["upload_allowed"] is False
    assert projection["manual_review_required"] is True
    assert projection["linkage_error"]["code"] == "stale_linkage"
    cancelled = renewal.cancel_renewal_request(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=request["revision"],
        cancel_reason="Canonical document version changed.",
        now=NOW,
        feature_flags=Flags(True),
    )
    assert cancelled["request_status"] == "cancelled"
    payload = renewal_db.execute(
        "SELECT payload FROM monitoring_document_renewal_events WHERE event_type='request_cancelled'"
    ).fetchone()["payload"]
    assert '"linkage_current":false' in payload
    assert '"linkage_error_code":"stale_linkage"' in payload


@pytest.mark.parametrize("cancel_after_upload", [False, True])
def test_parent_drift_suppresses_previously_valid_bound_projection(
    renewal_db, cancel_after_upload
):
    request = _create(renewal_db, request_id="renewal-bound-parent-drift")
    uploaded = renewal.record_renewal_upload(
        renewal_db,
        request["request_id"],
        **_upload_kwargs(renewal_db, request),
    )
    if cancel_after_upload:
        renewal.cancel_renewal_request(
            renewal_db,
            request["request_id"],
            actor=OFFICER,
            expected_revision=uploaded["revision"],
            cancel_reason="Replacement upload no longer required.",
            now=NOW,
            feature_flags=Flags(True),
        )
    renewal_db.execute("UPDATE documents SET version=2 WHERE id='doc-1'")
    renewal_db.commit()

    projection = renewal.get_renewal_request(
        renewal_db, request["request_id"], include_history=False
    )

    assert projection["linkage_current"] is False
    assert projection["binding_current"] is False
    assert projection["binding_status"] == "unavailable"
    assert projection["manual_review_required"] is True
    assert projection["linkage_error"]["code"] == "stale_linkage"
    assert "upload_binding" not in projection


def test_terminal_alert_does_not_trap_active_request_cancellation(renewal_db):
    request = _create(renewal_db, request_id="renewal-terminal")
    renewal_db.execute("UPDATE monitoring_alerts SET status='resolved' WHERE id=1")
    renewal_db.commit()
    cancelled = renewal.cancel_renewal_request(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=request["revision"],
        cancel_reason="Alert reached terminal owner state.",
        now=NOW,
        feature_flags=Flags(True),
    )
    assert cancelled["request_status"] == "cancelled"
    assert _scalar(renewal_db, "SELECT status FROM monitoring_alerts WHERE id=1") == "resolved"


def test_cross_alert_legacy_request_for_same_document_blocks_canonical(renewal_db):
    renewal_db.execute(
        """
        INSERT INTO application_enhanced_requirements(
            monitoring_alert_id, application_id, active, status,
            monitoring_document_id, linked_document_id
        ) VALUES (999, 'app-1', 1, 'requested', 'doc-1', 'doc-1')
        """
    )
    renewal_db.commit()
    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db)
    assert exc.value.code == "legacy_request_conflict"
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM monitoring_document_renewal_requests") == 0


@pytest.mark.parametrize(
    "legacy_status",
    ["requested", "uploaded", "under_review", "rejected"],
)
def test_each_exact_legacy_active_status_blocks_canonical_creation(
    renewal_db,
    legacy_status,
):
    renewal_db.execute(
        """
        INSERT INTO application_enhanced_requirements(
            monitoring_alert_id, application_id, active, status,
            monitoring_document_id, linked_document_id
        ) VALUES (1, 'app-1', 1, ?, 'doc-1', 'doc-1')
        """,
        (legacy_status,),
    )
    renewal_db.commit()
    with pytest.raises(renewal.RenewalError) as exc:
        _create(renewal_db)
    assert exc.value.code == "legacy_request_conflict"


@pytest.mark.parametrize(
    "legacy_status",
    ["accepted", "waived", "generated", "cancelled", "request_pending", ""],
)
def test_terminal_or_unknown_legacy_status_does_not_guess_active_conflict(
    renewal_db,
    legacy_status,
):
    renewal_db.execute(
        """
        INSERT INTO application_enhanced_requirements(
            monitoring_alert_id, application_id, active, status,
            monitoring_document_id, linked_document_id
        ) VALUES (1, 'app-1', 1, ?, 'doc-1', 'doc-1')
        """,
        (legacy_status,),
    )
    renewal_db.commit()
    request = _create(renewal_db)
    assert request["request_status"] == "awaiting_upload"


def test_active_request_blocks_second_canonical_request_for_new_version_same_slot(
    renewal_db,
):
    first = _create(renewal_db, request_id="renewal-v1")
    second_alert_id = _supersede_primary_document(renewal_db)
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.create_renewal_request(
            renewal_db,
            second_alert_id,
            reason="expired",
            actor=OFFICER,
            now=NOW,
            request_id="renewal-v2",
            feature_flags=Flags(True),
        )
    assert exc.value.code == "duplicate_active_request"
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_requests WHERE request_status <> 'cancelled'",
    ) == 1
    assert first["document_slot_key"] == "passport:entity"


def test_legacy_request_for_prior_version_blocks_canonical_same_slot(
    renewal_db,
):
    renewal_db.execute(
        """
        INSERT INTO application_enhanced_requirements(
            monitoring_alert_id, application_id, active, status,
            monitoring_document_id, linked_document_id
        ) VALUES (1, 'app-1', 1, 'requested', 'doc-1', 'doc-1')
        """
    )
    renewal_db.commit()
    second_alert_id = _supersede_primary_document(renewal_db)
    with pytest.raises(renewal.RenewalError) as exc:
        renewal.create_renewal_request(
            renewal_db,
            second_alert_id,
            reason="expired",
            actor=OFFICER,
            now=NOW,
            request_id="renewal-v2",
            feature_flags=Flags(True),
        )
    assert exc.value.code == "legacy_request_conflict"
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_requests",
    ) == 0


def test_eligibility_cursor_advances_past_blocked_prefix(renewal_db):
    renewal_db.execute(
        "UPDATE monitoring_alerts SET source_reference='document:missing' WHERE id=1"
    )
    _add_document_alert(
        renewal_db,
        alert_id=2,
        app_id="app-2",
        document_id="doc-2",
    )
    first = renewal.generate_eligible_renewal_requests(
        renewal_db,
        actor=SCHEDULER,
        limit=1,
        now=NOW,
        feature_flags=Flags(True),
    )
    second = renewal.generate_eligible_renewal_requests(
        renewal_db,
        actor=SCHEDULER,
        limit=1,
        now=NOW,
        feature_flags=Flags(True),
    )
    assert first["blocked"] == 1
    assert first["blocked_alerts"][0]["alert_id"] == "1"
    assert second["created"] == 1
    assert renewal.renewal_request_for_alert(renewal_db, 2) is not None


@pytest.mark.parametrize(
    "failure_code", ["notification_unavailable", "audit_unavailable"]
)
def test_eligibility_cursor_advances_past_deferrable_delivery_failure(
    renewal_db, monkeypatch, failure_code
):
    _add_document_alert(
        renewal_db,
        alert_id=2,
        app_id="app-2",
        document_id="doc-2",
    )

    call_options = {}
    fail_once = {"pending": True}
    if failure_code == "notification_unavailable":
        publish = renewal._publish_portal_notification

        def fail_first_notification(db, request, **kwargs):
            if fail_once["pending"] and request["monitoring_alert_id"] == 1:
                fail_once["pending"] = False
                raise renewal.RenewalError(
                    "notification_unavailable", "Notification unavailable."
                )
            return publish(db, request, **kwargs)

        monkeypatch.setattr(
            renewal, "_publish_portal_notification", fail_first_notification
        )
    else:

        def fail_first_audit(db, **kwargs):
            if fail_once["pending"] and kwargs["target"].startswith(
                "monitoring_renewal_request:"
            ):
                fail_once["pending"] = False
                raise renewal.RenewalError(
                    "audit_unavailable", "Audit unavailable."
                )
            return append_audit_log(db, **kwargs)

        call_options["audit_writer"] = fail_first_audit

    first = renewal.generate_eligible_renewal_requests(
        renewal_db,
        actor=SCHEDULER,
        limit=1,
        now=NOW,
        feature_flags=Flags(True),
        **call_options,
    )
    assert first["blocked_alerts"] == [
        {"alert_id": "1", "code": failure_code}
    ]
    assert first["degraded"] is True
    assert first["degraded_failures"] == first["blocked_alerts"]
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_requests",
    ) == 0
    assert _scalar(
        renewal_db,
        "SELECT COUNT(*) FROM monitoring_document_renewal_events",
    ) == 0
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM client_notifications") == 0
    assert _scalar(renewal_db, "SELECT COUNT(*) FROM audit_log") == 0
    second = renewal.generate_eligible_renewal_requests(
        renewal_db,
        actor=SCHEDULER,
        limit=1,
        now=NOW,
        feature_flags=Flags(True),
        **call_options,
    )
    retry = renewal.generate_eligible_renewal_requests(
        renewal_db,
        actor=SCHEDULER,
        limit=1,
        now=NOW,
        feature_flags=Flags(True),
        **call_options,
    )

    assert second["created"] == 1
    assert renewal.renewal_request_for_alert(renewal_db, 2) is not None
    assert second["degraded"] is False
    assert retry["created"] == 1
    assert renewal.renewal_request_for_alert(renewal_db, 1) is not None
    assert retry["degraded"] is False


def test_reminder_cursor_advances_past_blocked_prefix(renewal_db):
    blocked_request = _create(
        renewal_db,
        request_id="a-blocked",
        due_date="2026-08-16",
        now=NOW - timedelta(days=1),
    )
    _add_document_alert(
        renewal_db,
        alert_id=2,
        app_id="app-2",
        document_id="doc-2",
    )
    valid_request = renewal.create_renewal_request(
        renewal_db,
        2,
        reason="expired",
        actor=OFFICER,
        request_id="b-valid",
        due_date="2026-08-16",
        now=NOW - timedelta(days=1),
        feature_flags=Flags(True),
    )
    renewal_db.execute("UPDATE documents SET version=2 WHERE id='doc-1'")
    renewal_db.commit()
    first = renewal.generate_due_reminders(
        renewal_db,
        actor=SCHEDULER,
        limit=1,
        now=NOW,
        feature_flags=Flags(True),
    )
    second = renewal.generate_due_reminders(
        renewal_db,
        actor=SCHEDULER,
        limit=1,
        now=NOW,
        feature_flags=Flags(True),
    )
    assert first["blocked_requests"] == [
        {"request_id": blocked_request["request_id"], "code": "stale_linkage"}
    ]
    assert second["request_ids"] == [valid_request["request_id"]]


@pytest.mark.parametrize(
    "failure_code", ["notification_unavailable", "audit_unavailable"]
)
def test_reminder_cursor_advances_past_deferrable_delivery_failure(
    renewal_db, monkeypatch, failure_code
):
    blocked_request = _create(
        renewal_db,
        request_id="a-blocked",
        due_date="2026-08-16",
        now=NOW - timedelta(days=1),
    )
    _add_document_alert(
        renewal_db,
        alert_id=2,
        app_id="app-2",
        document_id="doc-2",
    )
    valid_request = renewal.create_renewal_request(
        renewal_db,
        2,
        reason="expired",
        actor=OFFICER,
        request_id="b-valid",
        due_date="2026-08-16",
        now=NOW - timedelta(days=1),
        feature_flags=Flags(True),
    )

    call_options = {}
    fail_once = {"pending": True}
    if failure_code == "notification_unavailable":
        publish = renewal._publish_portal_notification

        def fail_first_notification(db, request, **kwargs):
            if (
                fail_once["pending"]
                and request["request_id"] == blocked_request["request_id"]
            ):
                fail_once["pending"] = False
                raise renewal.RenewalError(
                    "notification_unavailable", "Notification unavailable."
                )
            return publish(db, request, **kwargs)

        monkeypatch.setattr(
            renewal, "_publish_portal_notification", fail_first_notification
        )
    else:
        def fail_first_audit(db, **kwargs):
            if (
                fail_once["pending"]
                and kwargs["target"].endswith(blocked_request["request_id"])
            ):
                fail_once["pending"] = False
                raise renewal.RenewalError(
                    "audit_unavailable", "Audit unavailable."
                )
            return append_audit_log(db, **kwargs)

        call_options["audit_writer"] = fail_first_audit

    first = renewal.generate_due_reminders(
        renewal_db,
        actor=SCHEDULER,
        limit=1,
        now=NOW,
        feature_flags=Flags(True),
        **call_options,
    )
    second = renewal.generate_due_reminders(
        renewal_db,
        actor=SCHEDULER,
        limit=1,
        now=NOW,
        feature_flags=Flags(True),
        **call_options,
    )
    retry = renewal.generate_due_reminders(
        renewal_db,
        actor=SCHEDULER,
        limit=1,
        now=NOW,
        feature_flags=Flags(True),
        **call_options,
    )

    assert first["blocked_requests"] == [
        {"request_id": blocked_request["request_id"], "code": failure_code}
    ]
    assert first["degraded"] is True
    assert first["degraded_failures"] == first["blocked_requests"]
    assert second["request_ids"] == [valid_request["request_id"]]
    assert second["degraded"] is False
    assert retry["request_ids"] == [blocked_request["request_id"]]
    assert retry["degraded"] is False


def test_portal_notifications_track_resend_due_change_and_cancellation(renewal_db):
    request = _create(renewal_db, request_id="renewal-notifications")
    request = renewal.resend_renewal_request(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=request["revision"],
        now=NOW,
        feature_flags=Flags(True),
    )
    request = renewal.update_renewal_due_date(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=request["revision"],
        due_date="2026-08-20",
        reason="Approved extension.",
        now=NOW,
        feature_flags=Flags(True),
    )
    renewal.cancel_renewal_request(
        renewal_db,
        request["request_id"],
        actor=OFFICER,
        expected_revision=request["revision"],
        cancel_reason="No longer required.",
        now=NOW,
        feature_flags=Flags(True),
    )
    types = [
        row["notification_type"]
        for row in renewal_db.execute(
            "SELECT notification_type FROM client_notifications ORDER BY id"
        ).fetchall()
    ]
    assert types == [
        "document_renewal_request",
        "document_renewal_request_resent",
        "document_renewal_due_date_changed",
        "document_renewal_request_cancelled",
    ]


def test_canonical_policy_constants_are_shared_not_copied():
    from document_health_monitor import DOCUMENT_EXPIRING_SOON_DAYS
    from monitoring_alert_state_machine import ACTIVE_STATUSES
    from monitoring_document_refresh import ACTIVE_REQUEST_STATUSES as LEGACY_ACTIVE

    assert renewal.EXPIRING_SOON_DAYS == DOCUMENT_EXPIRING_SOON_DAYS
    assert renewal.ACTIVE_ALERT_STATUSES is ACTIVE_STATUSES
    assert renewal.LEGACY_DOCUMENT_REFRESH_ACTIVE_STATUSES == LEGACY_ACTIVE


@pytest.mark.skipif(
    not (os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")),
    reason="PostgreSQL DSN not available",
)
def test_live_postgres_create_reminder_and_jsonb_history(monkeypatch):
    """Exercise the real PostgreSQL transaction, row locks and JSONB reads."""

    from tests.test_migration_chain_full import _fresh_pg

    db_module, db = _fresh_pg(monkeypatch)
    try:
        db.execute(
            """INSERT INTO clients(id, email, password_hash, company_name)
               VALUES (?, ?, ?, ?)""",
            ("renewal-pg-client", "renewal-pg@example.test", "unused", "PG Renewal Ltd"),
        )
        db.execute(
            """INSERT INTO applications(id, ref, client_id, company_name)
               VALUES (?, ?, ?, ?)""",
            ("renewal-pg-app", "RENEWAL-PG", "renewal-pg-client", "PG Renewal Ltd"),
        )
        db.execute(
            """INSERT INTO documents(
                   id, application_id, doc_type, doc_name, file_path,
                   slot_key, is_current, version, expiry_date,
                   verification_status, review_status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "renewal-pg-doc",
                "renewal-pg-app",
                "passport",
                "passport.pdf",
                "staged/passport.pdf",
                "passport:entity",
                True,
                1,
                "2026-07-01T00:00:00Z",
                "flagged",
                "pending",
            ),
        )
        alert = db.execute(
            """INSERT INTO monitoring_alerts(
                   application_id, client_name, alert_type, severity,
                   detected_by, summary, source_reference, status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               RETURNING id""",
            (
                "renewal-pg-app",
                "PG Renewal Ltd",
                "document_expired",
                "medium",
                "document_health",
                "Expired passport",
                "document:renewal-pg-doc",
                "open",
            ),
        ).fetchone()
        db.commit()

        request = renewal.create_renewal_request(
            db,
            alert["id"],
            reason="expired",
            actor=OFFICER,
            due_date="2026-08-16",
            now=NOW - timedelta(days=1),
            request_id="renewal-pg-request",
            feature_flags=Flags(True),
        )
        assert request["revision"] == 2
        assert request["due_date"] == "2026-08-16"

        summary = renewal.generate_due_reminders(
            db,
            actor=SCHEDULER,
            now=NOW,
            feature_flags=Flags(True),
        )
        assert summary["generated"] == 1
        detailed = renewal.get_renewal_request(db, request["request_id"])
        reminder = next(
            event
            for event in detailed["events"]
            if event["event_type"] == "reminder_generated"
        )
        assert reminder["payload"]["email_delivery_performed"] is False
        assert reminder["payload"]["delivery_channel"] == "client_portal"
        assert reminder["payload"]["portal_notification_id"]
        assert reminder["payload"]["days_until_due"] == 14

        client_actor = {
            "sub": "renewal-pg-client",
            "name": "PG Renewal Client",
            "role": "client",
            "type": "client",
        }
        upload_kwargs = {
            "actor": client_actor,
            "expected_revision": request["revision"],
            "upload_id": "renewal-pg-upload",
            "original_filename": "renewed-passport.pdf",
            "storage_key": (
                "renewals/renewal-pg-app/renewal-pg-request/renewal-pg-upload"
            ),
            "file_size": 2048,
            "mime_type": "application/pdf",
            "file_sha256": "d" * 64,
            "expected_application_id": "renewal-pg-app",
            "expected_customer_id": "renewal-pg-client",
            "expected_person_id": None,
            "expected_original_document_id": "renewal-pg-doc",
            "expected_document_type": "passport",
            "now": NOW,
            "feature_flags": Flags(True),
        }
        upload_kwargs["artifact_reservation_id"] = _stored_upload_artifact(
            db,
            request,
            upload_id=upload_kwargs["upload_id"],
            storage_key=upload_kwargs["storage_key"],
            cleanup_id="renewal-pg-artifact",
        )

        uploaded = renewal.record_renewal_upload(
            db,
            request["request_id"],
            **upload_kwargs,
        )
        assert uploaded["request_status"] == "upload_received"
        assert uploaded["binding_current"] is True
        binding = dict(
            db.execute(
                """SELECT *
                     FROM monitoring_document_renewal_upload_bindings
                    WHERE renewal_request_id = ?""",
                (request["request_id"],),
            ).fetchone()
        )
        assert binding["upload_id"] == "renewal-pg-upload"
        assert binding["renewal_request_id"] == "renewal-pg-request"
        assert binding["application_id"] == "renewal-pg-app"
        assert binding["customer_id"] == "renewal-pg-client"
        assert binding["person_id"] is None
        assert binding["person_type"] is None
        assert binding["original_document_id"] == "renewal-pg-doc"
        assert binding["original_document_version"] == 1
        assert binding["uploaded_document_id"] == (
            "renewal-candidate:renewal-pg-upload"
        )
        assert binding["document_type"] == "passport"
        assert binding["binding_status"] == "bound"
        assert binding["contract_version"] == (
            "monitoring_document_renewal_upload_binding_v1"
        )
        assert len(binding["binding_fingerprint"]) == 64
        assert binding["binding_fingerprint"] == binding["binding_fingerprint"].lower()

        assert db.execute(
            """SELECT COUNT(*) AS c
                 FROM monitoring_document_renewal_events
                WHERE request_id = ? AND event_type = 'upload_received'""",
            (request["request_id"],),
        ).fetchone()["c"] == 1
        assert db.execute(
            """SELECT COUNT(*) AS c
                 FROM audit_log
                WHERE action = 'monitoring.document_renewal.upload_bound'
                  AND target = ?""",
            ("monitoring_renewal_upload_binding:renewal-pg-upload",),
        ).fetchone()["c"] == 1

        retried = renewal.record_renewal_upload(
            db,
            request["request_id"],
            **upload_kwargs,
        )
        assert retried["idempotent"] is True
        for table in (
            "monitoring_document_renewal_uploads",
            "monitoring_document_renewal_upload_bindings",
        ):
            assert db.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE upload_id = ?",
                ("renewal-pg-upload",),
            ).fetchone()["c"] == 1
        assert db.execute(
            """SELECT COUNT(*) AS c
                 FROM monitoring_document_renewal_events
                WHERE request_id = ? AND event_type = 'upload_received'""",
            (request["request_id"],),
        ).fetchone()["c"] == 1
        assert db.execute(
            """SELECT COUNT(*) AS c
                 FROM audit_log
                WHERE action = 'monitoring.document_renewal.upload_bound'
                  AND target = ?""",
            ("monitoring_renewal_upload_binding:renewal-pg-upload",),
        ).fetchone()["c"] == 1
    finally:
        db.close()
        db_module.close_pg_pool()
