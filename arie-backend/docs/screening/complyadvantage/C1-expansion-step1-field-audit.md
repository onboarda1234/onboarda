# C1 Expansion Step 1 — ComplyAdvantage model field-set audit and expansion design

## Preflight

- Required branch: `c1-expansion/step1-field-audit-diagnosis`.
- Operated against commit SHA: `59aa48fb1f52492acf622d316b68f03c9d4ff739` (`59aa48f [C3 Step 2] ComplyAdvantage adapter implementation (#189)`).
- Required source-of-truth preflight was run before analysis: `git fetch origin main`, `git checkout FETCH_HEAD`, and `git --no-pager log --oneline -10`.
- Verified all required post-PR-#189 signposts: `models/output.py`, `models/primitives.py`, `models/enums.py`, `models/input.py`, `models/webhooks.py`, `normalizer.py`, `adapter.py`, `orchestrator.py`, `payloads.py`, `ComplyAdvantageScreeningAdapter` registration/import at `arie-backend/server.py:11754-11756`, and `arie-backend/tests/fixtures/complyadvantage/`.
- Required reading completed: all model files, normalizer, URL canonicalization helper, C3 adapter/orchestrator/payload/subscription files, all current CA tests, C2/C3 design docs, and all eight fixtures listed at `arie-backend/tests/fixtures/complyadvantage/README.md:5-12`.
- No production code in this PR. This is a design diagnosis for CTO review.

## 1. Model inventory audit

### 1.1 Pydantic model inventory

`output.py` contains the audit target output models: `CAAdditionalField` (`output.py:11-13`), `CAName` (`output.py:16-18`), `CARelationship` (`output.py:21-23`), `CAPosition` (`output.py:26-29`), `CAProfileCompanyName` (`output.py:32-34`), `CAProfileCompanyLocation` (`output.py:37-39`), `CAProfileCompanyRegistrationNumber` (`output.py:42-44`), `CAProfilePerson` with `names`, `date_of_birth`, `nationality`, `countries`, `relationships`, and `positions` (`output.py:47-53`), `CAProfileCompany` with `names`, `locations`, `registration_numbers`, and `entity_type` (`output.py:56-60`), `CAMatchDetails` (`output.py:63-66`), `CARiskType` with only `key` and `label` (`output.py:69-71`), `CASanctionValue` (`output.py:74-77`), `CAWatchlistValue` (`output.py:80-82`), `CAPEPValue` with `class_`, `position`, `country`, and alias config (`output.py:85-90`), `CAMediaArticleSnippet` (`output.py:93-95`), `CAMediaArticleValue` (`output.py:98-102`), four indicator classes (`output.py:105-122`), `CARiskDetailInner` (`output.py:133-135`), `CARiskDetail` (`output.py:138-139`), `CAProfile` (`output.py:142-158`), `CAStepDetail` (`output.py:169-172`), `CAWorkflowResponse` (`output.py:175-180`), `CAAlertResponse` (`output.py:183-186`), `CACaseResponse` (`output.py:189-192`), `CACustomerResponse` (`output.py:195-199`), `CAMonitoringState` (`output.py:202-204`), and `CAEntityScreeningState` (`output.py:207-210`). `CARiskIndicator` is a union alias, not a class, at `output.py:125-130`.

`input.py` contains `CAResidentialInformation` (`input.py:10-13`), `CAPersonalIdentification` (`input.py:16-20`), `CAContactInformation` (`input.py:23-26`), `CAAddress` with only `address_line_1`, `address_line_2`, `city`, `state`, `postal_code`, and `country` (`input.py:29-35`), `CACustomerPersonInput` (`input.py:38-61`), `CACustomerCompanyInput` (`input.py:64-76`), `CACustomerInput` with a person/company exactly-one validator (`input.py:79-88`), `CAMonitoringConfig` (`input.py:91-94`), `CAEntityScreeningConfig` (`input.py:97-100`), and `CACreateAndScreenRequest` (`input.py:103-106`).

`primitives.py` contains `CAPaginationMeta` (`primitives.py:10-15`), `CAPagination` (`primitives.py:18-26`), `CAPaginatedCollection[T]` (`primitives.py:29-33`), and `CADateOfBirth` (`primitives.py:36-42`). `enums.py` contains `NameType` (`enums.py:12-16`), `ScreeningStatus` (`enums.py:19-23`), and `WebhookType` (`enums.py:25-29`); it intentionally does not define strict AML taxonomy enums (`enums.py:1-7`). `webhooks.py` contains `CAWebhookCustomer` (`webhooks.py:8-11`), `CAWebhookSubject` (`webhooks.py:14-17`), `CAWebhookCaseStage` (`webhooks.py:20-24`), `CACaseCreatedWebhook` (`webhooks.py:27-38`), `CACaseAlertListUpdatedWebhook` (`webhooks.py:41-50`), `CAUnknownWebhookEnvelope` (`webhooks.py:53-62`), and `CAWebhookEnvelope` (`webhooks.py:65-69`).

### 1.2 Existing `extra` policy

Only `CAUnknownWebhookEnvelope` explicitly sets `ConfigDict(extra="allow")` (`webhooks.py:53-62`). `CAPEPValue` sets `ConfigDict(populate_by_name=True)` for the `class` alias but no `extra` policy (`output.py:85-90`). All other audited wire models omit `model_config`, so they use Pydantic v2's default `extra='ignore'`.

### 1.3 Optional/default pattern

The project pattern is `Optional[...] = None` for optional scalars and `Field(default_factory=...)` for lists/envelopes: examples include profile fields (`output.py:47-60`), media snippets (`output.py:98-102`), risk indicators (`output.py:133-135`), input addresses (`input.py:38-76`), and pagination values (`primitives.py:29-33`). Step 2 should use the same pattern and default new recon fields to optional/list-empty unless fixture enrichment proves they are always present.

### 1.4 Discriminated-union axes

The modeled axes are `CASanctionIndicator`, `CAWatchlistIndicator`, `CAPEPIndicator`, and `CAMediaIndicator` (`output.py:105-122`), unified as `CARiskIndicator` (`output.py:125-130`). `CARiskDetailInner.indicators` and `CAProfile.risk_indicators` use that union (`output.py:133-135`, `output.py:145-151`). C3 dispatches by taxonomy key/prefix at `orchestrator.py:303-311`, and normalizer rollups depend on the concrete classes at `normalizer.py:200-227`.

## 2. Recon evidence enumeration

The repo-local fixtures are explicitly synthetic s2-s7 response shapes (`arie-backend/tests/fixtures/complyadvantage/README.md:3-12`). C3 Step 1 confirms the runtime response traversal is workflow, alert-risk pages, then deep risks parsed into `CARiskDetail` (`arie-backend/docs/screening/complyadvantage/C3-step1-adapter-design.md:213-223`) and identifies the s7 two-pass fixture (`C3-step1-adapter-design.md:440-450`). C3 also documents live rich-address request keys and the current `CAAddress` mismatch (`C3-step1-adapter-design.md:291-303`). The prompt supplies additional locked live recon not yet fully captured in repo fixtures: full s3 PEP value fields, live `CARiskType {key,name,taxonomy}`, and nested `CAProfilePerson.additional_fields[]` / `risk_indicators[]`.

| Model | Field(s) | Evidence/scenario | Type/cardinality | Status |
|---|---|---|---|---|
| `CARiskType` | `key`, `label` | Current deep-risk fixtures line 1; normalizer key set (`normalizer.py:27-61`) | strings | PASS (`output.py:69-71`) |
| `CARiskType` | `name`, `taxonomy` | Locked live recon | optional strings, dotted taxonomy path | MISSING |
| `CAPEPValue` | `class`, `position`, `country` | PEP/RCA/sanctions fixtures line 1; alias test (`test_screening_complyadvantage_models.py:168-171`) | string + optional strings | PASS (`output.py:85-90`) |
| `CAPEPValue` | `level`, `scope_of_influence`, `political_position_type`, `institution_type`, `political_positions[]`, `political_parties[]`, `active_start_date`, `active_end_date`, `issuing_jurisdictions[]`, source metadata | Locked s3 recon | optional strings, lists, dict metadata | MISSING |
| `CAProfilePerson` | current `names`, `date_of_birth`, `nationality`, `countries`, `relationships`, `positions` | Person fixtures line 1; normalizer reads name/nationality/relationships (`normalizer.py:253-270`, `normalizer.py:413-420`, `normalizer.py:478-481`) | paginated collections, strings, list | PASS (`output.py:47-53`) |
| `CAProfilePerson` | `additional_fields[]`, nested `risk_indicators[]` | Locked live recon | likely collection/list and `list[CARiskIndicator]` | MISSING |
| `CAProfileCompany` | current company fields | `company_canonical.json` line 1 | paginated collections + optional string | PASS (`output.py:56-60`) |
| `CAProfileCompany` | `additional_fields[]`, nested `risk_indicators[]` | Consistency risk from live profile shape and company fixture (`README.md:10`) | collection/list | MISSING / needs fixture lock |
| `CAProfile` | root `entity_type` discriminator | Prompt plus C3 profile/deep-risk shape uncertainty (`C3-step1-adapter-design.md:475-479`) | optional string | MISSING |
| `CASanctionValue` | `program`, `authority`, `listed_at` | sanctions/company fixtures line 1 | optional strings/date | PASS (`output.py:74-77`) |
| `CAWatchlistValue` | `list_name`, `authority` | sanctions/watchlist fixture line 1 | optional strings | PASS (`output.py:80-82`) |
| `CASanctionValue` / `CAWatchlistValue` | richer source/list/jurisdiction/status/reason metadata | Locked s2 review scope; fixtures are synthetic and narrow (`README.md:3`, `README.md:6`) | optional strings/lists/dicts | MISSING / not repo-captured |
| `CAMediaArticleValue` | `title`, `url`, `publication_date`, `snippets` | adverse-media fixture line 1; canonicalizer path (`normalizer.py:503-509`) | optional strings + snippets | PASS (`output.py:98-102`) |
| `CAMediaArticleValue` | source/publisher/category/language metadata | Adverse-media scope (`README.md:9`) and prompt | optional strings/lists/dicts | MISSING / not repo-captured |
| `CAAddress` | current underscore address fields | Current model | optional strings | PASS (`input.py:29-35`) |
| `CAAddress` | `full_address`, `address_line1`, `address_line2`, `town_name`, `country_subdivision`, `country_code`, `location_type` | C3 live mapping (`C3-step1-adapter-design.md:291-303`); payload tests (`test_complyadvantage_payloads.py:19-41`) | optional strings | MISSING |
| Known webhook models | extras on known event types | Known s2 webhook models (`webhooks.py:27-50`) | preserved extras | MISSING preservation because known models default to ignore |

## 3. Gap analysis per model

### 3.1 `CARiskType`

Add `name: Optional[str] = None` and `taxonomy: Optional[str] = None` to current `key`/`label`. Do not rename `label`: tests construct it (`test_screening_complyadvantage_models.py:86-88`) and `_indicator_label()` reads it (`normalizer.py:408-410`). The normalizer should prefer `label` but fall back to `name`.

### 3.2 `CAPEPValue`

Keep `class_: str = Field(alias="class")`, `position`, and `country`; add `level`, `scope_of_influence`, `political_position_type`, `institution_type`, `political_positions: list[dict] = Field(default_factory=list)`, `political_parties: list[dict] = Field(default_factory=list)`, `active_start_date`, `active_end_date`, `issuing_jurisdictions: list[dict | str] = Field(default_factory=list)`, and `source_metadata: Optional[dict] = None`. Combine config as `ConfigDict(populate_by_name=True, extra="allow")`. Use dictionaries for nested political/source objects until fixtures lock exact nested keys.

### 3.3 Profiles

For `CAProfilePerson`, add `additional_fields: CAPaginatedCollection[CAAdditionalField] = Field(default_factory=CAPaginatedCollection[CAAdditionalField])` and `risk_indicators: list[CARiskIndicator] = Field(default_factory=list)`. For `CAProfileCompany`, add the same two fields unless CTO confirms the live fields are person-only. For `CAProfile`, add `entity_type: Optional[str] = None` as preserved evidence while keeping the current key-presence validator (`output.py:153-158`).

### 3.4 Sanction/watchlist/media values

Add conservative optional fields rather than invented exhaustive schemas: `source_metadata`, `issuing_jurisdictions`, `start_date`, `end_date`, `status`, and `reason` for sanction/watchlist values; `source_name`, `source_type`, `publisher`, `language`, `categories`, and `source_metadata` for media articles. Keep `CAMediaArticleSnippet.offset`; the model captures it (`output.py:93-95`) even though the normalizer currently drops it (`normalizer.py:512-513`).

### 3.5 `CAAddress` and webhooks

Add the live rich postal keys documented by C3 to `CAAddress`, all optional. Do not rename existing `address_line_1` / `address_line_2` because they are current model fields (`input.py:29-35`). Set `extra="allow"` on known webhook envelopes; no specific new known-event fields are repo-proven, but known events currently silently ignore future CA additions (`webhooks.py:27-50`).

## 4. Pydantic configuration policy

### 4.1 Recommendation: Option C, explicit expansion plus `extra="allow"`

Use a hybrid policy across CA wire models: explicitly model all known recon fields and set `ConfigDict(extra="allow")`. This is more audit-defensible than pure extras, avoids the current silent-drop failure, and avoids the production fragility of `extra="forbid"`. It is also consistent with existing forward-compatible taxonomy design (`enums.py:1-7`) and the unknown webhook envelope's `extra="allow"` precedent (`webhooks.py:53-62`).

### 4.2 Normalizer handling for extras

Expose preserved-but-unmodeled fields in `provider_specific.complyadvantage.raw_extras`, preferably per match:

```text
provider_specific.complyadvantage.matches[].raw_extras = {
  "profile": ...,
  "risk_detail": ...,
  "indicators": [...]
}
```

Implement this by extending the central `_dump()` / `_provider_match()` path (`normalizer.py:462-500`, `normalizer.py:566-570`) to recursively inspect `__pydantic_extra__`.

## 5. Normalizer flow-through plan

All new fields should pass through in `provider_specific.complyadvantage`; no database schema changes are needed. Existing serialization already dumps customer input, customer response, workflows, profiles, risk details, and indicator values (`normalizer.py:437-500`). `CAPEPValue` additions flow to `matches[].indicators[].value`; `CARiskType.name/taxonomy` flow to `matches[].risk_detail.values[].risk_type`; profile additions flow to `matches[].profile`; and rich `CAAddress` fields flow to `customer_input` (`normalizer.py:437-449`, `normalizer.py:462-500`, `normalizer.py:566-570`).

Do not change existing rollups in Step 2. `has_pep_hit`, `has_sanctions_hit`, `has_adverse_media_hit`, and `is_rca` are class/taxonomy/relationship driven (`normalizer.py:200-227`), and `pep_classes` uses `CAPEPValue.class_` (`normalizer.py:237-244`). Do not add new rollups such as `is_high_severity_pep`; keep new facts provider-specific. Keep `normalize_two_pass_screening(...)` unchanged (`normalizer.py:97-107`). Minimal normalizer changes: add raw-extra extraction, add `label`→`name` fallback in `_indicator_label()` (`normalizer.py:408-410`), and decide whether to preserve snippet offsets currently dropped by `_preserve_snippet_objects()` (`normalizer.py:512-513`). Defer hash expansion because current signatures are intentionally narrow (`normalizer.py:542-559`).

## 6. Test strategy

Add model tests for every new field family: full `CAPEPValue` s3 fields and alias behavior (`test_screening_complyadvantage_models.py:168-171`), `CARiskType.name/taxonomy` while preserving unknown-key compatibility (`test_screening_complyadvantage_models.py:86-88`), profile nested `additional_fields` / `risk_indicators` while preserving discriminator tests (`test_screening_complyadvantage_models.py:47-83`), rich `CAAddress` keys matching payload output (`payloads.py:42-52`; `test_complyadvantage_payloads.py:19-41`), and known-webhook extras analogous to the unknown webhook test (`test_screening_complyadvantage_models.py:91-99`). Add normalizer tests using the existing fixture loader (`test_screening_complyadvantage_normalizer.py:67-93`) to assert provider-specific flow-through.

Fixture enrichment plan: add full s3 PEP fields to `pep_canonical.json`; full s2 sanction/watchlist metadata and `name/taxonomy` to `sanctions_canonical.json`; nested person fields to `rca_canonical.json`; media source metadata and snippet offsets to `adverse_media_multi_source.json`; company root/nested fields to `company_canonical.json`; rich addresses to `monitoring_on_full_optional_fields.json`; extras across strict/relaxed/canonical matches to `two_pass_strict_misses_relaxed_catches.json`; and optional harmless workflow/customer extras to `clean_baseline.json`.

Backward-compatibility targets: C3 orchestrator tests assert behavior and paths rather than full model dumps (`test_complyadvantage_orchestrator.py:188-206`, `test_complyadvantage_orchestrator.py:224-267`, `test_complyadvantage_orchestrator.py:298-313`), so they should remain green under Option C. Shape-sensitive tests are snippet equality (`test_screening_complyadvantage_normalizer.py:173-178`) and hash tests if hash input changes (`test_screening_complyadvantage_normalizer.py:217-254`). Target 80% coverage for modified modules, matching the project standard cited by C2/C3 design docs (`C2-step1-oauth-client-design.md:468-472`; `C3-step1-adapter-design.md:452-454`).

## 7. Migration and backward compatibility

### 7.1 Call-site classification

- `adapter.py:36-51`, `adapter.py:53-64`, and `adapter.py:66-114` are compatible; they build customer dicts/context and delegate.
- `_combine_reports()` is compatible if new fields stay inside existing provider-specific JSON (`adapter.py:131-169`).
- `orchestrator.py:108-118` is compatible only if `normalize_two_pass_screening(...)` signature stays unchanged.
- `orchestrator.py:135` is behavior-sensitive under `extra="forbid"` because payloads add customer-level `reference` (`payloads.py:179-184`); compatible with Option C.
- `orchestrator.py:146` is behavior-sensitive under `extra="forbid"` because workflow raws/tests include fields outside `CAWorkflowResponse` (`test_complyadvantage_orchestrator.py:224-245`); compatible with Option C.
- `orchestrator.py:254` is shape-sensitive because `CAProfile.model_dump(mode="json")` will include new default fields.
- `orchestrator.py:295-311` is behavior-sensitive under `extra="forbid"` until all live risk/value fields are modeled; compatible with Option C.
- `payloads.py:33-53` is naming-sensitive with `CAAddress` because payloads emit `address_line1`/`town_name` while the model uses `address_line_1`/`city`.
- `subscriptions.py:9-43` is compatible; it does not use CA wire models.

Test classification: model discriminator tests (`test_screening_complyadvantage_models.py:47-83`) are compatible if additions are optional; unknown taxonomy test (`test_screening_complyadvantage_models.py:86-88`) should be expanded; unknown webhook extra test (`test_screening_complyadvantage_models.py:91-99`) should be mirrored for known webhooks; normalizer fixture parser (`test_screening_complyadvantage_normalizer.py:47-64`) must pass enriched values; snippet and hash tests are shape/behavior-sensitive; C2 auth/client/config/exception tests, subscription tests, adapter delegation tests, and URL canonicalization tests are compatible.

### 7.2 Shape assertions needing update

Potential updates are limited: `test_screening_complyadvantage_normalizer.py:177` if snippet offsets are emitted, `test_screening_complyadvantage_normalizer.py:217-254` if hash signatures are expanded, and no current exact full `CAPEPValue.model_dump()` assertion beyond the alias key at `test_screening_complyadvantage_models.py:171`.

### 7.3 `extra` policy breakage survey

`extra="forbid"` would break current or live paths at `orchestrator.py:135` (customer `reference`), `orchestrator.py:146` (workflow extras), `orchestrator.py:254` (profile extras), and `orchestrator.py:295-311` (risk/value extras). It would also make known webhook evolution brittle. This is why Option C is recommended.

### 7.4 Step 2 migration sequence

Apply model `extra="allow"` config; add explicit fields; export any new types from `models/__init__.py`; enrich fixtures; update model tests; add normalizer flow-through/raw-extra tests; keep adapter/orchestrator/payload behavior unchanged; then run CA-focused tests before the broader backend suite.

## 8. Open questions / explicit risks

### 8.1 Alternatives

Default to `dict` for nested PEP/source objects until fixtures lock keys; alternative typed sub-models risk inventing schema. Default to per-match `raw_extras`; alternative top-level extras are less traceable. Default to no hash change; alternative full-dump hashing may create downstream churn. Default to allow extras on known webhooks; alternative deferral to C4 leaves silent drops.

### 8.2 Unknown CA behaviours

Unknowns: exact nested keys/cardinality for political/source metadata; whether `additional_fields` is paginated or a bare list; whether nested `risk_indicators` appears on all subject kinds; full sanction/watchlist metadata keys across taxonomies; and whether `CARiskType.name` should replace `label` in displays.

### 8.3 Scope-creep risks

Webhook handler logic is C4; adapter/orchestrator behavior is locked post-PR-#189; production call-site migration is Track E; schema changes are forbidden. Step 2 must not touch Sumsub/legacy screening paths and must keep new evidence under `provider_specific.complyadvantage`.

### 8.4 Verbal recon not yet repo-captured

The prompt-only evidence not fully captured in repo fixtures/design docs is: full s3 PEP field set, live `CARiskType {key,name,taxonomy}`, nested `CAProfilePerson.additional_fields[]` / `risk_indicators[]`, and full s2 sanction/watchlist value shape. Step 2 should re-capture these in synthetic fixtures.

### 8.5 Downstream-impact risks beyond C1

If live risk types populate only `name`, `_indicator_label()` currently returns `None` (`normalizer.py:408-410`); a small fallback is C1 scope, not adapter scope. If root `entity_type` conflicts with key-presence discrimination, do not change adapter behavior; surface a follow-up. `extra="allow"` may grow provider-specific JSON size but needs no schema change. Hash expansion would affect downstream change detection, so defer by default.
