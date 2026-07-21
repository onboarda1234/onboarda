import json

import pytest

from security_hardening import (
    APPROVAL_ROUTE_BLOCKED,
    APPROVAL_ROUTE_COMPLIANCE_REQUIRED,
    APPROVAL_ROUTE_DIRECT_LOW_MEDIUM,
    APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED,
    APPROVAL_ROUTE_REJECTED,
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
    assert classify_approval_route(_app(level))["route"] == expected


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
    assert "screening_pending_or_unresolved" in route["reasons"]
    assert _policy_approval_route("MEDIUM", []) == APPROVAL_ROUTE_COMPLIANCE_REQUIRED


def test_rejected_application_returns_rejected_route():
    route = classify_approval_route(_app("LOW", status="rejected"))

    assert route["route"] == APPROVAL_ROUTE_REJECTED
    assert "terminal_state" not in route["reasons"]


def test_clear_screening_does_not_block_low_route():
    route = classify_approval_route(_app("LOW"))

    assert route["route"] == APPROVAL_ROUTE_DIRECT_LOW_MEDIUM
    assert "screening_pending_or_unresolved" not in route["reasons"]
