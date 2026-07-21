import json

import pytest

from security_hardening import (
    APPROVAL_ROUTE_BLOCKED,
    APPROVAL_ROUTE_COMPLIANCE_REQUIRED,
    APPROVAL_ROUTE_DIRECT_LOW_MEDIUM,
    APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED,
    APPROVAL_ROUTE_REJECTED,
    DECISION_ELIGIBILITY_BLOCKED,
    DECISION_ELIGIBILITY_ELIGIBLE,
    _policy_approval_route,
    classify_approval_route,
)


def _app(level, **overrides):
    app = {
        "id": f"route-{level.lower()}",
        "ref": f"ROUTE-{level}",
        "status": "compliance_review",
        "risk_level": level,
        "final_risk_level": level,
        "risk_escalations": "[]",
        "prescreening_data": json.dumps({"screening_report": {"status": "clear"}}),
    }
    app.update(overrides)
    return app


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("LOW", APPROVAL_ROUTE_DIRECT_LOW_MEDIUM),
        ("MEDIUM", APPROVAL_ROUTE_COMPLIANCE_REQUIRED),
        ("HIGH", APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED),
        ("VERY_HIGH", APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED),
    ],
)
def test_founder_approved_risk_level_route_matrix(level, expected):
    result = classify_approval_route(_app(level))

    assert result["route"] == expected
    assert result["approval_route"] == expected
    assert result["decision_eligibility"] == DECISION_ELIGIBILITY_ELIGIBLE
    assert result["eligibility_reason"] == ""
    assert result["requires_compliance_package"] is (
        expected in {
            APPROVAL_ROUTE_COMPLIANCE_REQUIRED,
            APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED,
        }
    )
    assert result["requires_dual_control"] is (
        expected == APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED
    )
    assert result["direct_low_medium"] is (
        expected == APPROVAL_ROUTE_DIRECT_LOW_MEDIUM
    )


@pytest.mark.parametrize("screening_state", ["pending", "pending_provider", "unresolved"])
def test_pending_or_unresolved_screening_blocks_without_changing_policy_route(screening_state):
    app = _app(
        "MEDIUM",
        prescreening_data=json.dumps(
            {"screening_report": {"screening_state": screening_state}}
        ),
    )

    route = classify_approval_route(app)

    assert route["route"] == APPROVAL_ROUTE_BLOCKED
    assert route["approval_route"] == APPROVAL_ROUTE_COMPLIANCE_REQUIRED
    assert route["decision_eligibility"] == DECISION_ELIGIBILITY_BLOCKED
    assert route["eligibility_reason"] == "screening_pending_or_unresolved"
    assert "screening_pending_or_unresolved" in route["reasons"]
    assert _policy_approval_route("MEDIUM", []) == APPROVAL_ROUTE_COMPLIANCE_REQUIRED


def test_rejected_application_returns_rejected_route():
    route = classify_approval_route(_app("LOW", status="rejected"))

    assert route["route"] == APPROVAL_ROUTE_REJECTED
    assert route["approval_route"] == APPROVAL_ROUTE_REJECTED
    assert route["decision_eligibility"] == DECISION_ELIGIBILITY_BLOCKED
    assert route["eligibility_reason"] == "rejected"
    assert "terminal_state" not in route["reasons"]


def test_clear_screening_does_not_block_low_route():
    route = classify_approval_route(_app("LOW"))

    assert route["route"] == APPROVAL_ROUTE_DIRECT_LOW_MEDIUM
    assert route["approval_route"] == APPROVAL_ROUTE_DIRECT_LOW_MEDIUM
    assert route["decision_eligibility"] == DECISION_ELIGIBILITY_ELIGIBLE
    assert "screening_pending_or_unresolved" not in route["reasons"]


@pytest.mark.parametrize(
    ("level", "status", "expected_route", "reason"),
    [
        ("LOW", "approved", APPROVAL_ROUTE_DIRECT_LOW_MEDIUM, "terminal_state"),
        ("MEDIUM", "approved", APPROVAL_ROUTE_COMPLIANCE_REQUIRED, "terminal_state"),
        ("HIGH", "approved", APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED, "terminal_state"),
        ("VERY_HIGH", "approved", APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED, "terminal_state"),
        (
            "MEDIUM", "kyc_documents", APPROVAL_ROUTE_COMPLIANCE_REQUIRED,
            "case_stage_not_decisionable",
        ),
    ],
)
def test_lifecycle_block_does_not_overwrite_underlying_policy_route(
    level, status, expected_route, reason
):
    result = classify_approval_route(_app(level, status=status))

    assert result["approval_route"] == expected_route
    assert result["decision_eligibility"] == DECISION_ELIGIBILITY_BLOCKED
    assert result["eligibility_reason"] == reason
    # The effective route remains blocked, preserving every existing gate.
    assert result["route"] == APPROVAL_ROUTE_BLOCKED
    assert result["requires_compliance_package"] is False
    assert result["requires_dual_control"] is False
    assert result["direct_low_medium"] is False
