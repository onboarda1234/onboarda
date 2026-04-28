from unittest.mock import MagicMock

from screening_models import validate_normalized_report
from screening_provider import ScreeningProvider
from screening_complyadvantage.adapter import ComplyAdvantageScreeningAdapter


def _report():
    return {
        "provider": "complyadvantage",
        "normalized_version": "2.0",
        "screened_at": "",
        "any_pep_hits": False,
        "any_sanctions_hits": False,
        "total_persons_screened": 1,
        "adverse_media_coverage": "none",
        "has_adverse_media_hit": None,
        "company_screening_coverage": "none",
        "has_company_screening_hit": None,
        "company_screening": {},
        "director_screenings": [{
            "person_name": "Jane Doe",
            "person_type": "director",
            "nationality": "",
            "declared_pep": "No",
            "has_pep_hit": False,
            "has_sanctions_hit": False,
            "has_adverse_media_hit": None,
            "adverse_media_coverage": "none",
            "screening": {"provider": "complyadvantage"},
            "screening_state": "completed_clear",
            "requires_review": False,
            "is_rca": False,
            "pep_classes": None,
        }],
        "ubo_screenings": [],
        "overall_flags": [],
        "total_hits": 0,
        "degraded_sources": [],
        "any_non_terminal_subject": False,
        "company_screening_state": "completed_clear",
        "provenance": None,
    }


class FakeOrchestrator:
    def __init__(self):
        self.calls = []

    def screen_customer_two_pass(self, **kwargs):
        self.calls.append(kwargs)
        return _report()


def test_adapter_basics_and_no_args_factory_construction():
    adapter = ComplyAdvantageScreeningAdapter()

    assert isinstance(adapter, ScreeningProvider)
    assert adapter.provider_name == "complyadvantage"
    assert adapter._client is None
    assert adapter._orchestrator is None


def test_is_configured_true_and_false(monkeypatch):
    for name, value in {
        "COMPLYADVANTAGE_API_BASE_URL": "https://api.example.test",
        "COMPLYADVANTAGE_AUTH_URL": "https://auth.example.test/v2/token",
        "COMPLYADVANTAGE_REALM": "regmind",
        "COMPLYADVANTAGE_USERNAME": "user",
        "COMPLYADVANTAGE_PASSWORD": "pass",
    }.items():
        monkeypatch.setenv(name, value)
    assert ComplyAdvantageScreeningAdapter().is_configured() is True

    monkeypatch.delenv("COMPLYADVANTAGE_PASSWORD")
    assert ComplyAdvantageScreeningAdapter().is_configured() is False


def test_constructor_does_not_read_env_or_touch_http_or_db(monkeypatch):
    monkeypatch.setattr("screening_complyadvantage.config.CAConfig.from_env", MagicMock(side_effect=AssertionError("env read")))

    adapter = ComplyAdvantageScreeningAdapter(db=MagicMock())

    assert adapter._client is None
    assert adapter._orchestrator is None


def test_screen_person_delegates_and_returns_plain_contract_dict():
    orchestrator = FakeOrchestrator()
    db = object()
    adapter = ComplyAdvantageScreeningAdapter(orchestrator=orchestrator, db=db)

    result = adapter.screen_person("Jane Doe", birth_date="1980-01-31", nationality="MU")

    assert result["provider"] == "complyadvantage"
    assert "subscription_seeded" not in result
    assert validate_normalized_report(result) == []
    call = orchestrator.calls[0]
    assert call["db"] is db
    assert call["application_context"].screening_subject_name == "Jane Doe"
    assert call["strict_customer"]["person"]["nationality"] == "MU"
    assert "nationality" not in call["relaxed_customer"]["person"]


def test_screen_company_delegates_to_entity_context():
    orchestrator = FakeOrchestrator()
    adapter = ComplyAdvantageScreeningAdapter(orchestrator=orchestrator)

    adapter.screen_company("Acme Ltd", jurisdiction="MU")

    call = orchestrator.calls[0]
    assert call["application_context"].screening_subject_kind == "entity"
    assert call["strict_customer"]["company"]["jurisdiction"] == "MU"


def test_run_full_screening_propagates_db_for_each_subject():
    orchestrator = FakeOrchestrator()
    db = object()
    adapter = ComplyAdvantageScreeningAdapter(orchestrator=orchestrator, db=db)

    result = adapter.run_full_screening(
        {"application_id": "app-1", "client_id": "client-1", "company_name": "Acme Ltd"},
        [{"person_key": "d-1", "full_name": "Jane Doe"}],
        [],
    )

    assert len(orchestrator.calls) == 2
    assert all(call["db"] is db for call in orchestrator.calls)
    assert result["provider"] == "complyadvantage"
    assert "subscription_seeded" not in result
