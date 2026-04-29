import hashlib
import hmac
import json
import logging
import os
from unittest.mock import MagicMock, patch

import pytest
from tornado.httputil import HTTPHeaders, HTTPServerRequest
from tornado.web import Application

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_complyadvantage.models.webhooks import CACaseCreatedWebhook
from screening_complyadvantage.webhook_handler import ComplyAdvantageWebhookHandler, _verify_signature

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "complyadvantage")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _signed(body, secret="fixture-secret"):
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _call_handler(
    payload,
    *,
    secret="fixture-secret",
    signature=None,
    include_signature=True,
    environment="development",
    storage_callback=None,
):
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    return _call_handler_body(
        body,
        secret=secret,
        signature=signature,
        include_signature=include_signature,
        environment=environment,
        storage_callback=storage_callback,
    )


def _call_handler_body(
    body,
    *,
    secret="fixture-secret",
    signature=None,
    include_signature=True,
    environment="development",
    storage_callback=None,
):
    headers = {}
    if include_signature:
        headers["x-complyadvantage-signature"] = (
            signature if signature is not None else _signed(body, secret or "fixture-secret")
        )
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
    monkeypatch.setenv("COMPLYADVANTAGE_WEBHOOK_SECRET", "fixture-secret")
    assert _verify_signature(body, _signed(body)) is True
    assert _verify_signature(body, "bad") is False
    monkeypatch.delenv("COMPLYADVANTAGE_WEBHOOK_SECRET", raising=False)
    assert _verify_signature(body, _signed(body)) is False


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


def test_unknown_event_returns_202_without_spawn(caplog):
    fake_loop = MagicMock()
    with caplog.at_level(logging.INFO, logger="screening_complyadvantage.webhook_handler"):
        with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
            handler = _call_handler(_fixture("webhook_unknown_type.json"))

    assert handler._status_code == 202
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()
    assert "ca_webhook_unknown_event" in caplog.text


def test_bad_signature_returns_401_without_spawn_or_body(caplog):
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_created.json"), signature="bad-signature")

    assert handler._status_code == 401
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()
    assert "signature_invalid" in caplog.text
    assert "bad-signature" not in caplog.text
    assert "fixture-secret" not in caplog.text


def test_missing_signature_returns_401_without_spawn_or_body():
    fake_loop = MagicMock()
    with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
        handler = _call_handler(_fixture("webhook_case_created.json"), include_signature=False)

    assert handler._status_code == 401
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()


@pytest.mark.parametrize("environment", ["staging", "development"])
def test_secret_unset_non_production_fails_open_with_warning(environment, caplog):
    fake_loop = MagicMock()
    with caplog.at_level(logging.WARNING, logger="screening_complyadvantage.webhook_handler"):
        with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
            handler = _call_handler(
                _fixture("webhook_case_created.json"),
                secret=None,
                include_signature=False,
                environment=environment,
            )

    assert handler._status_code == 202
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_called_once()
    assert "signature_mode=sandbox_fail_open" in caplog.text
    assert "signature_verification_disabled=true" in caplog.text


def test_secret_unset_production_fails_closed_with_503(caplog):
    fake_loop = MagicMock()
    with caplog.at_level(logging.ERROR, logger="screening_complyadvantage.webhook_handler"):
        with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
            handler = _call_handler(
                _fixture("webhook_case_created.json"),
                secret=None,
                include_signature=False,
                environment="production",
            )

    assert handler._status_code == 503
    assert b"".join(handler._write_buffer) == b""
    fake_loop.spawn_callback.assert_not_called()
    assert "signature_mode=production_fail_closed" in caplog.text


def test_malformed_json_returns_400_without_spawn_or_body(caplog):
    fake_loop = MagicMock()
    with caplog.at_level(logging.WARNING, logger="screening_complyadvantage.webhook_handler"):
        with patch("tornado.ioloop.IOLoop.current", return_value=fake_loop):
            handler = _call_handler_body(b'{"webhook_type": "CASE_CREATED"', secret="fixture-secret")

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

    with patch("screening_complyadvantage.webhook_storage.emit_metric") as metric:
        await handler._process_webhook_async(envelope)

    metric.assert_called_once_with("webhook_async_processing_failure", provider=COMPLYADVANTAGE_PROVIDER_NAME)
