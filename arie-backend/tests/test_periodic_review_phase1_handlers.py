from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

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
        self.assertEqual(body["projection"]["status_label"], "Awaiting client attestation")
        self.assertEqual(body["projection"]["blocker_count"], 0)
        self.assertGreaterEqual(body["projection"]["completion_blocker_count"], 1)

    def test_review_queue_list_supports_canonical_filters_and_payload(self):
        today = datetime.now(timezone.utc).date()
        overdue_due_date = (today - timedelta(days=2)).isoformat()
        open_due_date = (today + timedelta(days=10)).isoformat()
        completed_due_date = (today - timedelta(days=30)).isoformat()

        self._conn.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, 'active')",
            ("co001", "co001@example.com", "x", "Test CO", "co"),
        )
        self._conn.commit()

        overdue_id = self._create_review(status="pending", risk_level="HIGH")
        open_id = self._create_review(status="in_progress", risk_level="MEDIUM")
        completed_id = self._create_review(status="completed", risk_level="LOW")
        self._conn.execute(
            "UPDATE periodic_reviews SET due_date=?, next_review_date=?, assigned_officer=?, assigned_at=? WHERE id=?",
            (overdue_due_date, overdue_due_date, "co001", "2026-05-01T09:30:00Z", overdue_id),
        )
        self._conn.execute(
            "UPDATE periodic_reviews SET due_date=?, next_review_date=?, assigned_officer=?, assigned_at=? WHERE id=?",
            (open_due_date, open_due_date, "admin001", "2026-05-03T09:30:00Z", open_id),
        )
        self._conn.execute(
            "UPDATE periodic_reviews SET due_date=?, next_review_date=?, assigned_officer=?, completed_at=? WHERE id=?",
            (completed_due_date, completed_due_date, "co001", "2026-04-01T10:00:00Z", completed_id),
        )
        self._conn.commit()

        resp = self._get("/api/monitoring/reviews?queue=overdue&assigned_to_me=true", token=self.co_token)
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)

        self.assertEqual(len(body["reviews"]), 1)
        row = body["reviews"][0]
        self.assertEqual(row["id"], overdue_id)
        self.assertEqual(row["application_id"], self._app_id)
        self.assertEqual(row["application_ref"], "APP-PR03")
        self.assertEqual(row["client_name"], "PR03 Test Co")
        self.assertTrue(row["is_overdue"])
        self.assertEqual(row["queue_status"], "overdue")
        self.assertEqual(row["queue_status_label"], "Awaiting client attestation")
        self.assertEqual(row["owner_display_name"], "Test CO")
        self.assertEqual(row["primary_action_label"], "Open review case")
        self.assertEqual(row["projection"]["audit_reference"], f"periodic_review:{overdue_id}")

    def test_review_detail_projection_surfaces_linked_edd_case_id(self):
        rid = self._create_review(status="in_progress", risk_level="HIGH")
        self._conn.execute(
            "INSERT INTO edd_cases (application_id, client_name, risk_level, stage, origin_context, linked_periodic_review_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self._app_id, "PR03 Test Co", "HIGH", "analysis", "periodic_review", rid),
        )
        edd_id = self._conn.execute(
            "SELECT id FROM edd_cases ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        self._conn.execute(
            "UPDATE periodic_reviews SET linked_edd_case_id = ? WHERE id = ?",
            (edd_id, rid),
        )
        self._conn.commit()

        review_detail = self._get(f"/api/monitoring/reviews/{rid}")
        self.assertEqual(review_detail.code, 200)
        body = json.loads(review_detail.body)
        self.assertEqual(body["linked_edd_case_id"], edd_id)
        self.assertEqual(body["projection"]["linked_edd_case_id"], edd_id)

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

    def test_legacy_completed_review_detail_marks_modern_completion_readiness_not_applicable(self):
        rid = self._create_review(status="pending", officer_rationale=None)
        self._conn.execute(
            "UPDATE periodic_reviews "
            "SET status='completed', decision='continue', decision_reason='Legacy back-compat' "
            "WHERE id = ?",
            (rid,),
        )
        self._conn.commit()

        review_detail = self._get(f"/api/monitoring/reviews/{rid}")
        self.assertEqual(review_detail.code, 200)
        body = json.loads(review_detail.body)
        self.assertEqual(body["projection"]["status_label"], "Completed")
        self.assertFalse(body["projection"]["completion_readiness_applicable"])
        self.assertEqual(body["projection"]["completion_blocker_count"], 0)
        self.assertEqual(body["projection"]["completion_blocker_summary"], [])
        self.assertIsNone(body["projection"]["completion_ready"])

    def test_import_setup_endpoint_sets_ack_flag_for_high_risk(self):
        rid = self._create_review(status="pending", risk_level="HIGH")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/import-setup",
            {
                "last_review_date": "2025-01-01",
                "source_type": "internal_register",
                "source_note": "Legacy file migration",
                "review_evidence_note": "Prior review memo retained in migration packet",
                "confidence": "high",
                "assigned_officer": "co001",
            },
        )
        self.assertEqual(resp.code, 200)
        row = self._conn.execute(
            "SELECT import_requires_ack, assigned_officer, next_review_date, legacy_review_evidence_note FROM periodic_reviews WHERE id = ?",
            (rid,),
        ).fetchone()
        self.assertEqual(row["import_requires_ack"], 1)
        self.assertEqual(row["assigned_officer"], "co001")
        self.assertEqual(row["next_review_date"], "2026-01-01")
        self.assertEqual(row["legacy_review_evidence_note"], "Prior review memo retained in migration packet")
        detail = self._get(f"/api/monitoring/reviews/{rid}")
        self.assertEqual(detail.code, 200)
        baseline = json.loads(detail.body)["manual_legacy_baseline"]
        self.assertTrue(baseline["enabled"])
        self.assertEqual(baseline["review_evidence_note"], "Prior review memo retained in migration packet")
        self.assertEqual(baseline["frequency_months"], 12)

    def test_import_setup_endpoint_rejects_non_compliance_role(self):
        rid = self._create_review(status="pending", risk_level="LOW")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/import-setup",
            {
                "last_review_date": "2025-01-01",
                "source_type": "internal_register",
                "confidence": "high",
            },
            token=self.agent_token,
        )
        self.assertEqual(resp.code, 403)
