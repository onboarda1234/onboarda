# C2 Step 1 — ComplyAdvantage OAuth Client Design Diagnosis

## Preflight

- Branch: `c2/oauth-client-step1-diagnosis`.
- Operated against commit SHA: `397ee3c115d15e0bc71f0b035bdaab21298b8781` (`397ee3c [codex] Fix upload latency CloudWatch query sort syntax (#183)`).
- Verified post-PR-#178 signposts before diagnosis:
  - `arie-backend/screening_complyadvantage/normalizer.py`
  - `arie-backend/screening_complyadvantage/url_canonicalization.py`
  - `arie-backend/screening_complyadvantage/models/`
  - `normalize_two_pass_screening` and `normalize_single_pass` in `arie-backend/screening_complyadvantage/normalizer.py` lines 97 and 124
  - `arie-backend/migrations/scripts/migration_017_screening_monitoring_subscriptions.sql`
- This PR contains no production code. It is a design diagnosis for CTO review.

## 1. Existing patterns audit

### 1.1 HTTP client library in `sumsub_client.py`

`sumsub_client.py` uses synchronous `requests`.

Evidence:

- It imports `requests` and request exceptions at `arie-backend/sumsub_client.py:35-36`.
- `_request_with_retry()` issues synchronous calls via `requests.get()` and `requests.post()` at `arie-backend/sumsub_client.py:285-291`.
- The retry loop sleeps with blocking `time.sleep()` at `arie-backend/sumsub_client.py:323` and `arie-backend/sumsub_client.py:339`.

Recommendation: implement C2 with synchronous `requests` to align with the existing dependency set and avoid introducing `httpx` unless CTO explicitly chooses an async client in Section 7.

### 1.2 Tornado IOLoop and CA invocation contexts

`server.py` is a Tornado application running on Tornado's IOLoop:

- It imports `tornado.ioloop` and `tornado.web` at `arie-backend/server.py:56-57`.
- `make_app()` returns `tornado.web.Application` at `arie-backend/server.py:11674-11679`.
- Startup binds the app with `app.listen()` at `arie-backend/server.py:11776-11778` and starts `tornado.ioloop.IOLoop.current().start()` at `arie-backend/server.py:11818`.

Current screening/Sumsub call sites are overwhelmingly synchronous:

- Application submission calls `run_full_screening(...)` inside a regular handler method, not `async def`, at `arie-backend/server.py:2785-2790`.
- Manual screening calls `run_full_screening(...)` inside `ScreeningHandler.post()`, a synchronous handler declared at `arie-backend/server.py:7125-7127`, with the call at `arie-backend/server.py:7173`.
- Sumsub WebSDK token generation is synchronous: `SumsubAccessTokenHandler.post()` is declared at `arie-backend/server.py:7511-7513` and delegates to `sumsub_generate_access_token()` at `arie-backend/server.py:7523-7526`.
- Sumsub status lookup is synchronous: `SumsubStatusHandler.get()` is declared at `arie-backend/server.py:7530-7532` and delegates to `sumsub_get_applicant_status()` at `arie-backend/server.py:7552`.
- Sumsub webhook handling is synchronous: `SumsubWebhookHandler.post()` is declared at `arie-backend/server.py:7600-7627`.
- The one observed async handler in this area is `SupervisorRunHandler.post()` at `arie-backend/server.py:8213-8215`; it is not a screening-provider HTTP call site.

Within screening, calls are synchronous functions but may run concurrently on worker threads:

- `screening.py` imports `ThreadPoolExecutor` at `arie-backend/screening.py:22`.
- `run_full_screening()` documents that it uses `ThreadPoolExecutor` for concurrent HTTP calls at `arie-backend/screening.py:751-755`.
- It submits concurrent Sumsub AML and KYC tasks at `arie-backend/screening.py:783-840`.

Recommendation: C2 should expose a synchronous API first. Because current screening uses worker threads, token cache refresh coordination must be thread-safe even though Tornado itself runs a single IOLoop.

### 1.3 `config_loader.py` and env-var patterns

`config_loader.py` is not a general env-var API. It loads `jurisdiction_config.json`, caches it, and exposes typed jurisdiction-risk helpers:

- `_CONFIG_PATH` points at `jurisdiction_config.json` and `_config_data` caches the parsed JSON at `arie-backend/config_loader.py:19-20`.
- `_load()` reads the JSON file and caches it at `arie-backend/config_loader.py:23-34`.
- `JurisdictionConfig` exposes typed methods such as `is_sanctioned()`, `get_risk_score()`, and `get_monitoring_interval()` at `arie-backend/config_loader.py:37-95`.
- It creates a module singleton `config = JurisdictionConfig()` at `arie-backend/config_loader.py:96-97`.

The broader project has a centralized `config.py` for concrete environment values, but direct `os.environ` reads still exist:

- `config.py` states it is the unified configuration module and that modules should import values from there at `arie-backend/config.py:1-16`.
- `config.py` reads Sumsub env vars at module import time at `arie-backend/config.py:92-102`.
- `environment.py` provides getter functions for Sumsub credentials at `arie-backend/environment.py:406-446`.
- `sumsub_client.py` still reads `SUMSUB_APP_TOKEN`, `SUMSUB_SECRET_KEY`, `SUMSUB_BASE_URL`, and `SUMSUB_WEBHOOK_SECRET` directly in `__init__()` at `arie-backend/sumsub_client.py:182-186`.
- `screening_config.py` dynamically reads `ENABLE_SCREENING_ABSTRACTION` with `os.environ.get()` at `arie-backend/screening_config.py:38-52`.

Recommendation: add a CA-specific config module under `screening_complyadvantage/` with `CAConfig.from_env()` as the only env-reading boundary for C2. This intentionally does not extend `config_loader.py` because that module's scope is jurisdiction JSON, not secrets or external API config. Step 2 should either add these five locked values to centralized `config.py` and have `CAConfig.from_env()` read from `config.py`, or keep the reads local with an explicit comment that C2 is dormant and provider-scoped until Track E activation.

### 1.4 Structured logging and credential hygiene

Structured logging exists in `observability.py`:

- `StructuredFormatter.format()` emits a JSON object with timestamp, level, logger, and message at `arie-backend/observability.py:26-41`.
- `_log()` attaches arbitrary structured fields through `record.structured_data` at `arie-backend/observability.py:74-82`.
- Helpers such as `log_request_start()`, `log_request_end()`, and `log_error()` wrap `_log()` at `arie-backend/observability.py:85-99`.

Sumsub logging avoids credential leakage by logging operational events, not auth headers or secret values:

- `SumsubClient.__init__()` logs only whether live credentials exist, not the token or secret, at `arie-backend/sumsub_client.py:201-207`.
- `_log_non_2xx()` explicitly documents that auth headers and secrets are never logged at `arie-backend/sumsub_client.py:1152-1158` and logs endpoint, status, truncated response body, and IDs at `arie-backend/sumsub_client.py:1159-1168`.
- Webhook diagnostics log body length, algorithm, and 8-character HMAC prefixes only at `arie-backend/screening.py:646-654`.
- Tests assert Sumsub non-2xx logs do not contain `SUPER_SECRET_TOKEN` or `SUPER_SECRET_KEY` at `arie-backend/tests/test_sumsub_409_retry.py:133-151`.

Recommendation: CA logs may include method, path, status, duration, realm, and username. They must never include `password`, `access_token`, `Authorization`, or raw auth response bodies.

### 1.5 HTTP-calling test patterns

The tests use `unittest.mock`/`MagicMock`/`patch`, not `responses` or `requests_mock`, for Sumsub HTTP behavior:

- `test_sumsub_409_retry.py` imports `MagicMock` and `patch` at `arie-backend/tests/test_sumsub_409_retry.py:29`.
- It patches `client._request_with_retry` and supplies response tuples at `arie-backend/tests/test_sumsub_409_retry.py:41-48` and `arie-backend/tests/test_sumsub_409_retry.py:66-76`.
- It uses `caplog` to validate safe logging and secret redaction at `arie-backend/tests/test_sumsub_409_retry.py:133-151`.
- `test_sumsub_error_enrichment.py` imports `patch` and `MagicMock` at `arie-backend/tests/test_sumsub_error_enrichment.py:18-19` and patches `_request_with_retry` at `arie-backend/tests/test_sumsub_error_enrichment.py:48-51`, `59-62`, and `70-73`.
- A repository-wide search found no `responses`, `requests_mock`, or `httpretty` usage in tests.

Recommendation: C2 tests should use `unittest.mock.patch` against `requests.Session.request` or the CA client's private request method, plus fake response objects. Do not add a new HTTP mocking dependency.

### 1.6 Existing exception hierarchy for screening errors

Existing error classes are provider-local rather than shared:

- `sumsub_client.py` defines `SumsubAPIError`, `SumsubAuthError`, and `SumsubRetryError` at `arie-backend/sumsub_client.py:44-56`.
- `screening.py` defines `ScreeningProviderError` at `arie-backend/screening.py:723-725` for critical provider failures that prevent submission.
- `screening_provider.py` defines `ProviderNotRegistered` for registry lookup failures at `arie-backend/screening_provider.py:187-192`.
- `screening_provider.py` defines the abstract `ScreeningProvider` interface at `arie-backend/screening_provider.py:39-99`, but no shared provider exception hierarchy.

Recommendation: define CA-specific exceptions under `screening_complyadvantage/exceptions.py`. They should inherit from a CA base (`CAError`) and not inherit from Sumsub classes. The future C3 adapter may translate `CAError` to `ScreeningProviderError` only when a failure is critical to submission.

## 2. Module structure proposal

### 2.1 New module locations

Use the existing `screening_complyadvantage/` namespace:

- `arie-backend/screening_complyadvantage/config.py` — reads and validates the locked five env vars. This keeps CA config next to CA code and avoids extending `config_loader.py`, whose scope is jurisdiction JSON (`arie-backend/config_loader.py:1-12`, `19-34`).
- `arie-backend/screening_complyadvantage/exceptions.py` — defines `CAError` and typed subclasses. This mirrors Sumsub's local exception style (`arie-backend/sumsub_client.py:44-56`) without coupling to Sumsub.
- `arie-backend/screening_complyadvantage/auth.py` — owns OAuth token acquisition, cache, refresh, and auth-specific retry behavior. Keeping it separate from HTTP methods makes token behavior unit-testable in isolation.
- `arie-backend/screening_complyadvantage/client.py` — owns authenticated CA API requests (`get`, `post`) and the one-refresh-on-401 state machine.
- `arie-backend/screening_complyadvantage/__init__.py` — currently exports only `canonicalize_url` at `arie-backend/screening_complyadvantage/__init__.py:1-5`; update it only if Step 2 wants public imports. Keep import side effects at zero, consistent with `screening_provider.py` safety comments at `arie-backend/screening_provider.py:7-9`.

Do not modify `models/`, `normalizer.py`, `url_canonicalization.py`, or migrations in C2.

### 2.2 Token client shape

Recommend a class, not a module-level mutable state holder and not a process-global singleton by default.

Rationale:

- Sumsub offers a singleton accessor for reuse at `arie-backend/sumsub_client.py:1298-1343`, but C2 needs deterministic unit tests for token refresh, expiry, retry, and locking. Explicit instances are easier to test.
- `run_full_screening()` uses `ThreadPoolExecutor` (`arie-backend/screening.py:783-840`), so hidden module state would require global locks and reset hooks similar to `reset_sumsub_client()` at `arie-backend/sumsub_client.py:1346-1349`.
- A class can still be instantiated once by C3 adapter wiring when the provider becomes active.

Default: `ComplyAdvantageTokenClient(config: CAConfig, session: Optional[requests.Session] = None, clock: Callable[[], float] = time.time)`.

### 2.3 Authenticated client composition vs inheritance

Recommend composition: `ComplyAdvantageClient` owns a `ComplyAdvantageTokenClient`.

Rationale:

- The project describes adapters as thin wrappers around provider modules; `SumsubScreeningAdapter` delegates to `screening.py` rather than inheriting provider internals at `arie-backend/screening_adapter_sumsub.py:23-41` and `43-117`.
- The provider interface is modular and replaceable (`ScreeningProvider` methods at `arie-backend/screening_provider.py:39-99`).
- Token acquisition is a separate responsibility from CA resource requests. Composition lets tests replace the token client with a fake that returns tokens or raises auth errors.

## 3. OAuth token client design

### 3.1 Public API

Proposed class: `ComplyAdvantageTokenClient` in `screening_complyadvantage/auth.py`.

Public methods:

```python
class ComplyAdvantageTokenClient:
    def __init__(self, config: CAConfig, session: Optional[requests.Session] = None, clock: Callable[[], float] = time.time): ...
    def get_token(self) -> str: ...
    def force_refresh(self) -> str: ...
    def clear_cache(self) -> None: ...
```

Return types and failures:

- `get_token() -> str`: returns cached bearer token if fresh; otherwise refreshes. Raises `CAConfigurationError`, `CAAuthenticationFailed`, `CATimeout`, `CAServerError`, or `CAUnexpectedResponse`.
- `force_refresh() -> str`: bypasses freshness check, fetches a new token, updates cache, returns token. Same failures.
- `clear_cache() -> None`: removes token state; used by tests and by the authenticated HTTP client before a forced 401 refresh.

Auth request contract is locked: `POST https://api.mesh.complyadvantage.com/v2/token` with JSON body `{realm, username, password}` and `Content-Type: application/json`.

### 3.2 Token cache structure and staleness

Proposed private structure:

```python
@dataclass
class _TokenCache:
    access_token: str
    token_type: str
    expires_at_monotonic: float
    scope: str
```

- Store cache on the token client instance: `self._cache: Optional[_TokenCache]`.
- Compute expiry with a monotonic clock to avoid wall-clock changes: `expires_at = clock() + expires_in`.
- Treat a token as stale if `expires_at - clock() <= 60` seconds, per locked refresh strategy.
- Protect cache reads/writes with `threading.RLock` because existing screening work runs in `ThreadPoolExecutor` (`arie-backend/screening.py:783-840`). Tornado's IOLoop is single-threaded, but provider calls are not guaranteed to remain on the IOLoop.

### 3.3 Refresh trigger pseudocode

```text
get_token():
  with lock:
    if cache is absent:
      refresh_required = true
    else:
      remaining = cache.expires_at_monotonic - clock()
      if remaining <= 0:
        refresh_required = true
      elif remaining < 60:
        refresh_required = true
      else:
        return cache.access_token

  if refresh_required:
    return _refresh_under_lock()
```

`_refresh_under_lock()` should re-check freshness after acquiring the refresh lock so that a second racing caller can reuse the token fetched by the first caller.

### 3.4 Non-200/non-401 auth responses and retry policy

Existing Sumsub retry behavior retries 5xx and request exceptions with exponential delays `1s, 2s, 4s` via `2 ** attempt` and `max_retries=3` at `arie-backend/sumsub_client.py:166-180`, `281-345`. It does not retry 4xx responses (`arie-backend/sumsub_client.py:305-313`).

Recommended CA auth retry policy:

- Max attempts: 4 total attempts (initial + 3 retries), matching Sumsub's `max_retries=3` semantics.
- Retry only transient failures: HTTP 5xx, network errors, DNS failures, and timeouts.
- Do not retry HTTP 400/401/403. Map 401 to `CAAuthenticationFailed`; map 400/403 to `CABadRequest` or `CAAuthenticationFailed` depending on body.
- Base delay: 0.5s.
- Exponential factor: `delay = min(0.5 * 2 ** retry_index, 4.0)`.
- Jitter: add uniform random jitter in `[0, delay * 0.2]` to avoid synchronized refresh storms.
- Timeout: use the config/client default timeout described in Section 4.3.

This slightly improves on Sumsub by adding jitter and a lower base delay while preserving the same retry count pattern.

### 3.5 Concurrent token request races

Do not rely on Tornado single-threading. The screening pipeline explicitly uses worker threads for provider HTTP calls (`arie-backend/screening.py:751-755`, `783-840`).

Recommendation:

- Use a `threading.RLock` or `threading.Lock` around refresh coordination.
- Use double-checked freshness: check before lock, acquire lock, check again, then perform refresh.
- While one thread refreshes, other threads block on the lock and then reuse the refreshed token if it is fresh.
- Keep the lock scoped to token refresh only. Do not hold it during ordinary authenticated resource requests.

## 4. Authenticated HTTP client design

### 4.1 Public API

Proposed class: `ComplyAdvantageClient` in `screening_complyadvantage/client.py`.

```python
class ComplyAdvantageClient:
    def __init__(self, config: CAConfig, token_client: Optional[ComplyAdvantageTokenClient] = None, session: Optional[requests.Session] = None): ...
    def get(self, path: str, params: Optional[dict] = None, *, timeout: Optional[TimeoutConfig] = None) -> dict: ...
    def post(self, path: str, json_body: Optional[dict] = None, *, timeout: Optional[TimeoutConfig] = None) -> dict: ...
    def request(self, method: str, path: str, *, params: Optional[dict] = None, json_body: Optional[dict] = None, timeout: Optional[TimeoutConfig] = None) -> dict: ...
```

Return raw decoded JSON dictionaries from CA. Do not unwrap CA's universal collection envelope. The existing CA primitives model `{values, pagination}` as `CAPaginatedCollection` at `arie-backend/screening_complyadvantage/models/primitives.py:18-33`, and output models import that primitive at `arie-backend/screening_complyadvantage/models/output.py:3-9`. Keeping the envelope intact lets C3 adapters choose whether to parse into Pydantic models or manually traverse pagination.

Optional convenience for callers:

```python
def get_collection(self, path: str, params: Optional[dict] = None) -> CAPaginatedCollection[dict]
```

Default recommendation: do not add `get_collection()` in C2 unless tests show repeated envelope handling. Keep C2 as an authenticated HTTP layer only.

### 4.2 401 retry state machine

Required behavior: one 401 refresh and one retry; two consecutive 401s after refresh surface `CAAuthenticationFailed`.

State machine:

```text
START
  token = token_client.get_token()
  response = request(token)

if response.status != 401:
  map_response(response)

if response.status == 401:
  token_client.clear_cache()
  new_token = token_client.force_refresh()
  retry_response = request(new_token)

if retry_response.status == 401:
  raise CAAuthenticationFailed("ComplyAdvantage authentication failed after refresh")
else:
  map_response(retry_response)
```

Only a single retry is allowed per resource request. Do not enter a loop.

### 4.3 Timeout policy

Existing Sumsub uses a single timeout integer defaulting to 15 seconds (`arie-backend/sumsub_client.py:166-180`) and passes it directly to `requests` at `arie-backend/sumsub_client.py:285-291`.

Recommended CA timeout policy:

- Use `requests` tuple timeouts: `(connect_timeout, read_timeout)`.
- Defaults: connect timeout `3.0s`, read timeout `15.0s`.
- Configurable via constructor/config object, not new env vars in C2, because the five env vars are locked and no env-var renames/additions should be introduced without CTO approval.
- Allow per-call override through the `timeout=` keyword.
- Map `requests.exceptions.Timeout` to `CATimeout`.

### 4.4 Safe logging contract

Log these fields:

- `event`: `ca_auth_request`, `ca_auth_response`, `ca_api_request`, `ca_api_response`, or `ca_api_error`
- HTTP method
- URL path only, not full URL if it contains query parameters with sensitive values
- status code
- attempt number
- duration in milliseconds
- realm and username, because they are allowed non-sensitive values in the locked context
- exception class name for failures

Never log:

- `password`
- `access_token`
- `Authorization` header
- full token response body
- raw request body for `/v2/token`

This follows Sumsub's approach: log status and truncated non-secret response content (`arie-backend/sumsub_client.py:1159-1168`), and explicitly avoid secrets (`arie-backend/sumsub_client.py:1152-1158`).

### 4.5 Typed exceptions

Proposed hierarchy in `screening_complyadvantage/exceptions.py`:

```python
class CAError(Exception): ...
class CAConfigurationError(CAError): ...
class CAAuthenticationFailed(CAError): ...
class CARateLimited(CAError): ...
class CATimeout(CAError): ...
class CABadRequest(CAError): ...
class CAServerError(CAError): ...
class CAUnexpectedResponse(CAError): ...
```

Mapping:

- Missing/empty config: `CAConfigurationError`.
- Auth endpoint 401 or resource double-401 after refresh: `CAAuthenticationFailed`.
- HTTP 400/422: `CABadRequest`, carrying status, path, and sanitized body.
- HTTP 429: `CARateLimited`, carrying `Retry-After` when present.
- HTTP 5xx after retry exhaustion: `CAServerError`.
- Network timeout: `CATimeout`.
- Malformed JSON, missing `access_token`, wrong `token_type`, non-positive `expires_in`, or unexpected envelope shape: `CAUnexpectedResponse`.

Do not inherit these from `ScreeningProviderError`; that class is used for submission-level critical failures at `arie-backend/screening.py:723-758`, while C2 is provider HTTP infrastructure.

## 5. Configuration loading

### 5.1 Configuration class/module

Proposed module: `screening_complyadvantage/config.py`.

Proposed class:

```python
@dataclass(frozen=True)
class CAConfig:
    api_base_url: str
    auth_url: str
    realm: str
    username: str
    password: str

    @classmethod
    def from_env(cls) -> "CAConfig": ...
```

Required env vars, exactly as locked:

- `COMPLYADVANTAGE_API_BASE_URL`
- `COMPLYADVANTAGE_AUTH_URL`
- `COMPLYADVANTAGE_REALM`
- `COMPLYADVANTAGE_USERNAME`
- `COMPLYADVANTAGE_PASSWORD`

Behavior:

- Strip whitespace for validation.
- Missing or empty values raise `CAConfigurationError` with the missing env var names only.
- Do not log values during validation.
- Normalize trailing slash on `api_base_url` and `auth_url` internally without changing env var names.
- Validate `realm == "regmind"` and raise `CAConfigurationError` otherwise, because the realm is locked. This is a lowercase technical OAuth realm identifier confirmed by the CA contract, not a product-brand spelling of the internal back-office surface `RegMind`.

### 5.2 Startup vs lazy loading

Recommendation: load and validate CA config at provider instantiation, not module import, and instantiate the provider only when the abstraction is enabled or when tests explicitly instantiate it.

Rationale:

- The project has fail-fast startup patterns: `server.py` calls `validate_config()` before startup at `arie-backend/server.py:11692-11695`, and `validate_environment()` at `arie-backend/server.py:11697-11700`.
- `config.validate_config()` raises `ConfigError` for staging/production errors at `arie-backend/config.py:160-199`.
- `environment.validate_environment()` returns errors for unsafe production settings at `arie-backend/environment.py:247-305`.
- But C2 is dormant while `ENABLE_SCREENING_ABSTRACTION=false`; forcing CA config at app startup now would break current deployments where CA env vars may not be present in all environments.

Default: fail fast when a CA client/provider is constructed. C3/Track E can wire provider construction into startup when activation is intended.

### 5.3 Behavior with `ENABLE_SCREENING_ABSTRACTION=false`

When `ENABLE_SCREENING_ABSTRACTION=false`, the CA config loader should not instantiate automatically.

Evidence:

- Screening abstraction defaults off in every environment at `arie-backend/screening_config.py:17-27`.
- `is_abstraction_enabled()` returns false unless explicitly overridden at `arie-backend/screening_config.py:38-52`.
- `screening_provider.py` emphasizes no runtime side effects on import at `arie-backend/screening_provider.py:7-9` and initializes an empty factory registry at `arie-backend/screening_provider.py:195-198`.

Recommendation: importing `screening_complyadvantage.config`, `auth`, or `client` must not read env vars or validate config. Only `CAConfig.from_env()` should read env vars. This keeps C2 tests runnable and production dormant with the flag off.

## 6. Test strategy

### 6.1 Unit test plan

Use `pytest`, `unittest.mock`, and inline fake responses, matching Sumsub tests (`arie-backend/tests/test_sumsub_409_retry.py:29-48`, `arie-backend/tests/test_sumsub_error_enrichment.py:18-51`). Do not add `responses` or `requests_mock`.

Recommended test files:

- `arie-backend/tests/test_complyadvantage_config.py`
  - all five env vars present produces `CAConfig`
  - missing/empty values raise `CAConfigurationError`
  - wrong realm raises `CAConfigurationError`
  - no env read occurs at import time
- `arie-backend/tests/test_complyadvantage_auth.py`
  - first `get_token()` fetches token
  - second `get_token()` reuses cached token
  - token with more than 60s remaining is fresh
  - token with less than 60s remaining refreshes
  - expired token refreshes
  - 401 auth response raises `CAAuthenticationFailed`
  - 500/network timeout retries with patched sleep/jitter
  - concurrent `get_token()` calls issue one auth request
  - token and password are absent from logs
- `arie-backend/tests/test_complyadvantage_client.py`
  - `get()` and `post()` attach `Authorization: Bearer <token>`
  - non-401 success returns decoded JSON unchanged, including `{values, pagination}`
  - one resource 401 triggers `force_refresh()` and one retry
  - second 401 raises `CAAuthenticationFailed`
  - 429 maps to `CARateLimited`
  - timeout maps to `CATimeout`
  - malformed JSON maps to `CAUnexpectedResponse`

Mock OAuth response inline:

```python
{"access_token": "jwt-token", "token_type": "Bearer", "expires_in": 86400, "scope": "read:api write:api"}
```

Inline JSON is sufficient because the response shape is small and locked.

### 6.2 Live CA sandbox integration test

Recommend no live sandbox integration test in default CI.

Reasoning:

- The repository's CI runs all backend tests with coverage (`.github/workflows/ci.yml` lines 112-119), so a live external dependency would add flakiness and require secrets on every PR.
- The locked context already confirms the OAuth contract live against CA sandbox on 2026-04-28.
- C2's responsibility is deterministic OAuth/cache/HTTP behavior, which can be fully mocked.

Optional follow-up: add a manual-only test gated by `RUN_CA_SANDBOX_TESTS=true` and all five CA env vars. Mark it `pytest.mark.integration` and exclude it from normal CI. This should be a CTO-approved explicit decision, not part of C2 by default.

### 6.3 Coverage threshold

Project tooling currently has `.coveragerc` `fail_under = 30` at `arie-backend/.coveragerc:17-19`, while the task states the project standard for new modules is 80%.

Recommendation: target at least 80% line coverage for the new CA modules. This is achievable because the modules are small, deterministic, and can be tested without live network calls through mocked `requests.Session` responses, mocked clocks, and patched sleeps.

## 7. Open questions / explicit risks

### 7.1 Reasonable design alternatives

1. Synchronous `requests` vs async `httpx`/Tornado HTTP client
   - Synchronous `requests`: aligns with existing Sumsub implementation (`arie-backend/sumsub_client.py:35-36`, `285-291`) and existing dependency `requests==2.32.5` (`arie-backend/requirements.txt:16`). No new dependency.
   - Async client: would better fit Tornado's IOLoop in theory (`arie-backend/server.py:11818`) but most screening call sites are synchronous and thread-based (`arie-backend/screening.py:783-840`). It would require broader adapter and handler decisions.
   - Recommendation: default to synchronous `requests` for C2.

2. Class instance vs singleton accessor
   - Singleton accessor matches Sumsub's `get_sumsub_client()` pattern (`arie-backend/sumsub_client.py:1303-1343`).
   - Explicit class instances are easier to test and avoid hidden global token state.
   - Recommendation: class instance with optional future singleton factory if C3 needs it.

3. Fail-fast CA config at startup vs lazy construction
   - Startup validation aligns with existing reliability patterns (`arie-backend/server.py:11692-11700`, `arie-backend/config.py:160-199`).
   - Lazy construction keeps dormant C2 safe while `ENABLE_SCREENING_ABSTRACTION=false` (`arie-backend/screening_config.py:17-52`).
   - Recommendation: no import-time validation; fail fast when the CA client/provider is explicitly constructed.

4. Returning raw JSON vs parsing Pydantic models in C2
   - Raw JSON keeps C2 scoped to OAuth/authenticated HTTP and preserves the CA `{values, pagination}` envelope.
   - Pydantic parsing could catch schema drift earlier but risks pulling adapter/model concerns into C2.
   - Recommendation: return raw decoded JSON in C2; let C3 parse into existing models.

5. Add retry helper dependency such as `tenacity`
   - `tenacity` would reduce custom retry code.
   - It is a new top-level dependency and the hard rules require flagging, not adding, new dependencies.
   - Recommendation: no new dependency; implement small local retry loop.

### 7.2 Unknown CA behaviors to follow up

- Rate-limit response shape: status 429 is expected, but the exact body and `Retry-After` behavior are not confirmed.
- Token revocation triggers: unknown whether CA revokes all prior tokens on password rotation or concurrent logins.
- Concurrent refresh from the same user: unknown whether CA imposes auth endpoint throttles for simultaneous refreshes.
- Error body schema consistency: auth failure is known to return HTTP 401 with an error body, but 400/403/429/5xx response fields are not locked.
- Pagination link semantics: existing models represent `self`, `first`, `prev`, and `next` links (`arie-backend/screening_complyadvantage/models/primitives.py:18-26`), but whether those links are absolute URLs, relative paths, or stable across filters should be validated before C3 pagination traversal.
- Token clock skew: CA returns `expires_in=86400`; no server-issued `expires_at` was observed. The client should rely on local monotonic time and the locked 60-second refresh buffer.
