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


def _legacy_contradictory_screening_summary():
    return {
        "terminal": True,
        "canonical_state": "completed_match",
        "screening_result": "match",
        "defensible_clear": False,
        "approval_ready": True,
        "approval_blocking": True,
        "blocking_reasons": ["director_screening_0:live_terminal_match"],
        "has_uncleared_completed_match": True,
        "completed_match_blocking": True,
    }


def test_sanitize_screening_readiness_summary_clears_legacy_ready_blocking_contradiction():
    from screening_state import sanitize_screening_readiness_summary

    sanitized = sanitize_screening_readiness_summary(_legacy_contradictory_screening_summary())

    assert sanitized["approval_ready"] is False
    assert sanitized["approval_blocking"] is True
    assert sanitized["screening_gate_ready"] is False
    assert sanitized["approval_gate_ready"] is False
    assert sanitized["approval_blocked_reasons"] == ["director_screening_0:live_terminal_match"]


class PR4ScreeningMemoReadinessMetadataTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_pr4_memo_readiness_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        self._seed_fixture()

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

    def _headers(self):
        return {"Authorization": f"Bearer {self.admin_token}"}

    def _json(self, response):
        return json.loads(response.body.decode() or "{}")

    def _seed_fixture(self):
        self.db.execute(
            """
            INSERT OR REPLACE INTO clients
                (id, email, password_hash, company_name, status)
            VALUES ('pr4_client', 'pr4_client@example.test', 'test-only', 'PR4 Client Ltd', 'active')
            """
        )
        self.db.execute(
            """
            INSERT OR REPLACE INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_score, risk_level, risk_dimensions, prescreening_data)
            VALUES ('pr4_memo_app', 'PR4-MEMO-APP', 'pr4_client', 'PR4 Memo Ltd',
                    'Mauritius', 'Technology', 'Company', 'under_review',
                    45, 'MEDIUM', ?, ?)
            """,
            (
                json.dumps({"jurisdiction": "LOW"}),
                json.dumps({"screening_report": {}}),
            ),
        )
        legacy_summary = _legacy_contradictory_screening_summary()
        memo_data = {
            "sections": {"summary": {"content": "Existing memo row"}},
            "metadata": {
                "screening_state_summary": legacy_summary,
                "agent5_input_contract": {
                    "screening_terminality_summary": dict(legacy_summary),
                },
            },
        }
        self.db.execute(
            """
            INSERT INTO compliance_memos
                (application_id, version, memo_data, review_status,
                 validation_status, quality_score, memo_version, created_at)
            VALUES ('pr4_memo_app', 1, ?, 'approved', 'pass', 0.91, '1.0',
                    '2026-06-01T10:00:00Z')
            """,
            (json.dumps(memo_data),),
        )
        self.db.commit()

    def test_application_detail_sanitizes_legacy_memo_screening_readiness_metadata(self):
        response = self.fetch(
            "/api/applications/pr4_memo_app",
            headers=self._headers(),
        )
        assert response.code == 200, response.body.decode()
        body = self._json(response)

        metadata = body["latest_memo_data"]["metadata"]
        summary = metadata["screening_state_summary"]
        assert summary["approval_ready"] is False
        assert summary["approval_blocking"] is True
        assert summary["screening_gate_ready"] is False
        assert summary["approval_gate_ready"] is False
        assert summary["approval_blocked_reasons"] == ["director_screening_0:live_terminal_match"]

        contract_summary = metadata["agent5_input_contract"]["screening_terminality_summary"]
        assert contract_summary["approval_ready"] is False
        assert contract_summary["approval_blocking"] is True
        assert contract_summary["screening_gate_ready"] is False
        assert contract_summary["approval_gate_ready"] is False
        assert contract_summary["approval_blocked_reasons"] == ["director_screening_0:live_terminal_match"]
