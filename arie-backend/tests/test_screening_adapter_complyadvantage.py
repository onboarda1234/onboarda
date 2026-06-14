from unittest.mock import MagicMock

from screening_models import validate_normalized_report
from screening_provider import ScreeningProvider
from screening_complyadvantage.adapter import ComplyAdvantageScreeningAdapter


class FakeConfig:
    screening_configuration_identifier = "cfg-123"


def _report(kind="director", name="Jane Doe"):
    if kind == "entity":
        return {
            "provider": "complyadvantage",
            "normalized_version": "2.0",
            "screened_at": "2026-01-01T00:00:00Z",
            "any_pep_hits": False,
            "any_sanctions_hits": False,
            "total_persons_screened": 0,
            "adverse_media_coverage": "none",
            "has_adverse_media_hit": None,
            "company_screening_coverage": "full",
            "has_company_screening_hit": False,
            "company_screening": {"provider": "complyadvantage", "source": "complyadvantage", "api_status": "live", "matched": False, "results": []},
            "director_screenings": [],
            "ubo_screenings": [],
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
            "any_non_terminal_subject": False,
            "company_screening_state": "completed_clear",
            "provenance": None,
        }
    person = {
        "person_name": name,
        "person_type": kind,
        "nationality": "",
        "declared_pep": "No",
        "provider_detected_pep": True,
        "undeclared_pep": True,
        "has_pep_hit": True,
        "has_sanctions_hit": False,
        "has_adverse_media_hit": None,
        "adverse_media_coverage": "none",
        "screening": {
            "provider": "complyadvantage",
            "source": "complyadvantage",
            "api_status": "live",
            "matched": True,
            "results": [{"name": name, "is_pep": True}],
        },
        "screening_state": "completed_match",
        "requires_review": True,
        "is_rca": False,
        "pep_classes": ["PEP_CLASS_1"],
    }
    return {
        "provider": "complyadvantage",
        "normalized_version": "2.0",
        "screened_at": "2026-01-01T00:00:00Z",
        "any_pep_hits": True,
        "any_sanctions_hits": False,
        "total_persons_screened": 1,
        "adverse_media_coverage": "none",
        "has_adverse_media_hit": None,
        "company_screening_coverage": "none",
        "has_company_screening_hit": None,
        "company_screening": {},
        "director_screenings": [person] if kind == "director" else [],
        "ubo_screenings": [person] if kind == "ubo" else [],
        "intermediary_screenings": [person] if kind == "intermediary" else [],
        "overall_flags": [],
        "total_hits": 1,
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
        context = kwargs["application_context"]
        return _report(context.screening_subject_kind, context.screening_subject_name)


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
        "COMPLYADVANTAGE_SCREENING_CONFIG_ID": "cfg-123",
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
    adapter = ComplyAdvantageScreeningAdapter(orchestrator=orchestrator, config=FakeConfig(), db=db)

    result = adapter.screen_person("Jane Doe", birth_date="1980-01-31", nationality="MU")

    assert result["provider"] == "complyadvantage"
    assert "subscription_seeded" not in result
    assert validate_normalized_report(result) == []
    call = orchestrator.calls[0]
    assert call["db"] is db
    assert call["application_context"].screening_subject_name == "Jane Doe"
    assert call["strict_customer"]["person"]["nationality"] == ["MU"]
    assert "nationality" not in call["relaxed_customer"]["person"]
    assert call["screening_configuration_identifier"] == "cfg-123"


def test_screen_company_delegates_to_entity_context():
    orchestrator = FakeOrchestrator()
    adapter = ComplyAdvantageScreeningAdapter(orchestrator=orchestrator, config=FakeConfig())

    adapter.screen_company("Acme Ltd", jurisdiction="MU")

    call = orchestrator.calls[0]
    assert call["application_context"].screening_subject_kind == "entity"
    strict_company = call["strict_customer"]["company"]
    assert strict_company["legal_name"] == "Acme Ltd"
    assert strict_company["jurisdiction"] == "MU"
    assert strict_company["custom_fields"] == {"source_system": "regmind"}
    assert call["screening_configuration_identifier"] == "cfg-123"


def test_run_full_screening_propagates_db_for_each_subject():
    orchestrator = FakeOrchestrator()
    db = object()
    adapter = ComplyAdvantageScreeningAdapter(orchestrator=orchestrator, config=FakeConfig(), db=db)

    result = adapter.run_full_screening(
        {"application_id": "app-1", "client_id": "client-1", "company_name": "Acme Ltd"},
        [{"person_key": "d-1", "full_name": "Jane Doe"}],
        [],
    )

    assert len(orchestrator.calls) == 2
    assert all(call["db"] is db for call in orchestrator.calls)
    assert all(call["screening_configuration_identifier"] == "cfg-123" for call in orchestrator.calls)
    assert result["provider"] == "complyadvantage"
    assert "subscription_seeded" not in result


def test_run_full_screening_uses_distinct_subject_and_pass_external_identifiers():
    orchestrator = FakeOrchestrator()
    adapter = ComplyAdvantageScreeningAdapter(orchestrator=orchestrator, config=FakeConfig())

    adapter.run_full_screening(
        {"application_id": "app-1", "client_id": "client-1", "company_name": "Acme Ltd"},
        [{"person_key": "d-1", "full_name": "Jane Doe"}],
        [{"person_key": "u-1", "full_name": "John Roe"}],
    )

    company_call, director_call, ubo_call = orchestrator.calls
    assert company_call["strict_external_identifier"].endswith(":strict")
    assert company_call["relaxed_external_identifier"].endswith(":relaxed")
    assert ":company:" in company_call["strict_external_identifier"]
    assert ":director:" in director_call["strict_external_identifier"]
    assert ":ubo:" in ubo_call["strict_external_identifier"]
    assert director_call["strict_external_identifier"] != director_call["relaxed_external_identifier"]
    assert {
        company_call["strict_external_identifier"],
        director_call["strict_external_identifier"],
        ubo_call["strict_external_identifier"],
    } == {
        "app-1:company:name-0f72986d73ffd22009837ba841519e04:strict",
        "app-1:director:key-d-1:strict",
        "app-1:ubo:key-u-1:strict",
    }


def test_run_full_screening_dedupes_same_natural_person_across_roles():
    orchestrator = FakeOrchestrator()
    adapter = ComplyAdvantageScreeningAdapter(orchestrator=orchestrator, config=FakeConfig())

    result = adapter.run_full_screening(
        {"application_id": "app-1", "client_id": "client-1", "company_name": "Acme Ltd"},
        [{"person_key": "director-row", "full_name": "Same Person", "date_of_birth": "1980-01-01", "nationality": "MU", "is_pep": "No"}],
        [{"person_key": "ubo-row", "full_name": "Same Person", "date_of_birth": "1980-01-01", "nationality": "MU", "is_pep": "No"}],
    )

    assert len(orchestrator.calls) == 2  # company + one shared natural-person screen
    assert [call["application_context"].screening_subject_kind for call in orchestrator.calls] == ["entity", "director"]
    assert len(result["director_screenings"]) == 1
    assert len(result["ubo_screenings"]) == 1
    assert result["director_screenings"][0]["person_name"] == "Same Person"
    assert result["ubo_screenings"][0]["person_name"] == "Same Person"
    assert result["director_screenings"][0]["screening"]["shared_subject_key"] == result["ubo_screenings"][0]["screening"]["shared_subject_key"]
    assert result["total_hits"] == 1


def test_run_full_screening_includes_intermediaries_with_entity_payload():
    orchestrator = FakeOrchestrator()
    adapter = ComplyAdvantageScreeningAdapter(orchestrator=orchestrator, config=FakeConfig())

    result = adapter.run_full_screening(
        {
            "application_id": "app-1",
            "client_id": "client-1",
            "company_name": "Acme Ltd",
            "country": "Mauritius",
        },
        [],
        [],
        [{"person_key": "i-1", "entity_name": "HoldCo Ltd", "jurisdiction": "Mauritius", "registration_number": "H123"}],
    )

    assert len(orchestrator.calls) == 2
    intermediary_call = orchestrator.calls[1]
    assert intermediary_call["application_context"].screening_subject_kind == "intermediary"
    assert intermediary_call["application_context"].screening_subject_name == "HoldCo Ltd"
    assert ":intermediary:key-i-1:strict" in intermediary_call["strict_external_identifier"]
    assert intermediary_call["strict_customer"]["company"]["legal_name"] == "HoldCo Ltd"
    assert intermediary_call["strict_customer"]["company"]["registration_number"] == "H123"
    assert intermediary_call["strict_customer"]["company"]["jurisdiction"] == "MU"
    assert result["intermediary_screenings"][0]["person_type"] == "intermediary"
    assert result["intermediary_screenings"][0]["person_name"] == "HoldCo Ltd"
    assert result["intermediary_screenings"][0]["requires_review"] is True
    assert result["any_pep_hits"] is True
    assert result["total_intermediaries_screened"] == 1


def test_run_full_screening_records_missing_intermediary_subject_gap():
    adapter = ComplyAdvantageScreeningAdapter(orchestrator=FakeOrchestrator(), config=FakeConfig())

    result = adapter.run_full_screening(
        {"application_id": "app-1", "client_id": "client-1", "company_name": "Acme Ltd"},
        [],
        [],
        [{"person_key": "i-blank", "jurisdiction": "Mauritius"}],
    )

    gap = result["intermediary_screenings"][0]
    assert gap["person_type"] == "intermediary"
    assert gap["screening_state"] == "failed"
    assert gap["requires_review"] is True
    assert gap["screening"]["api_status"] == "failed"
    assert gap["screening"]["evidence_gap"] is True
    assert "intermediary_missing_required_subject_data" in result["degraded_sources"]
    assert result["any_non_terminal_subject"] is True
