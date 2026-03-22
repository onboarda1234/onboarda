"""
Tests for jurisdiction_config.json and config_loader.py.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestJurisdictionConfig:
    """Jurisdiction configuration is valid and complete."""

    def test_config_loads(self):
        from config_loader import config
        assert config.data is not None
        assert "risk_classifications" in config.data

    def test_sanctioned_countries(self):
        from config_loader import config
        assert config.is_sanctioned("Iran")
        assert config.is_sanctioned("north korea")
        assert not config.is_sanctioned("Mauritius")

    def test_risk_scores(self):
        from config_loader import config
        assert config.get_risk_score("Iran") == 4
        assert config.get_risk_score("Nigeria") == 3
        assert config.get_risk_score("United Kingdom") == 1
        assert config.get_risk_score("Unknown Country") == 2

    def test_risk_weights_sum_to_one(self):
        from config_loader import config
        weights = config.get_risk_weights()
        assert abs(sum(weights.values()) - 1.0) < 1e-10

    def test_confidence_thresholds(self):
        from config_loader import config
        assert config.get_confidence_threshold("LOW") == 0.70
        assert config.get_confidence_threshold("HIGH") == 0.75

    def test_monitoring_intervals(self):
        from config_loader import config
        assert config.get_monitoring_interval("LOW") == 730
        assert config.get_monitoring_interval("VERY_HIGH") == 90

    def test_high_risk_sectors(self):
        from config_loader import config
        sectors = config.get_high_risk_sectors()
        assert "Cryptocurrency" in sectors
        assert "Arms" in sectors

    def test_secrecy_jurisdictions(self):
        from config_loader import config
        secrecy = config.get_secrecy_jurisdictions()
        assert "bvi" in secrecy
        assert "cayman islands" in secrecy
