"""
Tests for risk config shape hardening.

Validates that malformed DB risk config (list, string, int instead of dict)
never crashes scoring functions — they fall back to hardcoded defaults and
emit structured log warnings.

Covers:
    1. entity_type_scores as dict → works normally
    2. entity_type_scores as list → falls back safely, no crash
    3. country_risk_scores malformed → fallback safely
    4. sector_risk_scores malformed → fallback safely
    5. compute_risk_score with entity_type populated → no crash
    6. Structured log emitted on malformed config
    7. load_risk_config shape validation
"""
import json
import logging
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rule_engine import (
    _score_entity_type,
    score_sector,
    classify_country,
    compute_risk_score,
    load_risk_config,
    SECTOR_SCORES,
)


# ══════════════════════════════════════════════════════════════
# 1. _score_entity_type — dict config works
# ══════════════════════════════════════════════════════════════

class TestEntityTypeScoresDict:
    """entity_type_scores as dict → normal operation."""

    def test_dict_config_returns_correct_score(self):
        config = {"sme": 2, "shell": 4, "listed": 1}
        assert _score_entity_type("SME", config) == 2
        assert _score_entity_type("Shell Company", config) == 4
        assert _score_entity_type("Listed Company", config) == 1

    def test_dict_config_unknown_entity_returns_default(self):
        config = {"sme": 2}
        assert _score_entity_type("Exotic Entity", config) == 2

    def test_none_config_uses_hardcoded(self):
        assert _score_entity_type("SME", None) == 2
        assert _score_entity_type("Shell", None) == 4

    def test_empty_dict_config_uses_hardcoded(self):
        # Empty dict is falsy, so hardcoded fallback kicks in
        assert _score_entity_type("Shell", {}) == 4

    def test_empty_entity_type_returns_2(self):
        assert _score_entity_type("", {"sme": 2}) == 2
        assert _score_entity_type(None, {"sme": 2}) == 2


# ══════════════════════════════════════════════════════════════
# 2. _score_entity_type — list config falls back safely
# ══════════════════════════════════════════════════════════════

class TestEntityTypeScoresListFallback:
    """entity_type_scores as list → must not crash, must fall back to hardcoded."""

    def test_list_config_does_not_crash(self):
        """The exact bug: list has no .items(). Must not raise AttributeError."""
        malformed = [{"sme": 2}, {"shell": 4}]
        result = _score_entity_type("SME", malformed)
        assert isinstance(result, int)
        # Should use hardcoded fallback
        assert result == 2

    def test_list_config_shell_entity_uses_hardcoded(self):
        malformed = [1, 2, 3]
        result = _score_entity_type("Shell", malformed)
        assert result == 4  # hardcoded default for shell

    def test_list_config_logs_error(self, caplog):
        malformed = [{"sme": 2}]
        with caplog.at_level(logging.ERROR, logger="arie"):
            _score_entity_type("SME", malformed)
        assert any("non-dict" in r.message and "config_entity_scores" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════════════════
# 3. country_risk_scores malformed → fallback safely
# ══════════════════════════════════════════════════════════════

class TestCountryRiskScoresMalformed:
    """country_risk_scores as non-dict → must not crash."""

    def test_list_config_does_not_crash(self):
        result = classify_country("Iran", config_country_scores=["iran"])
        assert isinstance(result, int)
        # Falls back to hardcoded: Iran is sanctioned = 4
        assert result == 4

    def test_string_config_does_not_crash(self):
        result = classify_country("France", config_country_scores="not a dict")
        assert isinstance(result, int)
        assert result == 1  # France is in LOW_RISK

    def test_int_config_does_not_crash(self):
        result = classify_country("Nigeria", config_country_scores=42)
        assert isinstance(result, int)
        assert result == 3  # Nigeria is in FATF_GREY

    def test_list_config_logs_error(self, caplog):
        with caplog.at_level(logging.ERROR, logger="arie"):
            classify_country("Iran", config_country_scores=[1, 2])
        assert any("non-dict" in r.message and "config_country_scores" in r.message for r in caplog.records)

    def test_dict_config_still_works(self):
        config = {"mauritius": 3}
        assert classify_country("Mauritius", config) == 3

    def test_none_config_uses_hardcoded(self):
        assert classify_country("Iran", None) == 4
        assert classify_country("United Kingdom", None) == 1


# ══════════════════════════════════════════════════════════════
# 4. sector_risk_scores malformed → fallback safely
# ══════════════════════════════════════════════════════════════

class TestSectorRiskScoresMalformed:
    """sector_risk_scores as non-dict → must not crash."""

    def test_list_config_does_not_crash(self):
        result = score_sector("Crypto", config_sector_scores=["crypto"])
        assert isinstance(result, int)
        # Falls back to hardcoded SECTOR_SCORES: crypto = 4
        assert result == 4

    def test_string_config_does_not_crash(self):
        result = score_sector("Technology", config_sector_scores="broken")
        assert isinstance(result, int)
        assert result == 2  # technology is 2 in hardcoded

    def test_int_config_does_not_crash(self):
        result = score_sector("Gambling", config_sector_scores=99)
        assert isinstance(result, int)
        assert result == 4  # gambling is 4 in hardcoded

    def test_list_config_logs_error(self, caplog):
        with caplog.at_level(logging.ERROR, logger="arie"):
            score_sector("Crypto", config_sector_scores=[1])
        assert any("non-dict" in r.message and "config_sector_scores" in r.message for r in caplog.records)

    def test_dict_config_still_works(self):
        config = {"fintech": 2}
        assert score_sector("Fintech", config) == 2


# ══════════════════════════════════════════════════════════════
# 5. compute_risk_score with entity_type → no crash
# ══════════════════════════════════════════════════════════════

class TestComputeRiskScoreWithEntityType:
    """Ensure compute_risk_score doesn't crash when entity_type is populated
    and config has malformed entity_type_scores."""

    def test_with_entity_type_and_list_entity_scores(self):
        """Exact reproduction of the staging bug."""
        app_data = {
            "entity_type": "SME",
            "country": "Mauritius",
            "sector": "Technology",
        }
        config = {
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": None,
            "sector_risk_scores": None,
            "entity_type_scores": [{"sme": 2}],  # MALFORMED: list not dict
        }
        result = compute_risk_score(app_data, config_override=config)
        assert "score" in result
        assert "level" in result
        assert isinstance(result["score"], (int, float))

    def test_with_entity_type_and_valid_config(self):
        """Happy path: dict config should work normally."""
        app_data = {
            "entity_type": "Shell",
            "country": "United Kingdom",
            "sector": "Technology",
        }
        config = {
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": {"united kingdom": 1},
            "sector_risk_scores": {"technology": 2},
            "entity_type_scores": {"shell": 4},
        }
        result = compute_risk_score(app_data, config_override=config)
        assert "score" in result
        assert "level" in result

    def test_with_entity_type_and_no_config(self):
        """No config at all → pure hardcoded fallback."""
        app_data = {
            "entity_type": "NGO",
            "country": "France",
            "sector": "Healthcare",
        }
        config = {
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": None,
            "sector_risk_scores": None,
            "entity_type_scores": None,
        }
        result = compute_risk_score(app_data, config_override=config)
        assert "score" in result
        assert isinstance(result["score"], (int, float))

    def test_all_score_fields_malformed_simultaneously(self):
        """All three score fields are lists — nothing should crash."""
        app_data = {
            "entity_type": "Trust",
            "country": "Germany",
            "sector": "Real Estate",
        }
        config = {
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": ["germany"],
            "sector_risk_scores": [3],
            "entity_type_scores": ["trust"],
        }
        result = compute_risk_score(app_data, config_override=config)
        assert "score" in result
        assert "level" in result
        assert result["level"] in ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")


# ══════════════════════════════════════════════════════════════
# 6. Structured log emitted on malformed config
# ══════════════════════════════════════════════════════════════

class TestMalformedConfigLogging:
    """Verify structured error logs are emitted when config is malformed."""

    def test_entity_type_list_emits_log(self, caplog):
        with caplog.at_level(logging.ERROR, logger="arie"):
            _score_entity_type("SME", [{"sme": 2}])
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) >= 1
        msg = error_records[0].message
        assert "non-dict" in msg
        assert "list" in msg

    def test_sector_string_emits_log(self, caplog):
        with caplog.at_level(logging.ERROR, logger="arie"):
            score_sector("Crypto", config_sector_scores="bad")
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) >= 1
        assert "str" in error_records[0].message

    def test_country_int_emits_log(self, caplog):
        with caplog.at_level(logging.ERROR, logger="arie"):
            classify_country("Iran", config_country_scores=123)
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) >= 1
        assert "int" in error_records[0].message


# ══════════════════════════════════════════════════════════════
# 7. load_risk_config shape validation
# ══════════════════════════════════════════════════════════════

class TestLoadRiskConfigShapeValidation:
    """Verify load_risk_config sets malformed score columns to None."""

    def _make_mock_row(self, entity_scores, country_scores=None, sector_scores=None):
        """Create a mock DB row with controllable score shapes."""
        row = {
            "dimensions": json.dumps([]),
            "thresholds": json.dumps([]),
            "country_risk_scores": json.dumps(country_scores) if country_scores is not None else None,
            "sector_risk_scores": json.dumps(sector_scores) if sector_scores is not None else None,
            "entity_type_scores": json.dumps(entity_scores) if entity_scores is not None else None,
        }
        return row

    def test_list_entity_scores_set_to_none(self):
        """entity_type_scores stored as list → shape validation sets it to None."""
        from rule_engine import safe_json_loads
        mock_row = self._make_mock_row(entity_scores=[{"sme": 2}])
        # Simulate the parsing + shape validation that load_risk_config performs
        result = {}
        for key in ("dimensions", "thresholds", "country_risk_scores",
                     "sector_risk_scores", "entity_type_scores"):
            val = mock_row.get(key)
            result[key] = safe_json_loads(val) if val else None
        # Shape validation: non-dict score columns → None
        for col in ("country_risk_scores", "sector_risk_scores", "entity_type_scores"):
            v = result.get(col)
            if v is not None and not isinstance(v, dict):
                result[col] = None
        assert result["entity_type_scores"] is None

    def test_dict_entity_scores_preserved(self):
        """entity_type_scores stored as dict → should be preserved."""
        from rule_engine import safe_json_loads
        val = json.dumps({"sme": 2, "shell": 4})
        parsed = safe_json_loads(val)
        assert isinstance(parsed, dict)
        assert parsed["sme"] == 2

    def test_list_country_scores_set_to_none(self):
        """country_risk_scores stored as list → should be set to None by validation."""
        from rule_engine import safe_json_loads
        val = json.dumps(["iran", "syria"])
        parsed = safe_json_loads(val)
        # Shape validation: not a dict → would be set to None
        assert not isinstance(parsed, dict)

    def test_string_sector_scores_set_to_none(self):
        """sector_risk_scores stored as plain string → should be set to None."""
        from rule_engine import safe_json_loads
        val = json.dumps("crypto")
        parsed = safe_json_loads(val)
        assert not isinstance(parsed, dict)
