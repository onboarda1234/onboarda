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

    def test_authorized_canonical_fixture_opt_in_is_label_ready_and_read_only(self):
        application_id = "f1xed-pilot-review"
        self._conn.execute(
            "INSERT INTO applications (id, ref, company_name, risk_level, status, is_fixture) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (application_id, "RM-PILOT-008", "Pilot Canonical Review Ltd", "MEDIUM", "approved", 1),
        )
        active_id = self._create_review(
            status="in_progress",
            risk_level="MEDIUM",
            trigger_source="pilot_canonical_dataset",
            review_reason="Canonical open review",
            application_id=application_id,
        )
        self._conn.execute(
            "UPDATE periodic_reviews SET due_date=?, next_review_date=?, priority=?, "
            "client_notification_status='failed', last_notification_error='No client linked', "
            "reminder_count=2, next_reminder_due_at='2026-07-08T00:00:00+00:00' WHERE id=?",
            ("2027-07-01", "2027-07-01", "normal", active_id),
        )
        completed_id = self._create_review(
            status="completed",
            risk_level="MEDIUM",
            trigger_source="pilot_canonical_dataset",
            review_reason="Canonical completed review",
            application_id=application_id,
        )
        self._conn.execute(
            "UPDATE periodic_reviews SET last_review_date=?, next_review_date=?, due_date=?, priority=? WHERE id=?",
            ("2026-07-01", "2027-07-01", "2027-07-01", "normal", completed_id),
        )
        self._conn.commit()

        before_audits = self._conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
        before_rows = [dict(row) for row in self._conn.execute(
            "SELECT id,status,due_date,next_review_date,last_review_date,priority,"
            "client_notification_status,last_notification_error,reminder_count,"
            "next_reminder_due_at "
            "FROM periodic_reviews WHERE id IN (?,?) ORDER BY id",
            (active_id, completed_id),
        ).fetchall()]

        default_resp = self._get("/api/monitoring/reviews")
        fixture_resp = self._get("/api/monitoring/reviews?show_fixtures=true")
        completed_resp = self._get(
            "/api/monitoring/reviews?show_fixtures=true&status=completed"
        )
        detail_resp = self._get(f"/api/monitoring/reviews/{active_id}")

        self.assertEqual(default_resp.code, 200)
        self.assertNotIn(active_id, [row["id"] for row in json.loads(default_resp.body)["reviews"]])
        self.assertEqual(fixture_resp.code, 200)
        active = next(
            row for row in json.loads(fixture_resp.body)["reviews"] if row["id"] == active_id
        )
        self.assertTrue(active["is_fixture"])
        self.assertTrue(active["projection"]["is_fixture"])
        self.assertEqual(active["application_ref"], "RM-PILOT-008")
        self.assertEqual(active["priority"], "normal")
        self.assertEqual(active["due_date"], "2027-07-01")
        self.assertEqual(active["client_notification_status"], "suppressed")
        self.assertEqual(
            active["client_notification_status_label"],
            "Suppressed — synthetic fixture",
        )
        self.assertIsNone(active["last_notification_error"])
        self.assertEqual(active["reminder_count"], 0)
        self.assertIsNone(active["next_reminder_due_at"])
        self.assertTrue(active["notification_summary"]["notification_suppressed"])
        self.assertEqual(
            active["notification_summary"]["notification_suppression_reason"],
            "fixture_application",
        )

        self.assertEqual(detail_resp.code, 200)
        detail = json.loads(detail_resp.body)
        self.assertTrue(detail["is_fixture"])
        self.assertTrue(detail["projection"]["is_fixture"])
        self.assertEqual(detail["client_notification_status"], "suppressed")
        self.assertEqual(
            detail["client_notification_status_label"],
            "Suppressed — synthetic fixture",
        )
        self.assertIsNone(detail["last_notification_error"])
        self.assertEqual(detail["reminder_count"], 0)
        self.assertTrue(detail["notification_summary"]["notification_suppressed"])

        self.assertEqual(completed_resp.code, 200)
        completed = next(
            row for row in json.loads(completed_resp.body)["reviews"] if row["id"] == completed_id
        )
        self.assertTrue(completed["is_fixture"])
        self.assertEqual(completed["last_review_date"], "2026-07-01")
        self.assertEqual(completed["priority"], "normal")

        after_audits = self._conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
        after_rows = [dict(row) for row in self._conn.execute(
            "SELECT id,status,due_date,next_review_date,last_review_date,priority,"
            "client_notification_status,last_notification_error,reminder_count,"
            "next_reminder_due_at "
            "FROM periodic_reviews WHERE id IN (?,?) ORDER BY id",
            (active_id, completed_id),
        ).fetchall()]
        self.assertEqual(after_audits, before_audits)
        self.assertEqual(after_rows, before_rows)

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
