#!/usr/bin/env python3
"""Provision the PR6 paid-pilot observability baseline.

The script is intentionally small and explicit: it creates CloudWatch metric
filters for app-emitted verification metrics and CloudWatch alarms for ALB,
ECS, verification queue, and RDS critical failure modes.

Default mode is dry-run. Use --apply to create/update AWS resources.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from branding import BRAND  # noqa: E402

CUSTOM_NAMESPACE = f"{BRAND['backoffice_name']}/Pilot"
DEFAULT_TOPIC_NAME = f"{BRAND['system_id']}-staging-pilot-alerts"


@dataclass(frozen=True)
class ObservabilityConfig:
    region: str
    environment: str
    log_group: str
    cluster_name: str
    backend_service: str
    worker_service: str
    load_balancer_dimension: str
    target_group_dimension: str
    rds_instance: str
    alarm_action_arn: str


def _dimension(name: str, value: str) -> Dict[str, str]:
    return {"Name": name, "Value": value}


def build_metric_filters(config: ObservabilityConfig) -> List[Dict[str, Any]]:
    filters: List[Dict[str, Any]] = []
    for metric_name, unit in (
        ("VerificationQueueDepth", "Count"),
        ("VerificationStuckJobs", "Count"),
        ("VerificationOldestPendingAgeSeconds", "Seconds"),
        ("VerificationFailedJobsLastHour", "Count"),
        ("VerificationEndToEndJobMs", "Milliseconds"),
        ("VerificationWorkerFailures", "Count"),
    ):
        filters.append(
            {
                "filterName": f"{config.environment}-{metric_name}",
                "logGroupName": config.log_group,
                "filterPattern": (
                    '{ $.message = "cloudwatch_metric" '
                    f'&& $.metric_namespace = "{CUSTOM_NAMESPACE}" '
                    f'&& $.metric_name = "{metric_name}" '
                    f'&& $.environment = "{config.environment}" }}'
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
        )
    return filters


def build_alarm_specs(config: ObservabilityConfig) -> List[Dict[str, Any]]:
    alarm_actions = [config.alarm_action_arn] if config.alarm_action_arn else []
    common = {
        "AlarmActions": alarm_actions,
        "OKActions": alarm_actions,
        "TreatMissingData": "notBreaching",
        "Tags": [
            {"Key": "Environment", "Value": config.environment},
            {"Key": "ManagedBy", "Value": "pr6-observability-baseline"},
        ],
    }
    ecs_backend_dimensions = [
        _dimension("ClusterName", config.cluster_name),
        _dimension("ServiceName", config.backend_service),
    ]
    ecs_worker_dimensions = [
        _dimension("ClusterName", config.cluster_name),
        _dimension("ServiceName", config.worker_service),
    ]
    custom_worker_dimensions = [
        _dimension("Environment", config.environment),
        _dimension("Service", "verification-worker"),
    ]
    return [
        {
            **common,
            "AlarmName": f"{config.environment}-api-target-5xx",
            "AlarmDescription": "ALB target 5xx spike for RegMind API.",
            "Namespace": "AWS/ApplicationELB",
            "MetricName": "HTTPCode_Target_5XX_Count",
            "Dimensions": [_dimension("LoadBalancer", config.load_balancer_dimension)],
            "Statistic": "Sum",
            "Period": 300,
            "EvaluationPeriods": 1,
            "DatapointsToAlarm": 1,
            "Threshold": 5,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
        {
            **common,
            "AlarmName": f"{config.environment}-alb-unhealthy-targets",
            "AlarmDescription": "ALB has unhealthy backend targets.",
            "Namespace": "AWS/ApplicationELB",
            "MetricName": "UnHealthyHostCount",
            "Dimensions": [
                _dimension("LoadBalancer", config.load_balancer_dimension),
                _dimension("TargetGroup", config.target_group_dimension),
            ],
            "Statistic": "Maximum",
            "Period": 60,
            "EvaluationPeriods": 2,
            "DatapointsToAlarm": 2,
            "Threshold": 1,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
        {
            **common,
            "AlarmName": f"{config.environment}-backend-live-task-count-low",
            "AlarmDescription": "Backend ECS service has no live task.",
            "Namespace": "AWS/ECS",
            "MetricName": "LiveTaskCount",
            "Dimensions": ecs_backend_dimensions,
            "Statistic": "Minimum",
            "Period": 60,
            "EvaluationPeriods": 2,
            "DatapointsToAlarm": 2,
            "Threshold": 1,
            "ComparisonOperator": "LessThanThreshold",
        },
        {
            **common,
            "AlarmName": f"{config.environment}-verification-worker-live-task-count-low",
            "AlarmDescription": "Verification worker ECS service has no live task.",
            "Namespace": "AWS/ECS",
            "MetricName": "LiveTaskCount",
            "Dimensions": ecs_worker_dimensions,
            "Statistic": "Minimum",
            "Period": 60,
            "EvaluationPeriods": 2,
            "DatapointsToAlarm": 2,
            "Threshold": 1,
            "ComparisonOperator": "LessThanThreshold",
        },
        {
            **common,
            "AlarmName": f"{config.environment}-verification-queue-depth-high",
            "AlarmDescription": "Verification queue depth is high for sustained period.",
            "Namespace": CUSTOM_NAMESPACE,
            "MetricName": "VerificationQueueDepth",
            "Dimensions": custom_worker_dimensions,
            "Statistic": "Maximum",
            "Period": 300,
            "EvaluationPeriods": 3,
            "DatapointsToAlarm": 3,
            "Threshold": 50,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
        {
            **common,
            "AlarmName": f"{config.environment}-verification-stuck-jobs",
            "AlarmDescription": "Verification queue has stuck jobs.",
            "Namespace": CUSTOM_NAMESPACE,
            "MetricName": "VerificationStuckJobs",
            "Dimensions": custom_worker_dimensions,
            "Statistic": "Maximum",
            "Period": 300,
            "EvaluationPeriods": 2,
            "DatapointsToAlarm": 1,
            "Threshold": 1,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
        {
            **common,
            "AlarmName": f"{config.environment}-verification-oldest-pending-age-high",
            "AlarmDescription": "Oldest active verification job exceeds the pending SLA.",
            "Namespace": CUSTOM_NAMESPACE,
            "MetricName": "VerificationOldestPendingAgeSeconds",
            "Dimensions": custom_worker_dimensions,
            "Statistic": "Maximum",
            "Period": 300,
            "EvaluationPeriods": 2,
            "DatapointsToAlarm": 2,
            "Threshold": 900,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
        {
            **common,
            "AlarmName": f"{config.environment}-verification-latency-high",
            "AlarmDescription": "Verification end-to-end latency is high.",
            "Namespace": CUSTOM_NAMESPACE,
            "MetricName": "VerificationEndToEndJobMs",
            "Dimensions": custom_worker_dimensions,
            "ExtendedStatistic": "p95",
            "Period": 300,
            "EvaluationPeriods": 3,
            "DatapointsToAlarm": 3,
            "Threshold": 300000,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
        {
            **common,
            "AlarmName": f"{config.environment}-rds-cpu-high",
            "AlarmDescription": "RDS CPU is high for sustained period.",
            "Namespace": "AWS/RDS",
            "MetricName": "CPUUtilization",
            "Dimensions": [_dimension("DBInstanceIdentifier", config.rds_instance)],
            "Statistic": "Average",
            "Period": 300,
            "EvaluationPeriods": 3,
            "DatapointsToAlarm": 3,
            "Threshold": 85,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
        {
            **common,
            "AlarmName": f"{config.environment}-rds-connections-high",
            "AlarmDescription": "RDS connection pressure is high.",
            "Namespace": "AWS/RDS",
            "MetricName": "DatabaseConnections",
            "Dimensions": [_dimension("DBInstanceIdentifier", config.rds_instance)],
            "Statistic": "Average",
            "Period": 300,
            "EvaluationPeriods": 3,
            "DatapointsToAlarm": 3,
            "Threshold": 60,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
        {
            **common,
            "AlarmName": f"{config.environment}-rds-free-storage-low",
            "AlarmDescription": "RDS free storage is below 2 GiB.",
            "Namespace": "AWS/RDS",
            "MetricName": "FreeStorageSpace",
            "Dimensions": [_dimension("DBInstanceIdentifier", config.rds_instance)],
            "Statistic": "Average",
            "Period": 300,
            "EvaluationPeriods": 3,
            "DatapointsToAlarm": 3,
            "Threshold": 2 * 1024 * 1024 * 1024,
            "ComparisonOperator": "LessThanOrEqualToThreshold",
        },
    ]


def summarize(config: ObservabilityConfig) -> Dict[str, Any]:
    return {
        "metric_filters": [
            item["filterName"] for item in build_metric_filters(config)
        ],
        "alarms": [
            {
                "name": item["AlarmName"],
                "metric": f"{item['Namespace']}/{item['MetricName']}",
                "threshold": item["Threshold"],
                "operator": item["ComparisonOperator"],
            }
            for item in build_alarm_specs(config)
        ],
        "alarm_action_arn": config.alarm_action_arn,
    }


def _resource_dimension_from_arn(arn: str, marker: str) -> str:
    index = arn.index(marker)
    if marker == "loadbalancer/":
        return arn[index + len(marker):]
    return arn[index:]


def resolve_config(args, *, create_topic: bool = False) -> ObservabilityConfig:
    import boto3

    elbv2 = boto3.client("elbv2", region_name=args.region)
    alarm_action_arn = args.alarm_action_arn
    if not alarm_action_arn:
        if create_topic:
            sns = boto3.client("sns", region_name=args.region)
            topic = sns.create_topic(Name=args.alarm_topic_name)
            alarm_action_arn = topic["TopicArn"]
        else:
            alarm_action_arn = (
                f"arn:aws:sns:{args.region}:000000000000:{args.alarm_topic_name}"
            )

    load_balancer = elbv2.describe_load_balancers(Names=[args.alb_name])["LoadBalancers"][0]
    target_group = elbv2.describe_target_groups(Names=[args.target_group_name])["TargetGroups"][0]
    return ObservabilityConfig(
        region=args.region,
        environment=args.environment,
        log_group=args.log_group,
        cluster_name=args.cluster,
        backend_service=args.backend_service,
        worker_service=args.worker_service,
        load_balancer_dimension=_resource_dimension_from_arn(load_balancer["LoadBalancerArn"], "loadbalancer/"),
        target_group_dimension=_resource_dimension_from_arn(target_group["TargetGroupArn"], "targetgroup/"),
        rds_instance=args.rds_instance,
        alarm_action_arn=alarm_action_arn,
    )


def apply_resources(config: ObservabilityConfig) -> None:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    from observability import log_error

    logs = boto3.client("logs", region_name=config.region)
    cloudwatch = boto3.client("cloudwatch", region_name=config.region)
    for metric_filter in build_metric_filters(config):
        logs.put_metric_filter(**metric_filter)
    for alarm in build_alarm_specs(config):
        tags = alarm.pop("Tags", None)
        cloudwatch.put_metric_alarm(**alarm)
        if tags:
            try:
                alarm_arn = (
                    f"arn:aws:cloudwatch:{config.region}:"
                    f"{config.alarm_action_arn.split(':')[4]}:alarm:{alarm['AlarmName']}"
                )
                cloudwatch.tag_resource(ResourceARN=alarm_arn, Tags=tags)
            except (BotoCoreError, ClientError) as exc:
                log_error(
                    "cloudwatch_alarm_tagging_failed",
                    handler="provision_pr6_observability",
                    alarm_name=alarm["AlarmName"],
                    alarm_arn=alarm_arn,
                    error_type=type(exc).__name__,
                )


def parse_args(argv: Optional[Iterable[str]] = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create or update metric filters and alarms")
    parser.add_argument("--region", default="af-south-1")
    parser.add_argument("--environment", default="staging")
    parser.add_argument("--cluster", default="regmind-staging")
    parser.add_argument("--backend-service", default="regmind-backend")
    parser.add_argument("--worker-service", default="regmind-verification-worker")
    parser.add_argument("--alb-name", default="regmind-staging-alb")
    parser.add_argument("--target-group-name", default="regmind-staging-tg")
    parser.add_argument("--rds-instance", default="regmind-staging-db")
    parser.add_argument("--log-group", default="/ecs/regmind-staging")
    parser.add_argument("--alarm-topic-name", default=DEFAULT_TOPIC_NAME)
    parser.add_argument("--alarm-action-arn", default="")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    config = resolve_config(args, create_topic=args.apply)
    summary = summarize(config)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.apply:
        apply_resources(config)
        print(json.dumps({"applied": True, "alarm_action_arn": config.alarm_action_arn}, sort_keys=True))
    else:
        print(json.dumps({"dry_run": True, "apply_hint": "rerun with --apply"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
