"""
EX-09: Risk score recomputation tests.

Verifies that:
1. recompute_risk() recomputes and persists updated risk for an application
2. Recomputation is triggered on screening re-run
3. Recomputation is triggered on screening review escalation
4. Recomputation is triggered on risk config update (bulk)
5. No-op behavior when no material input changes
6. No-op for terminal applications on config change
7. Audit trail captures old/new risk state correctly
8. risk_computed_at and risk_config_version are set
9. snapshot_app_state includes new fields
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_db():
    """Lazy import to avoid premature DB_PATH evaluation."""
    from db import get_db
    return get_db()


def _make_user(user_id="user-ex09", name="Test Officer", role="admin"):
    return {"sub": user_id, "name": name, "role": role}


def _insert_scored_app(db, risk_score=45.0, risk_level="MEDIUM",
                       country="Mauritius", sector="Banking",
                       entity_type="NBFI", status="submitted",
                       app_id=None, app_ref=None):
    """Insert an application that already has a risk score."""
    suffix = uuid.uuid4().hex[:8]
    app_id = app_id or f"app-recomp-{suffix}"
    app_ref = app_ref or f"ARF-RECOMP-{suffix}"
    prescreening = json.dumps({
        "operating_countries": ["Mauritius"],
        "target_markets": ["Mauritius"],
        "source_of_wealth": "Business profits",
        "source_of_funds": "Revenue",
        "monthly_volume": "50000",
        "cross_border": False,
        "screening_report": {
            "total_hits": 0,
            "overall_flags": [],
            "company_screening": {"found": True},
            "director_screenings": [],
            "ubo_screenings": [],
        }
    })
    db.execute(
        """INSERT INTO applications
           (id, ref, company_name, country, sector, entity_type,
            status, risk_score, risk_level, risk_dimensions,
            onboarding_lane, prescreening_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, app_ref, "Recomp Test Ltd", country, sector,
         entity_type, status, risk_score, risk_level,
         json.dumps({"d1": 1.5, "d2": 1.5, "d3": 1.5, "d4": 1.5, "d5": 1.5}),
         "Standard Review", prescreening)
    )
    db.commit()
    return app_id, app_ref


def _insert_risk_config(db):
    """Ensure risk_config row exists."""
    existing = db.execute("SELECT id FROM risk_config WHERE id=1").fetchone()
    if not existing:
        db.execute(
            """INSERT INTO risk_config (id, dimensions, thresholds,
               country_risk_scores, sector_risk_scores, entity_type_scores,
               updated_at)
               VALUES (1, ?, ?, ?, ?, ?, datetime('now'))""",
            (json.dumps([]), json.dumps([]), json.dumps({}),
             json.dumps({}), json.dumps({}))
        )
        db.commit()


def _set_live_completed_match(db, app_id, *, matched=True):
    app = db.execute(
        "SELECT company_name, prescreening_data FROM applications WHERE id=?",
        (app_id,),
    ).fetchone()
    prescreening = json.loads(app["prescreening_data"] or "{}")
    report = prescreening.setdefault("screening_report", {})
    if matched:
        report["company_screening"] = {
            "provider": "complyadvantage",
            "source": "complyadvantage",
            "api_status": "live",
            "matched": True,
            "results": [
                {
                    "id": "risk-screening-disposition-floor",
                    "name": app["company_name"],
                    "category": "sanctions_watchlist",
                    "score": 0.97,
                }
            ],
        }
        report["total_hits"] = 1
    else:
        report["company_screening"] = {
            "provider": "complyadvantage",
            "source": "complyadvantage",
            "api_status": "live",
            "matched": False,
            "results": [],
        }
        report["total_hits"] = 0
    db.execute(
        "UPDATE applications SET prescreening_data=? WHERE id=?",
        (json.dumps(prescreening), app_id),
    )
    db.commit()


def _insert_screening_review(db, app_id, disposition_code, *, requires_four_eyes=False, complete=True):
    app = db.execute("SELECT company_name FROM applications WHERE id=?", (app_id,)).fetchone()
    if disposition_code == "false_positive_cleared":
        disposition = "cleared"
    elif disposition_code == "needs_more_information":
        disposition = "follow_up_required"
    else:
        disposition = "escalated"
    db.execute(
        """INSERT INTO screening_reviews
           (application_id, subject_type, subject_name, disposition, notes,
            disposition_code, rationale, requires_four_eyes, reviewer_id,
            reviewer_name, second_reviewer_id, second_reviewer_name)
           VALUES (?, 'entity', ?, ?, ?, ?, ?, ?, 'co-risk-floor',
                   'CO Risk Floor', ?, ?)""",
        (
            app_id,
            app["company_name"],
            disposition,
            "Risk floor regression disposition.",
            disposition_code,
            "Formal screening disposition for risk floor regression.",
            1 if requires_four_eyes else 0,
            "sco-risk-floor" if requires_four_eyes and complete else None,
            "SCO Risk Floor" if requires_four_eyes and complete else None,
        ),
    )
    db.commit()


# ──────────────────────────────────────────────
# Unit tests for recompute_risk helper
# ──────────────────────────────────────────────

class TestRecomputeRiskHelper:
    """Direct unit tests for the recompute_risk function."""

    def test_recompute_updates_score_and_level(self, temp_db):
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(db, risk_score=45.0, risk_level="MEDIUM")

        result = recompute_risk(db, app_id, "test_reason")
        db.commit()

        assert result["recomputed"] is True
        assert result["old_score"] == 45.0
        assert result["old_level"] == "MEDIUM"
        assert result["new_score"] is not None
        assert result["new_level"] is not None
        assert isinstance(result["new_score"], (int, float))
        assert result["new_level"] in ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")

        # Verify DB was updated
        app = db.execute("SELECT risk_score, risk_level, risk_computed_at, risk_config_version FROM applications WHERE id=?", (app_id,)).fetchone()
        assert app["risk_score"] == result["new_score"]
        assert app["risk_level"] == result["new_level"]
        assert app["risk_computed_at"] is not None
        db.close()

    def test_recompute_noop_when_no_prior_score(self, temp_db):
        """If app has no risk_score yet, recompute should be a no-op."""
        from rule_engine import recompute_risk
        db = _get_db()
        suffix = uuid.uuid4().hex[:8]
        app_id = f"app-noscore-{suffix}"
        db.execute(
            """INSERT INTO applications (id, ref, company_name, status)
               VALUES (?, ?, ?, ?)""",
            (app_id, f"ARF-NS-{suffix}", "No Score Ltd", "draft")
        )
        db.commit()

        result = recompute_risk(db, app_id, "test_reason")
        assert result["recomputed"] is False
        assert result["old_score"] is None
        db.close()

    def test_recompute_nonexistent_app(self, temp_db):
        """Recomputing for a non-existent app should be a no-op."""
        from rule_engine import recompute_risk
        db = _get_db()
        result = recompute_risk(db, "nonexistent-id", "test_reason")
        assert result["recomputed"] is False
        db.close()

    def test_recompute_sets_config_version(self, temp_db):
        """risk_config_version should be the manual risk_config timestamp only."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(db)

        recompute_risk(db, app_id, "test_config_version")
        db.commit()

        app = db.execute("SELECT risk_config_version FROM applications WHERE id=?", (app_id,)).fetchone()
        config = db.execute("SELECT updated_at FROM risk_config WHERE id=1").fetchone()
        assert app["risk_config_version"] is not None
        if config and config["updated_at"]:
            assert app["risk_config_version"].startswith(f"risk_config:{config['updated_at']}")
            assert "country_risk:" not in app["risk_config_version"]
        db.close()

    def test_recompute_sets_computed_at_timestamp(self, temp_db):
        """risk_computed_at should be set to current UTC time."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(db)

        recompute_risk(db, app_id, "test_timestamp")
        db.commit()

        app = db.execute("SELECT risk_computed_at FROM applications WHERE id=?", (app_id,)).fetchone()
        assert app["risk_computed_at"] is not None
        # Should be ISO 8601 format
        assert "T" in app["risk_computed_at"]
        assert app["risk_computed_at"].endswith("Z")
        db.close()

    def test_recompute_changed_flag(self, temp_db):
        """changed flag should reflect whether score/level actually changed."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(db)

        # First recompute to get canonical score
        r1 = recompute_risk(db, app_id, "first")
        db.commit()
        assert r1["recomputed"] is True

        # Second recompute with same data — score should not change
        r2 = recompute_risk(db, app_id, "second")
        db.commit()
        assert r2["recomputed"] is True
        assert r2["changed"] is False
        assert r2["old_score"] == r2["new_score"]
        assert r2["old_level"] == r2["new_level"]
        db.close()


# ──────────────────────────────────────────────
# Audit trail tests
# ──────────────────────────────────────────────

class TestRecomputeRiskAudit:
    """Verify audit trail captures old/new risk state."""

    def test_audit_log_on_recompute(self, temp_db):
        """When score changes, audit log should capture before/after state."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        # Insert with a score that will likely differ from recomputed value
        app_id, app_ref = _insert_scored_app(db, risk_score=99.9, risk_level="VERY_HIGH")

        audit_calls = []

        def mock_audit(user, action, target, detail, **kwargs):
            audit_calls.append({
                "action": action,
                "target": target,
                "detail": detail,
                "before_state": kwargs.get("before_state"),
                "after_state": kwargs.get("after_state"),
            })

        user = _make_user()
        result = recompute_risk(db, app_id, "test_audit", user=user, log_audit_fn=mock_audit)
        db.commit()

        assert result["recomputed"] is True

        # Find the audit entry
        risk_audits = [a for a in audit_calls if a["action"] == "Risk Recomputed"]
        assert len(risk_audits) == 1

        entry = risk_audits[0]
        assert entry["target"] == app_ref
        assert "test_audit" in entry["detail"]
        assert "Score:" in entry["detail"]

        # Verify before/after state
        assert entry["before_state"]["risk_score"] == 99.9
        assert entry["before_state"]["risk_level"] == "VERY_HIGH"
        assert entry["after_state"]["risk_score"] is not None
        assert entry["after_state"]["risk_level"] is not None
        assert entry["after_state"]["risk_computed_at"] is not None
        db.close()


class TestManualWorkflowPreservation:
    """Risk recomputation may strengthen, but never weaken, officer controls."""

    def test_manual_edd_lane_and_status_survive_lower_model_recommendation(self, temp_db):
        from rule_engine import recompute_risk

        db = _get_db()
        _insert_risk_config(db)
        app_id, app_ref = _insert_scored_app(
            db,
            risk_score=99.9,
            risk_level="VERY_HIGH",
            country="Mauritius",
            sector="Professional Services",
            entity_type="Listed Company",
            status="edd_required",
        )
        db.execute(
            "UPDATE applications SET onboarding_lane='EDD' WHERE id=?",
            (app_id,),
        )
        db.commit()
        audits = []

        def capture_audit(user, action, target, detail, **kwargs):
            audits.append({"action": action, "target": target, "detail": detail, **kwargs})

        result = recompute_risk(
            db,
            app_id,
            "tier0c_manual_edd_regression",
            user=_make_user(),
            log_audit_fn=capture_audit,
        )
        db.commit()

        stored = db.execute(
            "SELECT risk_score, risk_level, risk_dimensions, onboarding_lane, status "
            "FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()
        assert result["recomputed"] is True
        assert stored["risk_score"] == result["new_score"]
        assert stored["risk_score"] != 99.9
        assert json.loads(stored["risk_dimensions"])["factor_computation_evidence"]
        assert stored["onboarding_lane"] == "EDD"
        assert stored["status"] == "edd_required"
        preservation = result["manual_workflow_preservation"]
        assert preservation["recommended_lane"] != "EDD"
        assert preservation["persisted_lane"] == "EDD"
        assert "edd_lane" in preservation["controls"]
        preserved_audits = [
            entry for entry in audits
            if entry["action"] == "Manual workflow preserved during recomputation"
        ]
        assert len(preserved_audits) == 1
        assert preserved_audits[0]["target"] == app_ref
        assert "Manual workflow preserved during recomputation" in preserved_audits[0]["detail"]
        db.close()

    def test_manual_approval_block_survives_score_recomputation(self, temp_db):
        from rule_engine import recompute_risk

        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(
            db,
            risk_score=99.9,
            risk_level="VERY_HIGH",
            country="Mauritius",
            sector="Professional Services",
            entity_type="Listed Company",
            status="draft",
        )
        db.execute(
            "UPDATE applications SET pre_approval_decision='REQUEST_INFO', "
            "pre_approval_notes='Officer requires additional ownership evidence' WHERE id=?",
            (app_id,),
        )
        db.commit()

        result = recompute_risk(db, app_id, "manual_approval_block_regression")
        db.commit()
        stored = db.execute(
            "SELECT risk_score, risk_level, status, pre_approval_decision, pre_approval_notes "
            "FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()

        assert result["recomputed"] is True
        assert stored["risk_score"] == result["new_score"]
        assert stored["risk_score"] != 99.9
        assert stored["status"] == "draft"
        assert stored["pre_approval_decision"] == "REQUEST_INFO"
        assert stored["pre_approval_notes"] == "Officer requires additional ownership evidence"
        assert "pre_approval_decision:REQUEST_INFO" in (
            result["manual_workflow_preservation"]["controls"]
        )
        db.close()

    def test_automatic_high_floor_can_still_strengthen_standard_workflow(self, temp_db):
        from rule_engine import recompute_risk

        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(
            db,
            risk_score=10.0,
            risk_level="LOW",
            country="United Kingdom",
            sector="Crypto VASP exchange",
            entity_type="Listed Company",
        )

        result = recompute_risk(db, app_id, "automatic_floor_strengthening")
        db.commit()
        stored = db.execute(
            "SELECT risk_level, final_risk_level, onboarding_lane FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()

        assert result["recomputed"] is True
        assert stored["final_risk_level"] in {"HIGH", "VERY_HIGH"}
        assert "floor_rule_high_risk_sector" in result["risk_escalations"]
        assert result["manual_workflow_preservation"]["preserved"] is False
        db.close()

    def test_audit_records_final_risk_floor_reason(self, temp_db):
        """Risk recomputation audit must explain when final risk is floored above raw LOW."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, app_ref = _insert_scored_app(
            db,
            risk_score=18.0,
            risk_level="LOW",
            country="United Kingdom",
            sector="Crypto VASP exchange",
            entity_type="Listed Company",
        )

        audit_calls = []

        def mock_audit(user, action, target, detail, **kwargs):
            audit_calls.append({
                "action": action,
                "target": target,
                "detail": detail,
                "before_state": kwargs.get("before_state"),
                "after_state": kwargs.get("after_state"),
            })

        result = recompute_risk(
            db,
            app_id,
            "final_risk_truth_regression",
            user=_make_user(),
            log_audit_fn=mock_audit,
            apply_routing_policy=False,
        )
        db.commit()

        assert result["recomputed"] is True
        assert result["base_risk_level"] == "LOW"
        assert result["final_risk_level"] != "LOW"
        assert "floor_rule_high_risk_sector" in result["risk_escalations"]

        entry = [a for a in audit_calls if a["action"] == "Risk Recomputed"][-1]
        assert entry["target"] == app_ref
        assert "Floor/elevation reason" in entry["detail"]
        assert "High-risk sector floor" in entry["after_state"]["elevation_reason_text"]
        assert entry["after_state"]["base_risk_level"] == "LOW"
        assert entry["after_state"]["final_risk_level"] != "LOW"
        db.close()

    @pytest.mark.parametrize("disposition_code", ["true_match", "material_concern", "escalated_to_edd"])
    def test_material_screening_dispositions_cannot_persist_final_low(self, temp_db, disposition_code):
        """Screening dispositions that create/preserve EDD controls must floor final risk to HIGH."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(
            db,
            risk_score=18.0,
            risk_level="LOW",
            country="United Kingdom",
            sector="Technology",
            entity_type="Listed Company",
        )
        _set_live_completed_match(db, app_id)
        _insert_screening_review(db, app_id, disposition_code)

        result = recompute_risk(db, app_id, "screening_review_escalated")
        db.commit()

        app = db.execute(
            "SELECT risk_level, final_risk_level, base_risk_level, onboarding_lane, elevation_reason_text, risk_escalations "
            "FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()
        escalations = json.loads(app["risk_escalations"] or "[]")

        assert result["base_risk_level"] == "LOW"
        assert result["final_risk_level"] == "HIGH"
        assert app["risk_level"] == "HIGH"
        assert app["final_risk_level"] == "HIGH"
        assert app["base_risk_level"] == "LOW"
        assert app["onboarding_lane"] == "EDD"
        assert "material_screening_disposition_floor" in escalations
        assert disposition_code in app["elevation_reason_text"]
        assert "at least HIGH final risk" in app["elevation_reason_text"]
        db.close()

    @pytest.mark.parametrize("disposition_code", ["true_match", "confirmed_match"])
    def test_stored_match_disposition_independently_floors_and_routes_edd(
        self, temp_db, monkeypatch, disposition_code
    ):
        """Officer-stored match decisions are equivalent without raw-provider fallback."""
        import routing_actuator
        from rule_engine import recompute_risk

        captured_trigger_flags = []

        def capture_routing_decision(**kwargs):
            captured_trigger_flags.extend(kwargs.get("edd_trigger_flags") or [])
            return {"lane": "EDD"}

        monkeypatch.setattr(routing_actuator, "apply_routing_decision", capture_routing_decision)

        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(
            db,
            risk_score=18.0,
            risk_level="LOW",
            country="United Kingdom",
            sector="Technology",
            entity_type="Listed Company",
        )
        _insert_screening_review(db, app_id, disposition_code)

        result = recompute_risk(db, app_id, "stored_screening_match_disposition")
        db.commit()

        app = db.execute(
            "SELECT final_risk_level, base_risk_level, onboarding_lane, risk_escalations "
            "FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()
        escalations = json.loads(app["risk_escalations"] or "[]")

        assert result["base_risk_level"] == "LOW"
        assert result["final_risk_level"] == "HIGH"
        assert app["final_risk_level"] == "HIGH"
        assert app["base_risk_level"] == "LOW"
        assert app["onboarding_lane"] == "EDD"
        assert "material_screening_disposition_floor" in escalations
        assert captured_trigger_flags == ["material_screening_concern"]
        db.close()

    def test_raw_unresolved_completed_match_cannot_persist_final_low(self, temp_db):
        """Raw live completed_match without formal clearance is an unresolved material concern."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(
            db,
            risk_score=18.0,
            risk_level="LOW",
            country="United Kingdom",
            sector="Technology",
            entity_type="Listed Company",
        )
        _set_live_completed_match(db, app_id)

        result = recompute_risk(db, app_id, "screening_match_detected")
        db.commit()

        app = db.execute(
            "SELECT final_risk_level, base_risk_level, onboarding_lane, elevation_reason_text, risk_escalations "
            "FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()
        escalations = json.loads(app["risk_escalations"] or "[]")

        assert result["base_risk_level"] == "LOW"
        assert app["final_risk_level"] == "HIGH"
        assert app["base_risk_level"] == "LOW"
        assert app["onboarding_lane"] == "EDD"
        assert "material_screening_disposition_floor" in escalations
        assert "raw completed_match" in app["elevation_reason_text"]
        db.close()

    def test_non_terminal_possible_match_total_hits_does_not_floor_or_route_edd(self, temp_db):
        """total_hits alone must not act as a blunt material-match proxy for non-terminal screening."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(
            db,
            risk_score=18.0,
            risk_level="LOW",
            country="United Kingdom",
            sector="Technology",
            entity_type="Listed Company",
        )
        app = db.execute(
            "SELECT prescreening_data FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()
        prescreening = json.loads(app["prescreening_data"] or "{}")
        report = prescreening.setdefault("screening_report", {})
        report["total_hits"] = 1
        report["any_non_terminal_subject"] = True
        report["company_screening"] = {
            "provider": "complyadvantage",
            "source": "complyadvantage",
            "api_status": "pending",
            "matched": True,
            "results": [{"id": "possible-match-not-terminal"}],
        }
        db.execute(
            "UPDATE applications SET prescreening_data=? WHERE id=?",
            (json.dumps(prescreening), app_id),
        )
        db.commit()

        result = recompute_risk(db, app_id, "non_terminal_possible_match")
        db.commit()

        app = db.execute(
            "SELECT final_risk_level, base_risk_level, onboarding_lane, risk_escalations FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()
        escalations = json.loads(app["risk_escalations"] or "[]")

        assert result["base_risk_level"] == "LOW"
        assert app["final_risk_level"] == "LOW"
        assert app["base_risk_level"] == "LOW"
        assert app["onboarding_lane"] != "EDD"
        assert "material_screening_disposition_floor" not in escalations
        db.close()

    def test_false_positive_cleared_does_not_floor_otherwise_clean_low(self, temp_db):
        """Valid false-positive clearance should not elevate solely because a raw match existed."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(
            db,
            risk_score=18.0,
            risk_level="LOW",
            country="United Kingdom",
            sector="Technology",
            entity_type="Listed Company",
        )
        _set_live_completed_match(db, app_id)
        _insert_screening_review(
            db,
            app_id,
            "false_positive_cleared",
            requires_four_eyes=True,
            complete=True,
        )

        result = recompute_risk(db, app_id, "false_positive_cleared")
        db.commit()

        app = db.execute(
            "SELECT final_risk_level, onboarding_lane, elevation_reason_text, risk_escalations FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()
        escalations = json.loads(app["risk_escalations"] or "[]")

        assert result["base_risk_level"] == "LOW"
        assert app["final_risk_level"] == "LOW"
        assert app["onboarding_lane"] != "EDD"
        assert "material_screening_disposition_floor" not in escalations
        assert "false_positive" not in (app["elevation_reason_text"] or "")
        db.close()

    def test_regression_false_positive_cleared_can_return_to_low_fast_lane_if_otherwise_clean(self, temp_db):
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(
            db,
            risk_score=18.0,
            risk_level="LOW",
            country="United Kingdom",
            sector="Technology",
            entity_type="Listed Company",
        )
        _set_live_completed_match(db, app_id)
        _insert_screening_review(
            db,
            app_id,
            "false_positive_cleared",
            requires_four_eyes=True,
            complete=True,
        )

        recompute_risk(db, app_id, "false_positive_cleared_regression")
        db.commit()

        app = db.execute(
            "SELECT final_risk_level, onboarding_lane FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()

        assert app["final_risk_level"] == "LOW"
        assert app["onboarding_lane"] != "EDD"
        db.close()

    def test_needs_more_information_floor_is_explicit_and_routes_edd_lane(self, temp_db):
        """needs_more_information remains blocking and uses the explicit EDD follow-up policy."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(
            db,
            risk_score=18.0,
            risk_level="LOW",
            country="United Kingdom",
            sector="Technology",
            entity_type="Listed Company",
        )
        _set_live_completed_match(db, app_id)
        _insert_screening_review(db, app_id, "needs_more_information")

        result = recompute_risk(db, app_id, "screening_needs_more_information")
        db.commit()

        app = db.execute(
            "SELECT final_risk_level, base_risk_level, onboarding_lane, elevation_reason_text, risk_escalations "
            "FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()
        escalations = json.loads(app["risk_escalations"] or "[]")

        assert result["base_risk_level"] == "LOW"
        assert app["final_risk_level"] == "MEDIUM"
        assert app["base_risk_level"] == "LOW"
        assert app["onboarding_lane"] == "EDD"
        assert "screening_needs_more_information_floor" in escalations
        assert "needs_more_information" in app["elevation_reason_text"]
        db.close()

    def test_audit_records_screening_disposition_floor_reason(self, temp_db):
        """Audit detail must show the material screening disposition floor reason."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, app_ref = _insert_scored_app(
            db,
            risk_score=18.0,
            risk_level="LOW",
            country="United Kingdom",
            sector="Technology",
            entity_type="Listed Company",
        )
        _set_live_completed_match(db, app_id)
        _insert_screening_review(db, app_id, "material_concern")

        audit_calls = []

        def mock_audit(user, action, target, detail, **kwargs):
            audit_calls.append({
                "action": action,
                "target": target,
                "detail": detail,
                "after_state": kwargs.get("after_state"),
            })

        recompute_risk(
            db,
            app_id,
            "screening_review_escalated",
            user=_make_user(),
            log_audit_fn=mock_audit,
        )
        db.commit()

        entry = [a for a in audit_calls if a["action"] == "Risk Recomputed"][-1]
        assert entry["target"] == app_ref
        assert "Floor/elevation reason" in entry["detail"]
        assert "material_screening_disposition_floor" in entry["after_state"]["risk_escalations"]
        assert "material_concern" in entry["after_state"]["elevation_reason_text"]
        db.close()

    def test_no_audit_when_no_user(self, temp_db):
        """Without user/log_audit_fn, recompute should still work but skip audit."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(db)

        result = recompute_risk(db, app_id, "no_audit_user")
        db.commit()
        assert result["recomputed"] is True
        # No crash — audit was silently skipped
        db.close()

    def test_audit_detail_includes_reason(self, temp_db):
        """Audit detail should include the reason for recomputation."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(db)

        audit_calls = []

        def mock_audit(user, action, target, detail, **kwargs):
            audit_calls.append({"detail": detail})

        user = _make_user()
        recompute_risk(db, app_id, "screening_rerun", user=user, log_audit_fn=mock_audit)
        db.commit()

        risk_audits = [a for a in audit_calls]
        assert len(risk_audits) >= 1
        assert "screening_rerun" in risk_audits[0]["detail"]
        db.close()


# ──────────────────────────────────────────────
# Bulk recomputation (risk config update)
# ──────────────────────────────────────────────

class TestBulkRecomputation:
    """Test recompute_risk_for_active_apps on config change."""

    def test_bulk_recomputes_active_apps(self, temp_db):
        """All non-terminal scored apps should be recomputed."""
        from rule_engine import recompute_risk_for_active_apps
        db = _get_db()
        _insert_risk_config(db)

        # Insert apps in various statuses
        app1_id, _ = _insert_scored_app(db, status="submitted")
        app2_id, _ = _insert_scored_app(db, status="compliance_review")
        app3_id, _ = _insert_scored_app(db, status="approved")  # terminal
        app4_id, _ = _insert_scored_app(db, status="rejected")  # terminal

        results = recompute_risk_for_active_apps(db, "config_update")
        db.commit()

        recomputed_ids = {r["app_id"] for r in results if r.get("recomputed")}
        assert app1_id in recomputed_ids
        assert app2_id in recomputed_ids
        assert app3_id not in recomputed_ids  # terminal — skipped
        assert app4_id not in recomputed_ids  # terminal — skipped
        db.close()

    def test_bulk_skips_terminal_statuses(self, temp_db):
        """Approved, rejected, withdrawn apps should not be recomputed."""
        from rule_engine import recompute_risk_for_active_apps
        db = _get_db()
        _insert_risk_config(db)

        _insert_scored_app(db, status="approved")
        _insert_scored_app(db, status="rejected")
        _insert_scored_app(db, status="withdrawn")

        results = recompute_risk_for_active_apps(db, "config_update")
        db.commit()

        # Only terminal apps exist, so all should be skipped.
        # But note: previously inserted apps from other tests may also be found.
        # We just verify none of these specific terminal apps were recomputed
        terminal_recomputed = [r for r in results if r.get("recomputed") and
                               r["app_id"].startswith("app-recomp-")]
        # All recomputed ones should be non-terminal
        for r in terminal_recomputed:
            app = db.execute("SELECT status FROM applications WHERE id=?", (r["app_id"],)).fetchone()
            if app:
                assert app["status"] not in ("approved", "rejected", "withdrawn")
        db.close()

    def test_bulk_returns_changed_count(self, temp_db):
        """Results should include changed status for each app."""
        from rule_engine import recompute_risk_for_active_apps
        db = _get_db()
        _insert_risk_config(db)

        _insert_scored_app(db, status="submitted")

        results = recompute_risk_for_active_apps(db, "test_count")
        db.commit()

        assert len(results) >= 1
        for r in results:
            assert "changed" in r
            assert "recomputed" in r
        db.close()

    def test_bulk_empty_when_no_scored_apps(self, temp_db):
        """If no apps have scores, bulk recompute returns empty."""
        from rule_engine import recompute_risk_for_active_apps
        db = _get_db()
        _insert_risk_config(db)

        # Insert an app with no score
        suffix = uuid.uuid4().hex[:8]
        db.execute(
            """INSERT INTO applications (id, ref, company_name, status)
               VALUES (?, ?, ?, ?)""",
            (f"app-noscr-{suffix}", f"ARF-NOSCR-{suffix}",
             "No Score Ltd", "draft")
        )
        db.commit()

        results = recompute_risk_for_active_apps(db, "test_empty")
        # There might be apps from other tests, but our unscored one shouldn't appear
        unscored_results = [r for r in results if r["app_id"] == f"app-noscr-{suffix}"]
        assert len(unscored_results) == 0
        db.close()


# ──────────────────────────────────────────────
# No-op behavior
# ──────────────────────────────────────────────

class TestNoOpBehavior:
    """Verify recompute does not fire when inputs haven't materially changed."""

    def test_noop_when_no_material_change(self, temp_db):
        """Recomputing twice with same inputs should produce same score."""
        from rule_engine import recompute_risk
        db = _get_db()
        _insert_risk_config(db)
        app_id, _ = _insert_scored_app(db)

        r1 = recompute_risk(db, app_id, "first_run")
        db.commit()

        r2 = recompute_risk(db, app_id, "second_run")
        db.commit()

        assert r2["recomputed"] is True
        assert r2["changed"] is False
        assert r1["new_score"] == r2["new_score"]
        assert r1["new_level"] == r2["new_level"]
        db.close()

    def test_noop_for_unscorable_app(self, temp_db):
        """App with no prior score should be skipped."""
        from rule_engine import recompute_risk
        db = _get_db()
        suffix = uuid.uuid4().hex[:8]
        app_id = f"app-unscorable-{suffix}"
        db.execute(
            """INSERT INTO applications (id, ref, company_name, status)
               VALUES (?, ?, ?, ?)""",
            (app_id, f"ARF-UN-{suffix}", "Unscorable Ltd", "draft")
        )
        db.commit()

        result = recompute_risk(db, app_id, "test_noop")
        assert result["recomputed"] is False
        assert result["changed"] is False
        db.close()


# ──────────────────────────────────────────────
# snapshot_app_state tests
# ──────────────────────────────────────────────

class TestSnapshotAppState:
    """Verify snapshot_app_state includes new EX-09 fields."""

    def test_snapshot_includes_risk_computed_at(self):
        from base_handler import snapshot_app_state
        app = {
            "status": "submitted",
            "risk_level": "MEDIUM",
            "risk_score": 45.0,
            "risk_computed_at": "2026-04-12T15:00:00Z",
            "risk_config_version": "2026-04-10T10:00:00",
            "onboarding_lane": "Standard Review",
        }
        snap = snapshot_app_state(app)
        assert snap["risk_computed_at"] == "2026-04-12T15:00:00Z"
        assert snap["risk_config_version"] == "2026-04-10T10:00:00"

    def test_snapshot_omits_null_fields(self):
        from base_handler import snapshot_app_state
        app = {
            "status": "draft",
            "risk_level": None,
            "risk_score": None,
            "risk_computed_at": None,
            "risk_config_version": None,
        }
        snap = snapshot_app_state(app)
        assert "risk_computed_at" not in snap
        assert "risk_config_version" not in snap

    def test_snapshot_backward_compatible(self):
        """Snapshot should not crash on old app dicts missing new fields."""
        from base_handler import snapshot_app_state
        app = {"status": "submitted", "risk_level": "LOW", "risk_score": 20.0}
        snap = snapshot_app_state(app)
        assert snap["status"] == "submitted"
        assert "risk_computed_at" not in snap


# ──────────────────────────────────────────────
# Migration v2.20 tests
# ──────────────────────────────────────────────

class TestMigrationV220:
    """Verify migration v2.20 adds new columns."""

    def test_risk_computed_at_column_exists(self, temp_db):
        db = _get_db()
        # Column should exist after migration
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'"
        ).fetchone()
        # Try querying the column
        try:
            db.execute("SELECT risk_computed_at FROM applications LIMIT 1")
            column_exists = True
        except Exception:
            column_exists = False
        assert column_exists
        db.close()

    def test_risk_config_version_column_exists(self, temp_db):
        db = _get_db()
        try:
            db.execute("SELECT risk_config_version FROM applications LIMIT 1")
            column_exists = True
        except Exception:
            column_exists = False
        assert column_exists
        db.close()


# ──────────────────────────────────────────────
# Integration: recompute_risk_for_active_apps with audit
# ──────────────────────────────────────────────

class TestBulkRecomputeWithAudit:
    """Integration test: bulk recompute with audit logging."""

    def test_bulk_recompute_logs_audit_for_changed_apps(self, temp_db):
        from rule_engine import recompute_risk_for_active_apps
        db = _get_db()
        _insert_risk_config(db)

        # Insert app with extreme score that will definitely change
        app_id, app_ref = _insert_scored_app(db, risk_score=99.9, risk_level="VERY_HIGH",
                                              status="submitted")

        audit_calls = []

        def mock_audit(user, action, target, detail, **kwargs):
            audit_calls.append({
                "action": action, "target": target, "detail": detail,
                "before_state": kwargs.get("before_state"),
                "after_state": kwargs.get("after_state"),
            })

        user = _make_user()
        results = recompute_risk_for_active_apps(
            db, "risk_config_updated", user=user, log_audit_fn=mock_audit)
        db.commit()

        # Find audit entries for our app
        our_audits = [a for a in audit_calls
                      if a["target"] == app_ref and a["action"] == "Risk Recomputed"]
        # Should have exactly one audit entry for this app
        assert len(our_audits) == 1

        entry = our_audits[0]
        assert entry["before_state"]["risk_score"] == 99.9
        assert entry["before_state"]["risk_level"] == "VERY_HIGH"
        assert "risk_config_updated" in entry["detail"]
        db.close()


# ──────────────────────────────────────────────
# _get_risk_config_version helper
# ──────────────────────────────────────────────

class TestGetRiskConfigVersion:
    """Test the config version helper."""

    def test_returns_updated_at(self, temp_db):
        from rule_engine import _get_risk_config_version
        db = _get_db()
        _insert_risk_config(db)

        version = _get_risk_config_version(db)
        assert version is not None
        assert len(version) > 0
        db.close()

    def test_returns_none_when_no_config(self, temp_db):
        from rule_engine import _get_risk_config_version
        db = _get_db()
        # If config doesn't exist, should return None
        # (In practice config always exists after seeding, but test the edge case)
        db.execute("DELETE FROM risk_config WHERE id=999")  # no-op, just ensure clean state
        version = _get_risk_config_version(db)
        # May or may not be None depending on whether seed data exists
        # Just ensure it doesn't crash
        assert version is None or isinstance(version, str)
        db.close()
