from datetime import datetime, timedelta, timezone
import sqlite3


def test_canonical_dashboard_stats_reconcile_counts_lanes_risk_and_fixtures():
    from server import _canonical_dashboard_stats

    client_id = "pr2_dashboard_truth_client"
    app_ids = (
        "app_pr2_truth_approved",
        "app_pr2_truth_declined",
        "app_pr2_truth_inprogress",
        "app_pr2_truth_edd",
        "app_pr2_truth_unknown",
        "f1xed_pr2_truth_fixture",
    )

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE applications (
            id TEXT PRIMARY KEY,
            ref TEXT,
            client_id TEXT,
            company_name TEXT,
            country TEXT,
            sector TEXT,
            entity_type TEXT,
            status TEXT,
            risk_level TEXT,
            final_risk_level TEXT,
            risk_score REAL,
            onboarding_lane TEXT,
            created_at TEXT,
            submitted_at TEXT,
            decided_at TEXT,
            is_fixture INTEGER,
            assigned_to TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            full_name TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE edd_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT,
            client_name TEXT,
            risk_level TEXT,
            risk_score REAL,
            trigger_source TEXT,
            trigger_notes TEXT,
            stage TEXT,
            assigned_officer TEXT,
            decided_at TEXT
        )
        """
    )
    db.execute(
        "DELETE FROM edd_cases WHERE application_id IN (?, ?, ?, ?, ?, ?)",
        app_ids,
    )
    db.execute(
        "DELETE FROM applications WHERE id IN (?, ?, ?, ?, ?, ?)",
        app_ids,
    )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0)
    previous_month = month_start - timedelta(days=2)
    previous_month_created = previous_month.replace(day=15, hour=9, minute=0, second=0)

    rows = (
        (
            "app_pr2_truth_approved", "ARF-PR2-TRUTH-APPROVED", client_id, "PR2 Truth Approved Ltd",
            "Mauritius", "Technology", "SME", "approved", "LOW", "HIGH", 81.0,
            "Fast Lane", previous_month_created.strftime("%Y-%m-%d %H:%M:%S"),
            (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"), 0,
        ),
        (
            "app_pr2_truth_declined", "ARF-PR2-TRUTH-DECLINED", client_id, "PR2 Truth Declined Ltd",
            "Mauritius", "Retail", "SME", "declined", "LOW", None, 22.0,
            "Standard Review", previous_month_created.strftime("%Y-%m-%d %H:%M:%S"),
            (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"), 0,
        ),
        (
            "app_pr2_truth_inprogress", "ARF-PR2-TRUTH-INPROGRESS", client_id, "PR2 Truth Pending Ltd",
            "Mauritius", "Finance", "SME", "pricing_review", "MEDIUM", None, 51.0,
            "standard", now.strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"), None, 0,
        ),
        (
            "app_pr2_truth_edd", "ARF-PR2-TRUTH-EDD", client_id, "PR2 Truth Escalated Ltd",
            "Mauritius", "Gaming", "SME", "edd_required", "LOW", "VERY_HIGH", 93.0,
            "edd", now.strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"), None, 0,
        ),
        (
            "app_pr2_truth_unknown", "ARF-PR2-TRUTH-UNKNOWN", client_id, "PR2 Truth Unknown Ltd",
            "Mauritius", "Services", "SME", "draft", None, None, None,
            None, now.strftime("%Y-%m-%d %H:%M:%S"),
            None, None, 0,
        ),
        (
            "f1xed_pr2_truth_fixture", "ARF-PR2-TRUTH-FIXTURE", client_id, "PR2 Truth Fixture Ltd",
            "Mauritius", "Technology", "SME", "approved", "LOW", "LOW", 10.0,
            "EDD", now.strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"), 1,
        ),
    )
    for row in rows:
        db.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status,
             risk_level, final_risk_level, risk_score, onboarding_lane,
             created_at, submitted_at, decided_at, is_fixture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )

    db.execute(
        """
        INSERT INTO edd_cases
        (application_id, client_name, risk_level, risk_score, trigger_source, trigger_notes, stage, assigned_officer)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("app_pr2_truth_edd", "PR2 Truth Escalated Ltd", "VERY_HIGH", 93.0, "pr2_test", "live case", "triggered", "admin001"),
    )
    db.execute(
        """
        INSERT INTO edd_cases
        (application_id, client_name, risk_level, risk_score, trigger_source, trigger_notes, stage, assigned_officer)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("f1xed_pr2_truth_fixture", "PR2 Truth Fixture Ltd", "LOW", 10.0, "pr2_test", "fixture case", "triggered", "admin001"),
    )
    db.commit()

    user = {"type": "client", "sub": client_id}
    stats = _canonical_dashboard_stats(db, user, show_fixtures=False)
    include_stats = _canonical_dashboard_stats(db, user, show_fixtures=True)

    assert stats["canonical_view"] == "dashboard_metrics_v2"
    assert stats["metrics"]["total_applications"] == {"value": 5, "kind": "applications"}
    assert stats["metrics"]["in_progress_applications"] == {"value": 2, "kind": "applications"}
    assert stats["metrics"]["approved_this_month"]["value"] == 1
    assert stats["metrics"]["approved_this_month"]["timestamp_field"] == "decided_at"
    assert stats["metrics"]["rejected_declined"] == {"value": 1, "kind": "applications"}
    assert stats["metrics"]["edd_in_progress"]["value"] == 1
    assert stats["metrics"]["edd_in_progress"]["kind"] == "edd_cases"
    assert stats["metrics"]["avg_processing_time"]["available"] is True
    assert stats["metrics"]["avg_processing_time"]["sample_size"] == 2

    assert stats["risk_distribution"] == {
        "LOW": 1,
        "MEDIUM": 1,
        "HIGH": 1,
        "VERY_HIGH": 1,
        "UNKNOWN": 1,
        "total": 5,
    }
    assert stats["lane_distribution"] == {
        "fast_lane": 1,
        "standard_review": 2,
        "enhanced_due_diligence": 1,
        "unknown": 1,
        "total": 5,
    }

    assert include_stats["metrics"]["total_applications"]["value"] == 6
    assert include_stats["metrics"]["edd_in_progress"]["value"] == 2
    assert include_stats["lane_distribution"]["enhanced_due_diligence"] == 2
