"""DCI-104 batch 2: foreign-key index coverage.

These FK columns had no covering index, so joins and ON DELETE CASCADE
integrity scans over them were full table scans. Indexes are additive (never
change query results). This guards that every batch-2 index is created by the
schema migrations on a fresh DB.
"""

EXPECTED_INDEXES = {
    "idx_screening_reviews_application_id": "screening_reviews",
    "idx_client_sessions_application_id": "client_sessions",
    "idx_verification_jobs_application_id": "verification_jobs",
    "idx_sar_reports_alert_id": "sar_reports",
    "idx_aer_linked_document_id": "application_enhanced_requirements",
    "idx_supervisor_human_reviews_escalation": "supervisor_human_reviews",
    "idx_data_purge_log_retention_policy": "data_purge_log",
    "idx_documents_superseded_by": "documents",
}


def test_all_batch2_fk_indexes_exist(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    ).fetchall()
    names = {row["name"] for row in rows}
    missing = sorted(set(EXPECTED_INDEXES) - names)
    assert not missing, f"missing FK indexes: {missing}"


def test_migration_050_ledger_file_present():
    from pathlib import Path

    p = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "scripts"
        / "migration_050_fk_index_coverage_batch2.sql"
    )
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    for idx in EXPECTED_INDEXES:
        assert idx in text, f"{idx} not in migration_050 ledger"
