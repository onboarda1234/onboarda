"""
Tests for risk_config DB scoring configuration integrity hardening.

Covers:
    1. Seeded config shape test — all columns have correct canonical types
    2. Schema validation functions — validate_score_map, validate_dimensions, validate_thresholds
    3. Admin write/read roundtrip — PUT validates and GET returns canonical shapes
    4. Malformed config rejected by PUT endpoint
    5. List-of-dicts normalization — auto-repair at load time
    6. submit scoring using DB-driven config, not fallback
    7. Migration repair function — _repair_risk_config_shapes
    8. Full validate_risk_config integration
"""
import json
import logging
import os
import sys
import sqlite3
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rule_engine import (
    validate_risk_config,
    validate_score_map,
    validate_dimensions,
    validate_thresholds,
    _normalize_score_map,
    _score_entity_type,
    score_sector,
    classify_country,
    compute_risk_score,
    load_risk_config,
    safe_json_loads,
)


# ══════════════════════════════════════════════════════════
# 1. SEEDED CONFIG SHAPE TEST
# ══════════════════════════════════════════════════════════

class TestSeededConfigShape:
    """Verify DB seed produces canonical shapes for all risk_config columns."""

    def test_seeded_dimensions_is_list_of_dicts(self, temp_db):
        from db import get_db
        db = get_db()
        row = db.execute("SELECT dimensions FROM risk_config WHERE id=1").fetchone()
        db.close()
        dims = json.loads(row["dimensions"])
        assert isinstance(dims, list), f"dimensions should be list, got {type(dims).__name__}"
        assert len(dims) == 5, f"Expected 5 dimensions (D1-D5), got {len(dims)}"
        for dim in dims:
            assert isinstance(dim, dict), f"Each dimension should be dict, got {type(dim).__name__}"
            assert "id" in dim, "Dimension missing 'id'"
            assert "name" in dim, "Dimension missing 'name'"
            assert "weight" in dim, "Dimension missing 'weight'"
            assert "subcriteria" in dim, "Dimension missing 'subcriteria'"
            assert isinstance(dim["subcriteria"], list), "subcriteria should be list"

    def test_seeded_thresholds_is_list_of_bands(self, temp_db):
        from db import get_db
        db = get_db()
        row = db.execute("SELECT thresholds FROM risk_config WHERE id=1").fetchone()
        db.close()
        thresholds = json.loads(row["thresholds"])
        assert isinstance(thresholds, list), f"thresholds should be list, got {type(thresholds).__name__}"
        assert len(thresholds) == 4, f"Expected 4 thresholds, got {len(thresholds)}"
        levels = {t["level"] for t in thresholds}
        assert levels == {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}

    def test_seeded_country_risk_scores_is_dict(self, temp_db):
        from db import get_db
        db = get_db()
        row = db.execute("SELECT country_risk_scores FROM risk_config WHERE id=1").fetchone()
        db.close()
        scores = json.loads(row["country_risk_scores"])
        assert isinstance(scores, dict), f"country_risk_scores should be dict, got {type(scores).__name__}"
        assert len(scores) > 50, f"Expected 50+ country scores, got {len(scores)}"
        # Spot check known values
        assert scores.get("iran") == 4
        assert scores.get("united kingdom") == 1
        assert scores.get("mauritius") == 2

    def test_seeded_sector_risk_scores_is_dict(self, temp_db):
        from db import get_db
        db = get_db()
        row = db.execute("SELECT sector_risk_scores FROM risk_config WHERE id=1").fetchone()
        db.close()
        scores = json.loads(row["sector_risk_scores"])
        assert isinstance(scores, dict), f"sector_risk_scores should be dict, got {type(scores).__name__}"
        assert len(scores) > 20, f"Expected 20+ sector scores, got {len(scores)}"
        assert scores.get("crypto") == 4
        assert scores.get("technology") == 2

    def test_seeded_entity_type_scores_is_dict(self, temp_db):
        from db import get_db
        db = get_db()
        row = db.execute("SELECT entity_type_scores FROM risk_config WHERE id=1").fetchone()
        db.close()
        scores = json.loads(row["entity_type_scores"])
        assert isinstance(scores, dict), f"entity_type_scores should be dict, got {type(scores).__name__}"
        assert len(scores) > 10, f"Expected 10+ entity type scores, got {len(scores)}"
        assert scores.get("shell company") == 4
        assert scores.get("sme") == 2
        assert scores.get("listed company") == 1

    def test_seeded_config_passes_full_validation(self, temp_db):
        """The full seeded config should pass validate_risk_config with zero errors."""
        from db import get_db
        db = get_db()
        row = db.execute("SELECT * FROM risk_config WHERE id=1").fetchone()
        db.close()
        config = {}
        for key in ("dimensions", "thresholds", "country_risk_scores",
                     "sector_risk_scores", "entity_type_scores"):
            config[key] = safe_json_loads(row[key])
        validated, errors = validate_risk_config(config)
        assert errors == [], f"Seeded config should have no validation errors, got: {errors}"


# ══════════════════════════════════════════════════════════
# 2. SCHEMA VALIDATION FUNCTIONS
# ══════════════════════════════════════════════════════════

class TestValidateScoreMap:
    """validate_score_map unit tests."""

    def test_valid_dict(self):
        val, errors = validate_score_map({"iran": 4, "uk": 1}, "country_risk_scores")
        assert errors == []
        assert val == {"iran": 4, "uk": 1}

    def test_none_is_valid(self):
        val, errors = validate_score_map(None, "test")
        assert errors == []
        assert val is None

    def test_empty_dict_is_valid(self):
        val, errors = validate_score_map({}, "test")
        assert errors == []
        assert val == {}

    def test_list_of_dicts_normalized(self):
        """List-of-dicts should be normalized to flat dict."""
        val, errors = validate_score_map([{"sme": 2}, {"shell": 4}], "entity_type_scores")
        assert errors == []
        assert val == {"sme": 2, "shell": 4}

    def test_list_of_ints_rejected(self):
        """List of non-dict items cannot be normalized."""
        val, errors = validate_score_map([1, 2, 3], "test")
        assert len(errors) == 1
        assert "expected dict" in errors[0]
        assert val is None

    def test_string_rejected(self):
        val, errors = validate_score_map("not a dict", "test")
        assert len(errors) == 1
        assert val is None

    def test_int_rejected(self):
        val, errors = validate_score_map(42, "test")
        assert len(errors) == 1
        assert val is None

    def test_non_numeric_values_flagged(self):
        val, errors = validate_score_map({"sme": "high"}, "entity_type_scores")
        assert len(errors) == 1
        assert "int/float" in errors[0]


class TestValidateDimensions:
    """validate_dimensions unit tests."""

    def test_valid_dimensions(self):
        dims = [
            {"id": "D1", "name": "Test", "weight": 30, "subcriteria": [{"name": "X", "weight": 100}]},
        ]
        val, errors = validate_dimensions(dims)
        assert errors == []
        assert val == dims

    def test_none_is_valid(self):
        val, errors = validate_dimensions(None)
        assert errors == []
        assert val is None

    def test_not_list_rejected(self):
        val, errors = validate_dimensions({"D1": 30})
        assert len(errors) == 1
        assert "expected list" in errors[0]
        assert val is None

    def test_missing_required_keys(self):
        val, errors = validate_dimensions([{"name": "Test"}])
        assert any("missing required key 'id'" in e for e in errors)
        assert any("missing required key 'weight'" in e for e in errors)

    def test_invalid_weight_type(self):
        val, errors = validate_dimensions([{"id": "D1", "name": "Test", "weight": "thirty"}])
        assert any("expected number" in e for e in errors)

    def test_invalid_subcriteria_type(self):
        val, errors = validate_dimensions([{"id": "D1", "name": "Test", "weight": 30, "subcriteria": "bad"}])
        assert any("expected list" in e for e in errors)


class TestValidateThresholds:
    """validate_thresholds unit tests."""

    def test_valid_thresholds(self):
        thresh = [
            {"level": "LOW", "min": 0, "max": 39.9},
            {"level": "MEDIUM", "min": 40, "max": 54.9},
            {"level": "HIGH", "min": 55, "max": 69.9},
            {"level": "VERY_HIGH", "min": 70, "max": 100},
        ]
        val, errors = validate_thresholds(thresh)
        assert errors == []
        assert val == thresh

    def test_none_is_valid(self):
        val, errors = validate_thresholds(None)
        assert errors == []
        assert val is None

    def test_not_list_rejected(self):
        val, errors = validate_thresholds("bad")
        assert len(errors) == 1
        assert "expected list" in errors[0]
        assert val is None

    def test_missing_level(self):
        thresh = [{"level": "LOW", "min": 0, "max": 39.9}]
        val, errors = validate_thresholds(thresh)
        assert any("missing levels" in e for e in errors)


class TestNormalizeScoreMap:
    """_normalize_score_map unit tests."""

    def test_dict_passthrough(self):
        assert _normalize_score_map({"a": 1}) == {"a": 1}

    def test_list_of_dicts_merged(self):
        assert _normalize_score_map([{"a": 1}, {"b": 2}]) == {"a": 1, "b": 2}

    def test_list_of_ints_returns_none(self):
        assert _normalize_score_map([1, 2, 3]) is None

    def test_empty_list_returns_none(self):
        assert _normalize_score_map([]) is None

    def test_string_returns_none(self):
        assert _normalize_score_map("bad") is None

    def test_none_returns_none(self):
        assert _normalize_score_map(None) is None

    def test_mixed_list_returns_none(self):
        """List with both dicts and non-dicts should return None."""
        assert _normalize_score_map([{"a": 1}, 42]) is None


# ══════════════════════════════════════════════════════════
# 3. ADMIN WRITE/READ ROUNDTRIP SHAPE TEST
# ══════════════════════════════════════════════════════════

class TestAdminWriteReadRoundtrip:
    """Verify PUT + GET roundtrip preserves canonical shapes."""

    def test_valid_config_roundtrip(self, temp_db):
        """PUT valid config → GET should return same canonical shapes."""
        from db import get_db
        config = {
            "dimensions": [
                {"id": "D1", "name": "Customer Risk", "weight": 30,
                 "subcriteria": [{"name": "Entity Type", "weight": 100}]},
            ],
            "thresholds": [
                {"level": "LOW", "min": 0, "max": 39.9},
                {"level": "MEDIUM", "min": 40, "max": 54.9},
                {"level": "HIGH", "min": 55, "max": 69.9},
                {"level": "VERY_HIGH", "min": 70, "max": 100},
            ],
            "country_risk_scores": {"iran": 4, "uk": 1},
            "sector_risk_scores": {"crypto": 4, "tech": 2},
            "entity_type_scores": {"shell": 4, "sme": 2},
        }
        # Simulate PUT by writing directly
        db = get_db()
        validated, errors = validate_risk_config(config)
        assert errors == []
        db.execute(
            "UPDATE risk_config SET dimensions=?, thresholds=?, "
            "country_risk_scores=?, sector_risk_scores=?, entity_type_scores=? WHERE id=1",
            (json.dumps(validated["dimensions"]),
             json.dumps(validated["thresholds"]),
             json.dumps(validated["country_risk_scores"]),
             json.dumps(validated["sector_risk_scores"]),
             json.dumps(validated["entity_type_scores"])),
        )
        db.commit()

        # Read back
        row = db.execute("SELECT * FROM risk_config WHERE id=1").fetchone()
        db.close()
        for col in ("country_risk_scores", "sector_risk_scores", "entity_type_scores"):
            parsed = json.loads(row[col])
            assert isinstance(parsed, dict), f"{col} should be dict after roundtrip"
        dims = json.loads(row["dimensions"])
        assert isinstance(dims, list)
        thresh = json.loads(row["thresholds"])
        assert isinstance(thresh, list)


# ══════════════════════════════════════════════════════════
# 4. MALFORMED CONFIG REJECTED BY VALIDATION
# ══════════════════════════════════════════════════════════

class TestMalformedConfigRejected:
    """Malformed config should produce validation errors."""

    def test_entity_scores_as_list_of_ints_rejected(self):
        config = {
            "dimensions": None,
            "thresholds": None,
            "entity_type_scores": [1, 2, 3],  # Not normalizable
        }
        validated, errors = validate_risk_config(config)
        assert len(errors) >= 1
        assert any("entity_type_scores" in e for e in errors)
        assert validated["entity_type_scores"] is None

    def test_country_scores_as_string_rejected(self):
        config = {
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": "not a dict",
        }
        validated, errors = validate_risk_config(config)
        assert len(errors) >= 1
        assert any("country_risk_scores" in e for e in errors)
        assert validated["country_risk_scores"] is None

    def test_dimensions_as_dict_rejected(self):
        config = {
            "dimensions": {"D1": 30},  # Should be a list
            "thresholds": None,
        }
        validated, errors = validate_risk_config(config)
        assert any("dimensions" in e and "expected list" in e for e in errors)
        assert validated["dimensions"] is None

    def test_thresholds_as_string_rejected(self):
        config = {
            "dimensions": None,
            "thresholds": "bad",
        }
        validated, errors = validate_risk_config(config)
        assert any("thresholds" in e and "expected list" in e for e in errors)
        assert validated["thresholds"] is None

    def test_list_of_dicts_entity_scores_normalized_not_rejected(self):
        """List-of-dicts should be auto-normalized, not rejected."""
        config = {
            "dimensions": None,
            "thresholds": None,
            "entity_type_scores": [{"sme": 2}, {"shell": 4}],
        }
        validated, errors = validate_risk_config(config)
        assert errors == []  # Normalization succeeds — no errors
        assert validated["entity_type_scores"] == {"sme": 2, "shell": 4}


# ══════════════════════════════════════════════════════════
# 5. LIST-OF-DICTS NORMALIZATION AT LOAD TIME
# ══════════════════════════════════════════════════════════

class TestListOfDictsNormalization:
    """Verify load_risk_config normalizes list-of-dicts → flat dict."""

    def test_load_normalizes_list_of_dicts_entity_scores(self, temp_db):
        """If DB has entity_type_scores as list-of-dicts, load should normalize to dict."""
        from db import get_db
        db = get_db()
        # Corrupt the entity_type_scores to list-of-dicts
        malformed = json.dumps([{"sme": 2}, {"shell": 4}])
        db.execute("UPDATE risk_config SET entity_type_scores=? WHERE id=1", (malformed,))
        db.commit()
        db.close()

        config = load_risk_config()
        assert config is not None
        assert config["entity_type_scores"] == {"sme": 2, "shell": 4}

    def test_load_sets_non_normalizable_to_none(self, temp_db):
        """If DB has entity_type_scores as plain list of ints, load should set to None."""
        from db import get_db
        db = get_db()
        malformed = json.dumps([1, 2, 3])
        db.execute("UPDATE risk_config SET entity_type_scores=? WHERE id=1", (malformed,))
        db.commit()
        db.close()

        config = load_risk_config()
        assert config is not None
        assert config["entity_type_scores"] is None  # Fell back to None


# ══════════════════════════════════════════════════════════
# 6. SUBMIT SCORING USES DB-DRIVEN CONFIG, NOT FALLBACK
# ══════════════════════════════════════════════════════════

class TestDBDrivenScoring:
    """Verify that compute_risk_score uses DB config, not hardcoded fallback."""

    def test_compute_uses_db_country_scores(self, temp_db):
        """DB config should override hardcoded country scores."""
        # Set Mauritius to score 4 (normally 2 in hardcoded)
        config = {
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": {"mauritius": 4, "france": 1},
            "sector_risk_scores": {"technology": 2},
            "entity_type_scores": {"sme": 2},
        }
        app_data = {"country": "Mauritius", "sector": "Technology", "entity_type": "SME"}
        result = compute_risk_score(app_data, config_override=config)
        assert result["score"] > 0
        # D2 should use score 4 for Mauritius (not hardcoded 2)
        assert result["dimensions"]["d2"] > 2.5  # Should be elevated due to high country score

    def test_compute_uses_db_entity_scores(self, temp_db):
        """DB entity scores should be used when available."""
        config = {
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": None,
            "sector_risk_scores": None,
            "entity_type_scores": {"sme": 4},  # Override: SME is now very high risk
        }
        app_data = {"entity_type": "SME", "country": "France", "sector": "Technology"}
        result = compute_risk_score(app_data, config_override=config)
        # D1 entity score should be 4 (not hardcoded 2)
        assert result["dimensions"]["d1"] > 2.0

    def test_db_config_produces_different_score_than_fallback(self, temp_db):
        """Config-driven scoring should produce a measurably different score than defaults."""
        app_data = {"entity_type": "SME", "country": "Mauritius", "sector": "Technology"}

        # Score with fallback (None config)
        fallback_config = {
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": None,
            "sector_risk_scores": None,
            "entity_type_scores": None,
        }
        fallback_result = compute_risk_score(app_data, config_override=fallback_config)

        # Score with custom config (different scores)
        custom_config = {
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": {"mauritius": 4},  # Override: much higher
            "sector_risk_scores": {"technology": 4},   # Override: much higher
            "entity_type_scores": {"sme": 4},          # Override: much higher
        }
        custom_result = compute_risk_score(app_data, config_override=custom_config)

        # Custom should produce a higher score (all overridden to 4)
        assert custom_result["score"] > fallback_result["score"], \
            f"Custom config score ({custom_result['score']}) should be higher than fallback ({fallback_result['score']})"

    def test_full_db_config_round_trip_scoring(self, temp_db):
        """Write config to DB, load it, score with it — end to end."""
        from db import get_db
        db = get_db()
        # Write custom config
        custom_entity = json.dumps({"sme": 3, "shell": 4, "listed": 1})
        custom_country = json.dumps({"mauritius": 3, "france": 1})
        custom_sector = json.dumps({"technology": 3, "crypto": 4})
        db.execute(
            "UPDATE risk_config SET entity_type_scores=?, country_risk_scores=?, sector_risk_scores=? WHERE id=1",
            (custom_entity, custom_country, custom_sector),
        )
        db.commit()
        db.close()

        # Load config from DB
        config = load_risk_config()
        assert config is not None
        assert config["entity_type_scores"] == {"sme": 3, "shell": 4, "listed": 1}
        assert config["country_risk_scores"]["mauritius"] == 3
        assert config["sector_risk_scores"]["technology"] == 3

        # Score with loaded config
        app_data = {"entity_type": "SME", "country": "Mauritius", "sector": "Technology"}
        result = compute_risk_score(app_data, config_override=config)
        assert "score" in result
        assert "level" in result


# ══════════════════════════════════════════════════════════
# 7. MIGRATION REPAIR FUNCTION
# ══════════════════════════════════════════════════════════

class TestRepairMigration:
    """Test _repair_risk_config_shapes migration function."""

    def test_repair_list_of_dicts(self, temp_db):
        """List-of-dicts in DB should be repaired to flat dict."""
        from db import get_db, _repair_risk_config_shapes
        db = get_db()
        # Corrupt entity_type_scores
        malformed = json.dumps([{"sme": 2}, {"shell": 4}])
        db.execute("UPDATE risk_config SET entity_type_scores=? WHERE id=1", (malformed,))
        db.commit()

        # Run repair
        _repair_risk_config_shapes(db)

        # Verify repair
        row = db.execute("SELECT entity_type_scores FROM risk_config WHERE id=1").fetchone()
        db.close()
        parsed = json.loads(row["entity_type_scores"])
        assert isinstance(parsed, dict), f"Should be dict after repair, got {type(parsed).__name__}"
        assert parsed == {"sme": 2, "shell": 4}

    def test_repair_all_three_columns(self, temp_db):
        """All three score columns can be repaired in one pass."""
        from db import get_db, _repair_risk_config_shapes
        db = get_db()
        db.execute(
            "UPDATE risk_config SET country_risk_scores=?, sector_risk_scores=?, entity_type_scores=? WHERE id=1",
            (
                json.dumps([{"iran": 4}, {"uk": 1}]),
                json.dumps([{"crypto": 4}]),
                json.dumps([{"sme": 2}]),
            ),
        )
        db.commit()

        _repair_risk_config_shapes(db)

        row = db.execute("SELECT * FROM risk_config WHERE id=1").fetchone()
        db.close()
        assert json.loads(row["country_risk_scores"]) == {"iran": 4, "uk": 1}
        assert json.loads(row["sector_risk_scores"]) == {"crypto": 4}
        assert json.loads(row["entity_type_scores"]) == {"sme": 2}

    def test_repair_leaves_valid_data_untouched(self, temp_db):
        """Already-valid dict data should not be modified."""
        from db import get_db, _repair_risk_config_shapes
        db = get_db()
        original = {"iran": 4, "france": 1, "mauritius": 2}
        db.execute(
            "UPDATE risk_config SET country_risk_scores=? WHERE id=1",
            (json.dumps(original),),
        )
        db.commit()

        _repair_risk_config_shapes(db)

        row = db.execute("SELECT country_risk_scores FROM risk_config WHERE id=1").fetchone()
        db.close()
        parsed = json.loads(row["country_risk_scores"])
        assert parsed == original

    def test_repair_resets_unparsable_data(self, temp_db):
        """Completely unparsable data should be reset to empty."""
        from db import get_db, _repair_risk_config_shapes
        db = get_db()
        db.execute(
            "UPDATE risk_config SET entity_type_scores=? WHERE id=1",
            ("this is not json at all {{{{",),
        )
        db.commit()

        _repair_risk_config_shapes(db)

        row = db.execute("SELECT entity_type_scores FROM risk_config WHERE id=1").fetchone()
        db.close()
        assert row["entity_type_scores"] == '{}'

    def test_repair_no_row_does_not_crash(self, temp_db):
        """Repair on empty table should not crash."""
        from db import get_db, _repair_risk_config_shapes, seed_initial_data
        db = get_db()
        db.execute("DELETE FROM risk_config")
        db.commit()
        # Should not raise
        _repair_risk_config_shapes(db)
        # Restore the seeded row so subsequent tests are not affected
        seed_initial_data(db)
        db.commit()
        db.close()


# ══════════════════════════════════════════════════════════
# 8. FULL validate_risk_config INTEGRATION
# ══════════════════════════════════════════════════════════

class TestFullValidateRiskConfig:
    """Integration tests for validate_risk_config."""

    def test_fully_valid_config(self):
        config = {
            "dimensions": [
                {"id": "D1", "name": "Test", "weight": 30,
                 "subcriteria": [{"name": "Sub1", "weight": 100}]},
            ],
            "thresholds": [
                {"level": "LOW", "min": 0, "max": 39.9},
                {"level": "MEDIUM", "min": 40, "max": 54.9},
                {"level": "HIGH", "min": 55, "max": 69.9},
                {"level": "VERY_HIGH", "min": 70, "max": 100},
            ],
            "country_risk_scores": {"iran": 4},
            "sector_risk_scores": {"crypto": 4},
            "entity_type_scores": {"shell": 4},
        }
        validated, errors = validate_risk_config(config)
        assert errors == []
        assert validated["country_risk_scores"] == {"iran": 4}

    def test_all_none_is_valid(self):
        config = {
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": None,
            "sector_risk_scores": None,
            "entity_type_scores": None,
        }
        validated, errors = validate_risk_config(config)
        assert errors == []

    def test_mixed_malformed_and_valid(self):
        config = {
            "dimensions": [{"id": "D1", "name": "Test", "weight": 30}],
            "thresholds": None,
            "country_risk_scores": {"iran": 4},  # valid
            "sector_risk_scores": "bad",  # invalid
            "entity_type_scores": [{"sme": 2}],  # normalizable
        }
        validated, errors = validate_risk_config(config)
        # sector_risk_scores should have an error
        assert any("sector_risk_scores" in e for e in errors)
        assert validated["sector_risk_scores"] is None
        # entity_type_scores should be normalized (no error)
        assert validated["entity_type_scores"] == {"sme": 2}
        # country_risk_scores should be preserved
        assert validated["country_risk_scores"] == {"iran": 4}

    def test_empty_config(self):
        validated, errors = validate_risk_config({})
        assert errors == []

    def test_none_config(self):
        validated, errors = validate_risk_config(None)
        assert errors == []
