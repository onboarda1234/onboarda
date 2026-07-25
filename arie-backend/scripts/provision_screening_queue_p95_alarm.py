#!/usr/bin/env python3
"""Provision the screening-queue p95 latency metric filter + CloudWatch alarm.

Closes the Phase-6 ops ticket "CloudWatch p95 alarm on /api/screening/queue"
recorded in docs/REMEDIATION_MASTER_LIST.md (screening-queue audit stream).

The backend emits a low-cardinality ``cloudwatch_metric`` log line
(``ScreeningQueueLatencyMs``, Milliseconds) from ``ScreeningQueueHandler.get``
via ``observability.emit_cloudwatch_metric_log``. This script creates:

1. a CloudWatch Logs metric filter extracting that value from the ECS log
   group, and
2. a p95 alarm (ExtendedStatistic) on the resulting custom metric.

Mirrors ``provision_pr6_observability.py``: default mode is DRY-RUN and prints
the exact API calls; ``--apply`` creates/updates the AWS resources (requires
boto3 + credentials with logs:PutMetricFilter and cloudwatch:PutMetricAlarm).
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
METRIC_NAME = "ScreeningQueueLatencyMs"


def build_metric_filter(*, environment: str, log_group: str) -> Dict[str, Any]:
    return {
        "filterName": f"{environment}-{METRIC_NAME}",
        "logGroupName": log_group,
        "filterPattern": (
            '{ $.message = "cloudwatch_metric" '
            f'&& $.metric_namespace = "{CUSTOM_NAMESPACE}" '
            f'&& $.metric_name = "{METRIC_NAME}" '
            f'&& $.environment = "{environment}" }}'
        ),
        "metricTransformations": [
            {
                "metricName": METRIC_NAME,
                "metricNamespace": CUSTOM_NAMESPACE,
                "metricValue": "$.metric_value",
                "unit": "Milliseconds",
                "dimensions": {
                    "Environment": "$.environment",
                    "Service": "$.service",
                },
            }
        ],
    }


def build_alarm_spec(*, environment: str, alarm_action_arn: str,
                     threshold_ms: float) -> Dict[str, Any]:
    alarm_actions = [alarm_action_arn] if alarm_action_arn else []
    return {
        "AlarmName": f"{environment}-screening-queue-p95-latency-high",
        "AlarmDescription": (
            f"p95 latency of GET /api/screening/queue above {threshold_ms:.0f}ms "
            "for 3 of 3 five-minute periods."
        ),
        "Namespace": CUSTOM_NAMESPACE,
        "MetricName": METRIC_NAME,
        "Dimensions": [
            {"Name": "Environment", "Value": environment},
            {"Name": "Service", "Value": "backend"},
        ],
        # p95 requires ExtendedStatistic (Statistic only supports the basic five).
        "ExtendedStatistic": "p95",
        "Period": 300,
        "EvaluationPeriods": 3,
        "DatapointsToAlarm": 3,
        "Threshold": float(threshold_ms),
        "ComparisonOperator": "GreaterThanThreshold",
        "TreatMissingData": "notBreaching",
        "AlarmActions": alarm_actions,
        "OKActions": alarm_actions,
        "Tags": [
            {"Key": "Environment", "Value": environment},
            {"Key": "ManagedBy", "Value": "screening-queue-p95-alarm"},
        ],
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="af-south-1")
    parser.add_argument("--environment", default="staging")
    parser.add_argument("--log-group", default="/ecs/regmind-staging")
    parser.add_argument("--alarm-action-arn", default="",
                        help="SNS topic ARN for ALARM/OK actions (optional)")
    parser.add_argument("--threshold-ms", type=float, default=2000.0,
                        help="p95 threshold in milliseconds (default 2000)")
    parser.add_argument("--apply", action="store_true",
                        help="Create/update AWS resources (default: dry-run)")
    args = parser.parse_args(argv)

    metric_filter = build_metric_filter(
        environment=args.environment, log_group=args.log_group)
    alarm = build_alarm_spec(
        environment=args.environment,
        alarm_action_arn=args.alarm_action_arn,
        threshold_ms=args.threshold_ms)

    if not args.apply:
        print("DRY-RUN (pass --apply to create). Planned resources:\n")
        print("logs.put_metric_filter:")
        print(json.dumps(metric_filter, indent=2))
        print("\ncloudwatch.put_metric_alarm:")
        print(json.dumps(alarm, indent=2))
        return 0

    import boto3  # imported lazily so dry-run needs no AWS SDK

    logs = boto3.client("logs", region_name=args.region)
    cloudwatch = boto3.client("cloudwatch", region_name=args.region)
    logs.put_metric_filter(**metric_filter)
    print(f"Created/updated metric filter {metric_filter['filterName']}")
    cloudwatch.put_metric_alarm(**alarm)
    print(f"Created/updated alarm {alarm['AlarmName']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
