import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_periodic_review_handlers import _PRReviewHandlerBase


class TestPeriodicReviewBaseline(_PRReviewHandlerBase):
    def test_review_detail_surfaces_compact_baseline_defaults(self):
        review_id = self._create_review(status="pending", risk_level="MEDIUM")

        resp = self._get(f"/api/monitoring/reviews/{review_id}")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)

        baseline = body["periodic_review_baseline"]
        assert baseline["status"] == "not_set"
        assert baseline["status_label"] == "Not set"
        assert baseline["next_review_due"] is None
        assert baseline["next_review_due_placeholder"] == "Not scheduled yet"

    def test_officer_can_save_na_baseline_without_recalculating_due_date(self):
        review_id = self._create_review(status="pending", risk_level="LOW")
        self._conn.execute(
            "UPDATE periodic_reviews SET next_review_date = ?, due_date = ? WHERE id = ?",
            ("2028-01-15", "2028-01-15", review_id),
        )
        self._conn.commit()

        resp = self._post(
            f"/api/monitoring/reviews/{review_id}/baseline",
            {
                "baseline_status": "not_applicable",
                "baseline_cadence": "risk_default",
                "officer_note": "New client; baseline not applicable yet.",
            },
        )
        self.assertEqual(resp.code, 200)

        row = self._conn.execute(
            "SELECT baseline_status, baseline_date, baseline_note, next_review_date FROM periodic_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        assert row["baseline_status"] == "not_applicable"
        assert row["baseline_date"] is None
        assert row["baseline_note"] == "New client; baseline not applicable yet."
        assert row["next_review_date"] == "2028-01-15"

        detail = json.loads(self._get(f"/api/monitoring/reviews/{review_id}").body)
        baseline = detail["periodic_review_baseline"]
        assert baseline["status"] == "not_applicable"
        assert baseline["next_review_due"] is None
        assert baseline["next_review_due_placeholder"] == "N/A"

        audits = self._conn.execute(
            "SELECT action, detail, target FROM audit_log WHERE action LIKE 'periodic_review_baseline_%' OR action = 'periodic_review_next_due_recalculated' ORDER BY id ASC"
        ).fetchall()
        actions = [row["action"] for row in audits]
        assert "periodic_review_baseline_saved" in actions
        assert "periodic_review_baseline_marked_na" in actions
        assert "periodic_review_next_due_recalculated" not in actions
        saved_detail = next(json.loads(row["detail"]) for row in audits if row["action"] == "periodic_review_baseline_saved")
        assert saved_detail["periodic_review_target"] == f"periodic_review:{review_id}"
        assert next(row["target"] for row in audits if row["action"] == "periodic_review_baseline_saved") == "APP-PR03"

    def test_date_based_baseline_save_recalculates_next_due_and_audits(self):
        review_id = self._create_review(status="pending", risk_level="HIGH")

        resp = self._post(
            f"/api/monitoring/reviews/{review_id}/baseline",
            {
                "baseline_status": "last_periodic_review_date",
                "baseline_date": "2025-01-15",
                "baseline_cadence": "12",
                "officer_note": "Imported from prior annual review.",
            },
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        assert body["result"]["next_review_due"] == "2026-01-15"

        row = self._conn.execute(
            "SELECT baseline_status, baseline_date, baseline_cadence_months, baseline_note, last_review_date, next_review_date, due_date FROM periodic_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        assert row["baseline_status"] == "last_periodic_review_date"
        assert row["baseline_date"] == "2025-01-15"
        assert row["baseline_cadence_months"] == 12
        assert row["baseline_note"] == "Imported from prior annual review."
        assert row["last_review_date"] == "2025-01-15"
        assert row["next_review_date"] == "2026-01-15"
        assert row["due_date"] == "2026-01-15"

        detail = json.loads(self._get(f"/api/monitoring/reviews/{review_id}").body)
        baseline = detail["periodic_review_baseline"]
        assert baseline["status"] == "last_periodic_review_date"
        assert baseline["cadence_months"] == 12
        assert baseline["next_review_due"] == "2026-01-15"

        audits = self._conn.execute(
            "SELECT action, detail, target FROM audit_log WHERE action LIKE 'periodic_review_baseline_%' OR action = 'periodic_review_next_due_recalculated' ORDER BY id ASC"
        ).fetchall()
        actions = [row["action"] for row in audits]
        assert "periodic_review_baseline_saved" in actions
        assert "periodic_review_next_due_recalculated" in actions
        recalculated_detail = next(json.loads(row["detail"]) for row in audits if row["action"] == "periodic_review_next_due_recalculated")
        assert recalculated_detail["new_baseline_status"] == "last_periodic_review_date"
        assert recalculated_detail["next_review_due_after"] == "2026-01-15"
        assert recalculated_detail["source_surface"] == "backoffice_application_overview_periodic_review_baseline"
        assert recalculated_detail["periodic_review_target"] == f"periodic_review:{review_id}"
        assert next(row["target"] for row in audits if row["action"] == "periodic_review_next_due_recalculated") == "APP-PR03"

    def test_baseline_audit_events_surface_in_application_audit_log(self):
        review_id = self._create_review(status="pending", risk_level="MEDIUM")

        resp = self._post(
            f"/api/monitoring/reviews/{review_id}/baseline",
            {
                "baseline_status": "last_periodic_review_date",
                "baseline_date": "2025-06-01",
                "baseline_cadence": "12",
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
        assert detail["periodic_review_target"] == f"periodic_review:{review_id}"
