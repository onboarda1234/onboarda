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

    def test_error_filter_counts_only_error_level(self):
        """4xx is logged at WARNING on purpose, so an ERROR-level filter counts
        genuine server-side failures and cannot be driven by unauthenticated
        callers."""
        spec = monitoring.build_error_rate_filter(
            environment="staging", log_group="/ecs/regmind-staging")
        assert spec["filterPattern"] == '{ $.level = "ERROR" }'
        transformation = spec["metricTransformations"][0]
        assert transformation["defaultValue"] == 0
        # No Service dimension: the stdlib logger path does not always attach
        # structured_data, and a sometimes-absent dimension drops datapoints.
        assert "Service" not in transformation["dimensions"]

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
            assert {"Name": "Environment", "Value": "production"} in alarm["Dimensions"]

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

    def test_production_identity_is_refused(self):
        with pytest.raises(RuntimeError):
            quarantine.assert_not_production(
                "postgresql://u@host/db", "production")
        with pytest.raises(RuntimeError):
            quarantine.assert_not_production(
                "postgresql://u@prod-db.example/regmind", "staging")
        # A clearly non-production identity is allowed.
        quarantine.assert_not_production(
            "postgresql://u@regmind-staging-db.example/regmind", "staging")

    def test_dry_run_is_the_default(self):
        source = (BACKEND / "scripts" / "quarantine_staging_test_logins.py").read_text(
            encoding="utf-8")
        assert '"--apply"' in source
        assert "DRY-RUN" in source
        # --delete-unused must never act on its own.
        assert "delete_unused=args.delete_unused" in source

    def test_accounts_with_activity_are_never_hard_deleted(self):
        """~20 tables reference users(id); deleting an officer who ever acted
        would orphan referential history AML recordkeeping depends on."""
        source = (BACKEND / "scripts" / "quarantine_staging_test_logins.py").read_text(
            encoding="utf-8")
        assert 'if delete_unused and not officer["has_activity"]:' in source

    def test_missing_activity_table_is_treated_as_activity(self):
        """A missing table must never be read as 'no activity' — that would
        turn a schema change into silent data loss."""
        class _RaisingDb:
            def execute(self, *_args, **_kwargs):
                raise RuntimeError("no such table")

        assert quarantine._has_activity(_RaisingDb(), "any") is True


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
