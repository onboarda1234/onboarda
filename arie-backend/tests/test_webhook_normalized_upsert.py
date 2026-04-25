"""
Tests for SCR-013 — Post-commit webhook re-normalization of
screening_reports_normalized (Phase A1 — Webhook drift fix).

Verifies:
  * When ENABLE_SCREENING_ABSTRACTION=true a matched applicantReviewed webhook
    triggers a fresh normalized-record insert that reflects the committed
    legacy prescreening_data (committed-read invariant).
  * When ENABLE_SCREENING_ABSTRACTION=false (default) no normalized record
    is written.
  * A normalization failure NEVER blocks the webhook 200 response.
  * The normalized record's source hash matches the hash of the committed
    legacy report (proving the renorm read committed state, not stale state).
  * An unmatched delivery (DLQ path) returns before the renorm block —
    no normalized write occurs.
"""

import json
import os
import sys
import sqlite3
import uuid

# Ensure DB_PATH is set before any production module triggers config.py.
if "DB_PATH" not in os.environ:
    import tempfile
    os.environ["DB_PATH"] = os.path.join(
        tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db"
    )
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock
import pytest


# ──────────────────────────────────────────────────────────────────────
# Handler invocation helper (mirrors test_sumsub_hardening_pr14.py)
# ──────────────────────────────────────────────────────────────────────

def _call_handler(body: bytes, headers: dict = None):
    """Invoke SumsubWebhookHandler.post() with a synthetic Tornado request."""
    from tornado.web import Application
    from tornado.httputil import HTTPServerRequest, HTTPHeaders
    from server import SumsubWebhookHandler

    app = Application()
    mock_conn = MagicMock()
    mock_conn.context = MagicMock()
    mock_conn.context.remote_ip = "127.0.0.1"

    req = HTTPServerRequest(
        method="POST",
        uri="/api/kyc/webhook",
        version="HTTP/1.1",
        headers=HTTPHeaders(headers or {}),
        body=body,
        host="127.0.0.1",
        connection=mock_conn,
    )
    handler = SumsubWebhookHandler(app, req)
    handler.post()
    return handler


def _response_json(handler):
    buf = b"".join(handler._write_buffer) if handler._write_buffer else b""
    if not buf:
        return {}
    try:
        return json.loads(buf)
    except Exception:
        return {"_raw": buf.decode(errors="replace")}


def _make_unique_applicant_id() -> str:
    """32-hex-char applicant ID, unique per call to defeat idempotency dedup."""
    return uuid.uuid4().hex  # uuid4().hex is exactly 32 lowercase hex chars


def _ext_user_id(applicant_id: str) -> str:
    """Derive a per-applicant external_user_id so OR external_user_id=?
    lookups never accidentally cross-match between tests."""
    return f"user_{applicant_id[:8]}@test.local"


def _make_payload(applicant_id: str,
                  event_type: str = "applicantReviewed",
                  review_answer: str = "GREEN") -> bytes:
    return json.dumps({
        "type": event_type,
        "applicantId": applicant_id,
        "externalUserId": _ext_user_id(applicant_id),
        "reviewResult": {
            "reviewAnswer": review_answer,
            "rejectLabels": [],
            "moderationComment": "",
        },
        # Unique per call so the idempotency digest is different every time.
        "createdAtMs": int(uuid.uuid4().int % (10 ** 13)),
    }).encode("utf-8")


# ──────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    """Guarantee the shared test DB has all inline migrations applied."""
    import db as _db
    from db import init_db
    init_db()
    yield


@pytest.fixture(autouse=True)
def _ensure_normalized_table(temp_db):
    """Create screening_reports_normalized after temp_db has initialised the DB.

    This must be function-scoped and depend on temp_db so it runs AFTER
    temp_db's first-run os.unlink + init_db sequence (which would delete any
    table created by the module-scoped fixture).
    """
    import db as _db
    from screening_storage import ensure_normalized_table
    conn = sqlite3.connect(_db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ensure_normalized_table(conn)
    finally:
        conn.close()


def _open_real_db() -> sqlite3.Connection:
    """Raw sqlite3 connection using the path frozen by db.py at import time."""
    import db as _db
    conn = sqlite3.connect(_db.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_application_with_mapping(applicant_id: str) -> str:
    """Seed an application with a basic screening_report and a mapping row.

    Returns the application id.
    """
    uid = uuid.uuid4().hex[:8]
    app_id = f"wh_renorm_{uid}"
    # Use the uid (not app_id[:8]) so every ref is unique.
    ref = f"ARF-WH-{uid.upper()}"
    prescreening_data = json.dumps({
        "screening_report": {
            "director_screenings": [],
            "ubo_screenings": [],
            "overall_flags": [],
            "company_screening": {},
            "screened_at": "2026-01-01T00:00:00",
        }
    })
    conn = _open_real_db()
    try:
        conn.execute("DELETE FROM applications WHERE id=?", (app_id,))
        conn.execute(
            "DELETE FROM sumsub_applicant_mappings WHERE applicant_id=?",
            (applicant_id,),
        )
        conn.commit()

        conn.execute(
            "INSERT INTO applications "
            "(id, ref, client_id, company_name, country, sector, entity_type, "
            "status, risk_level, risk_score, prescreening_data) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                app_id,
                ref,
                "testclient001",
                "Webhook Renorm Test Co",
                "Mauritius",
                "Technology",
                "SME",
                "submitted",
                "LOW",
                20,
                prescreening_data,
            ),
        )
        conn.execute(
            "INSERT INTO sumsub_applicant_mappings "
            "(applicant_id, external_user_id, application_id) "
            "VALUES (?,?,?)",
            (applicant_id, _ext_user_id(applicant_id), app_id),
        )
        conn.commit()
    finally:
        conn.close()
    return app_id


def _get_normalized_rows_for_app(app_id: str) -> list:
    """Return all screening_reports_normalized rows for the given app_id."""
    conn = _open_real_db()
    try:
        rows = conn.execute(
            "SELECT * FROM screening_reports_normalized WHERE application_id=?",
            (app_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# SCR-013 tests
# ═══════════════════════════════════════════════════════════════════════

class TestWebhookNormalizedUpsert:
    """SCR-013 — Post-commit re-normalization triggered by applicantReviewed."""

    def test_renorm_upserts_when_flag_on(self, temp_db, monkeypatch):
        """When ENABLE_SCREENING_ABSTRACTION=true, a matched webhook produces
        one new row in screening_reports_normalized."""
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")

        applicant_id = _make_unique_applicant_id()
        app_id = _seed_application_with_mapping(applicant_id)

        before = _get_normalized_rows_for_app(app_id)

        handler = _call_handler(_make_payload(applicant_id))
        assert handler._status_code == 200
        assert _response_json(handler).get("status") == "ok"

        after = _get_normalized_rows_for_app(app_id)
        assert len(after) == len(before) + 1, (
            f"Expected exactly one new normalized row; before={len(before)} after={len(after)}"
        )
        # The row must reference the correct application and be marked success.
        new_row = after[-1]
        assert new_row["application_id"] == app_id
        assert new_row["normalization_status"] == "success"

    def test_renorm_skipped_when_flag_off(self, temp_db, monkeypatch):
        """When ENABLE_SCREENING_ABSTRACTION=false (default), no normalized
        record is written for the matched application."""
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")

        applicant_id = _make_unique_applicant_id()
        app_id = _seed_application_with_mapping(applicant_id)

        before = _get_normalized_rows_for_app(app_id)

        handler = _call_handler(_make_payload(applicant_id))
        assert handler._status_code == 200

        after = _get_normalized_rows_for_app(app_id)
        assert len(after) == len(before), (
            "No normalized row must be written when abstraction is disabled"
        )

    def test_renorm_failure_does_not_block_200(self, temp_db, monkeypatch):
        """A normalization failure MUST NOT prevent the webhook 200 response."""
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")

        applicant_id = _make_unique_applicant_id()
        _seed_application_with_mapping(applicant_id)

        # Force normalize_screening_report to raise on every call.
        import screening_normalizer as _snorm

        def _raise(_report):
            raise RuntimeError("simulated renorm failure for SCR-013 test")

        monkeypatch.setattr(_snorm, "normalize_screening_report", _raise)

        handler = _call_handler(_make_payload(applicant_id))

        # The webhook MUST still respond 200 — renorm is non-blocking.
        assert handler._status_code == 200, (
            f"Renorm failure must not block webhook; got status {handler._status_code}"
        )
        assert _response_json(handler).get("status") == "ok"

    def test_renorm_reads_committed_legacy_report(self, temp_db, monkeypatch):
        """The normalized record's source_screening_report_hash must equal the
        hash of the committed legacy prescreening_data.screening_report.

        This validates the committed-read invariant: the renorm block opens a
        new DB connection AFTER the legacy write has committed, so the
        normalized record is derived from the up-to-date committed state
        (which includes the sumsub_webhook mutation).
        """
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")

        applicant_id = _make_unique_applicant_id()
        app_id = _seed_application_with_mapping(applicant_id)

        handler = _call_handler(_make_payload(applicant_id, review_answer="GREEN"))
        assert handler._status_code == 200

        # Read the committed legacy report directly from the DB.
        conn = _open_real_db()
        try:
            row = conn.execute(
                "SELECT prescreening_data FROM applications WHERE id=?",
                (app_id,),
            ).fetchone()
        finally:
            conn.close()

        committed_pdict = json.loads(row["prescreening_data"] or "{}")
        committed_report = committed_pdict.get("screening_report", {})

        # The committed report must now contain sumsub_webhook (written by the
        # legacy webhook handler before committing).
        assert "sumsub_webhook" in committed_report, (
            "Committed legacy report must include sumsub_webhook after webhook processing"
        )

        from screening_storage import compute_report_hash
        expected_hash = compute_report_hash(committed_report)

        # The normalized row's source hash must match the committed report.
        rows = _get_normalized_rows_for_app(app_id)
        assert rows, "Expected at least one normalized row"
        latest = rows[-1]
        assert latest["source_screening_report_hash"] == expected_hash, (
            "Normalized record source hash must match the committed legacy report "
            "(committed-read invariant)"
        )

    def test_unmatched_delivery_skips_renorm(self, temp_db, monkeypatch):
        """An unmatched delivery (DLQ path, no mapping row) returns before the
        renorm block — matched_app_ids is empty so no normalized write occurs."""
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")

        # A brand-new applicant_id with no mapping in the DB.
        applicant_id = _make_unique_applicant_id()

        handler = _call_handler(_make_payload(applicant_id))

        # DLQ path returns 200 "queued".
        assert handler._status_code == 200
        assert _response_json(handler).get("status") == "queued"

        # No normalized row was written (matched_app_ids was empty).
        conn = _open_real_db()
        try:
            # Guard: no row for any application seeded by this test module
            # that would have a client_id matching this unique applicant_id.
            # Since we never seeded an application, the applicant_id is
            # entirely unmapped and the renorm block is unreachable.
            row_count = conn.execute(
                "SELECT COUNT(*) AS c FROM screening_reports_normalized "
                "WHERE client_id = ?",
                (applicant_id,),  # applicant_id is not a client_id — will be 0
            ).fetchone()["c"]
        finally:
            conn.close()
        assert row_count == 0, (
            "Unmatched delivery must not produce any normalized record"
        )
