import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_periodic_review_handlers import _PRReviewHandlerBase


class TestPeriodicReviewBaselineSimplified(_PRReviewHandlerBase):
    def test_review_detail_surfaces_simplified_baseline_defaults(self):
        review_id = self._create_review(status="pending", risk_level="MEDIUM")

        resp = self._get(f"/api/monitoring/reviews/{review_id}")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)

        baseline = body["periodic_review_baseline"]
        assert baseline["legacy_file"] == "no"
        assert baseline["legacy_file_label"] == "No"
        assert baseline["status_label"] == "Current onboarding file"
        assert baseline["anchor_label"] == "Onboarding approval/completion date"

    def test_officer_can_save_legacy_baseline_and_audit_derived_cadence(self):
        review_id = self._create_review(status="pending", risk_level="HIGH")

        resp = self._post(
            f"/api/monitoring/reviews/{review_id}/baseline",
            {
                "legacy_file": "yes",
                "last_review_date": "2025-01-15",
                "officer_note": "Legacy annual review confirmed from prior file.",
            },
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        assert body["result"]["legacy_file"] == "yes"
        assert body["result"]["baseline_cadence_months"] == 12
        assert body["result"]["next_review_due"] == "2026-01-15"

        row = self._conn.execute(
            "SELECT baseline_status, baseline_date, baseline_cadence_months, baseline_note, "
            "last_review_date, next_review_date, due_date "
            "FROM periodic_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        assert row["baseline_status"] == "last_periodic_review_date"
        assert row["baseline_date"] == "2025-01-15"
        assert row["baseline_cadence_months"] == 12
        assert row["baseline_note"] == "Legacy annual review confirmed from prior file."
        assert row["last_review_date"] == "2025-01-15"
        assert row["next_review_date"] == "2026-01-15"
        assert row["due_date"] == "2026-01-15"

        audit = self._conn.execute(
            "SELECT action, detail FROM audit_log WHERE action = 'periodic_review_baseline_saved' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["legacy_file"] == "yes"
        assert detail["derived_cadence"] == 12
        assert detail["next_review_due"] == "2026-01-15"
        assert detail["source_surface"] == "backoffice_application_overview_periodic_review_baseline"

    def test_officer_can_save_non_legacy_baseline_from_onboarding_anchor(self):
        review_id = self._create_review(status="pending", risk_level="LOW")
        self._conn.execute(
            "UPDATE applications SET first_approved_at = ? WHERE id = ?",
            ("2026-02-10T00:00:00Z", self._app_id),
        )
        self._conn.commit()

        resp = self._post(
            f"/api/monitoring/reviews/{review_id}/baseline",
            {
                "legacy_file": "no",
                "officer_note": "Current file, use onboarding approval anchor.",
            },
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        assert body["result"]["legacy_file"] == "no"
        assert body["result"]["baseline_cadence_months"] == 36
        assert body["result"]["next_review_due"] == "2029-02-10"

        detail = json.loads(self._get(f"/api/monitoring/reviews/{review_id}").body)
        baseline = detail["periodic_review_baseline"]
        assert baseline["legacy_file"] == "no"
        assert baseline["legacy_file_label"] == "No"
        assert baseline["anchor_date"] == "2026-02-10"
        assert baseline["derived_cadence_months"] == 36
        assert baseline["next_review_due"] == "2029-02-10"

    def test_baseline_audit_events_surface_in_application_audit_log(self):
        review_id = self._create_review(status="pending", risk_level="MEDIUM")
        self._conn.execute(
            "UPDATE applications SET first_approved_at = ? WHERE id = ?",
            ("2026-03-01T00:00:00Z", self._app_id),
        )
        self._conn.commit()

        resp = self._post(
            f"/api/monitoring/reviews/{review_id}/baseline",
            {
                "legacy_file": "no",
                "officer_note": "Application audit visibility check.",
            },
        )
        self.assertEqual(resp.code, 200)

        audit_resp = self._get(f"/api/applications/{self._app_id}/audit-log?limit=20")
        self.assertEqual(audit_resp.code, 200)
        entries = json.loads(audit_resp.body)["entries"]
        actions = [entry["action"] for entry in entries]

        assert "periodic_review_baseline_saved" in actions
        detail = next(json.loads(entry["detail"]) for entry in entries if entry["action"] == "periodic_review_baseline_saved")
        assert detail["application_id"] == self._app_id
        assert detail["application_ref"] == "APP-PR03"
        assert detail["legacy_file"] == "no"
