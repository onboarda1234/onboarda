import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import RequestException, Timeout
from tornado.httputil import HTTPHeaders, HTTPServerRequest
from tornado.web import Application

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _mock_response(status_code, payload):
    response = MagicMock()
    response.status_code = status_code
    if isinstance(payload, Exception):
        response.json.side_effect = payload
    else:
        response.json.return_value = payload
    return response


def _contains(value, needle):
    return needle in json.dumps(value, sort_keys=True, default=str)


def _handler(handler_cls, uri, auth_user=None, body=b""):
    mock_conn = MagicMock()
    mock_conn.context = MagicMock()
    mock_conn.context.remote_ip = "127.0.0.1"
    request = HTTPServerRequest(
        method="GET",
        uri=uri,
        version="HTTP/1.1",
        headers=HTTPHeaders({"Host": "localhost"}),
        body=body,
        connection=mock_conn,
    )
    handler = handler_cls(Application(), request)
    handler._transforms = []
    audit_calls = []
    if auth_user is not None:
        handler.require_auth = lambda *a, **kw: auth_user
    handler.log_audit = lambda user, action, target, detail, **kw: audit_calls.append({
        "user": user,
        "action": action,
        "target": target,
        "detail": detail,
    })
    return handler, audit_calls


def _body(handler):
    raw = b"".join(handler._write_buffer).decode("utf-8")
    return json.loads(raw) if raw else {}


@pytest.fixture(autouse=True)
def _reset_companies_house_config(monkeypatch):
    import company_registry

    monkeypatch.setattr(company_registry, "COMPANIES_HOUSE_API_KEY", "test-companies-house-key")
    monkeypatch.setattr(
        company_registry,
        "COMPANIES_HOUSE_API_URL",
        "https://api.company-information.service.gov.uk",
    )
    monkeypatch.setattr(company_registry, "is_production", lambda: False)


class TestCompaniesHouseProvider:
    @patch("company_registry.requests.get")
    def test_search_success_uses_basic_auth_and_returns_normalized_summaries(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "title": "ACME LIMITED",
                "company_number": "12345678",
                "company_status": "active",
                "company_type": "ltd",
                "date_of_creation": "2020-02-03",
                "address_snippet": "London",
                "raw_secret": "do-not-return",
            }],
        })

        result = company_registry.search_companies_house("acme")

        assert len(result) == 1
        assert result[0]["provider"] == "companies_house"
        assert result[0]["jurisdiction"] == "GB"
        assert result[0]["company_name"] == "ACME LIMITED"
        assert result[0]["company_number"] == "12345678"
        assert not _contains(result, "do-not-return")
        _, kwargs = mock_get.call_args
        assert kwargs["auth"] == ("test-companies-house-key", "")
        assert "Authorization" not in kwargs
        assert kwargs["params"] == {"q": "acme"}

    @patch("company_registry.requests.get")
    def test_profile_success_has_stable_normalized_company_shape(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "company_name": "ACME LIMITED",
            "company_number": "12345678",
            "company_status": "active",
            "type": "ltd",
            "date_of_creation": "2020-02-03",
            "registered_office_address": {"address_line_1": "1 Road", "locality": "London"},
            "sic_codes": ["62012"],
        })

        result = company_registry.get_companies_house_profile("12345678")

        assert set(result.keys()) == {
            "provider",
            "jurisdiction",
            "company_name",
            "company_number",
            "company_status",
            "entity_type",
            "incorporation_date",
            "registered_address",
            "sic_codes",
            "officers",
            "beneficial_owners",
            "source_metadata",
        }
        assert result["company_name"] == "ACME LIMITED"
        assert result["registered_address"]["full_address"] == "1 Road, London"
        assert set(result["source_metadata"].keys()) == {
            "fetched_at",
            "endpoint",
            "response_hash",
            "simulation",
        }

    @patch("company_registry.requests.get")
    def test_officers_success_filters_to_active_director_candidates(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [
                {"name": "Active Director", "officer_role": "director", "appointed_on": "2020-01-01"},
                {"name": "Resigned Director", "officer_role": "director", "resigned_on": "2021-01-01"},
                {"name": "Company Secretary", "officer_role": "secretary"},
                {"name": "Corporate Director", "officer_role": "corporate-director"},
            ],
        })

        officers = company_registry.get_companies_house_officers("12345678")

        names = [officer["name"] for officer in officers]
        assert names == ["Active Director", "Corporate Director"]
        assert all(officer["is_candidate_director"] for officer in officers)
        assert officers[0]["candidate_type"] == "director_candidate"
        assert officers[1]["candidate_type"] == "corporate_structure_review"
        assert "Resigned Director" not in names
        assert "Company Secretary" not in names

    @patch("company_registry.requests.get")
    def test_officers_success_retains_active_llp_member_candidates(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [
                {"name": "Active Member", "officer_role": "llp-member", "appointed_on": "2020-01-01"},
                {"name": "Designated Member", "officer_role": "llp-designated-member", "appointed_on": "2020-01-02"},
                {"name": "Equivalent Designated Member", "officer_role": "designated-llp-member", "appointed_on": "2020-01-03"},
                {"name": "Resigned Member", "officer_role": "llp-member", "resigned_on": "2021-01-01"},
                {"name": "Company Secretary", "officer_role": "secretary"},
            ],
        })

        officers = company_registry.get_companies_house_officers("OC123456")

        assert [officer["name"] for officer in officers] == [
            "Active Member",
            "Designated Member",
            "Equivalent Designated Member",
        ]
        assert all(officer["candidate_type"] == "llp_member_candidate" for officer in officers)
        assert all(officer["is_candidate_llp_member"] is True for officer in officers)
        assert all(officer["is_candidate_director"] is False for officer in officers)
        assert all(officer["officer_entity_type"] == "individual" for officer in officers)

    @pytest.mark.parametrize(
        "officer_role",
        ["director", "llp-designated-member", "llp-member"],
    )
    @patch("company_registry.requests.get")
    def test_officers_success_retains_country_of_residence_for_individual_candidates(self, mock_get, officer_role):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "name": "Resident Candidate",
                "officer_role": officer_role,
                "appointed_on": "2020-01-01",
                "country_of_residence": "United Kingdom",
            }],
        })

        officers = company_registry.get_companies_house_officers("12345678")

        assert len(officers) == 1
        assert officers[0]["officer_role"] == officer_role
        assert officers[0]["country_of_residence"] == "United Kingdom"

    @patch("company_registry.requests.get")
    def test_officers_success_flags_corporate_llp_member_for_structure_review(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "name": "Corporate LLP Member Ltd",
                "officer_role": "corporate-llp-designated-member",
                "appointed_on": "2020-01-01",
            }],
        })

        officers = company_registry.get_companies_house_officers("OC123456")

        assert len(officers) == 1
        assert officers[0]["candidate_type"] == "corporate_structure_review"
        assert officers[0]["officer_entity_type"] == "corporate"
        assert officers[0]["is_candidate_llp_member"] is True
        assert officers[0]["is_candidate_director"] is False
        assert officers[0]["requires_individual_kyc"] is False
        assert officers[0]["requires_corporate_structure_review"] is True

    @patch("company_registry.requests.get")
    def test_oc381818_llp_member_roles_are_retained_as_staging_smoke_fixture(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [
                {
                    "name": "MARGOLIS, Stephen Howard",
                    "officer_role": "llp-designated-member",
                    "appointed_on": "2013-09-26",
                },
                {
                    "name": "TAURUS (DM) LIMITED",
                    "officer_role": "corporate-llp-designated-member",
                    "appointed_on": "2017-07-21",
                },
                {
                    "name": "GARDINER, Thomas James",
                    "officer_role": "llp-designated-member",
                    "appointed_on": "2013-01-22",
                    "resigned_on": "2013-11-04",
                },
            ],
        })

        officers = company_registry.get_companies_house_officers("OC381818")

        assert [officer["name"] for officer in officers] == [
            "MARGOLIS, Stephen Howard",
            "TAURUS (DM) LIMITED",
        ]
        assert [officer["candidate_type"] for officer in officers] == [
            "llp_member_candidate",
            "corporate_structure_review",
        ]

    @patch("company_registry.requests.get")
    def test_individual_director_classification_requires_individual_kyc(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "name": "Individual Director",
                "officer_role": "director",
                "appointed_on": "2020-01-01",
            }],
        })

        officers = company_registry.get_companies_house_officers("12345678")

        assert officers[0]["officer_entity_type"] == "individual"
        assert officers[0]["requires_individual_kyc"] is True
        assert officers[0]["requires_corporate_structure_review"] is False
        assert officers[0]["is_candidate_director"] is True

    @patch("company_registry.requests.get")
    def test_officer_normalization_keeps_registry_original_candidate_values(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "name": "SMITH, Jane Ann",
                "officer_role": "director",
                "appointed_on": "2020-01-01",
                "nationality": "British",
                "country_of_residence": "United Kingdom",
                "date_of_birth": {"month": 5, "year": 1980},
            }],
        })

        officers = company_registry.get_companies_house_officers("12345678")

        assert officers[0]["name"] == "SMITH, Jane Ann"
        assert officers[0]["nationality"] == "British"
        assert officers[0]["country_of_residence"] == "United Kingdom"
        assert officers[0]["appointed_on"] == "2020-01-01"
        assert officers[0]["date_of_birth"] == {"month": 5, "year": 1980}

    @patch("company_registry.requests.get")
    def test_corporate_director_classification_requires_structure_review(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "name": "Corporate Director Ltd",
                "officer_role": "corporate-director",
                "appointed_on": "2020-01-01",
            }],
        })

        officers = company_registry.get_companies_house_officers("12345678")

        assert officers[0]["officer_entity_type"] == "corporate"
        assert officers[0]["requires_individual_kyc"] is False
        assert officers[0]["requires_corporate_structure_review"] is True
        assert officers[0]["is_candidate_director"] is True

    @pytest.mark.parametrize(
        "payload,expected_state,expected_names,reason_text",
        [
            (
                {"items": [{"name": "Jane Owner", "kind": "individual-person-with-significant-control"}]},
                "psc_found",
                ["Jane Owner"],
                "active individual PSC entries",
            ),
            ({"active_count": 0, "items": []}, "no_psc", [], "No active PSC entries"),
            (
                {"items": [{"kind": "persons-with-significant-control-statement", "statement": "psc-exempt"}]},
                "psc_exempt",
                [],
                "PSC exemption statement",
            ),
            (
                {"items": [{"name": "HoldCo Ltd", "kind": "corporate-entity-person-with-significant-control"}]},
                "corporate_psc",
                ["HoldCo Ltd"],
                "corporate or legal-person PSC",
            ),
        ],
    )
    @patch("company_registry.requests.get")
    def test_psc_states_are_first_class(self, mock_get, payload, expected_state, expected_names, reason_text):
        import company_registry

        mock_get.return_value = _mock_response(200, payload)

        result = company_registry.get_companies_house_pscs("12345678")

        assert result["psc_state"] == expected_state
        assert result["registry_statement_type"]
        assert reason_text in result["psc_status_reason"]
        assert [owner["name"] for owner in result["beneficial_owners"]] == expected_names
        assert not _contains(result, "shareholder")
        for owner in result["beneficial_owners"]:
            assert owner["candidate_type"] == "beneficial_owner_candidate"
            assert owner["is_candidate_beneficial_owner"] is True

    @patch("company_registry.requests.get")
    def test_individual_psc_normalization_keeps_registry_original_candidate_values(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "name": "Jane PSC",
                "kind": "individual-person-with-significant-control",
                "nationality": "British",
                "country_of_residence": "United Kingdom",
                "date_of_birth": {"month": 6, "year": 1975},
            }],
        })

        result = company_registry.get_companies_house_pscs("12345678")
        owner = result["beneficial_owners"][0]

        assert owner["name"] == "Jane PSC"
        assert owner["nationality"] == "British"
        assert owner["country_of_residence"] == "United Kingdom"
        assert owner["date_of_birth"] == {"month": 6, "year": 1975}

    @patch("company_registry.requests.get")
    def test_no_psc_reason_uses_empty_registry_result(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {"items": []})

        result = company_registry.get_companies_house_pscs("12345678")

        assert result["psc_state"] == "no_psc"
        assert result["registry_statement_type"] == "no_active_psc_entries"
        assert "No active PSC entries" in result["psc_status_reason"]

    @patch("company_registry.requests.get")
    def test_psc_exempt_reason_uses_registry_statement(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "kind": "persons-with-significant-control-statement",
                "statement": "psc-exempt",
            }],
        })

        result = company_registry.get_companies_house_pscs("12345678")

        assert result["psc_state"] == "psc_exempt"
        assert result["registry_statement_type"] == "persons-with-significant-control-statement"
        assert "PSC exemption statement" in result["psc_status_reason"]

    @patch("company_registry.requests.get")
    def test_corporate_psc_reason_requires_structure_review(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "name": "HoldCo Ltd",
                "kind": "corporate-entity-person-with-significant-control",
                "identification": {
                    "country_registered": "United Kingdom",
                    "registration_number": "99999999",
                },
                "address": {
                    "address_line_1": "1 Holdco Street",
                    "locality": "London",
                },
            }],
        })

        result = company_registry.get_companies_house_pscs("12345678")

        assert result["psc_state"] == "corporate_psc"
        assert result["registry_statement_type"] == "corporate-entity-person-with-significant-control"
        assert "requires corporate structure review" in result["psc_status_reason"]
        owner = result["beneficial_owners"][0]
        assert owner["kind"] == "corporate"
        assert owner["country_of_incorporation"] == "United Kingdom"
        assert owner["registration_number"] == "99999999"
        assert owner["registered_address"]["full_address"] == "1 Holdco Street, London"

    @patch("company_registry.requests.get")
    def test_corporate_psc_normalization_skips_blank_identification_fallbacks(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "name": "Fallback HoldCo Ltd",
                "kind": "corporate-entity-person-with-significant-control",
                "identification": {
                    "country_registered": "   ",
                    "place_registered": "",
                    "registration_number": " ",
                },
                "country_of_incorporation": "Ireland",
                "registration_number": "IE-12345",
                "registered_office_address": {},
                "principal_office_address": {"address_line_1": "7 Fallback Street", "locality": "Dublin"},
            }],
        })

        result = company_registry.get_companies_house_pscs("12345678")

        owner = result["beneficial_owners"][0]
        assert owner["country_of_incorporation"] == "Ireland"
        assert owner["registration_number"] == "IE-12345"
        assert owner["registered_address"]["full_address"] == "7 Fallback Street, Dublin"

    @pytest.mark.parametrize(
        "status_code,error_code",
        [
            (404, "company_not_found"),
            (429, "provider_rate_limited"),
        ],
    )
    @patch("company_registry.requests.get")
    def test_provider_status_errors_are_structured(self, mock_get, status_code, error_code):
        import company_registry

        mock_get.return_value = _mock_response(status_code, {})

        result = company_registry.get_companies_house_profile("12345678")

        assert result["success"] is False
        assert result["provider"] == "companies_house"
        assert result["error_code"] == error_code
        assert result["manual_fallback_allowed"] is True

    @patch("company_registry.requests.get")
    def test_timeout_handling_is_structured(self, mock_get):
        import company_registry

        mock_get.side_effect = Timeout("slow")

        result = company_registry.search_companies_house("acme")

        assert result["error_code"] == "provider_timeout"
        assert result["manual_fallback_allowed"] is True

    @patch("company_registry.requests.get")
    def test_malformed_response_handling_is_structured(self, mock_get):
        import company_registry

        mock_get.return_value = _mock_response(200, {"unexpected": "shape"})

        result = company_registry.search_companies_house("acme")

        assert result["error_code"] == "provider_malformed_response"
        assert result["manual_fallback_allowed"] is True

    def test_missing_api_key_in_non_production_uses_safe_simulation(self, monkeypatch):
        import company_registry

        monkeypatch.setattr(company_registry, "COMPANIES_HOUSE_API_KEY", "")
        monkeypatch.setattr(company_registry, "is_production", lambda: False)

        result = company_registry.search_companies_house("acme")

        assert len(result) == 1
        assert result[0]["provider"] == "companies_house"
        assert result[0]["source_metadata"]["simulation"] is True

    def test_missing_api_key_in_production_fails_closed(self, monkeypatch):
        import company_registry

        monkeypatch.setattr(company_registry, "COMPANIES_HOUSE_API_KEY", "")
        monkeypatch.setattr(company_registry, "is_production", lambda: True)

        result = company_registry.search_companies_house("acme")

        assert result["success"] is False
        assert result["error_code"] == "provider_not_configured"

    @patch("company_registry.requests.get")
    def test_api_key_never_appears_in_response_payload(self, mock_get, monkeypatch):
        import company_registry

        secret = "secret-companies-house-key"
        monkeypatch.setattr(company_registry, "COMPANIES_HOUSE_API_KEY", secret)
        mock_get.return_value = _mock_response(200, {
            "company_name": "Secret Test Ltd",
            "company_number": "12345678",
            "provider_debug": secret,
        })

        result = company_registry.get_companies_house_profile("12345678")

        assert not _contains(result, secret)

    @patch("company_registry.requests.get")
    def test_request_failures_do_not_log_api_key(self, mock_get, monkeypatch, caplog):
        import company_registry

        secret = "secret-companies-house-key"
        monkeypatch.setattr(company_registry, "COMPANIES_HOUSE_API_KEY", secret)
        mock_get.side_effect = RequestException(f"provider exploded api_key={secret}")

        result = company_registry.search_companies_house("acme")

        assert result["error_code"] == "provider_unavailable"
        assert secret not in caplog.text


class TestOpenCorporatesUnification:
    @patch("screening.requests.get")
    def test_lookup_opencorporates_routes_output_through_normalizer(self, mock_get, monkeypatch):
        import screening

        monkeypatch.setattr(screening, "OPENCORPORATES_API_KEY", "test-key")
        mock_get.return_value = _mock_response(200, {
            "results": {
                "companies": [{
                    "company": {
                        "name": "OC Test Ltd",
                        "company_number": "OC123",
                        "jurisdiction_code": "mu",
                        "current_status": "Active",
                        "company_type": "Private Company",
                    }
                }],
                "total_count": 1,
            },
        })

        result = screening.lookup_opencorporates("OC Test", "mu")

        assert result["found"] is True
        assert result["source"] == "opencorporates"
        assert result["companies"][0]["name"] == "OC Test Ltd"
        assert result["companies"][0]["status"] == "Active"
        assert result["normalized_companies"][0]["provider"] == "opencorporates"
        assert result["normalized_companies"][0]["company_name"] == "OC Test Ltd"

    def test_lookup_opencorporates_simulation_preserves_legacy_fields(self, monkeypatch):
        import screening

        monkeypatch.setattr(screening, "OPENCORPORATES_API_KEY", "")
        monkeypatch.setattr(screening, "is_production", lambda: False)

        result = screening.lookup_opencorporates("Legacy Corp")

        assert "found" in result
        assert "companies" in result
        assert "total_results" in result
        assert "source" in result
        assert "api_status" in result
        assert "normalized_companies" in result
        if result["companies"]:
            legacy = result["companies"][0]
            assert {"name", "company_number", "jurisdiction", "status"}.issubset(legacy.keys())


class TestCompanyIntakeEndpoints:
    def test_unauthenticated_request_is_rejected(self):
        from server import CompanyIntakeSearchHandler

        handler, _audits = _handler(CompanyIntakeSearchHandler, "/api/company-intake/search?q=acme")

        handler.get()

        assert handler.get_status() == 401
        assert _body(handler)["error"] == "Authentication required"

    @patch("company_registry.requests.get")
    def test_authenticated_search_endpoint_returns_sanitized_normalized_payload(self, mock_get):
        import company_registry
        from server import CompanyIntakeSearchHandler

        mock_get.return_value = _mock_response(200, {
            "items": [{
                "title": "Endpoint Ltd",
                "company_number": "12345678",
                "company_status": "active",
                "raw_payload_marker": "must-not-return",
            }],
        })
        handler, audits = _handler(
            CompanyIntakeSearchHandler,
            "/api/company-intake/search?q=Endpoint",
            auth_user={"sub": "client1", "name": "Client", "role": "client", "type": "client"},
        )

        handler.get()
        payload = _body(handler)

        assert handler.get_status() == 200
        assert payload["success"] is True
        assert payload["provider"] == "companies_house"
        assert payload["results"][0]["company_name"] == "Endpoint Ltd"
        assert not _contains(payload, "must-not-return")
        assert len(audits) == 2
        assert all(call["action"] == "Company Registry Lookup" for call in audits)
        assert not _contains(audits, company_registry.COMPANIES_HOUSE_API_KEY)

    @patch("server.search_companies_house")
    def test_endpoint_error_response_uses_structured_shape(self, mock_search):
        from server import CompanyIntakeSearchHandler

        mock_search.return_value = {
            "success": False,
            "provider": "companies_house",
            "error_code": "provider_rate_limited",
            "message": "Company registry is temporarily unavailable. Please try again or continue manually.",
            "manual_fallback_allowed": True,
        }
        handler, audits = _handler(
            CompanyIntakeSearchHandler,
            "/api/company-intake/search?q=Endpoint",
            auth_user={"sub": "client1", "name": "Client", "role": "client", "type": "client"},
        )

        handler.get()
        payload = _body(handler)

        assert handler.get_status() == 429
        assert payload["success"] is False
        assert payload["error_code"] == "provider_rate_limited"
        assert payload["manual_fallback_allowed"] is True
        assert any("provider_error" in call["detail"] for call in audits)

    @patch("server.search_companies_house")
    def test_endpoint_provider_exception_does_not_return_stack_trace(self, mock_search):
        from server import CompanyIntakeSearchHandler

        mock_search.side_effect = RuntimeError("raw stack trace secret")
        handler, _audits = _handler(
            CompanyIntakeSearchHandler,
            "/api/company-intake/search?q=Endpoint",
            auth_user={"sub": "client1", "name": "Client", "role": "client", "type": "client"},
        )

        handler.get()
        payload = _body(handler)

        assert handler.get_status() == 503
        assert payload["error_code"] == "provider_unavailable"
        assert not _contains(payload, "RuntimeError")
        assert not _contains(payload, "raw stack trace secret")

    @patch("server.get_companies_house_pscs")
    def test_psc_endpoint_returns_no_psc_as_success_not_error(self, mock_pscs):
        from server import CompanyIntakePSCsHandler

        mock_pscs.return_value = {
            "provider": "companies_house",
            "jurisdiction": "GB",
            "company_number": "12345678",
            "psc_state": "no_psc",
            "beneficial_owners": [],
            "source_metadata": {
                "fetched_at": "2026-06-22T00:00:00+00:00",
                "endpoint": "/company/12345678/persons-with-significant-control",
                "response_hash": "abc",
                "simulation": False,
            },
        }
        handler, audits = _handler(
            CompanyIntakePSCsHandler,
            "/api/company-intake/company/12345678/pscs",
            auth_user={"sub": "client1", "name": "Client", "role": "client", "type": "client"},
        )

        handler.get("12345678")
        payload = _body(handler)

        assert handler.get_status() == 200
        assert payload["pscs"]["psc_state"] == "no_psc"
        assert any("no_psc" in call["detail"] for call in audits)
