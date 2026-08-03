from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

import document_renewal_staging_activation as activation
from fixtures.document_renewal_staging import fixture_spec, manifest_sha256


SHA = "a" * 40
NOW = datetime(2026, 8, 3, 8, 0, 0, tzinfo=timezone.utc)


def _env(*, expires_delta=900, **overrides):
    values = {
        "ENVIRONMENT": "staging",
        "GIT_SHA": SHA,
        "IMAGE_TAG": SHA,
        activation.RENEWAL_FLAG: "true",
        **{name: "false" for name in activation.OTHER_MONITORING_FLAGS},
        activation.HARNESS_MODE_ENV: "1",
        activation.HARNESS_RUN_ID_ENV: "gh-12345-1",
        activation.HARNESS_ISSUED_AT_ENV: "2026-08-03T07:59:00Z",
        activation.HARNESS_EXPIRES_AT_ENV: (
            NOW + timedelta(seconds=expires_delta)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        activation.HARNESS_MANIFEST_SHA_ENV: manifest_sha256(),
        activation.HARNESS_RELEASE_SHA_ENV: SHA,
    }
    values.update(overrides)
    return values


def _scoped_db():
    spec = fixture_spec()
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE clients (id TEXT, email TEXT, status TEXT, password_hash TEXT)"
    )
    db.execute(
        """CREATE TABLE applications (
               id TEXT, ref TEXT, client_id TEXT, status TEXT,
               is_fixture INTEGER, prescreening_data TEXT
           )"""
    )
    db.execute(
        "CREATE TABLE monitoring_alerts (id INTEGER, application_id TEXT, alert_type TEXT, source_reference TEXT)"
    )
    db.execute(
        """
        CREATE TABLE monitoring_document_renewal_requests (
            request_id TEXT, application_id TEXT, customer_id TEXT,
            person_id TEXT, person_type TEXT, document_id TEXT,
            document_type TEXT, document_version INTEGER,
            document_slot_key TEXT, monitoring_alert_id INTEGER
        )
        """
    )
    db.execute(
        "INSERT INTO clients VALUES (?, ?, 'inactive', 'fixture-no-login')",
        (spec["customer_id"], spec["customer_email"]),
    )
    marker = {
        "fixture_key": spec["fixture_key"],
        "fixture_marker": spec["fixture_marker"],
        "fixture_contract_version": spec["fixture_contract_version"],
        "manifest_sha256": spec["manifest_sha256"],
        "synthetic": True,
        "non_production": True,
        "source": spec["source"],
    }
    db.execute(
        "INSERT INTO applications VALUES (?, ?, ?, 'approved', 1, ?)",
        (
            spec["application_id"],
            spec["application_ref"],
            spec["customer_id"],
            json.dumps(marker, sort_keys=True),
        ),
    )
    db.execute(
        "INSERT INTO monitoring_alerts VALUES (1, ?, ?, ?)",
        (
            spec["application_id"],
            spec["alert_type"],
            spec["alert_source_reference"],
        ),
    )
    db.execute(
        "INSERT INTO monitoring_document_renewal_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            spec["request_id"],
            spec["application_id"],
            spec["customer_id"],
            spec["person_id"],
            spec["person_type"],
            spec["document_id"],
            spec["document_type"],
            spec["document_version"],
            spec["document_slot_key"],
        ),
    )
    return db


def test_row_conversion_supports_sqlite_row_and_rejects_invalid_values():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        db.execute("CREATE TABLE sample (id INTEGER, label TEXT)")
        db.execute("INSERT INTO sample VALUES (1, 'fixture')")
        row = db.execute("SELECT * FROM sample").fetchone()
        assert not isinstance(row, dict)
        assert activation._row_dict(row) == {"id": 1, "label": "fixture"}
        assert activation._row_dict(None) == {}
        assert activation._row_dict(object()) == {}
    finally:
        db.close()


def test_unconfigured_harness_preserves_ordinary_flag_contract():
    assert activation.effective_renewal_flag(True, environ={}) is True
    assert activation.effective_renewal_flag(False, environ={}) is False


def test_exact_short_lease_is_active_and_expiry_forces_off():
    state = activation.activation_state(
        environ=_env(), now=NOW, expected_manifest_sha256=manifest_sha256()
    )
    assert state.active is True
    assert state.run_id == "gh-12345-1"
    expired = activation.activation_state(
        environ=_env(expires_delta=0),
        now=NOW,
        expected_manifest_sha256=manifest_sha256(),
    )
    assert expired.configured is True
    assert expired.active is False
    assert expired.reason == "expired"
    assert activation.effective_renewal_flag(
        True, environ=_env(expires_delta=0), now=NOW
    ) is False


@pytest.mark.parametrize(
    "updates, message",
    [
        ({"ENVIRONMENT": "production"}, "only in staging"),
        ({activation.RENEWAL_FLAG: "false"}, "must be exactly 'true'"),
        ({activation.OTHER_MONITORING_FLAGS[0]: "true"}, "must be exactly false"),
        ({activation.HARNESS_RELEASE_SHA_ENV: "b" * 40}, "deployed task provenance"),
        ({activation.HARNESS_MANIFEST_SHA_ENV: "b" * 64}, "does not match"),
        ({activation.HARNESS_EXPIRES_AT_ENV: "2026-08-03T09:00:00Z"}, "1800"),
    ],
)
def test_invalid_activation_configuration_fails_closed(updates, message):
    with pytest.raises(activation.StagingHarnessActivationError, match=message):
        activation.activation_state(
            environ=_env(**updates),
            now=NOW,
            expected_manifest_sha256=manifest_sha256(),
        )
    assert activation.effective_renewal_flag(
        True, environ=_env(**updates), now=NOW
    ) is False


def test_scope_helpers_accept_only_the_registered_fixture(monkeypatch):
    monkeypatch.setattr(activation.os, "environ", _env())
    monkeypatch.setattr(activation, "_utc_now", lambda value=None: NOW)
    db = _scoped_db()
    spec = fixture_spec()
    try:
        assert activation.fixture_application_matches(db, spec["application_id"])
        assert activation.fixture_alert_matches(db, 1)
        assert activation.fixture_request_matches(db, spec["request_id"])
        assert not activation.fixture_application_matches(db, "real-app")
        assert not activation.fixture_alert_matches(db, 999)
        assert not activation.fixture_request_matches(db, "other-request")
        activation.require_fixture_write_scope(db, alert_id=1)
        activation.require_fixture_write_scope(
            db, request_id=spec["request_id"]
        )
        with pytest.raises(
            activation.StagingHarnessActivationError,
            match="outside the governed staging fixture",
        ):
            activation.require_fixture_write_scope(db, alert_id=999)
        with pytest.raises(
            activation.StagingHarnessActivationError,
            match="global document-renewal consumers",
        ):
            activation.require_fixture_write_scope(
                db, operation="global_scheduler"
            )
    finally:
        db.close()


def test_inactive_fixture_client_auth_is_exact_and_lease_scoped(monkeypatch):
    monkeypatch.setattr(activation.os, "environ", _env())
    monkeypatch.setattr(activation, "_utc_now", lambda value=None: NOW)
    spec = fixture_spec()
    claims = {
        "sub": spec["customer_id"],
        "type": "client",
        "role": "client",
        activation.HARNESS_CLIENT_PURPOSE_CLAIM: activation.HARNESS_CLIENT_PURPOSE,
        activation.HARNESS_CLIENT_RUN_CLAIM: "gh-12345-1",
    }
    row = {
        "id": spec["customer_id"],
        "email": spec["customer_email"],
        "status": "inactive",
        "password_hash": "fixture-no-login",
    }
    list_path = (
        f"/api/portal/applications/{spec['application_id']}/renewal-requests"
    )
    upload_path = (
        f"{list_path}/{spec['request_id']}/upload"
    )
    assert activation.fixture_client_actor_allowed(
        claims,
        row,
        method="GET",
        path=list_path,
    )
    assert activation.fixture_client_actor_allowed(
        claims,
        row,
        method="POST",
        path=upload_path,
    )
    assert not activation.fixture_client_actor_allowed(
        claims,
        row,
        method="GET",
        path=f"/api/portal/applications/{spec['application_id']}",
    )
    assert not activation.fixture_client_actor_allowed(
        claims,
        row,
        method="POST",
        path=list_path,
    )
    assert not activation.fixture_client_actor_allowed(
        {**claims, activation.HARNESS_CLIENT_RUN_CLAIM: "gh-99999-1"},
        row,
        method="GET",
        path=list_path,
    )
    assert not activation.fixture_client_actor_allowed(
        claims,
        {**row, "password_hash": "login-enabled"},
        method="GET",
        path=list_path,
    )
    monkeypatch.setattr(
        activation.os,
        "environ",
        _env(expires_delta=0),
    )
    assert not activation.fixture_client_actor_allowed(
        claims,
        row,
        method="GET",
        path=list_path,
    )


def test_base_handler_fixture_auth_exception_is_route_and_lease_scoped(monkeypatch):
    import base_handler

    monkeypatch.setattr(activation.os, "environ", _env())
    monkeypatch.setattr(activation, "_utc_now", lambda value=None: NOW)
    spec = fixture_spec()
    claims = {
        "sub": spec["customer_id"],
        "type": "client",
        "role": "client",
        "name": "Synthetic client",
        activation.HARNESS_CLIENT_PURPOSE_CLAIM: activation.HARNESS_CLIENT_PURPOSE,
        activation.HARNESS_CLIENT_RUN_CLAIM: "gh-12345-1",
    }
    client_row = {
        "id": spec["customer_id"],
        "email": spec["customer_email"],
        "company_name": "Document Renewal Fixture",
        "status": "inactive",
        "password_hash": "fixture-no-login",
    }

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    queries = []

    class Db:
        def __init__(self, row):
            self.row = row

        def execute(self, sql, *_args, **_kwargs):
            queries.append(sql)
            result = dict(self.row)
            if "password_hash" not in sql:
                result.pop("password_hash", None)
            return Cursor(result)

        def close(self):
            return None

    current_row = dict(client_row)
    monkeypatch.setattr(base_handler, "get_db", lambda: Db(current_row))
    handler = object.__new__(base_handler.BaseHandler)
    handler._log_inactive_token_denial = lambda *_args, **_kwargs: None
    handler._log_governed_fixture_client_grant = lambda *_args, **_kwargs: True
    list_path = (
        f"/api/portal/applications/{spec['application_id']}/renewal-requests"
    )
    upload_path = f"{list_path}/{spec['request_id']}/upload"

    for method, path in (("GET", list_path), ("POST", upload_path)):
        handler.request = SimpleNamespace(method=method, path=path)
        authenticated = handler._validate_current_actor(dict(claims))
        assert authenticated is not None
        assert authenticated["sub"] == spec["customer_id"]

    for method, path in (
        ("GET", f"/api/portal/applications/{spec['application_id']}"),
        ("POST", list_path),
    ):
        queries.clear()
        handler.request = SimpleNamespace(method=method, path=path)
        assert handler._validate_current_actor(dict(claims)) is None
        assert not any("password_hash" in query for query in queries)

    handler.request = SimpleNamespace(method="GET", path=list_path)
    queries.clear()
    assert handler._validate_current_actor(
        {**claims, activation.HARNESS_CLIENT_PURPOSE_CLAIM: "wrong"}
    ) is None
    assert not any("password_hash" in query for query in queries)
    assert handler._validate_current_actor(
        {**claims, activation.HARNESS_CLIENT_RUN_CLAIM: "gh-99999-1"}
    ) is None
    current_row["password_hash"] = "login-enabled"
    assert handler._validate_current_actor(dict(claims)) is None
    current_row.update(client_row)
    monkeypatch.setattr(activation.os, "environ", _env(expires_delta=0))
    assert handler._validate_current_actor(dict(claims)) is None

    # The protected ordinary path and every active-client request avoid the
    # credential column entirely.
    queries.clear()
    monkeypatch.setattr(activation.os, "environ", {})
    current_row.update(client_row)
    current_row["status"] = "active"
    assert handler._validate_current_actor(dict(claims)) is not None
    assert not any("password_hash" in query for query in queries)
    queries.clear()
    current_row["status"] = "inactive"
    assert handler._validate_current_actor(dict(claims)) is None
    assert not any("password_hash" in query for query in queries)


def test_fixture_client_auth_fails_closed_when_grant_audit_fails(monkeypatch):
    import base_handler

    monkeypatch.setattr(activation.os, "environ", _env())
    monkeypatch.setattr(activation, "_utc_now", lambda value=None: NOW)
    spec = fixture_spec()
    claims = {
        "sub": spec["customer_id"],
        "type": "client",
        "role": "client",
        activation.HARNESS_CLIENT_PURPOSE_CLAIM: activation.HARNESS_CLIENT_PURPOSE,
        activation.HARNESS_CLIENT_RUN_CLAIM: "gh-12345-1",
    }
    row = {
        "id": spec["customer_id"],
        "email": spec["customer_email"],
        "company_name": "Document Renewal Fixture",
        "status": "inactive",
        "password_hash": "fixture-no-login",
    }

    class Cursor:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return self.value

    class Db:
        def execute(self, sql, *_args):
            value = dict(row)
            if "password_hash" not in sql:
                value.pop("password_hash", None)
            return Cursor(value)

        def close(self):
            return None

    monkeypatch.setattr(base_handler, "get_db", Db)
    handler = object.__new__(base_handler.BaseHandler)
    handler.request = SimpleNamespace(
        method="GET",
        path=(
            f"/api/portal/applications/{spec['application_id']}"
            "/renewal-requests"
        ),
    )
    handler._log_inactive_token_denial = lambda *_args, **_kwargs: None
    handler._log_governed_fixture_client_grant = lambda *_args, **_kwargs: False

    assert handler._validate_current_actor(claims) is None


def test_fixture_client_grant_audit_is_hash_chained_and_secret_free(monkeypatch):
    import base_handler
    import db as db_module

    captured = {}

    class AuditDb:
        def close(self):
            captured["closed"] = True

    def append_audit_log(_db, **kwargs):
        captured.update(kwargs)
        return "a" * 64

    monkeypatch.setattr(base_handler, "get_db", AuditDb)
    monkeypatch.setattr(db_module, "append_audit_log", append_audit_log)
    handler = object.__new__(base_handler.BaseHandler)
    handler.request = SimpleNamespace(
        method="GET",
        path="/api/portal/applications/f1xedrnwapp00001/renewal-requests",
    )
    handler.get_client_ip = lambda: "127.0.0.1"
    granted = handler._log_governed_fixture_client_grant(
        {
            "sub": "f1xedrnwcli00001",
            "name": "Synthetic client",
            "raw_token": "never-record-this-token",
        },
        {
            "fixture_key": "document-renewal-expired-v1",
            "application_id": "f1xedrnwapp00001",
            "run_id": "gh-12345-1",
            "password_hash": "never-record-this-hash",
        },
    )

    assert granted is True
    assert captured["action"] == "monitoring.document_renewal.fixture_client_auth"
    assert captured["commit"] is True
    assert captured["closed"] is True
    evidence = json.dumps(captured, sort_keys=True)
    assert "never-record-this-token" not in evidence
    assert "never-record-this-hash" not in evidence


def test_ordinary_renewal_flag_path_never_imports_staging_harness(monkeypatch):
    import builtins
    import monitoring_document_renewal as renewal

    class Flags:
        def __init__(self, enabled):
            self.enabled = enabled

        def is_enabled(self, _name):
            return self.enabled

    monkeypatch.delenv(activation.HARNESS_MODE_ENV, raising=False)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "document_renewal_staging_activation":
            raise ImportError("staging harness deliberately unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert renewal.renewal_feature_enabled(Flags(True)) is True
    assert renewal.renewal_feature_enabled(Flags(False)) is False
    monkeypatch.setattr(renewal, "renewal_feature_enabled", lambda *_args: True)
    assert renewal.renewal_feature_enabled_for_alert(None, "ordinary-alert") is True
    assert renewal.renewal_feature_enabled_for_application(
        None, "ordinary-application"
    ) is True
    assert renewal.renewal_feature_enabled_for_request(None, "ordinary-request") is True


@pytest.mark.parametrize(
    "mutation",
    (
        "duplicate_application_ref",
        "marker_hash_drift",
        "client_enabled",
        "client_login_enabled",
    ),
)
def test_fixture_application_scope_rejects_collision_or_identity_drift(
    monkeypatch,
    mutation,
):
    monkeypatch.setattr(activation.os, "environ", _env())
    monkeypatch.setattr(activation, "_utc_now", lambda value=None: NOW)
    db = _scoped_db()
    spec = fixture_spec()
    try:
        if mutation == "duplicate_application_ref":
            db.execute(
                "INSERT INTO applications VALUES (?, ?, ?, 'approved', 1, ?)",
                (
                    "f1xed-collision",
                    spec["application_ref"],
                    spec["customer_id"],
                    db.execute(
                        "SELECT prescreening_data FROM applications LIMIT 1"
                    ).fetchone()[0],
                ),
            )
        elif mutation == "marker_hash_drift":
            marker = json.loads(
                db.execute(
                    "SELECT prescreening_data FROM applications LIMIT 1"
                ).fetchone()[0]
            )
            marker["manifest_sha256"] = "b" * 64
            db.execute(
                "UPDATE applications SET prescreening_data = ?",
                (json.dumps(marker, sort_keys=True),),
            )
        elif mutation == "client_enabled":
            db.execute("UPDATE clients SET status = 'active'")
        else:
            db.execute("UPDATE clients SET password_hash = 'login-enabled'")
        assert not activation.fixture_application_matches(
            db,
            spec["application_id"],
            customer_id=spec["customer_id"],
        )
    finally:
        db.close()
