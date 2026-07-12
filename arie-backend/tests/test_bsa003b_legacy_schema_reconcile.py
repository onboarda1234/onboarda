"""Regression coverage for BSA-003B legacy supervisor schema reconciliation."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]

EXPECTED_COLUMNS = {
    "supervisor_escalations": {
        "id", "pipeline_id", "application_id", "escalation_source",
        "source_id", "escalation_level", "priority", "reason",
        "context_json", "assigned_to", "status", "sla_deadline",
        "resolved_at", "escalated_by_id", "escalated_by_name",
        "escalated_by_role", "request_id", "created_at",
    },
    "supervisor_human_reviews": {
        "id", "pipeline_id", "application_id", "escalation_id",
        "review_type", "reviewer_id", "reviewer_name", "reviewer_role",
        "ai_recommendation", "ai_confidence", "ai_risk_level",
        "rules_recommendation", "rules_triggered", "contradictions_json",
        "decision", "decision_reason", "risk_level_assigned", "conditions",
        "follow_up_required", "follow_up_details", "is_ai_override",
        "override_reason", "review_started_at", "decision_at", "request_id",
        "created_at",
    },
    "supervisor_overrides": {
        "id", "review_id", "application_id", "agent_type", "override_type",
        "original_value", "override_value", "reason", "officer_id",
        "officer_name", "officer_role", "approver_id", "approver_name",
        "approved_at", "request_id", "created_at",
    },
}

EXPECTED_INDEXES = {
    "idx_sup_escalations_app",
    "idx_sup_escalations_pipeline",
    "idx_sup_escalations_status_created",
    "idx_sup_escalations_level_status",
    "idx_sup_reviews_app",
    "idx_sup_reviews_pipeline",
    "idx_sup_reviews_reviewer",
    "idx_sup_reviews_decision_at",
    "idx_sup_overrides_app",
    "idx_sup_overrides_review",
    "idx_sup_overrides_created",
}

LEGACY_DDL = """
CREATE TABLE supervisor_human_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    reviewer_name TEXT,
    reviewer_role TEXT,
    decision TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    risk_level_assigned TEXT,
    conditions TEXT,
    follow_up_required INTEGER DEFAULT 0,
    follow_up_details TEXT,
    override_ai INTEGER DEFAULT 0,
    override_reason TEXT,
    reviewed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_supervisor_reviews_pipeline
    ON supervisor_human_reviews(pipeline_id);

CREATE TABLE supervisor_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    override_type TEXT NOT NULL,
    ai_recommendation TEXT,
    officer_decision TEXT,
    officer_id TEXT NOT NULL,
    officer_name TEXT,
    reason TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE supervisor_escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    pipeline_id TEXT NOT NULL,
    escalation_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    escalated_by TEXT,
    escalated_by_role TEXT,
    assigned_to TEXT,
    status TEXT DEFAULT 'pending',
    resolved_at TEXT,
    resolved_by TEXT,
    resolution_notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_supervisor_escalations_app
    ON supervisor_escalations(application_id);
"""


def _run(script: str, env: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"subprocess failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_sqlite_legacy_tables_reconcile_before_indexes_and_runtime(tmp_path):
    """The exact observed old shape upgrades, repeats, and accepts UUID writes."""
    env = os.environ.copy()
    env["DATABASE_URL"] = ""
    env["ENVIRONMENT"] = "development"
    env["DB_PATH"] = str(tmp_path / "legacy-supervisor.db")
    env["LEGACY_DDL"] = LEGACY_DDL

    _run(
        """
        import os
        from types import SimpleNamespace
        from unittest.mock import Mock

        import db
        from supervisor.human_review import HumanReviewService

        legacy = db.get_db()
        legacy.executescript(os.environ["LEGACY_DDL"])
        legacy.commit()
        legacy.close()

        db.init_db()
        db.init_db()

        expected_columns = %r
        expected_indexes = %r
        check = db.get_db()
        for table, expected in expected_columns.items():
            info = check.execute(f"PRAGMA table_info({table})").fetchall()
            columns = {row["name"] for row in info}
            assert expected <= columns, (table, expected - columns)
            id_info = next(row for row in info if row["name"] == "id")
            assert id_info["type"].upper() == "TEXT", (table, id_info)
        indexes = {
            row["name"]
            for table in expected_columns
            for row in check.execute(f"PRAGMA index_list({table})").fetchall()
        }
        assert expected_indexes <= indexes
        check.close()

        pipeline = SimpleNamespace(
            application_id="synthetic-bsa003-hotfix",
            pipeline_id="pipeline-bsa003-hotfix",
            case_aggregate=SimpleNamespace(
                ai_recommendation="reject",
                aggregate_confidence=0.72,
                ai_risk_level="HIGH",
            ),
            rule_evaluations=[],
            contradictions=[],
        )
        service = HumanReviewService(audit_logger=Mock())
        review = service.submit_review(
            pipeline_result=pipeline,
            reviewer_id="server-co-hotfix",
            reviewer_name="Server CO Hotfix",
            reviewer_role="co",
            decision="approve",
            decision_reason="Synthetic legacy reconciliation",
            override_ai=True,
            override_reason="Synthetic override",
        )
        escalation = service.escalate_case(
            application_id=pipeline.application_id,
            pipeline_id=pipeline.pipeline_id,
            escalation_level="senior_compliance",
            reason="Synthetic legacy escalation",
            escalated_by_id="server-sco-hotfix",
            escalated_by="Server SCO Hotfix",
            escalated_by_role="sco",
        )

        check = db.get_db()
        assert check.execute(
            "SELECT id FROM supervisor_human_reviews WHERE id=?",
            (review.review_id,),
        ).fetchone()
        assert check.execute(
            "SELECT review_id FROM supervisor_overrides WHERE review_id=?",
            (review.review_id,),
        ).fetchone()
        assert check.execute(
            "SELECT id FROM supervisor_escalations WHERE id=?",
            (escalation["escalation_id"],),
        ).fetchone()
        check.close()
        """ % (EXPECTED_COLUMNS, EXPECTED_INDEXES),
        env,
    )


def test_fresh_sqlite_schema_keeps_expected_contract_and_is_idempotent(tmp_path):
    env = os.environ.copy()
    env["DATABASE_URL"] = ""
    env["ENVIRONMENT"] = "development"
    env["DB_PATH"] = str(tmp_path / "fresh-supervisor.db")

    _run(
        """
        import db

        db.init_db()
        db.init_db()
        expected_columns = %r
        expected_indexes = %r
        check = db.get_db()
        for table, expected in expected_columns.items():
            columns = {
                row["name"]
                for row in check.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert expected <= columns
        indexes = {
            row["name"]
            for table in expected_columns
            for row in check.execute(f"PRAGMA index_list({table})").fetchall()
        }
        assert expected_indexes <= indexes
        check.close()
        """ % (EXPECTED_COLUMNS, EXPECTED_INDEXES),
        env,
    )


def test_postgres_observed_legacy_shape_reconciles_before_schema_indexes():
    dsn = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")
    if not dsn:
        pytest.skip("PostgreSQL test DSN not configured")

    env = os.environ.copy()
    env["DATABASE_URL"] = dsn
    env["ENVIRONMENT"] = "development"
    env["LEGACY_DDL"] = LEGACY_DDL

    _run(
        """
        import os
        import psycopg2

        import db

        raw = psycopg2.connect(os.environ["DATABASE_URL"])
        cur = raw.cursor()
        cur.execute("DROP TABLE IF EXISTS supervisor_overrides CASCADE")
        cur.execute("DROP TABLE IF EXISTS supervisor_human_reviews CASCADE")
        cur.execute("DROP TABLE IF EXISTS supervisor_escalations CASCADE")
        cur.execute(os.environ["LEGACY_DDL"].replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
        ))
        raw.commit()
        raw.close()

        db.init_db()
        db.init_db()

        expected_columns = %r
        expected_indexes = %r
        check = db.get_db()
        for table, expected in expected_columns.items():
            rows = check.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name=?",
                (table,),
            ).fetchall()
            columns = {row["column_name"] for row in rows}
            assert expected <= columns, (table, expected - columns)
            id_type = next(
                row["data_type"] for row in rows if row["column_name"] == "id"
            )
            assert id_type == "text", (table, id_type)
        indexes = {
            row["indexname"]
            for row in check.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname=current_schema() AND tablename IN "
                "('supervisor_human_reviews','supervisor_overrides','supervisor_escalations')"
            ).fetchall()
        }
        assert expected_indexes <= indexes
        nullable = check.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema=current_schema() "
            "AND table_name='supervisor_overrides' AND column_name='pipeline_id'"
        ).fetchone()
        assert nullable["is_nullable"] == "YES"
        check.close()
        """ % (EXPECTED_COLUMNS, EXPECTED_INDEXES),
        env,
    )


def test_hotfix_keeps_portable_timestamps_and_marker_convention():
    db_source = (BACKEND / "db.py").read_text(encoding="utf-8")
    marker = (
        BACKEND / "migrations" / "scripts" /
        "migration_046_supervisor_human_review_persistence.sql"
    ).read_text(encoding="utf-8")
    assert "datetime('now')" not in marker
    assert "SELECT 1" in marker
    hotfix = db_source[db_source.index(
        "def _ensure_supervisor_human_review_persistence_schema"
    ):db_source.index("def _ensure_company_registry_schema")]
    assert "datetime('now')" not in hotfix
    assert "CURRENT_TIMESTAMP" in hotfix
