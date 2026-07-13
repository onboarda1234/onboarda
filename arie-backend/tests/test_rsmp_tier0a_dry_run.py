"""Tests for the offline RSMP Tier 0A activation dry run."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import environment
from risk_controlled_values import ACTIVATION_FLAG
from rsmp_tier0a_dry_run import run_dry_run


def _payload():
    return {
        "risk_config": {
            "updated_at": "gate0-test",
            "country_risk_scores": {"united kingdom": 1},
            "sector_risk_scores": {"software": 2, "fintech": 3},
            "entity_type_scores": {"listed company": 1},
        },
        "applications": [
            {
                "application": {
                    "id": "app-1",
                    "company_name": "Dry Run",
                    "country": "United Kingdom",
                    "sector": "Software / SaaS",
                    "entity_type": "Listed Company on Regulated Exchange",
                    "ownership_structure": "Simple — direct identifiable UBOs",
                    "risk_score": 12.0,
                    "risk_level": "LOW",
                    "prescreening_data": {
                        "monthly_volume": "Under USD 50,000 per month",
                        "transaction_complexity": "Simple — single currency, domestic corridors",
                        "introduction_method": "Direct application — client initiated",
                    },
                },
                "directors": [],
                "ubos": [],
                "intermediaries": [],
            }
        ],
    }


def test_dry_run_is_offline_pseudonymized_and_restores_flag(monkeypatch):
    monkeypatch.setitem(environment.flags._cache, ACTIVATION_FLAG, False)
    report = run_dry_run(_payload())
    assert report["metadata"]["mode"] == "read_only_offline"
    assert report["metadata"]["database_writes"] == 0
    assert report["summary"]["active_scored_applications"] == 1
    assert report["applications"][0]["application_key"] != "app-1"
    assert "app-1" not in str(report)
    assert environment.flags.is_enabled(ACTIVATION_FLAG) is False


def test_dry_run_reports_gate0_approved_sector_pending_runtime_implementation():
    payload = _payload()
    payload["applications"][0]["application"]["sector"] = "Cloud Services"
    report = run_dry_run(payload)
    assert report["summary"]["applications_with_unresolved_mappings"] == 1
    unresolved = report["summary"]["unresolved_counts"]
    assert {"family": "sector", "normalized_value": "cloud services", "count": 1} in unresolved
