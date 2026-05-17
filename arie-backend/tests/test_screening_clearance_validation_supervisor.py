import copy

from validation_engine import validate_compliance_memo
from supervisor_engine import run_memo_supervisor


def _production_shaped_memo():
    return {
        "sections": {
            "executive_summary": {
                "content": (
                    "Low-risk technology company domiciled in Mauritius. "
                    "Provider screening returned a match that was reviewed and cleared as a false positive with retained evidence."
                )
            },
            "client_overview": {"content": "Clearance Controls Ltd, SME, Technology sector, Mauritius."},
            "ownership_and_control": {
                "content": "Jane Doe holds 100% ownership and exercises effective control through direct shareholding.",
                "structure_complexity": "Simple",
                "control_statement": "Jane Doe exercises effective control through 100% direct ownership.",
            },
            "risk_assessment": {
                "content": "Overall final risk is LOW and consistent with all risk dimensions.",
                "sub_sections": {
                    "jurisdiction_risk": {"rating": "LOW", "content": "Mauritius standard jurisdiction rating with documented basis."},
                    "business_risk": {"rating": "LOW", "content": "Technology sector rated LOW based on activity profile."},
                    "transaction_risk": {"rating": "LOW", "content": "Expected volumes are low and consistent with stated activity."},
                    "ownership_risk": {"rating": "LOW", "content": "Simple direct ownership with 100% disclosed ownership; rating justified."},
                    "financial_crime_risk": {
                        "rating": "LOW",
                        "content": (
                            "Live provider screening returned match(es) that were reviewed and cleared as false positive "
                            "with evidence; no unresolved screening concern remains."
                        ),
                    },
                },
            },
            "screening_results": {
                "content": (
                    "ComplyAdvantage live provider screening returned a sanctions/watchlist match for the entity. "
                    "The match was reviewed by authorised officers and cleared as false positive using provider case FP-001 "
                    "and registry evidence. This is a reviewed and cleared match, not an original no-match result."
                )
            },
            "document_verification": {
                "content": "Certificate of incorporation and ownership documents were verified as authentic and consistent with submitted data."
            },
            "ai_explainability": {
                "content": "Weighted factor analysis followed the documented agent pathway with factor-level weights and evidence.",
                "risk_increasing_factors": [
                    "Limited operating history creates a residual onboarding risk requiring monitoring."
                ],
                "risk_decreasing_factors": [
                    "Simple verified ownership structure with complete control evidence.",
                    "Provider screening match reviewed and cleared as a false positive with retained evidence.",
                ],
            },
            "red_flags_and_mitigants": {
                "red_flags": [
                    "The provider returned a screening match that required formal officer review before reliance.",
                    "The client has limited operating history, which requires monitoring during onboarding.",
                ],
                "mitigants": [
                    "The screening match was cleared as false positive with provider and registry evidence retained.",
                    "Ownership and incorporation records were verified and align with submitted application data.",
                ],
            },
            "compliance_decision": {
                "decision": "APPROVE_WITH_CONDITIONS",
                "content": (
                    "Approve with conditions. The screening match was reviewed and cleared as false positive; "
                    "ongoing monitoring will confirm no new adverse screening alerts."
                ),
            },
            "ongoing_monitoring": {
                "content": "Standard monitoring applies with adverse media and screening refresh controls."
            },
            "audit_and_governance": {
                "content": "Audit trail records the screening review, false-positive clearance evidence, and memo validation."
            },
        },
        "metadata": {
            "risk_rating": "LOW",
            "risk_score": 22,
            "approval_recommendation": "APPROVE_WITH_CONDITIONS",
            "confidence_level": 0.91,
            "original_risk_level": "LOW",
            "aggregated_risk": "LOW",
            "document_count": 2,
            "verified_document_count": 2,
            "pending_document_count": 0,
            "key_findings": [
                "Screening match reviewed and cleared as false positive with evidence.",
                "No declared PEP exposure among directors or UBOs.",
            ],
            "conditions": [
                "Maintain standard ongoing monitoring and refresh screening on material profile change."
            ],
            "screening_state_summary": {
                "terminal": True,
                "canonical_state": "completed_match",
                "screening_result": "match",
                "approval_ready": True,
                "approval_blocking": False,
                "defensible_clear": False,
                "has_formally_cleared_match": True,
                "has_uncleared_completed_match": False,
                "completed_match_blocking": False,
                "declared_pep_count": 0,
            },
            "agent5_input_contract": {
                "final_risk_level": "LOW",
                "declared_pep_present": False,
                "risk_dimensions": {"jurisdiction": "LOW", "business": "LOW"},
                "ownership_transparency_status": "transparent",
                "screening_terminality_summary": {
                    "terminal": True,
                    "has_terminal_match": False,
                    "has_formally_cleared_match": True,
                    "has_uncleared_completed_match": False,
                    "completed_match_blocking": False,
                    "approval_blocking": False,
                },
            },
            "rule_engine": {"violations": [], "enforcements": [], "engine_status": "CLEAN"},
        },
    }


def test_false_positive_cleared_with_evidence_passes_validation_and_supervisor():
    memo = _production_shaped_memo()

    validation = validate_compliance_memo(copy.deepcopy(memo))
    supervisor = run_memo_supervisor(copy.deepcopy(memo))

    assert validation["validation_status"] == "pass"
    assert supervisor["verdict"] in ("CONSISTENT", "CONSISTENT_WITH_WARNINGS")
    assert supervisor["mandatory_escalation"] is False
    assert supervisor["can_approve"] is True
    assert not any(c["category"] == "pep_inconsistency" for c in supervisor["contradictions"])


def test_raw_completed_match_remains_supervisor_blocking():
    memo = _production_shaped_memo()
    memo["metadata"]["screening_state_summary"].update({
        "approval_blocking": True,
        "has_formally_cleared_match": False,
        "has_uncleared_completed_match": True,
        "completed_match_blocking": True,
    })
    memo["metadata"]["agent5_input_contract"]["screening_terminality_summary"].update({
        "has_terminal_match": True,
        "has_formally_cleared_match": False,
        "has_uncleared_completed_match": True,
        "completed_match_blocking": True,
        "approval_blocking": True,
    })

    supervisor = run_memo_supervisor(memo)

    assert supervisor["mandatory_escalation"] is True
    assert "material_screening_concern" in supervisor["mandatory_escalation_reasons"]
    assert supervisor["can_approve"] is False


def test_low_risk_review_with_disclosed_screening_reason_is_not_contradiction():
    memo = _production_shaped_memo()
    memo["metadata"]["approval_recommendation"] = "REVIEW"
    memo["sections"]["compliance_decision"]["decision"] = "REVIEW"
    memo["sections"]["compliance_decision"]["content"] = (
        "Senior compliance officer review is requested because the screening match was reviewed and cleared as false positive."
    )

    validation = validate_compliance_memo(copy.deepcopy(memo))
    supervisor = run_memo_supervisor(copy.deepcopy(memo))

    assert not any(
        issue["category"] == "decision_alignment" and issue["severity"] == "critical"
        for issue in validation["issues"]
    )
    assert not any(c["category"] == "risk_vs_decision" for c in supervisor["contradictions"])


def test_declared_pep_denial_still_fails():
    memo = _production_shaped_memo()
    memo["metadata"]["screening_state_summary"]["declared_pep_count"] = 1
    memo["sections"]["executive_summary"]["content"] = "Low-risk profile with no PEP exposure."

    validation = validate_compliance_memo(copy.deepcopy(memo))
    supervisor = run_memo_supervisor(copy.deepcopy(memo))

    assert any(issue["category"] == "declared_pep_truthfulness" for issue in validation["issues"])
    assert any(c["category"] == "declared_pep_contradiction" for c in supervisor["contradictions"])
    assert supervisor["verdict"] == "INCONSISTENT"


def test_real_risk_decision_contradiction_still_fails():
    memo = _production_shaped_memo()
    memo["metadata"]["risk_rating"] = "HIGH"
    memo["metadata"]["original_risk_level"] = "HIGH"
    memo["metadata"]["aggregated_risk"] = "HIGH"
    memo["metadata"]["agent5_input_contract"]["final_risk_level"] = "HIGH"
    memo["metadata"]["approval_recommendation"] = "APPROVE"
    memo["sections"]["compliance_decision"]["decision"] = "APPROVE"
    memo["sections"]["compliance_decision"]["content"] = "Unconditional approval."

    validation = validate_compliance_memo(copy.deepcopy(memo))
    supervisor = run_memo_supervisor(copy.deepcopy(memo))

    assert any(issue["category"] == "decision_alignment" and issue["severity"] == "critical" for issue in validation["issues"])
    assert any(c["category"] == "risk_vs_decision" for c in supervisor["contradictions"])
    assert supervisor["verdict"] == "INCONSISTENT"


def test_clean_low_completed_clear_passes_validation_and_supervisor():
    memo = _production_shaped_memo()
    memo["sections"]["screening_results"]["content"] = (
        "ComplyAdvantage live provider screening completed with no sanctions, PEP, adverse media, or watchlist matches."
    )
    memo["metadata"]["screening_state_summary"].update({
        "canonical_state": "completed_clear",
        "screening_result": "clear",
        "defensible_clear": True,
        "has_formally_cleared_match": False,
    })
    memo["metadata"]["agent5_input_contract"]["screening_terminality_summary"].update({
        "has_terminal_match": False,
        "has_formally_cleared_match": False,
    })

    validation = validate_compliance_memo(copy.deepcopy(memo))
    supervisor = run_memo_supervisor(copy.deepcopy(memo))

    assert validation["validation_status"] == "pass"
    assert supervisor["verdict"] in ("CONSISTENT", "CONSISTENT_WITH_WARNINGS")
    assert supervisor["mandatory_escalation"] is False


def test_blocking_dispositions_remain_blocking():
    for disposition_code in ("true_match", "material_concern", "needs_more_information", "escalated_to_edd"):
        memo = _production_shaped_memo()
        memo["metadata"]["screening_state_summary"].update({
            "approval_blocking": True,
            "has_formally_cleared_match": False,
            "has_uncleared_completed_match": True,
            "completed_match_blocking": True,
            "review_disposition_code": disposition_code,
        })
        memo["metadata"]["agent5_input_contract"]["screening_terminality_summary"].update({
            "has_terminal_match": True,
            "has_formally_cleared_match": False,
            "has_uncleared_completed_match": True,
            "completed_match_blocking": True,
            "approval_blocking": True,
        })

        supervisor = run_memo_supervisor(memo)

        assert supervisor["mandatory_escalation"] is True, disposition_code
        assert supervisor["can_approve"] is False, disposition_code
