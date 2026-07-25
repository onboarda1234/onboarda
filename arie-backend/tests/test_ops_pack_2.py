"""Static + unit guards for the second ops-hardening pack.

Register rows:
  * staging-SHA gate row, "delete test logins" ops half (ADMIN-AUDIT-006)
  * P9-8  = DCI-027 (CRITICAL) = FEO-009 — DR/backup drill, restore/PITR
  * P9-10 = DCI-030 = FEO-011 — prod monitoring/alerting/on-call

Same shape as `test_ops_hardening_pack.py`: the provisioning/ops scripts expose
pure builder functions returning plain dicts, so the policy they encode is
unit-testable with no AWS and no database, and the runbook is guarded for the
commands it must contain.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "arie-backend"
RUNBOOK = (REPO / "docs" / "OPS_HARDENING_RUNBOOK.md").read_text(encoding="utf-8")


def _load(script_name):
    path = BACKEND / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


monitoring = _load("provision_production_monitoring.py")
dr = _load("verify_dr_posture.py")
quarantine = _load("quarantine_staging_test_logins.py")


class TestProductionMonitoringAlarms:
    """P9-10 — the metrics are already emitted; these alarms make them visible."""

    def test_filters_cover_every_currently_unalarmed_metric(self):
        names = {name for name, _unit, _svc in monitoring.UNALARMED_METRICS}
        # Emitted by the workers/backend today with no filter and no alarm.
        assert {
            "ScreeningQueueDepth", "ScreeningInProgressJobs",
            "ScreeningOldestPendingAgeSeconds", "ScreeningFailedJobsLastHour",
            "ScreeningEndToEndJobMs", "ScreeningWorkerFailures",
            "SchemaDriftMissingObjects",
        } <= names

    def test_filter_pattern_matches_the_emitted_log_shape(self):
        spec = monitoring.build_metric_filter(
            environment="staging", log_group="/ecs/regmind-staging",
            metric_name="ScreeningQueueDepth", unit="Count")
        pattern = spec["filterPattern"]
        assert '$.message = "cloudwatch_metric"' in pattern
        assert '$.metric_name = "ScreeningQueueDepth"' in pattern
        assert '$.environment = "staging"' in pattern
        transformation = spec["metricTransformations"][0]
        assert transformation["metricValue"] == "$.metric_value"
        assert transformation["dimensions"] == {
            "Environment": "$.environment", "Service": "$.service"}

    def test_error_filter_has_no_dimensions_and_targets_a_named_metric(self):
        """B1: ERROR records carry no `environment` field, so a dimensioned
        filter matches nothing and leaves a dead alarm sitting in OK. Dimensions
        and defaultValue are also mutually exclusive in PutMetricFilter, so the
        pairing failed at --apply."""
        spec = monitoring.build_error_rate_filter(
            environment="staging", log_group="/ecs/regmind-staging")
        transformation = spec["metricTransformations"][0]
        assert "dimensions" not in transformation
        assert transformation["metricName"] == monitoring.error_metric_name("staging")
        assert monitoring.error_metric_name("staging") != monitoring.error_metric_name("production")

    def test_error_alarm_is_dimensionless_to_match_its_metric(self):
        alarms = {a["AlarmName"]: a for a in monitoring.build_alarm_specs(
            environment="staging", alarm_action_arn="",
            screening_backlog_threshold=50, screening_age_threshold_s=1800,
            error_threshold=5)}
        error_alarm = alarms["staging-application-error-rate-high"]
        assert error_alarm["Dimensions"] == []
        assert error_alarm["MetricName"] == monitoring.error_metric_name("staging")

    def test_apply_against_production_requires_explicit_confirmation(self):
        proc = subprocess.run(
            [sys.executable,
             str(BACKEND / "scripts" / "provision_production_monitoring.py"),
             "--environment", "production", "--apply"],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 2, proc.stdout
        assert "REFUSED" in proc.stderr

    def test_error_filter_counts_only_error_level(self):
        """4xx is logged at WARNING on purpose, so an ERROR-level filter counts
        genuine server-side failures and cannot be driven by unauthenticated
        callers."""
        spec = monitoring.build_error_rate_filter(
            environment="staging", log_group="/ecs/regmind-staging")
        assert spec["filterPattern"] == '{ $.level = "ERROR" }'
        transformation = spec["metricTransformations"][0]
        assert transformation["defaultValue"] == 0
        # Dimensionless entirely — see the B1 test above.
        assert "dimensions" not in transformation

    def test_heartbeat_alarm_treats_missing_data_as_breaching(self):
        """The whole point of a liveness alarm — and the opposite of the
        threshold alarms' notBreaching posture."""
        alarms = {a["AlarmName"]: a for a in monitoring.build_alarm_specs(
            environment="staging", alarm_action_arn="",
            screening_backlog_threshold=50, screening_age_threshold_s=1800,
            error_threshold=5)}
        heartbeat = alarms["staging-verification-worker-heartbeat-missing"]
        assert heartbeat["TreatMissingData"] == "breaching"
        assert heartbeat["Statistic"] == "SampleCount"
        assert heartbeat["ComparisonOperator"] == "LessThanThreshold"

    def test_threshold_alarms_do_not_use_breaching(self):
        for alarm in monitoring.build_alarm_specs(
                environment="staging", alarm_action_arn="",
                screening_backlog_threshold=50, screening_age_threshold_s=1800,
                error_threshold=5):
            if alarm["AlarmName"].endswith("heartbeat-missing"):
                continue
            assert alarm["TreatMissingData"] == "notBreaching", alarm["AlarmName"]

    def test_alarms_without_an_sns_arn_have_empty_actions(self):
        for alarm in monitoring.build_alarm_specs(
                environment="staging", alarm_action_arn="",
                screening_backlog_threshold=50, screening_age_threshold_s=1800,
                error_threshold=5):
            assert alarm["AlarmActions"] == []
            assert alarm["OKActions"] == []

    def test_alarms_are_environment_scoped(self):
        for alarm in monitoring.build_alarm_specs(
                environment="production", alarm_action_arn="arn:aws:sns:x",
                screening_backlog_threshold=50, screening_age_threshold_s=1800,
                error_threshold=5):
            assert alarm["AlarmName"].startswith("production-")
            if alarm["Dimensions"]:
                assert {"Name": "Environment", "Value": "production"} in alarm["Dimensions"]
            else:
                # The dimensionless error metric scopes by metric NAME instead.
                assert alarm["MetricName"].endswith("-production")

    def test_dry_run_is_the_default_and_succeeds_without_aws(self):
        proc = subprocess.run(
            [sys.executable, str(BACKEND / "scripts" / "provision_production_monitoring.py")],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        assert "DRY-RUN" in proc.stdout
        assert "put_metric_alarm" in proc.stdout


class TestDrPostureEvaluation:
    """P9-8 — policy is pure so it can be tested without touching AWS."""

    HEALTHY = {
        "DBInstanceIdentifier": "regmind-staging-db",
        "EngineVersion": "15.5",
        "BackupRetentionPeriod": 7,
        "DeletionProtection": True,
        "StorageEncrypted": True,
        "MultiAZ": False,
        "LatestRestorableTime": "2026-07-25T11:55:00Z",
    }
    NOW = "2026-07-25T12:00:00Z"

    def test_healthy_staging_posture_passes_with_multi_az_advisory(self):
        report = dr.evaluate_posture(self.HEALTHY, now_iso=self.NOW)
        assert report["verdict"] == "PASS"
        assert report["blocking_failures"] == []
        # Staging is single-AZ by design — advisory, must not block.
        assert report["advisory_failures"] == ["multi_az"]

    @pytest.mark.parametrize("field,value,expected_failure", [
        ("BackupRetentionPeriod", 1, "backup_retention_days"),
        ("DeletionProtection", False, "deletion_protection"),
        ("StorageEncrypted", False, "storage_encrypted"),
    ])
    def test_each_baseline_violation_blocks(self, field, value, expected_failure):
        instance = dict(self.HEALTHY, **{field: value})
        report = dr.evaluate_posture(instance, now_iso=self.NOW)
        assert report["verdict"] == "FAIL"
        assert expected_failure in report["blocking_failures"]

    def test_stale_restore_point_fails_even_with_retention_configured(self):
        """The check that proves PITR is WORKING, not merely configured: a
        retention window with a restore point hours behind cannot deliver the
        stated RPO."""
        instance = dict(self.HEALTHY, LatestRestorableTime="2026-07-25T06:00:00Z")
        report = dr.evaluate_posture(instance, now_iso=self.NOW)
        assert report["verdict"] == "FAIL"
        assert "pitr_latest_restorable_recent" in report["blocking_failures"]

    def test_missing_restore_time_fails_closed(self):
        instance = dict(self.HEALTHY)
        instance.pop("LatestRestorableTime")
        report = dr.evaluate_posture(instance, now_iso=self.NOW)
        assert report["verdict"] == "FAIL"
        assert "pitr_latest_restorable_recent" in report["blocking_failures"]

    def test_rpo_is_reported_and_rto_is_not_claimed(self):
        """RTO can only come from a timed restore drill — the script must not
        imply it verified one."""
        report = dr.evaluate_posture(self.HEALTHY, now_iso=self.NOW)
        assert report["rpo_seconds_observed"] == 300.0
        assert report["rto_seconds_observed"] is None

    def test_future_restore_time_does_not_pass(self):
        """Clock skew produced a negative lag that read as 'very fresh'."""
        instance = dict(self.HEALTHY, LatestRestorableTime="2026-07-25T18:00:00Z")
        report = dr.evaluate_posture(instance, now_iso=self.NOW)
        assert report["verdict"] == "FAIL"
        assert "pitr_latest_restorable_recent" in report["blocking_failures"]

    def test_naive_timestamp_on_the_real_path_does_not_crash(self):
        """main() never passes now_iso, so this is the path production takes —
        and the one no test covered. A naive timestamp against an aware `now`
        raised TypeError, which the handler did not catch."""
        instance = dict(self.HEALTHY, LatestRestorableTime="2026-07-25T11:55:00")
        report = dr.evaluate_posture(instance)  # no now_iso — real path
        assert report["verdict"] in ("PASS", "FAIL")

    def test_unparseable_timestamp_fails_closed(self):
        instance = dict(self.HEALTHY, LatestRestorableTime="not-a-timestamp")
        report = dr.evaluate_posture(instance, now_iso=self.NOW)
        assert report["verdict"] == "FAIL"

    def test_report_is_json_serialisable_for_evidence(self):
        report = dr.evaluate_posture(self.HEALTHY, now_iso=self.NOW)
        assert json.loads(json.dumps(report, default=str))["verdict"] == "PASS"


class TestTestLoginQuarantine:
    """Staging-SHA gate row ops half — quarantine, never blind-delete."""

    def test_founder_and_ci_smoke_accounts_are_protected(self):
        """The founder account is a real operator login; the CI smoke user is
        re-created AND force-reactivated on every boot, so quarantining it
        would be undone by the next deploy."""
        assert quarantine.classify_officer("asudally@onboarda.com") == "protected"
        assert quarantine.classify_officer(
            "github-actions-day6-staging-smoke@onboarda.internal") == "protected"

    def test_seeded_personas_are_flagged(self):
        for email in ("raj.patel@onboarda.com", "m.dubois@onboarda.com",
                      "l.wei@onboarda.com"):
            assert quarantine.classify_officer(email) == "test-persona", email

    def test_synthetic_fixture_domains_are_flagged(self):
        assert quarantine.classify_officer("x@example.test") == "synthetic"
        assert quarantine.classify_client("y@fixture.invalid") == "synthetic"

    def test_real_operator_accounts_are_left_alone(self):
        assert quarantine.classify_officer("new.officer@onboarda.com") == "keep"
        assert quarantine.classify_client("realbank@example.com") == "keep"

    def test_dry_run_is_the_default(self):
        source = (BACKEND / "scripts" / "quarantine_staging_test_logins.py").read_text(
            encoding="utf-8")
        assert '"--apply"' in source
        assert "DRY-RUN" in source

    def test_script_has_no_delete_path_at_all(self):
        """An earlier revision offered --delete-unused behind a three-table
        activity check; review proved it would hard-delete an officer who had
        approved compliance memos (22 tables carry 48 REFERENCES users(id)
        columns). The flag was removed entirely rather than completed."""
        source = (BACKEND / "scripts" / "quarantine_staging_test_logins.py").read_text(
            encoding="utf-8")
        assert "delete_unused" not in source
        assert "DELETE FROM users" not in source
        assert "DELETE FROM clients" not in source

    @pytest.mark.parametrize("environment,allow,confirm", [
        ("", "1", quarantine.CONFIRM_TOKEN),          # env unset
        ("demo", "1", quarantine.CONFIRM_TOKEN),      # live demo environment
        ("production", "1", quarantine.CONFIRM_TOKEN),
        ("staging", "", quarantine.CONFIRM_TOKEN),    # allowlist var missing
        ("staging", "1", ""),                          # confirm token missing
        ("staging", "1", "wrong-token"),
    ])
    def test_guard_refuses_everything_but_a_fully_declared_staging_run(
            self, environment, allow, confirm):
        with pytest.raises(RuntimeError):
            quarantine.assert_quarantine_allowed(
                "postgresql://u@regmind-staging-db.example/regmind",
                environment, allow_value=allow, confirm=confirm)

    def test_guard_refuses_when_no_database_identity_resolves(self):
        """Absence of evidence is not evidence of staging."""
        with pytest.raises(RuntimeError):
            quarantine.assert_quarantine_allowed(
                "", "staging", allow_value="1", confirm=quarantine.CONFIRM_TOKEN)

    def test_guard_refuses_sqlite_and_production_markers(self):
        for dsn in ("sqlite:///arie.db",
                    "postgresql://u@prod-db.example/regmind",
                    "postgresql://u@host/regmind_production"):
            with pytest.raises(RuntimeError):
                quarantine.assert_quarantine_allowed(
                    dsn, "staging", allow_value="1",
                    confirm=quarantine.CONFIRM_TOKEN)

    def test_guard_accepts_a_fully_declared_staging_run(self):
        quarantine.assert_quarantine_allowed(
            "postgresql://u@regmind-staging-db.example/regmind",
            "staging", allow_value="1", confirm=quarantine.CONFIRM_TOKEN)


class TestQuarantineBehaviour:
    """Behavioural tests over plan/apply against a real SQLite database —
    review finding: the previous suite asserted source substrings and never
    called either function."""

    @pytest.fixture()
    def db(self, tmp_path):
        import sqlite3

        conn = sqlite3.connect(tmp_path / "quarantine.db")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, full_name TEXT,
                                role TEXT, status TEXT);
            CREATE TABLE clients (id TEXT PRIMARY KEY, email TEXT,
                                  company_name TEXT, status TEXT);
            CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    user_id TEXT, user_name TEXT, user_role TEXT,
                                    action TEXT, target TEXT, detail TEXT,
                                    ip_address TEXT, request_id TEXT);
        """)
        conn.executemany(
            "INSERT INTO users (id,email,full_name,role,status) VALUES (?,?,?,?,?)",
            [
                ("u_founder", "asudally@onboarda.com", "Founder", "admin", "active"),
                ("u_smoke", "github-actions-day6-staging-smoke@onboarda.internal",
                 "CI Smoke", "sco", "active"),
                ("u_raj", "raj.patel@onboarda.com", "Raj Patel", "sco", "active"),
                ("u_fix", "probe@example.test", "Fixture", "analyst", "active"),
                ("u_real", "new.officer@onboarda.com", "Real Officer", "co", "active"),
            ])
        conn.executemany(
            "INSERT INTO clients (id,email,company_name,status) VALUES (?,?,?,?)",
            [
                ("c_demo", "demo@onboarda.com", "Demo Client", "active"),
                ("c_real", "realbank@example.com", "Real Bank", "active"),
            ])
        conn.commit()

        class _Wrapper:
            def __init__(self, conn):
                self.conn = conn

            def execute(self, sql, params=()):
                self._cur = self.conn.execute(sql.replace("?", "?"), params)
                return self._cur

            def commit(self):
                self.conn.commit()

        return _Wrapper(conn)

    def test_plan_selects_only_synthetic_active_accounts(self, db):
        plan = quarantine.plan_quarantine(db)
        assert {o["email"] for o in plan["officers"]} == {
            "raj.patel@onboarda.com", "probe@example.test"}
        assert {c["email"] for c in plan["clients"]} == {"demo@onboarda.com"}

    def test_apply_deactivates_and_leaves_protected_and_real_accounts_active(self, db):
        quarantine.apply_quarantine(db, quarantine.plan_quarantine(db))
        statuses = {r["email"]: r["status"] for r in
                    db.execute("SELECT email, status FROM users").fetchall()}
        assert statuses["raj.patel@onboarda.com"] == "inactive"
        assert statuses["probe@example.test"] == "inactive"
        assert statuses["asudally@onboarda.com"] == "active"
        assert statuses["github-actions-day6-staging-smoke@onboarda.internal"] == "active"
        assert statuses["new.officer@onboarda.com"] == "active"

    def test_rerunning_the_plan_after_apply_is_empty(self, db):
        """The runbook tells the operator to re-run the dry-run and expect an
        empty list; a status-blind plan reported 'failed' after success and
        pushed them toward the destructive flag."""
        quarantine.apply_quarantine(db, quarantine.plan_quarantine(db))
        second = quarantine.plan_quarantine(db)
        assert second["officers"] == []
        assert second["clients"] == []

    def test_no_row_is_ever_deleted(self, db):
        before = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        quarantine.apply_quarantine(db, quarantine.plan_quarantine(db))
        after = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        assert after == before

    def test_every_status_change_writes_an_audit_row_with_before_and_after(self, db):
        result = quarantine.apply_quarantine(db, quarantine.plan_quarantine(db))
        rows = db.execute("SELECT action, target, detail FROM audit_log").fetchall()
        assert len(rows) == (len(result["deactivated_officers"])
                             + len(result["deactivated_clients"]))
        for row in rows:
            detail = json.loads(row["detail"])
            assert detail["before_state"] == {"status": "active"}
            assert detail["after_state"] == {"status": "inactive"}
            assert "quarantine" in detail["reason"]


class TestRunbookDocumentsEachOpsStep:
    def test_runbook_covers_all_three_new_items(self):
        assert "quarantine_staging_test_logins.py" in RUNBOOK
        assert "verify_dr_posture.py" in RUNBOOK
        assert "provision_production_monitoring.py" in RUNBOOK

    def test_runbook_states_the_dr_drill_is_operator_executed(self):
        assert "rto" in RUNBOOK.lower()
        assert "point-in-time" in RUNBOOK.lower() or "PITR" in RUNBOOK

    def test_runbook_warns_that_alarms_without_sns_page_nobody(self):
        assert "--alarm-action-arn" in RUNBOOK
