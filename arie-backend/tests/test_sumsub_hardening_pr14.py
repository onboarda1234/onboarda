"""
PR 14 — Sumsub webhook hardening test pack (Rev 3).

Covers the remediation work for:
  * F-2  Digest algorithm allowlist (X-Payload-Digest-Alg honoring, fail-closed)
  * F-7  Legacy substring scan removal + DLQ (sumsub_unmatched_webhooks)
  * F-8  Applicant ID format validation + log masking
  * Explicit event-type gate (mutating vs acknowledged vs unknown)
  * DLQ failure mode (503 on insert failure)

Tests T2, T6, T7, T8, T9, T10, T13, T14, T14b, T15.

Implementation note on invocation strategy:
We instantiate ``SumsubWebhookHandler`` directly against a Tornado
``HTTPServerRequest`` with a mocked connection. This avoids needing a
background HTTP server and lets us monkeypatch the DB layer per-test.
Handler state is read off ``handler._status_code`` and
``handler._write_buffer`` — the Tornado 6 RequestHandler internals are
stable across 6.x. We never call ``finish()`` so no flush happens.
"""
import os
import sys
import json
import hmac
import hashlib
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ──────────────────────────────────────────────────────────────────────
# Handler invocation helper
# ──────────────────────────────────────────────────────────────────────

def _call_handler(body: bytes, headers: dict = None):
    """Directly invoke SumsubWebhookHandler.post() with a synthetic request.

    Returns the handler instance so tests can inspect status and body.
    """
    from tornado.web import Application
    from tornado.httputil import HTTPServerRequest, HTTPHeaders
    from server import SumsubWebhookHandler

    app = Application()
    mock_conn = MagicMock()
    # Tornado inspects connection.context.remote_ip; give it something.
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
    """Decode the captured response body into a dict."""
    buf = b"".join(handler._write_buffer) if handler._write_buffer else b""
    if not buf:
        return {}
    try:
        return json.loads(buf)
    except Exception:
        return {"_raw": buf.decode(errors="replace")}


def _make_payload(applicant_id="aabbccddeeff00112233445566778899",
                  external_user_id="user@example.com",
                  event_type="applicantReviewed",
                  review_answer="GREEN"):
    return json.dumps({
        "type": event_type,
        "applicantId": applicant_id,
        "externalUserId": external_user_id,
        "reviewResult": {
            "reviewAnswer": review_answer,
            "rejectLabels": [],
            "moderationComment": "",
        },
    }).encode("utf-8")


@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    """Guarantee that db.py's frozen DB_PATH has had migrations applied.

    The conftest `temp_db` fixture is function-scoped and uses a module
    global to avoid re-running init_db. That logic assumes the first
    temp_db call will trigger init_db — but we also need to handle the
    case where db.DB_PATH (frozen at import time) differs from the path
    that temp_db sets via env var (because config.DB_PATH is read once).

    This fixture ensures the real frozen DB_PATH has all migrations run
    against it before any of our tests touch it.
    """
    import db as _db
    from db import init_db
    init_db()
    yield


def _open_real_db():
    """Open a sqlite3 connection using whatever path db.py has frozen at
    import time. DO NOT read os.environ['DB_PATH'] — db.py caches it
    module-level, so the server and any direct connection must agree on
    that cached value, not the fixture-set env var.
    """
    import db as _db
    conn = sqlite3.connect(_db.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _count_audit_rows(_unused=None):
    conn = _open_real_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log "
            "WHERE user_name = 'Sumsub Webhook'"
        ).fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()


def _count_dlq_rows(_unused=None):
    conn = _open_real_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM sumsub_unmatched_webhooks"
        ).fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# T2 — F-2 Digest algorithm allowlist
# ══════════════════════════════════════════════════════════════════════

class TestDigestAlgorithmAllowlist:
    """F-2: sumsub_verify_webhook must honor X-Payload-Digest-Alg fail-closed."""

    def test_sha256_default_when_alg_none(self, monkeypatch):
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "test_secret_for_t2")
        body = b'{"type":"applicantReviewed"}'
        sig = hmac.new(b"test_secret_for_t2", body, hashlib.sha256).hexdigest()
        assert screening.sumsub_verify_webhook(body, sig, digest_alg=None) is True

    def test_sha512_accepted_when_advertised(self, monkeypatch):
        """T2: A SHA512-signed webhook with alg=HMAC_SHA512_HEX must verify."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "test_secret_for_t2")
        body = b'{"type":"applicantReviewed","applicantId":"abc"}'
        sig = hmac.new(b"test_secret_for_t2", body, hashlib.sha512).hexdigest()
        assert screening.sumsub_verify_webhook(body, sig,
                                               digest_alg="HMAC_SHA512_HEX") is True

    def test_sha512_body_with_sha256_alg_rejected(self, monkeypatch):
        """A SHA512 signature advertised as SHA256 must fail."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "test_secret_for_t2")
        body = b'{"type":"applicantReviewed"}'
        sha512_sig = hmac.new(b"test_secret_for_t2", body, hashlib.sha512).hexdigest()
        assert screening.sumsub_verify_webhook(body, sha512_sig,
                                               digest_alg="HMAC_SHA256_HEX") is False

    def test_unknown_algorithm_rejected_fail_closed(self, monkeypatch):
        """F-2: Unknown algorithm must be rejected fail-closed (no silent downgrade)."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "test_secret_for_t2")
        body = b'{"type":"applicantReviewed"}'
        # Compute a valid SHA256 sig — the point is the algorithm name is unknown.
        sig = hmac.new(b"test_secret_for_t2", body, hashlib.sha256).hexdigest()
        assert screening.sumsub_verify_webhook(body, sig,
                                               digest_alg="HMAC_MD5_HEX") is False
        assert screening.sumsub_verify_webhook(body, sig,
                                               digest_alg="arbitrary_garbage") is False

    def test_missing_algorithm_header_defaults_to_sha256(self, monkeypatch):
        """Backward-compat: no header → SHA256 (existing legacy deliveries)."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "test_secret_for_t2")
        body = b'{}'
        sig = hmac.new(b"test_secret_for_t2", body, hashlib.sha256).hexdigest()
        assert screening.sumsub_verify_webhook(body, sig, digest_alg="") is True


# ══════════════════════════════════════════════════════════════════════
# T8 — F-8 Applicant ID format validation
# ══════════════════════════════════════════════════════════════════════

class TestApplicantIdValidation:
    """F-8: malformed applicantId must be rejected with 400 before DB open."""

    def test_malformed_applicant_id_returns_400(self, temp_db):
        body = _make_payload(applicant_id="not-hex!!!")
        handler = _call_handler(body)
        assert handler._status_code == 400

    def test_short_applicant_id_returns_400(self, temp_db):
        body = _make_payload(applicant_id="abc123")  # < 16 chars
        handler = _call_handler(body)
        assert handler._status_code == 400

    def test_applicant_id_with_sql_injection_returns_400(self, temp_db):
        body = _make_payload(applicant_id="abc' OR 1=1--")
        handler = _call_handler(body)
        assert handler._status_code == 400

    def test_empty_applicant_id_returns_400(self, temp_db):
        body = _make_payload(applicant_id="")
        handler = _call_handler(body)
        assert handler._status_code == 400

    def test_valid_hex_applicant_id_accepted(self, temp_db):
        """A well-formed hex applicantId must pass validation (may still 200 via DLQ)."""
        body = _make_payload(applicant_id="aabbccddeeff00112233445566778899")
        handler = _call_handler(body)
        assert handler._status_code == 200

    def test_malformed_applicant_writes_no_audit_row(self, temp_db):
        """Malformed id must be rejected BEFORE DB open — no audit_log row written."""
        before = _count_audit_rows(temp_db)
        body = _make_payload(applicant_id="!!!bad!!!")
        handler = _call_handler(body)
        assert handler._status_code == 400
        after = _count_audit_rows(temp_db)
        assert after == before


# ══════════════════════════════════════════════════════════════════════
# T9 — Applicant ID masking in logs
# ══════════════════════════════════════════════════════════════════════

class TestApplicantIdMasking:
    """F-8: applicant_id must be masked (first 8 + ellipsis) in log lines."""

    def test_mask_applicant_id_truncates_long_id(self):
        from utils.sumsub_validation import mask_applicant_id
        masked = mask_applicant_id("aabbccddeeff00112233445566778899")
        assert masked.startswith("aabbccdd")
        assert len(masked) < len("aabbccddeeff00112233445566778899")
        # Full id must NOT appear in masked form
        assert "eeff00112233445566778899" not in masked

    def test_mask_short_id_returned_as_is(self):
        from utils.sumsub_validation import mask_applicant_id
        assert mask_applicant_id("abcd1234") == "abcd1234"

    def test_mask_none_and_empty(self):
        from utils.sumsub_validation import mask_applicant_id
        assert mask_applicant_id(None) == "<none>"
        assert mask_applicant_id("") == "<none>"

    def test_handler_log_line_uses_masked_form(self, temp_db, caplog):
        """Handler log lines for a valid applicant must use the masked prefix,
        not the full id. This protects logs from becoming tenant-correlation
        goldmines for anyone with log-read access."""
        import logging
        full_id = "ffeeddccbbaa99887766554433221100"
        body = _make_payload(applicant_id=full_id, event_type="applicantPending")
        with caplog.at_level(logging.INFO):
            handler = _call_handler(body)
        assert handler._status_code == 200
        # The full id must NOT appear in any log record.
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert full_id not in combined, \
            "Full applicant_id leaked into log output — masking is broken"
        # The masked prefix SHOULD appear.
        assert full_id[:8] in combined


# ══════════════════════════════════════════════════════════════════════
# T14 / T14b — Event-type gate
# ══════════════════════════════════════════════════════════════════════

class TestEventTypeGate:
    """Unknown and non-mutating event types must short-circuit before DB open.

    The Rev 3 design says audit_log is a state-change record, not a
    webhook-arrival record. Writing one row per delivery would create a
    high-volume, low-signal table and obscure real state transitions
    under review during a compliance audit.
    """

    def test_unknown_event_type_returns_200_no_audit_row(self, temp_db):
        """T14: Unknown event type → 200, zero new audit_log rows."""
        before = _count_audit_rows(temp_db)
        body = _make_payload(event_type="somethingUnrelatedXYZ")
        handler = _call_handler(body)
        assert handler._status_code == 200
        assert _count_audit_rows(temp_db) == before

    def test_acknowledged_non_mutating_event_no_audit_row(self, temp_db):
        """T14b: Known non-mutating event → 200, zero new audit_log rows."""
        before = _count_audit_rows(temp_db)
        for evt in ("applicantPending", "applicantCreated", "applicantOnHold",
                    "applicantPrechecked"):
            body = _make_payload(event_type=evt)
            handler = _call_handler(body)
            assert handler._status_code == 200
        assert _count_audit_rows(temp_db) == before, \
            "Non-mutating events must NOT write audit_log rows"

    def test_mutating_event_writes_audit_row(self, temp_db):
        """Sanity: applicantReviewed must still land in audit_log."""
        before = _count_audit_rows(temp_db)
        body = _make_payload(event_type="applicantReviewed")
        handler = _call_handler(body)
        assert handler._status_code == 200
        # Goes to DLQ (no mapping) but audit_log row MUST exist for the mutating
        # branch because that's the state-change record for the delivery.
        assert _count_audit_rows(temp_db) == before + 1


# ══════════════════════════════════════════════════════════════════════
# T6 / T10 — DLQ path for unmapped deliveries
# ══════════════════════════════════════════════════════════════════════

class TestDLQPath:
    """F-7: unmapped deliveries must route to sumsub_unmatched_webhooks,
    NEVER touch applications via substring scan."""

    def test_unmapped_delivery_queued_to_dlq(self, temp_db):
        """T6 + T10: Unmapped mutating delivery → 200 queued + DLQ row."""
        before = _count_dlq_rows(temp_db)
        applicant = "1122334455667788" + "99aabbccddeeff00"
        body = _make_payload(applicant_id=applicant,
                             external_user_id="lonely@example.com")
        handler = _call_handler(body)
        assert handler._status_code == 200
        resp = _response_json(handler)
        assert resp.get("status") == "queued"
        assert _count_dlq_rows(temp_db) == before + 1

    def test_dlq_row_has_correct_resolution_note(self, temp_db):
        """The inserted DLQ row should be tagged auto:no_mapping_found."""
        applicant = "deadbeefcafe1234" + "00112233aabbccdd"
        body = _make_payload(applicant_id=applicant,
                             external_user_id="orphan@example.com")
        _call_handler(body)

        conn = _open_real_db()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT applicant_id, external_user_id, event_type, status, "
                "resolution_note FROM sumsub_unmatched_webhooks "
                "WHERE applicant_id = ?",
                (applicant,),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row["applicant_id"] == applicant
        assert row["event_type"] == "applicantReviewed"
        assert row["status"] == "pending"
        assert row["resolution_note"] == "auto:no_mapping_found"


# ══════════════════════════════════════════════════════════════════════
# T7 — Real-handler cross-record mutation prevention
# ══════════════════════════════════════════════════════════════════════

class TestCrossRecordMutationPrevention:
    """F-7: with the legacy substring scan REMOVED, an unmapped applicant
    must never mutate any applications row, even if its id appears as a
    substring of some other row's prescreening_data.

    This test replaces the two tautology tests that previously ran the
    substring algorithm in the test body and asserted its output shape —
    that approach never exercised the real handler path at all.
    """

    def test_unmapped_applicant_does_not_mutate_any_row(self, temp_db):
        applicant = "abcdef0123456789" + "fedcba9876543210"

        # Seed two application rows. Row A's prescreening_data contains the
        # applicant_id as a raw substring — the legacy scan would have
        # falsely linked it. Row B is clean.
        conn = _open_real_db()
        conn.row_factory = sqlite3.Row
        try:
            # The frozen-path DB is persistent across runs; clean any residue.
            conn.execute("DELETE FROM applications WHERE id IN (?, ?)",
                         ("crossrec_a", "crossrec_b"))
            conn.execute("DELETE FROM applications WHERE ref IN (?, ?)",
                         ("ARF-T7-A", "ARF-T7-B"))
            conn.commit()
            poisoned = json.dumps({
                "note": f"historical reference to {applicant} in free text"
            })
            clean = json.dumps({"company": "Clean Co"})
            conn.execute(
                "INSERT INTO applications (id, ref, client_id, company_name, "
                "country, sector, entity_type, status, risk_level, risk_score, "
                "prescreening_data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("crossrec_a", "ARF-T7-A", "clientA", "Poisoned Co",
                 "Mauritius", "Technology", "SME", "draft", "LOW", 10, poisoned),
            )
            conn.execute(
                "INSERT INTO applications (id, ref, client_id, company_name, "
                "country, sector, entity_type, status, risk_level, risk_score, "
                "prescreening_data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("crossrec_b", "ARF-T7-B", "clientB", "Clean Co",
                 "Mauritius", "Technology", "SME", "draft", "LOW", 10, clean),
            )
            conn.commit()
        finally:
            conn.close()

        # Send a real webhook via the real handler.
        body = _make_payload(applicant_id=applicant)
        handler = _call_handler(body)
        assert handler._status_code == 200

        # Neither row must carry a sumsub_webhook mutation.
        conn = _open_real_db()
        conn.row_factory = sqlite3.Row
        try:
            for row_id in ("crossrec_a", "crossrec_b"):
                row = conn.execute(
                    "SELECT prescreening_data FROM applications WHERE id=?",
                    (row_id,),
                ).fetchone()
                pdict = json.loads(row["prescreening_data"] or "{}")
                screening_report = pdict.get("screening_report", {})
                assert "sumsub_webhook" not in screening_report, (
                    f"Row {row_id} was mutated by unmapped webhook — "
                    "legacy substring scan must be gone"
                )
        finally:
            conn.close()


# ══════════════════════════════════════════════════════════════════════
# T13 — DLQ insert failure → 503
# ══════════════════════════════════════════════════════════════════════

class TestDLQFailureMode:
    """If the DLQ INSERT itself fails, the handler MUST return 503 (not 200)
    so Sumsub retries. Silent swallow would lose compliance-relevant events.
    """

    def test_dlq_insert_failure_returns_503(self, temp_db, monkeypatch):
        import server

        real_get_db = server.get_db

        class _BreakingDB:
            def __init__(self, inner):
                self._inner = inner
                self._calls = 0

            def execute(self, sql, *args, **kwargs):
                self._calls += 1
                if "sumsub_unmatched_webhooks" in sql.lower():
                    raise sqlite3.OperationalError(
                        "simulated DLQ write failure"
                    )
                return self._inner.execute(sql, *args, **kwargs)

            def commit(self):
                return self._inner.commit()

            def rollback(self):
                return self._inner.rollback()

            def close(self):
                return self._inner.close()

        def fake_get_db(*args, **kwargs):
            return _BreakingDB(real_get_db(*args, **kwargs))

        monkeypatch.setattr(server, "get_db", fake_get_db)

        applicant = "badbadbadbadbad0" + "0badbadbadbadbad"
        body = _make_payload(applicant_id=applicant)
        handler = _call_handler(body)

        assert handler._status_code == 503, (
            f"DLQ insert failure must return 503, got {handler._status_code}"
        )


# ══════════════════════════════════════════════════════════════════════
# T15 — Mapping lookup exception routes to DLQ with diagnostic note
# ══════════════════════════════════════════════════════════════════════

class TestMappingLookupException:
    """If the mapping-table SELECT itself raises, the delivery must route
    to the DLQ with resolution_note='auto:mapping_lookup_failed' — never
    silently treated as 'no mapping found' (which hides infrastructure faults).
    """

    def test_mapping_lookup_exception_routes_to_dlq(self, temp_db, monkeypatch):
        import server
        real_get_db = server.get_db

        class _MappingBreakingDB:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *args, **kwargs):
                if "sumsub_applicant_mappings" in sql.lower():
                    raise sqlite3.OperationalError(
                        "simulated mapping table fault"
                    )
                return self._inner.execute(sql, *args, **kwargs)

            def commit(self):
                return self._inner.commit()

            def rollback(self):
                return self._inner.rollback()

            def close(self):
                return self._inner.close()

        def fake_get_db(*args, **kwargs):
            return _MappingBreakingDB(real_get_db(*args, **kwargs))

        monkeypatch.setattr(server, "get_db", fake_get_db)

        applicant = "feedfacefeedface" + "cafebabecafebabe"
        body = _make_payload(applicant_id=applicant)
        handler = _call_handler(body)

        assert handler._status_code == 200
        resp = _response_json(handler)
        assert resp.get("status") == "queued"

        # Verify the DLQ row carries the diagnostic resolution_note.
        conn = _open_real_db()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT resolution_note FROM sumsub_unmatched_webhooks "
                "WHERE applicant_id = ?",
                (applicant,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["resolution_note"] == "auto:mapping_lookup_failed"
