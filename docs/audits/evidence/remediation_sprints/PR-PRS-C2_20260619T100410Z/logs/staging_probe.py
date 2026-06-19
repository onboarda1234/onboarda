#!/usr/bin/env python3
"""PR-PRS-C2 staging behavioural probe (run IN-TASK via ECS Exec).

Why in-task: the memo-gate failure path is triggered by patching
``periodic_review_memo.build_memo_data`` in-process. It cannot be exercised
over HTTP, so this script must run inside the deployed staging backend task
(same image, same DB) — the same approach as PR-PRS-C1's ECS-Exec smoke.

Run:
    aws ecs execute-command --cluster regmind-staging \
        --task <task-id> --container <name> --interactive \
        --command "python /app/staging_probe.py"

It is self-cleaning: all rows it creates are FIXTURE-marked with the prefix
below and deleted in a finally block. It prints a JSON result and exits
non-zero if any assertion fails.

Behavioural assertions (the C2 fail-closed contract):
  1. memo failure  -> review status == completion_pending_memo, completed_at IS NULL
  2. canonical risk elevation applied during quarantine (risk-change variant)
  3. NO next-cycle review row scheduled while quarantined
  4. recovery (complete_review_with_memo) -> status == completed
  5. a 'generated' memo row now exists
  6. next-cycle review row now scheduled

NOTE: adjust the synthetic INSERTs only if the staging schema requires extra
NOT NULL columns; required_items is set to '[]' so completion is not blocked.
"""
import json
import sys
from unittest import mock

PREFIX = "prprsc2-staging-probe"
results = {"version": None, "scenarios": {}, "passed": False}


def _assert(cond, label):
    if not cond:
        raise AssertionError(label)


def main():
    import server  # noqa: F401 — ensures app modules + get_db are importable
    from server import get_db
    import periodic_review_engine as pre
    import periodic_review_memo as prm

    user = {
        "sub": "sco001",
        "id": "sco001",
        "role": "sco",
        "email": "raj.patel@onboarda.com",
    }

    def audit_writer(actor, action, target, detail, db=None, **kw):
        # no-op audit sink for the probe; real audits still fire in-engine
        return None

    db = get_db()
    app_id = None
    rid = None
    try:
        # --- synthetic, fixture-marked application + review (MEDIUM risk) ---
        app_id = f"{PREFIX}-app"
        db.execute(
            "INSERT INTO applications (id, ref, company_name, risk_level, final_risk_level, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (app_id, f"{PREFIX}-ref", f"{PREFIX}-co", "MEDIUM", "MEDIUM", "approved"),
        )
        rid = db.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status, required_items, due_date, "
            "client_attestation_status, baseline_status, officer_rationale) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                app_id, f"{PREFIX}-co", "MEDIUM", "in_progress", "[]", "2026-12-31",
                "submitted", "not_applicable", "staging probe rationale",
            ),
        ).fetchone()["id"]
        db.commit()

        # --- 1+2+3: force memo failure, expect quarantine + elevation + no cycle ---
        with mock.patch.object(prm, "build_memo_data",
                               side_effect=RuntimeError("probe-injected")):
            pre.record_review_outcome(
                db, rid,
                outcome="risk_rating_changed",
                outcome_reason="probe risk change",
                risk_changed=True, new_risk_level="HIGH",
                risk_impact="probe sanctions exposure",
                officer_acknowledgement=True,
                enforce_prs5_gates=True,
                memo_gate=True,
                user=user, audit_writer=audit_writer,
            )
            prm.complete_review_with_memo(db, rid, user=user, audit_writer=audit_writer)

        row = db.execute(
            "SELECT status, completed_at FROM periodic_reviews WHERE id = ?", (rid,)
        ).fetchone()
        _assert(row["status"] == "completion_pending_memo", "1: not quarantined")
        _assert(row["completed_at"] is None, "1: completed_at not null while quarantined")
        app = db.execute(
            "SELECT final_risk_level FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        _assert(str(app["final_risk_level"]).upper() in ("HIGH", "VERY_HIGH"),
                "2: canonical risk not elevated")
        cycles = db.execute(
            "SELECT COUNT(*) AS c FROM periodic_reviews WHERE application_id = ?", (app_id,)
        ).fetchone()
        _assert(cycles["c"] == 1, "3: next cycle scheduled while quarantined")
        results["scenarios"]["quarantine"] = "pass"

        # --- 4+5+6: recovery finalises, memo exists, next cycle scheduled ---
        rec = prm.complete_review_with_memo(db, rid, user=user, audit_writer=audit_writer)
        _assert(rec.get("finalized") is True and rec.get("status") == "completed",
                "4: recovery did not finalise")
        row2 = db.execute(
            "SELECT status, completed_at FROM periodic_reviews WHERE id = ?", (rid,)
        ).fetchone()
        _assert(row2["status"] == "completed", "4: status not completed after recovery")
        _assert(row2["completed_at"] is not None, "4: completed_at null after recovery")
        good = db.execute(
            "SELECT COUNT(*) AS c FROM periodic_review_memos "
            "WHERE periodic_review_id = ? AND status = 'generated'", (rid,)
        ).fetchone()
        _assert(good["c"] >= 1, "5: no generated memo after recovery")
        cycles2 = db.execute(
            "SELECT COUNT(*) AS c FROM periodic_reviews WHERE application_id = ?", (app_id,)
        ).fetchone()
        _assert(cycles2["c"] >= 2, "6: next cycle not scheduled after completion")
        results["scenarios"]["recovery"] = "pass"

        results["passed"] = True
    finally:
        # fixture cleanup — never leave probe rows behind
        try:
            if app_id is not None:
                db.execute("DELETE FROM periodic_review_memos WHERE application_id = ?", (app_id,))
                db.execute("DELETE FROM periodic_reviews WHERE application_id = ?", (app_id,))
                db.execute("DELETE FROM applications WHERE id = ?", (app_id,))
                db.commit()
        except Exception as exc:  # pragma: no cover
            results["cleanup_error"] = str(exc)
        print(json.dumps(results, indent=2, default=str))

    if not results["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
