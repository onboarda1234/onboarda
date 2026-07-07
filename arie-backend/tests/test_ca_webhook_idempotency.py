"""SW-4 / PR-24 — ComplyAdvantage webhook retry idempotency.

CA delivers webhooks at-least-once (retries on any non-2xx / timeout), and the
two backend ECS tasks can each receive the same retry. The claim table keyed on
`webhook_id` (PRIMARY KEY) is the dedup backstop; `_claim_webhook_delivery`
decides claim-vs-duplicate so effects apply exactly once.

These tests lock that contract, including the concurrent-race hardening added by
this PR: when two workers' SELECTs both miss and both attempt the INSERT, the PK
rejects the loser — which must resolve as a clean DUPLICATE (no double-apply, no
spurious async ERROR), not propagate an exception.
"""
import sqlite3

import pytest

from screening_complyadvantage.webhook_storage import (
    _claim_webhook_delivery,
    _claim_existing_webhook_delivery,
)

_DDL = """
CREATE TABLE complyadvantage_webhook_deliveries (
    webhook_id TEXT PRIMARY KEY,
    first_received_at TEXT DEFAULT (datetime('now')),
    last_seen_at TEXT DEFAULT (datetime('now')),
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    webhook_type TEXT,
    case_identifier TEXT,
    customer_identifier TEXT,
    processing_status TEXT NOT NULL DEFAULT 'processing',
    processing_result TEXT,
    failure_reason TEXT,
    trace_id TEXT,
    payload_json TEXT,
    alert_identifiers_json TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    processed_at TEXT
);
"""


class _DB:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        pass


class _RaceDB(_DB):
    """Real sqlite (real PRIMARY KEY), but the FIRST claim SELECT is forced to
    miss — simulating two workers whose SELECTs both return empty before either
    INSERTs. The subsequent INSERT then hits the real PK of the pre-existing
    winner row, exercising the concurrent-race branch."""

    def __init__(self, conn):
        super().__init__(conn)
        self._select_miss_used = False

    def execute(self, sql, params=()):
        if (not self._select_miss_used
                and sql.strip().upper().startswith("SELECT PROCESSING_STATUS")):
            self._select_miss_used = True
            return self.conn.execute(
                "SELECT processing_status, retry_count "
                "FROM complyadvantage_webhook_deliveries WHERE 1=0")
        return self.conn.execute(sql, params)


class _InsertBoomDB(_DB):
    """INSERT raises a NON-uniqueness error and no row exists — must re-raise."""

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("INSERT INTO COMPLYADVANTAGE_WEBHOOK_DELIVERIES"):
            raise RuntimeError("disk I/O error")
        return self.conn.execute(sql, params)


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    yield c
    c.close()


def _claim(db, webhook_id="wh-1", **kw):
    return _claim_webhook_delivery(
        db, webhook_id=webhook_id,
        webhook_type=kw.get("webhook_type", "CASE_ALERT_LIST_UPDATED"),
        case_identifier=kw.get("case_identifier", "case-1"),
        customer_identifier=kw.get("customer_identifier", "cust-1"),
        trace_id=kw.get("trace_id", "trace-1"))


def _row(conn, webhook_id="wh-1"):
    r = conn.execute(
        "SELECT * FROM complyadvantage_webhook_deliveries WHERE webhook_id=?",
        (webhook_id,)).fetchone()
    return dict(r) if r else None


# ── first delivery claims; sequential retry dedups ──

def test_first_delivery_is_claimed(conn):
    res = _claim(_DB(conn))
    assert res == {"claimed": True, "duplicate": False}
    assert _row(conn)["processing_status"] == "processing"
    assert _row(conn)["duplicate_count"] == 0


def test_sequential_retry_is_duplicate_not_reprocessed(conn):
    _claim(_DB(conn))
    res = _claim(_DB(conn))  # same webhook_id, row now 'processing'
    assert res == {"claimed": False, "duplicate": True}
    assert _row(conn)["duplicate_count"] == 1


def test_retry_after_processed_is_duplicate(conn):
    _claim(_DB(conn))
    conn.execute("UPDATE complyadvantage_webhook_deliveries SET processing_status='processed' WHERE webhook_id='wh-1'")
    conn.commit()
    res = _claim(_DB(conn))
    assert res == {"claimed": False, "duplicate": True}
    assert _row(conn)["duplicate_count"] == 1


def test_distinct_webhook_ids_both_claim(conn):
    a = _claim(_DB(conn), webhook_id="wh-a")
    b = _claim(_DB(conn), webhook_id="wh-b")
    assert a["claimed"] and b["claimed"]
    assert _row(conn, "wh-a") and _row(conn, "wh-b")


# ── failed delivery is retryable, then caps ──

def test_failed_delivery_is_reclaimed_and_counts_retry(conn):
    _claim(_DB(conn))
    conn.execute("UPDATE complyadvantage_webhook_deliveries SET processing_status='retry_pending' WHERE webhook_id='wh-1'")
    conn.commit()
    res = _claim(_DB(conn))
    assert res == {"claimed": True, "duplicate": False}
    row = _row(conn)
    assert row["processing_status"] == "processing"
    assert row["retry_count"] == 1


def test_retries_exhausted_becomes_duplicate(conn):
    _claim(_DB(conn))
    conn.execute("UPDATE complyadvantage_webhook_deliveries SET processing_status='failed', retry_count=3 WHERE webhook_id='wh-1'")
    conn.commit()
    res = _claim(_DB(conn))
    assert res == {"claimed": False, "duplicate": True}


# ── concurrent race: PK rejects the loser -> clean duplicate (the PR-24 fold) ──

def test_concurrent_insert_race_resolves_as_duplicate(conn):
    # Winner already inserted + committed (status 'processing').
    conn.execute(
        "INSERT INTO complyadvantage_webhook_deliveries "
        "(webhook_id, processing_status, retry_count) VALUES ('wh-race', 'processing', 0)")
    conn.commit()
    # Loser: SELECT forced to miss -> INSERT hits the PK -> must resolve duplicate.
    res = _claim(_RaceDB(conn), webhook_id="wh-race")
    assert res == {"claimed": False, "duplicate": True}, (
        "concurrent PK collision must resolve as a clean duplicate, not raise")
    assert _row(conn, "wh-race")["duplicate_count"] == 1
    # Still exactly one row for this webhook_id (no double-apply).
    n = conn.execute(
        "SELECT COUNT(*) c FROM complyadvantage_webhook_deliveries WHERE webhook_id='wh-race'"
    ).fetchone()["c"]
    assert n == 1


def test_concurrent_race_against_failed_winner_allows_reclaim(conn):
    # Winner row exists but FAILED under the cap: the racing delivery is a
    # legitimate retry -> re-claim (not a duplicate).
    conn.execute(
        "INSERT INTO complyadvantage_webhook_deliveries "
        "(webhook_id, processing_status, retry_count) VALUES ('wh-race2', 'retry_pending', 0)")
    conn.commit()
    res = _claim(_RaceDB(conn), webhook_id="wh-race2")
    assert res == {"claimed": True, "duplicate": False}
    assert _row(conn, "wh-race2")["processing_status"] == "processing"


def test_non_uniqueness_insert_failure_is_reraised(conn):
    # A real INSERT failure (not a PK race — row never appears) must surface,
    # not be silently swallowed as a duplicate.
    with pytest.raises(RuntimeError, match="disk I/O error"):
        _claim(_InsertBoomDB(conn), webhook_id="wh-boom")
    assert _row(conn, "wh-boom") is None


# ── guards ──

def test_empty_webhook_id_is_not_claimed(conn):
    res = _claim_webhook_delivery(
        _DB(conn), webhook_id="", webhook_type="x",
        case_identifier="c", customer_identifier="cu", trace_id="t")
    assert res == {"claimed": False, "duplicate": False}
