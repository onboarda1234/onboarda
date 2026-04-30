# C5 Step 1 — ComplyAdvantage Observability, Metrics, and Alerting Design Diagnosis

## Preflight

- Branch prepared locally: `c5/observability-step1-diagnosis`; fixups applied on `copilot/c5observability-step1-diagnosis` on top of `160a76d`.
- Source-of-truth preflight executed before analysis: `git fetch origin main`, `git checkout FETCH_HEAD`, `git --no-pager log --oneline -10`.
- Operated against post-PR-#195 base commit SHA `2e1a7928567983b9702dd747f0473ef7d3be1e82` (`2e1a792 [C4 Step 2] Webhook handler + dual-write implementation (#195)`), with this diagnosis fixup committed on top of `160a76d`.
- Required signpost output:

```text
arie-backend/docs/screening/complyadvantage/C4-step1-webhook-design.md
arie-backend/docs/screening/complyadvantage/C3-step1-adapter-design.md
arie-backend/docs/screening/complyadvantage/C2-step1-oauth-client-design.md
arie-backend/docs/screening/complyadvantage/C1-expansion-step1-field-audit.md
arie-backend/screening_complyadvantage/webhook_handler.py
arie-backend/screening_complyadvantage/webhook_fetch.py
arie-backend/screening_complyadvantage/webhook_mapping.py
arie-backend/screening_complyadvantage/webhook_storage.py
arie-backend/screening_complyadvantage/client.py
arie-backend/screening_complyadvantage/auth.py
arie-backend/screening_complyadvantage/adapter.py
arie-backend/screening_complyadvantage/orchestrator.py
arie-backend/screening_complyadvantage/subscriptions.py
arie-backend/screening_storage.py
arie-backend/screening_config.py
arie-backend/supervisor/agent_executors.py
1483:from screening_complyadvantage.webhook_handler import ComplyAdvantageWebhookHandler
11565:        (r"/api/webhooks/complyadvantage", ComplyAdvantageWebhookHandler),
arie-backend/tests/test_complyadvantage_webhook_handler.py:269:    with patch("screening_complyadvantage.webhook_storage.emit_metric") as metric:
arie-backend/screening_complyadvantage/webhook_storage.py:18:def emit_metric(name, **fields):
arie-backend/screening_complyadvantage/webhook_storage.py:96:            emit_metric("normalized_write_failure", provider=COMPLYADVANTAGE_PROVIDER_NAME)
arie-backend/screening_complyadvantage/webhook_storage.py:124:            emit_metric("monitoring_alerts_write_failure", provider=COMPLYADVANTAGE_PROVIDER_NAME)
arie-backend/screening_complyadvantage/webhook_storage.py:162:            emit_metric("agent_7_push_failure", provider=COMPLYADVANTAGE_PROVIDER_NAME)
arie-backend/screening_complyadvantage/webhook_handler.py:116:            from .webhook_storage import emit_metric
arie-backend/screening_complyadvantage/webhook_handler.py:123:            emit_metric("webhook_async_processing_failure", provider=COMPLYADVANTAGE_PROVIDER_NAME)
grep -c "Sandbox-confirmed CA shape" arie-backend/screening_complyadvantage/webhook_fetch.py returned: 1
```

- This document is design-only. No production code, application files, migrations, contracts, Agent 7 logic, schema, or CA business logic are changed.

## 1. Scope and locked assumptions

C5 exists because the C4 webhook path is functionally present but not yet production-visible enough for D1 sandbox validation, D2 shadow mode, or Track E cutover. This diagnosis treats the following as locked and does not propose changes to them:

- Three-hop webhook fetch chain:
  1. `GET /v2/cases/{case_identifier}` for case-shell enrichment.
  2. `GET /v2/alerts/{alert_id}/risks` for alert-risk listings, with `alert_id` sourced from webhook `alert_identifiers`.
  3. `GET /v2/entity-screening/risks/{risk_id}` for deep-risk payloads.
- Listing and deep-risk payloads are complementary. Listing payloads remain persisted under `provider_specific.complyadvantage.alert_risk_listings[risk_id]`, and the listing is attached to normalized deep-risk records as `alert_risk_listing`.
- `/v2/alerts/{alert_id}/risks` uses the outer top-level `risks` + `next` envelope; nested deep-risk resources may still use `values` + `pagination.next`.
- C5 Step 2 should add observability hooks only. It should not perform D1 validation, D2 execution, Track E cutover, schema changes, Agent 7 behavior changes, CA contract changes, or webhook/adapter business-logic rewrites.

## 2. Current observability inventory

### 2.1 CA implementation logs already present

Current CA logging is useful but uneven. The exact existing log and metric surfaces are:

> `screening_complyadvantage/auth.py:95` passes event name `ca_auth_response` into the auth logger path after token responses; `screening_complyadvantage/auth.py:160-170` emits it at INFO level with fields `method`, `path`, `status`, `duration_ms`, `attempt`, `realm`, and `username_fp`.

> `screening_complyadvantage/auth.py:172-181` emits `ca_auth_error` at WARNING level with fields `method`, `path`, `attempt`, `realm`, `username_fp`, and `exception`.

> `screening_complyadvantage/client.py:88-97` emits `ca_api_response` at INFO level with fields `method`, `path`, `status`, `duration_ms`, `attempt`, `realm`, and `username_fp`.

> `screening_complyadvantage/client.py:126-135` emits `ca_api_error` at WARNING level with fields `method`, `path`, `attempt`, `realm`, `username_fp`, and `exception`.

> `screening_complyadvantage/orchestrator.py:186-190` emits `ca_monitoring_subscription_skipped` at WARNING level with fields `workflow_id`, `customer_identifier`, and `reason`.

> `screening_complyadvantage/orchestrator.py:200-203` emits `ca_monitoring_subscription_seeded` at INFO level with fields `workflow_id` and `customer_identifier`.

> `screening_complyadvantage/webhook_handler.py:47-50` emits `ca_webhook_signature` at WARNING level for strict signature failures with fields `signature_mode=strict`, `signature_invalid=true`, and `body_len`.

> `screening_complyadvantage/webhook_handler.py:54-55` emits `ca_webhook_signature` at ERROR level when production is fail-closed because the webhook secret is missing, with fields `signature_mode=production_fail_closed` and `signature_secret_configured=false`.

> `screening_complyadvantage/webhook_handler.py:60-62` emits `ca_webhook_signature` at WARNING level when non-production signature verification is disabled, with fields `signature_mode=sandbox_fail_open`, `signature_verification_disabled=true`, and `environment`.

> `screening_complyadvantage/webhook_handler.py:65` emits `ca_webhook_signature` at INFO level for valid strict signatures with fields `signature_mode=strict`, `signature_valid=true`, and `body_len`.

> `screening_complyadvantage/webhook_handler.py:70` emits `ca_webhook_invalid_json` at WARNING level with field `body_len`.

> `screening_complyadvantage/webhook_handler.py:79` emits `ca_webhook_unknown_envelope_invalid` at WARNING level with field `event_type` and exception info.

> `screening_complyadvantage/webhook_handler.py:83-88` emits `ca_webhook_unknown_event` at INFO level with fields `event_type`, `case_identifier`, and `customer_identifier`.

> `screening_complyadvantage/webhook_handler.py:96` emits `ca_webhook_envelope_invalid` at WARNING level with field `event_type` and exception info.

> `screening_complyadvantage/webhook_handler.py:101-105` emits `ca_webhook_empty_alert_identifiers` at INFO level with fields `event_type`, `case_identifier`, and `no_op=true`.

> `screening_complyadvantage/webhook_handler.py:117-123` emits `ca_webhook_async_processing_failure` at ERROR level with fields `webhook_type` and `case_identifier`, then emits metric `webhook_async_processing_failure` tagged with provider `complyadvantage`.

> `screening_complyadvantage/webhook_fetch.py:139-144` emits `ca_webhook_fetch_page_cap_reached` at WARNING level with fields `alert_id`, `page_cap`, and `next_present=true`.

> `screening_complyadvantage/webhook_fetch.py:172-176` emits `ca_webhook_nested_pagination` at WARNING level with fields `risk_id`, `path`, and `next_present=true`.

> `screening_complyadvantage/webhook_fetch.py:200-206` emits `ca_webhook_fetch_api_call_budget_exceeded` at ERROR level with fields `max_calls`, `attempted_path`, `resource`, and `identifier`.

> `screening_complyadvantage/webhook_fetch.py:209-217` emits `ca_webhook_fetch_call` at INFO level with fields `method`, `path`, `call_number`, `max_calls`, `resource`, `identifier`, and `page`.

> `screening_complyadvantage/webhook_storage.py:44-49` emits `ca_webhook_subscription_missing` at WARNING level with fields `webhook_type`, `case_identifier`, and `customer_identifier`.

> `screening_complyadvantage/webhook_storage.py:52-58` emits `ca_webhook_subscription_ambiguous` at ERROR level with fields `webhook_type`, `case_identifier`, and `customer_identifier`.

> `screening_complyadvantage/webhook_storage.py:90-96` emits `ca_webhook_normalized_write_failure` at ERROR level with fields `case_identifier` and `customer_identifier`, then emits metric `normalized_write_failure` tagged with provider `complyadvantage`.

> `screening_complyadvantage/webhook_storage.py:118-124` emits `ca_webhook_monitoring_alerts_write_failure` at ERROR level with fields `case_identifier` and `customer_identifier`, then emits metric `monitoring_alerts_write_failure` tagged with provider `complyadvantage`.

> `screening_complyadvantage/webhook_storage.py:140-145` emits `ca_webhook_subscription_update_failure` at WARNING level with fields `case_identifier` and `customer_identifier`.

> `screening_complyadvantage/webhook_storage.py:156-162` emits `ca_webhook_agent_7_push_failure` at ERROR level with fields `application_id` and `case_identifier`, then emits metric `agent_7_push_failure` tagged with provider `complyadvantage`.

> `screening_complyadvantage/subscriptions.py:38-43` emits `ca_monitoring_subscription_duplicate` at WARNING level with fields `provider`, `client_id`, and `customer_identifier`.

No structured logging in `screening_complyadvantage/adapter.py`: the file has no logger event surface relevant to CA observability today.

No structured logging in `screening_complyadvantage/webhook_mapping.py`: mapping is currently pure transformation logic with no log or metric emission.

No structured logging in `screening_config.py`: `screening_config.py:12-14` imports and creates a logger, and `screening_config.py:55-67` resolves `SCREENING_PROVIDER`, but the file emits no log or metric when the active provider changes or drifts.

Security posture is broadly correct: CA auth/client logs include path, status, duration, realm, and username fingerprint, but not passwords, bearer tokens, authorization headers, raw token bodies, signatures, or secrets. Webhook signature handling logs body length and mode only, with the signature branches cited above.

### 2.2 Existing metric emission

> `screening_complyadvantage/webhook_storage.py:18-19` defines `emit_metric(name, **fields)` and logs `ca_webhook_metric metric=%s fields=%s` at INFO level.

Current metric call sites are:

> `screening_complyadvantage/webhook_handler.py:116-123` imports `emit_metric` in the async failure handler and emits `webhook_async_processing_failure` tagged with provider `complyadvantage`.

> `screening_complyadvantage/webhook_storage.py:90-96` emits `normalized_write_failure` after normalized provider-truth persistence fails.

> `screening_complyadvantage/webhook_storage.py:118-124` emits `monitoring_alerts_write_failure` after monitoring-alert upsert fails.

> `screening_complyadvantage/webhook_storage.py:156-162` emits `agent_7_push_failure` after Agent 7 push fails.

This is not yet a complete metrics system. There are no success counters, latency values, CloudWatch metric filters, dashboards, or CA-specific Logs Insights query definitions for these events in the current repo.

### 2.3 Existing CloudWatch / deployment monitoring

> `docs/observability/upload-latency-cloudwatch.md:3-15` documents CloudWatch Logs Insights usage for log group `/ecs/regmind-staging` and lists only upload-latency query definitions.

> `docs/observability/upload-latency-cloudwatch.md:17-23` shows manual `aws logs put-query-definition` usage for saved Logs Insights queries; it does not define alarms or dashboards.

> `docs/observability/cloudwatch/upload_latency_p50_p95.cwlogs:1-11`, `docs/observability/cloudwatch/upload_latency_errors.cwlogs:1-10`, and `docs/observability/cloudwatch/upload_latency_slow_requests.cwlogs:1-10` filter `upload_latency_telemetry`, not CA webhook or CA API events.

> `.github/workflows/deploy-staging.yml:126-149` verifies deployment health through `/api/readiness` and `/api/health`, but it does not create CA metrics, alarms, or dashboards.

> `.github/workflows/seed-staging-fixtures.yml:240-326` extracts CloudWatch log pointers for fixture-seeding operations; it does not define CA alarms or dashboards.

> `.github/workflows/ci.yml:184-229` performs container startup, `/api/health`, `/api/readiness`, and security-header smoke checks; it does not validate CA observability resources.

> `render.yaml:21` and `render.yaml:110` configure `/api/health` health checks for live and demo Render services; `render.yaml:40-98` and `render.yaml:133-181` define secrets and feature flags, but no CA-specific observability, metric, alarm, or dashboard resource.

No Terraform, CDK, CloudFormation, CloudWatch dashboard JSON, CloudWatch alarm definition, metric-filter definition, or CA-specific scheduled reconcile job was found in-repo during this pass.

### 2.4 Existing test coverage relevant to C5

CA tests already assert several observability-adjacent behaviors:

> `tests/test_complyadvantage_webhook_handler.py:189-199` asserts bad signatures return 401, log `signature_invalid`, and do not leak the raw signature or secret.

> `tests/test_complyadvantage_webhook_handler.py:213-228` asserts missing webhook secrets in non-production fail open with `signature_mode=sandbox_fail_open` and `signature_verification_disabled=true` logged.

> `tests/test_complyadvantage_webhook_handler.py:231-245` asserts missing webhook secrets in production fail closed with `signature_mode=production_fail_closed` logged.

> `tests/test_complyadvantage_webhook_handler.py:248-257` asserts malformed JSON logs `ca_webhook_invalid_json` and does not spawn storage work.

> `tests/test_complyadvantage_webhook_handler.py:261-272` asserts async processing failure emits metric `webhook_async_processing_failure` tagged with provider `complyadvantage`.

> `tests/test_complyadvantage_webhook_fetch.py:240-268` asserts page-cap truncation logs `ca_webhook_fetch_page_cap_reached` and the relevant `alert_id`.

> `tests/test_complyadvantage_webhook_fetch.py:271-294` asserts API-call budget exhaustion logs `ca_webhook_fetch_api_call_budget_exceeded`.

> `tests/test_complyadvantage_webhook_fetch.py:297-322` asserts nested deep pagination logs `ca_webhook_nested_pagination` with `risk_id` and nested `path`.

> `tests/test_complyadvantage_webhook_storage.py:162-184` asserts normalized write failure returns `normalized_write_failure` and halts best-effort steps.

> `tests/test_complyadvantage_webhook_storage.py:188-211` asserts monitoring-alert failure continues to subscription update and Agent 7 push.

> `tests/test_complyadvantage_webhook_storage.py:213-228` asserts the Sumsub active-provider flag skips Agent 7 push.

> `tests/test_complyadvantage_client.py:131-144` asserts CA client logs include username fingerprint and do not leak username, password, access token, or authorization header.

> `tests/test_complyadvantage_orchestrator.py:349-364` asserts subscription seeding logs `ca_monitoring_subscription_seeded`.

> `tests/test_complyadvantage_orchestrator.py:367-381` asserts missing DB injection logs a warning reason `db_handle_not_injected`.

> `tests/test_complyadvantage_subscriptions.py:59-66` asserts duplicate subscription seeding logs `ca_monitoring_subscription_duplicate`.

Gaps: current tests do not assert a full metric taxonomy, success counters, step durations, correlation ID propagation, structured JSON fields, CloudWatch metric-filter compatibility, provider-pair divergence, or alarm/dashboard definitions.

### 2.5 Storage and idempotency surfaces relevant to observability

Post-PR-#195 state includes the storage anchors C5 can use for traceability without schema changes:

> `db.py:570-590` defines `monitoring_alerts` with nullable `provider` and `case_identifier` columns in the PostgreSQL DDL, and `db.py:834-837` defines partial unique index `uq_monitoring_alerts_provider_case` on `(provider, case_identifier)` where both are non-null.

> `db.py:1325-1345` defines the same nullable `provider` and `case_identifier` columns in the SQLite `monitoring_alerts` DDL, and `db.py:1597-1599` defines the equivalent partial unique index.

> `screening_storage.py:89-121` persists normalized reports using provider-aware arguments, and `db.py:1008` / `db.py:1756` define unique index `uq_screening_normalized_app_provider_hash` on `(application_id, provider, source_screening_report_hash)`.

> `db.py:1015-1036` and `db.py:1763-1784` define `screening_monitoring_subscriptions` with `client_id`, `application_id`, `provider`, optional `person_key`, `customer_identifier`, `last_event_at`, `last_webhook_type`, and `monitoring_event_count` plus the unique customer index.

C5 should therefore use existing storage identifiers for correlation and dashboards, but should not add new tables, indexes, columns, or trace-ID persistence in Step 2.

## 3. Design decision: observability transport

C5 Step 2 should use **log-first CloudWatch observability with CloudWatch metrics derived from structured log events**, not direct AWS API calls from request handlers.

Recommended shape:

1. Add a small CA-local observability helper in the CA package in Step 2.
2. Emit single-line JSON logs for CA operational events and audit events.
3. For ECS/CloudWatch, use metric filters or Embedded Metric Format-style JSON records for counters and latency values.
4. For Render/live/demo, keep the same JSON logs useful in provider logs even if CloudWatch metrics are not attached.
5. Keep trace IDs, case IDs, application IDs, customer IDs, alert IDs, and risk IDs in logs only; do **not** use high-cardinality identifiers as CloudWatch metric dimensions.

Rationale:

- The repo already uses stdout logging and CloudWatch Logs Insights for upload latency.
- Direct `PutMetricData` would add AWS SDK/credential/runtime coupling to request and webhook paths.
- Log-first metrics allow C5 to remain provider-local, low risk, testable with `caplog`, and compatible with both ECS staging and Render services.

## 4. Metric namespace, naming, and dimensions

### 4.1 Namespace

Use CloudWatch namespace:

`RegMind/Screening/ComplyAdvantage`

### 4.2 Metric-name convention

Use PascalCase CloudWatch metric names and snake_case log event names.

Examples:

- Log event: `ca_webhook_signature_failure`
- Metric: `WebhookSignatureFailures`

### 4.3 Allowed dimensions

Use only low-cardinality dimensions:

- `Environment`: `development`, `testing`, `staging`, `demo`, `production`
- `Provider`: always `complyadvantage` for CA metrics
- `ActiveProvider`: `sumsub`, `complyadvantage`, `unknown`
- `WebhookType`: `CASE_CREATED`, `CASE_ALERT_LIST_UPDATED`, `UNKNOWN`, `none`
- `EndpointCategory`: `auth`, `case`, `alert_risks`, `deep_risk`, `workflow`, `subscription`, `unknown`
- `StatusFamily`: `2xx`, `4xx`, `5xx`, `timeout`, `network_error`, `invalid_payload`, `not_applicable`
- `Outcome`: `success`, `failure`, `skipped`, `truncated`, `no_op`
- `Step`: stable step names such as `signature`, `parse`, `case_fetch`, `alert_risks_fetch`, `deep_risk_fetch`, `normalized_write`, `monitoring_alert_write`, `subscription_update`, `agent7_push`
- `Mode`: `strict`, `sandbox_fail_open`, `production_fail_closed`

Do not use `trace_id`, `application_id`, `client_id`, `customer_identifier`, `case_identifier`, `alert_id`, `risk_id`, names, emails, or raw endpoint paths as metric dimensions.

### 4.4 Core metric catalogue

| Metric | Type | Emit from | Dimensions | Purpose |
|---|---:|---|---|---|
| `WebhookDeliveries` | Count | `webhook_handler.py` after signature/parse result | Environment, WebhookType, Outcome, Mode | Delivery volume and validation outcomes. |
| `WebhookSignatureFailures` | Count | `webhook_handler.py` invalid signature / missing prod secret | Environment, Mode, Outcome | Detect invalid signatures and env-mode failures. |
| `WebhookMalformedPayloads` | Count | `webhook_handler.py` invalid JSON/envelope | Environment, WebhookType, Outcome | Detect bad payloads and contract drift. |
| `WebhookUnknownEvents` | Count | `webhook_handler.py` unknown event branch | Environment, WebhookType=`UNKNOWN` | Detect new CA webhook types. |
| `WebhookAsyncProcessingFailures` | Count | `webhook_handler.py` async exception catch | Environment, WebhookType | Catch post-202 work loss. |
| `WebhookProcessingLatencyMs` | Value | `webhook_storage.py` full async processing | Environment, WebhookType, Outcome | End-to-end background processing SLO. |
| `WebhookStepLatencyMs` | Value | `webhook_storage.py` and `webhook_fetch.py` | Environment, Step, Outcome | Step-level bottleneck isolation. |
| `CaseFetchFailures` | Count | `webhook_fetch.py` around `/v2/cases/{id}` | Environment, StatusFamily | Hop 1 failure visibility. |
| `AlertRisksFetchFailures` | Count | `webhook_fetch.py` around `/v2/alerts/{id}/risks` | Environment, StatusFamily | Hop 2 failure visibility. |
| `DeepRiskFetchFailures` | Count | `webhook_fetch.py` around `/v2/entity-screening/risks/{id}` | Environment, StatusFamily | Hop 3 failure visibility. |
| `WebhookFetchApiCalls` | Count | `_WebhookFetchGuard.get()` | Environment, EndpointCategory, Outcome | API call volume and budget use. |
| `WebhookFetchApiCallBudgetExhausted` | Count | `_WebhookFetchGuard.get()` when max calls reached | Environment, WebhookType | Critical truncation signal. |
| `WebhookFetchPageCapReached` | Count | `_fetch_risk_listings_for_alert()` page cap | Environment, EndpointCategory=`alert_risks` | Pagination truncation signal. |
| `WebhookFetchNestedPaginationDetected` | Count | `_warn_nested_pagination()` | Environment, EndpointCategory=`deep_risk` | Detect unhandled nested pagination. |
| `CaApiRequests` | Count | `client.py` after each HTTP response/error | Environment, EndpointCategory, StatusFamily | CA API status/rate visibility. |
| `CaApiLatencyMs` | Value | `client.py` after each HTTP response | Environment, EndpointCategory, StatusFamily | CA API latency dashboard and alarms. |
| `CaAuthRequests` | Count | `auth.py` response/error | Environment, StatusFamily, Outcome | OAuth health. |
| `CaAuthLatencyMs` | Value | `auth.py` response/error | Environment, StatusFamily | OAuth latency. |
| `CaTokenCacheHits` / `CaTokenCacheMisses` | Count | `auth.py` cache decision | Environment | Token-cache behavior. |
| `NormalizedWriteFailures` | Count | `webhook_storage.py` Step 5 exception | Environment | Required provider-truth write failure. |
| `NormalizedWriteSuccesses` | Count | `webhook_storage.py` Step 5 commit success | Environment | Success-rate denominator. |
| `MonitoringAlertsWriteFailures` | Count | `webhook_storage.py` Step 7 exception | Environment | Operational alert state failure. |
| `MonitoringAlertsWriteSuccesses` | Count | `webhook_storage.py` Step 7 commit success | Environment | Success-rate denominator. |
| `SubscriptionUpdateFailures` | Count | `webhook_storage.py` Step 8 exception | Environment | Currently missing metric for best-effort update failure. |
| `SubscriptionUpdateSuccesses` | Count | `subscriptions.py` / Step 8 success | Environment | Event metadata health. |
| `Agent7PushFailures` | Count | `webhook_storage.py` Step 9 exception | Environment, ActiveProvider | Agent 7 delivery health. |
| `Agent7PushSkipped` | Count | `webhook_storage.py` active provider != CA | Environment, ActiveProvider | Shadow-mode visibility. |
| `EnvModeDrift` | Count | `webhook_handler.py` and startup/config check | Environment, Mode, ActiveProvider | Strict/fail-open/fail-closed drift detection. |
| `ShadowCaActivity` | Count | adapter/orchestrator/webhook storage when active provider is Sumsub | Environment, ActiveProvider | D2 shadow activity volume. |
| `ProviderPairDivergence` | Count | D2 comparator hook | Environment, DivergenceType | CA-vs-Sumsub output mismatch. |

## 5. Structured logging design

### 5.1 Common fields for every CA log event

Every CA observability log should include:

- `event_class`: `operational` or `audit`
- `event_name`: stable snake_case name
- `timestamp`: UTC ISO-8601
- `level`: standard logging level
- `provider`: `complyadvantage`
- `environment`
- `active_provider`
- `trace_id`
- `component`: `auth`, `client`, `adapter`, `orchestrator`, `webhook_handler`, `webhook_fetch`, `webhook_storage`, `subscriptions`, `shadow_comparator`
- `outcome`: `success`, `failure`, `skipped`, `truncated`, `no_op`
- `duration_ms`, when meaningful
- `error_type` and `exception_class`, when meaningful

### 5.2 Operational log class

Operational logs support on-call triage and short-term health monitoring. They may include internal correlation identifiers but never raw PII, CA secrets, request bodies, response bodies, bearer tokens, webhook signatures, or full HMAC values.

Allowed operational fields:

- `webhook_type`
- `signature_mode`
- `body_len`
- `case_identifier`
- `customer_identifier`
- `application_id`
- `client_id`
- `alert_id`
- `risk_id`
- `endpoint_category`
- `method`
- `path_template`, not full URL with query strings
- `status_code`
- `status_family`
- `attempt`
- `api_call_number`
- `api_call_budget`
- `page_number`
- `page_cap`
- `truncation_reason`
- `normalized_record_id`

Operational event examples:

- `ca_webhook_received`
- `ca_webhook_signature_failure`
- `ca_webhook_malformed_payload`
- `ca_webhook_unknown_event`
- `ca_webhook_processing_completed`
- `ca_webhook_fetch_hop_failed`
- `ca_webhook_fetch_truncated`
- `ca_normalized_write_failed`
- `ca_agent7_push_failed`
- `ca_api_response`
- `ca_api_error`

### 5.3 Audit log class

Audit logs support compliance review and provider cutover evidence. They should be lower volume and outcome-focused. They must still avoid raw CA payloads and secrets.

Audit events should include:

- `event_name`
- `trace_id`
- `application_id`
- `client_id`
- `provider`
- `active_provider`
- `source_screening_report_hash`
- `normalized_record_id`
- `webhook_type`
- `case_identifier`
- `customer_identifier`
- `authoritative`: boolean where relevant
- `decision_context`: e.g. `shadow_mode`, `active_cutover`, `agent7_skipped`, `provider_pair_compared`
- `divergence_type`, `sumsub_hash`, `complyadvantage_hash`, only for D2 comparison summaries

Audit event examples:

- `ca_provider_truth_persisted`
- `ca_monitoring_alert_upserted`
- `ca_subscription_event_recorded`
- `ca_agent7_push_attempted`
- `ca_agent7_push_skipped_shadow_mode`
- `ca_provider_pair_compared`
- `ca_provider_pair_divergence_detected`
- `ca_env_mode_drift_detected`

## 6. Correlation and traceability decision

C5 Step 2 should introduce a CA trace ID without changing CA contracts or database schema.

Decision:

- Accept inbound `X-Request-ID` only if present and reasonably bounded; otherwise generate `ca_trace_id` at the start of `ComplyAdvantageWebhookHandler.post()`.
- Pass `trace_id` through `spawn_callback` into `process_complyadvantage_webhook()`, fetch-back helpers, storage logs, and metric logs.
- Add `trace_id` to all CA operational/audit logs and to test assertions.
- Do not add `trace_id` to database schema in C5.
- Use existing domain keys for forensic joins:
  - webhook: `case_identifier`, `customer_identifier`, `webhook_type`
  - fetch chain: `alert_id`, `risk_id`, `endpoint_category`
  - storage: `client_id`, `application_id`, `source_screening_report_hash`, `normalized_record_id`
  - Agent 7: `application_id`, and Agent executor run ID where available
  - D2 comparison: `application_id`, provider, normalized source hashes

## 7. Alerting thresholds and recommended CloudWatch alarms

Thresholds should start conservative in staging/demo, then be tightened after D1 baseline traffic. All alarms should include runbook links once runbooks exist.

| Alarm | Environment | Threshold | Severity | Rationale |
|---|---|---:|---|---|
| CA production webhook secret missing | production | `WebhookSignatureFailures{Mode=production_fail_closed} >= 1` in 1 datapoint | Critical | Endpoint cannot process valid webhooks. |
| Signature failure burst | production | `WebhookSignatureFailures >= 5` in 5 minutes or >20% of deliveries | Warning | Detect bad CA config, attack noise, or secret mismatch without paging on one probe. |
| Sandbox fail-open in production | production | `EnvModeDrift{Mode=sandbox_fail_open} >= 1` | Critical | Production must not accept unsigned webhooks. |
| Malformed webhook payloads | staging/prod | `WebhookMalformedPayloads >= 3` in 10 minutes | Warning | Could indicate CA contract drift or bad integration. |
| Unknown webhook events | staging/prod | `WebhookUnknownEvents >= 10` in 1 hour, or any sustained new type after D1 | Warning | New CA event types need triage before cutover. |
| Async processing failures | all non-test | `WebhookAsyncProcessingFailures >= 1` in 5 minutes | Critical | Post-202 work may be lost. |
| Case-shell fetch failures | staging/prod | `CaseFetchFailures >= 1` in 5 minutes | Critical pre-cutover, Critical post-cutover | Hop 1 is required for resnapshot truth. |
| Alert-risks fetch failures | staging/prod | `AlertRisksFetchFailures >= 1` in 5 minutes | Critical | Hop 2 is required for alert updates. |
| Deep-risk fetch failures | staging/prod | `DeepRiskFetchFailures >= 1` in 5 minutes | Critical | Missing deep risks can hide sanctions/PEP facts. |
| Page-cap truncation | staging/prod | `WebhookFetchPageCapReached >= 1` in 15 minutes | Critical | Data completeness risk. |
| API call budget exhaustion | staging/prod | `WebhookFetchApiCallBudgetExhausted >= 1` in 15 minutes | Critical | Data completeness risk and possible CA volume change. |
| CA API 5xx rate | staging/prod | `CaApiRequests{StatusFamily=5xx} >= 3` in 5 minutes | Warning | Provider instability. |
| CA API 429 rate | staging/prod | `CaApiRequests{StatusFamily=429} >= 1` in 5 minutes | Warning | Rate-limit/call-budget risk. |
| CA API p95 latency | staging/prod | p95 `CaApiLatencyMs > 3000` for 15 minutes | Warning | Fetch-back may breach background SLO. |
| Webhook processing p95 latency | staging/prod | p95 `WebhookProcessingLatencyMs > 90000` for 15 minutes | Warning | C4 fetch-back may approach operational timeout/retry windows. |
| Normalized write failure | staging/prod | `NormalizedWriteFailures >= 1` in 5 minutes | Critical | Required provider-truth write failed. |
| Monitoring-alert write failure | staging/prod | `MonitoringAlertsWriteFailures >= 1` in 15 minutes | Warning | Officer-facing alert state may be stale. |
| Subscription update failure | staging/prod | `SubscriptionUpdateFailures >= 1` in 15 minutes | Warning | Monitoring event counters/timestamps unreliable. |
| Agent 7 push failure | production with active CA | `Agent7PushFailures >= 1` in 15 minutes | Critical | Downstream compliance propagation failed. |
| Unexpected Agent 7 skip after cutover | production with active CA | `Agent7PushSkipped >= 1` in 15 minutes | Critical | Active-provider drift or flag error. |
| D2 shadow CA inactivity | D2 staging/prod shadow | `ShadowCaActivity == 0` for 24 hours while traffic exists | Warning | Shadow mode not exercising CA. |
| Provider-pair divergence rate | D2 shadow | `ProviderPairDivergence / ShadowCaActivity > 10%` over 24 hours | Warning | Needs product/compliance review. |
| Critical provider-pair divergence | D2 shadow | any sanctions/PEP/adverse-media critical mismatch | Critical | Cutover blocker. |

## 8. Dashboard design

Create one CloudWatch dashboard named `RegMind-CA-Screening-Observability` for staging first, then clone for production/demo.

Recommended panels:

1. **CA Webhook Intake**
   - deliveries by `WebhookType` and `Outcome`
   - signature failures by `Mode`
   - malformed payloads
   - unknown events
2. **Webhook Background Processing**
   - p50/p95/p99 `WebhookProcessingLatencyMs`
   - step latency heatmap by `Step`
   - async processing failures
3. **Three-Hop Fetch Chain**
   - case fetch success/failure
   - alert-risks fetch success/failure
   - deep-risk fetch success/failure
   - API calls per webhook
   - page-cap and API-budget truncations
4. **CA API Client Health**
   - requests by `EndpointCategory` and `StatusFamily`
   - p95 latency by endpoint category
   - 401/429/5xx counts
   - auth latency and auth failures
   - token cache hits/misses
5. **Storage and Operational State**
   - normalized write successes/failures
   - monitoring-alert write successes/failures
   - subscription update successes/failures
   - duplicate/ambiguous/missing subscription events
6. **Agent 7 / Downstream Push**
   - Agent 7 push attempts, skips, failures
   - active provider over time
7. **Environment and Mode Drift**
   - `EnvModeDrift` by `Mode`
   - current `SCREENING_PROVIDER` sampled log/metric
   - fail-open vs fail-closed counts
8. **D2 Shadow Provider-Pair Comparison**
   - `ShadowCaActivity`
   - applications with both Sumsub and CA normalized reports
   - divergence rate by `DivergenceType`
   - critical divergence count
9. **Cost and Volume**
   - log ingestion volume for CA events
   - metric count and alarm count
   - top event names by volume

## 9. Where C5 Step 2 should emit metrics

| Code surface | Step 2 hooks | Notes |
|---|---|---|
| `screening_complyadvantage/auth.py` | auth request count/latency/status, cache hit/miss | Keep username fingerprint only; no secrets. |
| `screening_complyadvantage/client.py` | API request count/latency/status by endpoint category | Classify paths into `case`, `alert_risks`, `deep_risk`, `workflow`, `auth`, `unknown`. |
| `screening_complyadvantage/webhook_handler.py` | delivery, signature mode, invalid JSON/envelope, unknown events, empty alerts, async failure, generated trace ID | Must emit before/after 202 boundary. |
| `screening_complyadvantage/webhook_fetch.py` | per-hop start/end/failure, API call count, page cap, budget exhaustion, nested pagination | Keep alert/risk IDs in logs, not dimensions. |
| `screening_complyadvantage/webhook_storage.py` | end-to-end processing latency, 9-step success/failure/skip metrics, normalized/alert/subscription/Agent 7 outcomes | Existing `emit_metric` should be upgraded or wrapped rather than duplicated. |
| `screening_complyadvantage/subscriptions.py` | subscription seed/update/duplicate metrics | Add denominator metrics for update success. |
| `screening_complyadvantage/orchestrator.py` | workflow polling attempts, workflow latency, monitoring subscription seeded/skipped | Supports D1 and D2 non-webhook path visibility. |
| `screening_complyadvantage/adapter.py` | active/shadow invocation metrics | Helps distinguish CA active vs CA shadow activity. |
| D2 comparator hook | provider-pair comparison and divergence metrics | Should compare normalized outputs by application ID and provider without schema changes. |

## 10. Provider-pair divergence design for D2

C5 should prepare metrics/logs for D2 without running D2 itself.

Recommended D2 comparison model:

- Trigger comparison when both providers have a recent normalized report for the same `application_id`.
- Compare normalized summaries rather than raw provider payloads.
- Emit one audit event per comparison: `ca_provider_pair_compared`.
- Emit divergence metrics only for stable categories:
  - `pep_presence_mismatch`
  - `sanctions_presence_mismatch`
  - `adverse_media_presence_mismatch`
  - `risk_level_mismatch`
  - `match_count_delta`
  - `critical_flag_mismatch`
  - `provider_error_mismatch`
- Store hashes and counts in logs; do not log raw matches, names, document text, or provider payloads.
- Dashboard should show divergence rate and critical mismatch count, not individual identities.

## 11. Log retention policy

| Log class | Content | Retention recommendation | Reason |
|---|---|---:|---|
| CA operational logs | request/response metadata, webhook processing, step metrics, errors, trace IDs | 90 days in staging/prod; 30 days in demo; 14 days in development | Supports D1/D2 diagnosis and near-term incident response without retaining high-volume operational logs indefinitely. |
| CA audit logs | provider-truth persisted, Agent 7 attempted/skipped, provider-pair comparisons, env-mode drift | 7 years in production; 1 year in staging; 90 days in demo | Supports compliance evidence and cutover audit trail. |
| CA debug/sandbox validation logs | temporary verbose D1 diagnostics | 14 days, disabled by default outside D1 windows | Minimizes sensitive operational metadata retention. |
| Existing upload latency logs | upload/verify latency telemetry | Keep current policy; align to 90 days if formalized | Not CA-specific. |

If log groups are shared, use event fields and metric filters to distinguish retention class until separate log groups are provisioned. If separate log groups are provisioned later, use `/regmind/ca/operational/<env>` and `/regmind/ca/audit/<env>`.

## 12. Cost projection

Assumptions for initial staging/prod footprint:

- 35-45 custom CloudWatch metrics after dimensional rollups.
- 20-30 CloudWatch alarms.
- 1 dashboard.
- CA log volume below 1 GB/month during D1/D2, assuming no raw payload logging and low webhook volume.

Approximate monthly cost order of magnitude, using common CloudWatch pricing patterns and subject to AWS-region/pricing verification before provisioning:

| Item | Assumption | Approximate monthly cost |
|---|---:|---:|
| Custom metrics | 40 metrics | about $12/month |
| Alarms | 25 standard alarms | about $2.50/month |
| Dashboard | 1 dashboard | about $3/month |
| Log ingestion | <1 GB/month | about $0.50/month |
| Log storage | <1 GB retained | negligible to low |
| Logs Insights ad hoc queries | small D1/D2 usage | usually <$5/month |

Expected initial total: **<$25/month per monitored AWS environment** at low D1/D2 volume. A conservative budget guardrail of **<$75/month per environment** is reasonable if traffic, metric count, or Logs Insights usage is higher.

Cost controls:

- Keep dimensions low-cardinality.
- Never dimension by trace ID, application ID, customer ID, case ID, alert ID, risk ID, or raw path.
- Sample only high-volume success logs if webhook volume becomes material; never sample failures or audit events.
- Review metric cardinality after D1 before enabling production alarms.

## 13. Test strategy for C5 Step 2

### 13.1 Unit tests

Add tests that assert metric/log emission without real AWS calls:

- `auth.py`: cache hit/miss counters, auth success/failure metrics, no secret leakage.
- `client.py`: API status-family metrics, latency fields, endpoint category classification, no token/header leakage.
- `webhook_handler.py`: trace ID generation/propagation, signature failure metric, malformed payload metric, unknown event metric, env-mode drift metric.
- `webhook_fetch.py`: case/alert/deep hop metrics, page-cap metric, API-budget metric, nested pagination metric, per-hop latency fields.
- `webhook_storage.py`: success and failure metrics for all 9 steps, end-to-end latency, Agent 7 skipped metric when active provider is Sumsub.
- `subscriptions.py`: duplicate/update success/failure metrics.

Use `caplog` and `unittest.mock.patch`, matching existing CA tests. Do not add a new test dependency for metrics.

### 13.2 Contract tests for log schema

Add table-driven tests for the CA observability helper:

- every event has `event_class`, `event_name`, `provider`, `environment`, `active_provider`, `trace_id`, `component`, and `outcome`;
- metric records never include high-cardinality IDs as CloudWatch dimensions;
- operational logs never include `password`, `access_token`, `Authorization`, raw signatures, raw request bodies, or raw response bodies;
- audit logs include compliance-relevant hashes/IDs but no raw provider payloads.

### 13.3 Alarm/dashboard definition tests

If Step 2 adds CloudWatch query, dashboard, or metric-filter files, add static tests that verify:

- JSON/YAML/query files parse;
- every alarm references an existing metric name;
- every dashboard widget references the `RegMind/Screening/ComplyAdvantage` namespace;
- no dashboard query selects raw payload fields.

### 13.4 Regression command

For CA-focused validation after Step 2, run:

```bash
cd arie-backend
python -m pytest tests/test_complyadvantage_*.py tests/test_screening_complyadvantage_*.py tests/test_screening_adapter_complyadvantage.py -q
```

For broader backend validation, use the repository CI command pattern:

```bash
cd arie-backend
python -m pytest tests/ -v --tb=short --ignore=tests/test_pdf_generator.py
```

## 14. Gaps and Step 2 implementation checklist

Recommended C5 Step 2 order:

1. Add a CA-local observability helper with structured log and metric-emission functions.
2. Replace or wrap the current `emit_metric()` so existing metric names keep working while new metrics use the shared schema.
3. Add trace ID generation in `webhook_handler.py` and propagate it through async storage/fetch paths.
4. Add metrics for webhook intake, validation, unknown events, malformed payloads, and env-mode drift.
5. Add metrics and timers for the three-hop fetch chain and API-call budget/page-cap truncation.
6. Add success/failure/skip metrics and end-to-end latency for the C4 9-step storage sequence.
7. Add auth/client metrics for status, latency, token cache, 401 refresh, 429, 5xx, timeout, and network failures.
8. Add Agent 7 push/skipped metrics and explicit active-provider logs.
9. Add D2-ready shadow activity and provider-pair divergence event schema, but do not execute D2.
10. Add CloudWatch metric-filter/query/dashboard/alarm definitions under docs or infra conventions used by the repo.
11. Add unit/static tests for metrics/logging behavior and safe-log constraints.
12. Document retention and runbook links for each alarm before production enablement.

## 15. CTO inputs before Step 2

These inputs shape implementation details, but they are not a dispatch blocker for the recommendation below:

1. Confirm log-first CloudWatch metrics as the C5 transport, rather than direct AWS `PutMetricData` calls.
2. Confirm the namespace `RegMind/Screening/ComplyAdvantage` and low-cardinality dimension policy.
3. Confirm whether Step 2 may add CloudWatch dashboard/alarm definition files in-repo, or whether those remain manual console setup for D1.
4. Confirm retention split: 90-day operational logs and 7-year production audit logs.
5. Confirm D2 provider-pair divergence categories and critical mismatch rules before shadow-mode dispatch.

## 16. Step 2 Dispatch Recommendation

**GO** — dispatch C5 Step 2 with explicit scope cuts.

Ship in C5 Step 2:

- CA-local structured operational/audit logging helper and upgrade/wrap of existing `emit_metric()`.
- Trace ID generation and propagation through webhook handler, async storage, fetch-back helpers, and metric logs.
- Webhook intake metrics/logs for signature results, malformed JSON/envelopes, unknown events, empty alert identifiers, async processing failures, and env-mode drift.
- Three-hop fetch-chain metrics/logs for case-shell fetch, alert-risks listing fetch, deep-risk fetch, page-cap truncation, nested pagination, and API-call budget exhaustion.
- Auth/client metrics/logs for CA API status family, latency, timeout/network errors, 401 refresh, 429, 5xx, and token cache hit/miss.
- Storage sequence metrics/logs for normalized write, monitoring-alert write, subscription update, Agent 7 push, Agent 7 skipped in shadow mode, and end-to-end webhook processing latency.
- Initial CloudWatch Logs Insights query definitions plus dashboard and alarm definition artifacts under the repo's observability docs convention; Step 2 ships definitions and documented setup steps, not automatic alarm provisioning.
- Unit/static tests for safe logging, metric schema, trace propagation, success/failure counters, and dashboard/alarm definition parsing where definitions are added.

Defer to follow-up after C5 Step 2:

- Direct AWS `PutMetricData` integration, unless log-derived metrics prove insufficient.
- New webhook DLQ, queue/worker, reconcile job, or scheduler infrastructure.
- Any schema changes for trace IDs, provider-pair comparison persistence, or CA webhook receipt tables.
- D1 sandbox validation execution, D2 shadow-mode runbooks/execution, and Track E cutover controls.
- Automatic CloudWatch alarm provisioning and production paging enablement until D1 establishes baseline rates and final alarm actions/SNS targets are approved.
- Full provider-pair comparison implementation beyond D2-ready event schema and metric placeholders.
