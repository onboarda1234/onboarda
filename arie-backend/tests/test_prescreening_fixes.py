"""Tests for pre-screening page fixes:
1. Country dropdown consistency
2. Financial forecast profit auto-calculation
3. Authorised share capital numeric handling
"""

import json
import os
import re
import unittest


# ─── HTML fixtures ──────────────────────────────────────────────
PORTAL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
BACKOFFICE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "arie-backoffice.html")


def _read_portal():
    with open(PORTAL_PATH, encoding="utf-8") as f:
        return f.read()


def _read_backoffice():
    with open(BACKOFFICE_PATH, encoding="utf-8") as f:
        return f.read()


def _extract_all_countries(html):
    """Extract the ALL_COUNTRIES JS array from the portal HTML."""
    match = re.search(r"const ALL_COUNTRIES\s*=\s*\[([^\]]+)\]", html, re.DOTALL)
    assert match, "ALL_COUNTRIES array not found in portal HTML"
    entries = re.findall(r"'([^']+)'", match.group(1))
    return entries


def _extract_inc_country_options(html):
    """If f-inc-country is dynamically populated, return the populateIncCountry function exists."""
    return "populateIncCountry" in html


# ═══════════════════════════════════════════════════════════════
# 1. Country Dropdown Consistency
# ═══════════════════════════════════════════════════════════════
class TestCountryDropdownConsistency(unittest.TestCase):

    def setUp(self):
        self.html = _read_portal()
        self.all_countries = _extract_all_countries(self.html)

    def test_all_countries_includes_bvi(self):
        assert "British Virgin Islands" in self.all_countries

    def test_all_countries_includes_cayman_islands(self):
        assert "Cayman Islands" in self.all_countries

    def test_all_countries_includes_bermuda(self):
        assert "Bermuda" in self.all_countries

    def test_all_countries_includes_gibraltar(self):
        assert "Gibraltar" in self.all_countries

    def test_all_countries_includes_isle_of_man(self):
        assert "Isle of Man" in self.all_countries

    def test_all_countries_includes_jersey(self):
        assert "Jersey" in self.all_countries

    def test_all_countries_includes_guernsey(self):
        assert "Guernsey" in self.all_countries

    def test_all_countries_includes_turks_and_caicos(self):
        assert "Turks and Caicos Islands" in self.all_countries

    def test_all_countries_includes_anguilla(self):
        assert "Anguilla" in self.all_countries

    def test_all_countries_includes_labuan(self):
        assert "Labuan (Malaysia)" in self.all_countries

    def test_all_countries_includes_territories_separator(self):
        assert "── Territories / Offshore ──" in self.all_countries

    def test_inc_country_dynamically_populated(self):
        """f-inc-country dropdown should be populated from ALL_COUNTRIES via JS."""
        assert _extract_inc_country_options(self.html), (
            "populateIncCountry function not found — f-inc-country should be dynamically populated"
        )

    def test_no_duplicate_countries(self):
        """No duplicate country entries in ALL_COUNTRIES (excluding separators)."""
        countries_only = [c for c in self.all_countries if not c.startswith("──")]
        seen = set()
        duplicates = []
        for c in countries_only:
            if c in seen:
                duplicates.append(c)
            seen.add(c)
        assert not duplicates, f"Duplicate countries found: {duplicates}"

    def test_all_countries_has_common_countries(self):
        """Verify common countries are present."""
        expected = [
            "Mauritius", "United Kingdom", "United States",
            "United Arab Emirates", "Singapore", "Hong Kong SAR",
            "France", "Germany", "Japan", "India",
        ]
        for country in expected:
            assert country in self.all_countries, f"Missing common country: {country}"

    def test_inc_country_no_hardcoded_options(self):
        """f-inc-country should NOT have hardcoded <option> elements inline (beyond the placeholder)."""
        # Find the select element and check it only has the placeholder option
        match = re.search(
            r'<select id="f-inc-country"[^>]*>(.*?)</select>',
            self.html,
            re.DOTALL,
        )
        assert match, "f-inc-country select element not found"
        inner = match.group(1).strip()
        # Should only have the placeholder option
        options = re.findall(r"<option[^>]*>", inner)
        assert len(options) <= 1, (
            f"f-inc-country has {len(options)} hardcoded options; expected only the placeholder"
        )

    def test_populate_inc_country_called_on_init(self):
        """populateIncCountry() should be called during page initialization."""
        assert "populateIncCountry();" in self.html


# ═══════════════════════════════════════════════════════════════
# 2. Financial Forecast Profit Auto-Calculation
# ═══════════════════════════════════════════════════════════════
class TestFinancialForecastProfit(unittest.TestCase):

    def setUp(self):
        self.html = _read_portal()

    def test_profit_fields_are_readonly(self):
        """Profit input fields should be readonly."""
        for yr in (1, 2, 3):
            pattern = rf'id="f-profit{yr}"[^>]*readonly'
            assert re.search(pattern, self.html), f"f-profit{yr} should be readonly"

    def test_revenue_triggers_profit_update(self):
        """Revenue inputs should call updateProfitRow on input."""
        for yr in (1, 2, 3):
            pattern = rf'id="f-rev{yr}"[^>]*updateProfitRow'
            assert re.search(pattern, self.html), f"f-rev{yr} should trigger updateProfitRow"

    def test_cost_triggers_profit_update(self):
        """Cost inputs should call updateProfitRow on input."""
        for yr in (1, 2, 3):
            pattern = rf'id="f-cos{yr}"[^>]*updateProfitRow'
            assert re.search(pattern, self.html), f"f-cos{yr} should trigger updateProfitRow"

    def test_update_profit_row_function_exists(self):
        """updateProfitRow function should be defined."""
        assert "function updateProfitRow()" in self.html

    def test_profit_still_submitted(self):
        """Profit values should still be collected for submission."""
        assert "getOptionalNumber('f-profit1')" in self.html
        assert "getOptionalNumber('f-profit2')" in self.html
        assert "getOptionalNumber('f-profit3')" in self.html


class TestBackendProfitDerivation(unittest.TestCase):

    def test_profit_derived_from_revenue_minus_cost(self):
        from prescreening.normalize import normalize_prescreening_data

        data = {
            "prescreening_data": {
                "financial_forecast": {
                    "revenue": {"year_1": 100000, "year_2": 200000, "year_3": 300000},
                    "cost_of_sales": {"year_1": 60000, "year_2": 80000, "year_3": 100000},
                }
            }
        }
        result = normalize_prescreening_data(data)
        forecast = result.get("financial_forecast", {})
        profit = forecast.get("profit", {})
        assert profit.get("year_1") == 40000
        assert profit.get("year_2") == 120000
        assert profit.get("year_3") == 200000

    def test_profit_handles_zero_revenue(self):
        from prescreening.normalize import normalize_prescreening_data

        data = {
            "prescreening_data": {
                "financial_forecast": {
                    "revenue": {"year_1": 0, "year_2": 0, "year_3": 0},
                    "cost_of_sales": {"year_1": 10000, "year_2": 20000, "year_3": 30000},
                }
            }
        }
        result = normalize_prescreening_data(data)
        profit = result["financial_forecast"]["profit"]
        assert profit["year_1"] == -10000
        assert profit["year_2"] == -20000
        assert profit["year_3"] == -30000

    def test_profit_handles_missing_cost(self):
        from prescreening.normalize import normalize_prescreening_data

        data = {
            "prescreening_data": {
                "financial_forecast": {
                    "revenue": {"year_1": 50000, "year_2": 100000, "year_3": 150000},
                }
            }
        }
        result = normalize_prescreening_data(data)
        profit = result["financial_forecast"]["profit"]
        assert profit["year_1"] == 50000
        assert profit["year_2"] == 100000
        assert profit["year_3"] == 150000

    def test_profit_handles_null_values(self):
        from prescreening.normalize import normalize_prescreening_data

        data = {
            "prescreening_data": {
                "financial_forecast": {
                    "revenue": {"year_1": None, "year_2": 100000, "year_3": None},
                    "cost_of_sales": {"year_1": None, "year_2": None, "year_3": 50000},
                }
            }
        }
        result = normalize_prescreening_data(data)
        profit = result["financial_forecast"]["profit"]
        # year_1: both None — no entry expected
        assert "year_1" not in profit or profit.get("year_1") is None or profit.get("year_1") == 0
        assert profit["year_2"] == 100000
        assert profit["year_3"] == -50000

    def test_profit_negative_when_cost_exceeds_revenue(self):
        from prescreening.normalize import normalize_prescreening_data

        data = {
            "prescreening_data": {
                "financial_forecast": {
                    "revenue": {"year_1": 50000},
                    "cost_of_sales": {"year_1": 80000},
                }
            }
        }
        result = normalize_prescreening_data(data)
        profit = result["financial_forecast"]["profit"]
        assert profit["year_1"] == -30000

    def test_existing_profit_overridden_by_derivation(self):
        """If frontend sends manually entered profit, backend should re-derive it."""
        from prescreening.normalize import normalize_prescreening_data

        data = {
            "prescreening_data": {
                "financial_forecast": {
                    "revenue": {"year_1": 100000},
                    "cost_of_sales": {"year_1": 40000},
                    "profit": {"year_1": 999999},  # wrong manual value
                }
            }
        }
        result = normalize_prescreening_data(data)
        profit = result["financial_forecast"]["profit"]
        assert profit["year_1"] == 60000, "Profit should be derived, not from manual input"

    def test_no_forecast_data_is_safe(self):
        """No financial_forecast should not cause errors."""
        from prescreening.normalize import normalize_prescreening_data

        data = {"prescreening_data": {"registered_entity_name": "Test Corp"}}
        result = normalize_prescreening_data(data)
        assert result.get("registered_entity_name") == "Test Corp"

    def test_legacy_forecast_data_preserved(self):
        """Existing financial_forecast data should survive normalization."""
        from prescreening.normalize import normalize_prescreening_data

        data = {
            "prescreening_data": {
                "financial_forecast": {
                    "revenue": {"year_1": 100000, "year_2": 200000, "year_3": 300000},
                    "cost_of_sales": {"year_1": 50000, "year_2": 80000, "year_3": 100000},
                    "profit": {"year_1": 50000, "year_2": 120000, "year_3": 200000},
                }
            }
        }
        result = normalize_prescreening_data(data)
        assert "financial_forecast" in result
        forecast = result["financial_forecast"]
        assert isinstance(forecast, dict)
        assert "revenue" in forecast
        assert "cost_of_sales" in forecast
        assert "profit" in forecast


# ═══════════════════════════════════════════════════════════════
# 3. Authorised Share Capital Numeric Handling
# ═══════════════════════════════════════════════════════════════
class TestAuthorisedShareCapitalFrontend(unittest.TestCase):

    def setUp(self):
        self.html = _read_portal()

    def test_share_capital_has_numeric_inputmode(self):
        """Share capital input should have inputmode='numeric'."""
        match = re.search(
            r'id="f-authorised-share-capital"[^>]*inputmode="numeric"'
            r'|inputmode="numeric"[^>]*id="f-authorised-share-capital"',
            self.html,
        )
        assert match, "f-authorised-share-capital should have inputmode='numeric'"

    def test_share_capital_has_format_handler(self):
        """Share capital input should have formatCurrencyInput handler."""
        match = re.search(
            r'id="f-authorised-share-capital"[^>]*formatCurrencyInput'
            r'|formatCurrencyInput[^>]*id="f-authorised-share-capital"',
            self.html,
        )
        assert match, "f-authorised-share-capital should call formatCurrencyInput"

    def test_share_capital_submitted_as_clean_value(self):
        """Share capital should be submitted via getCurrencyFieldValue (stripping commas)."""
        assert "getCurrencyFieldValue('f-authorised-share-capital')" in self.html

    def test_share_capital_in_field_bindings(self):
        """f-authorised-share-capital should be in PRESCREENING_FIELD_BINDINGS."""
        match = re.search(
            r"'f-authorised-share-capital'\s*:\s*'authorised_share_capital'",
            self.html,
        )
        assert match, "f-authorised-share-capital should be mapped in PRESCREENING_FIELD_BINDINGS"


class TestBackendShareCapitalNormalization(unittest.TestCase):

    def test_numeric_value_normalized(self):
        from prescreening.normalize import normalize_prescreening_data

        data = {"prescreening_data": {"authorised_share_capital": "100000"}}
        result = normalize_prescreening_data(data)
        assert result["authorised_share_capital"] == "100000"

    def test_comma_formatted_value_normalized(self):
        from prescreening.normalize import normalize_prescreening_data

        data = {"prescreening_data": {"authorised_share_capital": "100,000"}}
        result = normalize_prescreening_data(data)
        assert result["authorised_share_capital"] == "100000"

    def test_legacy_text_value_preserved(self):
        """Legacy values like '100,000 USD' should preserve the numeric part."""
        from prescreening.normalize import normalize_prescreening_data

        data = {"prescreening_data": {"authorised_share_capital": "100,000 USD"}}
        result = normalize_prescreening_data(data)
        assert result["authorised_share_capital"] == "100000"

    def test_empty_value_preserved(self):
        from prescreening.normalize import normalize_prescreening_data

        data = {"prescreening_data": {"authorised_share_capital": ""}}
        result = normalize_prescreening_data(data)
        # Empty string is preserved since _safe_number returns None
        assert result.get("authorised_share_capital") == ""

    def test_none_value_safe(self):
        from prescreening.normalize import normalize_prescreening_data

        data = {"prescreening_data": {}}
        result = normalize_prescreening_data(data)
        # Should not crash

    def test_share_capital_in_session_field_map(self):
        from prescreening.fields import SESSION_PRESCREENING_FIELD_MAP

        assert "f-authorised-share-capital" in SESSION_PRESCREENING_FIELD_MAP
        assert SESSION_PRESCREENING_FIELD_MAP["f-authorised-share-capital"] == "authorised_share_capital"


# ═══════════════════════════════════════════════════════════════
# 4. Back-Office Display
# ═══════════════════════════════════════════════════════════════
class TestBackOfficeDisplay(unittest.TestCase):

    def setUp(self):
        self.html = _read_backoffice()

    def test_backoffice_displays_share_capital(self):
        """Back-office should display Authorised Share Capital in prescreening summary."""
        assert "Authorised Share Capital" in self.html

    def test_backoffice_financial_forecast_display(self):
        """Back-office should display Financial Forecast including Profit."""
        assert "formatFinancialForecast" in self.html
        assert "Profit:" in self.html


# ═══════════════════════════════════════════════════════════════
# 5. Regression Safety
# ═══════════════════════════════════════════════════════════════
class TestRegressionSafety(unittest.TestCase):

    def test_prescreening_submission_structure_intact(self):
        """The prescreening submission object should still include all required fields."""
        html = _read_portal()
        required_fields = [
            "registered_entity_name",
            "country_of_incorporation",
            "authorised_share_capital",
            "financial_forecast",
            "services_required",
            "countries_of_operation",
        ]
        for field in required_fields:
            assert field in html, f"Submission field '{field}' missing from portal"

    def test_normalization_preserves_all_fields(self):
        """Normalization should preserve all known prescreening fields."""
        from prescreening.normalize import normalize_prescreening_data

        data = {
            "company_name": "Test Corp",
            "country": "Mauritius",
            "prescreening_data": {
                "registered_entity_name": "Test Corp",
                "country_of_incorporation": "Mauritius",
                "entity_type": "Private Limited",
                "authorised_share_capital": "500000",
                "financial_forecast": {
                    "revenue": {"year_1": 100000, "year_2": 200000, "year_3": 300000},
                    "cost_of_sales": {"year_1": 50000, "year_2": 80000, "year_3": 100000},
                },
                "countries_of_operation": ["Mauritius", "British Virgin Islands"],
            },
        }
        result = normalize_prescreening_data(data)
        assert result["registered_entity_name"] == "Test Corp"
        assert result["country_of_incorporation"] == "Mauritius"
        assert result["entity_type"] == "Private Limited"
        assert result["authorised_share_capital"] == "500000"
        assert "financial_forecast" in result
        assert result["financial_forecast"]["profit"]["year_1"] == 50000

    def test_backoffice_prescreening_fields_complete(self):
        """Back-office prescreening display should include core fields."""
        html = _read_backoffice()
        expected_labels = [
            "Registered Entity Name",
            "Country of Incorporation",
            "Authorised Share Capital",
            "Financial Forecast",
            "Countries of Operation",
        ]
        for label in expected_labels:
            assert label in html, f"Back-office missing label: {label}"


if __name__ == "__main__":
    unittest.main()
