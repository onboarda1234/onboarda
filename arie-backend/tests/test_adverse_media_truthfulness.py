"""
Adverse media truthfulness tests.

Validates that the memo generator does not falsely claim adverse media screening
was conducted when no adverse media provider is integrated. This is a regulatory
compliance requirement — auditors reading the memo must not be misled about the
scope of screening actually performed.
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memo_handler import build_compliance_memo
from validation_engine import validate_compliance_memo

# Phrases that indicate a false claim of adverse media screening.
# Used across multiple tests to ensure consistency.
FALSE_ADVERSE_MEDIA_CLAIMS = [
    "adverse media screening returned no relevant hits",
    "comprehensive search conducted across global news and regulatory enforcement databases. no relevant hits",
    "adverse media review conducted — no relevant hits identified",
]


# ── Helpers ──

def _make_app(**overrides):
    """Minimal application dict for memo generation."""
    base = {
        "id": 1,
        "ref": "TEST-001",
        "brn": "BRN-TEST-001",
        "company_name": "Test Corp",
        "country": "United Kingdom",
        "sector": "Technology",
        "entity_type": "Private Limited Company",
        "risk_level": "LOW",
        "risk_score": 25,
        "source_of_funds": "Revenue",
        "expected_volume": "100000",
        "ownership_structure": "direct",
        "operating_countries": "United Kingdom",
        "incorporation_date": "2020-01-01",
        "business_activity": "Software development",
        "assigned_to": "test-officer",
        "prescreening_data": "{}",
        "risk_escalations": "[]",
    }
    base.update(overrides)
    return base


def _make_director(name="John Smith", is_pep="No"):
    return {"full_name": name, "is_pep": is_pep, "nationality": "GB", "date_of_birth": "1990-01-01"}


def _make_ubo(name="Jane Doe", ownership_pct=100, is_pep="No"):
    return {"full_name": name, "ownership_pct": ownership_pct, "is_pep": is_pep, "nationality": "GB", "date_of_birth": "1985-01-01"}


# ═══════════════════════════════════════════════════════════
# MEMO TRUTHFULNESS — No false adverse media claims
# ═══════════════════════════════════════════════════════════

class TestAdverseMediaTruthfulness:
    """Verify memo does not claim adverse media screening was done when it was not."""

    def test_no_false_adverse_media_clearance_without_data(self):
        """When no adverse media data exists, memo must NOT claim it was screened."""
        app = _make_app()
        directors = [_make_director()]
        ubos = [_make_ubo()]
        docs = []
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        # Serialize entire memo to search for false claims
        memo_text = json.dumps(memo).lower()

        # These phrases indicate a false claim of adverse media screening
        for claim in FALSE_ADVERSE_MEDIA_CLAIMS:
            assert claim not in memo_text, (
                f"Memo falsely claims adverse media screening was conducted: '{claim}'"
            )

    def test_adverse_media_disclosed_as_not_conducted(self):
        """Memo should explicitly state adverse media screening was not conducted."""
        app = _make_app()
        directors = [_make_director()]
        ubos = [_make_ubo()]
        docs = []
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        memo_text = json.dumps(memo).lower()

        # Should contain honest disclosure
        assert "not yet conducted" in memo_text, (
            "Memo should disclose that adverse media screening has not yet been conducted"
        )

    def test_adverse_media_truthful_with_pep(self):
        """Even with PEP matches, adverse media status must be truthful."""
        app = _make_app(risk_level="HIGH", risk_score=75)
        directors = [_make_director("Alice PEP", is_pep="Yes")]
        ubos = [_make_ubo()]
        docs = []
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        memo_text = json.dumps(memo).lower()
        for claim in FALSE_ADVERSE_MEDIA_CLAIMS:
            assert claim not in memo_text, (
                f"Memo falsely claims adverse media screening with PEP: '{claim}'"
            )

    def test_adverse_media_truthful_with_real_data(self):
        """When adverse media data IS present in screening_report, claims are valid."""
        screening_report = {
            "adverse_media": {
                "results": [{"name": "Test Corp", "source": "Reuters", "severity": "low"}],
                "screened_at": "2025-01-01T00:00:00",
            },
            "director_screenings": [],
            "ubo_screenings": [],
        }
        app = _make_app(
            prescreening_data=json.dumps({"screening_report": screening_report})
        )
        directors = [_make_director()]
        ubos = [_make_ubo()]
        docs = []
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        memo_text = json.dumps(memo).lower()

        # With real data, the "conducted" language is appropriate
        assert "not yet conducted" not in memo_text, (
            "When adverse media data exists, memo should not say 'not yet conducted'"
        )

    def test_key_findings_no_false_adverse_media_claim(self):
        """key_findings must not claim adverse media screening without data."""
        app = _make_app()
        directors = [_make_director()]
        ubos = [_make_ubo()]
        docs = []
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        key_findings = memo.get("metadata", {}).get("key_findings", [])
        findings_text = " ".join(key_findings).lower()

        # Must say "sanctions screening" not "sanctions and adverse media screening"
        # unless adverse media was actually done
        assert "and adverse media" not in findings_text, (
            "key_findings falsely implies adverse media was part of screening"
        )

    def test_review_checklist_adverse_media_honest(self):
        """review_checklist should reflect actual adverse media screening state."""
        app = _make_app()
        directors = [_make_director()]
        ubos = [_make_ubo()]
        docs = []
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        checklist = memo.get("metadata", {}).get("review_checklist", [])
        checklist_text = " ".join(checklist).lower()

        assert "adverse media review conducted — no relevant hits identified" not in checklist_text, (
            "review_checklist falsely claims adverse media review was conducted"
        )
        assert "not yet conducted" in checklist_text or "required" in checklist_text, (
            "review_checklist should disclose that adverse media screening is not yet conducted"
        )


# ═══════════════════════════════════════════════════════════
# VALIDATION ENGINE — Detect unsubstantiated adverse media claims
# ═══════════════════════════════════════════════════════════

class TestValidationEngineAdverseMediaDetection:
    """Validation engine should flag memos with unsubstantiated adverse media claims."""

    def test_flags_false_adverse_media_claim(self):
        """If screening_results claims adverse media was done, validation should flag it."""
        memo = {
            "sections": {
                "executive_summary": {"content": "Summary"},
                "risk_assessment": {"content": "Risk assessment"},
                "screening_results": {
                    "content": "Adverse media screening returned no relevant hits across global media databases. Sanctions completed."
                },
                "document_verification": {"content": "Documents verified"},
                "compliance_decision": {"content": "Approved"},
                "ongoing_monitoring": {"content": "Standard monitoring"},
            },
            "metadata": {
                "risk_rating": "LOW",
                "approval_recommendation": "approve",
                "risk_increasing_factors": [],
                "risk_decreasing_factors": [],
                "aggregated_risk": "LOW",
                "original_risk_level": "LOW",
            }
        }
        result = validate_compliance_memo(memo)
        issues = result.get("issues", [])

        adverse_issue = [i for i in issues if "adverse media" in i.get("description", "").lower() and i["severity"] == "critical"]
        assert len(adverse_issue) > 0, (
            "Validation engine should flag unsubstantiated adverse media claims as critical"
        )

    def test_no_flag_when_adverse_media_disclosed(self):
        """If memo honestly says adverse media not yet conducted, no critical flag."""
        memo = {
            "sections": {
                "executive_summary": {"content": "Summary"},
                "risk_assessment": {"content": "Risk assessment"},
                "screening_results": {
                    "content": "Sanctions completed. Adverse Media Screening: Not yet conducted. Current screening covers sanctions and PEP only."
                },
                "document_verification": {"content": "Documents verified"},
                "compliance_decision": {"content": "Approved"},
                "ongoing_monitoring": {"content": "Standard monitoring"},
            },
            "metadata": {
                "risk_rating": "LOW",
                "approval_recommendation": "approve",
                "risk_increasing_factors": [],
                "risk_decreasing_factors": [],
                "aggregated_risk": "LOW",
                "original_risk_level": "LOW",
            }
        }
        result = validate_compliance_memo(memo)
        issues = result.get("issues", [])

        adverse_critical = [i for i in issues if "adverse media" in i.get("description", "").lower() and i["severity"] == "critical"]
        assert len(adverse_critical) == 0, (
            "Validation engine should not flag truthful adverse media disclosure"
        )
