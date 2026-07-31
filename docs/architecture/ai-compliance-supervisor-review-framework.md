# AI Compliance Supervisor — Independent Review Framework (Phase 0A)

**Status:** Design only. No production code, schema, routes, flags or UI.
**Phase:** 0A — product and control architecture
**Supersedes in part:** [`challenge-mode-spec.md`](./challenge-mode-spec.md) (see §14.1)
**Audience:** founder review, prior to Phase 0B build authorisation

---

## 1. Executive product definition

### 1.1 What the Supervisor is

The AI Compliance Supervisor is an **independent reviewer of compliance
decisions**. It occupies the seat a second Senior Compliance Officer would
occupy: someone who did not work the case, has no stake in the outcome, and
reads the file looking for what is wrong with it.

> RegMind does not merely automate compliance decisions.
> RegMind independently challenges every compliance decision before the
> regulator does.

Challenge Mode is the **first capability** inside the Supervisor, not the
Supervisor itself. The Supervisor is a long-lived product surface that will
also carry institution memory, continuous customer supervision, portfolio
control intelligence, policy replay, and inspection readiness. Phase 0A defines
the subject model, finding taxonomy, evidence model and output contract that
all of those share.

### 1.2 The acceptance benchmark

A competent Senior Compliance Officer reads the output and thinks:

> *"I had not identified that issue."*

Any output that does not clear this bar is noise, and noise in a compliance
tool is worse than absence: it trains officers to dismiss the panel.

Section 12 applies this test explicitly and removes every candidate output that
fails it.

### 1.3 What the Supervisor is not

| Not | Why it is excluded |
|---|---|
| A summariser | The case data is already on screen. Restating it consumes officer attention and returns nothing. |
| A chatbot | Conversation is not a control. A control has a defined trigger, a defined output, and an audit trail. |
| A generic AI opinion | An opinion that cannot be reproduced cannot be evidenced, validated, or defended. |
| A duplicate risk score | `rule_engine.compute_risk_score()` is authoritative. A second score creates an unresolvable conflict at approval time. |
| A light feedback panel | "Consider further review" is not a finding. Every output must name a defect, its evidence, and its closure condition. |
| An LLM-generated approval recommendation | Decision authority stays with the human and the existing gates. The Supervisor has no vote. |
| A restatement of an existing screen | Explicitly tested and excluded in §12.2. |

### 1.4 The load-bearing design property

**Findings are deterministic. Prose is generated.**

The Supervisor computes findings from a versioned, pure probe engine and hashes
them. An LLM — when introduced in a later phase — receives *only the finding
list* and writes sentences about it. It cannot create, remove, re-score, or
reclassify a finding.

This is what makes the Supervisor defensible where a competitor's multi-agent
LLM panel is not: the same case reviewed twice yields a byte-identical finding
set, which can be validated under a model-risk framework, evidenced in an
inspection, and re-run years later.

---

## 2. Supervisor role and boundaries

### 2.1 Authority model

| Component | Authority | Supervisor's relationship to it |
|---|---|---|
| Sumsub IDV / `sumsub_idv_status.py` | **Authoritative** for identity verification | Reviews whether a resolution exists for every party and whether it was relied upon |
| Document OCR / `document_verification.py` | **Authoritative** for extraction | Reviews whether extraction succeeded and whether results were superseded |
| Screening provider (CA Mesh / Sumsub) | **Authoritative** for match data | Reviews terminality, provider mode, freshness, and disposition — never re-screens |
| `rule_engine.compute_risk_score()` | **Authoritative** for risk score and level | Reviews whether inputs resolved cleanly and whether the score is evidenced — never recomputes a competing score |
| `edd_routing_policy.evaluate_edd_routing()` | **Authoritative** for EDD routing | Re-evaluates the *same pure function* on the *same facts* to detect divergence between policy and actual route |
| `memo_handler.build_compliance_memo()` | **Authoritative** for the memo | Reviews whether memo conclusions are supported and whether commitments were actioned |
| `supervisor_engine.run_memo_supervisor()` | **Authoritative** consistency control (`can_approve`, `requires_sco_review`, `mandatory_escalation`) | Consumes its verdict as evidence. Does not modify, extend, or duplicate it |
| `document_reliance_gate.evaluate_document_reliance_gate()` | **Authoritative** approval gate | Consumes gate output as evidence |
| Human officer | **Authoritative** for the decision | Reviewed, challenged, never overridden |
| **AI Compliance Supervisor** | **Advisory only** | Produces findings. Blocks nothing. |

### 2.2 The review question

For each authoritative component the Supervisor asks four questions, and only
these four:

1. **Did it have sufficient input?** (was the evidence there to decide on)
2. **Did it operate consistently?** (did controlled values resolve; did the run complete)
3. **Did it produce a defensible result?** (is the output evidenced and explainable)
4. **Was it followed?** (did the officer act consistently with it)

The Supervisor never asks *"is the component's answer correct?"* — that would
make it a competing authority. It asks whether the component was in a position
to be correct, and whether its answer was honoured.

### 2.3 Hard boundaries

The Supervisor must never:

- write to `applications`, `documents`, `compliance_memos`, `decision_records`,
  or any approval-gate state;
- set, influence, or read-then-modify `can_approve`, `requires_sco_review`, or
  `mandatory_escalation`;
- append to the authoritative verdict hash chain
  (`supervisor/audit.py::append_verdict_chain_entry`);
- re-run a screening provider, IDV provider, or registry lookup;
- emit a decision, recommendation, or approval verdict of any kind.

---

## 3. Independent review framework

### 3.1 Subject model

Every review has exactly one **subject**. Phase 0B implements `application`
only; the other subject types are defined now so the taxonomy, evidence model
and output contract do not require rework later.

| `subject_type` | `subject_id` source | Phase |
|---|---|---|
| `application` | `applications.id` | **0B** |
| `customer` | `clients.id` | later |
| `periodic_review` | `periodic_reviews.id` | later |
| `monitoring_event` | `monitoring_alerts.id` | later |
| `change_request` | change-request ID (`change_management.py`) | later |

A review is identified by `(subject_type, subject_id, as_of, policy_version)`.
`as_of` is explicit and injected — never read from the system clock inside a
probe (see §10.4).

### 3.2 Review posture

The Supervisor reviews **from first principles**, in this order:

1. **Is the subject what it claims to be?** (identity, legal existence)
2. **Who ultimately owns and controls it?** (ownership, control, structure, people)
3. **Where does its money come from and go?** (SOW, SOF, activity, products)
4. **What exposure does it carry?** (geography, sector, screening, PEP, adverse media)
5. **Is the evidence sufficient and consistent?** (documents, registry)
6. **Is the assessment defensible?** (risk score, EDD, policy, memo)
7. **Is the decision sound and governed?** (consistency, overrides, audit, monitoring)
8. **What would a regulator attack?** (derived — adds no independent findings)

The 30 control domains in §4 are grouped along this spine.

---

## 4. Domain-by-domain control matrix

Two tables per cluster. **Table A** gives the control objective and the
questions the Supervisor must answer. **Table B** gives the evidence mapping,
what is deterministically checkable *today*, the gaps, the error risks, and the
phase.

Phase values: **0B** = build now · **L** = later phase · **NF** = not currently
feasible (data does not exist).

---

### Cluster 1 — Identity and legal existence (domains 1–2)

**Table A**

| # | Domain | Control objective | Supervisor questions |
|---|---|---|---|
| 1 | Identity | Every natural person subject to KYC has been identified to the required standard | Does every director and UBO have an identity resolution? Was it relied upon, or superseded? Does the identity document name match the declared name? |
| 2 | Legal existence | The applicant entity demonstrably exists and is in good standing | Is there a certificate of incorporation, verified? Do the incorporation number, date and jurisdiction agree across document, declaration and registry? |

**Table B**

| # | Sources / existing functions | Deterministic today | Data gaps | FP risk | FN risk | Phase |
|---|---|---|---|---|---|---|
| 1 | `directors`, `ubos`; `idv_resolutions`; `sumsub_idv_status.py`; `documents` slot `passport`/`national_id`; `verification_matrix` DOC name-match checks; `document_reliance_gate` per-party expectations | Per-party passport/POA expectation satisfied or blocked; name-match check status from `documents.verification_results` | Extracted-field values are inside `verification_results` JSON with no guaranteed schema across doc types — field-level comparison is not safely generic | Low — presence checks are unambiguous | Medium — a name match that "passed" on a weak comparator is not re-examined | **0B** (presence + check-status only) |
| 2 | `documents` slot `cert_inc`; `company_registry_lookups` (`normalized_json`, `response_hash`, `status`, `error_code`); `company_registry.py` (`_normalize_companies_house_company`, `_normalize_opencorporates_company`) | Cert-inc expectation satisfied; registry lookup row present and `status` terminal | Registry providers are credential-gated (`COMPANIES_HOUSE_API_KEY`, OpenCorporates). Where absent, no registry evidence exists at all | Low | **High** — absent credentials means legal existence is never independently corroborated. Must surface as `unavailable`, never `clear` | **0B** (with mandatory `unavailable` path) |

---

### Cluster 2 — Ownership, control, structure, people (domains 3–6)

**Table A**

| # | Domain | Control objective | Supervisor questions |
|---|---|---|---|
| 3 | Ownership | Beneficial ownership is complete and reconciles | Do declared holdings reconcile to 100%? Is any UBO above threshold missing? Is unattributed ownership explained? |
| 4 | Control | Control exercised other than through ownership is identified | Who controls the entity by means other than shareholding — voting agreements, senior managing official, board control? |
| 5 | Corporate structure | The chain from applicant to natural persons is traceable | Is there a structure chart? Does the chain terminate in natural persons? Are there opacity markers (nominee, trust, bearer) and are they explained? |
| 6 | Directors and key persons | The governing body is identified and verified | Is every director captured? Do declared directors agree with the registry officer list? Is each one KYC-complete? |

**Table B**

| # | Sources / existing functions | Deterministic today | Data gaps | FP risk | FN risk | Phase |
|---|---|---|---|---|---|---|
| 3 | `ubos.ownership_pct`; `intermediaries`; `documents` slot `structure_chart` (DOC-27 UBO Chain, DOC-28 Ownership Match); `rule_engine` factor `ownership_structure` | **Sum of `ownership_pct` across UBOs; count of UBOs at/above threshold; presence of unattributed residual** | No configured UBO threshold constant — the 25%/10% threshold is not encoded anywhere in the backend | Medium — legitimate free-float or listed-parent structures will not sum to 100%; needs an entity-type exemption list | Low | **0B** — highest-value probe |
| 4 | — | **None** | **No field for control-other-than-ownership anywhere in the schema.** `directors` captures appointment, not control rights. There is no senior-managing-official fallback field | — | Total — the domain is invisible | **NF** — requires a new intake field before any probe is possible |
| 5 | `intermediaries`; `rule_engine._is_opaque_ownership()`, `OPAQUE_OWNERSHIP_KEYWORDS`, `_ownership_transparency_tier()`; `applications.ownership_structure`; `structure_chart` slot | Opacity markers detected by keyword; `ownership_transparency_status` value; structure-chart expectation satisfied | Opacity detection is keyword matching over a free-text field — a nominee structure described in other words is missed | Medium — keyword hits on innocuous prose | **High** — free-text keyword matching has poor recall | **0B** — but scoped to *"opaque marker present AND no structure chart verified"*, which is a conjunction of two reliable signals |
| 6 | `directors`; `company_registry.py::_normalize_officers()`, `_is_director_role()`, `_normalize_companies_house_officer()`; per-director `passport`/`poa` expectations | Per-director document expectation satisfied; registry officer list retrieved (when credentialed) | Registry officer comparison requires name normalisation across sources; no canonical person-matching utility exists for registry↔declared reconciliation | Medium — name-form variance produces spurious mismatches | Medium | **0B** for KYC-completeness; **L** for registry officer reconciliation (needs a matching utility) |

---

### Cluster 3 — Financial profile (domains 7–8)

**Table A**

| # | Domain | Control objective | Supervisor questions |
|---|---|---|---|
| 7 | Source of wealth | The origin of the customer's accumulated wealth is declared and corroborated | Is SOW declared? Is it corroborated by evidence? Where SOW drives risk, is that evidence verified? |
| 8 | Source of funds | The origin of the funds to be transacted is declared and corroborated | Is SOF declared? Is it corroborated? Is it consistent with declared SOW? |

**Table B**

| # | Sources / existing functions | Deterministic today | Data gaps | FP risk | FN risk | Phase |
|---|---|---|---|---|---|---|
| 7 | `rule_engine` D1 factor `source_of_wealth` (`d1_sow`, default 2, **3 when undeclared**); `SOURCE_OF_WEALTH_SCORE_MAP`; `verification_matrix` doc type **`sow`** ("Source of Wealth Declaration", DOC-59 Name Match / DOC-60 Supporting Evidence) | **`d1_sow` score vs presence and verification state of a `sow` document** — a genuine evidence-to-risk coupling check | `sow` is defined in `verification_matrix` but is **not** in `document_reliance_gate.build_required_document_expectations()`, so it is never *required* — only checked if volunteered | Low | Medium — where SOW is declared as low-risk, no evidence is sought | **0B** — strong probe |
| 8 | `rule_engine` D1 factor `source_of_funds` (`d1_sof`, default 2, **3 when undeclared**); `SOURCE_OF_FUNDS` prescreening field | Declared / undeclared state and its risk contribution | **There is no `sof` document type in `verification_matrix`.** SOF has a declared value and a risk weight but no corroborating artifact anywhere in the system | Low | **High** — SOF can never be corroborated, only asserted | **0B** for the undeclared-SOF probe; corroboration is **NF** pending a `sof` document type |

---

### Cluster 4 — Activity and exposure (domains 9–13)

**Table A**

| # | Domain | Control objective | Supervisor questions |
|---|---|---|---|
| 9 | Business model plausibility | The stated business is coherent and consistent with the entity's profile | Does the business model make sense for this entity type, jurisdiction and sector? Is it consistent with the products requested? |
| 10 | Expected activity | Declared volumes and complexity are plausible | Are declared volumes plausible for this business? Do they reconcile with financial statements? |
| 11 | Products and services | Requested products are identified and risk-assessed | Which services were selected? Did every selection resolve to a controlled risk value? |
| 12 | Geographic exposure | All jurisdictional exposure is captured and scored | Are incorporation, operating, target-market, UBO-nationality and intermediary jurisdictions all captured and resolved? |
| 13 | Customer and sector risk | The sector is correctly classified | Did the declared sector resolve to a controlled value, or default silently? |

**Table B**

| # | Sources / existing functions | Deterministic today | Data gaps | FP risk | FN risk | Phase |
|---|---|---|---|---|---|---|
| 9 | `applications.sector`, `entity_type`; memo `client_overview` section | Nothing reliable | No business-plan document type in `verification_matrix`; no structured business-model field. Plausibility is genuine interpretation, not pattern matching | **High** — any heuristic here fires on legitimate businesses | High | **L** — and only as LLM *narration over other probes' hits*, never as an independent finding |
| 10 | `rule_engine` D3 factors `monthly_volume`, `transaction_complexity`; `fin_stmt` document | Declared values and their `resolution_status` | **No reference bands** linking plausible volume to sector/entity type. No extracted turnover from `fin_stmt` to compare against | **High** — a plausibility band invented now would be arbitrary | High | **NF** — requires reference data that does not exist |
| 11 | `rule_engine.resolve_selected_service_risk()`, `service_selection_evidence` (`normalized_services`, `resolution_status`, `final_max_score`); D3 factor `service_type` | **`resolution_status` != resolved on the service selection** — a silent default in a risk input | None material | Low — `resolution_status` is an explicit engine output, not a heuristic | Low | **0B** — folds into the risk-integrity probe |
| 12 | D2 factors `country_of_incorporation`, `ubo_nationalities`, `intermediary_jurisdictions`, `countries_of_operation`, `target_markets`; `classify_country()`; `country_risk_provenance`; `risk_controlled_values` (`REGISTRY_VERSION = "rsmp-tier0a-v1"`, `unresolved_mapping_sentinel`) | **Per-factor `resolution_status`; unresolved country aliases; `country_risk_provenance`** | None material | Low | Low | **0B** — folds into the risk-integrity probe |
| 13 | D4 factor `industry_sector`; `score_sector()`; `UNRESOLVED_SECTOR_LABELS`; `HIGH_RISK_SECTOR_KEYWORDS` | **`resolution_status` on the sector factor; unresolved sector labels** | None material | Low | Low | **0B** — folds into the risk-integrity probe |

---

### Cluster 5 — Screening (domains 14–17)

**Table A**

| # | Domain | Control objective | Supervisor questions |
|---|---|---|---|
| 14 | Screening | Screening was performed against a live provider, completed, and is current | Did every required subject get screened? Is the result terminal? Was the provider live, or sandbox/simulated? Is it stale? |
| 15 | Sanctions | Sanctions exposure is identified and dispositioned | Is there a sanctions hit? Has it been formally cleared, by whom, with what reason? |
| 16 | PEP exposure | PEP status is declared, detected and evidenced | Is a declared PEP corroborated by a PEP declaration and bank reference? Does screening-detected PEP status agree with the declaration? |
| 17 | Adverse media | Adverse media signals are identified and dispositioned | Is there an adverse-media hit without a disposition? |

**Table B**

| # | Sources / existing functions | Deterministic today | Data gaps | FP risk | FN risk | Phase |
|---|---|---|---|---|---|---|
| 14 | `screening_state.build_screening_truth_summary()` → `canonical_state`, `provider_mode` (`live_provider`/`sandbox_provider`/`simulated_fallback`), `terminal`, `defensible_clear`, `screening_gate_ready`, `has_stale`, `required_evidence[]`, `freshness`; `screening_freshness_metadata`; `environment.get_screening_validity_days()` | **Everything.** This is the richest deterministic surface in the codebase | None material | Very low — these are explicit engine states | Low | **0B** — strongest probe family |
| 15 | `screening_adverse_truth.py` (`STATE_SANCTIONS_HIT`, `EFFECT_PROHIBITED`, `MATERIALITY_*`); `screening_hit_dispositions`; `screening_reviews`; `screening_state._is_false_positive_clearance()`, `_review_second_signoff_satisfied()` | Hit present; disposition present; second sign-off satisfied; clearing officer identity and reason present | None material | Low | Low | **0B** |
| 16 | `directors.is_pep` / `pep_declaration`, `ubos.is_pep` / `pep_declaration`; `rule_engine.GATE0_DECLARED_PEP_SCORE = 4`, `_party_has_declared_or_confirmed_pep()`, `_declared_pep_score_evidence()`; `verification_matrix` doc types **`pep_declaration`**, **`bankref_pep`**; memo `declared_pep_count` | **Declared PEP present AND no verified `pep_declaration` document AND no `bankref_pep`** — a three-way conjunction, all deterministic | `pep_declaration` / `bankref_pep` are not in `build_required_document_expectations()`, so they are never *required* — which is precisely why the gap goes unnoticed | Low | Low | **0B** — high-value probe |
| 17 | `screening_adverse_truth.py` (`STATE_ADVERSE_MEDIA_HIT`, `STATE_ADVERSE_MEDIA_FALSE_POSITIVE`); `rule_engine` D1 factor `adverse_media`, `ADVERSE_MEDIA_SCORE_4_KEYWORDS` etc. | Adverse-media state and disposition presence | **No external adverse-media provider exists** (per CLAUDE.md). Signals are parsed from screening-provider results only. `d1_adverse` defaults to **1 (clear) when no screening data is available** — an absence-means-clear default | Low | **High** — the `d1_adverse = 1` default on missing data is a systematic false-negative source | **0B** — and the probe should specifically target *"adverse-media scored clear because no data was available"* |

---

### Cluster 6 — Documents and registry (domains 18–20)

**Table A**

| # | Domain | Control objective | Supervisor questions |
|---|---|---|---|
| 18 | Document sufficiency | All required evidence is present, verified and current | Which required documents are missing, unverified, stale or superseded? Was reliance placed on a document that failed verification? |
| 19 | Document consistency | Documents agree with each other and with declarations | Do names, numbers, dates and ownership figures agree across documents? |
| 20 | Registry consistency | Declared particulars match the public registry | Do entity name, number, status, address and officers match? |

**Table B**

| # | Sources / existing functions | Deterministic today | Data gaps | FP risk | FN risk | Phase |
|---|---|---|---|---|---|---|
| 18 | `document_reliance_gate.evaluate_document_reliance_gate()` → `blockers[]`, `documents[]` snapshots, `reliance_status`, `POLICY_VERSION = "document_reliance_gate_v2"`; blocker codes `missing_required_document`, `superseded_document`, `unsupported_document_type`, `missing_verification_results`, `missing_verified_at`, `stale_verification`, `missing_agent_execution_proof`; `ALLOWED_RELIANCE_STATES = ("verified","manual_accepted")`; `MANUAL_ACCEPTANCE_ROLES = {"admin","sco"}` | Everything, deterministically | Gate calls `datetime.now(timezone.utc)` internally and derives staleness from it — **not replay-safe without an injected `as_of`** (§10.4) | Very low | Low | **0B** — *but only in the evidence-to-risk coupling framing* (§12.2 excludes the plain blocker list as a restatement) |
| 19 | `documents.verification_results` JSONB; `verification_matrix` per-check `rule_type` (`name`/`date`/`numeric`/`enum`/`set`/`presence`/`hash`); `CheckStatus.{PASS,WARN,FAIL,SKIP,INCONCLUSIVE}`; DOC-28 Ownership Match | Per-check status roll-up (how many `FAIL`/`WARN`/`INCONCLUSIVE` across active documents) | **The extracted-field values behind each check are not guaranteed to persist in a stable schema across document types.** Cross-document field comparison cannot be written generically today | Medium | Medium | **L** — status roll-up is possible now but is close to a restatement; genuine cross-document comparison needs a stable extraction schema |
| 20 | `company_registry_lookups.normalized_json` / `response_hash` / `status` / `error_code`; `company_registry.py` normalizers | Lookup presence, terminality, and hash | Credential-gated; no declared-vs-registry field comparison utility exists | Medium (name normalisation) | High when uncredentialed | **L** — depends on the same matching utility as domain 6 |

---

### Cluster 7 — Assessment defensibility (domains 21–24)

**Table A**

| # | Domain | Control objective | Supervisor questions |
|---|---|---|---|
| 21 | Risk-score defensibility | The risk score is explainable, evidenced, and computed from resolved inputs | Did every scored factor resolve to a controlled value, or default silently? Was a floor or elevation applied, and is the reason recorded? Was the config current? |
| 22 | EDD requirements | EDD triggers were evaluated and honoured | Re-evaluating the policy on the same facts, does it route to EDD? Did the case actually go to EDD? |
| 23 | Policy adherence | Governed policy was applied at decision time | Was the routing invariant satisfied? Was the risk config version valid, or a staleness sentinel? |
| 24 | Memo quality | The memo is complete, validated, and its conclusions follow the evidence | Did validation pass? Are unresolved validation issues outstanding at decision time? Is the memo stale relative to its inputs? |

**Table B**

| # | Sources / existing functions | Deterministic today | Data gaps | FP risk | FN risk | Phase |
|---|---|---|---|---|---|---|
| 21 | `compute_risk_score()` → `factor_computation_evidence` (`schema_version: "risk-factor-evidence-v1"`) with per-factor `factor_key`, `factor_label`, `raw_value`, `normalized_value`, `rule_score`, `factor_weight`, `weighted_factor_contribution`, `resolution_status`; `controlled_mapping_evidence`; `risk_controlled_values.unresolved_mapping_sentinel`, `REGISTRY_VERSION`; `apply_risk_floor()`, `elevation_reason_text`, `escalations`; `applications.risk_config_version` | **Per-factor `resolution_status` across all 17 factors; unresolved-mapping sentinels; floor/elevation applied with or without a recorded reason; `risk_config_version` staleness sentinels (`stale:recompute_failed`, `stale:cm_recompute_pending`)** | None material — this is the best-instrumented surface in the codebase | Very low | Low | **0B** — highest-differentiation probe |
| 22 | `edd_routing_policy.evaluate_edd_routing()` (**pure**, `POLICY_VERSION = "edd_routing_policy_v1"`), `ALL_TRIGGERS` (9 triggers), `REQUIRED_FACT_KEYS` (8 keys), `assert_routing_invariant()`, `minimum_risk_level_for_routing()`; `edd_cases.stage`; memo `edd_routing` metadata | **Re-run the pure policy on the stored facts and compare `route` against whether an `edd_cases` row exists.** Also: `TRIGGER_INCOMPLETE_CONTRACT` firing means the fact contract was incomplete at routing time | None material | Low — the policy is pure and versioned | Low | **0B** — highest-value probe |
| 23 | `assert_routing_invariant()`; `rule_engine` Rules 4A–4E; `_is_valid_risk_config_version()`; `risk_model_view` (`EDD_POLICY_VERSION`, `RISK_FREQUENCY_MONTHS`, `ENHANCED_REVIEW_FLOOR_MONTHS`) | Invariant violation; invalid or sentinel config version at decision time | None material | Low | Low | **0B** — folds into the risk-integrity probe |
| 24 | `validation_engine.validate_compliance_memo()`; `compliance_memos.validation_status` (`pending`/`pass`/`pass_with_fixes`/`fail`), `validation_issues`, `quality_score`, `version`, `raw_output_hash`; `memo_governance.latest_compliance_memo_row()`, `memo_selection_metadata()` | Validation status and outstanding issues at decision time; memo version vs `applications.inputs_updated_at` | None material | Low | Low | **L** — largely visible on the memo screen already; see §12.2 |

---

### Cluster 8 — Decision and governance (domains 25–30)

**Table A**

| # | Domain | Control objective | Supervisor questions |
|---|---|---|---|
| 25 | Decision consistency | The decision follows the memo, the supervisor verdict and the risk rating | Does the recorded decision match the memo recommendation? Was the case approved against an `INCONSISTENT` verdict? |
| 26 | Overrides | Overrides are justified, attributed and specific | Was an override applied? Is the reason specific to this case? Who applied it? |
| 27 | Approval readiness | All gates were satisfied at the point of approval | — |
| 28 | Auditability | Every material action has an immutable, attributed record | Does every decision have a `decision_records` row? Does the verdict chain verify? Are there actions with no audit entry? |
| 29 | Monitoring requirements | Monitoring commitments made in the memo were actually established | The memo commits to enhanced monitoring at a stated tier — was a periodic review scheduled, at the right frequency? |
| 30 | Potential regulatory challenge | The file's weakest points are stated before an inspector finds them | Which findings would an inspector open on? |

**Table B**

| # | Sources / existing functions | Deterministic today | Data gaps | FP risk | FN risk | Phase |
|---|---|---|---|---|---|---|
| 25 | `run_memo_supervisor()` → `verdict` (`CONSISTENT`/`CONSISTENT_WITH_WARNINGS`/`INCONSISTENT`), `contradictions[]` (15 category codes), `can_approve`, `mandatory_escalation`; memo `approval_recommendation`; `applications.status`, `decided_at`, `decision_by`; `decision_records.decision_type` | Verdict vs recorded decision; memo recommendation vs recorded decision | None material | Low | Low | **0B** for the *time-of-decision* comparison; the live-state version restates the Case Command Centre |
| 26 | `decision_records.override_flag` / `override_reason` (reason enforced non-empty by `build_decision_record`); `supervisor_overrides`; `OverrideType` enum (6 values) | Override present; reason present; actor attributed; reason reused verbatim across multiple cases | No structured override taxonomy on `decision_records` — `OverrideType` lives only in the gated `supervisor/` package | **High** if "boilerplate detection" is attempted semantically | Medium | **0B** — restricted to *reason reused verbatim across cases*, which is exact-match and needs no judgement. Semantic quality scoring is **excluded** |
| 27 | `document_reliance_gate`, `screening_gate_ready`, `can_approve` | (all of them) | — | — | — | **Excluded** — see §12.2. This is exactly what the Case Command Centre already shows |
| 28 | `audit_log` (`action`, `target`, `application_id`, `user_id`, `request_id`); `decision_records`; `supervisor_audit_log`; `supervisor/audit.py` hash chain (`previous_hash`, `entry_hash`, `supervisor_entry_hash()`) | **Decision with no `decision_records` row; verdict-chain break; state transition with no `audit_log` entry** | `audit_log.action` is free text with no controlled vocabulary, so "expected audit entry" coverage must be defined per action type | Medium | Medium | **0B** for decision-record coverage and chain integrity; broader audit-coverage analysis is **L** |
| 29 | Memo `sections["ongoing_monitoring"]`, `metadata["conditions"]` (monitoring tier strings); `periodic_review_policy.RISK_FREQUENCY_MONTHS`, `ENHANCED_REVIEW_FLOOR_MONTHS`; `periodic_reviews` (`next_review_date`, `trigger_type`, `policy_version`); `periodic_review_engine.py` | **Memo commits to enhanced monitoring AND no `periodic_reviews` row exists, or `next_review_date` exceeds the policy frequency for the final risk level** | Monitoring commitments are embedded in generated prose strings in `metadata["conditions"]`, not structured fields — detection must key on the risk level and tier, not parse the sentence | Low if keyed on risk level rather than prose | Medium | **0B** — high-value, genuinely invisible today |
| 30 | Derived from domains 1–29 | Presentation only | — | — | — | **0B** as a *view*; emits no independent findings |

---

### 4.1 Honest summary of the matrix

- **Genuinely strong deterministic surfaces:** risk factor evidence (21), EDD
  routing (22), screening truth (14–16), ownership arithmetic (3), document
  reliance (18), monitoring commitments (29).
- **Not feasible without new intake data:** control-other-than-ownership (4),
  expected-activity plausibility (10), source-of-funds corroboration (8).
- **Feasible but low value because already on screen:** approval readiness (27),
  memo validation status (24), plain document blockers (18 in its raw form).
- **Feasible but unreliable, so excluded:** business model plausibility (9),
  semantic override-quality scoring (26), free-text opacity detection standing
  alone (5).

---

## 5. Finding taxonomy

Every finding **must** carry exactly one `category` from this closed list.
Free-form or unclassified findings are prohibited: an unclassified finding
cannot be routed, aggregated, or closed.

The taxonomy is versioned with the probe set (`CHALLENGE_POLICY_VERSION`).
Adding, removing or re-scoping a category is a governed change requiring a
version bump.

### 5.1 Severity vocabulary

Reuse `supervisor.schemas.Severity` — do not invent a parallel scale.

| Severity | Meaning |
|---|---|
| `critical` | The decision is not defensible as it stands |
| `high` | A material control weakness a regulator would open on |
| `medium` | A defect that weakens the file but does not undermine the decision |
| `low` | A quality or completeness issue |
| `info` | Contextual; no action required |

### 5.2 Category register

`WO` = responsible workflow owner. `Officer` = case officer · `SCO` = senior
compliance officer · `MLRO` = money laundering reporting officer ·
`Ops` = platform operations.

| ID | Category | Description | Example | Severity rule | Evidence requirement | Closure criterion | WO |
|---|---|---|---|---|---|---|---|
| F-01 | `identity` | Identity of a natural person not established to standard | Director has no IDV resolution and no verified passport | `high` if party is a UBO or director; `medium` otherwise | Party ref + document/IDV ref | Verified identity artifact present for the party | Officer |
| F-02 | `legal_existence` | Entity existence not independently corroborated | No registry lookup and no verified certificate of incorporation | `high` | Document ref + registry lookup ref (or `unavailable`) | Verified cert-inc or terminal registry lookup | Officer |
| F-03 | `ownership` | Beneficial ownership incomplete or non-reconciling | UBO holdings total 74%; 26% unattributed | `critical` if unattributed ≥ threshold; else `high` | Party refs with `ownership_pct` values | Holdings reconcile or residual is documented | Officer |
| F-04 | `control` | Control other than by ownership not identified | *(no probe — see domain 4)* | `high` | Application field ref | Control disclosure captured | Officer |
| F-05 | `corporate_structure` | Chain to natural persons not traceable | Nominee marker present, no verified structure chart | `high` | Application field ref + document ref | Verified structure chart tracing to natural persons | Officer |
| F-06 | `source_of_wealth` | SOW undeclared or uncorroborated where it drives risk | `d1_sow = 3` (undeclared) with no `sow` document | `high` if SOW factor ≥ 3; else `medium` | Risk factor ref + document ref | Verified SOW evidence present | Officer |
| F-07 | `source_of_funds` | SOF undeclared | `d1_sof = 3` (undeclared) | `medium` | Risk factor ref | SOF declared | Officer |
| F-08 | `business_activity` | Stated business incoherent or unevidenced | *(narration-only — no independent probe)* | `medium` | Application field ref | — | Officer |
| F-09 | `expected_activity` | Declared activity implausible | *(no probe — see domain 10)* | `medium` | Application field ref | — | Officer |
| F-10 | `products` | Requested service did not resolve to a controlled risk value | `service_selection_evidence.resolution_status = unresolved` | `medium` | Risk factor ref | Service resolves under current registry | Ops |
| F-11 | `jurisdiction` | Jurisdictional exposure unresolved or unscored | Operating country did not resolve to a controlled score | `high` if the factor drives an elevation; else `medium` | Risk factor ref with `resolution_status` | Country resolves under current registry | Ops |
| F-12 | `screening` | Screening not performed, not terminal, not live, or stale | `provider_mode = simulated_fallback` at decision time | `critical` if relied on for approval; else `high` | Screening evidence ref | Terminal live screening within validity window | Officer |
| F-13 | `sanctions` | Sanctions hit without adequate disposition | Sanctions match with no second sign-off | `critical` | Screening evidence ref + disposition ref | Disposition recorded with reason, actor and sign-off | SCO |
| F-14 | `pep` | PEP exposure declared or detected without required evidence | Declared PEP, no PEP declaration document | `high` | Party ref + document refs | PEP declaration and bank reference verified | Officer |
| F-15 | `adverse_media` | Adverse media hit undispositioned, **or scored clear on absent data** | `d1_adverse = 1` because no screening data existed | `high` when scored clear on absence; `medium` otherwise | Screening evidence ref + risk factor ref | Adverse-media state terminal and dispositioned | Officer |
| F-16 | `documentation` | Required evidence absent, unverified, stale or superseded **where it corroborates a material risk factor** | Risk factor scored 4 with no verified corroborating document | `high` | Document expectation ref + risk factor ref | Document verified under the reliance gate | Officer |
| F-17 | `registry` | Declared particulars diverge from registry | *(deferred — see domain 20)* | `high` | Registry lookup ref | Divergence explained or corrected | Officer |
| F-18 | `risk` | Risk score not defensible — silent defaults, unrecorded elevation, stale config | Sector defaulted via unresolved mapping sentinel | `high` | Risk factor refs + `risk_config_version` | All scored factors resolved under a valid config version | Ops / SCO |
| F-19 | `edd` | EDD policy and actual routing diverge | Policy routes `edd`; no `edd_cases` row exists | `critical` | EDD policy evaluation ref + EDD case ref | Case routed to EDD or divergence formally accepted | SCO |
| F-20 | `policy` | A governed policy was violated or applied under an invalid version | Routing invariant violated | `critical` | Policy evaluation ref | Invariant satisfied | SCO |
| F-21 | `memo` | Memo incomplete, unvalidated, or stale relative to inputs | Memo predates the last input change | `medium` | Memo ref with version | Memo regenerated and validated | Officer |
| F-22 | `decision` | Decision inconsistent with memo, verdict or rating | Approved against an `INCONSISTENT` verdict | `critical` | Decision record ref + verdict ref | Decision revisited or override documented | SCO |
| F-23 | `override` | Override unattributed, or reason reused verbatim across cases | Identical override reason on 9 cases | `high` | Decision record refs across subjects | Case-specific reason recorded | SCO |
| F-24 | `approval` | Approval executed with an unsatisfied gate | *(excluded — see §12.2)* | `critical` | Gate ref | — | SCO |
| F-25 | `governance` | Segregation-of-duties or authority defect | Same actor generated the memo and approved it | `high` | Actor refs on both events | Independent approver recorded | MLRO |
| F-26 | `monitoring` | Monitoring commitment not established | Memo commits to enhanced monitoring; no review scheduled | `high` | Memo section ref + periodic review ref | Review scheduled at policy frequency | Officer |
| F-27 | `audit` | Audit record missing or chain broken | Decision with no `decision_records` row | `critical` | Decision ref + audit refs | Record reconstructed or break formally logged | MLRO |

Categories F-04, F-08, F-09, F-17 and F-24 are **defined but have no Phase 0B
probe**. They are reserved so that later probes do not force a taxonomy version
bump.

---

## 6. Canonical finding record

### 6.1 Schema

```
finding_id                str    stable within a run; derived, not random (§10.6)
subject_type              enum   application | customer | periodic_review |
                                 monitoring_event | change_request
subject_id                str
probe_id                  str    e.g. "P-03"
probe_version             str    e.g. "p03-v1"
policy_version            str    e.g. "supervisor-review-v1" (governs the whole set)
category                  enum   F-01 … F-27 (§5.2)
severity                  enum   critical | high | medium | low | info
status                    enum   hit | clear | unavailable | not_applicable |
                                 not_replayable
availability_status       enum   available | dependency_gated | credentials_absent |
                                 data_absent | snapshot_incomplete
confidence                float  0.0–1.0 — probe-declared, NOT model-generated
claim                     str    what is wrong, in one sentence
evidence_refs             list   EvidenceRef[] (§7)
source_modules            list   e.g. ["rule_engine.compute_risk_score",
                                 "edd_routing_policy.evaluate_edd_routing"]
why_it_matters            str    the control consequence
regulatory_or_policy_basis str   internal policy version or regulatory anchor;
                                 "internal_policy_only" when no external anchor
officer_question          str    the question to put to the client or the team
required_action           str    what must be done
close_condition           str    the evidence or event that closes it
created_at                str    the run's injected as_of, NOT wall-clock (§10.4)
```

### 6.2 Status semantics — the critical rule

| Status | Meaning | Rendering obligation |
|---|---|---|
| `hit` | The probe ran and found a defect | Present as a finding |
| `clear` | The probe ran on complete data and found nothing | May be summarised in review completeness |
| `unavailable` | The probe **could not run** | **Must be shown distinctly.** Never rendered as, aggregated with, or counted as `clear` |
| `not_applicable` | The probe does not apply to this subject | Shown in review completeness |
| `not_replayable` | The probe requires historic state that was not snapshotted (§11) | **Must be shown distinctly.** Never rendered as `clear` |

> **Non-negotiable:** a check that could not be evaluated must never be
> presented as passing. `availability_status` records *why* it could not run, so
> the distinction between "no registry credentials", "gated module off", and
> "no historic snapshot" survives into the output and the evidence pack.

### 6.3 The five questions

Every finding must answer all five. A probe that cannot populate all five
fields is not ready to ship.

| Question | Field |
|---|---|
| 1. What is wrong? | `claim` |
| 2. What evidence supports it? | `evidence_refs` + `source_modules` |
| 3. Why does it matter? | `why_it_matters` + `regulatory_or_policy_basis` |
| 4. What action is required? | `required_action` + `officer_question` |
| 5. What would close it? | `close_condition` |

### 6.4 Confidence

`confidence` is **declared by the probe author**, fixed per probe version, and
reflects the probe's known false-positive rate — not a runtime estimate and
never a model output. A probe reading an explicit engine state
(`resolution_status`, `provider_mode`) declares high confidence; a probe
inferring from free-text keywords declares low, and should generally not ship.

---

## 7. Evidence model

### 7.1 Principle

A lightweight **evidence map**, not a graph database. Each finding carries a
list of typed, stable references into systems that already exist. No new
storage of evidence content — only addressing.

### 7.2 Evidence reference schema

```
evidence_type       enum   (§7.3)
ref                 str    stable address (§7.3)
source_module       str    the module or table that owns it
assertion           str    the specific value or claim being relied on
value               any    the raw value where scalar and non-sensitive
timestamp           str    when the evidence was produced
version             str    schema/policy/config version where applicable
verification_status enum   verified | unverified | failed | not_applicable | unknown
existed_at_decision bool   whether this evidence predates the decision (§7.4)
```

### 7.3 Evidence types and stable addressing

| `evidence_type` | Stable `ref` form | Owning source | Version field available |
|---|---|---|---|
| `application_field` | `application:{id}#{field}` | `applications` | `inputs_updated_at` |
| `party_record` | `director:{id}` / `ubo:{id}` / `intermediary:{id}` | `directors`, `ubos`, `intermediaries` | — |
| `document` | `document:{id}` | `documents` (`version`, `is_current`, `file_sha256`) | `version` |
| `document_verification` | `document:{id}#check:{check_id}` | `documents.verification_results`, `verification_matrix` | — |
| `document_expectation` | `expectation:{slot_key}` | `document_reliance_gate` | `document_reliance_gate_v2` |
| `screening_result` | `screening:{application_id}#subject:{subject_type}:{subject_name}` | `screening_state.build_screening_truth_summary().required_evidence[]` | `normalized_version` |
| `screening_normalized` | `screening_normalized:{id}` | `screening_reports_normalized` (`source_screening_report_hash`) | `normalized_version` |
| `screening_disposition` | `screening_disposition:{id}` | `screening_hit_dispositions`, `screening_reviews` | — |
| `registry_result` | `registry_lookup:{id}` | `company_registry_lookups` (`response_hash`) | provider |
| `risk_dimension` | `risk:{application_id}#dimension:{D1..D5}` | `factor_computation_evidence.dimensions[]` | `risk-factor-evidence-v1` |
| `risk_factor` | `risk:{application_id}#factor:{factor_key}` | `factor_computation_evidence.factors[]` | `risk-factor-evidence-v1` |
| `risk_config` | `risk_config:{version}` | `applications.risk_config_version`, `risk_config` | `REGISTRY_VERSION` |
| `risk_rule` | `risk_rule:{rule_id}` | `rule_engine` Rules 4A–4E | — |
| `edd_rule` | `edd_policy:{policy_version}#trigger:{trigger}` | `edd_routing_policy.ALL_TRIGGERS` | `edd_routing_policy_v1` |
| `edd_case` | `edd_case:{id}` | `edd_cases` | — |
| `memo_section` | `memo:{id}#section:{section_key}` | `compliance_memos.memo_data` | `version`, `raw_output_hash` |
| `supervisor_verdict` | `memo_supervisor:{application_id}#{checked_at}` | `run_memo_supervisor()` output | — |
| `officer_note` | `note:{id}` | `application_notes` | — |
| `decision_record` | `decision:{decision_id}` | `decision_records` | — |
| `override` | `decision:{decision_id}#override` | `decision_records.override_flag/reason` | — |
| `audit_event` | `audit:{id}` | `audit_log` | `request_id` |
| `verdict_chain_entry` | `supervisor_audit:{entry_hash}` | `supervisor_audit_log` | `previous_hash` |
| `monitoring_event` | `monitoring_alert:{id}` | `monitoring_alerts` | — |
| `periodic_review` | `periodic_review:{id}` | `periodic_reviews` | `policy_version` |

Valid memo `section_key` values (from `memo_handler`): `executive_summary`,
`client_overview`, `ownership_and_control`, `risk_assessment`,
`screening_results`, `document_verification`, `enhanced_review_edd`,
`ai_explainability`, `red_flags_and_mitigants`, `compliance_decision`,
`ongoing_monitoring`, `audit_and_governance`.

Valid `factor_key` values (17, from `compute_risk_score`):
D1 — `entity_type`, `ownership_structure`, `pep_status`, `adverse_media`,
`source_of_wealth`, `source_of_funds`;
D2 — `country_of_incorporation`, `ubo_nationalities`,
`intermediary_jurisdictions`, `countries_of_operation`, `target_markets`;
D3 — `service_type`, `monthly_volume`, `transaction_complexity`;
D4 — `industry_sector`;
D5 — `introduction_method`, `delivery_channel`.

### 7.4 `existed_at_decision`

The field that makes replay meaningful. It records whether the evidence was
available **before** the decision timestamp
(`applications.decided_at` / `decision_records.timestamp`).

This distinguishes two very different findings:

- *"The officer approved without this evidence"* — a control failure.
- *"This evidence arrived after approval"* — not a failure at the time, but a
  trigger for review.

Where the timestamp cannot be established, `existed_at_decision` is `null` and
any finding depending on it must carry `status: not_replayable`.

### 7.5 PII discipline

`evidence_refs` carry **addresses and assertions, not personal data**. `value`
is populated only for non-sensitive scalars (a percentage, a score, a state
token, a count). Names, identifiers, addresses and dates of birth are
referenced, never copied. This keeps the finding set safe to hash, log, export
and — later — pass to a narration model. It also keeps `gdpr_erasure.py`
semantics intact: erasing the underlying record makes the reference dangle
rather than leaving a hidden copy.

---

## 8. Supervisor output contract

### 8.1 Structure

```
review_id
subject_type, subject_id
as_of
policy_version
probe_set_version
input_bundle_hash
review_hash                     ← deterministic (§10)

1  overall_assessment
   • findings_by_severity: {critical, high, medium, low, info} counts
   • material_concern_count
   • NO verdict, NO score, NO recommendation

2  review_completeness
   • probes_run / probes_total
   • by status: hit, clear, unavailable, not_applicable, not_replayable
   • coverage_caveats[]        ← every domain that could not be reviewed

3  material_concerns[]          ← severity critical|high, ordered
4  critical_findings[]          ← severity critical
5  contradictions[]             ← findings whose probe compares two sources
6  missing_evidence[]           ← category F-16, plus expectation refs
7  policy_deviations[]          ← categories F-19, F-20
8  decision_inconsistencies[]   ← categories F-22, F-23, F-25
9  officer_questions[]          ← deduplicated officer_question values
10 potential_regulatory_challenges[]
                                ← derived view of 3–8; NO new findings
11 required_actions[]           ← deduplicated required_action values
12 close_conditions[]           ← close_condition per open finding
13 unavailable_checks[]         ← status unavailable | not_replayable,
                                  each with availability_status and reason
14 review_history[]             ← prior reviews of this subject: as_of,
                                  policy_version, review_hash, finding delta
15 evidence_index[]             ← every EvidenceRef cited, deduplicated
```

Sections 3–8 are **projections of the single finding list**, not independent
content. A finding appears in `material_concerns` *and* `policy_deviations`
if it qualifies for both; it is stored once and referenced by `finding_id`.

### 8.2 Prohibited output

The contract has **no free-prose field at the top level**. There is no
`summary`, no `assessment_narrative`, no `overall_opinion`. Banned constructions
include:

- "The application appears satisfactory."
- "Consider further review."
- "Risk may be elevated."
- "No significant issues identified." — replaced by explicit
  `review_completeness` counts, which cannot hide unavailable checks.

Every sentence the officer reads is either a `claim`, a `why_it_matters`, an
`officer_question`, a `required_action`, or a `close_condition` — each attached
to a finding, each attached to evidence.

### 8.3 Narration boundary (future phase — not implemented in 0B)

When narration is introduced:

| Rule | Enforcement point |
|---|---|
| The narrator receives the finding list only — never the case, documents, or party records | Function signature takes `List[Finding]` |
| Output is prose fields only | Response schema has no severity, category, status, confidence or verdict field |
| The finding set is identical before and after narration | Post-generation set comparison; mismatch discards the narration and logs it |
| Narration is excluded from `review_hash` | Hash computed before narration |
| Template fallback always available | Works under `CLAUDE_MOCK_MODE` and provider outage |

---

## 9. Phase 0B probe recommendation

### 9.1 Selection principle

**Seven probes.** Every one reads an explicit engine state or performs exact
arithmetic. None depends on keyword matching, semantic judgement, or an
uncredentialed provider. None restates an existing screen.

The bar applied: *would a competent SCO be surprised, and is the probe wrong
less than one time in twenty?*

### 9.2 The Phase 0B set

---

**P-01 — Ownership reconciliation** · category `F-03` · severity `critical`/`high`

- **Control objective:** beneficial ownership is complete and reconciles.
- **Question:** do declared UBO holdings account for the entity's ownership?
- **Source fields:** `ubos.ownership_pct`, `ubos.id`, `intermediaries.id`,
  `applications.entity_type`, `applications.ownership_structure`.
- **Source functions:** direct table read; `rule_engine._ownership_transparency_tier()`
  for context only.
- **Deterministic logic:** sum `ownership_pct` across UBO rows for the
  application. Hit when the residual exceeds a configured tolerance and the
  entity type is not on an exemption list (listed entity, widely-held fund).
- **Evidence refs:** `ubo:{id}` per party with its `ownership_pct`;
  `application:{id}#entity_type`.
- **Severity logic:** `critical` when residual ≥ the UBO disclosure threshold
  (an unidentified person could be a UBO); `high` otherwise.
- **Availability:** `data_absent` when no UBO rows exist — reported as
  `unavailable`, since zero UBOs is itself unreviewable rather than clear.
- **Limitations:** the UBO threshold is **not currently encoded anywhere** in
  the backend. Phase 0B must introduce it as a probe-local constant and flag it
  for policy ownership. Exemption list must be founder-approved.
- **Fixtures:** exact 100%; residual 26%; residual 3% (tolerance); zero UBOs;
  listed-entity exemption; intermediary-held chain.
- **Closure:** holdings reconcile, or the residual is documented and accepted.

---

**P-02 — Risk factor resolution integrity** · category `F-18`/`F-10`/`F-11` · severity `high`

- **Control objective:** the risk score is computed from resolved, controlled inputs under a valid config.
- **Question:** did any scored factor silently default instead of resolving?
- **Source fields:** `factor_computation_evidence.factors[].resolution_status`,
  `.factor_key`, `.raw_value`, `.rule_score`; `controlled_mapping_evidence`;
  `service_selection_evidence.resolution_status`;
  `applications.risk_config_version`; `applications.risk_escalations`,
  `elevation_reason_text`.
- **Source functions:** `rule_engine.compute_risk_score()`,
  `risk_controlled_values.unresolved_mapping_sentinel()`,
  `_is_valid_risk_config_version()`, `risk_model_view`.
- **Deterministic logic:** hit for each factor whose `resolution_status` is not
  resolved; hit when `risk_config_version` is absent, invalid, or a staleness
  sentinel (`stale:recompute_failed`, `stale:cm_recompute_pending`); hit when a
  floor or elevation was applied with an empty `elevation_reason_text`.
- **Evidence refs:** `risk:{app}#factor:{factor_key}`, `risk_config:{version}`.
- **Severity logic:** `high` when the unresolved factor contributes to a
  level-changing elevation; `medium` otherwise.
- **Availability:** `data_absent` when `factor_computation_evidence` is missing
  (pre-dates `risk-factor-evidence-v1`) → `not_replayable`.
- **Limitations:** only covers factors the engine instruments; a factor absent
  from the evidence blob is invisible.
- **Fixtures:** unresolved country alias; unresolved sector; unresolved service
  selection; `stale:recompute_failed`; elevation with no reason; fully clean case.
- **Closure:** all scored factors resolve under a valid config version.

---

**P-03 — EDD routing divergence** · category `F-19`/`F-20` · severity `critical`

- **Control objective:** EDD triggers were evaluated and honoured.
- **Question:** re-running the governed policy on the stored facts, does it
  route to EDD — and did the case go there?
- **Source fields:** the 8 `REQUIRED_FACT_KEYS`; `edd_cases.application_id`,
  `.stage`; memo `metadata.edd_routing`.
- **Source functions:** `edd_routing_policy.evaluate_edd_routing()` (**pure**),
  `assert_routing_invariant()`, `minimum_risk_level_for_routing()`.
- **Deterministic logic:** rebuild the fact dict from stored state, call the
  pure policy, compare `route` to the existence of an `edd_cases` row. Hit on
  divergence. Separately hit when `TRIGGER_INCOMPLETE_CONTRACT` fires, meaning
  the fact contract was incomplete when routing occurred. Separately hit when
  `assert_routing_invariant()` returns a violation.
- **Evidence refs:** `edd_policy:{version}#trigger:{trigger}` per fired trigger;
  `edd_case:{id}` or its absence; the risk-factor refs behind each fact.
- **Severity logic:** `critical` for route divergence on an approved case;
  `high` for an incomplete contract.
- **Availability:** `snapshot_incomplete` when any required fact cannot be
  reconstructed → `not_replayable`.
- **Limitations:** reconstructing the fact dict is only faithful for current
  state; historic reconstruction is bounded by §11.
- **Fixtures:** policy says EDD / no case; policy says standard / EDD case
  exists; incomplete contract; invariant violation; matching pair.
- **Closure:** case routed to EDD, or divergence formally accepted by an SCO.

---

**P-04 — Screening reliance defensibility** · category `F-12`/`F-13`/`F-15` · severity `critical`/`high`

- **Control objective:** the approval relies on live, terminal, current screening.
- **Question:** was the screening relied upon actually defensible?
- **Source fields:** `canonical_state`, `provider_mode`, `terminal`,
  `defensible_clear`, `screening_gate_ready`, `has_uncleared_completed_match`,
  `has_formally_cleared_match`, `has_stale`, `freshness`, `required_evidence[]`.
- **Source functions:** `screening_state.build_screening_truth_summary()`,
  `build_screening_terminality_summary()`, `derive_screening_truth()`,
  `_is_false_positive_clearance()`, `_review_second_signoff_satisfied()`;
  `environment.get_screening_validity_days()`.
- **Deterministic logic:** hit when `provider_mode` is `sandbox_provider` or
  `simulated_fallback`; hit when a completed match is uncleared; hit when a
  match was cleared without second sign-off; hit when the screening was stale at
  the decision timestamp. **Distinct sub-check:** hit when the `adverse_media`
  risk factor scored 1 (clear) while no screening data existed — the
  absence-means-clear default identified in domain 17.
- **Evidence refs:** `screening:{app}#subject:...` per required subject;
  `screening_disposition:{id}`; `risk:{app}#factor:adverse_media`.
- **Severity logic:** `critical` when a non-live or uncleared state supported an
  approval; `high` otherwise.
- **Availability:** `data_absent` when no screening record exists → `unavailable`.
- **Limitations:** adverse media is limited to signals inside screening-provider
  payloads; there is no external adverse-media source. The probe must state this
  in `why_it_matters` rather than implying full coverage.
- **Fixtures:** simulated fallback at approval; uncleared completed match;
  cleared without second sign-off; stale at decision; adverse-media clear on
  absent data; fully clean live terminal case.
- **Closure:** terminal live screening within the validity window, all matches
  dispositioned with sign-off.

---

**P-05 — Declared PEP evidence gap** · category `F-14` · severity `high`

- **Control objective:** declared PEP exposure carries its required evidence.
- **Question:** is there a declared PEP with no PEP declaration and no bank reference?
- **Source fields:** `directors.is_pep` / `pep_declaration`,
  `ubos.is_pep` / `pep_declaration`; `documents.doc_type` in
  (`pep_declaration`, `bankref_pep`) with `verification_status`.
- **Source functions:** `rule_engine._party_has_declared_or_confirmed_pep()`,
  `_declared_pep_score_evidence()`, `GATE0_DECLARED_PEP_SCORE`;
  `verification_matrix` doc definitions.
- **Deterministic logic:** for each party where PEP is declared or confirmed,
  hit when no current, verified `pep_declaration` document is linked to that
  party; separate lower-severity hit when no `bankref_pep` exists.
- **Evidence refs:** `director:{id}` / `ubo:{id}`; `document:{id}` or the absent
  expectation; `risk:{app}#factor:pep_status`.
- **Severity logic:** `high` for a missing declaration; `medium` for a missing
  bank reference.
- **Availability:** always available — reads only stored state.
- **Limitations:** `pep_declaration` and `bankref_pep` are **not** in
  `build_required_document_expectations()`, so no gate ever demanded them. That
  is exactly why the gap exists, and the probe should say so.
- **Fixtures:** declared PEP with full evidence; declared PEP with neither;
  declared PEP with declaration but no bank reference; screening-detected PEP
  with no declaration; no PEP.
- **Closure:** verified PEP declaration and bank reference linked to the party.

---

**P-06 — Monitoring commitment not established** · category `F-26` · severity `high`

- **Control objective:** monitoring commitments made at approval were actually created.
- **Question:** the memo commits to enhanced monitoring — was a review scheduled, at the right frequency?
- **Source fields:** memo `sections["ongoing_monitoring"]`,
  `metadata["conditions"]`, `metadata["risk_rating"]`, `final_risk_level`;
  `periodic_reviews.next_review_date`, `.policy_version`, `.trigger_type`.
- **Source functions:** `periodic_review_policy.RISK_FREQUENCY_MONTHS`,
  `ENHANCED_REVIEW_FLOOR_MONTHS`; `periodic_review_engine`.
- **Deterministic logic:** key on `final_risk_level` (**not** on parsing the
  generated prose). Hit when the risk level requires a review frequency and no
  `periodic_reviews` row exists for the application; hit when `next_review_date`
  exceeds the policy frequency for that level.
- **Evidence refs:** `memo:{id}#section:ongoing_monitoring`;
  `periodic_review:{id}` or its absence; `application:{id}#final_risk_level`.
- **Severity logic:** `high` when no schedule exists on an approved case;
  `medium` when the frequency is longer than policy.
- **Availability:** `not_applicable` for non-approved cases.
- **Limitations:** commitments live in generated prose, so the probe verifies
  *policy-required* monitoring rather than *memo-stated* monitoring. Where the
  memo promises more than policy requires, the probe will not catch the
  shortfall. State this limitation in the finding.
- **Fixtures:** HIGH risk approved with no review; approved with review beyond
  frequency; approved with correct review; non-approved case.
- **Closure:** review scheduled at or within the policy frequency.

---

**P-07 — Evidence–risk coupling** · category `F-16`/`F-06` · severity `high`

- **Control objective:** every materially elevated risk factor is corroborated by verified evidence.
- **Question:** which risk factors scored high with nothing verified behind them?
- **Source fields:** `factor_computation_evidence.factors[]` (`rule_score`,
  `factor_key`); `document_reliance_gate` snapshots and blockers;
  `documents.verification_status`, `is_current`.
- **Source functions:** `compute_risk_score()`,
  `document_reliance_gate.evaluate_document_reliance_gate()`,
  `build_required_document_expectations()`.
- **Deterministic logic:** a **static, founder-approved mapping** from
  `factor_key` → corroborating document slot(s). Hit when the factor's
  `rule_score` ≥ 3 and no document in the mapped slots is in an allowed
  reliance state (`verified`, `manual_accepted`). Initial mapping:
  `source_of_wealth` → `sow`; `ownership_structure` → `structure_chart`,
  `reg_sh`; `pep_status` → `pep_declaration`, `bankref_pep`;
  `entity_type` → `cert_inc`, `memarts`.
- **Evidence refs:** `risk:{app}#factor:{factor_key}` with its score;
  `expectation:{slot_key}` with its reliance state.
- **Severity logic:** `high` — an elevated factor with no evidence is the
  archetypal inspection finding.
- **Availability:** `data_absent` when `factor_computation_evidence` is absent.
- **Limitations:** the mapping is a policy artifact, not a derivation. It must
  be reviewed and signed off, and versioned with the probe set.
- **Fixtures:** high SOW score with no `sow` document; high SOW with verified
  `sow`; high ownership score with no structure chart; PEP score 4 with no
  declaration; all-low case.
- **Closure:** a corroborating document reaches an allowed reliance state.

---

### 9.3 Classification of the full candidate set

**Ready now (Phase 0B):** P-01, P-02, P-03, P-04, P-05, P-06, P-07.

**Requiring gated `supervisor/` package assets — deferred:** cross-agent
contradiction detection (`supervisor/contradictions.py`, 9 checks), UBO chain
mapping and registry cross-verification from Agents 2 and 4. These sit behind
`_enterprise_scope_permitted()` and are refused in any pilot deployment
regardless of flag state. Phase 0B must not depend on them.

**Requiring missing historic snapshots — bounded:** any probe evaluated
retrospectively over historic decisions (see §11). Phase 0B evaluates **current
state only**; historic replay is a later phase and requires the snapshot work in
§11.3.

**Too vague or unreliable — excluded:** business model plausibility (domain 9),
expected-activity plausibility (domain 10), semantic override-quality scoring
(domain 26), free-text opacity detection standing alone (domain 5).

**Not feasible — no data:** control other than ownership (domain 4),
source-of-funds corroboration (domain 8).

**Deferred as restatement:** approval readiness (domain 27), memo validation
status (domain 24), plain document-blocker listing (domain 18 raw).

### 9.4 Mapping from the previous C1–C12 set

| Old | Disposition |
|---|---|
| C1 Evidence sufficiency | **Kept** as P-07, tightened to a signed-off factor→slot mapping |
| C2 Unsupported conclusion | **Dropped** — requires assertion-to-artifact linkage that memo sections do not carry |
| C3 Screening vs documents | **Dropped as standalone** — needs the field-level extraction schema that does not exist (domain 19) |
| C4 Ownership arithmetic | **Kept** as P-01, and moved off the gated Agent 4 onto direct `ubos.ownership_pct` reads |
| C5 Registry divergence | **Deferred** — credential-gated and needs a name-matching utility |
| C6 Risk-score divergence | **Reframed** as P-02 — resolution integrity, not score comparison. The original framing risked creating a competing score |
| C7 Peer divergence | **Deferred** to Institution Memory, unchanged |
| C8 Policy adherence | **Kept and promoted** to P-03, the highest-value probe |
| C9 Staleness | **Absorbed** into P-02 (config staleness) and P-04 (screening staleness) |
| C10 Override scrutiny | **Narrowed** to exact-match reason reuse; semantic scoring dropped |
| C11 Missing evidence set | **Absorbed** into P-07; the standalone list was a restatement |
| C12 Inspector lens | **Kept** as output section 10 — a derived view, no independent findings |
| — | **New:** P-05 declared-PEP evidence gap |
| — | **New:** P-06 monitoring commitment not established |
| — | **New:** the adverse-media-clear-on-absent-data sub-check inside P-04 |

---

## 10. Reproducibility contract

### 10.1 The invariant

```
identical canonical input bundle
  + identical policy_version
  = identical finding set
  + identical review_hash
```

Narration is outside the hash. Prose may vary; findings may not.

### 10.2 Canonical input bundle

A read-only snapshot assembled before any probe runs. No probe may read the
database directly — every probe is a pure function of the bundle. This is what
makes the invariant testable, and it is a hard architectural requirement, not a
convention.

| Section | Required contents |
|---|---|
| `meta` | `subject_type`, `subject_id`, `as_of`, `bundle_schema_version` |
| `application` | Whitelisted `applications` columns, `prescreening_data` |
| `parties` | `directors[]`, `ubos[]`, `intermediaries[]` — PII fields excluded, replaced by stable IDs and non-sensitive scalars (`ownership_pct`, `nationality`, `is_pep`) |
| `documents` | Active document rows (`is_current = TRUE`) with `doc_type`, `slot_key`, `verification_status`, `expiry_date`, `version`, `file_sha256` |
| `document_gate` | Output of `evaluate_document_reliance_gate()` with `generated_at` **stripped** |
| `screening` | Output of `build_screening_truth_summary()` |
| `risk` | `factor_computation_evidence`, `controlled_mapping_evidence`, `risk_config_version`, `final_risk_level`, `escalations`, `elevation_reason_text` |
| `edd` | The `REQUIRED_FACT_KEYS` fact dict; `edd_cases` rows |
| `memo` | Latest memo `metadata` and section keys, `version`, `raw_output_hash`, `validation_status` |
| `supervisor_verdict` | `run_memo_supervisor()` output with `checked_at` **stripped** |
| `decision` | `decision_records` rows; `applications.decided_at`, `decision_by` |
| `monitoring` | `periodic_reviews` rows for the subject |
| `availability` | Per-dependency availability flags: registry credentials, gated modules, provider configuration |

### 10.3 Canonical JSON rules

1. UTF-8, no BOM.
2. Object keys sorted lexicographically at every level.
3. No insignificant whitespace (`separators=(',', ':')`).
4. Floats serialised at fixed precision — **4 decimal places**, matching the
   rounding already used in `factor_computation_evidence`.
5. `null` is preserved and is **not** equivalent to an absent key.
6. Arrays sorted by a declared stable key per section (documents by `id`,
   parties by `id`, factors by `factor_key`, findings by
   `(probe_id, category, primary evidence ref)`).
7. Booleans normalised to JSON `true`/`false` — never `1`/`0`, never `"true"`.

```
input_bundle_hash = sha256(canonical_json(bundle))
review_hash       = sha256(canonical_json({
                        policy_version, probe_set_version,
                        input_bundle_hash, findings   # sorted, narration-free
                    }))
```

### 10.4 Timestamps — the main determinism hazard

Three known non-determinism sources exist in the modules the Supervisor reads:

| Source | Problem | Treatment |
|---|---|---|
| `evaluate_document_reliance_gate()` calls `datetime.now(timezone.utc)` for `generated_at` **and derives staleness from it** | Same case yields different gate output on different days | Strip `generated_at`; **derive staleness inside the probe from the injected `as_of`**, not from the gate's internal clock. Phase 0B must not call the gate for staleness |
| `run_memo_supervisor()` emits `"checked_at": datetime.now().isoformat()` | Hash-breaking field | Strip before hashing |
| `Finding.created_at` | Would make every run unique | Set from the injected `as_of`, never wall-clock |

**Rule:** no probe may call a clock. `as_of` is the only time source, and it is
an input.

### 10.5 Missing data

Absent data is a **first-class state**, never a default. A probe that cannot
read a required bundle key emits `status: unavailable` with the corresponding
`availability_status` — it does not emit `clear`, and it does not skip silently.
The absent key is recorded in the bundle as an explicit `null` so the hash
reflects the absence.

### 10.6 Deterministic `finding_id`

`finding_id` must be derived, not random, or the hash breaks between runs:

```
finding_id = sha256(canonical_json({
                 subject_type, subject_id,
                 probe_id, probe_version,
                 category,
                 primary_evidence_ref
             }))[:16]
```

This also makes findings stable across reviews, which is what
`review_history[]` (output section 14) needs to compute a delta.

### 10.7 Distinguishing the five causes of divergence

When two reviews of the same subject differ, the cause must be identifiable
without investigation:

| Cause | Signature | Correct handling |
|---|---|---|
| **Engine non-determinism** | Same `input_bundle_hash`, same `policy_version`, different `review_hash` | **A bug.** The CI gate in §10.8 exists to make this impossible to ship |
| **Source-data change** | Different `input_bundle_hash`, same `policy_version` | Expected. Diff the bundles to show what changed |
| **Policy-version change** | Same `input_bundle_hash`, different `policy_version` | Expected. This is the Policy Replay primitive |
| **Incomplete historic snapshot** | Bundle assembled with `availability.snapshot_incomplete` | Affected findings carry `not_replayable` |
| **Unavailable dependency** | `availability` flags show a gated module or absent credentials | Affected findings carry `unavailable` + `availability_status` |

### 10.8 The CI gate

A single test decides whether Phase 0B is real:

> Assemble the bundle for each fixture case once. Run the probe set three
> times. Assert identical `review_hash` on all three, and identical
> `finding_id` sets.

If this cannot be made to pass, the Supervisor is not shippable as specified,
and that must be discovered in Phase 0B week one — not in an inspection.

---

## 11. Historic replay limitations

An honest assessment, because it constrains what can be promised commercially.

### 11.1 What **is** recoverable

| Artifact | Why |
|---|---|
| Documents | `documents.is_current`, `version`, `superseded_by_document_id`, `superseded_at`, `file_sha256` give true version history |
| Memos | `compliance_memos.version`, `raw_output_hash`, `validation_status` are versioned rows |
| Decisions | `decision_records` is append-only and immutable by design |
| Screening | `screening_reports_normalized` carries `source_screening_report_hash` and `normalized_version`; `screening_report_archive` retains history |
| Verdict chain | `supervisor_audit_log` is hash-chained (`previous_hash` / `entry_hash`) |
| Registry lookups | `company_registry_lookups` retains `raw_response_json` and `response_hash` |

### 11.2 What is **not** recoverable

**`applications` is mutated in place.** There is no application-snapshot table.
`prescreening_data`, `risk_score`, `risk_level`, `risk_dimensions`,
`final_risk_level`, `base_risk_level` and `elevation_reason_text` are
overwritten on every recompute. `risk_config_version` records *which* config was
used, but the *input values* the config was applied to are gone.

`directors`, `ubos` and `intermediaries` are likewise mutable with no history.

**Consequence:** for a historic decision, the Supervisor cannot reconstruct the
application state as it stood at `decided_at`. Probes P-01, P-02, P-03 and P-07
all depend on that state.

### 11.3 The consequence for phasing

- **Phase 0B evaluates current state only.** No retrospective claims.
- Any historic review must mark affected findings `not_replayable` with
  `availability_status: snapshot_incomplete`.
- Meaningful historic replay — and therefore Policy Replay — requires a
  **decision input snapshot** written at decision time. That is a schema change
  and belongs to a later phase, but it should be scheduled **early**, because
  replay depth only starts accruing from the day it ships. Every month of delay
  is a permanently missing month of replayable history.

This is the single most important sequencing insight in Phase 0A.

---

## 12. Commercial value assessment

### 12.1 Value test applied to the Phase 0B set

| Probe | Identifies something missed | Prevents a weak approval | Improves evidence quality | Reduces senior-review time | Demonstrates oversight | Audit / inspection ready | Surfaces inconsistent officer behaviour |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| P-01 Ownership reconciliation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| P-02 Risk factor integrity | ✅ | ✅ | — | ✅ | ✅ | ✅ | — |
| P-03 EDD routing divergence | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ |
| P-04 Screening defensibility | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| P-05 Declared PEP evidence gap | ✅ | ✅ | ✅ | — | ✅ | ✅ | — |
| P-06 Monitoring commitment | ✅ | — | — | — | ✅ | ✅ | ✅ |
| P-07 Evidence–risk coupling | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |

Every probe clears the "identifies something missed" bar. Any future probe that
cannot must not ship.

### 12.2 What the Supervisor will identify that RegMind does not already show clearly today

This is the section to read if you read nothing else.

1. **Ownership that does not reconcile.** No screen in RegMind sums
   `ubos.ownership_pct`. A 74% total renders exactly as legibly as 100%. An
   inspector's first question on a corporate file is "who owns the rest?", and
   today nothing asks it.

2. **Risk factors that defaulted instead of resolving.** `resolution_status`
   and the unresolved-mapping sentinels are computed on every scoring run and
   stored inside `factor_computation_evidence` — and surfaced nowhere. A case
   can be rated LOW because a country alias failed to resolve, and the file
   looks identical to one rated LOW on merit.

3. **EDD policy divergence.** `evaluate_edd_routing()` is pure and versioned, so
   it can be re-run at any time against the same facts. Nothing in the product
   does this. A case where the policy says EDD and the case went standard is
   invisible today, and is the finding most likely to be material in a
   supervisory visit.

4. **A declared PEP with no PEP declaration on file.** `pep_declaration` and
   `bankref_pep` exist as document types in `verification_matrix` but are absent
   from `build_required_document_expectations()`, so no gate has ever demanded
   them. The gap is structural, not incidental.

5. **Adverse media scored clear because there was no data.** `d1_adverse`
   defaults to **1 (clear)** when screening data is absent. On screen this reads
   as a clean adverse-media assessment. It is an absence, not a clearance, and
   the distinction is currently invisible.

6. **Approval on sandbox or simulated screening.** `provider_mode` distinguishes
   `live_provider`, `sandbox_provider` and `simulated_fallback`. The
   time-of-decision question — *what mode was in force when this was approved?* —
   is not asked anywhere.

7. **Monitoring promised but never scheduled.** The memo commits to enhanced
   monitoring; whether a `periodic_reviews` row was created at the policy
   frequency is not checked against that commitment.

8. **A materially elevated risk factor with no verified evidence behind it.**
   The score and the document list are both on screen — the *coupling* between
   them is not.

### 12.3 Outputs explicitly removed as restatements

| Candidate | Already shown by |
|---|---|
| Approval-readiness blocker list | Case Command Centre |
| Raw document-reliance blocker list | Document sections / approval gates |
| Memo validation status and issues | Memo screen |
| Screening queue state | Screening Queue (validated, change-controlled) |
| Supervisor verdict restatement | Application Review memo panel |
| Risk score and level restatement | Risk panel |

---

## 13. Future compatibility

Phase 0A commits to four primitives. This section confirms each extends without
rewrite.

| Future capability | What it needs | Provided by Phase 0A | Rework risk |
|---|---|---|---|
| **Institution Memory** | Findings comparable across subjects over time | Deterministic `finding_id`; closed category register; stable evidence addressing | **None** — an index over existing findings |
| **Peer divergence** | Compare a subject against similar prior subjects | Category + severity + `factor_key`-level evidence refs give the comparison keys | **None** — new probe, existing schema |
| **Continuous customer supervision** | Review a customer, not an application | `subject_type: customer` reserved; evidence types already span monitoring and review tables | **Low** — needs a customer-scoped bundle assembler |
| **Monitoring events** | Review an alert as a subject | `subject_type: monitoring_event`; `monitoring_alert:{id}` evidence type defined | **Low** — same |
| **Periodic reviews** | Review a review | `subject_type: periodic_review`; `periodic_review:{id}` evidence type defined; `policy_version` already stamped on `periodic_reviews` | **Low** |
| **Change requests** | Review a change before implementation | `subject_type: change_request`; `change_management.py` already versions approved profiles | **Low** |
| **Portfolio control themes** | Aggregate findings across the book | Closed category register + severity + workflow owner make aggregation a group-by | **None** |
| **Policy replay** | Re-run findings under a different policy version | `review_hash` explicitly separates `input_bundle_hash` from `policy_version` (§10.7) — replay is "same bundle, different policy" | **None in design.** Blocked in practice by §11.2 until decision input snapshots exist |
| **Inspection readiness** | Export findings as evidence | Output contract sections 13–15 (`unavailable_checks`, `review_history`, `evidence_index`) are designed for it; slots into `evidence_pack_export.py` export types | **None** |

The subject model, taxonomy, finding schema and evidence model are all
subject-agnostic. Nothing in Phase 0A hard-codes "application".

---

## 14. Open founder decisions

### 14.1 Changes from the existing Challenge Mode spec requiring acknowledgement

| # | Change | Rationale |
|---|---|---|
| 1 | Challenge Mode is now **one capability inside** the Supervisor, not the whole product | Positioning; prevents the Supervisor collapsing into a single panel |
| 2 | Probe set reduced from 12 to **7**, with 5 dropped and 2 added | Reliability over coverage; every dropped probe is justified in §9.4 |
| 3 | "Risk-score divergence" (old C6) **reframed** as resolution integrity | The original framing implied a competing score, which conflicts with §2.1 |
| 4 | Phase 0B is **current state only** — no historic replay | §11.2: application state is not snapshotted |
| 5 | Findings now carry `not_replayable` as a distinct status | Required by 4 |

### 14.2 Decisions required before Phase 0B starts

| # | Decision | Why it blocks | Recommendation |
|---|---|---|---|
| **D1** | **UBO disclosure threshold** (25% / 10% / other) and the entity-type exemption list for P-01 | The threshold is not encoded anywhere in the backend; P-01 cannot compute severity without it | 25% with exemptions for listed entities and widely-held funds |
| **D2** | **Sign off the `factor_key` → document slot mapping** for P-07 | It is a policy artifact, not a derivation; it must be owned | Approve the four initial mappings in §9.2 P-07; extend later |
| **D3** | **Decision input snapshot** — schedule it now or accept permanent loss of replay history | Every month without it is a permanently unreplayable month (§11.3) | Schedule for the phase immediately after 0B |
| **D4** | **Enterprise-veto exemption** — should the Supervisor be visible in pilot? | `_enterprise_scope_permitted()` refuses the gated `supervisor/` package in any pilot regardless of flags. Phase 0B avoids that package entirely, but the eventual surface needs a decision | Exempt: the Supervisor is non-authoritative and read-only |
| **D5** | **Required-document policy** — should `sow`, `pep_declaration` and `bankref_pep` become required expectations? | P-05 and P-07 exist *because* they are not required. Making them required would move enforcement into the gate and change the Supervisor's role for those checks | Keep them non-required for now; let the Supervisor surface the gap first, then decide with evidence |
| **D6** | **Adverse-media absence default** — `d1_adverse = 1` on missing data | This is a scoring-behaviour question, not a Supervisor question. The Supervisor can only report it | Report via P-04 in Phase 0B; treat the scoring change as a separate governed change |
| **D7** | **Control-other-than-ownership intake field** (domain 4) | An entire FATF-relevant control domain is currently invisible | Add to the intake roadmap; not a Phase 0B dependency |

---

## 15. Final recommendation

### **Proceed to Phase 0B — with the revisions in §14.1.**

**Why proceed.** The determinism claim is well founded. `evaluate_edd_routing()`
is documented as pure. `build_compliance_memo()` is documented as pure
computation with no DB or HTTP dependency. `compute_risk_score()` is a pure
function of `(app_data, config)` and already emits per-factor evidence under a
declared `schema_version`. `build_screening_truth_summary()` returns a fully
explicit state vector. The reproducibility invariant in §10 is achievable
against real data, not aspirational.

The commercial case is also real: §12.2 lists eight defects the platform
computes today and displays nowhere. That is the entire product thesis, and it
did not require inventing a single new capability.

**Why with revisions.** Three findings from the codebase review change the plan
materially:

1. **Historic replay is not available** (§11.2). The `applications` row is
   mutated in place with no snapshot. Phase 0B must be scoped to current state,
   and the decision input snapshot must be scheduled early or the replay
   proposition erodes by a month every month.

2. **The document reliance gate is not replay-safe** (§10.4). It calls
   `datetime.now()` internally and derives staleness from it. Probes must derive
   staleness from the injected `as_of` rather than calling the gate for it.

3. **Five of the twelve original probes do not survive contact with the data**
   (§9.4) — chiefly because cross-document field comparison depends on an
   extraction schema that `documents.verification_results` does not guarantee.

**What must not be built:** business model plausibility, expected-activity
plausibility, semantic override scoring, control-other-than-ownership,
source-of-funds corroboration, and any output listed in §12.3. Each is either
unreliable, unsupported by data, or already on screen.

**The gate on Phase 0B:** the CI reproducibility test in §10.8. Build the bundle
assembler and that test **first**. If `review_hash` is not stable across three
runs on real fixtures, stop and reassess before writing a single probe beyond
P-01.
