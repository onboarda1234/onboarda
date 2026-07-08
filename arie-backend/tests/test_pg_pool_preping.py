"""P12-6 / DCI-007 — PostgreSQL pool connection validation (pre-ping).

psycopg2's ThreadedConnectionPool does not validate liveness on checkout, so a
connection that went stale after an RDS failover / network blip would be handed
to a request handler and fail on its first statement. get_db() now pre-pings
(``SELECT 1``) each borrowed connection and, on failure, discards it from the
pool and retries with a fresh one.

These tests drive db._checkout_validated_pg_conn against a fake pool so they run
without a live PostgreSQL server; a live-PG behavioural probe is exercised
separately in the PR's validation step.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

import db as db_module


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *a):
        self._conn.executed.append(sql)
        if self._conn.alive:
            return
        raise RuntimeError("SSL connection has been closed unexpectedly")

    def fetchone(self):
        return (1,)

    def close(self):
        pass


class _FakeConn:
    """A pooled connection that is either alive or stale (dead)."""

    def __init__(self, alive=True, ident=0):
        self.alive = alive
        self.ident = ident
        self.executed = []
        self.closed = False
        self.rolled_back = 0

    def rollback(self):
        self.rolled_back += 1
        if not self.alive:
            raise RuntimeError("connection already closed")

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        self.closed = True


class _FakePool:
    """Hands out a scripted sequence of connections; records discards."""

    def __init__(self, conns):
        self._conns = list(conns)
        self.discarded = []  # conns returned with close=True
        self.returned = []   # conns returned normally
        self._next = 0

    def getconn(self):
        conn = self._conns[self._next]
        self._next += 1
        return conn

    def putconn(self, conn, close=False):
        if close:
            self.discarded.append(conn)
            conn.close()
        else:
            self.returned.append(conn)


@pytest.fixture
def fake_pg(monkeypatch):
    monkeypatch.setattr(db_module, "USE_POSTGRESQL", True)

    def _install(conns):
        pool = _FakePool(conns)
        monkeypatch.setattr(db_module, "_pg_pool", pool)
        monkeypatch.setattr(db_module, "init_pg_pool", lambda: None)
        return pool

    return _install


class TestPrePing:
    def test_healthy_connection_passes_through(self, fake_pg):
        good = _FakeConn(alive=True, ident=1)
        pool = fake_pg([good])
        conn = db_module._checkout_validated_pg_conn()
        assert conn is good
        assert "SELECT 1" in good.executed  # it WAS pinged
        assert pool.discarded == []

    def test_stale_connection_discarded_then_fresh_returned(self, fake_pg):
        stale = _FakeConn(alive=False, ident=1)
        fresh = _FakeConn(alive=True, ident=2)
        pool = fake_pg([stale, fresh])
        conn = db_module._checkout_validated_pg_conn()
        assert conn is fresh
        assert pool.discarded == [stale]      # dead one thrown away
        assert stale.closed is True

    def test_two_stale_then_good(self, fake_pg):
        s1 = _FakeConn(alive=False, ident=1)
        s2 = _FakeConn(alive=False, ident=2)
        good = _FakeConn(alive=True, ident=3)
        pool = fake_pg([s1, s2, good])
        conn = db_module._checkout_validated_pg_conn()
        assert conn is good
        assert pool.discarded == [s1, s2]

    def test_all_attempts_stale_raises(self, fake_pg):
        conns = [_FakeConn(alive=False, ident=i) for i in range(3)]
        pool = fake_pg(conns)
        with pytest.raises(RuntimeError, match="live PostgreSQL connection"):
            db_module._checkout_validated_pg_conn(max_attempts=3)
        assert pool.discarded == conns  # every dead conn discarded, none leaked

    def test_get_db_uses_validated_checkout(self, fake_pg, monkeypatch):
        stale = _FakeConn(alive=False, ident=1)
        fresh = _FakeConn(alive=True, ident=2)
        pool = fake_pg([stale, fresh])
        wrapped = db_module.get_db()
        assert wrapped.conn is fresh
        assert wrapped.is_postgres is True
        assert pool.discarded == [stale]


class TestSqliteUnaffected:
    def test_sqlite_path_does_not_preping(self, db):
        # The `db` fixture is a live SQLite DBConnection; sanity-check that the
        # SQLite branch of get_db still works end-to-end (no pre-ping applies).
        row = db.execute("SELECT 1 AS one").fetchone()
        assert row["one"] == 1
