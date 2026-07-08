"""P12-3 / DCI-010 + DCI-011 — memo compliance-logic corrections.

DCI-010: the memo's SANCTIONED_COUNTRY_FLOOR recorded an enforcement entry
("enforced: VERY_HIGH") but did NOT actually set the displayed jurisdiction
rating to VERY_HIGH. Because `jur_rating` is taken from the manual/DB
country-risk value (`country_risk.get("risk_rating")`) FIRST, a
sanctioned/FATF-black country whose manual value was mis-set to a lower rating
would show that lower jurisdiction sub-rating while the memo claimed the floor
was applied. Now the floor OVERRIDES the value, and the audit record keeps the
true pre-floor value.

DCI-011: the MULTI_GAP_ESCALATION HIGH branch (>=4 gaps -> HIGH) was unreachable
for a LOW base because the >=3 -> MEDIUM branch ran first and shadowed it. The
branch is now resolved by `multi_gap_escalation_level`, which checks the
strongest condition first. These tests pin the pure decision function (robust,
no dependence on the weighted-risk arithmetic) plus one end-to-end memo check.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

import memo_handler


def _base_app(**over):
    app = {
        "id": "app-p123",
        "ref": "ARF-P123",
        "reference_number": "ARF-P123",
        "company_name": "P123 Test Ltd",
        "brn": "P123001",
        "entity_type": "SME",
        "country": "Mauritius",
        "sector": "Technology",
        "ownership_structure": "simple",
        "source_of_funds": "Operating revenue",
        "expected_volume": "USD 100,000 monthly",
        "risk_level": "LOW",
        "risk_score": 20,
        "assigned_to": "admin001",
        "operating_countries": "Mauritius",
        "incorporation_date": "2024-01-01",
        "business_activity": "Technology services",
    }
    app.update(over)
    return app


def _enforcements(memo):
    """Enforcement records live under metadata.rule_engine.enforcements."""
    return (memo.get("metadata", {}).get("rule_engine", {}) or {}).get("enforcements") or []


def _find_enforcement(memo, rule):
    return [e for e in _enforcements(memo) if e.get("rule") == rule]


def _jur_rating(memo):
    return memo["metadata"]["risk_evidence"]["jurisdiction"]["rating"]


# ══════════════════════════════════════════════════════════
# DCI-010 — sanctioned-country floor actually applies
# ══════════════════════════════════════════════════════════

class TestSanctionedCountryFloor:
    def test_floor_overrides_a_lower_manual_rating(self, monkeypatch):
        """Core DCI-010 regression: when the manual/DB country-risk value returns
        a LOWER rating for a country memo_handler flags as sanctioned (via the
        SANCTIONED/FATF_BLACK sets), the memo's jurisdiction rating must be
        forced to VERY_HIGH — not left at the lower manual value. Fails pre-fix."""
        real = memo_handler.country_risk_details

        def _stub(country, cfg=None):
            details = dict(real(country, cfg))
            if str(country).strip().lower() == "iran":
                # Simulate a mis-set manual value: MEDIUM, and NOT flagged via the
                # sanctions_status/fatf_status fields — so is_sanctioned_country is
                # reached only through SANCTIONED/FATF_BLACK key membership. The
                # mis-set `risk_rating` is what jur_rating takes first (the bug).
                details.update({
                    "risk_rating": "MEDIUM",
                    "risk_score": 2,
                    "sanctions_status": "none",
                    "fatf_status": "none",
                    "country_key": "iran",
                })
            return details

        monkeypatch.setattr(memo_handler, "country_risk_details", _stub)

        app = _base_app(country="Iran", operating_countries="Iran")
        memo, _, _, _ = memo_handler.build_compliance_memo(
            app,
            [{"full_name": "D", "nationality": "Mauritian", "is_pep": "No"}],
            [{"full_name": "U", "nationality": "Mauritian", "ownership_pct": 100, "is_pep": "No"}],
            [],
        )

        assert _jur_rating(memo) == "VERY_HIGH", "sanctioned-country floor must set VERY_HIGH"
        floor = _find_enforcement(memo, "SANCTIONED_COUNTRY_FLOOR")
        assert floor, "SANCTIONED_COUNTRY_FLOOR enforcement must be recorded"
        # the audit record must show the true pre-floor value, not VERY_HIGH
        assert floor[0]["original"] == "MEDIUM"
        assert floor[0]["enforced"] == "VERY_HIGH"

    def test_non_sanctioned_country_unaffected(self):
        app = _base_app(country="Mauritius")
        memo, _, _, _ = memo_handler.build_compliance_memo(
            app,
            [{"full_name": "D", "nationality": "Mauritian", "is_pep": "No"}],
            [{"full_name": "U", "nationality": "Mauritian", "ownership_pct": 100, "is_pep": "No"}],
            [],
        )
        assert _jur_rating(memo) != "VERY_HIGH"
        assert _find_enforcement(memo, "SANCTIONED_COUNTRY_FLOOR") == []


# ══════════════════════════════════════════════════════════
# DCI-011 — multi-gap escalation branch order
# ══════════════════════════════════════════════════════════

class TestMultiGapEscalationLevel:
    """Pin the pure decision function. The DCI-011 fix is precisely that the
    >=4 -> HIGH branch is checked BEFORE the >=3 -> MEDIUM branch; pre-fix the
    (4, LOW) case returned MEDIUM because the MEDIUM branch shadowed it."""

    def test_four_gaps_low_base_escalates_to_high(self):
        # The regression: pre-fix this returned "MEDIUM" (HIGH branch shadowed).
        assert memo_handler.multi_gap_escalation_level(4, "LOW") == "HIGH"

    def test_four_gaps_medium_base_escalates_to_high(self):
        assert memo_handler.multi_gap_escalation_level(4, "MEDIUM") == "HIGH"

    def test_three_gaps_low_base_escalates_to_medium(self):
        # The >=3 -> MEDIUM behaviour for a LOW base must be preserved.
        assert memo_handler.multi_gap_escalation_level(3, "LOW") == "MEDIUM"

    def test_three_gaps_medium_base_no_escalation(self):
        # Already MEDIUM -> the >=3 branch (which only lifts LOW) does not fire.
        assert memo_handler.multi_gap_escalation_level(3, "MEDIUM") is None

    def test_two_gaps_no_escalation(self):
        assert memo_handler.multi_gap_escalation_level(2, "LOW") is None

    def test_high_base_never_downgraded(self):
        # A base already at HIGH/VERY_HIGH is never touched.
        assert memo_handler.multi_gap_escalation_level(4, "HIGH") is None
        assert memo_handler.multi_gap_escalation_level(5, "VERY_HIGH") is None


class TestMultiGapEscalationEndToEnd:
    def test_many_gaps_escalate_memo_to_high(self):
        """End-to-end: an app with 4+ critical data gaps at a MEDIUM-or-lower base
        records a MULTI_GAP_ESCALATION enforcement to HIGH (never capped at
        MEDIUM). Gaps: no UBOs (-> no_ubo_data + ownership_gaps), no documents,
        source-of-funds missing, expected-volume missing."""
        app = _base_app(
            risk_level="LOW",
            risk_score=15,
            source_of_funds="Information not provided",
            source_of_funds_details="Information not provided",
            expected_volume="Information not provided",
        )
        memo, _, _, _ = memo_handler.build_compliance_memo(
            app,
            [{"full_name": "D", "nationality": "Mauritian", "is_pep": "No"}],
            [],  # no UBOs
            [],  # no documents
        )
        esc = _find_enforcement(memo, "MULTI_GAP_ESCALATION")
        assert esc, "4+ gaps must trigger MULTI_GAP_ESCALATION"
        assert esc[0]["enforced"] == "HIGH", "4+ gaps must escalate to HIGH, not stop at MEDIUM"
