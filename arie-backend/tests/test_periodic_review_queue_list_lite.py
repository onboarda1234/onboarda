"""PR-PRS-QUEUE-LIST-LITE-PERF-1 — queue list-lite serializer.

Proves two things for GET /api/monitoring/reviews (the queue/list view):

1. PARITY: the lite serializer (used by the list endpoint) produces the SAME
   queue-displayed fields as the full detail serializer — including the
   operational labels (status_label/queue_status_label), is_blocked and the
   embedded projection — across review states (incl. attestation pending and a
   missing mandatory document request, which exercise the document-signal label
   path). Nothing the back-office queue renders is dropped or changed.

2. PERFORMANCE: the lite path performs ZERO per-row DB queries (the detail
   serializer fires ~5 per row: required items, application, document requests,
   risk-reassessment snapshot, alerts). This is the over-fetch the queue was
   doing for a 10-column table.
"""
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_periodic_review_memo import _PRDBase  # noqa: E402

# Fields the back-office queue (normalizePeriodicReview in arie-backoffice.html)
# reads from each row / its embedded projection.
QUEUE_FIELDS = [
    "status", "ui_status", "ui_status_label", "status_label", "queue_status",
    "queue_status_label", "is_blocked", "blocker_count", "is_overdue",
    "is_due_date_missing", "due_state", "primary_action_label", "can_take_action",
    "is_terminal", "owner_display_name", "application_ref", "review_reference",
    "notification_summary", "officer_alert_status", "client_notification_status",
    "client_notification_status_label", "reminder_count", "next_reminder_due_at",
    "client_action_required_label", "trigger_source", "trigger_source_label",
    "last_activity_at", "risk_level",
]
PROJECTION_FIELDS = [
    "status_label", "queue_status", "queue_status_label", "operational_status",
    "is_blocked", "blocker_count", "primary_action_label", "can_take_action",
    "is_terminal",
]


class TestQueueListLiteParity(_PRDBase):
    def _raw_row(self, rid):
        return self._conn.execute(
            "SELECT * FROM periodic_reviews WHERE id = ?", (rid,)
        ).fetchone()

    def _projection_for(self, rid):
        import periodic_review_projection_service as projsvc
        projs = {
            p["review_id"]: p
            for p in projsvc.list_review_projections(self._conn, review_ids=[rid])
        }
        return projs.get(rid)

    def _add_missing_document_requirement(self, rid):
        # A mandatory evidence document request with no linked document ->
        # drives missing_count > 0 -> "Awaiting documents" operational label.
        try:
            self._conn.execute(
                "INSERT INTO application_enhanced_requirements "
                "(application_id, linked_periodic_review_id, trigger_key, trigger_label, "
                " requirement_key, requirement_label, requirement_type, "
                " mandatory, status, active) "
                "VALUES (?, ?, ?, ?, ?, ?, 'document', 1, 'requested', 1)",
                (self._app_id, rid,
                 f"periodic_review_{rid}_shareholders_changed", "Shareholders changed",
                 "updated_register_of_shareholders", "Updated Register of Shareholders"),
            )
            self._conn.commit()
            return True
        except Exception:
            return False

    def _assert_parity(self, rid, *, label):
        import server
        row = self._raw_row(rid)
        projection = self._projection_for(rid)
        full = server._serialize_periodic_review_row(self._conn, row, projection=projection)
        lite = server._serialize_periodic_review_row(self._conn, row, projection=projection, lite=True)
        for key in QUEUE_FIELDS:
            self.assertEqual(
                lite.get(key), full.get(key),
                msg=f"[{label}] queue field '{key}' differs: lite={lite.get(key)!r} full={full.get(key)!r}",
            )
        lp = lite.get("projection") or {}
        fp = full.get("projection") or {}
        for key in PROJECTION_FIELDS:
            self.assertEqual(
                lp.get(key), fp.get(key),
                msg=f"[{label}] projection.{key} differs: lite={lp.get(key)!r} full={fp.get(key)!r}",
            )

    def test_parity_in_progress_attestation_submitted(self):
        rid = self._create_review(status="in_progress", client_attestation_status="submitted")
        self._assert_parity(rid, label="in_progress_attested")

    def test_parity_awaiting_attestation(self):
        rid = self._create_review(status="in_progress", client_attestation_status="not_started")
        self._assert_parity(rid, label="awaiting_attestation")

    def test_parity_awaiting_information(self):
        rid = self._create_review(status="awaiting_information", client_attestation_status="submitted")
        self._assert_parity(rid, label="awaiting_information")

    def test_parity_completed(self):
        rid = self._create_review(status="completed", client_attestation_status="submitted")
        self._conn.execute(
            "UPDATE periodic_reviews SET outcome='no_change', outcome_reason='ok', "
            "completed_at=datetime('now') WHERE id = ?", (rid,)
        )
        self._conn.commit()
        self._assert_parity(rid, label="completed")

    def test_parity_with_missing_document_request(self):
        # Exercises the document-signal operational label path in both the
        # projection and the full serializer's workspace -> must still match.
        rid = self._create_review(status="in_progress", client_attestation_status="submitted")
        if not self._add_missing_document_requirement(rid):
            self.skipTest("application_enhanced_requirements schema unavailable")
        self._assert_parity(rid, label="missing_document")

    def test_lite_path_does_no_per_row_db_queries(self):
        rid = self._create_review(status="in_progress")
        import server
        row = self._raw_row(rid)
        projection = self._projection_for(rid)

        calls = {"lite": 0, "full": 0}
        real_execute = self._conn.execute

        def counting_execute(mode):
            def _exec(*args, **kwargs):
                calls[mode] += 1
                return real_execute(*args, **kwargs)
            return _exec

        # Lite: projection supplied -> must issue ZERO db.execute.
        self._conn.execute = counting_execute("lite")
        try:
            server._serialize_periodic_review_row(self._conn, row, projection=projection, lite=True)
        finally:
            self._conn.execute = real_execute
        self.assertEqual(calls["lite"], 0,
                         f"lite serializer issued {calls['lite']} per-row DB queries (expected 0)")

        # Full: same inputs -> issues several per-row queries (the over-fetch).
        self._conn.execute = counting_execute("full")
        try:
            server._serialize_periodic_review_row(self._conn, row, projection=projection)
        finally:
            self._conn.execute = real_execute
        self.assertGreaterEqual(
            calls["full"], 3,
            f"full serializer issued only {calls['full']} queries; expected the per-row over-fetch",
        )

    # ── PR-PRS-QUEUE-LIST-LITE-PERF-1 rework: contract + endpoint guards ──

    # The list response must always carry these keys (lightweight defaults in
    # lite mode) so no consumer sees a key silently disappear.
    EXPECTED_LIST_CONTRACT_KEYS = {
        "id", "application_id", "status", "projection",
        "ui_status", "ui_status_label", "status_label", "queue_status",
        "queue_status_label", "is_blocked", "blocker_count",
        "primary_action_label", "can_take_action", "is_terminal",
        "owner_display_name", "application_ref", "review_reference",
        "notification_summary",
        "required_items", "required_items_count", "client_attestation",
        "periodic_review_baseline", "periodic_review_document_requests",
        "periodic_review_document_request_count", "risk_reassessment",
        "open_document_issues_count", "open_alerts_count", "screening_status",
    }

    def test_lite_preserves_list_response_contract_keys(self):
        import server
        rid = self._create_review(status="in_progress")
        row = self._raw_row(rid)
        projection = self._projection_for(rid)
        lite = server._serialize_periodic_review_row(self._conn, row, projection=projection, lite=True)
        full = server._serialize_periodic_review_row(self._conn, row, projection=projection)
        missing_in_lite = self.EXPECTED_LIST_CONTRACT_KEYS - set(lite.keys())
        self.assertEqual(missing_in_lite, set(),
                         f"lite response dropped contract keys: {missing_in_lite}")
        missing_in_full = self.EXPECTED_LIST_CONTRACT_KEYS - set(full.keys())
        self.assertEqual(missing_in_full, set(),
                         f"contract set names keys the full serializer does not set: {missing_in_full}")

    def test_list_endpoint_does_no_per_row_detail_work(self):
        # Endpoint-level guard: GET /api/monitoring/reviews must NOT invoke the
        # per-row detail helpers for any row, regardless of N. Guards against a
        # future regression that re-adds per-row work in the list handler.
        import server
        import periodic_review_engine
        import periodic_review_risk_reassessment
        for _ in range(3):
            self._create_review(status="in_progress")

        with mock.patch.object(
            server, "_list_backoffice_periodic_review_document_requests",
            wraps=server._list_backoffice_periodic_review_document_requests,
        ) as spy_docs, mock.patch.object(
            periodic_review_engine, "get_required_items",
            wraps=periodic_review_engine.get_required_items,
        ) as spy_items, mock.patch.object(
            periodic_review_risk_reassessment, "build_reassessment_snapshot",
            wraps=periodic_review_risk_reassessment.build_reassessment_snapshot,
        ) as spy_rr:
            resp = self._get("/api/monitoring/reviews")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body.decode())
        self.assertGreaterEqual(len(body.get("reviews") or []), 3)
        self.assertEqual(spy_docs.call_count, 0, "list endpoint fetched document requests per row")
        self.assertEqual(spy_items.call_count, 0, "list endpoint fetched required items per row")
        self.assertEqual(spy_rr.call_count, 0, "list endpoint built risk-reassessment per row")

    def test_detail_endpoint_still_does_full_per_row_work(self):
        # Sanity: the detail endpoint DOES exercise the per-row detail helpers
        # (proves the lite skip is list-only, not a global removal).
        import server
        import periodic_review_risk_reassessment
        rid = self._create_review(status="in_progress")
        with mock.patch.object(
            server, "_list_backoffice_periodic_review_document_requests",
            wraps=server._list_backoffice_periodic_review_document_requests,
        ) as spy_docs, mock.patch.object(
            periodic_review_risk_reassessment, "build_reassessment_snapshot",
            wraps=periodic_review_risk_reassessment.build_reassessment_snapshot,
        ) as spy_rr:
            resp = self._get(f"/api/monitoring/reviews/{rid}")
        self.assertEqual(resp.code, 200)
        self.assertGreater(spy_docs.call_count + spy_rr.call_count, 0,
                           "detail endpoint did not build full per-row detail")
