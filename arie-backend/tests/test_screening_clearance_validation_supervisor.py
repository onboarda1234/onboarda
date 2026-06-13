import copy
import json

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
                "screening_terminal": True,
                "screening_provider_clear": False,
                "screening_gate_ready": True,
                "approval_gate_ready": True,
                "approval_ready": True,
                "approval_ready_scope": "screening_truth_gate_only",
                "approval_blocking": False,
                "defensible_clear": True,
                "approval_blocked_reasons": [],
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
                    "screening_gate_ready": True,
                    "approval_gate_ready": True,
                    "approval_ready": True,
                    "approval_ready_scope": "screening_truth_gate_only",
                    "approval_blocking": False,
                    "approval_blocked_reasons": [],
                },
            },
            "rule_engine": {"violations": [], "enforcements": [], "engine_status": "CLEAN"},
        },
    }


def _generated_memo_for_screening_disposition(*, disposition_code=None, raw_match=False):
    from memo_handler import build_compliance_memo

    matched = bool(raw_match or disposition_code)
    sanctions_record = {
        "matched": matched,
        "results": [{
            "name": "Potential Watchlist Entity",
            "is_sanctioned": True,
            "match_categories": ["sanctions"],
        }] if matched else [],
        "source": "complyadvantage",
        "provider": "ComplyAdvantage",
        "api_status": "live",
    }
    app = {
        "id": "app-validation-severity",
        "ref": "ARF-VALIDATION-SEVERITY",
        "company_name": "Validation Severity Ltd",
        "brn": "BRN-VAL-001",
        "country": "United Kingdom",
        "sector": "Technology",
        "entity_type": "SME",
        "ownership_structure": "Single-tier direct ownership",
        "operating_countries": "United Kingdom",
        "incorporation_date": "2024-01-01",
        "business_activity": "Software consulting",
        "source_of_funds": "Trading revenue",
        "expected_volume": "0-50000",
        "risk_level": "LOW",
        "risk_score": 24,
        "risk_escalations": "[]",
        "assigned_to": "Compliance Officer",
        "prescreening_data": json.dumps({
            "screening_report": {
                "screened_at": "2026-05-17T00:00:00Z",
                "screening_mode": "live",
                "adverse_media_coverage": "full",
                "adverse_media": {"status": "clear", "has_hit": False},
                "company_screening": {
                    "found": True,
                    "source": "opencorporates",
                    "sanctions": sanctions_record,
                },
                "director_screenings": [{
                    "person_name": "Alex Director",
                    "person_type": "director",
                    "declared_pep": "No",
                    "screening": {
                        "matched": False,
                        "results": [],
                        "source": "complyadvantage",
                        "provider": "ComplyAdvantage",
                        "api_status": "live",
                    },
                }],
                "ubo_screenings": [],
                "overall_flags": [],
                "total_hits": 1 if matched else 0,
                "any_sanctions_hits": matched,
                "has_company_screening_hit": matched,
            }
        }),
    }
    if disposition_code:
        app["screening_reviews"] = [{
            "subject_type": "entity",
            "subject_name": "Validation Severity Ltd",
            "disposition": "cleared" if disposition_code == "false_positive_cleared" else disposition_code,
            "disposition_code": disposition_code,
            "rationale": "Officer reviewed provider hit against registry records and retained case evidence.",
            "notes": "Provider case CA-VAL-001 and registry extract retained.",
            "evidence_reference": "Provider case CA-VAL-001 and registry extract retained.",
            "reviewer_id": "co001",
            "reviewer_name": "Compliance Officer",
            "created_at": "2026-05-17T10:00:00Z",
            "audit_confirmed": True,
            "requires_four_eyes": disposition_code == "false_positive_cleared",
            "second_reviewer_id": "sco001" if disposition_code == "false_positive_cleared" else None,
            "second_reviewer_name": "Senior Compliance Officer" if disposition_code == "false_positive_cleared" else None,
            "second_reviewed_at": "2026-05-17T10:30:00Z" if disposition_code == "false_positive_cleared" else None,
            "second_rationale": "Second reviewer confirmed the false-positive rationale and evidence." if disposition_code == "false_positive_cleared" else None,
        }]

    directors = [{
        "full_name": "Alex Director",
        "nationality": "United Kingdom",
        "date_of_birth": "1980-01-01",
        "is_pep": "No",
        "ownership_pct": 0,
    }]
    ubos = [{
        "full_name": "Jamie UBO",
        "nationality": "United Kingdom",
        "date_of_birth": "1985-02-02",
        "is_pep": "No",
        "ownership_pct": 100,
    }]
    documents = [
        {"doc_type": "Certificate of Incorporation", "verification_status": "verified"},
        {"doc_type": "UBO Identity Document", "verification_status": "verified"},
    ]
    return build_compliance_memo(app, directors, ubos, documents)


def test_false_positive_cleared_with_evidence_passes_validation_and_supervisor():
    memo = _production_shaped_memo()

    validation = validate_compliance_memo(copy.deepcopy(memo))
    supervisor = run_memo_supervisor(copy.deepcopy(memo))

    assert validation["validation_status"] == "pass"
    assert supervisor["verdict"] in ("CONSISTENT", "CONSISTENT_WITH_WARNINGS")
    assert supervisor["mandatory_escalation"] is False
    assert supervisor["can_approve"] is True
    assert not any(c["category"] == "pep_inconsistency" for c in supervisor["contradictions"])


def test_generated_clean_low_completed_clear_validates_as_pass():
    memo, _, supervisor, validation = _generated_memo_for_screening_disposition()

    assert memo["metadata"]["screening_state_summary"]["canonical_state"] == "completed_clear"
    assert memo["metadata"]["screening_state_summary"]["defensible_clear"] is True
    assert validation["validation_status"] == "pass"
    assert not any(
        issue["category"] == "ownership_risk" and issue["severity"] == "critical"
        for issue in validation["issues"]
    )
    assert not any(
        issue["category"] == "screening" and issue["severity"] == "warning"
        for issue in validation["issues"]
    )
    assert supervisor["can_approve"] is True
    assert validation["warning_count"] == 0


def test_generated_false_positive_cleared_validates_as_pass():
    memo, _, supervisor, validation = _generated_memo_for_screening_disposition(
        disposition_code="false_positive_cleared"
    )

    summary = memo["metadata"]["screening_state_summary"]
    screening_text = memo["sections"]["screening_results"]["content"].lower()
    assert summary["canonical_state"] == "completed_match"
    assert summary["has_formally_cleared_match"] is True
    assert summary["approval_blocking"] is False
    assert summary["defensible_clear"] is True
    assert summary["screening_gate_ready"] is True
    assert "false positive" in screening_text
    assert "not a clear no-match result" in screening_text
    assert validation["validation_status"] == "pass"
    assert supervisor["can_approve"] is True
    assert validation["warning_count"] == 0


def test_generated_raw_completed_match_does_not_clean_pass_validation():
    memo, _, _, validation = _generated_memo_for_screening_disposition(raw_match=True)

    assert memo["metadata"]["screening_state_summary"]["approval_blocking"] is True
    assert validation["validation_status"] != "pass"
    assert any(
        issue["category"] == "screening" and issue["severity"] == "critical"
        for issue in validation["issues"]
    )


def test_generated_blocking_screening_dispositions_do_not_clean_pass_validation():
    for disposition_code in ("true_match", "material_concern", "needs_more_information", "escalated_to_edd"):
        memo, _, _, validation = _generated_memo_for_screening_disposition(
            disposition_code=disposition_code
        )

        assert memo["metadata"]["screening_state_summary"]["approval_blocking"] is True, disposition_code
        assert validation["validation_status"] != "pass", disposition_code
        assert any(
            issue["category"] == "screening" and issue["severity"] == "critical"
            for issue in validation["issues"]
        ), disposition_code


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
