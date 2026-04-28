from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
import requests


@dataclass(frozen=True)
class FakeConfig:
    api_base_url: str = "https://api.example.test"
    auth_url: str = "https://auth.example.test/v2/token"
    realm: str = "regmind"
    username: str = "officer@example.test"
    password: str = "secret-password"


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._json_error = json_error
        self.headers = headers or {}

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


def token_payload(token="token-1", expires_in=3600):
    return {"access_token": token, "expires_in": expires_in, "token_type": "Bearer", "scope": "read"}


def make_client(responses, clock=None):
    from screening_complyadvantage.auth import ComplyAdvantageTokenClient

    session = MagicMock()
    session.post.side_effect = responses
    return ComplyAdvantageTokenClient(FakeConfig(), session=session, clock=clock or FakeClock()), session


def test_get_token_fetches_then_uses_fresh_cache():
    clock = FakeClock()
    client, session = make_client([FakeResponse(payload=token_payload("cached"))], clock)

    assert client.get_token() == "cached"
    clock.advance(100)
    assert client.get_token() == "cached"
    assert session.post.call_count == 1


def test_get_token_refreshes_inside_60_second_buffer_and_when_expired():
    clock = FakeClock()
    client, session = make_client(
        [
            FakeResponse(payload=token_payload("first", expires_in=120)),
            FakeResponse(payload=token_payload("second", expires_in=120)),
            FakeResponse(payload=token_payload("third", expires_in=120)),
        ],
        clock,
    )

    assert client.get_token() == "first"
    clock.advance(61)
    assert client.get_token() == "second"
    clock.advance(121)
    assert client.get_token() == "third"
    assert session.post.call_count == 3


def test_clear_cache_and_force_refresh_fetch_new_token():
    client, session = make_client(
        [
            FakeResponse(payload=token_payload("first")),
            FakeResponse(payload=token_payload("second")),
            FakeResponse(payload=token_payload("third")),
        ]
    )

    assert client.get_token() == "first"
    client.clear_cache()
    assert client.get_token() == "second"
    assert client.force_refresh() == "third"
    assert session.post.call_count == 3


def test_concurrent_stale_cache_only_performs_one_http_call():
    clock = FakeClock()
    client, session = make_client([FakeResponse(payload=token_payload("shared"))], clock)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: client.get_token(), range(8)))

    assert results == ["shared"] * 8
    assert session.post.call_count == 1


def test_auth_retries_5xx_network_and_timeout_then_succeeds():
    clock = FakeClock()
    client, session = make_client(
        [
            FakeResponse(status_code=500, payload={"error": "server"}),
            requests.exceptions.ConnectionError("dns"),
            requests.exceptions.Timeout("slow"),
            FakeResponse(payload=token_payload("ok")),
        ],
        clock,
    )

    with patch("screening_complyadvantage.auth.time.sleep") as sleep, patch(
        "screening_complyadvantage.auth.random.uniform", return_value=0
    ):
        assert client.get_token() == "ok"

    assert session.post.call_count == 4
    assert sleep.call_count == 3


def test_auth_max_attempts_cap_on_5xx():
    from screening_complyadvantage.exceptions import CAServerError

    client, session = make_client([FakeResponse(status_code=500)] * 4)

    with patch("screening_complyadvantage.auth.time.sleep"), patch(
        "screening_complyadvantage.auth.random.uniform", return_value=0
    ), pytest.raises(CAServerError):
        client.get_token()

    assert session.post.call_count == 4


@pytest.mark.parametrize(
    "response,expected",
    [
        (FakeResponse(status_code=401), "CAAuthenticationFailed"),
        (FakeResponse(status_code=400), "CABadRequest"),
        (FakeResponse(status_code=403), "CABadRequest"),
        (FakeResponse(status_code=429), "CARateLimited"),
    ],
)
def test_auth_does_not_retry_4xx(response, expected):
    import screening_complyadvantage.exceptions as exc

    client, session = make_client([response])

    with pytest.raises(getattr(exc, expected)):
        client.get_token()

    assert session.post.call_count == 1


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(payload={"expires_in": 3600}),
        FakeResponse(payload={"access_token": "token"}),
        FakeResponse(payload={"access_token": "token", "expires_in": 0}),
        FakeResponse(payload=token_payload(), json_error=ValueError("bad json")),
        FakeResponse(status_code=302),
    ],
)
def test_unexpected_auth_response_shapes(response):
    from screening_complyadvantage.exceptions import CAUnexpectedResponse

    client, _ = make_client([response])

    with pytest.raises(CAUnexpectedResponse):
        client.get_token()
