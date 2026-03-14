"""
Tests for risk scoring engine.
"""
import pytest


class TestRiskScoring:
    def test_low_risk_application(self, temp_db):
        from server import compute_risk_score
        data = {
            "entity_type": "Listed Company",
            "ownership_structure": "Simple",
            "country": "Mauritius",
            "sector": "Technology",
            "directors": [],
            "ubos": [],
        }
        result = compute_risk_score(data)
        assert result["level"] in ("LOW", "MEDIUM")
        assert 0 <= result["score"] <= 100
        assert "d1" in result["dimensions"]

    def test_high_risk_application(self, temp_db):
        from server import compute_risk_score
        data = {
            "entity_type": "Shell Company",
            "ownership_structure": "Complex multi-layered",
            "country": "Iran",
            "sector": "Crypto",
            "directors": [{"is_pep": "Yes"}],
            "ubos": [{"is_pep": "Yes"}],
        }
        result = compute_risk_score(data)
        assert result["level"] in ("HIGH", "VERY_HIGH")
        assert result["score"] >= 55

    def test_country_classification(self, temp_db):
        from server import classify_country
        assert classify_country("Mauritius") == 1
        assert classify_country("Iran") == 4
        assert classify_country("Nigeria") == 3
        assert classify_country("Unknown Country") == 2
        assert classify_country(None) == 2

    def test_sector_scoring(self, temp_db):
        from server import score_sector
        assert score_sector("Regulated Financial") == 1
        assert score_sector("Crypto Exchange") == 4
        assert score_sector("Technology") == 2
        assert score_sector(None) == 2

    def test_risk_dimensions_present(self, temp_db):
        from server import compute_risk_score
        data = {"entity_type": "SME", "country": "UK", "sector": "Retail"}
        result = compute_risk_score(data)
        dims = result["dimensions"]
        assert all(k in dims for k in ["d1", "d2", "d3", "d4", "d5"])

    def test_risk_lane_assignment(self, temp_db):
        from server import compute_risk_score
        low = compute_risk_score({"entity_type": "Listed", "country": "UK", "sector": "Bank"})
        assert low["lane"] in ("Fast Lane", "Standard Review", "EDD")


class TestScreening:
    def test_simulated_sanctions_screen(self, temp_db):
        from server import _simulate_sanctions_screen
        result = _simulate_sanctions_screen("John Smith")
        assert "matched" in result
        assert "results" in result
        assert result["source"] == "simulated"

    def test_simulated_company_lookup(self, temp_db):
        from server import _simulate_company_lookup
        result = _simulate_company_lookup("Test Corp")
        assert "found" in result
        assert result["source"] == "simulated"

    def test_full_screening_with_mocks(self, temp_db, mock_screening):
        from server import run_full_screening
        data = {"company_name": "Test Corp", "country": "Mauritius"}
        directors = [{"full_name": "John Smith", "nationality": "Mauritius"}]
        ubos = [{"full_name": "Jane Doe", "nationality": "UK", "ownership_pct": 51}]

        report = run_full_screening(data, directors, ubos, client_ip="127.0.0.1")
        assert report["total_hits"] == 0
        assert report["company_screening"]["found"] is True
        assert len(report["director_screenings"]) == 1
        assert len(report["ubo_screenings"]) == 1
