# Onboarda / RegMind Audit Fix Prompt Bank

Use this file as the stable execution prompt bank for the audit remediation work. You can ask: "proceed with prompt 1", "proceed with prompt 2", etc.

Recommended order:

1. Prompt 1 - Approval Gate Timestamp Fixes
2. Prompt 2 - Demo Auth And Public Credential Hardening
3. Prompt 3 - Reset Controls And Dangerous Admin Endpoints
4. Prompt 4 - Document Download Safety
5. Prompt 5 - Deployment, CORS, Ports, Branding
6. Prompt 6 - AI Determinism And Model Routing
7. Prompt 8 - CI, Coverage, PDF Strategy
8. Prompt 7 - Memo Evidence And Approval Auditability
9. Prompt 9 - Marketing-Vs-Code Scope Corrections
10. Prompt 10 - Data Governance, SQL Safety, Schema Drift

## Prompt 1: Approval Gate Timestamp Fixes

```text
You are fixing the Onboarda/RegMind approval-gate test failures. Work only in the backend unless tests prove otherwise.

Subagent requirement:
Before editing, delegate a read-only exploration task to the Explore subagent with medium thoroughness. Ask it to map:
- every timestamp parser/comparison used by ApprovalGateValidator and related approval checks;
- the exact failing expectations in test_memo_staleness_approval.py, test_phase4_verification_hardening.py, and test_screening_freshness.py;
- any existing helper that should be reused for UTC/SQLite timestamp normalization.
Use the subagent result to guide the implementation, but verify critical claims directly before editing.

Goal:
Make the CI-equivalent test suite pass for the approval/staleness/screening freshness cluster without weakening compliance controls.

Verified baseline:
- Run from arie-backend with PYTHONUTF8=1 on Windows.
- Current CI-equivalent run excluding PDF tests has 20 failures, clustered in:
  - tests/test_memo_staleness_approval.py
  - tests/test_phase4_verification_hardening.py
  - tests/test_screening_freshness.py
- Root cause suspected: naive SQLite timestamps mixed with timezone-aware datetime.now(timezone.utc), plus validation ordering where future/stale checks can mask the intended error.

Requirements:
1. Normalize timestamp parsing/comparison in ApprovalGateValidator and related approval checks.
2. Preserve strict behavior: expired screening, stale memo, future-dated screening, missing memo, missing screening, same-officer high-risk approval must still block.
3. Do not bypass or loosen the approval gate to make tests pass.
4. Add or adjust focused tests only where behavior is currently ambiguous.
5. Run focused failing test files first, then full CI-equivalent pytest excluding PDF tests.
6. Report exact test counts and any residual failures.

Do not modify frontend, Render config, docs, or unrelated modules.
```

## Prompt 2: Demo Auth And Public Credential Hardening

```text
You are hardening demo authentication in Onboarda/RegMind.

Subagent requirement:
Do not use a subagent by default. This prompt is narrow enough for direct implementation. If the auth surface appears broader than expected, delegate a quick read-only Explore subagent to map frontend login/token paths and backend demo login/config endpoints before editing.

Verified issues:
- arie-backoffice.html accepts password === "admin123" in demo offline login.
- arie-portal.html and arie-backoffice.html can mint demo_ tokens client-side.
- /api/config/environment exposes demo_credentials from environment.py in demo mode.
- EnvironmentInfoHandler is unauthenticated.

Goal:
Remove public/demo credential leakage and client-side auth bypasses while preserving a usable demo login through backend authentication.

Requirements:
1. Remove hardcoded admin123 acceptance.
2. Remove or disable client-side demo token minting paths.
3. Stop returning demo_credentials from get_environment_info().
4. Ensure frontend still handles demo mode UI without needing passwords from public config.
5. Use backend login endpoints for authentication.
6. Add regression tests or static tests proving demo credentials are not exposed.
7. Search for any remaining demo password exposure or demo_ token minting.
8. Do not change unrelated branding or deployment files in this PR.

Verification:
- grep for admin123, demo_credentials, demo_ token minting.
- Run relevant auth/config tests.
- Run CI-equivalent backend tests if feasible.
```

## Prompt 3: Reset Controls And Dangerous Admin Endpoints

```text
You are fixing hardcoded destructive/reset controls.

Subagent requirement:
Do not use a subagent by default. This prompt is narrow. Before editing, directly inspect the two affected handlers, their tests, and environment/config helper patterns.

Verified issues:
- server.py has hardcoded confirmation WIPE_STAGING_2026.
- server.py has hardcoded confirmation RESET_STAGING_ADMIN.

Goal:
Remove hardcoded reset/wipe secrets and make non-production destructive admin actions controlled by environment config, authorization, audit logging, and safe failure.

Requirements:
1. Replace hardcoded confirmation strings with environment variables.
2. Fail closed if the relevant env var is missing.
3. Keep production blocked.
4. Require admin authorization exactly as current endpoint intends or stricter.
5. Preserve audit logging.
6. Update tests that currently assert the hardcoded strings.
7. Add tests for missing env var, wrong confirmation, correct confirmation in allowed environment, and production block.
8. Do not broaden endpoint access.

Verification:
- grep confirms no WIPE_STAGING_2026 or RESET_STAGING_ADMIN remains outside tests/docs.
- Run affected endpoint tests and auth tests.
```

## Prompt 4: Document Download Safety

```text
You are fixing document download security and reliability.

Subagent requirement:
Before editing, delegate a read-only exploration task to the Explore subagent with medium thoroughness. Ask it to map:
- all document file_path reads/writes in server.py and related helpers;
- every document download/delete/verify path that opens local files;
- existing containment or basename patterns that can be reused;
- S3 ownership/presign call sites and DB connection lifetime.
Use the subagent result to avoid fixing only one path while leaving a sibling path exposed.

Verified issues:
- DocumentDownloadHandler closes db before calling s3.get_presigned_url_with_ownership(... db_connection=db ...).
- Local fallback uses document file_path and can open absolute paths without a clear UPLOAD_DIR containment check.
- Some other handlers use basename/containment patterns; follow the safest existing pattern.

Goal:
Document downloads must work from S3 after redeploy and must never read files outside the configured upload directory.

Requirements:
1. Keep DB connection open through S3 ownership verification, then close in finally.
2. Add a shared or local helper to resolve document paths safely under UPLOAD_DIR.
3. Reject absolute or traversal paths outside UPLOAD_DIR.
4. Preserve ownership checks for client and officer roles.
5. Add tests for:
   - S3 presign path uses open DB connection.
   - local relative file resolves under UPLOAD_DIR.
   - absolute outside path is rejected.
   - traversal path is rejected.
6. Do not alter upload validation except where required for path consistency.

Verification:
- Run document upload/download tests.
- Run security/path traversal focused tests.
```

## Prompt 5: Deployment, CORS, Ports, Branding

```text
You are fixing deployment and demo configuration drift.

Subagent requirement:
Do not use a subagent by default. This is mostly config/docs work. Use direct searches for render.yaml, Dockerfile, README, CLAUDE.md, server startup banners, start.sh, ARIE/ariefinance strings, CORS origins, and port values before editing.

Verified issues:
- Two render.yaml files exist with different service definitions.
- Root demo render.yaml sets ALLOWED_ORIGIN="*" and CORS_ORIGIN="*".
- Dockerfile defaults/exposes/checks 8080 while Render uses PORT=10000.
- README says local 8080 and Render 10000; CLAUDE.md says local 10000.
- server.py boot banner still says ARIE Finance API Server.
- start.sh prints asudally@ariefinance.mu.

Goal:
Make deployment config unambiguous and demo-safe.

Requirements:
1. Decide one canonical Render blueprint. Prefer root render.yaml unless repo evidence indicates otherwise.
2. Remove or clearly mark the duplicate backend render.yaml so Render cannot accidentally use stale config.
3. Replace wildcard demo CORS with explicit demo domains/origins.
4. Align Dockerfile, healthcheck, README, CLAUDE.md, and server startup docs on ports.
5. Replace user-visible ARIE Finance strings with Onboarda/RegMind branding where appropriate.
6. Do not change app behavior beyond config/docs/branding.

Verification:
- grep for ariefinance.mu, ARIE Finance, CORS "*", duplicate render service names, 8080/10000 inconsistencies.
- Validate YAML syntax.
```

## Prompt 6: AI Determinism And Model Routing

```text
You are fixing AI governance claims around determinism and model routing.

Subagent requirement:
Use a read-only Explore subagent with medium thoroughness if the Claude/model-routing call graph is not obvious after initial inspection. Ask it to map every messages.create call, every model selection helper, all hardcoded model names, and tests/docs that assert risk-based routing. If there are only two direct API call sites, direct implementation is acceptable after verifying call sites yourself.

Verified issues:
- claude_client.py calls messages.create without explicit temperature.
- Product/docs claim deterministic 4-layer AI pipeline.
- Risk-based Sonnet/Opus routing appears implemented for memo generation but other AI agent calls are hardcoded to Sonnet.

Goal:
Make AI generation behavior explicit and make model routing claims true or scoped correctly.

Requirements:
1. Add explicit generation parameters to every Claude messages.create call, at minimum temperature=0 where deterministic compliance output is claimed.
2. Avoid changing prompts unless required.
3. Centralize model selection if a local pattern exists; otherwise keep edits minimal.
4. Ensure HIGH/VERY_HIGH routing to Opus applies wherever docs claim it applies, or update docs to state routing is memo-only.
5. Add tests or static assertions for temperature being pinned and routing behavior.
6. Do not invent new AI features.

Verification:
- grep confirms all messages.create calls include explicit temperature.
- Tests cover model selection for LOW/MEDIUM/HIGH/VERY_HIGH.
```

## Prompt 7: Memo Evidence And Approval Auditability

```text
You are improving compliance memo auditability and high-risk approval evidence.

Subagent requirement:
Before editing, delegate a read-only exploration task to the Explore subagent with thorough coverage. Ask it to map:
- compliance memo generation inputs and output schema;
- validation engine outputs and any approval-blocking integration;
- application approval fields, audit_log before/after snapshots, decision_records, and screening review tables;
- API responses consumed by the back office for approval/memo detail.
Use the subagent result to choose the smallest schema/API change that improves auditability without duplicating existing decision/audit records.

Verified context:
- Current high-risk dual approval exists using first_approver_id and first_approved_at.
- Final approver is represented via decision_by, but applications lacks explicit second_approver_id.
- Generated memo content lacks strong source attribution/evidence references.

Goal:
Make approval and memo artifacts auditable for a compliance buyer without changing risk policy.

Requirements:
1. Add explicit second-approver auditability for high-risk final approval, either as structured columns or a normalized decision/audit record if that fits existing schema better.
2. Preserve same-officer blocking.
3. Ensure first and second approvals are visible in API responses or audit logs used by back office.
4. Add source/evidence attribution to generated compliance memos using existing application, screening, document, and rule-engine data.
5. Ensure validation failures can block approval where current policy requires.
6. Add tests for two-officer approval evidence and memo source references.
7. Avoid broad schema churn.

Verification:
- Run dual approval tests.
- Run memo generation/validation tests.
- Confirm old high-risk approval flow still works with two different users.
```

## Prompt 8: CI, Coverage, PDF Strategy

```text
You are fixing CI quality gates.

Subagent requirement:
Do not use a subagent by default. This is configuration-heavy. Directly inspect CI workflow, coverage config, pytest config, PDF tests, requirements, and Docker/native dependency setup. Use a subagent only if PDF dependency handling becomes unclear.

Verified issues:
- CI ignores tests/test_pdf_generator.py.
- CI minimum test-count check is 150 despite around 4,000 tests existing.
- Coverage threshold is 25%.
- .coveragerc omits security_hardening.py, production_controls.py, resilience/* and other critical modules.
- PDF tests fail locally on Windows because WeasyPrint cannot load libgobject-2.0-0.

Goal:
Make CI representative without making local Windows development impossible.

Requirements:
1. Raise test-count threshold to a realistic floor based on current collected tests, with some buffer.
2. Remove unjustified coverage omissions for security-critical modules.
3. Decide PDF strategy:
   - install native dependencies in CI and run PDF tests, or
   - mark PDF tests with an explicit dependency skip and add a separate CI job/container that runs them.
4. Keep Windows-local limitations documented.
5. Do not lower coverage expectations to make CI pass.
6. Update docs with exact test commands and prerequisites.

Verification:
- Run test collection.
- Run CI-equivalent tests excluding PDF only if PDF job is separately represented.
- Validate coverage command still works.
```

## Prompt 9: Marketing-Vs-Code Scope Corrections

```text
You are reconciling product claims with implemented code.

Subagent requirement:
Before editing, delegate a read-only exploration task to the Explore subagent with thorough coverage. Ask it to determine, with file references:
- whether ComplyAdvantage is actually production-wireable or only scaffolded/non-authoritative;
- whether adverse media performs a real external call or only placeholder/scaffolded logic;
- whether periodic review has any automatic scheduler/cron/APScheduler/IOLoop task or only manual endpoints;
- which frontend/docs/marketing surfaces claim these as live functionality.
Use the subagent result to decide whether to wire a small existing implementation or de-scope claims. Do not invent broad vendor integrations.

Verified concerns:
- ComplyAdvantage appears scaffolded/non-authoritative; provider defaults to Sumsub.
- Adverse media appears placeholder/scaffolded rather than actually called.
- Periodic review has engine/schema/endpoints, but no automatic scheduler/cron/APScheduler was found.

Goal:
Make public/demo claims match executable behavior.

Requirements:
1. For each area, determine if implementation exists:
   - ComplyAdvantage production provider wiring.
   - adverse media external call path.
   - automatic periodic review scheduler.
2. If implementation is small and safe, wire it behind existing feature flags.
3. If implementation is not demo-ready, de-scope UI/docs/marketing claims so the demo does not imply live functionality.
4. Add tests proving the feature is either wired or explicitly disabled/hidden.
5. Do not build broad new vendor integrations unless already scaffolded enough.

Verification:
- grep and tests prove no misleading claim remains.
- Feature flags behave consistently in production/demo.
```

## Prompt 10: Data Governance, SQL Safety, Schema Drift

```text
You are fixing structural compliance and data-governance risks.

Subagent requirement:
Before editing, delegate a read-only exploration task to the Explore subagent with thorough coverage. Ask it to enumerate:
- dynamic SQL identifier interpolation in db.py, gdpr.py, change_management.py, and nearby modules;
- all purge_expired_data definitions/call sites and any scheduler/startup hooks;
- manual override fields, handlers, API payloads, audit logs, and tests;
- UBO/name-match threshold usage and tests;
- migration/schema drift hotspots, including inline migrations, migration_007, schema_version handling, and duplicate screening_reports_normalized definitions.
Use the subagent output to break the implementation into safe, focused commits or request splitting if the scope is too large.

Verified concerns:
- purge_expired_data exists but is not scheduled.
- Manual override fields exist but lack MLRO co-sign/expiry/structured governance.
- SQL identifier f-string interpolation patterns exist across db.py, gdpr.py, change_management.py.
- UBO document name-match threshold is 0.70.
- Schema drift hotspots: inline migration v2.29 not in schema_version, migration_007 SELECT 1, screening_reports_normalized defined multiple times.

Goal:
Reduce governance and schema risk without refactoring the whole backend.

Requirements:
1. Add a safe scheduled or startup-triggered GDPR purge mechanism appropriate for Tornado/Render.
2. Add override governance: structured rationale, role requirement, optional expiry/review, and audit trail.
3. Review SQL identifier interpolation. Replace unsafe dynamic SQL with whitelisted identifiers or parameterized values where possible.
4. Reassess UBO name-match threshold and align with compliance expectations; add tests for false-positive prevention.
5. Consolidate or document schema migrations so current schema is reproducible.
6. Add focused regression tests.
7. Avoid broad unrelated database rewrites.

Verification:
- Run migration/init tests.
- Run GDPR tests.
- Run override/approval tests.
- Static search for unsafe SQL identifier interpolation after fixes.
```

## Subagent Guidance

Use read-only subagents before coding for the riskier prompts, then keep implementation owned by one main agent per PR.

- Prompt 1: use an Explore subagent to map timestamp parser/comparison sites and failing test expectations.
- Prompt 4: use an Explore subagent to map document path reads/writes and ownership checks.
- Prompt 7: use an Explore subagent to map memo data sources, validation outputs, audit logs, and approval records.
- Prompt 9: use an Explore subagent to determine whether ComplyAdvantage/adverse media/periodic review are wireable or should be de-scoped.
- Prompt 10: use an Explore subagent to enumerate dynamic SQL identifier usage and schema drift before edits.

## Execution Notes

- Do not commit unless explicitly asked.
- For Python work, configure the Python environment before running Python commands.
- On Windows test runs, set `PYTHONUTF8=1` to avoid encoding failures in tests that read UTF-8 assets.
- Keep each prompt/PR focused. Do not mix unrelated prompt scopes unless explicitly requested.