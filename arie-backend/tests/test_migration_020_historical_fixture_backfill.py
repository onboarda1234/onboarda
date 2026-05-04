from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._migration_idempotency_helpers import fresh_migration_db


MIGRATION_VERSION = "020"
MIGRATION_FILE = "migration_020_historical_fixture_backfill.sql"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "migrations" / "scripts" / MIGRATION_FILE
)

TARGETS = (
    ("ARF-2026-900031", "PHASE4 Closeout Runtime 20260503160321 Ltd"),
    ("ARF-2026-900030", "PHASE4 Closeout Runtime 20260503160217 Ltd"),
    ("ARF-2026-900029", "PHASE2 Postdeploy Validation 20260503T122058Z Ltd"),
    ("ARF-2026-900028", "PHASE2 Diagnosis 20260503T094527Z Ltd"),
    ("ARF-2026-900027", "PHASE1 Memo Truth Smoke 1777801085 Ltd"),
    ("ARF-2026-900026", "PHASE1 Memo Truth Smoke 1777801021 Ltd"),
    ("ARF-2026-900025", "PHASE1 Memo Truth Smoke 1777800962 Ltd"),
    ("ARF-2026-900024", "PHASE1 Memo Truth Smoke 1777800913 Ltd"),
    ("ARF-2026-900023", "PHASE0 Baseline Audit 1777793617 Ltd"),
    ("ARF-2026-900022", "D2 Verify Probe Ltd"),
    ("ARF-2026-900021", "AUDIT May2 Runtime 1777708928 Ltd"),
    ("ARF-2026-900020", "AUDIT May2 Runtime 1777688957 Ltd"),
    ("ARF-2026-900019", "AUDIT Runtime Upload 1777663856 Ltd"),
    ("ARF-2026-900018", "Codex RMI Smoke 1777639540 Ltd"),
    ("ARF-2026-900017", "Codex Phase1C Smoke 1777617157 Ltd"),
    ("ARF-2026-900016", "Codex Resume Smoke 1777617050 Ltd"),
    ("ARF-2026-900015", "E2E Test Corp 1777617014"),
    ("ARF-2026-900014", "QA E2E Test 1 Standard Trading Ltd"),
    ("ARF-2026-900013", "test"),
    ("ARF-2026-100470", "Priority C QA Validation Ltd"),
    ("ARF-2026-100469", "QA Audit Crypto Payments Ltd"),
    ("ARF-2026-100468", "QA Audit MU SME Ltd"),
    ("ARF-2026-100451", "EntityType Validator 31553 Ltd"),
    ("ARF-2026-100447", "Phase2 Validator Delta"),
    ("ARF-2026-100446", "Phase2 Validator Ltd"),
    ("ARF-2026-100422", "Staging E2E Corp"),
)


def _rewind_020(db):
    db.execute("DELETE FROM schema_version WHERE version = ?", (MIGRATION_VERSION,))
    db.commit()


def _insert_application(db, app_id: str, ref: str, company_name: str):
    db.execute(
        """
        INSERT INTO applications
            (id, ref, company_name, country, sector, entity_type, status, is_fixture)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            ref,
            company_name,
            "Mauritius",
            "fintech",
            "company",
            "in_review",
            0,
        ),
    )


def _fixture_values(db):
    rows = db.execute(
        "SELECT ref, is_fixture FROM applications WHERE ref IN (%s)"
        % ",".join(["?"] * len(TARGETS)),
        tuple(ref for ref, _name in TARGETS),
    ).fetchall()
    return {row["ref"]: row["is_fixture"] for row in rows}


def _audit_count(db):
    return db.execute(
        """
        SELECT COUNT(*) AS count
        FROM audit_log
        WHERE action = ?
          AND target = ?
        """,
        ("Fixture Backfill", "migration:020_historical_fixture_backfill"),
    ).fetchone()["count"]


def test_migration_020_marks_exact_historical_fixture_pairs_and_is_idempotent(
    tmp_path,
    monkeypatch,
):
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        _rewind_020(db)
        for idx, (ref, company_name) in enumerate(TARGETS, start=1):
            _insert_application(db, f"day1fix{idx:08d}", ref, company_name)
        db.commit()

        from migrations.runner import run_all_migrations_with_connection

        assert run_all_migrations_with_connection(db) == 1
        assert set(_fixture_values(db).values()) == {1}
        assert _audit_count(db) == 1

        # Direct re-execution bypasses schema_version. The SQL must remain safe.
        db.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
        db.commit()
        assert set(_fixture_values(db).values()) == {1}
        assert _audit_count(db) == 1


def test_migration_020_requires_exact_company_name_match(tmp_path, monkeypatch):
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        _rewind_020(db)
        _insert_application(
            db,
            "day1wrong000001",
            "ARF-2026-900031",
            "Legitimate Same Ref Customer Ltd",
        )
        db.commit()

        from migrations.runner import run_all_migrations_with_connection

        assert run_all_migrations_with_connection(db) == 1
        row = db.execute(
            "SELECT is_fixture FROM applications WHERE id = ?",
            ("day1wrong000001",),
        ).fetchone()
        assert row["is_fixture"] == 0
        assert _audit_count(db) == 0
