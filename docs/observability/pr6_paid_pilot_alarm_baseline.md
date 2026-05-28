# PR6 Paid-Pilot Alarm Baseline

PR6 adds a narrow alarm baseline for staging paid-pilot readiness. It does not
change screening provider behavior or product workflow gates.

## Signal Sources

- **ALB / API:** AWS/ApplicationELB target 5xx and unhealthy target metrics.
- **ECS:** AWS/ECS `LiveTaskCount` for backend and verification worker services.
- **Verification worker:** low-cardinality `cloudwatch_metric` log events emitted
  by the worker and converted to CloudWatch metrics with metric filters.
- **RDS:** AWS/RDS CPU, connection pressure, and free storage metrics.

## Verification Metrics

The worker emits these PII-safe metrics under `RegMind/Pilot`:

- `VerificationQueueDepth`
- `VerificationStuckJobs`
- `VerificationOldestPendingAgeSeconds`
- `VerificationFailedJobsLastHour`
- `VerificationEndToEndJobMs`
- `VerificationWorkerFailures`

Metric log payloads intentionally exclude application IDs, customer IDs,
document IDs, and job IDs.

## Provisioning

Dry run:

```bash
python3 arie-backend/scripts/provision_pr6_observability.py
```

Apply to staging:

```bash
python3 arie-backend/scripts/provision_pr6_observability.py --apply
```

The script creates or updates the `regmind-staging-pilot-alerts` SNS topic when
no `--alarm-action-arn` is supplied. Human paging requires a confirmed
subscription or an explicitly supplied alarm action ARN.

## Alarm Coverage

- `staging-api-target-5xx`
- `staging-alb-unhealthy-targets`
- `staging-backend-live-task-count-low`
- `staging-verification-worker-live-task-count-low`
- `staging-verification-queue-depth-high`
- `staging-verification-stuck-jobs`
- `staging-verification-oldest-pending-age-high`
- `staging-verification-latency-high`
- `staging-rds-cpu-high`
- `staging-rds-connections-high`
- `staging-rds-free-storage-low`

Thresholds are intentionally conservative enough to avoid alerting on one-off
slow or failed jobs while still paging for sustained degradation, stuck queues,
service outage, and database pressure.
