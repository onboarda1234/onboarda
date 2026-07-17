"""SRP-2 batch-1 findings: two product defects surfaced by the staged rollout.

RISK-FC-1 — a conflict-errored re-screen stored a 0-hit non-terminal report and
the risk recompute dropped the application HIGH -> LOW (TESCO, 55 -> 12.3).
Risk must never DECREASE on non-terminal/degraded screening evidence; raises
stay allowed (holding a raise would itself be fail-open).

RESCREEN-1 (classification half) — re-screening an already-screened subject
errors Mesh customer-creation ("external identifier already assigned"). The
report must carry a distinct, honest degraded source instead of a generic
workflow error, so officers and dashboards can tell "provider re-screen
wiring needed" from "provider outage".
"""

import inspect
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_db():
    from db import get_db
    return get_db()


def _make_user():
    return {"sub": "user-srp2b1", "name": "Batch1 Officer", "role": "admin"}


def _insert_scored_app(db, *, risk_score, risk_level, report):
    suffix = uuid.uuid4().hex[:8].replace("e", "d")  # avoid fixture text tokens
    app_id = f"app-srp2b1-{suffix}"
    app_ref = f"ARF-SRP2B1-{suffix}"
    prescreening = json.dumps({
        "operating_countries": ["Mauritius"],
        "target_markets": ["Mauritius"],
        "source_of_wealth": "Business profits",
        "source_of_funds": "Revenue",
        "monthly_volume": "50000",
        "cross_border": False,
        "screening_report": report,
    })
    db.execute(
        """INSERT INTO applications
           (id, ref, company_name, country, sector, entity_type,
            status, risk_score, risk_level, risk_dimensions,
            onboarding_lane, prescreening_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, app_ref, "Batch1 Regression Ltd", "Mauritius", "Banking",
         "NBFI", "submitted", risk_score, risk_level,
         json.dumps({"d1": 1.5, "d2": 1.5, "d3": 1.5, "d4": 1.5, "d5": 1.5}),
         "Standard Review", prescreening),
    )
    db.commit()
    return app_id, app_ref


def _non_terminal_report():
    """The exact stored shape SRP-2 batch 1 produced from the conflict failure."""
    return {
        "provider": "complyadvantage",
        "screening_mode": "unknown",
        "total_hits": 0,
        "degraded_sources": ["complyadvantage_workflow_errored"],
        "any_non_terminal_subject": True,
        "overall_flags": [],
        "company_screening": {}, "director_screenings": [], "ubo_screenings": [],
    }


def _terminal_clean_report():
    return {
        "provider": "complyadvantage",
        "screening_mode": "live",
        "screened_at": "2026-07-17T00:00:00Z",
        "total_hits": 0,
        "degraded_sources": [],
        "any_non_terminal_subject": False,
        "overall_flags": [],
        "company_screening": {"found": True},
        "director_screenings": [], "ubo_screenings": [],
    }


class TestRiskFailClosedHold:
    def test_non_terminal_report_cannot_lower_risk(self, temp_db):
        from rule_engine import recompute_risk

        db = _get_db()
        try:
            app_id, _ = _insert_scored_app(
                db, risk_score=55.0, risk_level="HIGH", report=_non_terminal_report()
            )
            result = recompute_risk(db, app_id, "screening_rerun", user=_make_user())
            db.commit()
            assert result["held_non_terminal_screening"] is True
            assert result["recomputed"] is False
            row = db.execute(
                "SELECT risk_score, risk_level FROM applications WHERE id=?", (app_id,)
            ).fetchone()
            assert row["risk_score"] == 55.0
            assert row["risk_level"] == "HIGH"
        finally:
            db.close()

    def test_terminal_report_recomputes_normally(self, temp_db):
        from rule_engine import recompute_risk

        db = _get_db()
        try:
            app_id, _ = _insert_scored_app(
                db, risk_score=55.0, risk_level="HIGH", report=_terminal_clean_report()
            )
            result = recompute_risk(db, app_id, "screening_rerun", user=_make_user())
            db.commit()
            assert result.get("held_non_terminal_screening") is None
            assert result["recomputed"] is True
            row = db.execute(
                "SELECT risk_score FROM applications WHERE id=?", (app_id,)
            ).fetchone()
            assert row["risk_score"] == result["new_score"]
        finally:
            db.close()

    def test_raises_still_allowed_on_non_terminal_report(self, temp_db):
        from rule_engine import recompute_risk

        db = _get_db()
        try:
            app_id, _ = _insert_scored_app(
                db, risk_score=0.5, risk_level="LOW", report=_non_terminal_report()
            )
            result = recompute_risk(db, app_id, "screening_rerun", user=_make_user())
            db.commit()
            assert result["recomputed"] is True
            assert result["new_score"] > 0.5
        finally:
            db.close()

    def test_detector_ignores_absent_report(self):
        from rule_engine import _screening_report_is_non_terminal

        assert _screening_report_is_non_terminal(None) is False
        assert _screening_report_is_non_terminal({}) is False
        assert _screening_report_is_non_terminal(_terminal_clean_report()) is False
        assert _screening_report_is_non_terminal(_non_terminal_report()) is True
        assert _screening_report_is_non_terminal({"screening_mode": "unknown", "total_hits": 1}) is True


class TestCustomerConflictClassification:
    def test_conflict_detected_from_raw_step_detail(self):
        from screening_complyadvantage.orchestrator import _customer_identifier_conflict

        raw = {
            "step_details": {
                "customer-creation": {
                    "status": "ERRORED",
                    "step_output": {
                        "error": "External identifier app-1:company:name-x:strict is already assigned to customer 019f..."
                    },
                }
            }
        }
        assert _customer_identifier_conflict(raw) is True
        # Alternate phrasing tolerated
        raw["step_details"]["customer-creation"]["step_output"] = {
            "message": "customer with this external_identifier already exists"
        }
        assert _customer_identifier_conflict(raw) is True

    def test_unrelated_errors_are_not_conflicts(self):
        from screening_complyadvantage.orchestrator import _customer_identifier_conflict

        assert _customer_identifier_conflict({}) is False
        assert _customer_identifier_conflict({"step_details": {}}) is False
        assert _customer_identifier_conflict({
            "step_details": {"customer-creation": {"status": "ERRORED",
                                                   "step_output": {"error": "internal provider timeout"}}}
        }) is False
        assert _customer_identifier_conflict({
            "step_details": {"screening": {"status": "ERRORED",
                                           "step_output": {"error": "already assigned"}}}
        }) is False

    def test_mark_report_customer_conflict_is_idempotent_and_distinct(self):
        from screening_complyadvantage.orchestrator import (
            CUSTOMER_CONFLICT_DEGRADED_SOURCE,
            _mark_report_customer_conflict,
        )

        report = {"degraded_sources": ["complyadvantage_workflow_errored"], "overall_flags": []}
        _mark_report_customer_conflict(report)
        _mark_report_customer_conflict(report)
        assert report["degraded_sources"].count(CUSTOMER_CONFLICT_DEGRADED_SOURCE) == 1
        assert report["customer_identifier_conflict"] is True
        assert sum("Re-screen blocked" in f for f in report["overall_flags"]) == 1
        # The distinct source coexists with (not replaces) the generic one.
        assert "complyadvantage_workflow_errored" in report["degraded_sources"]

    def test_two_pass_wiring_is_present(self):
        """Static pin: the conflict classification is wired into both the
        per-pass degraded branch and the two-pass report marker."""
        import screening_complyadvantage.orchestrator as orch

        run_one_pass = inspect.getsource(orch.ComplyAdvantageScreeningOrchestrator._run_one_pass)
        assert "_customer_identifier_conflict(polled.raw)" in run_one_pass
        two_pass = inspect.getsource(orch.ComplyAdvantageScreeningOrchestrator.screen_customer_two_pass)
        assert "_mark_report_customer_conflict(report)" in two_pass

    def test_conflict_report_holds_risk_end_to_end(self, temp_db):
        """A conflict-degraded report must trigger the fail-closed risk hold."""
        from rule_engine import recompute_risk
        from screening_complyadvantage.orchestrator import _mark_report_customer_conflict

        report = _non_terminal_report()
        report["degraded_sources"] = []
        _mark_report_customer_conflict(report)

        db = _get_db()
        try:
            app_id, _ = _insert_scored_app(db, risk_score=55.0, risk_level="HIGH", report=report)
            result = recompute_risk(db, app_id, "screening_rerun", user=_make_user())
            db.commit()
            assert result["held_non_terminal_screening"] is True
            row = db.execute(
                "SELECT risk_score, risk_level FROM applications WHERE id=?", (app_id,)
            ).fetchone()
            assert (row["risk_score"], row["risk_level"]) == (55.0, "HIGH")
        finally:
            db.close()
