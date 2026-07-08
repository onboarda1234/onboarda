"""P12-3 / DCI-008 — risk-config load failure fails CLOSED in staging/production.

Pre-fix, `load_risk_config()` swallowed every failure (DB unreachable, missing
risk_config row, malformed score maps) and returned None, so regulated scoring
silently fell back to the hardcoded default model — which may not match the
approved live risk model. Post-fix, in staging/production those conditions
raise RiskConfigUnavailable and the decision path aborts (surfaced as 503 by
the submit / memo-generation handlers). Dev/test/demo keep the historical
fallback so local work and this test suite run without a seeded row.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

import db as db_module
import rule_engine
from rule_engine import RiskConfigUnavailable, load_risk_config


class _FakeDB:
    """Stands in for get_db(): returns a canned risk_config row (or raises)."""

    def __init__(self, row=None, raise_exc=None):
        self._row = row
        self._raise = raise_exc
        self.closed = False

    def execute(self, sql, params=None):
        if self._raise is not None:
            raise self._raise
        return self

    def fetchone(self):
        return self._row

    def close(self):
        self.closed = True


def _use_fake_db(monkeypatch, fake):
    monkeypatch.setattr(db_module, "get_db", lambda: fake)


_MALFORMED_ROW = {
    "dimensions": None,
    "thresholds": None,
    # scalar instead of a dict → validate_score_map reports an error
    "country_risk_scores": "5",
    "sector_risk_scores": None,
    "entity_type_scores": None,
}

_HEALTHY_ROW = {
    "dimensions": None,
    "thresholds": None,
    "country_risk_scores": '{"mauritius": 1, "iran": 4}',
    "sector_risk_scores": '{"technology": 1}',
    "entity_type_scores": '{"sme": 2}',
}


# ══════════════════════════════════════════════════════════
# Fail-closed environments: staging / production
# ══════════════════════════════════════════════════════════

class TestFailClosedEnvironments:
    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_db_failure_raises(self, monkeypatch, env):
        """DB unreachable → RiskConfigUnavailable, never a silent None fallback."""
        monkeypatch.setenv("ENVIRONMENT", env)
        _use_fake_db(monkeypatch, _FakeDB(raise_exc=RuntimeError("db down")))
        with pytest.raises(RiskConfigUnavailable):
            load_risk_config()

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_missing_row_raises(self, monkeypatch, env):
        """No risk_config row seeded → the live model is absent → fail closed."""
        monkeypatch.setenv("ENVIRONMENT", env)
        _use_fake_db(monkeypatch, _FakeDB(row=None))
        with pytest.raises(RiskConfigUnavailable, match="missing"):
            load_risk_config()

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_malformed_score_map_raises(self, monkeypatch, env):
        """A malformed score map (scalar where a dict is required) → fail closed
        instead of silently nulling the column and scoring on hardcoded lists."""
        monkeypatch.setenv("ENVIRONMENT", env)
        _use_fake_db(monkeypatch, _FakeDB(row=dict(_MALFORMED_ROW)))
        with pytest.raises(RiskConfigUnavailable, match="validation"):
            load_risk_config()

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_malformed_jsonb_scalar_raises(self, monkeypatch, env):
        """PG/JSONB shape: psycopg2 returns columns ALREADY parsed, so a
        malformed scalar arrives as int 5 (not the string '5'). Pre-fix,
        safe_json_loads() coerced that to {} and the validator never saw it —
        the fail-closed gate was silently defeated on exactly the PostgreSQL
        environments it protects. Caught by the live-PG probe."""
        monkeypatch.setenv("ENVIRONMENT", env)
        row = dict(_MALFORMED_ROW)
        row["country_risk_scores"] = 5  # already-parsed JSONB number
        _use_fake_db(monkeypatch, _FakeDB(row=row))
        with pytest.raises(RiskConfigUnavailable, match="validation"):
            load_risk_config()

    def test_healthy_config_loads_normally(self, monkeypatch):
        """Fail-closed mode must not reject a valid config."""
        monkeypatch.setenv("ENVIRONMENT", "staging")
        _use_fake_db(monkeypatch, _FakeDB(row=dict(_HEALTHY_ROW)))
        cfg = load_risk_config()
        assert cfg["country_risk_scores"] == {"mauritius": 1, "iran": 4}
        assert cfg["sector_risk_scores"] == {"technology": 1}

    def test_empty_containers_mean_not_configured(self, monkeypatch):
        """Empty JSONB containers ({} / []) are 'not configured', NOT malformed
        — pre-fix parity with the old `if val` truthiness check on the PG path
        (and the schema default for thresholds is '{}'::jsonb). They must not
        trip the fail-closed gate."""
        monkeypatch.setenv("ENVIRONMENT", "staging")
        row = {
            "dimensions": [],       # already-parsed empty JSONB array
            "thresholds": {},       # already-parsed empty JSONB object (schema default)
            "country_risk_scores": '{"mauritius": 1}',
            "sector_risk_scores": {},
            "entity_type_scores": None,
        }
        _use_fake_db(monkeypatch, _FakeDB(row=row))
        cfg = load_risk_config()
        assert cfg["thresholds"] is None
        assert cfg["dimensions"] is None
        assert cfg["country_risk_scores"] == {"mauritius": 1}

    def test_country_scores_helper_propagates(self, monkeypatch):
        """_country_scores_from_db_if_available must not launder the fail-closed
        signal into a silent hardcoded-fallback (its except Exception arm)."""
        monkeypatch.setenv("ENVIRONMENT", "staging")
        _use_fake_db(monkeypatch, _FakeDB(raise_exc=RuntimeError("db down")))
        with pytest.raises(RiskConfigUnavailable):
            rule_engine._country_scores_from_db_if_available()


# ══════════════════════════════════════════════════════════
# Fallback environments: development / testing / demo
# ══════════════════════════════════════════════════════════

class TestFallbackEnvironments:
    @pytest.mark.parametrize("env", ["development", "testing", "demo"])
    def test_db_failure_returns_none(self, monkeypatch, env):
        monkeypatch.setenv("ENVIRONMENT", env)
        _use_fake_db(monkeypatch, _FakeDB(raise_exc=RuntimeError("db down")))
        assert load_risk_config() is None

    def test_malformed_map_nulled_not_raised(self, monkeypatch):
        """Historical behaviour preserved outside staging/prod: malformed columns
        are nulled so hardcoded fallbacks take over."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        _use_fake_db(monkeypatch, _FakeDB(row=dict(_MALFORMED_ROW)))
        cfg = load_risk_config()
        assert cfg is not None
        assert cfg["country_risk_scores"] is None

    def test_missing_row_returns_none(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "testing")
        _use_fake_db(monkeypatch, _FakeDB(row=None))
        assert load_risk_config() is None


# ══════════════════════════════════════════════════════════
# Decision paths remain usable with an explicit override (no DB)
# ══════════════════════════════════════════════════════════

class TestConfigOverrideUnaffected:
    def test_compute_risk_score_with_override_never_touches_db(self, monkeypatch):
        """config_override short-circuits load_risk_config, so scoring with an
        explicit config works even when the DB would fail closed."""
        monkeypatch.setenv("ENVIRONMENT", "staging")
        _use_fake_db(monkeypatch, _FakeDB(raise_exc=RuntimeError("db down")))
        result = rule_engine.compute_risk_score(
            {
                "country": "Mauritius",
                "sector": "Technology",
                "entity_type": "SME",
                "ownership_structure": "simple",
                "directors": [],
                "ubos": [],
            },
            config_override={"country_risk_scores": {"mauritius": 1}},
        )
        assert result["level"] in ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")
