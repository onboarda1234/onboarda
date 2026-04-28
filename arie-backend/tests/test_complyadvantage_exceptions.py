import pytest


def test_exception_hierarchy():
    from screening_complyadvantage.exceptions import (
        CAAuthenticationFailed,
        CABadRequest,
        CAConfigurationError,
        CAError,
        CARateLimited,
        CAServerError,
        CATimeout,
        CAUnexpectedResponse,
    )

    for cls in (
        CAConfigurationError,
        CAAuthenticationFailed,
        CARateLimited,
        CATimeout,
        CABadRequest,
        CAServerError,
        CAUnexpectedResponse,
    ):
        assert issubclass(cls, CAError)
        assert isinstance(cls(), CAError)


@pytest.mark.parametrize(
    "cls_name",
    [
        "CAError",
        "CAConfigurationError",
        "CAAuthenticationFailed",
        "CARateLimited",
        "CATimeout",
        "CABadRequest",
        "CAServerError",
        "CAUnexpectedResponse",
    ],
)
def test_exception_messages_and_repr_do_not_leak_credentials(cls_name):
    import screening_complyadvantage.exceptions as exc

    raw_username = "officer@example.test"
    password = "secret-password"
    token = "ey.fake.access.token"
    instance = getattr(exc, cls_name)(
        f"username={raw_username} password={password} access_token={token} Authorization=Bearer {token}",
        password=password,
        access_token=token,
        authorization=f"Bearer {token}",
        body={"username": raw_username, "password": password},
    )

    rendered = f"{str(instance)} {repr(instance)} {instance.args!r} {instance.__dict__!r}"
    assert password not in rendered
    assert token not in rendered
    assert raw_username not in rendered
    assert "Authorization=Bearer" not in rendered
