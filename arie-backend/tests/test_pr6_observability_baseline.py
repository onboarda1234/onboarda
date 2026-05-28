import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import observability
import verification_worker
from branding import BRAND
from verification_jobs import (
    MAX_PENDING_SECONDS,
    verification_queue_observability_snapshot,
)


class _Result:
    def __init__(self, *, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _FakeDb:
    def __init__(self, *, active_rows, stuck_rows, failed_last_hour=0):
        self.active_rows = active_rows
        self.stuck_rows = stuck_rows
        self.failed_last_hour = failed_last_hour

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "SELECT status, created_at, locked_at FROM verification_jobs" in normalized:
            return _Result(rows=self.active_rows)
        if "SELECT COUNT(*) AS count FROM verification_jobs" in normalized:
            return _Result(row={"count": self.failed_last_hour})
        if "SELECT * FROM verification_jobs WHERE (" in normalized:
            return _Result(rows=self.stuck_rows)
        raise AssertionError(f"Unexpected SQL in fake DB: {normalized}")


def _load_pr6_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "provision_pr6_observability.py"
    spec = importlib.util.spec_from_file_location("provision_pr6_observability", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _config():
    module = _load_pr6_script()
    system_id = BRAND["system_id"]
    return module.ObservabilityConfig(
        region="af-south-1",
        environment="staging",
        log_group=f"/ecs/{system_id}-staging",
        cluster_name=f"{system_id}-staging",
        backend_service=f"{system_id}-backend",
        worker_service=f"{system_id}-verification-worker",
        load_balancer_dimension=f"app/{system_id}-staging-alb/123",
        target_group_dimension=f"targetgroup/{system_id}-staging-tg/456",
        rds_instance=f"{system_id}-staging-db",
        alarm_action_arn=f"arn:aws:sns:af-south-1:123456789012:{system_id}-staging-pilot-alerts",
    )


def test_cloudwatch_metric_log_is_low_cardinality(monkeypatch):
    captured = {}

    def fake_log(level, message, **kwargs):
        captured["message"] = message
        captured["kwargs"] = kwargs

    monkeypatch.setattr(observability, "_log", fake_log)
    observability.emit_cloudwatch_metric_log(
        "VerificationQueueDepth",
        3,
        environment="staging",
        service="verification-worker",
    )

    assert captured["message"] == "cloudwatch_metric"
    assert captured["kwargs"] == {
        "metric_namespace": f"{BRAND['backoffice_name']}/Pilot",
        "metric_name": "VerificationQueueDepth",
        "metric_value": 3.0,
        "metric_unit": "Count",
        "environment": "staging",
        "service": "verification-worker",
    }
    assert "application_id" not in captured["kwargs"]
    assert "document_id" not in captured["kwargs"]
    assert "job_id" not in captured["kwargs"]


def test_verification_queue_snapshot_counts_active_stuck_and_failed_jobs():
    now = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
    stale_pending = (now - timedelta(seconds=MAX_PENDING_SECONDS + 30)).strftime("%Y-%m-%d %H:%M:%S")
    fresh_pending = (now - timedelta(seconds=12)).strftime("%Y-%m-%d %H:%M:%S")
    db = _FakeDb(
        active_rows=[
            {"status": "pending", "created_at": stale_pending, "locked_at": None},
            {"status": "in_progress", "created_at": fresh_pending, "locked_at": fresh_pending},
        ],
        stuck_rows=[{"id": "job_1", "status": "pending", "created_at": stale_pending}],
        failed_last_hour=2,
    )

    snapshot = verification_queue_observability_snapshot(db, now=now)

    assert snapshot["queue_depth"] == 2
    assert snapshot["stuck_jobs"] == 1
    assert snapshot["failed_last_hour"] == 2
    assert snapshot["oldest_pending_age_seconds"] >= MAX_PENDING_SECONDS
    assert snapshot["alert_destination"] == f"cloudwatch_alarm_{BRAND['system_id']}_pilot_alerts"


def test_pr6_alarm_specs_cover_required_critical_failure_modes():
    module = _load_pr6_script()
    alarms = {alarm["AlarmName"]: alarm for alarm in module.build_alarm_specs(_config())}

    expected = {
        "staging-api-target-5xx",
        "staging-alb-unhealthy-targets",
        "staging-backend-live-task-count-low",
        "staging-verification-worker-live-task-count-low",
        "staging-verification-queue-depth-high",
        "staging-verification-stuck-jobs",
        "staging-verification-oldest-pending-age-high",
        "staging-verification-latency-high",
        "staging-rds-cpu-high",
        "staging-rds-connections-high",
        "staging-rds-free-storage-low",
    }

    assert expected.issubset(alarms)
    assert alarms["staging-api-target-5xx"]["Threshold"] == 5
    assert alarms["staging-verification-stuck-jobs"]["Threshold"] == 1
    assert alarms["staging-rds-free-storage-low"]["Threshold"] == 2 * 1024 * 1024 * 1024
    assert all(alarm["AlarmActions"] for alarm in alarms.values())


def test_pr6_metric_filters_cover_verification_runtime_signals():
    module = _load_pr6_script()
    filters = module.build_metric_filters(_config())
    metric_names = {
        item["metricTransformations"][0]["metricName"] for item in filters
    }

    assert metric_names == {
        "VerificationQueueDepth",
        "VerificationStuckJobs",
        "VerificationOldestPendingAgeSeconds",
        "VerificationFailedJobsLastHour",
        "VerificationEndToEndJobMs",
        "VerificationWorkerFailures",
    }
    for item in filters:
        transformation = item["metricTransformations"][0]
        assert transformation["metricNamespace"] == f"{BRAND['backoffice_name']}/Pilot"
        assert transformation["metricValue"] == "$.metric_value"
        assert transformation["dimensions"] == {
            "Environment": "$.environment",
            "Service": "$.service",
        }


def test_worker_metric_emit_failure_is_non_blocking(monkeypatch):
    def raise_metric_error(*args, **kwargs):
        raise RuntimeError("metric sink unavailable")

    monkeypatch.setattr(verification_worker, "emit_cloudwatch_metric_log", raise_metric_error)

    verification_worker._safe_emit_worker_metric("VerificationWorkerFailures", 1)
