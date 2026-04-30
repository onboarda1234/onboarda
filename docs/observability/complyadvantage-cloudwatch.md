# ComplyAdvantage CloudWatch Observability

C5 Step 2 emits app-local structured JSON logs only. It does not call AWS metric APIs from request, webhook, or screening paths.

## Log groups

- Operational: `/regmind/ca/operational/`
- Audit: `/regmind/ca/audit/`

Each CA observability event includes `event_class`, `event_name`, `provider`, `environment`, `active_provider`, `trace_id`, `component`, and `outcome`.

## Logs Insights queries

Pre-written query definitions live in:

- `docs/observability/cloudwatch/ca_webhook_intake.cwlogs`
- `docs/observability/cloudwatch/ca_processing_latency.cwlogs`
- `docs/observability/cloudwatch/ca_api_health.cwlogs`
- `docs/observability/cloudwatch/ca_webhook_fetch.cwlogs`
- `docs/observability/cloudwatch/ca_storage_agent7.cwlogs`

To save a query:

```bash
aws logs put-query-definition \
  --name "RegMind CA Webhook Intake" \
  --log-group-names "/regmind/ca/operational/" \
  --query-string "$(cat docs/observability/cloudwatch/ca_webhook_intake.cwlogs)"
```

## Dashboard and alarms

- Dashboard definition: `docs/observability/cloudwatch/ca_dashboard.json`
- Alarm starter definitions: `docs/observability/cloudwatch/ca_alarm_definitions.json`

These are checked-in definitions for D1/D2 setup and review. They are not automatic provisioning artifacts and intentionally have no alarm actions wired.

## Deferred alarms

The alarm starter definitions include webhook fetch-health metrics that are emitted as structured logs before CloudWatch alarm actions are wired. Keep these definitions action-free until D1/D2 setup validates thresholds and notification routing.

## Safety constraints

Metric dimensions are low-cardinality only. Trace IDs, application IDs, client IDs, customer identifiers, case IDs, alert IDs, risk IDs, raw request bodies, raw response bodies, webhook signatures, bearer tokens, and credentials must not be used as metric dimensions or logged as raw payloads.
