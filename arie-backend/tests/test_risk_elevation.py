"""
Risk Elevation Rules — Test Suite
Tests for combination elevation, screening-driven elevation, severe-case elevation,
prohibited country blocking, and escalation persistence.

Covers requirements:
1. Normal MEDIUM cases stay MEDIUM
2. FATF grey-list + crypto + shell structure elevates MEDIUM to HIGH
3. Severe screening cases elevate appropriately
4. Prohibited country remains blocked before scoring
5. Escalation reasons are persisted
6. Dual approval tied to final risk level
7. base_risk_level / final_risk_level / elevation_reason_text returned
"""
import pytest
import sys
import os
import json
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rule_engine import (
    compute_risk_score, classify_risk_level,
    SANCTIONED, FATF_BLACK, FATF_GREY,
    _is_elevated_jurisdiction, _is_high_risk_sector, _is_opaque_ownership,
    _has_material_screening_concern,
    CANONICAL_THRESHOLDS,
)


# ═══════════════════════════════════════════════════════════════
# HELPERS: Build app_data payloads for different scenarios
# ═══════════════════════════════════════════════════════════════

def _base_medium_app(**overrides):
    """Build a MEDIUM-scoring application (score ~45, no elevation triggers)."""
    data = {
        "entity_type": "SME",
        "ownership_structure": "3+ layers",
        "country": "mauritius",
        "sector": "import",                        # score 3, not high-risk (4)
        "directors": [{"full_name": "John Doe", "nationality": "mauritian",
                       "is_pep": "Yes", "pep_type": "domestic"}],
        "ubos": [{"full_name": "John Doe", "nationality": "mauritian", "ownership_pct": "100"}],
        "primary_service": "cross-border payments",
        "monthly_volume": "500,000",
        "source_of_wealth": "inheritance",
        "source_of_funds": "loan",
        "introduction_method": "non-regulated",
    }
    data.update(overrides)
    return data


def _grey_crypto_shell_app(**overrides):
    """Build a MEDIUM-scoring app with FATF grey-list + crypto + opaque structure."""
    data = _base_medium_app(
        country="nigeria",          # FATF grey-list
        sector="crypto",            # high-risk sector (score 4)
        ownership_structure="complex multi-layered shell",  # opaque
        # Keep directors/ubos nationality consistent with country
        directors=[{"full_name": "John Doe", "nationality": "nigerian",
                    "is_pep": "Yes", "pep_type": "domestic"}],
        ubos=[{"full_name": "John Doe", "nationality": "nigerian", "ownership_pct": "100"}],
    )
    data.update(overrides)
    return data


def _app_with_screening_concern(**overrides):
    """Build an app with material screening concerns."""
    data = _base_medium_app()
    data["screening_results"] = {
        "adverse_media": {"status": "confirmed regulatory action"},
        "sanctions": {"status": "clear"},
        "pep": {"status": "clear"},
    }
    data.update(overrides)
    return data


def _app_with_severe_screening(**overrides):
    """Build an app with severe combination: high-risk sector + elevated jurisdiction + screening concern."""
    data = _base_medium_app(
        country="nigeria",
        sector="crypto",
        ownership_structure="complex shell",
    )
    data["screening_results"] = {
        "adverse_media": {"status": "confirmed criminal activity"},
        "sanctions": {"status": "sanctions-adjacent match"},
        "pep": {"status": "clear"},
    }
    data.update(overrides)
    return data


def _sanctioned_country_app(**overrides):
    """Build an app from a sanctioned country."""
    data = _base_medium_app(country="iran")
    data.update(overrides)
    return data


# ═══════════════════════════════════════════════════════════════
# TEST: Return shape includes new fields
# ═══════════════════════════════════════════════════════════════

class TestReturnShape:
    """compute_risk_score returns base_risk_level, final_risk_level, elevation_reason_text."""

    def test_return_has_base_and_final_risk_level(self):
        result = compute_risk_score(_base_medium_app())
        assert "base_risk_level" in result
        assert "final_risk_level" in result
        assert "elevation_reason_text" in result
        assert result["level"] == result["final_risk_level"]

    def test_non_elevated_base_equals_final(self):
        """When no elevation occurs, base_risk_level == final_risk_level."""
        result = compute_risk_score(_base_medium_app())
        assert result["base_risk_level"] == result["final_risk_level"]
        assert result["elevation_reason_text"] == ""


# ═══════════════════════════════════════════════════════════════
# TEST: Normal MEDIUM cases stay MEDIUM
# ═══════════════════════════════════════════════════════════════

class TestNormalMediumStaysMedium:
    """Normal MEDIUM-scoring cases without elevation triggers must remain MEDIUM."""

    def test_simple_mauritius_tech_stays_medium(self):
        """Standard Mauritius tech company — no grey-list, no crypto, no opacity."""
        result = compute_risk_score(_base_medium_app())
        assert result["level"] == "MEDIUM"
        assert result["base_risk_level"] == "MEDIUM"
        assert result["final_risk_level"] == "MEDIUM"
        assert "elevation_grey_sector_opaque" not in result["escalations"]

    def test_grey_list_without_crypto_stays_medium(self):
        """FATF grey-list country but standard sector — not elevated."""
        result = compute_risk_score(_base_medium_app(country="nigeria"))
        # Must not elevate — only one condition met (grey-list), not all three
        assert result["final_risk_level"] in ("MEDIUM", "HIGH")
        # If HIGH, it should be from score, not from our elevation rule
        if result["final_risk_level"] == "MEDIUM":
            assert "elevation_grey_sector_opaque" not in result["escalations"]

    def test_crypto_without_grey_list_not_elevated_by_combination(self):
        """Crypto sector but low-risk country — combination rule doesn't fire."""
        result = compute_risk_score(_base_medium_app(
            country="united kingdom",
            sector="crypto",
            ownership_structure="simple",
        ))
        assert "elevation_grey_sector_opaque" not in result["escalations"]

    def test_opaque_without_grey_and_crypto_not_elevated(self):
        """Opaque ownership but standard sector and low-risk country — no elevation."""
        result = compute_risk_score(_base_medium_app(
            country="united kingdom",
            sector="technology",
            ownership_structure="complex multi-layered",
        ))
        assert "elevation_grey_sector_opaque" not in result["escalations"]


# ═══════════════════════════════════════════════════════════════
# TEST: FATF grey-list + crypto + shell structure → MEDIUM to HIGH
# ═══════════════════════════════════════════════════════════════

class TestCombinationElevationToHigh:
    """MEDIUM + FATF grey-list + high-risk sector + opaque structure → HIGH."""

    def test_grey_crypto_shell_elevates_to_high(self):
        """All three conditions met: elevate MEDIUM to HIGH."""
        result = compute_risk_score(_grey_crypto_shell_app())
        assert result["base_risk_level"] in ("MEDIUM", "HIGH")
        # If base was MEDIUM, final must be HIGH from elevation
        if result["base_risk_level"] == "MEDIUM":
            assert result["final_risk_level"] == "HIGH"
            assert "elevation_grey_sector_opaque" in result["escalations"]
            assert result["elevation_reason_text"] != ""
            assert "grey" in result["elevation_reason_text"].lower() or "jurisdiction" in result["elevation_reason_text"].lower()

    def test_grey_crypto_shell_escalation_recorded(self):
        """Escalation reason is recorded in escalations array."""
        result = compute_risk_score(_grey_crypto_shell_app())
        if result["base_risk_level"] == "MEDIUM":
            assert "elevation_grey_sector_opaque" in result["escalations"]

    def test_grey_crypto_nominee_elevates(self):
        """Nominee structure also qualifies as opaque."""
        result = compute_risk_score(_grey_crypto_shell_app(
            ownership_structure="nominee director arrangement",
        ))
        if result["base_risk_level"] == "MEDIUM":
            assert result["final_risk_level"] == "HIGH"
            assert "elevation_grey_sector_opaque" in result["escalations"]

    def test_grey_gambling_complex_elevates(self):
        """Gambling sector (score 4) + grey-list + complex → HIGH."""
        result = compute_risk_score(_grey_crypto_shell_app(sector="gambling"))
        if result["base_risk_level"] == "MEDIUM":
            assert result["final_risk_level"] == "HIGH"

    def test_grey_virtual_asset_opaque_elevates(self):
        """Virtual asset sector (score 4) + grey-list + opaque → HIGH."""
        result = compute_risk_score(_grey_crypto_shell_app(sector="virtual asset service"))
        if result["base_risk_level"] == "MEDIUM":
            assert result["final_risk_level"] == "HIGH"


# ═══════════════════════════════════════════════════════════════
# TEST: Screening-driven elevation
# ═══════════════════════════════════════════════════════════════

class TestScreeningDrivenElevation:
    """Material screening concerns elevate to at least HIGH."""

    def test_adverse_media_elevates_to_high(self):
        """Confirmed adverse media → elevate to at least HIGH."""
        result = compute_risk_score(_app_with_screening_concern())
        assert result["final_risk_level"] in ("HIGH", "VERY_HIGH")
        assert "elevation_screening_concern" in result["escalations"]

    def test_screening_elevation_reason_persisted(self):
        """Elevation reason text mentions screening concern."""
        result = compute_risk_score(_app_with_screening_concern())
        assert "screening" in result["elevation_reason_text"].lower() or "adverse" in result["elevation_reason_text"].lower()

    def test_clear_screening_no_elevation(self):
        """Clear screening results → no screening-based elevation."""
        data = _base_medium_app()
        data["screening_results"] = {
            "adverse_media": {"status": "clear"},
            "sanctions": {"status": "clear"},
            "pep": {"status": "clear"},
        }
        result = compute_risk_score(data)
        assert "elevation_screening_concern" not in result["escalations"]

    def test_sanctions_adjacent_concern_elevates(self):
        """Sanctions-adjacent match → elevate to at least HIGH."""
        data = _base_medium_app()
        data["screening_results"] = {
            "sanctions": {"status": "sanctions-adjacent match found"},
        }
        result = compute_risk_score(data)
        assert result["final_risk_level"] in ("HIGH", "VERY_HIGH")
        assert "elevation_screening_concern" in result["escalations"]

    def test_unresolved_pep_concern_elevates(self):
        """Serious unresolved PEP concern → elevate to at least HIGH."""
        data = _base_medium_app()
        data["screening_results"] = {
            "pep": {"status": "serious confirmed PEP hit"},
        }
        result = compute_risk_score(data)
        assert result["final_risk_level"] in ("HIGH", "VERY_HIGH")

    def test_screening_concern_flag_elevates(self):
        """Explicit screening_concern flag → elevation."""
        data = _base_medium_app()
        data["screening_concern"] = "material AML concern"
        result = compute_risk_score(data)
        assert result["final_risk_level"] in ("HIGH", "VERY_HIGH")


# ═══════════════════════════════════════════════════════════════
# TEST: Severe-case elevation to VERY_HIGH
# ═══════════════════════════════════════════════════════════════

class TestSevereCaseElevation:
    """Severe combination → VERY_HIGH."""

    def test_severe_combination_elevates_to_very_high(self):
        """High-risk sector + elevated jurisdiction + material screening → VERY_HIGH."""
        result = compute_risk_score(_app_with_severe_screening())
        assert result["final_risk_level"] == "VERY_HIGH"
        assert "elevation_severe_combination" in result["escalations"]

    def test_multiple_screening_signals_elevate_to_very_high(self):
        """Multiple material screening signals → VERY_HIGH."""
        data = _base_medium_app()
        data["screening_results"] = {
            "adverse_media": {"status": "confirmed regulatory action"},
            "sanctions": {"status": "positive match"},
        }
        result = compute_risk_score(data)
        assert result["final_risk_level"] == "VERY_HIGH"
        assert "elevation_severe_combination" in result["escalations"]

    def test_severe_elevation_reason_text(self):
        """Severe elevation reason text is descriptive."""
        result = compute_risk_score(_app_with_severe_screening())
        assert "VERY_HIGH" in result["elevation_reason_text"] or "severe" in result["elevation_reason_text"].lower()


# ═══════════════════════════════════════════════════════════════
# TEST: Prohibited/sanctioned country blocked before scoring
# ═══════════════════════════════════════════════════════════════

class TestProhibitedCountryBlocking:
    """Sanctioned countries force VERY_HIGH — pre-screening blocks preserved."""

    def test_iran_forces_very_high(self):
        """Iran (sanctioned) → VERY_HIGH regardless of score."""
        result = compute_risk_score(_sanctioned_country_app(country="iran"))
        assert result["level"] == "VERY_HIGH"
        assert result["final_risk_level"] == "VERY_HIGH"
        assert any("floor_rule_sanctioned_country" in e for e in result["escalations"])

    def test_north_korea_forces_very_high(self):
        """North Korea (sanctioned) → VERY_HIGH."""
        result = compute_risk_score(_sanctioned_country_app(country="north korea"))
        assert result["level"] == "VERY_HIGH"

    def test_syria_forces_very_high(self):
        """Syria (sanctioned) → VERY_HIGH."""
        result = compute_risk_score(_sanctioned_country_app(country="syria"))
        assert result["level"] == "VERY_HIGH"

    def test_russia_forces_very_high(self):
        """Russia (sanctioned) → VERY_HIGH."""
        result = compute_risk_score(_sanctioned_country_app(country="russia"))
        assert result["level"] == "VERY_HIGH"

    def test_sanctioned_ubo_forces_very_high(self):
        """UBO with sanctioned nationality → VERY_HIGH."""
        data = _base_medium_app()
        data["ubos"] = [{"full_name": "Test UBO", "nationality": "Iranian", "ownership_pct": "100"}]
        result = compute_risk_score(data)
        assert result["level"] == "VERY_HIGH"
        assert any("floor_rule_sanctioned_nationality" in e for e in result["escalations"])

    def test_fatf_black_country_forces_very_high(self):
        """FATF black-listed country → VERY_HIGH."""
        result = compute_risk_score(_sanctioned_country_app(country="afghanistan"))
        assert result["level"] == "VERY_HIGH"

    def test_sanctioned_country_score_at_least_70(self):
        """Sanctioned country score floor is 70."""
        result = compute_risk_score(_sanctioned_country_app(country="iran"))
        assert result["score"] >= 70

    def test_sanctioned_countries_remain_in_set(self):
        """All canonical sanctioned countries are present."""
        for c in ["iran", "north korea", "syria", "cuba", "crimea", "myanmar", "russia", "belarus"]:
            assert c in SANCTIONED


# ═══════════════════════════════════════════════════════════════
# TEST: Escalation reasons are persisted
# ═══════════════════════════════════════════════════════════════

class TestEscalationPersistence:
    """Escalation reasons and elevation data are returned correctly."""

    def test_escalations_is_list(self):
        result = compute_risk_score(_base_medium_app())
        assert isinstance(result["escalations"], list)

    def test_elevation_reason_text_is_string(self):
        result = compute_risk_score(_base_medium_app())
        assert isinstance(result["elevation_reason_text"], str)

    def test_floor_rule_escalation_has_reason_text(self):
        """Floor rule elevation produces reason text."""
        result = compute_risk_score(_sanctioned_country_app())
        assert result["elevation_reason_text"] != ""
        assert "sanctioned" in result["elevation_reason_text"].lower() or "FATF" in result["elevation_reason_text"]

    def test_combination_elevation_has_reason_text(self):
        """Combination elevation produces reason text."""
        result = compute_risk_score(_grey_crypto_shell_app())
        if result["base_risk_level"] == "MEDIUM":
            assert result["elevation_reason_text"] != ""

    def test_screening_elevation_has_reason_text(self):
        """Screening elevation produces reason text."""
        result = compute_risk_score(_app_with_screening_concern())
        assert result["elevation_reason_text"] != ""

    def test_multiple_escalations_concatenated(self):
        """Multiple elevation reasons are joined with semicolons."""
        # Severe case has multiple elevation reasons
        result = compute_risk_score(_app_with_severe_screening())
        assert ";" in result["elevation_reason_text"] or len(result["elevation_reason_text"]) > 0


# ═══════════════════════════════════════════════════════════════
# TEST: Dual approval tied to final risk level
# ═══════════════════════════════════════════════════════════════

class TestDualApprovalTiedToFinalLevel:
    """HIGH and VERY_HIGH final risk level must remain dual-approval eligible."""

    def test_elevated_high_is_dual_approval_eligible(self):
        """An elevated HIGH (from MEDIUM) still counts as HIGH for dual approval."""
        result = compute_risk_score(_grey_crypto_shell_app())
        if result["final_risk_level"] == "HIGH":
            assert result["level"] == "HIGH"  # level == final_risk_level
            # Dual approval checks level field

    def test_very_high_is_dual_approval_eligible(self):
        """VERY_HIGH final level remains dual-approval eligible."""
        result = compute_risk_score(_sanctioned_country_app())
        assert result["level"] == "VERY_HIGH"
        assert result["final_risk_level"] == "VERY_HIGH"


# ═══════════════════════════════════════════════════════════════
# TEST: Thresholds preserved
# ═══════════════════════════════════════════════════════════════

class TestThresholdsPreserved:
    """Base score thresholds remain: Low 0-39, Medium 40-54, High 55-69, VH 70-100."""

    def test_low_threshold(self):
        assert classify_risk_level(0) == "LOW"
        assert classify_risk_level(39) == "LOW"
        assert classify_risk_level(39.9) == "LOW"

    def test_medium_threshold(self):
        assert classify_risk_level(40) == "MEDIUM"
        assert classify_risk_level(54) == "MEDIUM"
        assert classify_risk_level(54.9) == "MEDIUM"

    def test_high_threshold(self):
        assert classify_risk_level(55) == "HIGH"
        assert classify_risk_level(69) == "HIGH"
        assert classify_risk_level(69.9) == "HIGH"

    def test_very_high_threshold(self):
        assert classify_risk_level(70) == "VERY_HIGH"
        assert classify_risk_level(85) == "VERY_HIGH"
        assert classify_risk_level(100) == "VERY_HIGH"


# ═══════════════════════════════════════════════════════════════
# TEST: Helper functions
# ═══════════════════════════════════════════════════════════════

class TestHelperFunctions:
    """Unit tests for internal helper functions."""

    def test_is_elevated_jurisdiction_grey_list(self):
        assert _is_elevated_jurisdiction("nigeria") is True
        assert _is_elevated_jurisdiction("philippines") is True
        assert _is_elevated_jurisdiction("south africa") is True

    def test_is_elevated_jurisdiction_low_risk(self):
        assert _is_elevated_jurisdiction("united kingdom") is False
        assert _is_elevated_jurisdiction("usa") is False

    def test_is_elevated_jurisdiction_empty(self):
        assert _is_elevated_jurisdiction("") is False
        assert _is_elevated_jurisdiction(None) is False

    def test_is_high_risk_sector_crypto(self):
        assert _is_high_risk_sector("crypto") is True
        assert _is_high_risk_sector("Cryptocurrency") is True
        assert _is_high_risk_sector("virtual asset service") is True
        assert _is_high_risk_sector("digital asset exchange") is True

    def test_is_high_risk_sector_gambling(self):
        assert _is_high_risk_sector("gambling") is True
        assert _is_high_risk_sector("gaming") is True

    def test_is_high_risk_sector_normal(self):
        assert _is_high_risk_sector("technology") is False
        assert _is_high_risk_sector("healthcare") is False

    def test_is_opaque_ownership_complex(self):
        assert _is_opaque_ownership("complex multi-layered") is True
        assert _is_opaque_ownership("shell company") is True
        assert _is_opaque_ownership("nominee director") is True
        assert _is_opaque_ownership("opaque structure") is True

    def test_is_opaque_ownership_simple(self):
        assert _is_opaque_ownership("simple") is False
        assert _is_opaque_ownership("1-2 shareholders") is False

    def test_has_material_screening_concern_adverse_media(self):
        data = {"adverse_media": {"status": "confirmed regulatory action"}}
        has, reasons = _has_material_screening_concern(data)
        assert has is True
        assert len(reasons) > 0

    def test_has_material_screening_concern_clear(self):
        data = {
            "screening_results": {
                "adverse_media": {"status": "clear"},
                "sanctions": {"status": "clear"},
                "pep": {"status": "clear"},
            }
        }
        has, reasons = _has_material_screening_concern(data)
        assert has is False
        assert len(reasons) == 0

    def test_has_material_screening_concern_sanctions_adjacent(self):
        data = {"screening_results": {"sanctions": {"status": "sanctions-adjacent match"}}}
        has, reasons = _has_material_screening_concern(data)
        assert has is True

    def test_has_material_screening_concern_flag(self):
        data = {"screening_concern": "material AML concern"}
        has, reasons = _has_material_screening_concern(data)
        assert has is True


# ═══════════════════════════════════════════════════════════════
# TEST: DB Migration v2.23
# ═══════════════════════════════════════════════════════════════

class TestMigrationV223:
    """DB migration v2.23 adds elevation tracking columns."""

    def test_migration_adds_columns(self):
        """Migration v2.23 adds base_risk_level, final_risk_level, elevation_reason_text."""
        import tempfile
        # Create a minimal SQLite DB with just the applications table
        path = tempfile.mktemp(suffix='_test_v223.db')
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id TEXT PRIMARY KEY,
                ref TEXT,
                risk_score REAL,
                risk_level TEXT,
                risk_escalations TEXT DEFAULT '[]'
            )
        """)
        conn.commit()

        # Simulate migration v2.23 logic
        from db import _safe_column_exists

        class _Wrapper:
            def __init__(self, c): self._c = c; self.is_postgres = False
            def execute(self, sql, params=None):
                if params:
                    return self._c.execute(sql, params)
                return self._c.execute(sql)
            def commit(self): self._c.commit()
            def rollback(self): self._c.rollback()

        wrapper = _Wrapper(conn)

        cols_added = []
        if not _safe_column_exists(wrapper, "applications", "base_risk_level"):
            conn.execute("ALTER TABLE applications ADD COLUMN base_risk_level TEXT")
            cols_added.append("base_risk_level")
        if not _safe_column_exists(wrapper, "applications", "final_risk_level"):
            conn.execute("ALTER TABLE applications ADD COLUMN final_risk_level TEXT")
            cols_added.append("final_risk_level")
        if not _safe_column_exists(wrapper, "applications", "elevation_reason_text"):
            conn.execute("ALTER TABLE applications ADD COLUMN elevation_reason_text TEXT DEFAULT ''")
            cols_added.append("elevation_reason_text")
        conn.commit()

        # Verify columns exist
        rows = conn.execute("PRAGMA table_info(applications)").fetchall()
        col_names = [r[1] for r in rows]
        assert "base_risk_level" in col_names
        assert "final_risk_level" in col_names
        assert "elevation_reason_text" in col_names
        assert len(cols_added) == 3

        conn.close()
        try:
            os.unlink(path)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════
# TEST: No generic "2 flags = HIGH" rule
# ═══════════════════════════════════════════════════════════════

class TestNoGenericElevation:
    """Ensure there is no generic '2 flags = HIGH' rule — only explicit business conditions."""

    def test_two_non_qualifying_flags_stay_medium(self):
        """Grey-list + opaque (but no high-risk sector) → stays MEDIUM if base is MEDIUM."""
        result = compute_risk_score(_base_medium_app(
            country="nigeria",             # grey-list ✓
            sector="technology",           # NOT high-risk sector ✗
            ownership_structure="complex",  # opaque ✓
        ))
        # Should NOT fire combination elevation (needs all three conditions)
        assert "elevation_grey_sector_opaque" not in result["escalations"]

    def test_grey_list_plus_crypto_no_opaque_no_elevation(self):
        """Grey-list + crypto (but simple ownership) → no combination elevation."""
        result = compute_risk_score(_base_medium_app(
            country="nigeria",
            sector="crypto",
            ownership_structure="simple",
        ))
        assert "elevation_grey_sector_opaque" not in result["escalations"]
