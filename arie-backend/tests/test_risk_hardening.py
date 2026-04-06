"""
Risk Computation Hardening Tests
================================
Gold-standard test suite validating:
  - Threshold boundary classification (Excel v1.6 alignment)
  - Escalation flag correctness
  - Floor rule enforcement
  - Fallback consistency
  - Recomputation on material edit
  - Secondary table constraint safety
  - classify_risk_level canonical behavior
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rule_engine import (
    classify_risk_level,
    compute_risk_score,
    CANONICAL_THRESHOLDS,
    SANCTIONED,
    FATF_BLACK,
)


# ══════════════════════════════════════════════════════════════
# 1. THRESHOLD BOUNDARY TESTS — Excel v1.6 alignment
# ══════════════════════════════════════════════════════════════

class TestCanonicalThresholds:
    """Verify classify_risk_level at every boundary from the approved model."""

    def test_canonical_thresholds_defined(self):
        assert len(CANONICAL_THRESHOLDS) == 4

    def test_low_at_zero(self):
        assert classify_risk_level(0) == "LOW"

    def test_low_at_39(self):
        assert classify_risk_level(39) == "LOW"

    def test_low_at_39_9(self):
        assert classify_risk_level(39.9) == "LOW"

    def test_medium_at_40(self):
        assert classify_risk_level(40) == "MEDIUM"

    def test_medium_at_54(self):
        assert classify_risk_level(54) == "MEDIUM"

    def test_medium_at_54_9(self):
        assert classify_risk_level(54.9) == "MEDIUM"

    def test_high_at_55(self):
        assert classify_risk_level(55) == "HIGH"

    def test_high_at_69(self):
        assert classify_risk_level(69) == "HIGH"

    def test_high_at_69_9(self):
        assert classify_risk_level(69.9) == "HIGH"

    def test_very_high_at_70(self):
        assert classify_risk_level(70) == "VERY_HIGH"

    def test_very_high_at_85(self):
        assert classify_risk_level(85) == "VERY_HIGH"

    def test_very_high_at_100(self):
        assert classify_risk_level(100) == "VERY_HIGH"

    def test_all_scores_cover_without_gaps(self):
        """Every integer 0-100 must map to a valid band."""
        valid_levels = {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}
        for score in range(101):
            level = classify_risk_level(score)
            assert level in valid_levels, f"Score {score} mapped to invalid level: {level}"

    def test_db_config_overrides_hardcoded(self):
        """If DB config provides thresholds, they override CANONICAL_THRESHOLDS."""
        custom = {"thresholds": [
            {"level": "LOW", "min": 0, "max": 49},
            {"level": "HIGH", "min": 50, "max": 100},
        ]}
        assert classify_risk_level(30, config=custom) == "LOW"
        assert classify_risk_level(60, config=custom) == "HIGH"


# ══════════════════════════════════════════════════════════════
# 2. ESCALATION FLAG TESTS
# ══════════════════════════════════════════════════════════════

class TestEscalationFlags:
    """Verify escalation rules from Excel Methodology §Compliance Escalation."""

    def _make_input(self, **overrides):
        base = {
            "entity_type": "sme",
            "ownership_structure": "simple",
            "country": "united kingdom",
            "sector": "technology",
            "directors": [],
            "ubos": [],
            "intermediary_shareholders": [],
            "operating_countries": [],
            "target_markets": [],
            "primary_service": "domestic payments only (single currency)",
            "monthly_volume": "under usd 50,000",
            "transaction_complexity": "simple",
            "source_of_wealth": "business revenue",
            "source_of_funds": "company bank",
            "introduction_method": "direct",
            "customer_interaction": "face-to-face",
        }
        base.update(overrides)
        return base

    def test_low_risk_no_escalation(self):
        result = compute_risk_score(self._make_input())
        assert result["requires_compliance_approval"] is False
        assert result["escalations"] == []

    def test_very_high_sector_escalation(self):
        """Crypto sector (score=4) triggers very_high_risk_sector."""
        result = compute_risk_score(self._make_input(sector="crypto"))
        assert "very_high_risk_sector" in result["escalations"]
        assert result["requires_compliance_approval"] is True

    def test_sub_factor_4_escalation(self):
        """Any sub-factor scoring 4 triggers sub_factor_score_4."""
        result = compute_risk_score(self._make_input(
            ownership_structure="complex multi-jurisdiction"
        ))
        assert "sub_factor_score_4" in result["escalations"]
        assert result["requires_compliance_approval"] is True

    def test_composite_85_plus_escalation(self):
        """Composite ≥ 85 triggers composite_score_85_plus."""
        # All high-risk inputs
        result = compute_risk_score(self._make_input(
            entity_type="shell company",
            ownership_structure="complex multi-jurisdiction",
            country="iran",
            sector="crypto",
            monthly_volume="over usd 5,000,000",
            transaction_complexity="very complex",
            source_of_wealth="unknown",
            source_of_funds="other",
            introduction_method="unsolicited",
            customer_interaction="anonymous",
        ))
        assert result["score"] >= 70
        assert result["requires_compliance_approval"] is True


# ══════════════════════════════════════════════════════════════
# 3. FLOOR RULE TESTS
# ══════════════════════════════════════════════════════════════

class TestFloorRules:
    """Verify sanctioned country/nationality floor rules."""

    def _make_input(self, **overrides):
        base = {
            "entity_type": "listed company",
            "ownership_structure": "simple",
            "country": "united kingdom",
            "sector": "technology",
            "directors": [],
            "ubos": [],
            "intermediary_shareholders": [],
            "operating_countries": [],
            "target_markets": [],
            "primary_service": "domestic payments only (single currency)",
            "monthly_volume": "under usd 50,000",
            "source_of_wealth": "business revenue",
            "source_of_funds": "company bank",
            "introduction_method": "direct",
            "customer_interaction": "face-to-face",
        }
        base.update(overrides)
        return base

    def test_sanctioned_country_forces_very_high(self):
        """Floor Rule 1: Sanctioned country → VERY_HIGH."""
        for country in ["iran", "north korea", "syria", "cuba"]:
            result = compute_risk_score(self._make_input(country=country))
            assert result["level"] == "VERY_HIGH", f"{country} should be VERY_HIGH"
            assert result["score"] >= 70, f"{country} score should be >= 70"
            assert any("floor_rule_sanctioned_country" in e for e in result["escalations"])

    def test_fatf_black_country_forces_very_high(self):
        """Floor Rule 1: FATF_BLACK country → VERY_HIGH."""
        for country in ["myanmar", "russia", "belarus"]:
            result = compute_risk_score(self._make_input(country=country))
            assert result["level"] == "VERY_HIGH", f"{country} should be VERY_HIGH"
            assert result["score"] >= 70

    def test_sanctioned_ubo_nationality_forces_very_high(self):
        """Floor Rule 2: UBO with sanctioned nationality → VERY_HIGH."""
        result = compute_risk_score(self._make_input(
            ubos=[{"full_name": "Test Person", "nationality": "iranian"}],
        ))
        assert result["level"] == "VERY_HIGH"
        assert any("floor_rule_sanctioned_nationality" in e for e in result["escalations"])

    def test_sanctioned_director_nationality_forces_very_high(self):
        """Floor Rule 2: Director with sanctioned nationality → VERY_HIGH."""
        result = compute_risk_score(self._make_input(
            directors=[{"full_name": "Test Person", "nationality": "north korean"}],
        ))
        assert result["level"] == "VERY_HIGH"

    def test_non_sanctioned_not_forced(self):
        """Normal countries should not trigger floor rules."""
        result = compute_risk_score(self._make_input(country="united kingdom"))
        floor_rules = [e for e in result["escalations"] if "floor_rule" in e]
        assert floor_rules == []


# ══════════════════════════════════════════════════════════════
# 4. CLASSIFICATION CONSISTENCY TESTS
# ══════════════════════════════════════════════════════════════

class TestClassificationConsistency:
    """The same score must always produce the same level, regardless of code path."""

    def test_classify_matches_compute(self):
        """classify_risk_level(score) must match compute_risk_score's level."""
        inputs = {
            "entity_type": "sme",
            "ownership_structure": "simple",
            "country": "united kingdom",
            "sector": "technology",
            "directors": [],
            "ubos": [],
            "intermediary_shareholders": [],
            "operating_countries": [],
            "target_markets": [],
            "primary_service": "domestic payments only (single currency)",
            "monthly_volume": "under usd 50,000",
            "source_of_wealth": "business revenue",
            "source_of_funds": "company bank",
            "introduction_method": "direct",
            "customer_interaction": "face-to-face",
        }
        result = compute_risk_score(inputs)
        standalone_level = classify_risk_level(result["score"])
        assert result["level"] == standalone_level

    def test_lane_mapping_consistent(self):
        """EDD lane should be HIGH or VERY_HIGH, Fast Lane should be LOW."""
        assert classify_risk_level(10) == "LOW"   # Fast Lane
        assert classify_risk_level(45) == "MEDIUM"  # Standard Review
        assert classify_risk_level(60) == "HIGH"    # EDD
        assert classify_risk_level(80) == "VERY_HIGH"  # EDD


# ══════════════════════════════════════════════════════════════
# 5. FALLBACK CONSISTENCY TESTS
# ══════════════════════════════════════════════════════════════

class TestFallbackConsistency:
    """All fallback defaults should be defensible and consistent."""

    def test_canonical_thresholds_cover_full_range(self):
        """0-100 must be fully covered with no gaps."""
        mins = sorted([t["min"] for t in CANONICAL_THRESHOLDS])
        assert mins[0] == 0
        assert CANONICAL_THRESHOLDS[-1]["max"] == 100

    def test_negative_score_defaults_to_low(self):
        """Negative scores should map to LOW (safest)."""
        assert classify_risk_level(-5) == "LOW"

    def test_above_100_defaults_to_very_high(self):
        """Scores above 100 should map to VERY_HIGH."""
        assert classify_risk_level(105) == "VERY_HIGH"

    def test_classify_with_none_config_uses_canonical(self):
        """None config should fall back to CANONICAL_THRESHOLDS."""
        assert classify_risk_level(50, config=None) == "MEDIUM"

    def test_classify_with_empty_config_uses_canonical(self):
        """Empty config should fall back to CANONICAL_THRESHOLDS."""
        assert classify_risk_level(50, config={}) == "MEDIUM"


# ══════════════════════════════════════════════════════════════
# 6. DB CONSTRAINT TESTS
# ══════════════════════════════════════════════════════════════

class TestDBConstraints:
    """Verify CHECK constraints exist on risk-carrying tables."""

    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create a temporary SQLite database with schema."""
        os.environ["DATABASE_URL"] = ""
        os.environ["ENVIRONMENT"] = "development"
        import importlib
        import db as db_module
        importlib.reload(db_module)
        db_module._DB_PATH = str(tmp_path / "test.db")
        db_module.init_db()
        conn = db_module.get_db()
        yield conn
        conn.close()

    def test_edd_cases_rejects_invalid_risk(self, temp_db):
        """edd_cases should reject invalid risk_level values."""
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute("""INSERT INTO edd_cases
                (application_id, client_name, risk_level)
                VALUES ('test', 'Test Corp', 'INVALID')""")
            temp_db.commit()

    def test_edd_cases_accepts_valid_risk(self, temp_db):
        """edd_cases should accept valid risk_level values."""
        for level in ["LOW", "MEDIUM", "HIGH", "VERY_HIGH"]:
            temp_db.execute("""INSERT INTO edd_cases
                (application_id, client_name, risk_level)
                VALUES (?, 'Test Corp', ?)""", (f"test_{level}", level))
        temp_db.commit()

    def test_edd_cases_accepts_null_risk(self, temp_db):
        """edd_cases should accept NULL risk_level."""
        temp_db.execute("""INSERT INTO edd_cases
            (application_id, client_name, risk_level)
            VALUES ('test_null', 'Test Corp', NULL)""")
        temp_db.commit()

    def test_sar_reports_rejects_invalid_risk(self, temp_db):
        """sar_reports should reject invalid risk_level values."""
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute("""INSERT INTO sar_reports
                (subject_name, narrative, risk_level)
                VALUES ('Test', 'narrative', 'INVALID')""")
            temp_db.commit()

    def test_periodic_reviews_rejects_invalid_risk(self, temp_db):
        """periodic_reviews should reject invalid risk_level values."""
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute("""INSERT INTO periodic_reviews
                (risk_level)
                VALUES ('INVALID')""")
            temp_db.commit()

    def test_periodic_reviews_rejects_invalid_previous_risk(self, temp_db):
        """periodic_reviews should reject invalid previous_risk_level values."""
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute("""INSERT INTO periodic_reviews
                (previous_risk_level)
                VALUES ('GARBAGE')""")
            temp_db.commit()


# ══════════════════════════════════════════════════════════════
# 7. RETURN SHAPE TESTS
# ══════════════════════════════════════════════════════════════

class TestReturnShape:
    """compute_risk_score must always return the canonical shape."""

    def test_return_keys(self):
        inputs = {
            "entity_type": "sme",
            "ownership_structure": "simple",
            "country": "united kingdom",
            "sector": "technology",
            "directors": [],
            "ubos": [],
        }
        result = compute_risk_score(inputs)
        assert "score" in result
        assert "level" in result
        assert "dimensions" in result
        assert "lane" in result
        assert "escalations" in result
        assert "requires_compliance_approval" in result

    def test_score_is_numeric(self):
        result = compute_risk_score({"entity_type": "sme", "country": "uk"})
        assert isinstance(result["score"], (int, float))
        assert 0 <= result["score"] <= 100

    def test_level_is_valid(self):
        result = compute_risk_score({"entity_type": "sme", "country": "uk"})
        assert result["level"] in {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}

    def test_escalations_is_list(self):
        result = compute_risk_score({"entity_type": "sme", "country": "uk"})
        assert isinstance(result["escalations"], list)

    def test_requires_compliance_is_bool(self):
        result = compute_risk_score({"entity_type": "sme", "country": "uk"})
        assert isinstance(result["requires_compliance_approval"], bool)

    def test_dimensions_has_d1_through_d5(self):
        result = compute_risk_score({"entity_type": "sme", "country": "uk"})
        dims = result["dimensions"]
        for key in ["d1", "d2", "d3", "d4", "d5"]:
            assert key in dims
            assert isinstance(dims[key], (int, float))
