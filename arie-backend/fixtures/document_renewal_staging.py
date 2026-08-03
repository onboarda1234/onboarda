"""Reviewed contract for the one Document Renewal staging fixture.

Importing this module is data-only. It performs no database access, feature
activation, scheduler registration, or fixture seeding.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


MANIFEST_PATH = Path(__file__).with_name(
    "document_renewal_staging_fixture_v1.json"
)
FIXTURE_KEY = "document-renewal-expired-v1"
FIXTURE_VERSION = "document_renewal_staging_fixture_v1"
FIXTURE_MARKER = "FIX_DOC_RENEWAL_STAGING_V1"


class DocumentRenewalFixtureValidationError(RuntimeError):
    """The reviewed fixture manifest is incomplete or internally inconsistent."""


def load_manifest() -> Dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def manifest_sha256() -> str:
    return hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise DocumentRenewalFixtureValidationError(
            f"{field} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise DocumentRenewalFixtureValidationError(
            f"{field} must include an explicit timezone"
        )
    return parsed.astimezone(timezone.utc)


def validate_manifest(
    manifest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    source = dict(load_manifest() if manifest is None else manifest)
    errors = []
    expected_sections = {
        "fixture", "client", "application", "person", "document", "alert", "renewal"
    }
    if set(source) != expected_sections:
        errors.append(
            "manifest sections must be exactly " + ", ".join(sorted(expected_sections))
        )

    fixture = source.get("fixture") or {}
    client = source.get("client") or {}
    application = source.get("application") or {}
    person = source.get("person") or {}
    document = source.get("document") or {}
    alert = source.get("alert") or {}
    renewal = source.get("renewal") or {}

    expected_fixture = {
        "fixture_key": FIXTURE_KEY,
        "fixture_marker": FIXTURE_MARKER,
        "fixture_version": FIXTURE_VERSION,
        "synthetic": True,
        "non_production": True,
        "permanent_baseline": True,
        "deterministic_epoch": "2026-08-01T00:00:00+00:00",
    }
    for key, expected in expected_fixture.items():
        if fixture.get(key) != expected:
            errors.append(f"fixture.{key} must be {expected!r}")

    deterministic_ids = {
        "client.id": client.get("id"),
        "application.id": application.get("id"),
        "person.id": person.get("id"),
        "document.id": document.get("id"),
    }
    for field, value in deterministic_ids.items():
        if not isinstance(value, str) or not value.startswith("f1xed"):
            errors.append(f"{field} must use the reserved f1xed fixture namespace")
    if len(set(deterministic_ids.values())) != len(deterministic_ids):
        errors.append("fixture primary identifiers must be unique")

    if client.get("status") != "inactive":
        errors.append("client.status must be 'inactive' outside the minted-token harness")
    if not str(client.get("email") or "").endswith("@fixture.invalid"):
        errors.append("client.email must use the fixture.invalid domain")
    if application.get("ref") != "FX-DOC-RENEWAL-001":
        errors.append("application.ref must use the reviewed reserved reference")
    if application.get("status") != "approved":
        errors.append("application.status must be 'approved'")
    if person.get("person_type") != "director":
        errors.append("person.person_type must be 'director'")

    expected_slot = (
        f"person:{person.get('person_type')}:{person.get('id')}:"
        f"{document.get('document_type')}"
    )
    if document.get("slot_key") != expected_slot:
        errors.append("document.slot_key must encode the exact owner and document type")
    if document.get("version") != 1 or document.get("is_current") is not True:
        errors.append("document must be the single current version 1")
    if document.get("document_type") != "passport":
        errors.append("document.document_type must be the reviewed passport type")
    if document.get("verification_status") != "verified":
        errors.append("document.verification_status must be 'verified'")
    if document.get("review_status") != "accepted":
        errors.append("document.review_status must be 'accepted'")

    try:
        expiry = _parse_timestamp(document.get("expiry_date"), field="document.expiry_date")
        epoch = _parse_timestamp(
            fixture.get("deterministic_epoch"), field="fixture.deterministic_epoch"
        )
        if expiry >= epoch:
            errors.append("document.expiry_date must be before the deterministic epoch")
    except DocumentRenewalFixtureValidationError as exc:
        errors.append(str(exc))

    if alert.get("source_reference") != f"document:{document.get('id')}":
        errors.append("alert.source_reference must identify the exact canonical document")
    expected_alert = {
        "alert_type": "document_expired",
        "detected_by": "document_health_monitor",
        "discovered_via": "document_health",
        "status": "open",
    }
    for key, expected in expected_alert.items():
        if alert.get(key) != expected:
            errors.append(f"alert.{key} must be {expected!r}")
    if renewal.get("request_reason") != "expired":
        errors.append("renewal.request_reason must be 'expired'")
    request_id = str(renewal.get("request_id") or "")
    if not request_id or len(request_id) > 255:
        errors.append("renewal.request_id must be a bounded deterministic identifier")

    if errors:
        raise DocumentRenewalFixtureValidationError("; ".join(errors))
    return {
        "valid": True,
        "fixture_key": FIXTURE_KEY,
        "fixture_version": FIXTURE_VERSION,
        "manifest_sha256": manifest_sha256() if manifest is None else None,
    }


def fixture_spec() -> Dict[str, Any]:
    """Return the only registered fixture as a flat, immutable-by-copy contract."""

    manifest = load_manifest()
    validate_manifest(manifest)
    fixture = manifest["fixture"]
    client = manifest["client"]
    application = manifest["application"]
    person = manifest["person"]
    document = manifest["document"]
    alert = manifest["alert"]
    renewal = manifest["renewal"]
    return {
        "fixture_key": fixture["fixture_key"],
        "fixture_version": fixture["fixture_version"],
        "fixture_contract_version": fixture["fixture_version"],
        "fixture_marker": fixture["fixture_marker"],
        "synthetic": True,
        "non_production": True,
        "source": "fixtures.document_renewal_staging",
        "manifest_sha256": manifest_sha256(),
        "deterministic_epoch": fixture["deterministic_epoch"],
        "customer_id": client["id"],
        "customer_email": client["email"],
        "application_id": application["id"],
        "application_ref": application["ref"],
        "person_id": person["id"],
        "person_type": person["person_type"],
        "person_key": person["person_key"],
        "document_id": document["id"],
        "document_type": document["document_type"],
        "document_version": document["version"],
        "document_slot_key": document["slot_key"],
        "document_expiry_date": document["expiry_date"],
        "alert_type": alert["alert_type"],
        "alert_source_reference": alert["source_reference"],
        "alert_status": alert["status"],
        "request_id": renewal["request_id"],
        "request_reason": renewal["request_reason"],
    }


__all__ = [
    "DocumentRenewalFixtureValidationError",
    "FIXTURE_KEY",
    "FIXTURE_MARKER",
    "FIXTURE_VERSION",
    "MANIFEST_PATH",
    "fixture_spec",
    "load_manifest",
    "manifest_sha256",
    "validate_manifest",
]
