"""P10-2 / RDI-001 + RDI-007 + RDI-011 — fail-closed decision & memo persistence.

Proves, end-to-end over HTTP with targeted SQL fault injection (the real
production failure mode: a specific INSERT/UPDATE raising at the DB layer):

  RDI-001  A final application decision can NEVER commit without its normalized
           decision_records row: injected decision-record failure -> 500, and the
           status update / audit_log Decision row roll back with it. Clean retry
           succeeds and writes the record.
  RDI-007  Memo approval never reports "approved" when persistence failed:
           injected failure -> 500 and review_status unchanged in the DB.
  RDI-011  Memo validation never reports results as recorded when persistence
           failed: injected failure -> 500 and no validation_run_at stamp.

Plus a unit test that `save_decision_record` raises (no log-and-continue), and
source guards locking the fail-closed wiring.
"""
import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from tornado.testing import AsyncHTTPTestCase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SIGNOFF_DECISION = {"acknowledged": True, "scope": "decision", "source_context": "ai_advisory"}
_SIGNOFF_MEMO = {"acknowledged": True, "scope": "memo", "source_context": "ai_advisory"}


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
                attrs[attr] = (hasattr(module, attr), getattr(module, attr, None))
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


def _live_clear_prescreening():
    now = datetime.now(timezone.utc)
    return json.dumps({
        "screening_report": {
            "screening_mode": "live",
            "screened_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "company_screening": {
                "provider": "complyadvantage",
                "source": "complyadvantage",
                "api_status": "live",
                "matched": False,
                "results": [],
                "provider_references": {"case_id": "ca-p10-2-clean"},
            },
            "company_registry": {"api_status": "live"},
            "ip_geolocation": {"api_status": "live"},
        },
        "screening_valid_until": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
        "screening_validity_days": 90,
    })


_MEMO_DATA = json.dumps({
    "ai_source": "deterministic",
    "metadata": {"ai_source": "deterministic",
                 "edd_routing": {"route": "standard", "triggers": []}},
    "supervisor": {"verdict": "CONSISTENT", "can_approve": True,
                   "mandatory_escalation": False},
})


@contextmanager
def _fail_sql(fragment, params_fragment=None):
    """Patch DBConnection.execute to raise ONLY for statements containing
    ``fragment`` (and, when given, whose params contain ``params_fragment``) —
    the closest analogue of a real DB failure on that specific write."""
    import db as db_module
    original = db_module.DBConnection.execute

    def _patched(self, sql, *args, **kwargs):
        if isinstance(sql, str) and fragment in sql:
            if params_fragment is None or params_fragment in str(args):
                raise RuntimeError(f"injected DB failure for statement: {fragment}")
        return original(self, sql, *args, **kwargs)

    db_module.DBConnection.execute = _patched
    try:
        yield
    finally:
        db_module.DBConnection.execute = original


class FailClosedPersistenceTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"p10_2_failclosed_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        self.db.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) "
            "VALUES ('sco001', 'sco001@example.test', 'test-only', 'Test SCO', 'sco', 'active')"
        )
        self.db.commit()
        self.sco_token = create_token("sco001", "sco", "Test SCO", "officer")
        import base_handler
        base_handler.rate_limiter._attempts.clear()

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

    # ── helpers ──

    def _h(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _seed_approvable(self, *, with_memo=False, memo_review_status="draft"):
        """Insert a fully-approvable LOW application (all approve gates pass).

        Mirrors the proven fixture in test_e2e_authority_matrix.py. The app's
        inputs_updated_at is set in the past so a memo inserted now is fresh.
        """
        from tests.conftest import insert_verified_required_documents
        suffix = uuid.uuid4().hex[:8]
        app_id = f"p102_{suffix}"
        app_ref = f"P102-{suffix}"
        now = datetime.now(timezone.utc)
        created = now.strftime("%Y-%m-%dT%H:%M:%S")
        inputs_past = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        self.db.execute(
            """
            INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_level, final_risk_level, risk_score,
                 prescreening_data, screening_mode, submitted_at, created_at,
                 updated_at, inputs_updated_at)
            VALUES (?, ?, ?, ?, 'Mauritius', 'Technology', 'SME',
                    'compliance_review', 'LOW', 'LOW', 20, ?, 'live', ?, ?, ?, ?)
            """,
            (app_id, app_ref, f"{app_id}_c", f"{app_ref} Ltd",
             _live_clear_prescreening(), created, created, created, inputs_past),
        )
        if with_memo:
            self.db.execute(
                """
                INSERT INTO compliance_memos
                    (application_id, memo_data, generated_by, ai_recommendation,
                     review_status, quality_score, validation_status, supervisor_status)
                VALUES (?, ?, 'system', 'APPROVE', ?, 9.0, 'pass', 'CONSISTENT')
                """,
                (app_id, _MEMO_DATA, memo_review_status),
            )
        insert_verified_required_documents(self.db, app_id)
        self.db.commit()
        return app_id, app_ref

    def _approve(self, app_id):
        return self.fetch(
            f"/api/applications/{app_id}/decision", method="POST",
            headers=self._h(self.sco_token),
            body=json.dumps({"decision": "approve",
                             "decision_reason": "P10-2 fail-closed test",
                             "officer_signoff": _SIGNOFF_DECISION}),
        )

    def _app_row(self, app_id):
        return dict(self.db.execute(
            "SELECT status, decided_at, decision_by FROM applications WHERE id=?",
            (app_id,)).fetchone())

    def _decision_records(self, ref):
        return self.db.execute(
            "SELECT id, decision_type FROM decision_records WHERE application_ref=?",
            (ref,)).fetchall()

    def _decision_audit(self, ref):
        return self.db.execute(
            "SELECT id FROM audit_log WHERE target=? AND action='Decision'",
            (ref,)).fetchall()

    # ── RDI-001: final decision is atomic with its decision record ──

    def test_decision_record_failure_rolls_back_entire_decision(self):
        app_id, ref = self._seed_approvable()
        with _fail_sql("INSERT INTO decision_records"):
            resp = self._approve(app_id)
        assert resp.code == 500, resp.body.decode()
        assert "could not persist final decision" in resp.body.decode()

        row = self._app_row(app_id)
        assert row["status"] == "compliance_review", (
            "decision-record failure must roll back the status update, got %r" % row)
        assert row["decided_at"] is None
        assert self._decision_records(ref) == [], "no decision record may exist"
        assert self._decision_audit(ref) == [], (
            "the audit_log Decision row must roll back with the decision")

    def test_governance_write_failure_also_fails_closed(self):
        """Review fold (BLOCKING finding): log_governance_attempt swallows its
        own failures by default; on PostgreSQL a failed statement rolls back
        the WHOLE transaction, so a swallowed governance failure would let
        db.commit() commit an empty transaction and return 201 with nothing
        persisted. The fail-closed block passes best_effort=False so the
        failure propagates -> 500 + rollback, never a false 201."""
        app_id, ref = self._seed_approvable()
        with _fail_sql("INSERT INTO audit_log", params_fragment="Governance Attempt"):
            resp = self._approve(app_id)
        assert resp.code == 500, resp.body.decode()
        row = self._app_row(app_id)
        assert row["status"] == "compliance_review", (
            "governance failure must roll the decision back, got %r" % row)
        assert self._decision_records(ref) == []

    def test_failed_decision_leaves_rejected_governance_trace(self):
        """Review fold: a 500-failed decision must not vanish from audit_log —
        after rollback, a REJECTED governance attempt is re-logged on a fresh
        connection."""
        app_id, ref = self._seed_approvable()
        with _fail_sql("INSERT INTO decision_records"):
            resp = self._approve(app_id)
        assert resp.code == 500
        rows = self.db.execute(
            "SELECT detail FROM audit_log WHERE action='Governance Attempt' "
            "AND target=? ORDER BY id DESC", (ref,)).fetchall()
        assert rows, "expected a governance-attempt trace for the failed decision"
        detail = json.loads(rows[0]["detail"])
        assert detail["outcome"] == "rejected"
        assert detail["response_code"] == 500
        assert "persistence_failure" in detail["rejection_reason"]

    def test_clean_approval_succeeds_and_writes_decision_record(self):
        """Positive control: proves the failure test above reached the persist
        stage (same fixture + same request succeeds without the injection)."""
        app_id, ref = self._seed_approvable()
        resp = self._approve(app_id)
        assert resp.code == 201, resp.body.decode()
        row = self._app_row(app_id)
        assert row["status"] == "approved"
        records = self._decision_records(ref)
        assert len(records) == 1 and records[0]["decision_type"] == "approve"
        assert len(self._decision_audit(ref)) == 1

    # ── RDI-007: memo approval never fakes success ──

    def test_memo_approval_persist_failure_returns_500_and_keeps_draft(self):
        app_id, _ref = self._seed_approvable(with_memo=True)
        with _fail_sql("UPDATE compliance_memos SET review_status = 'approved'"):
            resp = self.fetch(
                f"/api/applications/{app_id}/memo/approve", method="POST",
                headers=self._h(self.sco_token),
                body=json.dumps({"approval_reason": "P10-2 fail-closed test",
                                 "officer_signoff": _SIGNOFF_MEMO}),
            )
        assert resp.code == 500, resp.body.decode()
        assert "NOT been approved" in resp.body.decode()
        memo = dict(self.db.execute(
            "SELECT review_status, approved_by FROM compliance_memos "
            "WHERE application_id=? ORDER BY id DESC LIMIT 1", (app_id,)).fetchone())
        assert memo["review_status"] == "draft", (
            "failed persist must not leave the memo approved: %r" % memo)
        assert memo["approved_by"] is None

    def test_memo_approval_clean_run_succeeds(self):
        """Positive control for the injection test above."""
        app_id, _ref = self._seed_approvable(with_memo=True)
        resp = self.fetch(
            f"/api/applications/{app_id}/memo/approve", method="POST",
            headers=self._h(self.sco_token),
            body=json.dumps({"approval_reason": "P10-2 fail-closed test",
                             "officer_signoff": _SIGNOFF_MEMO}),
        )
        assert resp.code == 200, resp.body.decode()
        memo = dict(self.db.execute(
            "SELECT review_status FROM compliance_memos "
            "WHERE application_id=? ORDER BY id DESC LIMIT 1", (app_id,)).fetchone())
        assert memo["review_status"] == "approved"

    # ── RDI-011: memo validation never fakes success ──

    def test_memo_validate_persist_failure_returns_500_and_records_nothing(self):
        app_id, _ref = self._seed_approvable(with_memo=True)
        with _fail_sql("UPDATE compliance_memos SET quality_score"):
            resp = self.fetch(
                f"/api/applications/{app_id}/memo/validate", method="POST",
                headers=self._h(self.sco_token), body=json.dumps({}),
            )
        assert resp.code == 500, resp.body.decode()
        assert "NOT been recorded" in resp.body.decode()
        memo = dict(self.db.execute(
            "SELECT validation_run_at FROM compliance_memos "
            "WHERE application_id=? ORDER BY id DESC LIMIT 1", (app_id,)).fetchone())
        assert memo["validation_run_at"] is None, (
            "failed persist must not stamp validation_run_at: %r" % memo)

    def test_memo_validate_clean_run_persists(self):
        """Positive control for the injection test above."""
        app_id, _ref = self._seed_approvable(with_memo=True)
        resp = self.fetch(
            f"/api/applications/{app_id}/memo/validate", method="POST",
            headers=self._h(self.sco_token), body=json.dumps({}),
        )
        assert resp.code == 200, resp.body.decode()
        memo = dict(self.db.execute(
            "SELECT validation_run_at FROM compliance_memos "
            "WHERE application_id=? ORDER BY id DESC LIMIT 1", (app_id,)).fetchone())
        assert memo["validation_run_at"] is not None


# ── Unit: save_decision_record raises (no log-and-continue) ──

class _ExplodingDB:
    def execute(self, *a, **k):
        raise RuntimeError("boom")


def test_save_decision_record_raises_on_db_failure():
    from decision_model import build_decision_record, save_decision_record
    record = build_decision_record(
        application_ref="P102-UNIT",
        decision_type="approve",
        source="manual",
        actor={"user_id": "sco001", "role": "sco"},
        risk_level="LOW",
    )
    with pytest.raises(RuntimeError, match="boom"):
        save_decision_record(_ExplodingDB(), record)


# ── Review fold: record building tolerates cosmetic legacy data ──
# (the record save is now fail-closed, so a legacy "Very High" or junk
#  confidence must not convert a fully-gated decision into a 500)

def test_record_building_canonicalizes_legacy_risk_levels():
    from decision_model import build_from_application_decision
    user = {"sub": "sco001", "role": "sco"}
    for raw, expected in (("Very High", "VERY_HIGH"), ("high", "HIGH"),
                          ("LOW", "LOW"), ("garbage", None), (None, None)):
        rec = build_from_application_decision(
            app={"ref": "P102-CANON", "final_risk_level": raw},
            decision="approve", decision_reason="r", user=user)
        assert rec["risk_level"] == expected, (raw, rec["risk_level"])


def test_record_building_coerces_unusable_confidence():
    from decision_model import build_from_application_decision
    user = {"sub": "sco001", "role": "sco"}
    for raw, expected in (("abc", None), (250, None), (85, 0.85),
                          (0.7, 0.7), (None, None), (-1, None)):
        rec = build_from_application_decision(
            app={"ref": "P102-CONF", "risk_level": "LOW"},
            decision="approve", decision_reason="r", user=user,
            supervisor_result={"supervisor_confidence": raw, "verdict": "CONSISTENT"})
        assert rec["confidence_score"] == expected, (raw, rec["confidence_score"])


# ── Source guards: the fail-closed wiring stays wired ──

def test_source_guards_fail_closed_persistence():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, "decision_model.py"), encoding="utf-8") as fh:
        dm = fh.read()
    # The swallow comment and swallow behaviour are gone; the helper re-raises.
    assert "Non-fatal: decision records are an audit overlay" not in dm
    assert "raise" in dm.split("def save_decision_record", 1)[1].split("def ", 1)[0]

    with open(os.path.join(base, "server.py"), encoding="utf-8") as fh:
        src = fh.read()
    # Decision handler: record save happens inside the atomic commit try
    # (single except -> rollback + 500), not in its own swallowed try.
    assert "Failed to record decision record for app" not in src
    assert "save_decision_record(db, decision_record)  # raises on failure (RDI-001)" in src
    # Memo approve + validate: persist failure paths roll back and error.
    approve_tail = src.split("Failed to store memo approval", 1)[1][:600]
    assert "_rollback_and_close(db)" in approve_tail
    assert "NOT been approved" in approve_tail
    validate_tail = src.split("Failed to store memo validation results", 1)[1][:600]
    assert "_rollback_and_close(db)" in validate_tail
    assert "NOT been recorded" in validate_tail
