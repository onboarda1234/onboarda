# PR-DOC1 Root Cause - DOC-001

DOC-001 was caused by fragmented document reliance policy.

## Root Causes

1. Document upload and Agent 1 verification were decoupled. Upload created a pending row but did not guarantee verification execution or a downstream-blocking failure/skipped state.
2. KYC submission used a narrow document gate instead of a canonical reliance model. It did not require complete `verification_results`, `verified_at`, Agent 1 execution proof, current-version state, or governed manual acceptance.
3. Memo generation, memo validation, and memo approval did not share a hard document evidence gate, so a memo could rely on documents that were pending, failed, skipped, stale, superseded, or missing proof.
4. Final approval only checked limited flagged-document behavior and missed pending, failed, skipped, stale, missing-result, missing-`verified_at`, missing-Agent-execution, unsupported-type, and superseded cases.
5. Manual acceptance used existing review fields without a strict reliance policy requiring reason, actor, role, and timestamp.
6. Officer/client readiness surfaces did not consistently distinguish uploaded or status-only documents from reliance-ready evidence.

## Document Reliance State Model

Allowed for reliance:

- `verified`: current document, matching required slot type, clean verification results, `verified_at`, and Agent 1 execution proof.
- `manual_accepted`: admin/SCO accepted with reason, actor, timestamp, and audit-visible fields.

Blocked for reliance:

- `missing`
- `uploaded` / `pending`
- `running`
- `failed`
- `flagged` unless governed manual acceptance exists
- `skipped`
- `stale`
- missing `verification_results`
- missing `verified_at`
- missing Agent 1 execution proof
- unsupported document type for a required slot
- superseded/replaced/deleted document

## Design Summary

PR-DOC1 adds one shared server-side document reliance gate for canonical KYC/onboarding documents in the existing `documents` table. The gate evaluates required entity/person slots, document status, current-version state, verification results, timestamp freshness, Agent 1 proof, and manual acceptance governance. KYC submit, memo generation, memo validation, memo approval, final approval, application detail, evidence pack, portal readiness, and back-office readiness now consume this model.

Out of scope by design: change management evidence, EDD evidence model, periodic review evidence, monitoring/SAR/PEP-SOW evidence, universal evidence document architecture, presigned upload architecture, and full async verification queue migration.

