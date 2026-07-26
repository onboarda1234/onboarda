#!/usr/bin/env python3
"""Provision the production monitoring/alerting baseline (P9-10 / DCI-030 / FEO-011).

`provision_pr6_observability.py` covers the six ``Verification*`` metrics and
the infra-level ALB/ECS/RDS alarms. Seven metrics the workers and backend
ALREADY emit have no metric filter and no alarm at all:

    ScreeningQueueDepth                  ScreeningFailedJobsLastHour
    ScreeningInProgressJobs              ScreeningEndToEndJobMs
    ScreeningOldestPendingAgeSeconds     ScreeningWorkerFailures
    SchemaDriftMissingObjects (emitted once per BOOT, not on a timer — its
    alarm therefore only sees data after a restart, by design)

plus ``VerificationWorkerFailures`` and ``VerificationFailedJobsLastHour``,
which have filters but no alarm (this script alarms the first; the second is
left to the PR-6 baseline that owns it). This script closes that gap — no application change is required, the telemetry is
already being written.

It also adds the two signals the register calls out that infra alarms cannot
see:

* **Worker liveness.** ``staging-verification-worker-live-task-count-low``
  alarms when no ECS task is running, but not when a task is running and its
  loop is wedged. The workers emit their gauge metrics at least once per ~60s
  even when idle (``DEFAULT_OBSERVABILITY_INTERVAL_SECONDS``; the emitter has
  no empty-queue early return and coalesces via ``or 0``), so a
  ``TreatMissingData: breaching`` alarm on the queue-depth SampleCount is a
  true heartbeat. NOTE this is deliberately the OPPOSITE posture to the
  backlog alarm THIS script creates on the same metric: for a threshold,
  missing data is not a breach; for a heartbeat, absence IS the alarm.
* **Application error rate.** The only 5xx signal today is the ALB metric. A
  log-based filter on ``$.level = "ERROR"`` catches failures that never reach
  the load balancer (worker exceptions, boot-path errors) and gives the
  "ERROR = 0 in the validation window" convention a real metric.

Mirrors `provision_screening_queue_p95_alarm.py`: builders are pure and return
plain dicts (so they can be unit-tested without AWS), dry-run is the default,
and boto3 is imported lazily so a dry-run needs no SDK.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from branding import BRAND  # noqa: E402

CUSTOM_NAMESPACE = f"{BRAND['backoffice_name']}/Pilot"

# (metric name, unit, emitting service dimension)
UNALARMED_METRICS = (
    ("ScreeningQueueDepth", "Count", "verification-worker"),
    ("ScreeningInProgressJobs", "Count", "verification-worker"),
    ("ScreeningOldestPendingAgeSeconds", "Seconds", "verification-worker"),
    ("ScreeningFailedJobsLastHour", "Count", "verification-worker"),
    ("ScreeningEndToEndJobMs", "Milliseconds", "verification-worker"),
    ("ScreeningWorkerFailures", "Count", "verification-worker"),
    ("SchemaDriftMissingObjects", "Count", "backend"),
)

ERROR_METRIC_NAME = "ApplicationErrorCount"


HEARTBEAT_METRIC = "ScreeningQueueDepth"


def apply_refusal(*, apply: bool, environment: str,
                  confirm_production: bool, alarm_action_arn: str) -> str:
    """Return a fail-closed refusal reason before any AWS client is loaded."""
    if not apply:
        return ""
    if environment == "production" and not confirm_production:
        return "REFUSED: --apply against production requires --confirm-production."
    if not str(alarm_action_arn or "").strip():
        return (
            "REFUSED: --apply requires --alarm-action-arn; actionless alarms "
            "page nobody. Confirm the SNS subscription before applying."
        )
    return ""


def error_metric_name(environment: str) -> str:
    """Environment goes in the NAME because the metric carries no dimensions."""
    return f"{ERROR_METRIC_NAME}-{environment}"


def build_metric_filter(*, environment: str, log_group: str, metric_name: str,
                        unit: str) -> Dict[str, Any]:
    """Metric filter for one emitted ``cloudwatch_metric`` log line.

    The pattern matches the exact JSON shape written by
    ``observability.emit_cloudwatch_metric_log``.
    """
    return {
        "filterName": f"{environment}-{metric_name}",
        "logGroupName": log_group,
        "filterPattern": (
            '{ $.message = "cloudwatch_metric" '
            f'&& $.metric_namespace = "{CUSTOM_NAMESPACE}" '
            f'&& $.metric_name = "{metric_name}" '
            f'&& $.environment = "{environment}" }}'
        ),
        "metricTransformations": [
            {
                "metricName": metric_name,
                "metricNamespace": CUSTOM_NAMESPACE,
                "metricValue": "$.metric_value",
                "unit": unit,
                "dimensions": {
                    "Environment": "$.environment",
                    "Service": "$.service",
                },
            }
        ],
    }


def build_error_rate_filter(*, environment: str, log_group: str) -> Dict[str, Any]:
    """Count structured ERROR records.

    4xx is logged at WARNING on purpose (base_handler: a client mistake is not
    a service error and must not give an unauthenticated caller an ERROR-log
    spam vector), so this filter counts only genuine server-side failures.

    NO DIMENSIONS, deliberately (adversarial review B1). ERROR records carry
    only timestamp/level/logger/message — `environment` is injected solely by
    emit_cloudwatch_metric_log, so a `{"Environment": "$.environment"}`
    dimension would match nothing and leave a dead alarm sitting in OK,
    indistinguishable from healthy. Dimensions and `defaultValue` are also
    mutually exclusive in PutMetricFilter, so that pairing fails at --apply.
    Environment scoping comes from the metric NAME instead, since each
    environment has its own log group.
    """
    return {
        "filterName": f"{environment}-{ERROR_METRIC_NAME}",
        "logGroupName": log_group,
        "filterPattern": '{ $.level = "ERROR" }',
        "metricTransformations": [
            {
                "metricName": error_metric_name(environment),
                "metricNamespace": CUSTOM_NAMESPACE,
                "metricValue": "1",
                # Safe now that there are no dimensions: makes "no errors"
                # publish a 0 rather than a gap, so the alarm is evaluable.
                "defaultValue": 0,
                "unit": "Count",
            }
        ],
    }


def _alarm(*, name: str, description: str, metric_name: str, environment: str,
           service: str, statistic: str, period: int, evaluation_periods: int,
           datapoints: int, threshold: float, comparison: str,
           treat_missing: str, alarm_action_arn: str,
           dimensionless: bool = False,
           managed_by: str = "production-monitoring") -> Dict[str, Any]:
    actions = [alarm_action_arn] if alarm_action_arn else []
    # An alarm's dimensions must match the metric's exactly; a dimensionless
    # metric with a dimensioned alarm resolves to nothing.
    dimensions = ([] if dimensionless
                  else [{"Name": "Environment", "Value": environment}])
    spec: Dict[str, Any] = {
        "AlarmName": name,
        "AlarmDescription": description,
        "Namespace": CUSTOM_NAMESPACE,
        "MetricName": metric_name,
        "Dimensions": dimensions,
        "Statistic": statistic,
        "Period": period,
        "EvaluationPeriods": evaluation_periods,
        "DatapointsToAlarm": datapoints,
        "Threshold": float(threshold),
        "ComparisonOperator": comparison,
        "TreatMissingData": treat_missing,
        "AlarmActions": actions,
        "OKActions": actions,
        "Tags": [
            {"Key": "Environment", "Value": environment},
            {"Key": "ManagedBy", "Value": managed_by},
        ],
    }
    if service:
        spec["Dimensions"].append({"Name": "Service", "Value": service})
    return spec


def build_alarm_specs(*, environment: str, alarm_action_arn: str,
                      screening_backlog_threshold: int,
                      screening_age_threshold_s: int,
                      error_threshold: int) -> List[Dict[str, Any]]:
    worker = "verification-worker"
    return [
        _alarm(
            name=f"{environment}-screening-queue-backlog-high",
            description=(
                f"Screening queue depth above {screening_backlog_threshold} for "
                "3 of 3 five-minute periods."),
            metric_name="ScreeningQueueDepth", environment=environment,
            service=worker, statistic="Maximum", period=300,
            evaluation_periods=3, datapoints=3,
            threshold=screening_backlog_threshold,
            comparison="GreaterThanThreshold", treat_missing="notBreaching",
            alarm_action_arn=alarm_action_arn,
        ),
        _alarm(
            name=f"{environment}-screening-oldest-pending-age-high",
            description=(
                f"Oldest pending screening job older than "
                f"{screening_age_threshold_s}s — jobs are not draining."),
            metric_name="ScreeningOldestPendingAgeSeconds",
            environment=environment, service=worker, statistic="Maximum",
            period=300, evaluation_periods=2, datapoints=2,
            threshold=screening_age_threshold_s,
            comparison="GreaterThanThreshold", treat_missing="notBreaching",
            alarm_action_arn=alarm_action_arn,
        ),
        _alarm(
            name=f"{environment}-screening-worker-failures",
            description="Screening worker reported a failure in the last period.",
            metric_name="ScreeningWorkerFailures", environment=environment,
            service=worker, statistic="Sum", period=300,
            evaluation_periods=1, datapoints=1, threshold=0,
            comparison="GreaterThanThreshold", treat_missing="notBreaching",
            alarm_action_arn=alarm_action_arn,
        ),
        _alarm(
            name=f"{environment}-verification-worker-failures",
            description="Verification worker reported a failure in the last period.",
            metric_name="VerificationWorkerFailures", environment=environment,
            service=worker, statistic="Sum", period=300,
            evaluation_periods=1, datapoints=1, threshold=0,
            comparison="GreaterThanThreshold", treat_missing="notBreaching",
            alarm_action_arn=alarm_action_arn,
        ),
        # Liveness: the workers publish this gauge at least once per ~60s even
        # when idle, so NO datapoint for 10 minutes means the loop is wedged
        # (or the task is gone). `breaching` is the whole point — do not copy
        # the notBreaching posture used by the threshold alarms above.
        _alarm(
            name=f"{environment}-verification-worker-heartbeat-missing",
            description=(
                "No worker observability datapoint for 2 consecutive 5-minute "
                "periods — the worker loop is wedged or the task is gone. "
                "Complements the ECS LiveTaskCount alarm, which cannot see a "
                "running-but-stuck task."),
            metric_name=HEARTBEAT_METRIC, environment=environment,
            service=worker, statistic="SampleCount", period=300,
            evaluation_periods=2, datapoints=2, threshold=1,
            comparison="LessThanThreshold", treat_missing="breaching",
            alarm_action_arn=alarm_action_arn,
        ),
        _alarm(
            name=f"{environment}-application-error-rate-high",
            description=(
                f"More than {error_threshold} ERROR log records in a 5-minute "
                "period. Catches server-side failures that never reach the "
                "ALB (worker exceptions, boot-path errors)."),
            metric_name=error_metric_name(environment), environment=environment,
            service="", statistic="Sum", period=300, dimensionless=True,
            evaluation_periods=1, datapoints=1, threshold=error_threshold,
            comparison="GreaterThanThreshold", treat_missing="notBreaching",
            alarm_action_arn=alarm_action_arn,
        ),
        _alarm(
            name=f"{environment}-schema-drift-detected",
            description=(
                "Boot-time schema drift check reported missing objects — the "
                "deployed schema does not match the manifest."),
            metric_name="SchemaDriftMissingObjects", environment=environment,
            service="backend", statistic="Maximum", period=300,
            evaluation_periods=1, datapoints=1, threshold=0,
            comparison="GreaterThanThreshold", treat_missing="notBreaching",
            alarm_action_arn=alarm_action_arn,
        ),
    ]


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="af-south-1")
    parser.add_argument("--environment", default="staging")
    parser.add_argument("--log-group", default="/ecs/regmind-staging")
    parser.add_argument(
        "--alarm-action-arn",
        default="",
        help=(
            "SNS topic ARN for ALARM/OK actions; required with --apply "
            "(the topic must have a confirmed subscription)"
        ),
    )
    parser.add_argument("--screening-backlog-threshold", type=int, default=50)
    parser.add_argument("--screening-age-threshold-s", type=int, default=1800)
    parser.add_argument("--error-threshold", type=int, default=5)
    parser.add_argument("--apply", action="store_true",
                        help="Create/update AWS resources (default: dry-run)")
    parser.add_argument("--confirm-production", action="store_true",
                        help="Required with --apply when --environment=production")
    args = parser.parse_args(argv)

    refusal = apply_refusal(
        apply=args.apply,
        environment=args.environment,
        confirm_production=args.confirm_production,
        alarm_action_arn=args.alarm_action_arn,
    )
    if refusal:
        print(refusal, file=sys.stderr)
        return 2

    filters = [
        build_metric_filter(environment=args.environment,
                            log_group=args.log_group,
                            metric_name=name, unit=unit)
        for name, unit, _service in UNALARMED_METRICS
    ]
    filters.append(build_error_rate_filter(environment=args.environment,
                                           log_group=args.log_group))
    alarms = build_alarm_specs(
        environment=args.environment,
        alarm_action_arn=args.alarm_action_arn,
        screening_backlog_threshold=args.screening_backlog_threshold,
        screening_age_threshold_s=args.screening_age_threshold_s,
        error_threshold=args.error_threshold,
    )

    if not args.apply:
        print("DRY-RUN (pass --apply to create). Planned resources:\n")
        print(f"{len(filters)} logs.put_metric_filter calls:")
        print(json.dumps(filters, indent=2))
        print(f"\n{len(alarms)} cloudwatch.put_metric_alarm calls:")
        print(json.dumps(alarms, indent=2))
        if not args.alarm_action_arn:
            print("\nNOTE: no --alarm-action-arn given — alarms will have no "
                  "ALARM/OK actions and will page nobody.")
        return 0

    import boto3  # imported lazily so dry-run needs no AWS SDK

    logs = boto3.client("logs", region_name=args.region)
    cloudwatch = boto3.client("cloudwatch", region_name=args.region)
    for metric_filter in filters:
        logs.put_metric_filter(**metric_filter)
        print(f"Created/updated metric filter {metric_filter['filterName']}")
    for alarm in alarms:
        cloudwatch.put_metric_alarm(**alarm)
        print(f"Created/updated alarm {alarm['AlarmName']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
