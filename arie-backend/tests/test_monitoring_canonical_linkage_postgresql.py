"""PostgreSQL-backed canonical Monitoring linkage contract tests."""

from __future__ import annotations

import json
import os
from datetime import timedelta

import pytest

import monitoring_alert_linkage as linkage
from scripts.qa.monitoring_canonical_linkage_audit import ReadOnlyPostgresAdapter


def _postgres_dsn() -> str | None:
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get(
        "DATABASE_URL_TEST"
    )


@pytest.mark.skipif(not _postgres_dsn(), reason="No disposable PostgreSQL test DSN")
def test_postgres_json_projection_and_plan_are_read_only_and_repeatable():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    connection = psycopg2.connect(_postgres_dsn())
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMP TABLE clients (id TEXT PRIMARY KEY) ON COMMIT PRESERVE ROWS;
                CREATE TEMP TABLE applications (
                    id TEXT PRIMARY KEY,
                    ref TEXT,
                    client_id TEXT,
                    company_name TEXT
                ) ON COMMIT PRESERVE ROWS;
                CREATE TEMP TABLE monitoring_alerts (
                    id BIGINT PRIMARY KEY,
                    application_id TEXT,
                    alert_type TEXT,
                    source_reference TEXT,
                    provider TEXT,
                    case_identifier TEXT,
                    status TEXT DEFAULT 'open',
                    resolved_at TIMESTAMPTZ
                ) ON COMMIT PRESERVE ROWS;
                CREATE TEMP TABLE monitoring_alert_evidence (
                    id BIGSERIAL PRIMARY KEY,
                    monitoring_alert_id BIGINT,
                    application_id TEXT,
                    provider TEXT,
                    case_identifier TEXT,
                    alert_identifier TEXT,
                    match_identifier TEXT,
                    risk_identifier TEXT,
                    profile_identifier TEXT,
                    evidence_status TEXT,
                    fetched_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ
                ) ON COMMIT PRESERVE ROWS;
                CREATE TEMP TABLE screening_reviews (
                    id BIGSERIAL PRIMARY KEY,
                    application_id TEXT,
                    subject_type TEXT,
                    subject_name TEXT,
                    disposition TEXT,
                    disposition_code TEXT,
                    rationale TEXT,
                    notes TEXT,
                    requires_four_eyes BOOLEAN,
                    reviewer_id TEXT,
                    reviewer_name TEXT,
                    second_reviewer_id TEXT,
                    second_disposition_code TEXT,
                    second_rationale TEXT,
                    second_reviewed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ
                ) ON COMMIT PRESERVE ROWS;
                CREATE TEMP TABLE screening_hit_dispositions (
                    id BIGSERIAL PRIMARY KEY,
                    application_id TEXT,
                    subject_type TEXT,
                    subject_name TEXT,
                    hit_id TEXT,
                    disposition TEXT,
                    created_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ
                ) ON COMMIT PRESERVE ROWS;
                CREATE TEMP TABLE audit_log (
                    id BIGSERIAL PRIMARY KEY,
                    action TEXT,
                    target TEXT,
                    timestamp TIMESTAMPTZ,
                    detail TEXT
                ) ON COMMIT PRESERVE ROWS;
                CREATE TEMP TABLE users (
                    id TEXT PRIMARY KEY,
                    role TEXT,
                    status TEXT
                ) ON COMMIT PRESERVE ROWS;
                CREATE TEMP TABLE screening_reports_normalized (
                    id BIGINT PRIMARY KEY,
                    application_id TEXT,
                    client_id TEXT,
                    provider TEXT,
                    normalized_report_json JSONB,
                    normalization_status TEXT,
                    created_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ
                ) ON COMMIT PRESERVE ROWS
                """
            )
            source = {
                "provider": "complyadvantage",
                "case_identifier": "case-pg-1",
                "alert_identifier": "provider-alert-pg-1",
                "normalized_record_id": 71,
                "screening_subject": {"kind": "entity", "scope": "entity"},
            }
            normalized = {
                "screening_subject_kind": "entity",
                "subject_scope": "entity",
                "screening_subject_person_key": "",
                "provider_specific": {
                    "complyadvantage": {
                        "screening_subject": {
                            "kind": "entity",
                            "scope": "entity",
                            "person_key": "",
                        },
                        "provider_references": {
                            "case_ids": ["case-pg-1"],
                            "alert_ids": ["provider-alert-pg-1"],
                        },
                    }
                },
            }
            cursor.execute("INSERT INTO clients(id) VALUES ('client-pg-1')")
            cursor.execute(
                "INSERT INTO applications(id,ref,client_id,company_name) "
                "VALUES ('app-pg-1','APP-PG-1','client-pg-1','PostgreSQL Contract')"
            )
            cursor.execute(
                "INSERT INTO monitoring_alerts(id,application_id,alert_type,"
                "source_reference,provider,case_identifier) "
                "VALUES (71,'app-pg-1','media',%s,'complyadvantage','case-pg-1')",
                (json.dumps(source, sort_keys=True),),
            )
            cursor.execute(
                "INSERT INTO monitoring_alert_evidence("
                "monitoring_alert_id,application_id,provider,case_identifier,"
                "alert_identifier,match_identifier,evidence_status,fetched_at,created_at) "
                "VALUES (71,'app-pg-1','complyadvantage','case-pg-1',"
                "'provider-alert-pg-1','match-pg-1','fetched',NOW(),NOW())"
            )
            cursor.execute(
                "INSERT INTO screening_reports_normalized("
                "id,application_id,client_id,provider,normalized_report_json,"
                "normalization_status,created_at,updated_at) "
                "VALUES (71,'app-pg-1','client-pg-1','complyadvantage',%s,"
                "'success',NOW(),NOW())",
                (json.dumps(normalized, sort_keys=True),),
            )
            cursor.execute(
                "SELECT CURRENT_TIMESTAMP AS transaction_at, "
                "date_trunc('second', clock_timestamp()) AS maker_event_at"
            )
            times = cursor.fetchone()
            transaction_at = times[0]
            maker_event_at = times[1]
            checker_event_at = maker_event_at + timedelta(seconds=1)
            cursor.execute(
                "INSERT INTO users(id,role,status) VALUES "
                "('maker-pg','co','active'),('checker-pg','sco','active')"
            )
            cursor.execute(
                """
                INSERT INTO screening_reviews(
                    application_id,subject_type,subject_name,disposition,
                    disposition_code,rationale,requires_four_eyes,reviewer_id,
                    second_reviewer_id,second_disposition_code,second_rationale,
                    second_reviewed_at,created_at,updated_at
                ) VALUES (
                    'app-pg-1','entity','PostgreSQL Contract','cleared',
                    'false_positive_cleared','Maker rationale',TRUE,'maker-pg',
                    'checker-pg','false_positive_cleared','Checker rationale',
                    %s,%s,%s
                )
                """,
                (checker_event_at, transaction_at, transaction_at),
            )
            common_audit = {
                "application_id": "app-pg-1",
                "subject_name": "PostgreSQL Contract",
                "subject_type": "entity",
                "disposition_code": "false_positive_cleared",
                "case_identifier": "case-pg-1",
                "provider": "complyadvantage",
                "requires_four_eyes": True,
                "provider_references": {
                    "case_ids": ["case-pg-1"],
                    "alert_ids": ["provider-alert-pg-1"],
                },
            }
            maker_audit = {
                **common_audit,
                "actor": "maker-pg",
                "actor_role": "co",
                "rationale": "Maker rationale",
                "four_eyes_status": "second_review_required",
                "timestamp": maker_event_at.isoformat().replace("+00:00", "Z"),
            }
            checker_audit = {
                **common_audit,
                "actor": "checker-pg",
                "actor_role": "sco",
                "rationale": "Checker rationale",
                "four_eyes_status": "second_review_complete",
                "timestamp": checker_event_at.isoformat().replace("+00:00", "Z"),
            }
            cursor.execute(
                "INSERT INTO audit_log(action,target,timestamp,detail) "
                "VALUES ('Screening Review','APP-PG-1',%s,%s),"
                "('Screening Review','APP-PG-1',%s,%s)",
                (
                    transaction_at,
                    json.dumps(maker_audit, sort_keys=True),
                    transaction_at,
                    json.dumps(checker_audit, sort_keys=True),
                ),
            )
        connection.commit()
        connection.set_session(
            readonly=True,
            autocommit=False,
            isolation_level="REPEATABLE READ",
        )
        db = ReadOnlyPostgresAdapter(connection, RealDictCursor)

        result = linkage.resolve_alert_linkage(db, 71)
        first = linkage.build_linkage_plan(db)
        second = linkage.build_linkage_plan(db)

        assert result["linkage_status"] == "linked"
        assert result["subject"] == {
            "id": "app-pg-1",
            "person_key": None,
            "type": "entity",
            "scope": "entity",
        }
        assert result["screening_case"]["linked_snapshot_id"] == 71
        assert result["screening_case"]["current_snapshot_id"] == 71
        assert result["screening_case"]["snapshot_role"] == "current"
        assert result["owner"]["state"]["key"] == "resolved_in_screening_review"
        assert result["owner"]["state"]["audit_confirmed"] is True
        assert first["counts"]["linked_correctly"] == 1
        assert first["counts"]["data_changes_planned"] == 0
        assert first["apply_supported"] is False
        assert first["fingerprint"] == second["fingerprint"]
        assert linkage.plan_without_timestamp(first) == linkage.plan_without_timestamp(
            second
        )
        assert connection.info.transaction_status != 0
    finally:
        connection.rollback()
        connection.close()
