"""PR-5B regression tests for concise, decision-safe memo output."""

import json
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _pending_screening_app(**overrides):
    app = {
        "id": "app-pr5b",
        "ref": "ARF-PR5B-001",
        "company_name": "PR5B Simple Review Ltd",
        "brn": "BRN-PR5B",
        "country": "Mauritius",
        "sector": "Technology",
        "entity_type": "SME",
        "source_of_funds": "Operating revenue",
        "expected_volume": "MUR 250,000 monthly",
        "ownership_structure": "Simple direct ownership",
        "operating_countries": "Mauritius",
        "business_activity": "Software services",
        "risk_level": "LOW",
        "risk_score": 22,
        "prescreening_data": json.dumps(
            {
                "screening_report": {
                    "screening_mode": "live",
                    "company_screening": {
                        "source": "complyadvantage",
                        "api_status": "pending",
                        "status": "pending",
                    },
                    "director_screenings": [],
                    "ubo_screenings": [],
                }
            }
        ),
        "screening_reviews": [
            {
                "subject_type": "company",
                "subject_name": "PR5B Simple Review Ltd",
                "disposition": "false_positive",
                "disposition_code": "fp",
                "rationale": "test note    with   messy\n\nspacing Officer notes: raw rough draft",
                "reviewer_name": "Officer One",
            }
        ],
    }
    app.update(overrides)
    return app


def _directors():
    return [{"full_name": "Director One", "nationality": "MU", "is_pep": "No"}]


def _ubos():
    return [{"full_name": "Owner One", "nationality": "MU", "ownership_pct": 100, "is_pep": "No"}]


def _documents():
    return [
        {"doc_type": "Certificate of Incorporation", "verification_status": "verified"},
        {"doc_type": "Bank Reference", "verification_status": "pending"},
    ]


def _build_memo():
    from memo_handler import build_compliance_memo

    memo, _, _, _ = build_compliance_memo(
        _pending_screening_app(),
        _directors(),
        _ubos(),
        _documents(),
    )
    return memo


def _flatten_default_sections(memo):
    return json.dumps(memo["sections"], sort_keys=True)


def _flatten_default_content(memo):
    return " ".join(
        str(section.get("content") or "")
        for section in memo["sections"].values()
        if isinstance(section, dict)
    )


def _read_repo_file(path):
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(repo_root, path), "r", encoding="utf-8") as f:
        return f.read()


def test_blocked_pending_screening_has_one_authoritative_review_recommendation():
    memo = _build_memo()

    assert memo["metadata"]["approval_recommendation"] == "REVIEW"
    assert memo["metadata"]["decision_label"] == "SCREENING RESOLUTION REQUIRED"
    assert memo["sections"]["compliance_decision"]["decision"] == "REVIEW"
    assert "APPROVE_WITH_CONDITIONS" not in _flatten_default_sections(memo)
    assert "Conditional approval is not available" in memo["sections"]["compliance_decision"]["content"]


def test_low_risk_score_uses_canonical_low_not_routing_high_wording():
    memo = _build_memo()
    default_content = _flatten_default_content(memo)

    assert memo["metadata"]["risk_rating"] == "LOW"
    assert memo["metadata"]["risk_score"] == 22
    assert "LOW risk with score 22/100" in memo["sections"]["executive_summary"]["content"]
    assert "Canonical risk rating: LOW; recorded score: 22" in memo["sections"]["risk_assessment"]["content"]
    assert "HIGH risk with score 22/100" not in default_content
    assert "Composite position: HIGH; recorded score: 22" not in default_content


def test_pending_screening_is_blocker_not_mitigant_or_risk_decreasing_factor():
    memo = _build_memo()
    ai = memo["sections"]["ai_explainability"]
    mitigants = memo["sections"]["red_flags_and_mitigants"]["mitigants"]
    blockers = memo["sections"]["red_flags_and_mitigants"]["approval_blockers"]

    for item in ai["risk_decreasing_factors"] + mitigants:
        lowered = item.lower()
        assert "pending" not in lowered
        assert "not yet returned" not in lowered
        assert "not terminal" not in lowered
        assert "not approval-ready" not in lowered

    assert any("screening" in item.lower() for item in blockers)
    assert memo["metadata"]["memo_output_profile"]["screening_pending_is_blocker_not_mitigant"] is True


def test_blocked_memo_exposes_canonical_blockers_for_ui_snapshot():
    memo = _build_memo()
    blockers = memo["metadata"]["primary_blockers"]

    assert blockers
    assert any("screening" in blocker.lower() for blocker in blockers)
    assert any("document" in blocker.lower() or "bank reference" in blocker.lower() for blocker in blockers)
    assert memo["sections"]["red_flags_and_mitigants"]["approval_blockers"] == blockers
    assert memo["sections"]["screening_results"]["approval_blocked_reasons"] == blockers
    assert memo["sections"]["executive_summary"]["decision_summary"]["primary_blockers"] == blockers


def test_simple_blocked_memo_is_concise_and_preserves_appendix_evidence():
    memo = _build_memo()
    profile = memo["metadata"]["memo_output_profile"]

    assert profile["profile_version"] == "pr5b_decision_paper_v1"
    assert profile["default_sections_word_count"] < profile["original_sections_word_count"]
    assert profile["default_sections_word_count"] <= 1100
    assert "appendix_sections" in memo
    assert "risk_evidence" in memo["metadata"]
    assert "source_attribution" in memo["metadata"]
    assert "audit" in memo["sections"]["audit_and_governance"]["content"].lower()


def test_screening_pending_boilerplate_is_not_repeated_across_default_sections():
    memo = _build_memo()
    flat = _flatten_default_content(memo).lower()

    assert flat.count("sanctions screening status:") <= 2
    assert flat.count("terminal provider result") <= 2
    assert flat.count("screening resolution required") <= 2


def test_ai_explainability_default_is_compact_and_no_agent_pathway():
    memo = _build_memo()
    ai = memo["sections"]["ai_explainability"]

    assert len(ai["content"].split()) <= 80
    lowered = ai["content"].lower()
    assert "agent 6" not in lowered
    assert "monitoring pipeline" not in lowered
    assert "decision pathway" not in lowered
    assert "detailed rule evidence is retained in the appendix" in lowered


def test_messy_officer_note_is_sanitized_from_formal_default_memo():
    memo = _build_memo()
    flat = _flatten_default_sections(memo).lower()
    appendix = json.dumps(memo["appendix_sections"]).lower()

    assert "test note" not in flat
    assert "messy spacing" not in flat
    assert "raw rough draft" not in flat
    assert "officer rationale recorded" in flat
    assert "test note" in appendix
    assert "raw rough draft" in appendix


def test_pdf_renderer_keeps_decision_paper_and_appendix_index(monkeypatch):
    import pdf_generator

    memo = _build_memo()
    captured = {}

    class FakeHTML:
        def __init__(self, string):
            captured["html"] = string

        def write_pdf(self):
            return b"%PDF-pr5b-test"

    class FakeWeasyPrint:
        HTML = FakeHTML

    monkeypatch.setattr(pdf_generator, "_get_weasyprint", lambda: FakeWeasyPrint)

    pdf = pdf_generator.generate_memo_pdf(
        memo,
        {
            "ref": memo["application_ref"],
            "company_name": memo["company_name"],
            "country": "Mauritius",
            "sector": "Technology",
            "entity_type": "SME",
            "risk_level": "LOW",
            "risk_score": 22,
        },
        validation_result=memo["validation"],
        supervisor_result=memo["supervisor"],
    )

    html = captured["html"]
    assert pdf == b"%PDF-pr5b-test"
    assert "SCREENING RESOLUTION REQUIRED" in html
    assert "APPROVE WITH CONDITIONS" not in html
    assert "Appendix Evidence Index" in html
    assert "appendix_sections" in html
    assert "Content Hash:" in html
    assert "HIGH risk with score 22/100" not in html


def test_backoffice_memo_browser_consistency_static_contract():
    html = _read_repo_file("arie-backoffice.html")

    assert "function memoCanonicalBlockers(memoData)" in html
    assert "memoCanonicalBlockers(data).forEach" in html
    assert "memoCanonicalBlockers(memoData).forEach" in html
    assert "Fixes required before approval" in html
    assert "Validation passed, but approval remains blocked" in html
    assert "if (status === 'pass_with_fixes')" in html
    assert "No issues found — memo meets quality standards" in html
    pass_with_fixes_idx = html.index("status === 'pass_with_fixes'", html.index("function renderValidationPanel"))
    clean_idx = html.index("No issues found — memo meets quality standards", html.index("function renderValidationPanel"))
    assert pass_with_fixes_idx < clean_idx
