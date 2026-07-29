"""Contracts for the read-only Monitoring Alert runtime audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "arie-backend"
    / "scripts"
    / "qa"
    / "monitoring_alert_runtime_audit.py"
)
SPEC = importlib.util.spec_from_file_location("monitoring_alert_runtime_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audit)

PSEUDONYMIZATION_KEY = b"audit-test-key-that-is-never-committed"


def _snapshot(
    *alerts,
    documents=(),
    evidence=(),
    document_requests=(),
    edd=(),
    periodic_reviews=(),
    change_requests=(),
    screening_contexts=(),
):
    return {
        "alerts": list(alerts),
        "documents": list(documents),
        "evidence": list(evidence),
        "document_requests": list(document_requests),
        "edd": list(edd),
        "periodic_reviews": list(periodic_reviews),
        "change_requests": list(change_requests),
        "screening_contexts": list(screening_contexts),
        "audit_trail": [],
    }


def _alert(alert_id, **changes):
    row = {
        "id": alert_id,
        "application_id": f"app-{alert_id}",
        "application_ref": f"REF-{alert_id}",
        "application_is_fixture": False,
        "application_has_test_like_name": False,
        "client_id": f"client-{alert_id}",
        "client_record_exists": True,
        "alert_type": "media",
        "severity": "medium",
        "status": "open",
        "source_reference": (
            '{"provider":"complyadvantage","alert_identifier":"a-1",'
            '"case_identifier":"c-1","subject_scope":"entity"}'
        ),
        "provider": "complyadvantage",
        "case_identifier": "c-1",
        "reviewed_by": None,
        "has_officer_notes": False,
        "has_ai_recommendation": False,
    }
    row.update(changes)
    return row


def test_every_declared_query_is_select_only():
    audit.validate_query_set()
    for sql in audit.QUERY_SET.values():
        first = sql.strip().split(None, 1)[0].lower()
        assert first in {"select", "with", "show"}


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE monitoring_alerts SET status='open'",
        "DELETE FROM monitoring_alerts",
        "INSERT INTO monitoring_alerts(id) VALUES (1)",
        "ALTER TABLE monitoring_alerts ADD COLUMN nope text",
        "WITH changed AS (DELETE FROM monitoring_alerts RETURNING *) SELECT * FROM changed",
    ),
)
def test_sql_guard_rejects_every_mutation_shape(statement):
    with pytest.raises(ValueError):
        audit.assert_select_only(statement)


def test_live_collector_is_read_only_and_rollback_only():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "readonly=True" in text
    assert 'isolation_level="REPEATABLE READ"' in text
    assert 'cursor.execute("SHOW transaction_read_only")' in text
    assert "connection.rollback()" in text
    assert "connection.commit(" not in text
    assert "db.py" not in text


def test_provider_source_is_sanitised_and_free_text_is_not_exported():
    result = audit.analyse_snapshot(_snapshot(_alert(1)))
    source = result["alerts"][0]["source_reference"]
    assert source == {
        "provider": "complyadvantage",
        "alert_identifier": "a-1",
        "case_identifier": "c-1",
        "subject_scope": "entity",
    }
    serialised = str(result)
    assert "free-form officer note text" not in serialised
    assert "free-form AI recommendation text" not in serialised
    assert result["alerts"][0]["resolution_evidence"]["has_officer_notes"] is False
    raw = "person@example.com free-form customer payload"
    redacted = audit._source_reference(raw, PSEUDONYMIZATION_KEY)
    assert raw not in str(redacted)
    assert redacted["kind"] == "opaque_text"
    assert len(redacted["hmac_sha256"]) == 64


@pytest.mark.parametrize("raw", ("12345", "true", "null", '"quoted text"'))
def test_json_scalar_source_reference_is_keyed_without_crashing(raw):
    redacted = audit._source_reference(raw, PSEUDONYMIZATION_KEY)
    assert redacted["kind"] == "opaque_text"
    assert len(redacted["hmac_sha256"]) == 64
    assert raw not in str(redacted)


def test_execute_snapshot_rejects_fingerprint_drift(monkeypatch):
    fingerprints = iter(
        (
            [{"relation": "monitoring_alerts", "row_count": 1}],
            [{"relation": "monitoring_alerts", "row_count": 2}],
        )
    )

    def fake_fetch(_cursor, sql):
        if sql == audit.SOURCE_FINGERPRINT_SQL:
            return next(fingerprints)
        return []

    class Connection:
        @staticmethod
        def cursor():
            return object()

    monkeypatch.setattr(audit, "_fetch", fake_fetch)
    with pytest.raises(RuntimeError, match="source changed"):
        audit._execute_snapshot(Connection())


def test_runtime_metadata_records_sha_environment_and_governed_flags(monkeypatch):
    import environment

    expected_flags = {
        "ENABLE_DOCUMENT_RENEWAL_AUTOMATION": False,
        "ENABLE_AGENT1_REFRESH_VERIFICATION": False,
        "ENABLE_MONITORING_SCREENING_CHANGE": False,
        "ENABLE_MONITORING_AUTO_RESOLUTION": False,
    }
    monkeypatch.setenv("GIT_SHA", "a" * 40)
    monkeypatch.setattr(environment, "ENV", "staging")
    monkeypatch.setattr(
        environment,
        "get_monitoring_feature_state",
        lambda: expected_flags,
    )
    metadata = audit._runtime_collection_metadata()
    assert metadata == {
        "environment": "staging",
        "deployed_git_sha": "a" * 40,
        "monitoring_feature_flags": expected_flags,
    }


def test_document_link_assigns_documents_owner_without_making_monitoring_owner():
    alert = _alert(
        7,
        alert_type="document_expiry_missing",
        source_reference="document:doc-7",
        provider=None,
        case_identifier=None,
    )
    documents = [
        {
            "alert_id": 7,
            "document_id": "doc-7",
            "document_application_id": "app-7",
            "doc_type": "passport",
            "is_current": True,
        }
    ]
    item = audit.analyse_snapshot(_snapshot(alert, documents=documents))["alerts"][0]
    assert item["workflow_owner"] == "Documents"
    assert item["classification"] == "Valid"
    assert item["migration_ready"] is True


def test_missing_application_is_orphaned_and_requires_manual_review():
    alert = _alert(
        1,
        application_id=None,
        application_ref=None,
        client_id=None,
        client_record_exists=False,
        source_reference="Manual entry",
        provider=None,
        case_identifier=None,
        alert_type="other",
    )
    item = audit.analyse_snapshot(_snapshot(alert))["alerts"][0]
    assert item["classification"] == "Orphaned"
    assert item["migration_disposition"] == "Manual review required"
    assert "missing_application_link" in item["linkage_findings"]


def test_extended_status_is_legacy_and_mapping_requires_link_evidence():
    without_link = audit.analyse_snapshot(
        _snapshot(_alert(1, status="escalated"))
    )["alerts"][0]
    assert without_link["status_validity"] == "Legacy"
    assert without_link["future_status"] is None
    assert without_link["migration_disposition"] == "Manual review required"

    with_link = audit.analyse_snapshot(
        _snapshot(
            _alert(2, status="in_review"),
            edd=[
                {
                    "alert_id": 2,
                    "edd_case_id": 9,
                    "application_id": "app-2",
                    "status": "analysis",
                    "direction": "reverse",
                    "linked_monitoring_alert_id": 2,
                    "alert_direct_edd_case_id": None,
                }
            ],
        )
    )["alerts"][0]
    assert with_link["workflow_owner"] == "EDD"
    assert with_link["future_status"] == "routed_to_edd"


def test_duplicate_requires_same_source_identity_not_merely_same_type():
    same = audit.analyse_snapshot(
        _snapshot(
            _alert(1),
            _alert(2, application_id="app-1", application_ref="REF-1"),
        )
    )
    assert same["statistics"]["duplicates"] == 2

    distinct = audit.analyse_snapshot(
        _snapshot(
            _alert(1),
            _alert(
                2,
                application_id="app-1",
                application_ref="REF-1",
                source_reference=(
                    '{"provider":"complyadvantage","alert_identifier":"a-2",'
                    '"case_identifier":"c-2","subject_scope":"person"}'
                ),
                case_identifier="c-2",
            ),
        )
    )
    assert distinct["statistics"]["duplicates"] == 0


def test_cross_application_workflow_link_is_impossible():
    result = audit.analyse_snapshot(
        _snapshot(
            _alert(1),
            periodic_reviews=[
                {
                    "alert_id": 1,
                    "periodic_review_id": 10,
                    "application_id": "another-app",
                    "status": "completed",
                    "direction": "reverse",
                    "linked_monitoring_alert_id": 1,
                    "alert_direct_periodic_review_id": None,
                }
            ],
        )
    )
    item = result["alerts"][0]
    assert item["workflow_owner"] == "Periodic Review"
    assert item["classification"] == "Orphaned"
    assert "impossible_cross_application_periodic_review_link" in item[
        "linkage_findings"
    ]


def test_terminal_status_without_resolved_at_is_not_migration_ready():
    item = audit.analyse_snapshot(
        _snapshot(
            _alert(
                1,
                status="dismissed",
                officer_action="dismiss",
                has_officer_notes=True,
                resolved_at=None,
            )
        )
    )["alerts"][0]
    assert item["classification"] == "Valid"
    assert item["migration_ready"] is False
    assert "terminal_status_without_resolved_at" in item["migration_findings"]


def test_test_like_non_fixture_is_flagged_without_reclassifying_fixture_truth():
    item = audit.analyse_snapshot(
        _snapshot(
            _alert(
                1,
                application_is_fixture=False,
                application_has_test_like_name=True,
            )
        )
    )["alerts"][0]
    assert item["classification"] == "Valid"
    assert item["test_like_non_fixture"] is True
    assert item["migration_disposition"] == "Manual review required"


def test_risk_drift_without_explicit_route_is_manual_not_screening_owned():
    item = audit.analyse_snapshot(
        _snapshot(
            _alert(
                1,
                alert_type="Risk Drift",
                provider=None,
                case_identifier=None,
                source_reference="Manual entry",
            )
        )
    )["alerts"][0]
    assert item["workflow_owner"] == "Manual"
    assert "missing_screening_case_link" not in item["linkage_findings"]


def test_canonical_triaged_status_is_preserved_without_route_evidence():
    item = audit.analyse_snapshot(
        _snapshot(_alert(1, status="triaged"))
    )["alerts"][0]
    assert item["status_validity"] == "Valid"
    assert item["future_status"] == "triaged"


def test_refresh_overloaded_statuses_are_included_in_duplicate_analysis():
    result = audit.analyse_snapshot(
        _snapshot(
            _alert(1, status="under_review"),
            _alert(
                2,
                application_id="app-1",
                application_ref="REF-1",
                status="under_review",
            ),
        )
    )
    assert result["statistics"]["duplicates"] == 2


def test_application_level_screening_context_is_non_causal():
    item = audit.analyse_snapshot(
        _snapshot(
            _alert(
                1,
                status="in_review",
                alert_type="other",
                provider=None,
                case_identifier=None,
                source_reference="Manual entry",
            ),
            screening_contexts=[
                {
                    "alert_id": 1,
                    "screening_review_id": 9,
                    "application_id": "app-1",
                    "subject_type": "entity",
                    "disposition": "escalated",
                    "requires_four_eyes": False,
                    "has_second_review": False,
                }
            ],
        )
    )["alerts"][0]
    assert item["screening_review_context"]
    assert item["workflow_owner"] == "Manual"
    assert item["future_status"] is None


def test_cross_application_evidence_and_broken_request_targets_are_reported():
    item = audit.analyse_snapshot(
        _snapshot(
            _alert(1),
            evidence=[
                {
                    "alert_id": 1,
                    "evidence_count": 1,
                    "provider_count": 1,
                    "case_count": 1,
                    "application_ids": ["another-app"],
                    "providers": ["complyadvantage"],
                    "case_identifiers": ["case-1"],
                }
            ],
            document_requests=[
                {
                    "alert_id": 1,
                    "request_id": 10,
                    "application_id": "app-1",
                    "linked_document_id": "missing-doc",
                    "linked_document_exists": None,
                    "monitoring_document_id": None,
                    "linked_periodic_review_id": None,
                }
            ],
        )
    )["alerts"][0]
    assert "impossible_cross_application_screening_evidence_link" in item[
        "linkage_findings"
    ]
    assert "broken_linked_document_link" in item["linkage_findings"]


def test_edd_owner_link_drift_blocks_migration_readiness():
    item = audit.analyse_snapshot(
        _snapshot(
            _alert(1, status="open"),
            edd=[
                {
                    "alert_id": 1,
                    "edd_case_id": 8,
                    "application_id": "app-1",
                    "status": "analysis",
                    "direction": "reverse",
                    "linked_monitoring_alert_id": 1,
                    "alert_direct_edd_case_id": None,
                }
            ],
        )
    )["alerts"][0]
    assert item["future_status"] == "routed_to_edd"
    assert item["migration_ready"] is False
    assert "active_status_conflicts_with_edd_owner_link" in item[
        "migration_findings"
    ]


@pytest.mark.parametrize("status", ("open", "in_review"))
def test_periodic_review_link_maps_route_and_blocks_canonical_owner_drift(status):
    item = audit.analyse_snapshot(
        _snapshot(
            _alert(1, status=status),
            periodic_reviews=[
                {
                    "alert_id": 1,
                    "periodic_review_id": 12,
                    "application_id": "app-1",
                    "status": "scheduled",
                    "direction": "reverse",
                    "linked_monitoring_alert_id": 1,
                    "alert_direct_periodic_review_id": None,
                }
            ],
        )
    )["alerts"][0]
    assert item["workflow_owner"] == "Periodic Review"
    assert item["future_status"] == "routed_to_review"
    if status == "open":
        assert item["migration_ready"] is False
        assert "active_status_conflicts_with_periodic_review_owner_link" in item[
            "migration_findings"
        ]
    else:
        assert item["migration_disposition"] == "Candidate for future migration"


def test_change_management_query_uses_schema_valid_change_alert_bridge():
    sql = audit.CHANGE_REQUEST_LINK_SQL
    assert "JOIN change_alerts" in sql
    assert "cr.source_alert_id = ca.id" in sql
    assert "cr.source_alert_id = ma.id::text" not in sql


def test_inventory_omits_names_and_raw_unstructured_source_text():
    sql = audit.ALERT_INVENTORY_SQL
    assert "a.company_name AS application_name" not in sql
    assert "client_label_md5" not in sql
    assert "MD5(" not in sql
    assert "u.full_name" not in sql
    item = audit.analyse_snapshot(
        _snapshot(
            _alert(
                1,
                source_reference="person@example.com confidential",
                detected_by="Monitoring agent Human Person",
            )
        )
    )["alerts"][0]
    rendered = str(item)
    assert "person@example.com" not in rendered
    assert "Human Person" not in rendered
    assert item["detected_by"]["kind"] == "pseudonymous"
    assert len(item["detected_by"]["hmac_sha256"]) == 64
    assert item["client"] == {"id": "client-1"}
    assert "name" not in item["assigned_officer"]


def test_status_mapping_aggregate_preserves_manual_review_disposition():
    result = audit.analyse_snapshot(
        _snapshot(
            _alert(
                1,
                application_has_test_like_name=True,
            )
        )
    )
    mapping = audit.render_reports(result)["STATUS_MAPPING_REPORT.md"]
    open_row = next(line for line in mapping.splitlines() if line.startswith("| open |"))
    assert "Manual review required" in open_row
    assert open_row.endswith("| 1 |")


def test_report_bundle_contains_all_six_requested_outputs():
    result = audit.analyse_snapshot(_snapshot(_alert(1)))
    reports = audit.render_reports(result)
    assert set(reports) == {
        "PR-MON-M1-STATUS-AUDIT-1.md",
        "MIGRATION_READINESS_REPORT.md",
        "STATUS_MAPPING_REPORT.md",
        "DUPLICATE_REPORT.md",
        "ORPHAN_REPORT.md",
        "LINKAGE_REPORT.md",
    }
    summary = reports["PR-MON-M1-STATUS-AUDIT-1.md"]
    assert "No Monitoring workflow" in summary
    assert "feature flag was activated by this audit" in summary
