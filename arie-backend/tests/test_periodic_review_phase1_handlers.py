from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_periodic_review_handlers import _PRReviewHandlerBase


class TestPhase1PeriodicReviewHandlers(_PRReviewHandlerBase):
    def setUp(self):
        super().setUp()
        self._conn.execute(
            "INSERT OR IGNORE INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, 'active')",
            ("agent001", "agent001@example.com", "x", "Test Agent", "agent"),
        )
        self._conn.commit()
        self.agent_token = self._server.create_token(
            "agent001", "agent", "Test Agent", "officer"
        )

    def test_assignment_endpoint_persists_assigned_officer(self):
        rid = self._create_review(status="pending")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/assignment",
            {"assigned_officer": "co001"},
        )
        self.assertEqual(resp.code, 200)
        row = self._conn.execute(
            "SELECT assigned_officer, assigned_by FROM periodic_reviews WHERE id = ?",
            (rid,),
        ).fetchone()
        self.assertEqual(row["assigned_officer"], "co001")
        self.assertEqual(row["assigned_by"], "admin001")

    def test_agent_role_cannot_mutate_attestation_rationale_or_completion(self):
        rid = self._create_review(status="pending")
        for path, body in (
            (f"/api/monitoring/reviews/{rid}/officer-rationale", {"officer_rationale": "Agent text"}),
            (f"/api/monitoring/reviews/{rid}/material-change-attestation", {"material_change_attestation": "no_material_change"}),
            (f"/api/monitoring/reviews/{rid}/complete", {"outcome": "no_change", "outcome_reason": "Agent completion"}),
        ):
            resp = self._post(path, body, token=self.agent_token)
            self.assertEqual(resp.code, 403)

    def test_application_surfaces_use_shared_projection_state(self):
        rid = self._create_review(status="pending", risk_level="HIGH")
        self._conn.execute(
            "UPDATE periodic_reviews SET assigned_officer=?, import_requires_ack=?, officer_rationale=?, required_items=? WHERE id=?",
            (
                "co001",
                1,
                "",
                json.dumps([
                    {"id": "req-1", "item_type": "screening_refresh", "label": "Refresh screening", "severity": "high", "status": "open"}
                ]),
                rid,
            ),
        )
        self._conn.commit()

        detail = self._get(f"/api/applications/{self._app_id}")
        self.assertEqual(detail.code, 200)
        detail_body = json.loads(detail.body)
        self.assertEqual(detail_body["periodic_review"]["status_label"], "Blocked")
        self.assertEqual(detail_body["periodic_reviews"][0]["status_label"], "Blocked")

        listing = self._get("/api/applications")
        self.assertEqual(listing.code, 200)
        listing_body = json.loads(listing.body)
        app_row = next(app for app in listing_body["applications"] if app["id"] == self._app_id)
        self.assertEqual(app_row["periodic_review"]["status_label"], "Blocked")

        review_detail = self._get(f"/api/monitoring/reviews/{rid}")
        self.assertEqual(review_detail.code, 200)
        review_body = json.loads(review_detail.body)
        self.assertEqual(review_body["projection"]["status_label"], "Blocked")
        self.assertFalse(review_body["projection"]["completion_ready"])

    def test_clean_pending_review_surfaces_as_due_not_blocked(self):
        rid = self._create_review(status="pending", risk_level="LOW")

        review_detail = self._get(f"/api/monitoring/reviews/{rid}")
        self.assertEqual(review_detail.code, 200)
        body = json.loads(review_detail.body)
        self.assertEqual(body["projection"]["status_label"], "Due")
        self.assertEqual(body["projection"]["blocker_count"], 0)
        self.assertGreaterEqual(body["projection"]["completion_blocker_count"], 1)

    def test_legacy_decision_endpoint_still_returns_legacy_flag(self):
        rid = self._create_review(status="pending")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/decision",
            {"decision": "continue", "decision_reason": "Legacy back-compat"},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertTrue(body["legacy"])
        row = self._conn.execute(
            "SELECT decision, outcome FROM periodic_reviews WHERE id = ?",
            (rid,),
        ).fetchone()
        self.assertEqual(row["decision"], "continue")
        self.assertIsNone(row["outcome"])

    def test_import_setup_endpoint_sets_ack_flag_for_high_risk(self):
        rid = self._create_review(status="pending", risk_level="HIGH")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/import-setup",
            {
                "last_review_date": "2025-01-01",
                "source_type": "internal_register",
                "confidence": "high",
                "assigned_officer": "co001",
            },
        )
        self.assertEqual(resp.code, 200)
        row = self._conn.execute(
            "SELECT import_requires_ack, assigned_officer, next_review_date FROM periodic_reviews WHERE id = ?",
            (rid,),
        ).fetchone()
        self.assertEqual(row["import_requires_ack"], 1)
        self.assertEqual(row["assigned_officer"], "co001")
        self.assertEqual(row["next_review_date"], "2026-01-01")
