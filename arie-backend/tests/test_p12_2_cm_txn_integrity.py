"""P12-2 (audit DCI-012 / DCI-013) — change-management transaction integrity.

DCI-012: ``implement_change_request`` used to commit the live profile update
and the ``implemented`` status BEFORE post-change risk recomputation, and a
recompute failure was only warning-logged — leaving an application approvable
on its stale pre-change risk score. Now a quarantine sentinel
(``stale:cm_recompute_pending``) is stamped on the application IN THE SAME
TRANSACTION as the implementation; a successful post-commit recompute
overwrites it with the real config version, and any failure (raise, soft
``recomputed=False``, missing recompute function, crash between commits)
leaves the sentinel durable so the decision-time staleness gate blocks
approval until a successful re-score.

DCI-013: ``approve_change_request`` / ``implement_change_request`` (and the
sibling ``reject_change_request``) used to write their audit rows AFTER
``db.commit()`` — an audit failure left a durable state change with no audit
evidence. Audit rows now join the state-transition transaction
(``commit=False``) and commit atomically with it; a failed audit write rolls
the state change back.
"""

import os
import secrets
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.cm_evidence_test_helpers import attach_verified_cm_evidence

ADMIN_USER = {"sub": "admin-1", "name": "Admin User", "role": "admin"}
SCO_USER = {"sub": "sco-1", "name": "SCO User", "role": "sco"}

CM_PENDING_SENTINEL = "stale:cm_recompute_pending"


def _get_cm():
    import change_management as cm
    return cm


class _DBWrapper:
    """Wrap raw sqlite3 connection to match cm module expectations."""

    def __init__(self, conn):
        self._conn = conn
        self.is_postgres = False

    def execute(self, sql, params=None):
        if params:
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass


class _OrderSpyDB(_DBWrapper):
    """Records commit()/rollback() calls into a shared event list so tests can
    assert the audit write happened INSIDE the transaction (before commit)."""

    def __init__(self, conn, events):
        super().__init__(conn)
        self.events = events

    def commit(self):
        self.events.append(("commit",))
        super().commit()

    def rollback(self):
        self.events.append(("rollback",))
        super().rollback()


def _setup_app(raw_db, *, risk_score=42.0, risk_level="MEDIUM",
               risk_config_version="risk_config:2026-01-01T00:00:00Z"):
    app_id = f"test-p122-{secrets.token_hex(4)}"
    client_id = f"test-cl-{secrets.token_hex(4)}"
    raw_db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"test-{secrets.token_hex(3)}@test.com", "hash", "Test Company"),
    )
    raw_db.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type, status,
            risk_score, risk_level, risk_config_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, f"APP-{secrets.token_hex(4)}", client_id, "P122 Co",
         "GB", "Financial Services", "Limited Company", "approved",
         risk_score, risk_level, risk_config_version),
    )
    raw_db.commit()
    return app_id


def _make_approved_cr(cm, wdb, app_id, *, field="company_name",
                      old="P122 Co", new="P122 Co Renamed"):
    req = cm.create_change_request(
        wdb, app_id, "backoffice_manual", "backoffice", "P12-2 test change",
        [{"change_type": "company_details", "field_name": field,
          "old_value": old, "new_value": new}],
        ADMIN_USER,
    )
    req_id = req["id"]
    cm.submit_change_request(wdb, req_id, ADMIN_USER)
    cm.update_change_request_status(wdb, req_id, "triage_in_progress", ADMIN_USER)
    cm.update_change_request_status(wdb, req_id, "ready_for_review", ADMIN_USER)
    cm.update_change_request_status(wdb, req_id, "approval_pending", ADMIN_USER)
    attach_verified_cm_evidence(cm, wdb, req_id)
    cm.record_precondition_result(
        wdb, req_id, "screening", SCO_USER,
        result={"screening_ref": "test-screen",
                "screened_at": "2026-01-01T00:00:00Z",
                "unresolved_match": False})
    cm.record_precondition_result(
        wdb, req_id, "risk", SCO_USER, result={"risk_level": "MEDIUM"})
    row = dict(wdb.execute(
        "SELECT created_by FROM change_requests WHERE id = ?", (req_id,)
    ).fetchone())
    approver = ADMIN_USER if row.get("created_by") != ADMIN_USER["sub"] else SCO_USER
    ok, err = cm.approve_change_request(wdb, req_id, approver)
    assert ok, f"fixture approval failed: {err}"
    return req_id


def _risk_config_version(raw_db, app_id):
    row = raw_db.execute(
        "SELECT risk_config_version FROM applications WHERE id = ?", (app_id,)
    ).fetchone()
    return row["risk_config_version"] if row else None


def _cr_flag_true(raw_db, req_id, col):
    row = dict(raw_db.execute(
        f"SELECT {col} FROM change_requests WHERE id = ?", (req_id,)
    ).fetchone())
    return bool(row.get(col))


# ============================================================================
# DCI-012 — recompute failure must quarantine, never silently pass
# ============================================================================

class TestDci012RecomputeQuarantine:

    def test_raising_recompute_keeps_sentinel_and_implementation(self, db):
        """Recompute raising must leave the change implemented AND the app
        quarantined — not approvable on the stale pre-change score."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req_id = _make_approved_cr(cm, wdb, app_id)
        assert _cr_flag_true(db, req_id, "risk_review_required"), (
            "fixture expectation: company_details change is tier1/tier2 → "
            "risk_review_required; if materiality mapping changed, set the "
            "flag explicitly in this test"
        )

        def exploding_recompute(db_, app_id_, reason, user, log_audit_fn):
            raise RuntimeError("simulated recompute crash")

        ok, err, version_id = cm.implement_change_request(
            wdb, req_id, ADMIN_USER, recompute_risk_fn=exploding_recompute,
        )
        assert ok, f"implementation itself must stay committed: {err}"
        assert version_id

        cr = db.execute(
            "SELECT status FROM change_requests WHERE id = ?", (req_id,)
        ).fetchone()
        assert cr["status"] == "implemented"
        assert _risk_config_version(db, app_id) == CM_PENDING_SENTINEL

    def test_soft_failed_recompute_keeps_sentinel(self, db):
        """recompute_risk swallows generic errors and returns recomputed=False
        — that soft failure must keep the quarantine sentinel."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req_id = _make_approved_cr(cm, wdb, app_id)

        def soft_fail_recompute(db_, app_id_, reason, user, log_audit_fn):
            return {"recomputed": False, "changed": False}

        ok, err, _ = cm.implement_change_request(
            wdb, req_id, ADMIN_USER, recompute_risk_fn=soft_fail_recompute,
        )
        assert ok, err
        assert _risk_config_version(db, app_id) == CM_PENDING_SENTINEL

    def test_missing_recompute_fn_still_quarantines(self, db):
        """No recompute function at all (rule engine unavailable) is the WORST
        DCI-012 case — the sentinel must still be stamped."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req_id = _make_approved_cr(cm, wdb, app_id)

        ok, err, _ = cm.implement_change_request(wdb, req_id, ADMIN_USER)
        assert ok, err
        assert _risk_config_version(db, app_id) == CM_PENDING_SENTINEL

    def test_successful_recompute_clears_sentinel(self, db):
        """A successful recompute overwrites the sentinel with the real config
        version — the app is NOT left quarantined."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req_id = _make_approved_cr(cm, wdb, app_id)
        new_version = "risk_config:2026-07-08T00:00:00Z"

        def good_recompute(db_, app_id_, reason, user, log_audit_fn):
            # Mimic rule_engine.recompute_risk: write on the caller's
            # connection, never commit, stamp real provenance.
            db_.execute(
                "UPDATE applications SET risk_config_version=? WHERE id=?",
                (new_version, app_id_),
            )
            return {"recomputed": True, "changed": True}

        ok, err, _ = cm.implement_change_request(
            wdb, req_id, ADMIN_USER, recompute_risk_fn=good_recompute,
        )
        assert ok, err
        assert _risk_config_version(db, app_id) == new_version

    def test_false_positive_recomputed_flag_is_not_trusted(self, db):
        """recompute_risk can report recomputed=True even when its persistence
        UPDATE failed (flag set before the write). Ground truth is the stored
        provenance — the sentinel must survive such a false positive."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req_id = _make_approved_cr(cm, wdb, app_id)

        def lying_recompute(db_, app_id_, reason, user, log_audit_fn):
            return {"recomputed": True, "changed": True}  # wrote nothing

        ok, err, _ = cm.implement_change_request(
            wdb, req_id, ADMIN_USER, recompute_risk_fn=lying_recompute,
        )
        assert ok, err
        # Sentinel remains because nothing overwrote it — and the staleness
        # gate therefore still blocks approval.
        assert _risk_config_version(db, app_id) == CM_PENDING_SENTINEL

    def test_sentinel_stamped_in_same_txn_as_implementation(self, db):
        """The quarantine marker must be part of the implementation commit —
        visible from a second connection immediately after implement returns,
        even though the recompute (which raises here) never persisted."""
        import sqlite3 as _sqlite3
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req_id = _make_approved_cr(cm, wdb, app_id)

        observed = {}

        def crashing_recompute(db_, app_id_, reason, user, log_audit_fn):
            # At this point the implementation MUST already be durable —
            # including the sentinel — because we run post-commit.
            db_path = db.execute("PRAGMA database_list").fetchone()[2]
            other = _sqlite3.connect(db_path)
            other.row_factory = _sqlite3.Row
            try:
                row = other.execute(
                    "SELECT risk_config_version FROM applications WHERE id=?",
                    (app_id_,),
                ).fetchone()
                observed["version_at_recompute"] = row["risk_config_version"]
                row2 = other.execute(
                    "SELECT status FROM change_requests WHERE id=?", (req_id,)
                ).fetchone()
                observed["cr_status_at_recompute"] = row2["status"]
            finally:
                other.close()
            raise RuntimeError("crash after observing durable state")

        ok, err, _ = cm.implement_change_request(
            wdb, req_id, ADMIN_USER, recompute_risk_fn=crashing_recompute,
        )
        assert ok, err
        # Second connection saw the sentinel + implemented status ALREADY
        # committed before the recompute ran — a crash between the two
        # commits can never leave an unquarantined implemented change.
        assert observed["version_at_recompute"] == CM_PENDING_SENTINEL
        assert observed["cr_status_at_recompute"] == "implemented"

    def test_no_risk_review_required_means_no_sentinel(self, db):
        """tier3 / non-risk changes must NOT be quarantined."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        original_version = "risk_config:2026-01-01T00:00:00Z"
        app_id = _setup_app(db, risk_config_version=original_version)
        req_id = _make_approved_cr(cm, wdb, app_id)
        # Force the flag off to isolate the sentinel behaviour.
        db.execute(
            "UPDATE change_requests SET risk_review_required = 0 WHERE id = ?",
            (req_id,),
        )
        db.commit()

        ok, err, _ = cm.implement_change_request(wdb, req_id, ADMIN_USER)
        assert ok, err
        assert _risk_config_version(db, app_id) == original_version

    def test_staleness_gate_blocks_on_cm_sentinel(self, db):
        """End-to-end: the sentinel left by a failed CM recompute must trip the
        decision-time staleness gate (server._application_risk_staleness_error)."""
        import server as server_mod
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req_id = _make_approved_cr(cm, wdb, app_id)

        ok, err, _ = cm.implement_change_request(
            wdb, req_id, ADMIN_USER,
            recompute_risk_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert ok, err

        app_row = dict(db.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone())
        assert str(app_row["risk_config_version"]).startswith("stale:")

        from rule_engine import _get_risk_config_version_strict
        try:
            current = _get_risk_config_version_strict(wdb)
        except Exception:
            current = "unreadable"  # gate fail-closes on unreadable provenance

        gate_error = server_mod._application_risk_staleness_error(
            wdb, app_row, action_label="approve"
        )
        if current:
            # Versioning in force (or unreadable): the sentinel must block.
            assert gate_error is not None
        else:
            # No risk_config row in this fixture DB: versioning not in use —
            # the gate's documented no-op branch. The sentinel is still
            # stamped (asserted above) so any configured environment blocks.
            assert gate_error is None


# ============================================================================
# DCI-013 — audit evidence joins the state-transition transaction
# ============================================================================

class TestDci013AuditAtomicity:

    def test_approve_audit_written_before_commit(self, db):
        cm = _get_cm()
        events = []
        wdb = _OrderSpyDB(db, events)
        app_id = _setup_app(db)

        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "audit order",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "P122 Co", "new_value": "Ordered Co"}],
            ADMIN_USER,
        )
        req_id = req["id"]
        cm.submit_change_request(wdb, req_id, ADMIN_USER)
        cm.update_change_request_status(wdb, req_id, "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req_id, "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req_id, "approval_pending", ADMIN_USER)
        attach_verified_cm_evidence(cm, wdb, req_id)
        cm.record_precondition_result(
            wdb, req_id, "screening", SCO_USER,
            result={"screening_ref": "s", "screened_at": "2026-01-01T00:00:00Z",
                    "unresolved_match": False})
        cm.record_precondition_result(
            wdb, req_id, "risk", SCO_USER, result={"risk_level": "MEDIUM"})

        def spy_audit(user, action, target, detail, **kwargs):
            events.append(("audit", action, kwargs.get("commit")))

        events.clear()
        ok, err = cm.approve_change_request(wdb, req_id, SCO_USER, log_audit_fn=spy_audit)
        assert ok, err

        audit_idx = [i for i, e in enumerate(events)
                     if e[0] == "audit" and e[1] == "Change Request Approved"]
        commit_idx = [i for i, e in enumerate(events) if e[0] == "commit"]
        assert audit_idx, "approval audit event missing"
        assert commit_idx, "commit event missing"
        assert audit_idx[0] < commit_idx[-1], (
            "DCI-013: the approval audit row must be written BEFORE the "
            "state-transition commit, not after"
        )
        # commit=False must be forwarded so the audit row joins the txn
        assert events[audit_idx[0]][2] is False

    def test_approve_audit_failure_rolls_back_approval(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "audit fail",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "P122 Co", "new_value": "NeverLands Co"}],
            ADMIN_USER,
        )
        req_id = req["id"]
        cm.submit_change_request(wdb, req_id, ADMIN_USER)
        cm.update_change_request_status(wdb, req_id, "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req_id, "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req_id, "approval_pending", ADMIN_USER)
        attach_verified_cm_evidence(cm, wdb, req_id)
        cm.record_precondition_result(
            wdb, req_id, "screening", SCO_USER,
            result={"screening_ref": "s", "screened_at": "2026-01-01T00:00:00Z",
                    "unresolved_match": False})
        cm.record_precondition_result(
            wdb, req_id, "risk", SCO_USER, result={"risk_level": "MEDIUM"})

        def failing_audit(user, action, target, detail, **kwargs):
            if action == "Change Request Approved":
                raise RuntimeError("audit store down")

        ok, err = cm.approve_change_request(wdb, req_id, SCO_USER, log_audit_fn=failing_audit)
        assert not ok
        assert "approval failed" in err.lower()

        cr = db.execute(
            "SELECT status, approved_by FROM change_requests WHERE id = ?",
            (req_id,),
        ).fetchone()
        assert cr["status"] == "approval_pending", (
            "DCI-013: an approval whose audit evidence could not be written "
            "must NOT become durable"
        )
        reviews = db.execute(
            "SELECT COUNT(*) AS c FROM change_request_reviews WHERE request_id = ?",
            (req_id,),
        ).fetchone()["c"]
        assert reviews == 0

    def test_implement_audit_written_before_commit(self, db):
        cm = _get_cm()
        events = []
        wdb = _OrderSpyDB(db, events)
        app_id = _setup_app(db)
        req_id = _make_approved_cr(cm, wdb, app_id)

        def spy_audit(user, action, target, detail, **kwargs):
            events.append(("audit", action, kwargs.get("commit")))

        events.clear()
        ok, err, _ = cm.implement_change_request(
            wdb, req_id, ADMIN_USER, log_audit_fn=spy_audit,
        )
        assert ok, err

        audit_idx = [i for i, e in enumerate(events)
                     if e[0] == "audit" and e[1] == "Change Request Implemented"]
        commit_idx = [i for i, e in enumerate(events) if e[0] == "commit"]
        assert audit_idx, "implementation audit event missing"
        assert commit_idx, "commit event missing"
        assert audit_idx[0] < commit_idx[0], (
            "DCI-013: the implementation audit row must be written BEFORE the "
            "atomic implementation commit"
        )
        assert events[audit_idx[0]][2] is False

    def test_implement_audit_failure_rolls_back_everything(self, db):
        """A failed implementation-audit write must roll back the live update,
        the profile version, the status flip AND the quarantine sentinel."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        original_version = "risk_config:2026-01-01T00:00:00Z"
        app_id = _setup_app(db, risk_config_version=original_version)
        req_id = _make_approved_cr(
            cm, wdb, app_id, new="MustNotLand Co",
        )
        versions_before = len(cm.get_profile_versions(wdb, app_id))

        def failing_audit(user, action, target, detail, **kwargs):
            if action == "Change Request Implemented":
                raise RuntimeError("audit store down")

        ok, err, version_id = cm.implement_change_request(
            wdb, req_id, ADMIN_USER, log_audit_fn=failing_audit,
        )
        assert not ok
        assert version_id is None

        cr = db.execute(
            "SELECT status FROM change_requests WHERE id = ?", (req_id,)
        ).fetchone()
        assert cr["status"] == "approved"
        app = db.execute(
            "SELECT company_name, risk_config_version FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        assert app["company_name"] == "P122 Co", "live change must be rolled back"
        assert app["risk_config_version"] == original_version, (
            "quarantine sentinel must be rolled back with the implementation"
        )
        assert len(cm.get_profile_versions(wdb, app_id)) == versions_before

    def test_reject_audit_written_before_commit(self, db):
        cm = _get_cm()
        events = []
        wdb = _OrderSpyDB(db, events)
        app_id = _setup_app(db)
        req = cm.create_change_request(
            wdb, app_id, "backoffice_manual", "backoffice", "reject order",
            [{"change_type": "company_details", "field_name": "company_name",
              "old_value": "P122 Co", "new_value": "Rejected Co"}],
            ADMIN_USER,
        )
        req_id = req["id"]
        cm.submit_change_request(wdb, req_id, ADMIN_USER)
        cm.update_change_request_status(wdb, req_id, "triage_in_progress", ADMIN_USER)
        cm.update_change_request_status(wdb, req_id, "ready_for_review", ADMIN_USER)
        cm.update_change_request_status(wdb, req_id, "approval_pending", ADMIN_USER)

        def spy_audit(user, action, target, detail, **kwargs):
            events.append(("audit", action, kwargs.get("commit")))

        events.clear()
        ok, err = cm.reject_change_request(wdb, req_id, SCO_USER, log_audit_fn=spy_audit)
        assert ok, err

        audit_idx = [i for i, e in enumerate(events)
                     if e[0] == "audit" and e[1] == "Change Request Rejected"]
        commit_idx = [i for i, e in enumerate(events) if e[0] == "commit"]
        assert audit_idx and commit_idx
        assert audit_idx[0] < commit_idx[-1]
        assert events[audit_idx[0]][2] is False

    def test_legacy_four_arg_collector_still_works(self, db):
        """Backward compat: minimal collectors without **kwargs must not break
        approve/implement (the compat shim drops commit/db for them)."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_app(db)
        req_id = _make_approved_cr(cm, wdb, app_id)

        seen = []

        def four_arg_collector(user, action, target, detail):
            seen.append(action)

        ok, err, _ = cm.implement_change_request(
            wdb, req_id, ADMIN_USER, log_audit_fn=four_arg_collector,
        )
        assert ok, err
        assert "Change Request Implemented" in seen
