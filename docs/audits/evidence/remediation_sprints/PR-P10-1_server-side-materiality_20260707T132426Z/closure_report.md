# PR-P10-1 Server-Side Change-Request Materiality Closure

Finding: FINDING-RDI-006 / P10-1  
Status: CLOSED / PASS  
PR: https://github.com/onboarda1234/onboarda/pull/697  
Final head SHA: `e28d71562e5b3076790b37fee8f63e285c4f4910`  
Merge SHA: `b6192fbc775ce6616f8a90f56956016d3c89f68c`

## Scope

Before: client portal and back-office change-request payloads could supply `items[].materiality`; downstream controls could be influenced by that client-supplied value.

After: Client-supplied materiality is ignored for all control decisions. Materiality is server-computed from `change_type`.

Maker-checker scope was not changed. `MAKER_CHECKER_TIERS` remains `{'tier1', 'tier2'}`. No approval control was relaxed.

Files changed by #697:
- `arie-backend/change_management.py`
- `arie-backend/tests/test_change_management.py`
- `arie-backend/tests/test_cm_approval_preconditions.py`
- `arie-backend/tests/test_cm_defects_closure.py`
- `arie-backend/tests/test_cm_lock_and_auto_draft_api.py`

## Source Validation

- `create_change_request()` no longer reads `item.get("materiality", ...)` from inbound items for control decisions.
- Each item tier is computed via `classify_materiality(change_type)`.
- Request tier uses `_highest_materiality(...)` over computed item tiers.
- `change_request_items.materiality` and `change_requests.materiality` persist computed values.
- Downstream flags use `get_downstream_actions(overall_materiality)`.
- Unknown/unmapped classifier behavior remains Tier 2.
- `control_change` remains a server-known Tier 1 alert-conversion type.
- `DOWNSTREAM_ACTION_MAP` and maker-checker/four-eyes logic were not changed.

## Tests And CI

- Local targeted change-management / portal / API-boundary suite: `217 passed`.
- Local full backend SQLite suite: `6549 passed, 41 skipped, 4 xfailed`.
- Local PostgreSQL migration/path subset: `15 passed, 5 skipped`; local live PG DSN was not configured.
- PR CI on head `e28d71562e5b3076790b37fee8f63e285c4f4910`: `lint-and-test`, `docker-validate`, `pdf-tests` passed.
- Merge deploy CI run `28864427075`: `lint-and-test`, `pdf-tests`, `docker-validate`, and `deploy` all passed.

## Staging Deploy

Deploy run: https://github.com/onboarda1234/onboarda/actions/runs/28864427075

- Backend task definition: `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:780`
- Worker task definition: `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-verification-worker:228`
- Image tag: `b6192fbc775ce6616f8a90f56956016d3c89f68c`
- `/api/version.git_sha`: `b6192fbc775ce6616f8a90f56956016d3c89f68c`
- `/api/version.image_tag`: `b6192fbc775ce6616f8a90f56956016d3c89f68c`
- `/api/liveness`: 200 / `status=ok`
- `/api/health`: 200 / `status=ok`
- authenticated `/api/readiness`: 200 / `ready=true`
- Backend ECS image/env SHA: merge SHA
- Worker ECS image/env SHA: merge SHA
- ALB target health: healthy
- CloudWatch validation window `2026-07-07T13:01:55Z` to `2026-07-07T13:31:01Z`: `ERROR=0`, `Exception=0`, `Traceback=0`, HTTP 5xx indicators `0`

Raw evidence:
- `runtime_json/summary.json`
- `runtime_json/version.json`
- `runtime_json/runtime_baseline.json`
- `runtime_json/health.json`
- `runtime_json/liveness.json`
- `runtime_json/readiness.json`
- `runtime_json/alb_target_health.json`
- `runtime_json/cloudwatch_validation_window.json`

## Runtime Validation

Synthetic data was clearly marked `SMOKE FIXTURE`; no real/pilot data was mutated.

- Portal downgrade attempt: `ubo_change` + client `materiality=tier3` persisted item tier `tier1`, request tier `tier1`, and set `screening_required=true`, `risk_review_required=true`, `memo_addendum_hook=true`, `periodic_review_acceleration_hook=true`.
- Portal upgrade attempt: `contact_detail_update` + client `materiality=tier1` persisted item/request tier `tier3` and did not trigger Tier 1/Tier 2 downstream controls.
- Mixed portal items: server-computed tiers `tier3` + `tier1`; request tier `tier1`.
- Back-office downgrade attempt: `ubo_change` + client hint `tier3` persisted item/request tier `tier1`.

Runtime report: `runtime_json/p10_1_runtime_validation.json`.

Browser smoke:
- Back-office login, Applications page, application detail, and core workspaces passed with no console errors, page errors, failed requests, or unexpected bad responses.
- Client portal login, My Applications, and Change Requests rendered for a synthetic client. Unsuppressed browser run observed one non-application default favicon 404 console entry; API/page/request checks were clean. Icon-suppressed app-surface rerun passed with no console errors.

Browser reports:
- `browser_smoke/backoffice_explicit_app/report.json`
- `browser_smoke/client_portal/report.json`
- `browser_smoke/client_portal_icon_suppressed/report.json`

## Cleanup Status

Synthetic runtime/API data was left in place, clearly marked as `SMOKE FIXTURE`, to preserve audit traceability. No real or pilot data was deleted.

## Residual Limitation

`change_type` itself is still client-supplied. A client that mislabels the nature of a change can still influence the computed tier. Unknown/unmapped types default to Tier 2. Semantic validation of `change_type` is out of scope for P10-1.

## Verdict

PASS. Client-supplied materiality cannot downgrade Tier 1 or upgrade Tier 3 control decisions after #697 on staging. This closure does not claim production readiness and does not change maker-checker/four-eyes scope.
