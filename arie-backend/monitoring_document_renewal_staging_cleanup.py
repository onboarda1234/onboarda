"""Staging-only cleanup and fingerprint boundary for the renewal QA fixture.

This module is deliberately not imported by ``server.py``.  It is an operator
surface for one governed synthetic fixture and has no generic application or
table-wipe mode.

The permanent fixture root (client, application, person, canonical document
and Monitoring Alert) is never deleted.  Cleanup removes only the transient
renewal request graph and its exact portal notification after first deleting
the identity-bound staged candidate.  S3 cleanup inventories every version and
delete marker for the exact key and succeeds only when no residue remains.  The
regulated workflow/audit records are handled differently on purpose:

* transient renewal events are removed with their request;
* canonical ``audit_log`` rows are append-only and are never removed;
* one final hash-chained cleanup entry records the exact deletion plan.

The public call sequence used by the staging operator is::

    before = capture_renewal_harness_fingerprint(db, fixture, run_id=run_id)
    plan = build_renewal_harness_cleanup_plan(db, fixture, run_id=run_id)
    result = cleanup_renewal_harness_operational_state(
        db,
        fixture,
        run_id=run_id,
        expected_plan_fingerprint=plan["fingerprint"],
        actor_id="operator-id",
        confirmation=CLEANUP_CONFIRMATION,
        expected_database_fingerprint="...",
    )

``fixture`` is the normalized mapping returned by
``fixtures.document_renewal_staging.fixture_spec`` after its permanent alert
ID has been resolved.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import unquote, urlsplit

from regulated_deletion import (
    FIXTURE_CLEANUP_CONFIRMATION,
    is_verified_disposable_postgres_test_db,
    is_verified_isolated_test_database,
)
from fixtures.cleanup import fixture_cleanup_context


CONTRACT_VERSION = "monitoring_document_renewal_staging_cleanup_v1"
CLEANUP_CONFIRMATION = "CLEAN-DOCUMENT-RENEWAL-STAGING-FIXTURE"
STAGING_DATABASE_FINGERPRINT_ENV = (
    "DOCUMENT_RENEWAL_HARNESS_STAGING_DATABASE_FINGERPRINT"
)
HARNESS_ADVISORY_LOCK_KEY = 8_674_309_941
STAGING_ARTIFACT_BUCKET = "regmind-documents-staging"
STAGING_ARTIFACT_REGION = "af-south-1"
ARTIFACT_LEASE_SAFETY_MARGIN = timedelta(minutes=5)

FIXTURE_SOURCE = "fixtures.document_renewal_staging"
FIXTURE_CONTRACT_VERSION = "document_renewal_staging_fixture_v1"

CLEANUP_DELETE_ORDER = (
    "monitoring_document_renewal_upload_bindings",
    "monitoring_document_renewal_uploads",
    "monitoring_document_renewal_upload_cleanup",
    "monitoring_document_renewal_events",
    "monitoring_document_renewal_requests",
    "client_notifications",
    "shared_rate_limits",
)

REGULATED_CLEANUP_TABLES = tuple(
    table
    for table in CLEANUP_DELETE_ORDER
    if table not in {"client_notifications", "shared_rate_limits"}
)

_RENEWAL_NOTIFICATION_TYPES = frozenset(
    {
        "document_renewal_request",
        "document_renewal_request_resent",
        "document_renewal_due_date_changed",
        "document_renewal_request_cancelled",
        "document_renewal_reminder",
    }
)
_HARNESS_EVENT_TYPES = (
    "request_created",
    "request_sent",
    "upload_received",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_JSON_COLUMNS = frozenset(
    {
        "after_state",
        "before_state",
        "checks_json",
        "detail",
        "documents_list",
        "flags_json",
        "payload",
        "prescreening_data",
        "risk_dimensions",
    }
)
_BOOLEAN_COLUMNS = frozenset(
    {
        "is_current",
        "is_fixture",
        "read_status",
        "requires_review",
    }
)


class RenewalHarnessCleanupError(RuntimeError):
    """Fail-closed staging harness reconciliation error."""


def _mapping(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise RenewalHarnessCleanupError("fixture specification must be a mapping")


def _required_text(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise RenewalHarnessCleanupError(f"fixture specification is missing {name}")
    return normalized


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_fixture(fixture: Any) -> dict[str, Any]:
    raw = _mapping(fixture)
    # The operator may supply only the database-assigned alert id. Every
    # stable identity comes from the reviewed, versioned fixture manifest.
    # Refusing caller-defined fixture coordinates prevents this narrow cleanup
    # surface from being redirected at another row merely marked is_fixture.
    from fixtures.document_renewal_staging import fixture_spec

    authoritative = fixture_spec()
    fixture_version = _required_text(
        authoritative.get("fixture_version"), "fixture_version"
    )
    if fixture_version != FIXTURE_CONTRACT_VERSION:
        raise RenewalHarnessCleanupError(
            f"unsupported authoritative fixture contract: {fixture_version}"
        )
    expected_fields = {
        "fixture_key": authoritative["fixture_key"],
        "fixture_marker": authoritative["fixture_marker"],
        "fixture_version": fixture_version,
        "manifest_sha256": authoritative["manifest_sha256"],
        "customer_id": authoritative["customer_id"],
        "application_id": authoritative["application_id"],
        "application_ref": authoritative["application_ref"],
        "person_id": authoritative.get("person_id"),
        "person_type": authoritative.get("person_type"),
        "document_id": authoritative["document_id"],
        "document_type": authoritative["document_type"],
        "document_version": authoritative["document_version"],
        "document_slot_key": authoritative.get("document_slot_key"),
        "alert_type": authoritative["alert_type"],
        "source_reference": authoritative["alert_source_reference"],
        "fixture_source": authoritative["source"],
        "request_id": authoritative["request_id"],
    }
    raw_aliases = {
        "fixture_version": (
            raw.get("fixture_version")
            or raw.get("fixture_contract_version")
            or raw.get("contract_version")
        ),
        "source_reference": (
            raw.get("source_reference") or raw.get("alert_source_reference")
        ),
        "fixture_source": raw.get("fixture_source") or raw.get("source"),
        "application_ref": raw.get("application_ref") or raw.get("ref"),
        "document_slot_key": raw.get("document_slot_key") or raw.get("slot_key"),
    }
    for field, expected_value in expected_fields.items():
        supplied = raw_aliases.get(field, raw.get(field))
        if supplied is None or str(supplied) != str(expected_value):
            raise RenewalHarnessCleanupError(
                f"fixture specification does not match the reviewed manifest at {field}"
            )
    try:
        alert_id = int(raw.get("alert_id") or raw.get("monitoring_alert_id"))
    except (TypeError, ValueError) as exc:
        raise RenewalHarnessCleanupError(
            "fixture alert_id must be an exact integer"
        ) from exc
    if alert_id < 1:
        raise RenewalHarnessCleanupError("fixture alert_id must be positive")
    normalized = {
        "fixture_key": expected_fields["fixture_key"],
        "fixture_marker": expected_fields["fixture_marker"],
        "fixture_version": fixture_version,
        "fixture_source": expected_fields["fixture_source"],
        "manifest_sha256": expected_fields["manifest_sha256"],
        "customer_id": expected_fields["customer_id"],
        "application_id": expected_fields["application_id"],
        "application_ref": expected_fields["application_ref"],
        "person_id": expected_fields["person_id"],
        "person_type": expected_fields["person_type"],
        "document_id": expected_fields["document_id"],
        "document_type": expected_fields["document_type"],
        "document_version": expected_fields["document_version"],
        "document_slot_key": expected_fields["document_slot_key"],
        "alert_id": alert_id,
        "alert_type": expected_fields["alert_type"],
        "source_reference": expected_fields["source_reference"],
        "request_id": expected_fields["request_id"],
    }
    if not _SHA256_RE.fullmatch(normalized["manifest_sha256"]):
        raise RenewalHarnessCleanupError("manifest_sha256 must be lowercase SHA-256")
    return normalized


def _row(value: Any) -> Optional[dict[str, Any]]:
    return dict(value) if value is not None else None


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value]


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _canonical_scalar(column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in _BOOLEAN_COLUMNS:
        return _truthy(value)
    if column in _JSON_COLUMNS:
        if isinstance(value, (Mapping, list, tuple)):
            return _canonical_value(value)
        try:
            return _canonical_value(json.loads(str(value)))
        except (TypeError, ValueError):
            return str(value)
    if column == "due_date" or column.endswith("_date"):
        if isinstance(value, datetime):
            return value.date().isoformat()
        return str(value).strip().split("T", 1)[0].split(" ", 1)[0]
    if isinstance(value, datetime):
        current = value
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return {"byte_length": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if column.endswith("_at") and isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return value


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_scalar(str(key), item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return _canonical_scalar("", value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _digest_rows(rows: Iterable[Mapping[str, Any]], *, order_key: str) -> dict[str, Any]:
    normalized = [_canonical_value(dict(item)) for item in rows]
    normalized.sort(
        key=lambda item: (
            str(item.get(order_key) if isinstance(item, Mapping) else ""),
            _canonical_json(item),
        )
    )
    digest = hashlib.sha256()
    for item in normalized:
        digest.update(_canonical_json(item).encode("utf-8"))
        digest.update(b"\n")
    return {"row_count": len(normalized), "sha256": digest.hexdigest()}


def _fingerprint_descriptors(descriptors: Mapping[str, Mapping[str, Any]]) -> str:
    return hashlib.sha256(_canonical_json(descriptors).encode("utf-8")).hexdigest()


def _audit_evidence_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    actions: dict[str, int] = {}
    chained = 0
    unchained = 0
    for row in rows:
        action = str(row.get("action") or "").strip()
        if not action.startswith("monitoring.document_renewal."):
            raise RenewalHarnessCleanupError(
                "fixture renewal audit scope contains an unexpected action"
            )
        actions[action] = actions.get(action, 0) + 1
        entry_hash = str(row.get("entry_hash") or "").strip().lower()
        if entry_hash and not _SHA256_RE.fullmatch(entry_hash):
            raise RenewalHarnessCleanupError(
                "fixture renewal audit contains a malformed entry hash"
            )
        if entry_hash:
            chained += 1
        else:
            unchained += 1
    return {
        "action_counts": dict(sorted(actions.items())),
        "chained_count": chained,
        "unchained_count": unchained,
    }


def _assert_fixture_identity(db: Any, fixture: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    from fixtures.document_renewal_staging import fixture_spec, load_manifest

    authoritative = fixture_spec()
    manifest = load_manifest()
    client = _row(
        db.execute(
            "SELECT * FROM clients WHERE id = ?", (fixture["customer_id"],)
        ).fetchone()
    )
    application = _row(
        db.execute(
        "SELECT * FROM applications WHERE id = ? AND ref = ?",
        (fixture["application_id"], fixture["application_ref"]),
        ).fetchone()
    )
    document = _row(
        db.execute(
        "SELECT * FROM documents WHERE id = ? AND application_id = ?",
        (fixture["document_id"], fixture["application_id"]),
        ).fetchone()
    )
    alert = _row(
        db.execute(
        "SELECT * FROM monitoring_alerts WHERE id = ? AND application_id = ?",
        (fixture["alert_id"], fixture["application_id"]),
        ).fetchone()
    )
    if not client or not application or not document or not alert:
        raise RenewalHarnessCleanupError("permanent renewal fixture identity is incomplete")
    mismatches: list[str] = []

    def expect(label: str, actual: Any, expected: Any, *, column: str = "") -> None:
        if _canonical_scalar(column, actual) != _canonical_scalar(column, expected):
            mismatches.append(label)

    expected_client = manifest["client"]
    expect("client.id", client.get("id"), authoritative["customer_id"])
    expect("client.email", client.get("email"), authoritative["customer_email"])
    expect("client.company_name", client.get("company_name"), expected_client["company_name"])
    expect("client.status", client.get("status"), expected_client["status"])
    expect("client.password_hash", client.get("password_hash"), "fixture-no-login")

    expected_application = manifest["application"]
    for field, expected in {
        "id": authoritative["application_id"],
        "ref": authoritative["application_ref"],
        "client_id": authoritative["customer_id"],
        "company_name": expected_application["company_name"],
        "country": expected_application["country"],
        "sector": expected_application["sector"],
        "entity_type": expected_application["entity_type"],
        "risk_level": expected_application["risk_level"],
        "status": expected_application["status"],
    }.items():
        expect(f"application.{field}", application.get(field), expected)
    try:
        if Decimal(str(application.get("risk_score"))) != Decimal(
            str(expected_application["risk_score"])
        ):
            mismatches.append("application.risk_score")
    except Exception:
        mismatches.append("application.risk_score")
    if not _truthy(application.get("is_fixture")):
        mismatches.append("application.is_fixture")
    if _json_object(application.get("risk_dimensions")) != {
        "fixture": fixture["fixture_key"]
    }:
        mismatches.append("application.risk_dimensions")
    marker = _json_object(application.get("prescreening_data"))
    expected_marker = {
        "fixture_key": fixture["fixture_key"],
        "fixture_marker": fixture["fixture_marker"],
        "fixture_contract_version": fixture["fixture_version"],
        "manifest_sha256": fixture["manifest_sha256"],
        "source": fixture["fixture_source"],
        "synthetic": True,
        "non_production": True,
    }
    if marker != expected_marker:
        mismatches.append("application.prescreening_data")

    expected_document = manifest["document"]
    for field, expected in {
        "id": authoritative["document_id"],
        "application_id": authoritative["application_id"],
        "person_id": authoritative["person_id"],
        "person_type": authoritative["person_type"],
        "doc_type": authoritative["document_type"],
        "doc_name": expected_document["doc_name"],
        "file_path": expected_document["file_path"],
        "slot_key": authoritative["document_slot_key"],
        "version": authoritative["document_version"],
        "expiry_source": expected_document["expiry_source"],
        "verification_status": expected_document["verification_status"],
        "review_status": expected_document["review_status"],
        "uploaded_by_actor_type": "system",
        "uploaded_by_actor_id": "fixture_seed",
        "uploaded_by_display": "Document Renewal staging fixture",
        "upload_source": "fixture_seed",
    }.items():
        expect(f"document.{field}", document.get(field), expected)
    if not _truthy(document.get("is_current")):
        mismatches.append("document.is_current")
    if document.get("superseded_at") not in (None, "") or document.get(
        "superseded_by_document_id"
    ) not in (None, ""):
        mismatches.append("document.version_chain")
    expect(
        "document.expiry_date",
        document.get("expiry_date"),
        authoritative["document_expiry_date"],
        column="expiry_date",
    )
    expect(
        "document.uploaded_at",
        document.get("uploaded_at"),
        expected_document["uploaded_at"],
        column="uploaded_at",
    )
    if _json_object(document.get("verification_results")) != {
        "fixture": fixture["fixture_key"]
    }:
        mismatches.append("document.verification_results")

    expected_alert = manifest["alert"]
    for field, expected in {
        "application_id": authoritative["application_id"],
        "client_name": expected_application["company_name"],
        "alert_type": authoritative["alert_type"],
        "severity": expected_alert["severity"],
        "detected_by": expected_alert["detected_by"],
        "summary": expected_alert["summary"],
        "source_reference": authoritative["alert_source_reference"],
        "status": authoritative["alert_status"],
        "discovered_via": expected_alert["discovered_via"],
    }.items():
        expect(f"alert.{field}", alert.get(field), expected)

    person: Optional[dict[str, Any]] = None
    if fixture["person_id"]:
        person_params = (fixture["person_id"], fixture["application_id"])
        if fixture["person_type"] == "director":
            person = _row(
                db.execute(
                    "SELECT * FROM directors WHERE id = ? AND application_id = ?",
                    person_params,
                ).fetchone()
            )
        elif fixture["person_type"] == "ubo":
            person = _row(
                db.execute(
                    "SELECT * FROM ubos WHERE id = ? AND application_id = ?",
                    person_params,
                ).fetchone()
            )
        elif fixture["person_type"] == "intermediary":
            person = _row(
                db.execute(
                    "SELECT * FROM intermediaries WHERE id = ? AND application_id = ?",
                    person_params,
                ).fetchone()
            )
        else:
            raise RenewalHarnessCleanupError("unsupported fixture person type")
        if not person:
            mismatches.append("person.ownership")
        else:
            expected_person = manifest["person"]
            for field, expected in {
                "id": authoritative["person_id"],
                "application_id": authoritative["application_id"],
                "person_key": authoritative["person_key"],
                "full_name": expected_person["full_name"],
                "nationality": expected_person["nationality"],
            }.items():
                expect(f"person.{field}", person.get(field), expected)
            if _truthy(person.get("is_pep")):
                mismatches.append("person.is_pep")
            if _json_object(person.get("pep_declaration")) != {
                "fixture": fixture["fixture_key"]
            }:
                mismatches.append("person.pep_declaration")

    application_id = fixture["application_id"]
    customer_id = fixture["customer_id"]
    # Keep every query literal so the repository's static Monitoring-write
    # enforcement can prove this baseline validator has no hidden SQL path.
    observed_cardinalities = {
        "client_applications": int(
            db.execute(
                "SELECT COUNT(*) AS count FROM applications WHERE client_id = ?",
                (customer_id,),
            ).fetchone()["count"]
        ),
        "application_directors": int(
            db.execute(
                "SELECT COUNT(*) AS count FROM directors WHERE application_id = ?",
                (application_id,),
            ).fetchone()["count"]
        ),
        "application_ubos": int(
            db.execute(
                "SELECT COUNT(*) AS count FROM ubos WHERE application_id = ?",
                (application_id,),
            ).fetchone()["count"]
        ),
        "application_intermediaries": int(
            db.execute(
                "SELECT COUNT(*) AS count FROM intermediaries WHERE application_id = ?",
                (application_id,),
            ).fetchone()["count"]
        ),
        "application_documents": int(
            db.execute(
                "SELECT COUNT(*) AS count FROM documents WHERE application_id = ?",
                (application_id,),
            ).fetchone()["count"]
        ),
        "application_alerts": int(
            db.execute(
                "SELECT COUNT(*) AS count FROM monitoring_alerts WHERE application_id = ?",
                (application_id,),
            ).fetchone()["count"]
        ),
        "application_agent_executions": int(
            db.execute(
                "SELECT COUNT(*) AS count FROM agent_executions WHERE application_id = ?",
                (application_id,),
            ).fetchone()["count"]
        ),
        "application_verification_jobs": int(
            db.execute(
                "SELECT COUNT(*) AS count FROM verification_jobs WHERE application_id = ?",
                (application_id,),
            ).fetchone()["count"]
        ),
        "application_legacy_requirements": int(
            db.execute(
                "SELECT COUNT(*) AS count FROM application_enhanced_requirements "
                "WHERE application_id = ?",
                (application_id,),
            ).fetchone()["count"]
        ),
    }
    expected_cardinalities = {
        "client_applications": 1,
        "application_directors": 1 if fixture["person_type"] == "director" else 0,
        "application_ubos": 1 if fixture["person_type"] == "ubo" else 0,
        "application_intermediaries": (
            1 if fixture["person_type"] == "intermediary" else 0
        ),
        "application_documents": 1,
        "application_alerts": 1,
        "application_agent_executions": 0,
        "application_verification_jobs": 0,
        "application_legacy_requirements": 0,
    }
    for label, expected_count in expected_cardinalities.items():
        observed = observed_cardinalities[label]
        if observed != expected_count:
            mismatches.append(label)

    if mismatches:
        raise RenewalHarnessCleanupError(
            "permanent renewal fixture baseline drift: "
            + ", ".join(sorted(set(mismatches)))
        )
    return {
        "client": client,
        "application": application,
        "person": person or {},
        "document": document,
        "monitoring_alert": alert,
    }


def _harness_rate_limit_key(fixture: Mapping[str, Any], run_id: str) -> str:
    from auth import build_shared_rate_limit_key

    return build_shared_rate_limit_key(
        "document_renewal_upload",
        fixture_request=fixture["request_id"],
        harness_run=run_id,
        user=fixture["customer_id"],
    )


def _relation_rows(
    db: Any,
    fixture: Mapping[str, Any],
    *,
    run_id: str,
) -> dict[str, list[dict[str, Any]]]:
    core = _assert_fixture_identity(db, fixture)
    app_id = fixture["application_id"]
    customer_id = fixture["customer_id"]
    alert_id = fixture["alert_id"]
    request_identity_params = (
        app_id,
        alert_id,
        fixture["document_id"],
        fixture["request_id"],
    )
    requests = _rows(
        db.execute(
        """
        SELECT * FROM monitoring_document_renewal_requests
         WHERE application_id = ? OR monitoring_alert_id = ? OR document_id = ?
            OR request_id = ?
         ORDER BY request_id
        """,
        request_identity_params,
        ).fetchall()
    )
    request_params = (fixture["request_id"],)
    renewal_events = _rows(
        db.execute(
            "SELECT * FROM monitoring_document_renewal_events WHERE request_id = ?",
            request_params,
        ).fetchall()
    )
    renewal_uploads = _rows(
        db.execute(
            "SELECT * FROM monitoring_document_renewal_uploads WHERE request_id = ?",
            request_params,
        ).fetchall()
    )
    renewal_upload_bindings = _rows(
        db.execute(
            "SELECT * FROM monitoring_document_renewal_upload_bindings "
            "WHERE renewal_request_id = ?",
            request_params,
        ).fetchall()
    )
    renewal_upload_cleanup = _rows(
        db.execute(
            "SELECT * FROM monitoring_document_renewal_upload_cleanup "
            "WHERE request_id = ?",
            request_params,
        ).fetchall()
    )

    notifications = _rows(
        db.execute(
        """
        SELECT * FROM client_notifications
         WHERE application_id = ? AND client_id = ?
           AND notification_type LIKE 'document_renewal_%%'
         ORDER BY id
        """,
        (app_id, customer_id),
        ).fetchall()
    )
    relations = {
        "fixture_client": [core["client"]],
        "fixture_application": [core["application"]],
        "fixture_person": [core["person"]] if core["person"] else [],
        "fixture_document": [core["document"]],
        "fixture_monitoring_alert": [core["monitoring_alert"]],
        # Complete same-application footprints close the gap between the exact
        # permanent roots above and the non-fixture (application_id <>)
        # isolation set below. An unexpected canonical document, alert,
        # person, or unrelated notification must change a baseline digest.
        "fixture_application_directors": _rows(
            db.execute(
                "SELECT * FROM directors WHERE application_id = ? ORDER BY id",
                (app_id,),
            ).fetchall()
        ),
        "fixture_application_ubos": _rows(
            db.execute(
                "SELECT * FROM ubos WHERE application_id = ? ORDER BY id",
                (app_id,),
            ).fetchall()
        ),
        "fixture_application_intermediaries": _rows(
            db.execute(
                "SELECT * FROM intermediaries WHERE application_id = ? ORDER BY id",
                (app_id,),
            ).fetchall()
        ),
        "fixture_application_documents": _rows(
            db.execute(
                "SELECT * FROM documents WHERE application_id = ? ORDER BY id",
                (app_id,),
            ).fetchall()
        ),
        "fixture_application_monitoring_alerts": _rows(
            db.execute(
                "SELECT * FROM monitoring_alerts WHERE application_id = ? ORDER BY id",
                (app_id,),
            ).fetchall()
        ),
        "fixture_application_other_notifications": _rows(
            db.execute(
            """
            SELECT * FROM client_notifications
             WHERE application_id = ?
               AND (
                    client_id IS NULL OR client_id <> ?
                    OR notification_type IS NULL
                    OR notification_type NOT LIKE 'document_renewal_%%'
               )
             ORDER BY id
            """,
            (app_id, customer_id),
            ).fetchall()
        ),
        "fixture_agent_executions": _rows(
            db.execute(
                "SELECT * FROM agent_executions WHERE application_id = ? ORDER BY id",
                (app_id,),
            ).fetchall()
        ),
        "fixture_verification_jobs": _rows(
            db.execute(
                "SELECT * FROM verification_jobs "
                "WHERE application_id = ? OR document_id = ? ORDER BY id",
                (app_id, fixture["document_id"]),
            ).fetchall()
        ),
        "fixture_legacy_requirements": _rows(
            db.execute(
            """
            SELECT * FROM application_enhanced_requirements
             WHERE application_id = ? OR monitoring_alert_id = ?
             ORDER BY id
            """,
            (app_id, alert_id),
            ).fetchall()
        ),
        "renewal_requests": requests,
        "renewal_events": renewal_events,
        "renewal_uploads": renewal_uploads,
        "renewal_upload_bindings": renewal_upload_bindings,
        "renewal_upload_cleanup": renewal_upload_cleanup,
        "renewal_notifications": notifications,
        "harness_rate_limit": _rows(
            db.execute(
                "SELECT * FROM shared_rate_limits WHERE key = ?",
                (_harness_rate_limit_key(fixture, run_id),),
            ).fetchall()
        ),
        "other_harness_rate_limits": _rows(
            db.execute(
                "SELECT * FROM shared_rate_limits "
                "WHERE key LIKE "
                "'shared:v1:document_renewal_upload:fixture_request:%%:"
                "harness_run:%%:user:%%' AND key <> ? ORDER BY key",
                (_harness_rate_limit_key(fixture, run_id),),
            ).fetchall()
        ),
        "scheduler_state": _rows(
            db.execute(
                "SELECT * FROM monitoring_document_renewal_scheduler_state "
                "ORDER BY scheduler_key"
            ).fetchall()
        ),
        "fixture_audit": _rows(
            db.execute(
            """
            SELECT * FROM audit_log
             WHERE application_id = ?
               AND action LIKE 'monitoring.document_renewal.%%'
             ORDER BY id
            """,
            (app_id,),
            ).fetchall()
        ),
        "nonfixture_monitoring_alerts": _rows(
            db.execute(
                "SELECT * FROM monitoring_alerts "
                "WHERE application_id IS NULL OR application_id <> ? ORDER BY id",
                (app_id,),
            ).fetchall()
        ),
        "nonfixture_documents": _rows(
            db.execute(
                "SELECT * FROM documents WHERE application_id <> ? ORDER BY id",
                (app_id,),
            ).fetchall()
        ),
        "nonfixture_agent_executions": _rows(
            db.execute(
                "SELECT * FROM agent_executions WHERE application_id <> ? ORDER BY id",
                (app_id,),
            ).fetchall()
        ),
        "nonfixture_verification_jobs": _rows(
            db.execute(
                "SELECT * FROM verification_jobs "
                "WHERE application_id <> ? AND document_id <> ? ORDER BY id",
                (app_id, fixture["document_id"]),
            ).fetchall()
        ),
        "nonfixture_renewal_requests": _rows(
            db.execute(
            """
            SELECT * FROM monitoring_document_renewal_requests
             WHERE application_id <> ? ORDER BY request_id
            """,
            (app_id,),
            ).fetchall()
        ),
        "nonfixture_renewal_uploads": _rows(
            db.execute(
            """
            SELECT * FROM monitoring_document_renewal_uploads
             WHERE application_id <> ? ORDER BY upload_id
            """,
            (app_id,),
            ).fetchall()
        ),
        "nonfixture_renewal_upload_bindings": _rows(
            db.execute(
            """
            SELECT * FROM monitoring_document_renewal_upload_bindings
             WHERE application_id <> ? ORDER BY upload_id
            """,
            (app_id,),
            ).fetchall()
        ),
        "nonfixture_renewal_upload_cleanup": _rows(
            db.execute(
            """
            SELECT * FROM monitoring_document_renewal_upload_cleanup
             WHERE application_id <> ? ORDER BY cleanup_id
            """,
            (app_id,),
            ).fetchall()
        ),
        "nonfixture_notifications": _rows(
            db.execute(
            """
            SELECT * FROM client_notifications
             WHERE application_id IS NULL OR application_id <> ?
             ORDER BY id
            """,
            (app_id,),
            ).fetchall()
        ),
        "nonfixture_audit": _rows(
            db.execute(
            """
            SELECT * FROM audit_log
             WHERE application_id IS NULL OR application_id <> ?
                OR action NOT LIKE 'monitoring.document_renewal.%%'
             ORDER BY id
            """,
            (app_id,),
            ).fetchall()
        ),
        "nonfixture_renewal_events": _rows(
            db.execute(
            """
            SELECT e.* FROM monitoring_document_renewal_events e
            JOIN monitoring_document_renewal_requests r ON r.request_id = e.request_id
            WHERE r.application_id <> ? ORDER BY e.event_id
            """,
            (app_id,),
            ).fetchall()
        ),
    }
    return relations


_ORDER_KEYS = {
    "fixture_client": "id",
    "fixture_application": "id",
    "fixture_person": "id",
    "fixture_document": "id",
    "fixture_monitoring_alert": "id",
    "fixture_application_directors": "id",
    "fixture_application_ubos": "id",
    "fixture_application_intermediaries": "id",
    "fixture_application_documents": "id",
    "fixture_application_monitoring_alerts": "id",
    "fixture_application_other_notifications": "id",
    "fixture_agent_executions": "id",
    "fixture_verification_jobs": "id",
    "fixture_legacy_requirements": "id",
    "renewal_requests": "request_id",
    "renewal_events": "event_id",
    "renewal_uploads": "upload_id",
    "renewal_upload_bindings": "upload_id",
    "renewal_upload_cleanup": "cleanup_id",
    "renewal_notifications": "id",
    "harness_rate_limit": "key",
    "other_harness_rate_limits": "key",
    "scheduler_state": "scheduler_key",
    "fixture_audit": "id",
    "nonfixture_monitoring_alerts": "id",
    "nonfixture_documents": "id",
    "nonfixture_agent_executions": "id",
    "nonfixture_verification_jobs": "id",
    "nonfixture_renewal_requests": "request_id",
    "nonfixture_renewal_events": "event_id",
    "nonfixture_renewal_uploads": "upload_id",
    "nonfixture_renewal_upload_bindings": "upload_id",
    "nonfixture_renewal_upload_cleanup": "cleanup_id",
    "nonfixture_notifications": "id",
    "nonfixture_audit": "id",
}

_CORE_RELATIONS = (
    "fixture_client",
    "fixture_application",
    "fixture_person",
    "fixture_document",
    "fixture_monitoring_alert",
    "fixture_application_directors",
    "fixture_application_ubos",
    "fixture_application_intermediaries",
    "fixture_application_documents",
    "fixture_application_monitoring_alerts",
    "fixture_application_other_notifications",
    "fixture_agent_executions",
    "fixture_verification_jobs",
    "fixture_legacy_requirements",
    "other_harness_rate_limits",
)
_OPERATIONAL_RELATIONS = (
    "renewal_requests",
    "renewal_events",
    "renewal_uploads",
    "renewal_upload_bindings",
    "renewal_upload_cleanup",
    "renewal_notifications",
    "harness_rate_limit",
)
_ISOLATION_RELATIONS = tuple(
    key for key in _ORDER_KEYS if key.startswith("nonfixture_")
)


def _runtime_feature_state() -> dict[str, bool]:
    from environment import (
        FeatureFlags,
        MONITORING_FEATURE_FLAGS,
        get_monitoring_feature_state,
    )

    environment = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    state = get_monitoring_feature_state(FeatureFlags(environment))
    return {name: bool(state[name]) for name in MONITORING_FEATURE_FLAGS}


def capture_renewal_harness_fingerprint(
    db: Any,
    fixture: Any,
    *,
    run_id: str,
    feature_state: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Capture sanitized, deterministic fixture and isolation fingerprints.

    Raw rows, storage keys, filenames, hashes, notification copy and audit
    details are never returned.  The audit descriptor is separate because a
    successful validation must append evidence and therefore cannot equal its
    pre-run value.
    """

    environment = (os.environ.get("ENVIRONMENT") or "development").strip().lower()
    if environment == "staging" and feature_state is not None:
        raise RenewalHarnessCleanupError(
            "staging fingerprints must evaluate feature flags from the runtime"
        )
    spec = _normalize_fixture(fixture)
    normalized_run_id = _required_text(run_id, "run_id")
    relations = _relation_rows(db, spec, run_id=normalized_run_id)
    descriptors = {
        name: _digest_rows(rows, order_key=_ORDER_KEYS[name])
        for name, rows in relations.items()
    }
    core = {name: descriptors[name] for name in _CORE_RELATIONS}
    operational = {name: descriptors[name] for name in _OPERATIONAL_RELATIONS}
    isolation = {name: descriptors[name] for name in _ISOLATION_RELATIONS}
    scheduler = {"scheduler_state": descriptors["scheduler_state"]}
    audit = {"fixture_audit": descriptors["fixture_audit"]}
    audit_summary = _audit_evidence_summary(relations["fixture_audit"])
    from db import verify_audit_log_chain

    chain = verify_audit_log_chain(db)
    audit_chain = {
        "verified": chain.get("verified") is True,
        "entries_checked": int(chain.get("entries_checked") or 0),
        "chained_rows": int(chain.get("chained_rows") or 0),
        "legacy_rows": int(chain.get("legacy_rows") or 0),
        "coverage_gaps": int(chain.get("coverage_gaps") or 0),
        "coverage_complete": chain.get("coverage_complete"),
        "broken_link_count": len(chain.get("broken_links") or []),
        "total_entries": int(chain.get("total_entries") or 0),
    }
    flags = {
        str(key): bool(value)
        for key, value in (feature_state or _runtime_feature_state()).items()
    }
    from environment import MONITORING_FEATURE_FLAGS

    all_flags_off = set(flags) == set(MONITORING_FEATURE_FLAGS) and not any(
        flags.values()
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "run_id": normalized_run_id,
        "fixture": {
            key: spec[key]
            for key in (
                "fixture_key",
                "fixture_marker",
                "fixture_version",
                "manifest_sha256",
                "customer_id",
                "application_id",
                "application_ref",
                "person_id",
                "person_type",
                "document_id",
                "document_type",
                "document_version",
                "alert_id",
                "request_id",
            )
        },
        "relations": descriptors,
        "core_fingerprint": _fingerprint_descriptors(core),
        "operational_fingerprint": _fingerprint_descriptors(operational),
        "isolation_fingerprint": _fingerprint_descriptors(isolation),
        "scheduler_fingerprint": _fingerprint_descriptors(scheduler),
        "audit_evidence_fingerprint": _fingerprint_descriptors(audit),
        "fixture_audit_actions": audit_summary["action_counts"],
        "fixture_audit_chained_count": audit_summary["chained_count"],
        "fixture_audit_unchained_count": audit_summary["unchained_count"],
        "audit_chain": audit_chain,
        "feature_state": flags,
        "all_monitoring_flags_off": all_flags_off,
    }


def _notification_ids_from_events(events: Iterable[Mapping[str, Any]]) -> list[str]:
    result: list[str] = []
    for event in events:
        payload = _json_object(event.get("payload"))
        value = str(payload.get("portal_notification_id") or "").strip()
        if value:
            result.append(value)
    if len(result) != len(set(result)):
        raise RenewalHarnessCleanupError("fixture event notification IDs are duplicated")
    return sorted(result)


def _validate_cleanup_graph(
    relations: Mapping[str, list[dict[str, Any]]],
    fixture: Mapping[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    requests = relations["renewal_requests"]
    events = relations["renewal_events"]
    uploads = relations["renewal_uploads"]
    bindings = relations["renewal_upload_bindings"]
    artifacts = relations["renewal_upload_cleanup"]
    notifications = relations["renewal_notifications"]
    rate_limits = relations["harness_rate_limit"]
    if len(rate_limits) > 1:
        raise RenewalHarnessCleanupError("fixture rate-limit state is ambiguous")
    if rate_limits:
        expected_key = _harness_rate_limit_key(fixture, run_id)
        # `_relation_rows` queries the exact key; nevertheless, retain an
        # explicit identity assertion before the destructive plan is built.
        if str(rate_limits[0].get("key") or "") != expected_key:
            raise RenewalHarnessCleanupError("fixture rate-limit identity drifted")
    if len(requests) > 1:
        raise RenewalHarnessCleanupError("fixture has more than one renewal request")
    if not requests:
        if any((events, uploads, bindings, artifacts, notifications, rate_limits)):
            raise RenewalHarnessCleanupError("fixture has orphaned renewal operational state")
        return {
            "already_clean": True,
            "request_ids": [],
            "event_ids": [],
            "upload_ids": [],
            "binding_ids": [],
            "cleanup_ids": [],
            "notification_ids": [],
            "rate_limit_keys": [],
        }

    request = requests[0]
    expected_request = {
        "request_id": fixture["request_id"],
        "application_id": fixture["application_id"],
        "customer_id": fixture["customer_id"],
        "person_id": fixture["person_id"],
        "person_type": fixture["person_type"],
        "document_id": fixture["document_id"],
        "document_type": fixture["document_type"],
        "document_version": fixture["document_version"],
        "monitoring_alert_id": fixture["alert_id"],
    }
    for key, expected in expected_request.items():
        actual = request.get(key)
        if key in {"document_version", "monitoring_alert_id"}:
            try:
                actual = int(actual)
            except (TypeError, ValueError):
                pass
        if actual != expected:
            raise RenewalHarnessCleanupError(f"fixture renewal request drifted at {key}")
    request_id = str(request["request_id"])
    if request.get("request_status") not in {"awaiting_upload", "upload_received"}:
        raise RenewalHarnessCleanupError("fixture renewal request has an unexpected status")

    ordered_events = sorted(events, key=lambda item: int(item.get("event_sequence") or 0))
    if any(str(item.get("request_id")) != request_id for item in ordered_events):
        raise RenewalHarnessCleanupError("fixture renewal event ownership drifted")
    event_types = [str(item.get("event_type") or "") for item in ordered_events]
    sequences = [int(item.get("event_sequence") or 0) for item in ordered_events]
    if event_types not in [
        list(_HARNESS_EVENT_TYPES[:2]),
        list(_HARNESS_EVENT_TYPES),
    ] or sequences != list(range(1, len(sequences) + 1)):
        raise RenewalHarnessCleanupError("fixture renewal event sequence is unexpected")

    expected_notification_ids = _notification_ids_from_events(ordered_events)
    actual_notification_ids = sorted(str(item.get("id")) for item in notifications)
    if actual_notification_ids != expected_notification_ids:
        raise RenewalHarnessCleanupError(
            "fixture portal notifications are not exactly owned by renewal events"
        )
    if any(
        str(item.get("notification_type") or "") not in _RENEWAL_NOTIFICATION_TYPES
        for item in notifications
    ):
        raise RenewalHarnessCleanupError("fixture portal notification type is unexpected")

    if len(uploads) > 1 or len(bindings) > 1 or len(artifacts) > 1:
        raise RenewalHarnessCleanupError("fixture upload graph is not one-to-one")
    if bool(uploads) != bool(bindings):
        raise RenewalHarnessCleanupError("fixture upload and binding are incomplete")
    if uploads and not artifacts:
        raise RenewalHarnessCleanupError("fixture bound upload has no cleanup ledger")
    if request.get("request_status") == "upload_received":
        if event_types != list(_HARNESS_EVENT_TYPES) or not uploads or not bindings:
            raise RenewalHarnessCleanupError("upload_received fixture graph is incomplete")
    elif uploads or bindings or event_types != list(_HARNESS_EVENT_TYPES[:2]):
        raise RenewalHarnessCleanupError("awaiting_upload fixture graph is inconsistent")

    upload_id: Optional[str] = None
    if uploads:
        upload = uploads[0]
        binding = bindings[0]
        upload_id = str(upload.get("upload_id") or "")
        expected_upload = {
            "request_id": request_id,
            "application_id": fixture["application_id"],
            "customer_id": fixture["customer_id"],
        }
        for key, expected in expected_upload.items():
            if upload.get(key) != expected:
                raise RenewalHarnessCleanupError(f"fixture upload drifted at {key}")
        expected_binding = {
            "upload_id": upload_id,
            "renewal_request_id": request_id,
            "application_id": fixture["application_id"],
            "customer_id": fixture["customer_id"],
            "person_id": fixture["person_id"],
            "person_type": fixture["person_type"],
            "original_document_id": fixture["document_id"],
            "original_document_version": fixture["document_version"],
            "document_type": fixture["document_type"],
            "binding_status": "bound",
            "uploaded_document_id": f"renewal-candidate:{upload_id}",
        }
        for key, expected in expected_binding.items():
            actual = binding.get(key)
            if key == "original_document_version":
                try:
                    actual = int(actual)
                except (TypeError, ValueError):
                    pass
            if actual != expected:
                raise RenewalHarnessCleanupError(f"fixture upload binding drifted at {key}")

    if artifacts:
        artifact = artifacts[0]
        expected_artifact = {
            "request_id": request_id,
            "application_id": fixture["application_id"],
            "customer_id": fixture["customer_id"],
        }
        if upload_id:
            expected_artifact["upload_id"] = upload_id
        for key, expected in expected_artifact.items():
            if artifact.get(key) != expected:
                raise RenewalHarnessCleanupError(f"fixture cleanup ledger drifted at {key}")
        if artifact.get("artifact_state") not in {
            "reserved",
            "stored",
            "attached",
            "cleanup_pending",
            "cleaned",
        }:
            raise RenewalHarnessCleanupError("fixture cleanup ledger state is unexpected")
        if upload_id and artifact.get("artifact_state") != "attached":
            raise RenewalHarnessCleanupError("bound fixture artifact is not attached")
        lease_owner = str(artifact.get("lease_owner") or "").strip()
        lease_expires_at = artifact.get("lease_expires_at")
        if lease_owner:
            # The ordinary artifact reconciler commits its lease before doing
            # external I/O.  Harness cleanup must never compete with that
            # worker, even if a wall-clock expiry has just elapsed while the
            # worker is still finishing its exact-key operation.
            if lease_expires_at in (None, ""):
                raise RenewalHarnessCleanupError(
                    "fixture cleanup ledger lease has no expiry"
                )
            try:
                if isinstance(lease_expires_at, datetime):
                    lease_expiry = lease_expires_at
                else:
                    lease_expiry = datetime.fromisoformat(
                        str(lease_expires_at).strip().replace("Z", "+00:00")
                    )
                if lease_expiry.tzinfo is None:
                    lease_expiry = lease_expiry.replace(tzinfo=timezone.utc)
                lease_expiry = lease_expiry.astimezone(timezone.utc)
            except (TypeError, ValueError) as exc:
                raise RenewalHarnessCleanupError(
                    "fixture cleanup ledger lease expiry is invalid"
                ) from exc
            if datetime.now(timezone.utc) <= (
                lease_expiry + ARTIFACT_LEASE_SAFETY_MARGIN
            ):
                raise RenewalHarnessCleanupError(
                    "fixture cleanup ledger is leased by the artifact reconciler"
                )
        elif lease_expires_at not in (None, ""):
            raise RenewalHarnessCleanupError(
                "fixture cleanup ledger has an ownerless lease expiry"
            )

    return {
        "already_clean": False,
        "request_ids": [request_id],
        "event_ids": sorted(str(item["event_id"]) for item in events),
        "upload_ids": sorted(str(item["upload_id"]) for item in uploads),
        "binding_ids": sorted(str(item["upload_id"]) for item in bindings),
        "cleanup_ids": sorted(str(item["cleanup_id"]) for item in artifacts),
        "notification_ids": expected_notification_ids,
        "rate_limit_keys": [str(item["key"]) for item in rate_limits],
    }


def _cleanup_plan_payload(
    relations: Mapping[str, list[dict[str, Any]]],
    fixture: Mapping[str, Any],
    run_id: str,
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    relation_digests = {
        name: _digest_rows(relations[name], order_key=_ORDER_KEYS[name])
        for name in _OPERATIONAL_RELATIONS
    }
    # The fingerprint includes complete canonical operational rows internally;
    # only their count/digest descriptors and synthetic IDs are returned.
    internal = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "fixture_key": fixture["fixture_key"],
        "fixture_version": fixture["fixture_version"],
        "manifest_sha256": fixture.get("manifest_sha256"),
        "application_id": fixture["application_id"],
        "alert_id": fixture["alert_id"],
        "relations": {
            name: [_canonical_value(item) for item in relations[name]]
            for name in _OPERATIONAL_RELATIONS
        },
        "delete_order": list(CLEANUP_DELETE_ORDER),
    }
    fingerprint = hashlib.sha256(_canonical_json(internal).encode("utf-8")).hexdigest()
    return {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "fixture": {
            "fixture_key": fixture["fixture_key"],
            "fixture_version": fixture["fixture_version"],
            "manifest_sha256": fixture.get("manifest_sha256"),
            "application_id": fixture["application_id"],
            "alert_id": fixture["alert_id"],
        },
        "status": "already_clean" if graph["already_clean"] else "ready",
        "already_clean": bool(graph["already_clean"]),
        "ids": {key: list(graph[key]) for key in (
            "request_ids",
            "event_ids",
            "upload_ids",
            "binding_ids",
            "cleanup_ids",
            "notification_ids",
            "rate_limit_keys",
        )},
        "counts": {
            "renewal_requests": len(graph["request_ids"]),
            "renewal_events": len(graph["event_ids"]),
            "renewal_uploads": len(graph["upload_ids"]),
            "renewal_upload_bindings": len(graph["binding_ids"]),
            "renewal_upload_cleanup": len(graph["cleanup_ids"]),
            "renewal_notifications": len(graph["notification_ids"]),
            "harness_rate_limit": len(graph["rate_limit_keys"]),
        },
        "relation_fingerprints": relation_digests,
        "delete_order": list(CLEANUP_DELETE_ORDER),
        "audit_policy": "preserve_append_only_and_append_cleanup_evidence",
        "fingerprint": fingerprint,
    }


def build_renewal_harness_cleanup_plan(
    db: Any,
    fixture: Any,
    *,
    run_id: str,
) -> dict[str, Any]:
    """Build an exact, read-only and repeatable cleanup plan."""

    spec = _normalize_fixture(fixture)
    normalized_run_id = _required_text(run_id, "run_id")
    relations = _relation_rows(db, spec, run_id=normalized_run_id)
    graph = _validate_cleanup_graph(
        relations,
        spec,
        run_id=normalized_run_id,
    )
    return _cleanup_plan_payload(relations, spec, normalized_run_id, graph)


def _database_identity_fingerprint(identity: Any) -> str:
    try:
        parsed = urlsplit(str(identity or "").strip())
        if parsed.scheme.lower() not in {"postgres", "postgresql"}:
            raise ValueError
        if parsed.query or parsed.fragment:
            # PostgreSQL URI query parameters can override the authority/path
            # target (for example host= or dbname=). Refuse every override so
            # the approved credential-free fingerprint cannot be spoofed.
            raise ValueError
        host = unquote(parsed.hostname or "").strip().lower().rstrip(".")
        database = unquote((parsed.path or "").lstrip("/")).strip()
        if not host or not database or "/" in database:
            raise ValueError
        canonical = f"postgresql://{host}:{parsed.port or 5432}/{database}"
    except (TypeError, ValueError) as exc:
        raise RenewalHarnessCleanupError(
            "cleanup requires a complete PostgreSQL database identity"
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _assert_cleanup_environment(
    db: Any,
    *,
    expected_database_fingerprint: Optional[str],
    allow_test_database: bool,
) -> str:
    environment = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    if environment == "staging":
        if not getattr(db, "is_postgres", False):
            raise RenewalHarnessCleanupError("staging cleanup requires PostgreSQL")
        expected = str(expected_database_fingerprint or "").strip().lower()
        if not _SHA256_RE.fullmatch(expected):
            raise RenewalHarnessCleanupError(
                f"staging cleanup requires {STAGING_DATABASE_FINGERPRINT_ENV}"
            )
        actual = _database_identity_fingerprint(getattr(db, "database_identity", None))
        if not hmac.compare_digest(actual, expected):
            raise RenewalHarnessCleanupError(
                "staging database identity does not match the approved fingerprint"
            )
        return environment
    if (
        allow_test_database
        and environment in {"test", "testing"}
        and (
            is_verified_isolated_test_database(
                getattr(db, "database_identity", None),
                bool(getattr(db, "is_postgres", False)),
            )
            or is_verified_disposable_postgres_test_db(
                getattr(db, "database_identity", None),
                bool(getattr(db, "is_postgres", False)),
                environment=environment,
            )
        )
    ):
        return environment
    raise RenewalHarnessCleanupError("cleanup is permitted only on approved staging")


def _assert_all_flags_off(feature_state: Optional[Mapping[str, Any]]) -> dict[str, bool]:
    from environment import MONITORING_FEATURE_FLAGS

    state = {
        str(key): bool(value)
        for key, value in (feature_state or _runtime_feature_state()).items()
    }
    if set(state) != set(MONITORING_FEATURE_FLAGS) or any(state.values()):
        raise RenewalHarnessCleanupError(
            "all governed Monitoring feature flags must be OFF before cleanup"
        )
    return state


def _begin_locked_cleanup(db: Any, fixture: Mapping[str, Any]) -> None:
    if getattr(db, "is_postgres", False):
        db.execute("SELECT pg_advisory_xact_lock(?)", (HARNESS_ADVISORY_LOCK_KEY,))
        db.execute(
            "SELECT id FROM applications WHERE id = ? FOR UPDATE",
            (fixture["application_id"],),
        )
        db.execute(
            "SELECT id FROM documents WHERE id = ? FOR UPDATE",
            (fixture["document_id"],),
        )
        db.execute(
            "SELECT id FROM monitoring_alerts WHERE id = ? FOR UPDATE",
            (fixture["alert_id"],),
        )
        db.execute(
            """
            SELECT request_id FROM monitoring_document_renewal_requests
             WHERE application_id = ? OR monitoring_alert_id = ? OR document_id = ?
                OR request_id = ?
             FOR UPDATE
            """,
            (
                fixture["application_id"],
                fixture["alert_id"],
                fixture["document_id"],
                fixture["request_id"],
            ),
        )
        # Lock every exact fixture cleanup-ledger candidate before rebuilding
        # the approved plan and before any S3 I/O.  A concurrently starting
        # ordinary reconciler will block and then observe the row as deleted;
        # a reconciler that claimed first is rejected by the lease guard.
        db.execute(
            """
            SELECT cleanup_id FROM monitoring_document_renewal_upload_cleanup
             WHERE request_id = ?
             FOR UPDATE
            """,
            (fixture["request_id"],),
        )
    else:
        raw = getattr(db, "conn", db)
        if not getattr(raw, "in_transaction", False):
            db.execute("BEGIN IMMEDIATE")


def _rollback(db: Any) -> None:
    try:
        db.rollback()
    except Exception:
        pass


def _local_artifact_path_allowed(artifact: Mapping[str, Any]) -> bool:
    from config import UPLOAD_DIR

    upload_id = str(artifact.get("upload_id") or "")
    extension = str(artifact.get("file_extension") or "")
    path = Path(str(artifact.get("local_path") or ""))
    storage_key = str(artifact.get("storage_key") or "")
    expected_parent = Path(str(UPLOAD_DIR)).resolve() / "monitoring-renewal-candidates"
    try:
        parent_matches = path.parent.resolve(strict=True) == expected_parent.resolve(
            strict=True
        )
    except OSError:
        parent_matches = False
    return bool(
        re.fullmatch(r"[a-f0-9]{32}", upload_id)
        and re.fullmatch(r"(?:\.[a-z0-9]{1,20})?", extension)
        and parent_matches
        and path.name == upload_id + extension
        and path.parent.name == "monitoring-renewal-candidates"
        and storage_key == f"local://monitoring-renewal-candidates/{path.name}"
        and not path.is_symlink()
    )


def _cleanup_local_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    if not _local_artifact_path_allowed(artifact):
        raise RenewalHarnessCleanupError("fixture local candidate path is outside its allowlist")
    path = Path(str(artifact["local_path"]))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(str(path.parent), flags)
    try:
        try:
            current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
            existed = True
        except FileNotFoundError:
            current = None
            existed = False
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise RenewalHarnessCleanupError(
                "fixture local candidate is not a regular file"
            )
        if existed:
            os.unlink(path.name, dir_fd=directory_fd)
        try:
            os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RenewalHarnessCleanupError(
                "fixture local candidate still exists after cleanup"
            )
    finally:
        os.close(directory_fd)
    return {
        "cleanup_id": str(artifact["cleanup_id"]),
        "backend": "local",
        "outcome": "deleted" if existed else "already_absent",
    }


def _inspect_s3_candidate_inventory(
    artifact_client: Any,
    storage_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory_method = getattr(
        artifact_client,
        "inspect_monitoring_renewal_candidate_versions",
        None,
    )
    if not callable(inventory_method):
        raise RenewalHarnessCleanupError(
            "fixture S3 candidate version inventory is unavailable"
        )
    inspected, result = inventory_method(storage_key)
    if not inspected or not isinstance(result, Mapping):
        raise RenewalHarnessCleanupError(
            "fixture S3 candidate version inventory is unavailable"
        )
    if str(result.get("key") or "") != storage_key:
        raise RenewalHarnessCleanupError(
            "fixture S3 candidate version inventory is not exact-key scoped"
        )
    raw_versions = result.get("versions")
    raw_markers = result.get("delete_markers")
    if not isinstance(raw_versions, list) or not isinstance(raw_markers, list):
        raise RenewalHarnessCleanupError(
            "fixture S3 candidate version inventory is malformed"
        )
    versions: list[dict[str, Any]] = []
    markers: list[dict[str, Any]] = []
    for raw, destination, label in (
        (raw_versions, versions, "version"),
        (raw_markers, markers, "delete marker"),
    ):
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, Mapping):
                raise RenewalHarnessCleanupError(
                    "fixture S3 candidate version inventory is malformed"
                )
            version_id = str(item.get("version_id") or "").strip()
            if not version_id or version_id in seen:
                raise RenewalHarnessCleanupError(
                    f"fixture S3 candidate {label} inventory is ambiguous"
                )
            seen.add(version_id)
            destination.append(
                {
                    "version_id": version_id,
                    "is_latest": item.get("is_latest") is True,
                    "size": item.get("size") if label == "version" else None,
                }
            )
    if result.get("version_count") != len(versions) or result.get(
        "delete_marker_count"
    ) != len(markers):
        raise RenewalHarnessCleanupError(
            "fixture S3 candidate version inventory counts are inconsistent"
        )
    return versions, markers


def _cleanup_s3_artifact(
    artifact: Mapping[str, Any],
    upload: Optional[Mapping[str, Any]],
    artifact_client: Any,
) -> dict[str, Any]:
    storage_key = str(artifact["storage_key"])
    try:
        from s3_client import build_monitoring_renewal_candidate_key

        extension = str(artifact.get("file_extension") or "")
        expected_key = build_monitoring_renewal_candidate_key(
            str(artifact.get("customer_id") or ""),
            str(artifact.get("request_id") or ""),
            str(artifact.get("upload_id") or ""),
            "synthetic-candidate" + extension,
        )
    except (TypeError, ValueError) as exc:
        raise RenewalHarnessCleanupError(
            "fixture S3 candidate identity cannot produce an exact reserved key"
        ) from exc
    if storage_key != expected_key:
        raise RenewalHarnessCleanupError(
            "fixture S3 candidate key does not match its exact owned identity"
        )
    expected_version = str(artifact.get("s3_version_id") or "").strip()
    artifact_state = str(artifact.get("artifact_state") or "").strip()
    if not expected_version and artifact_state not in {
        "reserved",
        "cleanup_pending",
        "cleaned",
    }:
        raise RenewalHarnessCleanupError(
            "fixture S3 candidate state requires a recorded exact object version"
        )
    versions, delete_markers = _inspect_s3_candidate_inventory(
        artifact_client,
        storage_key,
    )
    inspected, result = artifact_client.inspect_monitoring_renewal_candidate(
        storage_key
    )
    if not inspected or not isinstance(result, Mapping):
        raise RenewalHarnessCleanupError("fixture S3 candidate ownership is unavailable")
    if result.get("exists") is True:
        if delete_markers or len(versions) != 1:
            raise RenewalHarnessCleanupError(
                "fixture S3 candidate has unexpected object versions or delete markers"
            )
        if versions[0]["is_latest"] is not True:
            raise RenewalHarnessCleanupError(
                "fixture S3 candidate exact version is not current"
            )
        expected = {
            "request_id": str(artifact["request_id"]),
            "upload_id": str(artifact["upload_id"]),
            "cleanup_id": str(artifact["cleanup_id"]),
        }
        if upload:
            expected["file_sha256"] = str(upload.get("file_sha256") or "")
        for key, value in expected.items():
            if str(result.get(key) or "") != value:
                raise RenewalHarnessCleanupError(
                    f"fixture S3 candidate ownership drifted at {key}"
                )
        if upload and int(result.get("content_length") or -1) != int(
            upload.get("file_size") or -2
        ):
            raise RenewalHarnessCleanupError("fixture S3 candidate size drifted")
        observed_version = str(result.get("version_id") or "").strip() or None
        inventory_version = str(versions[0]["version_id"] or "").strip() or None
        if not observed_version or observed_version != inventory_version:
            raise RenewalHarnessCleanupError("fixture S3 candidate version drifted")
        if expected_version and expected_version != observed_version:
            raise RenewalHarnessCleanupError("fixture S3 candidate version drifted")
        if not expected_version and artifact_state not in {
            "reserved",
            "cleanup_pending",
        }:
            raise RenewalHarnessCleanupError(
                "fixture S3 candidate exists without a recorded owned version"
            )
        delete_version = expected_version or observed_version
        deleted, code = artifact_client.delete_monitoring_renewal_candidate(
            storage_key,
            version_id=delete_version,
            expected_request_id=artifact["request_id"],
            expected_upload_id=artifact["upload_id"],
            expected_cleanup_id=artifact["cleanup_id"],
        )
        if not deleted:
            raise RenewalHarnessCleanupError(
                f"fixture S3 candidate cleanup failed: {str(code or 'unknown')[:64]}"
            )
        outcome = str(code or "candidate_deleted")[:64]
    else:
        # This is the resumable path after an exact object deletion succeeded
        # but the following database transaction rolled back.  The caller has
        # already supplied the exact unchanged database plan fingerprint.
        if versions or delete_markers:
            raise RenewalHarnessCleanupError(
                "fixture S3 candidate has residual object versions or delete markers"
            )
        outcome = "already_absent"
    verified, after = artifact_client.inspect_monitoring_renewal_candidate(
        storage_key
    )
    if not verified or not isinstance(after, Mapping) or after.get("exists") is not False:
        raise RenewalHarnessCleanupError("fixture S3 candidate remains after cleanup")
    after_versions, after_delete_markers = _inspect_s3_candidate_inventory(
        artifact_client,
        storage_key,
    )
    if after_versions or after_delete_markers:
        raise RenewalHarnessCleanupError(
            "fixture S3 candidate version residue remains after cleanup"
        )
    return {
        "cleanup_id": str(artifact["cleanup_id"]),
        "backend": "s3",
        "outcome": outcome,
    }


def _cleanup_artifacts(
    relations: Mapping[str, list[dict[str, Any]]],
    *,
    environment: str,
    artifact_client: Any,
) -> list[dict[str, Any]]:
    uploads = {str(item["upload_id"]): item for item in relations["renewal_uploads"]}
    results: list[dict[str, Any]] = []
    for artifact in relations["renewal_upload_cleanup"]:
        backend = str(artifact.get("backend") or "")
        if backend == "local":
            if environment == "staging":
                raise RenewalHarnessCleanupError("staging fixture candidate must use S3")
            results.append(_cleanup_local_artifact(artifact))
        elif backend == "s3":
            client = artifact_client
            if client is None:
                from s3_client import get_s3_client

                client = get_s3_client()
            if environment == "staging":
                bucket = str(getattr(client, "bucket_name", "") or "").strip()
                region = str(getattr(client, "region", "") or "").strip()
                if bucket != STAGING_ARTIFACT_BUCKET or region != STAGING_ARTIFACT_REGION:
                    raise RenewalHarnessCleanupError(
                        "staging fixture candidate client is not pinned to the reviewed bucket and region"
                    )
            results.append(
                _cleanup_s3_artifact(
                    artifact,
                    uploads.get(str(artifact.get("upload_id") or "")),
                    client,
                )
            )
        else:
            raise RenewalHarnessCleanupError("fixture candidate backend is unsupported")
    return results


def _delete_exact_ids(
    db: Any,
    table: str,
    id_column: str,
    ids: Iterable[str],
    *,
    extra_where: str = "",
    extra_params: tuple[Any, ...] = (),
) -> None:
    values = tuple(ids)
    if not values:
        return
    placeholders = ",".join("?" for _ in values)
    db.execute(
        f"DELETE FROM {table} WHERE {id_column} IN ({placeholders}){extra_where}",
        (*values, *extra_params),
    )


def _completed_cleanup_audit_exists(
    db: Any,
    fixture: Mapping[str, Any],
    *,
    run_id: str,
    plan_fingerprint: str,
) -> bool:
    rows = _rows(
        db.execute(
            """
            SELECT detail FROM audit_log
             WHERE action = 'monitoring.document_renewal.fixture_cleanup'
               AND target = ? AND application_id = ? AND request_id = ?
             ORDER BY id
            """,
            (
                f"monitoring_renewal_fixture:{fixture['fixture_key']}",
                fixture["application_id"],
                run_id,
            ),
        ).fetchall()
    )
    matches = 0
    for row in rows:
        detail = _json_object(row.get("detail"))
        if (
            detail.get("contract_version") == CONTRACT_VERSION
            and detail.get("run_id") == run_id
            and hmac.compare_digest(
                str(detail.get("plan_fingerprint") or ""), plan_fingerprint
            )
        ):
            matches += 1
    if matches > 1:
        raise RenewalHarnessCleanupError("duplicate fixture cleanup audit evidence exists")
    return matches == 1


def cleanup_renewal_harness_operational_state(
    db: Any,
    fixture: Any,
    *,
    run_id: str,
    expected_plan_fingerprint: str,
    actor_id: str,
    confirmation: str,
    expected_database_fingerprint: Optional[str] = None,
    feature_state: Optional[Mapping[str, Any]] = None,
    artifact_client: Any = None,
    audit_writer: Optional[Callable[..., Any]] = None,
    preserve_audit: bool = True,
    allow_test_database: bool = False,
) -> dict[str, Any]:
    """Remove only the exact synthetic renewal operational graph.

    ``preserve_audit=False`` is intentionally unsupported.  The supplied plan
    fingerprint is re-created under the fixture lock before any artifact or
    database deletion.  A second call after success is an idempotent no-op.
    """

    if confirmation != CLEANUP_CONFIRMATION:
        raise RenewalHarnessCleanupError("cleanup confirmation is missing or invalid")
    if not preserve_audit:
        raise RenewalHarnessCleanupError("renewal fixture audit evidence must be preserved")
    expected = str(expected_plan_fingerprint or "").strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise RenewalHarnessCleanupError("expected cleanup plan fingerprint is invalid")
    spec = _normalize_fixture(fixture)
    normalized_run_id = _required_text(run_id, "run_id")
    normalized_actor_id = _required_text(actor_id, "actor_id")
    environment = _assert_cleanup_environment(
        db,
        expected_database_fingerprint=expected_database_fingerprint,
        allow_test_database=allow_test_database,
    )
    if environment == "staging":
        if feature_state is not None:
            raise RenewalHarnessCleanupError(
                "staging cleanup must evaluate feature flags from the runtime"
            )
        if artifact_client is not None or audit_writer is not None:
            raise RenewalHarnessCleanupError(
                "staging cleanup must resolve canonical artifact and audit dependencies"
            )
        if allow_test_database:
            raise RenewalHarnessCleanupError(
                "staging cleanup cannot enable the disposable-test database override"
            )
    flags = _assert_all_flags_off(feature_state)
    try:
        _begin_locked_cleanup(db, spec)
        relations = _relation_rows(db, spec, run_id=normalized_run_id)
        graph = _validate_cleanup_graph(
            relations,
            spec,
            run_id=normalized_run_id,
        )
        locked_plan = _cleanup_plan_payload(relations, spec, normalized_run_id, graph)
        plan_matches = hmac.compare_digest(locked_plan["fingerprint"], expected)
        completed_evidence = (
            graph["already_clean"]
            and _completed_cleanup_audit_exists(
                db,
                spec,
                run_id=normalized_run_id,
                plan_fingerprint=expected,
            )
        )
        if not plan_matches and not completed_evidence:
            raise RenewalHarnessCleanupError(
                "locked cleanup plan differs from the approved fingerprint"
            )
        if locked_plan["already_clean"]:
            db.commit()
            return {
                "contract_version": CONTRACT_VERSION,
                "run_id": normalized_run_id,
                "fixture": locked_plan["fixture"],
                "plan_fingerprint": expected,
                "idempotent": True,
                "prior_cleanup_audit_confirmed": completed_evidence,
                "deleted_counts": {key: 0 for key in locked_plan["counts"]},
                "artifact_cleanup": [],
                "audit_preserved": True,
                "feature_state": flags,
            }

        artifact_results = _cleanup_artifacts(
            relations,
            environment=environment,
            artifact_client=artifact_client,
        )
        ids = locked_plan["ids"]
        with fixture_cleanup_context(
            db,
            spec["application_id"],
            actor_id=normalized_actor_id,
            confirmation=FIXTURE_CLEANUP_CONFIRMATION,
            reason=(
                f"{CONTRACT_VERSION} cleanup for {spec['fixture_key']} "
                f"run {normalized_run_id}"
            ),
            allowed_tables=REGULATED_CLEANUP_TABLES,
        ):
            _delete_exact_ids(
                db,
                "monitoring_document_renewal_upload_bindings",
                "upload_id",
                ids["binding_ids"],
                extra_where=" AND application_id = ?",
                extra_params=(spec["application_id"],),
            )
            _delete_exact_ids(
                db,
                "monitoring_document_renewal_uploads",
                "upload_id",
                ids["upload_ids"],
                extra_where=" AND application_id = ?",
                extra_params=(spec["application_id"],),
            )
            _delete_exact_ids(
                db,
                "monitoring_document_renewal_upload_cleanup",
                "cleanup_id",
                ids["cleanup_ids"],
                extra_where=" AND application_id = ?",
                extra_params=(spec["application_id"],),
            )
            _delete_exact_ids(
                db,
                "monitoring_document_renewal_events",
                "event_id",
                ids["event_ids"],
                extra_where=(
                    " AND request_id IN (SELECT request_id FROM "
                    "monitoring_document_renewal_requests WHERE application_id = ?)"
                ),
                extra_params=(spec["application_id"],),
            )
            _delete_exact_ids(
                db,
                "monitoring_document_renewal_requests",
                "request_id",
                ids["request_ids"],
                extra_where=" AND application_id = ? AND monitoring_alert_id = ?",
                extra_params=(spec["application_id"], spec["alert_id"]),
            )
            _delete_exact_ids(
                db,
                "client_notifications",
                "id",
                ids["notification_ids"],
                extra_where=" AND application_id = ? AND client_id = ?",
                extra_params=(spec["application_id"], spec["customer_id"]),
            )
            _delete_exact_ids(
                db,
                "shared_rate_limits",
                "key",
                ids["rate_limit_keys"],
            )

        after_relations = _relation_rows(db, spec, run_id=normalized_run_id)
        after_graph = _validate_cleanup_graph(
            after_relations,
            spec,
            run_id=normalized_run_id,
        )
        if not after_graph["already_clean"]:
            raise RenewalHarnessCleanupError("fixture operational residue remains")
        writer = audit_writer
        if writer is None:
            from db import append_audit_log

            writer = append_audit_log
        evidence = writer(
            db,
            action="monitoring.document_renewal.fixture_cleanup",
            user_id=normalized_actor_id,
            user_name="Document Renewal Staging Harness",
            user_role="system",
            target=f"monitoring_renewal_fixture:{spec['fixture_key']}",
            detail=_canonical_json(
                {
                    "contract_version": CONTRACT_VERSION,
                    "fixture_version": spec["fixture_version"],
                    "run_id": normalized_run_id,
                    "plan_fingerprint": expected,
                    "deleted_ids": ids,
                    "artifact_cleanup": artifact_results,
                    "audit_policy": "preserved",
                }
            ),
            before_state={"counts": locked_plan["counts"]},
            after_state={"counts": {key: 0 for key in locked_plan["counts"]}},
            application_id=spec["application_id"],
            request_id=normalized_run_id,
            commit=False,
        )
        if not isinstance(evidence, str) or not evidence.strip():
            raise RenewalHarnessCleanupError("cleanup audit writer returned no evidence")
        db.commit()
    except Exception:
        _rollback(db)
        raise

    final = capture_renewal_harness_fingerprint(
        db,
        spec,
        run_id=normalized_run_id,
        feature_state=None if environment == "staging" else flags,
    )
    if any(
        final["relations"][name]["row_count"]
        for name in _OPERATIONAL_RELATIONS
    ):
        raise RenewalHarnessCleanupError("fixture final fingerprint contains operational residue")
    return {
        "contract_version": CONTRACT_VERSION,
        "run_id": normalized_run_id,
        "fixture": locked_plan["fixture"],
        "plan_fingerprint": expected,
        "idempotent": False,
        "deleted_counts": locked_plan["counts"],
        "artifact_cleanup": artifact_results,
        "audit_preserved": True,
        "audit_evidence": evidence,
        "feature_state": flags,
        "final_fingerprint": final,
    }


def compare_renewal_harness_baseline(
    before: Mapping[str, Any],
    final: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare restorable state while treating audit growth as evidence."""

    before_audit = (
        (before.get("relations") or {}).get("fixture_audit") or {}
        if isinstance(before.get("relations"), Mapping)
        else {}
    )
    final_audit = (
        (final.get("relations") or {}).get("fixture_audit") or {}
        if isinstance(final.get("relations"), Mapping)
        else {}
    )
    try:
        before_audit_count = int(before_audit.get("row_count") or 0)
        final_audit_count = int(final_audit.get("row_count") or 0)
        audit_count_increased = final_audit_count > before_audit_count
        before_chained = int(before.get("fixture_audit_chained_count") or 0)
        final_chained = int(final.get("fixture_audit_chained_count") or 0)
        before_unchained = int(before.get("fixture_audit_unchained_count") or 0)
        final_unchained = int(final.get("fixture_audit_unchained_count") or 0)
        cleanup_action = "monitoring.document_renewal.fixture_cleanup"
        before_cleanup = int(
            (before.get("fixture_audit_actions") or {}).get(cleanup_action, 0)
        )
        final_cleanup = int(
            (final.get("fixture_audit_actions") or {}).get(cleanup_action, 0)
        )
        exact_chained_growth = (
            final_chained - before_chained == final_audit_count - before_audit_count
            and final_unchained == before_unchained
        )
        exact_cleanup_evidence = final_cleanup == before_cleanup + 1
        before_chain = before.get("audit_chain") or {}
        final_chain = final.get("audit_chain") or {}
        audit_chain_preserved = (
            before_chain.get("verified") is True
            and final_chain.get("verified") is True
            and before_chain.get("coverage_complete") is True
            and final_chain.get("coverage_complete") is True
            and int(before_chain.get("broken_link_count") or 0) == 0
            and int(final_chain.get("broken_link_count") or 0) == 0
            and int(before_chain.get("coverage_gaps") or 0) == 0
            and int(final_chain.get("coverage_gaps") or 0) == 0
            and int(final_chain.get("legacy_rows") or 0)
            == int(before_chain.get("legacy_rows") or 0)
        )
    except (TypeError, ValueError):
        audit_count_increased = False
        exact_chained_growth = False
        exact_cleanup_evidence = False
        audit_chain_preserved = False
    comparisons = {
        "core_restored": before.get("core_fingerprint") == final.get("core_fingerprint"),
        "operational_restored": before.get("operational_fingerprint")
        == final.get("operational_fingerprint"),
        "nonfixture_unchanged": before.get("isolation_fingerprint")
        == final.get("isolation_fingerprint"),
        "scheduler_unchanged": before.get("scheduler_fingerprint")
        == final.get("scheduler_fingerprint"),
        "all_flags_off": bool(final.get("all_monitoring_flags_off")),
        "audit_evidence_appended": audit_count_increased
        and exact_chained_growth
        and exact_cleanup_evidence
        and audit_chain_preserved
        and before.get("audit_evidence_fingerprint")
        != final.get("audit_evidence_fingerprint"),
    }
    return {
        **comparisons,
        "baseline_restored": all(
            comparisons[key]
            for key in (
                "core_restored",
                "operational_restored",
                "nonfixture_unchanged",
                "scheduler_unchanged",
                "all_flags_off",
                "audit_evidence_appended",
            )
        ),
    }


def _load_cli_fixture(db: Any) -> dict[str, Any]:
    from fixtures.document_renewal_staging import fixture_spec

    try:
        spec = fixture_spec(db)
    except TypeError:
        spec = fixture_spec()
    result = _mapping(spec)
    if not (result.get("alert_id") or result.get("monitoring_alert_id")):
        source_reference = _required_text(
            result.get("source_reference") or result.get("alert_source_reference"),
            "source_reference",
        )
        rows = _rows(
            db.execute(
                """
                SELECT id FROM monitoring_alerts
                 WHERE application_id = ? AND source_reference = ?
                 ORDER BY id
                """,
                (
                    _required_text(result.get("application_id"), "application_id"),
                    source_reference,
                ),
            ).fetchall()
        )
        if len(rows) != 1:
            raise RenewalHarnessCleanupError(
                "fixture alert identity did not resolve to exactly one row"
            )
        result["alert_id"] = rows[0]["id"]
    return result


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "plan"):
        item = sub.add_parser(name)
        item.add_argument("--run-id", required=True)
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--run-id", required=True)
    cleanup.add_argument("--expected-plan-fingerprint", required=True)
    cleanup.add_argument("--actor-id", required=True)
    cleanup.add_argument("--confirm", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _cli_parser().parse_args(argv)
    from db import get_db

    db = get_db()
    try:
        fixture = _load_cli_fixture(db)
        if args.command == "snapshot":
            result = capture_renewal_harness_fingerprint(
                db, fixture, run_id=args.run_id
            )
        elif args.command == "plan":
            result = build_renewal_harness_cleanup_plan(
                db, fixture, run_id=args.run_id
            )
        else:
            result = cleanup_renewal_harness_operational_state(
                db,
                fixture,
                run_id=args.run_id,
                expected_plan_fingerprint=args.expected_plan_fingerprint,
                actor_id=args.actor_id,
                confirmation=args.confirm,
                expected_database_fingerprint=os.environ.get(
                    STAGING_DATABASE_FINGERPRINT_ENV
                ),
            )
        print(json.dumps(result, sort_keys=True, indent=2, default=str))
        return 0
    except Exception as exc:
        _rollback(db)
        print(
            json.dumps(
                {
                    "contract_version": CONTRACT_VERSION,
                    "status": "failed",
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
