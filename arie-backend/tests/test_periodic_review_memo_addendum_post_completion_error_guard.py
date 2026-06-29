import json
import logging
import os
import sys
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from test_periodic_review_handlers import _PRReviewHandlerBase  # noqa: E402


class _CaptureHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class TestMemoAddendumPostCompletionErrorGuard(_PRReviewHandlerBase):
    def _seed_screening_report(self):
        self._conn.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (
                json.dumps({
                    "screening_report": {
                        "screened_at": datetime.now(timezone.utc).isoformat(),
                    },
                }),
                self._app_id,
            ),
        )
        self._conn.commit()

    def test_completion_finalizes_without_post_terminal_memo_addendum_error(self):
        self._seed_screening_report()
        rid = self._create_review(status="in_progress")

        capture = _CaptureHandler(logging.ERROR)
        arie_logger = logging.getLogger("arie")
        arie_logger.addHandler(capture)
        try:
            resp = self._post(
                f"/api/monitoring/reviews/{rid}/complete",
                self._completion_payload(reason="all checks pass"),
            )
        finally:
            arie_logger.removeHandler(capture)

        self.assertEqual(resp.code, 200, resp.body)
        body = json.loads(resp.body)
        self.assertEqual(body["status"], "periodic_review_completed")
        self.assertTrue(body["memo_gate"]["finalized"])
        self.assertIsNotNone(body["memo"]["memo_id"])
        self.assertIsNotNone(body["result"]["next_review_id"])

        noisy_errors = [
            record for record in capture.records
            if "Periodic review memo addendum status/audit update failed" in record.getMessage()
        ]
        self.assertEqual(noisy_errors, [])

        row = self._conn.execute(
            "SELECT status, completed_at, closed_at FROM periodic_reviews WHERE id = ?",
            (rid,),
        ).fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["completed_at"])
        self.assertIsNotNone(row["closed_at"])

        next_cycle = self._conn.execute(
            "SELECT id, status FROM periodic_reviews WHERE id = ?",
            (body["result"]["next_review_id"],),
        ).fetchone()
        self.assertIsNotNone(next_cycle)
        self.assertNotEqual(next_cycle["id"], rid)

        actions = {
            row["action"]
            for row in self._conn.execute("SELECT action FROM audit_log").fetchall()
        }
        self.assertIn("periodic_review.outcome_recorded", actions)
        self.assertIn("periodic_review.completion_pending_memo", actions)
        self.assertIn("periodic_review.completion_finalized", actions)
        self.assertIn("periodic_review.next_cycle_scheduled", actions)
        self.assertIn("periodic_review_completed", actions)

        modern_replay = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="replay"),
        )
        self.assertEqual(modern_replay.code, 409)

        legacy_replay = self._post(
            f"/api/monitoring/reviews/{rid}/decision",
            {
                "decision": "continue",
                "decision_reason": "legacy replay",
                "officer_acknowledgement": True,
            },
        )
        self.assertEqual(legacy_replay.code, 409)

        legacy_blocked_next_cycle = self._post(
            f"/api/monitoring/reviews/{next_cycle['id']}/decision",
            {
                "decision": "continue",
                "decision_reason": "blocked next cycle",
                "officer_acknowledgement": True,
            },
        )
        self.assertEqual(legacy_blocked_next_cycle.code, 409)
        self.assertIn("blocking_items", json.loads(legacy_blocked_next_cycle.body))

    def test_expected_terminal_memo_addendum_update_logs_info_not_error(self):
        import periodic_review_risk_reassessment as prr
        import server

        capture = _CaptureHandler(logging.INFO)
        arie_logger = logging.getLogger("arie")
        original_level = arie_logger.level
        arie_logger.setLevel(logging.INFO)
        arie_logger.addHandler(capture)
        try:
            with mock.patch.object(
                prr,
                "mark_memo_addendum_generated",
                side_effect=prr.ReviewClosedError(
                    "periodic_review id=59 is already completed and cannot be memo addendum updated"
                ),
            ):
                result = server._mark_periodic_review_memo_addendum_generated_best_effort(
                    object(),
                    59,
                    memo_result={"status": "generated"},
                    user={"sub": "admin001"},
                    audit_writer=lambda *args, **kwargs: None,
                    context="unit_test",
                )
        finally:
            arie_logger.removeHandler(capture)
            arie_logger.setLevel(original_level)

        self.assertEqual(result, {})
        self.assertFalse(any(record.levelno >= logging.ERROR for record in capture.records))
        self.assertTrue(any(
            "memo addendum status/audit update skipped" in record.getMessage()
            for record in capture.records
        ))

    def test_unrelated_memo_addendum_update_exception_still_logs_error(self):
        import periodic_review_risk_reassessment as prr
        import server

        capture = _CaptureHandler(logging.ERROR)
        arie_logger = logging.getLogger("arie")
        arie_logger.addHandler(capture)
        try:
            with mock.patch.object(
                prr,
                "mark_memo_addendum_generated",
                side_effect=RuntimeError("database unavailable"),
            ):
                result = server._mark_periodic_review_memo_addendum_generated_best_effort(
                    object(),
                    59,
                    memo_result={"status": "generated"},
                    user={"sub": "admin001"},
                    audit_writer=lambda *args, **kwargs: None,
                    context="unit_test",
                )
        finally:
            arie_logger.removeHandler(capture)

        self.assertEqual(result, {})
        self.assertTrue(any(
            record.levelno >= logging.ERROR
            and "Periodic review memo addendum status/audit update failed" in record.getMessage()
            for record in capture.records
        ))
