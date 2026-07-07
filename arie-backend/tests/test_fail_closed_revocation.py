"""P11-1 / BSA-001 + BSA-014 — fail-closed session revocation.

BSA-001: token/session revocation was fail-open under persistent-store
failure: `is_revoked()` returned "not revoked" when its DB lookup failed, and
logout / password-reset / password-change returned success even when the
durable revocation write failed (leaving the killed token honoured by other
workers / after restart).

Now:
- `_db_lookup_active` raises `RevocationCheckUnavailable` on DB error and
  `decode_token` REJECTS the token (an unverifiable session is never valid).
- The password flows write the revocation row in the SAME transaction as the
  password update: both commit or both roll back (503, no false success).
- Logout returns 503 and does not claim "logged_out" when the durable write
  fails (in-memory revocation still protects the serving worker).

BSA-014: SupervisorRunHandler re-validates the actor (fresh revocation check +
actor row active + role) after its long await, before persisting results —
via BaseHandler.revalidate_actor_post_await.
"""
import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from auth import RevocationCheckUnavailable, create_token, decode_token


def _fresh_list():
    from security_hardening import TokenRevocationList
    trl = TokenRevocationList()
    trl._db_loaded = True  # isolate from the lazy bulk load
    return trl


class _BoomDB:
    """DB double whose execute always fails (simulates store outage)."""

    def execute(self, *a, **k):
        raise RuntimeError("boom: revocation store unreachable")

    def commit(self):
        pass

    def close(self):
        pass


# ══════════════════════════════════════════════════════════
# Read path — is_revoked / decode_token fail closed
# ══════════════════════════════════════════════════════════

class TestFailClosedReadPath:
    def test_lookup_db_error_raises_unavailable(self, monkeypatch):
        import db as db_module
        trl = _fresh_list()
        monkeypatch.setattr(db_module, "get_db", lambda: _BoomDB())
        with pytest.raises(RevocationCheckUnavailable):
            trl.is_revoked("some-unknown-jti")

    def test_in_memory_revocation_needs_no_db(self, monkeypatch):
        """A revoked-in-memory token stays revoked even with the store down."""
        import db as db_module
        trl = _fresh_list()
        trl._revoked["dead-jti"] = time.time() + 3600
        monkeypatch.setattr(db_module, "get_db", lambda: _BoomDB())
        assert trl.is_revoked("dead-jti") is True

    def test_decode_token_rejects_when_store_unavailable(self, db, monkeypatch):
        """decode_token returns None (401) when revocation can't be verified."""
        import security_hardening as sh
        token = create_token("user-fcr-1", "co", "FCR Test", token_type="officer")
        assert decode_token(token) is not None  # sanity: valid with healthy store

        def _unavailable(jti):
            raise RevocationCheckUnavailable("store down")

        monkeypatch.setattr(
            sh.token_revocation_list, "_db_lookup_active", _unavailable)
        # ensure the per-JTI check misses memory and must consult the store
        sh.token_revocation_list._revoked.pop("user:user-fcr-1", None)
        assert decode_token(token) is None

    def test_healthy_store_still_admits_valid_token(self, db):
        token = create_token("user-fcr-2", "co", "FCR Test2", token_type="officer")
        decoded = decode_token(token)
        assert decoded is not None and decoded["sub"] == "user-fcr-2"


# ══════════════════════════════════════════════════════════
# Write path — transactional revoke (db= mode)
# ══════════════════════════════════════════════════════════

class TestTransactionalRevoke:
    def test_revoke_on_caller_connection_commits_with_caller(self, db):
        trl = _fresh_list()
        jti = f"txn-{uuid.uuid4().hex[:8]}"
        assert trl.revoke(jti, time.time() + 3600, db=db) is True
        # not yet visible to a raw second connection until commit? SQLite test
        # fixture shares the connection — assert the row is there post-commit.
        db.commit()
        row = db.execute(
            "SELECT jti FROM revoked_tokens WHERE jti = ?", (jti,)).fetchone()
        assert row is not None

    def test_revoke_on_caller_connection_rolls_back_with_caller(self, db):
        trl = _fresh_list()
        jti = f"txn-rb-{uuid.uuid4().hex[:8]}"
        assert trl.revoke(jti, time.time() + 3600, db=db) is True
        db.rollback()
        row = db.execute(
            "SELECT jti FROM revoked_tokens WHERE jti = ?", (jti,)).fetchone()
        assert row is None
        # fail-closed direction: memory still holds it (over-revocation is safe)
        assert jti in trl._revoked

    def test_revoke_with_db_propagates_write_failure(self):
        trl = _fresh_list()
        with pytest.raises(RuntimeError):
            trl.revoke("boom-jti", time.time() + 3600, db=_BoomDB())

    def test_revoke_own_connection_returns_false_on_failure(self, monkeypatch):
        import db as db_module
        trl = _fresh_list()
        monkeypatch.setattr(db_module, "get_db", lambda: _BoomDB())
        assert trl.revoke("own-conn-jti", time.time() + 3600) is False
        # in-memory entry still set — this worker rejects the token regardless
        assert "own-conn-jti" in trl._revoked


# ══════════════════════════════════════════════════════════
# Handler write paths — source-level fail-closed guards
# ══════════════════════════════════════════════════════════

_SERVER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")


def _handler_body(src, cls):
    start = src.index(f"class {cls}(")
    rest = src[start:]
    import re
    nxt = re.search(r"\nclass [A-Za-z_]", rest[10:])
    return rest[: nxt.start() + 10] if nxt else rest


class TestHandlerFailClosedGuards:
    """Static locks: each credential-mutating handler keeps its revocation in
    the SAME transaction as the credential change, rolls back on failure, and
    returns 503 instead of a false success. (Behavioural proof lives in the
    unit tests above + the transactional-revoke suite; these guards stop the
    fail-open pattern from silently returning.)"""

    @classmethod
    def setup_class(cls):
        with open(_SERVER, encoding="utf-8") as fh:
            cls.src = fh.read()

    def test_reset_password_atomic_and_fail_closed(self):
        body = _handler_body(self.src, "ResetPasswordHandler")
        assert "_revoke_all_client_sessions(db, client[\"id\"])" in body
        # revocation happens BEFORE the commit in the same try-block
        assert body.index("_revoke_all_client_sessions") < body.index("db.commit()")
        assert "db.rollback()" in body
        assert "503" in body

    def test_change_password_atomic_and_fail_closed(self):
        body = _handler_body(self.src, "ClientChangePasswordHandler")
        assert body.index("_revoke_all_client_sessions") < body.index("db.commit()")
        assert "token_revocation_list.revoke(jti, exp, db=db)" in body
        assert "db.rollback()" in body
        assert "503" in body
        # the old fail-open escape hatch is gone
        assert "did not persist durably" not in body

    def test_admin_resets_atomic_and_fail_closed(self):
        for cls_name in ("AdminResetPasswordHandler", "AdminOfficerPasswordResetHandler"):
            body = _handler_body(self.src, cls_name)
            assert body.index("_revoke_all_client_sessions") < body.index("db.commit()"), cls_name
            assert "db.rollback()" in body, cls_name
            assert "503" in body, cls_name

    def test_logout_fail_closed(self):
        body = _handler_body(self.src, "LogoutHandler")
        assert "failed_revocations" in body
        assert "503" in body
        # success claim only on the fully-revoked path
        assert body.index("failed_revocations") < body.index('"logged_out"')

    def test_revoke_all_uses_caller_transaction(self):
        import re
        m = re.search(
            r"def _revoke_all_client_sessions\(db, user_id\):.*?return token_revocation_list\.revoke\(user_jti, expires_at, db=db\)",
            self.src, re.DOTALL)
        assert m, "_revoke_all_client_sessions must revoke on the caller's connection"


# ══════════════════════════════════════════════════════════
# BSA-014 — post-await actor re-validation
# ══════════════════════════════════════════════════════════

class TestPostAwaitRevalidation:
    @classmethod
    def setup_class(cls):
        with open(_SERVER, encoding="utf-8") as fh:
            cls.src = fh.read()

    def test_supervisor_run_revalidates_before_persist(self):
        body = _handler_body(self.src, "SupervisorRunHandler")
        assert "revalidate_actor_post_await" in body
        # re-validation sits AFTER the awaited pipeline and BEFORE persistence
        assert body.index("await asyncio.wait_for") \
            < body.index("revalidate_actor_post_await") \
            < body.index("persist_pipeline_result")

    def test_base_handler_helper_resets_auth_cache(self):
        base = os.path.join(os.path.dirname(_SERVER), "base_handler.py")
        with open(base, encoding="utf-8") as fh:
            src = fh.read()
        import re
        m = re.search(
            r"def revalidate_actor_post_await\(self, roles=None\):.*?self\._auth_user_checked = False.*?self\._auth_user = None.*?return self\.require_auth\(roles=roles\)",
            src, re.DOTALL)
        assert m, "revalidate_actor_post_await must clear the cache and re-run require_auth"

    def test_helper_behaviour_reruns_full_chain(self, db):
        """Behavioural: the helper re-runs decode + actor validation, so a
        token revoked after first auth is rejected on re-validation."""
        import security_hardening as sh
        from base_handler import BaseHandler

        # active officer row so _validate_current_actor passes
        uid = f"fcr-officer-{uuid.uuid4().hex[:6]}"
        db.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) "
            "VALUES (?, ?, 'x', 'FCR Officer', 'co', 'active')",
            (uid, f"{uid}@test.local"),
        )
        db.commit()

        token = create_token(uid, "co", "FCR Officer", token_type="officer")

        class _Req:
            def __init__(self, tok):
                self.headers = {"Authorization": f"Bearer {tok}"}
                self.path = "/test"
                self.method = "POST"
                self.remote_ip = "127.0.0.1"

        handler = BaseHandler.__new__(BaseHandler)
        handler.request = _Req(token)
        handler._status = None
        handler._written = []
        handler.set_status = lambda code, reason=None: setattr(handler, "_status", code)
        handler.write = lambda chunk: handler._written.append(chunk)
        handler.get_cookie = lambda name, default=None: default

        # first validation passes
        assert handler.revalidate_actor_post_await(roles=["admin", "sco", "co"]) is not None

        # revoke the token mid-"pipeline", then re-validate → rejected
        decoded = decode_token(token)
        sh.token_revocation_list.revoke(decoded["jti"], decoded["exp"])
        assert handler.revalidate_actor_post_await(roles=["admin", "sco", "co"]) is None
        assert handler._status == 401
