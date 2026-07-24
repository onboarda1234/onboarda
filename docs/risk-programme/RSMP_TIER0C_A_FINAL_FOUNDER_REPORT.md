# RSMP Tier 0C-A Final Founder Activation Assessment

## Executive summary

**Verdict: NOT READY**

The Tier 0 runtime implementation is technically reconciled against the approved Gate 0 model. The final read-only replay found no remaining scoring-engine defect, all 28 previously identified scored multi-service cases now apply the maximum selected-service risk, all targeted policy checks pass, and there are zero unexplained deltas.

Tier 0C-B must nevertheless not start. The current staging data contains a material unresolved population: 705 of 802 active applications are in the exclusive `BLOCKED` class, including 405 nonfixture applications. Activation would cause 363 applications to move from their stored approval route to fail-closed `blocked`, including 132 nonfixture applications and 76 pilot-relevant nonfixture cases. All 104 current compliance-review applications would be blocked; all 12 active periodic-review applications would be blocked.

The founder-approved disposition catalogue is present and preserved (105 quarantine rows and 9 reject rows), but the frozen baseline also contains 22 additional unresolved controlled labels affecting 44 active applications, 15 unresolved service labels affecting 32 active applications, and extensive missing required data. These are data/governance blockers, not evidence of a runtime scoring defect.

Direct answers:

- **Is Tier 0 engineering complete?** Yes for the currently approved model and runtime paths assessed here; no additional engineering defect was found.
- **Is the runtime model correct?** Yes. Policy checks pass and the replay has zero unexplained deltas.
- **Is real-data cleanup still required?** Yes. It is the principal blocker.
- **Is officer/founder disposition work required?** Yes, for quarantined, rejected, service, and newly observed unresolved values.
- **Can Tier 0C-B begin?** No.
- **Exact blockers:** 405 nonfixture active applications in `BLOCKED`; 47 additional nonfixture applications in `READY_AFTER_DATA_CLEANUP`; 22 unclassified controlled labels; 15 unresolved service labels; 130 active nonfixture applications without a stored score; and 76 pilot-relevant nonfixture applications that would become newly blocked.

This is an activation-safety finding only. It is not a pilot-readiness or production-readiness claim.

## 1. Frozen baseline

The baseline was pinned before replay and re-pinned after replay. No pinned value changed.

| Baseline item | Frozen value |
|---|---|
| `origin/main` | `8025040089000fdca2e4c013f70397d59f436e55` |
| Backend task definition | `regmind-staging:861`; desired/running `2/2`; rollout complete |
| Worker task definition | `regmind-verification-worker:309`; desired/running `6/6`; rollout complete |
| Backend/worker image tag | `8025040089000fdca2e4c013f70397d59f436e55` |
| Backend/worker `GIT_SHA` and `IMAGE_TAG` | Exact match to `origin/main` |
| Authenticated `/api/version` | HTTP 200; `git_sha` and `image_tag` exact match |
| Liveness / health / readiness | HTTP 200 / HTTP 200 / HTTP 200 with `ready=true` |
| RSMP activation environment | Absent |
| Runtime activation evaluation | `false` |
| Gate 0 hash | `33cdcaac5f01ba431776a4b8a300aee4cb6e48f0d585d9c1665c726d655f66f0` |
| Risk-config version | `risk_config:2026-07-13 07:15:16.941658` |
| Full risk-config hash | `6b00334da7fe82a4250fef864888b2ae7412bbe4b277a34b2d77dc3e19dfc91e` |
| Application snapshot hash | `16dd44d188015d1db39f4fc7fa57cdcbf49039c62a1f6959ea2540a0247d15ee` |
| Total / active applications | `944 / 802` |
| Active fixture / nonfixture | `308 / 494` |
| Active scored / unscored | `650 / 152` |
| Active scored fixture / nonfixture | `286 / 364` |
| Latest risk recomputation | `2026-07-13 07:16:03` |

Current active tier distribution: 397 Low, 15 Medium, 214 High, 24 Very High, and 152 unscored. Current lanes: 393 Fast Lane, 247 EDD, 10 standard, and 152 blank. Current approval routes: 124 direct low/medium, 102 compliance required, 143 dual control required, and 433 blocked.

The deployed main SHA is the merged multi-service hotfix baseline, not the stale pre-hotfix Tier 0C-A baseline.

## 2. Read-only replay result

The replay evaluated the complete Tier 0 model as if activation were enabled, while the deployed flag remained OFF. It ran inside a PostgreSQL `READ ONLY` transaction and compared the legacy/flag-OFF result to the Tier 0/flag-ON result for every active application. Terminal applications were evaluated separately and never included in a recomputation set.

| Metric | Total | Nonfixture | Fixture |
|---|---:|---:|---:|
| Applications replayed | 802 | 494 | 308 |
| Active scored | 650 | 364 | 286 |
| Active unscored | 152 | 130 | 22 |
| Weighted-score increases | 25 | 20 | 5 |
| Weighted-score decreases | 153 | 141 | 12 |
| No weighted-score change | 624 | 333 | 291 |
| Tier upgrades | 0 | 0 | 0 |
| Tier downgrades | 0 | 0 | 0 |
| EDD-route changes | 0 | 0 | 0 |
| Approval-route changes | 384 | 140 | 244 |
| High-floor changes | 0 | 0 | 0 |
| Compliance-review classification changes | 256 | 112 | 144 |
| Applications acquiring one or more mapping sentinels | 748 | 447 | 301 |
| Applications removing mapping sentinels | 0 | 0 | 0 |
| Remaining unresolved applications | 748 | 447 | 301 |
| Unexplained deltas | 0 | 0 | 0 |

Weighted scores changed on 178 applications. The final persisted score would change on 116 applications: 15 increases and 101 decreases. The difference arises because existing floor rules hold some final scores constant while the underlying weighted score changes. Floors themselves did not change.

Every score delta is explained by an approved factor-path change. Affected-factor application counts are: monthly volume 152, ownership 19, service 16, complexity 8, sector 4, introduction 3, and entity type 2; applications can have more than one affected family. The per-application old/new scores, tiers, routes, floors, factor deltas, and sentinel changes are in `RSMP_TIER0C_A_FINAL_CHANGED_APPLICATIONS.csv`.

All 384 model approval-route changes are explained fail-closed transitions caused by unresolved evidence:

| Flag-OFF route → Tier 0 route | Total | Nonfixture | Fixture |
|---|---:|---:|---:|
| Compliance required → blocked | 118 | 58 | 60 |
| Direct low/medium → blocked | 128 | 28 | 100 |
| Dual control required → blocked | 138 | 54 | 84 |

Compared with stored operational routes, 363 applications would become newly blocked: 132 nonfixture and 231 fixture. No EDD route, tier, or High floor changes remain to explain.

## 3. Exclusive recomputation classification

Every one of the 944 applications has a pseudonymised row in `RSMP_TIER0C_A_FINAL_APPLICATION_CLASSIFICATION.csv`. Classes are mutually exclusive and sum to the frozen application count.

| Classification | Total | Nonfixture | Fixture | Required handling |
|---|---:|---:|---:|---|
| `READY` | 48 | 42 | 6 | Technically eligible for automated recomputation after separate Tier 0C-B approval |
| `READY_WITH_OFFICER_REVIEW` | 0 | 0 | 0 | None in the clean, fully mapped population |
| `READY_AFTER_DATA_CLEANUP` | 49 | 47 | 2 | Resolve missing/stale/incomplete evidence, then replay |
| `BLOCKED` | 705 | 405 | 300 | Do not recompute; mapping/disposition remediation first |
| `TERMINAL_READ_ONLY` | 142 | 64 | 78 | Never mutate; retain would-have-changed evidence only |

The zero count for `READY_WITH_OFFICER_REVIEW` does not mean there is no officer workload. It means every active case needing a mapping decision is more conservatively classified as `BLOCKED`, not as recomputable with review.

Operationally significant populations:

- All 104 `compliance_review` applications are `BLOCKED`, including 12 nonfixture cases.
- There are no nonfixture applications in the plain `submitted` status; all three submitted fixtures are blocked.
- Of 12 nonfixture `submitted_to_compliance` applications, 11 are blocked and 1 requires data cleanup.
- All 12 active periodic-review applications are blocked; 9 would become newly blocked from their stored route, including 3 nonfixture cases.
- 76 pilot-relevant nonfixture applications would become newly blocked.
- The 122 approved applications are terminal/read-only. Nine approved applications would have a score change (8 nonfixture); none would have a tier change.

The terminal would-have-changed detail is in `RSMP_TIER0C_A_FINAL_TERMINAL_WOULD_HAVE_CHANGED.csv`.

## 4. Unresolved and quarantine population

The prior 602 count remains correct for active **scored** applications. The final scope required replaying all active applications, which adds 146 unresolved unscored applications and produces the current total of 748 unresolved active applications.

| Family | Active applications | Nonfixture | Fixture | Field occurrences | Current handling |
|---|---:|---:|---:|---:|---|
| Complexity | 720 | 420 | 300 | 720 | Missing data, quarantine, or new unresolved value |
| Entity type | 379 | 251 | 128 | 379 | Missing data, quarantine, or new unresolved value |
| Incorporation country | 31 | 29 | 2 | 31 | Blank country; data remediation required |
| Introduction | 670 | 370 | 300 | 670 | Missing, quarantine, reject, or new unresolved value |
| Monthly volume | 631 | 341 | 290 | 631 | Missing, quarantine, or new unresolved value |
| Ownership | 349 | 145 | 204 | 349 | Missing, quarantine, reject, or new unresolved value |
| Sector | 661 | 388 | 273 | 661 | Missing, quarantine, reject, or new unresolved value |
| Selected service | 32 | 31 | 1 | 63 | 15 legacy service labels require an explicit mapping decision |

Family counts overlap because one application may have several unresolved fields. The replay produced 3,504 unresolved field occurrences. Sentinel evidence remained structured, multiple sentinels coexisted, and every application with unresolved controlled evidence classified to `blocked`; no sentinel was silently overwritten or cleared.

Disposition reconciliation:

- All 105 founder-approved quarantine rows are still present and were observed in the active population.
- All 9 founder-approved reject rows are still present and were observed in the active population.
- Quarantine values affect 696 active applications (401 nonfixture).
- Reject values affect 48 active applications (19 nonfixture).
- Missing required data affects 654 active applications (356 nonfixture).
- Fifteen legacy service labels affect 32 active applications (31 nonfixture).
- Twenty-two additional unresolved controlled labels, not covered by the 105/9 founder disposition catalogue, affect 44 active applications (35 nonfixture). These require explicit review before Tier 0C-B; they must not be silently mapped.

These groups overlap. The exact raw/normalised values, counts, founder row IDs, statuses, and required actions are in `RSMP_TIER0C_A_FINAL_REPLAY_EVIDENCE.json`.

## 5. Gate 0 and regression proof

The deployed runtime was exercised with synthetic in-memory inputs under a read-only database transaction. All checks passed:

- Private Banking = 4 and the sector High floor applies.
- Unregulated Fund / SPV = 3.
- Precious Metals / Gems = 3.
- Investment Management = 3.
- Cloud Services = 2.
- Domestic, foreign, international-organisation, family-member, and close-associate declared PEP roles all score 4 and apply the existing High floor.
- Opaque ownership scores 4 and applies its existing High floor.
- Unsolicited/unknown referral scores 4 as a factor, emits only generic score-4 evidence, and creates no automatic High floor.
- Exact `Over USD 5,000,000 per month` emits `monthly_volume_score_4`, requires compliance review, emits no generic score-4 reason, and creates no High floor.
- Generic score-4 evidence from sector, ownership, PEP, or other factors does not trigger the volume rule.
- The 28 active scored multi-service applications resolve 28/28 to the maximum selected-service risk. Including unscored records, all 42 active multi-service payloads resolve correctly; there are zero mismatches.
- Lane B and other unknown controlled values remain fail-closed.
- Geography remains limited to the three approved D1a aliases: Hong Kong SAR, Congo (DRC), and Türkiye. Other geography treatment was not expanded.
- Sector-score-4, opaque-ownership, and declared-PEP High floors are unchanged.

Machine-readable policy evidence is in `RSMP_TIER0C_A_FINAL_POLICY_EVIDENCE.json`. A focused source-aligned regression run completed with **86 passed** across Tier 0D runtime/UI alignment, multi-service maximum-risk enforcement, PEP alignment, and Tier 0B fail-closed routing.

## 6. Runtime UI and export consistency

Authenticated staging checks returned HTTP 200 from `/api/config/risk-model`. The projection reported `read_only=true`, config version `risk_config:2026-07-13 07:15:16.941658`, activation disabled, the runtime scorer/loader as its source, 22 Lane B entries separated from active catalogues, and all 13 documented runtime rule IDs including multi-service maximum risk and unresolved-mapping blocking.

The deployed Back Office page remains read-only and source-aligned. Focused regression tests prove that displayed labels/scores come from the runtime projection; no browser-side risk scorer or mutable model state remains; CSV/PDF evidence uses stored backend-authoritative results; PEP evidence is score 4; D3 weights are runtime values; and stale or missing authoritative evidence blocks export.

Because activation remained OFF and writes were prohibited, hypothetical Tier 0 results were not written back to application screens or exports. Changed and unchanged application outcomes were therefore reconciled against the read-only replay evidence, while current UI/export values remained matched to current stored authoritative evidence. No misleading hypothetical export was generated.

## 7. Safety, logs, and invariants

| Safety check | Result |
|---|---|
| Database transaction | `READ ONLY` |
| Application snapshot | Unchanged before/after (`16dd44d…d15ee`) |
| Application count | Unchanged at 944 |
| Latest recomputation timestamp | Unchanged at `2026-07-13 07:16:03` |
| Risk config | Version and full hash unchanged |
| Activation flag | Environment absent; runtime false throughout |
| Memo regeneration | None |
| Application/risk/approval/EDD writes | None |
| Provider calls caused by assessment | None |
| Email sends caused by assessment | None |
| Deployment / ECS task-definition update | None |

CloudWatch Logs Insights query `0c4194fd-f460-433c-bf95-55ce6af2f9a1` covered the assessment window, scanned 2,541 records / 830,550 bytes, and matched zero ERROR, Exception, Traceback, unexpected 5xx, startup/boot failure, routing/replay failure, risk-config mutation, recomputation, provider-call, or email-send events. The synthetic policy evaluator emitted only expected informational floor messages in the bounded command session.

## 8. Remaining issues and rollback plan

**Remaining engineering issues:** none identified in the approved Tier 0 runtime behavior. The multi-service defect is fixed and the assessment produced zero unexplained deltas.

**Remaining operational blockers:** the 405 nonfixture blocked population, 47 nonfixture cleanup-only cases, 22 newly observed unresolved controlled labels, 15 unresolved service labels, 130 active nonfixture unscored records, all compliance-review cases blocked, and the affected periodic/pilot-relevant populations described above.

No rollback was required because nothing was activated or written. Before any future Tier 0C-B attempt, the rollback control must remain: pin code/config/data snapshots; activate staging only under an approved change window; halt recomputation on the first unexplained delta; set the activation flag back to OFF; restore the pinned task definitions if code alignment changes; verify application snapshot and recomputation timestamps; and never reverse application records by bulk overwrite without a separately reviewed recovery plan.

## 9. Founder decision required

Tier 0C-B must remain blocked until:

1. The 22 unclassified controlled labels and 15 service labels receive explicit founder/officer dispositions or are remediated as data.
2. The 405 nonfixture blocked applications have an approved handling plan, with pilot-relevant and periodic-review cases resolved first.
3. The 47 nonfixture cleanup-only cases and the wider overlapping missing-data population have complete authoritative evidence.
4. A fresh frozen-baseline replay returns an acceptable recomputation population with zero unexplained deltas.
5. Founder approval explicitly authorizes Tier 0C-B scope, exclusions, officer review, and rollback controls.

**Final verdict: NOT READY**

Activation remains OFF. Tier 0C-B has not started. No recomputation, deployment, staging-data mutation, or production-readiness claim was made.
