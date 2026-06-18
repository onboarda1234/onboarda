"""
HTTP/API-level tests for PR-03 periodic review handlers.

These cover the new additive endpoints introduced by PR-03:

* POST /api/monitoring/reviews/:id/state
* GET  /api/monitoring/reviews/:id/required-items
* POST /api/monitoring/reviews/:id/required-items/generate
* POST /api/monitoring/reviews/:id/escalate
* POST /api/monitoring/reviews/:id/complete

The existing legacy ``PeriodicReviewDecisionHandler`` is intentionally
NOT modified by PR-03 and continues to work (back-compat). PR-02
follow-up reality: nearby PR-02 review-routing handlers remain covered
by ``test_monitoring_routing.py`` (engine-level); these PR-03 handler
tests add HTTP coverage on the periodic-review review handler seam
where PR-03 changes the operating model.
"""
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tornado.testing import AsyncHTTPTestCase


class _PRReviewHandlerBase(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_pr03_handler_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        # Mutate the module-level DB_PATH directly (matches the pattern
        # used by other server-level handler tests; avoids reloading
        # config/db modules which has cross-test side effects on the
        # Prometheus registry and shared module state). Restored in
        # tearDown so test_periodic_review_handlers does not pollute
        # the DB_PATH seen by other suites in the same pytest session.
        os.environ["DB_PATH"] = self._db_path
        import config as config_module
        import db as db_module
        self._orig_config_db_path = config_module.DB_PATH
        self._orig_db_db_path = db_module.DB_PATH
        config_module.DB_PATH = self._db_path
        db_module.DB_PATH = self._db_path

        db_module.init_db()
        conn = db_module.get_db()

        # Pre-mark migrations 001..007 as applied (init_db reflects the
        # full post-007 schema already; runner would otherwise replay
        # historical migrations and fail with duplicate-column errors).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "version TEXT UNIQUE NOT NULL, "
            "filename TEXT NOT NULL, "
            "description TEXT DEFAULT '', "
            "applied_at TEXT DEFAULT (datetime('now')), "
            "checksum TEXT)"
        )
        for v, fn in [
            ("001", "migration_001_initial.sql"),
            ("002", "migration_002_supervisor_tables.sql"),
            ("003", "migration_003_monitoring_indexes.sql"),
            ("004", "migration_004_documents_s3_key.sql"),
            ("005", "migration_005_applications_truth_schema.sql"),
            ("006", "migration_006_person_dob.sql"),
            ("007", "migration_007_screening_reports_normalized.sql"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, filename) "
                "VALUES (?, ?)", (v, fn),
            )
        conn.commit()
        from migrations.runner import run_all_migrations_with_connection
        run_all_migrations_with_connection(conn)

        # Seed an application for tests
        self._app_id = "test-app-pr03"
        try:
            conn.execute(
                "INSERT INTO applications "
                "(id, ref, company_name, country, sector, "
                " ownership_structure, risk_level, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self._app_id, "APP-PR03", "PR03 Test Co",
                 "Mauritius", "Fintech", "single-tier", "MEDIUM",
                 "approved"),
            )
        except Exception:
            conn.execute(
                "INSERT OR IGNORE INTO applications (id, ref, company_name) "
                "VALUES (?, ?, ?)",
                (self._app_id, "APP-PR03", "PR03 Test Co"),
            )
        conn.commit()
        conn.close()

        from server import make_app, create_token  # noqa: F401
        import server as server_module
        self._server = server_module
        return make_app()

    def setUp(self):
        super().setUp()
        from db import get_db
        self._conn = get_db()
        self.admin_token = self._server.create_token(
            "admin001", "admin", "Test Admin", "officer"
        )
        self.co_token = self._server.create_token(
            "co001", "co", "Test CO", "officer"
        )
        self.client_token = self._server.create_token(
            "client001", "client", "Test Client", "client"
        )

    def tearDown(self):
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            os.unlink(self._db_path)
        except Exception:
            pass
        # Restore module-level DB_PATH to avoid polluting other
        # suites in the same pytest session.
        try:
            import config as config_module
            import db as db_module
            config_module.DB_PATH = self._orig_config_db_path
            db_module.DB_PATH = self._orig_db_db_path
        except Exception:
            pass
        super().tearDown()

    # ---- helpers ----
    def _create_review(self, *, status="pending", risk_level="MEDIUM",
                       trigger_source=None, linked_alert_id=None,
                       review_reason=None, application_id=None,
                       officer_rationale="Fixture rationale",
                       client_attestation_status="submitted",
                       baseline_status="not_applicable"):
        application_id = application_id or self._app_id
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status, "
            " trigger_source, linked_monitoring_alert_id, review_reason, officer_rationale, "
            " client_attestation_status, baseline_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (application_id, "PR03 Test Co", risk_level, status,
             trigger_source, linked_alert_id, review_reason, officer_rationale,
             client_attestation_status, baseline_status),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _completion_payload(self, outcome="no_change", reason="all checks pass", **overrides):
        payload = {
            "outcome": outcome,
            "outcome_reason": reason,
            "officer_acknowledgement": True,
        }
        payload.update(overrides)
        return payload

    def _create_document(self, *, doc_id="doc-1", doc_type="passport", expiry_date=None):
        self._conn.execute(
            "INSERT INTO documents "
            "(id, application_id, doc_type, doc_name, file_path, expiry_date, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                self._app_id,
                doc_type,
                f"{doc_type}.pdf",
                f"/tmp/{doc_type}.pdf",
                expiry_date,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def _create_alert(self, *, status="open", severity="high", alert_type="adverse_media", resolved_at=None):
        self._conn.execute(
            "INSERT INTO monitoring_alerts "
            "(application_id, client_name, alert_type, severity, status, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self._app_id, "PR03 Test Co", alert_type, severity, status, resolved_at),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM monitoring_alerts ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _create_edd(self, *, stage="triggered"):
        self._conn.execute(
            "INSERT INTO edd_cases (application_id, client_name, stage) VALUES (?, ?, ?)",
            (self._app_id, "PR03 Test Co", stage),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM edd_cases ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _create_monitoring_agent(self, *, name, agent_type, alerts_generated=0):
        self._conn.execute(
            "INSERT INTO monitoring_agent_status "
            "(agent_name, agent_type, alerts_generated, status) VALUES (?, ?, ?, 'active')",
            (name, agent_type, alerts_generated),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM monitoring_agent_status ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _post(self, path, body, token=None):
        return self.fetch(
            path, method="POST",
            body=json.dumps(body or {}),
            headers={
                "Authorization": f"Bearer {token or self.admin_token}",
                "Content-Type": "application/json",
            },
        )

    def _get(self, path, token=None):
        return self.fetch(
            path, method="GET",
            headers={"Authorization": f"Bearer {token or self.admin_token}"},
        )

    def _patch(self, path, body, token=None):
        return self.fetch(
            path, method="PATCH",
            body=json.dumps(body or {}),
            headers={
                "Authorization": f"Bearer {token or self.admin_token}",
                "Content-Type": "application/json",
            },
        )


# ─────────────────────────────────────────────────────────────────
# State endpoint
# ─────────────────────────────────────────────────────────────────
class TestStateHandler(_PRReviewHandlerBase):
    def test_requires_auth(self):
        rid = self._create_review()
        resp = self.fetch(
            f"/api/monitoring/reviews/{rid}/state",
            method="POST", body=json.dumps({"state": "in_progress"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.code, 401)

    def test_client_role_forbidden(self):
        rid = self._create_review()
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/state",
            {"state": "in_progress"}, token=self.client_token,
        )
        self.assertEqual(resp.code, 403)

    def test_pending_to_in_progress_succeeds(self):
        rid = self._create_review()
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/state",
            {"state": "in_progress"},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["status"], "state_changed")
        self.assertEqual(body["result"]["to"], "in_progress")
        # Persisted
        row = self._conn.execute(
            "SELECT status FROM periodic_reviews WHERE id = ?", (rid,)
        ).fetchone()
        self.assertEqual(row["status"], "in_progress")

    def test_invalid_transition_returns_409(self):
        rid = self._create_review(status="pending")
        # pending -> awaiting_information is not allowed
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/state",
            {"state": "awaiting_information"},
        )
        self.assertEqual(resp.code, 409)

    def test_terminal_completion_via_state_blocked(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/state",
            {"state": "completed"},
        )
        self.assertEqual(resp.code, 409)

    def test_missing_state_returns_400(self):
        rid = self._create_review()
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/state", {},
        )
        self.assertEqual(resp.code, 400)


# ─────────────────────────────────────────────────────────────────
# Required-items endpoints
# ─────────────────────────────────────────────────────────────────
class TestRequiredItemsHandler(_PRReviewHandlerBase):
    def test_get_empty_default(self):
        rid = self._create_review()
        resp = self._get(f"/api/monitoring/reviews/{rid}/required-items")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["items"], [])

    def test_generate_persists_items(self):
        rid = self._create_review(risk_level="HIGH")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/required-items/generate", {},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertGreater(body["count"], 0)
        codes = {it["code"] for it in body["items"]}
        self.assertIn("kyc_refresh", codes)
        self.assertIn("source_of_funds_refresh", codes)
        # Read-back via GET returns the same list
        resp2 = self._get(f"/api/monitoring/reviews/{rid}/required-items")
        body2 = json.loads(resp2.body)
        self.assertEqual(len(body2["items"]), body["count"])

    def test_generate_blocked_on_completed(self):
        rid = self._create_review(status="completed")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/required-items/generate", {},
        )
        self.assertEqual(resp.code, 409)

    def test_generated_items_expose_source_and_source_id(self):
        alert_id = self._create_alert(status="open", severity="high")
        rid = self._create_review(
            status="in_progress",
            trigger_source="monitoring_alert",
            linked_alert_id=alert_id,
            review_reason="linked alert context",
        )
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/required-items/generate", {},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        item = next(it for it in body["items"] if it["source"] == "monitoring_alert")
        self.assertEqual(item["source_id"], alert_id)

    def test_can_add_custom_required_evidence_item(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/required-items/custom",
            {
                "label": "Provide signed organisational chart",
                "rationale": "Officer requires current structure evidence.",
                "severity": "high",
            },
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["status"], "required_item_added")
        self.assertEqual(body["item"]["item_type"], "custom_evidence_requirement")
        row = self._conn.execute(
            "SELECT required_items FROM periodic_reviews WHERE id = ?",
            (rid,),
        ).fetchone()
        items = json.loads(row["required_items"])
        self.assertEqual(items[-1]["label"], "Provide signed organisational chart")

    def test_custom_required_evidence_item_rejects_missing_rationale(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/required-items/custom",
            {"label": "Provide updated corporate structure chart"},
        )
        self.assertEqual(resp.code, 400)

    def test_completed_review_rejects_custom_required_evidence_item(self):
        rid = self._create_review(status="completed")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/required-items/custom",
            {
                "label": "Provide refreshed trust deed",
                "rationale": "Backfill evidence",
                "severity": "high",
            },
        )
        self.assertEqual(resp.code, 409)


class TestRequiredItemPatchHandler(_PRReviewHandlerBase):
    def test_officer_can_clear_item(self):
        rid = self._create_review(status="in_progress")
        generated = json.loads(self._post(
            f"/api/monitoring/reviews/{rid}/required-items/generate", {},
        ).body)
        item_id = generated["items"][0]["id"]
        resp = self._patch(
            f"/api/monitoring/reviews/{rid}/required-items/{item_id}",
            {"status": "cleared", "officer_note": "done"},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["item"]["status"], "cleared")

    def test_invalid_status_returns_400(self):
        rid = self._create_review(status="in_progress")
        generated = json.loads(self._post(
            f"/api/monitoring/reviews/{rid}/required-items/generate", {},
        ).body)
        item_id = generated["items"][0]["id"]
        resp = self._patch(
            f"/api/monitoring/reviews/{rid}/required-items/{item_id}",
            {"status": "bogus"},
        )
        self.assertEqual(resp.code, 400)

    def test_client_role_forbidden(self):
        rid = self._create_review(status="in_progress")
        generated = json.loads(self._post(
            f"/api/monitoring/reviews/{rid}/required-items/generate", {},
        ).body)
        item_id = generated["items"][0]["id"]
        resp = self._patch(
            f"/api/monitoring/reviews/{rid}/required-items/{item_id}",
            {"status": "cleared", "officer_note": "done"},
            token=self.client_token,
        )
        self.assertEqual(resp.code, 403)

    def test_completed_review_cannot_be_mutated(self):
        rid = self._create_review(status="completed")
        self._conn.execute(
            "UPDATE periodic_reviews SET required_items = ? WHERE id = ?",
            (json.dumps([{"id": "item-1", "code": "kyc_refresh", "label": "x", "rationale": "y"}]), rid),
        )
        self._conn.commit()
        resp = self._patch(
            f"/api/monitoring/reviews/{rid}/required-items/item-1",
            {"status": "cleared", "officer_note": "done"},
        )
        self.assertEqual(resp.code, 409)

    def test_monitoring_alert_item_cannot_be_cleared(self):
        alert_id = self._create_alert(status="open", severity="high")
        rid = self._create_review(
            status="in_progress",
            trigger_source="monitoring_alert",
            linked_alert_id=alert_id,
            review_reason="linked alert context",
        )
        generated = json.loads(self._post(
            f"/api/monitoring/reviews/{rid}/required-items/generate", {},
        ).body)
        item = next(it for it in generated["items"] if it["source"] == "monitoring_alert")
        resp = self._patch(
            f"/api/monitoring/reviews/{rid}/required-items/{item['id']}",
            {"status": "cleared", "officer_note": "done"},
        )
        self.assertEqual(resp.code, 400)


# ─────────────────────────────────────────────────────────────────
# Escalate endpoint
# ─────────────────────────────────────────────────────────────────
class TestEscalateHandler(_PRReviewHandlerBase):
    def test_creates_edd_first_time(self):
        rid = self._create_review(risk_level="HIGH")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/escalate",
            {"trigger_notes": "high risk"},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertTrue(body["result"]["created"])
        edd_id = body["result"]["edd_case_id"]
        # PR-01 origin recorded
        edd = self._conn.execute(
            "SELECT origin_context, linked_periodic_review_id FROM edd_cases "
            "WHERE id = ?", (edd_id,),
        ).fetchone()
        self.assertEqual(edd["origin_context"], "periodic_review")
        self.assertEqual(edd["linked_periodic_review_id"], rid)

    def test_repeat_escalate_is_dedup_safe(self):
        rid = self._create_review(risk_level="HIGH")
        first = json.loads(self._post(
            f"/api/monitoring/reviews/{rid}/escalate", {},
        ).body)
        second = json.loads(self._post(
            f"/api/monitoring/reviews/{rid}/escalate", {},
        ).body)
        self.assertEqual(
            first["result"]["edd_case_id"],
            second["result"]["edd_case_id"],
        )
        self.assertTrue(second["result"]["reused"])
        # Exactly one EDD
        n = self._conn.execute(
            "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id = ?",
            (self._app_id,),
        ).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_escalate_blocked_on_completed(self):
        rid = self._create_review(status="completed")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/escalate", {},
        )
        self.assertEqual(resp.code, 409)

    def test_unknown_review_returns_404(self):
        resp = self._post(
            "/api/monitoring/reviews/99999/escalate", {},
        )
        self.assertEqual(resp.code, 404)


# ─────────────────────────────────────────────────────────────────
# Legacy decision endpoint
# ─────────────────────────────────────────────────────────────────
class TestLegacyDecisionHandler(_PRReviewHandlerBase):
    def test_legacy_decision_uses_modern_blockers_and_does_not_write_decision(self):
        rid = self._create_review(status="in_progress", client_attestation_status="not_started")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/decision",
            {
                "decision": "continue",
                "decision_reason": "legacy caller attempt",
                "officer_acknowledgement": True,
            },
        )
        self.assertEqual(resp.code, 409)
        body = json.loads(resp.body)
        labels = [item["label"] for item in body["blocking_items"]]
        self.assertIn("Client attestation has not been submitted", labels)
        row = self._conn.execute(
            "SELECT status, decision, outcome FROM periodic_reviews WHERE id = ?",
            (rid,),
        ).fetchone()
        self.assertEqual(row["status"], "in_progress")
        self.assertIsNone(row["decision"])
        self.assertIsNone(row["outcome"])


# ─────────────────────────────────────────────────────────────────
# Complete endpoint
# ─────────────────────────────────────────────────────────────────
class TestCompleteHandler(_PRReviewHandlerBase):
    def test_requires_auth(self):
        rid = self._create_review(status="in_progress")
        resp = self.fetch(
            f"/api/monitoring/reviews/{rid}/complete",
            method="POST",
            body=json.dumps(self._completion_payload(reason="x")),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.code, 401)

    def test_client_role_forbidden(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="x"),
            token=self.client_token,
        )
        self.assertEqual(resp.code, 403)

    def test_unknown_review_returns_404(self):
        resp = self._post(
            "/api/monitoring/reviews/99999/complete",
            self._completion_payload(reason="x"),
        )
        self.assertEqual(resp.code, 404)

    def test_records_outcome_and_closes(self):
        self._conn.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (json.dumps({"screening_report": {"screened_at": datetime.now(timezone.utc).isoformat()}}), self._app_id),
        )
        self._conn.commit()
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="all checks pass"),
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["status"], "periodic_review_completed")
        self.assertIsNotNone(body["result"]["next_review_date"])
        row = self._conn.execute(
            "SELECT status, outcome, outcome_reason, officer_rationale, completed_at, decided_by, next_review_date, closed_at "
            "FROM periodic_reviews WHERE id = ?", (rid,)
        ).fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["outcome"], "no_change")
        self.assertEqual(row["officer_rationale"], "all checks pass")
        self.assertIsNotNone(row["completed_at"])
        self.assertEqual(row["decided_by"], "admin001")
        self.assertIsNotNone(row["next_review_date"])
        self.assertIsNotNone(row["closed_at"])
        audit = self._conn.execute(
            "SELECT action, before_state, after_state FROM audit_log WHERE action = 'periodic_review_completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(audit)
        self.assertIn("completed", audit["after_state"])
        open_queue = self._get("/api/monitoring/reviews?queue=open")
        self.assertEqual(open_queue.code, 200)
        open_reviews = json.loads(open_queue.body)["reviews"]
        self.assertFalse(any(item["id"] == rid for item in open_reviews))

    def test_completion_payload_rationale_satisfies_gate(self):
        self._conn.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (json.dumps({"screening_report": {"screened_at": datetime.now(timezone.utc).isoformat()}}), self._app_id),
        )
        self._conn.commit()
        rid = self._create_review(status="in_progress", officer_rationale="")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="Payload rationale supplied at closure"),
        )
        self.assertEqual(resp.code, 200)
        row = self._conn.execute(
            "SELECT status, officer_rationale FROM periodic_reviews WHERE id = ?",
            (rid,),
        ).fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["officer_rationale"], "Payload rationale supplied at closure")

    def test_replay_blocked(self):
        self._conn.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (json.dumps({"screening_report": {"screened_at": datetime.now(timezone.utc).isoformat()}}), self._app_id),
        )
        self._conn.commit()
        rid = self._create_review(status="in_progress")
        first = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="x"),
        )
        self.assertEqual(first.code, 200)
        second = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="y"),
        )
        self.assertEqual(second.code, 409)

    def test_invalid_outcome_400(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome": "bogus", "outcome_reason": "x"},
        )
        self.assertEqual(resp.code, 400)

    def test_missing_outcome_400(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome_reason": "x"},
        )
        self.assertEqual(resp.code, 400)

    def test_missing_outcome_reason_400(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome": "no_change"},
        )
        self.assertEqual(resp.code, 400)

    def test_missing_acknowledgement_blocks_completion(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome": "no_change", "outcome_reason": "x"},
        )
        self.assertEqual(resp.code, 409)
        body = json.loads(resp.body)
        labels = [item["label"] for item in body["blocking_items"]]
        self.assertIn("Officer acknowledgement is required", labels)

    def test_missing_attestation_blocks_completion(self):
        rid = self._create_review(status="in_progress", client_attestation_status="not_started")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="x"),
        )
        self.assertEqual(resp.code, 409)
        body = json.loads(resp.body)
        labels = [item["label"] for item in body["blocking_items"]]
        self.assertIn("Client attestation has not been submitted", labels)

    def test_missing_baseline_blocks_completion(self):
        rid = self._create_review(status="in_progress", baseline_status="not_set")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="x"),
        )
        self.assertEqual(resp.code, 409)
        body = json.loads(resp.body)
        labels = [item["label"] for item in body["blocking_items"]]
        self.assertIn("Periodic review baseline is missing or not marked N/A", labels)

    def test_required_periodic_review_document_request_blocks_until_resolved(self):
        rid = self._create_review(status="in_progress")
        self._conn.execute(
            """
            INSERT INTO application_enhanced_requirements
            (application_id, trigger_key, trigger_label, trigger_category,
             requirement_key, requirement_label, requirement_type, mandatory,
             status, linked_periodic_review_id, active)
            VALUES (?, ?, ?, ?, ?, ?, 'document', 1, 'requested', ?, 1)
            """,
            (
                self._app_id,
                f"periodic_review_{rid}_directors_changed",
                "Directors changed",
                "periodic_review_attestation",
                "updated_register_of_directors",
                "Updated Register of Directors",
                rid,
            ),
        )
        self._conn.commit()
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="x"),
        )
        self.assertEqual(resp.code, 409)
        body = json.loads(resp.body)
        labels = [item["label"] for item in body["blocking_items"]]
        self.assertIn("Updated Register of Directors is still missing", labels)

    def test_risk_change_does_not_mutate_application_risk(self):
        rid = self._create_review(status="in_progress", risk_level="MEDIUM")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(
                outcome="risk_rating_changed",
                reason="Risk profile changed during review",
                risk_changed=True,
                new_risk_level="HIGH",
                risk_impact="New adverse media requires higher periodic-review risk.",
            ),
        )
        self.assertEqual(resp.code, 200)
        review = self._conn.execute(
            "SELECT status, outcome, new_risk_level, risk_change_attestation, risk_rerate_reason FROM periodic_reviews WHERE id = ?",
            (rid,),
        ).fetchone()
        app = self._conn.execute(
            "SELECT risk_level FROM applications WHERE id = ?",
            (self._app_id,),
        ).fetchone()
        self.assertEqual(review["status"], "completed")
        self.assertEqual(review["outcome"], "risk_rating_changed")
        self.assertEqual(review["new_risk_level"], "HIGH")
        self.assertEqual(review["risk_change_attestation"], "risk_change_required")
        self.assertEqual(app["risk_level"], "MEDIUM")

    def test_edd_required_blocks_without_linked_edd_case(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(
                outcome="edd_required",
                reason="EDD is required",
                edd_required=True,
                risk_impact="EDD rationale recorded.",
            ),
        )
        self.assertEqual(resp.code, 409)
        body = json.loads(resp.body)
        labels = [item["label"] for item in body["blocking_items"]]
        self.assertIn("EDD outcome selected but no linked EDD case exists", labels)

    def test_edd_required_with_open_case_moves_to_awaiting_edd_not_completed(self):
        edd_id = self._create_edd()
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(
                outcome="edd_required",
                reason="EDD is required",
                edd_required=True,
                risk_impact="EDD rationale recorded.",
            ),
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["status"], "awaiting_edd")
        self.assertEqual(body["result"]["linked_edd_case_id"], edd_id)
        row = self._conn.execute(
            "SELECT status, completed_at, closed_at, linked_edd_case_id FROM periodic_reviews WHERE id = ?",
            (rid,),
        ).fetchone()
        self.assertEqual(row["status"], "awaiting_edd")
        self.assertIsNone(row["completed_at"])
        self.assertIsNone(row["closed_at"])
        self.assertEqual(row["linked_edd_case_id"], edd_id)
        pending_next = self._conn.execute(
            "SELECT COUNT(*) AS c FROM periodic_reviews WHERE application_id = ? AND id != ? AND status = 'pending'",
            (self._app_id, rid),
        ).fetchone()["c"]
        self.assertEqual(pending_next, 0)

    def test_client_follow_up_required_blocks_clean_closure(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(
                outcome="client_follow_up_required",
                reason="Client must clarify ownership changes",
                follow_up_required=True,
                follow_up_notes="Ask client for updated ownership chart.",
            ),
        )
        self.assertEqual(resp.code, 409)
        body = json.loads(resp.body)
        labels = [item["label"] for item in body["blocking_items"]]
        self.assertIn("Client follow-up required must be resolved before closure", labels)

    def test_blocks_completion_for_expired_document(self):
        self._conn.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (json.dumps({"screening_report": {"screened_at": datetime.now(timezone.utc).isoformat()}}), self._app_id),
        )
        self._conn.commit()
        self._create_document(
            doc_id="passport-expired",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
        )
        rid = self._create_review(status="in_progress")
        self._post(f"/api/monitoring/reviews/{rid}/required-items/generate", {})
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="try"),
        )
        self.assertEqual(resp.code, 409)
        body = json.loads(resp.body)
        self.assertEqual(body["error"], "Periodic review cannot be completed")
        self.assertTrue(body["blocking_items"])

    def test_dismissed_and_routed_alerts_do_not_block(self):
        self._conn.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (json.dumps({"screening_report": {"screened_at": datetime.now(timezone.utc).isoformat()}}), self._app_id),
        )
        self._conn.commit()
        self._create_alert(status="dismissed", severity="high")
        self._create_alert(status="routed_to_review", severity="critical")
        self._create_alert(status="routed_to_edd", severity="critical")
        rid = self._create_review(status="in_progress")
        self._post(f"/api/monitoring/reviews/{rid}/required-items/generate", {})
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="terminal alerts only"),
        )
        self.assertEqual(resp.code, 200)

    def test_resolved_at_alert_does_not_block(self):
        self._conn.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (json.dumps({"screening_report": {"screened_at": datetime.now(timezone.utc).isoformat()}}), self._app_id),
        )
        self._conn.commit()
        self._create_alert(
            status="open",
            severity="critical",
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )
        rid = self._create_review(status="in_progress")
        self._post(f"/api/monitoring/reviews/{rid}/required-items/generate", {})
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            self._completion_payload(reason="resolved alert"),
        )
        self.assertEqual(resp.code, 200)


# ─────────────────────────────────────────────────────────────────
# PR-03a hardening: non-numeric review_id must not 500
# ─────────────────────────────────────────────────────────────────
class TestNonNumericReviewIdHandling(_PRReviewHandlerBase):
    """All PR-03 review handlers must reject non-numeric path
    segments at the HTTP boundary with a clean 400, never a 500.

    The route regex is permissive (``[^/]+``) so a string like ``abc``
    reaches the handler. Without the PR-03a ``_parse_review_id`` guard
    this would either materialise as a Postgres type error (500) or
    silently round-trip through the engine as a ReviewNotFound (404),
    both of which are inconsistent. PR-03a pins the explicit 400.
    """

    BAD_IDS = ["abc", "1; DROP TABLE", "0", "-5", "1.5", " "]

    def _assert_clean_400(self, resp):
        # The hardening contract: NEVER a 500; explicitly 400.
        self.assertNotEqual(resp.code, 500,
                            "non-numeric review_id must not 500")
        self.assertEqual(resp.code, 400,
                         f"expected 400, got {resp.code}: {resp.body!r}")

    def test_state_endpoint_non_numeric_id(self):
        for bad in self.BAD_IDS:
            resp = self._post(
                f"/api/monitoring/reviews/{bad}/state",
                {"state": "in_progress"},
            )
            self._assert_clean_400(resp)

    def test_required_items_get_non_numeric_id(self):
        for bad in self.BAD_IDS:
            resp = self._get(
                f"/api/monitoring/reviews/{bad}/required-items",
            )
            self._assert_clean_400(resp)

    def test_required_items_generate_non_numeric_id(self):
        for bad in self.BAD_IDS:
            resp = self._post(
                f"/api/monitoring/reviews/{bad}/required-items/generate",
                {},
            )
            self._assert_clean_400(resp)

    def test_required_items_custom_non_numeric_id(self):
        for bad in self.BAD_IDS:
            resp = self._post(
                f"/api/monitoring/reviews/{bad}/required-items/custom",
                {"label": "Provide updated chart", "rationale": "Need evidence"},
            )
            self._assert_clean_400(resp)

    def test_escalate_non_numeric_id(self):
        for bad in self.BAD_IDS:
            resp = self._post(
                f"/api/monitoring/reviews/{bad}/escalate", {},
            )
            self._assert_clean_400(resp)

    def test_complete_non_numeric_id(self):
        for bad in self.BAD_IDS:
            resp = self._post(
                f"/api/monitoring/reviews/{bad}/complete",
                {"outcome": "no_change", "outcome_reason": "x"},
            )
            self._assert_clean_400(resp)

    def test_unknown_numeric_id_still_returns_404_on_state(self):
        # Sanity: the boundary guard must not regress the existing
        # contract for a well-formed but non-existent review id.
        resp = self._post(
            "/api/monitoring/reviews/99999/state",
            {"state": "in_progress"},
        )
        self.assertEqual(resp.code, 404)


# ─────────────────────────────────────────────────────────────────
# PR-03a hardening: priority is persisted to edd_cases.priority
# ─────────────────────────────────────────────────────────────────
class TestEscalatePriorityPersistence(_PRReviewHandlerBase):
    """Prove the PR-03a contract: if the escalate handler accepts a
    ``priority`` value, it is actually written to ``edd_cases.priority``
    on both the create-new-EDD and the reuse-existing-EDD paths. Prior
    to PR-03a the parameter was silently ignored on the reuse path.
    """

    def test_priority_persisted_when_creating_new_edd(self):
        rid = self._create_review(risk_level="HIGH")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/escalate",
            {"priority": "high", "trigger_notes": "elevated risk"},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertTrue(body["result"]["created"])
        edd_id = body["result"]["edd_case_id"]
        row = self._conn.execute(
            "SELECT priority FROM edd_cases WHERE id = ?", (edd_id,),
        ).fetchone()
        self.assertEqual(row["priority"], "high")


class TestMonitoringAgentRunHandler(_PRReviewHandlerBase):
    def test_agents_endpoint_includes_document_health_when_table_empty(self):
        self._conn.execute("DELETE FROM monitoring_agent_status")
        self._conn.commit()
        resp = self._get("/api/monitoring/agents")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        doc_agent = next(
            agent for agent in body["agents"]
            if agent.get("key") == "document_health"
        )
        self.assertEqual(doc_agent["label"], "Document Health Monitor")
        self.assertEqual(doc_agent["agent_type"], "document_health")
        self.assertIn("document_expired", doc_agent["types"])
        self.assertIn("document_expiring_soon", doc_agent["types"])

    def test_registry_agent_triggers_document_health_sync(self):
        agent_id = self._create_monitoring_agent(
            name="Registry Monitoring Agent",
            agent_type="registry",
            alerts_generated=2,
        )
        self._create_document(
            doc_id="passport-expired",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
        )
        resp = self._post(f"/api/monitoring/agents/{agent_id}/run", {})
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["document_health_sync"]["created"], 1)
        row = self._conn.execute(
            "SELECT alerts_generated FROM monitoring_agent_status WHERE id = ?",
            (agent_id,),
        ).fetchone()
        self.assertEqual(row["alerts_generated"], 3)

    def test_non_document_health_agent_does_not_fake_alert_counts(self):
        agent_id = self._create_monitoring_agent(
            name="Adverse Media Agent",
            agent_type="adverse_media",
            alerts_generated=5,
        )
        resp = self._post(f"/api/monitoring/agents/{agent_id}/run", {})
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertFalse(body["document_health_sync"]["triggered"])
        row = self._conn.execute(
            "SELECT alerts_generated FROM monitoring_agent_status WHERE id = ?",
            (agent_id,),
        ).fetchone()
        self.assertEqual(row["alerts_generated"], 5)

    def test_document_health_virtual_agent_can_run_without_seeded_row(self):
        self._conn.execute("DELETE FROM monitoring_agent_status")
        self._conn.commit()
        self._create_document(
            doc_id="passport-expired-virtual",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
        )
        resp = self._post("/api/monitoring/agents/document_health/run", {})
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["agent_key"], "document_health")
        self.assertEqual(body["document_health_sync"]["created"], 1)
        alert = self._conn.execute(
            "SELECT alert_type, discovered_via FROM monitoring_alerts "
            "WHERE source_reference = ?",
            ("document:passport-expired-virtual",),
        ).fetchone()
        self.assertEqual(alert["alert_type"], "document_expired")
        self.assertEqual(alert["discovered_via"], "document_health")

    def test_monitoring_agent_run_does_not_mutate_periodic_review_judgment_fields(self):
        agent_id = self._create_monitoring_agent(
            name="Ongoing Compliance Review Agent",
            agent_type="ongoing_compliance_review",
            alerts_generated=0,
        )
        rid = self._create_review(status="in_progress")
        self._conn.execute(
            """
            UPDATE periodic_reviews
               SET material_change_attestation = ?,
                   material_change_categories = ?,
                   risk_change_attestation = ?,
                   officer_rationale = ?,
                   outcome = ?,
                   status = ?,
                   memo_status = ?
             WHERE id = ?
            """,
            (
                "no_material_change",
                json.dumps([]),
                "risk_retained",
                "Officer-owned rationale",
                "continue_no_change",
                "in_progress",
                "generated",
                rid,
            ),
        )
        self._conn.commit()
        before = dict(self._conn.execute(
            """
            SELECT material_change_attestation,
                   material_change_categories,
                   risk_change_attestation,
                   officer_rationale,
                   outcome,
                   status,
                   memo_status
              FROM periodic_reviews
             WHERE id = ?
            """,
            (rid,),
        ).fetchone())

        resp = self._post(f"/api/monitoring/agents/{agent_id}/run", {})
        self.assertEqual(resp.code, 200)

        after = dict(self._conn.execute(
            """
            SELECT material_change_attestation,
                   material_change_categories,
                   risk_change_attestation,
                   officer_rationale,
                   outcome,
                   status,
                   memo_status
              FROM periodic_reviews
             WHERE id = ?
            """,
            (rid,),
        ).fetchone())
        self.assertEqual(after, before)

    def test_invalid_non_numeric_agent_key_returns_404_not_500(self):
        resp = self._post("/api/monitoring/agents/not-a-real-agent/run", {})
        self.assertEqual(resp.code, 404)

    def test_priority_persisted_when_reusing_linked_edd(self):
        # First escalate without priority -> creates EDD with NULL priority
        rid = self._create_review(risk_level="HIGH")
        first = json.loads(self._post(
            f"/api/monitoring/reviews/{rid}/escalate", {},
        ).body)
        edd_id = first["result"]["edd_case_id"]
        before = self._conn.execute(
            "SELECT priority FROM edd_cases WHERE id = ?", (edd_id,),
        ).fetchone()["priority"]
        self.assertIsNone(before)
        # Re-escalate with priority -> reuses same EDD AND now persists.
        second = json.loads(self._post(
            f"/api/monitoring/reviews/{rid}/escalate",
            {"priority": "urgent"},
        ).body)
        self.assertEqual(second["result"]["edd_case_id"], edd_id)
        self.assertTrue(second["result"]["reused"])
        after = self._conn.execute(
            "SELECT priority FROM edd_cases WHERE id = ?", (edd_id,),
        ).fetchone()["priority"]
        self.assertEqual(after, "urgent")

    def test_invalid_priority_returns_400_not_500(self):
        rid = self._create_review(risk_level="HIGH")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/escalate",
            {"priority": "ludicrous"},
        )
        # Must surface as a clean validation error, never a 500.
        self.assertNotEqual(resp.code, 500)
        self.assertEqual(resp.code, 400)
        # And no EDD case should have been created as a side effect.
        n = self._conn.execute(
            "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id = ?",
            (self._app_id,),
        ).fetchone()["c"]
        self.assertEqual(n, 0)

    def test_omitting_priority_does_not_overwrite_existing(self):
        # Set a priority on first escalate, then re-escalate without
        # priority and confirm the existing value is preserved.
        rid = self._create_review(risk_level="HIGH")
        self._post(
            f"/api/monitoring/reviews/{rid}/escalate",
            {"priority": "high"},
        )
        edd_id = self._conn.execute(
            "SELECT id FROM edd_cases WHERE application_id = ?",
            (self._app_id,),
        ).fetchone()["id"]
        self._post(
            f"/api/monitoring/reviews/{rid}/escalate", {},
        )
        row = self._conn.execute(
            "SELECT priority FROM edd_cases WHERE id = ?", (edd_id,),
        ).fetchone()
        self.assertEqual(row["priority"], "high")


class TestMonitoringAlertCreateHandler(_PRReviewHandlerBase):
    def test_manual_alert_is_not_labeled_webhook_live(self):
        resp = self._post(
            "/api/monitoring/alerts",
            {
                "application_id": self._app_id,
                "alert_type": "Manual escalation",
                "severity": "High",
                "summary": "Officer-created alert",
            },
        )
        self.assertEqual(resp.code, 201)
        body = json.loads(resp.body)
        self.assertEqual(body["discovered_via"], "manual")
        row = self._conn.execute(
            "SELECT discovered_via FROM monitoring_alerts WHERE id = ?",
            (body["id"],),
        ).fetchone()
        self.assertEqual(row["discovered_via"], "manual")


class TestDocumentListExpiryMetadata(_PRReviewHandlerBase):
    def test_documents_endpoint_exposes_expiry_metadata(self):
        self._create_document(
            doc_id="passport-expiry-visible",
            doc_type="passport",
            expiry_date="2026-06-01",
        )
        self._conn.execute(
            """
            UPDATE documents
               SET valid_until = ?,
                   expiry_source = ?,
                   expiry_confidence = ?,
                   expiry_extracted_at = ?
             WHERE id = ?
            """,
            (
                "2026-06-01",
                "manual_entry",
                0.9,
                datetime.now(timezone.utc).isoformat(),
                "passport-expiry-visible",
            ),
        )
        self._conn.commit()

        resp = self._get(f"/api/applications/{self._app_id}/documents")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        doc = next(d for d in body if d["id"] == "passport-expiry-visible")
        self.assertEqual(doc["expiry_date"], "2026-06-01")
        self.assertEqual(doc["valid_until"], "2026-06-01")
        self.assertEqual(doc["expiry_source"], "manual_entry")
        self.assertEqual(doc["expiry_confidence"], 0.9)
        self.assertTrue(doc["expiry_extracted_at"])


class TestPeriodicReviewScreeningRefreshHandler(_PRReviewHandlerBase):
    def _set_screening_current(self):
        now = datetime.now(timezone.utc)
        self._conn.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (
                json.dumps({
                    "screening_report": {
                        "screened_at": now.isoformat(),
                        "timestamp": now.isoformat(),
                    },
                    "screening_valid_until": (
                        now + timedelta(days=30)
                    ).isoformat(),
                }),
                self._app_id,
            ),
        )
        self._conn.commit()

    def test_run_screening_refresh_resolves_item_when_screening_current(self):
        self._set_screening_current()
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/run-screening-refresh",
            {},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["status"], "screening_refresh_resolved")
        self.assertEqual(body["item"]["item_type"], "screening_refresh")
        self.assertEqual(body["item"]["status"], "cleared")

    def test_run_screening_refresh_blocks_when_screening_not_current(self):
        rid = self._create_review(status="in_progress")
        resp = self._post(
            f"/api/monitoring/reviews/{rid}/run-screening-refresh",
            {},
        )
        self.assertEqual(resp.code, 409)
        body = json.loads(resp.body)
        self.assertIn("Screening refresh is still required", body["error"])
        self.assertIn("next_action", body)
