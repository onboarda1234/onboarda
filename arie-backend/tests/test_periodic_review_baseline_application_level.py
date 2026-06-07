import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_periodic_review_handlers import _PRReviewHandlerBase


class TestPeriodicReviewBaselineApplicationLevel(_PRReviewHandlerBase):
    def test_application_detail_surfaces_baseline_without_active_review_case(self):
        self._conn.execute(
            "UPDATE applications SET first_approved_at = ? WHERE id = ?",
            ("2026-04-10T00:00:00Z", self._app_id),
        )
        self._conn.commit()

        resp = self._get(f"/api/applications/{self._app_id}")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)

        baseline = body["periodic_review_baseline"]
        eligibility = body["periodic_review_baseline_eligibility"]
        assert baseline["legacy_file"] == "no"
        assert baseline["anchor_label"] == "Onboarding approval/completion date"
        assert eligibility["enabled"] is True
        assert "No periodic review case is available yet" not in json.dumps(body)

    def test_officer_can_save_baseline_without_active_review_case(self):
        self._conn.execute(
            "UPDATE applications SET first_approved_at = ?, risk_level = ?, final_risk_level = ? WHERE id = ?",
            ("2026-02-10T00:00:00Z", "LOW", "LOW", self._app_id),
        )
        self._conn.commit()

        resp = self._post(
            f"/api/applications/{self._app_id}/periodic-review-baseline",
            {
                "legacy_file": "no",
                "officer_note": "Use onboarding approval as the scheduling anchor.",
            },
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        assert body["result"]["application_id"] == self._app_id
        assert body["result"]["review_id"] is None
        assert body["result"]["baseline_cadence_months"] == 36
        assert body["result"]["next_review_due"] == "2029-02-10"

        app_row = self._conn.execute(
            """
            SELECT periodic_review_baseline_status, periodic_review_baseline_date,
                   periodic_review_baseline_cadence_months, periodic_review_baseline_note,
                   periodic_review_next_review_due
            FROM applications WHERE id = ?
            """,
            (self._app_id,),
        ).fetchone()
        assert app_row["periodic_review_baseline_status"] == "last_onboarding_date"
        assert app_row["periodic_review_baseline_date"] == "2026-02-10"
        assert app_row["periodic_review_baseline_cadence_months"] == 36
        assert app_row["periodic_review_baseline_note"] == "Use onboarding approval as the scheduling anchor."
        assert app_row["periodic_review_next_review_due"] == "2029-02-10"

        audit = self._conn.execute(
            "SELECT action, detail FROM audit_log WHERE action = 'periodic_review_baseline_saved' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["application_id"] == self._app_id
        assert detail["periodic_review_id"] is None
        assert detail["has_active_periodic_review"] is False
        assert detail["derived_cadence"] == 36
        assert detail["next_review_due"] == "2029-02-10"

    def test_legacy_file_requires_last_review_date_without_review_case(self):
        self._conn.execute(
            "UPDATE applications SET first_approved_at = ? WHERE id = ?",
            ("2026-02-10T00:00:00Z", self._app_id),
        )
        self._conn.commit()

        resp = self._post(
            f"/api/applications/{self._app_id}/periodic-review-baseline",
            {"legacy_file": "yes", "officer_note": "Missing date should fail."},
        )
        self.assertEqual(resp.code, 400)
        assert "last_review_date is required" in resp.body.decode("utf-8")

    def test_unapproved_application_cannot_save_application_level_baseline(self):
        self._conn.execute(
            """
            UPDATE applications
            SET status = ?, first_approved_at = NULL,
                decided_at = NULL,
                periodic_review_baseline_status = NULL,
                periodic_review_baseline_date = NULL,
                periodic_review_baseline_cadence_months = NULL,
                periodic_review_baseline_note = NULL,
                periodic_review_last_review_date = NULL,
                periodic_review_next_review_due = NULL
            WHERE id = ?
            """,
            ("compliance_review", self._app_id),
        )
        self._conn.commit()

        resp = self._post(
            f"/api/applications/{self._app_id}/periodic-review-baseline",
            {"legacy_file": "no", "officer_note": "Should not persist before approval."},
        )

        self.assertEqual(resp.code, 400)
        assert "after onboarding approval" in resp.body.decode("utf-8")
        app_row = self._conn.execute(
            """
            SELECT periodic_review_baseline_status, periodic_review_next_review_due
            FROM applications WHERE id = ?
            """,
            (self._app_id,),
        ).fetchone()
        assert app_row["periodic_review_baseline_status"] is None
        assert app_row["periodic_review_next_review_due"] is None

    def test_officer_can_save_not_applicable_baseline_without_review_case(self):
        self._conn.execute(
            "UPDATE applications SET first_approved_at = ?, risk_level = ?, final_risk_level = ? WHERE id = ?",
            ("2026-02-10T00:00:00Z", "MEDIUM", "MEDIUM", self._app_id),
        )
        self._conn.commit()

        resp = self._post(
            f"/api/applications/{self._app_id}/periodic-review-baseline",
            {
                "legacy_file": "n/a",
                "officer_note": "No manual baseline applies to this onboarding record.",
            },
        )

        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        assert body["result"]["legacy_file"] == "n/a"
        assert body["result"]["baseline_status"] == "not_applicable"
        assert body["result"]["next_review_due"] is None

        detail = json.loads(self._get(f"/api/applications/{self._app_id}").body)
        baseline = detail["periodic_review_baseline"]
        assert baseline["legacy_file"] == "n/a"
        assert baseline["legacy_file_label"] == "N/A"
        assert baseline["status_label"] == "Not applicable"
        assert baseline["next_review_due"] is None

        app_row = self._conn.execute(
            """
            SELECT periodic_review_baseline_status, periodic_review_baseline_date,
                   periodic_review_baseline_cadence_months, periodic_review_next_review_due
            FROM applications WHERE id = ?
            """,
            (self._app_id,),
        ).fetchone()
        assert app_row["periodic_review_baseline_status"] == "not_applicable"
        assert app_row["periodic_review_baseline_date"] is None
        assert app_row["periodic_review_baseline_cadence_months"] is None
        assert app_row["periodic_review_next_review_due"] is None

        audit = self._conn.execute(
            """
            SELECT action, before_state, after_state, detail
            FROM audit_log
            WHERE action = 'periodic_review_baseline_saved'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        assert audit is not None
        assert json.loads(audit["detail"])["legacy_file"] == "n/a"
        assert json.loads(audit["after_state"])["application_baseline_status"] == "not_applicable"

    def test_review_detail_uses_application_level_baseline_when_review_exists(self):
        self._conn.execute(
            "UPDATE applications SET first_approved_at = ?, risk_level = ?, final_risk_level = ? WHERE id = ?",
            ("2026-01-05T00:00:00Z", "HIGH", "HIGH", self._app_id),
        )
        self._conn.commit()
        review_id = self._create_review(status="pending", risk_level="HIGH")

        save_resp = self._post(
            f"/api/applications/{self._app_id}/periodic-review-baseline",
            {
                "legacy_file": "yes",
                "last_review_date": "2025-01-15",
                "officer_note": "Legacy annual review confirmed from prior file.",
            },
        )
        self.assertEqual(save_resp.code, 200)

        review_resp = self._get(f"/api/monitoring/reviews/{review_id}")
        self.assertEqual(review_resp.code, 200)
        review_body = json.loads(review_resp.body)
        baseline = review_body["periodic_review_baseline"]
        assert baseline["legacy_file"] == "yes"
        assert baseline["last_review_date"] == "2025-01-15"
        assert baseline["derived_cadence_months"] == 12
        assert baseline["next_review_due"] == "2026-01-15"

    def test_baseline_is_editable_when_completion_anchor_exists_without_approved_status(self):
        self._conn.execute(
            "UPDATE applications SET status = ?, first_approved_at = ? WHERE id = ?",
            ("compliance_review", "2026-03-05T00:00:00Z", self._app_id),
        )
        self._conn.commit()

        resp = self._get(f"/api/applications/{self._app_id}")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        eligibility = body["periodic_review_baseline_eligibility"]
        baseline = body["periodic_review_baseline"]

        assert eligibility["enabled"] is True
        assert eligibility["reason"] == "approval_anchor_or_existing_periodic_review_context"
        assert baseline["anchor_label"] == "Onboarding approval/completion date"
