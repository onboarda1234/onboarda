# RSMP Tier 0C-A Multi-Service Maximum-Risk Hotfix Evidence

**Assessment date:** 2026-07-16

**Scope:** read-only hotfix validation; no activation, recomputation, deployment, or staging mutation

**Verdict:** reconciled for draft human review; no pilot- or production-readiness claim

## Pinned baseline and drift

| Check | Pinned value |
|---|---|
| Current branch base / `origin/main` | `5fcd707f3814d88a8956f297705ae74e4419626d` |
| Replay snapshot deployed `GIT_SHA` / `IMAGE_TAG` | `1de43ae8e44cfcf842b6414423ee6aff0d672f9f` |
| Backend task definition | `regmind-staging:858` (2/2 running; rollout completed) |
| Worker task definition | `regmind-verification-worker:306` (6/6 running; rollout completed) |
| Activation environment | Absent; default remains OFF |
| Gate 0 canonical hash | `33cdcaac5f01ba431776a4b8a300aee4cb6e48f0d585d9c1665c726d655f66f0` |
| Risk-config version | `risk_config:2026-07-13 07:15:16.941658` |
| Full risk-config row hash | `6b00334da7fe82a4250fef864888b2ae7412bbe4b277a34b2d77dc3e19dfc91e` |
| Applications / scored applications | 944 / 790 |
| Active scored applications replayed | 650 |
| Latest stored risk computation | `2026-07-13 07:16:03` |

The merged Tier 0A, Tier 0B, PEP-alignment, and Tier 0D components are present. Since the prior Tier 0C-A baseline, PR #772 was documentation-only; PR #771 added only a database index plus its test; and PR #774 made that same index creation commit and verify independently. None overlaps service parsing, submission, scoring, recomputation, EDD, or approval routing. The branch was rebased onto #774 before publication. The replay snapshot stayed on the prior deployed image throughout the read-only transaction, and no material or policy-sensitive interaction was found.

## Root cause

The portal and database correctly retained every selected service in `services_required`. The canonical normalizer also projected the first list element into the legacy singular aliases `primary_service` and `service_required`. D3.1 in `compute_risk_score()` then read only those singular aliases. Consequently:

- list order could decide the score;
- a stale historical singular alias could override the persisted selections;
- submission, replay, and recomputation all inherited the defect because they share `build_prescreening_risk_input()` and `compute_risk_score()`;
- the fault was not caused by a lost database value, delimiter parsing, duplicate values, or a different recompute scorer.

The active-scored snapshot contained 28 true multi-service applications, all using list-shaped raw, canonical, and scorer payloads. It also contained legacy string/list variants outside that population; focused tests cover JSON-list strings, Python-list strings, delimited strings, nested objects, arrays, tuples, sets, blanks, duplicates, and mixed case/whitespace.

## Exact correction

`resolve_selected_service_risk()` is the one runtime-owned resolver for plural service payloads. It preserves the complete raw selection, normalizes each value independently without fuzzy matching, records every individual score, and returns the maximum. Submission and recomputation receive the complete source payload from `build_prescreening_risk_input()`; replay uses the same scorer path.

Unknown values in a true multi-select collection are recorded separately and reuse the existing hashed `stale:unmapped_*` approval block. They do not silently disappear or lower the maximum. No classifier, quarantine registry, score catalogue, weight, threshold, floor, route, schema, or migration changed. Zero single-service applications changed in the 650-application replay.

## Read-only replay summary

| Metric | Before hotfix | After hotfix |
|---|---:|---:|
| Multi-service applications | 28 | 28 |
| Correct max-risk outcomes | 13 | 28 |
| Incorrect outcomes | 15 | 0 |
| Service-factor changes | 0 | 15 |
| Composite-score changes | 0 | 9 |
| Tier changes | 0 | 0 |
| EDD-route changes | 0 | 0 |
| Approval-route changes | 0 | 0 |
| Newly unresolved applications | 0 | 0 |
| Unexplained deltas | 0 | 0 |

Twenty already-unresolved applications gained service-specific structured evidence/sentinels for legacy labels. Every one already had at least one unrelated unresolved mapping; therefore no application became newly unresolved or newly approval-blocked. The existing Tier 0C-A unresolved, newly blocked, and data-remediation backlogs were not remediated or otherwise changed by this exercise.

## Reconciled 28-case population

Identifiers are irreversible 16-character SHA-256 pseudonyms. “Route” covers both EDD and approval classification.

| Application | Fixture? | Selected services | Service factor | Composite score | Tier | Route change | Classification |
|---|---:|---|---:|---:|---|---|---|
| `ad872cca36f2775c` | No | Virtual asset services; Cross-border international transfers | 3 → 3 | 55.0 → 55.0 | HIGH → HIGH | None | Expected |
| `8ad9f4d9495b02e9` | No | Multi-currency account; Cross-border payments; Card issuing | 2 → 3 | 67.1 → 69.8 | HIGH → HIGH | None | Expected |
| `bbf8b2ef7250e594` | No | Domestic payments; Multi-currency corporate accounts; Cross-border international transfers | 1 → 3 | 55.0 → 55.0 | HIGH → HIGH | None | Expected (existing floor) |
| `2fbdaced8f6254c9` | Yes | Domestic payments; Cross-border international transfers | 3 → 3 | 25.0 → 25.0 | LOW → LOW | None | Expected |
| `badc55a396588846` | No | payments; virtual_accounts; treasury | 2 → 3 | 55.0 → 55.0 | HIGH → HIGH | None | Expected (existing floor) |
| `93a78ccab931e080` | Yes | Payment Processing; Virtual IBAN; FX Conversion | 3 → 3 | 63.0 → 63.0 | HIGH → HIGH | None | Expected |
| `37547b2af9f437d2` | No | account_opening; payments | 3 → 3 | 55.0 → 55.0 | HIGH → HIGH | None | Expected |
| `c8c91233da0ee381` | No | Domestic payments; Cross-border international transfers | 1 → 3 | 21.7 → 27.0 | LOW → LOW | None | Expected |
| `21866a59b44efdef` | No | Cross-border payments; Virtual asset payments | 2 → 3 | 59.0 → 61.7 | HIGH → HIGH | None | Expected |
| `1b49b4fd681d2a69` | No | Domestic payments; Cross-border international transfers | 1 → 3 | 12.3 → 17.7 | LOW → LOW | None | Expected |
| `206acc952fb6ff17` | No | account_opening; payments | 1 → 3 | 10.7 → 16.0 | LOW → LOW | None | Expected |
| `ce3cc760523c5bd4` | No | Domestic payments; Multi-currency corporate accounts; Cross-border international transfers | 1 → 3 | 55.0 → 55.0 | HIGH → HIGH | None | Expected (existing floor) |
| `e5a379c0f9d2ceb7` | No | Account opening; Compliance onboarding; Domestic payments | 3 → 3 | 23.7 → 23.7 | LOW → LOW | None | Expected |
| `15b7c1bee562858f` | No | Domestic payments; Cross-border international transfers | 1 → 3 | 70.0 → 70.0 | VERY_HIGH → VERY_HIGH | None | Expected (existing floor) |
| `102745b86c4f8ea6` | No | Virtual asset payments; Cross-border payments | 3 → 3 | 55.0 → 55.0 | HIGH → HIGH | None | Expected |
| `3c543f72794acf3e` | No | Corporate account; Domestic payments | 2 → 2 | 55.0 → 55.0 | HIGH → HIGH | None | Expected |
| `0e9c7931a9aecbe6` | No | account_opening; payments | 3 → 3 | 18.0 → 18.0 | LOW → LOW | None | Expected |
| `ef7d4e085897c639` | No | Corporate account; Domestic payments | 2 → 2 | 55.0 → 55.0 | HIGH → HIGH | None | Expected |
| `c4f3492cbed61e1d` | Yes | Domestic payments; Multi-currency corporate accounts | 1 → 2 | 23.7 → 26.3 | LOW → LOW | None | Expected |
| `80d9b1cc4fc553ec` | No | account_opening; payments | 1 → 3 | 10.7 → 16.0 | LOW → LOW | None | Expected |
| `5c8d0d45909892ad` | No | account_opening; payments | 3 → 3 | 18.0 → 18.0 | LOW → LOW | None | Expected |
| `d592337face7c474` | No | account_opening; payments | 1 → 3 | 55.0 → 55.0 | HIGH → HIGH | None | Expected (existing floor) |
| `9e49ddb2122e8ffb` | No | Virtual asset payments; Cross-border payments | 3 → 3 | 55.0 → 55.0 | HIGH → HIGH | None | Expected |
| `6419454f2c456972` | No | account_opening; payments | 1 → 3 | 10.7 → 16.0 | LOW → LOW | None | Expected |
| `09b226dc35ab8cc8` | No | account_opening; payments | 1 → 3 | 10.7 → 16.0 | LOW → LOW | None | Expected |
| `b0e5e2fd47effdaa` | No | account_opening; payments | 3 → 3 | 18.0 → 18.0 | LOW → LOW | None | Expected |
| `002744595894452e` | No | payments; virtual_accounts; treasury | 3 → 3 | 55.0 → 55.0 | HIGH → HIGH | None | Expected |
| `a1cc994a772bea1e` | No | Domestic payments; Multi-currency corporate accounts | 1 → 2 | 55.0 → 55.0 | HIGH → HIGH | None | Expected (existing floor) |

## Safety proof

The replay ran inside `BEGIN; SET TRANSACTION READ ONLY` and reported `transaction_read_only=on`. Before and after values were identical for application count (944), scored count (790), latest risk-computation timestamp, risk-config version, and full risk-config hash. The activation environment remained absent. No application was recomputed and no database, ECS, staging configuration, provider, email, or memo action was invoked.

Focused regression tests prove that sector, opaque-ownership, and declared-PEP High floors remain unchanged; the exact volume-specific reason remains distinct from the generic score-4 reason; unresolved sentinels still block approval; runtime/UI projection documents the same maximum rule; and the flag-OFF primary-service path remains equivalent.

## Next controlled step

After CI and human review, this draft may be considered for merge. It must not be activated or deployed by this PR workflow. A fresh Tier 0C-A read-only assessment remains the next step; Tier 0C-B, recomputation, pilot readiness, and production readiness remain out of scope.
