# Upload Latency CloudWatch Queries

Use log group `/ecs/regmind-staging`.

The backend emits parse-ready lines beginning with `upload_latency_telemetry`
for:

- `POST /api/applications/{application_id}/documents`
- `POST /api/documents/{document_id}/verify`

Pre-written query definitions live in:

- `docs/observability/cloudwatch/upload_latency_p50_p95.cwlogs`
- `docs/observability/cloudwatch/upload_latency_errors.cwlogs`
- `docs/observability/cloudwatch/upload_latency_slow_requests.cwlogs`

To save a query in CloudWatch Logs Insights:

```bash
aws logs put-query-definition \
  --name "RegMind Upload Latency p50/p95" \
  --log-group-names "/ecs/regmind-staging" \
  --query-string "$(cat docs/observability/cloudwatch/upload_latency_p50_p95.cwlogs)"
```

Expected telemetry fields:

- `operation`: `document_upload` or `document_verify`
- `path_template`: route template, with IDs removed from the path
- `status`: HTTP response status
- `duration_ms`: total request duration at the Tornado handler boundary
- `request_bytes`: request `Content-Length`
- `environment`: runtime environment

The logs intentionally do not include file names, company names, document text,
or other client-submitted content.
