# RSMP Tier 0D — Runtime/UI Reconciliation

Status: reconciled for the repository runtime and read-only Back Office projection. This is not a production-readiness claim.

## Scope and source of truth

The Back Office Risk Scoring Model page now receives one read-only projection from `GET /api/config/risk-model`. The handler loads the validated model with `rule_engine.load_risk_config`; the projection evaluates parser-owned scores with `rule_engine.compute_risk_score`, uses the same controlled registry, and imports the exact EDD, approval, screening-floor, and monitoring policy constants used at runtime. The UI contains no hardcoded score table and has no fallback model.

Application-detail CSV/PDF risk reports now consume `risk_report_evidence` produced by the application-detail API. That read-only evidence package joins the persisted application score, final tier, five stored dimensions, escalation/floor reasons, EDD lane, and backend approval route to the exact validated runtime configuration version. The browser does not score, recompute, weight, classify, infer, or substitute any risk value. A missing computation timestamp, missing/stale/mismatched configuration version, incomplete dimension evidence, missing route, or malformed runtime projection blocks export with a controlled message.

This repository snapshot was generated with `ENABLE_RSMP_TIER0A_MAPPING_FIDELITY=false`, matching the default activation state. The page reports the evaluated flag and parser mode dynamically; it does not activate, configure, recompute, or persist anything.

## Reconciliation summary

| Measure | Count | Disposition |
|---|---:|---|
| UI items reviewed | 348 | Complete |
| Active scoring entries matching runtime | 270 | Correct after runtime projection |
| Runtime rule rows matching runtime | 12 | Correct; 7 formerly hidden rules are now documented |
| Lane B rows | 22 | Excluded from active scoring and labelled pending calibration |
| Structural / policy items | 44 | 5 dimensions, 17 criteria, 4 thresholds, 9 EDD triggers, 4 approval routes, 5 monitoring cadences |
| Concrete stale-value defects corrected | 12 | 7 sector scores, 2 entity scores, 2 D3 criterion weights, and 1 PEP scoring description |
| Duplicated UI scoring definitions removed | 160 | 103 country-list entries, 26 sector rows, 12 entity rows, 15 scoring strings, and 4 thresholds |
| Editing controls removed | 6 | Model edit/save/cancel and country/sector/entity edit controls |
| Hidden runtime rules surfaced | 7 | Sector keyword, two country floors, two screening rules, unresolved-mapping block, composite-85 review |
| Browser risk calculators remaining | 0 | The CSV/PDF scorer and application-detail recomputation path were removed |
| Client-side model mutation handlers remaining | 0 | Runtime projection is recursively frozen; no model save/update handler exists in the page |

## Classification findings

- **Wrong score:** seven sector rows and two entity rows in the former UI disagreed with the active legacy parser; they are no longer stored in the UI.
- **Wrong wording:** the former PEP description assigned different scores by PEP type, although the approved runtime assigns every declared or officer-confirmed PEP score 4.
- **Duplicate / UI only / obsolete:** the hardcoded country, sector, entity, threshold, and scoring-description lists duplicated runtime configuration and were removed.
- **Runtime only:** validated runtime lookup keys not represented by a controlled label remain visible and are explicitly classified as runtime-only.
- **Hidden runtime rule:** seven enforced floor/review/block rules were absent from the old model page and are now shown.
- **Lane B:** 22 unresolved sector labels are shown only in the pending-calibration section and never as active scoring entries.

## Report/export and error-path reconciliation

| UI / export item | Runtime or authoritative source | Displayed/exported value | Match? | Action |
|---|---|---|---|---|
| Composite score | `applications.risk_score` via `risk_report_evidence` | Persisted score | Yes | Export directly; no browser calculation |
| Final tier | `applications.final_risk_level` / `risk_level` via `risk_report_evidence` | Persisted canonical tier | Yes | Export directly; no browser threshold table |
| D1–D5 scores | `applications.risk_dimensions` via `risk_report_evidence` | Persisted dimension values | Yes | Export directly; no weighted contribution calculation |
| D3 criterion weights | validated `risk_config.dimensions[D3].subcriteria` | 40 / 35 / 25 | Yes | Runtime configuration reference only |
| Declared PEP policy evidence | stored `pep_declaration.pep_role_type` + `rule_engine.GATE0_DECLARED_PEP_SCORE` | 4 | Yes | Runtime-owned policy evidence; no role-specific browser score |
| High floors | stored `risk_escalations` entries beginning `floor_rule_` | Persisted floor reasons | Yes | Export directly |
| EDD / onboarding route | `applications.onboarding_lane` | Persisted lane | Yes | Export directly |
| Approval route | `security_hardening.classify_approval_route` | Backend-classified route and reasons | Yes | Export directly |
| Missing or stale evidence | API evidence validator | Export blocked | Yes | Fail closed; no fallback values |
| Malformed runtime config | typed `RiskModelProjectionUnavailable` at API boundary | Controlled HTTP 503 | Yes | No `StopIteration`, traceback, or fallback substitution |

## Mandatory rule presentation

| Rule | Runtime result | Reconciliation |
|---|---|---|
| Sector score 4 | High floor | Displayed from runtime rule |
| Opaque ownership | High floor | Displayed from runtime rule |
| Declared or officer-confirmed PEP | Score 4 and High floor | Displayed from runtime rule and structured `pep_declaration.pep_role_type` evidence |
| Monthly volume score 4 | Compliance Review; no automatic High floor | Displayed from exact runtime reason/effect |
| Unsolicited / unknown referral | Score 4 only; no automatic High floor | Displayed from runtime parser |

## Remaining Tier 1 work

- Tier 1A sector redesign remains out of scope.
- The 22 Lane B sector labels remain pending calibration.
- The 130 deferred countries and 19 regions remain Tier 1B; this PR does not invent or activate geography scores.
- Governed editing, model versions, maker-checker, effective dating, rollback, draft models, and activation workflow remain future work.

## Full reconciliation table

| UI Item | Runtime Source | Runtime Score | UI Score | Match? | Action |
|---|---|---:|---:|---|---|
| sector — Fintech / Payments | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Forex / FX Trading (Retail) | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Forex / FX Trading (Institutional) | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Crypto / Digital Assets Exchange | rule_engine.score_sector + risk_config.sector_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| sector — Crypto / Digital Assets Custody | rule_engine.score_sector + risk_config.sector_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| sector — Crypto / Web3 / DeFi | rule_engine.score_sector + risk_config.sector_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| sector — Remittance / Money Transfer | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — E-Money / E-Wallet Provider | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Insurance / InsurTech | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Investment Management | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Family Office / Wealth Management | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Private Banking | rule_engine.score_sector + risk_config.sector_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| sector — Banking-as-a-Service | rule_engine.score_sector + risk_config.sector_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| sector — E-Commerce / Online Retail | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Import / Export | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Precious Metals / Gems | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Oil & Gas / Energy Trading | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Logistics / Freight Forwarding | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Software / SaaS | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Cloud Services | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Telecommunications | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Media Technology | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — iGaming / Online Gambling | rule_engine.score_sector + risk_config.sector_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| sector — Online Casino / Sports Betting | rule_engine.score_sector + risk_config.sector_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| sector — NFT / Gaming Assets | rule_engine.score_sector + risk_config.sector_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| sector — Entertainment / Media | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — MSB / Money Services Business | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Law Firm / Legal Services | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Accounting / Audit Firm | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Real Estate (Commercial) | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Real Estate (Development) | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Management Consulting | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Financial / Tax Advisory | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Healthcare / MedTech | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Education / EdTech | rule_engine.score_sector + risk_config.sector_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| sector — Manufacturing | rule_engine.score_sector + risk_config.sector_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| sector — Construction | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Charity / NGO / Non-Profit | rule_engine.score_sector + risk_config.sector_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| sector — Government / Public Sector | rule_engine.score_sector + risk_config.sector_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| sector — advisory | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — agriculture | risk_config.sector_risk_scores | 1 | 1 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — arms | risk_config.sector_risk_scores | 4 | 4 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — bank | risk_config.sector_risk_scores | 1 | 1 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — consulting | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — defence | risk_config.sector_risk_scores | 4 | 4 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — energy | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — export | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — gas | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — listed company | risk_config.sector_risk_scores | 1 | 1 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — maritime | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — military | risk_config.sector_risk_scores | 4 | 4 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — mining | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — ngo | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — nominee | risk_config.sector_risk_scores | 4 | 4 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — non-profit | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — precious | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — regulated financial | risk_config.sector_risk_scores | 1 | 1 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — retail | risk_config.sector_risk_scores | 2 | 2 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — saas | risk_config.sector_risk_scores | 2 | 2 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — shell company | risk_config.sector_risk_scores | 4 | 4 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — shipping | risk_config.sector_risk_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — technology | risk_config.sector_risk_scores | 2 | 2 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| sector — virtual asset | risk_config.sector_risk_scores | 4 | 4 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — Listed Company on Regulated Exchange | rule_engine._score_entity_type + risk_config.entity_type_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| entity_type — Regulated Financial Institution | rule_engine._score_entity_type + risk_config.entity_type_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| entity_type — Government / Public Sector Entity | rule_engine._score_entity_type + risk_config.entity_type_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| entity_type — Large Private Company (revenue > USD 10m) | rule_engine._score_entity_type + risk_config.entity_type_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| entity_type — SME / Private Company | rule_engine._score_entity_type + risk_config.entity_type_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| entity_type — Newly Incorporated Company (< 1 year) | rule_engine._score_entity_type + risk_config.entity_type_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| entity_type — Trust | rule_engine._score_entity_type + risk_config.entity_type_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| entity_type — Foundation | rule_engine._score_entity_type + risk_config.entity_type_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| entity_type — Regulated Fund (CIS / Licensed) | rule_engine._score_entity_type + risk_config.entity_type_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| entity_type — Unregulated Fund / SPV | rule_engine._score_entity_type + risk_config.entity_type_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| entity_type — Non-Profit Organisation / NGO | rule_engine._score_entity_type + risk_config.entity_type_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| entity_type — Shell Company / Special Purpose Vehicle | rule_engine._score_entity_type + risk_config.entity_type_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| entity_type — government body | risk_config.entity_type_scores | 1 | 1 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — large private | risk_config.entity_type_scores | 2 | 2 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — listed | risk_config.entity_type_scores | 1 | 1 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — ngo | risk_config.entity_type_scores | 3 | 3 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — private company | risk_config.entity_type_scores | 2 | 2 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — public sector | risk_config.entity_type_scores | 1 | 1 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — regulated | risk_config.entity_type_scores | 1 | 1 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — regulated entity | risk_config.entity_type_scores | 1 | 1 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — regulated fi | risk_config.entity_type_scores | 1 | 1 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — shell | risk_config.entity_type_scores | 4 | 4 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| entity_type — spv | risk_config.entity_type_scores | 4 | 4 | Yes | Runtime only — Show as a runtime-only configured lookup key |
| ownership — Simple — direct identifiable UBOs | rule_engine.compute_risk_score (ownership parser) | 1 | 1 | Yes | Correct — Display from runtime projection |
| ownership — 1–2 ownership layers | rule_engine.compute_risk_score (ownership parser) | 2 | 2 | Yes | Correct — Display from runtime projection |
| ownership — 3+ ownership layers / nominee shareholders | rule_engine.compute_risk_score (ownership parser) | 3 | 3 | Yes | Correct — Display from runtime projection |
| ownership — Opaque — UBOs cannot be fully identified | rule_engine.compute_risk_score (ownership parser) | 4 | 4 | Yes | Correct — Display from runtime projection |
| complexity — Simple — single currency, domestic corridors | rule_engine.compute_risk_score (complexity parser) | 1 | 1 | Yes | Correct — Display from runtime projection |
| complexity — Standard — multi-currency, established corridors | rule_engine.compute_risk_score (complexity parser) | 2 | 2 | Yes | Correct — Display from runtime projection |
| complexity — Complex — multiple international corridors | rule_engine.compute_risk_score (complexity parser) | 3 | 3 | Yes | Correct — Display from runtime projection |
| complexity — Very complex — includes monitored corridors | rule_engine.compute_risk_score (complexity parser) | 3 | 3 | Yes | Correct — Display from runtime projection |
| introduction — Direct application — client initiated | rule_engine.compute_risk_score (introduction parser) | 1 | 1 | Yes | Correct — Display from runtime projection |
| introduction — Introduced by regulated intermediary / agent | rule_engine.compute_risk_score (introduction parser) | 1 | 1 | Yes | Correct — Display from runtime projection |
| introduction — Introduced by non-regulated intermediary | rule_engine.compute_risk_score (introduction parser) | 1 | 1 | Yes | Correct — Display from runtime projection |
| introduction — Unsolicited / unknown referral source | rule_engine.compute_risk_score (introduction parser) | 4 | 4 | Yes | Correct — Display from runtime projection |
| monthly_volume — Under USD 50,000 per month | rule_engine.compute_risk_score (monthly_volume parser) | 2 | 2 | Yes | Correct — Display from runtime projection |
| monthly_volume — USD 50,000 to USD 500,000 per month | rule_engine.compute_risk_score (monthly_volume parser) | 3 | 3 | Yes | Correct — Display from runtime projection |
| monthly_volume — USD 500,000 to USD 5,000,000 per month | rule_engine.compute_risk_score (monthly_volume parser) | 4 | 4 | Yes | Correct — Display from runtime projection |
| monthly_volume — Over USD 5,000,000 per month | rule_engine.compute_risk_score (monthly_volume parser) | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — afghanistan | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — algeria | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — australia | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — austria | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — bahrain | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — belarus | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — belgium | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — bermuda | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — botswana | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — brazil | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — british virgin islands | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — burkina faso | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — bvi | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — cameroon | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — canada | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — cayman islands | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — chile | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — china | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — crimea | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — cuba | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — democratic republic of congo | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — denmark | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — eritrea | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — estonia | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — finland | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — france | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — germany | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — ghana | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — guernsey | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — haiti | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — hong kong | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — iceland | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — india | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — indonesia | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — iran | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — iraq | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — ireland | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — isle of man | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — israel | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — italy | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — ivory coast | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — japan | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — jersey | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — jordan | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — kenya | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — kuwait | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — laos | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — lebanon | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — libya | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — liechtenstein | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — luxembourg | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — malaysia | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — mali | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — marshall islands | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — mauritius | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — mexico | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — monaco | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — morocco | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — mozambique | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — myanmar | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — netherlands | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — new zealand | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — nigeria | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — north korea | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — norway | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — oman | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — pakistan | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — panama | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — philippines | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — portugal | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — qatar | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — russia | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — rwanda | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — samoa | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — saudi arabia | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — senegal | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — seychelles | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — singapore | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — somalia | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — south africa | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — south korea | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — south sudan | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — spain | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — sri lanka | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — sudan | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — sweden | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — switzerland | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — syria | rule_engine.classify_country + risk_config.country_risk_scores | 4 | 4 | Yes | Correct — Display from runtime projection |
| country — taiwan | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — tanzania | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — tunisia | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — turkey | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — uae | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — uganda | rule_engine.classify_country + risk_config.country_risk_scores | 2 | 2 | Yes | Correct — Display from runtime projection |
| country — uk | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — united kingdom | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — united states | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — usa | rule_engine.classify_country + risk_config.country_risk_scores | 1 | 1 | Yes | Correct — Display from runtime projection |
| country — vanuatu | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — venezuela | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — vietnam | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| country — yemen | rule_engine.classify_country + risk_config.country_risk_scores | 3 | 3 | Yes | Correct — Display from runtime projection |
| pep — Any declared or officer-confirmed PEP role | rule_engine._declared_pep_score_evidence | 4 | 4 | Yes | Correct — Display from runtime projection |
| adverse_media — confirmed | rule_engine.compute_risk_score + rule_engine.ADVERSE_MEDIA_SCORE_4_KEYWORDS | 4 | 4 | Yes | Correct — Display from runtime projection |
| adverse_media — regulatory | rule_engine.compute_risk_score + rule_engine.ADVERSE_MEDIA_SCORE_4_KEYWORDS | 4 | 4 | Yes | Correct — Display from runtime projection |
| adverse_media — criminal | rule_engine.compute_risk_score + rule_engine.ADVERSE_MEDIA_SCORE_4_KEYWORDS | 4 | 4 | Yes | Correct — Display from runtime projection |
| adverse_media — minor | rule_engine.compute_risk_score + rule_engine.ADVERSE_MEDIA_SCORE_2_KEYWORDS | 2 | 2 | Yes | Correct — Display from runtime projection |
| adverse_media — unsubstantiated | rule_engine.compute_risk_score + rule_engine.ADVERSE_MEDIA_SCORE_2_KEYWORDS | 2 | 2 | Yes | Correct — Display from runtime projection |
| adverse_media — clear | rule_engine.compute_risk_score + rule_engine.ADVERSE_MEDIA_CLEAR_VALUES | 1 | 1 | Yes | Correct — Display from runtime projection |
| adverse_media — none | rule_engine.compute_risk_score + rule_engine.ADVERSE_MEDIA_CLEAR_VALUES | 1 | 1 | Yes | Correct — Display from runtime projection |
| adverse_media — no | rule_engine.compute_risk_score + rule_engine.ADVERSE_MEDIA_CLEAR_VALUES | 1 | 1 | Yes | Correct — Display from runtime projection |
| adverse_media — Default branch (unrecognised or missing) | rule_engine.compute_risk_score + rule_engine.compute_risk_score adverse-media default | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_wealth — business revenue | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_wealth — trading profits | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_wealth — investment | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_wealth — dividends | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_wealth — government funding | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_wealth — grants | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_wealth — sale of assets | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_wealth — property | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_wealth — venture capital | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_wealth — investor funding | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_wealth — inheritance | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_wealth — family wealth | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_wealth — loan | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_wealth — credit | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_wealth — other | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_SCORE_MAP | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_wealth — information not provided | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_UNKNOWN_VALUES | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_wealth — not provided | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_UNKNOWN_VALUES | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_wealth — unknown | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_WEALTH_UNKNOWN_VALUES | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_wealth — Default unmatched declared value | rule_engine.compute_risk_score + rule_engine.compute_risk_score declared-value default | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_funds — company bank | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_funds — parent company | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_funds — group entity | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_funds — client payments | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_funds — receivables | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_funds — revenue | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_funds — business operations | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 1 | 1 | Yes | Correct — Display from runtime projection |
| source_of_funds — shareholder | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_funds — director | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_funds — capital injection | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_funds — investment round | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_funds — fundraise | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_funds — sale of assets | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 2 | 2 | Yes | Correct — Display from runtime projection |
| source_of_funds — loan | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_funds — credit facility | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_funds — other | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_SCORE_MAP | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_funds — information not provided | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_UNKNOWN_VALUES | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_funds — not provided | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_UNKNOWN_VALUES | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_funds — unknown | rule_engine.compute_risk_score + rule_engine.SOURCE_OF_FUNDS_UNKNOWN_VALUES | 3 | 3 | Yes | Correct — Display from runtime projection |
| source_of_funds — Default unmatched declared value | rule_engine.compute_risk_score + rule_engine.compute_risk_score declared-value default | 2 | 2 | Yes | Correct — Display from runtime projection |
| service_type — domestic + single | rule_engine.compute_risk_score + rule_engine.SERVICE_DOMESTIC_REQUIRED_KEYWORDS | 1 | 1 | Yes | Correct — Display from runtime projection |
| service_type — multi-currency | rule_engine.compute_risk_score + rule_engine.SERVICE_SCORE_2_KEYWORDS | 2 | 2 | Yes | Correct — Display from runtime projection |
| service_type — multi currency | rule_engine.compute_risk_score + rule_engine.SERVICE_SCORE_2_KEYWORDS | 2 | 2 | Yes | Correct — Display from runtime projection |
| service_type — cross-border | rule_engine.compute_risk_score + rule_engine.SERVICE_SCORE_3_KEYWORDS | 3 | 3 | Yes | Correct — Display from runtime projection |
| service_type — international | rule_engine.compute_risk_score + rule_engine.SERVICE_SCORE_3_KEYWORDS | 3 | 3 | Yes | Correct — Display from runtime projection |
| service_type — cross_border = true | rule_engine.compute_risk_score + rule_engine.compute_risk_score cross_border branch | 3 | 3 | Yes | Correct — Display from runtime projection |
| service_type — Default unmatched or missing value | rule_engine.compute_risk_score + rule_engine.compute_risk_score service default | 2 | 2 | Yes | Correct — Display from runtime projection |
| delivery_channel — face-to-face | rule_engine.compute_risk_score + rule_engine.DELIVERY_SCORE_1_KEYWORDS | 1 | 1 | Yes | Correct — Display from runtime projection |
| delivery_channel — in-person | rule_engine.compute_risk_score + rule_engine.DELIVERY_SCORE_1_KEYWORDS | 1 | 1 | Yes | Correct — Display from runtime projection |
| delivery_channel — in person | rule_engine.compute_risk_score + rule_engine.DELIVERY_SCORE_1_KEYWORDS | 1 | 1 | Yes | Correct — Display from runtime projection |
| delivery_channel — video | rule_engine.compute_risk_score + rule_engine.DELIVERY_SCORE_2_KEYWORDS | 2 | 2 | Yes | Correct — Display from runtime projection |
| delivery_channel — non-face | rule_engine.compute_risk_score + rule_engine.DELIVERY_REMOTE_KEYWORDS | 2 | 2 | Yes | Correct — Display from runtime projection |
| delivery_channel — remote | rule_engine.compute_risk_score + rule_engine.DELIVERY_REMOTE_KEYWORDS | 2 | 2 | Yes | Correct — Display from runtime projection |
| delivery_channel — anonymous | rule_engine.compute_risk_score + rule_engine.DELIVERY_SCORE_4_KEYWORDS | 4 | 4 | Yes | Correct — Display from runtime projection |
| delivery_channel — unverified | rule_engine.compute_risk_score + rule_engine.DELIVERY_SCORE_4_KEYWORDS | 4 | 4 | Yes | Correct — Display from runtime projection |
| delivery_channel — remote + incorporation country score >= 3 | rule_engine.compute_risk_score + rule_engine.DELIVERY_REMOTE_KEYWORDS + classify_country | 3 | 3 | Yes | Correct — Display from runtime projection |
| delivery_channel — Default unmatched or missing value | rule_engine.compute_risk_score + rule_engine.compute_risk_score delivery default | 2 | 2 | Yes | Correct — Display from runtime projection |
| High floor — Sector score 4 | rule_engine._is_high_risk_sector + apply_local_floor | Rule | Rule | Yes | Correct — display Minimum final risk HIGH |
| High floor — High-risk sector keyword match | rule_engine.HIGH_RISK_SECTOR_KEYWORDS | Rule | Rule | Yes | Hidden runtime rule — display Minimum final risk HIGH |
| High floor — Opaque ownership | rule_engine._is_opaque_ownership + apply_local_floor | Rule | Rule | Yes | Correct — display Minimum final risk HIGH |
| High floor — Declared or officer-confirmed PEP | rule_engine._declared_pep_score_evidence + apply_local_floor | Rule | Rule | Yes | Correct — display Score 4 and minimum final risk HIGH |
| Compliance review — Monthly volume score 4 | rule_engine.compute_risk_score + security_hardening.classify_approval_route | Rule | Rule | Yes | Correct — display Compliance Review; no automatic tier floor |
| No automatic floor — Unsolicited / unknown referral source | rule_engine.compute_risk_score introduction parser | Rule | Rule | Yes | Correct — display Score 4 only; no automatic HIGH floor |
| High floor — Country score 3 or higher | rule_engine._is_elevated_jurisdiction + apply_local_floor | Rule | Rule | Yes | Hidden runtime rule — display Minimum final risk HIGH |
| Very High floor — Country score 4 or sanctioned/FATF-black incorporation country | rule_engine._country_triggers_very_high_floor | Rule | Rule | Yes | Hidden runtime rule — display Minimum final risk VERY_HIGH |
| Screening floor — Material unresolved screening concern | rule_engine._has_material_screening_concern | Rule | Rule | Yes | Hidden runtime rule — display Minimum final risk HIGH; EDD trigger |
| Screening floor — High-risk sector + elevated jurisdiction + screening concern, or multiple concerns | rule_engine.compute_risk_score elevation rule 3 | Rule | Rule | Yes | Hidden runtime rule — display Minimum final risk VERY_HIGH |
| Approval block — Unresolved controlled mapping sentinel | risk_controlled_values.reconcile_mapping_staleness + security_hardening.classify_approval_route | Rule | Rule | Yes | Hidden runtime rule — display Approval blocked until all unresolved mappings are cleared |
| Compliance review — Composite score 85 or above | rule_engine.compute_risk_score escalation rule C | Rule | Rule | Yes | Hidden runtime rule — display Compliance approval required |
| sector — Agricultural Commodities | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Artificial Intelligence / ML | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Bureau de Change | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Capital Markets / Brokerage | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Commodities Trading | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Corporate Services Provider | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Crowdfunding / P2P Lending | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Cybersecurity | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Fashion / Luxury Goods | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Food & Beverage | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Hedge Fund | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — IT Services / Outsourcing | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Lending / Credit Services | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Licensed Brokerage | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Other | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Payment Processing / Gateway | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Private Equity / Venture Capital | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Streaming / Content Platforms | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Travel & Hospitality | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Trust / Fiduciary Services | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Video Games / Esports | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| sector — Wholesale / Distribution | risk_controlled_values.UNRESOLVED_SECTOR_LABELS | — | — | N/A | Lane B — exclude from active scoring; pending RSMP calibration |
| Dimension D1 — Customer / Entity Risk | risk_config.dimensions via rule_engine.load_risk_config | 30% | 30% | Yes | Correct — display runtime dimension weight |
| D1 criterion — Entity Type | risk_config.dimensions via rule_engine.load_risk_config | 20% | 20% | Yes | Correct — display runtime criterion label and weight |
| D1 criterion — Ownership Structure | risk_config.dimensions via rule_engine.load_risk_config | 20% | 20% | Yes | Correct — display runtime criterion label and weight |
| D1 criterion — PEP Status | risk_config.dimensions via rule_engine.load_risk_config | 25% | 25% | Yes | Correct — display runtime criterion label and weight |
| D1 criterion — Adverse Media | risk_config.dimensions via rule_engine.load_risk_config | 15% | 15% | Yes | Correct — display runtime criterion label and weight |
| D1 criterion — Source of Wealth | risk_config.dimensions via rule_engine.load_risk_config | 10% | 10% | Yes | Correct — display runtime criterion label and weight |
| D1 criterion — Source of Funds | risk_config.dimensions via rule_engine.load_risk_config | 10% | 10% | Yes | Correct — display runtime criterion label and weight |
| Dimension D2 — Geographic Risk | risk_config.dimensions via rule_engine.load_risk_config | 25% | 25% | Yes | Correct — display runtime dimension weight |
| D2 criterion — Country of Incorporation | risk_config.dimensions via rule_engine.load_risk_config | 25% | 25% | Yes | Correct — display runtime criterion label and weight |
| D2 criterion — UBO Nationalities | risk_config.dimensions via rule_engine.load_risk_config | 20% | 20% | Yes | Correct — display runtime criterion label and weight |
| D2 criterion — Intermediary Shareholder Jurisdictions | risk_config.dimensions via rule_engine.load_risk_config | 20% | 20% | Yes | Correct — display runtime criterion label and weight |
| D2 criterion — Countries of Operation | risk_config.dimensions via rule_engine.load_risk_config | 20% | 20% | Yes | Correct — display runtime criterion label and weight |
| D2 criterion — Target Markets | risk_config.dimensions via rule_engine.load_risk_config | 15% | 15% | Yes | Correct — display runtime criterion label and weight |
| Dimension D3 — Product / Service Risk | risk_config.dimensions via rule_engine.load_risk_config | 20% | 20% | Yes | Correct — display runtime dimension weight |
| D3 criterion — Service Type | risk_config.dimensions via rule_engine.load_risk_config | 40% | 40% | Yes | Correct — display runtime criterion label and weight |
| D3 criterion — Monthly Volume | risk_config.dimensions via rule_engine.load_risk_config | 35% | 35% | Yes | Correct — display runtime criterion label and weight |
| D3 criterion — Transaction Complexity | risk_config.dimensions via rule_engine.load_risk_config | 25% | 25% | Yes | Correct — display runtime criterion label and weight |
| Dimension D4 — Industry / Sector Risk | risk_config.dimensions via rule_engine.load_risk_config | 15% | 15% | Yes | Correct — display runtime dimension weight |
| D4 criterion — Industry Sector | risk_config.dimensions via rule_engine.load_risk_config | 100% | 100% | Yes | Correct — display runtime criterion label and weight |
| Dimension D5 — Delivery Channel Risk | risk_config.dimensions via rule_engine.load_risk_config | 10% | 10% | Yes | Correct — display runtime dimension weight |
| D5 criterion — Introduction Method | risk_config.dimensions via rule_engine.load_risk_config | 50% | 50% | Yes | Correct — display runtime criterion label and weight |
| D5 criterion — Delivery Channel | risk_config.dimensions via rule_engine.load_risk_config | 50% | 50% | Yes | Correct — display runtime criterion label and weight |
| Threshold — LOW | risk_config.thresholds via rule_engine.load_risk_config | 0–39.9 | 0–39.9 | Yes | Correct — display runtime classification boundary |
| Threshold — MEDIUM | risk_config.thresholds via rule_engine.load_risk_config | 40–54.9 | 40–54.9 | Yes | Correct — display runtime classification boundary |
| Threshold — HIGH | risk_config.thresholds via rule_engine.load_risk_config | 55–69.9 | 55–69.9 | Yes | Correct — display runtime classification boundary |
| Threshold — VERY_HIGH | risk_config.thresholds via rule_engine.load_risk_config | 70–100 | 70–100 | Yes | Correct — display runtime classification boundary |
| EDD trigger — high_or_very_high_risk | edd_routing_policy.evaluate_edd_routing | Trigger | Trigger | Yes | Correct — display runtime EDD trigger |
| EDD trigger — declared_pep_present | edd_routing_policy.evaluate_edd_routing | Trigger | Trigger | Yes | Correct — display runtime EDD trigger |
| EDD trigger — high_risk_sector | edd_routing_policy.evaluate_edd_routing | Trigger | Trigger | Yes | Correct — display runtime EDD trigger |
| EDD trigger — crypto_or_virtual_asset_sector | edd_routing_policy.evaluate_edd_routing | Trigger | Trigger | Yes | Correct — display runtime EDD trigger |
| EDD trigger — elevated_jurisdiction | edd_routing_policy.evaluate_edd_routing | Trigger | Trigger | Yes | Correct — display runtime EDD trigger |
| EDD trigger — opaque_or_incomplete_ownership | edd_routing_policy.evaluate_edd_routing | Trigger | Trigger | Yes | Correct — display runtime EDD trigger |
| EDD trigger — supervisor_mandatory_escalation | edd_routing_policy.evaluate_edd_routing | Trigger | Trigger | Yes | Correct — display runtime EDD trigger |
| EDD trigger — material_screening_concern | edd_routing_policy.evaluate_edd_routing | Trigger | Trigger | Yes | Correct — display runtime EDD trigger |
| EDD trigger — incomplete_contract | edd_routing_policy.evaluate_edd_routing | Trigger | Trigger | Yes | Correct — display runtime EDD trigger |
| Approval route — direct_low_medium | security_hardening.classify_approval_route | Route | Route | Yes | Correct — display runtime approval route |
| Approval route — compliance_required | security_hardening.classify_approval_route | Route | Route | Yes | Correct — display runtime approval route |
| Approval route — dual_control_required | security_hardening.classify_approval_route | Route | Route | Yes | Correct — display runtime approval route |
| Approval route — blocked | security_hardening.classify_approval_route | Route | Route | Yes | Correct — display runtime approval route |
| Monitoring cadence — LOW | periodic_review_policy.policy_snapshot_for_application | 36 months | 36 months | Yes | Correct — display post-approval review cadence; does not change initial score |
| Monitoring cadence — MEDIUM | periodic_review_policy.policy_snapshot_for_application | 24 months | 24 months | Yes | Correct — display post-approval review cadence; does not change initial score |
| Monitoring cadence — HIGH | periodic_review_policy.policy_snapshot_for_application | 12 months | 12 months | Yes | Correct — display post-approval review cadence; does not change initial score |
| Monitoring cadence — VERY_HIGH | periodic_review_policy.policy_snapshot_for_application | 6 months | 6 months | Yes | Correct — display post-approval review cadence; does not change initial score |
| Monitoring cadence — enhanced-review floor | periodic_review_policy.policy_snapshot_for_application | 12 months | 12 months | Yes | Correct — display enhanced-review cadence floor; does not change initial score |

## Validation statement

The exhaustive tests independently recompute every displayed score against the runtime scorer with the activation flag both OFF and ON, verify every controlled label/config key, assert every floor and adjacent policy against its runtime source, exclude Lane B, and prove the page has no editor, PUT call, hardcoded score list, or silent fallback. Extracting inline parser maps into immutable runtime-owned constants is a source-of-truth refactor only; the scorer consumes the same values and ordering and its scoring behaviour is unchanged.
