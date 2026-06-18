import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_periodic_review_handlers import _PRReviewHandlerBase


class TestPeriodicReviewQueueHygiene(_PRReviewHandlerBase):
    def test_reviews_endpoint_excludes_fixture_reviews_by_default(self):
        self._conn.execute(
            "INSERT INTO applications (id, ref, company_name, risk_level, status) VALUES (?, ?, ?, ?, ?)",
            ("f1xed-prs2b-queue", "ARF-FIXTURE-PRS2B", "Fixture Queue Co", "HIGH", "approved"),
        )
        self._conn.commit()
        self._create_review(
            status="pending",
            risk_level="HIGH",
            trigger_source="monitoring_alert",
            review_reason="Fixture-only review",
            application_id="f1xed-prs2b-queue",
        )
        live_review_id = self._create_review(
            status="pending",
            risk_level="MEDIUM",
            trigger_source="schedule",
            review_reason="Live review",
        )

        resp = self._get("/api/monitoring/reviews")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)

        assert [row["id"] for row in body["reviews"]] == [live_review_id]

    def test_reviews_endpoint_excludes_raw_monitoring_alert_rows_but_keeps_real_review_cases(self):
        linked_alert_id = self._create_alert(status="open", alert_type="adverse_media")
        self._create_alert(status="open", alert_type="pep")
        review_id = self._create_review(
            status="pending",
            risk_level="HIGH",
            trigger_source="monitoring_alert",
            linked_alert_id=linked_alert_id,
            review_reason="Escalated from monitoring",
        )

        resp = self._get("/api/monitoring/reviews")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)

        assert len(body["reviews"]) == 1
        row = body["reviews"][0]
        assert row["id"] == review_id
        assert row["linked_monitoring_alert_id"] == linked_alert_id
        assert row["trigger_source"] == "monitoring_alert"
        assert row["trigger_source_label"] == "Monitoring escalation"

    def test_reviews_endpoint_labels_policy_trigger_cases_cleanly(self):
        self._create_review(
            status="pending",
            risk_level="MEDIUM",
            trigger_source="policy_routing",
            review_reason="Policy-triggered review",
        )

        resp = self._get("/api/monitoring/reviews")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)

        assert len(body["reviews"]) == 1
        assert body["reviews"][0]["trigger_source_label"] == "Policy trigger"

    def test_reviews_endpoint_defaults_to_actionable_statuses_but_explicit_filters_include_terminal(self):
        pending_id = self._create_review(status="pending", review_reason="Actionable")
        completed_id = self._create_review(status="completed", review_reason="Historical complete")
        cancelled_id = self._create_review(status="cancelled", review_reason="Historical cancel")

        default_resp = self._get("/api/monitoring/reviews")
        self.assertEqual(default_resp.code, 200)
        default_ids = [row["id"] for row in json.loads(default_resp.body)["reviews"]]
        self.assertIn(pending_id, default_ids)
        self.assertNotIn(completed_id, default_ids)
        self.assertNotIn(cancelled_id, default_ids)

        completed_resp = self._get("/api/monitoring/reviews?status=completed")
        self.assertEqual(completed_resp.code, 200)
        completed_ids = [row["id"] for row in json.loads(completed_resp.body)["reviews"]]
        self.assertIn(completed_id, completed_ids)

        cancelled_resp = self._get("/api/monitoring/reviews?queue=cancelled")
        self.assertEqual(cancelled_resp.code, 200)
        cancelled_ids = [row["id"] for row in json.loads(cancelled_resp.body)["reviews"]]
        self.assertIn(cancelled_id, cancelled_ids)
