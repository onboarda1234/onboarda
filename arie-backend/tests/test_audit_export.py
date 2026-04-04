"""
Tests for audit log export endpoints:
  GET /api/audit/export
  GET /api/audit/supervisor/export
"""
import json
import csv
import io
import os
import sys
import uuid
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from datetime import datetime, timedelta, timezone
from tornado.testing import AsyncHTTPTestCase

# Unique marker prefix to tag test-seeded data for safe cleanup
_TEST_MARKER = "__audit_export_test__"


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

class _AuditExportTestBase(AsyncHTTPTestCase):
    """Shared setup for audit export tests."""

    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_audit_export_test_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        os.environ["DB_PATH"] = self._db_path

        from db import init_db, seed_initial_data, get_db
        init_db()
        conn = get_db()
        seed_initial_data(conn)
        conn.commit()
        conn.close()
        from server import make_app
        return make_app()

    def setUp(self):
        super().setUp()
        from db import get_db
        from server import create_token
        self._conn = get_db()
        self._inserted_supervisor_ids = []
        self._inserted_decision_ids = []
        self._seeded_audit = False
        self.admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        self.sco_token = create_token("sco001", "sco", "Test SCO", "officer")
        self.co_token = create_token("co001", "co", "Test CO", "officer")
        self.client_token = create_token("client001", "client", "Test Client", "client")

    def tearDown(self):
        # Clean up only rows we inserted (tracked by ID or marker)
        if self._inserted_supervisor_ids:
            placeholders = ",".join("?" for _ in self._inserted_supervisor_ids)
            self._conn.execute(
                f"DELETE FROM supervisor_audit_log WHERE id IN ({placeholders})",
                tuple(self._inserted_supervisor_ids),
            )
        if self._inserted_decision_ids:
            placeholders = ",".join("?" for _ in self._inserted_decision_ids)
            self._conn.execute(
                f"DELETE FROM decision_records WHERE id IN ({placeholders})",
                tuple(self._inserted_decision_ids),
            )
        if self._seeded_audit:
            self._conn.execute(
                "DELETE FROM audit_log WHERE detail LIKE ?",
                (f"{_TEST_MARKER}%",),
            )
        self._conn.commit()
        self._conn.close()
        super().tearDown()

    # Convenience helpers
    def _seed_audit_rows(self, num_rows=3, user_id="admin001", action="login", target="target_0"):
        for i in range(num_rows):
            self._conn.execute(
                "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
                "VALUES (?,?,?,?,?,?,?)",
                (user_id, "Admin", "admin", action, target, f"{_TEST_MARKER}{i}", "127.0.0.1"),
            )
        self._seeded_audit = True
        self._conn.commit()

    def _seed_supervisor_audit_rows(self, num_rows=3, actor_id="admin001", action="review", application_id=None):
        for i in range(num_rows):
            row_id = uuid.uuid4().hex
            app_id = application_id if application_id else f"app_{i}"
            self._conn.execute(
                "INSERT INTO supervisor_audit_log (id, timestamp, event_type, severity, actor_id, actor_name, "
                "actor_role, action, detail, application_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    row_id,
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "pipeline_run",
                    "info",
                    actor_id,
                    "Admin",
                    "admin",
                    action,
                    f"{_TEST_MARKER}{i}",
                    app_id,
                ),
            )
            self._inserted_supervisor_ids.append(row_id)
        self._conn.commit()

    def _seed_decision_records(self, num_records=2, actor_user_id="admin001", application_ref="app_0"):
        for i in range(num_records):
            row_id = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO decision_records (id, application_ref, decision_type, risk_level, confidence_score, "
                "source, actor_user_id, actor_role, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    row_id,
                    application_ref,
                    "approve",
                    "MEDIUM",
                    0.85,
                    "manual",
                    actor_user_id,
                    "admin",
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            self._inserted_decision_ids.append(row_id)
        self._conn.commit()


# ===========================================================================
# /api/audit/export tests
# ===========================================================================

class TestAuditExportAuth(_AuditExportTestBase):
    """Auth & role gating for /api/audit/export."""

    def test_requires_authentication(self):
        resp = self.fetch("/api/audit/export")
        self.assertEqual(resp.code, 401)

    def test_admin_allowed(self):
        self._seed_audit_rows(1)
        resp = self.fetch("/api/audit/export", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 200)

    def test_sco_allowed(self):
        self._seed_audit_rows(1)
        resp = self.fetch("/api/audit/export", headers={"Authorization": f"Bearer {self.sco_token}"})
        self.assertEqual(resp.code, 200)

    def test_co_forbidden(self):
        resp = self.fetch("/api/audit/export", headers={"Authorization": f"Bearer {self.co_token}"})
        self.assertEqual(resp.code, 403)

    def test_client_forbidden(self):
        resp = self.fetch("/api/audit/export", headers={"Authorization": f"Bearer {self.client_token}"})
        self.assertEqual(resp.code, 403)


class TestAuditExportJSON(_AuditExportTestBase):
    """JSON output for /api/audit/export."""

    def test_default_format_is_json(self):
        self._seed_audit_rows(2)
        resp = self.fetch("/api/audit/export", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertIn("entries", body)
        self.assertIn("total", body)
        self.assertGreaterEqual(body["total"], 2)

    def test_entries_have_required_fields(self):
        self._seed_audit_rows(1, user_id="u1", action="doc_upload")
        resp = self.fetch("/api/audit/export?format=json", headers={"Authorization": f"Bearer {self.admin_token}"})
        body = json.loads(resp.body)
        entry = next((e for e in body["entries"] if e.get("user_id") == "u1"), None)
        self.assertIsNotNone(entry)
        for field in ("timestamp", "user_id", "action", "target"):
            self.assertIn(field, entry)

    def test_filter_by_action(self):
        self._seed_audit_rows(2, action="login")
        self._seed_audit_rows(1, action="doc_upload")
        resp = self.fetch("/api/audit/export?action=doc_upload", headers={"Authorization": f"Bearer {self.admin_token}"})
        body = json.loads(resp.body)
        for entry in body["entries"]:
            self.assertEqual(entry["action"], "doc_upload")

    def test_filter_by_actor_user_id(self):
        self._seed_audit_rows(2, user_id="admin001")
        self._seed_audit_rows(1, user_id="other_user")
        resp = self.fetch("/api/audit/export?actor_user_id=other_user", headers={"Authorization": f"Bearer {self.admin_token}"})
        body = json.loads(resp.body)
        for entry in body["entries"]:
            self.assertEqual(entry["user_id"], "other_user")

    def test_include_decisions_by_target(self):
        """Decision enrichment joins via audit_log.target <-> decision_records.application_ref."""
        self._seed_audit_rows(1, user_id="admin001", target="APP-TEST-001")
        self._seed_decision_records(1, actor_user_id="admin001", application_ref="APP-TEST-001")
        resp = self.fetch("/api/audit/export?include_decisions=true", headers={"Authorization": f"Bearer {self.admin_token}"})
        body = json.loads(resp.body)
        found = [e for e in body["entries"] if e.get("target") == "APP-TEST-001" and e.get("decision_type")]
        self.assertGreater(len(found), 0)
        self.assertEqual(found[0]["decision_type"], "approve")

    def test_invalid_start_date_returns_400(self):
        resp = self.fetch("/api/audit/export?start_date=not-a-date", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 400)

    def test_invalid_end_date_returns_400(self):
        resp = self.fetch("/api/audit/export?end_date=baddate", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 400)

    def test_valid_date_filtering(self):
        self._seed_audit_rows(1)
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        resp = self.fetch(f"/api/audit/export?start_date={yesterday}&end_date={tomorrow}",
                          headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 200)


class TestAuditExportCSV(_AuditExportTestBase):
    """CSV output for /api/audit/export."""

    def test_csv_format(self):
        self._seed_audit_rows(2)
        resp = self.fetch("/api/audit/export?format=csv", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 200)
        self.assertIn("text/csv", resp.headers.get("Content-Type", ""))
        reader = csv.reader(io.StringIO(resp.body.decode()))
        headers = next(reader)
        self.assertIn("timestamp", headers)
        self.assertIn("user_id", headers)
        self.assertIn("action", headers)

    def test_csv_no_decision_columns_by_default(self):
        self._seed_audit_rows(1)
        resp = self.fetch("/api/audit/export?format=csv", headers={"Authorization": f"Bearer {self.admin_token}"})
        reader = csv.reader(io.StringIO(resp.body.decode()))
        headers = next(reader)
        self.assertNotIn("decision_type", headers)

    def test_csv_with_decisions(self):
        self._seed_audit_rows(1, user_id="admin001", target="APP-CSV-001")
        self._seed_decision_records(1, actor_user_id="admin001", application_ref="APP-CSV-001")
        resp = self.fetch("/api/audit/export?format=csv&include_decisions=true", headers={"Authorization": f"Bearer {self.admin_token}"})
        reader = csv.reader(io.StringIO(resp.body.decode()))
        headers = next(reader)
        self.assertIn("decision_type", headers)
        self.assertIn("decision_risk_level", headers)

    def test_invalid_format_returns_400(self):
        resp = self.fetch("/api/audit/export?format=xml", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 400)


# ===========================================================================
# /api/audit/supervisor/export tests
# ===========================================================================

class TestSupervisorAuditExportAuth(_AuditExportTestBase):
    """Auth & role gating for /api/audit/supervisor/export."""

    def test_requires_authentication(self):
        resp = self.fetch("/api/audit/supervisor/export")
        self.assertEqual(resp.code, 401)

    def test_admin_allowed(self):
        self._seed_supervisor_audit_rows(1)
        resp = self.fetch("/api/audit/supervisor/export", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 200)

    def test_sco_allowed(self):
        self._seed_supervisor_audit_rows(1)
        resp = self.fetch("/api/audit/supervisor/export", headers={"Authorization": f"Bearer {self.sco_token}"})
        self.assertEqual(resp.code, 200)

    def test_co_forbidden(self):
        resp = self.fetch("/api/audit/supervisor/export", headers={"Authorization": f"Bearer {self.co_token}"})
        self.assertEqual(resp.code, 403)

    def test_client_forbidden(self):
        resp = self.fetch("/api/audit/supervisor/export", headers={"Authorization": f"Bearer {self.client_token}"})
        self.assertEqual(resp.code, 403)


class TestSupervisorAuditExportJSON(_AuditExportTestBase):
    """JSON output for /api/audit/supervisor/export."""

    def test_default_json(self):
        self._seed_supervisor_audit_rows(2)
        resp = self.fetch("/api/audit/supervisor/export", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body)
        self.assertIn("entries", body)
        self.assertGreaterEqual(body["total"], 2)

    def test_entries_have_required_fields(self):
        self._seed_supervisor_audit_rows(1)
        resp = self.fetch("/api/audit/supervisor/export?format=json", headers={"Authorization": f"Bearer {self.admin_token}"})
        body = json.loads(resp.body)
        self.assertGreater(len(body["entries"]), 0)
        entry = body["entries"][0]
        for field in ("timestamp", "actor_id", "action", "event_type", "application_id"):
            self.assertIn(field, entry)
        # Internal hash fields should be stripped
        self.assertNotIn("previous_hash", entry)
        self.assertNotIn("entry_hash", entry)

    def test_filter_by_action(self):
        self._seed_supervisor_audit_rows(2, action="review")
        self._seed_supervisor_audit_rows(1, action="escalate")
        resp = self.fetch("/api/audit/supervisor/export?action=escalate", headers={"Authorization": f"Bearer {self.admin_token}"})
        body = json.loads(resp.body)
        for entry in body["entries"]:
            self.assertEqual(entry["action"], "escalate")

    def test_filter_by_actor_user_id(self):
        self._seed_supervisor_audit_rows(2, actor_id="admin001")
        self._seed_supervisor_audit_rows(1, actor_id="other_user")
        resp = self.fetch("/api/audit/supervisor/export?actor_user_id=other_user", headers={"Authorization": f"Bearer {self.admin_token}"})
        body = json.loads(resp.body)
        for entry in body["entries"]:
            self.assertEqual(entry["actor_id"], "other_user")

    def test_include_decisions(self):
        self._seed_supervisor_audit_rows(1, actor_id="admin001", application_id="app_0")
        self._seed_decision_records(1, actor_user_id="admin001", application_ref="app_0")
        resp = self.fetch("/api/audit/supervisor/export?include_decisions=true", headers={"Authorization": f"Bearer {self.admin_token}"})
        body = json.loads(resp.body)
        found = [e for e in body["entries"] if e.get("application_id") == "app_0" and e.get("decision_type")]
        self.assertGreater(len(found), 0)
        self.assertEqual(found[0]["decision_type"], "approve")

    def test_invalid_start_date_returns_400(self):
        resp = self.fetch("/api/audit/supervisor/export?start_date=nope", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 400)


class TestSupervisorAuditExportCSV(_AuditExportTestBase):
    """CSV output for /api/audit/supervisor/export."""

    def test_csv_format(self):
        self._seed_supervisor_audit_rows(2)
        resp = self.fetch("/api/audit/supervisor/export?format=csv", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 200)
        self.assertIn("text/csv", resp.headers.get("Content-Type", ""))
        reader = csv.reader(io.StringIO(resp.body.decode()))
        headers = next(reader)
        self.assertIn("timestamp", headers)
        self.assertIn("actor_id", headers)
        self.assertIn("action", headers)

    def test_csv_no_decision_columns_by_default(self):
        self._seed_supervisor_audit_rows(1)
        resp = self.fetch("/api/audit/supervisor/export?format=csv", headers={"Authorization": f"Bearer {self.admin_token}"})
        reader = csv.reader(io.StringIO(resp.body.decode()))
        headers = next(reader)
        self.assertNotIn("decision_type", headers)

    def test_csv_with_decisions(self):
        self._seed_supervisor_audit_rows(1, actor_id="admin001", application_id="app_0")
        self._seed_decision_records(1, actor_user_id="admin001", application_ref="app_0")
        resp = self.fetch("/api/audit/supervisor/export?format=csv&include_decisions=true", headers={"Authorization": f"Bearer {self.admin_token}"})
        reader = csv.reader(io.StringIO(resp.body.decode()))
        headers = next(reader)
        self.assertIn("decision_type", headers)
        self.assertIn("decision_risk_level", headers)

    def test_invalid_format_returns_400(self):
        resp = self.fetch("/api/audit/supervisor/export?format=xml", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 400)
