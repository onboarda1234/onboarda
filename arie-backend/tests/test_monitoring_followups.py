"""M2.1 PR-2 — officer follow-up tracker tests.

Proves the follow-up ledger is additive and NEVER mutates the alert status,
that add/resolve emit the right audit events, that resolve is idempotency-safe,
that the CHECK constraint rejects bad actions, and that the derived
open-count / next-due surfacing is correct.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

import monitoring_followups as mf


# ── DB fixture (mirrors test_document_health_scheduler.sched_db) ─────────────
@pytest.fixture
def fu_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr("config.DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr("db.DB_PATH", str(tmp_path / "test.db"))
    import db as db_module

    db_module.init_db()
    conn = db_module.get_db()
    conn.execute(
        "INSERT OR REPLACE INTO applications (id, ref, company_name, risk_level, status) "
        "VALUES ('app-fu-1', 'REF-FU-1', 'Followup Test Ltd', 'MEDIUM', 'approved')"
    )
    conn.execute(
        "INSERT INTO monitoring_alerts (application_id, alert_type, severity, status, summary) "
        "VALUES ('app-fu-1', 'screening_change', 'high', 'open', 'Test alert')"
    )
    conn.commit()
    row = conn.execute("SELECT id FROM monitoring_alerts ORDER BY id DESC LIMIT 1").fetchone()
    alert_id = row["id"]
    yield conn, alert_id
    conn.close()


class _Audit:
    def __init__(self):
        self.events = []

    def __call__(self, user, action, target, details, db=None, **kw):
        self.events.append({"action": action, "target": target, "details": details})


def _user(sub="qa1", role="sco"):
    return {"sub": sub, "role": role}


def _alert_status(conn, alert_id):
    return conn.execute("SELECT status FROM monitoring_alerts WHERE id = ?", (alert_id,)).fetchone()["status"]


# ── add ──────────────────────────────────────────────────────────────────────
def test_add_followup_inserts_and_audits_without_touching_alert(fu_db):
    conn, alert_id = fu_db
    audit = _Audit()
    before = _alert_status(conn, alert_id)
    fu = mf.add_followup(conn, alert_id=alert_id, action="next_step",
                         note="Chase client for updated passport", due_at="2026-08-01 09:00:00",
                         user=_user(), audit_writer=audit)
    assert fu["action"] == "next_step" and fu["resolved_at"] is None
    assert fu["created_by"] == "qa1"
    assert _alert_status(conn, alert_id) == before  # status untouched
    assert [e["action"] for e in audit.events] == ["monitoring.alert.followup_added"]


@pytest.mark.parametrize("action", list(mf.ACTIONS))
def test_all_valid_actions_accepted(fu_db, action):
    conn, alert_id = fu_db
    kw = {"note": "x"}
    if action == "snooze_until":
        kw = {"note": None, "due_at": "2026-09-01 00:00:00"}
    fu = mf.add_followup(conn, alert_id=alert_id, action=action,
                         note=kw.get("note"), due_at=kw.get("due_at"),
                         user=_user(), audit_writer=_Audit())
    assert fu["action"] == action


def test_invalid_action_rejected(fu_db):
    conn, alert_id = fu_db
    with pytest.raises(mf.FollowupError) as exc:
        mf.add_followup(conn, alert_id=alert_id, action="delete_everything",
                        note="x", due_at=None, user=_user(), audit_writer=_Audit())
    assert exc.value.status_code == 400


def test_snooze_requires_due_date(fu_db):
    conn, alert_id = fu_db
    with pytest.raises(mf.FollowupError):
        mf.add_followup(conn, alert_id=alert_id, action="snooze_until",
                        note="later", due_at=None, user=_user(), audit_writer=_Audit())


def test_empty_followup_rejected(fu_db):
    conn, alert_id = fu_db
    with pytest.raises(mf.FollowupError):
        mf.add_followup(conn, alert_id=alert_id, action="note",
                        note="   ", due_at=None, user=_user(), audit_writer=_Audit())


def test_check_constraint_rejects_bad_action_at_db_level(fu_db):
    conn, alert_id = fu_db
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO monitoring_alert_followups (alert_id, action, note) VALUES (?, 'bogus', 'x')",
            (alert_id,),
        )
        conn.commit()


# ── resolve ──────────────────────────────────────────────────────────────────
def test_resolve_followup_marks_and_audits(fu_db):
    conn, alert_id = fu_db
    audit = _Audit()
    fu = mf.add_followup(conn, alert_id=alert_id, action="note", note="watch",
                         due_at=None, user=_user(), audit_writer=audit)
    before = _alert_status(conn, alert_id)
    after = mf.resolve_followup(conn, followup_id=fu["id"], alert_id=alert_id,
                                user=_user("qa2"), audit_writer=audit)
    assert after["resolved_at"] is not None and after["resolved_by"] == "qa2"
    assert _alert_status(conn, alert_id) == before  # status untouched
    assert audit.events[-1]["action"] == "monitoring.alert.followup_resolved"


def test_double_resolve_is_conflict_and_single_audit(fu_db):
    conn, alert_id = fu_db
    audit = _Audit()
    fu = mf.add_followup(conn, alert_id=alert_id, action="note", note="watch",
                         due_at=None, user=_user(), audit_writer=audit)
    mf.resolve_followup(conn, followup_id=fu["id"], alert_id=alert_id, user=_user(), audit_writer=audit)
    with pytest.raises(mf.FollowupError) as exc:
        mf.resolve_followup(conn, followup_id=fu["id"], alert_id=alert_id, user=_user(), audit_writer=audit)
    assert exc.value.status_code == 409
    assert sum(e["action"] == "monitoring.alert.followup_resolved" for e in audit.events) == 1


def test_resolve_wrong_alert_is_404(fu_db):
    conn, alert_id = fu_db
    fu = mf.add_followup(conn, alert_id=alert_id, action="note", note="x",
                         due_at=None, user=_user(), audit_writer=_Audit())
    with pytest.raises(mf.FollowupError) as exc:
        mf.resolve_followup(conn, followup_id=fu["id"], alert_id="app-fu-other",
                            user=_user(), audit_writer=_Audit())
    assert exc.value.status_code == 404


# ── derived surfacing ────────────────────────────────────────────────────────
def test_open_summary_counts_and_earliest_due(fu_db):
    conn, alert_id = fu_db
    audit = _Audit()
    mf.add_followup(conn, alert_id=alert_id, action="snooze_until", note=None,
                    due_at="2026-08-15 00:00:00", user=_user(), audit_writer=audit)
    mf.add_followup(conn, alert_id=alert_id, action="next_step", note="call",
                    due_at="2026-08-01 00:00:00", user=_user(), audit_writer=audit)
    resolved = mf.add_followup(conn, alert_id=alert_id, action="note", note="done-soon",
                               due_at=None, user=_user(), audit_writer=audit)
    mf.resolve_followup(conn, followup_id=resolved["id"], alert_id=alert_id,
                        user=_user(), audit_writer=audit)
    summary = mf.open_summary(conn, alert_id)
    assert summary["open_count"] == 2  # resolved one excluded
    assert summary["next_due_at"] == "2026-08-01 00:00:00"  # earliest open due


def test_open_summary_for_alerts_batch(fu_db):
    conn, alert_id = fu_db
    mf.add_followup(conn, alert_id=alert_id, action="note", note="x",
                    due_at=None, user=_user(), audit_writer=_Audit())
    batch = mf.open_summary_for_alerts(conn, [{"id": alert_id}, {"id": None}])
    assert batch[alert_id]["open_count"] == 1


def test_list_for_alert_open_first(fu_db):
    conn, alert_id = fu_db
    audit = _Audit()
    a = mf.add_followup(conn, alert_id=alert_id, action="note", note="first",
                        due_at=None, user=_user(), audit_writer=audit)
    mf.add_followup(conn, alert_id=alert_id, action="note", note="second",
                    due_at=None, user=_user(), audit_writer=audit)
    mf.resolve_followup(conn, followup_id=a["id"], alert_id=alert_id, user=_user(), audit_writer=audit)
    rows = mf.list_for_alert(conn, alert_id)
    assert len(rows) == 2
    assert rows[0]["resolved_at"] is None  # open first
    assert rows[-1]["id"] == a["id"]       # resolved last


def test_no_new_monitoring_status_from_followups(fu_db):
    """A full add+resolve cycle must leave monitoring_alerts.status untouched."""
    conn, alert_id = fu_db
    audit = _Audit()
    start = _alert_status(conn, alert_id)
    fu = mf.add_followup(conn, alert_id=alert_id, action="pending_review", note="await review",
                         due_at=None, user=_user(), audit_writer=audit)
    mf.resolve_followup(conn, followup_id=fu["id"], alert_id=alert_id, user=_user(), audit_writer=audit)
    assert _alert_status(conn, alert_id) == start
