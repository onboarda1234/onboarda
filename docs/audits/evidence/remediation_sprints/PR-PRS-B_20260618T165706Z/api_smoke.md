# PR-PRS-B API Smoke

## Staging API Smoke

- Target: deployed staging backend task via ECS Exec, HTTP base `http://127.0.0.1:8080`
- Public version endpoint: `https://staging.regmind.co/api/version`
- Merge SHA under test: `69effaafce6e14dd493497e692c290f69018dcb5`
- Image tag under test: `69effaafce6e14dd493497e692c290f69018dcb5`
- Synthetic data prefix: `PRPRSB-STAGING-20260618181835`
- Synthetic rows are fixture-marked and isolated to PR-PRS-B smoke cases.
- Result JSON: `logs/api_smoke_staging_results.json`
- Raw ECS Exec log: `logs/api_smoke_staging_ecs_raw.log`

## Scenario Results

- Agent 1 now runs: pass. `updated_register_of_directors` uploaded through the deployed enhanced-requirement handler, Agent 1 triggered with `DOC-ENTITY-REGDIR-v1`, persisted status `flagged`, `verified_at` set, `checks_count=7`.
- Accepted is not verified: pass. Plain CO accepted a skipped/unverified requirement, but periodic-review completion returned `409` with blocking item `Updated Register of Directors still requires officer review`.
- Verified satisfies: pass. A seeded verified current document linked to the periodic-review requirement allowed canonical completion (`200`, review status `completed`).
- Senior manual exception: pass. Plain CO manual acceptance returned `403`; SCO acceptance with a comment returned `200`; completion succeeded.
- Stale re-block: pass. A previously verified/accepted document marked non-current caused completion to return `409` with the periodic-review document blocker.
- Onboarding/EDD regression: pass. `licence_or_registration_certificate` upload triggered Agent 1 with `DOC-ENTITY-LICENCE-v1`, persisted status `verified`, `verified_at` set, `checks_count=1`.

## Local Pre-Merge Smoke

- Local API smoke against `http://127.0.0.1:10000`: pass.
- Local JSON: `logs/api_smoke_results.json`
- Local log: `logs/api_smoke_local.log`

