# RegMind Admin Pilot Controls Hardening - Post-Merge Validation

Date: 2026-06-11  
Environment: `https://staging.regmind.co/backoffice`  
Validation scope: PR #451, merged `main`, deployed staging, focused admin controls probes  
Final verdict: **FAIL**

## Executive Result

PR #451 is **not merged**. GitHub reports it as `OPEN` and `isDraft=true`, with `mergeCommit=null` and `mergedAt=null`.

Staging is running hardening code SHA `6bbf86e3fb0bc0292fa84c45b9c541075dd71712`, but that SHA is **not contained in `origin/main`**. `origin/main` is currently `224a9eb38a52c50cc8daa7de8d86df5cdcb75532`.

The deployed staging API behavior for the hardening controls passes the focused probes, and clean browser smoke passes. However, this validation cannot close the post-merge requirement because staging is not proven to be running merged `main`.

## Deployment Evidence

| Item | Value |
|---|---|
| PR | `#451` |
| PR URL | `https://github.com/onboarda1234/onboarda/pull/451` |
| PR state | `OPEN` |
| PR draft | `true` |
| Merge commit SHA | `null` |
| Merged at | `null` |
| PR head SHA | `26aa22d8a5c64d59d351dd53a9a3dcefd77464ff` |
| `origin/main` SHA | `224a9eb38a52c50cc8daa7de8d86df5cdcb75532` |
| Deployed `/api/version` SHA | `6bbf86e3fb0bc0292fa84c45b9c541075dd71712` |
| Staging SHA contained in `origin/main` | `no` |
| ECS task definition | `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:534` |
| ECS rollout state | `COMPLETED`, desired `2`, running `2` |
| ECS deployment updated at | `2026-06-11T11:20:10.184000+04:00` |

Post-merge closure status: **blocked/failing**. The branch is deployed, not merged-main provenance.

## Focused API Validation

Artifact: `/Users/Aisha/Onboarda-pr410/tmp/admin_pilot_controls_post_merge_api_probe_20260611.json`

| Probe | Result | Evidence |
|---|---:|---|
| Officer login | PASS | HTTP `200` |
| `/api/version` readable after auth | PASS | SHA `6bbf86e3fb0bc0292fa84c45b9c541075dd71712` |
| Invalid one-dimension risk payload returns 400 | PASS | HTTP `400`, code `risk_config_invalid` |
| Invalid risk payload error codes | PASS | Includes `risk_dimension_missing`, `risk_dimension_unknown`, `risk_subcriteria_required`, `risk_dimension_weight_total_invalid`, `risk_thresholds_required` |
| Invalid risk payload does not mutate persisted config | PASS | Before/after config hash both `43d018abe6cf80de` |
| Invalid risk payload does not trigger recompute | PASS | `Risk Recomputed` audit count remained `617`; response did not include `risk_recomputed_apps` |
| Valid partial country score-map update preserves dimensions/thresholds/unrelated maps | PASS | Dimensions, thresholds, sector scores, entity scores preserved; synthetic country present |
| Partial country score-map update restored original map | PASS | Restore HTTP `200` |
| AI agent synthetic update/revert audited with before/after | PASS | Update `200`, revert `200` |
| AI verification check insert audit | EXPECTED PARTIAL | Insert has `after_state`; first insert has `before_state=null` because no prior row existed |
| AI verification check second update audited with before/after | PASS | First PUT `200`, second PUT `200`, latest update has both states |
| System settings synthetic save audited with before/after | PASS | HTTP `200`, audit row has before/after |
| Synthetic user create/update/deactivate audited with before/after | PASS | Create `201`, update `200`, audit row has before/after |
| Audit CSV formula escaping | PASS | Formula-like target exported with leading quote; 2 matching CSV rows |

Risk-model invalid-payload closure evidence: **PASS on deployed staging branch SHA**. The mandatory post-merge provenance evidence: **FAIL**.

## Browser Smoke Validation

Clean browser artifact: `/Users/Aisha/Onboarda-pr410/tmp/admin_pilot_controls_post_merge_browser_20260611_clean/browser_smoke_summary.json`

Browser tool: Playwright Chromium headless. The in-app Browser control tool was unavailable in this session, so Playwright was used directly.

Viewports:
- Desktop: `1440x1000`
- Narrow: `390x844`

Pages checked:
- Audit Trail
- Audit Chain
- User Management
- Roles & Permissions
- Risk Scoring Model
- AI Verification Checks
- AI Agents
- Enhanced Requirements
- Resources
- Settings

Browser result:

| Check | Result |
|---|---:|
| Authenticated shell established | PASS |
| All requested pages loaded | PASS |
| Agent Health hidden in all viewports | PASS |
| Console errors | PASS: `0` |
| Failed network requests | PASS: `0` |
| Authenticated HTTP errors | PASS: `0` |

Screenshot directory: `/Users/Aisha/Onboarda-pr410/tmp/admin_pilot_controls_post_merge_browser_20260611_clean/`

## Required Final Gate Assessment

| Gate | Result |
|---|---:|
| PR #451 merged to main | FAIL |
| Merge commit SHA available | FAIL |
| Staging `/api/version` matches merge/main SHA | FAIL |
| Invalid risk payload returns `400 risk_config_invalid` | PASS |
| Invalid risk payload does not mutate config | PASS |
| Invalid risk payload does not trigger recompute | PASS |
| Valid partial score-map update preserves unrelated config | PASS |
| Synthetic admin mutation audit before/after evidence | PASS |
| CSV formula escaping | PASS |
| Browser smoke clean | PASS |

## Remaining Blockers

1. **PR #451 is still open and draft.** It has not been merged to `main`.
2. **No merge commit exists.** `mergeCommit=null`.
3. **Staging is not running merged `main`.** `/api/version` reports `6bbf86e3fb0bc0292fa84c45b9c541075dd71712`, which is not contained in `origin/main`.

## Synthetic Mutations Performed

Only controlled synthetic mutations were used:
- Risk-model partial country score-map update with `ADMIN_AUDIT_POSTMERGE_TEST_COUNTRY`, then restored.
- AI agent description update and revert on an existing agent.
- Synthetic AI verification check rows with `admin_audit_postmerge_*`.
- System settings no-op/current-value save with explicit dangerous-change confirmation.
- Synthetic inactive user with formula-like email target for CSV escaping validation.

No Sumsub, AML, screening, or live provider calls were triggered.

## Final Verdict

**FAIL**

The deployed hardening behavior is materially working on staging, but this is not a valid post-merge closure. Do not mark ADMIN-PILOT-CONTROLS-HARDENING closed until PR #451 is merged to `main`, staging is redeployed from that merged main SHA, and this validation is rerun with `/api/version` matching the merged main commit.
