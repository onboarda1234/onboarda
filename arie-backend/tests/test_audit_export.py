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

from datetime import datetime, timedelta
from tornado.testing import AsyncHTTPTestCase


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

_db_ready = False


class _AuditExportTestBase(AsyncHTTPTestCase):
    """Shared setup for audit export tests."""

    def get_app(self):
        global _db_ready
        db_path = os.path.join(tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db")
        os.environ["DB_PATH"] = db_path
        if not _db_ready:
            from db import init_db, seed_initial_data, get_db
            init_db()
            try:
                conn = get_db()
                seed_initial_data(conn)
                conn.commit()
                conn.close()
            except Exception:
                pass
            _db_ready = True
        from server import make_app
        return make_app()

    def setUp(self):
        super().setUp()
        from db import get_db
        from server import create_token
        self._conn = get_db()
        self.admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        self.sco_token = create_token("sco001", "sco", "Test SCO", "officer")
        self.co_token = create_token("co001", "co", "Test CO", "officer")
        self.client_token = create_token("client001", "client", "Test Client", "client")

    def tearDown(self):
        # Clean up seeded test data to avoid contaminating other tests
        try:
            self._conn.execute("DELETE FROM audit_log WHERE detail LIKE 'detail_%'")
            self._conn.execute("DELETE FROM supervisor_audit_log WHERE detail LIKE 'detail_%'")
            self._conn.execute("DELETE FROM decision_records WHERE source = 'manual' AND actor_role = 'admin' AND risk_level = 'MEDIUM'")
            self._conn.commit()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass
        super().tearDown()

    # Convenience helpers
    def _seed_audit_rows(self, count=3, user_id="admin001", action="login"):
        for i in range(count):
            self._conn.execute(
                "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
                "VALUES (?,?,?,?,?,?,?)",
                (user_id, "Admin", "admin", action, f"target_{i}", f"detail_{i}", "127.0.0.1"),
            )
        self._conn.commit()

    def _seed_supervisor_audit_rows(self, count=3, actor_id="admin001", action="review"):
        for i in range(count):
            self._conn.execute(
                "INSERT INTO supervisor_audit_log (id, timestamp, event_type, severity, actor_id, actor_name, "
                "actor_role, action, detail, application_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    datetime.utcnow().isoformat(),
                    "pipeline_run",
                    "info",
                    actor_id,
                    "Admin",
                    "admin",
                    action,
                    f"detail_{i}",
                    f"app_{i}",
                ),
            )
        self._conn.commit()

    def _seed_decision_records(self, count=2, actor_user_id="admin001", application_ref="app_0"):
        for i in range(count):
            self._conn.execute(
                "INSERT INTO decision_records (id, application_ref, decision_type, risk_level, confidence_score, "
                "source, actor_user_id, actor_role, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    application_ref,
                    "approve",
                    "MEDIUM",
                    0.85,
                    "manual",
                    actor_user_id,
                    "admin",
                    datetime.utcnow().isoformat(),
                ),
            )
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

    def test_include_decisions(self):
        self._seed_audit_rows(1, user_id="admin001")
        self._seed_decision_records(1, actor_user_id="admin001")
        resp = self.fetch("/api/audit/export?include_decisions=true", headers={"Authorization": f"Bearer {self.admin_token}"})
        body = json.loads(resp.body)
        found = [e for e in body["entries"] if e.get("user_id") == "admin001" and e.get("decision_type")]
        self.assertGreater(len(found), 0)
        self.assertEqual(found[0]["decision_type"], "approve")


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
        self._seed_audit_rows(1, user_id="admin001")
        self._seed_decision_records(1, actor_user_id="admin001")
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
        self._seed_supervisor_audit_rows(1, actor_id="admin001")
        self._seed_decision_records(1, actor_user_id="admin001", application_ref="app_0")
        resp = self.fetch("/api/audit/supervisor/export?include_decisions=true", headers={"Authorization": f"Bearer {self.admin_token}"})
        body = json.loads(resp.body)
        found = [e for e in body["entries"] if e.get("application_id") == "app_0" and e.get("decision_type")]
        self.assertGreater(len(found), 0)
        self.assertEqual(found[0]["decision_type"], "approve")


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
        self._seed_supervisor_audit_rows(1, actor_id="admin001")
        self._seed_decision_records(1, actor_user_id="admin001", application_ref="app_0")
        resp = self.fetch("/api/audit/supervisor/export?format=csv&include_decisions=true", headers={"Authorization": f"Bearer {self.admin_token}"})
        reader = csv.reader(io.StringIO(resp.body.decode()))
        headers = next(reader)
        self.assertIn("decision_type", headers)
        self.assertIn("decision_risk_level", headers)

    def test_invalid_format_returns_400(self):
        resp = self.fetch("/api/audit/supervisor/export?format=xml", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(resp.code, 400)
