"""APP-AUD-gov-dup-1 (CM sibling): the change-request approve path must not
double-write under a concurrent race.

The named finding's own path (application /decision) was verified already
idempotent (locked + 409 on replay). Its sibling — change_management.
approve_change_request — read status with a plain SELECT and then issued an
UNCONDITIONAL `UPDATE ... WHERE id=?`, so two concurrent approvals on the same
pending request could both pass validation and both write an approval + review
+ "Change Request Approved" audit row. The fix guards the transition with a
compare-and-set on the exact status read (WHERE id=? AND status=?) and aborts
before writing the review/audit when 0 rows change.

These guards prove the CAS SQL semantics on the real DB dialect and that the
function wires the guard.
"""
import inspect

from test_change_management import (
    _get_cm,
    _DBWrapper,
    _setup_test_data,
    _cm_clear_and_approve,
)


def _make_pending_tier1_request(cm, wdb, app_id, sco):
    items = [{"change_type": "director_change", "materiality": "tier1"}]
    req = cm.create_change_request(
        wdb, app_id, "backoffice_manual", "backoffice", "Dir change", items, sco
    )
    cm.submit_change_request(wdb, req["id"], sco)
    cm.update_change_request_status(wdb, req["id"], "triage_in_progress", sco)
    cm.update_change_request_status(wdb, req["id"], "ready_for_review", sco)
    cm.update_change_request_status(wdb, req["id"], "approval_pending", sco)
    return req


def test_cm_approve_cas_refuses_stale_second_writer(db):
    cm = _get_cm()
    wdb = _DBWrapper(db)
    app_id, _ = _setup_test_data(db)
    sco = {"sub": "sco1", "name": "SCO", "role": "sco"}
    req = _make_pending_tier1_request(cm, wdb, app_id, sco)

    ok, err = _cm_clear_and_approve(cm, wdb, req["id"])
    assert ok, err  # first approval succeeds; status -> approved

    # A concurrent second approver read status='approval_pending' before the
    # first commit, so its compare-and-set targets that (now stale) status. The
    # row is 'approved' now, so the guarded UPDATE must match 0 rows and never
    # write a second approval.
    stale = wdb.execute(
        "UPDATE change_requests SET status='approved', approved_by=?, approved_at=?, "
        "decision_notes=?, updated_at=? WHERE id=? AND status=?",
        ("second-officer", "2026-01-01T00:00:00Z", "concurrent",
         "2026-01-01T00:00:00Z", req["id"], "approval_pending"),
    )
    assert stale.rowcount == 0

    # Positive control: a CAS matching the actual current status does apply.
    fresh = wdb.execute(
        "UPDATE change_requests SET updated_at=? WHERE id=? AND status=?",
        ("2026-01-02T00:00:00Z", req["id"], "approved"),
    )
    assert fresh.rowcount == 1


def test_approve_change_request_wires_status_guarded_cas():
    import change_management as cm

    src = inspect.getsource(cm.approve_change_request)
    assert "WHERE id = ? AND status = ?" in src, "approve UPDATE must be status-guarded (CAS)"
    assert "rowcount" in src, "must check rows affected"
    assert "already decided by another reviewer" in src, "must abort on 0 rows"
