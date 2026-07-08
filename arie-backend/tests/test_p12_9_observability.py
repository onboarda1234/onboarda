"""P12-9 (audit DCI-028 / DCI-029) — observability hardening.

DCI-028: structured JSON logging was optional (ARIE_LOG_FORMAT=text would
silently drop every structured field in CloudWatch) and request correlation
ids were caller-remembered, not guaranteed. JSON is now FORCED in
staging/production; a contextvar correlation id is bound in
BaseHandler.prepare (sanitised X-Request-ID or generated), echoed in the
response, auto-injected into every structured log line, persisted on
audit_log rows (new nullable request_id column, Migration v2.49 — the v2.46
hash chain computes from an explicit field list, so it is chain-safe), added
to governance-attempt detail JSON, and bound per job in the verification
worker.

DCI-029: /api/readiness could report ready while document storage was
unavailable or the disk was full. The payload now carries a gating local
disk-capacity check (uploads land on local disk first) and a cached
non-destructive S3 head_bucket reachability probe (gating in deployed
environments, not_configured tolerated only outside them).
"""

import json
import logging
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# DCI-028 — log format forcing
# ---------------------------------------------------------------------------

class TestForcedJsonFormat:

    def test_text_override_honoured_outside_deployed_envs(self, monkeypatch):
        import observability
        import environment
        monkeypatch.setenv("ARIE_LOG_FORMAT", "text")
        monkeypatch.setattr(environment, "get_environment", lambda: "development")
        assert observability._resolve_log_format() == "text"

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_json_forced_in_deployed_envs(self, monkeypatch, env):
        import observability
        import environment
        monkeypatch.setenv("ARIE_LOG_FORMAT", "text")
        monkeypatch.setattr(environment, "get_environment", lambda: env)
        assert observability._resolve_log_format() == "json"

    def test_garbage_format_defaults_to_json(self, monkeypatch):
        import observability
        import environment
        monkeypatch.setenv("ARIE_LOG_FORMAT", "csv")
        monkeypatch.setattr(environment, "get_environment", lambda: "development")
        assert observability._resolve_log_format() == "json"


# ---------------------------------------------------------------------------
# DCI-028 — correlation ids
# ---------------------------------------------------------------------------

class TestRequestCorrelation:

    def test_set_get_clear_roundtrip(self):
        from observability import clear_request_id, get_request_id, set_request_id
        rid = set_request_id("req-abc.123:z")
        assert rid == "req-abc.123:z"
        assert get_request_id() == rid
        clear_request_id()
        assert get_request_id() is None

    def test_generated_when_missing_or_garbage(self):
        from observability import set_request_id
        generated = set_request_id(None)
        assert len(generated) == 32  # uuid4 hex
        injected = set_request_id("evil\nvalue\x1b[31m$(rm -rf /)")
        assert "\n" not in injected and "\x1b" not in injected
        assert "$(" not in injected and " " not in injected

    def test_length_bounded(self):
        from observability import set_request_id
        rid = set_request_id("a" * 5000)
        assert len(rid) == 128

    def test_log_lines_auto_carry_request_id(self):
        from observability import arie_logger, clear_request_id, set_request_id, _log

        captured = []

        class _Capture(logging.Handler):
            def emit(self, record):
                captured.append(getattr(record, "structured_data", {}))

        handler = _Capture()
        arie_logger.addHandler(handler)
        try:
            rid = set_request_id("corr-test-1")
            _log(logging.INFO, "unit_test_event", foo="bar")
            clear_request_id()
            _log(logging.INFO, "unit_test_event_2")
        finally:
            arie_logger.removeHandler(handler)
        assert captured[0]["request_id"] == rid
        assert captured[0]["foo"] == "bar"
        assert "request_id" not in captured[1]  # cleared → filtered out as None

    def test_context_isolation_across_threads(self):
        """Contextvars: another thread must not see this thread's id."""
        from observability import get_request_id, set_request_id, clear_request_id
        set_request_id("main-thread-id")
        seen = {}

        def other():
            seen["other"] = get_request_id()

        t = threading.Thread(target=other)
        t.start()
        t.join(timeout=5)
        clear_request_id()
        assert seen["other"] is None


# ---------------------------------------------------------------------------
# DCI-028 — wiring (source guards + schema)
# ---------------------------------------------------------------------------

class TestWiring:

    def test_base_handler_wiring(self):
        with open(os.path.join(BACKEND, "base_handler.py"), encoding="utf-8") as fh:
            src = fh.read()
        prepare = src.split("def prepare(self):")[1].split("def ")[0]
        assert "set_request_id" in prepare, "prepare must bind the correlation id"
        assert 'X-Request-ID' in prepare
        on_finish = src.split("def on_finish(self):")[1].split("\n    def ")[0]
        assert "clear_request_id()" in on_finish
        assert "finally:" in on_finish, "id must clear even if logging raises"
        # audit rows persist the id
        assert "after_state, request_id) VALUES" in src.replace("\n", " ").replace("  ", " ") or \
            "request_id) VALUES (?,?,?,?,?,?,?,?,?,?)" in src

    def test_worker_wiring(self):
        with open(os.path.join(BACKEND, "verification_worker.py"), encoding="utf-8") as fh:
            src = fh.read()
        assert 'set_request_id(f"job-screening-' in src
        assert 'set_request_id(f"job-verification-' in src
        assert "clear_request_id()" in src

    def test_audit_log_has_request_id_column_fresh(self, temp_db):
        import sqlite3
        conn = sqlite3.connect(temp_db)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
            assert "request_id" in cols
        finally:
            conn.close()

    def test_v249_migration_block_exists(self):
        with open(os.path.join(BACKEND, "db.py"), encoding="utf-8") as fh:
            src = fh.read()
        assert "Migration v2.49" in src
        assert "ALTER TABLE audit_log ADD COLUMN request_id TEXT" in src

    def test_governance_detail_carries_request_id(self):
        with open(os.path.join(BACKEND, "base_handler.py"), encoding="utf-8") as fh:
            src = fh.read()
        detail_block = src.split('"event": "governance_attempt"')[1].split("}")[0]
        assert '"request_id": get_request_id()' in detail_block


# ---------------------------------------------------------------------------
# DCI-029 — S3 reachability probe
# ---------------------------------------------------------------------------

class _FakeBoto:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def head_bucket(self, Bucket):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated S3 outage")


class _FakeS3Client:
    def __init__(self, fail=False):
        self.bucket_name = "probe-bucket"
        self.s3_client = _FakeBoto(fail=fail)


class TestS3ReadinessProbe:

    def _fresh_cache(self, server_mod):
        server_mod._S3_READINESS_CACHE["at"] = 0.0
        server_mod._S3_READINESS_CACHE["result"] = None

    def test_not_configured_when_s3_unavailable(self, monkeypatch):
        import server as server_mod
        self._fresh_cache(server_mod)
        monkeypatch.setattr(server_mod, "HAS_S3", False)
        status = server_mod._s3_readiness_status()
        assert status["status"] == "not_configured"

    def test_ok_and_cached(self, monkeypatch):
        import server as server_mod
        self._fresh_cache(server_mod)
        fake = _FakeS3Client()
        monkeypatch.setattr(server_mod, "HAS_S3", True)
        monkeypatch.setattr(server_mod, "get_s3_client", lambda: fake)
        first = server_mod._s3_readiness_status()
        second = server_mod._s3_readiness_status()
        assert first["status"] == "ok" and first["bucket"] == "probe-bucket"
        assert second["status"] == "ok" and second.get("cached") is True
        assert fake.s3_client.calls == 1, "second call must hit the cache"

    def test_force_bypasses_cache(self, monkeypatch):
        import server as server_mod
        self._fresh_cache(server_mod)
        fake = _FakeS3Client()
        monkeypatch.setattr(server_mod, "HAS_S3", True)
        monkeypatch.setattr(server_mod, "get_s3_client", lambda: fake)
        server_mod._s3_readiness_status()
        server_mod._s3_readiness_status(force=True)
        assert fake.s3_client.calls == 2

    def test_unreachable_on_probe_failure(self, monkeypatch):
        import server as server_mod
        self._fresh_cache(server_mod)
        monkeypatch.setattr(server_mod, "HAS_S3", True)
        monkeypatch.setattr(server_mod, "get_s3_client", lambda: _FakeS3Client(fail=True))
        status = server_mod._s3_readiness_status()
        assert status["status"] == "unreachable"
        assert "simulated S3 outage" in status["detail"]


# ---------------------------------------------------------------------------
# DCI-029 — readiness payload gating
# ---------------------------------------------------------------------------

class _Usage:
    def __init__(self, free_mb, total_mb=100_000):
        self.free = free_mb * 1024 * 1024
        self.total = total_mb * 1024 * 1024
        self.used = self.total - self.free


class TestReadinessGating:

    def test_disk_exhaustion_fails_readiness(self, temp_db, monkeypatch):
        import shutil
        import server as server_mod
        server_mod._S3_READINESS_CACHE["at"] = 0.0
        server_mod._S3_READINESS_CACHE["result"] = None
        monkeypatch.setattr(shutil, "disk_usage", lambda path: _Usage(free_mb=10))
        ready, payload = server_mod._readiness_status_payload()
        disk = payload["checks"]["disk"]
        assert disk["status"] == "failed"
        assert disk["free_mb"] == 10
        assert ready is False

    def test_healthy_disk_reports_ok(self, temp_db, monkeypatch):
        import shutil
        import server as server_mod
        server_mod._S3_READINESS_CACHE["at"] = 0.0
        server_mod._S3_READINESS_CACHE["result"] = None
        monkeypatch.setattr(shutil, "disk_usage", lambda path: _Usage(free_mb=50_000))
        ready, payload = server_mod._readiness_status_payload()
        assert payload["checks"]["disk"]["status"] == "ok"

    def test_disk_threshold_env_override(self, temp_db, monkeypatch):
        import shutil
        import server as server_mod
        server_mod._S3_READINESS_CACHE["at"] = 0.0
        server_mod._S3_READINESS_CACHE["result"] = None
        monkeypatch.setenv("READINESS_MIN_FREE_DISK_MB", "60000")
        monkeypatch.setattr(shutil, "disk_usage", lambda path: _Usage(free_mb=50_000))
        ready, payload = server_mod._readiness_status_payload()
        assert payload["checks"]["disk"]["status"] == "failed"
        assert ready is False

    def test_s3_unreachable_gates_in_staging_only(self, temp_db, monkeypatch):
        import shutil
        import server as server_mod
        monkeypatch.setattr(shutil, "disk_usage", lambda path: _Usage(free_mb=50_000))
        monkeypatch.setattr(server_mod, "HAS_S3", True)
        monkeypatch.setattr(server_mod, "get_s3_client", lambda: _FakeS3Client(fail=True))

        # development: reported but non-gating
        server_mod._S3_READINESS_CACHE["at"] = 0.0
        server_mod._S3_READINESS_CACHE["result"] = None
        ready_dev, payload_dev = server_mod._readiness_status_payload()
        assert payload_dev["checks"]["document_storage_s3"]["status"] == "unreachable"

        # staging: gating
        server_mod._S3_READINESS_CACHE["at"] = 0.0
        server_mod._S3_READINESS_CACHE["result"] = None
        monkeypatch.setattr(server_mod, "ENVIRONMENT", "staging")
        ready_stg, payload_stg = server_mod._readiness_status_payload()
        assert payload_stg["checks"]["document_storage_s3"]["status"] == "unreachable"
        assert ready_stg is False

    def test_s3_not_configured_gates_in_staging(self, temp_db, monkeypatch):
        import shutil
        import server as server_mod
        server_mod._S3_READINESS_CACHE["at"] = 0.0
        server_mod._S3_READINESS_CACHE["result"] = None
        monkeypatch.setattr(shutil, "disk_usage", lambda path: _Usage(free_mb=50_000))
        monkeypatch.setattr(server_mod, "HAS_S3", False)
        monkeypatch.setattr(server_mod, "ENVIRONMENT", "staging")
        ready, payload = server_mod._readiness_status_payload()
        assert payload["checks"]["document_storage_s3"]["status"] == "not_configured"
        assert ready is False
