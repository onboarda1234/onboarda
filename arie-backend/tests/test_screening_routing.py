import pytest

import screening_provider
from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME, register_provider
from screening_routing import run_screening_for_active_provider


def test_sumsub_route_preserves_legacy_runner(monkeypatch):
    monkeypatch.setattr("screening_routing.get_active_provider_name", lambda: "sumsub")
    db = object()
    sentinel = {"legacy_shape": True}
    calls = []

    def legacy_runner(application_data, directors, ubos, client_ip=None):
        calls.append((application_data, directors, ubos, client_ip))
        return sentinel

    result = run_screening_for_active_provider(
        {"application_id": "app-1"},
        [{"full_name": "Director"}],
        [],
        client_ip="203.0.113.10",
        db=db,
        legacy_runner=legacy_runner,
    )

    assert result is sentinel
    assert calls == [({"application_id": "app-1"}, [{"full_name": "Director"}], [], "203.0.113.10")]


def test_complyadvantage_route_uses_registered_provider_with_db(monkeypatch):
    monkeypatch.setattr(screening_provider, "_factory_registry", {}, raising=False)
    monkeypatch.setattr(
        "screening_routing.get_active_provider_name",
        lambda: COMPLYADVANTAGE_PROVIDER_NAME,
    )
    db = object()

    class FakeCAProvider:
        instances = []

        def __init__(self, db=None):
            self.db = db
            self.calls = []
            self.instances.append(self)

        def run_full_screening(self, application_data, directors, ubos, client_ip=None):
            self.calls.append((application_data, directors, ubos, client_ip))
            return {"provider": "complyadvantage", "normalized_version": "2.0"}

    register_provider(COMPLYADVANTAGE_PROVIDER_NAME, FakeCAProvider)

    result = run_screening_for_active_provider(
        {"application_id": "app-2"},
        [],
        [{"full_name": "Owner"}],
        client_ip="203.0.113.11",
        db=db,
    )

    assert result["provider"] == "complyadvantage"
    assert FakeCAProvider.instances[0].db is db
    assert FakeCAProvider.instances[0].calls == [
        ({"application_id": "app-2"}, [], [{"full_name": "Owner"}], "203.0.113.11")
    ]


def test_unknown_active_provider_fails_closed(monkeypatch):
    monkeypatch.setattr(screening_provider, "_factory_registry", {}, raising=False)
    monkeypatch.setattr("screening_routing.get_active_provider_name", lambda: "missing-provider")

    with pytest.raises(RuntimeError):
        run_screening_for_active_provider({}, [], [], legacy_runner=lambda *args, **kwargs: {})
