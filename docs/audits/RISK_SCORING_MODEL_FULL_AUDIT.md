# RegMind Risk Scoring Model — Full Review and Audit

**Audit date:** 13 July 2026
**Audited repository:** `onboarda1234/onboarda`
**Audited `origin/main` commit:** `c83f0107c3227e538d2ad30f063c9cf14172221c`
**Review branch:** `codex/risk-scoring-model-review-pack`
**Scope:** discovery, audit and reporting only

## 1. Opinion and use restriction

The implementation has a coherent five-dimension weighted model, deterministic floors, database-backed settings, recomputation hooks and several strong approval safeguards. It is nevertheless **not supportable as production-regulator-ready on the evidence reviewed**. One critical class of lookup defects silently under-scores labels that are explicitly configured as higher risk; the country taxonomy is materially out of date against the FATF statement in force at the audit date; and model governance does not retain approved immutable versions or the complete input/configuration snapshot needed to reproduce a historic decision.

This is a design-and-code audit, not a validation of a deployed tenant, production database, production permissions, production data, or legal compliance. The seeded configuration at the audited commit was used for behavioural reproduction. A live database may contain manually changed settings and must be exported and compared before any production conclusion. Regulatory observations are control-design comparisons, not legal advice.

No runtime weights, thresholds, configuration, model logic, UI, schema or data were changed. DCI-108, DCI-109, DCI-110 and `PR-RISK-SECTOR-CALIBRATION-1` were assessed but not implemented.

## 2. Executive result

| Area | Result | Summary |
|---|---|---|
| Mathematical core | Amber | Weighted formula and ordinary threshold boundaries reproduce, but classification ignores threshold `max`, semantic loader validation is incomplete, and floors can separate numeric score from final tier. |
| Taxonomy mapping | Red | First-substring-wins produces silent collisions, including `Unregulated Fund / SPV` → 1 although configured 4, and `Precious Metals / Gems` → 3 although configured 4. |
| Country risk | Red | Manual database settings are authoritative but have no current-source ingestion/attestation. They do not match FATF's 19 June 2026 publications. |
| PEP and screening | Amber | Declared/confirmed PEP and material screening concerns receive strong High floors; missing screening data is treated as clear within the score, relying on separate gates. |
| EDD and approval | Amber/Green | High/Very High dual control, compliance routing, staleness sentinels, screening gates and memo mismatch gates are meaningful strengths. Missing legacy provenance remains permissive. |
| Change governance | Red | An administrator mutates the single active row directly. There is no maker-checker approval, immutable version, effective date, regulator/tenant overlay, rollback package or old-config replay. |
| Historic reproducibility | Red | A timestamp is stored, not the full approved model or full normalized input snapshot. Some evidence reconstruction uses current configuration. |
| Ongoing monitoring | Red for risk drift | Monitoring alerts do not automatically invoke the model; the pilot explicitly excludes broad risk drift. Periodic reassessment is human-confirmed and deliberately non-mutating until completion. |
| Test evidence | Amber | 442 focused tests and the complete 7,017-case collection passed to its expected outcome on supported Python 3.11. Existing tests do not assert exact portal-label lookup outcomes for the collision cases or exact DCI-108/109/110 boundaries. |

**Disposition:** do not make a production-readiness or regulatory-compliance claim from the current model. Treat RSM-01, RSM-02, RSM-03, RSM-05, RSM-06, RSM-09 and RSM-10 as decision blockers until risk owners define and approve the intended policy and independent validation confirms the corrected implementation.

## 3. Architecture and authoritative data flow

### 3.1 Sources of truth

| Concern | Effective source | Fallback/secondary source | Audit conclusion |
|---|---|---|---|
| D1–D5 weights and subweights | `risk_config.dimensions` row `id=1` | Hardcoded arrays in `compute_risk_score` | Database is canonical in normal operation; no immutable approved version. |
| Risk bands | `risk_config.thresholds` | `CANONICAL_THRESHOLDS` | Only each `min` is used at runtime. |
| Country, sector and entity scores | `risk_config` JSON maps | Module constants/manual lists | Database first; substring matching is order-sensitive for sector/entity. |
| Base/final score | `rule_engine.compute_risk_score` | None | Canonical deterministic scorer for submission/recompute. |
| EDD routing | `edd_routing_policy.evaluate_edd_routing` plus routing actuator | Supervisor/memo recommendations | Separate policy can floor a score to Medium/High. Routing errors are caught in some paths. |
| Persisted application risk | `applications.risk_score`, `base_risk_*`, `final_risk_level`, dimensions/escalations, version timestamp | Legacy `risk_level` and optional `final_risk_score` | Most authoritative display helpers prefer final level. Some legacy queries and Supervisor Agent 5 read raw `risk_level`. |
| Approval route | `security_hardening.classify_approval_route` and server gates | Memo/supervisor evidence | High/Very High requires two distinct approvers; escalated lower tiers require compliance. |
| Memo display | Application canonical snapshot injected into memo metadata | Memo's separate seven-factor aggregation | Display is overwritten from application risk, but memo reasoning can contain a different aggregate. |

Primary code trace: [`rule_engine.py`](../../arie-backend/rule_engine.py), [`db.py`](../../arie-backend/db.py), [`prescreening/risk_inputs.py`](../../arie-backend/prescreening/risk_inputs.py), [`edd_routing_policy.py`](../../arie-backend/edd_routing_policy.py), [`security_hardening.py`](../../arie-backend/security_hardening.py), [`memo_handler.py`](../../arie-backend/memo_handler.py), [`supervisor/agent_executors.py`](../../arie-backend/supervisor/agent_executors.py), [`periodic_review_engine.py`](../../arie-backend/periodic_review_engine.py) and [`periodic_review_risk_reassessment.py`](../../arie-backend/periodic_review_risk_reassessment.py).

### 3.2 Formula

The active seed contains five dimensions and 17 subcriteria:

```text
D1 Customer / Entity       30%
D2 Geographic              25%
D3 Product / Service       20%
D4 Industry / Sector       15%
D5 Delivery Channel        10%

weighted_average = Σ(dimension_score × dimension_weight)
normalized_score = round(((weighted_average - 1) / 3) × 100, 1)
```

The configured bands are Low 0–39.9, Medium 40–54.9, High 55–69.9 and Very High 70–100. Boundary reproduction returned Low at 39.99, Medium at 40, High at 55 and Very High at 70. This demonstrates that configured maxima are descriptive rather than operative: 39.99 still maps Low despite exceeding configured Low `max=39.9`, and 101 maps Very High.

### 3.3 Post-score floors and escalation

The score-based tier is retained as `base_risk_level`; deterministic rules can then raise `final_risk_level`:

- score-4/sanctions/FATF-call-for-action incorporation country → Very High/70;
- score-4/sanctions/FATF-call-for-action director or UBO nationality → Very High/70;
- declared/confirmed PEP → at least High/55;
- sector score 4 or high-risk keyword → at least High/55;
- incorporation country score 3+ → at least High/55;
- opaque/complex ownership keyword → at least High/55;
- material screening concern → at least High/55, with severe combinations → Very High/70;
- separate EDD routing or screening disposition can apply Medium or High floors during submission/recompute.

Any subfactor score 4, sector score 4, score ≥85, or added floor/escalation can require compliance approval. A Medium floor intentionally preserves the raw numeric score, so a record can be final Medium with a numeric score below 40.

## 4. Findings

Severity reflects possible impact on regulated decisions, not exploitability.

### RSM-01 — Critical — Order-sensitive substring lookups silently defeat configured values

`score_sector` and `_score_entity_type` iterate the configured dictionary in insertion order and return the first key contained anywhere in the incoming label. They do not prefer an exact match, longest match or controlled identifier.

Reproduced against the seeded canonical row:

| Portal/input label | First matched key | Actual | Explicit configured intent |
|---|---:|---:|---:|
| `Unregulated Fund / SPV` | `regulated` | 1 | `unregulated fund` = 4 / `spv` = 4 |
| `Regulated Fund (CIS / Licensed)` | `regulated` | 1 | `regulated fund` = 2 |
| `Precious Metals / Gems` | `precious` | 3 | `precious metals` = 4 |
| `Banking-as-a-Service` | `bank` | 1 | `banking` = 2 |
| `Private Banking` | `bank` | 1 | hardcoded fallback describes private banking as higher risk, but canonical DB has no precise label |

The first and third cases suppress score-4 escalation and the High sector floor. A clean `Unregulated Fund / SPV` profile reproduced as 7.3/Low; a clean precious-metals profile reproduced as 12.3/Low. The configured high-risk entries exist but are unreachable for these portal labels.

**Decision required:** freeze a canonical controlled vocabulary using stable IDs. Define exact-match and alias precedence, then longest-specific fallback. Independently enumerate every portal label against its intended score before implementation. Do not solve by merely reordering keys without a collision-proof contract.

### RSM-02 — Critical — Country configuration is stale and lacks an authoritative refresh control

The database seed is a manually maintained 102-key score map. A dormant country-risk governance snapshot mechanism exists, but the active scorer uses the manual `risk_config` map. There is no source URL, publication date, effective date, review due date, ingestion job, approval evidence or freshness gate attached to the active map.

The map does not match the FATF statements dated 19 June 2026. Examples include current increased-monitoring jurisdictions missing or not scored 3 (Angola, Bolivia, Bosnia and Herzegovina, Bulgaria, Côte d'Ivoire, Kuwait, Nepal and Papua New Guinea) and jurisdictions retained at 3 after removal from the current statement (for example Nigeria and South Africa). Missing countries generally default to 2. The hardcoded `FATF_BLACK` name also contains countries beyond FATF's call-for-action list, conflating FATF status with sanctions/other policy.

The scorer automatically floors every score-3 incorporation country to High/EDD. FATF expressly states that increased monitoring does not itself call for EDD and should inform a risk-based analysis. Consequently, stale membership can cause both under-control and over-control.

**Decision required:** select the regulator-specific authoritative sources and overlay policy; separate FATF call-for-action, increased monitoring, sanctions, corruption, secrecy and internal appetite signals; attach source/effective/review metadata; require dual approval; and fail closed on stale data according to a defined service level.

### RSM-03 — High — DCI-108 and DCI-109 can jointly cross the Low/Medium boundary

The complexity parser checks `complex` before `very complex`; therefore `Very complex` scores 3 rather than 4. The introduction parser checks `regulated` before `non-regulated`; therefore the exact portal label `Introduced by non-regulated intermediary` scores 1 rather than 3.

A reproduced boundary profile scored 36.0/Low. Applying only the documented intended factor values would add approximately 1.7 points for complexity and 3.3 points for introduction, producing 41.0/Medium. This is a concrete classification change, not a theoretical sensitivity.

**Decision required:** approve the intended controlled values and add exact portal-label tests plus threshold-crossing regression tests before implementing DCI-108/109.

### RSM-04 — High — DCI-110 over-scores the middle turnover band and creates a false escalation

The volume parser checks for `5,000,000` before `500,000`. The portal label `USD 500,000 to USD 5,000,000 per month` therefore scores 4, not the configured/model-intended 3. In a clean profile it produced 12.0 instead of approximately 9.7 and added `sub_factor_score_4`, making compliance approval mandatory even though the overall tier remained Low.

**Decision required:** define stable band IDs or numeric lower/upper bounds; do not infer bands from overlapping formatted text.

### RSM-05 — High — Active model changes lack an approved immutable lifecycle

An admin can PUT directly to the single active `risk_config` row. The endpoint performs useful semantic checks, audits before/after and recomputes active applications, but there is no draft, maker-checker approval, validation status, effective date, model owner, regulatory basis, tenant/regulator scope, immutable version, superseded record, rollback artefact or independent validation sign-off. Terminal applications are not recomputed and prior model contents are not retained.

The `updated_at` timestamp is used as a version string. It identifies when the row changed, not what exact settings were approved. Config updates can commit while failed application recomputations are quarantined. That protects approval but leaves an operational partial rollout.

**Decision required:** establish an immutable model-version/change-request lifecycle, signed validation evidence, controlled activation and rollback, and explicit treatment of active versus historic cases.

### RSM-06 — High — Missing or unresolved facts can produce reassuringly low model scores

Examples reproduced or traced:

- no directors/UBO nationalities → nationality factor 1;
- no intermediary → intermediary factor 1;
- no operating/target country → incorporation value is reused;
- missing adverse-media result → 1, documented as “assume clear”;
- unmapped country, sector or entity → 2;
- no UBO, failed IDV and missing documents are not inputs to `compute_risk_score`;
- submission checks for at least one director but does not make a UBO row a universal scoring prerequisite.

Missing source of wealth/funds correctly scores 3, and separate IDV/document/screening/approval gates mitigate several cases. The defect is that the displayed risk score does not distinguish “verified low risk” from “low because the factor is absent or handled elsewhere.” If a downstream gate is bypassed, not applicable or stale, the score remains reassuring.

**Decision required:** approve a data-quality/uncertainty policy. At minimum retain per-factor value, source, status, default reason and confidence; make unresolved required facts explicit in the authoritative decision contract.

### RSM-07 — High — Geographic treatment is materially asymmetric

An incorporation country scored 3 is floored to High. A director/UBO nationality scored 3 only changes the D2 weighted average; the clean profile with a Nigerian UBO remained 10.7/Low. A nationality floor applies only at score 4. Operating and target countries scored 3 also do not individually trigger the incorporation-country floor, although EDD routing may use broader facts depending on how they are built.

This may be a valid risk-appetite choice, but it is not documented as a deliberate policy and is inconsistent with the broad “countries/geographic areas” risk framing.

**Decision required:** risk owners must explicitly approve which party/corridor relationships trigger a floor, a weight, EDD, or a prohibition.

### RSM-08 — High — Ongoing monitoring does not automatically refresh authoritative risk

Monitoring alert creation/upsert does not call `recompute_risk`. The UI/pilot contract states that broad risk drift and transaction monitoring are not active. Periodic reassessment creates a suggestion and audit trail without mutating application risk. On completed, officer-confirmed change, the periodic engine recomputes and applies the higher of prior final, model and confirmed floors; automatic downgrade is prohibited.

This human-control design is defensible for a pilot, but an open material alert can coexist with an unchanged authoritative risk score until a separate disposition/review path acts. A failed model recomputation during periodic completion can still apply an officer tier while leaving the numeric score/config provenance inconsistent.

**Decision required:** define which monitoring events quarantine approval, force risk stale, open EDD, invoke recomputation or require a timed human decision. Document this as a limitation until implemented.

### RSM-09 — High — Canonical loader validation is shape-only, not semantic

`validate_risk_config` checks container shapes and numeric types for map values but not 1–4 ranges, weight totals/uniqueness, threshold numeric types/ranges/order/continuity, or threshold overlaps. Reproduction showed `sector_risk_scores={"x":99}` and malformed threshold content both pass this validator with zero errors. A non-numeric threshold then raises during classification. In staging/production the loader is described as fail-closed, but these malformed values are considered valid until used.

The admin PUT endpoint separately validates more semantics, including 1–4 score ranges and weight totals, but does not make the canonical row intrinsically safe from direct DB changes, migrations or corruption. It also does not enforce that adjacent `max`/`min` values are continuous, and runtime ignores `max` anyway.

**Decision required:** make one exhaustive validator mandatory at load, write, migration, activation and startup; validate the exact runtime semantics and run golden cases before activation.

### RSM-10 — High — Legacy blank model provenance is explicitly allowed through staleness

The approval staleness gate blocks stale sentinels, mismatched non-empty versions and config lookup failure. It explicitly allows a missing/blank `risk_config_version`, and also allows approval where no current config version exists. That preserves legacy cases but treats unknown provenance more permissively than known stale provenance.

**Decision required:** migrate/re-score legacy records or require a documented senior exception. Unknown model provenance should not silently pass as current.

### RSM-11 — High — Risk truth is not completely uniform across consumers

Most helpers prefer `final_risk_level`; however:

- several legacy dashboard/report queries still read `risk_level` directly;
- Supervisor Agent 5 begins from raw `app.risk_level`, then uses the memo's separate aggregate and only flags divergence greater than one tier;
- memo reasoning uses a separate seven-factor aggregation (`jurisdiction`, `business`, `transaction`, `ownership`, `fincrime`, `documentation`, `data_quality`) and takes the maximum of stored and memo-effective risk;
- the server overwrites memo display metadata with application canonical risk, but internal memo fields can retain the alternate aggregate;
- PEP narrative paths include legacy exact-string checks that may not mirror the canonical declaration helper.

One-tier disagreement can be reported as aligned, and narrative/routing can diverge from the displayed deterministic score.

**Decision required:** publish one authoritative risk-decision contract for score, tier, floors, route and evidence; label advisory assessments explicitly and test every consumer.

### RSM-12 — High — Historic decisions are not fully reproducible

The application stores a timestamp-like config version, dimensions, score, level and escalation text, but not the complete normalized factor inputs, per-factor scores, source/status/default reasons, country/sector/entity maps or an immutable config snapshot. Prescreening and party data are mutable. The evidence pack can reconstruct a base score from stored dimensions using the **current** configuration if a stored base is absent. Terminal applications are excluded from config-update recomputation.

**Decision required:** persist a content-addressed decision record containing normalized inputs, factor results, config/version, source provenance, floors, routing result, code/model version and actor/system timestamps.

### RSM-13 — High — Routing failures can be non-fatal after scoring

Submission and recomputation contain paths where EDD routing/audit failures are caught and logged while scoring/persistence continues. During recompute, failure of `_apply_edd_routing_floor_for_recompute` returns an empty routing result. A risk score can therefore be refreshed without the associated EDD floor/route if that subsystem fails.

**Decision required:** classify routing as decision-critical and fail closed or quarantine the application when route/floor computation cannot be proven.

### RSM-14 — Medium — Final tier and numeric score can intentionally disagree

High and Very High floors raise numeric score to 55/70. Medium floors preserve the raw score. Examples include `needs_more_information` screening dispositions or EDD triggers that carry a Medium minimum. Tests showed persisted Medium with score 23.6. This is documented in code but can confuse dashboards, exports, threshold validation and downstream systems that infer tier from number.

**Decision required:** either floor the number consistently or make `base_score`, `final_tier` and `floor_reason` mandatory and prohibit consumers from re-deriving tier.

### RSM-15 — Medium — Portal vocabulary is broader than the score taxonomy

The portal exposes labels such as investment management, capital markets/brokerage, private equity, family office, crowdfunding, lending, payment gateway, corporate services, travel, food, fashion, AI and cybersecurity. Many have no precise configured key and fall to a generic substring or default 2. The scoring settings are keywords, not a maintained mapping from every controlled UI option.

**Decision required:** approve a total mapping for every accepted option and a conservative, auditable policy for novel free text.

### RSM-16 — Medium — Existing tests overstate model validation coverage

The forensic test module says 18 subfactors, while the active model has 17. Several “Excel alignment” tests assert only broad ranges or presence of keys, not exact output. Seed tests prove entries exist, not that their portal labels reach those entries. No current test catches the RSM-01 collisions, and known DCI labels are not tested at exact factor values/boundaries.

**Decision required:** add an independently owned golden dataset, exact factor-level observability and mutation/boundary testing after policy approval.

## 5. Regulatory control comparison

### 5.1 Mauritius

The current [Mauritius FIAMLA, version 10](https://lawsofmauritius.govmu.org/portal/viewlegislationdocument/web/?docnumber=&doctitle=RmluYW5jaWFsIEludGVsbGlnZW5jZSBhbmQgQW50aS1Nb25leSBMYXVuZGVyaW5nIEFjdA%3D%3D&doctype=act) section 17 requires identification and assessment of customer, country/geographic, product/service/transaction and delivery-channel risks; consideration of all relevant factors; written, current assessments; and national/guidance inputs. D1–D5 broadly cover those headings. The implementation weakness is not absence of headings but stale taxonomies, implicit defaults and incomplete decision evidence.

Section 17A requires proportionate policies/controls, regular review/update, written change records and senior-management approval. The admin audit log and recompute process are useful, but the single-row direct-update model does not evidence an approved immutable lifecycle or independent validation.

Section 17H links FATF-identified significant/strategic deficiencies to enhanced CDD and proportionate additional measures for high-risk countries. RegMind's lack of current source metadata and its conflation of FATF, sanctions and internal policy prevent reliable evidence of alignment.

The [FSC AML/CFT Handbook](https://www.fscmauritius.org/media/131386/updated-aml-cft-handbook.pdf) calls for consideration of complexity, high volumes, non-face-to-face delivery and introducers, and for beneficial-owner identification/verification. The model contains corresponding factors, but DCI-108/109/110 and the no-UBO/missing-data behaviour undermine their execution. Its PEP floor, EDD route, source-of-wealth/funds collection and senior approval controls are comparatively strong.

Mauritius published a [Second National Risk Assessment in 2025](https://www.fscmauritius.org/en/aml/amlcft/national-money-laundering-and-terrorist-financing-risk-assessment-of-mauritius). No explicit model version/source record proves that its sectoral and emerging-risk findings were considered in the active seed.

### 5.2 United Kingdom and FATF reference points

UK [Money Laundering Regulations 2017](https://www.legislation.gov.uk/uksi/2017/692/pdfs/uksi_20170692_en.pdf) regulations 33 and 35 require EDD/enhanced monitoring for assessed high risk and PEPs, with senior approval, source-of-wealth/funds measures and enhanced ongoing monitoring for PEP relationships. The High/Very High dual-control and PEP floor/routing align directionally. The regulations also identify complex structures, private banking, anonymity, non-face-to-face delivery, nominee services and geographic sources as factors; several of those are affected by the taxonomy/parsing defects above.

The FATF publishes its jurisdiction statements three times a year. Its [19 June 2026 increased-monitoring statement](https://www.fatf-gafi.org/en/publications/High-risk-and-other-monitored-jurisdictions/increased-monitoring-june-2026.html) also says the grey list should inform risk analysis and does not itself call for EDD or wholesale de-risking. RegMind's universal High floor for score-3 incorporation countries therefore needs an approved regulator/risk-appetite basis rather than a generic “FATF grey” label.

### 5.3 Regulatory conclusion

The model is directionally aligned with common risk-based-factor and EDD concepts, but **directional alignment is not validation**. Production suitability requires current authoritative taxonomies, documented local legal interpretation, approved calibration, complete evidence, independent validation, change control and operational monitoring.

## 6. Recompute, staleness and decision controls

### 6.1 Recompute events traced

The reusable recompute path is invoked after material application edits, controlled prescreening corrections, officer corrections, KYC submission, screening completion/rerun/review disposition, confirmed periodic-review risk change, risk-config update for active cases and certain change-management implementations. It uses compare-and-set persistence and stamps failed config-update recomputations with `stale:recompute_failed`; change-management uses `stale:cm_recompute_pending:<id>`.

Positive controls:

- staging/production config read/missing/shape failure is intended to fail closed;
- stale sentinels and known version mismatches block approval;
- active config updates attempt bulk recomputation and quarantine failures;
- recomputation never automatically downgrades an EDD/periodic confirmed floor;
- formal screening dispositions are bridged into recompute floors;
- material score/level changes make memo evidence stale;
- High/Very High final approvals require two distinct approvers.

Residual weaknesses:

- routing failure can be logged and ignored;
- blank legacy config provenance passes;
- terminal cases are not replayed against new settings and old settings are not retained;
- monitoring alerts do not automatically mark risk stale;
- memo records without a legacy risk snapshot do not produce a mismatch;
- exact old inputs cannot be reconstructed reliably.

## 7. Validation performed

All commands were run against the audited commit on macOS using Python 3.11, the repository-supported minimum. The default system Python 3.9 was rejected as an invalid test environment because the project declares Python ≥3.11.

| Validation | Result |
|---|---|
| Seeded configuration inventory | 5 dimensions, 17 subcriteria, 4 bands, 102 country keys, 53 sector keys, 23 entity keys; 204 configured entries under the counting convention in the settings register. |
| Scenario reproduction | 39 documented scenario/control cases, including all requested scenarios and DCI/collision boundary cases. |
| Core score/config/recompute/staleness tests | 243 passed. |
| Approval, IDV, memo, display, periodic and monitoring tests | 199 passed. |
| Complete repository test collection | 6,964 passed, 49 skipped and 4 expected failures; 7,017 collected in 13m28s. |
| Boundary probe | 0/39.9/40/54.9/55/69.9/70/100 reproduced; 39.99 and 54.99 demonstrate ignored maxima; 101 remains Very High. |
| Semantic corruption probe | Out-of-range map score and malformed threshold content passed `validate_risk_config`; malformed threshold raised at classification. |
| Exact portal-label probes | Reproduced RSM-01 and DCI-108/109/110 outcomes. |

These are regression and forensic observations. They are not an independent statistical validation, back-test, fairness study, loss/outcome calibration or production data-quality assessment.

## 8. Required remediation sequence (no implementation in this review)

1. Obtain founder/risk/compliance decisions in the decision pack; name accountable model owner and independent validator.
2. Freeze canonical factor definitions, controlled option IDs, missing-data policy, floor semantics and jurisdiction source hierarchy.
3. Build an immutable versioned model package with maker-checker approval, effective dating, rollback and regulator/tenant overlays.
4. Correct exact/alias matching and DCI-108/109/110 only after policy approval; exhaustively map every portal option.
5. Add complete semantic validation and activation-time golden tests; fail closed on route computation and stale source data.
6. Persist a content-addressed decision record with normalized inputs, per-factor evidence, complete config and code version.
7. Define monitoring-to-risk-staleness/recompute/quarantine rules and service levels.
8. Perform independent quantitative validation using representative, outcome-linked data and document limitations.
9. Re-run the entire test suite, data migration rehearsal, rollback rehearsal and operational acceptance before any production claim.

## 9. Companion artefacts

- [Risk Scoring Settings Register](RISK_SCORING_SETTINGS_REGISTER.md)
- [Risk Scoring Scenario Matrix](RISK_SCORING_SCENARIO_MATRIX.md)
- [Founder Decision Pack](RISK_SCORING_FOUNDER_DECISION_PACK.md)
