import logging
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
import requests


@dataclass(frozen=True)
class FakeConfig:
    api_base_url: str = "https://api.example.test"
    auth_url: str = "https://auth.example.test/v2/token"
    realm: str = "regmind"
    username: str = "officer@example.test"
    password: str = "secret-password"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._json_error = json_error
        self.headers = {}

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


class FakeTokenClient:
    def __init__(self):
        self.get_token = MagicMock(return_value="access-token-1")
        self.force_refresh = MagicMock(return_value="access-token-2")
        self.clear_cache = MagicMock()


def make_client(responses, token_client=None):
    from screening_complyadvantage.client import ComplyAdvantageClient

    client = ComplyAdvantageClient(FakeConfig(), token_client=token_client or FakeTokenClient())
    client.session = MagicMock()
    client.session.request.side_effect = responses
    return client


def test_success_returns_raw_json_and_sends_authorization_header():
    payload = {"values": [{"id": "1"}], "pagination": {"total": 1}}
    token_client = FakeTokenClient()
    client = make_client([FakeResponse(payload=payload)], token_client)

    assert client.get("/searches", params={"q": "acme"}) == payload
    _, kwargs = client.session.request.call_args
    assert kwargs["headers"] == {"Authorization": "Bearer access-token-1"}
    assert kwargs["params"] == {"q": "acme"}
    assert token_client.force_refresh.call_count == 0


def test_post_sends_json_body_and_default_timeout():
    client = make_client([FakeResponse(payload={"ok": True})])

    assert client.post("searches", json_body={"name": "Acme"}) == {"ok": True}
    args, kwargs = client.session.request.call_args
    assert args[:2] == ("POST", "https://api.example.test/searches")
    assert kwargs["json"] == {"name": "Acme"}
    assert kwargs["timeout"] == (3.0, 15.0)


def test_single_401_refresh_and_retry_succeeds():
    token_client = FakeTokenClient()
    client = make_client(
        [FakeResponse(status_code=401), FakeResponse(status_code=200, payload={"ok": True})],
        token_client,
    )

    assert client.get("/cases") == {"ok": True}
    assert token_client.clear_cache.call_count == 1
    assert token_client.force_refresh.call_count == 1
    assert client.session.request.call_count == 2
    assert client.session.request.call_args_list[1].kwargs["headers"] == {
        "Authorization": "Bearer access-token-2"
    }


def test_second_401_raises_without_further_retry():
    from screening_complyadvantage.exceptions import CAAuthenticationFailed

    token_client = FakeTokenClient()
    client = make_client([FakeResponse(status_code=401), FakeResponse(status_code=401)], token_client)

    with pytest.raises(CAAuthenticationFailed):
        client.get("/cases")

    assert token_client.clear_cache.call_count == 1
    assert token_client.force_refresh.call_count == 1
    assert client.session.request.call_count == 2


@pytest.mark.parametrize(
    "response,expected",
    [
        (FakeResponse(status_code=400), "CABadRequest"),
        (FakeResponse(status_code=422), "CABadRequest"),
        (FakeResponse(status_code=429), "CARateLimited"),
        (FakeResponse(status_code=500), "CAServerError"),
        (FakeResponse(status_code=302), "CAUnexpectedResponse"),
        (FakeResponse(status_code=200, json_error=ValueError("bad json")), "CAUnexpectedResponse"),
    ],
)
def test_exception_mapping(response, expected):
    import screening_complyadvantage.exceptions as exc

    client = make_client([response])

    with pytest.raises(getattr(exc, expected)):
        client.get("/cases")


def test_timeout_and_network_mapping():
    from screening_complyadvantage.exceptions import CATimeout, CAUnexpectedResponse

    timeout_client = make_client([requests.exceptions.Timeout("slow")])
    with pytest.raises(CATimeout):
        timeout_client.get("/cases")

    network_client = make_client([requests.exceptions.ConnectionError("dns")])
    with pytest.raises(CAUnexpectedResponse):
        network_client.get("/cases")


def test_logs_do_not_leak_credentials_and_include_username_fingerprint(caplog):
    from screening_complyadvantage.auth import _username_fingerprint

    client = make_client([FakeResponse(payload={"ok": True})])

    with caplog.at_level(logging.INFO, logger="screening_complyadvantage.client"):
        assert client.get("/cases?query=secret") == {"ok": True}

    rendered = caplog.text
    assert "secret-password" not in rendered
    assert "access-token-1" not in rendered
    assert "officer@example.test" not in rendered
    assert "Authorization" not in rendered
    assert _username_fingerprint("officer@example.test") in rendered
    assert "path=/cases" in rendered
