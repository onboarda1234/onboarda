"""Safety contracts for PR-MON-M1-STATUS-BACKFILL-1."""

from __future__ import annotations

import json
import os
import sqlite3
from urllib.parse import urlsplit, urlunsplit
import uuid

import pytest

import db as db_module
import monitoring_status_backfill as backfill
from scripts.qa import monitoring_alert_status_backfill as backfill_cli


FLAGS_OFF = {flag: False for flag in backfill.GOVERNED_FLAGS}


def _manifest(*, two_automatic: bool = False):
    rows = [
        {
            "id": 10,
            "application_id": "app-10",
            "application_ref": "REF-10",
            "alert_type": "sanctions",
            "severity": "high",
            "effective_updated_at": "2026-07-01 04:04:00",
            "provider": "fixture",
            "case_identifier": "case-10",
            "source_reference": "ref-10",
            "officer_action": "create_edd",
            "required_edd_link": {
                "edd_case_id": 110,
                "application_id": "app-10",
                "linked_monitoring_alert_id": 10,
                "stage": "analysis",
            },
        }
    ]
    if two_automatic:
        rows.append(
            {
                "id": 11,
                "application_id": "app-11",
                "application_ref": "REF-11",
                "alert_type": "sanctions",
                "severity": "high",
                "effective_updated_at": "2026-07-01 04:05:00",
                "provider": "fixture",
                "case_identifier": "case-11",
                "source_reference": "ref-11",
                "officer_action": "create_edd",
                "required_edd_link": {
                    "edd_case_id": 111,
                    "application_id": "app-11",
                    "linked_monitoring_alert_id": 11,
                    "stage": "analysis",
                },
            }
        )
    canonical_id = 20
    manual_id = 30
    expected_count = len(rows) + 2
    return {
        "manifest_version": "1.0.0",
        "migration_id": "PR-MON-M1-STATUS-BACKFILL-1",
        "pr_identifier": "PR-MON-M1-STATUS-BACKFILL-1",
        "baseline": {
            "expected_alert_count": expected_count,
            "postgres_full_row_md5": None,
            "expected_status_counts": {
                "in_review": len(rows),
                "open": 2,
            },
            "expected_feature_flags": FLAGS_OFF,
        },
        "automatic_mappings": [
            {
                "source_status": "in_review",
                "target_status": "routed_to_edd",
                "mode": "automatic",
                "expected_count": len(rows),
                "allowed_alert_types": ["sanctions"],
                "reason": "exact audited EDD route",
                "evidence_source": "PR #900",
                "rollback_target": "in_review",
                "rows": rows,
            }
        ],
        "already_canonical": [
            {
                "id": canonical_id,
                "application_id": "app-20",
                "status": "open",
                "future_status": "open",
                "effective_updated_at": "2026-07-01 05:00:00",
                "reason": "already canonical",
            }
        ],
        "manual_review": [
            {
                "id": manual_id,
                "application_id": None,
                "status": "open",
                "future_status": "open",
                "effective_updated_at": "2026-07-01 06:00:00",
                "reason": "orphan; manual review only",
            }
        ],
        "duplicate_alert_ids": [],
        "orphan_alert_ids": [manual_id],
        "repeated_source_reference_groups": [],
    }


def _connection(manifest=None, *, missing_audit_column: bool = False):
    manifest = manifest or _manifest()
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    conn = db_module.DBConnection(raw, is_postgres=False)
    audit_entry_hash = "" if missing_audit_column else ", entry_hash TEXT"
    conn.executescript(
        f"""
        CREATE TABLE applications (id TEXT PRIMARY KEY, ref TEXT);
        CREATE TABLE monitoring_alerts (
            id INTEGER PRIMARY KEY,
            application_id TEXT,
            alert_type TEXT,
            severity TEXT,
            status TEXT,
            provider TEXT,
            case_identifier TEXT,
            source_reference TEXT,
            officer_action TEXT,
            created_at TEXT,
            reviewed_at TEXT,
            triaged_at TEXT,
            assigned_at TEXT,
            resolved_at TEXT,
            discovered_at TEXT
        );
        CREATE TABLE edd_cases (
            id INTEGER PRIMARY KEY,
            application_id TEXT,
            linked_monitoring_alert_id INTEGER,
            stage TEXT
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            user_id TEXT,
            user_name TEXT,
            user_role TEXT,
            action TEXT NOT NULL,
            target TEXT,
            detail TEXT,
            ip_address TEXT,
            before_state TEXT,
            after_state TEXT,
            previous_hash TEXT
            {audit_entry_hash},
            application_id TEXT,
            request_id TEXT
        );
        CREATE UNIQUE INDEX uq_audit_previous_hash
            ON audit_log(previous_hash) WHERE previous_hash IS NOT NULL;
        """
    )
    expected = backfill._rows_by_id(manifest)
    for row_id, row in expected.items():
        application_id = row.get("application_id")
        if application_id:
            conn.execute(
                "INSERT OR IGNORE INTO applications (id, ref) VALUES (?, ?)",
                (application_id, row.get("application_ref") or f"REF-{row_id}"),
            )
        created_at = row["effective_updated_at"]
        conn.execute(
            """
            INSERT INTO monitoring_alerts
                (id, application_id, alert_type, severity, status, provider,
                 case_identifier, source_reference, officer_action, created_at,
                 discovered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                application_id,
                row.get("alert_type") or "media",
                row.get("severity") or "medium",
                row["source_status"],
                row.get("provider"),
                row.get("case_identifier"),
                row.get("source_reference"),
                row.get("officer_action"),
                created_at,
                created_at,
            ),
        )
        link = row.get("required_edd_link")
        if link:
            conn.execute(
                """
                INSERT INTO edd_cases
                    (id, application_id, linked_monitoring_alert_id, stage)
                VALUES (?, ?, ?, ?)
                """,
                (
                    link["edd_case_id"],
                    link["application_id"],
                    link["linked_monitoring_alert_id"],
                    link["stage"],
                ),
            )
    conn.commit()
    return conn


def _status(conn, row_id=10):
    return conn.execute(
        "SELECT status FROM monitoring_alerts WHERE id = ?", (row_id,)
    ).fetchone()["status"]


def _audit_count(conn, action=backfill.MIGRATION_ACTION):
    return conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = ?", (action,)
    ).fetchone()["c"]


def _approve_test_manifest(monkeypatch, manifest):
    monkeypatch.setattr(
        backfill,
        "APPROVED_MANIFEST_SHA256",
        backfill.manifest_digest(manifest),
    )


def test_exact_mapping_only_and_manual_orphan_is_unchanged():
    manifest = _manifest()
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    assert plan["safe_to_apply"] is True
    assert [row["id"] for row in plan["eligible_rows"]] == [10]
    assert [row["id"] for row in plan["manual_review_rows"]] == [30]
    assert 30 in plan["unchanged_row_ids"]
    assert _status(conn, 30) == "open"


def test_unknown_status_and_stale_row_each_block_everything():
    for sql, params in (
        ("UPDATE monitoring_alerts SET status = ? WHERE id = ?", ("mystery", 10)),
        (
            "UPDATE monitoring_alerts SET reviewed_at = ? WHERE id = ?",
            ("2026-07-02 00:00:00", 10),
        ),
    ):
        manifest = _manifest()
        conn = _connection(manifest)
        conn.execute(sql, params)
        conn.commit()
        plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
        assert plan["safe_to_apply"] is False
        assert plan["blocked_by_changed_preconditions"]
        assert _audit_count(conn) == 0
        conn.close()


def test_expected_count_drift_and_duplicate_edd_link_block_everything():
    manifest = _manifest()
    conn = _connection(manifest)
    conn.execute("DELETE FROM monitoring_alerts WHERE id = 20")
    conn.commit()
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    assert plan["safe_to_apply"] is False
    assert any(item["reason"] == "alert count mismatch" for item in plan["blocked_by_changed_preconditions"])

    conn = _connection(manifest)
    conn.execute(
        "INSERT INTO edd_cases VALUES (?, ?, ?, ?)",
        (112, "app-10", 10, "analysis"),
    )
    conn.commit()
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    assert plan["safe_to_apply"] is False
    assert any(item["reason"] == "required EDD linkage changed" for item in plan["blocked_by_changed_preconditions"])


def test_missing_audit_infrastructure_and_enabled_flag_fail_closed():
    manifest = _manifest()
    conn = _connection(manifest, missing_audit_column=True)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    assert plan["safe_to_apply"] is False
    assert any(item["reason"] == "canonical audit writer infrastructure unavailable" for item in plan["blocked_by_changed_preconditions"])

    conn = _connection(manifest)
    enabled = dict(FLAGS_OFF)
    enabled["ENABLE_MONITORING_AUTO_RESOLUTION"] = True
    plan = backfill.build_plan(conn, manifest, enabled)
    assert plan["safe_to_apply"] is False
    assert any(item.get("flag") == "ENABLE_MONITORING_AUTO_RESOLUTION" for item in plan["blocked_by_changed_preconditions"])


def test_approved_production_manifest_cannot_apply_without_postgres_fingerprint():
    manifest = backfill.load_manifest()
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    assert plan["safe_to_apply"] is False
    assert any(
        item["reason"] == "required PostgreSQL full-row fingerprint is unavailable"
        for item in plan["blocked_by_changed_preconditions"]
    )
    with pytest.raises(
        backfill.ReconciliationError, match="requires PostgreSQL"
    ):
        backfill.apply_backfill(
            conn,
            manifest,
            FLAGS_OFF,
            actor="founder",
            expected_plan_fingerprint=plan["plan_fingerprint"],
            approval_phrase=backfill.APPROVAL_PHRASE,
        )


def test_drift_evidence_never_echoes_raw_source_reference():
    manifest = _manifest()
    conn = _connection(manifest)
    secret_like = "person@example.com bearer-secret-like-source"
    conn.execute(
        "UPDATE monitoring_alerts SET source_reference = ? WHERE id = ?",
        (secret_like, 10),
    )
    conn.commit()
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    rendered = json.dumps(plan, sort_keys=True)
    assert secret_like not in rendered
    assert "person@example.com" not in rendered
    mismatch = next(
        item
        for item in plan["blocked_by_changed_preconditions"]
        if item.get("id") == 10
    )
    source = next(
        item
        for item in mismatch["mismatches"]
        if item["field"] == "source_reference"
    )
    assert len(source["observed"]["sha256"]) == 64


def test_dry_run_is_repeatable_and_makes_no_writes():
    manifest = _manifest()
    conn = _connection(manifest)
    before = conn.conn.total_changes
    first = backfill.build_plan(conn, manifest, FLAGS_OFF)
    second = backfill.build_plan(conn, manifest, FLAGS_OFF)
    assert first == second
    assert first["plan_fingerprint"] == second["plan_fingerprint"]
    assert conn.conn.total_changes == before
    assert _status(conn) == "in_review"
    assert _audit_count(conn) == 0


def test_database_enforced_read_only_planner_rejects_writes():
    conn = _connection(_manifest())
    backfill.begin_read_only_transaction(conn)
    plan = backfill.build_plan(conn, _manifest(), FLAGS_OFF)
    assert plan["safe_to_apply"] is True
    with pytest.raises(Exception):
        conn.execute(
            "UPDATE monitoring_alerts SET status = ? WHERE id = ?",
            ("routed_to_edd", 10),
        )
    conn.rollback()
    # query_only is connection-scoped on SQLite; restore it for fixture reuse.
    conn.execute("PRAGMA query_only = OFF")
    assert _status(conn) == "in_review"


def test_audit_failure_rolls_back_status_change(monkeypatch):
    manifest = _manifest()
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit writer unavailable")

    with pytest.raises(RuntimeError, match="audit writer unavailable"):
        backfill.apply_backfill(
            conn,
            manifest,
            FLAGS_OFF,
            actor="founder",
            expected_plan_fingerprint=plan["plan_fingerprint"],
            approval_phrase=backfill.APPROVAL_PHRASE,
            audit_writer=fail_audit,
        )
    assert _status(conn) == "in_review"
    assert _audit_count(conn) == 0


def test_partial_audit_failure_rolls_back_all_rows_and_audits(monkeypatch):
    manifest = _manifest(two_automatic=True)
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    calls = 0

    def fail_second(db, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second audit failed")
        return db_module.append_audit_log(db, **kwargs)

    with pytest.raises(RuntimeError, match="second audit failed"):
        backfill.apply_backfill(
            conn,
            manifest,
            FLAGS_OFF,
            actor="founder",
            expected_plan_fingerprint=plan["plan_fingerprint"],
            approval_phrase=backfill.APPROVAL_PHRASE,
            audit_writer=fail_second,
        )
    assert _status(conn, 10) == "in_review"
    assert _status(conn, 11) == "in_review"
    assert _audit_count(conn) == 0


def test_apply_is_atomic_audited_and_idempotent(monkeypatch):
    manifest = _manifest()
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    applied = backfill.apply_backfill(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_plan_fingerprint=plan["plan_fingerprint"],
        approval_phrase=backfill.APPROVAL_PHRASE,
    )
    assert applied["changed_rows"] == [
        {"id": 10, "previous_status": "in_review", "new_status": "routed_to_edd"}
    ]
    assert applied["post_commit_plan"]["phase"] == "post_migration"
    assert _status(conn) == "routed_to_edd"
    assert _audit_count(conn) == 1
    audit = conn.execute(
        "SELECT before_state, after_state, detail, entry_hash FROM audit_log"
    ).fetchone()
    assert json.loads(audit["before_state"])["status"] == "in_review"
    assert json.loads(audit["after_state"])["status"] == "routed_to_edd"
    assert json.loads(audit["detail"])["dry_run_plan_fingerprint"] == plan["plan_fingerprint"]
    assert audit["entry_hash"]

    second = backfill.apply_backfill(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_plan_fingerprint=plan["plan_fingerprint"],
        approval_phrase=backfill.APPROVAL_PHRASE,
    )
    assert second["idempotent"] is True
    assert second["changed_rows"] == []
    assert _audit_count(conn) == 1


def test_post_commit_query_failure_reports_durable_commit_truthfully(monkeypatch):
    manifest = _manifest()
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    original_build_plan = backfill.build_plan
    calls = 0

    def fail_fresh_snapshot(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("fresh snapshot unavailable")
        return original_build_plan(*args, **kwargs)

    monkeypatch.setattr(backfill, "build_plan", fail_fresh_snapshot)
    with pytest.raises(backfill.PostCommitReconciliationError) as captured:
        backfill.apply_backfill(
            conn,
            manifest,
            FLAGS_OFF,
            actor="founder",
            expected_plan_fingerprint=plan["plan_fingerprint"],
            approval_phrase=backfill.APPROVAL_PHRASE,
        )

    error = captured.value
    payload = backfill.reconciliation_error_payload(error)
    assert payload["committed"] is True
    assert payload["failed_closed"] is False
    assert payload["changed_rows"] == [
        {"id": 10, "previous_status": "in_review", "new_status": "routed_to_edd"}
    ]
    assert payload["audit_entries"][0]["action"] == backfill.MIGRATION_ACTION
    assert len(payload["audit_entries"][0]["entry_hash"]) == 64
    assert _status(conn) == "routed_to_edd"
    assert _audit_count(conn) == 1


def test_post_commit_mismatch_reports_durable_commit_truthfully(monkeypatch):
    manifest = _manifest()
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    original_build_plan = backfill.build_plan
    calls = 0

    def mismatch_fresh_snapshot(*args, **kwargs):
        nonlocal calls
        calls += 1
        observed = original_build_plan(*args, **kwargs)
        if calls == 3:
            observed["blocked_by_changed_preconditions"] = [
                {"reason": "injected fresh-snapshot mismatch"}
            ]
        return observed

    monkeypatch.setattr(backfill, "build_plan", mismatch_fresh_snapshot)
    with pytest.raises(backfill.PostCommitReconciliationError) as captured:
        backfill.apply_backfill(
            conn,
            manifest,
            FLAGS_OFF,
            actor="founder",
            expected_plan_fingerprint=plan["plan_fingerprint"],
            approval_phrase=backfill.APPROVAL_PHRASE,
        )

    payload = backfill.reconciliation_error_payload(captured.value)
    assert payload["committed"] is True
    assert payload["failed_closed"] is False
    assert payload["plan"]["blocked_by_changed_preconditions"]
    assert _status(conn) == "routed_to_edd"
    assert _audit_count(conn) == 1


def test_cli_preserves_committed_error_when_cleanup_rollback_fails(monkeypatch):
    entry_hash = "a" * 64
    committed_error = backfill.PostCommitReconciliationError(
        "migration committed but connection was lost",
        changed_rows=[
            {
                "id": 10,
                "previous_status": "in_review",
                "new_status": "routed_to_edd",
            }
        ],
        audit_entries=[
            {
                "id": 10,
                "action": backfill.MIGRATION_ACTION,
                "entry_hash": entry_hash,
            }
        ],
    )
    written = []

    class LostConnection:
        def rollback(self):
            raise RuntimeError("connection already lost")

        def close(self):
            pass

    monkeypatch.setattr(backfill_cli, "load_manifest", lambda _path: {})
    monkeypatch.setattr(
        backfill_cli, "get_monitoring_feature_state", lambda: FLAGS_OFF
    )
    monkeypatch.setattr(backfill_cli, "get_environment", lambda: "staging")
    monkeypatch.setattr(backfill_cli, "get_db", LostConnection)
    monkeypatch.setattr(
        backfill_cli,
        "apply_backfill",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(committed_error),
    )
    monkeypatch.setattr(
        backfill_cli, "_write", lambda value, _output: written.append(value)
    )

    result = backfill_cli.main(["apply"])
    assert result == 2
    assert len(written) == 1
    assert written[0]["committed"] is True
    assert written[0]["failed_closed"] is False
    assert written[0]["changed_rows"][0]["id"] == 10
    assert written[0]["audit_entries"][0]["entry_hash"] == entry_hash


def test_malformed_audit_hashes_are_redacted_and_block_reconciliation(monkeypatch):
    manifest = _manifest()
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    backfill.apply_backfill(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_plan_fingerprint=plan["plan_fingerprint"],
        approval_phrase=backfill.APPROVAL_PHRASE,
    )
    secret_fingerprint = "person@example.com bearer-audit-secret"
    secret_entry_hash = "another@example.com private-entry-secret"
    audit = conn.execute(
        "SELECT id, detail FROM audit_log WHERE action = ?",
        (backfill.MIGRATION_ACTION,),
    ).fetchone()
    detail = json.loads(audit["detail"])
    detail["dry_run_plan_fingerprint"] = secret_fingerprint
    conn.execute(
        "UPDATE audit_log SET detail = ?, entry_hash = ? WHERE id = ?",
        (json.dumps(detail), secret_entry_hash, audit["id"]),
    )
    conn.commit()

    observed = backfill.build_plan(conn, manifest, FLAGS_OFF)
    rollback = backfill.build_rollback_plan(conn, manifest, FLAGS_OFF)
    rendered = json.dumps({"plan": observed, "rollback": rollback}, sort_keys=True)
    assert secret_fingerprint not in rendered
    assert secret_entry_hash not in rendered
    assert "person@example.com" not in rendered
    assert "another@example.com" not in rendered
    assert observed["safe_to_apply"] is False
    assert rollback["safe_to_rollback"] is False
    assert any(
        item["reason"] == "migration audit hash evidence is malformed"
        for item in observed["blocked_by_changed_preconditions"]
    )


def test_malformed_audit_writer_hash_rolls_back_everything(monkeypatch):
    manifest = _manifest()
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)

    def malformed_hash_writer(db, **kwargs):
        db_module.append_audit_log(db, **kwargs)
        return "not-a-sha256"

    with pytest.raises(backfill.ReconciliationError, match="malformed entry hash"):
        backfill.apply_backfill(
            conn,
            manifest,
            FLAGS_OFF,
            actor="founder",
            expected_plan_fingerprint=plan["plan_fingerprint"],
            approval_phrase=backfill.APPROVAL_PHRASE,
            audit_writer=malformed_hash_writer,
        )
    assert _status(conn) == "in_review"
    assert _audit_count(conn) == 0


def test_exact_approval_phrase_and_plan_fingerprint_are_mandatory(monkeypatch):
    manifest = _manifest()
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    for approval, fingerprint in (
        ("approve", plan["plan_fingerprint"]),
        (backfill.APPROVAL_PHRASE, "wrong"),
    ):
        with pytest.raises(backfill.ReconciliationError):
            backfill.apply_backfill(
                conn,
                manifest,
                FLAGS_OFF,
                actor="founder",
                expected_plan_fingerprint=fingerprint,
                approval_phrase=approval,
            )
        assert _status(conn) == "in_review"
        assert _audit_count(conn) == 0


def test_rollback_plan_is_exact_and_refuses_later_alert_mutation(monkeypatch):
    manifest = _manifest()
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    backfill.apply_backfill(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_plan_fingerprint=plan["plan_fingerprint"],
        approval_phrase=backfill.APPROVAL_PHRASE,
    )
    rollback = backfill.build_rollback_plan(conn, manifest, FLAGS_OFF)
    assert rollback["safe_to_rollback"] is True
    assert [row["id"] for row in rollback["eligible_rows"]] == [10]

    db_module.append_audit_log(
        conn,
        action="monitoring.alert.officer_note_changed",
        user_id="officer",
        target="monitoring_alert:10",
        detail="later governed action",
        commit=True,
    )
    refused = backfill.build_rollback_plan(conn, manifest, FLAGS_OFF)
    assert refused["safe_to_rollback"] is False
    assert any(
        item["reason"] == "later alert audit evidence exists; automated rollback refused"
        for item in refused["blocked_by_changed_preconditions"]
    )


def test_rollback_restores_only_the_recorded_row_and_writes_new_audit(monkeypatch):
    manifest = _manifest()
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    backfill.apply_backfill(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_plan_fingerprint=plan["plan_fingerprint"],
        approval_phrase=backfill.APPROVAL_PHRASE,
    )
    rollback = backfill.build_rollback_plan(conn, manifest, FLAGS_OFF)
    result = backfill.apply_rollback(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_rollback_fingerprint=rollback["rollback_plan_fingerprint"],
    )
    assert result["changed_rows"] == [
        {"id": 10, "previous_status": "routed_to_edd", "new_status": "in_review"}
    ]
    assert _status(conn, 10) == "in_review"
    assert _status(conn, 20) == "open"
    assert _status(conn, 30) == "open"
    assert _audit_count(conn, backfill.MIGRATION_ACTION) == 1
    assert _audit_count(conn, backfill.ROLLBACK_ACTION) == 1
    second = backfill.apply_rollback(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_rollback_fingerprint=rollback["rollback_plan_fingerprint"],
    )
    assert second["idempotent"] is True
    assert second["changed_rows"] == []
    assert _audit_count(conn, backfill.ROLLBACK_ACTION) == 1


def test_malformed_rollback_audit_hashes_are_redacted_and_block_idempotency(
    monkeypatch,
):
    manifest = _manifest()
    _approve_test_manifest(monkeypatch, manifest)
    conn = _connection(manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    backfill.apply_backfill(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_plan_fingerprint=plan["plan_fingerprint"],
        approval_phrase=backfill.APPROVAL_PHRASE,
    )
    rollback = backfill.build_rollback_plan(conn, manifest, FLAGS_OFF)
    backfill.apply_rollback(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_rollback_fingerprint=rollback["rollback_plan_fingerprint"],
    )
    secret_fingerprint = "founder@example.com rollback-private-value"
    secret_entry_hash = "operator@example.com rollback-entry-private"
    audit = conn.execute(
        "SELECT id, detail FROM audit_log WHERE action = ?",
        (backfill.ROLLBACK_ACTION,),
    ).fetchone()
    detail = json.loads(audit["detail"])
    detail["rollback_plan_fingerprint"] = secret_fingerprint
    conn.execute(
        "UPDATE audit_log SET detail = ?, entry_hash = ? WHERE id = ?",
        (json.dumps(detail), secret_entry_hash, audit["id"]),
    )
    conn.commit()

    observed = backfill.build_rollback_plan(conn, manifest, FLAGS_OFF)
    rendered = json.dumps(observed, sort_keys=True)
    assert secret_fingerprint not in rendered
    assert secret_entry_hash not in rendered
    assert "founder@example.com" not in rendered
    assert "operator@example.com" not in rendered
    assert observed["safe_to_rollback"] is False
    assert any(
        item["reason"] == "rollback audit hash evidence is malformed"
        for item in observed["blocked_by_changed_preconditions"]
    )


def test_manifest_rejects_fuzzy_or_unenumerated_mapping():
    manifest = _manifest()
    manifest["automatic_mappings"][0]["rows"] = []
    with pytest.raises(backfill.ReconciliationError):
        backfill.validate_manifest(manifest)


def test_mutation_manifest_is_digest_pinned_and_target_is_canonical():
    approved = backfill.load_manifest()
    altered = json.loads(json.dumps(approved))
    altered["automatic_mappings"][0]["reason"] = "unreviewed copy"
    with pytest.raises(backfill.ReconciliationError, match="digest"):
        backfill.validate_approved_manifest(altered)

    arbitrary = json.loads(json.dumps(approved))
    arbitrary["automatic_mappings"][0]["target_status"] = "arbitrary_status"
    with pytest.raises(backfill.ReconciliationError, match="not canonical"):
        backfill.validate_manifest(arbitrary)


def test_operator_module_has_no_runtime_consumer():
    root = backfill.Path(__file__).resolve().parents[1]
    consumers = []
    for path in root.glob("*.py"):
        if path.name == "monitoring_status_backfill.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "import monitoring_status_backfill" in text or "from monitoring_status_backfill import" in text:
            consumers.append(path.name)
    assert consumers == []


def _postgres_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


@pytest.fixture()
def postgres_backfill_db():
    """Fresh disposable PostgreSQL DB; never point this fixture at staging."""
    base_dsn = _postgres_dsn()
    if not base_dsn:
        pytest.skip("No PostgreSQL test DSN available")
    parts = urlsplit(base_dsn)
    if parts.hostname not in (None, "localhost", "127.0.0.1", "::1"):
        pytest.skip("Backfill PostgreSQL test requires a local disposable server")
    import psycopg2

    database_name = f"onboarda_test_status_backfill_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(base_dsn)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f'CREATE DATABASE "{database_name}"')
    dsn = urlunsplit(
        (parts.scheme, parts.netloc, f"/{database_name}", parts.query, parts.fragment)
    )
    try:
        raw = psycopg2.connect(dsn)
        conn = db_module.DBConnection(
            raw, is_postgres=True, database_identity=dsn
        )
        conn.executescript(
            """
            CREATE TABLE applications (id TEXT PRIMARY KEY, ref TEXT);
            CREATE TABLE monitoring_alerts (
                id SERIAL PRIMARY KEY,
                application_id TEXT,
                alert_type TEXT,
                severity TEXT,
                status TEXT,
                provider TEXT,
                case_identifier TEXT,
                source_reference TEXT,
                officer_action TEXT,
                created_at TIMESTAMP,
                reviewed_at TIMESTAMP,
                triaged_at TIMESTAMP,
                assigned_at TIMESTAMP,
                resolved_at TIMESTAMP,
                discovered_at TIMESTAMP
            );
            CREATE TABLE edd_cases (
                id SERIAL PRIMARY KEY,
                application_id TEXT,
                linked_monitoring_alert_id INTEGER,
                stage TEXT
            );
            CREATE TABLE audit_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT,
                user_name TEXT,
                user_role TEXT,
                action TEXT NOT NULL,
                target TEXT,
                detail TEXT,
                ip_address TEXT,
                before_state TEXT,
                after_state TEXT,
                previous_hash TEXT,
                entry_hash TEXT,
                application_id TEXT,
                request_id TEXT
            );
            CREATE UNIQUE INDEX uq_audit_previous_hash
                ON audit_log(previous_hash) WHERE previous_hash IS NOT NULL;
            """
        )
        manifest = _manifest()
        expected = backfill._rows_by_id(manifest)
        for row_id, row in expected.items():
            application_id = row.get("application_id")
            if application_id:
                conn.execute(
                    "INSERT INTO applications (id, ref) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (
                        application_id,
                        row.get("application_ref") or f"REF-{row_id}",
                    ),
                )
            created_at = row["effective_updated_at"]
            conn.execute(
                """
                INSERT INTO monitoring_alerts
                    (id, application_id, alert_type, severity, status, provider,
                     case_identifier, source_reference, officer_action, created_at,
                     discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    application_id,
                    row.get("alert_type") or "media",
                    row.get("severity") or "medium",
                    row["source_status"],
                    row.get("provider"),
                    row.get("case_identifier"),
                    row.get("source_reference"),
                    row.get("officer_action"),
                    created_at,
                    created_at,
                ),
            )
            link = row.get("required_edd_link")
            if link:
                conn.execute(
                    """
                    INSERT INTO edd_cases
                        (id, application_id, linked_monitoring_alert_id, stage)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        link["edd_case_id"],
                        link["application_id"],
                        link["linked_monitoring_alert_id"],
                        link["stage"],
                    ),
                )
        conn.commit()
        manifest["baseline"]["postgres_full_row_md5"] = backfill._full_row_md5(
            conn, manifest
        )
        conn.rollback()
        yield conn, dsn, manifest
        conn.close()
    finally:
        cleanup = psycopg2.connect(base_dsn)
        cleanup.autocommit = True
        with cleanup.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        cleanup.close()
        admin.close()


def test_postgres_transaction_audit_and_idempotency(
    postgres_backfill_db, monkeypatch
):
    conn, _dsn, manifest = postgres_backfill_db
    _approve_test_manifest(monkeypatch, manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    conn.rollback()
    applied = backfill.apply_backfill(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_plan_fingerprint=plan["plan_fingerprint"],
        approval_phrase=backfill.APPROVAL_PHRASE,
    )
    assert applied["post_migration_plan"]["phase"] == "post_migration"
    assert _status(conn) == "routed_to_edd"
    assert _audit_count(conn) == 1
    conn.rollback()
    second = backfill.apply_backfill(
        conn,
        manifest,
        FLAGS_OFF,
        actor="founder",
        expected_plan_fingerprint=plan["plan_fingerprint"],
        approval_phrase=backfill.APPROVAL_PHRASE,
    )
    assert second["idempotent"] is True
    assert _audit_count(conn) == 1


def test_postgres_writer_lock_blocks_concurrent_status_update(postgres_backfill_db):
    conn, dsn, manifest = postgres_backfill_db
    import psycopg2

    competing = db_module.DBConnection(
        psycopg2.connect(dsn), is_postgres=True, database_identity=dsn
    )
    try:
        backfill._begin_write_transaction(conn)
        locked_plan = backfill.build_plan(
            conn, manifest, FLAGS_OFF, lock_rows=True
        )
        assert locked_plan["safe_to_apply"] is True
        competing.execute("SET lock_timeout = '150ms'")
        with pytest.raises(Exception) as exc_info:
            competing.execute(
                "UPDATE monitoring_alerts SET status = ? WHERE id = ?",
                ("routed_to_edd", 10),
            )
        assert "lock timeout" in str(exc_info.value).lower()
        competing.execute("SET lock_timeout = '150ms'")
        with pytest.raises(Exception) as audit_exc:
            competing.execute(
                """
                INSERT INTO audit_log (action, target, detail)
                VALUES (?, ?, ?)
                """,
                (
                    "monitoring.alert.concurrent",
                    "monitoring_alert:10",
                    "must wait behind controlled transaction",
                ),
            )
        assert "lock timeout" in str(audit_exc.value).lower()
        assert _status(conn) == "in_review"
    finally:
        competing.rollback()
        competing.close()
        conn.rollback()


def test_postgres_audit_failure_rolls_back_status(
    postgres_backfill_db, monkeypatch
):
    conn, _dsn, manifest = postgres_backfill_db
    _approve_test_manifest(monkeypatch, manifest)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    conn.rollback()

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("injected PostgreSQL audit failure")

    with pytest.raises(RuntimeError, match="injected PostgreSQL audit failure"):
        backfill.apply_backfill(
            conn,
            manifest,
            FLAGS_OFF,
            actor="founder",
            expected_plan_fingerprint=plan["plan_fingerprint"],
            approval_phrase=backfill.APPROVAL_PHRASE,
            audit_writer=fail_audit,
        )
    assert _status(conn) == "in_review"
    assert _audit_count(conn) == 0


def test_postgres_dry_run_transaction_is_database_enforced_read_only(
    postgres_backfill_db,
):
    conn, _dsn, manifest = postgres_backfill_db
    backfill.begin_read_only_transaction(conn)
    plan = backfill.build_plan(conn, manifest, FLAGS_OFF)
    assert plan["safe_to_apply"] is True
    with pytest.raises(Exception) as exc_info:
        conn.execute(
            "UPDATE monitoring_alerts SET status = ? WHERE id = ?",
            ("routed_to_edd", 10),
        )
    assert "read-only transaction" in str(exc_info.value).lower()
    conn.rollback()
    assert _status(conn) == "in_review"
