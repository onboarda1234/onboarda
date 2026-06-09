import base64
import hashlib
import hmac
import json
import logging
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from tornado.httputil import HTTPHeaders, HTTPServerRequest
from tornado.web import Application

from screening_complyadvantage.models.webhooks import CACaseCreatedWebhook
from screening_complyadvantage.webhook_handler import ComplyAdvantageWebhookHandler, _verify_signature

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "complyadvantage")
STANDARD_SECRET = base64.b64encode(b"1" * 32).decode("ascii")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _legacy_signed(body, secret="fixture-secret"):
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _standard_signed(body, secret=STANDARD_SECRET, webhook_id="msg-fixture", webhook_timestamp=None):
    webhook_timestamp = webhook_timestamp or str(int(time.time()))
    signed_content = b".".join([webhook_id.encode("utf-8"), webhook_timestamp.encode("utf-8"), body])
    return base64.b64encode(hmac.new(base64.b64decode(secret), signed_content, hashlib.sha256).digest()).decode("ascii")


def _call_handler(
    payload,
    *,
    secret=STANDARD_SECRET,
    signature=None,
    include_signature=True,
    signature_scheme="standard",
    webhook_id="msg-fixture",
    webhook_timestamp=None,
    environment="development",
    storage_callback=None,
    request_id=None,
):
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    return _call_handler_body(
        body,
        secret=secret,
        signature=signature,
        include_signature=include_signature,
        signature_scheme=signature_scheme,
        webhook_id=webhook_id,
        webhook_timestamp=webhook_timestamp,
        environment=environment,
        storage_callback=storage_callback,
        request_id=request_id,
    )


def _call_handler_body(
    body,
    *,
    secret=STANDARD_SECRET,
    signature=None,
    include_signature=True,
    signature_scheme="standard",
    webhook_id="msg-fixture",
    webhook_timestamp=None,
    environment="development",
    storage_callback=None,
    request_id=None,
):
    headers = {}
    webhook_timestamp = webhook_timestamp or str(int(time.time()))
    if include_signature:
        if signature_scheme == "standard":
            headers["webhook-id"] = webhook_id
            headers["webhook-timestamp"] = webhook_timestamp
            headers["webhook-signature"] = (
                signature if signature is not None else f"v1,{_standard_signed(body, secret or 'fixture-secret', webhook_id, webhook_timestamp)}"
            )
        elif signature_scheme == "legacy":
            headers["x-complyadvantage-signature"] = (
                signature if signature is not None else _legacy_signed(body, secret or "fixture-secret")
            )
        elif signature_scheme == "both":
            headers["webhook-id"] = webhook_id
            headers["webhook-timestamp"] = webhook_timestamp
            headers["webhook-signature"] = (
                signature if signature is not None else f"v1,{_standard_signed(body, secret or 'fixture-secret', webhook_id, webhook_timestamp)}"
            )
            headers["x-complyadvantage-signature"] = _legacy_signed(body, secret or "fixture-secret")
        else:
            raise AssertionError(f"unknown signature scheme {signature_scheme}")
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    app = Application()
    mock_conn = MagicMock()
    mock_conn.context = MagicMock()
    mock_conn.context.remote_ip = "127.0.0.1"
    req = HTTPServerRequest(
        method="POST",
        uri="/api/webhooks/complyadvantage",
        version="HTTP/1.1",
        headers=HTTPHeaders(headers),
        body=body,
        host="127.0.0.1",
        connection=mock_conn,
    )
    handler = ComplyAdvantageWebhookHandler(app, req, storage_callback=storage_callback)
    env = {"ENVIRONMENT": environment}
    if secret is not None:
        env["COMPLYADVANTAGE_WEBHOOK_SECRET"] = secret
    with patch.dict(os.environ, env, clear=False):
        if secret is None:
            os.environ.pop("COMPLYADVANTAGE_WEBHOOK_SECRET", None)
        handler.post()
    return handler


def test_verify_signature_success_and_failure(monkeypatch):
    body = b'{"webhook_type":"CASE_CREATED"}'
    timestamp = str(int(time.time()))
    monkeypatch.setenv("COMPLYADVANTAGE_WEBHOOK_SECRET", STANDARD_SECRET)
    valid_headers = HTTPHeaders({
        "webhook-id": "msg-fixture",
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{_standard_signed(body, webhook_timestamp=timestamp)}",
    })
    invalid_headers = HTTPHeaders({
        "webhook-id": "msg-fixture",
        "webhook-timestamp": timestamp,
        "webhook-signature": "v1,bad",
    })
    assert _verify_signature(body, valid_headers) is True
    assert _verify_signature(body, invalid_headers) is False
    monkeypatch.delenv("COMPLYADVANTAGE_WEBHOOK_SECRET", raising=False)
    assert _verify_signature(body, valid_headers) is False


def test_known_case_created_returns_202_and_spawns_callback():
    storage = MagicMock()
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_created.json"), storage_callback=storage)

    assert handler._status_code == 202
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_called_once()
    callback, envelope = fake_loop.spawn_callback.call_args.args
    assert callback == handler._process_webhook_async
    assert envelope.webhook_type == "CASE_CREATED"
    storage.assert_not_called()


def test_case_alert_list_updated_parses_and_spawns():
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_alert_list_updated.json"))

    assert handler._status_code == 202
    envelope = fake_loop.spawn_callback.call_args.args[1]
    assert envelope.webhook_type == "CASE_ALERT_LIST_UPDATED"
    assert envelope.alert_identifiers == ["alert-san"]


def test_known_webhook_propagates_bounded_trace_id_to_spawned_callback():
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        _call_handler(_fixture("webhook_case_created.json"), request_id="req-ca-123")

    assert fake_loop.spawn_callback.call_args.kwargs["trace_id"] == "req-ca-123"


def test_unknown_event_returns_202_without_spawn(caplog):
    fake_loop = MagicMock()
    with caplog.at_level(logging.INFO, logger="screening_complyadvantage.webhook_handler"):
        with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
            handler = _call_handler(_fixture("webhook_unknown_type.json"))

    assert handler._status_code == 202
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()
    assert "ca_webhook_unknown_event" in caplog.text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.pop("case_identifier"),
        lambda payload: payload.update({"case_identifier": "short"}),
        lambda payload: payload.update({"case_identifier": 123}),
    ],
)
def test_known_event_invalid_case_identifier_returns_400_without_spawn(mutate):
    payload = _fixture("webhook_case_created.json")
    mutate(payload)
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(payload)

    assert handler._status_code == 400
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [None, "alert-one", ["alert-one", 123]],
)
def test_case_alert_list_updated_invalid_alert_identifiers_returns_400_without_spawn(value):
    payload = _fixture("webhook_case_alert_list_updated.json")
    if value is None:
        payload.pop("alert_identifiers")
    else:
        payload["alert_identifiers"] = value
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(payload)

    assert handler._status_code == 400
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()


def test_case_alert_list_updated_empty_alert_identifiers_is_noop_without_spawn_or_storage(caplog):
    payload = _fixture("webhook_case_alert_list_updated.json")
    payload["alert_identifiers"] = []
    storage = MagicMock()
    fake_loop = MagicMock()
    with caplog.at_level(logging.INFO, logger="screening_complyadvantage.webhook_handler"):
        with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
            handler = _call_handler(payload, storage_callback=storage)

    assert handler._status_code == 202
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()
    storage.assert_not_called()
    assert "ca_webhook_empty_alert_identifiers" in caplog.text
    assert "no_op=true" in caplog.text


def test_valid_standard_webhooks_signature_returns_202_and_spawns_callback():
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_created.json"))

    assert handler._status_code == 202
    fake_loop.spawn_callback.assert_called_once()


def test_malformed_standard_webhooks_signature_returns_401_without_spawn():
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_created.json"), signature="not-versioned")

    assert handler._status_code == 401
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()


def test_invalid_standard_webhooks_signature_returns_401_without_spawn():
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_created.json"), signature="v1,bad")

    assert handler._status_code == 401
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()


def test_standard_webhooks_accepts_any_matching_rotation_signature():
    payload = _fixture("webhook_case_created.json")
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = f"v1,bad v1,{_standard_signed(body, webhook_timestamp=timestamp)}"
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(payload, signature=signature, webhook_timestamp=timestamp)

    assert handler._status_code == 202
    fake_loop.spawn_callback.assert_called_once()


def test_standard_webhooks_verifies_exact_raw_body_bytes():
    payload = _fixture("webhook_case_created.json")
    compact = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    signature = f"v1,{_standard_signed(compact)}"
    fake_loop = MagicMock()

    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler_body(pretty, signature=signature)

    assert handler._status_code == 401
    fake_loop.spawn_callback.assert_not_called()


def test_standard_webhooks_rejects_stale_timestamp():
    payload = _fixture("webhook_case_created.json")
    stale = str(int(time.time()) - 3600)
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(payload, webhook_timestamp=stale)

    assert handler._status_code == 401
    fake_loop.spawn_callback.assert_not_called()


def test_standard_webhooks_rejects_non_base64_secret(monkeypatch):
    body = b'{"webhook_type":"CASE_CREATED"}'
    timestamp = str(int(time.time()))
    monkeypatch.setenv("COMPLYADVANTAGE_WEBHOOK_SECRET", "fixture-secret")
    headers = HTTPHeaders({
        "webhook-id": "msg-fixture",
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{_standard_signed(body, webhook_timestamp=timestamp)}",
    })

    assert _verify_signature(body, headers) is False


def test_standard_webhooks_uses_constant_time_compare(monkeypatch):
    body = b'{"webhook_type":"CASE_CREATED"}'
    timestamp = str(int(time.time()))
    monkeypatch.setenv("COMPLYADVANTAGE_WEBHOOK_SECRET", STANDARD_SECRET)
    headers = HTTPHeaders({
        "webhook-id": "msg-fixture",
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{_standard_signed(body, webhook_timestamp=timestamp)}",
    })
    compare = MagicMock(side_effect=hmac.compare_digest)
    monkeypatch.setattr("screening_complyadvantage.webhook_handler.hmac.compare_digest", compare)

    assert _verify_signature(body, headers) is True
    assert compare.called


def test_legacy_signature_remains_temporarily_supported_without_standard_headers():
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_created.json"), signature_scheme="legacy")

    assert handler._status_code == 202
    fake_loop.spawn_callback.assert_called_once()


def test_standard_webhooks_takes_precedence_over_valid_legacy_signature():
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_created.json"), signature_scheme="both", signature="v1,bad")

    assert handler._status_code == 401
    fake_loop.spawn_callback.assert_not_called()


def test_bad_signature_returns_401_without_spawn_or_body(caplog):
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_created.json"), signature="bad-signature")

    assert handler._status_code == 401
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()
    assert "signature_status=invalid" in caplog.text
    assert "bad-signature" not in caplog.text
    assert "fixture-secret" not in caplog.text


def test_missing_signature_returns_401_without_spawn_or_body():
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_created.json"), include_signature=False)

    assert handler._status_code == 401
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()


def test_secret_unset_development_fails_open_with_warning(caplog):
    fake_loop = MagicMock()
    with caplog.at_level(logging.WARNING, logger="screening_complyadvantage.webhook_handler"):
        with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
            handler = _call_handler(
                _fixture("webhook_case_created.json"),
                secret=None,
                include_signature=False,
                environment="development",
            )

    assert handler._status_code == 202
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_called_once()
    assert "signature_mode=sandbox_fail_open" in caplog.text
    assert "signature_verification_disabled=true" in caplog.text


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_secret_unset_deployed_environments_fail_closed_with_503(environment, caplog):
    fake_loop = MagicMock()
    with caplog.at_level(logging.ERROR, logger="screening_complyadvantage.webhook_handler"):
        with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
            handler = _call_handler(
                _fixture("webhook_case_created.json"),
                secret=None,
                include_signature=False,
                environment=environment,
            )

    assert handler._status_code == 503
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()
    assert "signature_mode=deployed_fail_closed" in caplog.text


def test_malformed_json_returns_400_without_spawn_or_body(caplog):
    fake_loop = MagicMock()
    with caplog.at_level(logging.WARNING, logger="screening_complyadvantage.webhook_handler"):
        with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
            handler = _call_handler_body(b'{"webhook_type": "CASE_CREATED"')

    assert handler._status_code == 400
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()
    assert "ca_webhook_invalid_json" in caplog.text


@pytest.mark.asyncio
async def test_async_processing_failure_logs_and_emits_metric():
    async def failing_storage(envelope):
        raise RuntimeError("boom")

    with patch("tornado.ioloop.IOLoop.current", return_value=MagicMock()):
        handler = _call_handler(_fixture("webhook_case_created.json"), storage_callback=failing_storage)
    envelope = CACaseCreatedWebhook.model_validate(_fixture("webhook_case_created.json"))

    with patch("screening_complyadvantage.webhook_handler.emit_metric") as metric:
        await handler._process_webhook_async(envelope, trace_id="trace-safe")

    metric.assert_called_once_with(
        "webhook_async_processing_failure",
        trace_id="trace-safe",
        component="webhook_handler",
        outcome="failure",
        webhook_type="CASE_CREATED",
        case_identifier=envelope.case_identifier,
    )
