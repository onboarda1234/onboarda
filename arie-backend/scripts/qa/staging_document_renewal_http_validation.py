#!/usr/bin/env python3
"""Bounded real-route staging validation for the renewal fixture.

The caller supplies the staging Secrets Manager JSON through an environment
variable.  This script mints two ten-minute machine tokens in memory, never
prints them, and performs only the reviewed fixture GETs plus two upload POSTs:
one accepted synthetic PDF and one required duplicate rejection.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import ssl
import sys
import time
from typing import Any, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import (
    HTTPSHandler,
    HTTPRedirectHandler,
    Request,
    build_opener,
)


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from document_renewal_staging_activation import (  # noqa: E402
    HARNESS_CLIENT_PURPOSE,
    HARNESS_CLIENT_PURPOSE_CLAIM,
    HARNESS_CLIENT_RUN_CLAIM,
    OTHER_MONITORING_FLAGS,
    RENEWAL_FLAG,
)
from fixtures.document_renewal_staging import fixture_spec  # noqa: E402


SECRET_JSON_ENV = "DOCUMENT_RENEWAL_STAGING_SECRET_JSON"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
QA_SMOKE_OFFICER_ID = "github-actions:day6-staging-smoke"
UPLOAD_BINDING_CONTRACT_VERSION = "monitoring_document_renewal_upload_binding_v1"
UPLOADED_DOCUMENT_ID_PREFIX = "renewal-candidate:"
_PORTAL_PUBLIC_BINDING_FIELDS = frozenset(
    {
        "upload_id",
        "renewal_request_id",
        "application_id",
        "document_type",
        "upload_timestamp",
        "binding_status",
        "contract_version",
    }
)
_PORTAL_PUBLIC_UPLOAD_FIELDS = frozenset(
    {
        "upload_id",
        "renewal_request_id",
        "application_id",
        "original_filename",
        "file_size",
        "mime_type",
        "uploaded_at",
        "upload_timestamp",
    }
)

# PR #921 deliberately keeps these authoritative identities out of the client
# response.  The same request is re-read through the authenticated officer
# Monitoring projection below, where the complete linkage can be checked
# without widening the portal's disclosure boundary.
_PORTAL_PROTECTED_RESPONSE_FIELDS = frozenset(
    {
        "customer_id",
        "person_id",
        "person_type",
        "document_id",
        "document_version",
        "monitoring_alert_id",
        "original_document_id",
        "original_document_version",
        "uploaded_document_id",
        "created_by",
        "cancelled_by",
        "cancel_reason",
        "updated_by",
        "uploaded_by",
        "storage_key",
        "file_sha256",
        "binding_fingerprint",
        "cleanup_id",
        "local_path",
        "s3_version_id",
    }
)


class HttpValidationError(RuntimeError):
    """The exact fixture route did not satisfy the validation contract."""


class _RejectRedirects(HTTPRedirectHandler):
    """Never forward an Authorization header across a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _token(secret: bytes, payload: Mapping[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (
        f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url(json.dumps(dict(payload), separators=(',', ':')).encode())}"
    )
    signature = hmac.new(
        secret,
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _machine_tokens(secret_json: str, *, customer_id: str, run_id: str) -> tuple[str, str]:
    try:
        secret = str(json.loads(secret_json)["JWT_SECRET"]).encode("utf-8")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HttpValidationError("staging JWT signing material is unavailable") from exc
    if len(secret) < 32:
        raise HttpValidationError("staging JWT signing material is invalid")
    now = int(time.time())
    common = {
        "iss": "arie-finance",
        "iat": now,
        "nbf": now,
        "exp": now + 600,
    }
    client = _token(
        secret,
        {
            **common,
            "sub": customer_id,
            "role": "client",
            "name": "Document Renewal Synthetic Client",
            "type": "client",
            HARNESS_CLIENT_PURPOSE_CLAIM: HARNESS_CLIENT_PURPOSE,
            HARNESS_CLIENT_RUN_CLAIM: run_id,
            "jti": f"{run_id}-client-{secrets.token_hex(8)}",
        },
    )
    officer = _token(
        secret,
        {
            **common,
            "sub": QA_SMOKE_OFFICER_ID,
            "role": "sco",
            "name": "Document Renewal Staging Harness",
            "type": "officer",
            "jti": f"{run_id}-officer-{secrets.token_hex(8)}",
        },
    )
    return client, officer


def _safe_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "staging.regmind.co"
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise HttpValidationError(
            "validation URL must be exactly the approved staging HTTPS host"
        )
    return "https://staging.regmind.co"


def _request(
    base_url: str,
    method: str,
    path: str,
    token: str,
    *,
    body: Optional[bytes] = None,
    content_type: Optional[str] = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    if method not in {"GET", "POST"}:
        raise HttpValidationError("unsupported validation method")
    if not path.startswith("/api/") or ".." in path:
        raise HttpValidationError("unsafe validation path")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "regmind-document-renewal-staging-harness/1",
        "X-Request-ID": f"renewal-harness-{secrets.token_hex(12)}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(base_url + path, data=body, headers=headers, method=method)
    opener = build_opener(
        HTTPSHandler(context=ssl.create_default_context()),
        _RejectRedirects(),
    )
    try:
        response = opener.open(request, timeout=30)
        status = int(response.status)
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        response_headers = dict(response.headers.items())
    except HTTPError as exc:
        status = int(exc.code)
        raw = exc.read(MAX_RESPONSE_BYTES + 1)
        response_headers = dict(exc.headers.items()) if exc.headers else {}
    except (URLError, TimeoutError, OSError) as exc:
        raise HttpValidationError("staging request failed") from exc
    if 300 <= status < 400:
        raise HttpValidationError("staging redirects are prohibited")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise HttpValidationError("staging response exceeded the safety limit")
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpValidationError("staging returned a non-JSON response") from exc
    if not isinstance(payload, dict):
        raise HttpValidationError("staging returned a non-object response")
    allowlisted_headers = {
        key.lower(): value
        for key, value in response_headers.items()
        if key.lower() in {"x-request-id", "x-ratelimit-limit", "x-ratelimit-remaining"}
    }
    return status, payload, allowlisted_headers


def _multipart_pdf() -> tuple[bytes, str]:
    boundary = "----RegMindRenewalHarness" + secrets.token_hex(12)
    # Harmless, bounded synthetic content.  It is deliberately not a real
    # identity document and is deleted by the approved cleanup path.
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        b"% RegMind synthetic renewal harness - no customer data\n"
        b"%%EOF\n"
    )
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; '
        'filename="synthetic-renewal-passport.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("ascii")
    tail = f"\r\n--{boundary}--\r\n".encode("ascii")
    return head + pdf + tail, f"multipart/form-data; boundary={boundary}"


def _monitoring_flags(payload: Mapping[str, Any]) -> dict[str, str]:
    status = payload.get("monitoring_feature_flags") or {}
    if status.get("read_only") is not True or status.get("activation_controls") is not False:
        raise HttpValidationError("Monitoring feature status endpoint is not read-only")
    features = status.get("features") or {}
    result = {}
    for name in (RENEWAL_FLAG, *OTHER_MONITORING_FLAGS):
        item = features.get(name) or {}
        state = str(item.get("state") or "")
        if item.get("enabled") is not (state == "ON") or state not in {"ON", "OFF"}:
            raise HttpValidationError("Monitoring feature status is malformed")
        result[name] = state
    return result


def _protected_portal_response_fields(value: Any) -> set[str]:
    """Find protected key names at every level of a client response."""

    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _PORTAL_PROTECTED_RESPONSE_FIELDS:
                found.add(str(key))
            found.update(_protected_portal_response_fields(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_protected_portal_response_fields(item))
    return found


def _is_timezone_aware_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _validated_portal_binding(
    payload: Mapping[str, Any],
    *,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the exact client-safe binding without requesting private IDs."""

    request = payload.get("request")
    top_level = payload.get("binding")
    nested = request.get("upload_binding") if isinstance(request, Mapping) else None
    if not isinstance(top_level, Mapping) or not isinstance(nested, Mapping):
        raise HttpValidationError("real upload route omitted the public binding")
    binding = dict(top_level)
    if binding != dict(nested):
        raise HttpValidationError("real upload route returned conflicting bindings")

    upload_id = binding.get("upload_id")
    upload_timestamp = binding.get("upload_timestamp")
    expected = {
        "renewal_request_id": spec["request_id"],
        "application_id": spec["application_id"],
        "document_type": spec["document_type"],
        "binding_status": "bound",
        "contract_version": UPLOAD_BINDING_CONTRACT_VERSION,
    }
    if (
        not isinstance(upload_id, str)
        or not upload_id
        or upload_id != upload_id.strip()
        or len(upload_id) > 255
        or not _is_timezone_aware_iso_timestamp(upload_timestamp)
        or any(binding.get(key) != value for key, value in expected.items())
    ):
        raise HttpValidationError("real upload route returned an invalid public binding")

    if _protected_portal_response_fields(payload):
        raise HttpValidationError("real upload route exposed protected binding linkage")
    if set(binding) != _PORTAL_PUBLIC_BINDING_FIELDS:
        raise HttpValidationError("real upload route returned an invalid public schema")

    uploads = request.get("uploads")
    if not isinstance(uploads, list) or len(uploads) != 1:
        raise HttpValidationError("real upload route omitted the public upload metadata")
    upload = uploads[0]
    expected_upload = {
        "upload_id": upload_id,
        "renewal_request_id": spec["request_id"],
        "application_id": spec["application_id"],
        "original_filename": "synthetic-renewal-passport.pdf",
        "mime_type": "application/pdf",
        "uploaded_at": upload_timestamp,
        "upload_timestamp": upload_timestamp,
    }
    if (
        not isinstance(upload, Mapping)
        or set(upload) != _PORTAL_PUBLIC_UPLOAD_FIELDS
        or any(upload.get(key) != value for key, value in expected_upload.items())
        or type(upload.get("file_size")) is not int
        or upload["file_size"] < 1
    ):
        raise HttpValidationError("real upload route returned invalid public upload metadata")
    return binding


def _validate_authoritative_monitoring_binding(
    renewal: Mapping[str, Any],
    *,
    spec: Mapping[str, Any],
    portal_binding: Mapping[str, Any],
) -> None:
    """Prove the full stable-ID tuple on the authenticated officer projection."""

    binding = renewal.get("upload_binding")
    if not isinstance(binding, Mapping):
        raise HttpValidationError("Monitoring omitted the authoritative upload binding")
    upload_id = portal_binding["upload_id"]
    expected = {
        "upload_id": upload_id,
        "renewal_request_id": spec["request_id"],
        "application_id": spec["application_id"],
        "customer_id": spec["customer_id"],
        "person_id": spec["person_id"],
        "person_type": spec["person_type"],
        "original_document_id": spec["document_id"],
        "original_document_version": spec["document_version"],
        "uploaded_document_id": f"{UPLOADED_DOCUMENT_ID_PREFIX}{upload_id}",
        "document_type": spec["document_type"],
        "upload_timestamp": portal_binding["upload_timestamp"],
        "uploaded_by": spec["customer_id"],
        "binding_status": "bound",
        "contract_version": UPLOAD_BINDING_CONTRACT_VERSION,
    }
    if (
        renewal.get("binding_current") is not True
        or type(binding.get("original_document_version")) is not int
        or any(binding.get(key) != value for key, value in expected.items())
    ):
        raise HttpValidationError(
            "Monitoring returned an invalid authoritative upload binding"
        )
    if {"storage_key", "file_sha256", "binding_fingerprint"}.intersection(binding):
        raise HttpValidationError("Monitoring exposed private upload storage metadata")


def validate_real_routes(
    *,
    base_url: str,
    expected_sha: str,
    run_id: str,
    secret_json: str,
) -> dict[str, Any]:
    spec = fixture_spec()
    base = _safe_base_url(base_url)
    if len(expected_sha) != 40 or any(ch not in "0123456789abcdef" for ch in expected_sha):
        raise HttpValidationError("expected SHA is invalid")
    client_token, officer_token = _machine_tokens(
        secret_json,
        customer_id=str(spec["customer_id"]),
        run_id=run_id,
    )

    status, version, _ = _request(base, "GET", "/api/version", officer_token)
    if (
        status != 200
        or version.get("git_sha") != expected_sha
        or version.get("image_tag") != expected_sha
        or version.get("environment") != "staging"
    ):
        raise HttpValidationError("authenticated deployed version is not exact")
    status, config, _ = _request(
        base, "GET", "/api/config/environment", officer_token
    )
    if status != 200:
        raise HttpValidationError("feature-state read failed")
    flags = _monitoring_flags(config)
    if flags[RENEWAL_FLAG] != "ON" or any(
        flags[name] != "OFF" for name in OTHER_MONITORING_FLAGS
    ):
        raise HttpValidationError("temporary governed flag state is not exact")

    application = quote(str(spec["application_id"]), safe="")
    request_id = quote(str(spec["request_id"]), safe="")
    list_path = (
        f"/api/portal/applications/{application}/renewal-requests?limit=50&offset=0"
    )
    status, before, _ = _request(base, "GET", list_path, client_token)
    requests = before.get("renewal_requests") or []
    if status != 200 or before.get("feature_enabled") is not True or len(requests) != 1:
        raise HttpValidationError("portal did not expose exactly one enabled fixture request")
    request = requests[0]
    if (
        request.get("request_id") != spec["request_id"]
        or request.get("request_status") != "awaiting_upload"
        or request.get("upload_allowed") is not True
    ):
        raise HttpValidationError("portal renewal request is not awaiting the exact upload")

    body, content_type = _multipart_pdf()
    upload_path = (
        f"/api/portal/applications/{application}/renewal-requests/{request_id}/upload"
    )
    status, uploaded, upload_headers = _request(
        base,
        "POST",
        upload_path,
        client_token,
        body=body,
        content_type=content_type,
    )
    uploaded_request = uploaded.get("request")
    if (
        status != 201
        or uploaded.get("status") != "upload_received"
        or not isinstance(uploaded_request, Mapping)
        or uploaded_request.get("request_id") != spec["request_id"]
        or uploaded_request.get("request_status") != "upload_received"
    ):
        raise HttpValidationError("real upload route did not accept the exact request")
    portal_binding = _validated_portal_binding(uploaded, spec=spec)

    duplicate_status, duplicate, duplicate_headers = _request(
        base,
        "POST",
        upload_path,
        client_token,
        body=body,
        content_type=content_type,
    )
    if (
        duplicate_status != 409
        or duplicate.get("error_code") != "upload_already_received"
    ):
        raise HttpValidationError("duplicate upload did not fail closed")

    status, after, _ = _request(base, "GET", list_path, client_token)
    after_requests = after.get("renewal_requests") or []
    if (
        status != 200
        or len(after_requests) != 1
        or after_requests[0].get("request_id") != spec["request_id"]
        or after_requests[0].get("request_status") != "upload_received"
        or after_requests[0].get("upload_binding") != portal_binding
    ):
        raise HttpValidationError("portal did not project Upload Received")
    if _protected_portal_response_fields(after):
        raise HttpValidationError(
            "portal follow-up exposed protected binding linkage"
        )

    query = urlencode(
        {
            "show_fixtures": "true",
            "client": spec["application_id"],
            "include_closed": "true",
            "include_internal_types": "true",
            "include_unmapped": "true",
            "limit": "100",
        }
    )
    status, alert_list, _ = _request(
        base, "GET", f"/api/monitoring/alerts?{query}", officer_token
    )
    candidates = [
        item
        for item in alert_list.get("alerts") or []
        if str(item.get("application_id") or "") == str(spec["application_id"])
        and str(item.get("alert_type") or item.get("type") or "")
        == str(spec["alert_type"])
    ]
    if status != 200 or len(candidates) != 1:
        raise HttpValidationError("Monitoring did not expose exactly one fixture alert")
    alert_id = candidates[0].get("id")
    status, alert, _ = _request(
        base, "GET", f"/api/monitoring/alerts/{int(alert_id)}", officer_token
    )
    renewal = alert.get("renewal_request") or {}
    if (
        status != 200
        or alert.get("status") != "open"
        or alert.get("renewal_feature_enabled") is not True
        or renewal.get("request_id") != spec["request_id"]
        or renewal.get("request_status") != "upload_received"
    ):
        raise HttpValidationError(
            "Monitoring projection changed the alert or missed Upload Received"
        )
    _validate_authoritative_monitoring_binding(
        renewal,
        spec=spec,
        portal_binding=portal_binding,
    )

    return {
        "schema_version": "document_renewal_staging_http_validation_v1",
        "ok": True,
        "run_id": run_id,
        "release_sha": expected_sha,
        "fixture_key": spec["fixture_key"],
        "application_id": spec["application_id"],
        "alert_id": int(alert_id),
        "request_id": spec["request_id"],
        "portal_before_status": "awaiting_upload",
        "upload_status": "upload_received",
        "binding_status": "bound",
        "binding_contract_version": UPLOAD_BINDING_CONTRACT_VERSION,
        "binding_lineage_verified": True,
        "portal_binding_redaction_verified": True,
        "duplicate_status": duplicate_status,
        "duplicate_error_code": duplicate.get("error_code"),
        "monitoring_alert_status": alert.get("status"),
        "governed_flags": flags,
        "request_ids": {
            "upload": upload_headers.get("x-request-id"),
            "duplicate": duplicate_headers.get("x-request-id"),
        },
        "credential_values_recorded": False,
        "synthetic_upload_sha256": hashlib.sha256(body).hexdigest(),
    }


def validate_restored_routes(
    *,
    base_url: str,
    expected_sha: str,
    run_id: str,
    secret_json: str,
) -> dict[str, Any]:
    """Read-only proof that the exact restored service evaluates every flag OFF."""

    spec = fixture_spec()
    base = _safe_base_url(base_url)
    if len(expected_sha) != 40 or any(
        ch not in "0123456789abcdef" for ch in expected_sha
    ):
        raise HttpValidationError("expected SHA is invalid")
    _client_token, officer_token = _machine_tokens(
        secret_json,
        customer_id=str(spec["customer_id"]),
        run_id=run_id,
    )
    status, version, version_headers = _request(
        base, "GET", "/api/version", officer_token
    )
    if (
        status != 200
        or version.get("git_sha") != expected_sha
        or version.get("image_tag") != expected_sha
        or version.get("environment") != "staging"
    ):
        raise HttpValidationError("restored deployed version is not exact")
    status, config, config_headers = _request(
        base, "GET", "/api/config/environment", officer_token
    )
    if status != 200:
        raise HttpValidationError("restored feature-state read failed")
    flags = _monitoring_flags(config)
    if any(flags[name] != "OFF" for name in (RENEWAL_FLAG, *OTHER_MONITORING_FLAGS)):
        raise HttpValidationError("restored governed feature state is not all OFF")
    return {
        "schema_version": "document_renewal_staging_restored_http_v1",
        "ok": True,
        "run_id": run_id,
        "release_sha": expected_sha,
        "environment": "staging",
        "governed_flags": flags,
        "request_ids": {
            "version": version_headers.get("x-request-id"),
            "config": config_headers.get("x-request-id"),
        },
        "credential_values_recorded": False,
        "mutations_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://staging.regmind.co")
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--mode",
        choices=("full", "restored-off"),
        default="full",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    secret_json = os.environ.get(SECRET_JSON_ENV)
    if not secret_json:
        print("staging_document_renewal_http_validation: secret input is missing", file=sys.stderr)
        return 2
    try:
        validator = (
            validate_real_routes
            if args.mode == "full"
            else validate_restored_routes
        )
        result = validator(
            base_url=args.base_url,
            expected_sha=args.expected_sha,
            run_id=args.run_id,
            secret_json=secret_json,
        )
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"ok": True, "output": args.output}))
        return 0
    except HttpValidationError as exc:
        print(f"staging_document_renewal_http_validation: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HttpValidationError",
    "SECRET_JSON_ENV",
    "validate_real_routes",
    "validate_restored_routes",
]
