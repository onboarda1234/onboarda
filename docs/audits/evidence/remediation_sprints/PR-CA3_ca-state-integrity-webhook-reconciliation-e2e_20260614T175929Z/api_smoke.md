# PR-CA3 API Smoke

Status: failed on merged PR #491 staging validation; corrective branch pending.

Required staging API/runtime smoke must prove:

- Canonical screening truth has no impossible clear/unresolved combinations in tested paths.
- Queue/detail/gate agree on current CA state for safe test cases.
- Provider failure and stale states block approval reliance.
- Duplicate webhook/replay fixture does not duplicate hits/evidence.
- Reconciliation job or equivalent retry recovers missed webhook/detail-fetch work.
- Safe no-hit/hit/failure/stale/rescreen E2E paths pass.
- PR-CA1 provider source truth remains passing.
- PR-CA2 evidence/audit chain remains passing.
- No tokens, secrets, webhook signatures, or provider credentials appear in outputs.

## Merged PR #491 staging smoke

Timestamp: `2026-06-14T18:48:02Z` to `2026-06-14T18:53:36Z`

Role/token type: approved staging QA officer login (`sco`), bearer token redacted.

Merged SHA under test: `9b210f3884f9cd3bf0c28d82457e8f2b1dac69ca`

Results:

- `/api/version`: PASS. `git_sha` and `image_tag` both matched `9b210f3884f9cd3bf0c28d82457e8f2b1dac69ca`.
- `/api/screening/status`: PASS. Active AML provider was `ComplyAdvantage Mesh`; Sumsub remained `Sumsub IDV/KYC`; fallback mode was `disabled`; CA status was `live`.
- `/api/screening/queue?show_fixtures=true&limit=100`: INCONCLUSIVE for hit/stale/failure runtime paths because the queue returned zero rows.
- `/api/applications?show_fixtures=true&limit=100` plus sampled application details: FAIL. Existing CA-backed application detail records exposed `screening_truth_summary.canonical_state=completed_clear` and `approval_ready=true` while backend approval gate blockers included `Screening is stale` for material input changes after screening.
- Secret redaction check: PASS for captured API projections. No tokens, provider credentials, webhook signatures, or staging passwords were found in the saved redacted outputs.

Saved redacted evidence:

- `runtime_json/staging_pr_ca3_api_smoke_summary_redacted.json`
- `runtime_json/staging_pr_ca3_provider_status_redacted.json`
- `runtime_json/staging_pr_ca3_queue_projection_redacted.json`
- `runtime_json/staging_pr_ca3_application_sample_projection_redacted.json`
- `runtime_json/staging_pr_ca3_detail_truth_projection_redacted.json`

Corrective action:

- New corrective branch: `codex/pr-ca3-corrective-input-staleness`.
- Fix: canonical CA screening truth now treats material screening input updates after provider `screened_at` as stale and approval-blocking, matching the existing approval gate.
- Post-corrective staging API/runtime smoke remains pending until the corrective branch is merged and redeployed.
