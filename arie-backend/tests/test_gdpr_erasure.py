"""Tests for the H2 draft GDPR erasure executor (gdpr_erasure.py).

Covers the safe, deterministic behaviour: retention classification, dry-run
safety, actual anonymisation of out-of-retention subjects, refusal of records
inside the AML retention window, and the override guard. The module is a draft
and is not wired into any runtime path.
"""
from datetime import datetime, timedelta, timezone


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def _ensure_policies(db):
    """H2B is fail-closed on retention policy; guarantee the ones these tests
    need (the shared conftest DB can lack them — see H2B test module)."""
    try:
        from db import _DEFAULT_RETENTION_POLICIES as pols
    except Exception:
        pols = [("client_pii", 2555, "AML", "", False, True),
                ("application_data", 2555, "Regulatory", "", False, True)]
    for pol in pols:
        db.execute(
            "INSERT OR IGNORE INTO data_retention_policies "
            "(data_category, retention_days, legal_basis, description, auto_purge, requires_review) "
            "VALUES (?,?,?,?,?,?)",
            pol,
        )
    db.commit()


def _seed(db, client_id, app_id, ref, decided_days_ago):
    _ensure_policies(db)
    db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name, status) VALUES (?, ?, ?, ?, 'active')",
        (client_id, f"{client_id}@example.com", "hash", "Acme Co"),
    )
    db.execute(
        "INSERT INTO applications (id, ref, client_id, company_name, status, decided_at) VALUES (?, ?, ?, ?, ?, ?)",
        (app_id, ref, client_id, "Acme Co", "approved", _iso(decided_days_ago)),
    )
    db.execute(
        "INSERT INTO directors (id, application_id, full_name, first_name, last_name, nationality, date_of_birth, residential_address) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (f"{app_id}-d1", app_id, "John Doe", "John", "Doe", "GB", "1980-01-01", "1 High Street"),
    )
    db.commit()


def test_plan_classifies_retention(db):
    import gdpr_erasure as ge
    _seed(db, "c-old", "a-old", "R-OLD", 4000)   # ~11 years ago -> erasable
    _seed(db, "c-new", "a-new", "R-NEW", 30)     # 30 days ago -> retained

    plan_old = ge.plan_subject_erasure(db, "c-old")
    assert plan_old["fully_erasable"] is True
    assert plan_old["erasable_application_ids"] == ["a-old"]

    plan_new = ge.plan_subject_erasure(db, "c-new")
    assert plan_new["fully_erasable"] is False
    assert plan_new["retained_application_ids"] == ["a-new"]


def test_dry_run_makes_no_changes(db):
    import gdpr_erasure as ge
    _seed(db, "c-dry", "a-dry", "R-DRY", 4000)
    res = ge.execute_subject_erasure(db, "c-dry", requested_by="admin", dry_run=True)
    assert res["action"] == "dry_run"
    assert res["changes_made"] is False
    d = db.execute("SELECT full_name FROM directors WHERE application_id = 'a-dry'").fetchone()
    assert d["full_name"] == "John Doe"


def test_execute_erases_out_of_retention_subject(db):
    import gdpr_erasure as ge
    _seed(db, "c-ex", "a-ex", "R-EX", 4000)
    res = ge.execute_subject_erasure(db, "c-ex", requested_by="admin", dry_run=False)
    db.commit()
    assert res["action"] == "executed"
    assert "a-ex" in res["erased_application_ids"]

    d = db.execute("SELECT full_name, residential_address FROM directors WHERE application_id = 'a-ex'").fetchone()
    assert d["full_name"] == "[ERASED]"
    assert d["residential_address"] == "[ERASED]"

    c = db.execute("SELECT email FROM clients WHERE id = 'c-ex'").fetchone()
    assert c["email"].startswith("erased+")

    log = db.execute(
        "SELECT action FROM gdpr_erasure_log WHERE client_id = 'c-ex' AND action = 'erased'"
    ).fetchone()
    assert log is not None


def test_retained_subject_refused_and_pii_preserved(db):
    import gdpr_erasure as ge
    _seed(db, "c-ret", "a-ret", "R-RET", 30)
    res = ge.execute_subject_erasure(db, "c-ret", requested_by="admin", dry_run=False)
    db.commit()
    assert "a-ret" in res["retained_refused_application_ids"]

    d = db.execute("SELECT full_name FROM directors WHERE application_id = 'a-ret'").fetchone()
    assert d["full_name"] == "John Doe"  # AML retention: PII must remain intact

    log = db.execute(
        "SELECT note FROM gdpr_erasure_log WHERE client_id = 'c-ret' AND action = 'retained_refused'"
    ).fetchone()
    assert log is not None


def test_override_retention_requires_reason(db):
    import gdpr_erasure as ge
    _seed(db, "c-ov", "a-ov", "R-OV", 30)
    res = ge.execute_subject_erasure(
        db, "c-ov", requested_by="admin", dry_run=False,
        override_retention=True, override_reason="",
    )
    assert res["action"] == "refused"
    d = db.execute("SELECT full_name FROM directors WHERE application_id = 'a-ov'").fetchone()
    assert d["full_name"] == "John Doe"


def test_override_without_reason_refusal_is_logged(db):
    """The refused override-without-reason attempt must leave an audit trail."""
    import gdpr_erasure as ge
    _seed(db, "c-ovlog", "a-ovlog", "R-OVLOG", 30)
    res = ge.execute_subject_erasure(
        db, "c-ovlog", requested_by="admin", dry_run=False,
        override_retention=True, override_reason="   ",
    )
    db.commit()
    assert res["action"] == "refused"
    log = db.execute(
        "SELECT action, retention_overridden FROM gdpr_erasure_log "
        "WHERE client_id = 'c-ovlog' AND action = 'refused_override_without_reason'"
    ).fetchone()
    assert log is not None
    assert log["retention_overridden"] in (1, True)
