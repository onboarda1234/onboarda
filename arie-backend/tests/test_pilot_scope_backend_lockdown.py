import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tornado.testing import AsyncHTTPTestCase


class PilotScopeBackendLockdownTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_pilot_scope_lockdown_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        os.environ["DB_PATH"] = self._db_path

        import config as config_module
        import db as db_module

        self._orig_config_db_path = config_module.DB_PATH
        self._orig_db_db_path = db_module.DB_PATH
        config_module.DB_PATH = self._db_path
        db_module.DB_PATH = self._db_path
        db_module.init_db()
        conn = db_module.get_db()
        db_module.seed_initial_data(conn)
        self._app_id = "pilot-lockdown-app"
        conn.execute(
            """
            INSERT OR IGNORE INTO applications
                (id, ref, company_name, country, sector, ownership_structure, risk_level, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (self._app_id, "APP-PILOT-LOCK", "Pilot Lockdown Ltd", "Mauritius", "Fintech", "single-tier", "HIGH", "submitted"),
        )
        conn.commit()
        conn.close()

        from server import make_app

        return make_app()

    def setUp(self):
        super().setUp()
        import db as db_module
        import environment as environment_module
        import server as server_module

        self._server = server_module
        self._environment = environment_module
        self._conn = db_module.get_db()
        self._orig_flags = dict(server_module.flags._cache)
        self._orig_environment_flags = dict(environment_module.flags._cache)
        lockdown_flags = {
            "ENABLE_REGULATORY_INTELLIGENCE_FULL": False,
            "ENABLE_SAR_WORKFLOW": False,
            "ENABLE_SAR_STR": False,
            "ENABLE_AI_SUPERVISOR": False,
            "ENABLE_SUPERVISOR_DASHBOARD": False,
            "ENABLE_SUPERVISOR_AUDIT": False,
            "ENABLE_KPI_DASHBOARD": False,
            "ENABLE_KPI_DEMO_DATA": False,
        }
        server_module.flags._cache.update(lockdown_flags)
        environment_module.flags._cache.update(lockdown_flags)
        self.admin_token = server_module.create_token("admin001", "admin", "Test Admin", "officer")
        self.co_token = server_module.create_token("co001", "co", "Test CO", "officer")

    def tearDown(self):
        try:
            self._server.flags._cache.clear()
            self._server.flags._cache.update(self._orig_flags)
            self._environment.flags._cache.clear()
            self._environment.flags._cache.update(self._orig_environment_flags)
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            import config as config_module
            import db as db_module

            config_module.DB_PATH = self._orig_config_db_path
            db_module.DB_PATH = self._orig_db_db_path
        except Exception:
            pass
        try:
            os.unlink(self._db_path)
        except Exception:
            pass
        super().tearDown()

    def _headers(self, token=None):
        return {
            "Authorization": f"Bearer {token or self.admin_token}",
            "Content-Type": "application/json",
        }

    def _json(self, response):
        return json.loads(response.body.decode("utf-8"))

    def _post_json(self, path, payload, token=None):
        return self.fetch(
            path,
            method="POST",
            headers=self._headers(token),
            body=json.dumps(payload),
            raise_error=False,
        )

    def test_sar_create_and_auto_trigger_are_disabled_without_records(self):
        self._conn.execute(
            """
            INSERT INTO monitoring_alerts
                (application_id, client_name, alert_type, severity, status, detected_by, summary, source_reference, ai_recommendation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (self._app_id, "Pilot Lockdown Ltd", "adverse_media", "high", "open", "Agent 7", "High risk alert", "alert:test", "Review"),
        )
        alert_id = self._conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        self._conn.commit()

        create = self._post_json("/api/sar", {
            "subject_name": "Pilot Lockdown Ltd",
            "narrative": "Pilot test must not create SAR.",
        })
        auto = self._post_json("/api/sar/auto-trigger", {"alert_id": alert_id}, token=self.co_token)

        for response in (create, auto):
            assert response.code == 403
            body = self._json(response)
            assert body["code"] == "enterprise_module_inactive"
            assert body["module"] == "SAR/STR"
            assert body["availability"] == "Not active in pilot"
            assert "ENABLE_" not in response.body.decode("utf-8")
            assert "SECRET" not in response.body.decode("utf-8")

        count = self._conn.execute("SELECT COUNT(*) AS c FROM sar_reports").fetchone()["c"]
        alert = self._conn.execute("SELECT officer_action FROM monitoring_alerts WHERE id=?", (alert_id,)).fetchone()
        assert count == 0
        assert alert["officer_action"] is None

    def test_ai_supervisor_run_and_result_are_disabled_without_pipeline_records(self):
        run = self._post_json(f"/api/applications/{self._app_id}/supervisor/run", {"trigger_type": "onboarding"}, token=self.co_token)
        result = self.fetch(
            f"/api/applications/{self._app_id}/supervisor/result",
            method="GET",
            headers=self._headers(self.co_token),
            raise_error=False,
        )

        assert run.code == 403
        assert result.code == 403
        assert self._json(run)["module"] == "AI Compliance Supervisor"
        assert self._json(result)["module"] == "AI Compliance Supervisor"
        count = self._conn.execute("SELECT COUNT(*) AS c FROM supervisor_pipeline_results").fetchone()["c"]
        audit_count = self._conn.execute("SELECT COUNT(*) AS c FROM supervisor_audit_log").fetchone()["c"]
        assert count == 0
        assert audit_count == 0

    def test_regulatory_intelligence_and_supervisor_audit_are_disabled(self):
        reg = self.fetch(
            "/api/regulatory-intelligence",
            method="GET",
            headers=self._headers(),
            raise_error=False,
        )
        audit = self.fetch(
            "/api/audit/supervisor/export",
            method="GET",
            headers=self._headers(),
            raise_error=False,
        )

        assert reg.code == 403
        assert audit.code == 403
        assert self._json(reg)["module"] == "Regulatory Intelligence"
        assert self._json(audit)["module"] == "AI Compliance Supervisor Audit"

    def test_agent_8_9_10_cannot_be_enabled_via_config_api(self):
        response = self._post_json("/api/config/ai-agents", {
            "agent_number": 8,
            "name": "Enterprise Roadmap Agent",
            "stage": "Monitoring",
            "enabled": True,
            "checks": ["roadmap check"],
        })

        assert response.code == 400
        body = self._json(response)
        assert body["code"] == "ai_agent_invalid"
        assert any(err["code"] == "enterprise_agent_inactive" for err in body["errors"])

    def test_config_environment_exposes_pilot_lockdown_flags_without_secrets(self):
        response = self.fetch(
            "/api/config/environment",
            method="GET",
            headers=self._headers(),
            raise_error=False,
        )

        assert response.code == 200
        body = self._json(response)
        features = body["features"]
        for flag in (
            "ENABLE_REGULATORY_INTELLIGENCE_FULL",
            "ENABLE_SAR_WORKFLOW",
            "ENABLE_SAR_STR",
            "ENABLE_AI_SUPERVISOR",
            "ENABLE_SUPERVISOR_DASHBOARD",
            "ENABLE_SUPERVISOR_AUDIT",
            "ENABLE_KPI_DASHBOARD",
            "ENABLE_KPI_DEMO_DATA",
        ):
            assert features[flag] is False
        encoded = response.body.decode("utf-8").upper()
        assert "SECRET" not in encoded
        assert "PASSWORD" not in encoded
