import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets
from tornado.testing import AsyncHTTPTestCase

from branding import BRAND


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


def test_sanitize_screening_readiness_summary_normalizes_legacy_string_booleans():
    from screening_state import sanitize_screening_readiness_summary

    sanitized = sanitize_screening_readiness_summary(
        {
            "terminal": "false",
            "screening_provider_clear": "false",
            "defensible_clear": "false",
            "approval_ready": "true",
            "approval_blocking": "false",
        }
    )

    assert sanitized["screening_terminal"] is False
    assert sanitized["screening_provider_clear"] is False
    assert sanitized["approval_blocking"] is False
    assert sanitized["approval_gate_ready"] is False
    assert sanitized["approval_ready"] is False


def _new_test_db_path(label):
    brand_slug = BRAND.get("slug", "onboarda")
    return os.path.join(
        tempfile.gettempdir(),
        f"{brand_slug}_{label}_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
    )


def _make_test_app_with_db(label):
    db_path_state = _capture_db_path_state()
    db_path = _new_test_db_path(label)
    try:
        os.unlink(db_path)
    except OSError:
        pass
    _sync_test_db_path(db_path)

    from db import get_db, init_db, seed_initial_data
    from server import make_app

    init_db()
    db = get_db()
    seed_initial_data(db)
    db.commit()
    return make_app(), db, db_path, db_path_state


def _close_test_db(db, db_path, db_path_state):
    try:
        db.close()
    finally:
        if db_path:
            try:
                os.unlink(db_path)
            except OSError:
                pass
        _restore_db_path_state(db_path_state)


def _insert_ca4b_memo_adverse_media_fixture(db, app_id):
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    prescreening = {
        "screening_report": {
            "screening_mode": "live",
            "screened_at": now.isoformat(),
            "provider": "complyadvantage",
            "source": "complyadvantage",
            "total_hits": 0,
            "adverse_media_coverage": "none",
            "company_screening": {
                "provider": "complyadvantage",
                "source": "complyadvantage",
                "matched": False,
                "sanctions": {
                    "api_status": "live",
                    "matched": False,
                    "source": "complyadvantage",
                    "provider": "complyadvantage",
                    "results": [],
                },
                "adverse_media": {
                    "api_status": "live",
                    "matched": False,
                    "source": "complyadvantage",
                    "provider": "complyadvantage",
                    "results": [],
                },
            },
        },
        "screening_valid_until": (now + timedelta(days=30)).isoformat(),
    }
    db.execute(
        """
        INSERT OR REPLACE INTO clients
            (id, email, password_hash, company_name, status)
        VALUES ('pr4_ca4b_client', 'pr4_ca4b@example.test', 'test-only',
                'PR4 CA4B Client Ltd', 'active')
        """
    )
    db.execute(
        """
        INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type,
             status, risk_score, risk_level, prescreening_data, updated_at,
             inputs_updated_at)
        VALUES (?, 'PR4-CA4B-MEMO', 'pr4_ca4b_client', 'PR4 CA4B Memo Ltd',
                'Mauritius', 'Technology', 'Company', 'under_review',
                45, 'MEDIUM', ?, '2026-06-01T09:00:00Z',
                '2026-06-01T09:00:00Z')
        """,
        (app_id, json.dumps(prescreening)),
    )
    source_reference = {
        "provider": "complyadvantage",
        "case_identifier": f"case-pr4-ca4b-{suffix}",
        "alert_identifier": f"alert-pr4-ca4b-{suffix}",
        "risk_identifier": f"risk-pr4-ca4b-{suffix}",
        "subject_scope": "entity",
    }
    db.execute(
        """
        INSERT INTO monitoring_alerts
            (application_id, client_name, alert_type, severity, detected_by,
             summary, source_reference, status, provider, case_identifier,
             discovered_via)
        VALUES (?, 'PR4 CA4B Memo Ltd', 'media', 'High', 'complyadvantage',
                'ComplyAdvantage Mesh adverse-media match', ?, 'open',
                'complyadvantage', ?, 'manual')
        """,
        (app_id, json.dumps(source_reference), f"case-pr4-ca4b-{suffix}"),
    )
    alert_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    db.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, case_identifier,
             alert_identifier, risk_identifier, profile_identifier,
             evidence_type, matched_subject_name, relationship_to_client,
             match_category, risk_indicator, match_confidence, source_title,
             source_name, source_url, source_url_available, publication_date,
             snippet, evidence_json, raw_provider_reference, evidence_status,
             evidence_hash, fetched_at)
        VALUES (?, ?, 'complyadvantage', ?, ?, ?, ?, 'adverse_media',
                'PR4 CA4B Memo Ltd', 'entity', 'Adverse Media',
                'Adverse Media', '0.95', 'Provider adverse-media article',
                'Provider News', 'https://provider.example.test/article',
                1, '2026-06-01',
                'Provider adverse-media snippet for memo parity.',
                ?, ?, 'fetched', ?, '2026-06-15T00:00:00Z')
        """,
        (
            alert_id,
            app_id,
            f"case-pr4-ca4b-{suffix}",
            f"alert-pr4-ca4b-{suffix}",
            f"risk-pr4-ca4b-{suffix}",
            f"profile-pr4-ca4b-{suffix}",
            json.dumps({"title": "Provider adverse-media article"}),
            json.dumps({"risk_identifier": f"risk-pr4-ca4b-{suffix}"}),
            f"hash-pr4-ca4b-{suffix}",
        ),
    )
    memo_data = {
        "sections": {"summary": {"content": "Existing stale memo row"}},
        "metadata": {
            "adverse_media_state_summary": {
                "coverage": "none",
                "has_hit": False,
                "terminal": False,
            },
            "canonical_screening_current_summary": {
                "current_risk_count": 0,
                "current_unresolved_risk_count": 0,
                "has_adverse_media_hit": False,
                "adverse_media_coverage": "none",
            },
        },
    }
    db.execute(
        """
        INSERT INTO compliance_memos
            (application_id, version, memo_data, review_status,
             validation_status, quality_score, memo_version, created_at,
             raw_output_hash)
        VALUES (?, 1, ?, 'approved', 'pass', 0.91, '1.0',
                '2026-06-01T10:00:00Z', 'legacy-hash')
        """,
        (app_id, json.dumps(memo_data)),
    )
    db.commit()


@pytest.mark.asyncio
async def test_application_detail_marks_memo_stale_when_ca_adverse_media_truth_changed():
    from server import create_token

    app, db, db_path, db_path_state = _make_test_app_with_db("pr4_ca4b_memo_readiness")
    app_id = "pr4_ca4b_memo_app"
    server = None
    client = None
    try:
        _insert_ca4b_memo_adverse_media_fixture(db, app_id)
        admin_token = create_token("admin001", "admin", "Test Admin", "officer")

        sockets = bind_sockets(0, "127.0.0.1")
        port = sockets[0].getsockname()[1]
        server = HTTPServer(app)
        server.add_sockets(sockets)
        client = AsyncHTTPClient()
        response = await client.fetch(
            HTTPRequest(
                f"http://127.0.0.1:{port}/api/applications/{app_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
            ),
            raise_error=False,
        )
        assert response.code == 200, response.body.decode()
        body = json.loads(response.body.decode() or "{}")

        assert body["memo_is_stale"] is True
        assert body["memo_requires_regeneration"] is True
        assert body["memo_stale_trigger"] == "memo_screening_adverse_media_truth_mismatch"
        snapshot = body["memo_screening_current_snapshot"]
        assert snapshot["has_adverse_media_hit"] is True
        assert snapshot["current_risk_count"] >= 1
        assert snapshot["current_unresolved_risk_count"] == snapshot["current_risk_count"]
        metadata = body["latest_memo_data"]["metadata"]
        assert metadata["is_stale"] is True
        assert metadata["stale_trigger"] == "memo_screening_adverse_media_truth_mismatch"
    finally:
        if client is not None:
            client.close()
        if server is not None:
            server.stop()
        _close_test_db(db, db_path, db_path_state)


class PR4ScreeningMemoReadinessMetadataTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = _new_test_db_path("pr4_memo_readiness")
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
