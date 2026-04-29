# C4 Step 1 — ComplyAdvantage Webhook Handler + Dual-Write Diagnosis

## Preflight

- Branch: `c4/webhook-step1-diagnosis`.
- Source-of-truth preflight executed: `git fetch origin main`, `git checkout FETCH_HEAD`, `git --no-pager log --oneline -10`.
- Operated against post-PR-#191 commit `c525cd0` (`[C1 Expansion Step 2] Model field-set expansion implementation (#191)`).
- All required signposts succeeded: C1/C2/C3 design docs, CA `auth.py`, `client.py`, `adapter.py`, `orchestrator.py`, `payloads.py`, `subscriptions.py`, `normalizer.py`, `models/webhooks.py`, `screening_storage.py`, `ComplyAdvantageScreeningAdapter` registration/import in `server.py`, Sumsub webhook signposts, and migration 017.
- Required reading completed, including `screening_config.py` for §9.3 and all eight synthetic CA fixtures listed in `arie-backend/tests/fixtures/complyadvantage/README.md:5-12`.
- Locked recon context: s2/s3 establish CA webhook notification + fetch-back, `CASE_CREATED` and `CASE_ALERT_LIST_UPDATED`, HMAC-SHA256 header `x-complyadvantage-signature`, unsafe ordering, and unknown event fallback.
- Locked latency context: s3 observed long fetch-back latency.
- Fixture/recon lineage: s2-s7 response shapes are represented by synthetic fixtures documented at `arie-backend/tests/fixtures/complyadvantage/README.md:1-12`; C1 summarizes the repo-local and prompt-only recon evidence at `arie-backend/docs/screening/complyadvantage/C1-expansion-step1-field-audit.md:34-37`.
- This is documentation only. No production code, migrations, env values, C1/C2/C3 production behavior, Sumsub paths, or schema changes are modified.

## 1. Sumsub webhook handler precedent audit

### 1.1 Implementation and route registration

The Sumsub webhook handler is `SumsubWebhookHandler` in `arie-backend/server.py:7600-7907`. Its route is registered as `(r"/api/kyc/webhook", SumsubWebhookHandler)` at `arie-backend/server.py:11558-11563`, immediately after Sumsub applicant/token/status/document routes.

The handler docstring records the current hardening intent: digest algorithm allowlist, removal of legacy substring scan, unmatched-webhook DLQ, applicant-id validation, event-type gate, and 503 on DLQ insert failure (`arie-backend/server.py:7600-7625`).

### 1.2 Request flow

Current Sumsub flow is synchronous:

1. Handler starts in `post()` (`arie-backend/server.py:7627`).
2. Raw request body is read from `self.request.body` before JSON parsing (`arie-backend/server.py:7636`).
3. Signature headers are selected from `X-App-Access-Sig` first, then `X-Payload-Digest`, plus `X-Payload-Digest-Alg` (`arie-backend/server.py:7638-7653`).
4. Diagnostic logs include body length, header names, signature source, algorithm, and only an 8-character signature prefix (`arie-backend/server.py:7655-7671`).
5. Signature verification is always called before parsing: `sumsub_verify_webhook(body, signature, digest_alg=_digest_alg or None)`; failure returns 401 (`arie-backend/server.py:7673-7678`).
6. JSON parsing occurs only after signature verification (`arie-backend/server.py:7680-7683`).
7. The handler extracts `type`, `applicantId`, `externalUserId`, and `reviewResult.reviewAnswer` (`arie-backend/server.py:7685-7689`).
8. Applicant IDs are validated before DB open and masked for logs (`arie-backend/server.py:7691-7708`).
9. Non-mutating or unknown events are acknowledged with HTTP 200 before DB open (`arie-backend/server.py:7710-7730`).
10. Mutating `applicantReviewed` opens DB, inserts idempotency marker, writes audit state, maps applicant to applications, writes DLQ if unmatched, updates applications, commits, then optionally re-normalizes after commit (`arie-backend/server.py:7732-7907`).

### 1.3 Signature verification and secret-loading pattern

There are two Sumsub verification implementations in the repo:

- The route currently imports and uses `sumsub_verify_webhook` from `screening.py` (`arie-backend/server.py:1453-1458`, `arie-backend/server.py:7676`). That function is defined at `arie-backend/screening.py:593-656`. It loads the module-level `SUMSUB_WEBHOOK_SECRET`, accepts dev/demo when missing but rejects in production/staging (`arie-backend/screening.py:616-624`), resolves `digest_alg` through the allowlist (`arie-backend/screening.py:613-638`), computes HMAC with the selected hash constructor (`arie-backend/screening.py:640-644`), logs only prefixes (`arie-backend/screening.py:646-654`), and returns `hmac.compare_digest(expected, signature_header or "")` (`arie-backend/screening.py:656`).
- `SumsubClient.verify_webhook_signature()` also exists at `arie-backend/sumsub_client.py:1106-1139`; it uses instance `webhook_secret`, HMAC-SHA256, and `hmac.compare_digest` (`arie-backend/sumsub_client.py:1121-1135`). The handler does not call this method.

The digest allowlist is `ALLOWED_DIGEST_ALGS = {"HMAC_SHA256_HEX": hashlib.sha256, "HMAC_SHA512_HEX": hashlib.sha512}` at `arie-backend/utils/sumsub_validation.py:56-64`.

### 1.4 Out-of-order handling

Sumsub does not implement general webhook ordering reconciliation. It gates by event type: only `applicantReviewed` mutates (`arie-backend/utils/sumsub_validation.py:67-91` and `arie-backend/server.py:7710-7730`). A mutating event without applicant mapping is routed to `sumsub_unmatched_webhooks` and acknowledged if persisted (`arie-backend/server.py:7786-7847`). This is an unmatched-event DLQ pattern, not ordered replay.

For CA, locked C4 ordering is unsafe (`CASE_ALERT_LIST_UPDATED` may precede `CASE_CREATED`). C4 should not assume order; missing subscription lookup should produce a warning and 202, or an explicit queue decision (§6.2, §7.3).

### 1.5 Mapping into operational state

Sumsub maps reviewed events into legacy application state, not `monitoring_alerts`:

- It builds `kyc_data` with applicant, external user, review answer, rejection labels, moderation comment, event type, and received time (`arie-backend/server.py:7769-7777`).
- It writes an `audit_log` row for mutating state changes (`arie-backend/server.py:7779-7784`).
- It resolves application IDs through `sumsub_applicant_mappings` (`arie-backend/server.py:7786-7797`).
- Unmatched deliveries write `sumsub_unmatched_webhooks` (`arie-backend/server.py:7807-7847`).
- Matched applications receive `prescreening_data.screening_report.sumsub_webhook`, and RED reviews add an `overall_flags` entry (`arie-backend/server.py:7849-7874`).
- If abstraction is enabled, post-commit `webhook_renormalize_from_committed_legacy()` writes `screening_reports_normalized` as non-blocking re-normalization (`arie-backend/server.py:7886-7903`).

### 1.6 Existing webhook test patterns

Representative patterns C4 tests should follow:

- `test_sumsub_hardening_pr14.py` directly instantiates `SumsubWebhookHandler` against a synthetic Tornado `HTTPServerRequest`, without a live server. The helper is documented at `arie-backend/tests/test_sumsub_hardening_pr14.py:13-20` and implemented at `arie-backend/tests/test_sumsub_hardening_pr14.py:40-66`.
- Signature unit tests cover accepted SHA256/SHA512 and fail-closed unknown algorithms in `TestDigestAlgorithmAllowlist` (`arie-backend/tests/test_sumsub_hardening_pr14.py:154-200`). The unknown-algorithm test computes a valid SHA256 signature but passes `HMAC_MD5_HEX`/garbage and expects `False` (`arie-backend/tests/test_sumsub_hardening_pr14.py:182-192`).
- DLQ request-flow tests assert unmapped delivery returns 200 `queued` and inserts a row (`arie-backend/tests/test_sumsub_hardening_pr14.py:336-350`), and assert the row has `status='pending'` plus `resolution_note='auto:no_mapping_found'` (`arie-backend/tests/test_sumsub_hardening_pr14.py:352-375`).
- DLQ failure-mode test monkeypatches `server.get_db` with a wrapper that raises on `sumsub_unmatched_webhooks` insert and asserts HTTP 503, forcing provider retry (`arie-backend/tests/test_sumsub_hardening_pr14.py:457-500`).
- SCR-013 normalized-upsert tests mirror the direct invocation pattern (`arie-backend/tests/test_webhook_normalized_upsert.py:40-66`) and assert post-commit normalized hash correctness (`arie-backend/tests/test_webhook_normalized_upsert.py:301-347`), unmatched delivery skip (`arie-backend/tests/test_webhook_normalized_upsert.py:349-379`), and idempotent replay behavior beginning at `arie-backend/tests/test_webhook_normalized_upsert.py:381`.

C4 should use direct handler invocation, fixture-backed fake CA clients, `monkeypatch` for env/secrets/db failures, and explicit response-code assertions per dual-write step.

## 2. Schema and infrastructure audit

### 2.1 `monitoring_alerts` schema

`monitoring_alerts` exists in both PostgreSQL and SQLite schema strings:

- PostgreSQL DDL: `arie-backend/db.py:569-591`.
- SQLite DDL: `arie-backend/db.py:1319-1341`.

Columns: `id`, `application_id`, `client_name`, `alert_type`, `severity`, `detected_by`, `summary`, `source_reference`, `ai_recommendation`, `status`, `officer_action`, `officer_notes`, `created_at`, `reviewed_at`, `reviewed_by`, `linked_periodic_review_id`, `linked_edd_case_id`, `triaged_at`, `assigned_at`, `resolved_at` (`arie-backend/db.py:570-590`, `arie-backend/db.py:1320-1340`).

Critical finding: there is **no** `provider` column, **no** `case_identifier` column, and **no UNIQUE constraint on `(provider, case_identifier)` or equivalent** in current DDL. C4's one-row-per-CA-case idempotency cannot be DB-enforced directly without schema changes. Since C4 hard rules forbid schema changes, Step 2 must either use application-level dedup via `source_reference` reads or surface a schema follow-up. This is a §12.5 downstream risk.

### 2.2 `screening_reports_normalized` schema

`screening_reports_normalized` is declared in `screening_storage.py` and consolidated in `db.py`:

- `screening_storage.py` DDL columns are `id`, `client_id`, `application_id`, `provider`, `normalized_version`, `source_screening_report_hash`, `normalized_report_json`, `normalization_status`, `normalization_error`, `is_authoritative`, `source`, `created_at`, `updated_at` (`arie-backend/screening_storage.py:37-55`).
- Indexes include `idx_screening_normalized_client_app`, `idx_screening_normalized_app_id`, and unique `uq_screening_normalized_app_provider_hash ON screening_reports_normalized(application_id, provider, source_screening_report_hash)` (`arie-backend/screening_storage.py:57-61`).
- The PostgreSQL consolidated DDL mirrors this and creates the same unique index (`arie-backend/db.py:984-1003`).

This satisfies required idempotency for the provider-truth write using `(application_id, provider, source_screening_report_hash)`.

### 2.3 `screening_monitoring_subscriptions` schema

Migration 017 creates `screening_monitoring_subscriptions` with columns `id`, `client_id`, `application_id`, `provider`, `person_key`, `customer_identifier`, `external_subscription_id`, `status`, `subscribed_at`, `last_event_at`, `last_webhook_type`, `monitoring_event_count`, `is_authoritative`, `source`, `created_at`, and `updated_at` (`arie-backend/migrations/scripts/migration_017_screening_monitoring_subscriptions.sql:21-41`).

Indexes/constraints:

- `idx_screening_monitoring_subs_app(application_id)` (`arie-backend/migrations/scripts/migration_017_screening_monitoring_subscriptions.sql:43-44`).
- `idx_screening_monitoring_subs_client(client_id, application_id)` (`arie-backend/migrations/scripts/migration_017_screening_monitoring_subscriptions.sql:46-47`).
- Unique `uq_screening_monitoring_subs_customer(client_id, provider, customer_identifier)` (`arie-backend/migrations/scripts/migration_017_screening_monitoring_subscriptions.sql:49-50`).

C4 updates should target only existing fields: `last_event_at`, `last_webhook_type`, `monitoring_event_count`, and `updated_at` (`arie-backend/migrations/scripts/migration_017_screening_monitoring_subscriptions.sql:31-40`). The consolidated schema mirrors this for PostgreSQL at `arie-backend/db.py:1005-1031` and SQLite at `arie-backend/db.py:1748-1774`.

### 2.4 Reusable helpers in `screening_storage.py`

Reusable functions:

- `ensure_normalized_table(db) -> None` creates the table/indexes and commits DDL (`arie-backend/screening_storage.py:64-77`). Step 2 tests may reuse it; production should rely on migrations/init.
- `compute_report_hash(report: dict) -> str` hashes generic reports (`arie-backend/screening_storage.py:80-86`). CA must instead use `compute_ca_screening_hash` for enriched CA state (§6.1).
- `persist_normalized_report(db, client_id, application_id, normalized_report, source_report_hash, provider='sumsub', normalized_version='1.0') -> int` performs an idempotent upsert on `(application_id, provider, source_screening_report_hash)` (`arie-backend/screening_storage.py:89-121`). C4 can reuse this with `provider='complyadvantage'` and `normalized_version='2.0'`.
- `webhook_renormalize_from_committed_legacy(legacy_db, application_id) -> None` is Sumsub-specific post-commit legacy re-normalization (`arie-backend/screening_storage.py:124-181`). C4 should not use it for CA provider truth.
- `persist_normalization_failure(...)` inserts failed normalized attempts (`arie-backend/screening_storage.py:184-205`). C4 provider-truth write is required and should return HTTP 500 on write failure rather than silently storing failure only.

### 2.5 Webhook idempotency / DLQ infrastructure audit

Audit performed for `webhook_processed_events`, `webhook_event_log`, `processed_webhooks`, `webhook_dead_letter`, `unmatched_webhooks`, `webhook_failures`, and generic event receipt helpers.

Found infrastructure:

- `webhook_processed_events` is Sumsub idempotency. Schema: `id`, unique `event_digest`, `event_type`, `applicant_id`, `external_user_id`, `review_answer`, `received_at` (`arie-backend/db.py:3218-3249`). Consumer: Sumsub mutating path inserts an event digest before mutating state and returns 200 `already_processed` on any insert exception treated as duplicate (`arie-backend/server.py:7736-7767`). Recommendation: do **not** reuse directly for CA enriched-state idempotency because the locked C4 hash is computed against enriched case state, not webhook envelope. A generic event receipt table could be useful later, but current schema is Sumsub-shaped.
- `sumsub_unmatched_webhooks` is a Sumsub-specific DLQ. Schema: `id`, `applicant_id`, `external_user_id`, `event_type`, `review_answer`, `payload`, `status`, `resolution_note`, `resolved_by`, `received_at`, `resolved_at`, plus indexes on status/applicant/received (`arie-backend/db.py:2757-2797`). Consumer: Sumsub inserts unmatched mutating deliveries and returns 200 if the DLQ insert succeeds, 503 if it fails (`arie-backend/server.py:7807-7847`). Recommendation: do **not** reuse for CA because fields are applicant-centric and provider-specific. If C4 needs CA unmatched storage, that is new infrastructure/schema and out of scope.

No generic `webhook_event_log`, `processed_webhooks`, `webhook_dead_letter`, or `webhook_failures` table/helper suitable for CA was found. This is a §12 decision point: current DB unique constraints cover normalized-provider truth but not generic CA webhook receipt/DLQ.

### 2.6 Async/queue/worker infrastructure audit

Audit performed for Celery, RQ, Dramatiq, AWS SQS/SNS, custom thread-pool workers, scheduled tasks, and deferred-processing patterns.

Findings:

- No Celery/RQ/Dramatiq/SQS/SNS production worker was found.
- `resilience/task_queue.py` defines a SQLite-backed async `ExternalTaskQueue` with `enqueue`, `dequeue_ready`, `mark_completed`, `mark_failed`, `mark_processing`, `get_task`, and `get_queue_stats` (`arie-backend/resilience/task_queue.py:45-69`, `arie-backend/resilience/task_queue.py:112-153`, `arie-backend/resilience/task_queue.py:155-239`, `arie-backend/resilience/task_queue.py:240-368`). It stores rows in `external_retry_queue` (`arie-backend/resilience/task_queue.py:85-103`, `arie-backend/resilience/task_queue.py:122-130`). It is a helper, not a running worker service; no webhook handler currently uses it.
- The CA orchestrator uses `ThreadPoolExecutor(max_workers=2)` for strict/relaxed screening (`arie-backend/screening_complyadvantage/orchestrator.py:78-107`), and legacy screening also uses thread pools per C2/C3 docs, but that is per-request concurrency, not deferred processing.

Conclusion: there is **no suitable existing deferred webhook processing infrastructure** that can accept CA webhook receipt, return immediately, and guarantee background fetch-back/dual-write. Recommending async implies new infrastructure, scheduling, operational ownership, and likely env/config decisions; this must be surfaced in §7.3/§12.5.

## 3. CA webhook envelope models audit

### 3.1 Model classes and fields

`arie-backend/screening_complyadvantage/models/webhooks.py:1-72` defines:

- `CAWebhookCustomer`: `identifier`, `external_identifier`, `version` (`models/webhooks.py:10-13`).
- `CAWebhookSubject`: `identifier`, `external_identifier`, `type` (`models/webhooks.py:16-20`).
- `CAWebhookCaseStage`: `identifier`, optional `display_name`, optional `display_order`, optional `stage_type` (`models/webhooks.py:22-27`).
- `CACaseCreatedWebhook`: literal `webhook_type='CASE_CREATED'`, `api_version`, `account_identifier`, `case_identifier`, `case_type`, optional `case_state`, optional `case_stage`, `customer`, `subjects` (`models/webhooks.py:29-40`).
- `CACaseAlertListUpdatedWebhook`: literal `webhook_type='CASE_ALERT_LIST_UPDATED'`, `api_version`, `account_identifier`, `case_identifier`, `alert_identifiers`, `customer`, `subjects` (`models/webhooks.py:43-52`).
- `CAUnknownWebhookEnvelope`: `webhook_type`, `api_version`, `account_identifier`, optional `case_identifier`, optional `customer` (`models/webhooks.py:55-64`).
- `CAWebhookEnvelope` union of the two known models plus unknown fallback (`models/webhooks.py:67-71`).

### 3.2 Unknown fallback

`CAUnknownWebhookEnvelope` explicitly sets `model_config = ConfigDict(extra='allow')` (`arie-backend/screening_complyadvantage/models/webhooks.py:55-64`). This matches the hard rule that unrecognized webhook event types must not be silently dropped.

### 3.3 Sufficiency and gaps

The models are sufficient to parse the locked known event types and extract customer/case/subjects/alert IDs needed for fetch-back lookup (`models/webhooks.py:29-52`). Known models do not explicitly set `extra='allow'`; C1's field audit states only the unknown envelope had `extra='allow'` and known models otherwise used Pydantic default `extra='ignore'` (`arie-backend/docs/screening/complyadvantage/C1-expansion-step1-field-audit.md:22-25`). C1 recommends adding `extra='allow'` to known webhook envelopes to prevent future silent drops (`arie-backend/docs/screening/complyadvantage/C1-expansion-step1-field-audit.md:76-79`), but C4 Step 1 must not modify models. Step 2 can avoid silent field loss by storing the raw parsed JSON beside the typed envelope in handler-local logic, while surfacing model `extra` as a downstream C1 follow-up if CTO wants model-level preservation.

## 4. Webhook receiver design

### 4.1 Route path and method

Recommend `POST /api/webhooks/complyadvantage`.

Reasoning: it is explicit, provider-scoped, and contains `/webhook`, so it matches existing CSRF exemption logic (`arie-backend/base_handler.py:273-286`). Place the route near existing Sumsub webhook registration for auditability, after `(r"/api/kyc/webhook", SumsubWebhookHandler)` or in a new provider-webhook block immediately below Sumsub (`arie-backend/server.py:11558-11563`).

### 4.2 Signature verification flow

C4 should mirror the Sumsub raw-body-first discipline but use the locked CA scheme:

1. Read `body = self.request.body` before JSON parsing, as Sumsub does at `arie-backend/server.py:7636`.
2. Read `x-complyadvantage-signature` from request headers. Tornado header lookup is case-insensitive, but use the locked lowercase name in constants/tests.
3. Load `COMPLYADVANTAGE_WEBHOOK_SECRET` from `os.environ` at runtime. This env var does not exist today; see §12.
4. If missing in any environment, reject fail-closed with HTTP 401 and an ERROR log. Unlike legacy Sumsub's dev/demo bypass (`arie-backend/screening.py:616-624`), C4 should not add a dev bypass because CA webhook work is new security-sensitive code.
5. Compute `hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()`.
6. Compare using `hmac.compare_digest(expected, header or '')`.
7. On mismatch: WARNING log with event `ca_webhook_signature_invalid`, body length, and header presence only; return HTTP 401 with no sensitive body. Never log the raw signature, expected HMAC, or secret. Sumsub logs only prefixes (`arie-backend/server.py:7655-7671`, `arie-backend/screening.py:646-656`); CA can be stricter and log no prefixes.
8. Only after signature success, call `json.loads(body)` and validate into known/fallback envelope.

No new dependency is needed; use stdlib `hmac` and `hashlib`.

### 4.3 Proposed env var

Proposed new env var: `COMPLYADVANTAGE_WEBHOOK_SECRET`.

It is required for C4 signature verification and must be surfaced in §12. It is not one of the existing five C2 CA env vars listed in C2 (`COMPLYADVANTAGE_API_BASE_URL`, `COMPLYADVANTAGE_AUTH_URL`, `COMPLYADVANTAGE_REALM`, `COMPLYADVANTAGE_USERNAME`, `COMPLYADVANTAGE_PASSWORD`) at `arie-backend/docs/screening/complyadvantage/C2-step1-oauth-client-design.md:375-381`.

### 4.4 Route handler flow

Proposed handler sequence:

1. Read raw body and signature header.
2. Verify HMAC and return 401 before parsing on failure.
3. Parse JSON; on malformed JSON return 400.
4. Determine `webhook_type`; validate into `CACaseCreatedWebhook`, `CACaseAlertListUpdatedWebhook`, or `CAUnknownWebhookEnvelope`. Preserve raw JSON in local variables for logs/debug; do not silently drop unknown event payload.
5. If unknown webhook type: log warning and return HTTP 202 accepted/no processing because model fallback captured it but C4 has no locked fetch-back mapping. Do not write provider truth or monitoring alerts.
6. Resolve subscription by `(client_id?, provider='complyadvantage', customer_identifier=envelope.customer.identifier)`. The schema unique key includes `client_id` (`migration_017...sql:49-50`), but webhook payload does not include `client_id`. Therefore Step 2 must either search provider/customer across tenants and require exactly one row, or derive `client_id` from `customer.external_identifier` if C3 populated it deterministically. This is a key input-resolution detail (§5.3, §12).
7. If subscription missing: log warning and return HTTP 202 without normalized/alert writes (§6.2).
8. Fetch back enriched case/current alert state.
9. Normalize with `normalize_single_pass` if a single-pass fetch entrypoint can produce `workflow`, `alerts`, `deep_risks`, `customer_input`, `customer_response`, `application_context`, and `resnapshot_context` (`normalizer.py:124-156`).
10. Persist normalized report with required idempotency.
11. Map to monitoring alert and best-effort write.
12. Best-effort subscription update.
13. Best-effort Agent 7 push only if provider flag says CA is primary.
14. Return response per §7.

### 4.5 Accessing C2 HTTP client

Reuse C3's lazy construction pattern. `ComplyAdvantageScreeningAdapter` accepts optional `client`, `config`, and `orchestrator`, but only constructs the client on first use (`arie-backend/screening_complyadvantage/adapter.py:22-28`, `arie-backend/screening_complyadvantage/adapter.py:116-128`). C4 can create a handler-local service with optional dependency injection for tests and lazily instantiate `CAConfig.from_env()` + `ComplyAdvantageClient(config)` only after signature and envelope validation.

The client exposes `get(path, params=None)` and `post(path, json_body=None)` and performs one 401 refresh (`arie-backend/screening_complyadvantage/client.py:22-65`). It logs sanitized path/status metadata and never logs tokens (`arie-backend/screening_complyadvantage/client.py:67-135`).

### 4.6 Route-level security and raw-body audit

- Auth bypass: webhook handlers do not call `require_auth()`. `BaseHandler.require_auth()` is explicit and only runs when handlers call it (`arie-backend/base_handler.py:333-340`). `SumsubWebhookHandler.post()` never calls it (`arie-backend/server.py:7627-7907`). CA should match this and rely on HMAC only.
- CSRF bypass: `BaseHandler.check_xsrf_cookie()` exempts any URI containing `/webhook` because webhooks use HMAC signatures (`arie-backend/base_handler.py:273-286`). `make_app()` sets `xsrf_cookies=False` and delegates CSRF to BaseHandler (`arie-backend/server.py:11674-11679`). The proposed `/api/webhooks/complyadvantage` contains `/webhook`, so it inherits the same exemption.
- Raw body: Sumsub reads `self.request.body` before JSON parsing (`arie-backend/server.py:7636-7683`). `BaseHandler.get_json()` simply parses `self.request.body` and does not mutate it (`arie-backend/base_handler.py:312-316`). CA must not call `get_json()` before HMAC verification.
- Middleware mutation risk: `BaseHandler.prepare()` only enforces HTTPS redirect in production and does not mutate body (`arie-backend/base_handler.py:120-130`). `set_default_headers()` writes response headers only (`arie-backend/base_handler.py:180-234`). `make_app()` sets a 20MB max body size and no body-transforming middleware (`arie-backend/server.py:11674-11679`). No compression/body transformation wrapper was found in route setup.

Therefore CA should sit in `server.py` beside Sumsub and match Sumsub's no-session-auth/no-CSRF/raw-body-first pattern. Deviation: CA should not log HMAC prefixes; log only header presence/body length.

## 5. Fetch-back orchestration design

### 5.1 Can `screen_customer_two_pass` be reused?

No. `screen_customer_two_pass()` creates new strict and relaxed workflows using `create_and_screen()` and `ThreadPoolExecutor`, then normalizes the two-pass result (`arie-backend/screening_complyadvantage/orchestrator.py:78-120`). Webhook fetch-back is notification + current-state resnapshot, not new customer screening.

### 5.2 Single-pass entrypoint options and recommendation

Current public orchestrator methods are:

- `screen_customer_two_pass()` (`orchestrator.py:78-120`).
- `create_and_screen()` (`orchestrator.py:122-137`).
- `poll_workflow_until_complete()` (`orchestrator.py:139-151`).
- `fetch_risks_paginated_for_alert()` (`orchestrator.py:153-161`).
- `fetch_deep_risk()` (`orchestrator.py:163-168`).

The reusable three-layer traversal exists inside private `_run_one_pass()` (`orchestrator.py:170-191`), but it starts with `create_and_screen()` and therefore is not a webhook fetch-back entrypoint. Helper functions for alert ID extraction, customer identifier extraction, pagination normalization, risk parsing, and indicator parsing are private (`orchestrator.py:251-311`).

Options:

- Option A: add `fetch_single_workflow(...)` to `ComplyAdvantageScreeningOrchestrator`. This is clean for reuse but modifies C3, which locked context forbids unless CTO approves a boundary refactor.
- Option B: create a C4-specific orchestrator/service that composes `ComplyAdvantageClient.get()` and duplicates private traversal logic. This avoids C3 modifications but duplicates parsing/fetch traversal and may drift.
- Option C: extract three-layer fetch into a shared service used by C3 and C4. This is architecturally cleanest but also changes C3 boundaries and tests.

Recommendation for Step 2 dispatch: **Option B for C4 implementation if CTO insists no C3 production behavior changes**, with explicit TODO/risk to refactor to Option C later. If CTO allows a boundary-only C3 refactor, choose **Option C** because it prevents duplicated CA traversal. Option A is less attractive than C because it grows the orchestrator API around two different workflows without separating fetch mechanics.

This is a key §12.5 downstream decision.

### 5.3 Input-resolution flow

Webhook payload provides `customer.identifier`, `customer.external_identifier`, `case_identifier`, `subjects`, and possibly `alert_identifiers` (`models/webhooks.py:29-52`). Subscription unique key is `(client_id, provider, customer_identifier)` (`migration_017...sql:49-50`), but `client_id` is not a webhook field.

Recommended resolution:

1. Use `envelope.customer.identifier` as `customer_identifier`.
2. Query active subscriptions where `provider='complyadvantage' AND customer_identifier=?`.
3. If zero rows: warn and return 202; no normalized or alert writes.
4. If multiple rows across clients: log ERROR and return 202 without writes. Step 2 should not implement a `customer.external_identifier` parser unless CTO separately confirms that C3 encoded a stable tenant/application format. Do not guess tenant.
5. If exactly one row: use `client_id`, `application_id`, `person_key`, and status from subscription for `ScreeningApplicationContext`.
6. Use `case_identifier` and `alert_identifiers` to fetch current case/alert state. If the existing C3 client lacks a clean case endpoint wrapper, call `client.get()` directly with CA Mesh paths from locked contract; validate response with C1 output models where possible.

## 6. Idempotency, hashing, and out-of-order tolerance

### 6.1 `compute_ca_screening_hash`

`compute_ca_screening_hash(merged_matches: list[MergedMatch]) -> str` lives at `arie-backend/screening_complyadvantage/normalizer.py:230-234`. It sorts matches by `profile_identifier`, serializes `_hash_input_for_match(...)` with sorted keys and compact separators, and returns a 32-character SHA256 prefix (`normalizer.py:230-234`). `_build_report()` stores this value as `source_screening_report_hash` (`normalizer.py:321-335`).

C4 must reuse this hash after fetch-back normalization. It must not compute idempotency from the webhook envelope.

### 6.2 Out-of-order tolerance

Because CA ordering is unsafe, the handler must tolerate `CASE_ALERT_LIST_UPDATED` before subscription/case creation is visible locally. Recommended behavior:

- Lookup subscription by `provider='complyadvantage'` and `customer_identifier` (plus tenant disambiguation as §5.3 allows).
- If no subscription row exists: log WARNING with `webhook_type`, `case_identifier`, `customer_identifier`, and `reason=subscription_missing`; return HTTP 202 Accepted; do **not** write `screening_reports_normalized` or `monitoring_alerts`.
- If multiple rows: log ERROR and return 202 without writes to avoid cross-tenant contamination.

Alternative: enqueue unmatched events for deferred reconciliation. §2.6 found no suitable existing worker; implementing this implies new infrastructure/schema. Therefore 202 + logs is the only C4-scope-safe recommendation unless CTO expands scope.

### 6.3 Retry idempotency

- `screening_reports_normalized`: required idempotent. Existing unique index `(application_id, provider, source_screening_report_hash)` supports `persist_normalized_report()` upsert (`screening_storage.py:57-61`, `screening_storage.py:89-121`). Failure returns HTTP 500 to CA.
- `monitoring_alerts`: best-effort but must avoid duplicates. Current schema lacks provider/case columns and unique constraint (§2.1). Step 2 can use application-level read/update by JSON `source_reference` containing `{provider, case_identifier}`; this is not as strong as DB uniqueness and has race risk. Since schema changes are forbidden, surface schema follow-up in §12.5.
- `screening_monitoring_subscriptions`: best-effort update by existing row, increment `monitoring_event_count`, set `last_event_at`, `last_webhook_type`, and `updated_at` (`migration_017...sql:31-40`).

## 7. Dual-write sequence and HTTP response semantics

### 7.1 Ordered sequence

1. **Fetch-back — read-only.** Use CA client/orchestrator service to fetch current enriched case/alert/deep-risk state. Failure means no trustworthy provider truth; return HTTP 500 to CA so it retries. This step is not a write but blocks downstream.
2. **Normalize — pure compute.** Build `normalize_single_pass(...)` inputs and compute normalized report, including `source_screening_report_hash`. Failure returns HTTP 500 because provider truth could not be produced. This step is pure but required.
3. **Write `screening_reports_normalized` — REQUIRED idempotent.** Call `persist_normalized_report(db, client_id, application_id, normalized_report, source_hash, provider='complyadvantage', normalized_version='2.0')`. Failure returns HTTP 500 to trigger CA retry. Idempotency comes from the unique normalized index.
4. **Map normalized → `monitoring_alerts` row — pure compute.** Determine alert type/severity/summary/source reference. Mapping failure due to unsupported shape should log and still return HTTP 200 only if normalized provider truth was already persisted; however malformed normalized data should be treated as implementation error and tested. This step is pure; it does not need retry by CA once provider truth is stored.
5. **Write `monitoring_alerts` — BEST-EFFORT.** Insert/update one operational row per CA case. Failure logs error and metric, records retry need if/when infrastructure exists, and returns HTTP 200 to CA to avoid retry storm after provider truth is persisted.
6. **Update `screening_monitoring_subscriptions` — BEST-EFFORT.** Increment count and set last-event fields. Failure logs warning and returns HTTP 200.
7. **Agent 7 push — BEST-EFFORT and flag-aware.** If provider flag says CA is primary, trigger actual Agent 7 entrypoint if one exists; failure logs error and returns HTTP 200. If shadow mode/default Sumsub, skip push but keep capture/dual-write.

### 7.2 Transactions

Do not wrap all writes in one transaction.

Recommended transaction boundaries:

- Use a short transaction for required normalized upsert and commit before best-effort operations. This preserves the locked provider-truth write before operational queue attempts.
- Use separate transactions for `monitoring_alerts` and subscription update. A best-effort failure must not roll back provider truth.
- Agent 7 push should happen after DB commits and outside the DB transaction.

A single transaction would couple required and best-effort writes and would force either retry storms for operational queue failures or rollback of provider truth after it was already fetch-backed. That contradicts the locked discipline.

### 7.3 Async vs sync handler recommendation

Recommendation: **synchronous C4 handler for Step 2, with tight timeout/error handling and explicit §12 risk**, not async queue.

Reasoning:

- Locked s3 recon says fetch-back can take ~146 seconds, while typical webhook timeouts are likely 10-30 seconds. Pure sync is operationally risky.
- However §2.6 found no suitable existing webhook queue/worker. `ExternalTaskQueue` exists as an async SQLite helper (`resilience/task_queue.py:45-239`) but no production worker integrates it with webhooks, no scheduling/daemon lifecycle is wired, and no CA task schema exists.
- A true async recommendation would imply new infrastructure, likely additional env/config, retry semantics, operator tooling, and deployment changes. C4 Step 1/2 hard rules forbid schema changes and ask for no hand-wavy infrastructure.

Therefore Step 2 implements the synchronous path as the default design, with provider retry dependence for slow fetch-back documented as an operational risk. Alternative: if CTO chooses async, that is an explicit scope expansion requiring a concrete worker design before Step 2.

## 8. `monitoring_alerts` mapping design

### 8.1 Deterministic mapping rules and summary

Locked rules:

- One row per CA case.
- `alert_type` from indicator priority: sanctions > watchlist > PEP > media.
- `severity` defaulted by type.
- `summary` is one operational sentence, no narrative.
- `source_reference` JSON contains `{provider, case_identifier, alert_identifier, normalized_record_id}`.

Proposed type/severity defaults:

- sanctions: `alert_type='Sanctions'`, `severity='Critical'`.
- watchlist: `alert_type='Watchlist'`, `severity='High'`.
- PEP: `alert_type='PEP'`, `severity='High'`.
- media: `alert_type='Adverse Media'`, `severity='Medium'`.
- no actionable indicators: skip `monitoring_alerts` write or create low-severity informational row? Recommendation: skip alert row for clean resnapshot; keep provider truth only.

Proposed summary format:

`ComplyAdvantage monitoring update for case {case_identifier}: {alert_type} indicator requires review.`

Use synthetic-safe identifiers in tests and no real PII.

### 8.2 Indicator-priority logic via normalizer rollups

`compute_match_rollups()` determines `has_pep_hit`, `has_sanctions_hit`, `has_adverse_media_hit`, and `is_rca` from normalized CA indicators (`normalizer.py:200-227`). Watchlist indicators with taxonomy key prefix `r_sanctions_exposure` are currently rolled into sanctions (`normalizer.py:213-214`). The orchestrator parser distinguishes PEP/RCA, adverse media, sanctions exposure/watchlist, and sanctions by risk key (`orchestrator.py:303-311`).

For C4 mapping, inspect `provider_specific.complyadvantage.matches[].indicators[]` generated by `_provider_match()` rather than only top-level booleans, so watchlist vs sanctions can honor the locked priority. `_build_report()` stores provider-specific data under `provider_specific.complyadvantage` (`normalizer.py:321-335`).

### 8.3 Helper location

Place mapping helper in a new C4 module, e.g. `arie-backend/screening_complyadvantage/webhook_mapping.py`, not in `normalizer.py`. It consumes normalized report + webhook/case metadata and produces a `monitoring_alerts` dict. This preserves normalizer signatures and avoids C1/C3 behavior changes.

## 9. Subscription update + Agent 7 push integration

### 9.1 Subscription helper

Existing helper:

`seed_monitoring_subscription(db, client_id, application_id, customer_identifier, person_key=None, source='c3_create_and_screen')` inserts provider `complyadvantage`, commits if possible, and swallows unique violations (`arie-backend/screening_complyadvantage/subscriptions.py:9-43`). It uses `?` placeholders by repository convention (`subscriptions.py:55-57`).

Proposed complementary helper for Step 2:

`update_monitoring_subscription_event(db, *, client_id, customer_identifier, webhook_type, event_at) -> None`

It should update the existing row where `client_id=? AND provider='complyadvantage' AND customer_identifier=?`, increment `monitoring_event_count`, set `last_event_at`, `last_webhook_type`, `updated_at`, and not commit unless matching the existing helper's explicit commit pattern is chosen. Because it is best-effort, callers catch/log operational errors.

### 9.2 Agent 7 entrypoint

Actual public executor found:

`def execute_adverse_media_pep(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]` (`arie-backend/supervisor/agent_executors.py:3797-3802`). It reads app data, directors, UBOs, and monitoring alerts (`agent_executors.py:3803-3810`), then performs checks (`agent_executors.py:3812-3831`). Executors are registered through `register_all_executors(supervisor, db_path: str)`; wrappers inject `context['db_path']` and call each executor (`agent_executors.py:4585-4606`). Server startup calls `register_all_executors(supervisor_instance, DB_PATH)` (`arie-backend/server.py:11738-11744`).

No entrypoint was found that accepts a normalized screening record or a `monitoring_alerts` row directly. The HTTP `/api/monitoring/agents/{id}/run` route is a manual/simulated run and does not invoke supervisor executor logic (`arie-backend/server.py:9586-9608`). Therefore the locked "push to Agent 7" intent has an entrypoint mismatch: Step 2 can call `execute_adverse_media_pep(application_id, {'db_path': DB_PATH})` only as a full application-level agent run, not normalized-record processing. This is a §12.5 downstream risk requiring CTO decision.

### 9.3 `screening_primary_provider` flag

`screening_config.py` has `is_abstraction_enabled()` and `get_active_provider_name()`; the active provider comes from `SCREENING_PROVIDER` and defaults to `sumsub` (`arie-backend/screening_config.py:38-67`). There is **no** `screening_primary_provider` symbol or env var in `arie-backend/`.

Recommendation: use `get_active_provider_name() == 'complyadvantage'` as the current real flag if CTO agrees it is equivalent to `screening_primary_provider`. Do not introduce a new provider flag in C4 Step 2 unless surfaced and approved.

### 9.4 Shadow-mode behavior

Default `SCREENING_PROVIDER` is `sumsub` in every environment (`screening_config.py:29-35`), and abstraction defaults off (`screening_config.py:17-27`). Therefore current behavior is shadow: CA webhook capture can write normalized provider truth and best-effort monitoring alerts, but it should skip Agent 7 push unless `get_active_provider_name()` returns `complyadvantage` and the agent-entrypoint risk is resolved.

## 10. Module structure proposal

### 10.1 Filenames

Proposed Step 2 modules under `screening_complyadvantage/`:

- `webhook_handler.py` — pure handler/service functions: signature verification, envelope parsing, request orchestration, response decision. No Tornado inheritance if possible; server route class delegates here.
- `webhook_fetch.py` — C4-specific fetch-back service using `ComplyAdvantageClient.get()` and existing model parsing. Approximate scope: case/workflow/alert/deep-risk traversal and conversion to `normalize_single_pass` inputs.
- `webhook_mapping.py` — normalized-report to `monitoring_alerts` row mapping. Approximate scope: indicator priority, severity defaults, summary, source_reference JSON.
- `webhook_storage.py` — C4 storage helpers if `screening_storage.py` changes are not justified. Approximate scope: subscription lookup/update and best-effort alert upsert/read-update logic.
- `__init__.py` — export only stable public objects needed by `server.py`/tests.

Avoid touching forbidden C1/C2/C3 modules unless CTO approves boundary refactor.

### 10.2 Route location

Add a small `ComplyAdvantageWebhookHandler(BaseHandler)` in `server.py` near `SumsubWebhookHandler` and register it immediately after `(r"/api/kyc/webhook", SumsubWebhookHandler)` (`server.py:11558-11563`). The handler should be thin: read raw body/headers, delegate to `screening_complyadvantage.webhook_handler`, and translate service result to Tornado response.

### 10.3 Composition pattern

Use dependency injection like C2/C3 tests:

- Constructor/service accepts optional `client`, `db_factory`, `clock`, and `logger` for tests.
- Production lazily constructs `CAConfig.from_env()` and `ComplyAdvantageClient` only after signature verification.
- Storage helpers accept injected DB handles and do not import Tornado.
- Tests use fake clients/DB wrappers rather than live CA.

## 11. Test strategy

### 11.1 Unit tests

Add CA webhook-focused tests, following Sumsub direct-handler and CA fake-client patterns:

- Signature verifier: valid HMAC, invalid HMAC, missing signature, missing secret, no raw signature/secret in logs.
- Envelope parser: `CASE_CREATED`, `CASE_ALERT_LIST_UPDATED`, unknown type via `CAUnknownWebhookEnvelope` with raw payload preserved.
- Subscription resolver: zero, one, multiple matches.
- Fetch service: fixture-backed current-state fetch, pagination, deep-risk failure.
- Mapping helper: sanctions > watchlist > PEP > media priority; severity defaults; clean resnapshot skip.
- Storage helper: normalized required upsert calls `persist_normalized_report` with `provider='complyadvantage'`; monitoring alert best-effort behavior; subscription update increments fields.

### 11.2 Integration tests and fixtures

Reuse all eight synthetic fixtures documented at `tests/fixtures/complyadvantage/README.md:5-12`:

- `clean_baseline.json`
- `sanctions_canonical.json`
- `pep_canonical.json`
- `rca_canonical.json`
- `adverse_media_multi_source.json`
- `company_canonical.json`
- `monitoring_on_full_optional_fields.json`
- `two_pass_strict_misses_relaxed_catches.json`

Add webhook-specific synthetic fixtures for `CASE_CREATED` and `CASE_ALERT_LIST_UPDATED` bodies. Use only fake identifiers and `test-fixture.example.com` URLs, consistent with fixture README synthetic-PII discipline (`README.md:1-3`).

### 11.3 Out-of-order tests

- `CASE_ALERT_LIST_UPDATED` with missing subscription returns 202, logs warning, and writes neither normalized report nor monitoring alert.
- Multiple subscription rows for same provider/customer across clients returns 202 and logs tenant ambiguity.
- Subsequent event after subscription exists writes normalized provider truth.

### 11.4 Idempotency tests

- Same enriched CA state twice produces same `compute_ca_screening_hash` and one/upserted `screening_reports_normalized` row.
- Changed fixture/deep-risk state produces a different normalized hash and a new provider-truth row.
- `monitoring_alerts` duplicate handling uses application-level dedup by `source_reference` until schema uniqueness exists; test the best available behavior and document race limitation.

### 11.5 Failure-mode tests

Mirror §7.1 exactly:

- Fetch-back failure before normalized write returns HTTP 500.
- Normalization failure returns HTTP 500.
- `screening_reports_normalized` write failure returns HTTP 500.
- `monitoring_alerts` write failure returns HTTP 200, logs error, emits metric/retry marker if implemented.
- Subscription update failure returns HTTP 200 with warning.
- Agent 7 failure returns HTTP 200 with error log.

### 11.6 Flag-aware tests

- Default `SCREENING_PROVIDER=sumsub` or unset: no Agent 7 call.
- `SCREENING_PROVIDER=complyadvantage`: attempts configured Agent 7 entrypoint if CTO approves using `execute_adverse_media_pep`.
- Missing `screening_primary_provider` symbol is not assumed; tests patch `screening_config.get_active_provider_name()`.

### 11.7 Coverage target

Target at least 80% coverage per new C4 module, consistent with C2/C3 coverage targets (`C2-step1-oauth-client-design.md:468-472`, `C3-step1-adapter-design.md:452-454`). Documentation-only Step 1 does not require running the backend suite.

## 12. Open questions / explicit risks

### 12.1 Trade-offs

- Sync implementation is smaller and uses no new infrastructure, but may exceed CA webhook timeout during long fetch-back.
- Application-level `monitoring_alerts` dedup avoids schema changes but is weaker than a database unique key.
- C4-specific fetch service avoids forbidden C3 edits but duplicates private orchestrator traversal.
- Logging-only handling for missing subscriptions avoids new DLQ schema but may lose automatic reconciliation.

### 12.2 Unknown CA webhook behaviors blocking design

- Exact CA fetch-back endpoints for case/current alert state need Step 2 sandbox confirmation; current C3 methods fetch workflows/alerts/risks but start from create-and-screen handles (`orchestrator.py:122-191`).
- Whether CA signs hex digest, base64 digest, or prefixed digest in `x-complyadvantage-signature` beyond locked HMAC-SHA256 needs confirmation before implementation tests are finalized.
- Whether `customer.external_identifier` reliably carries tenant/application context from C3 for webhook disambiguation.
- Whether unknown event types should return 200 or 202; recommendation is 202 accepted/no processing.

### 12.3 Scope-creep risks

- Do not modify `screening.py`, `sumsub_client.py`, Sumsub adapter, C1/C2/C3 modules, config modules, migrations, or schema.
- Do not add top-level dependencies; stdlib `hmac`/`hashlib` is enough.
- Do not flip `ENABLE_SCREENING_ABSTRACTION` or provider flags.
- Do not change `normalize_two_pass_screening` or `normalize_single_pass` signatures (`normalizer.py:97-156`).

### 12.4 Verbal-recon findings in CTO memory but not repo

C1 documents prompt-only/verbal recon not fully captured in fixtures: full s3 PEP fields, live `CARiskType {key,name,taxonomy}`, nested profile additional fields/risk indicators, and full s2 sanction/watchlist value shape (`C1-expansion-step1-field-audit.md:156-159`). C4 additionally relies on locked prompt context for CA webhook signature header, unsafe ordering, notification+fetch-back model, and event types.

### 12.5 Downstream-impact risks beyond C4

- **C3 orchestrator change required:** Applies if CTO wants clean reuse. Current public orchestrator lacks a webhook single-pass fetch-back entrypoint; reusable logic is private and create-and-screen oriented (`orchestrator.py:78-191`). Step 2 can avoid C3 edits with a C4-specific fetch service, but that duplicates logic.
- **Agent 7 entrypoint mismatch:** Applies. Actual entrypoint is `execute_adverse_media_pep(application_id, context)` (`agent_executors.py:3797-3802`), not a normalized-record push API. HTTP manual run is simulated (`server.py:9586-9608`). CTO must decide whether full application-level Agent 7 run is acceptable.
- **`monitoring_alerts` UNIQUE constraint gap:** Applies. No provider/case columns or unique constraint exist (`db.py:569-591`, `db.py:1319-1341`). C4 cannot get DB-level one-row-per-case idempotency without schema follow-up.
- **Webhook idempotency infrastructure needed:** Partially applies. Normalized provider truth has idempotent unique hash; generic CA webhook receipt/DLQ infrastructure does not exist. Existing `webhook_processed_events` and `sumsub_unmatched_webhooks` are Sumsub-shaped (`db.py:3218-3249`, `db.py:2757-2797`). If CTO requires replay/DLQ beyond logs/202, new infrastructure is needed.
- **Async/queue infrastructure needed:** Applies if CTO rejects synchronous fetch-back. `ExternalTaskQueue` is only a helper, not a running webhook worker (`resilience/task_queue.py:45-239`). A real async CA webhook design implies new worker/deployment/config scope.

### Proposed new env vars

- `COMPLYADVANTAGE_WEBHOOK_SECRET` — required for CA HMAC verification.

No additional env vars are recommended for the synchronous Step 2 design. `COMPLYADVANTAGE_WEBHOOK_SECRET` is therefore the only proposed new env var. If CTO chooses async queue infrastructure in §7.3, additional queue/worker configuration may be required, but this diagnosis does not design or name those env vars.

### Acceptance-signal confirmations

- Commit SHA preflight: `c525cd0`.
- Signposts verified: yes, all required signposts succeeded.
- Modules to create in Step 2: `webhook_handler.py`, `webhook_fetch.py`, `webhook_mapping.py`, `webhook_storage.py`, plus a thin `server.py` route and optional `__init__.py` exports (§10.1).
- §5.2 recommendation: C4-specific fetch service by default; shared fetch extraction only with CTO approval because it changes C3 boundaries.
- §7.3 recommendation: synchronous Step 2 handler unless CTO explicitly expands scope for real queue/worker infrastructure.
- §12.5 downstream summary: C3 refactor risk applies if clean reuse is required; Agent 7 entrypoint mismatch applies; `monitoring_alerts` unique gap applies; generic webhook idempotency/DLQ infrastructure gap applies beyond normalized hash; async infrastructure gap applies if async is selected.
- New env vars: only `COMPLYADVANTAGE_WEBHOOK_SECRET` for sync design.
- §2.5 idempotency/DLQ audit performed: found Sumsub-specific `webhook_processed_events` and `sumsub_unmatched_webhooks`; no suitable generic CA infrastructure.
- §2.6 async audit performed: found no suitable running queue/worker; `ExternalTaskQueue` helper exists but is not integrated.
- §4.6 route constraints audit performed: Sumsub auth/CSRF/raw-body pattern quoted and CA route position justified.
- §1.6 Sumsub webhook test pattern audit performed: representative signature, request-flow/DLQ, and failure-mode tests quoted.
- §7.1 sequence tags each step as required idempotent, pure, read-only, or best-effort.
