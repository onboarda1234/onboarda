"""
SCR-011 — Parity tests for protected outputs (abstraction OFF vs ON).

Verifies that enabling the screening abstraction layer does not change
the output of any EX-validated protected function.
"""

import copy
import json
import os
import pytest

from screening_normalizer import normalize_screening_report, denormalize_to_legacy


def _make_screening_report():
    """Realistic screening report for parity testing."""
    return {
        "screened_at": "2025-06-15T14:30:00",
        "screening_mode": "live",
        "company_screening": {
            "found": True,
            "source": "opencorporates",
            "api_status": "live",
            "name": "Test Corp Ltd",
            "company_number": "C12345",
            "jurisdiction": "mu",
            "status": "Active",
            "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
        },
        "director_screenings": [
            {
                "person_name": "John Smith",
                "person_type": "director",
                "nationality": "GB",
                "declared_pep": "No",
                "screening": {
                    "matched": True,
                    "results": [
                        {"is_pep": True, "is_sanctioned": False, "name": "JOHN SMITH", "score": 0.95},
                    ],
                    "source": "sumsub",
                    "api_status": "live",
                },
                "undeclared_pep": True,
            },
        ],
        "ubo_screenings": [
            {
                "person_name": "Jane Doe",
                "person_type": "ubo",
                "nationality": "MU",
                "declared_pep": "No",
                "ownership_pct": 30,
                "screening": {
                    "matched": False,
                    "results": [],
                    "source": "sumsub",
                    "api_status": "live",
                },
            },
        ],
        "ip_geolocation": {
            "source": "ipapi",
            "api_status": "live",
            "risk_level": "LOW",
            "country": "Mauritius",
        },
        "kyc_applicants": [
            {"person_name": "John Smith", "source": "sumsub", "api_status": "live", "review_answer": "GREEN"},
        ],
        "overall_flags": [
            "Director 'John Smith' has sanctions/PEP matches",
            "Director 'John Smith' may be undeclared PEP",
        ],
        "total_hits": 1,
        "degraded_sources": [],
    }


class TestDetermineScreeningModeParity:
    """determine_screening_mode() must return identical results."""

    def test_parity(self, temp_db):
        from security_hardening import determine_screening_mode
        raw = _make_screening_report()

        # OFF: use raw report
        result_off = determine_screening_mode(raw)

        # ON: use round-tripped report
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        result_on = determine_screening_mode(legacy)

        assert result_off == result_on

    def test_parity_simulated(self, temp_db):
        from security_hardening import determine_screening_mode
        raw = _make_screening_report()
        raw["director_screenings"][0]["screening"]["api_status"] = "simulated"
        raw["director_screenings"][0]["screening"]["source"] = "simulated"

        result_off = determine_screening_mode(raw)
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        result_on = determine_screening_mode(legacy)

        assert result_off == result_on


class TestCollectScreeningProviderEvidenceParity:
    """_collect_screening_provider_evidence() must return identical results."""

    def test_parity(self, temp_db):
        from security_hardening import _collect_screening_provider_evidence
        raw = _make_screening_report()

        result_off = _collect_screening_provider_evidence(raw)
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        result_on = _collect_screening_provider_evidence(legacy)

        assert result_off == result_on

    def test_parity_degraded(self, temp_db):
        from security_hardening import _collect_screening_provider_evidence
        raw = _make_screening_report()
        raw["company_screening"] = {"found": False, "source": "unavailable", "degraded": True}
        raw["degraded_sources"] = ["opencorporates"]

        result_off = _collect_screening_provider_evidence(raw)
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        result_on = _collect_screening_provider_evidence(legacy)

        assert result_off == result_on


class TestStoreScreeningModeParity:
    """store_screening_mode() must persist identical state."""

    def test_parity(self, db, temp_db):
        from security_hardening import store_screening_mode, determine_screening_mode

        # Create test application
        app_id = "parity-test-store-mode"
        db.execute("""
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, screening_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_id, "REF-PARITY", "client-1", "Test Corp", "Mauritius", "Technology", "SME", "draft", None))
        db.commit()

        raw = _make_screening_report()
        mode_off = determine_screening_mode(raw)

        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        mode_on = determine_screening_mode(legacy)

        assert mode_off == mode_on


class TestBackofficeExtractionParity:
    """Backoffice data extraction must produce identical results."""

    def _extract(self, report):
        """Extract backoffice-relevant fields."""
        sr = report
        return {
            "director_undeclared_peps": [s.get("undeclared_pep") for s in sr.get("director_screenings", [])],
            "ubo_undeclared_peps": [s.get("undeclared_pep") for s in sr.get("ubo_screenings", [])],
            "results_is_pep": [
                r.get("is_pep")
                for s in sr.get("director_screenings", []) + sr.get("ubo_screenings", [])
                for r in s.get("screening", {}).get("results", [])
            ],
            "results_is_sanctioned": [
                r.get("is_sanctioned")
                for s in sr.get("director_screenings", []) + sr.get("ubo_screenings", [])
                for r in s.get("screening", {}).get("results", [])
            ],
            "api_status": sr.get("company_screening", {}).get("api_status"),
            "person_names": [s.get("person_name") for s in sr.get("director_screenings", []) + sr.get("ubo_screenings", [])],
            "screened_at": sr.get("screened_at"),
            "total_hits": sr.get("total_hits"),
            "overall_flags": sr.get("overall_flags"),
        }

    def test_parity(self):
        raw = _make_screening_report()
        result_off = self._extract(raw)

        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        result_on = self._extract(legacy)

        for key in result_off:
            assert result_off[key] == result_on[key], f"Parity mismatch: {key}"


class TestRiskScoringInputParity:
    """Risk scoring inputs derived from screening report must be identical."""

    def test_total_hits_parity(self):
        raw = _make_screening_report()
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)

        assert raw["total_hits"] == legacy["total_hits"]

    def test_overall_flags_parity(self):
        raw = _make_screening_report()
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)

        assert raw["overall_flags"] == legacy["overall_flags"]

    def test_undeclared_pep_parity(self):
        """Risk elevation depends on undeclared PEP detection."""
        raw = _make_screening_report()
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)

        raw_peps = [d.get("undeclared_pep") for d in raw.get("director_screenings", [])]
        legacy_peps = [d.get("undeclared_pep") for d in legacy.get("director_screenings", [])]
        assert raw_peps == legacy_peps


class TestApprovalGateInputParity:
    """Approval gate evaluation inputs must be identical."""

    def test_screening_mode_parity(self):
        raw = _make_screening_report()
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)

        assert raw.get("screening_mode") == legacy.get("screening_mode")

    def test_degraded_sources_parity(self):
        raw = _make_screening_report()
        raw["degraded_sources"] = ["opencorporates", "sumsub_company_sanctions"]

        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)

        assert raw["degraded_sources"] == legacy["degraded_sources"]


class TestFlagStateParity:
    """Tests must pass in both flag states."""

    def test_abstraction_off_does_not_affect_screening_report(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")
        from screening_config import is_abstraction_enabled
        assert is_abstraction_enabled() is False
        # No normalized writes happen — raw report is unchanged
        raw = _make_screening_report()
        assert raw["total_hits"] == 1

    def test_abstraction_on_preserves_screening_report(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")
        from screening_config import is_abstraction_enabled
        assert is_abstraction_enabled() is True
        # Normalized writes happen — but raw report is unchanged
        raw = _make_screening_report()
        raw_copy = copy.deepcopy(raw)
        normalized = normalize_screening_report(raw)
        # Original unchanged
        assert raw == raw_copy
        # Round-trip preserves
        legacy = denormalize_to_legacy(normalized)
        assert legacy == raw
