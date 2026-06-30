# PR-PROV1 Provider Status After Switch

## Status

BLOCKED / NEEDS EVIDENCE.

No PR-PROV1 credential switch was performed and no runtime screening request was
sent.

Operator approval, approved subjects, case cap, billing cap, and webhook
subscription confirmation were provided. The remaining blocker is
dashboard/account-mode confirmation because a previous CA Mesh dashboard
screenshot reportedly showed `Sandbox`.

## Confirmed After Approval

- Staging `/api/version`: PASS, `git_sha` and `image_tag` both
  `6e44c13d79066fa4751cf2050e61bc009d7f9356`.
- Staging ECS backend/worker runtime: PASS, image/env provenance aligned to
  `6e44c13d79066fa4751cf2050e61bc009d7f9356`.
- `/api/screening/status`: PASS, ComplyAdvantage Mesh active as AML provider,
  fallback disabled, Sumsub IDV/KYC only.
- CA API/auth hosts: `api.mesh.complyadvantage.com`.
- API credential mode inference: `production_domain`.
- CA runtime config names are present in ECS task definition.

Evidence:

- `runtime_json/post_approval_preflight_redacted.json`
- `runtime_json/post_approval_ecs_runtime_redacted.json`

## Not Confirmed

- CA Mesh dashboard/account visual mode: NOT TESTABLE in this run without a
  secure dashboard session or operator-provided screenshot.
- No before/after dashboard screenshot was captured.
- No production/sandbox toggle or workspace switch was performed.

## Required Before Runtime Screening

After an approved switch or confirmation to keep the current production-domain credential mode:

1. Confirm dashboard/account mode is `Production`, or document why the prior
   `Sandbox` screenshot does not apply to the active API credentials.
2. Capture redacted dashboard evidence if dashboard mode is used as proof.
3. Restart/deploy staging if a config switch is performed.
4. Confirm `/api/version` still matches the deployed main SHA.
5. Confirm `/api/screening/status` shows:
   - AML provider: ComplyAdvantage Mesh
   - fallback/simulation disabled
   - Sumsub remains IDV/KYC only
   - OpenCorporates/registry remains separate
6. Save redacted JSON evidence to `runtime_json/screening_status_after.json`.

## Current Result

Status is `BLOCKED / NEEDS EVIDENCE`.
