"""
Tests for PR-D -- Lightweight periodic review memo artifact.

Covers:

* Migration 013 creates ``periodic_review_memos`` + indexes, additive
  only (no touch to ``compliance_memos``), and is idempotent.
* ``generate_periodic_review_memo`` writes a row with
  ``memo_context`` = ``{"kind":"periodic_review"}``, ``status='generated'``,
  ``generated_by='system:periodic-review-memo-generator'``, ``version=1``,
  and a ``memo_data`` payload containing all 9 top-level sections.
* Auto-generation is wired into ``PeriodicReviewCompleteHandler`` --
  completing a review produces a memo row as a side effect, without
  rolling back the outcome commit.
* Read endpoint ``GET /api/periodic-reviews/:id/memo``:
    - 200 + generated payload when memo exists,
    - 200 + ``status='generation_failed'`` when failure row exists,
    - 404 when no memo row,
    - 401 when unauthenticated.
* Isolation:
    - no row is ever written to ``compliance_memos``,
    - ``edd_memo_integration.resolve_active_memo_context`` does not
      consult ``periodic_review_memos``.
* Failure handling: when the generator raises mid-flow, a
  ``status='generation_failed'`` row is persisted and the outcome
  commit is NOT rolled back.
* Determinism: zero calls to Anthropic / OpenAI during a full
  generation flow.
"""
import json
import os
import sys
import tempfile
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tornado.testing import AsyncHTTPTestCase  # noqa: E402


# ─────────────────────────────────────────────────────────────────
# Base fixture mirroring test_periodic_review_handlers.py
# ─────────────────────────────────────────────────────────────────
class _PRDBase(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_prd_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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

        # Seed an application.
        self._app_id = "test-app-prd"
        try:
            conn.execute(
                "INSERT INTO applications "
                "(id, ref, company_name, country, sector, "
                " ownership_structure, entity_type, risk_level, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self._app_id, "APP-PRD", "PRD Test Co",
                 "Mauritius", "Fintech", "single-tier", "Private Limited",
                 "MEDIUM", "approved"),
            )
        except Exception:
            conn.execute(
                "INSERT OR IGNORE INTO applications (id, ref, company_name) "
                "VALUES (?, ?, ?)",
                (self._app_id, "APP-PRD", "PRD Test Co"),
            )
        conn.commit()
        conn.close()

        from server import make_app
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

    def tearDown(self):
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            os.unlink(self._db_path)
        except Exception:
            pass
        try:
            import config as config_module
            import db as db_module
            config_module.DB_PATH = self._orig_config_db_path
            db_module.DB_PATH = self._orig_db_db_path
        except Exception:
            pass
        super().tearDown()

    # ── helpers ──
    def _create_review(self, *, status="in_progress", risk_level="MEDIUM",
                       trigger_source=None, linked_alert_id=None,
                       linked_edd_id=None, required_items=None,
                       review_reason=None):
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status, "
            " trigger_source, linked_monitoring_alert_id, "
            " linked_edd_case_id, required_items, review_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self._app_id, "PRD Test Co", risk_level, status,
             trigger_source, linked_alert_id, linked_edd_id,
             json.dumps(required_items) if required_items is not None else None,
             review_reason),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _create_alert(self, *, alert_type="adverse_media",
                      severity="medium", status="open",
                      summary="Test adverse media hit"):
        self._conn.execute(
            "INSERT INTO monitoring_alerts "
            "(application_id, client_name, alert_type, severity, "
            " status, summary) VALUES (?, ?, ?, ?, ?, ?)",
            (self._app_id, "PRD Test Co", alert_type, severity,
             status, summary),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM monitoring_alerts ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _create_edd(self, *, stage="triggered"):
        self._conn.execute(
            "INSERT INTO edd_cases "
            "(application_id, client_name, stage) VALUES (?, ?, ?)",
            (self._app_id, "PRD Test Co", stage),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM edd_cases ORDER BY id DESC LIMIT 1"
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

    def _get(self, path, token=None, anonymous=False):
        headers = {"Content-Type": "application/json"}
        if not anonymous:
            headers["Authorization"] = f"Bearer {token or self.admin_token}"
        return self.fetch(path, method="GET", headers=headers)


# ─────────────────────────────────────────────────────────────────
# Migration 013
# ─────────────────────────────────────────────────────────────────
class TestMigration013(_PRDBase):
    def test_table_and_indexes_exist(self):
        row = self._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='periodic_review_memos'"
        ).fetchone()
        self.assertIsNotNone(row)

        idx_rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='periodic_review_memos'"
        ).fetchall()
        names = {r["name"] for r in idx_rows}
        self.assertIn("idx_prm_review", names)
        self.assertIn("idx_prm_app", names)

    def test_columns_present(self):
        rows = self._conn.execute(
            "PRAGMA table_info(periodic_review_memos)"
        ).fetchall()
        cols = {r["name"] for r in rows}
        expected = {
            "id", "periodic_review_id", "application_id", "version",
            "memo_data", "memo_context", "generated_at", "generated_by",
            "status",
        }
        self.assertTrue(expected.issubset(cols),
                        f"missing cols: {expected - cols}")

    def test_compliance_memos_untouched(self):
        # Still exists with its original shape; no periodic_review_id col.
        rows = self._conn.execute(
            "PRAGMA table_info(compliance_memos)"
        ).fetchall()
        cols = {r["name"] for r in rows}
        self.assertNotIn("periodic_review_id", cols)

    def test_migration_idempotent_via_runner(self):
        # Running the runner again should be a no-op (0 applied).
        from migrations.runner import run_all_migrations_with_connection
        applied = run_all_migrations_with_connection(self._conn)
        self.assertEqual(applied, 0)


# ─────────────────────────────────────────────────────────────────
# Direct generator tests
# ─────────────────────────────────────────────────────────────────
class TestGenerator(_PRDBase):
    EXPECTED_SECTIONS = {
        "header", "review_purpose", "current_profile_snapshot",
        "monitoring_screening_summary", "required_items", "edd_summary",
        "risk_reassessment", "conclusion", "artifact_references",
    }

    def _complete_review(self, rid, outcome="no_change",
                         reason="baseline checks pass"):
        # Mark the review completed directly so the generator can be
        # exercised in isolation (without the handler).
        self._conn.execute(
            "UPDATE periodic_reviews SET status='completed', "
            "outcome=?, outcome_reason=?, "
            "outcome_recorded_at=datetime('now'), "
            "completed_at=datetime('now') WHERE id = ?",
            (outcome, reason, rid),
        )
        self._conn.commit()

    def test_build_memo_data_has_all_9_sections(self):
        rid = self._create_review()
        self._complete_review(rid)
        import periodic_review_memo as prm
        data = prm.build_memo_data(self._conn, rid)
        self.assertEqual(set(data.keys()), self.EXPECTED_SECTIONS)

    def test_generate_writes_row_with_expected_invariants(self):
        alert_id = self._create_alert()
        rid = self._create_review(
            trigger_source="monitoring_alert",
            linked_alert_id=alert_id,
            required_items=[
                {"id": 1, "label": "Updated CoI", "rationale": "doc stale"},
            ],
        )
        self._complete_review(rid, outcome="enhanced_monitoring")

        import periodic_review_memo as prm
        result = prm.generate_periodic_review_memo(self._conn, rid)
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["status"], prm.STATUS_GENERATED)

        row = self._conn.execute(
            "SELECT * FROM periodic_review_memos "
            "WHERE periodic_review_id = ?", (rid,)
        ).fetchone()
        self.assertEqual(row["version"], 1)
        self.assertEqual(row["status"], "generated")
        self.assertEqual(row["generated_by"],
                         "system:periodic-review-memo-generator")
        self.assertEqual(row["application_id"], self._app_id)

        ctx = json.loads(row["memo_context"])
        self.assertEqual(ctx, {"kind": "periodic_review"})

        data = json.loads(row["memo_data"])
        self.assertEqual(set(data.keys()), self.EXPECTED_SECTIONS)
        self.assertEqual(
            data["header"]["application_id"], self._app_id
        )
        self.assertEqual(
            data["risk_reassessment"]["outcome"], "enhanced_monitoring"
        )
        # Alert linkage flowed through.
        self.assertEqual(
            data["monitoring_screening_summary"]["linked_alerts"][0]["id"],
            alert_id,
        )
        # Required-items normalised.
        self.assertEqual(data["required_items"][0]["label"], "Updated CoI")
        # Artifact references do not link to onboarding memo.
        self.assertIsNone(data["artifact_references"]["onboarding_memo_reference"])

    def test_compliance_memos_never_written(self):
        rid = self._create_review()
        self._complete_review(rid)
        import periodic_review_memo as prm
        prm.generate_periodic_review_memo(self._conn, rid)
        count = self._conn.execute(
            "SELECT COUNT(*) AS c FROM compliance_memos"
        ).fetchone()["c"]
        self.assertEqual(count, 0)

    def test_edd_linkage_populates_edd_summary(self):
        edd_id = self._create_edd()
        rid = self._create_review(linked_edd_id=edd_id)
        self._complete_review(rid, outcome="edd_required")
        import periodic_review_memo as prm
        prm.generate_periodic_review_memo(self._conn, rid)
        row = self._conn.execute(
            "SELECT memo_data FROM periodic_review_memos "
            "WHERE periodic_review_id = ?", (rid,)
        ).fetchone()
        data = json.loads(row["memo_data"])
        self.assertTrue(data["edd_summary"]["triggered"])
        self.assertEqual(data["edd_summary"]["linked_edd_id"], edd_id)

    def test_generation_failure_persists_failure_row(self):
        rid = self._create_review()
        self._complete_review(rid)
        import periodic_review_memo as prm
        with mock.patch.object(prm, "build_memo_data",
                               side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                prm.generate_periodic_review_memo(self._conn, rid)
        row = self._conn.execute(
            "SELECT status, memo_data FROM periodic_review_memos "
            "WHERE periodic_review_id = ?", (rid,)
        ).fetchone()
        self.assertEqual(row["status"], "generation_failed")
        # Outcome still committed.
        self.assertEqual(
            self._conn.execute(
                "SELECT status FROM periodic_reviews WHERE id = ?",
                (rid,),
            ).fetchone()["status"],
            "completed",
        )


# ─────────────────────────────────────────────────────────────────
# Auto-generation hook inside PeriodicReviewCompleteHandler
# ─────────────────────────────────────────────────────────────────
class TestCompleteHandlerHook(_PRDBase):
    def test_outcome_recorded_generates_memo_row(self):
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status) "
            "VALUES (?, ?, ?, ?)",
            (self._app_id, "PRD Test Co", "MEDIUM", "in_progress"),
        )
        self._conn.commit()
        rid = self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

        resp = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome": "no_change", "outcome_reason": "baseline pass"},
        )
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body.decode())
        self.assertIn("memo", body)
        self.assertEqual(body["memo"]["status"], "generated")
        self.assertEqual(body["memo"]["version"], 1)

        row = self._conn.execute(
            "SELECT version, status FROM periodic_review_memos "
            "WHERE periodic_review_id = ?", (rid,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["version"], 1)
        self.assertEqual(row["status"], "generated")

    def test_generator_failure_does_not_rollback_outcome(self):
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status) "
            "VALUES (?, ?, ?, ?)",
            (self._app_id, "PRD Test Co", "MEDIUM", "in_progress"),
        )
        self._conn.commit()
        rid = self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

        import periodic_review_memo as prm
        with mock.patch.object(prm, "build_memo_data",
                               side_effect=RuntimeError("injected")):
            resp = self._post(
                f"/api/monitoring/reviews/{rid}/complete",
                {"outcome": "no_change", "outcome_reason": "baseline pass"},
            )
        self.assertEqual(resp.code, 200)
        # Outcome is committed.
        self.assertEqual(
            self._conn.execute(
                "SELECT status, outcome FROM periodic_reviews WHERE id = ?",
                (rid,),
            ).fetchone()["status"],
            "completed",
        )
        # Failure-indicator row persisted.
        row = self._conn.execute(
            "SELECT status FROM periodic_review_memos "
            "WHERE periodic_review_id = ?", (rid,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "generation_failed")


# ─────────────────────────────────────────────────────────────────
# Read endpoint
# ─────────────────────────────────────────────────────────────────
class TestMemoReadEndpoint(_PRDBase):
    def test_404_when_no_memo(self):
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status) "
            "VALUES (?, ?, ?, ?)",
            (self._app_id, "PRD Test Co", "MEDIUM", "in_progress"),
        )
        self._conn.commit()
        rid = self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

        resp = self._get(f"/api/periodic-reviews/{rid}/memo")
        self.assertEqual(resp.code, 404)

    def test_200_with_generated_status(self):
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status) "
            "VALUES (?, ?, ?, ?)",
            (self._app_id, "PRD Test Co", "MEDIUM", "in_progress"),
        )
        self._conn.commit()
        rid = self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        complete = self._post(
            f"/api/monitoring/reviews/{rid}/complete",
            {"outcome": "no_change", "outcome_reason": "ok"},
        )
        self.assertEqual(complete.code, 200)

        resp = self._get(f"/api/periodic-reviews/{rid}/memo")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body.decode())
        self.assertEqual(body["status"], "generated")
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["generated_by"],
                         "system:periodic-review-memo-generator")
        self.assertEqual(body["memo_context"], {"kind": "periodic_review"})
        self.assertIn("header", body["memo_data"])

    def test_200_with_generation_failed_not_404(self):
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status, "
            " outcome, outcome_reason, "
            " outcome_recorded_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (self._app_id, "PRD Test Co", "MEDIUM", "completed",
             "no_change", "ok"),
        )
        self._conn.commit()
        rid = self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        # Manually insert a generation_failed row (simulates the
        # handler's failure path).
        self._conn.execute(
            "INSERT INTO periodic_review_memos "
            "(periodic_review_id, application_id, version, memo_data, "
            " memo_context, generated_by, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rid, self._app_id, 1, json.dumps({"error": "boom"}),
             json.dumps({"kind": "periodic_review"}),
             "system:periodic-review-memo-generator",
             "generation_failed"),
        )
        self._conn.commit()

        resp = self._get(f"/api/periodic-reviews/{rid}/memo")
        self.assertEqual(resp.code, 200)
        body = json.loads(resp.body.decode())
        self.assertEqual(body["status"], "generation_failed")

    def test_unauthenticated_is_rejected(self):
        resp = self._get("/api/periodic-reviews/1/memo", anonymous=True)
        self.assertIn(resp.code, (401, 403))

    def test_non_numeric_id_rejected_cleanly(self):
        resp = self._get("/api/periodic-reviews/abc/memo")
        self.assertEqual(resp.code, 400)


# ─────────────────────────────────────────────────────────────────
# Isolation: edd_memo_integration must not consult this table
# ─────────────────────────────────────────────────────────────────
class TestEDDIntegrationIsolation(_PRDBase):
    def test_resolve_active_memo_context_does_not_read_periodic_review_memos(self):
        # Seed a periodic_review_memos row.
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status, "
            " outcome, outcome_reason, "
            " outcome_recorded_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (self._app_id, "PRD Test Co", "MEDIUM", "completed",
             "no_change", "ok"),
        )
        self._conn.commit()
        rid = self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        import periodic_review_memo as prm
        prm.generate_periodic_review_memo(self._conn, rid)

        # Seed an edd_case on the same application.
        self._conn.execute(
            "INSERT INTO edd_cases "
            "(application_id, client_name, stage) VALUES (?, ?, ?)",
            (self._app_id, "PRD Test Co", "triggered"),
        )
        self._conn.commit()
        edd_id = self._conn.execute(
            "SELECT id FROM edd_cases ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

        import edd_memo_integration as emi

        # Spy every call through the DB connection. If the resolver
        # touches periodic_review_memos, the SQL text will contain it.
        original_execute = self._conn.execute
        touched = {"periodic_review_memos": False}

        def _spy_execute(sql, *a, **kw):
            if "periodic_review_memos" in (sql or "").lower():
                touched["periodic_review_memos"] = True
            return original_execute(sql, *a, **kw)

        with mock.patch.object(self._conn, "execute", _spy_execute):
            try:
                emi.resolve_active_memo_context(self._conn, edd_id)
            except Exception:
                # Resolver may raise for unrelated reasons on this
                # minimal fixture; we only care that it did not query
                # periodic_review_memos.
                pass
        self.assertFalse(
            touched["periodic_review_memos"],
            "edd_memo_integration.resolve_active_memo_context must not "
            "consult periodic_review_memos (PR-D isolation contract)."
        )


# ─────────────────────────────────────────────────────────────────
# Determinism: zero AI calls during a full generation flow
# ─────────────────────────────────────────────────────────────────
class TestDeterminism(_PRDBase):
    def test_no_ai_client_calls_during_generation(self):
        rid = self._create_review()
        self._conn.execute(
            "UPDATE periodic_reviews SET status='completed', "
            "outcome=?, outcome_reason=?, "
            "outcome_recorded_at=datetime('now'), "
            "completed_at=datetime('now') WHERE id = ?",
            ("no_change", "ok", rid),
        )
        self._conn.commit()

        # Import AI clients under aliases so we can spy on them
        # regardless of whether the generator imports them directly.
        import importlib
        patched = []
        for mod_name in ("claude_client", "anthropic", "openai"):
            try:
                mod = importlib.import_module(mod_name)
            except Exception:
                continue
            # Block any callable attribute that would issue an API call.
            for attr in ("Anthropic", "Client", "OpenAI", "generate",
                         "complete", "chat"):
                if hasattr(mod, attr):
                    patcher = mock.patch.object(
                        mod, attr,
                        side_effect=AssertionError(
                            f"AI call {mod_name}.{attr} during PR-D memo "
                            "generation is forbidden"),
                    )
                    patcher.start()
                    patched.append(patcher)
        try:
            import periodic_review_memo as prm
            prm.generate_periodic_review_memo(self._conn, rid)
        finally:
            for p in patched:
                p.stop()
