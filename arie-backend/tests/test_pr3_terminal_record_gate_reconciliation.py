import inspect
import json
import os
import sys
import tempfile
import uuid

from tornado.testing import AsyncHTTPTestCase


def _sync_test_db_path(path):
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


def _capture_db_path_state():
    state = {"env": os.environ.get("DB_PATH"), "modules": {}}
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        attrs = {}
        if module is not None:
            for attr in ("DB_PATH", "_CFG_DB_PATH"):
                attrs[attr] = (
                    hasattr(module, attr),
                    getattr(module, attr, None),
                )
        state["modules"][module_name] = attrs
    return state


def _restore_db_path_state(state):
    original_env = state.get("env")
    if original_env is None:
        os.environ.pop("DB_PATH", None)
    else:
        os.environ["DB_PATH"] = original_env
    for module_name, attrs in state.get("modules", {}).items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attr, (existed, value) in attrs.items():
            if existed:
                setattr(module, attr, value)
            elif hasattr(module, attr):
                delattr(module, attr)


class PR3TerminalRecordGateReconciliationTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_pr3_terminal_gate_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        _sync_test_db_path(self._db_path)

        from db import init_db, seed_initial_data, get_db
        from server import make_app

        init_db()
        db = get_db()
        seed_initial_data(db)
        db.commit()
        db.close()
        return make_app()

    def setUp(self):
        super().setUp()
        from db import get_db
        from server import create_token

        self.db = get_db()
        self.admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        self.client_token = create_token("pr3_client", "client", "PR3 Client Ltd", "client")
        self._seed_fixture_data()

    def tearDown(self):
        self.db.close()
        super().tearDown()
        db_path = getattr(self, "_db_path", None)
        if db_path:
            try:
                os.unlink(db_path)
            except OSError:
                pass
        _restore_db_path_state(getattr(self, "_db_path_state", {}))

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _json(self, response):
        return json.loads(response.body.decode() or "{}")

    def _seed_application(self, app_id, ref, status, *, client_id="pr3_client", decided_at=None):
        self.db.execute(
            """
            INSERT OR REPLACE INTO clients
                (id, email, password_hash, company_name, status)
            VALUES (?, ?, 'test-only', ?, 'active')
            """,
            (client_id, f"{client_id}@example.test", f"{client_id} Ltd"),
        )
        self.db.execute(
            """
            INSERT OR REPLACE INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_score, risk_level, risk_dimensions, decided_at, decision_by,
                 prescreening_data)
            VALUES (?, ?, ?, ?, 'Mauritius', 'Technology', 'Company', ?, 21, 'LOW', ?, ?, 'admin001', ?)
            """,
            (
                app_id,
                ref,
                client_id,
                f"{ref} Ltd",
                status,
                json.dumps({"jurisdiction": "LOW"}),
                decided_at,
                json.dumps({}),
            ),
        )

    def _seed_decision_record(self, app_ref, decision_type, *, with_gate_snapshot):
        extra = {"decision_reason": "Historical decision recorded"}
        if with_gate_snapshot:
            extra["approval_gate_snapshot"] = {
                "checked_at": "2026-06-01T10:00:00Z",
                "result": "pass",
                "validator": "ApprovalGateValidator.validate_approval",
                "blocker_count": 0,
                "blockers": [],
                "risk_level": "LOW",
                "risk_score": 21,
            }
        self.db.execute(
            """
            INSERT OR REPLACE INTO decision_records
                (id, application_ref, decision_type, risk_level, confidence_score,
                 source, actor_user_id, actor_role, timestamp, key_flags,
                 override_flag, override_reason, extra_json)
            VALUES (?, ?, ?, 'LOW', 0.91, 'manual', 'admin001', 'admin',
                    '2026-06-01T10:01:00Z', '[]', 0, NULL, ?)
            """,
            (
                f"pr3_decision_{app_ref}",
                app_ref,
                decision_type,
                json.dumps(extra),
            ),
        )

    def _seed_fixture_data(self):
        self._seed_application(
            "pr3_approved_snapshot",
            "PR3-APPROVED-SNAPSHOT",
            "approved",
            decided_at="2026-06-01T10:02:00Z",
        )
        self._seed_decision_record("PR3-APPROVED-SNAPSHOT", "approve", with_gate_snapshot=True)

        self._seed_application(
            "pr3_approved_legacy",
            "PR3-APPROVED-LEGACY",
            "approved",
            decided_at="2026-05-01T10:02:00Z",
        )

        self._seed_application(
            "pr3_rejected_legacy",
            "PR3-REJECTED-LEGACY",
            "rejected",
            decided_at="2026-05-02T10:02:00Z",
        )

        self._seed_application(
            "pr3_active_blocked",
            "PR3-ACTIVE-BLOCKED",
            "in_review",
        )
        # No screening_report is intentionally seeded for this active fixture;
        # collect_approval_gate_blockers deterministically emits screening_missing.
        self.db.commit()

    def test_approved_record_with_decision_snapshot_separates_current_diagnostics(self):
        response = self.fetch(
            "/api/applications/pr3_approved_snapshot",
            headers=self._headers(self.admin_token),
        )
        assert response.code == 200, response.body.decode()
        body = self._json(response)

        assert body["status"] == "approved"
        assert body["gate_blockers"] == []
        assert body["gate_blocker_count"] == 0
        assert body["approval_gate_presentation"]["mode"] == "terminal_decision_context"
        assert body["approval_gate_presentation"]["legacy_evidence_incomplete"] is False
        assert body["decision_basis"]["available"] is True
        assert body["decision_basis"]["approval_gate_snapshot_available"] is True
        assert body["decision_basis"]["approval_gate_snapshot"]["result"] == "pass"
        assert body["current_gate_diagnostics"]["applies_to"] == "current_state_only"
        assert isinstance(body["current_gate_diagnostics"]["blocker_count"], int)

    def test_legacy_approved_record_is_labelled_without_action_required_gate_blockers(self):
        response = self.fetch(
            "/api/applications/pr3_approved_legacy",
            headers=self._headers(self.admin_token),
        )
        assert response.code == 200, response.body.decode()
        body = self._json(response)

        assert body["status"] == "approved"
        assert body["gate_blockers"] == []
        assert body["gate_blocker_count"] == 0
        assert body["approval_gate_presentation"]["mode"] == "terminal_decision_context"
        assert body["approval_gate_presentation"]["legacy_evidence_incomplete"] is True
        assert body["decision_basis"]["available"] is False
        assert isinstance(body["current_gate_diagnostics"]["blocker_count"], int)

    def test_rejected_terminal_record_does_not_show_approval_action_blockers(self):
        response = self.fetch(
            "/api/applications/pr3_rejected_legacy",
            headers=self._headers(self.admin_token),
        )
        assert response.code == 200, response.body.decode()
        body = self._json(response)

        assert body["status"] == "rejected"
        assert body["gate_blockers"] == []
        assert body["gate_blocker_count"] == 0
        assert body["approval_gate_presentation"]["mode"] == "terminal_decision_context"
        assert isinstance(body["current_gate_diagnostics"]["blocker_count"], int)

    def test_non_terminal_record_keeps_fail_closed_approval_gate_blockers(self):
        response = self.fetch(
            "/api/applications/pr3_active_blocked",
            headers=self._headers(self.admin_token),
        )
        assert response.code == 200, response.body.decode()
        body = self._json(response)

        assert body["status"] == "in_review"
        assert body["approval_gate_presentation"]["mode"] == "active_approval_gate"
        assert body["gate_blocker_count"] > 0
        assert body["gate_blockers"], "Active records must retain blocking gate failures"
        assert any(blocker.get("id") == "screening_missing" for blocker in body["gate_blockers"])
        assert body["current_gate_diagnostics"] is None

    def test_terminal_gate_fields_are_not_exposed_to_client_projection(self):
        response = self.fetch(
            "/api/applications/pr3_approved_snapshot",
            headers=self._headers(self.client_token),
        )
        assert response.code == 200, response.body.decode()
        body = self._json(response)
        forbidden = {
            "approval_gate_presentation",
            "current_gate_diagnostics",
            "decision_basis",
            "gate_blockers",
            "gate_blocker_count",
        }
        assert not forbidden.intersection(body)


def test_application_decision_records_approval_gate_snapshot_for_future_approvals():
    from server import ApplicationDecisionHandler

    source = inspect.getsource(ApplicationDecisionHandler.post)
    assert "approval_gate_snapshot" in source
    assert "ApprovalGateValidator.validate_approval" in source
    assert "decision_record.setdefault(\"extra\", {})[\"approval_gate_snapshot\"]" in source


def test_backoffice_case_command_centre_has_terminal_decision_context_renderer():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    html_path = os.path.join(root, "arie-backoffice.html")
    with open(html_path, "r", encoding="utf-8") as handle:
        source = handle.read()

    assert "function renderTerminalCaseCommandCentre" in source
    assert "Current-state diagnostics only; not the historical approval basis." in source
    assert "Legacy evidence incomplete" in source
    assert "if (isTerminalGatePresentation(app)) return [];" in source
    assert "renderTerminalCaseCommandCentre(app, container);" in source
