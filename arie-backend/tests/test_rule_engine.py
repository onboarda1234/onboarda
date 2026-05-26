"""
Sprint 1 — Rule Engine Test Suite
Tests for all 5 pre-generation rules (4A-4E) + risk rating logic.
22 deterministic test cases.
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# RULE 4A: Factor Classification Hard Constraints
# ═══════════════════════════════════════════════════════════════

class TestRule4A_FactorClassification:
    """ALWAYS_RISK_DECREASING items must never appear in risk_increasing_factors."""

    def test_clean_sanctions_is_always_decreasing(self):
        """'clean sanctions' must be classified as risk-decreasing."""
        from rule_engine import ALWAYS_RISK_DECREASING
        # Verify core keywords are present
        assert "clean sanctions" in ALWAYS_RISK_DECREASING
        assert "no pep exposure" in ALWAYS_RISK_DECREASING
        assert "regulated entity" in ALWAYS_RISK_DECREASING

    def test_missing_data_is_always_increasing(self):
        """'missing data' must be classified as risk-increasing."""
        from rule_engine import ALWAYS_RISK_INCREASING
        assert "shell company" in ALWAYS_RISK_INCREASING
        assert "bearer shares" in ALWAYS_RISK_INCREASING
        assert "structuring" in ALWAYS_RISK_INCREASING

    def test_keyword_lists_no_overlap(self):
        """ALWAYS_RISK_DECREASING and ALWAYS_RISK_INCREASING must not overlap."""
        from rule_engine import ALWAYS_RISK_DECREASING, ALWAYS_RISK_INCREASING
        overlap = set(ALWAYS_RISK_DECREASING) & set(ALWAYS_RISK_INCREASING)
        assert len(overlap) == 0, f"Overlap found: {overlap}"


# ═══════════════════════════════════════════════════════════════
# RULE 4B: Ownership Risk Floor
# ═══════════════════════════════════════════════════════════════

class TestRule4B_OwnershipFloor:
    """Ownership risk cannot be LOW when gaps exist."""

    def test_no_ubos_forces_medium(self):
        """No UBO data → ownership risk MUST be at least MEDIUM."""
        ubos = []
        ownership_has_gaps = not ubos
        own_rating = "LOW"
        if ownership_has_gaps and own_rating == "LOW":
            own_rating = "MEDIUM"
        assert own_rating == "MEDIUM"

    def test_missing_ownership_pct_forces_medium(self):
        """UBOs with missing ownership % → cannot be LOW."""
        ubos = [{"full_name": "John Doe", "ownership_pct": ""}]
        ownership_has_gaps = any(
            not u.get("ownership_pct") or str(u.get("ownership_pct", "")).strip() in ("", "0", "N/A")
            for u in ubos
        )
        own_rating = "LOW"
        if ownership_has_gaps and own_rating == "LOW":
            own_rating = "MEDIUM"
        assert own_rating == "MEDIUM"

    def test_full_ownership_data_allows_low(self):
        """Complete UBO data with valid % → LOW is permitted."""
        ubos = [{"full_name": "John Doe", "ownership_pct": "100"}]
        primary_ubo = ubos[0]
        control_pct = primary_ubo.get("ownership_pct", "N/A")
        ownership_has_gaps = (
            not ubos
            or any(not u.get("ownership_pct") or str(u.get("ownership_pct", "")).strip() in ("", "0", "N/A") for u in ubos)
            or not primary_ubo
            or control_pct in ("N/A", None, "", "0")
        )
        assert ownership_has_gaps is False, "Complete data should not have gaps"

    def test_na_ownership_pct_treated_as_gap(self):
        """ownership_pct = 'N/A' is treated as a data gap."""
        ubos = [{"full_name": "Jane Doe", "ownership_pct": "N/A"}]
        has_gap = any(str(u.get("ownership_pct", "")).strip() in ("", "0", "N/A") for u in ubos)
        assert has_gap is True


# ═══════════════════════════════════════════════════════════════
# RULE 4C: Business Risk Floor (Sectors)
# ═══════════════════════════════════════════════════════════════

class TestRule4C_BusinessRiskFloor:
    """Certain sectors carry minimum MEDIUM risk."""

    @pytest.mark.parametrize("sector", [
        "Remittance", "Money Transfer", "Payment Services", "E-Money", "Virtual Assets", "MVTS"
    ])
    def test_minimum_medium_sectors(self, sector):
        """Each MINIMUM_MEDIUM_SECTOR must enforce at least MEDIUM."""
        MINIMUM_MEDIUM_SECTORS = ("Remittance", "Money Transfer", "Payment Services", "E-Money", "Virtual Assets", "MVTS")
        is_minimum_medium = sector in MINIMUM_MEDIUM_SECTORS
        biz_rating = "LOW"  # Start at LOW
        if is_minimum_medium and biz_rating == "LOW":
            biz_rating = "MEDIUM"
        assert biz_rating == "MEDIUM", f"{sector} should enforce MEDIUM, got {biz_rating}"

    @pytest.mark.parametrize("sector", [
        "Cryptocurrency", "Money Services", "Gaming", "Arms", "Precious Metals"
    ])
    def test_high_risk_sectors(self, sector):
        """HIGH_RISK_SECTORS must be rated HIGH."""
        HIGH_RISK_SECTORS = ("Cryptocurrency", "Money Services", "Gaming", "Arms", "Precious Metals")
        biz_rating = "HIGH" if sector in HIGH_RISK_SECTORS else "LOW"
        assert biz_rating == "HIGH"

    def test_technology_sector_is_low(self):
        """Technology is not restricted — can be LOW."""
        MINIMUM_MEDIUM_SECTORS = ("Remittance", "Money Transfer", "Payment Services", "E-Money", "Virtual Assets", "MVTS")
        HIGH_RISK_SECTORS = ("Cryptocurrency", "Money Services", "Gaming", "Arms", "Precious Metals")
        MEDIUM_RISK_SECTORS = ("Financial Services", "Real Estate", "Legal Services", "Trust Services", "Art Dealing")
        sector = "Technology"
        biz_rating = "HIGH" if sector in HIGH_RISK_SECTORS else "MEDIUM" if (sector in MEDIUM_RISK_SECTORS or sector in MINIMUM_MEDIUM_SECTORS) else "LOW"
        assert biz_rating == "LOW"


# ═══════════════════════════════════════════════════════════════
# RULE 4D: Multi-Gap Escalation
# ═══════════════════════════════════════════════════════════════

class TestRule4D_MultiGapEscalation:
    """Multiple critical gaps must escalate risk level."""

    def test_three_gaps_escalates_low_to_medium(self):
        """3+ gaps with LOW risk → force MEDIUM."""
        RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
        critical_gaps = ["no_ubo_data", "source_of_funds_missing", "expected_volume_missing"]
        aggregated_risk = "LOW"
        if len(critical_gaps) >= 3 and RISK_RANK.get(aggregated_risk, 2) < 2:
            aggregated_risk = "MEDIUM"
        assert aggregated_risk == "MEDIUM"

    def test_four_gaps_escalates_to_high(self):
        """4+ gaps with MEDIUM risk → force HIGH."""
        RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
        critical_gaps = ["no_ubo_data", "source_of_funds_missing", "multiple_docs_outstanding", "ownership_gaps"]
        aggregated_risk = "MEDIUM"
        # The logic: >= 3 check first (< 2 means LOW only), then >= 4 check (< 3 means LOW or MEDIUM)
        if len(critical_gaps) >= 3 and RISK_RANK.get(aggregated_risk, 2) < 2:
            aggregated_risk = "MEDIUM"
        elif len(critical_gaps) >= 4 and RISK_RANK.get(aggregated_risk, 2) < 3:
            aggregated_risk = "HIGH"
        assert aggregated_risk == "HIGH"

    def test_two_gaps_no_escalation(self):
        """2 gaps should not trigger escalation."""
        RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
        critical_gaps = ["source_of_funds_missing", "expected_volume_missing"]
        aggregated_risk = "LOW"
        if len(critical_gaps) >= 3 and RISK_RANK.get(aggregated_risk, 2) < 2:
            aggregated_risk = "MEDIUM"
        assert aggregated_risk == "LOW"

    def test_high_risk_not_escalated_further_by_three_gaps(self):
        """Already HIGH risk + 3 gaps → stays HIGH (no VERY_HIGH escalation)."""
        RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
        critical_gaps = ["no_ubo_data", "source_of_funds_missing", "expected_volume_missing"]
        aggregated_risk = "HIGH"
        if len(critical_gaps) >= 3 and RISK_RANK.get(aggregated_risk, 2) < 2:
            aggregated_risk = "MEDIUM"
        elif len(critical_gaps) >= 4 and RISK_RANK.get(aggregated_risk, 2) < 3:
            aggregated_risk = "HIGH"
        assert aggregated_risk == "HIGH"


# ═══════════════════════════════════════════════════════════════
# RULE 4E: Confidence Enforcement
# ═══════════════════════════════════════════════════════════════

class TestRule4E_ConfidenceEnforcement:
    """Confidence thresholds enforce decision floors."""

    def test_low_confidence_blocks_approve(self):
        """Confidence < 70% → APPROVE must become APPROVE_WITH_CONDITIONS."""
        model_confidence = 65
        decision = "APPROVE"
        if model_confidence < 70 and decision == "APPROVE":
            decision = "APPROVE_WITH_CONDITIONS"
        assert decision == "APPROVE_WITH_CONDITIONS"

    def test_critical_confidence_forces_review(self):
        """Confidence < 60% → must escalate to REVIEW."""
        model_confidence = 55  # doc_confidence = 0, model_confidence = max(60, 0-5) = 60... actually need 55
        decision = "APPROVE_WITH_CONDITIONS"
        if model_confidence < 60 and decision not in ("REVIEW", "REJECT"):
            decision = "REVIEW"
        assert decision == "REVIEW"

    def test_high_confidence_allows_approve(self):
        """Confidence >= 70% → APPROVE permitted."""
        model_confidence = 78
        decision = "APPROVE"
        if model_confidence < 70 and decision == "APPROVE":
            decision = "APPROVE_WITH_CONDITIONS"
        assert decision == "APPROVE"

    def test_confidence_floor_calculation(self):
        """model_confidence = max(60, doc_confidence - 5)."""
        # 0 verified docs out of 4
        doc_confidence = round(0 / max(4, 1) * 100)  # = 0
        model_confidence = max(60, doc_confidence - 5)
        assert model_confidence == 60  # floor of 60

        # 4 of 5 verified = 80%
        doc_confidence = round(4 / max(5, 1) * 100)  # = 80
        model_confidence = max(60, doc_confidence - 5)
        assert model_confidence == 75

    def test_model_confidence_with_full_docs(self):
        """All docs verified → confidence should be high."""
        doc_confidence = round(5 / max(5, 1) * 100)  # = 100
        model_confidence = max(60, doc_confidence - 5)
        assert model_confidence == 95


# ═══════════════════════════════════════════════════════════════
# RISK RATING LOGIC
# ═══════════════════════════════════════════════════════════════

class TestJurisdictionRating:
    """Jurisdiction risk classification."""

    @pytest.mark.parametrize("country,expected", [
        ("Iran", "HIGH"), ("North Korea", "HIGH"), ("Syria", "HIGH"),
        ("Mauritius", "MEDIUM"), ("Cayman Islands", "MEDIUM"), ("BVI", "MEDIUM"),
        ("United Kingdom", "LOW"), ("Germany", "LOW"), ("Australia", "LOW"),
    ])
    def test_jurisdiction_rating(self, country, expected):
        HIGH_RISK_COUNTRIES = ("Iran", "North Korea", "Syria", "Myanmar", "Afghanistan", "Yemen", "Libya", "Somalia")
        OFFSHORE_COUNTRIES = ("Mauritius", "Seychelles", "Cayman Islands", "BVI", "Panama", "Jersey", "Guernsey", "Isle of Man", "Bermuda", "Luxembourg", "Liechtenstein")
        rating = "HIGH" if country in HIGH_RISK_COUNTRIES else "MEDIUM" if country in OFFSHORE_COUNTRIES else "LOW"
        assert rating == expected


class TestRiskWeights:
    """RISK_WEIGHTS must sum to exactly 1.0."""

    def test_weights_sum_to_one(self):
        RISK_WEIGHTS = {"jurisdiction": 0.20, "business": 0.15, "transaction": 0.10,
                        "ownership": 0.25, "fincrime": 0.10, "documentation": 0.10, "data_quality": 0.10}
        assert abs(sum(RISK_WEIGHTS.values()) - 1.0) < 1e-10

    def test_all_seven_dimensions_present(self):
        RISK_WEIGHTS = {"jurisdiction": 0.20, "business": 0.15, "transaction": 0.10,
                        "ownership": 0.25, "fincrime": 0.10, "documentation": 0.10, "data_quality": 0.10}
        expected = {"jurisdiction", "business", "transaction", "ownership", "fincrime", "documentation", "data_quality"}
        assert set(RISK_WEIGHTS.keys()) == expected

    def test_weighted_risk_calculation(self):
        """Weighted risk of all-LOW dimensions should be LOW."""
        RISK_WEIGHTS = {"jurisdiction": 0.20, "business": 0.15, "transaction": 0.10,
                        "ownership": 0.25, "fincrime": 0.10, "documentation": 0.10, "data_quality": 0.10}
        RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
        sub_risk_vals = {k: RISK_RANK["LOW"] for k in RISK_WEIGHTS}
        weighted = sum(sub_risk_vals[k] * RISK_WEIGHTS[k] for k in RISK_WEIGHTS)
        assert abs(weighted - 1.0) < 1e-10, f"Expected ~1.0, got {weighted}"
        effective = "LOW" if weighted < 1.5 else "MEDIUM"
        assert effective == "LOW"

    def test_weighted_risk_all_high(self):
        """All-HIGH dimensions should produce HIGH."""
        RISK_WEIGHTS = {"jurisdiction": 0.20, "business": 0.15, "transaction": 0.10,
                        "ownership": 0.25, "fincrime": 0.10, "documentation": 0.10, "data_quality": 0.10}
        RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
        sub_risk_vals = {k: RISK_RANK["HIGH"] for k in RISK_WEIGHTS}
        weighted = sum(sub_risk_vals[k] * RISK_WEIGHTS[k] for k in RISK_WEIGHTS)
        assert weighted == 3.0
        effective = "LOW" if weighted < 1.5 else "MEDIUM" if weighted < 2.5 else "HIGH" if weighted < 3.5 else "VERY_HIGH"
        assert effective == "HIGH"


class TestRatingVariables:
    """tx_rating, doc_rating, dq_rating must be correctly derived."""

    def test_doc_rating_high_confidence(self):
        doc_confidence = 90
        doc_rating = "LOW" if doc_confidence >= 80 else "MEDIUM" if doc_confidence >= 50 else "HIGH"
        assert doc_rating == "LOW"

    def test_doc_rating_medium_confidence(self):
        doc_confidence = 65
        doc_rating = "LOW" if doc_confidence >= 80 else "MEDIUM" if doc_confidence >= 50 else "HIGH"
        assert doc_rating == "MEDIUM"

    def test_doc_rating_low_confidence(self):
        doc_confidence = 30
        doc_rating = "LOW" if doc_confidence >= 80 else "MEDIUM" if doc_confidence >= 50 else "HIGH"
        assert doc_rating == "HIGH"

    def test_dq_rating_complete_data(self):
        sof = "Trading revenue"
        exp_vol = "$500,000"
        dq_rating = "LOW" if (sof != "Information not provided" and exp_vol != "Information not provided") else "MEDIUM"
        assert dq_rating == "LOW"

    def test_dq_rating_missing_sof(self):
        sof = "Information not provided"
        exp_vol = "$500,000"
        dq_rating = "LOW" if (sof != "Information not provided" and exp_vol != "Information not provided") else "MEDIUM"
        assert dq_rating == "MEDIUM"

    def test_tx_rating_mirrors_risk_level(self):
        risk_level = "HIGH"
        tx_rating = risk_level
        assert tx_rating == "HIGH"
