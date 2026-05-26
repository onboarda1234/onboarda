# C3 Step 1 — ComplyAdvantage Screening Adapter Design Diagnosis

## Preflight

- Branch required by prompt: `c3/adapter-step1-diagnosis`.
- Operated against full 40-character commit SHA: `457810952038350070cea462826a2f0d2220f778` (`4578109 [C2 Step 2] ComplyAdvantage OAuth client implementation (#185)`).
- Verified post-PR-#185 signposts before diagnosis:
  - `arie-backend/screening_complyadvantage/auth.py`
  - `arie-backend/screening_complyadvantage/client.py`
  - `arie-backend/screening_complyadvantage/config.py`
  - `arie-backend/screening_complyadvantage/exceptions.py`
  - `arie-backend/screening_complyadvantage/normalizer.py`
  - `arie-backend/screening_complyadvantage/url_canonicalization.py`
  - `arie-backend/screening_complyadvantage/models/`
  - `ComplyAdvantageTokenClient` and `ComplyAdvantageClient` exports in `arie-backend/screening_complyadvantage/__init__.py:3-4,28-29`
  - `arie-backend/migrations/scripts/migration_017_screening_monitoring_subscriptions.sql`
- No production code in this PR. This is a design diagnosis for CTO review.
- Load-bearing dependencies for the design: C2 `CAConfig`, `ComplyAdvantageTokenClient`, `ComplyAdvantageClient`, and `CAError` subclasses; C1 `normalize_two_pass_screening`, `ScreeningApplicationContext`, `CAPaginatedCollection`, `CAWorkflowResponse`, `CAAlertResponse`, `CARiskDetail`, `CACustomerInput`, and `CACustomerResponse`.

## 1. `ScreeningProvider` interface audit

### 1.1 Exact interface signatures

`ScreeningProvider` is a concrete base class, not an `abc.ABC`; unimplemented methods raise `NotImplementedError` (`arie-backend/screening_provider.py:39-99`). Its exact provider-facing members are:

- `provider_name = ""` at `arie-backend/screening_provider.py:47`.
- `def screen_person(self, name, birth_date=None, nationality=None, entity_type="Person")` at `arie-backend/screening_provider.py:49`.
  - The docstring says it returns `dict: Normalized person screening result` at `arie-backend/screening_provider.py:59-60`.
- `def screen_company(self, company_name, jurisdiction=None)` at `arie-backend/screening_provider.py:64`.
  - The docstring says it returns `dict: Normalized company screening result` at `arie-backend/screening_provider.py:72-73`.
- `def run_full_screening(self, application_data, directors, ubos, client_ip=None)` at `arie-backend/screening_provider.py:77`.
  - The docstring says it returns `dict: Normalized screening report` at `arie-backend/screening_provider.py:87-88`.
- `def is_configured(self) -> bool` at `arie-backend/screening_provider.py:92`.
  - The docstring says it returns true when required configuration is available at `arie-backend/screening_provider.py:93-98`.

### 1.2 Sumsub adapter implementation audit

`SumsubScreeningAdapter` subclasses `ScreeningProvider` at `arie-backend/screening_adapter_sumsub.py:16-23` and sets `provider_name = "sumsub"` at `arie-backend/screening_adapter_sumsub.py:32`.

- `run_full_screening(self, application_data, directors, ubos, client_ip=None)` is implemented at `arie-backend/screening_adapter_sumsub.py:34-41`.
  - Receives the same four values as the interface.
  - Delegates to legacy `screening.run_full_screening(...)` at `arie-backend/screening_adapter_sumsub.py:39-40`.
  - Returns `normalize_screening_report(raw_report)` at `arie-backend/screening_adapter_sumsub.py:41`.
  - The adapter does not catch exceptions. Legacy `screening.run_full_screening()` documents that individual source failures are degraded, but a complete inability to produce data raises `ScreeningProviderError` at `arie-backend/screening.py:751-759`; the submission handler also catches `ScreeningProviderError` and generic `Exception` around `run_full_screening()` at `arie-backend/server.py:2787-2808`.
- `screen_person(self, name, birth_date=None, nationality=None, entity_type="Person")` is implemented at `arie-backend/screening_adapter_sumsub.py:43-85`.
  - Receives a name plus optional DOB, nationality, and entity type.
  - Calls `screening.screen_sumsub_aml(name, birth_date=..., nationality=..., entity_type=...)` at `arie-backend/screening_adapter_sumsub.py:53-61`.
  - Derives terminal/non-terminal state with `derive_screening_state(raw_result)` at `arie-backend/screening_adapter_sumsub.py:63`.
  - Returns a plain dict from `create_normalized_person_screening(...)` at `arie-backend/screening_adapter_sumsub.py:75-85`.
  - It does not catch provider exceptions; the underlying legacy function catches broad exceptions and returns provider-status dicts at `arie-backend/screening.py:212-220`.
- `screen_company(self, company_name, jurisdiction=None)` is implemented at `arie-backend/screening_adapter_sumsub.py:87-117`.
  - Receives company name and optional jurisdiction, but ignores `jurisdiction` and calls Sumsub with `entity_type="Company"` at `arie-backend/screening_adapter_sumsub.py:96-102`.
  - Returns a plain dict from `create_normalized_company_screening(...)` at `arie-backend/screening_adapter_sumsub.py:112-117`.
  - It does not catch exceptions; any unhandled exception bubbles.
- `is_configured(self) -> bool` is implemented at `arie-backend/screening_adapter_sumsub.py:119-125`.
  - Receives no arguments.
  - Reads `SUMSUB_APP_TOKEN` and `SUMSUB_SECRET_KEY` directly at `arie-backend/screening_adapter_sumsub.py:123-124`.
  - Returns `bool(token and secret)` at `arie-backend/screening_adapter_sumsub.py:125` and does not raise for missing configuration.

### 1.3 Canonical `run_full_screening` return shape

The canonical interface contract is a plain dict. Evidence:

- `ScreeningProvider.run_full_screening()` documents `dict: Normalized screening report` at `arie-backend/screening_provider.py:87-88`.
- The normalized full-report schema is defined as the plain dict schema `NORMALIZED_SCREENING_REPORT_SCHEMA` at `arie-backend/screening_models.py:61-82`.
- `create_normalized_screening_report(**kwargs) -> dict` is the factory for full report dicts at `arie-backend/screening_models.py:118-143`.
- `normalize_screening_report(raw_report: dict) -> dict` returns a dict and only adds metadata to a shallow-copy of the legacy dict at `arie-backend/screening_normalizer.py:106-121` and `arie-backend/screening_normalizer.py:130-138`.
- The CA normalizer also returns a plain dict: `normalize_two_pass_screening(...) -> dict` at `arie-backend/screening_complyadvantage/normalizer.py:97-108` and returns `_build_report(...)` at `arie-backend/screening_complyadvantage/normalizer.py:121`; `_build_report()` constructs a dict at `arie-backend/screening_complyadvantage/normalizer.py:321-339`.

Recommendation: C3 adapter methods should return plain normalized dicts, not new dataclasses or Pydantic objects.

### 1.4 `is_configured()` contract

Existing precedent is non-throwing boolean readiness:

- The interface says it returns true when API keys or equivalent configuration are available at `arie-backend/screening_provider.py:92-98`.
- Sumsub reads env vars and returns `bool(token and secret)` at `arie-backend/screening_adapter_sumsub.py:119-125`.
- Tests pin this behavior: missing token returns `False` at `arie-backend/tests/test_screening_adapter_sumsub.py:33-37`, and missing secret returns `False` at `arie-backend/tests/test_screening_adapter_sumsub.py:38-42`.

Recommendation: `ComplyAdvantageScreeningAdapter.is_configured()` should call `CAConfig.from_env()` and return `False` on `CAConfigurationError`, never raise for absent or invalid env vars. This reuses C2 validation while preserving the provider contract.

### 1.5 Other `ScreeningProvider` methods/properties

The provider interface has no other methods. The only provider-level property is `provider_name` at `arie-backend/screening_provider.py:47`. The remaining classes/functions in `screening_provider.py` are registry helpers: instance registry methods at `arie-backend/screening_provider.py:102-171`, module registry access at `arie-backend/screening_provider.py:174-180`, factory registry at `arie-backend/screening_provider.py:187-198`, `screening_abstraction_enabled()` at `arie-backend/screening_provider.py:214-221`, and factory helpers at `arie-backend/screening_provider.py:224-268`.

## 2. Factory registration pattern audit

### 2.1 Sumsub registration location

`server.py` registers Sumsub in the `__main__` block after supervisor setup and before `make_app()`:

- The supervisor setup block calls `setup_supervisor()` and `register_all_executors(...)` at `arie-backend/server.py:11738-11744`.
- Sumsub registration begins at `arie-backend/server.py:11749-11755`: it imports `SUMSUB_PROVIDER_NAME` and `register_provider`, imports `SumsubScreeningAdapter`, calls `register_provider(SUMSUB_PROVIDER_NAME, SumsubScreeningAdapter)`, then logs registration.
- `make_app()` starts afterward at `arie-backend/server.py:11757-11759`.

### 2.2 Factory function signature

`register_provider(name: str, factory) -> None` stores the factory as-is and does not instantiate it:

- The function signature and docstring are at `arie-backend/screening_provider.py:224-231`.
- The implementation assigns `_factory_registry[name] = factory` at `arie-backend/screening_provider.py:235-238`.
- `get_provider(name)` returns the same callable/object at `arie-backend/screening_provider.py:241-259`.
- Startup passes the adapter class itself, `SumsubScreeningAdapter`, at `arie-backend/server.py:11752-11754`.
- Tests call the returned factory with no arguments and assert it creates a `SumsubScreeningAdapter` at `arie-backend/tests/test_screening_provider_registry.py:394-402`.

Recommendation: register the CA adapter class itself: `register_provider(COMPLYADVANTAGE_PROVIDER_NAME, ComplyAdvantageScreeningAdapter)`. Constructor arguments should have defaults so the registry can instantiate with `factory()`.

### 2.3 `register_all_executors` sequence

`register_all_executors(supervisor_instance, DB_PATH)` initializes supervisor agent executors after `setup_supervisor(DB_PATH)` at `arie-backend/server.py:11738-11744`. Provider registration happens after that block at `arie-backend/server.py:11749-11755` and before `make_app()` at `arie-backend/server.py:11757-11759`. C3 should preserve this established sequence: migrations, supervisor setup/executor registration, provider registration, then app construction.

### 2.4 Registration ordering

Recommendation: register `complyadvantage` immediately after the existing Sumsub call. Ordering does not matter for lookup because `get_provider(name)` performs keyed dict access at `arie-backend/screening_provider.py:254-259`. However, `list_providers()` returns dict keys in insertion order at `arie-backend/screening_provider.py:262-268`, and tests expect Sumsub-only registration to produce `[SUMSUB_PROVIDER_NAME]` in the isolated registry at `arie-backend/tests/test_screening_provider_registry.py:385-391`. Placing CA after Sumsub minimizes behavioral drift and preserves Sumsub as the first registered provider in logs/listing.

## 3. Module structure proposal for C3

### 3.1 Proposed module locations

The C2 namespace already holds CA-specific config, auth, client, exceptions, normalizer, URL helper, and models under `screening_complyadvantage/` (`arie-backend/screening_complyadvantage/__init__.py:1-31`). C3 should live in the same namespace and add adapter-specific modules only:

- `arie-backend/screening_complyadvantage/adapter.py` — defines `ComplyAdvantageScreeningAdapter`, implements `ScreeningProvider`, exposes `COMPLYADVANTAGE_PROVIDER_NAME` if the constant is not placed in `screening_provider.py`.
- `arie-backend/screening_complyadvantage/orchestrator.py` — owns workflow create-and-screen, polling, alert/risk/deep-fetch traversal, and two-pass execution.
- `arie-backend/screening_complyadvantage/payloads.py` — maps RegMind application/person/company dicts into CA create-and-screen request payloads and relaxed-pass payloads.
- `arie-backend/screening_complyadvantage/polling.py` is optional; keep polling inside `orchestrator.py` unless that file becomes too large.

Justification: C2 keeps HTTP concerns in `client.py`, auth concerns in `auth.py`, config in `config.py`, and exception types in `exceptions.py` (`arie-backend/screening_complyadvantage/client.py:1-35`, `arie-backend/screening_complyadvantage/auth.py:1-68`, `arie-backend/screening_complyadvantage/config.py:1-36`, `arie-backend/screening_complyadvantage/exceptions.py:1-80`). C3 should follow the same single-responsibility boundary.

### 3.2 One file vs separate files

Recommendation: separate adapter, orchestration, and payload building.

- `adapter.py` should be thin like `screening_adapter_sumsub.py`, which delegates `run_full_screening()` to existing logic and normalizes at `arie-backend/screening_adapter_sumsub.py:34-41`.
- `orchestrator.py` should contain CA Mesh API workflow mechanics so HTTP sequencing is testable without provider-interface concerns.
- `payloads.py` should contain data mapping because RegMind input shapes are independent of CA polling and normalization.

This mirrors the project architecture split between rule engine, memo generation, validation, supervisor, Sumsub client, and screening modules described by the codebase layout; the existing C2 package also separates auth/client/config/model responsibilities rather than concentrating them in one file.

### 3.3 Composition with C2 client and C1 normalizer

Dependency graph:

- `ComplyAdvantageScreeningAdapter` (`adapter.py`)
  - depends on `ScreeningProvider` and registry constant/function patterns from `screening_provider.py` (`arie-backend/screening_provider.py:39-99`, `arie-backend/screening_provider.py:224-238`)
  - constructs or receives `CAConfig` from `screening_complyadvantage.config.CAConfig.from_env()` (`arie-backend/screening_complyadvantage/config.py:19-36`)
  - constructs or receives `ComplyAdvantageClient` (`arie-backend/screening_complyadvantage/client.py:22-35`)
  - delegates screening work to `ComplyAdvantageScreeningOrchestrator`
  - calls/passes through `normalize_two_pass_screening(...)` from the C1 normalizer (`arie-backend/screening_complyadvantage/normalizer.py:97-121`)
- `ComplyAdvantageScreeningOrchestrator` (`orchestrator.py`)
  - calls only `ComplyAdvantageClient.get()` and `.post()` (`arie-backend/screening_complyadvantage/client.py:31-35`)
  - validates CA responses into C1 output models such as `CAWorkflowResponse`, `CAAlertResponse`, `CARiskDetail`, `CACustomerResponse` (`arie-backend/screening_complyadvantage/models/output.py:175-199`)
  - uses `CAPaginatedCollection[T]` for collection envelopes (`arie-backend/screening_complyadvantage/models/primitives.py:29-33`)
- `payloads.py`
  - maps internal dicts into existing `CACustomerInput` where possible (`arie-backend/screening_complyadvantage/models/input.py:79-88`)
  - should not modify C1/C2 models.

## 4. CA workflow orchestration design

### 4.1 Two-pass invocation and public API

Recommendation: `screen_person()` and `screen_company()` should delegate to an orchestrator that runs both passes. Public API proposal:

```python
class ComplyAdvantageScreeningOrchestrator:
    def screen_customer_two_pass(self, *, strict_customer, relaxed_customer, application_context, monitoring_enabled=True) -> dict:
        ...
```

`screen_person(name, birth_date=None, nationality=None, entity_type="Person", **kwargs)` builds strict and relaxed `customer.person` payloads, invokes `screen_customer_two_pass(...)`, and returns the normalized dict for one subject. `screen_company(company_name, jurisdiction=None, **kwargs)` builds strict and relaxed `customer.company` payloads and uses the same orchestration path. `run_full_screening(application_data, directors, ubos, client_ip=None)` loops through company/director/UBO subjects and assembles a normalized full report consistent with `NORMALIZED_SCREENING_REPORT_SCHEMA` (`arie-backend/screening_models.py:63-82`).

Reasoning: the CA normalizer is already designed to accept strict and relaxed workflow/alert/deep-risk sets together (`arie-backend/screening_complyadvantage/normalizer.py:97-107`), and `merge_two_pass_results()` owns the dedupe semantics at `arie-backend/screening_complyadvantage/normalizer.py:159-197`.

### 4.2 Strict/relaxed concurrency

Recommendation: run strict and relaxed passes concurrently with `ThreadPoolExecutor(max_workers=2)` inside the orchestrator.

Justification:

- Existing screening is synchronous and imports `ThreadPoolExecutor` at `arie-backend/screening.py:21-22`.
- Legacy `run_full_screening()` documents concurrent HTTP calls at `arie-backend/screening.py:751-755` and submits them at `arie-backend/screening.py:783-840`.
- C2 client is synchronous (`ComplyAdvantageClient.get()`/`.post()` call blocking request flow at `arie-backend/screening_complyadvantage/client.py:31-79`), and token refresh is protected by `threading.RLock` in `ComplyAdvantageTokenClient` at `arie-backend/screening_complyadvantage/auth.py:47-68`.

Alternative: sequential strict then relaxed is simpler and avoids doubling concurrent CA workflow load. Default remains concurrent because the locked context requires every screening to run both passes and live polling can be variable/long.

### 4.3 Polling structure and bounds

Recommendation:

- Initial delay before first poll: `0` seconds after create-and-screen, because immediate workflow state is useful and the locked recon observed a 0s poll.
- Backoff: exponential with jitter and caps: start at 5 seconds, multiply by 1.6, cap at 30 seconds.
- Total timeout: 180 seconds per pass; raise `CATimeout` from C2 exceptions on expiry (`arie-backend/screening_complyadvantage/exceptions.py:58-61`).
- Completion condition: workflow `status == "COMPLETED"` and case-creation step status is either `COMPLETED` or `SKIPPED`. The C1 `ScreeningStatus` enum already accepts `COMPLETED` and `SKIPPED` at `arie-backend/screening_complyadvantage/models/enums.py:19-23`.
- In-progress condition: workflow `status == "IN-PROGRESS"` or missing/unfinished case-creation step.
- Unexpected terminal/malformed status: raise `CAUnexpectedResponse`, because C2 defines that for unexpected HTTP/JSON response semantics at `arie-backend/screening_complyadvantage/exceptions.py:76-79`.

Pseudocode:

```python
deadline = time.monotonic() + 180
sleep = 0
while True:
    if sleep:
        time.sleep(sleep * (0.9 + random.random() * 0.2))
    workflow = CAWorkflowResponse.model_validate(client.get(f"/v2/workflows/{workflow_id}"))
    case_status = workflow.step_details.get("case-creation", {}).status
    if workflow.status == "COMPLETED" and case_status in {"COMPLETED", "SKIPPED"}:
        return workflow
    if time.monotonic() >= deadline:
        raise CATimeout("ComplyAdvantage workflow polling timed out")
    sleep = 5 if sleep == 0 else min(sleep * 1.6, 30)
```

### 4.4 Three-layer fetch and pagination loops

Recommendation: use deterministic nested loops first; add batched parallel deep-risk fetch only if tests or sandbox timings show material latency.

Iteration structure:

1. `GET /v2/workflows/{workflow_id}` returns workflow state and alert identifiers. Validate into `CAWorkflowResponse`, whose `step_details` field is a dict of `CAStepDetail` at `arie-backend/screening_complyadvantage/models/output.py:169-181`.
2. Extract alert identifiers from workflow step details/cases according to the live response shape; for each alert id, call `GET /v2/alerts/{alert_id}` if required to obtain `CAAlertResponse` and profile context. `CAAlertResponse` holds `identifier`, optional `profile`, and `risk_details` collection at `arie-backend/screening_complyadvantage/models/output.py:183-187`.
3. For each alert id, call `GET /v2/alerts/{alert_id}/risks` and parse a `CAPaginatedCollection[...]`. Continue while `pagination.next` is truthy; stop when `pagination.next` is null/empty or `values` is empty. `CAPagination.next` is modeled at `arie-backend/screening_complyadvantage/models/primitives.py:18-27`, and `CAPaginatedCollection.values`/`.pagination` at `arie-backend/screening_complyadvantage/models/primitives.py:29-33`.
4. For every risk id discovered from all pages, call `GET /v2/entity-screening/risks/{risk_id}` and validate `CARiskDetail`, whose deep indicator tree is `values: list[CARiskDetailInner]` at `arie-backend/screening_complyadvantage/models/output.py:133-140`.

### 4.5 Failure mode policy

- Workflow timeout: raise `CATimeout`. This is fail-closed because no reliable normalized provider answer exists.
- Pagination next-link broken: raise `CAUnexpectedResponse`. Pagination is mandatory per locked context, and missing risk #26 is a material false-negative risk.
- Deep fetch returns 404 for a `risk_id`: raise `CAUnexpectedResponse` by default, with `risk_id` in sanitized context. A missing deep risk means the adapter cannot use precise deep-endpoint scores and indicator trees.
- Partial success: fail the subject screening rather than normalize partial results. C3 should not silently drop failed risks because the normalizer's hash and rollups are decision inputs (`compute_ca_screening_hash()` hashes merged CA truth at `arie-backend/screening_complyadvantage/normalizer.py:230-234`).

Alternative: degrade partial failures into `degraded_sources`. Reject for C3 because CA is the target authoritative provider and partial risk loss can hide PEP/sanctions/adverse-media hits.

### 4.6 Merge-by-`profile.identifier`

C1 already implements the merge. In this context, `profile.identifier` means CA's unique matched-profile identifier from the API response, modeled as `CAProfile.identifier` at `arie-backend/screening_complyadvantage/models/output.py:142-149`, not a generic RegMind field. `merge_two_pass_results(strict_deep, relaxed_deep)` maps risks by that profile id, unions profile ids, and assigns `surfaced_by_pass` at `arie-backend/screening_complyadvantage/normalizer.py:159-186`. It sets `"both"` when strict and relaxed contain the same profile at `arie-backend/screening_complyadvantage/normalizer.py:171-174`, `"strict"` for strict-only at `arie-backend/screening_complyadvantage/normalizer.py:174-176`, and `"relaxed"` for relaxed-only at `arie-backend/screening_complyadvantage/normalizer.py:177-179`. It also emits provenance counts at `arie-backend/screening_complyadvantage/normalizer.py:187-197`.

Adapter pseudocode should therefore not duplicate merge logic:

```python
strict = run_one_pass(strict_payload)
relaxed = run_one_pass(relaxed_payload)
return normalize_two_pass_screening(
    strict.workflow, strict.alerts, strict.deep_risks,
    relaxed.workflow, relaxed.alerts, relaxed.deep_risks,
    strict.customer_input, strict.customer_response, application_context,
)
```

## 5. Request payload mapping

### 5.1 Internal RegMind input shapes

`run_full_screening(application_data, directors, ubos, client_ip=None)` receives application data plus director and UBO lists:

- The submission path builds `scoring_input` through `build_prescreening_risk_input(...)` using application, prescreening data, directors, UBOs, and intermediaries at `arie-backend/server.py:2770-2783`, then calls `run_full_screening(scoring_input, directors, ubos, client_ip=client_ip)` at `arie-backend/server.py:2785-2790`.
- Manual screening builds `app_data = {"company_name", "country", "sector", "entity_type"}` at `arie-backend/server.py:7166-7171` and calls `run_full_screening(app_data, directors, ubos, ...)` at `arie-backend/server.py:7173`.
- Legacy screening reads `company_name` and `country` from `application_data` at `arie-backend/screening.py:760-761`.
- Legacy director/UBO dicts are read with `full_name`, `date_of_birth`, `nationality`, `is_pep`, `email`, and `ownership_pct` at `arie-backend/screening.py:791-808` and `arie-backend/screening.py:817-838`.
- Database schemas include director `person_key`, `first_name`, `last_name`, `full_name`, `nationality`, `is_pep`, `pep_declaration`, and `date_of_birth` at `arie-backend/db.py:350-363`; UBOs add `ownership_pct` at `arie-backend/db.py:365-378`.
- `get_application_parties()` returns lists of hydrated dicts with PII decrypted and JSON fields parsed at `arie-backend/party_utils.py:158-178`; `hydrate_party_record()` ensures `full_name` and parses `pep_declaration` at `arie-backend/party_utils.py:148-155`.

Representative input shape:

```python
application_data = {"company_name": "Acme Co", "country": "MU", "sector": "Payments", "entity_type": "Private Company"}
director = {"person_key": "...", "first_name": "Jane", "last_name": "Doe", "full_name": "Jane Doe", "date_of_birth": "1980-01-31", "nationality": "MU", "is_pep": False, "pep_declaration": {}}
ubo = {**director, "ownership_pct": 51.0}
```

### 5.2 DOB conversion

Recommendation: add a pure helper in `payloads.py`:

```python
def to_ca_dob(value):
    if not value:
        return None
    if isinstance(value, date):
        return {"day": value.day, "month": value.month, "year": value.year}
    if isinstance(value, str):
        # accept YYYY-MM-DD only; return None for partial/invalid strings
        try:
            parsed = datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
        return {"day": parsed.day, "month": parsed.month, "year": parsed.year}
```

The CA primitive supports structured `day`, `month`, `year` fields at `arie-backend/screening_complyadvantage/models/primitives.py:36-42`. Tests already validate partial `CADateOfBirth(year=1975)` support at `arie-backend/tests/test_screening_complyadvantage_models.py:162-166`, but the locked create-and-screen API contract from prior CA Mesh recon requires split integer DOB values for C3 create-and-screen requests, so C3 should emit full day/month/year only when available.

### 5.3 Address mapping

Existing canonical prescreening has a full-text registered address: `registered_address` maps from portal fields at `arie-backend/prescreening/fields.py:21-24` and is normalized to `entity.registered_address.full_text` at `arie-backend/prescreening/normalize.py:165-170`. Existing C1 input models have `CAAddress(address_line_1, address_line_2, city, state, postal_code, country)` at `arie-backend/screening_complyadvantage/models/input.py:29-36`, while the locked recon says CA customer input accepts richer postal fields (`full_address`, `address_line1`, `address_line2`, `town_name`, `postal_code`, `country_subdivision`, `country`, `country_code`, `location_type`).

Recommendation: C3 `payloads.py` should build plain request dicts with CA's live rich address keys and validate only the stable `customer` subset with existing C1 models where compatible. Mapping:

- `full_address` ← `registered_address`, `registered_office_address`, or canonical `entity.registered_address.full_text`.
- `address_line1` ← first line if structured input exists; otherwise omit.
- `town_name` ← city/town if structured input exists; otherwise omit.
- `country` / `country_code` ← `application_data["country"]` normalized to ISO where possible.
- `location_type` ← `"registered_address"` for company registered office, `"residential_address"` for person residential address.

Open risk: current C1 `CAAddress` names differ from locked rich-postal names. Because C3 must not modify `screening_complyadvantage/models/`, any stricter Pydantic request model should live outside `models/` until CTO approves a C1 model update.

### 5.4 Relaxed pass field stripping

Recommendation: relaxed person payload includes only:

```json
{
  "customer": {
    "person": {
      "first_name": "Jane",
      "last_name": "Doe",
      "full_name": "Jane Doe",
      "date_of_birth": {"day": 31, "month": 1, "year": 1980}
    }
  },
  "monitoring": {"entity_screening": {"enabled": true}},
  "configuration": {}
}
```

Strict person payload may include name, DOB, nationality, country, gender, residential/contact/personal-identification fields, and metadata. Relaxed excludes nationality, country/country of birth, gender, addresses/locations, documents, occupation, employer, wealth/funds, contact info, and custom fields.

For companies, strict includes name plus registration number, jurisdiction, incorporation date, entity type, industry, website, addresses, and metadata when available; relaxed includes company `name` only unless the locked Step 2 prompt chooses `name + registration_number` for companies. Default recommendation: company relaxed is name-only to match the policy intent of minimizing over-filtering.

### 5.5 Monitoring opt-in default and override

The locked create-and-screen contract requires `monitoring.entity_screening.enabled: true` by default. Existing C1 `CAMonitoringConfig.enabled` defaults to `False` at `arie-backend/screening_complyadvantage/models/input.py:91-94`, so C3 must explicitly set monitoring true in payload construction rather than relying on model defaults. This divergence is intentional for C3 because C1 models are stable and forbidden to modify in this step; a future C1 model-alignment PR can revisit the default if CTO wants the wire model to encode the C3 policy.

Recommendation: expose a keyword-only override on adapter/orchestrator methods:

```python
def screen_person(..., *, monitoring_enabled=True): ...
def screen_company(..., *, monitoring_enabled=True): ...
def screen_customer_two_pass(..., monitoring_enabled=True): ...
```

Only explicit `monitoring_enabled=False` disables monitoring. The request body always contains `"monitoring": {"entity_screening": {"enabled": monitoring_enabled}}` and `"configuration": {}`.

### 5.6 Customer-input vs profile-output models

Existing C1 models intentionally separate input and output:

- `models/input.py` defines `CACustomerPersonInput`, `CACustomerCompanyInput`, `CACustomerInput`, and `CACreateAndScreenRequest` at `arie-backend/screening_complyadvantage/models/input.py:38-107`.
- `models/output.py` defines `CAProfilePerson`, `CAProfileCompany`, `CAWorkflowResponse`, `CAAlertResponse`, and `CACustomerResponse` at `arie-backend/screening_complyadvantage/models/output.py:47-199`.
- Tests pin divergence between `CACustomerCompanyInput` and `CAProfileCompany` at `arie-backend/tests/test_screening_complyadvantage_models.py:153-160`.

Because C3 is forbidden from modifying `screening_complyadvantage/models/`, new adapter-specific request wrappers should live in `screening_complyadvantage/payloads.py` as plain dict builders or local Pydantic classes named `CACreateAndScreenMeshRequest` / `CAMeshMonitoringBlock`. If Step 2 needs reusable Pydantic models under `models/`, that should be a separate C1 model amendment, not C3.

## 6. Configuration and `is_configured()`

### 6.1 Instantiation inputs

At minimum, the adapter needs a C2 client. C2 client construction requires `CAConfig` and optionally a token client and timeout: `ComplyAdvantageClient.__init__(self, config, token_client=None, timeout=DEFAULT_TIMEOUT)` at `arie-backend/screening_complyadvantage/client.py:25-30`. `CAConfig` contains `api_base_url`, `auth_url`, `realm`, `username`, and `password` at `arie-backend/screening_complyadvantage/config.py:9-18`.

Recommendation: adapter constructor accepts optional DI plus stdlib-only polling knobs:

```python
def __init__(self, client=None, config=None, orchestrator=None, poll_timeout_seconds=180): ...
```

Do not add env vars for polling in C3; if configurable polling is desired, list it as an open question.

### 6.2 `is_configured()` readiness check

Recommendation: implement:

```python
def is_configured(self) -> bool:
    try:
        CAConfig.from_env()
        return True
    except CAConfigurationError:
        return False
```

`CAConfig.from_env()` validates all five locked CA env vars at `arie-backend/screening_complyadvantage/config.py:19-36`, including `COMPLYADVANTAGE_REALM == "regmind"` at `arie-backend/screening_complyadvantage/config.py:23-27`. This preserves the Sumsub non-throwing precedent described in Section 1.4.

### 6.3 Eager vs lazy client construction

Recommendation: lazy construction on first screening call, with constructor-time optional validation only when explicit `config` or `client` is provided.

Justification:

- Sumsub adapter instantiation has no side effects; tests assert adapter instance creation triggers no screening calls at `arie-backend/tests/test_screening_adapter_sumsub.py:228-231`.
- Provider registration stores the class as a factory and does not instantiate at registration time (`arie-backend/screening_provider.py:224-238` and `arie-backend/server.py:11752-11754`).
- C2 token acquisition is lazy: `ComplyAdvantageClient.request()` gets a token only when `.get()`/`.post()` is called at `arie-backend/screening_complyadvantage/client.py:37-40`; `ComplyAdvantageTokenClient.__init__()` only initializes cache/lock state at `arie-backend/screening_complyadvantage/auth.py:47-54`.

Therefore, `ComplyAdvantageScreeningAdapter()` should not read env vars or create tokens at import/registration time. First screening call should call `CAConfig.from_env()`, construct `ComplyAdvantageClient`, then execute.

## 7. Test strategy

### 7.1 Unit test split

Recommended files:

- `arie-backend/tests/test_screening_adapter_complyadvantage.py`
  - provider subclass and `provider_name`
  - zero side effects on import/instantiation, mirroring Sumsub tests at `arie-backend/tests/test_screening_adapter_sumsub.py:218-231`
  - `is_configured()` true/false behavior using C2 env vars
  - `screen_person`, `screen_company`, and `run_full_screening` delegate to orchestrator and return normalized dicts
- `arie-backend/tests/test_screening_complyadvantage_orchestrator.py`
  - create-and-screen payload path
  - workflow polling completion on `case-creation` `COMPLETED` and `SKIPPED`
  - timeout raises `CATimeout`
  - paginated `/alerts/{id}/risks` loops until `next` is null/empty
  - deep fetch failure policies
- `arie-backend/tests/test_screening_complyadvantage_payloads.py`
  - RegMind person/company mapping
  - DOB helper
  - strict vs relaxed payloads
  - monitoring default/override

### 7.2 Mocking C2 client

Recommendation: constructor injection of a client instance plus fake client classes in tests.

Evidence: existing C2 tests construct `ComplyAdvantageClient(FakeConfig(), token_client=...)` and replace `client.session` with a `MagicMock` at `arie-backend/tests/test_complyadvantage_client.py:38-44`. For C3, a higher-level `FakeCAClient` with `.get()` and `.post()` is better than patching internals because adapter/orchestrator behavior should be tested at the C2 boundary, not `requests` boundary. This also enforces the hard rule that adapter code calls `ComplyAdvantageClient.get()`/`.post()` rather than raw `requests`; C2 already imports and owns `requests` at `arie-backend/screening_complyadvantage/client.py:1-7`.

### 7.3 Fixture-based end-to-end tests

The C1 normalizer tests already use fixtures from `arie-backend/tests/fixtures/complyadvantage/`:

- Fixture directory is defined at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:39`.
- `_fixture(name)` loads JSON files at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:42-44`.
- `_objects(...)` validates fixture workflow, customer input/response, alerts, and deep risks into C1 models at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:67-83`.
- The README lists eight synthetic fixtures, including clean baseline, sanctions, PEP, RCA, adverse media, company, monitoring/full optional fields, and two-pass strict-misses-relaxed-catches at `arie-backend/tests/fixtures/complyadvantage/README.md:5-12`.

Recommendation: add a `FakeCAClient` that returns fixture-backed responses by workflow ID:

- `.post('/v2/workflows/create-and-screen', ...)` returns a synthetic `workflow_instance_identifier` for strict or relaxed based on payload marker.
- `.get('/v2/workflows/{id}')` returns fixture `strict_workflow`, `relaxed_workflow`, or `workflow`.
- `.get('/v2/alerts/{id}/risks')` returns paginated risk-id envelopes, including a two-page test for the 26th risk case.
- `.get('/v2/entity-screening/risks/{risk_id}')` returns fixture `risk_detail`.

Assertions should compare adapter output to existing normalizer output where possible, because `normalize_two_pass_screening()` is already tested to produce provider `complyadvantage` and normalized version `2.0` at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:261-270`.

### 7.4 Two-pass `s7` test

Use `two_pass_strict_misses_relaxed_catches.json`. The current C1 test loads strict and relaxed fixture objects at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:176-181` and asserts the canonical match has `surfaced_by_pass == "relaxed"` at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:182-184`.

C3 should add an adapter/orchestrator-level test with the fake client returning that fixture. Assert:

- both strict and relaxed workflows were created;
- strict lower-confidence profiles remain present unless intentionally filtered by normalizer policy;
- `prof-canonical` exists exactly once in `provider_specific.complyadvantage.matches`;
- `prof-canonical.surfaced_by_pass == "relaxed"`;
- top-level provenance counts match the C1 merge semantics.

### 7.5 Coverage target

Target 80% per new C3 module (`adapter.py`, `orchestrator.py`, `payloads.py`). This is consistent with the current test style of module-specific unit files for C2 (`arie-backend/tests/test_complyadvantage_client.py`, `arie-backend/tests/test_complyadvantage_auth.py`, `arie-backend/tests/test_complyadvantage_config.py`) and C1 (`arie-backend/tests/test_screening_complyadvantage_models.py`, `arie-backend/tests/test_screening_complyadvantage_normalizer.py`).

## 8. Open questions / explicit risks

### 8.1 Reasonable design forks

1. **Where to place `COMPLYADVANTAGE_PROVIDER_NAME`.**
   - Default recommendation: add it to `screening_provider.py` beside `SUMSUB_PROVIDER_NAME`, because Sumsub's canonical name lives there at `arie-backend/screening_provider.py:32-36` and startup imports the Sumsub constant from that module at `arie-backend/server.py:11752`.
   - Alternative: define it in `screening_complyadvantage/adapter.py` and export it in `screening_complyadvantage/__init__.py`. This avoids touching `screening_provider.py` but creates a second provider-name location.
2. **Concurrent vs sequential strict/relaxed passes.**
   - Default: concurrent with `ThreadPoolExecutor(max_workers=2)` to align with legacy screening concurrency (`arie-backend/screening.py:751-755`, `arie-backend/screening.py:783-840`).
   - Alternative: sequential to reduce CA load and simplify traces.
3. **Request model strictness.**
   - Default: build plain dict request bodies in `payloads.py` and validate compatible customer parts with existing C1 models.
   - Alternative: add Pydantic request models under `screening_complyadvantage/models/`, but this violates C3's forbidden-modification boundary unless CTO reopens C1 model scope.
4. **Partial deep-risk failures.**
   - Default: fail closed with `CAUnexpectedResponse`.
   - Alternative: persist partial normalized result with `degraded_sources`; reject by default because omitted risks can hide regulated hits.

### 8.2 Unknown CA behaviors

- Exact workflow response location for alert identifiers across clean vs match workflows. C1 models support step details and cases (`arie-backend/screening_complyadvantage/models/output.py:169-199`), but live Mesh response shape should be confirmed during Step 2 sandbox tests.
- Polling rate-limit shape and whether CA emits `Retry-After`. C2 maps status `429` to `CARateLimited` at `arie-backend/screening_complyadvantage/client.py:107-108`, but C3 needs a retry/backoff decision if polling hits 429.
- Whether `step_details.case-creation.status` can be absent, `IN-PROGRESS`, `FAILED`, or other values beyond `COMPLETED`/`SKIPPED`. Current enum only includes `IN-PROGRESS`, `COMPLETED`, and `SKIPPED` at `arie-backend/screening_complyadvantage/models/enums.py:19-23`.
- Whether `/v2/alerts/{id}/risks` `pagination.next` is an absolute URL or relative path. C2 `ComplyAdvantageClient._url()` expects a path and prepends `api_base_url` at `arie-backend/screening_complyadvantage/client.py:115-124`, so C3 may need a safe next-link-to-path helper.
- Whether `GET /v2/entity-screening/risks/{risk_id}` returns the same `CARiskDetail` root shape for all taxonomy families. Current C1 tests cover sanctions at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:145-149`, PEP at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:151-154`, RCA at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:157-160`, adverse media at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:163-167`, and company profiles at `arie-backend/tests/test_screening_complyadvantage_normalizer.py:170-173`.
- Whether company relaxed pass should be `name` only or `name + registration_number`. Default recommendation is name only; CTO may choose otherwise.
- Whether polling bounds should be configurable without new env vars. C3 hard rule forbids new env vars; constructor/test-only overrides are acceptable.

### 8.3 Scope-creep risks

- **Webhook handling belongs to C4.** C1 has webhook envelope models at `arie-backend/screening_complyadvantage/models/webhooks.py:1-69`, but C3 must not add webhook handlers or resnapshot flows. `normalize_single_pass()` exists for event-driven resnapshot at `arie-backend/screening_complyadvantage/normalizer.py:124-156`; C3 should reference it only as future C4 context.
- **Dual-write to `monitoring_alerts` belongs to C4.** C3 should at most produce normalized reports; it must not create monitoring-alert writes. Existing normalized storage helper persists only `screening_reports_normalized` rows at `arie-backend/screening_storage.py:89-121`.
- **Production call-site migration belongs to Track E.** `ENABLE_SCREENING_ABSTRACTION` defaults off in all environments at `arie-backend/screening_config.py:17-27`, and active provider defaults to `sumsub` at `arie-backend/screening_config.py:29-35`. C3 should register CA unconditionally but not flip flags or route production traffic.
- **Legacy paths are off-limits.** Sumsub adapter wraps legacy `screening.py` without moving logic (`arie-backend/screening_adapter_sumsub.py:1-10`); C3 should not modify `screening.py` or `sumsub_client.py`.
- **C1/C2 stability.** C3 must use `normalize_two_pass_screening()` (`arie-backend/screening_complyadvantage/normalizer.py:97-121`) and `ComplyAdvantageClient.get()`/`.post()` (`arie-backend/screening_complyadvantage/client.py:31-35`) as stable boundaries, not alter or bypass them.
