"""BSA PR-A regression coverage for supervisor security and CSRF scope."""

from __future__ import annotations

import ast
import inspect
import json
import logging
import re
from pathlib import Path
from unittest.mock import Mock, patch

import tornado.web
from tornado.testing import AsyncHTTPTestCase

from base_handler import BaseHandler
from supervisor import api as supervisor_api


ROOT = Path(__file__).resolve().parents[2]


class _ReviewResult:
    def model_dump(self):
        return {"ok": True}


class _PipelineResult:
    pipeline_id = "pipeline-security-test"

    def to_dict(self):
        return {"pipeline_id": self.pipeline_id, "status": "completed"}


class _FakeSupervisor:
    def __init__(self):
        self.run_calls = []

    async def run_pipeline(self, **kwargs):
        self.run_calls.append(kwargs)
        return _PipelineResult()

    def get_stats(self):
        return {"status": "ok"}


class _CsrfProbeHandler(BaseHandler):
    def post(self):
        self.write({"ok": True})


class TestSupervisorSecurityHTTP(AsyncHTTPTestCase):
    def get_app(self):
        from server import SumsubWebhookHandler
        from screening_complyadvantage.webhook_handler import ComplyAdvantageWebhookHandler

        return tornado.web.Application(
            supervisor_api.get_supervisor_routes()
            + [
                (r"/api/kyc/webhook", SumsubWebhookHandler),
                (r"/api/webhooks/complyadvantage", ComplyAdvantageWebhookHandler),
                (r"/api/kyc/webhook/", _CsrfProbeHandler),
                (r"/api/supervisor/webhook-bypass-test", _CsrfProbeHandler),
                (r"/api/supervisor/action", _CsrfProbeHandler),
                (r"/api/foo/webhook/evil", _CsrfProbeHandler),
                (r"/api/not-webhook", _CsrfProbeHandler),
            ],
            xsrf_cookies=False,
        )

    def setUp(self):
        super().setUp()
        self.token_users = {
            "admin-token": {
                "sub": "server-admin-1",
                "role": "admin",
                "name": "Server Admin",
                "type": "officer",
            },
            "co-token": {
                "sub": "server-co-1",
                "role": "co",
                "name": "Server CO",
                "type": "officer",
            },
            "analyst-token": {
                "sub": "server-analyst-1",
                "role": "analyst",
                "name": "Server Analyst",
                "type": "officer",
            },
            "client-token": {
                "sub": "server-client-1",
                "role": "client",
                "name": "Server Client",
                "type": "client",
            },
        }
        self.decode_patch = patch(
            "base_handler.decode_token",
            side_effect=lambda token: dict(self.token_users[token])
            if token in self.token_users
            else None,
        )
        self.decode_patch.start()
        # Exercise BaseHandler's real Bearer/cookie decode path without needing
        # a persistent actor table in this isolated Tornado application.
        self.actor_patch = patch.object(
            BaseHandler,
            "_validate_current_actor",
            autospec=True,
            side_effect=lambda _handler, token_user: token_user,
        )
        self.actor_patch.start()

        self.original_review_service = supervisor_api._review_service
        self.original_supervisor = supervisor_api._supervisor
        self.original_pipeline_cache = supervisor_api._pipeline_cache
        self.review_service = Mock()
        self.review_service.submit_review.return_value = _ReviewResult()
        self.review_service.escalate_case.return_value = {
            "escalation_id": "esc-security-test",
            "status": "pending",
        }
        self.review_service.get_pending_escalations.return_value = []
        self.fake_supervisor = _FakeSupervisor()
        supervisor_api._review_service = self.review_service
        supervisor_api._supervisor = self.fake_supervisor
        supervisor_api._pipeline_cache = {"pipeline-existing": _PipelineResult()}
        supervisor_api._pipeline_cache["pipeline-existing"].pipeline_id = "pipeline-existing"

        self.persist_patch = patch.object(supervisor_api, "persist_pipeline_result")
        self.persist_mock = self.persist_patch.start()

        import server
        from screening_complyadvantage import webhook_handler as ca_webhook

        self.sumsub_signature_patch = patch.object(
            server, "sumsub_verify_webhook", return_value=False
        )
        self.sumsub_signature_mock = self.sumsub_signature_patch.start()
        self.ca_signature_patch = patch.object(
            ca_webhook, "_signature_status", return_value="invalid"
        )
        self.ca_signature_mock = self.ca_signature_patch.start()

        self.admin_token = "admin-token"
        self.co_token = "co-token"
        self.analyst_token = "analyst-token"
        self.client_token = "client-token"

    def tearDown(self):
        self.ca_signature_patch.stop()
        self.sumsub_signature_patch.stop()
        self.persist_patch.stop()
        self.actor_patch.stop()
        self.decode_patch.stop()
        supervisor_api._review_service = self.original_review_service
        supervisor_api._supervisor = self.original_supervisor
        supervisor_api._pipeline_cache = self.original_pipeline_cache
        super().tearDown()

    @staticmethod
    def _bearer(token):
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _cookie(token, *, csrf_cookie=None, csrf_header=None):
        cookies = [f"arie_session={token}"]
        if csrf_cookie:
            cookies.append(f"csrf_token={csrf_cookie}")
        headers = {"Cookie": "; ".join(cookies), "Content-Type": "application/json"}
        if csrf_header:
            headers["X-CSRF-Token"] = csrf_header
        return headers

    def _post(self, path, payload, headers=None):
        return self.fetch(
            path,
            method="POST",
            body=json.dumps(payload),
            headers=headers or {"Content-Type": "application/json"},
            raise_error=False,
        )

    @staticmethod
    def _review_body(**extra):
        return {
            "pipeline_id": "pipeline-existing",
            "decision": "approve",
            "decision_reason": "Synthetic security test",
            **extra,
        }

    def test_supervisor_route_uses_common_security_headers_and_no_wildcard_cors(self):
        import base_handler

        # Simulate deployed same-origin policy; BaseHandler intentionally keeps
        # wildcard CORS only for local development/demo.
        with patch.multiple(
            base_handler,
            ALLOWED_ORIGIN="",
            IS_DEVELOPMENT=False,
            IS_DEMO=False,
        ):
            response = self.fetch(
                "/api/supervisor/dashboard",
                headers=self._bearer(self.admin_token),
                raise_error=False,
            )
        assert response.code == 200, response.body.decode()
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Server"] == "RegMind"
        assert response.headers.get("X-Request-ID")
        assert response.headers.get("Access-Control-Allow-Origin") != "*"

    def test_unauthenticated_and_client_roles_are_denied(self):
        unauthenticated = self._post(
            "/api/supervisor/review", self._review_body()
        )
        assert unauthenticated.code == 401

        client = self._post(
            "/api/supervisor/review",
            self._review_body(),
            self._bearer(self.client_token),
        )
        assert client.code == 403
        self.review_service.submit_review.assert_not_called()

    def test_existing_read_and_write_role_policy_is_preserved(self):
        analyst_read = self.fetch(
            "/api/supervisor/dashboard",
            headers=self._bearer(self.analyst_token),
            raise_error=False,
        )
        assert analyst_read.code == 200, analyst_read.body.decode()

        analyst_write = self._post(
            "/api/supervisor/review",
            self._review_body(),
            self._bearer(self.analyst_token),
        )
        assert analyst_write.code == 403

        co_write = self._post(
            "/api/supervisor/review",
            self._review_body(),
            self._bearer(self.co_token),
        )
        assert co_write.code == 200, co_write.body.decode()

    def test_cookie_auth_supervisor_write_requires_csrf(self):
        response = self._post(
            "/api/supervisor/review",
            self._review_body(),
            self._cookie(self.admin_token, csrf_cookie="csrf-good"),
        )
        assert response.code == 403
        assert "CSRF" in response.body.decode()
        self.review_service.submit_review.assert_not_called()

    def test_cookie_auth_supervisor_write_with_csrf_succeeds(self):
        response = self._post(
            "/api/supervisor/review",
            self._review_body(),
            self._cookie(
                self.admin_token,
                csrf_cookie="csrf-good",
                csrf_header="csrf-good",
            ),
        )
        assert response.code == 200, response.body.decode()
        self.review_service.submit_review.assert_called_once()

    def test_bearer_review_ignores_forged_actor_and_persists_session_actor(self):
        with self.assertLogs("arie.supervisor.api", level=logging.WARNING) as logs:
            response = self._post(
                "/api/supervisor/review",
                self._review_body(
                    reviewer_id="forged-id",
                    reviewer_name="Forged Name",
                    reviewer_role="admin",
                ),
                self._bearer(self.co_token),
            )
        assert response.code == 200, response.body.decode()
        kwargs = self.review_service.submit_review.call_args.kwargs
        assert kwargs["reviewer_id"] == "server-co-1"
        assert kwargs["reviewer_name"] == "Server CO"
        assert kwargs["reviewer_role"] == "co"
        assert any("supervisor_actor_forgery_attempt" in line for line in logs.output)

    def test_review_storage_failure_returns_controlled_500(self):
        self.review_service.submit_review.side_effect = RuntimeError(
            "synthetic storage failure"
        )
        response = self._post(
            "/api/supervisor/review",
            self._review_body(),
            self._bearer(self.co_token),
        )
        assert response.code == 500
        assert "synthetic storage failure" not in response.body.decode()

    def test_review_read_failure_is_not_returned_as_empty_success(self):
        self.review_service.get_reviews.side_effect = RuntimeError(
            "synthetic read failure"
        )
        response = self.fetch(
            "/api/supervisor/reviews",
            headers=self._bearer(self.co_token),
            raise_error=False,
        )
        assert response.code == 500
        assert "synthetic read failure" not in response.body.decode()

    def test_escalation_ignores_forged_actor_and_persists_session_actor(self):
        response = self._post(
            "/api/supervisor/escalate",
            {
                "application_id": "synthetic-app",
                "pipeline_id": "pipeline-existing",
                "escalation_level": "senior_review",
                "reason": "Synthetic security test",
                "escalated_by": "Forged Escalator",
                "escalated_by_role": "admin",
            },
            self._bearer(self.co_token),
        )
        assert response.code == 200, response.body.decode()
        kwargs = self.review_service.escalate_case.call_args.kwargs
        assert kwargs["escalated_by_id"] == "server-co-1"
        assert kwargs["escalated_by"] == "Server CO"
        assert kwargs["escalated_by_role"] == "co"

    def test_pipeline_trigger_source_is_server_derived(self):
        response = self._post(
            "/api/supervisor/pipeline/run",
            {
                "application_id": "synthetic-app",
                "trigger_type": "onboarding",
                "trigger_source": "forged-client-source",
            },
            self._bearer(self.admin_token),
        )
        assert response.code == 200, response.body.decode()
        assert self.fake_supervisor.run_calls[0]["trigger_source"] == (
            "supervisor_api:server-admin-1"
        )
        assert self.persist_mock.call_args.kwargs["trigger_source"] == (
            "supervisor_api:server-admin-1"
        )

    def test_exact_webhook_paths_bypass_csrf_but_keep_signature_checks(self):
        cookie = self._cookie(self.admin_token)

        sumsub = self._post("/api/kyc/webhook", {}, cookie)
        assert sumsub.code == 401
        self.sumsub_signature_mock.assert_called_once()

        ca = self._post("/api/webhooks/complyadvantage", {}, cookie)
        assert ca.code == 401
        self.ca_signature_mock.assert_called_once()

        sumsub_query = self._post("/api/kyc/webhook?delivery=1", {}, cookie)
        assert sumsub_query.code == 401
        assert self.sumsub_signature_mock.call_count == 2

    def test_webhook_substrings_query_injection_and_trailing_slash_are_not_exempt(self):
        cookie = self._cookie(self.admin_token)
        paths = (
            "/api/supervisor/webhook-bypass-test",
            "/api/foo/webhook/evil",
            "/api/not-webhook",
            "/api/supervisor/action?x=/webhook",
            "/api/kyc/webhook/",
        )
        for path in paths:
            response = self._post(path, {}, cookie)
            assert response.code == 403, (path, response.code, response.body.decode())
            assert "CSRF" in response.body.decode()


def test_supervisor_basehandler_collision_decisions_use_common_implementations():
    cls = supervisor_api.SupervisorBaseHandler
    assert issubclass(cls, BaseHandler)
    for method in (
        "prepare",
        "set_default_headers",
        "options",
        "write_error",
        "get_current_user_token",
        "require_auth",
    ):
        assert method not in cls.__dict__
        assert getattr(cls, method) is getattr(BaseHandler, method)
    assert cls.get_json_body is not BaseHandler.get_json
    assert "return self.get_json()" in inspect.getsource(cls.get_json_body)


def test_missing_server_actor_provenance_fails_closed():
    handler = supervisor_api.SupervisorBaseHandler.__new__(
        supervisor_api.SupervisorBaseHandler
    )
    handler.write_error_json = Mock()
    actor = handler.get_server_actor(
        {"sub": "actor-without-name", "role": "co"}, "review_submit"
    )
    assert actor is None
    handler.write_error_json.assert_called_once_with(
        403, "Authenticated actor provenance unavailable"
    )


def test_server_actor_mapping_prefers_actual_basehandler_claims_with_trusted_fallbacks():
    handler = supervisor_api.SupervisorBaseHandler.__new__(
        supervisor_api.SupervisorBaseHandler
    )
    actor = handler.get_server_actor(
        {
            "sub": "canonical-sub",
            "user_id": "legacy-user-id",
            "id": "legacy-id",
            "name": "Authenticated Name",
            "email": "actor@example.test",
            "role": "SCO",
        },
        "review_submit",
    )
    assert actor == {
        "id": "canonical-sub",
        "name": "Authenticated Name",
        "role": "sco",
    }

    fallback = handler.get_server_actor(
        {"user_id": "trusted-user-id", "email": "actor@example.test", "role": "co"},
        "review_submit",
    )
    assert fallback == {
        "id": "trusted-user-id",
        "name": "actor@example.test",
        "role": "co",
    }


def test_all_registered_supervisor_routes_use_supervisor_basehandler():
    routes = supervisor_api.get_supervisor_routes()
    assert len(routes) == 14
    assert all(issubclass(handler, supervisor_api.SupervisorBaseHandler) for _, handler in routes)


def test_state_changing_supervisor_actor_sweep_is_explicit():
    source = Path(supervisor_api.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    state_changing = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id == "SupervisorBaseHandler"
            for base in node.bases
        )
        and any(
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name in {"post", "put", "patch", "delete"}
            for child in node.body
        )
    }
    assert state_changing == {
        "PipelineRunHandler",
        "ReviewSubmitHandler",
        "EscalationHandler",
        "AssistantReviewHandler",
    }
    # The first three persist action provenance and are covered above.
    # AssistantReviewHandler returns a generated summary and does not persist an actor.
    assistant_source = inspect.getsource(supervisor_api.AssistantReviewHandler)
    assert "reviewer_" not in assistant_source
    assert "escalated_by" not in assistant_source
    assert "persist" not in assistant_source


def test_no_browser_state_changing_api_supervisor_calls_exist():
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    assert "/api/supervisor/" not in html
    supervisor_fetches = re.findall(
        r"fetch\(BO_API_BASE \+ '(/supervisor[^']*)'\s*,\s*\{(.*?)\}\)",
        html,
        flags=re.S,
    )
    assert supervisor_fetches
    assert all(
        not re.search(r"method\s*:\s*['\"](?:POST|PUT|PATCH|DELETE)['\"]", options)
        for _, options in supervisor_fetches
    )
    # The one browser-side supervisor write uses a separate BaseHandler route
    # and explicitly sends Bearer auth; PR-A does not need a frontend change.
    run_start = html.index("async function runSupervisorPipeline")
    run_end = html.index("async function loadSupervisorForApp", run_start)
    run_source = html[run_start:run_end]
    assert "/applications/' + app.ref + '/supervisor/run" in run_source
    assert "'Authorization': 'Bearer '" in run_source


def test_provider_webhook_signature_code_remains_present():
    from server import SumsubWebhookHandler
    from screening_complyadvantage.webhook_handler import ComplyAdvantageWebhookHandler

    assert "sumsub_verify_webhook" in inspect.getsource(SumsubWebhookHandler)
    assert "_signature_status" in inspect.getsource(ComplyAdvantageWebhookHandler)
