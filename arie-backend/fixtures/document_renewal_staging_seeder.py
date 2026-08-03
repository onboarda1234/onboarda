"""Fail-closed seeder for the one governed Document Renewal fixture.

This is intentionally separate from ``fixtures.seeder.seed_all``. The fixture
is staging workflow substrate, not a general demo scenario, and can never be
created by a broad fixture command. No renewal request or upload state is
created here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from typing import Any, Dict, Mapping, Optional
from urllib.parse import unquote, urlsplit

from fixtures.document_renewal_staging import (
    FIXTURE_KEY,
    fixture_spec,
    load_manifest,
    manifest_sha256,
    validate_manifest,
)


ALLOWED_ENVIRONMENTS = frozenset({"staging", "test", "testing"})
_DISABLED_LOGIN_HASH = "fixture-no-login"
STAGING_DATABASE_FINGERPRINT_ENV = (
    "DOCUMENT_RENEWAL_HARNESS_STAGING_DATABASE_FINGERPRINT"
)


class DocumentRenewalFixtureSeedError(RuntimeError):
    """The governed fixture could not be proven safe to seed or reuse."""


def _environment() -> str:
    return str(os.environ.get("ENVIRONMENT") or "").strip().lower()


def _require_environment() -> str:
    environment = _environment()
    if environment not in ALLOWED_ENVIRONMENTS:
        raise DocumentRenewalFixtureSeedError(
            "Document Renewal fixture seeding is allowed only in staging or testing."
        )
    return environment


def _database_identity_fingerprint(identity: Any) -> str:
    try:
        parsed = urlsplit(str(identity or "").strip())
        if parsed.scheme.lower() not in {"postgres", "postgresql"}:
            raise ValueError
        # libpq accepts target-changing query parameters such as host= and
        # dbname=. Ignore none of them: an approved fingerprint must describe
        # the complete target, so this governed path accepts only a canonical
        # authority/path URI with no query or fragment overrides.
        if parsed.query or parsed.fragment:
            raise ValueError
        host = unquote(parsed.hostname or "").strip().lower().rstrip(".")
        database = unquote((parsed.path or "").lstrip("/")).strip()
        if not host or not database or "/" in database:
            raise ValueError
        canonical = f"postgresql://{host}:{parsed.port or 5432}/{database}"
    except (TypeError, ValueError) as exc:
        raise DocumentRenewalFixtureSeedError(
            "Fixture apply requires a complete PostgreSQL database identity."
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_database_identity(db: Any, environment: str) -> None:
    if environment != "staging":
        return
    if not bool(getattr(db, "is_postgres", False)):
        raise DocumentRenewalFixtureSeedError(
            "Staging fixture apply requires PostgreSQL."
        )
    expected = str(
        os.environ.get(STAGING_DATABASE_FINGERPRINT_ENV) or ""
    ).strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise DocumentRenewalFixtureSeedError(
            f"Staging fixture apply requires {STAGING_DATABASE_FINGERPRINT_ENV}."
        )
    actual = _database_identity_fingerprint(
        getattr(db, "database_identity", None)
    )
    if not hmac.compare_digest(actual, expected):
        raise DocumentRenewalFixtureSeedError(
            "Staging fixture database identity does not match the approved fingerprint."
        )


def _row(value: Any) -> Optional[Dict[str, Any]]:
    return dict(value) if value is not None else None


def _rows(value: Any) -> list[Dict[str, Any]]:
    return [dict(row) for row in value.fetchall()]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "on"}


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _date_part(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date().isoformat()
    return str(value or "").strip().split("T", 1)[0].split(" ", 1)[0]


def _identity_payload(spec: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "fixture_key": spec["fixture_key"],
        "fixture_marker": spec["fixture_marker"],
        "fixture_contract_version": spec["fixture_contract_version"],
        "manifest_sha256": spec["manifest_sha256"],
        "synthetic": True,
        "non_production": True,
        "source": "fixtures.document_renewal_staging",
    }


def _load_reserved_state(db: Any, spec: Mapping[str, Any]) -> Dict[str, Any]:
    clients = _rows(
        db.execute(
            "SELECT * FROM clients WHERE id = ? OR email = ? ORDER BY id",
            (spec["customer_id"], spec["customer_email"]),
        )
    )
    applications = _rows(
        db.execute(
            "SELECT * FROM applications WHERE id = ? OR ref = ? ORDER BY id",
            (spec["application_id"], spec["application_ref"]),
        )
    )
    people = _rows(
        db.execute(
            "SELECT * FROM directors WHERE id = ? OR person_key = ? ORDER BY id",
            (spec["person_id"], spec["person_key"]),
        )
    )
    documents = _rows(
        db.execute(
            "SELECT * FROM documents WHERE id = ? OR file_path = ? ORDER BY id",
            (spec["document_id"], load_manifest()["document"]["file_path"]),
        )
    )
    alerts = _rows(
        db.execute(
            "SELECT * FROM monitoring_alerts WHERE source_reference = ? ORDER BY id",
            (spec["alert_source_reference"],),
        )
    )
    application_id = spec["application_id"]
    return {
        "clients": clients,
        "applications": applications,
        "people": people,
        "documents": documents,
        "alerts": alerts,
        "client_applications": _rows(
            db.execute(
                "SELECT * FROM applications WHERE client_id = ? ORDER BY id",
                (spec["customer_id"],),
            )
        ),
        "application_directors": _rows(
            db.execute(
                "SELECT * FROM directors WHERE application_id = ? ORDER BY id",
                (application_id,),
            )
        ),
        "application_ubos": _rows(
            db.execute(
                "SELECT * FROM ubos WHERE application_id = ? ORDER BY id",
                (application_id,),
            )
        ),
        "application_intermediaries": _rows(
            db.execute(
                "SELECT * FROM intermediaries WHERE application_id = ? ORDER BY id",
                (application_id,),
            )
        ),
        "application_documents": _rows(
            db.execute(
                "SELECT * FROM documents WHERE application_id = ? ORDER BY id",
                (application_id,),
            )
        ),
        "application_alerts": _rows(
            db.execute(
                "SELECT * FROM monitoring_alerts WHERE application_id = ? ORDER BY id",
                (application_id,),
            )
        ),
        "application_notifications": _rows(
            db.execute(
                "SELECT * FROM client_notifications WHERE application_id = ? ORDER BY id",
                (application_id,),
            )
        ),
        "application_agent_executions": _rows(
            db.execute(
                "SELECT * FROM agent_executions WHERE application_id = ? ORDER BY id",
                (application_id,),
            )
        ),
        "application_verification_jobs": _rows(
            db.execute(
                "SELECT * FROM verification_jobs WHERE application_id = ? ORDER BY id",
                (application_id,),
            )
        ),
        "application_legacy_requirements": _rows(
            db.execute(
                "SELECT * FROM application_enhanced_requirements "
                "WHERE application_id = ? ORDER BY id",
                (application_id,),
            )
        ),
    }


def _validate_existing(
    state: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> Optional[int]:
    manifest = load_manifest()
    expected_counts = {
        "clients": 1,
        "applications": 1,
        "people": 1,
        "documents": 1,
        "alerts": 1,
        "client_applications": 1,
        "application_directors": 1,
        "application_ubos": 0,
        "application_intermediaries": 0,
        "application_documents": 1,
        "application_alerts": 1,
        "application_notifications": 0,
        "application_agent_executions": 0,
        "application_verification_jobs": 0,
        "application_legacy_requirements": 0,
    }
    counts = {key: len(state[key]) for key in expected_counts}
    if not any(counts.values()):
        return None
    if counts != expected_counts:
        raise DocumentRenewalFixtureSeedError(
            "Reserved Document Renewal fixture identity is partial or colliding."
        )

    client = state["clients"][0]
    application = state["applications"][0]
    person = state["people"][0]
    document = state["documents"][0]
    alert = state["alerts"][0]
    identity = _identity_payload(spec)
    application_identity = _json_object(application.get("prescreening_data"))

    mismatches = []

    def expect(label: str, actual: Any, expected: Any) -> None:
        if str(actual if actual is not None else "") != str(
            expected if expected is not None else ""
        ):
            mismatches.append(label)

    expect("client.id", client.get("id"), spec["customer_id"])
    expect("client.email", client.get("email"), spec["customer_email"])
    expect("client.company_name", client.get("company_name"), manifest["client"]["company_name"])
    expect("client.status", client.get("status"), manifest["client"]["status"])
    if str(client.get("password_hash") or "") != _DISABLED_LOGIN_HASH:
        mismatches.append("client.password_hash")

    expect("application.id", application.get("id"), spec["application_id"])
    expect("application.ref", application.get("ref"), spec["application_ref"])
    expect("application.client_id", application.get("client_id"), spec["customer_id"])
    expect("application.status", application.get("status"), manifest["application"]["status"])
    if not _truthy(application.get("is_fixture")):
        mismatches.append("application.is_fixture")
    for key, expected in identity.items():
        if application_identity.get(key) != expected:
            mismatches.append(f"application.prescreening_data.{key}")

    expect("person.id", person.get("id"), spec["person_id"])
    expect("person.application_id", person.get("application_id"), spec["application_id"])
    expect("person.person_key", person.get("person_key"), spec["person_key"])
    expect("person.full_name", person.get("full_name"), manifest["person"]["full_name"])

    expect("document.id", document.get("id"), spec["document_id"])
    expect("document.application_id", document.get("application_id"), spec["application_id"])
    expect("document.person_id", document.get("person_id"), spec["person_id"])
    expect("document.person_type", document.get("person_type"), spec["person_type"])
    expect("document.doc_type", document.get("doc_type"), spec["document_type"])
    expect("document.slot_key", document.get("slot_key"), spec["document_slot_key"])
    expect("document.version", document.get("version"), spec["document_version"])
    expect("document.file_path", document.get("file_path"), manifest["document"]["file_path"])
    if not _truthy(document.get("is_current")):
        mismatches.append("document.is_current")
    if document.get("superseded_at") not in (None, "") or document.get(
        "superseded_by_document_id"
    ) not in (None, ""):
        mismatches.append("document.version_chain")
    if _date_part(document.get("expiry_date")) != _date_part(
        spec["document_expiry_date"]
    ):
        mismatches.append("document.expiry_date")

    expect("alert.application_id", alert.get("application_id"), spec["application_id"])
    expect("alert.alert_type", alert.get("alert_type"), spec["alert_type"])
    expect("alert.source_reference", alert.get("source_reference"), spec["alert_source_reference"])
    expect("alert.status", alert.get("status"), spec["alert_status"])
    expect("alert.detected_by", alert.get("detected_by"), manifest["alert"]["detected_by"])
    expect("alert.discovered_via", alert.get("discovered_via"), manifest["alert"]["discovered_via"])

    if mismatches:
        raise DocumentRenewalFixtureSeedError(
            "Document Renewal fixture baseline drift: " + ", ".join(sorted(mismatches))
        )
    return int(alert["id"])


def _insert_fixture(
    db: Any,
    spec: Mapping[str, Any],
) -> int:
    manifest = load_manifest()
    client = manifest["client"]
    application = manifest["application"]
    person = manifest["person"]
    document = manifest["document"]
    alert = manifest["alert"]
    epoch = manifest["fixture"]["deterministic_epoch"]
    identity = _identity_payload(spec)
    from db import append_audit_log

    def audit(
        *,
        action: str,
        target: str,
        detail: str,
        before_state: Any = None,
        after_state: Any = None,
    ) -> str:
        tagged_action = (
            action if action.startswith("fixture.") else f"fixture.{action}"
        )
        application_scope = (
            None if target.startswith("client:") else application["id"]
        )
        return append_audit_log(
            db,
            action=tagged_action,
            user_id="fixture_seed",
            user_name="fixture_seed",
            user_role="system",
            target=target,
            detail=detail,
            ip_address="127.0.0.1",
            before_state=before_state,
            after_state=after_state,
            application_id=application_scope,
            request_id=f"fixture-seed:{FIXTURE_KEY}",
            commit=False,
        )

    db.execute(
        "INSERT INTO clients (id,email,password_hash,company_name,status,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            client["id"], client["email"], _DISABLED_LOGIN_HASH,
            client["company_name"], client["status"], epoch,
        ),
    )
    audit(
        action="document_renewal_staging_client_seeded",
        target=f"client:{client['id']}",
        detail=f"Seeded governed fixture {FIXTURE_KEY} client",
        after_state={"id": client["id"], "email": client["email"]},
    )

    db.execute(
        """
        INSERT INTO applications
            (id,ref,client_id,company_name,country,sector,entity_type,
             prescreening_data,risk_score,risk_level,risk_dimensions,status,
             is_fixture,created_at,updated_at,inputs_updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            application["id"], application["ref"], client["id"],
            application["company_name"], application["country"],
            application["sector"], application["entity_type"],
            json.dumps(identity, sort_keys=True), application["risk_score"],
            application["risk_level"],
            json.dumps({"fixture": FIXTURE_KEY}, sort_keys=True),
            application["status"], True, epoch, epoch, epoch,
        ),
    )
    audit(
        action="document_renewal_staging_application_seeded",
        target=f"application:{application['id']}",
        detail=f"Seeded governed fixture {FIXTURE_KEY} application",
        after_state={"id": application["id"], "ref": application["ref"]},
    )

    db.execute(
        """
        INSERT INTO directors
            (id,application_id,person_key,full_name,nationality,is_pep,
             pep_declaration,created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            person["id"], application["id"], person["person_key"],
            person["full_name"], person["nationality"], False,
            json.dumps({"fixture": FIXTURE_KEY}, sort_keys=True), epoch,
        ),
    )
    audit(
        action="document_renewal_staging_person_seeded",
        target=f"director:{person['id']}",
        detail=f"Seeded governed fixture {FIXTURE_KEY} person",
        after_state={"id": person["id"], "person_key": person["person_key"]},
    )

    db.execute(
        """
        INSERT INTO documents
            (id,application_id,person_id,person_type,doc_type,doc_name,file_path,
             slot_key,is_current,version,expiry_date,expiry_source,
             expiry_confidence,expiry_extracted_at,verification_status,
             verification_results,review_status,uploaded_by_actor_type,
             uploaded_by_actor_id,uploaded_by_display,upload_source,uploaded_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            document["id"], application["id"], person["id"],
            person["person_type"], document["document_type"],
            document["doc_name"], document["file_path"], document["slot_key"],
            True, document["version"], document["expiry_date"],
            document["expiry_source"], 1.0, epoch,
            document["verification_status"],
            json.dumps({"fixture": FIXTURE_KEY}, sort_keys=True),
            document["review_status"], "system", "fixture_seed",
            "Document Renewal staging fixture", "fixture_seed", document["uploaded_at"],
        ),
    )
    audit(
        action="document_renewal_staging_document_seeded",
        target=f"document:{document['id']}",
        detail=f"Seeded governed fixture {FIXTURE_KEY} expired document",
        after_state={
            "id": document["id"],
            "slot_key": document["slot_key"],
            "expiry_date": document["expiry_date"],
        },
    )

    db.execute(
        """
        INSERT INTO monitoring_alerts
            (application_id,client_name,alert_type,severity,detected_by,summary,
             source_reference,status,discovered_via,discovered_at,created_at)
        VALUES (?,?,?,?,?,?,?,'open',?,?,?)
        """,
        (
            application["id"], application["company_name"], alert["alert_type"],
            alert["severity"], alert["detected_by"], alert["summary"],
            alert["source_reference"], alert["discovered_via"],
            epoch, epoch,
        ),
    )
    inserted = _rows(
        db.execute(
            "SELECT id FROM monitoring_alerts WHERE application_id = ? "
            "AND source_reference = ? ORDER BY id",
            (application["id"], alert["source_reference"]),
        )
    )
    if len(inserted) != 1:
        raise DocumentRenewalFixtureSeedError(
            "The governed fixture alert was not created exactly once."
        )
    alert_id = int(inserted[0]["id"])
    audit(
        action="document_renewal_staging_alert_seeded",
        target=f"monitoring_alert:{alert_id}",
        detail=f"Seeded governed fixture {FIXTURE_KEY} alert",
        after_state={
            "id": alert_id,
            "source_reference": alert["source_reference"],
            "status": alert["status"],
        },
    )
    return alert_id


def seed_document_renewal_staging_fixture(
    *,
    dry_run: bool,
    db: Any = None,
) -> Dict[str, Any]:
    """Plan, seed, or verify exactly one fixture.

    Existing matching state is an idempotent no-op. Partial, colliding, or
    drifted state is never repaired implicitly; it requires operator review.
    A dry run is strictly read-only rather than an insert-and-rollback: unlike
    row changes, PostgreSQL sequence increments do not roll back.
    """

    environment = _require_environment()
    validate_manifest()
    spec = fixture_spec()
    owns_connection = db is None
    if owns_connection:
        from db import get_db

        db = get_db()
    try:
        _require_database_identity(db, environment)
        state = _load_reserved_state(db, spec)
        alert_id = _validate_existing(state, spec)
        would_create = alert_id is None
        if dry_run and would_create:
            return {
                **spec,
                "alert_id": None,
                "created": False,
                "would_create": True,
                "dry_run": True,
                "read_only": True,
                "rolled_back": False,
                "linkage_status": "planned",
            }
        if would_create:
            alert_id = _insert_fixture(db, spec)

        from monitoring_alert_linkage import resolve_alert_linkage

        linkage = resolve_alert_linkage(db, alert_id)
        document = linkage.get("document") or {}
        application = linkage.get("application") or {}
        if (
            linkage.get("linkage_status") != "linked"
            or linkage.get("linkage_type") != "document_expiry"
            or str(application.get("id")) != spec["application_id"]
            or str(application.get("customer_id")) != spec["customer_id"]
            or str(document.get("canonical_document_id")) != spec["document_id"]
            or str(document.get("person_id")) != spec["person_id"]
            or str(document.get("person_type")) != spec["person_type"]
            or int(document.get("current_document_version") or 0)
            != spec["document_version"]
        ):
            raise DocumentRenewalFixtureSeedError(
                "The seeded fixture does not satisfy canonical document linkage."
            )
        if not dry_run:
            db.commit()
        return {
            **spec,
            "alert_id": int(alert_id),
            "created": would_create,
            "would_create": would_create,
            "dry_run": bool(dry_run),
            "read_only": bool(dry_run),
            "rolled_back": False,
            "linkage_status": "linked",
        }
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_connection:
            db.close()


__all__ = [
    "ALLOWED_ENVIRONMENTS",
    "DocumentRenewalFixtureSeedError",
    "STAGING_DATABASE_FINGERPRINT_ENV",
    "seed_document_renewal_staging_fixture",
]
