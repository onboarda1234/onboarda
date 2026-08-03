"""Fail-closed staging lease for the governed document-renewal harness.

The normal governed feature flag remains the source of truth.  This module
adds a second, staging-only boundary used while the permanent synthetic
fixture is being exercised through the real HTTP routes.  A harness rollout
is effective only while every signed-off environment value is exact and its
short lease has not expired.  It never enables production and never accepts
an operator-selected fixture.

The lease is deliberately process local.  The operator workflow temporarily
deploys a backend task definition derived from the exact live revision and
restores the captured revision afterwards.  Even if that restoration is
delayed, expiry makes the effective feature state OFF and all writes fail
closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import json
import re
from typing import Any, Mapping, Optional


RENEWAL_FLAG = "ENABLE_DOCUMENT_RENEWAL_AUTOMATION"
OTHER_MONITORING_FLAGS = (
    "ENABLE_AGENT1_REFRESH_VERIFICATION",
    "ENABLE_MONITORING_SCREENING_CHANGE",
    "ENABLE_MONITORING_AUTO_RESOLUTION",
)

HARNESS_MODE_ENV = "DOCUMENT_RENEWAL_STAGING_HARNESS"
HARNESS_RUN_ID_ENV = "DOCUMENT_RENEWAL_STAGING_HARNESS_RUN_ID"
HARNESS_ISSUED_AT_ENV = "DOCUMENT_RENEWAL_STAGING_HARNESS_ISSUED_AT"
HARNESS_EXPIRES_AT_ENV = "DOCUMENT_RENEWAL_STAGING_HARNESS_EXPIRES_AT"
HARNESS_MANIFEST_SHA_ENV = "DOCUMENT_RENEWAL_STAGING_HARNESS_MANIFEST_SHA256"
HARNESS_RELEASE_SHA_ENV = "DOCUMENT_RENEWAL_STAGING_HARNESS_RELEASE_SHA"
HARNESS_CLIENT_PURPOSE_CLAIM = "document_renewal_harness_purpose"
HARNESS_CLIENT_RUN_CLAIM = "document_renewal_harness_run_id"
HARNESS_CLIENT_PURPOSE = "governed_fixture_portal_validation_v1"

MAX_ACTIVATION_SECONDS = 30 * 60
MAX_CLOCK_SKEW_SECONDS = 60
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^gh-[1-9][0-9]{0,19}-[1-9][0-9]{0,2}$")


class StagingHarnessActivationError(RuntimeError):
    """The temporary staging activation is absent, invalid, or out of scope."""


@dataclass(frozen=True)
class ActivationState:
    configured: bool
    active: bool
    reason: str
    run_id: Optional[str] = None
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    release_sha: Optional[str] = None
    manifest_sha256: Optional[str] = None


def _utc_now(value: Optional[datetime] = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0)


def _timestamp(value: Any, *, name: str) -> datetime:
    raw = str(value or "")
    if not raw.endswith("Z"):
        raise StagingHarnessActivationError(f"{name} must be a UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise StagingHarnessActivationError(
            f"{name} must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.microsecond:
        raise StagingHarnessActivationError(
            f"{name} must not contain fractional seconds"
        )
    return parsed.astimezone(timezone.utc)


def _fixture_manifest_sha256() -> str:
    # Lazy import avoids making the permanent fixture registry a production
    # startup dependency when harness mode is not configured.
    from fixtures.document_renewal_staging import manifest_sha256

    value = str(manifest_sha256() or "")
    if not _DIGEST_RE.fullmatch(value):
        raise StagingHarnessActivationError(
            "the governed fixture manifest fingerprint is invalid"
        )
    return value


def harness_mode_configured(environ: Optional[Mapping[str, str]] = None) -> bool:
    values = environ if environ is not None else os.environ
    raw = values.get(HARNESS_MODE_ENV)
    if raw in (None, ""):
        return False
    if raw != "1":
        raise StagingHarnessActivationError(
            f"{HARNESS_MODE_ENV} must be exactly '1' when configured"
        )
    return True


def activation_state(
    *,
    environ: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
    expected_manifest_sha256: Optional[str] = None,
) -> ActivationState:
    """Evaluate the process-local harness lease without leaking its inputs."""

    values = environ if environ is not None else os.environ
    if not harness_mode_configured(values):
        return ActivationState(False, False, "not_configured")
    if values.get("ENVIRONMENT") != "staging":
        raise StagingHarnessActivationError(
            "document-renewal harness mode is permitted only in staging"
        )
    if values.get(RENEWAL_FLAG) != "true":
        raise StagingHarnessActivationError(
            f"{RENEWAL_FLAG} must be exactly 'true' in harness mode"
        )
    enabled_forbidden = [
        name for name in OTHER_MONITORING_FLAGS if values.get(name) != "false"
    ]
    if enabled_forbidden:
        raise StagingHarnessActivationError(
            "every non-renewal governed Monitoring flag must be exactly false"
        )

    run_id = str(values.get(HARNESS_RUN_ID_ENV) or "")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise StagingHarnessActivationError("harness run ID is invalid")
    release_sha = str(values.get(HARNESS_RELEASE_SHA_ENV) or "")
    deployed_sha = str(values.get("GIT_SHA") or "")
    image_tag = str(values.get("IMAGE_TAG") or "")
    if not _SHA_RE.fullmatch(release_sha):
        raise StagingHarnessActivationError("harness release SHA is invalid")
    if deployed_sha != release_sha or image_tag != release_sha:
        raise StagingHarnessActivationError(
            "harness release SHA does not match deployed task provenance"
        )

    manifest_sha = str(values.get(HARNESS_MANIFEST_SHA_ENV) or "")
    authoritative_manifest = (
        str(expected_manifest_sha256)
        if expected_manifest_sha256 is not None
        else _fixture_manifest_sha256()
    )
    if not _DIGEST_RE.fullmatch(authoritative_manifest):
        raise StagingHarnessActivationError(
            "expected fixture manifest fingerprint is invalid"
        )
    if manifest_sha != authoritative_manifest:
        raise StagingHarnessActivationError(
            "harness fixture manifest fingerprint does not match"
        )

    issued_at = _timestamp(values.get(HARNESS_ISSUED_AT_ENV), name=HARNESS_ISSUED_AT_ENV)
    expires_at = _timestamp(
        values.get(HARNESS_EXPIRES_AT_ENV), name=HARNESS_EXPIRES_AT_ENV
    )
    lifetime = int((expires_at - issued_at).total_seconds())
    if lifetime < 60 or lifetime > MAX_ACTIVATION_SECONDS:
        raise StagingHarnessActivationError(
            "harness lease must last between 60 and 1800 seconds"
        )
    current = _utc_now(now)
    if issued_at > current + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise StagingHarnessActivationError("harness lease is not active yet")
    if current >= expires_at:
        return ActivationState(
            True,
            False,
            "expired",
            run_id,
            issued_at,
            expires_at,
            release_sha,
            manifest_sha,
        )
    return ActivationState(
        True,
        True,
        "active",
        run_id,
        issued_at,
        expires_at,
        release_sha,
        manifest_sha,
    )


def effective_renewal_flag(
    base_enabled: bool,
    *,
    environ: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Return the ordinary flag, narrowed by the staging lease when present."""

    values = environ if environ is not None else os.environ
    try:
        configured = harness_mode_configured(values)
    except StagingHarnessActivationError:
        return False
    if not configured:
        return bool(base_enabled)
    if not base_enabled:
        return False
    try:
        return activation_state(environ=values, now=now).active
    except StagingHarnessActivationError:
        return False


def validate_configured_activation(
    *,
    environ: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
) -> ActivationState:
    """Fail startup when configured harness metadata is malformed.

    An already expired, otherwise valid lease is safe and returns an inactive
    state so a delayed rollback cannot put the service into a crash loop.
    """

    return activation_state(environ=environ, now=now)


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _row_dicts(cursor: Any) -> list[dict[str, Any]]:
    try:
        return [_row_dict(row) for row in cursor.fetchall()]
    except (AttributeError, TypeError, ValueError):
        return []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "on"}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _fixture_spec() -> dict[str, Any]:
    from fixtures.document_renewal_staging import fixture_spec

    spec = dict(fixture_spec())
    required = (
        "manifest_sha256",
        "customer_id",
        "application_id",
        "application_ref",
        "person_id",
        "person_type",
        "document_id",
        "document_type",
        "document_version",
        "document_slot_key",
        "alert_type",
        "alert_source_reference",
        "request_id",
    )
    if any(spec.get(name) in (None, "") for name in required):
        raise StagingHarnessActivationError(
            "governed fixture specification is incomplete"
        )
    if str(spec["manifest_sha256"]) != _fixture_manifest_sha256():
        raise StagingHarnessActivationError(
            "governed fixture specification fingerprint is inconsistent"
        )
    return spec


def fixture_client_actor_allowed(
    token_user: Mapping[str, Any],
    client_row: Mapping[str, Any],
    *,
    method: Any,
    path: Any,
) -> bool:
    """Permit only the no-login fixture client during its exact active lease.

    The permanent client deliberately remains inactive and has no usable
    password.  This narrow bearer-token exception exists solely so the real
    portal route can be exercised by the governed harness; ordinary inactive
    clients and ordinary tokens remain denied.
    """

    try:
        state = activation_state()
        spec = _fixture_spec()
    except StagingHarnessActivationError:
        return False
    if not state.active or not state.run_id:
        return False
    token = _row_dict(token_user)
    row = _row_dict(client_row)
    exact_application = str(spec["application_id"])
    exact_request = str(spec["request_id"])
    allowed_routes = {
        (
            "GET",
            f"/api/portal/applications/{exact_application}/renewal-requests",
        ),
        (
            "POST",
            "/api/portal/applications/"
            f"{exact_application}/renewal-requests/{exact_request}/upload",
        ),
    }
    return (
        (str(method or "").upper(), str(path or "")) in allowed_routes
        and
        str(token.get("sub") or "") == str(spec["customer_id"])
        and str(token.get("type") or "") == "client"
        and str(token.get("role") or "") == "client"
        and str(token.get(HARNESS_CLIENT_PURPOSE_CLAIM) or "")
        == HARNESS_CLIENT_PURPOSE
        and str(token.get(HARNESS_CLIENT_RUN_CLAIM) or "") == state.run_id
        and str(row.get("id") or "") == str(spec["customer_id"])
        and str(row.get("email") or "") == str(spec["customer_email"])
        and str(row.get("status") or "") == "inactive"
        and str(row.get("password_hash") or "") == "fixture-no-login"
    )


def fixture_application_matches(
    db: Any,
    application_id: Any,
    *,
    customer_id: Any = None,
) -> bool:
    """Return true only for the one registered synthetic application."""

    if not activation_state().active:
        return False
    spec = _fixture_spec()
    supplied = str(application_id or "")
    if supplied not in {str(spec["application_id"]), str(spec["application_ref"])}:
        return False
    # Query both reserved identifiers and require exact cardinality. A
    # colliding ID/ref must never be hidden by ``fetchone()``.
    rows = _row_dicts(
        db.execute(
            """
            SELECT id, ref, client_id, status, is_fixture, prescreening_data
              FROM applications
             WHERE id = ? OR ref = ?
             ORDER BY id
            """,
            (spec["application_id"], spec["application_ref"]),
        )
    )
    clients = _row_dicts(
        db.execute(
            """
            SELECT id, email, status, password_hash
              FROM clients
             WHERE id = ? OR email = ?
             ORDER BY id
            """,
            (spec["customer_id"], spec["customer_email"]),
        )
    )
    if len(rows) != 1 or len(clients) != 1:
        return False
    row = rows[0]
    client = clients[0]
    marker = _json_object(row.get("prescreening_data"))
    expected_marker = {
        "fixture_key": spec["fixture_key"],
        "fixture_marker": spec["fixture_marker"],
        "fixture_contract_version": spec["fixture_contract_version"],
        "manifest_sha256": spec["manifest_sha256"],
        "synthetic": True,
        "non_production": True,
        "source": spec["source"],
    }
    if (
        str(row.get("id") or "") != str(spec["application_id"])
        or str(row.get("ref") or "") != str(spec["application_ref"])
        or str(row.get("client_id") or "") != str(spec["customer_id"])
        or str(row.get("status") or "") != "approved"
        or not _truthy(row.get("is_fixture"))
        or any(marker.get(key) != value for key, value in expected_marker.items())
        or str(client.get("id") or "") != str(spec["customer_id"])
        or str(client.get("email") or "") != str(spec["customer_email"])
        or str(client.get("status") or "") != "inactive"
        or str(client.get("password_hash") or "") != "fixture-no-login"
    ):
        return False
    return customer_id in (None, "") or str(customer_id) == str(spec["customer_id"])


def fixture_alert_matches(db: Any, alert_id: Any) -> bool:
    if not activation_state().active:
        return False
    spec = _fixture_spec()
    row = _row_dict(
        db.execute(
            """
            SELECT id, application_id, alert_type, source_reference
              FROM monitoring_alerts
             WHERE id = ?
            """,
            (alert_id,),
        ).fetchone()
    )
    return bool(row) and (
        str(row.get("application_id") or "") == str(spec["application_id"])
        and str(row.get("alert_type") or "") == str(spec["alert_type"])
        and str(row.get("source_reference") or "")
        == str(spec["alert_source_reference"])
        and fixture_application_matches(db, spec["application_id"])
    )


def fixture_request_matches(db: Any, request_id: Any) -> bool:
    if not activation_state().active:
        return False
    spec = _fixture_spec()
    if str(request_id or "") != str(spec["request_id"]):
        return False
    row = _row_dict(
        db.execute(
            """
            SELECT request_id, application_id, customer_id, person_id,
                   person_type, document_id, document_type, document_version,
                   document_slot_key, monitoring_alert_id
              FROM monitoring_document_renewal_requests
             WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
    )
    if not row:
        return False
    expected = {
        "request_id": spec["request_id"],
        "application_id": spec["application_id"],
        "customer_id": spec["customer_id"],
        "person_id": spec["person_id"],
        "person_type": spec["person_type"],
        "document_id": spec["document_id"],
        "document_type": spec["document_type"],
        "document_version": spec["document_version"],
        "document_slot_key": spec["document_slot_key"],
    }
    if any(str(row.get(key) or "") != str(value) for key, value in expected.items()):
        return False
    return fixture_alert_matches(db, row.get("monitoring_alert_id"))


def require_fixture_write_scope(
    db: Any,
    *,
    alert_id: Any = None,
    request_id: Any = None,
    application_id: Any = None,
    customer_id: Any = None,
    operation: str = "write",
) -> None:
    """Reject every harness write not proven to target the one fixture."""

    if not harness_mode_configured():
        return
    state = activation_state()
    if not state.active:
        raise StagingHarnessActivationError("staging harness activation is expired")
    if operation in {"global_scheduler", "global_reminders"}:
        raise StagingHarnessActivationError(
            "global document-renewal consumers are prohibited in harness mode"
        )
    if operation == "fixture_scheduler":
        # The fixture-only scheduler immediately performs the complete
        # repository-owned manifest validation; the delegated canonical create
        # is separately guarded by the resolved alert ID below.
        return
    if db is None:
        raise StagingHarnessActivationError(
            "staging harness writes require an authoritative database scope"
        )
    proven = False
    if alert_id not in (None, ""):
        proven = fixture_alert_matches(db, alert_id)
    elif request_id not in (None, ""):
        proven = fixture_request_matches(db, request_id)
    elif application_id not in (None, ""):
        proven = fixture_application_matches(
            db, application_id, customer_id=customer_id
        )
    if not proven:
        raise StagingHarnessActivationError(
            "document-renewal write is outside the governed staging fixture"
        )


__all__ = [
    "ActivationState",
    "HARNESS_EXPIRES_AT_ENV",
    "HARNESS_ISSUED_AT_ENV",
    "HARNESS_MANIFEST_SHA_ENV",
    "HARNESS_MODE_ENV",
    "HARNESS_RELEASE_SHA_ENV",
    "HARNESS_RUN_ID_ENV",
    "MAX_ACTIVATION_SECONDS",
    "OTHER_MONITORING_FLAGS",
    "RENEWAL_FLAG",
    "StagingHarnessActivationError",
    "activation_state",
    "effective_renewal_flag",
    "fixture_alert_matches",
    "fixture_application_matches",
    "fixture_request_matches",
    "harness_mode_configured",
    "require_fixture_write_scope",
    "validate_configured_activation",
]
