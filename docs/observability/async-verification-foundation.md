# Async Verification Foundation

PR6 adds the dark async-verification foundation behind `FF_ASYNC_VERIFY=false`.
The default synchronous verification path remains authoritative until PR7
explicitly flips the flag in staging.

## Invariant

- `documents.verification_status` and `documents.verification_results` remain
  the compatibility fields read by portal and Back Office.
- `verification_jobs` is a worker coordination table, not a replacement source
  of truth for document state.
- System-driven transitions use `actor_type=system` and include `job_id` and
  `worker_id` in the audit detail.
- Screening provider selection, Sumsub timing, and ComplyAdvantage activation
  are unchanged by this PR.

## SLA Contract

- Maximum pending age: 900 seconds.
- Maximum in-progress age: 1200 seconds.
- Stuck-job threshold: 1200 seconds.
- Retry backoff: 120 seconds.
- Maximum attempts: 3.
- Alert destination: saved CloudWatch query
  `verification_async_stuck_jobs.cwlogs`, routed to compliance operations.
- Manual recovery: inspect the provider/file failure, resolve the root cause,
  then requeue the failed job or rerun synchronous verification from Back
  Office while the async flag remains off.

## Sumsub / Mesh Hazard Note

This PR does not alter Sumsub applicant creation, configured screening-provider checks, screening
provider selection, ComplyAdvantage abstraction state, or downstream screening
workflow timing. If PR7 later enables async verification, soak validation must
confirm that any downstream logic expecting immediate document verification
completion still sees truthful `pending`/`in_progress` states and does not treat
queued jobs as approval evidence.

## PR7A Worker Runtime

PR7A adds `arie-backend/verification_worker.py`, a separate-process runtime for
the PR6 Postgres-backed queue. The worker:

- runs from the same API image;
- claims jobs from `verification_jobs`;
- reuses the existing synchronous document-verification handler path with
  `force_sync=True`;
- marks jobs terminal after the document compatibility fields are updated;
- writes system-actor audit entries with `worker_id` and `job_id`;
- reclaims stale `in_progress` locks as `retrying` while attempts remain;
- does not use `resilience/task_queue.py`;
- does not activate ComplyAdvantage or change screening-provider selection.

## Staging ECS Worker Shape

The staging deploy workflow now updates the verification worker service with
the same SHA-pinned image used by the API service. Manual deployment remains a
break-glass fallback only.

Worker service shape:

- Cluster: `regmind-staging`.
- Service name: `regmind-verification-worker`.
- Desired count: `1`.
- Load balancer: none.
- Image: same SHA-pinned image as `regmind-backend`.
- Task role, execution role, secrets, and environment: same as API, except the
  command override below.
- Network: same VPC subnets and security group as API.
- Deployment circuit breaker: enabled with rollback when supported.
- Command: `python verification_worker.py --poll-interval 5`.

Task-definition JSON delta from the active API task definition:

```json
{
  "family": "regmind-verification-worker",
  "containerDefinitions": [
    {
      "name": "regmind-backend",
      "command": ["python", "verification_worker.py", "--poll-interval", "5"]
    }
  ]
}
```

Break-glass CLI outline, using the active API task definition as the source:

```bash
aws ecs describe-task-definition \
  --region af-south-1 \
  --task-definition regmind-staging \
  --query taskDefinition > /tmp/regmind-api-taskdef.json

# Remove read-only taskDefinitionArn/revision/status/compatibilities/
# registeredAt/registeredBy/requiresAttributes fields, change family to
# regmind-verification-worker, and set the container command above.

aws ecs register-task-definition \
  --region af-south-1 \
  --cli-input-json file:///tmp/regmind-worker-taskdef.json

aws ecs create-service \
  --region af-south-1 \
  --cluster regmind-staging \
  --service-name regmind-verification-worker \
  --task-definition regmind-verification-worker \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[subnet-06d572e471e13e00e,subnet-057856991d61eb646],securityGroups=[sg-07668458bac8df943],assignPublicIp=ENABLED}' \
  --deployment-configuration 'deploymentCircuitBreaker={enable=true,rollback=true}'
```

Post-deploy smoke checks:

```bash
aws ecs wait services-stable \
  --region af-south-1 \
  --cluster regmind-staging \
  --services regmind-verification-worker

aws ecs describe-services \
  --region af-south-1 \
  --cluster regmind-staging \
  --services regmind-verification-worker
```

The runtime gate is not satisfied by a running service alone. Staging must also
prove:

- backend `/api/version` matches merged main;
- backend and worker ECS task definitions use the same SHA-pinned image, unless
  a version-pinned exception is explicitly documented;
- worker desired/running counts are healthy;
- one controlled queued job moves `pending -> in_progress -> terminal`;
- `documents.verification_status` and `documents.verification_results` update;
- system audit rows include `worker_id` and `job_id`;
- no screening-provider regression is introduced.

Read-only runtime baseline:

```bash
python arie-backend/scripts/staging_runtime_baseline.py \
  --expected-sha <merged-main-sha> \
  --strict
```

Safe synthetic worker smoke, to run inside the deployed task environment:

```bash
python arie-backend/scripts/verification_worker_smoke.py \
  --run-id pr6smoke \
  --worker-id pr6-smoke \
  --cleanup
```
