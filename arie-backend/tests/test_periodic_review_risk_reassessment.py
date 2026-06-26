import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_periodic_review_handlers import _PRReviewHandlerBase


def _audit_actions(conn):
    rows = conn.execute("SELECT action, detail FROM audit_log ORDER BY id ASC").fetchall()
    return [(row["action"], row["detail"]) for row in rows]


class TestPeriodicReviewRiskReassessment(_PRReviewHandlerBase):
    def _save_payload(self, **overrides):
        payload = {
            "risk_impact_category": "no_risk_impact_identified",
            "officer_risk_decision": "keep_current_risk_rating",
            "confirmed_risk_level": "MEDIUM",
            "rationale": "Periodic review evidence does not change the risk profile.",
            "senior_review_required": False,
        }
        payload.update(overrides)
        return payload

    def test_snapshot_suggests_impact_from_material_change_data(self):
        rid = self._create_review()
        self._conn.execute(
            "UPDATE periodic_reviews SET client_attestation_payload=?, material_change_categories=? WHERE id=?",
            (
                json.dumps({
                    "answers": {
                        "directors_changed": {
                            "answer": "yes",
                            "comment": "New director appointed.",
                        }
                    }
                }),
                json.dumps(["directors_changed"]),
                rid,
            ),
        )
        self._conn.commit()

        import periodic_review_risk_reassessment as prr

        snapshot = prr.build_reassessment_snapshot(self._conn, rid)
        assert snapshot["suggested"]["suggested_risk_impact"] in {
            "profile_update_only",
            "potential_risk_increase",
        }
        assert snapshot["attestation_summary"]["material_change_count"] == 1

    def test_keep_current_risk_rating_with_rationale_is_saved_and_audited(self):
        rid = self._create_review()

        resp = self._post(
            f"/api/monitoring/reviews/{rid}/risk-reassessment",
            self._save_payload(),
        )

        assert resp.code == 200
        body = json.loads(resp.body.decode())
        risk = body["risk_reassessment"]
        assert risk["officer_risk_decision"] == "keep_current_risk_rating"
        assert risk["confirmed_risk_level"] == "MEDIUM"
        assert risk["risk_reassessment_status"] == "confirmed"

        stored = self._conn.execute(
            "SELECT risk_level FROM applications WHERE id=?",
            (self._app_id,),
        ).fetchone()
        assert stored["risk_level"] == "MEDIUM"

        actions = [action for action, _detail in _audit_actions(self._conn)]
        assert "periodic_review_risk_reassessment_saved" in actions
        assert "periodic_review_risk_decision_confirmed" in actions

    def test_missing_rationale_blocks_risk_change(self):
        rid = self._create_review()

        resp = self._post(
            f"/api/monitoring/reviews/{rid}/risk-reassessment",
            self._save_payload(
                officer_risk_decision="increase_risk_rating",
                confirmed_risk_level="HIGH",
                rationale="",
            ),
        )

        assert resp.code == 400
        assert "rationale is required" in json.loads(resp.body.decode())["error"].lower()

    def test_increased_risk_rating_flags_senior_review_and_does_not_mutate_application(self):
        rid = self._create_review()

        resp = self._post(
            f"/api/monitoring/reviews/{rid}/risk-reassessment",
            self._save_payload(
                risk_impact_category="potential_risk_increase",
                officer_risk_decision="increase_risk_rating",
                confirmed_risk_level="HIGH",
                rationale="New adverse media requires a higher review-level risk position.",
            ),
        )

        assert resp.code == 200
        risk = json.loads(resp.body.decode())["risk_reassessment"]
        assert risk["confirmed_risk_level"] == "HIGH"
        assert risk["senior_review_required"] is True
        assert risk["human_control_note"].startswith("Officer decision required")

        review = self._conn.execute(
            "SELECT new_risk_level, risk_change_attestation FROM periodic_reviews WHERE id=?",
            (rid,),
        ).fetchone()
        assert review["new_risk_level"] == "HIGH"
        assert review["risk_change_attestation"] == "risk_change_required"
        app = self._conn.execute(
            "SELECT risk_level FROM applications WHERE id=?",
            (self._app_id,),
        ).fetchone()
        assert app["risk_level"] == "MEDIUM"

        actions = [action for action, _detail in _audit_actions(self._conn)]
        assert "periodic_review_risk_rating_changed" in actions
        assert "periodic_review_senior_review_required" in actions

    def test_client_token_cannot_save_risk_reassessment(self):
        rid = self._create_review()

        resp = self._post(
            f"/api/monitoring/reviews/{rid}/risk-reassessment",
            self._save_payload(),
            token=self.client_token,
        )

        assert resp.code in (401, 403)

    def test_terminal_reviews_reject_risk_reassessment_without_mutation(self):
        for status in ("completed", "cancelled", "canceled"):
            rid = self._create_review(status=status)
            before = self._conn.execute(
                "SELECT status, risk_reassessment_status, risk_impact_category, "
                "officer_risk_decision, confirmed_risk_level, "
                "risk_reassessment_rationale, risk_reassessment_saved_by, "
                "risk_reassessment_saved_at, memo_addendum_status, "
                "periodic_review_memo_id "
                "FROM periodic_reviews WHERE id=?",
                (rid,),
            ).fetchone()
            audit_before = self._conn.execute(
                "SELECT COUNT(*) AS c FROM audit_log"
            ).fetchone()["c"]

            resp = self._post(
                f"/api/monitoring/reviews/{rid}/risk-reassessment",
                self._save_payload(rationale=f"terminal {status} mutation attempt"),
            )

            assert resp.code == 409
            after = self._conn.execute(
                "SELECT status, risk_reassessment_status, risk_impact_category, "
                "officer_risk_decision, confirmed_risk_level, "
                "risk_reassessment_rationale, risk_reassessment_saved_by, "
                "risk_reassessment_saved_at, memo_addendum_status, "
                "periodic_review_memo_id "
                "FROM periodic_reviews WHERE id=?",
                (rid,),
            ).fetchone()
            assert dict(after) == dict(before)
            audit_after = self._conn.execute(
                "SELECT COUNT(*) AS c FROM audit_log"
            ).fetchone()["c"]
            assert audit_after == audit_before

    def test_memo_addendum_generation_contains_periodic_review_evidence_and_audit(self):
        rid = self._create_review()
        save = self._post(
            f"/api/monitoring/reviews/{rid}/risk-reassessment",
            self._save_payload(),
        )
        assert save.code == 200

        resp = self._post(f"/api/periodic-reviews/{rid}/memo", {})

        assert resp.code == 200
        body = json.loads(resp.body.decode())
        assert body["status"] == "memo_addendum_generated"
        assert body["risk_reassessment"]["memo_addendum_status"] == "draft_generated"
        memo = body["memo"]["memo_data"]
        assert memo["header"]["memo_type"] == "periodic_review_memo_addendum"
        assert "attestation_summary" in memo
        assert "documents_summary" in memo
        assert "officer_findings" in memo
        assert memo["risk_reassessment"]["officer_confirmed_risk_decision"] == "keep_current_risk_rating"
        assert "next_review_date" in memo["conclusion"]

        actions = [action for action, _detail in _audit_actions(self._conn)]
        assert "periodic_review_memo_addendum_generated" in actions

    def test_terminal_reviews_reject_memo_generation_without_mutation(self):
        for status in ("completed", "cancelled", "canceled"):
            rid = self._create_review(status=status)
            before = self._conn.execute(
                "SELECT status, memo_status, periodic_review_memo_id, "
                "memo_addendum_status, memo_addendum_generated_at "
                "FROM periodic_reviews WHERE id=?",
                (rid,),
            ).fetchone()
            memo_count_before = self._conn.execute(
                "SELECT COUNT(*) AS c FROM periodic_review_memos WHERE periodic_review_id=?",
                (rid,),
            ).fetchone()["c"]

            resp = self._post(f"/api/periodic-reviews/{rid}/memo", {})

            assert resp.code == 409
            after = self._conn.execute(
                "SELECT status, memo_status, periodic_review_memo_id, "
                "memo_addendum_status, memo_addendum_generated_at "
                "FROM periodic_reviews WHERE id=?",
                (rid,),
            ).fetchone()
            assert dict(after) == dict(before)
            memo_count_after = self._conn.execute(
                "SELECT COUNT(*) AS c FROM periodic_review_memos WHERE periodic_review_id=?",
                (rid,),
            ).fetchone()["c"]
            assert memo_count_after == memo_count_before

    def test_memo_addendum_finalize_updates_status_and_audit(self):
        rid = self._create_review()
        assert self._post(f"/api/periodic-reviews/{rid}/memo", {}).code == 200

        resp = self._post(f"/api/periodic-reviews/{rid}/memo/finalize", {})

        assert resp.code == 200
        body = json.loads(resp.body.decode())
        assert body["risk_reassessment"]["memo_addendum_status"] == "finalized"
        memo = self._conn.execute(
            "SELECT status FROM periodic_review_memos WHERE periodic_review_id=? ORDER BY version DESC LIMIT 1",
            (rid,),
        ).fetchone()
        assert memo["status"] == "finalized"
        actions = [action for action, _detail in _audit_actions(self._conn)]
        assert "periodic_review_memo_addendum_finalized" in actions

    def test_terminal_reviews_reject_memo_finalization_without_mutation(self):
        for status in ("completed", "cancelled", "canceled"):
            rid = self._create_review(status=status)
            self._conn.execute(
                "INSERT INTO periodic_review_memos "
                "(periodic_review_id, application_id, version, memo_data, memo_context, generated_by, status) "
                "VALUES (?, ?, 1, '{}', '{}', 'test', 'generated')",
                (rid, self._app_id),
            )
            self._conn.commit()
            before = self._conn.execute(
                "SELECT status, memo_addendum_status, memo_addendum_finalized_at, "
                "memo_addendum_finalized_by, periodic_review_memo_id "
                "FROM periodic_reviews WHERE id=?",
                (rid,),
            ).fetchone()

            resp = self._post(f"/api/periodic-reviews/{rid}/memo/finalize", {})

            assert resp.code == 409
            after = self._conn.execute(
                "SELECT status, memo_addendum_status, memo_addendum_finalized_at, "
                "memo_addendum_finalized_by, periodic_review_memo_id "
                "FROM periodic_reviews WHERE id=?",
                (rid,),
            ).fetchone()
            assert dict(after) == dict(before)
            memo = self._conn.execute(
                "SELECT status FROM periodic_review_memos WHERE periodic_review_id=?",
                (rid,),
            ).fetchone()
            assert memo["status"] == "generated"

    def test_memo_addendum_generation_failure_is_recorded_safely(self):
        rid = self._create_review()
        import periodic_review_memo as prm

        with mock.patch.object(prm, "build_memo_data", side_effect=RuntimeError("boom")):
            resp = self._post(f"/api/periodic-reviews/{rid}/memo", {})

        assert resp.code == 500
        body = json.loads(resp.body.decode())
        assert body["risk_reassessment"]["memo_addendum_status"] == "failed"
        stored = self._conn.execute(
            "SELECT memo_addendum_status FROM periodic_reviews WHERE id=?",
            (rid,),
        ).fetchone()
        assert stored["memo_addendum_status"] == "failed"
        actions = [action for action, _detail in _audit_actions(self._conn)]
        assert "periodic_review_memo_addendum_failed" in actions
