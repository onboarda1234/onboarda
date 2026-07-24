"""RDI-004 / RDI-005 / RDI-014 / RDI-016 — Enterprise supervisor integrity.

Regression coverage for the four Audit-1 findings on the (feature-flagged-OFF)
Enterprise supervisor subsystem:

  RDI-004 (CRITICAL): AuditLogger persists BEFORE advancing the chain head,
    derives previous_hash from the COMMITTED DB tail, and RAISES (fail-closed)
    on any persist failure instead of swallowing it.
  RDI-005 (CRITICAL): the supervisor pipeline never reports success without a
    committed durable record; persist returns a typed success or raises, the
    handler persists before caching/returning, and the cache is non-authoritative.
  RDI-016 (HIGH): the pipeline re-validates the actor AFTER the long await and
    BEFORE any privileged mutation / audit persistence.
  RDI-014 (HIGH): full-chain audit verification with no artificial 5,000-row cap.

Each test is written to FAIL against the pre-fix code and PASS after.

These fixes HARDEN the supervisor; they do NOT enable it (ENABLE_AI_SUPERVISOR is
untouched). The frozen memo-verdict hash-chain writer (append_verdict_chain_entry,
supervisor_hash_payload, supervisor_entry_hash, AuditEntry.compute_hash) is reused
but never modified.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ─────────────────────────── shared helpers ───────────────────────────

def _clear_chain():
    """Empty supervisor_audit_log so link/genesis assertions are exact."""
    from db import get_db
    db = get_db()
    db.execute("DELETE FROM supervisor_audit_log")
    db.commit()
    db.close()


class _BoomDB:
    """DB double whose execute always fails, tracking rollback/close."""

    is_postgres = False

    def __init__(self):
        self.rolled_back = False
        self.closed = False

    def execute(self, *a, **k):
        raise RuntimeError("synthetic audit store failure")

    def commit(self):
        raise AssertionError("commit must not run after a failed insert")

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


# ═══════════════════════════════════════════════════════════════════════
# RDI-004 — persist-before-advance in AuditLogger
# ═══════════════════════════════════════════════════════════════════════

class TestRDI004AuditPersistBeforeAdvance:
    def test_persist_failure_raises_and_does_not_advance_head(self, temp_db, monkeypatch):
        """A persist failure raises AuditPersistenceError and leaves the
        in-memory head / buffer / counter untouched — the pipeline cannot
        continue past a lost legal record."""
        import supervisor.audit as audit_mod
        from supervisor.audit import AuditLogger, AuditPersistenceError
        from supervisor.schemas import AuditEventType

        al = AuditLogger(db_path=temp_db)
        al._last_hash = "SENTINEL_HEAD"
        before_total = al._total_entries
        before_buf = len(al._buffer)

        boom = _BoomDB()
        monkeypatch.setattr(audit_mod, "_get_db", lambda: boom)

        with pytest.raises(AuditPersistenceError):
            al.log(
                event_type=AuditEventType.AGENT_RUN_STARTED,
                action="x",
                application_id="app-rdi004-fail",
            )

        # Head NOT advanced, nothing buffered/counted, rolled back + closed.
        assert al._last_hash == "SENTINEL_HEAD"
        assert al._total_entries == before_total
        assert len(al._buffer) == before_buf
        assert boom.rolled_back is True
        assert boom.closed is True

    def test_none_db_when_configured_raises_not_swallows(self, temp_db, monkeypatch):
        """A None db while persistence is configured must fail closed, not be
        silently skipped."""
        import supervisor.audit as audit_mod
        from supervisor.audit import AuditLogger, AuditPersistenceError
        from supervisor.schemas import AuditEventType

        al = AuditLogger(db_path=temp_db)
        monkeypatch.setattr(audit_mod, "_get_db", lambda: None)

        with pytest.raises(AuditPersistenceError):
            al.log(
                event_type=AuditEventType.AGENT_RUN_STARTED,
                action="x",
                application_id="app-rdi004-none",
            )

    def test_next_previous_hash_derives_from_committed_tail(self, temp_db):
        """The next entry's previous_hash comes from the COMMITTED DB tail, not
        the mutable in-memory head — even if the in-memory head has diverged."""
        _clear_chain()
        from db import get_db
        from supervisor.audit import AuditLogger
        from supervisor.schemas import AuditEventType

        al = AuditLogger(db_path=temp_db)
        a = al.log(
            event_type=AuditEventType.AGENT_RUN_STARTED,
            action="A",
            application_id="app-tail",
        )
        # Simulate the exact RDI-004 hazard: an in-memory head that no longer
        # matches the committed tail.
        al._last_hash = "STALE_NONEXISTENT_HASH_0000"
        b = al.log(
            event_type=AuditEventType.AGENT_RUN_COMPLETED,
            action="B",
            application_id="app-tail",
        )

        db = get_db()
        row = db.execute(
            "SELECT previous_hash FROM supervisor_audit_log WHERE id = ?",
            (b.audit_id,),
        ).fetchone()
        db.close()
        assert row["previous_hash"] == a.entry_hash
        assert row["previous_hash"] != "STALE_NONEXISTENT_HASH_0000"

    def test_concurrent_appends_stay_linked(self, temp_db):
        """Two independent AuditLoggers appending against the same DB stay a
        single correctly-linked chain (one genesis), because each append chains
        to the committed tail rather than its own divergent in-memory head."""
        _clear_chain()
        from db import get_db
        from supervisor.audit import AuditLogger
        from supervisor.schemas import AuditEventType

        al1 = AuditLogger(db_path=temp_db)
        al2 = AuditLogger(db_path=temp_db)  # independent in-memory head

        al1.log(event_type=AuditEventType.PIPELINE_STARTED, action="p0", application_id="app-cc")
        al2.log(event_type=AuditEventType.AGENT_RUN_STARTED, action="p1", application_id="app-cc")
        al1.log(event_type=AuditEventType.AGENT_RUN_COMPLETED, action="p2", application_id="app-cc")
        al2.log(event_type=AuditEventType.PIPELINE_COMPLETED, action="p3", application_id="app-cc")

        result = al1.verify_chain_integrity(limit=100)
        assert result["verified"] is True, result.get("broken_links")
        assert result["entries_checked"] == 4

        db = get_db()
        genesis = db.execute(
            "SELECT COUNT(*) AS c FROM supervisor_audit_log WHERE previous_hash IS NULL"
        ).fetchone()
        db.close()
        assert genesis["c"] == 1


# ═══════════════════════════════════════════════════════════════════════
# RDI-005 — no supervisor success without persistence (unit level)
# ═══════════════════════════════════════════════════════════════════════

def _fake_pipeline_result(pipeline_id="pl-rdi005-unit", application_id="app-rdi005"):
    return SimpleNamespace(
        pipeline_id=pipeline_id,
        application_id=application_id,
        status="completed",
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:00:05Z",
        agent_outputs={},
        failed_agents=[],
        to_dict=lambda: {"pipeline_id": pipeline_id, "status": "completed"},
    )


class TestRDI005PersistPipelineResult:
    def test_returns_typed_success_only_after_commit(self, temp_db):
        from supervisor.api import (
            PipelinePersistResult,
            load_pipeline_result_by_id,
            persist_pipeline_result,
        )

        result = _fake_pipeline_result()
        outcome = persist_pipeline_result(result, trigger_type="onboarding")
        assert isinstance(outcome, PipelinePersistResult)
        assert outcome.persisted is True
        assert outcome.pipeline_id == "pl-rdi005-unit"

        # The durable row exists and is readable from the durable store.
        loaded = load_pipeline_result_by_id("pl-rdi005-unit")
        assert loaded is not None
        assert loaded["pipeline_id"] == "pl-rdi005-unit"

    def test_raises_when_db_unavailable(self, monkeypatch):
        import supervisor.api as api_mod
        from supervisor.api import PipelinePersistError, persist_pipeline_result

        monkeypatch.setattr(api_mod, "_get_db", lambda: None)
        with pytest.raises(PipelinePersistError):
            persist_pipeline_result(_fake_pipeline_result())

    def test_raises_and_rolls_back_on_write_failure(self, monkeypatch):
        import supervisor.api as api_mod
        from supervisor.api import PipelinePersistError, persist_pipeline_result

        boom = _BoomDB()
        monkeypatch.setattr(api_mod, "_get_db", lambda: boom)
        with pytest.raises(PipelinePersistError):
            persist_pipeline_result(_fake_pipeline_result())
        assert boom.rolled_back is True
        assert boom.closed is True


# ═══════════════════════════════════════════════════════════════════════
# RDI-005 + RDI-016 — handler behaviour (HTTP)
# ═══════════════════════════════════════════════════════════════════════

import tornado.web  # noqa: E402
from tornado.testing import AsyncHTTPTestCase  # noqa: E402

from base_handler import BaseHandler  # noqa: E402
from supervisor import api as supervisor_api  # noqa: E402


class _PipelineResult:
    pipeline_id = "pipeline-rdi-http"

    def to_dict(self):
        return {"pipeline_id": self.pipeline_id, "status": "completed"}


class _SupervisorHandlerBase(AsyncHTTPTestCase):
    """Shared harness for the supervisor pipeline-run handler tests."""

    def get_app(self):
        return tornado.web.Application(
            supervisor_api.get_supervisor_routes(), xsrf_cookies=False
        )

    def setUp(self):
        super().setUp()
        # Ensure the DB (and shared_rate_limits) exists for check_sensitive_rate_limit.
        try:
            from db import get_db, init_db, seed_initial_data
            init_db()
            conn = get_db()
            seed_initial_data(conn)
            conn.commit()
            conn.close()
        except Exception:
            pass

        self.admin_user = {
            "sub": "server-admin-1",
            "role": "admin",
            "name": "Server Admin",
            "type": "officer",
        }
        # Shared state so a run can "revoke" the actor mid-request (RDI-016).
        self.actor_state = {"revoked": False}

        self.decode_patch = patch(
            "base_handler.decode_token",
            side_effect=lambda token: dict(self.admin_user)
            if token == "admin-token" else None,
        )
        self.decode_patch.start()

        def _validate(_handler, token_user):
            if token_user is None:
                return None
            if self.actor_state["revoked"]:
                return None
            return token_user

        self.actor_patch = patch.object(
            BaseHandler, "_validate_current_actor", autospec=True, side_effect=_validate
        )
        self.actor_patch.start()

        self.original_supervisor = supervisor_api._supervisor
        self.original_cache = supervisor_api._pipeline_cache
        supervisor_api._pipeline_cache = {}

        self.persist_patch = patch.object(supervisor_api, "persist_pipeline_result")
        self.persist_mock = self.persist_patch.start()

    def tearDown(self):
        self.persist_patch.stop()
        self.actor_patch.stop()
        self.decode_patch.stop()
        supervisor_api._supervisor = self.original_supervisor
        supervisor_api._pipeline_cache = self.original_cache
        super().tearDown()

    def _bearer(self):
        return {"Authorization": "Bearer admin-token", "Content-Type": "application/json"}

    def _run(self):
        return self.fetch(
            "/api/supervisor/pipeline/run",
            method="POST",
            body=json.dumps({"application_id": "synthetic-app", "trigger_type": "onboarding"}),
            headers=self._bearer(),
            raise_error=False,
        )


class TestRDI005HandlerNoSuccessWithoutPersistence(_SupervisorHandlerBase):
    def setUp(self):
        super().setUp()

        class _FakeSupervisor:
            async def run_pipeline(self, **kwargs):
                return _PipelineResult()

            def get_stats(self):
                return {"status": "ok"}

        supervisor_api._supervisor = _FakeSupervisor()

    def test_persistence_failure_returns_non_2xx_and_cache_not_authoritative(self):
        self.persist_mock.side_effect = supervisor_api.PipelinePersistError(
            "synthetic persist failure"
        )
        response = self._run()

        # No decision-equivalent 2xx body when the durable write failed.
        assert response.code >= 500, response.body.decode()
        # The internal failure detail is not leaked.
        assert "synthetic persist failure" not in response.body.decode()
        # The cache must NOT hold the result — it is only populated AFTER a
        # committed durable row, so it can never mask a missing record.
        assert "pipeline-rdi-http" not in supervisor_api._pipeline_cache

    def test_success_only_after_commit_populates_cache(self):
        # Default persist mock returns a truthy value and does not raise.
        response = self._run()
        assert response.code == 200, response.body.decode()
        self.persist_mock.assert_called_once()
        # Cache populated only after the (successful) persist.
        assert "pipeline-rdi-http" in supervisor_api._pipeline_cache


class TestRDI016PostAwaitRevalidation(_SupervisorHandlerBase):
    def setUp(self):
        super().setUp()
        test_self = self

        class _RevokingSupervisor:
            async def run_pipeline(self, **kwargs):
                # Actor is logged out / demoted / deactivated during the await.
                test_self.actor_state["revoked"] = True
                return _PipelineResult()

            def get_stats(self):
                return {"status": "ok"}

        supervisor_api._supervisor = _RevokingSupervisor()

    def test_midrequest_revocation_aborts_before_persist(self):
        response = self._run()

        # Aborted with the standard auth error, no decision returned.
        assert response.code == 401, response.body.decode()
        # No privileged mutation happened: persistence was never attempted and
        # the result was never cached.
        self.persist_mock.assert_not_called()
        assert "pipeline-rdi-http" not in supervisor_api._pipeline_cache


# ═══════════════════════════════════════════════════════════════════════
# RDI-014 — full-chain audit verification (no 5,000-row cap)
# ═══════════════════════════════════════════════════════════════════════

def _seed_linear_chain(n, app_id="app-full-chain"):
    """Insert n directly-hashed, correctly-linked supervisor_audit_log rows.

    Hashes are computed with the FROZEN supervisor_entry_hash so the seeded
    chain is exactly what the verifier reconstructs — this avoids the O(n^2)
    tail-select of appending one-by-one for large n.
    """
    from db import get_db
    from supervisor.audit import supervisor_entry_hash

    db = get_db()
    ids = []
    hashes = []
    prev = None
    try:
        for i in range(n):
            aid = str(uuid4())
            row = {
                "id": aid,
                "timestamp": f"2026-01-01T00:00:{i % 60:02d}Z",
                "event_type": "supervisor_verdict",
                "severity": "info",
                "pipeline_id": None,
                "application_id": app_id,
                "run_id": None,
                "agent_type": None,
                "actor_type": "system",
                "actor_id": None,
                "actor_name": None,
                "actor_role": None,
                "action": f"a{i}",
                "detail": f"d{i}",
                "data_json": "{}",
            }
            h = supervisor_entry_hash(row, prev)
            db.execute(
                """INSERT INTO supervisor_audit_log
                   (id, timestamp, event_type, severity, pipeline_id, application_id,
                    run_id, agent_type, actor_type, actor_id, actor_name, actor_role,
                    action, detail, data_json, ip_address, session_id,
                    previous_hash, entry_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    aid, row["timestamp"], "supervisor_verdict", "info", None, app_id,
                    None, None, "system", None, None, None,
                    f"a{i}", f"d{i}", "{}", None, None, prev, h,
                ),
            )
            ids.append(aid)
            hashes.append(h)
            prev = h
        db.commit()
    finally:
        db.close()
    return ids, hashes


class TestRDI014FullChainVerification:
    def test_full_chain_verifies_beyond_5000_rows(self, temp_db):
        _clear_chain()
        from supervisor.audit import AuditLogger

        n = 5050  # beyond the old 5,000 API ceiling
        ids, hashes = _seed_linear_chain(n)

        al = AuditLogger(db_path=temp_db)
        result = al.verify_full_chain()

        assert result["verified"] is True, result.get("broken_links")
        assert result["total_entries"] == n
        assert result["entries_verified"] == n
        assert result["coverage_complete"] is True
        assert result["chain_root_hash"] == hashes[0]
        assert result["chain_head_hash"] == hashes[-1]
        assert isinstance(result["verified_at"], str) and result["verified_at"].endswith("Z")

    def test_tamper_beyond_5000_is_detected_by_full_but_missed_by_window(self, temp_db):
        _clear_chain()
        from db import get_db
        from supervisor.audit import AuditLogger

        n = 5050
        ids, hashes = _seed_linear_chain(n)

        # Tamper an OLD row (index 10) — outside the newest-5000 window.
        target_id = ids[10]
        db = get_db()
        db.execute(
            "UPDATE supervisor_audit_log SET detail = 'TAMPERED' WHERE id = ?",
            (target_id,),
        )
        db.commit()
        db.close()

        al = AuditLogger(db_path=temp_db)

        # The windowed mode (old max cap) checks only the newest 5000 → misses it.
        windowed = al.verify_chain_integrity(limit=5000)
        assert windowed["verified"] is True, "sanity: the tamper is outside the window"

        # The full-chain mode covers every row → detects it.
        full = al.verify_full_chain()
        assert full["verified"] is False
        assert any(
            b.get("entry_id") == target_id for b in full["broken_links"]
        ), full["broken_links"]

    def test_full_chain_reports_root_head_count_and_timestamp(self, temp_db):
        _clear_chain()
        from supervisor.audit import AuditLogger

        ids, hashes = _seed_linear_chain(3)
        al = AuditLogger(db_path=temp_db)
        result = al.verify_full_chain()

        assert result["mode"] == "full_chain"
        assert result["verified"] is True
        assert result["total_entries"] == 3
        assert result["entries_verified"] == 3
        assert result["chain_root_hash"] == hashes[0]
        assert result["chain_head_hash"] == hashes[2]
        assert result["verified_at"]

    def test_empty_chain_full_mode_is_not_a_reassuring_success(self, temp_db):
        _clear_chain()
        from supervisor.audit import AuditLogger

        al = AuditLogger(db_path=temp_db)
        result = al.verify_full_chain()
        assert result["verified"] is False
        assert result.get("status") == "no_entries"
        assert result["entries_verified"] == 0

    def test_audit_verify_handler_wires_full_chain_mode(self):
        """The API exposes the uncapped full-chain path under ?full=true while
        keeping the bounded windowed default."""
        src = inspect.getsource(supervisor_api.AuditVerifyHandler.get)
        assert "verify_full_chain" in src
        assert "full" in src
        assert "verify_chain_integrity" in src  # windowed default retained
