"""Register item 33 — server-side pilot-scope guard.

PR-PILOT-SCOPE-1/1B gated the enterprise modules behind per-module feature
flags. Item 33 is the residual control: a server-side veto ABOVE those flags,
so a pilot deployment refuses enterprise surfaces even when an individual flag
is on. That matters because the deployed values of three enterprise flags live
outside version control (`render.yaml` pins them `sync: false`, i.e. hand-set
in a hosting dashboard) — today the exclusion is a convention no test can see.

The load-bearing test here is the mirror image of the existing lockdown suite:
`test_pilot_scope_backend_lockdown.py` proves the modules are refused when the
flags are OFF; this proves they are refused when the flags are **ON** and
`PILOT_SCOPE` is active. That is the whole value of item 33.

Also covers the two AI-Supervisor surfaces PR-PILOT-SCOPE-1B missed
(`/memo/supervisor/run`, `/memo/supervisor`), which had no enterprise gate at
all and were reachable by a broader role set (`analyst`) than their gated
siblings `/supervisor/run` and `/supervisor/result`.
"""

import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

import pytest
from tornado.testing import AsyncHTTPTestCase

import environment as environment_module


class TestPilotScopeResolution:
    """Unit tests for the resolver — defaults and fail-closed parsing."""

    def _resolve(self, env, raw, monkeypatch):
        if raw is None:
            monkeypatch.delenv(environment_module.PILOT_SCOPE_VAR, raising=False)
        else:
            monkeypatch.setenv(environment_module.PILOT_SCOPE_VAR, raw)
        return environment_module.pilot_scope_active(env)

    @pytest.mark.parametrize("env,expected", [
        ("staging", True),
        ("production", True),
        ("development", False),
        ("demo", False),
        ("testing", False),
    ])
    def test_defaults_are_behaviour_preserving(self, env, expected, monkeypatch):
        """ON where every enterprise flag already defaults off (a no-op that
        makes the exclusion enforceable); OFF where those modules are
        deliberately enabled, so dev/demo/CI behaviour is unchanged."""
        assert self._resolve(env, None, monkeypatch) is expected

    def test_unknown_environment_defaults_guard_on(self, monkeypatch):
        assert self._resolve("some-new-env", None, monkeypatch) is True

    @pytest.mark.parametrize("raw", ["false", "FALSE", " False ", "0", "no", "off"])
    def test_explicit_disable_values_turn_the_guard_off(self, raw, monkeypatch):
        assert self._resolve("staging", raw, monkeypatch) is False

    @pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "", "flase", "maybe", "  "])
    def test_anything_else_leaves_the_guard_on(self, raw, monkeypatch):
        """Fail-closed, inverting FeatureFlags' parse direction on purpose: a
        stuck-on guard refuses an enterprise module; a stuck-off guard exposes
        one. Only a well-formed disable may turn it off."""
        assert self._resolve("staging", raw, monkeypatch) is True

    def test_env_var_overrides_the_environment_default(self, monkeypatch):
        assert self._resolve("development", "true", monkeypatch) is True

    def test_scope_var_name_carries_no_enable_prefix(self):
        """The lockdown suite asserts no "ENABLE_" substring reaches a disabled
        response body; keeping the name unprefixed keeps that assertion true
        if the value is ever surfaced."""
        assert "ENABLE_" not in environment_module.PILOT_SCOPE_VAR


class PilotScopeGuardVetoTest(AsyncHTTPTestCase):
    """Enterprise modules stay refused with every enterprise flag ON."""

    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_pilot_scope_guard_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        self._app_id = "pilot-scope-guard-app"
        conn.execute(
            """
            INSERT OR IGNORE INTO applications
                (id, ref, company_name, country, sector, ownership_structure, risk_level, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (self._app_id, "APP-PILOT-GUARD", "Pilot Guard Ltd", "Mauritius",
             "Fintech", "single-tier", "HIGH", "submitted"),
        )
        conn.commit()
        conn.close()

        from server import make_app

        return make_app()

    def setUp(self):
        super().setUp()
        import server as server_module

        self._server = server_module
        self._orig_flags = dict(server_module.flags._cache)
        self._orig_env_flags = dict(environment_module.flags._cache)
        self._orig_scope = os.environ.get(environment_module.PILOT_SCOPE_VAR)

        # Every enterprise flag ON — the adversarial case. Without the item-33
        # veto each of these routes would serve.
        enterprise_on = {
            "ENABLE_REGULATORY_INTELLIGENCE_FULL": True,
            "ENABLE_SAR_WORKFLOW": True,
            "ENABLE_SAR_STR": True,
            "ENABLE_AI_SUPERVISOR": True,
            "ENABLE_SUPERVISOR_AUDIT": True,
        }
        server_module.flags._cache.update(enterprise_on)
        environment_module.flags._cache.update(enterprise_on)
        os.environ[environment_module.PILOT_SCOPE_VAR] = "true"

        self.admin_token = server_module.create_token(
            "admin001", "admin", "Test Admin", "officer")
        self.analyst_token = server_module.create_token(
            "analyst001", "analyst", "Test Analyst", "officer")

    def tearDown(self):
        try:
            self._server.flags._cache.clear()
            self._server.flags._cache.update(self._orig_flags)
            environment_module.flags._cache.clear()
            environment_module.flags._cache.update(self._orig_env_flags)
        except Exception:
            pass
        if self._orig_scope is None:
            os.environ.pop(environment_module.PILOT_SCOPE_VAR, None)
        else:
            os.environ[environment_module.PILOT_SCOPE_VAR] = self._orig_scope
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

    def _fetch(self, path, method="GET", payload=None, token=None):
        kwargs = {"method": method, "headers": self._headers(token), "raise_error": False}
        if method in ("POST", "PUT"):
            kwargs["body"] = json.dumps(payload or {})
        return self.fetch(path, **kwargs)

    def _assert_refused(self, response, module, path):
        assert response.code == 403, f"{path} returned {response.code}: {response.body!r}"
        body = json.loads(response.body.decode("utf-8"))
        assert body["code"] == "enterprise_module_inactive", path
        assert body["module"] == module, path
        assert body["availability"] == "Not active in pilot", path
        text = response.body.decode("utf-8")
        assert "ENABLE_" not in text, path
        assert "PILOT_SCOPE" not in text, path
        assert "SECRET" not in text, path

    def test_sar_routes_refused_despite_flags_on(self):
        for method, path, payload in (
            ("GET", "/api/sar", None),
            ("POST", "/api/sar", {"subject_name": "X", "narrative": "n"}),
            ("POST", "/api/sar/auto-trigger", {"alert_id": 1}),
        ):
            self._assert_refused(self._fetch(path, method, payload), "SAR/STR", path)

    def test_regulatory_intelligence_refused_despite_flag_on(self):
        resp = self._fetch("/api/regulatory-intelligence")
        self._assert_refused(resp, "Regulatory Intelligence", "/api/regulatory-intelligence")

    def test_ai_supervisor_routes_refused_despite_flag_on(self):
        for method, path in (
            ("POST", f"/api/applications/{self._app_id}/supervisor/run"),
            ("GET", f"/api/applications/{self._app_id}/supervisor/result"),
        ):
            self._assert_refused(
                self._fetch(path, method), "AI Compliance Supervisor", path)

    def test_memo_supervisor_surfaces_are_gated(self):
        """The PR-PILOT-SCOPE-1B gap: these two had NO enterprise gate at all."""
        for method, path in (
            ("POST", f"/api/applications/{self._app_id}/memo/supervisor/run"),
            ("GET", f"/api/applications/{self._app_id}/memo/supervisor"),
        ):
            self._assert_refused(
                self._fetch(path, method), "AI Compliance Supervisor", path)

    def test_memo_supervisor_gap_closed_for_the_broader_role_too(self):
        """These handlers admit `analyst` — a role the gated siblings refuse —
        so the enterprise gate must hold for analyst as well."""
        for method, path in (
            ("POST", f"/api/applications/{self._app_id}/memo/supervisor/run"),
            ("GET", f"/api/applications/{self._app_id}/memo/supervisor"),
        ):
            self._assert_refused(
                self._fetch(path, method, token=self.analyst_token),
                "AI Compliance Supervisor", path)

    def test_supervisor_audit_export_refused_despite_flags_on(self):
        resp = self._fetch("/api/audit/supervisor/export")
        self._assert_refused(
            resp, "AI Compliance Supervisor Audit", "/api/audit/supervisor/export")

    def test_guard_off_restores_flag_driven_behaviour(self):
        """The veto must not be a one-way door: with PILOT_SCOPE explicitly
        disabled the flags decide again, so dev/demo keep their modules."""
        os.environ[environment_module.PILOT_SCOPE_VAR] = "false"
        resp = self._fetch(f"/api/applications/{self._app_id}/memo/supervisor")
        assert resp.code != 403 or (
            json.loads(resp.body.decode("utf-8")).get("code")
            != "enterprise_module_inactive"
        ), "enterprise gate should not fire when the pilot-scope veto is off"

    def test_pilot_scope_routes_are_unaffected(self):
        """Sanity: the guard vetoes ENTERPRISE surfaces only. A core pilot
        route must keep serving with the veto active."""
        resp = self._fetch("/api/applications")
        assert resp.code == 200, resp.body[:200]
