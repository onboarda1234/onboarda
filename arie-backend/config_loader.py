"""
ARIE Finance — Configuration Loader
Loads jurisdiction_config.json and provides typed access to risk parameters.

Usage:
    from config_loader import config

    config.is_sanctioned("iran")          # True
    config.get_risk_score("north korea")  # 4
    config.get_risk_weights()             # {"jurisdiction": 0.20, ...}
    config.get_confidence_threshold()     # 0.70
"""
import json
import os
import logging

logger = logging.getLogger("arie")

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "jurisdiction_config.json")
_config_data = None


def _load():
    """Load configuration from JSON file (cached after first load)."""
    global _config_data
    if _config_data is None:
        try:
            with open(_CONFIG_PATH, "r") as f:
                _config_data = json.load(f)
            logger.info("Loaded jurisdiction config from %s", _CONFIG_PATH)
        except FileNotFoundError:
            logger.warning("jurisdiction_config.json not found, using empty config")
            _config_data = {}
    return _config_data


class JurisdictionConfig:
    """Typed access to jurisdiction risk configuration."""

    @property
    def data(self):
        return _load()

    def is_sanctioned(self, country):
        """Check if a country is sanctioned (no onboarding permitted)."""
        countries = (self.data.get("risk_classifications", {})
                    .get("sanctioned", {}).get("countries", []))
        return country.lower().strip() in countries

    def get_risk_score(self, country):
        """Get risk score (1-4) for a country from configuration."""
        c = country.lower().strip() if country else ""
        classifications = self.data.get("risk_classifications", {})
        for level in ["sanctioned", "fatf_black", "fatf_grey", "low_risk"]:
            entry = classifications.get(level, {})
            if c in entry.get("countries", []):
                return entry.get("risk_score", 2)
        return classifications.get("standard", {}).get("risk_score", 2)

    def get_risk_weights(self):
        """Get the 7-dimension risk aggregation weights."""
        return self.data.get("risk_weights", {
            "jurisdiction": 0.20, "business": 0.15, "transaction": 0.10,
            "ownership": 0.25, "fincrime": 0.10, "documentation": 0.10, "data_quality": 0.10
        })

    def get_confidence_threshold(self, risk_level="MEDIUM"):
        """Get confidence threshold for the given risk level."""
        thresholds = self.data.get("confidence_thresholds", {})
        if risk_level in ("HIGH", "VERY_HIGH"):
            return thresholds.get("high_risk_threshold", 0.75)
        return thresholds.get("normal", 0.70)

    def get_monitoring_interval(self, risk_level):
        """Get periodic review interval in days for the given risk level."""
        intervals = self.data.get("monitoring_intervals_days", {})
        return intervals.get(risk_level, 365)

    def get_high_risk_sectors(self):
        """Get list of high-risk sectors."""
        return self.data.get("sector_risk", {}).get("high_risk", [])

    def get_minimum_medium_sectors(self):
        """Get list of minimum-medium risk sectors."""
        return self.data.get("sector_risk", {}).get("minimum_medium", [])

    def get_secrecy_jurisdictions(self):
        """Get list of secrecy/opacity jurisdictions."""
        return self.data.get("secrecy_jurisdictions", [])

    def get_escalation_rules(self):
        """Get escalation rule configuration."""
        return self.data.get("escalation_rules", {})


# Singleton instance
config = JurisdictionConfig()
