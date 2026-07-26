# RegMind Production Audit — Audit 2 of 4

## Backend Security & Authorization

Audit date: 2026-07-25  
Source of truth: GitHub repository `onboarda1234/onboarda`  
Pinned revision: [`62c629da70ab1325af012632a7416afc731375da`](https://github.com/onboarda1234/onboarda/tree/62c629da70ab1325af012632a7416afc731375da)  
Revision time: 2026-07-25 22:07:47 +04:00  
Audited backend root: `arie-backend/`

The prompt's repository dimensions are stale. At the pinned revision, `arie-backend/server.py` is 44,794 lines and `arie-backend/security_hardening.py` is 4,227 lines. The runtime has 220 route patterns, of which 214 are `/api` patterns. The static inventory has 257 route-method rows because some patterns accept more than one method.

### Verification and scope

`CODE-VERIFIED` means the claim was demonstrated from the pinned source. `CONFIG-VERIFIED` means it was demonstrated from committed configuration or CI. `ENVIRONMENT-REQUIRED` means the deployed AWS/ECS/secrets/runtime state is necessary. `OPERATIONAL-REQUIRED` means procedure or interview evidence is necessary.

This audit covers backend application code and the directly relevant dependency/configuration files only. It does not audit frontend behavior, database schema design, or infrastructure. A UI-label assertion requested by AI7-04 is therefore explicitly left for Audit 3.

The following exhaustive generated inventories are part of this report:

| Inventory | Rows | Purpose |
|---|---:|---|
| `server_top_level_imports.csv` | 103 | Every module-scope `server.py` import, including direct-entry and conditional startup blocks, with exact source, dependency mapping, and startup behavior |
| `module_mutable_state.csv` | 374 | Every AST-detectable module-level mutable container/object across 143 production Python files |
| `broad_exception_handlers.csv` | 1,043 | Every bare, `Exception`, or `BaseException` handler with exact source and disposition |
| `dynamic_sql_sites.csv` | 230 | Every `execute`/`executemany` call whose SQL expression is an f-string or concatenation |
| `request_body_and_argument_sites.csv` | 2 | Direct `json.loads(request.body)` and `get_argument`-without-default sites |
| `file_path_operations.csv` | 379 | File/path construction, resolution, I/O, mutation, and serving operations |
| `async_blocking_candidates.csv` | 4 | Direct synchronous-looking I/O calls statically visible inside `async def` bodies |
| `rate_limit_sites.csv` | 40 | Every shared/legacy rate-limit call, key/action, threshold, window, handler, and exact source |
| `logging_calls.csv` | 1,290 | Every production `logger.*`/`logging.*` call |
| `route_authentication_inventory.csv` | 257 | Every route method, handler, gate, role list, and exact handler body |
| `direct_dependencies.csv` | 17 | Every direct pin and production import assessment |
| `top_transitive_dependencies.csv` | 5 | Top five transitive packages by lockfile `# via` dependent count |

`async_blocking_candidates.csv` is intentionally conservative: Python cannot determine from a call name alone whether an arbitrary helper is synchronous. The manual call-graph review below therefore adds blocking paths through supervisor executors, external clients, and PDF helpers.

---

## Section 1 — Monolith Risk and Startup Safety

### M1-01 — Import safety

**CODE-VERIFIED.** The complete 103-row list is `server_top_level_imports.csv`. It includes imports nested in module-scope `if`/`try` blocks and the direct-entry startup block while excluding function/class-local imports. All third-party module-scope imports resolve to an exact direct pin in `requirements.txt`; repository-local imports are commit-pinned with the repository. No import was found that is exclusively used by a development/test path but imported unconditionally.

There are 23 control-scope import rows. Twelve are under `try/except ImportError`: `s3_client` at `server.py:74`, `claude_client` at `server.py:100`, `gdpr` at `server.py:295`, `document_verification` at `server.py:1118`, `pdf_generator` at `server.py:1128`, `supervisor.api` and `supervisor.agent_executors` at `server.py:1144-1145`, `psycopg2` and `psycopg2.extras` at `server.py:1220-1221`, `prometheus_client` at `server.py:2934`, `change_management` at `server.py:42122`, and `migrations.runner` at `server.py:44334`. PostgreSQL correctly exits if its driver is absent. The migration runner, S3, AI, GDPR purge, authoritative document verification, PDF, supervisor, metrics, and change-management imports can degrade functionality instead. Direct-entry imports for schema drift, monitoring automation, document health, risk config, and memo recovery sit under broader exception policies; document health and memo recovery re-raise in deployed environments, while monitoring automation registration merely logs and continues. The exact condition and fallback classification for all 23 rows is in the CSV. This is FINDING-BSA-001. The mandatory `security_hardening` import at `server.py:83-96` correctly fails startup.

### M1-02 — Global mutable state

**CODE-VERIFIED.** The complete 374-row list and per-object classification is `module_mutable_state.csv`: 344 import-time lookup/config containers, 20 module containers, seven runtime objects requiring synchronization review, one semaphore, one readiness cache, and one supervisor pipeline cache.

Security/reliability-relevant mutable state:

| Object | Exact location | Concurrency result |
|---|---|---|
| `RateLimiter._attempts` | `auth.py:225` | Lockless per-process dict/list; incorrect aggregate service limit and non-atomic DB merge. FINDING-BSA-007 |
| `_pipeline_cache` | `supervisor/api.py:49` | Process-local review package; lost on restart and inconsistent across workers. FINDING-BSA-023 |
| `_BACKFILL_SEMAPHORE` | `screening_complyadvantage/historical_backfill.py:26` | Limits only one process to three backfills; aggregate replicas exceed the intended cap |
| Claude circuit state | `claude_client.py:73-149` | Lock-protected locally; not service-wide. FINDING-BSA-021 |
| token revocation cache | `security_hardening.py:3671` | Cache misses and user cutoffs re-read the database; DB failure fails closed |
| S3 readiness cache | `server.py` inventory row | TTL-bounded local staleness; no authorization consequence |

Read-only matrices, schemas, constant lists, and registries are shared between coroutines but have no identified runtime mutation after initialization.

### M1-03 — Event-loop blocking

**CODE-VERIFIED.** The following manually traced paths block Tornado's IOLoop:

1. `server.py:32379-32425`, `SupervisorRunHandler.post`, and `supervisor/api.py:352-441`, `PipelineRunHandler.post`, await `Supervisor.run_pipeline`.
2. `supervisor/supervisor.py:436-455`, `_execute_agent`, invokes every registered synchronous executor directly. `supervisor/agent_executors.py:4606-4639` registers synchronous functions.
3. `supervisor/audit.py:392-490`, `_persist`, performs synchronous DB queries and commit from the pipeline coroutine.
4. `claude_client.py:2201` and `2283` call the synchronous Anthropic SDK, and its retry path sleeps synchronously.
5. `screening_complyadvantage/webhook_handler.py:274-283` spawns an async callback, but `webhook_storage.py:86-180` and `181+` perform synchronous DB work; the synchronous ComplyAdvantage client uses `requests.Session.request` and `time.sleep` at `screening_complyadvantage/client.py:87-148`.
6. WeasyPrint is synchronous at `pdf_generator.py:647`, `pdf_generator.py:924`, and `evidence_pack_export.py:506`. These helpers are invoked from Tornado handlers on the IOLoop, including `ApplicationEvidencePackHandler`, `MemoPDFDownloadHandler`, and `ScreeningReportPDFDownloadHandler`.

These are FINDING-BSA-002 through FINDING-BSA-004. SHA-256 operations over uploaded files are either on already-buffered request bodies or chunked in ordinary synchronous handlers; they are CPU/I/O pressure but not additional `async def` violations.

### M1-04 — Silent exception swallowing

**CODE-VERIFIED.** The exhaustive list is `broad_exception_handlers.csv`: 1,043 broad handlers; 478 terminate/return/write a response, 251 are silent `pass`, 168 log and fall back, and 146 perform fallback/cleanup. There are 419 pass-or-log-only rows. Most are bounded cleanup, observability, compatibility, or explicitly best-effort operations. Security/reliability-significant swallows are:

- Compliance memo EDD audit and actuation errors are logged but the transaction commits and returns success (`server.py:32337-32374`): FINDING-BSA-005.
- PDF audit failures are logged while regulated artifacts are still returned (`server.py:34591-34626`, `34682-34699`): FINDING-BSA-006.
- Legacy rate-limit persistence failures are silently discarded (`auth.py:247-291`): FINDING-BSA-007.
- Sumsub idempotency insert catches every exception as a duplicate (`server.py:30993-31006`): FINDING-BSA-017.
- Sumsub per-application update errors are logged, then the idempotency row is committed (`server.py:31115-31146`): FINDING-BSA-018.

The remainder remains reviewable row-by-row in the inventory; no row is omitted.

### M1-05 — Handler completion safety

**CODE-VERIFIED — prompt premise disproved.** Tornado automatically finishes a request after the handler method returns when `_auto_finish` is true. The framework source calls `self.finish()` after the awaited method completes. Therefore an early `return` from a normal handler does not leave the connection open. The explicit `finish()` call is required only when asynchronous work is deliberately detached or automatic finishing is disabled. The audited code does not set `_auto_finish=False`. See Tornado's [`RequestHandler._execute`](https://www.tornadoweb.org/en/stable/_modules/tornado/web.html) and [`RequestHandler` documentation](https://www.tornadoweb.org/en/stable/web.html). No M1-05 finding.

---

## Section 2 — Authentication

### A2-01 — Token lifecycle

**CODE-VERIFIED.** `auth.py` was reviewed completely.

- `create_token` at `auth.py:103-117` is the only JWT issuer. It includes `sub`, `type`, `role`, `iat`, `nbf`, `exp`, `jti`, and `iss`; `exp` is 24 hours by default.
- It issues two principal variants through the same signed format: `token_type="officer"` at `server.py:5279` and `token_type="client"` at `server.py:5318,5371`. Neither variant has a refresh token.
- `decode_token` at `auth.py:137-188` restricts the algorithm to HS256, requires `exp`, `iat`, and `sub`, verifies issuer, then checks both the JTI and `user:{sub}` cutoff. A revocation-store error returns no identity.
- `BaseHandler.get_current_user_token` at `base_handler.py:727-747` gives an explicit Bearer token precedence over the cookie. If Bearer decoding fails, it does not fall back to the cookie.
- `BaseHandler._get_current_actor` at `base_handler.py:663-725` re-loads the active client/officer from the database and uses the current database role/type on every authenticated request.
- The separate client password-reset capability token is generated with `secrets.token_urlsafe(32)`, stored only as SHA-256, expires after one hour, is rate-limited by token and IP, and is cleared transactionally on use (`server.py:5418-5424,5456-5520`). It is exposed in the response only outside production.

No token without an expiry was found.

### A2-02 — Revocation

**CODE-VERIFIED, with one unverified requested capability.**

- Logout: `server.py:5612-5686` decodes the supplied Bearer/cookie token, requires durable revocation, returns 503 and retains cookies if persistence fails, then clears cookies only before the success response.
- Password reset/change/admin reset: all call `_revoke_all_client_sessions` (`server.py:1346-1373`) in the same transaction as the credential change. It upserts a `user:{id}` cutoff.
- Per request: `auth.py:174-178` checks JTI and user cutoff; `security_hardening.py:3699-3732` performs an authoritative database lookup on a cache miss; failures raise and are rejected.
- Index: `db.py:1957-1962` and `3359-3364` define `jti TEXT PRIMARY KEY`.
- `admin-forced logout` as a standalone endpoint: **UNVERIFIED — no dedicated forced-logout endpoint exists at the pinned revision.** Admin password reset does revoke all target sessions. User deactivation and role changes become effective immediately because `_get_current_actor` re-loads current state, but there is no general-purpose administrator “kill all sessions” operation.

### A2-03 — Mid-request validity

**CODE-VERIFIED.** The security-sensitive async route methods are `SupervisorRunHandler.post` and `supervisor.api.PipelineRunHandler.post`. Both call `revalidate_actor_after_await` before persisting post-await results (`base_handler.py:807-824`). No post-await authorization gap was found. Blocking synchronous work inside those coroutines is handled under M1-03.

### A2-04 — Password security

**CODE-VERIFIED, partially failing.**

- `PasswordPolicy.MIN_LENGTH = 12` at `security_hardening.py:3345`.
- Uppercase, lowercase, digit, and special-character checks are at `3375-3389`.
- Password verification uses `bcrypt.checkpw`, not string equality. `hmac.compare_digest` is the proper primitive for equal-length secrets/signatures; an adaptive password hash verifier is the correct password-comparison primitive.
- Officer/client login and registration call the legacy `RateLimiter`; its DB persistence is best-effort and its check/record sequence is not atomic. That is not brute-force protection “beyond the in-memory limiter” in a fail-closed service-wide sense: FINDING-BSA-007.

### A2-05 — Admin password reset

**CODE-VERIFIED.**

- Officer reset: `server.py:4592-4673` is admin-only, disabled in production, uses the shared fail-closed limiter, requires the administrator's own password via `bcrypt.checkpw`, requires confirmation, applies `PasswordPolicy`, and revokes the target user's sessions in the credential-update transaction.
- Client reset: `server.py:4507-4590` follows the same admin/reauthentication/revocation design.
- `co`, `analyst`, and `sco` cannot reach either endpoint because the role gate is `roles=["admin"]`.

### A2-06 — Client/officer session isolation

**CODE-VERIFIED.** `BaseHandler.require_backoffice_auth` at `base_handler.py:782-805` requires `user.type == "officer"` and an allowed officer role. A client JWT is structurally rejected with 403. Dual-use handlers use ownership checks. The route inventory records every direct gate. One dangerous dual-use exception is not a back-office access bypass but an authority-design flaw: the client-accessible document verification endpoint can run authoritative Agent 1 and persist `verified`; see FINDING-BSA-011.

---

## Section 3 — Authorization (RBAC)

### R3-01 — Role/permission matrix

**CODE-VERIFIED.** There are five authenticated principal categories: four officer roles (`admin`, `sco`, `co`, `analyst`) plus `client`. `ROLE_PERMISSION_MATRIX` contains 21 permissions:

| Permission | Declared roles | Endpoint family and enforcement result |
|---|---|---|
| `view_dashboard` | admin, sco, co, analyst | dashboard/KPI handlers use all four |
| `view_all_applications` | admin, sco, co, analyst | application list/queue handlers use all four; client views are separately scoped |
| `view_application_details` | admin, sco, co, analyst | detail handler accepts officers; client requires ownership |
| `approve_low_medium` | admin, sco, co | application decision endpoint uses centralized `can_decide_application` |
| `approve_high_very_high` | admin, sco | centralized decision gate checks risk and role |
| `reject_applications` | admin, sco, co | decision endpoint role gate aligns |
| `request_more_information` | admin, sco, co | decision/RMI handlers align |
| `assign_reassign_cases` | admin, sco | assignment branches explicitly restrict admin/SCO |
| `escalate_to_sco` | admin, sco, co | generic status PATCH permits analyst to set `edd_required`: mismatch, FINDING-BSA-009 |
| `view_compliance_memo` | all four | memo read/PDF routes use all four |
| `override_ai_risk_score` | admin, sco | override endpoints align |
| `edd_review_signoff` | admin, sco | EDD signoff/approval endpoints align |
| `view_screening_results` | all four | screening/report read routes use all four |
| `view_reports_analytics` | admin, sco, co | report/analytics routes generally align |
| `manage_users` | admin | user-create/update/reset routes are admin-only |
| `manage_roles_permissions` | admin | role/user-management routes are admin-only |
| `view_audit_trail` | admin, sco | generic audit endpoint aligns; application and supervisor audit endpoints allow co/analyst: FINDING-BSA-010 |
| `system_settings` | admin | settings endpoints are admin-only |
| `manage_enhanced_requirements` | admin, sco | mutation endpoints align |
| `view_enhanced_requirements` | admin, sco, co | read endpoints align |

There is no `assertPermission()` implementation or invocation in production code. The matrix is descriptive and handlers duplicate literal role arrays, allowing drift. FINDING-BSA-008. The exact handler-level map is `route_authentication_inventory.csv`.

### R3-02, R3-04, R3-05 — Object authorization and portal isolation

**CODE-VERIFIED.** All resource-ID route methods were examined in `route_authentication_inventory.csv`, and the dynamic path/SQL inventories were cross-referenced.

- Client application reads and writes use `_require_portal_client`, `check_app_ownership`, `_portal_application_for_user`, or a client/application join.
- Document read/download/delete routes load the document and application, then verify client ownership before access. `_resolve_upload_document_path` at `server.py:1272-1295` constrains stored paths.
- Back-office roles are service-wide roles; per-officer assignment is not consistently an access boundary by design. Actions with ownership/signoff semantics call the relevant post-load authorization helpers.
- A client cannot directly set `status`, `assigned_to`, or `decision_by`: `ApplicationDetailHandler.patch`, `server.py:8835-8843`, rejects these fields for `type == "client"`.
- No client-to-client application or document disclosure was found.
- The exception is authority rather than horizontal scope: an owning client can invoke authoritative document verification (FINDING-BSA-011).

### R3-03 — Role boundaries

**CODE-VERIFIED.**

- `admin` versus `sco`: user management, password reset, role management, and system settings use explicit admin-only gates. No SCO parameter-manipulation path to an admin action was found.
- `sco` versus `co`: high/very-high decisions, final EDD signoff, AI overrides, enhanced-requirement management, and assignment are restricted by explicit role gates and/or centralized decision policy.
- `co` versus `analyst`: decision endpoints block analyst, but the generic application PATCH allows any officer—including analyst—to transition review states to `edd_required` and update the EDD lane. Application-specific and supervisor audit routes also allow analyst despite the matrix. FINDING-BSA-009 and FINDING-BSA-010.

---

## Section 4 — Input Validation and Injection

### I4-01 and I4-05 — SQL and JSONB re-injection

**CODE-VERIFIED.** `dynamic_sql_sites.csv` contains all 230 f-string/concatenated SQL expressions with exact code. Manual provenance review found dynamic fragments built from constants, allowlisted identifiers, placeholder-count generation, and fixed filter helpers; request/database values remain bound parameters. No request-tainted value was found directly interpolated into SQL.

Values extracted from `prescreening_data`/other JSON are re-bound when later used in a query. No JSONB re-injection vulnerability was found.

### I4-02 — ID handling

**CODE-VERIFIED — prompt premise is not applicable to most identifiers.** RegMind application, user, document, and case IDs are UUID/text identifiers, so coercing them to `int` would break valid requests rather than improve SQL safety. They are passed as bound values (`... WHERE id=?`, tuple parameters). Routes that require numeric IDs use regexes such as `[0-9]+` or bounded integer parsing. A string such as `1 OR 1=1` remains data in a bound parameter and cannot change the query.

### I4-03 and I4-06 — Body/argument parsing

**CODE-VERIFIED.** Only two direct `json.loads(request.body)` sites exist:

- `server.py:14341-14345`, `DocumentAIVerifyHandler.post`, catches parse failure and returns 400.
- `server.py:34090-34098`, `MemoApproveHandler.post`, catches parse failure; mandatory signoff/rationale validation at `34159-34169` then returns 400.

All ordinary handlers inherit `BaseHandler.get_json`, which raises structured HTTP 400 for malformed non-empty JSON at `base_handler.py:573-597`. There are zero `self.get_argument()` calls without a positional or keyword default across the 143 production Python files (170 calls reviewed). Exact sites are in `request_body_and_argument_sites.csv`.

### I4-04 — Path traversal

**CODE-VERIFIED.** All 379 operations are in `file_path_operations.csv`. Most uploaded-document paths use `Path.resolve()` plus parent membership through `_resolve_upload_document_path`. One request-controlled path uses a lexical string-prefix test, allowing a sibling directory whose name begins with the allowed prefix; FINDING-BSA-012.

---

## Section 5 — Security Headers and Transport

### S5-01 — Headers

**CODE-VERIFIED, one coverage gap.** `BaseHandler.set_default_headers`, `base_handler.py:407-491`, sets:

```python
self.set_header("X-Content-Type-Options", "nosniff")
self.set_header("X-Frame-Options", "DENY")
self.set_header("Referrer-Policy", "strict-origin-when-cross-origin")
self.set_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
self.set_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
```

`SecureStaticFileHandler` and `HardenedNotFoundHandler` set equivalent headers. The root route uses Tornado's built-in `RedirectHandler` at `server.py:44215`, not a hardened base class; those guarantees do not cover that response. FINDING-BSA-013.

### S5-02 — CORS

**CODE-VERIFIED / deployed value ENVIRONMENT-REQUIRED.** The effective implementation imports `config.ALLOWED_ORIGIN`, sourced only from process environment at `config.py:168`. `BaseHandler.set_default_headers` emits that exact origin if non-default; emits `*` only in `IS_DEVELOPMENT` or `IS_DEMO`; and emits no `Access-Control-Allow-Origin` in staging/production if no explicit value is configured. Environment classification comes from process configuration, not a request header. The actual deployed `ALLOWED_ORIGIN` value is **ENVIRONMENT-REQUIRED**. A second helper, `environment.get_cors_origin`, defines environment-specific defaults but is not the value used by `BaseHandler`.

### S5-03 — CSRF

**CODE-VERIFIED.** Exact callback exemptions are `/api/kyc/webhook` and `/api/webhooks/complyadvantage`; pre-session exemptions are login/register/logout/health (`base_handler.py:221-236`). Cookie-authenticated POST/PUT/PATCH/DELETE requires the header and cookie and compares them with `hmac.compare_digest` (`535-570`). Tokens are generated with `secrets.token_hex(32)`, scoped to `/`, `SameSite=Lax`, one day, and Secure in deployed/HTTPS contexts (`493-524`). The CSRF cookie must be JavaScript-readable for double-submit and is therefore intentionally not HttpOnly. A cross-origin attacker cannot choose/read the unpredictable cookie under the configured origin/cookie policy.

### S5-04 — Logging

**CODE-VERIFIED.** All 1,290 logging calls are in `logging_calls.csv`. No static direct log of `SECRET_KEY`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `SUMSUB_SECRET_KEY`, a password value, or an Authorization header was found. However, provider exception strings can include a token-bearing URL, and numerous calls log PII/external identifiers/raw AI text. FINDING-BSA-015.

### S5-05 — File serving

**CODE-VERIFIED.** `DocumentDownloadHandler`, `server.py:14569-14654`, verifies ownership and constrains the resolved file path. It nevertheless uses stored `mime_type` directly and permits inline display when `?view=inline`, including PDF/images. This violates the requested server-side allowlist/attachment-only control. FINDING-BSA-014.

---

## Section 6 — Webhook and Callback Security

### W6-01 — Signature verification

**CODE-VERIFIED.** `SumsubWebhookHandler.post`, `server.py:30847-30922`, selects known signature headers and calls `sumsub_verify_webhook(body, signature, ...)` before JSON parsing. `sumsub_client.py:1122-1140` computes HMAC-SHA256 and uses `hmac.compare_digest`. Invalid signatures are rejected without processing, but return 401 rather than the requested indistinguishable 200: FINDING-BSA-016.

ComplyAdvantage similarly validates signature/timestamp before JSON. Missing production secret fails closed with 503; invalid/stale signatures return 401.

### W6-02 — Idempotency

**CODE-VERIFIED, failing.** Sumsub inserts the SHA-256 event digest into a database table in the same transaction as processing; this is structurally atomic and database-backed. But every insert exception is interpreted as a uniqueness collision, so database outages/schema errors are falsely acknowledged as duplicates (FINDING-BSA-017). Per-application failures are then swallowed and the idempotency row commits, preventing a retry from repairing the failed application (FINDING-BSA-018).

ComplyAdvantage durably records a validated receipt before returning 202 and maintains received/processing/retry states. Its async callback still blocks the IOLoop (FINDING-BSA-003).

### W6-03 — Legacy fallback

**CODE-VERIFIED.** The prior substring scan is absent. Sumsub uses `sumsub_applicant_mappings`; unmatched deliveries enter `sumsub_unmatched_webhooks`, and DLQ persistence failure returns 503. No unverified-processing fallback was found.

---

## Section 7 — AI Layer Security

### AI7-01 — Prompt injection

**CODE-VERIFIED, failing by default.** `ClaudeClient._sanitize_for_prompt`, `claude_client.py:982-1066`, performs three passes, recursively sanitizes dictionaries/lists, removes known injection patterns, strips control characters, and length-bounds text. But `ENABLE_AI_PROMPT_FENCING` defaults false (`config.py:106-112`). Tests explicitly assert hostile text remains when the flag is off (`tests/test_p11_5_ai_hardening.py:60-69,250-260`). In `extract_document_fields`, `entity_name` and `person_name` are inserted into `context_hint` without sanitization even when fencing is enabled (`claude_client.py:1797-1802`), while document type/name are sanitized only behind the flag. FINDING-BSA-019. The deployed flag value is **ENVIRONMENT-REQUIRED**.

### AI7-02 — Mock mode

**CODE-VERIFIED.** Production configuration rejects mock mode as an error at `config.py:239-255`; Claude construction also fails closed in deployed mode (`claude_client.py:875-880`). `tests/test_production_readiness.py:509-526` sets production explicitly. This is a hard startup/runtime block, not a warning.

### AI7-03 — Output validation

**CODE-VERIFIED, one uncovered authoritative input.** `_AGENT_SCHEMAS`, `claude_client.py:209-268`, maps six raw AI operations to Pydantic models. `_parse_json_response`, `2427-2468`, validates before return; validation failure produces `_rejected=True` and `_requires_manual_review=True`, and downstream gates treat it as failure.

| Raw AI operation | Pydantic model | Applied before caller access | Validation failure |
|---|---|---|---|
| `score_risk` | `RiskScoreSchema` | Yes, in `_parse_json_response` | Rejected/manual review |
| `verify_document` | `DocumentVerificationSchema` | Yes | Rejected/manual review |
| `analyze_corporate_structure` | `CorporateStructureSchema` | Yes | Rejected/manual review |
| `assess_business_plausibility` | `BusinessPlausibilitySchema` | Yes | Rejected/manual review |
| `interpret_fincrime_screening` | `FinCrimeScreeningSchema` | Yes | Rejected/manual review |
| `generate_compliance_memo` | `ComplianceMemoSchema` | Yes | Rejected/manual review |
| `extract_document_fields` | **None** | No | Parsed dictionary accepted: FINDING-BSA-020 |

`extract_document_fields` calls `_parse_json_response(raw, "extract_document_fields")`, but no schema is registered under that method. Any parsed dictionary is returned and fed into deterministic rule checks at `document_verification.py:1521-1561`, including name/registration/date comparisons. FINDING-BSA-020.

### AI7-04 — Agent authority

**CODE-VERIFIED backend; UI labeling UNVERIFIED — Audit 3 scope.** `ai_agent_catalog.py` enumerates 10 agents:

| Agent | Authority |
|---:|---|
| 1 Identity & Document Integrity | authoritative |
| 2 Corporate Structure & UBO | decision_support |
| 3 Business Model Plausibility | decision_support |
| 4 FinCrime Screening | decision_support |
| 5 Compliance Memo | authoritative |
| 6 Continuous Monitoring | decision_support |
| 7 EDD | decision_support |
| 8 Regulatory Reporting | decision_support |
| 9 Regulatory Change | decision_support |
| 10 Quality Assurance | decision_support |

Agent 5's approval path requires admin/SCO, structured officer signoff, rationale, document gate, and audit. Agent 1's ordinary document-verification path is callable by an owning client, can persist `verification_status="verified"` from AI/rule results, and logs `agent_executions` only after committing, with log failure swallowed. The reliance gate accepts AI-verified status as usable without a mandatory human acceptance. FINDING-BSA-011.

Whether all decision-support outputs are visually labeled advisory is **UNVERIFIED — frontend review belongs to Audit 3**. Backend memo language labels the memo workflow as pending human review.

### AI7-05 — Timeout/circuit/retry

**CODE-VERIFIED / deployed flag ENVIRONMENT-REQUIRED.** Both Anthropic calls (`claude_client.py:2201`, `2283`) pass configured timeouts and are wrapped by the circuit-breaker execution path. Non-retryable authentication and ordinary 4xx API failures are not retried (`2378-2384`). The breaker is disabled by default and process-local, so the repository does not provide a service-wide production breaker unless the flag is set. FINDING-BSA-021.

---

## Section 8 — Rate Limiting and DoS Resistance

### D8-01 — Rate-limit scope

**CODE-VERIFIED.**

The legacy `check_rate_limit` uses `auth.RateLimiter`: local memory plus best-effort persistence for key prefixes. The newer `check_sensitive_rate_limit` uses an atomic database UPSERT and fails closed on backend unavailability.

Shared DB-backed sensitive limits cover password recovery/reset, document uploads, both document-verification paths, supervisor pipeline execution, administrator resets, and enhanced-requirement uploads. Login and registration remain on the non-atomic legacy limiter (FINDING-BSA-007). Memory-only limits are also used on lower-risk status/dashboard/probe, submission/preapproval, resource/regulatory-intelligence upload, legacy agent/screening/memo, pricing, profile hydration, and monitoring-replacement paths. The exhaustive 40-call inventory is `rate_limit_sites.csv`: 12 route-level shared fail-closed checks, one route-level shared evaluation with caller-specific response handling, 23 route-level legacy calls, and four limiter-wrapper/internal calls. `route_authentication_inventory.csv` maps each handler to its exact route.

### D8-02 — Upload DoS

**CODE-VERIFIED, failing.** `server.py:44229-44234` supplies `max_body_size` to `tornado.web.Application`, but Tornado defines it as an `HTTPServer` constructor/listen argument. `app.listen(PORT, address="0.0.0.0")` at `44428` does not pass it, so the intended pre-buffer cap is inactive. Tornado documents that [`Application.listen(**kwargs)` passes listen keywords to HTTPServer`](https://www.tornadoweb.org/en/stable/web.html) and [`HTTPServer(max_body_size=...)`](https://www.tornadoweb.org/en/stable/httpserver.html) owns the option. Existing tests inspect source text rather than exercising a large HTTP request.

Post-buffer limits are inconsistent: ordinary uploads use 10 MiB (`server.py:1231`), application body default is 20 MiB (`1240`), `FileUploadValidator` allows 25 MiB (`security_hardening.py:3938`), and resource/regulatory-intelligence endpoints permit 25 MiB. FINDING-BSA-022.

### D8-03 — AI cost DoS

**CODE-VERIFIED.** Cost-bearing document verification and supervisor triggers require authentication and use the shared database limiter. One user cannot make unbounded calls within configured windows, subject to correct shared DB operation. Claude usage captures actual input/output token counts from the API response at `claude_client.py:2211-2219` and `2300-2309`. The synchronous calls still create availability risk (FINDING-BSA-002).

---

## Section 9 — Dependency Audit

### DEP9-01 — Pinning

**CONFIG-VERIFIED.** All 17 direct production dependencies use exact `==` pins. `requirements-dev.txt` contains exact test/dev pins. The production Docker image installs `requirements.lock` with `--require-hashes` (`Dockerfile:36-43`); CI verifies runtime and dev lockfiles under hash enforcement (`.github/workflows/ci.yml:67-77`). CI's test environment installs the direct files at lines 62-65 but separately proves the production lock. No pinning finding.

### DEP9-02 and DEP9-05 — CVEs

**CONFIG-VERIFIED with live completeness ENVIRONMENT-REQUIRED.** CI pins `pip-audit==2.10.1` and ignores `CVE-2026-49452` for WeasyPrint 68.1 until 2026-08-09 because there is no fixed release and the code does not enable `presentational_hints=True` (`.github/workflows/ci.yml:141-155`). The public advisory rates the issue moderate and describes the affected mode: [GHSA-jhhc-3hcp-qhm5](https://github.com/advisories/GHSA-jhhc-3hcp-qhm5). FINDING-BSA-025 tracks the expiring exception.

GitHub exposes no status checks or completed workflow run for the pinned commit. A fresh full-manifest vulnerability query was not authorized in this audit because it would submit the dependency manifest to an external advisory service. Therefore the absence of additional HIGH/CRITICAL CVEs, including for Tornado, cryptography, PyJWT, requests, and the top five transitive packages, is **ENVIRONMENT-REQUIRED**. Pillow is not a direct pin; any occurrence is represented through the hash lock.

Every direct pin is explicitly classified below; “ENVIRONMENT-REQUIRED” means no current scanner result exists for this pinned GitHub revision, not that the package passed:

| Direct dependency | Pin | CVE result for this revision |
|---|---|---|
| tornado | 6.5.7 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| bcrypt | 5.0.0 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| PyJWT | 2.13.0 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| cryptography | 48.0.1 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| pydantic | 2.12.5 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| typing_extensions | 4.15.0 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| requests | 2.33.0 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| anthropic | 0.49.0 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| python-dotenv | 1.2.2 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| gunicorn | 25.1.0 | ENVIRONMENT-REQUIRED — fresh scanner result absent; unused |
| psycopg2-binary | 2.9.11 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| boto3 | 1.42.73 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| prometheus-client | 0.24.1 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| psutil | 7.2.2 | ENVIRONMENT-REQUIRED — fresh scanner result absent |
| aiosqlite | 0.22.1 | ENVIRONMENT-REQUIRED — fresh scanner result absent; unused |
| weasyprint | 68.1 | CONFIG-VERIFIED known moderate CVE-2026-49452; mitigated call mode and dated CI exception |
| pycountry | 26.2.16 | ENVIRONMENT-REQUIRED — fresh scanner result absent |

Top five transitive packages by lockfile dependent count:

| Rank | Package | Pin | Dependents |
|---:|---|---|---|
| 1 | certifi | 2026.6.17 | 3 |
| 2 | idna | 3.18 | 3 |
| 3 | webencodings | 0.5.1 | 3 |
| 4 | anyio | 4.14.2 | 2 |
| 5 | botocore | 1.42.97 | 2 |

### DEP9-03 — Release maintenance

**CODE/OSINT-VERIFIED.** `webencodings==0.5.1` has not released since 2017 ([PyPI](https://pypi.org/project/webencodings/)); `distro==1.9.0` has not released since December 2023 ([PyPI](https://pypi.org/project/distro/)). Both exceed the prompt's 18-month threshold. `anthropic==0.49.0` was released 2025-02-28 and is roughly 17 months old at audit time, so it does not cross the literal threshold, but it is far behind current SDK releases ([PyPI](https://pypi.org/project/anthropic/0.49.0/)). FINDING-BSA-026.

### DEP9-04 — Unused direct dependencies

**CODE-VERIFIED.** `aiosqlite` is not imported in production. `gunicorn` is not imported or invoked and the Docker entrypoint is `python server.py`. `typing_extensions` is not directly imported but is retained as a Pydantic/transitive compatibility dependency, so it is not classified as safely removable. FINDING-BSA-024.

---

## Finding register

| ID | Severity | Category | Blocking |
|---|---|---|---|
| BSA-001 | MEDIUM | Reliability | No |
| BSA-002 | HIGH | Reliability | Yes |
| BSA-003 | HIGH | Reliability | Yes |
| BSA-004 | HIGH | Reliability | Yes |
| BSA-005 | HIGH | Reliability | Yes |
| BSA-006 | MEDIUM | Reliability | No |
| BSA-007 | HIGH | Auth | Yes |
| BSA-008 | MEDIUM | AuthZ | No |
| BSA-009 | HIGH | AuthZ | Yes |
| BSA-010 | MEDIUM | AuthZ | No |
| BSA-011 | HIGH | AuthZ / AI Security | Yes |
| BSA-012 | HIGH | Injection | Yes |
| BSA-013 | LOW | Transport | No |
| BSA-014 | MEDIUM | Transport | No |
| BSA-015 | HIGH | Transport | Yes |
| BSA-016 | LOW | Auth | No |
| BSA-017 | HIGH | Reliability | Yes |
| BSA-018 | HIGH | Reliability | Yes |
| BSA-019 | HIGH | AI Security | Yes |
| BSA-020 | HIGH | AI Security | Yes |
| BSA-021 | MEDIUM | AI Security / Reliability | No |
| BSA-022 | HIGH | Reliability | Yes |
| BSA-023 | MEDIUM | Reliability | No |
| BSA-024 | LOW | Dependency | No |
| BSA-025 | LOW | Dependency | No |
| BSA-026 | MEDIUM | Dependency | No |

---

## Section 10 — Detailed findings

### FINDING-BSA-001

Severity:      MEDIUM  
Verification:  CODE-VERIFIED  
Category:      Reliability  
File:          `arie-backend/server.py`  
Line(s):       72-77, 98-107, 293-299, 1116-1149, 1217-1225, 2932-2962, 42120-42126, 44282-44637  
Function:      module initialization  
Description:   Production-capability modules are imported under caught `ImportError`/startup exception policies and converted into feature flags or skipped registration. A missing local module or one of its import-time dependencies can therefore produce a partially functional server instead of a failed deployment. Most seriously, scheduled GDPR purge, migrations, monitoring automation, and authoritative document verification can disappear while the server continues; supervisor startup can also catch initialization failure later and disable itself. PostgreSQL, document-health, and memo-recovery failures have stronger deployed fail-closed branches and are not included in the silent-degradation subset.  
Evidence:

```python
# GDPR retention and purge engine (optional import — continues if unavailable)
try:
    from gdpr import run_scheduled_purge as _gdpr_run_scheduled_purge
    HAS_GDPR_PURGE = True
except ImportError:
    HAS_GDPR_PURGE = False
    _gdpr_run_scheduled_purge = None

# Layered document verification engine (Agent 1)
try:
    from document_verification import verify_document_layered, to_legacy_result, _canonicalise_country
    HAS_DOC_VERIFICATION = True
except ImportError:
    HAS_DOC_VERIFICATION = False
    verify_document_layered = None
```

The exact code, surrounding condition, and failure assessment for all 23 control-scope import rows is in `server_top_level_imports.csv`.  
Impact:        A bad image/package can pass process startup and readiness while regulated retention, verification, PDF, metrics, or supervisor behavior is absent. Operators see endpoint-level 503s or missing scheduled work rather than a deploy failure.  
Fix:           Make GDPR, document verification, supervisor, PDF, and production S3 dependencies mandatory in deployed environments. Catch `ImportError` only for explicitly supported local/test profiles, emit a capability manifest at readiness, and fail startup when a required capability is unavailable.

### FINDING-BSA-002

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      Reliability  
File:          `arie-backend/supervisor/supervisor.py`; `arie-backend/supervisor/agent_executors.py`; `arie-backend/supervisor/audit.py`; `arie-backend/claude_client.py`  
Line(s):       `supervisor.py:184-250,436-455`; `agent_executors.py:4606-4639`; `audit.py:392-490`; `claude_client.py:2201,2283`  
Function:      `Supervisor.run_pipeline`; `Supervisor._execute_agent`; `register_all_executors`; `AuditLogger._persist`; `ClaudeClient._call_claude`  
Description:   An awaited supervisor pipeline executes registered synchronous agents directly on the IOLoop. Those agents perform synchronous database work and synchronous Anthropic network calls. `asyncio.wait_for` bounds elapsed time but does not move blocking work off the event loop and cannot preempt a synchronous call.  
Evidence:

```python
raw_output = await self._execute_agent(
    agent_type, application_id, run_id, context
)

if asyncio.iscoroutinefunction(executor):
    raw_output = await executor(application_id, context)
else:
    raw_output = executor(application_id, context)
```

```python
def make_wrapper(fn):
    def wrapper(application_id, context):
        context = context or {}
        context["db_path"] = db_path
        return fn(application_id, context)
```

```python
response = self.client.messages.create(
    model=model,
    max_tokens=max_tokens,
    system=system_prompt,
    messages=messages,
)
```

Impact:        One slow pipeline/Anthropic call blocks unrelated requests, health processing, webhook callbacks, and timers on the same process, creating request pileups and cascading timeouts.  
Fix:           Run the full blocking pipeline in a bounded worker queue (`run_in_executor`/`asyncio.to_thread`) or convert every DB/provider executor to genuinely async I/O. Enforce queue depth, cancellation semantics, and service-wide concurrency limits.

### FINDING-BSA-003

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      Reliability  
File:          `arie-backend/screening_complyadvantage/webhook_handler.py`; `arie-backend/screening_complyadvantage/webhook_storage.py`; `arie-backend/screening_complyadvantage/client.py`  
Line(s):       `webhook_handler.py:273-289`; `webhook_storage.py:86-206`; `client.py:87-148`  
Function:      `ComplyAdvantageWebhookHandler._process_webhook_async`; `process_complyadvantage_webhook`; `_process_claimed_webhook`; `ComplyAdvantageClient._send_with_retries`  
Description:   The handler returns 202 and spawns an async callback, but the callback opens synchronous DB connections, executes synchronous SQL, constructs a synchronous Requests client, makes provider calls with `requests.Session.request`, and sleeps with `time.sleep`. Detached execution does not make those operations non-blocking.  
Evidence:

```python
self.set_status(202)
tornado.ioloop.IOLoop.current().spawn_callback(
    self._process_webhook_async,
    envelope,
    trace_id=trace_id,
    webhook_id=webhook_id,
)

async def _process_webhook_async(self, envelope, trace_id=None, webhook_id=None):
    await _call_storage_callback(self._storage_callback, envelope, trace_id, webhook_id)
```

```python
async def process_complyadvantage_webhook(...):
    db = db_factory()
    try:
        claim = _claim_webhook_delivery(db, ...)
```

```python
self.sleep_fn(self.retry_backoff_seconds)
response = self.session.request(
    method, url, params=params, json=json_body,
    headers=request_headers, timeout=timeout or self.timeout,
)
```

Impact:        A callback can stall the process after the provider has already received 202, delaying all other requests and increasing the chance that background processing is lost on process termination.  
Fix:           Enqueue the durable receipt to a separate worker, or run the complete synchronous callback in a bounded executor. Do not call Requests, synchronous DB methods, or `time.sleep` on Tornado's IOLoop.

### FINDING-BSA-004

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      Reliability  
File:          `arie-backend/pdf_generator.py`; `arie-backend/evidence_pack_export.py`; `arie-backend/server.py`  
Line(s):       `pdf_generator.py:647,924`; `evidence_pack_export.py:506`; `server.py:20037-20199,34567-34578,34675`  
Function:      `generate_memo_pdf`; `generate_screening_report_pdf`; `_render_pdf`; `ApplicationEvidencePackHandler.get`; `MemoPDFDownloadHandler.get`; `ScreeningReportPDFDownloadHandler.get`  
Description:   WeasyPrint rendering is CPU- and I/O-heavy and is invoked synchronously in Tornado request handlers. Ordinary synchronous handler methods also run on the IOLoop unless explicitly offloaded.  
Evidence:

```python
pdf_bytes = weasyprint.HTML(string=html).write_pdf()
```

```python
return weasyprint.HTML(string=document).write_pdf()
```

```python
pdf_bytes = generate_screening_report_pdf(dict(app), screening_report)
```

Impact:        A single large evidence pack or PDF render blocks every concurrent request handled by that process; repeated authenticated requests can create an application-layer DoS.  
Fix:           Offload rendering to a bounded process/worker pool, cap source complexity and page count, cache immutable artifacts, and apply a shared per-user/application render limit.

### FINDING-BSA-005

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      Reliability  
File:          `arie-backend/server.py`  
Line(s):       32337-32374  
Function:      compliance memo generation handler `post`  
Description:   EDD routing audit and EDD workflow actuation failures are logged and swallowed. The surrounding memo transaction then commits and the API returns the memo as a success. This cross-references Audit 1's decision-integrity concern: a policy result can say EDD while the case never enters the EDD workflow.  
Evidence:

```python
try:
    _routing = memo.get("metadata", {}).get("edd_routing")
    if _routing:
        _emit_edd_routing_audit(db, user, app["ref"], _routing, self.get_client_ip())
except Exception as _re:
    logger.error("Failed to emit EDD routing audit row for %s: %s", app["ref"], _re)

try:
    ...
    _actuation = _actuate_edd_routing(...)
except Exception as _ae:
    logger.error("Failed to actuate EDD routing for %s: %s", ...)

db.commit()
self.success(memo)
```

Impact:        The API can report a successful compliance memo while mandatory enhanced due diligence is neither auditable nor operationally created.  
Fix:           Treat routing audit and required EDD actuation as transaction-critical. Roll back and return non-2xx if either fails; persist a retryable work item only if the design explicitly supports asynchronous actuation.

### FINDING-BSA-006

Severity:      MEDIUM  
Verification:  CODE-VERIFIED  
Category:      Reliability  
File:          `arie-backend/server.py`  
Line(s):       34591-34626, 34682-34699  
Function:      `MemoPDFDownloadHandler.get`; `ScreeningReportPDFDownloadHandler.get`  
Description:   Failure to persist the PDF-generation timestamp or audit record is logged but the regulated PDF is still served.  
Evidence:

```python
try:
    db.execute("UPDATE compliance_memos SET pdf_generated_at = ? WHERE id = ?", ...)
    db.execute("INSERT INTO audit_log ...", ...)
    db.commit()
except Exception as e:
    logger.error(f"Failed to store PDF generation audit for {app_id}: {e}", exc_info=True)
db.close()
...
self.write(pdf_bytes)
```

The screening PDF uses the same pattern at lines 34682-34699.  
Impact:        A user can receive/export a compliance artifact with no durable audit evidence that it was generated or downloaded.  
Fix:           Make artifact audit persistence a prerequisite to delivery, or create an immutable artifact row in one transaction and serve only a committed artifact ID.

### FINDING-BSA-007

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      Auth  
File:          `arie-backend/auth.py`  
Line(s):       221-322  
Function:      `RateLimiter._db_load`; `_db_record`; `is_limited`  
Description:   Login/registration brute-force protection uses a per-process dictionary and best-effort database persistence. DB errors fail open to memory, and the read-count-append-insert flow is not atomic across workers. Concurrent containers can each admit attempts below their local threshold.  
Evidence:

```python
self._attempts = {}  # key → list of timestamps (in-memory hot path)
...
except Exception:
    # Table may not exist yet or DB unavailable — fall back to in-memory
    return []
...
except Exception:
    pass  # Best-effort — in-memory still protects us
...
if len(self._attempts[key]) >= max_attempts:
    return True
self._attempts[key].append(now)
if persist:
    self._db_record(key, now)
```

Impact:        Distributed brute-force attempts bypass the intended threshold; database outage weakens protection precisely when authentication telemetry/state is unavailable.  
Fix:           Move login/register to `check_shared_limit`, whose database UPSERT is atomic and fail-closed. Key by normalized account plus trusted client IP, record attempts atomically, and add exponential/account backoff without enabling user enumeration.

### FINDING-BSA-008

Severity:      MEDIUM  
Verification:  CODE-VERIFIED  
Category:      AuthZ  
File:          `arie-backend/server.py`  
Line(s):       17252-17283; handler role literals throughout `server.py` and `supervisor/api.py`  
Function:      `ROLE_PERMISSION_MATRIX`; route handlers  
Description:   The 21-entry permission matrix is descriptive data only. No production `assertPermission()` exists or is called. Each endpoint repeats literal roles, causing already-observed drift for EDD escalation and audit access.  
Evidence:

```python
ROLE_PERMISSION_MATRIX = [
    {"id": "approve_low_medium", ... "roles": ["admin", "sco", "co"]},
    ...
    {"id": "view_audit_trail", ... "roles": ["admin", "sco"]},
]
```

Repository search result: no `assertPermission` definition or call; only comments/reference text.  
Impact:        Changing the matrix does not change authorization. New or modified handlers can silently diverge, so UI/config expectations are not a security boundary.  
Fix:           Define canonical permission constants and one server-side `require_permission(permission, resource=None)` implementation. Derive role grants from one immutable policy and test every route-to-permission mapping.

### FINDING-BSA-009

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      AuthZ  
File:          `arie-backend/server.py`  
Line(s):       8816-8847, 8884-8903, 8967-8977, 17264  
Function:      `ApplicationDetailHandler.patch`  
Description:   The matrix excludes analyst from `escalate_to_sco`, but generic application PATCH requires only any authenticated user, rejects status changes only for `type == "client"`, and permits several review states to transition to `edd_required`. An analyst is an officer and therefore passes.  
Evidence:

```python
user = self.require_auth()
...
if user.get("type") == "client":
    if "status" in data or "assigned_to" in data or "decision_by" in data:
        return self.error("Only officers can change application status", 403)
```

```python
"compliance_review": ["in_review", "edd_required"],
"in_review": ["edd_required"],
"under_review": ["edd_required"],
...
if lane_synced:
    db.execute(
        "UPDATE applications SET status=?, onboarding_lane='EDD', ...",
        (new_status, real_id),
    )
```

Impact:        An analyst can perform a CO/SCO escalation action and move a regulated case into the EDD lane, changing operational workflow outside the declared role boundary.  
Fix:           Gate each status transition with a named permission after loading the application. For `edd_required`, require `escalate_to_sco` and reject analyst regardless of generic officer type.

### FINDING-BSA-010

Severity:      MEDIUM  
Verification:  CODE-VERIFIED  
Category:      AuthZ  
File:          `arie-backend/server.py`; `arie-backend/supervisor/api.py`  
Line(s):       `server.py:17272,19683-19708`; `supervisor/api.py:637-654`  
Function:      `ApplicationAuditLogHandler.get`; `AuditLogHandler.get`  
Description:   `view_audit_trail` is declared admin/SCO-only, and the generic `/api/audit` route honors it. Application-specific and supervisor audit routes explicitly allow CO and analyst.  
Evidence:

```python
{"id": "view_audit_trail", "label": "View audit trail", "roles": ["admin", "sco"]},
```

```python
class ApplicationAuditLogHandler(BaseHandler):
    def get(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
```

```python
class AuditLogHandler(SupervisorBaseHandler):
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
```

Impact:        Lower roles can read detailed application/supervisor audit evidence despite the policy advertising a narrower boundary, increasing PII and investigation-data exposure.  
Fix:           Decide whether audit access is globally admin/SCO or resource-scoped. Enforce that permission consistently after application scope/assignment checks and document any deliberate narrower audit view.

### FINDING-BSA-011

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      AuthZ / AI Security  
File:          `arie-backend/server.py`; `arie-backend/document_reliance_gate.py`; `arie-backend/ai_agent_catalog.py`  
Line(s):       `server.py:12999-13080,13291-13362,13444,13519-13584,13834-13962`; `document_reliance_gate.py:884-955,958-1025`; `ai_agent_catalog.py:10-18`  
Function:      `DocumentVerifyHandler.post`; `_evaluate_document`; `evaluate_document_reliance_gate`; `DocumentReviewHandler.post`  
Description:   Agent 1 is classified authoritative, yet an owning client can invoke `DocumentVerifyHandler`. AI/rule output determines `all_passed`, writes `verification_status="verified"`, and commits before the best-effort `agent_executions` write. The reliance gate accepts AI-verified documents with results/timestamp/agent proof; manual acceptance is not mandatory. The review handler requires senior manual acceptance only when the status is not already verified.  
Evidence:

```python
user = self.require_auth()
...
if user.get("type") == "client":
    if str(app["client_id"]) != str(user["sub"]):
        return self.error("Access denied", 403)
```

```python
all_passed = ...
verification_status = "verified" if all_passed else ...
...
db.execute(
    """UPDATE documents
       SET verification_status=?, verification_results=?, verified_at=? ...
    """,
    (...),
)
db.commit()

try:
    log_agent_execution(...)
except Exception as e:
    logger.debug("Could not log agent execution: %s", e)
```

Impact:        A client can trigger an authoritative AI transition that becomes compliance-reliance evidence without a mandatory human approval and without guaranteed agent-execution audit persistence.  
Fix:           Make the client operation upload/submit only. Restrict authoritative verification to an officer/worker capability; persist the agent execution and proposed result atomically; require explicit officer acceptance before `verified` becomes reliance-eligible.

### FINDING-BSA-012

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      Injection  
File:          `arie-backend/server.py`  
Line(s):       30778-30822  
Function:      `SumsubDocumentHandler.post`  
Description:   A request-supplied `file_path` is resolved but checked with a string prefix. A sibling such as `/app/uploads_evil/secret.pdf` begins with `/app/uploads` and passes. The resulting path is sent to Sumsub's document-upload helper.  
Evidence:

```python
file_path = data.get("file_path")
if file_path:
    import pathlib
    allowed_dir = pathlib.Path(os.path.join(os.path.dirname(__file__), "uploads")).resolve()
    requested = pathlib.Path(file_path).resolve()
    if not str(requested).startswith(str(allowed_dir)):
        return self.error("file_path must be within the uploads directory", 400)
    file_path = str(requested)
```

Impact:        An authenticated user with a valid applicant mapping can cause the service to read and transmit a file outside the intended upload directory when a prefix-colliding path exists and is readable.  
Fix:           Use `requested.is_relative_to(allowed_dir)` on Python 3.11, or `allowed_dir in requested.parents`, reject symlinks where appropriate, require a server-side document ID rather than a raw path, and open through the standard safe resolver.

### FINDING-BSA-013

Severity:      LOW  
Verification:  CODE-VERIFIED  
Category:      Transport  
File:          `arie-backend/server.py`; `arie-backend/base_handler.py`  
Line(s):       `server.py:44215`; `base_handler.py:407-491`  
Function:      `make_app`; `BaseHandler.set_default_headers`  
Description:   Security headers are strong on BaseHandler, secure static, and hardened 404 responses, but the root route uses Tornado's built-in `RedirectHandler`, which does not inherit RegMind's header middleware.  
Evidence:

```python
(r"/", tornado.web.RedirectHandler, {"url": "/portal"}),
```

```python
class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("X-Frame-Options", "DENY")
        ...
```

Impact:        The root redirect response is not guaranteed to carry HSTS, frame, referrer, content-type, and permissions policy headers, violating “every response” coverage.  
Fix:           Replace the built-in redirect with a hardened handler inheriting `BaseHandler`, or attach the headers at a server-wide transform/proxy layer and test every route class.

### FINDING-BSA-014

Severity:      MEDIUM  
Verification:  CODE-VERIFIED  
Category:      Transport  
File:          `arie-backend/server.py`  
Line(s):       14569-14654  
Function:      `DocumentDownloadHandler.get`  
Description:   The document response Content-Type comes directly from the stored `documents.mime_type`, and clients can request `Content-Disposition: inline` for PDF/images. The preview set controls disposition but does not constrain the emitted Content-Type to a server-derived allowlist.  
Evidence:

```python
INLINE_PREVIEWABLE_TYPES = {
    "application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp",
}
...
mime_type = doc.get("mime_type") or "application/octet-stream"
is_previewable = mime_type in INLINE_PREVIEWABLE_TYPES
disposition = "inline" if (inline_view and is_previewable) else "attachment"
...
self.set_header("Content-Type", mime_type)
self.set_header("Content-Disposition", f'{disposition}; filename="{safe_doc_name}"')
```

Impact:        A corrupted or attacker-influenced stored MIME value controls a response header, and inline serving expands browser rendering exposure. This violates the attachment-only control requested for regulated uploads.  
Fix:           Re-detect type from validated magic bytes or a canonical server-side document-type mapping at download time; map unknown values to `application/octet-stream`; use attachment universally unless a separately sandboxed preview service is approved.

### FINDING-BSA-015

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      Transport  
File:          `arie-backend/screening.py`; `arie-backend/server.py`; `arie-backend/claude_client.py`; `arie-backend/gdpr.py`; `arie-backend/sumsub_client.py`  
Line(s):       `screening.py:290-355`; `server.py:4672`; `claude_client.py:1932-1934,2114-2119,2424-2452`; `gdpr.py:753`; `sumsub_client.py:379,684,1050-1062` and provider-response paths  
Function:      `lookup_opencorporates`; password reset/logging paths; Claude response parser/file readers; DSAR creation; Sumsub client methods  
Description:   Logs can contain PII, local paths, raw AI response text, provider identifiers, and—on OpenCorporates connection exceptions—a URL whose query parameters include the API token. While no code directly formats a named secret variable into a log call, Requests exception text can reproduce its prepared query URL. Pydantic validation errors may also echo rejected input values.  
Evidence:

```python
params = {"q": company_name, "api_token": OPENCORPORATES_API_KEY}
resp = requests.get(
    f"{OPENCORPORATES_API_URL}/companies/search",
    params=params,
    timeout=15
)
...
except Exception as e:
    logger.error(f"OpenCorporates error: {e}")
```

```python
logger.error(f"Failed to parse Claude response as JSON: {e}\nText: {text[:200]}")
...
logger.error(
    f"H-02 SECURITY: AI output REJECTED for {agent_method} — schema validation failed: {e}"
)
```

```python
logger.info("DSAR created: type=%s, email=%s, due=%s",
            request_type, requester_email, due_at)
```

The full logging corpus and sensitive-term flags are in `logging_calls.csv`.  
Impact:        Credentials or regulated PII can enter centralized logs, backups, alerts, and support systems with broader retention/access than the source records.  
Fix:           Log exception classes/reason codes rather than raw provider exceptions; redact query strings and secret-bearing keys centrally; hash/mask emails and external IDs; never log raw AI text or Pydantic input values; add structured logger redaction tests.

### FINDING-BSA-016

Severity:      LOW  
Verification:  CODE-VERIFIED  
Category:      Auth  
File:          `arie-backend/server.py`; `arie-backend/sumsub_client.py`  
Line(s):       `server.py:30912-30922`; `sumsub_client.py:1122-1140`  
Function:      `SumsubWebhookHandler.post`; `SumsubClient.verify_webhook_signature`  
Description:   Signature verification is correctly performed before parsing with HMAC-SHA256 and constant-time comparison, but an invalid/missing signature returns 401. This contradicts the requested indistinguishable 200 acknowledgment.  
Evidence:

```python
if not sumsub_verify_webhook(body, signature, digest_alg=_digest_alg or None):
    logger.warning("Sumsub webhook: Invalid or missing signature")
    return self.error("Invalid signature", 401)
```

```python
expected = hmac.new(
    self.webhook_secret.encode("utf-8"),
    payload,
    hashlib.sha256
).hexdigest()
is_valid = hmac.compare_digest(expected, signature_header or "")
```

Impact:        The endpoint reveals signature acceptance status and may cause provider retries/noise. It does not permit processing of an invalid payload.  
Fix:           If provider delivery semantics permit, record a sanitized security metric and return the same minimal 200 body as an accepted no-op while performing no payload parsing or state mutation.

### FINDING-BSA-017

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      Reliability  
File:          `arie-backend/server.py`  
Line(s):       30982-31006  
Function:      `SumsubWebhookHandler.post`  
Description:   The atomic database insert is the correct idempotency primitive, but the handler catches every exception—not only a unique-key violation—and returns `already_processed`. Connection failure, schema drift, permission failure, or storage exhaustion therefore masquerades as a duplicate.  
Evidence:

```python
try:
    db.execute("""
        INSERT INTO webhook_processed_events
            (event_digest, event_type, applicant_id, external_user_id, review_answer, received_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (...))
except Exception:
    try:
        db.rollback()
    except Exception:
        pass
    logger.info("Sumsub webhook: duplicate delivery skipped ...")
    self.set_status(200)
    self.write(json.dumps({"status": "already_processed"}))
    return
```

Impact:        A genuine first delivery can be permanently acknowledged without processing during any database error.  
Fix:           Catch the database driver's unique-constraint exception and verify the existing digest row. Roll back and return 503 for all other storage failures.

### FINDING-BSA-018

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      Reliability  
File:          `arie-backend/server.py`  
Line(s):       31104-31146  
Function:      `SumsubWebhookHandler.post`  
Description:   Each matched application update catches and logs any exception. The handler then commits the idempotency row and successful updates, returns 200, and makes the failed application permanently ineligible for retry under the same digest.  
Evidence:

```python
for app_id in matched_app_ids:
    try:
        ...
        db.execute("UPDATE applications SET prescreening_data=? WHERE id=?",
                   (json.dumps(pdict), row["id"]))
    except Exception as e:
        logger.error(
            "Sumsub webhook: failed to update application %s applicant=%s: %s",
            app_id, _masked_id, e,
        )

db.commit()
...
self.set_status(200)
self.write(json.dumps({"status": "ok"}))
```

Impact:        KYC review state can remain stale for one or more applications while the webhook is recorded as processed and all provider retries are deduplicated.  
Fix:           Make all required mapped updates atomic with the processed marker, or store per-target delivery states and return/retry until every target is durably complete. Never mark a partially failed event complete.

### FINDING-BSA-019

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      AI Security  
File:          `arie-backend/claude_client.py`; `arie-backend/config.py`; `arie-backend/tests/test_p11_5_ai_hardening.py`  
Line(s):       `claude_client.py:982-1066,1797-1820`; `config.py:109-112`; `test_p11_5_ai_hardening.py:60-69,250-260`  
Function:      `ClaudeClient._sanitize_for_prompt`; `_deep_sanitize`; `extract_document_fields`  
Description:   A three-pass recursive sanitizer exists, but prompt fencing defaults off. Even when enabled, `entity_name` and `person_name` are interpolated into the field-extraction prompt before any sanitizer call. Uploaded document content remains untrusted multimodal content; the anti-injection system directive is also flag-gated.  
Evidence:

```python
max_passes = 3
for _ in range(max_passes):
    cleaned = result
    for pattern in _injection_patterns:
        cleaned = re.sub(pattern, '[BLOCKED]', cleaned)
    if cleaned == result:
        break
    result = cleaned
```

```python
ENABLE_AI_PROMPT_FENCING = os.getenv(
    "ENABLE_AI_PROMPT_FENCING", "false"
).lower() == "true"
```

```python
context_hint = ""
if entity_name:
    context_hint += f" The expected entity name is '{entity_name}'."
if person_name:
    context_hint += f" The expected person name is '{person_name}'."
...
display_file_name = self._sanitize_for_prompt(...) if _fencing else file_name
user_prompt = (
    f"Document type: {display_doc_type}\nFile: {display_file_name}{context_hint}\n\n"
    ...
)
```

Impact:        Client-supplied metadata or hostile document content can alter model behavior in authoritative document verification and decision-support workflows.  
Fix:           Enable fencing fail-closed in deployed environments; sanitize every untrusted scalar including names; delimit untrusted metadata and document content as data; require schema/semantic validation and human approval so prompt defenses are not the sole boundary.

### FINDING-BSA-020

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      AI Security  
File:          `arie-backend/claude_client.py`; `arie-backend/document_verification.py`  
Line(s):       `claude_client.py:209-268,1837-1840,2427-2468`; `document_verification.py:1515-1563`  
Function:      `ClaudeClient.extract_document_fields`; `_parse_json_response`; `verify_document_layered`; `run_rule_checks`  
Description:   Six AI methods have Pydantic schemas, but `extract_document_fields` is absent from `_AGENT_SCHEMAS`. `_parse_json_response` therefore returns any parsed JSON value; the caller accepts any dict and feeds its fields into deterministic compliance checks.  
Evidence:

```python
_AGENT_SCHEMAS = {
    "score_risk": RiskScoreSchema,
    "verify_document": DocumentVerificationSchema,
    "analyze_corporate_structure": CorporateStructureSchema,
    "assess_business_plausibility": BusinessPlausibilitySchema,
    "interpret_fincrime_screening": FinCrimeScreeningSchema,
    "generate_compliance_memo": ComplianceMemoSchema,
}
```

```python
parsed = self._parse_json_response(raw, "extract_document_fields")
if isinstance(parsed, dict):
    return {k: v for k, v in parsed.items() if v is not None}
```

```python
extracted_fields = claude_client.extract_document_fields(...)
...
rule_results = run_rule_checks(
    doc_type, category, extracted_fields, prescreening_data, risk_level
)
```

Impact:        Type-confused, unexpected, or malicious AI output can influence authoritative name/date/registration checks without raw-output validation.  
Fix:           Define strict per-document extraction Pydantic models with forbidden extra fields, bounded strings/lists, canonical dates and identifiers. Reject validation failure and route to manual review before running deterministic checks.

### FINDING-BSA-021

Severity:      MEDIUM  
Verification:  CODE-VERIFIED  
Category:      AI Security / Reliability  
File:          `arie-backend/config.py`; `arie-backend/claude_client.py`  
Line(s):       `config.py:102-112`; `claude_client.py:73-149,2184-2226,2268-2290,2378-2392`  
Function:      `_ai_breaker_preflight`; `_ai_breaker_record_failure`; `ClaudeClient.generate`; `_call_claude`  
Description:   Both paid Anthropic paths correctly call the breaker and set a timeout, and non-retryable 4xx/auth errors are not retried. However, the breaker defaults disabled and stores state in a module dictionary, so even when enabled it isolates only one worker. Half-open permits a bounded burst rather than one service-wide probe.  
Evidence:

```python
ENABLE_AI_CIRCUIT_BREAKER = os.getenv(
    "ENABLE_AI_CIRCUIT_BREAKER", "false"
).lower() == "true"
```

```python
_AI_BREAKER_LOCK = _threading.Lock()
_AI_BREAKER = {"consecutive_failures": 0, "open_until": 0.0}
...
if not enabled:
    return
```

```python
_ai_breaker_preflight(operation="_call_claude")
response = self.client.messages.create(..., timeout=timeout)
...
if not failure.get("retryable"):
    raise VerificationProviderError(failure) from e
```

Impact:        In default configuration, every request continues hitting a failing provider. With multiple workers, each worker independently reaches the threshold and retries, amplifying outage load and latency.  
Fix:           Require the breaker in staging/production, store breaker/half-open lease state in a shared atomic backend, permit one probe per service, and expose open/half-open metrics/readiness detail.

### FINDING-BSA-022

Severity:      HIGH  
Verification:  CODE-VERIFIED  
Category:      Reliability  
File:          `arie-backend/server.py`; `arie-backend/security_hardening.py`  
Line(s):       `server.py:1231-1240,44229-44234,44426-44428`; `security_hardening.py:3938`  
Function:      `make_app`; main listener startup; upload handlers  
Description:   The intended pre-buffer request-body cap is passed as an Application setting, not to `HTTPServer`/`Application.listen`, so Tornado does not enforce it before buffering. Endpoint checks occur after `self.request.body` exists and use inconsistent 10/20/25 MiB ceilings.  
Evidence:

```python
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
MAX_REQUEST_BODY_MB = int(os.getenv("MAX_REQUEST_BODY_MB", "20"))
```

```python
return tornado.web.Application(
    routes,
    ...
    max_body_size=MAX_REQUEST_BODY_MB * 1024 * 1024,
)
...
app.listen(PORT, address="0.0.0.0")
```

```python
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB
```

Impact:        An unauthenticated or authenticated peer can force each process to buffer oversized request bodies before handler validation, exhausting memory; obscure 25 MiB routes bypass the nominal 10 MiB policy.  
Fix:           Pass `max_body_size=...` to `app.listen`/an explicit `HTTPServer`, add streaming handlers where needed, reject on Content-Length and streamed byte count, and define one canonical per-route policy whose maximum never exceeds the framework cap.

### FINDING-BSA-023

Severity:      MEDIUM  
Verification:  CODE-VERIFIED  
Category:      Reliability  
File:          `arie-backend/supervisor/api.py`  
Line(s):       43-49, 438-472, 479-525  
Function:      `PipelineRunHandler.post`; `PipelineReviewPackageHandler.get`; `ReviewSubmitHandler.post`  
Description:   Pipeline detail correctly reloads the durable store, but review-package generation and review submission require the process-local `_pipeline_cache`. A restart or load-balancer hop to another worker returns 404 for a durably persisted pipeline.  
Evidence:

```python
_pipeline_cache: Dict[str, SupervisorPipelineResult] = {}
...
_pipeline_cache[result.pipeline_id] = result
```

```python
result = _pipeline_cache.get(pipeline_id)
if not result:
    return self.write_error_json(404, "Pipeline not found")
```

Impact:        Human review becomes intermittently unavailable or loses continuity under normal horizontal scaling/deployment, delaying regulated decisions.  
Fix:           Rehydrate `SupervisorPipelineResult` from the durable pipeline result for both routes; remove the cache as an authorization/workflow dependency.

### FINDING-BSA-024

Severity:      LOW  
Verification:  CODE-VERIFIED  
Category:      Dependency  
File:          `arie-backend/requirements.txt`; `arie-backend/Dockerfile`  
Line(s):       `requirements.txt:33-34,48-49`; `Dockerfile:74-75`  
Function:      production dependency manifest/entrypoint  
Description:   `aiosqlite` and `gunicorn` are direct production pins but are not imported by production Python. The container runs `python server.py`, not Gunicorn.  
Evidence:

```text
gunicorn==25.1.0
...
aiosqlite==0.22.1
```

```dockerfile
CMD ["python", "server.py"]
```

Production import search found no `aiosqlite` or `gunicorn` use.  
Impact:        Unused packages expand the installed attack surface and vulnerability/upgrade workload.  
Fix:           Remove both pins and regenerate the hash lock unless a documented production entrypoint or migration proves they are required.

### FINDING-BSA-025

Severity:      LOW  
Verification:  CONFIG-VERIFIED  
Category:      Dependency  
File:          `.github/workflows/ci.yml`; `arie-backend/requirements.txt`  
Line(s):       `ci.yml:141-155`; `requirements.txt:51-52`  
Function:      dependency-audit CI step  
Description:   WeasyPrint 68.1 is subject to `CVE-2026-49452`/`GHSA-jhhc-3hcp-qhm5`. CI deliberately ignores it because the vulnerable `presentational_hints=True` mode is not used and no fixed release exists, but the exception expires 2026-08-09.  
Evidence:

```yaml
# Allowlist: CVE-2026-49452 / GHSA-jhhc-3hcp-qhm5
# Reason: pip-audit reports no fixed WeasyPrint release; current PDF
# generation calls HTML(...).write_pdf() without presentational_hints=True,
# which is the vulnerable mode identified by the advisory.
# Review/expiry: 2026-08-09.
... pip-audit \
  -r arie-backend/requirements.txt \
  -r arie-backend/requirements-dev.txt \
  --ignore-vuln CVE-2026-49452
```

Impact:        The current call pattern is not the advisory's affected mode, but a future renderer option change or an expired exception can silently invalidate the risk acceptance.  
Fix:           Add a test forbidding `presentational_hints=True`, review before expiry, upgrade when a fix exists, and fail CI when the dated exception expires.

### FINDING-BSA-026

Severity:      MEDIUM  
Verification:  CODE-VERIFIED  
Category:      Dependency  
File:          `arie-backend/requirements.lock`; `arie-backend/requirements.txt`  
Line(s):       `requirements.lock:463-466,1063-1069`; `requirements.txt:27-28`  
Function:      dependency manifests  
Description:   `webencodings==0.5.1` (last release 2017) and `distro==1.9.0` (last release December 2023) exceed the prompt's 18-month no-release threshold. The direct Anthropic SDK 0.49.0 is approximately 17 months old—below the literal threshold—but substantially behind the current SDK line.  
Evidence:

```text
distro==1.9.0
    # via anthropic
...
webencodings==0.5.1
    # via
    #   cssselect2
    #   tinycss2
    #   tinyhtml5
```

```text
anthropic==0.49.0
```

Release evidence: [webencodings on PyPI](https://pypi.org/project/webencodings/), [distro on PyPI](https://pypi.org/project/distro/), [Anthropic 0.49.0 on PyPI](https://pypi.org/project/anthropic/0.49.0/).  
Impact:        Old transitive components may receive slow/no upstream security maintenance, while the stale AI SDK increases compatibility and security-fix lag.  
Fix:           Upgrade Anthropic and regenerate the lock; determine whether current WeasyPrint's tree can eliminate `webencodings`; formally risk-accept stable dormant packages only with compensating advisory monitoring and an owner/review date.

---

## Required conclusions

### 1. Authentication Coverage

**Route classification coverage: YES. Security adequacy: NO.**

All 220 runtime route patterns (214 API patterns; 257 method rows) are either covered by a direct/helper authentication gate or explicitly classified as public, pre-session, callback, static shell, redirect, or token-gated in `route_authentication_inventory.csv`. No unclassified runtime method remains.

That does not mean the authentication design is production-ready: distributed login brute-force protection is fail-open/non-atomic (BSA-007), and authoritative document verification is exposed through a client-authenticated dual-use route (BSA-011).

### 2. RBAC Coverage

| Principal | Server-side boundary | Result |
|---|---|---|
| `admin` | Admin-only user/reset/role/settings endpoints use explicit role gates; no SCO bypass found | Enforced for reviewed admin actions |
| `sco` | High-risk decisions, EDD signoff, overrides, enhanced requirements, assignment use explicit/central gates | Mostly enforced |
| `co` | Low/medium decision and RMI boundaries are enforced; audit visibility is broader than matrix | Partially enforced |
| `analyst` | Decision endpoints exclude analyst, but generic status PATCH permits EDD escalation and audit routes exceed matrix | **Not enforced consistently** |
| `client` | Back-office structural gate rejects client tokens; ownership protects application/document reads, but authoritative document verification is client-triggerable | **Not enforced at authority boundary** |

The prompt asks for “the 4 roles”; the repository actually has four officer roles plus the structurally separate `client` principal, so all five are reported.

### 3. Audit 2 Verdict

**REMEDIATE BEFORE PROCEEDING**

Blocking findings:

- BSA-002 — synchronous supervisor/Anthropic work blocks the IOLoop
- BSA-003 — ComplyAdvantage async callback performs synchronous DB/network/sleep
- BSA-004 — synchronous WeasyPrint rendering blocks request processing
- BSA-005 — EDD audit/actuation failure still returns successful memo
- BSA-007 — login brute-force control is per-process, non-atomic, and fail-open
- BSA-009 — analyst can transition a case into EDD despite matrix exclusion
- BSA-011 — client-triggered authoritative Agent 1 result lacks mandatory human approval/atomic audit
- BSA-012 — Sumsub document raw-path prefix traversal
- BSA-015 — credential/PII leakage paths in logs
- BSA-017 — any Sumsub idempotency insert error is acknowledged as duplicate
- BSA-018 — partial Sumsub update failure is permanently deduplicated
- BSA-019 — prompt fencing defaults off and leaves raw name fields
- BSA-020 — authoritative extraction output has no Pydantic schema
- BSA-022 — Tornado pre-buffer body cap is configured on the wrong object and upload limits diverge

Dependency CVE completeness and deployed values for `ALLOWED_ORIGIN`, AI fencing, and AI circuit-breaker flags remain explicitly `ENVIRONMENT-REQUIRED`; they are not silently treated as passing.
