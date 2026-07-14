# RegMind Risk Scoring — Founder Decision Pack

**Audited commit:** `c83f0107c3227e538d2ad30f063c9cf14172221c`
**Decision status:** open; no decision in this pack has been implemented
**Use:** founder, MLRO/compliance, model owner, engineering owner and independent validator

## 1. Decision sought

Do **not** declare the current risk-scoring model production-regulator-ready. Authorize a controlled remediation and independent validation programme, while keeping runtime settings and model logic unchanged until the policy decisions below are signed.

The immediate reason is not a preference for different calibration. Exact current portal labels can bypass explicitly configured high-risk values: `Unregulated Fund / SPV` resolves to entity score 1 instead of 4, and `Precious Metals / Gems` resolves to sector score 3 instead of 4. The jurisdiction map also lacks current-source governance and does not match FATF's 19 June 2026 statements.

## 2. Founder summary

### What works

- A transparent D1–D5 weighted formula with seeded weights and bands.
- Deterministic High/Very High floors for PEPs, high-risk sectors, opaque ownership, severe jurisdictions and material screening concerns.
- Database-backed settings and staging/production fail-closed posture for missing or shape-malformed configuration.
- Recompute hooks for material edits, screening, KYC, periodic confirmation and config changes.
- Stale sentinels, memo mismatch blocking, screening/IDV/readiness gates and two-person High/Very High approval.
- 442 focused regression tests passed; the full 7,017-test collection completed with 6,964 passed, 49 skipped and 4 expected failures on supported Python 3.11.

### What prevents a readiness claim

- Critical first-substring-wins taxonomy defects silently under-score live portal labels.
- Known DCI-108/109 can move a reproduced profile from 36.0 Low to approximately 41.0 Medium.
- DCI-110 and an additional `Under 50,000` ordering defect mis-score exact portal volume labels.
- Manual country settings are stale/no-source and conflate FATF, sanctions and internal appetite.
- Unknown/missing inputs often default to 1 or 2; absence can look low risk.
- A single active row is edited directly without maker-checker activation, immutable versions or complete historic replay.
- Monitoring alerts do not automatically stale or recompute authoritative risk.
- Multiple consumers can use different risk fields or alternate memo/supervisor aggregation.

## 3. Required decisions

Record one option, owner and date for every decision. Recommendations are audit recommendations, not implemented settings.

### D-01 — Production claim and release posture

**Question:** may RegMind describe this model as production/regulator ready?

- A — Yes, immediately.
- B — No; continue pilot/internal evaluation only until the exit criteria in section 5 pass.
- C — Disable all scoring use.

**Recommendation:** B. Keep the model available only within the already-approved pilot/control envelope and make limitations explicit. Do not expand scope.

### D-02 — Canonical taxonomy contract

**Question:** how should controlled portal labels map to scores?

- A — Stable IDs with exact approved mapping; explicit aliases; longest-specific fallback only for legacy text.
- B — Reorder existing substring keys.
- C — Preserve first-substring-wins.

**Recommendation:** A. Reordering alone is fragile and will recreate collisions.

**Required approvers:** model owner, compliance/MLRO, product owner.
**Required evidence:** exhaustive mapping of every accepted portal/API value, unknown-value rule and collision test report.

### D-03 — Entity-type intent

**Question:** confirm scores for `Regulated Fund`, `Unregulated Fund / SPV`, shell companies, trusts/foundations and novel entity types.

**Current evidence:** explicit seed says regulated fund 2 and unregulated fund/SPV 4, but actual exact portal labels return 1. Unknown returns 2.

**Recommendation:** affirm or replace the intended values in a controlled policy table before engineering changes. Treat the current output as a defect, not approved calibration.

### D-04 — Sector calibration (`PR-RISK-SECTOR-CALIBRATION-1`)

**Question:** what regulator/tenant-specific sector tier should every portal sector receive, including private banking, banking-as-a-service, precious metals/gems, fintech/payments, gaming and professional services?

- A — Approve a single global mapping.
- B — Approve a base taxonomy plus regulator/tenant overlays.
- C — Continue generic keyword/default mapping.

**Recommendation:** B. The repository already records that calibration is paused pending compliance policy. Do not implement the calibration PR until this mapping, evidence and override hierarchy are approved.

### D-05 — Jurisdiction source and overlay hierarchy

**Question:** which sources create which control consequences?

Approve distinct signals for:

1. FATF call-for-action;
2. FATF increased monitoring;
3. UN/UK/Mauritius/tenant sanctions;
4. regulator-defined high-risk countries;
5. corruption/organized-crime indicators;
6. secrecy/offshore indicators;
7. internal risk appetite.

**Recommendation:** do not use one list labelled “FATF black/grey” for all signals. Record source URL, publication/effective date, review due date and approval. Current FATF grey status should inform risk analysis; it should not automatically imply the same response as a sanctions prohibition unless the applicable legal/risk policy says so.

### D-06 — Jurisdiction floor symmetry

**Question:** should score-3 exposure floor risk for incorporation only, or also for UBO/director nationality, operations, target markets and intermediaries?

**Current behaviour:** Nigeria incorporation → 55 High; otherwise clean British entity with Nigerian UBO → 10.7 Low. Score-4 nationality forces Very High.

**Recommendation:** document a relationship-specific matrix. Avoid a universal nationality rule without proportionality analysis, but do not leave the current asymmetry accidental.

### D-07 — Unknown and missing-data policy

**Question:** what score/status applies when a required factor is absent, unmapped, stale, provider-error or unverified?

- A — Add explicit `UNKNOWN/UNVERIFIED` status, conservative score/floor and timed exception workflow.
- B — Continue numeric defaults only.
- C — Block all incomplete cases before scoring.

**Recommendation:** A, with C for legally indispensable CDD facts. Separate inherent risk from data quality, but make both visible in the authoritative decision contract. Do not treat missing adverse-media output as “clear.”

### D-08 — DCI-108 and DCI-109 intended values

**Question:** confirm `Very complex` = 4 and `Introduced by non-regulated intermediary` = 3.

**Evidence:** current boundary profile is 36.0 Low; these intended values produce approximately 41.0 Medium.

**Recommendation:** confirm with compliance and the controlled calculator/methodology, then implement together with exact IDs/labels and boundary tests. Do not patch substrings independently.

### D-09 — DCI-110 and numeric bands

**Question:** confirm monthly volume bands and whether volume score 4 alone must require compliance approval.

**Current behaviour:** the 500k–5m label scores 4; the under-50k label scores 2. Both are caused by overlapping text checks.

**Recommendation:** store numeric bounds or stable band IDs. Confirm whether a score-4 subfactor should require compliance even if final tier is Low; this is current policy.

### D-10 — Floors and score/tier consistency

**Question:** when a control applies a Medium floor, should the displayed numeric score also become at least 40?

- A — Floor both number and tier.
- B — Preserve base number, but expose separate base score/final tier and prohibit deriving one from the other.

**Recommendation:** B if the score is intended to remain the weighted model output; persist/display `base_score`, `base_tier`, `final_tier`, `floor`, `route` and reason as a single contract. Current mixed convention (Medium preserves number; High/Very High changes it) should end.

### D-11 — Model change governance

**Question:** approve an immutable maker-checker activation lifecycle?

**Recommendation:** yes. Minimum roles: proposer, model owner, compliance approver, independent validator and release operator, with segregation appropriate to team size. Require version/hash, effective date, reason, impact analysis, test evidence, sign-off, rollback and superseded linkage. Emergency changes require retrospective approval and automatic expiry.

### D-12 — Historic decision evidence

**Question:** how much evidence must be retained to reproduce a decision?

**Recommendation:** retain a content-addressed snapshot of normalized inputs, factor values/sources/statuses, per-factor scores, complete model configuration, country/sector source provenance, floors, EDD route, code/model version and decision actors/timestamps for the applicable legal retention period. A mutable prescreening record plus config timestamp is insufficient.

### D-13 — Monitoring-to-risk policy

**Question:** which events should mark authoritative risk stale, quarantine approval, recompute immediately, open EDD or require a human decision by SLA?

**Recommendation:** approve an event matrix before enabling broad monitoring/risk drift. Until then, state clearly that alert creation does not itself change the risk score and measure time-to-disposition.

### D-14 — One authoritative risk contract

**Question:** which fields may UI, memo, supervisor, evidence packs and reports use?

**Recommendation:** one canonical service/record should provide base score/tier, final tier, active floors, route, provenance and freshness. Alternate memo/supervisor risk assessments must be labelled advisory; any divergence should be surfaced, including a one-tier difference.

### D-15 — Independent quantitative validation

**Question:** what evidence is required beyond code tests?

**Recommendation:** an independent validator must test conceptual soundness, data representativeness, calibration/discrimination, stability, overrides, false-negative/false-positive patterns, outcome drift, sensitivity, protected-class/fairness implications where applicable, and operational controls. Define acceptance thresholds before seeing results.

## 4. Decision record

| Decision | Selected option/policy | Accountable owner | Compliance approval | Independent validation | Date/effective version |
|---|---|---|---|---|---|
| D-01 Release posture |  |  |  |  |  |
| D-02 Taxonomy contract |  |  |  |  |  |
| D-03 Entity types |  |  |  |  |  |
| D-04 Sector calibration |  |  |  |  |  |
| D-05 Jurisdiction hierarchy |  |  |  |  |  |
| D-06 Geographic floors |  |  |  |  |  |
| D-07 Unknown/missing |  |  |  |  |  |
| D-08 DCI-108/109 |  |  |  |  |  |
| D-09 DCI-110/volume |  |  |  |  |  |
| D-10 Floor semantics |  |  |  |  |  |
| D-11 Change governance |  |  |  |  |  |
| D-12 Historic evidence |  |  |  |  |  |
| D-13 Monitoring policy |  |  |  |  |  |
| D-14 Risk contract |  |  |  |  |  |
| D-15 Independent validation |  |  |  |  |  |

## 5. Minimum exit criteria before a production-readiness claim

All criteria are mandatory unless a named accountable executive and compliance authority accept a documented, time-limited exception.

- [ ] D-01 through D-15 are approved and version-controlled.
- [ ] RSM-01 taxonomy collisions and DCI-108/109/110/S39 parser defects are corrected against stable identifiers.
- [ ] Every portal/API country, sector, entity, volume, complexity, channel and interaction value has an exact golden expected outcome.
- [ ] Country sources are current, separately classified, effective-dated and dual-approved; stale-source failure posture is tested.
- [ ] One exhaustive semantic validator is used at write, load, migration, activation and startup.
- [ ] Immutable model versioning, maker-checker activation, rollback and partial-rollout recovery are rehearsed.
- [ ] Unknown/missing/provider-error states are explicit and cannot masquerade as verified low risk.
- [ ] Routing is decision-critical and fails closed/quarantines when unavailable.
- [ ] Legacy blank config provenance and memo snapshots are migrated, re-scored or formally excepted.
- [ ] A complete historic decision snapshot is retained and independently replayed.
- [ ] Monitoring/periodic event handling has approved stale/recompute/EDD/quarantine rules and measured SLAs.
- [ ] All consumers use the canonical risk contract; advisory divergence is visible.
- [ ] Exact boundary, mutation, collision, normalization, recompute, staleness, approval, memo, periodic and monitoring tests pass.
- [ ] Full supported-environment test suite passes; migration/rollback and failure-injection evidence is attached.
- [ ] Independent quantitative/model validation passes pre-agreed thresholds on representative data.
- [ ] Legal/compliance review confirms Mauritius/regulator/tenant obligations and retention requirements.
- [ ] Operational owners, monitoring thresholds, review cadence and incident/override procedures are live.

## 6. Suggested programme and ownership

| Workstream | Accountable role | Deliverable | Must precede |
|---|---|---|---|
| Policy and legal interpretation | MLRO/compliance | signed D-03–D-10 matrices and regulatory basis | scoring changes |
| Model governance | model owner/founder | immutable lifecycle, roles, validation standard | activation |
| Taxonomy/data contract | product + compliance | stable IDs and total portal/API mappings | DCI/sector implementation |
| Engineering remediation | engineering owner | collision-proof matcher, semantic validator, decision record, canonical consumer contract | release candidate |
| Ongoing-risk controls | compliance operations | monitoring/recompute/quarantine event matrix and SLAs | broad monitoring |
| Independent validation | independent validator | conceptual, empirical and operational validation report | production claim |
| Release assurance | release owner | migration, replay, rollback, failure injection and full-suite evidence | activation |

## 7. Explicit non-decisions in this audit

This review does **not** choose new weights, thresholds, sector scores, country scores, entity scores, volume bands, PEP treatment, EDD triggers or approval policy. It does not implement DCI-108, DCI-109, DCI-110 or `PR-RISK-SECTOR-CALIBRATION-1`. The sensitivity numbers show why accountable policy decisions are necessary; they are not proposed runtime values.

## 8. Supporting material

- [Full Risk Scoring Model Audit](RISK_SCORING_MODEL_FULL_AUDIT.md)
- [Risk Scoring Settings Register](RISK_SCORING_SETTINGS_REGISTER.md)
- [Risk Scoring Scenario Matrix](RISK_SCORING_SCENARIO_MATRIX.md)
