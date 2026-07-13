# RegMind Risk Scoring Scenario Matrix

**Audited commit:** `c83f0107c3227e538d2ad30f063c9cf14172221c`
**Execution date:** 13 July 2026
**Configuration:** seeded `risk_config` row at the audited commit
**Scenario count:** 39 model/control scenarios, plus 12 explicit band/config boundary probes

## 1. How to read this matrix

`Base` is the normalized weighted result before floors. `Final` is the authoritative deterministic tier after scorer floors. Some requested conditions—IDV, documents, memo freshness, config freshness, periodic decisions and monitoring alerts—are not inputs to `compute_risk_score`; their outcome is therefore shown in the control/gate column. “Unchanged score” must not be read as “no control.”

The matrix is forensic reproduction of the current implementation, not a statement that the result is the correct risk policy. “Intended/corrected” values for DCI-108/109/110 are sensitivity calculations based on the documented remediation statements; no code/config was changed.

## 2. Reference clean profile

Unless overridden, executable score scenarios use:

```text
country: United Kingdom
sector: Software / SaaS
entity type: Listed Company on Regulated Exchange
ownership: Simple — direct identifiable UBOs
director and UBO: British, explicit non-PEP
operations/target: United Kingdom
service: Domestic single currency
volume: Under USD 50,000 per month
complexity: Simple domestic
introduction: Direct application — client initiated
interaction: Face-to-face
source of wealth: Business revenue
source of funds: Company bank account
```

Observed reference result: D1 1.00, D2 1.00, D3 1.35, D4 2.00, D5 1.00; Base 7.3 Low; Final 7.3 Low; no escalation.

## 3. Core model scenarios

| ID | Scenario/change from reference | Observed base | Observed final | Escalation/control outcome | Audit assessment |
|---|---|---:|---|---|---|
| S01 | Clean low-risk baseline | 7.3 Low | 7.3 Low | Fast Lane; no scorer escalation | Reproduces. D3 is 1.35 rather than 1 because the phrase `Under USD 50,000` contains `50,000` and is scored 2 before the later `under` check. This is an additional parsing-order issue. |
| S02 | Newly incorporated entity | 11.3 Low | 11.3 Low | No scorer escalation | Entity score 3 is diluted within D1; no automatic EDD/floor solely for newness. |
| S03 | Crypto / Digital Assets Exchange | 17.3 Low | 55.0 High | `floor_rule_high_risk_sector`, score-4 and sector escalation; EDD/dual control | Strong floor works. |
| S04 | Unmapped `Quantum Moon Services` / `Alien DAO` | 9.3 Low | 9.3 Low | Both maps default 2 | Novel/unknown taxonomy does not create uncertainty escalation. |
| S05 | Nigeria incorporation, operations and target | 17.3 Low | 55.0 High | elevated-jurisdiction floor; EDD/dual control | Current seed says Nigeria 3 even though not on the 19 June 2026 FATF increased-monitoring list. |
| S06 | British clean entity with Nigerian UBO | 10.7 Low | 10.7 Low | No floor/escalation | Shows asymmetry: score-3 party nationality remains Low. |
| S07 | Iran incorporation/operations/target | 22.3 Low | 70.0 Very High | sanctioned-country floor; score-4 escalation | Direct scorer result. Real submission blocks prohibited incorporation **before scoring**, so this is a counterfactual model output. |
| S08 | British clean entity with Iranian UBO | 12.3 Low | 70.0 Very High | sanctioned-nationality floor; score-4 escalation | Very High/EDD/dual control; not the same as the incorporation submission prohibition. |
| S09 | Foreign PEP director (`client_declared_pep=true`) | 14.8 Low | 55.0 High | declared-PEP floor; score-4 escalation; EDD/dual control and senior authority | Canonical separated PEP state works. |
| S10 | Domestic PEP UBO | 12.3 Low | 55.0 High | declared-PEP floor; EDD/dual control and senior authority | Connected-person PEP is included. |
| S11 | Confirmed sanctions match in screening result | 7.3 Low | 55.0 High | screening-concern floor; downstream screening truth blocks decision until formal disposition | The score is not a permission to onboard. |
| S12 | `possible match` sanctions status | 7.3 Low | 55.0 High | substring `match` creates screening-concern floor; screening gate applies | Conservative for this exact string. Provider pending/error without match wording does not itself score higher. |
| S13 | Confirmed criminal adverse media | 11.8 Low | 55.0 High | screening-concern floor and score-4 escalation | D1 adverse increases; material floor dominates final. |
| S14 | Nigeria + crypto + opaque nominee + two screening concerns + high activity | 72.5 Very High | 72.5 Very High | grey/sector/opaque and severe-combination escalations; EDD/dual control | Reproduces severe-case route without needing numeric floor. |
| S15 | Complex multi-jurisdiction/opaque ownership | 13.3 Low | 55.0 High | opaque-ownership and score-4 escalations; EDD/dual control | Strong floor works for the portal phrase. |
| S16 | Trust + 3+ ownership layers/nominees | 15.3 Low | 55.0 High | opaque-ownership floor | Trust alone scores 3; nominee phrase drives floor. |
| S17 | No UBO rows | 7.3 Low | 7.3 Low | No score uncertainty; nationality factor becomes 1 | Submission requires a director, not a universal UBO row. Other CDD/document controls may apply, but the scorer does not show the absence. |
| S18 | `idv_status=failed` attached to profile | 7.3 Low | 7.3 Low | IDV is not a score input; approval IDV gate blocks | Control exists outside model. Score can still display Low. |
| S19 | Empty/missing documents attached to profile | 7.3 Low | 7.3 Low | Documents are not score inputs; enhanced-requirement/memo/readiness gates determine blockage | No generic documentation uncertainty in D1–D5, despite memo's separate documentation factor. |
| S20 | Over USD 5,000,000 per month | 12.0 Low | 12.0 Low | `sub_factor_score_4` requires compliance even at Low | High volume alone does not floor tier but removes direct approval. |
| S21 | Cross-border high-risk corridor with Nigeria target | 14.8 Low | 14.8 Low | complexity score 4 adds mandatory compliance | The incorporation floor does not apply to a score-3 target market. Routing may still use broader facts. |
| S22 | Several medium factors (new entity, layered ownership, consulting, cross-border, mid-volume, video) | 29.3 Low | 29.3 Low | No scorer escalation | Demonstrates dilution: multiple score-2/3 factors can remain Low. |
| S23 | Stronger mixed profile: Mauritius, new entity, layered ownership, real estate, cross-border, 500k, unsolicited, video, high SoW/SoF | 45.0 Medium | 45.0 Medium | score-4 introduction creates compliance requirement | Reproduces an ordinary Medium band case. |
| S24 | Explicit clear sanctions/adverse-media results | 7.3 Low | 7.3 Low | no screening floor | Appropriate only if clearance is current, terminal and linked to the scored subjects/inputs. |

## 4. Staleness, memo, periodic and monitoring scenarios

| ID | Scenario | Score/tier behaviour | Decision/control behaviour | Gap or conclusion |
|---|---|---|---|---|
| S25 | Stored non-empty config version older than current | Scorer output itself unchanged until recompute | Final approval blocked by staleness gate | Strong known-stale protection. |
| S26 | `stale:recompute_failed` or `stale:cm_recompute_pending:<id>` | Prior score can remain displayed | Approval blocked/quarantined until successful recompute | Strong inter-transaction/config rollout guard. |
| S27 | Blank/missing `risk_config_version` | Existing score accepted as unknown provenance | Config staleness gate explicitly does **not** block; other integrity gates may | Legacy compatibility is fail-open for provenance. |
| S28 | Memo score/tier snapshot differs from application | Application risk remains authoritative | Memo marked stale; approval/export blocked where memo required | Strong mismatch control. |
| S29 | Legacy memo has no risk snapshot | Application risk remains authoritative | No memo risk mismatch can be computed | Legacy evidence gap. |
| S30 | Periodic review detects material change but officer has not confirmed/completed | No automatic mutation or recompute | Suggestion and audit record only | Intentionally human-controlled; SLA/quarantine policy needed. |
| S31 | Officer confirms higher periodic risk and completes review | Model recomputed; final tier is maximum of prior/model/officer-confirmed; no automatic downgrade | Memo/addendum and lifecycle updates follow | If recompute fails, confirmed tier can still be applied while numeric/config provenance may remain inconsistent. |
| S32 | New high/critical monitoring alert | No direct `compute_risk_score` invocation and no automatic score staleness | Alert can drive triage, EDD or periodic-review linkage through separate lifecycle paths | Pilot explicitly excludes broad risk drift/transaction monitoring; authoritative risk can remain unchanged. |

## 5. Known remediation and collision scenarios

| ID | Exact label/profile | Current observation | Intended/sensitivity observation | Decision impact |
|---|---|---|---|---|
| S33 | DCI-108 + DCI-109 boundary profile: `Very complex`; `Introduced by non-regulated intermediary` | complexity 3, introduction 1; Base/Final **36.0 Low** | With factor values 4 and 3, approximately **41.0 Medium** | Concrete Low→Medium flip; no implementation in this review. |
| S34 | DCI-110: `USD 500,000 to USD 5,000,000 per month` | volume 4; clean profile 12.0 Low plus mandatory score-4 compliance escalation | Intended volume 3; approximately 9.7 Low without that score-4 escalation | Tier unchanged in clean case, but approval route/effort changes. |
| S35 | `Precious Metals / Gems` | sector 3; 12.3 Low; no sector floor | Explicit key `precious metals` is 4, which would cause High/55 | Silent under-tier caused by substring order. |
| S36 | `Unregulated Fund / SPV` | entity 1; 7.3 Low | Explicit `unregulated fund` and `spv` keys are 4; factor-level escalation expected | Severe silent under-score; configured value is unreachable for exact portal label. |
| S37 | `Regulated Fund (CIS / Licensed)` | entity 1 | Explicit `regulated fund` key is 2 | Lower than configured due generic `regulated`. |
| S38 | `Banking-as-a-Service` | sector 1; clean profile 2.3 Low | Explicit `banking` key is 2; private-banking policy may be higher | Generic `bank` masks more specific term. |
| S39 | Reference volume `Under USD 50,000 per month` | volume 2 because `50,000` branch precedes `under`; reference score 7.3 | If intended low band score 1, reference would be approximately 5.0 | Additional order defect not named in DCI-110. |

The 39 cases include every scenario requested in the review brief and four additional collision/parser variants that share the same root causes.

## 6. Boundary and validation probes

### 6.1 Tier boundaries

| Numeric input | Runtime tier | Observation |
|---:|---|---|
| 0 | Low | lower configured edge |
| 39.9 | Low | configured Low maximum |
| 39.99 | Low | exceeds configured max but remains Low because `max` is ignored |
| 40 | Medium | Medium minimum |
| 54.9 | Medium | configured Medium maximum |
| 54.99 | Medium | exceeds configured max but remains Medium |
| 55 | High | High minimum |
| 69.9 | High | configured High maximum |
| 69.99 | High | exceeds configured max but remains High |
| 70 | Very High | Very High minimum |
| 100 | Very High | configured maximum |
| 101 | Very High | no upper-bound rejection |

### 6.2 Malformed configuration

| Probe | Loader validator | Use-time result |
|---|---|---|
| sector map `{x: 99}` | zero validation errors | score can escape intended 1–4 scale and normalized assumptions |
| threshold `min="bad"`, reversed/out-of-range max values | zero validation errors | `TypeError` while sorting/comparing at classification |
| gapped threshold maxima/minima | shape valid | classification follows only minima; maxima/gaps have no effect |

The admin API separately rejects map scores outside 1–4 and performs stronger weight/threshold checks. The problem is that canonical load/startup trust is weaker than admin-write validation.

## 7. Normalization and source-boundary observations

| Boundary | Behaviour | Risk |
|---|---|---|
| Application/prescreening | Canonical mapper prefers normalized values and projects compatibility aliases | Multiple legacy aliases remain; factor parsers still operate on display text. |
| Party PEP | Explicit client/officer declaration metadata has priority; legacy `is_pep` is fallback only | Good truth separation; tests/narratives still contain some legacy strings. |
| Country aliases | Several prefixes/aliases and demonyms normalized | Coverage is finite; missing alias silently defaults 2. |
| Sector/entity | No stable ID; substring keyword over full display label | Critical collision class. |
| Volumes/complexity/introduction | Formatted labels parsed by substring order | DCI-108/109/110 plus S39. |
| Screening | Provider/report state translated through multiple truth/disposition layers | Formal disposition bridge is good; raw errors/no-result do not affect score. |
| Memo/supervisor | Separate aggregation/reasoning over mutable evidence | Can diverge from application deterministic model. |

## 8. Control outcome map

| Condition | Score | EDD route | Approval | Memo/evidence |
|---|---|---|---|---|
| Clean Low | weighted Low | no deterministic EDD | direct if all other gates clear | compliance memo may be skipped |
| Low/Medium with score-4 subfactor | may stay Low/Medium | trigger-dependent | compliance required | compliance package required |
| High/Very High/floored | ≥55/≥70 except separate Medium floor | EDD | two-person dual control | memo/supervisor/EDD evidence required |
| Failed IDV/missing required document | score unchanged | trigger-dependent | readiness/IDV gate blocks | evidence incomplete/stale |
| Unresolved screening | High floor where recognized; formal dispositions also floor | EDD | screening gate blocks | memo stale/regeneration as applicable |
| Known stale config | prior score remains until recompute | no inference | blocked | provenance invalid |
| Stale memo | application score unchanged | no inference | blocked where memo required | regenerate/approve current memo |
| Monitoring alert | score unchanged by alert itself | lifecycle may open EDD | depends on linked case/control state | separate monitoring evidence |

## 9. Reproduction evidence

Focused existing tests on supported Python 3.11:

- 243 passed: scoring, elevation, config integrity, config fail-closed, recomputation and config staleness;
- 199 passed: approval, IDV, portal-to-approval, memo staleness, risk display, periodic review and monitoring routing/status;
- full repository collection: 6,964 passed, 49 skipped and 4 expected failures (7,017 total) in 13m28s;
- exact one-off behavioural probes used the seeded database and `compute_risk_score(..., config_override=seeded_config)` to avoid environmental fallback ambiguity.

The repository's default macOS `python3` was 3.9 and caused import-time union-type errors. The project declares Python ≥3.11, so those failures were environmental and were rerun successfully on Python 3.11.
