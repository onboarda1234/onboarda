"""BSA-003B durable supervisor human-review persistence coverage."""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import db as db_module
from db import DBConnection
from observability import clear_request_id, set_request_id
from regulated_deletion import RegulatedDeleteDenied, is_regulated_table
from server import cleanup_application_delete_artifacts, regulated_application_evidence
from supervisor import human_review as human_review_module
from supervisor.human_review import HumanReviewService


BACKEND = Path(__file__).resolve().parents[1]
TABLES = {
    "supervisor_human_reviews",
    "supervisor_overrides",
    "supervisor_escalations",
}


def _pipeline(application_id="synthetic-bsa003-app", pipeline_id="pipeline-bsa003"):
    return SimpleNamespace(
        application_id=application_id,
        pipeline_id=pipeline_id,
        case_aggregate=SimpleNamespace(
            ai_recommendation="reject",
            aggregate_confidence=0.72,
            ai_risk_level="HIGH",
        ),
        rule_evaluations=[],
        contradictions=[],
    )


def _table_names(db):
    return {
        row["name"]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def test_fresh_schema_and_repeated_init_create_three_portable_tables(temp_db):
    db_module.init_db()
    db_module.init_db()
    db = db_module.get_db()
    try:
        assert TABLES <= _table_names(db)
        for table in TABLES:
            assert db.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        db.close()

    service_source = inspect.getsource(human_review_module)
    migration_source = (
        BACKEND / "migrations" / "scripts" /
        "migration_046_supervisor_human_review_persistence.sql"
    ).read_text(encoding="utf-8")
    assert "sqlite3.connect" not in service_source
    assert "datetime('now')" not in service_source
    assert "datetime('now')" not in migration_source
    assert "CURRENT_TIMESTAMP" in db_module._get_postgres_schema()
    assert "CURRENT_TIMESTAMP" in db_module._get_sqlite_schema()


def test_db_path_is_compatibility_only_and_no_arie_db_is_created(tmp_path):
    candidate = tmp_path / "arie.db"
    HumanReviewService(db_path=str(candidate), audit_logger=Mock())
    assert not candidate.exists()


def test_review_and_override_commit_to_shared_db_with_actor_and_request_id(
    temp_db, monkeypatch
):
    monkeypatch.setattr(human_review_module, "_get_db", db_module.get_db)
    audit = Mock()
    service = HumanReviewService(db_path="ignored-local.db", audit_logger=audit)
    set_request_id("req-bsa003-review")
    try:
        review = service.submit_review(
            pipeline_result=_pipeline(),
            reviewer_id="server-co-1",
            reviewer_name="Server CO",
            reviewer_role="co",
            decision="approve",
            decision_reason="Synthetic BSA-003B review",
            override_ai=True,
            override_reason="Synthetic override reason",
        )
    finally:
        clear_request_id()

    db = db_module.get_db()
    try:
        review_row = db.execute(
            "SELECT * FROM supervisor_human_reviews WHERE id=?",
            (review.review_id,),
        ).fetchone()
        override_row = db.execute(
            "SELECT * FROM supervisor_overrides WHERE review_id=?",
            (review.review_id,),
        ).fetchone()
    finally:
        db.close()

    assert review_row["application_id"] == "synthetic-bsa003-app"
    assert review_row["reviewer_id"] == "server-co-1"
    assert review_row["reviewer_name"] == "Server CO"
    assert review_row["reviewer_role"] == "co"
    assert review_row["request_id"] == "req-bsa003-review"
    assert override_row["officer_id"] == "server-co-1"
    assert override_row["officer_name"] == "Server CO"
    assert override_row["officer_role"] == "co"
    assert override_row["request_id"] == "req-bsa003-review"

    public_review = service.get_reviews(application_id="synthetic-bsa003-app")[0]
    public_override = service.get_overrides(application_id="synthetic-bsa003-app")[0]
    assert "request_id" not in public_review
    assert "request_id" not in public_override


def test_escalation_commits_server_actor_and_request_id(temp_db, monkeypatch):
    monkeypatch.setattr(human_review_module, "_get_db", db_module.get_db)
    service = HumanReviewService(db_path=None, audit_logger=Mock())
    set_request_id("req-bsa003-escalation")
    try:
        result = service.escalate_case(
            application_id="synthetic-bsa003-app",
            pipeline_id="pipeline-bsa003",
            escalation_level="senior_compliance",
            reason="Synthetic BSA-003B escalation",
            escalated_by_id="server-sco-1",
            escalated_by="Server SCO",
            escalated_by_role="sco",
        )
    finally:
        clear_request_id()

    db = db_module.get_db()
    try:
        row = db.execute(
            "SELECT * FROM supervisor_escalations WHERE id=?",
            (result["escalation_id"],),
        ).fetchone()
    finally:
        db.close()

    assert row["application_id"] == "synthetic-bsa003-app"
    assert row["escalated_by_id"] == "server-sco-1"
    assert row["escalated_by_name"] == "Server SCO"
    assert row["escalated_by_role"] == "sco"
    assert row["request_id"] == "req-bsa003-escalation"

    public_row = service.get_pending_escalations(
        escalation_level="senior_compliance"
    )[0]
    assert "request_id" not in public_row
    assert "escalated_by_id" not in public_row
    assert "escalated_by_name" not in public_row
    assert "escalated_by_role" not in public_row


class _FailingDB:
    def __init__(self):
        self.rolled_back = False
        self.closed = False

    def execute(self, *_args, **_kwargs):
        raise RuntimeError("synthetic storage failure")

    def commit(self):
        raise AssertionError("commit must not run after failed insert")

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_write_failure_rolls_back_and_propagates(monkeypatch):
    failing = _FailingDB()
    monkeypatch.setattr(human_review_module, "_get_db", lambda: failing)
    service = HumanReviewService(audit_logger=Mock())

    with pytest.raises(RuntimeError, match="synthetic storage failure"):
        service.submit_review(
            pipeline_result=_pipeline(),
            reviewer_id="server-co-1",
            reviewer_name="Server CO",
            reviewer_role="co",
            decision="reject",
            decision_reason="Synthetic failure test",
        )

    assert failing.rolled_back is True
    assert failing.closed is True


def test_override_failure_rolls_back_review_transaction(temp_db, monkeypatch):
    monkeypatch.setattr(human_review_module, "_get_db", db_module.get_db)
    db = db_module.get_db()
    db.executescript(
        """
        CREATE TRIGGER bsa003_fail_override
        BEFORE INSERT ON supervisor_overrides
        BEGIN
            SELECT RAISE(ABORT, 'synthetic override failure');
        END;
        """
    )
    db.commit()
    db.close()

    service = HumanReviewService(audit_logger=Mock())
    try:
        with pytest.raises(Exception, match="synthetic override failure"):
            service.submit_review(
                pipeline_result=_pipeline(
                    application_id="synthetic-bsa003-atomic",
                    pipeline_id="pipeline-bsa003-atomic",
                ),
                reviewer_id="server-co-1",
                reviewer_name="Server CO",
                reviewer_role="co",
                decision="approve",
                decision_reason="Synthetic atomicity test",
                override_ai=True,
                override_reason="Synthetic atomic override",
            )

        db = db_module.get_db()
        try:
            row = db.execute(
                "SELECT id FROM supervisor_human_reviews WHERE application_id=?",
                ("synthetic-bsa003-atomic",),
            ).fetchone()
            assert row is None
        finally:
            db.close()
    finally:
        db = db_module.get_db()
        db.execute("DROP TRIGGER IF EXISTS bsa003_fail_override")
        db.commit()
        db.close()


def test_read_failure_propagates_instead_of_returning_fake_empty(monkeypatch):
    failing = _FailingDB()
    monkeypatch.setattr(human_review_module, "_get_db", lambda: failing)
    service = HumanReviewService(audit_logger=Mock())

    with pytest.raises(RuntimeError, match="synthetic storage failure"):
        service.get_reviews(application_id="synthetic-bsa003-app")

    assert failing.closed is True


def test_application_delete_preflight_and_direct_guards_cover_new_tables(temp_db):
    assert all(is_regulated_table(table) for table in TABLES)
    db = db_module.get_db()
    try:
        db.execute(
            """
            INSERT INTO supervisor_escalations
            (id, pipeline_id, application_id, escalation_source,
             escalation_level, priority, reason, status,
             escalated_by_id, escalated_by_name, escalated_by_role)
            VALUES (?, ?, ?, 'manual', 'senior_compliance', 'high', ?, 'pending', ?, ?, ?)
            """,
            (
                "esc-p12-bsa003", "pipeline-p12-bsa003", "app-p12-bsa003",
                "Synthetic regulated evidence", "server-sco-1", "Server SCO", "sco",
            ),
        )
        db.commit()

        evidence = regulated_application_evidence(
            db, "app-p12-bsa003", "ARF-P12-BSA003"
        )
        assert evidence["supervisor_escalations"] == 1
        with pytest.raises(RegulatedDeleteDenied):
            cleanup_application_delete_artifacts(
                db, "app-p12-bsa003", "ARF-P12-BSA003"
            )
    finally:
        db.close()

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    guarded = DBConnection(
        raw, is_postgres=False, database_identity="/runtime/onboarda.db"
    )
    guarded.execute("CREATE TABLE supervisor_human_reviews (id TEXT PRIMARY KEY)")
    guarded.execute("INSERT INTO supervisor_human_reviews VALUES ('review-p12')")
    with pytest.raises(RegulatedDeleteDenied):
        guarded.execute("DELETE FROM supervisor_human_reviews WHERE id='review-p12'")
    guarded.close()


def test_app_727_audit_hash_contract_is_not_imported_into_review_persistence():
    source = inspect.getsource(human_review_module)
    assert "append_audit_log" not in source
    assert "hash_version" not in source
    assert "INSERT INTO audit_log" not in source
