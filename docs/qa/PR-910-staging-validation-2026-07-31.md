# PR #910 staging validation

Date: 2026-07-31  
Environment: AWS staging (`af-south-1`, ECS cluster `regmind-staging`)  
Change under validation: `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4`
Validation mode: read-only

## Verdict

**INCOMPLETE — deployment baseline and runtime logs validated; browser-only
functional checks UNVERIFIED.**

The previous Step 0 failure is superseded. Deploy-to-staging workflow run
[#1161](https://github.com/onboarda1234/onboarda/actions/runs/30612244525)
completed successfully at `2026-07-31T09:06:47Z`. The live backend and worker
now both run the exact PR #910 merge SHA. The required signed-in browser-control
capability was not available in the validation session, so visual, DevTools,
keyboard, PDF-export and cross-browser claims were not substituted with weaker
checks and are explicitly UNVERIFIED.

No pilot or production-readiness claim is made.

## Step 0 — Pin the baseline: PASS with endpoint limitation

Observed at `2026-07-31T10:05:25Z`:

| Service | Task definition | Image tag | Running / desired | Rollout | Failed tasks |
|---|---|---|---:|---|---:|
| Backend | `regmind-staging:991` | `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4` | 2 / 2 | `COMPLETED` | 0 |
| Verification worker | `regmind-verification-worker:439` | `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4` | 6 / 6 | `COMPLETED` | 0 |

Both task definitions reference:

`782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:85c70431a2d2a2f4bd6dd3078257d5f22d92bad4`

`git merge-base --is-ancestor 85c70431a2d2a2f4bd6dd3078257d5f22d92bad4
85c70431a2d2a2f4bd6dd3078257d5f22d92bad4` returned exit code 0. The deployed
commit is exactly the change under validation; no later commits are included.

### ALB and endpoint observations

- Backend target group `regmind-staging-tg/5e7928153c29b613`: targets
  `10.0.4.209:8080` and `10.0.3.31:8080`, both `healthy`.
- `GET /api/liveness`: HTTP 200, `status=ok`.
- `GET /api/health`: HTTP 200, `status=ok`, `environment=staging`.
- `GET /api/version`: HTTP 401 without an authenticated officer session.
- `GET /api/readiness`: HTTP 401 without an authenticated officer session.

The image-SHA, ECS, ALB, liveness and health portions pass. Authenticated
version-response content and readiness (`ready=true`) are UNVERIFIED because
the signed-in browser session was unavailable. The task-definition images are
the authoritative SHA evidence used for the baseline gate.

## Functional validation

| Step | Result | Recorded evidence / reason |
|---|---|---|
| 1 — Risk Assessment panel and population count | UNVERIFIED | No signed-in controllable browser was available. No application was opened, no application ref or stored tier was recorded, no panel/fail-closed population count was made, and no screenshot was captured. |
| 2 — Zero contribution renders as `0` | UNVERIFIED | No staging application could be inspected through the required UI; no simulation was used. |
| 3 — Bar arithmetic and inline widths | UNVERIFIED | DevTools inline styles were inaccessible. No visual or source-code substitute was treated as a pass. |
| 4 — LOW versus HIGH/VERY_HIGH comparability | UNVERIFIED | No eligible pair could be opened and screenshotted. This highest-value regression check remains outstanding. |
| 5 — Screen versus authoritative PDF | UNVERIFIED | Neither authenticated screen data nor a read-only export could be obtained. |
| 6 — Long-driver clamp and keyboard behavior | UNVERIFIED | Visual layout, toggle presence and keyboard operation require browser control. |
| 7 — Firefox and Safari/WebKit | UNVERIFIED | Cross-browser control was unavailable. |
| 8 — Surrounding Application Review workflow | UNVERIFIED | The frozen workflow was not opened or actioned. |
| 9 — Runtime health | PASS with non-attributable background findings | See the CloudWatch section below. |

## Step 9 — CloudWatch runtime health

Window scanned: `2026-07-31T08:53:53Z` (creation of backend deployment
`regmind-staging:991`) through the validation pass.

- `ERROR`: 0 occurrences.
- `CRITICAL`: 0 occurrences.
- `Exception`: 0 occurrences.
- `Traceback`: 0 occurrences.
- Literal HTTP-500 indicators checked: 0 occurrences.
- No errors or unexpected 5xx were attributable to PR #910 or to this
  read-only validation.

The window was not devoid of background activity:

- Scheduled ComplyAdvantage historical-backfill operations made successful
  provider requests at approximately `2026-07-31T09:30Z`; sampled events show
  `/v2/token` and `/v2/{case_id}` returning 2xx.
- Periodic-review notification ticks processed canonical fixtures, but logs
  report `sent=0`, `failed=0`, `officer_alerts=0`; fixture notifications were
  explicitly suppressed.
- No outbound call, email, notification or webhook was initiated by this
  validation, and none was attributable to the Risk Assessment presentation
  change. The existing scheduled activity is recorded rather than hidden.

## Screenshots and recorded application numbers

None. Because browser control was unavailable, no application was selected and
no screenshot, risk tier, score, weight, contribution, inline width, PDF value,
or population count was collected. These omissions are validation gaps, not
passes.

## Coverage and constraints

This was a read-only staging check. No application was actioned, no data or
configuration was changed, no deployment was initiated, and no production,
RSMP, Tier 0C-A, Tier 0C-B, recomputation, reseed, risk-config change or direct
SQL action was performed. The frozen Application Review and Screening Queue
workflows were not modified or actioned.

A complete verdict requires rerunning Steps 1–8 and authenticated version and
readiness checks in a controllable, signed-in officer browser, including the
requested screenshots, DevTools widths, PDF comparison and Firefox plus
Safari/WebKit coverage.
