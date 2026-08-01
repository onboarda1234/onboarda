# AI Compliance Supervisor — Independent Review Framework (Phase 0A)

**Status:** Design only. No production code, schema, routes, flags or UI.
**Phase:** 0A — product and control architecture
**Revision:** amended per founder review — approved in principle subject to the
twelve amendments in §14.2, with eight consistency invariants confirmed in
§14.6. All seven decisions D1–D7 resolved (§14.3). **Corrected in the Phase
0B-1 closure pass (A11) and extended for bundle v2 (A12)** so the determinism, `as_of` and privacy sections match
the delivered implementation.
**Supersedes in part:** [`challenge-mode-spec.md`](./challenge-mode-spec.md) (see §14.1)
**Audience:** founder review of the amended specification, prior to Phase 0B build authorisation

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

**No Phase 0B probe changes existing behaviour.** Stated exhaustively, because
it is the invariant every other guarantee rests on — no probe in any 0B stage
may change:

| Domain | Authoritative owner, unchanged |
|---|---|
| Existing workflow and case state transitions | `server.py` handlers, `applications.status` |
| Risk scoring | `rule_engine.compute_risk_score()` — see also D6, §14.4 |
| EDD routing | `edd_routing_policy.evaluate_edd_routing()` and `edd_cases` |
| Document gates | `document_reliance_gate.py` — see also D5, §14.3 |
| Screening | `screening_state.py`, screening providers, dispositions |
| Approval | approval gates, `can_approve`, dual-approval controls |
| Monitoring | `periodic_review_engine.py`, `monitoring_automation.py` |
| Decision authority | the human officer, `decision_records` |

The Supervisor reads all of these and writes to none of them. Enforcement is by
the non-authoritative guard test named in §9.5 stage 0B-2.

See also §3.3.5 — the institution policy contract is an input, not a licence to
build a policy-management subsystem.

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

### 3.3 Institution policy configuration model

**Founder amendment (D1, D2, D5).** The Supervisor reviews a case against **the
institution's active, approved, versioned policy** — never against a universal
rule hard-coded by RegMind. Compliance policy is jurisdiction-specific and
risk-appetite-specific; a threshold that is correct for a Mauritius EMI is not
automatically correct for a UK payments firm or a Luxembourg fund
administrator.

#### 3.3.1 The three-layer hierarchy

This is the governing structure for every evidence and threshold question in
this document:

| Layer | Owner | Responsibility |
|---|---|---|
| **1. Institution policy** | The institution's MLRO / compliance function | *Defines* what is required — thresholds, exemptions, acceptable evidence, conditional requirements |
| **2. Document gate** | `document_reliance_gate.py` (authoritative) | *Enforces* the explicit mandatory requirements that policy has declared |
| **3. Supervisor** | This framework (advisory) | *Reviews* policy configuration completeness, evidence–risk coupling, conditional requirements, and officer reliance |

The Supervisor sits **above** the gate, not beside it. It does not enforce, and
it does not compensate for gaps in enforcement by design. Where an evidence
requirement should be mandatory, the correct remedy is to configure it in policy
and let the gate enforce it — not to leave it unenforced so the Supervisor has
something to find (see D5, §14.3).

The Supervisor's distinct contribution at layer 3 is the class of questions the
gate structurally cannot ask: *is the policy itself complete?* *does the
evidence actually corroborate the risk it is claimed to address?* *was a
conditional requirement triggered and honoured?* *did the officer rely on
something the policy does not accept?*

#### 3.3.2 The policy profile

A versioned, approved configuration object. RegMind ships **deployment
templates**; an institution must review, amend and approve one before it becomes
active. A template value is a proposal, never a default that silently governs a
finding.

```
policy_profile
  policy_version            str    institution-approved, immutable once active
  policy_jurisdiction       str    the regulatory regime the profile encodes
  approved_by               str    approving officer
  approved_at               str
  effective_from            str
  effective_to              str    null while active
  supersedes                str    prior policy_version
  <configuration keys below>
```

#### 3.3.3 Configuration key register (Phase 0B scope)

| Key | Type | Governs | Used by |
|---|---|---|---|
| `ubo_identification_threshold` | percent | The holding at or above which a person must be identified as a beneficial owner | P-01 |
| `ownership_reconciliation_tolerance` | percent | Residual below which no finding is raised | P-01 |
| `ownership_exemption_types` | list | Entity types exempt from full reconciliation (e.g. listed, widely-held fund) | P-01 |
| `control_person_required` | bool | Whether a fallback controller must be identified where no owner meets the threshold | P-01 |
| `pep_evidence_requirements` | map | Per PEP class, which of the seven evidence elements are required (§9.2 P-05) | P-05 |
| `factor_evidence_mapping` | map | Per `factor_key`, the set of evidence types any one of which corroborates it | P-07 |
| `factor_materiality_threshold` | int | The `rule_score` at or above which a factor requires corroboration | P-07 |
| `manual_acceptance_roles` | list | Roles permitted to manually accept evidence (mirrors `MANUAL_ACCEPTANCE_ROLES`) | P-07 |

The register is versioned with the probe set. Adding a key, or changing what a
key governs, is a governed change requiring both a `policy_version` bump by the
institution and a `probe_set_version` bump by RegMind.

#### 3.3.4 The unconfigured-policy rule

**A probe that depends on policy configuration and finds none configured must
emit `status: unavailable` with `availability_status: policy_not_configured`.**

It must not fall back to a template value, a RegMind default, or an inferred
threshold. A finding derived from an unapproved default is a finding the
institution never agreed to be measured against, and it is indefensible in an
inspection — the officer's correct response would be "we never adopted that
rule."

The absence of configuration is itself a reportable condition, surfaced through
category `F-28 policy_configuration` (§5.2) and visible in output section 2
`review_completeness`. Reviewing whether the institution has configured a
complete policy is a legitimate — and genuinely valuable — Supervisor function.

#### 3.3.5 Implementation boundary

> **The institution policy profile defined in Phase 0A is an input contract for
> policy-dependent Supervisor probes. It is not authorisation to build a full
> policy-management subsystem during Phase 0B. Phases 0B-1 and 0B-2 must not
> introduce a policy administration UI, generic rule builder, policy approval
> workflow, operational risk-scoring changes, document-gate changes or
> authoritative enforcement. Only the minimum typed and versioned contract
> needed to represent policy availability may be introduced. Detailed policy
> persistence and administration require separate founder approval.**

**What this permits in 0B-1 and 0B-2.** A typed, versioned representation of the
policy contract — sufficient to record *which* keys a probe requires, *whether*
they are configured, and *which* `policy_version` was in force — and nothing
more. In practice that is a read-only contract definition plus the availability
metadata it feeds into `availability_status`, `F-28` findings, and the
`policy_profile` evidence type.

**What this prohibits in 0B-1 and 0B-2.** Explicitly:

| Prohibited | Why it is out of scope |
|---|---|
| Policy administration UI | Administration is a product surface in its own right, requiring its own design, permissions and change control |
| Generic rule builder | A configurable rule engine is a far larger system than an input contract, and would create a second authority over decisions |
| Policy approval workflow | Approval, versioning and supersession of institution policy is a governance workflow needing separate design |
| Operational risk-scoring changes | `rule_engine` remains authoritative and untouched (see also D6, §14.4) |
| Document-gate changes | `document_reliance_gate` remains authoritative and untouched (see also D5, §14.3) |
| Authoritative enforcement of any kind | The Supervisor is advisory. A policy contract must not become a gate |

**Consequence for §3.3.3.** The configuration key register is a *contract
specification* — it declares what a probe needs and how it is versioned. It is
not a build specification for a configuration management system. How policy is
persisted, edited, approved and superseded is deliberately unspecified here and
requires separate founder approval before any work begins.

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
| 3 | `ubos.ownership_pct`; `intermediaries`; `documents` slot `structure_chart` (DOC-27 UBO Chain, DOC-28 Ownership Match); `rule_engine` factor `ownership_structure` | **Sum of `ownership_pct` (including totals exceeding 100%); count of parties at/above the configured identification threshold; unattributed residual** | No identification threshold, tolerance or exemption list is encoded anywhere in the backend. Per D1 these become **institution-configured policy keys** (§3.3.3), not RegMind constants | Medium if reduced to "must sum to 100%" — legitimate free-float, widely-held fund and co-operative structures do not. Mitigated by policy-configured exemptions and the five-sub-check design in §9.2 P-01 | Low | **0B-3** — gated on policy configuration |
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
| 16 | `directors.is_pep` / `pep_declaration`, `ubos.is_pep` / `pep_declaration`; `rule_engine.GATE0_DECLARED_PEP_SCORE = 4`, `_party_has_declared_or_confirmed_pep()`, `_declared_pep_score_evidence()`; `verification_matrix` doc types **`pep_declaration`**, **`bankref_pep`**; screening PEP state; `decision_records`; `periodic_reviews`; memo `declared_pep_count` | **Presence or absence of each of the seven evidence elements in §9.2 P-05, evaluated against the institution's configured requirement set** | `pep_declaration` / `bankref_pep` are not in `build_required_document_expectations()`, so they are never *required*. Per D5 the remedy is to configure them in policy where genuinely mandatory, not to leave the gate weak. PEP class (domestic / foreign / by association) is not distinguished in the schema | Medium if a fixed document list is assumed — a bank reference is not universally mandatory. Eliminated by driving requirements from `pep_evidence_requirements` | Low | **0B-3** — gated on policy configuration |
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
| F-03 | `ownership` | Ownership coverage falls short of the institution's configured policy, or is internally incoherent | Holdings total 118%; or no party above the configured identification threshold on a non-exempt entity | `critical` if the gap ≥ `ubo_identification_threshold`; else `high`; `medium` for a missing exemption rationale | Party refs with `ownership_pct` + the `policy_profile` keys relied on | Coverage satisfies the active policy, or the shortfall carries a recorded attributed rationale | Officer |
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
| F-14 | `pep` | PEP exposure declared or detected without an evidence element the institution's policy requires | Declared PEP; `pep_evidence_requirements` mandates source-of-wealth evidence; none present | From the configured requirement — mandatory yields `high`, recommended yields `medium`. Never hard-coded per element | Party ref + the specific element's refs + `policy_profile:{version}#pep_evidence_requirements` | Every element the **active policy** requires for that PEP class is present and verified | Officer |
| F-15 | `adverse_media` | Adverse media hit undispositioned, **or scored clear on absent data** | `d1_adverse = 1` because no screening data existed | `high` when scored clear on absence; `medium` otherwise | Screening evidence ref + risk factor ref | Adverse-media state terminal and dispositioned. **Note (D6):** the scoring default itself is a separate governed remediation (§14.4) — the Supervisor reports it, never changes it | Officer |
| F-16 | `documentation` | No artifact accepted by the institution's evidence policy corroborates a materially elevated risk factor | Factor scored 4; none of the accepted evidence types for it is in an allowed reliance state | From the configured materiality band — `high` where the factor drives a level-changing elevation | Risk factor ref + every candidate expectation ref + `policy_profile:{version}#factor_evidence_mapping` | At least one accepted artifact reaches an allowed reliance state under the active policy | Officer |
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
| F-28 | `policy_configuration` | A policy key the Supervisor depends on is unconfigured, expired, or incomplete | No `ubo_identification_threshold` configured; `factor_evidence_mapping` missing entries for scored factors | `high` when it disables a control the institution's regime requires; `medium` otherwise | `policy_profile:{version}#{key}` ref | Key configured and approved under an active policy version | MLRO |

Categories F-04, F-08, F-09, F-17 and F-24 are **defined but have no Phase 0B
probe**. They are reserved so that later probes do not force a taxonomy version
bump.

**F-28 is the taxonomy consequence of §3.3.4.** When a probe cannot run because
policy is unconfigured, two records result: the probe's own
`status: unavailable` / `availability_status: policy_not_configured`, and an
F-28 finding naming the missing key. The first tells the officer the check did
not happen; the second tells the MLRO why, and who can fix it. Reviewing policy
completeness is a Supervisor function in its own right, not merely an error
path.

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
                                 data_absent | snapshot_incomplete |
                                 policy_not_configured
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
| `unavailable` | The probe **could not run** — including because the policy it depends on is unconfigured (§3.3.4) | **Must be shown distinctly.** Never rendered as, aggregated with, or counted as `clear` |
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
| `policy_profile` | `policy_profile:{policy_version}#{config_key}` | institution policy profile (§3.3) | `policy_version` |
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

### 7.5 Privacy discipline — PII-minimised, not anonymous

`evidence_refs` carry **addresses and assertions, not direct identifiers**.
`value` is populated only for non-sensitive scalars (a percentage, a score, a
state token, a count).

**Excluded:** names, dates of birth, residential and registered addresses,
contact details, professional profile URLs, raw extracted document field values,
free-text officer prose, file paths and storage keys.

**Retained, deliberately:** stable internal identifiers (`ubo:{id}`,
`document:{id}`, `person_key`) and name fingerprints used as sort keys. These
are **pseudonymous, linkable identifiers**. They contain no direct identifier,
but they resolve to a natural person by joining the source tables, and a name
fingerprint is reversible by hashing a list of candidate names — the input space
of personal names is small and enumerable.

**Therefore the bundle and the finding set are personal data under GDPR** and
must be access-controlled, retention-bound, and in scope for `gdpr_erasure.py`.
They are not de-identified artifacts, and no part of this system may be
described as "PII-free" or "all PII removed". The correct terms are *direct
identifiers excluded* and *PII-minimised*.

Erasing an underlying record makes a reference dangle rather than leaving a
hidden copy — which is the property that keeps erasure meaningful — but a
dangling reference plus a retained fingerprint is still linkable, so erasure
handling must reach the bundle store when one exists.

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

**Policy dependency splits the set in two.** Four probes (P-02, P-03, P-04,
P-06) read governed engine state and versioned platform policy that already
exists in code — `risk_config_version`, `edd_routing_policy_v1`,
`document_reliance_gate_v2`, `RISK_FREQUENCY_MONTHS`. They can be built and
validated immediately.

Three probes (P-01, P-05, P-07) depend on **institution policy configuration**
that does not yet exist (§3.3). They must not be built against RegMind defaults,
because a finding derived from an unapproved default is one the institution
never agreed to be measured against. This split drives the staging in §9.5.

### 9.2 The Phase 0B set

---

**P-01 — Ownership coverage and unexplained residual** · category `F-03`/`F-05`/`F-28` · severity per sub-check

> **Founder amendment (D1).** This probe does **not** assume ownership must sum
> to 100%. Many legitimate structures do not: listed entities with free float,
> widely-held funds, co-operatives, foundations and partnerships with fluid
> capital accounts. The probe tests **coverage against the institution's
> configured policy**, not arithmetic against a universal rule.

- **Control objective:** beneficial ownership is identified to the standard the
  institution's policy requires, and any shortfall is explained.
- **Question:** does the institution know who ultimately owns and controls this
  entity, to the standard it has committed to — and where it does not, is the
  gap explained?
- **Source fields:** `ubos.ownership_pct`, `ubos.id`, `ubos.full_name`;
  `intermediaries.id`, `.jurisdiction`; `applications.entity_type`,
  `.ownership_structure`; `documents` slot `structure_chart`,
  `reg_sh`; `application_notes` for recorded rationale.
- **Source functions:** direct table reads;
  `rule_engine._ownership_transparency_tier()`,
  `_is_opaque_ownership()` for structural context;
  `document_reliance_gate` reliance state for the structure chart.
- **Policy inputs (§3.3.3):** `ubo_identification_threshold`,
  `ownership_reconciliation_tolerance`, `ownership_exemption_types`,
  `control_person_required`, `policy_jurisdiction`, `policy_version`.

**Five distinct sub-checks**, each its own finding:

| Sub-check | Condition | Category |
|---|---|---|
| **(a) Overstated ownership** | Sum of `ownership_pct` **exceeds 100%** beyond `ownership_reconciliation_tolerance` | `F-03` |
| **(b) No identified beneficial owner** | No party meets `ubo_identification_threshold`, **and** policy expects one — i.e. entity type is not in `ownership_exemption_types` | `F-03` |
| **(c) Unexplained residual** | Unattributed ownership ≥ `ubo_identification_threshold`, with no recorded rationale | `F-03` |
| **(d) Chain does not terminate** | Ownership traces to intermediaries with no natural person, **and** no permitted fallback controller where `control_person_required` is true | `F-05` |
| **(e) Missing exemption rationale** | Entity type **is** in `ownership_exemption_types` but no exemption rationale is recorded on the case | `F-03` |

Sub-check (a) is the only one that is pure arithmetic and policy-independent —
holdings exceeding 100% is a data-integrity defect under any regime. It is the
one sub-check that remains available when policy is unconfigured.

- **Evidence refs:** `ubo:{id}` per party with `ownership_pct`;
  `intermediary:{id}` for chain links; `application:{id}#entity_type`;
  `policy_profile:{version}#ubo_identification_threshold` and the other
  configured keys relied on; `document:{id}` for the structure chart;
  `note:{id}` for a recorded rationale.
- **Severity logic:** `critical` for (b) and (c) where the gap equals or exceeds
  the identification threshold — an unidentified person could be a beneficial
  owner. `high` for (a) and (d). `medium` for (e).
- **Availability:** `policy_not_configured` for sub-checks (b)–(e) when the
  relevant keys are unset, accompanied by an `F-28` finding. `data_absent` when
  no party rows exist at all — reported as `unavailable`, because zero parties
  is unreviewable, not clear.
- **Limitations:** `ownership_pct` is a declared figure; the probe tests
  internal coherence and policy coverage, not truthfulness. Sub-check (d)
  depends on intermediary chain data that is captured but not always complete.
  Recorded rationale detection keys on structured note linkage, not free-text
  interpretation.
- **Fixtures:** sum 118% (a); no party above threshold, non-exempt entity (b);
  residual 26% with threshold 25% (c); residual 3% within tolerance; chain
  terminating in a foreign intermediary with no natural person (d); listed
  entity with recorded exemption; listed entity with no rationale (e); zero
  parties; policy unconfigured.
- **Closure:** ownership coverage satisfies the active, approved policy, or the
  shortfall carries a recorded, attributed rationale.

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
- **D6 boundary:** the adverse-media sub-check **reports** current-state cases
  where a clear rating rests on absent data. It does **not** change
  `rule_engine` scoring. Correcting the `d1_adverse = 1`-on-absence default, and
  moving to the three-state model (`clear` · `adverse_media_detected` ·
  `not_assessed_unavailable`), is a separate governed remediation tracked in
  §14.4.
- **Fixtures:** simulated fallback at approval; uncleared completed match;
  cleared without second sign-off; stale at decision; adverse-media clear on
  absent data; fully clean live terminal case.
- **Closure:** terminal live screening within the validity window, all matches
  dispositioned with sign-off.

---

**P-05 — PEP evidence requirement gap** · category `F-14`/`F-28` · severity per configured requirement

> **Founder amendment (D2).** A bank reference is **not** universally mandatory
> for every PEP. PEP evidence expectations vary by institution, by jurisdiction,
> and by PEP class — a domestic PEP holding a minor public office and a foreign
> head of state do not attract the same file. The probe tests against the
> institution's configured requirement set and **names which specific
> requirement is missing**, rather than asserting a fixed document list.

- **Control objective:** PEP exposure carries the evidence the institution's
  active policy requires for that PEP class.
- **Question:** for each PEP-exposed party, which configured evidence elements
  are absent?
- **Source fields:** `directors.is_pep` / `pep_declaration`,
  `ubos.is_pep` / `pep_declaration`, party `id`, `country_of_residence`;
  `documents.doc_type` with `verification_status` and `is_current`;
  screening PEP state from `build_screening_truth_summary().required_evidence[]`;
  `decision_records` for senior approval; `periodic_reviews` for enhanced
  monitoring; risk factors `source_of_wealth`, `source_of_funds`.
- **Source functions:** `rule_engine._party_has_declared_or_confirmed_pep()`,
  `_declared_pep_score_evidence()`, `GATE0_DECLARED_PEP_SCORE`;
  `screening_adverse_truth` (`STATE_PEP_DETECTED`); `verification_matrix`
  document definitions.
- **Policy inputs (§3.3.3):** `pep_evidence_requirements` — a map from PEP class
  to the subset of the seven elements below that the institution requires,
  plus `policy_jurisdiction` and `policy_version`.

**The seven distinguishable evidence elements.** Each is independently
detectable and independently reportable:

| # | Element | Detected from |
|---|---|---|
| 1 | PEP declaration | current verified `pep_declaration` document linked to the party |
| 2 | Screening evidence | terminal, live PEP screening result for that party |
| 3 | Source-of-wealth evidence | verified artifact accepted under `factor_evidence_mapping[source_of_wealth]` |
| 4 | Source-of-funds evidence | verified artifact accepted under `factor_evidence_mapping[source_of_funds]` |
| 5 | Senior approval | `decision_records` entry by an actor holding the required role |
| 6 | Enhanced monitoring | `periodic_reviews` row at or within the enhanced frequency |
| 7 | Bank reference | current verified `bankref_pep` document — **only where policy requires it** |

- **Deterministic logic:** for each party where PEP is declared or
  screening-confirmed, resolve the party's PEP class, look up the configured
  requirement set, and emit **one finding per missing required element**, naming
  that element. Elements the institution does not require are not evaluated and
  produce no finding.
- **Evidence refs:** `director:{id}` / `ubo:{id}`;
  `policy_profile:{version}#pep_evidence_requirements`;
  `document:{id}` or the absent expectation per element;
  `screening:{app}#subject:...`; `decision:{id}`; `periodic_review:{id}`;
  `risk:{app}#factor:pep_status`.
- **Severity logic:** taken from the configured requirement — an element the
  institution marks mandatory yields `high`; an element marked recommended
  yields `medium`. Severity is **not** hard-coded per element.
- **Availability:** `policy_not_configured` when `pep_evidence_requirements` is
  unset, accompanied by an `F-28` finding. The probe does **not** fall back to
  assuming declaration + bank reference.
- **Limitations:** PEP class must be derivable from stored state. Where the
  schema does not distinguish domestic from foreign PEP, or PEP by association
  from principal, the probe evaluates against the institution's default class
  and must say so in the finding. Refining PEP classification is a later intake
  question, not a Phase 0B dependency.
- **Fixtures:** declared PEP, all configured elements present; declared PEP
  missing element 1 only; missing elements 3 and 5; policy that does **not**
  require element 7 with no bank reference present (must produce **no** finding);
  screening-detected PEP with no declaration; no PEP; policy unconfigured.
- **Closure:** every element the **active, approved institution policy** requires
  for that party's PEP class is present and verified. Closure is evaluated
  against the approved policy version in force, never against a template value
  or a global document list.

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

**P-07 — Evidence–risk coupling** · category `F-16`/`F-06`/`F-28` · severity per configured materiality

> **Founder amendment (D2).** The factor-to-evidence mapping is a **versioned
> institutional policy artifact**, not a RegMind constant. Each factor may have
> **several acceptable evidence types**, any one of which satisfies
> corroboration. No single document type is treated as universally sufficient or
> universally mandatory.

- **Control objective:** every materially elevated risk factor is corroborated
  by at least one artifact the institution's evidence policy accepts.
- **Question:** *"Does a materially elevated factor have at least one verified
  artifact accepted by the institution's active evidence policy?"*
- **Source fields:** `factor_computation_evidence.factors[]` (`factor_key`,
  `rule_score`, `normalized_value`, `resolution_status`);
  `documents.doc_type`, `verification_status`, `is_current`, `person_id`;
  `document_reliance_gate` per-slot reliance snapshots; manual-acceptance
  fields (`workflow_test_accepted*`, `evidence_class`,
  `evidence_classification_note`, `evidence_classified_by`).
- **Source functions:** `compute_risk_score()`,
  `document_reliance_gate.evaluate_document_reliance_gate()`,
  `manual_acceptance_details()`; `ALLOWED_RELIANCE_STATES`.
- **Policy inputs (§3.3.3):** `factor_evidence_mapping`,
  `factor_materiality_threshold`, `manual_acceptance_roles`, `policy_version`.
- **Deterministic logic:** for each factor whose `rule_score` ≥
  `factor_materiality_threshold`, hit when **no** artifact of **any** accepted
  evidence type for that `factor_key` is in an allowed reliance state
  (`verified` or `manual_accepted`). Satisfaction is a disjunction across
  accepted types — one qualifying artifact closes the coupling.
- **Evidence refs:** `risk:{app}#factor:{factor_key}` with its `rule_score`;
  `policy_profile:{version}#factor_evidence_mapping`; every candidate
  `expectation:{slot_key}` with its reliance state; `document:{id}` where an
  artifact exists but is not in an allowed state.
- **Severity logic:** from the configured materiality band for that factor.
  Where the factor drives a level-changing elevation, `high`; otherwise
  `medium`.
- **Availability:** `policy_not_configured` when `factor_evidence_mapping` has
  no entry for a scored factor, accompanied by an `F-28` finding naming the
  factor. `data_absent` when `factor_computation_evidence` is absent.

**Interpretive limits that must appear in the finding text.** These are the
reasons the mapping is a policy artifact rather than a derivation, and the
Supervisor must not overstate what a satisfied coupling proves:

| Limit | Why it matters |
|---|---|
| **A SOW declaration may not by itself corroborate wealth.** A signed declaration is an assertion by the customer, not independent evidence of origin. An institution may accept it alone, or may require corroborating financial records alongside it — that is a policy choice, and the finding must reflect which was configured. | Prevents "coupling satisfied" from reading as "wealth verified" |
| **A certificate of incorporation demonstrates legal existence but may not fully corroborate entity-type risk.** It evidences that the entity exists in a legal form; it says little about the risk that form carries in practice, particularly for trusts, foundations and hybrid vehicles. | Prevents a formation document from closing a substantive risk question |
| **PEP evidence requirements vary by policy.** The coupling for `pep_status` is defined by `pep_evidence_requirements` (P-05), not by a fixed document pair. | Keeps P-05 and P-07 consistent under one policy source |
| **Manual acceptance remains governed by role and rationale.** An artifact in `manual_accepted` state satisfies the coupling **only** when accepted by an actor holding a role in `manual_acceptance_roles`, with a recorded reason, actor and timestamp — the conditions `document_reliance_gate` already enforces. A manual acceptance failing those conditions does not satisfy the coupling, and is itself a finding. | Prevents manual acceptance becoming a silent bypass |

- **Limitations:** the mapping expresses what the institution *accepts as
  corroboration*, not what *proves* the underlying fact. A satisfied coupling
  means the file meets the institution's evidence standard — no more. The
  finding text must not imply substantive verification.
- **Fixtures:** elevated SOW with no accepted artifact; elevated SOW satisfied
  by the second of three accepted types; elevated SOW satisfied only by a
  manual acceptance with a valid role and reason; the same manual acceptance by
  an ineligible role (must **not** satisfy); elevated factor with no mapping
  entry (must yield `F-28`); all-low case; policy unconfigured.
- **Closure:** at least one artifact of an accepted evidence type for that
  factor reaches an allowed reliance state under the active, approved policy version.

---

### 9.3 Classification of the full candidate set

**Ready now (stage 0B-2):** P-02, P-03, P-04, P-06 — governed engine state and
platform policy that already exists in code.

**Ready once institution policy is configured (stage 0B-3):** P-01, P-05, P-07.
Blocked on the `policy_profile` keys in §3.3.3, not on engineering. Building
them earlier against RegMind defaults is explicitly prohibited by §3.3.4.

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
| C1 Evidence sufficiency | **Kept** as P-07, reframed as a versioned institution evidence policy with multiple acceptable types per factor |
| C2 Unsupported conclusion | **Dropped** — requires assertion-to-artifact linkage that memo sections do not carry |
| C3 Screening vs documents | **Dropped as standalone** — needs the field-level extraction schema that does not exist (domain 19) |
| C4 Ownership arithmetic | **Kept but substantially reframed** as P-01 *ownership coverage and unexplained residual* — five policy-driven sub-checks, no universal sum-to-100% assumption. Moved off the gated Agent 4 onto direct `ubos.ownership_pct` reads |
| C5 Registry divergence | **Deferred** — credential-gated and needs a name-matching utility |
| C6 Risk-score divergence | **Reframed** as P-02 — resolution integrity, not score comparison. The original framing risked creating a competing score |
| C7 Peer divergence | **Deferred** to Institution Memory, unchanged |
| C8 Policy adherence | **Kept and promoted** to P-03, the highest-value probe |
| C9 Staleness | **Absorbed** into P-02 (config staleness) and P-04 (screening staleness) |
| C10 Override scrutiny | **Narrowed** to exact-match reason reuse; semantic scoring dropped |
| C11 Missing evidence set | **Absorbed** into P-07; the standalone list was a restatement |
| C12 Inspector lens | **Kept** as output section 10 — a derived view, no independent findings |
| — | **New:** P-05 PEP evidence requirement gap, driven by configured requirements rather than a fixed document pair |
| — | **New:** P-06 monitoring commitment not established |
| — | **New:** the adverse-media-clear-on-absent-data sub-check inside P-04 |
| — | **New:** category `F-28 policy_configuration` and status `policy_not_configured`, the taxonomy consequences of §3.3 |

### 9.5 Phase 0B implementation stages

**Founder amendment (D3, sequencing).** Four stages, strictly ordered. Each
stage has an exit criterion that must be met before the next begins.

---

#### 0B-1 — Deterministic harness *(no probes, no UI)*

The foundation, and the gate on everything after it.

- Canonical input bundle assembler (§10.2)
- Deterministic canonical-JSON serializer (§10.3)
- Injected `as_of` — **no probe may call a clock** (§10.4)
- `input_bundle_hash` and `review_hash`
- Deterministic `finding_id` derivation (§10.6)
- Availability status handling, including `policy_not_configured` (§3.3.4)
- Reproducibility test (§10.8) and frozen-module guard tests

**Policy dependency: none.** 0B-1 does **not** require an approved institution
policy profile. It requires only the minimum typed, versioned contract needed to
*represent policy availability* — enough to record which keys a probe would
require and whether they are configured. Subject to the implementation boundary
in §3.3.5.

**Exit criterion:** three consecutive runs over the fixture corpus produce an
identical `review_hash` and an identical `finding_id` set. If this cannot be
achieved, **stop** — the Supervisor is not shippable as specified.

---

#### 0B-2 — Policy-independent probes

Four probes that read governed state already present in code.

- **P-02** risk factor resolution integrity
- **P-03** EDD routing divergence
- **P-04** screening reliance defensibility
- **P-06** monitoring requirement establishment

**Policy dependency: none.** These four read versioned *platform* policy that
already exists in code — `risk_config_version`, `edd_routing_policy_v1`,
`document_reliance_gate_v2`, `RISK_FREQUENCY_MONTHS`. They do **not** require an
approved institution policy profile, and must not be blocked waiting for one.
Availability metadata is the only policy-contract surface they touch. Subject to
the implementation boundary in §3.3.5.

**Exit criterion:** all four pass the reproducibility harness on real fixtures;
no frozen-module guard test regresses; findings are advisory-only and provably
cannot reach an approval gate — enforced by a guard test asserting no write path
exists to any authority listed in §2.3.

##### 0B-2 as implemented — `probe-set-0b2-v1`

Package `supervisor_foundation/probes/`, exit criteria met. Real-case results,
the three defects the corpus exposed and the SHIP/REVISE/DROP calls are in
[`supervisor-0b2-real-case-validation.md`](supervisor-0b2-real-case-validation.md).
Six deviations from the specification above are worth recording here, because
each was a decision rather than an omission. Items 1 and 5 were found in the
pre-merge closure review, not during implementation:

1. **P-03 compares routes in one direction only.** ``evaluate_edd_routing``
   reads ten fact keys; ``REQUIRED_FACT_KEYS`` — the policy's *completeness*
   contract — names eight, and the bundle projects those. The two it reads but
   does not require are ``sector_label`` and
   ``supervisor_mandatory_escalation_reasons``. The reasons list is recoverable
   from the supervisor-verdict section and is now supplied; ``sector_label`` is
   not carried and doing so is a bundle-contract change.

   An absent input can only *remove* a trigger, so the recomputed route can only
   under-trigger. P-03 therefore reports divergence only where stored is
   ``standard`` and the re-run says ``edd`` — the direction no absent key can
   manufacture. The opposite direction is reported ``not_replayable``, and the
   conservative-over-routing check was removed because it rested entirely on the
   unsafe direction. Measured on the canonical corpus: 15 of 38 evaluable cases
   diverged in trigger set before the reasons fix, 1 after (the crypto case,
   route unaffected); 0 route mismatches either way.

2. **P-04 ships four checks, not five.** A terminality check was implemented and
   removed. `screening_state.derive_screening_state()` derives the state from the
   same provider record `provider_mode_from_record()` reads, so a non-terminal
   state is entailed by a non-live provider mode and the two checks reported one
   fact twice. The derived state is named in the provider finding instead. The
   entailment is asserted across the whole provider-mode vocabulary by
   `test_terminality_is_entailed_by_provider_mode`; if `screening_state` ever
   lets the two diverge, the check is reinstated deliberately.

3. **Findings declare their identity anchor.** §10.6 derives `finding_id` from a
   *primary evidence reference*, which the 0B-1 runner took to be the
   lowest-sorting reference. Every P-04 finding cites the same application-level
   approval references, so several findings on one case collapsed onto a single
   identifier. A probe that can fire more than once per subject now declares
   `primary_evidence_ref` explicitly, naming the field the check examined; the
   runner validates it is one of the finding's own references and records it on
   the output. The fallback is unchanged for single-hit probes.

4. **P-03 calls `evaluate_edd_routing()`.** The clock-hazard register above
   previously said it would not be called in Phase 0B. Re-running the governed
   policy *is* the probe, and its one clock-bearing field is cosmetic and
   excluded from comparison. The register row is updated accordingly.

5. **P-06 governs on open periodic reviews only.** Completing a review inserts
   the next cycle as a new ``pending`` row, so a reviewed customer accumulates
   closed rows whose ``next_review_date`` is in the past. Selecting the earliest
   date across *all* rows — as the first implementation did — would grade a
   closed historical cycle from the second cycle onward and report ``clear``
   while the live schedule went unexamined: a systematic false negative across
   the entire reviewed population. A file holding only closed reviews is now the
   same control gap as a file holding none.

6. **Two silent authoritative fallbacks are refused, not inherited.**
   `periodic_review_policy.parse_review_date()` (§10.4.3) returns today for
   unreadable input, and `periodic_review_policy.normalize_risk_level()` maps any
   unrecognised level to `MEDIUM`. Inheriting either would convert a defective
   record into a compliant-looking one — a corrupt date read as "scheduled
   today", or a governed 24-month cycle attributed to an ungoverned level. P-06
   parses dates itself and refuses the frequency lookup for a level outside
   `RISK_FREQUENCY_MONTHS`. The second is reachable in practice:
   `applications.risk_level` carries a CHECK constraint on the four governed
   levels and `final_risk_level`, the column the probe reads, does not.

**Not addressed, by instruction:** the five-field cross-section duplication and
the risk/decision null-versus-absent inconsistency deferred at bundle v2.

---

#### 0B-3 — Policy-dependent probes

**Blocked until the institution's detailed `policy_profile` mappings are defined
and approved.** This is a compliance-configuration task, not an engineering one,
and it can proceed in parallel with 0B-1 and 0B-2. Approval of the *contract*
in Phase 0A is not approval of the *mappings* — D2 is approved in principle with
the detailed mapping still pending (§14.3).

- **P-01** ownership coverage and unexplained residual
- **P-05** PEP evidence requirement gap
- **P-07** evidence–risk coupling

Prerequisite configuration — all eight keys in §3.3.3:
`ubo_identification_threshold`, `ownership_reconciliation_tolerance`,
`ownership_exemption_types`, `control_person_required`,
`pep_evidence_requirements`, `factor_evidence_mapping`,
`factor_materiality_threshold`, `manual_acceptance_roles`.

`manual_acceptance_roles` mirrors the existing
`document_reliance_gate.MANUAL_ACCEPTANCE_ROLES` constant. It is listed for
completeness of the contract; where the institution has not overridden it, the
gate's own value governs and no `F-28` finding arises.

**Exit criterion:** each probe demonstrably emits `unavailable` /
`policy_not_configured` plus an `F-28` finding when its keys are unset, and
correct findings when they are set. Both paths are fixture-tested.

---

#### 0B-4 — Immutable decision-input snapshot *(before any Supervisor UI)*

**Founder amendment (D3).** Moved ahead of UI delivery. See §11.3 for the
capture triggers and immutability requirements.

**A separate implementation with its own change control.** Unlike 0B-1 to 0B-3,
this stage writes to the database and hooks decision-path events. It is
therefore governed independently:

| Requirement | Detail |
|---|---|
| **Separate change record** | Not covered by the Phase 0B authorisation for the probe stages. Requires its own approval before work begins |
| **Regression validation** | Full test-suite pass, plus explicit evidence that the five capture triggers do not alter decision-path behaviour, latency, or transaction boundaries on approval, rejection, override, EDD completion or risk recomputation |
| **Frozen-module clearance** | Capture hooks on the approval and decision paths touch the frozen Application Review surface and need founder sign-off |
| **Write-path isolation** | Snapshot writes must not be able to fail a decision. A capture failure is logged and alerted, never propagated into the decision transaction |

**Exit criterion:** snapshots are written on every trigger event, are immutable,
versioned and hashed; a snapshot re-loads into the 0B-1 bundle assembler and
reproduces the review current at capture time; and the regression evidence above
is complete.

---

#### After 0B-4 — Supervisor UI

Not in Phase 0B. Requires founder sign-off in any case, since the Application
Review surface is frozen.

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
| `parties` | `directors[]`, `ubos[]`, `intermediaries[]` — **direct identifiers excluded** (names, DOB, addresses, profile URLs), replaced by stable IDs and non-sensitive scalars (`ownership_pct`, `nationality`, `is_pep`). The retained IDs are pseudonymous and linkable, so the bundle remains personal data — see §10.9 |
| `documents` | Active document rows (`is_current = TRUE`) with `doc_type`, `slot_key`, `verification_status`, `expiry_date`, `version`, `file_sha256` |
| `document_gate` | Output of `evaluate_document_reliance_gate()` with `generated_at` **stripped** |
| `screening` | Stored report projection, officer dispositions, and **(v2)** `subjects[]` — per-subject provider mode, screening state, match state, disposition linkage and raw validity timestamps. Never `build_screening_truth_summary()` (§10.4.1), and never the raw match payload |
| `risk` | `factor_computation_evidence`, `risk_config_version`, `final_risk_level`, `escalations`, `elevation_reason_text`, and **(v2)** `dimension_scores` — the stored D1–D5 composites. Nothing recomputed; no derived tier added |
| `edd` | `edd_cases` rows, stored routing metadata, and **(v2)** `routing_facts` — the stored `agent5_input_contract` projected to `REQUIRED_FACT_KEYS`. A memo-time snapshot, labelled as such; missing keys are reported absent, never defaulted to `false` |
| `memo` | Latest memo `metadata` and section keys, `version`, `raw_output_hash`, `validation_status` |
| `supervisor_verdict` | `run_memo_supervisor()` output with `checked_at` **stripped** |
| `decision` | `decision_records` rows; `applications.decided_at`, `decision_by` |
| `monitoring` | `periodic_reviews` rows for the subject, including **(v2)** `policy_version` |
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

Five known non-determinism sources exist in the modules the Supervisor reads.
The list below was **verified against the code during Phase 0B-1**; the audit
table is reproduced in `supervisor_foundation/adapters.py`.

Two distinct severities matter here, and conflating them is the trap:

- **Cosmetic** — the clock reaches a single terminal output field and influences
  nothing else. Stripping the field makes the whole result deterministic, so the
  function may still be called.
- **Material** — the clock feeds a *verdict*. Stripping a timestamp is not
  enough, because the substantive answer changes between days for unchanged
  evidence. The function must not be called at all; its **inputs** are assembled
  instead and the time-relative judgement is deferred to a probe using `as_of`.

| Source | Severity | Problem | Treatment |
|---|---|---|---|
| `evaluate_document_reliance_gate()` | **Material** | Calls `datetime.now(timezone.utc)` for `generated_at` **and derives the `stale_verification` blocker from it** | **Do not call.** Assemble `build_required_document_expectations()` output plus per-document reliance state; derive staleness from `as_of` |
| `build_screening_truth_summary()` | **Material** | Reaches a clock transitively via `screening_state._timestamp_is_past`, which compares `screening_valid_until` against `datetime.now(timezone.utc)`. This feeds **`freshness`, `stale` and `approval_blocking`** | **Do not call.** Assemble the stored screening report and officer dispositions; derive freshness from `as_of` |
| `evaluate_edd_routing()` | Cosmetic | Emits `evaluated_at` | **Called in Phase 0B-2** by probe P-03, which re-runs the policy on the stored fact contract. `evaluated_at` is excluded from every comparison the probe makes (`edd_divergence.COSMETIC_KEYS`) and never reaches a finding. The route and triggers the probe compares are pure functions of the facts |
| `run_memo_supervisor()` | Cosmetic | Emits `"checked_at": datetime.now().isoformat()` | Call behind a stripping adapter; strip before hashing |
| `periodic_review_policy.parse_review_date()` | **Material — silent** | Falls back to `datetime.now(timezone.utc).date()` on malformed or missing input, and `add_months()` / `_interval_days()` inherit it | **Do not call on unvalidated input.** Parse dates in the probe; report malformed or missing dates explicitly. See §10.4.3 |
| `Finding.created_at` | Cosmetic | Would make every run unique | Set from the injected `as_of`, never wall-clock |

#### 10.4.1 `build_screening_truth_summary()` — recorded in Phase 0B-1

This entry was **absent from the original Phase 0A list** and was found during
implementation. It is recorded here because it is the least obvious of the five:
the clock is two calls deep, and the affected outputs are exactly the ones a
screening probe would most want to rely on.

`screening_state._timestamp_is_past()` returns `parsed < datetime.now(timezone.utc)`.
It is reached from `build_screening_truth_summary()` via the
`stale_from_expiry` computation, which sets `stale`, which in turn forces
`approval_blocking = True` and rewrites `blocking_reasons`. A case whose
screening validity lapses overnight therefore produces a materially different
summary the next morning **from identical stored evidence**.

Consequences, all binding on later phases:

1. The output of `build_screening_truth_summary()` **must not enter the
   canonical hashed bundle**, directly or in any derived form.
2. The bundle carries **stored screening evidence** instead: the persisted
   report projection, `screening_valid_until`, and officer dispositions from
   `screening_reviews`.
3. Any time-relative screening judgement — freshness, staleness, validity
   expiry — **must be computed by a probe from the injected `as_of`**, not
   inherited from the authoritative summary.
4. `screening_state.py` is **not** modified to accommodate this. It is
   authoritative and correct for its own runtime purpose, where reading the
   current clock is exactly right. The constraint belongs to the Supervisor.

**Rule:** no probe may call a clock. `as_of` is the only time source, and it is
an input.

### 10.4.2 `as_of` is part of the bundle — and therefore part of its hash

**Intentional, and easy to misread.** `as_of` sits in `meta`, so it is covered by
`input_bundle_hash`. Two consequences follow, and both must be understood before
review-history logic is designed:

1. **Changing `as_of` changes `input_bundle_hash`, even when every stored
   evidence value is identical.** A review of the same untouched case yesterday
   and today produces two different bundle hashes.
2. **`input_bundle_hash` therefore means "review input state as of time X"** —
   not "the evidence content". It is an identity for *a review's inputs*,
   not a content digest of the case.

This is the correct design, because `as_of` is a genuine input: it is the basis
against which every time-relative judgement (document staleness, screening
freshness, review-frequency compliance) is made. Two reviews taken at different
instants can legitimately reach different conclusions from the same stored rows,
and the hash must distinguish them or the conclusions would be
indistinguishable in the audit record.

**Consequence for review history (§8, output section 14).** A later phase
comparing two reviews of the same subject must distinguish **three independent
causes** of divergence, and must not collapse them:

| Cause | Signature | Meaning to an officer |
|---|---|---|
| **Evidence change** | `input_bundle_hash` differs; `as_of` and `policy_version` unchanged | The case itself changed — a new document, a new screening result, a new decision |
| **Time-basis change** | `input_bundle_hash` differs; **only** `as_of` differs | Nothing about the case changed; it was simply re-reviewed later, and time-relative judgements may have moved |
| **Policy change** | `input_bundle_hash` identical; `review_hash` differs | Same evidence, same instant, different institution policy |

Presenting a time-basis change as an evidence change would be actively
misleading — it would tell an officer a file moved when it did not. The
mechanism for separating them already exists: `as_of` is recorded in `meta` and
on every finding, so a diff can compare the two bundles field-by-field and
attribute the difference precisely. §10.7 lists the two further causes
(engine non-determinism and unavailable dependencies) that a full divergence
analysis must also rule out.

**Not redesigned in Phase 0B-1.** Hoisting `as_of` out of the hashed bundle
would make two reviews at different instants collide, which would be worse. The
current implementation matches the merged architecture; this section documents
its semantics rather than changing them.

#### 10.4.3 `parse_review_date()` — recorded in Phase 0B-2 preparation

The most dangerous of the five, because it fails **silently and plausibly**.

```python
def parse_review_date(value: Any) -> date:
    ...
    return datetime.now(timezone.utc).date()   # ← fallback on malformed/missing
```

Every other hazard in this register announces itself: a timestamp field appears
in the output and can be stripped. This one does not. Handed a null, an empty
string or an unparseable value, it silently substitutes **today** and returns a
perfectly ordinary-looking date. `add_months()` and `_interval_days()` both call
it and inherit the behaviour.

The failure mode for a monitoring probe is specific and bad: a periodic review
whose `next_review_date` is missing or corrupt would be treated as *scheduled
for today* — that is, compliant — so the probe would report `clear` on exactly
the record most likely to be defective. A missing date would be laundered into a
passing check.

Binding on all Supervisor code:

1. **Do not call `parse_review_date()`, `add_months()` or `_interval_days()` on
   unvalidated input.** Parse dates in the probe, deterministically.
2. **Compute all date arithmetic from the injected `as_of`.**
3. **Report a malformed or missing date explicitly** — `unavailable` with the
   applicable `availability_status`, never `clear`.
4. **Never substitute the current date for a bad one.**

`RISK_FREQUENCY_MONTHS`, `ENHANCED_REVIEW_FLOOR_MONTHS` and
`frequency_months_for_risk()` are clock-free and may be used directly.

`periodic_review_policy.py` is **not** modified. Reading the current clock is
correct for its own runtime purpose; the constraint belongs to the Supervisor.

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

**Strengthened in Phase 0B-1.** In-process repetition cannot catch drift that
varies per interpreter — hash randomisation, set-iteration order, an
import-time cache. The gate therefore also assembles the same stored row from
independent Python processes, including under varied `PYTHONHASHSEED`, and
requires byte-identical canonical JSON.

A caveat that fair comparison depends on: several columns carry database
`CURRENT_TIMESTAMP` defaults — `applications.inputs_updated_at` among them — so
two *separately created* fixture cases legitimately differ. Determinism means
*the same stored row* yields the same bundle, not that two independently seeded
cases collide. Cross-process tests must pin those columns before comparing, or
they measure the fixture rather than the assembler.

### 10.9 Hash comparability across schema versions and environments

#### 10.9.1 Across bundle schema versions

`supervisor-bundle-v2` adds four projections (§10.2). A v2 bundle therefore
hashes differently from a v1 bundle **for identical stored evidence**.

That is intended and is not a determinism regression. The hash identifies *the
set of inputs a review consumed*; v2 consumes a different, larger set. Four
rules follow:

| Rule | Reason |
|---|---|
| A v1 hash remains valid **as a v1 hash** | It correctly identifies what a v1 review consumed |
| **No equality is expected or meaningful across versions** | Comparing a v1 hash to a v2 hash answers no question |
| A v1 artifact must **never** be silently reinterpreted as v2 | Its evidence set is genuinely smaller; treating it as v2 would assert coverage it never had |
| Consumers must read `meta.bundle_schema_version` and **refuse** a version they do not understand | The field exists for exactly this |

Within a version the invariant is unchanged: identical bundle + identical policy
version ⇒ identical findings and review hash.

`probe_set_version` and `BUNDLE_SCHEMA_VERSION` are **separate contracts** and
move independently. v2 pairs with `probe-set-0b2-empty` — the bundle contract
changed; the probe set is still empty.

#### 10.9.2 Across environments

`input_bundle_hash` is **not** comparable between databases. Surrogate keys —
`screening_reviews.id`, `edd_cases.id`, `periodic_reviews.id`,
`compliance_memos.id` — are autoincrement values, so structurally identical
cases in staging and production carry different references and therefore
different hashes.

This is correct: the hash identifies *this subject's evidence state in this
system of record*, and `screening_review:{id}` is a pointer to a specific
persisted record, not a content descriptor. It is stated here because the
tempting misuse — comparing a staging hash against a production hash to assert
"same case" — would silently always report a difference.

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

### 11.3 The decision-input snapshot

**Founder amendment (D3 — approved).** The minimum canonical decision-input
snapshot is scheduled as **stage 0B-4**: immediately after the deterministic
probe harness and the probe set, and **before any Supervisor UI development**.

The reasoning is that replay depth accrues only from the day capture ships.
Every month of delay is a permanently missing month of replayable history, and
no later work recovers it. UI can be built at any time; history cannot be
built retroactively.

#### 11.3.1 Properties

The snapshot must be **immutable, versioned and hashed**:

| Property | Requirement |
|---|---|
| **Immutable** | Append-only. No update path. A correction is a new snapshot superseding the prior one, never an edit |
| **Versioned** | Carries `snapshot_schema_version`, plus the `policy_version`, `risk_config_version`, and relevant platform policy versions in force at capture |
| **Hashed** | `snapshot_hash = sha256(canonical_json(snapshot))` under the §10.3 rules, so tampering is detectable and identity is stable |
| **Self-describing** | Records which sections were captured and which were unavailable, so a later replay can distinguish "not captured" from "captured as empty" |
| **Replay-loadable** | Must load directly into the 0B-1 bundle assembler. If a snapshot cannot reproduce the review that was current at capture, it is not fit for purpose |

#### 11.3.2 Capture triggers

At minimum, a snapshot is written on:

| Trigger | Why |
|---|---|
| **Final approval** | The primary regulatory artifact — the state the institution relied on to accept the customer |
| **Final rejection** | Rejections are challenged too, and the basis must be reconstructable |
| **Decision override** | The case most likely to be examined, and the one where contemporaneous state matters most |
| **Completed EDD decision** | EDD conclusions carry their own evidentiary weight and a distinct approval path |
| **Material post-approval risk recomputation** | A rating that moves after onboarding creates a before/after pair; without both, neither the original decision nor the change is defensible |

#### 11.3.3 Interim position

- **Phase 0B probes remain current-state only.** No retrospective claims are
  made in 0B-2 or 0B-3.
- Any historic review before capture ships marks affected findings
  `not_replayable` with `availability_status: snapshot_incomplete`.
- Replayable history begins accumulating at 0B-4 — **before** UI delivery, so
  that by the time the Supervisor is visible to officers, it already has a
  growing base of reproducible decisions behind it.

This sequencing is the single most consequential decision in Phase 0A.

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

1. **Ownership coverage that falls short of policy.** No screen in RegMind sums
   `ubos.ownership_pct` or tests coverage against an identification threshold. A
   74% total renders exactly as legibly as 100%, and an entity with no party
   above the threshold renders as legibly as one with a clearly identified
   owner. An inspector's first question on a corporate file is "who owns the
   rest, and why is that acceptable?" — today nothing asks it.

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

4. **PEP exposure missing a specific required evidence element.** `pep_declaration`
   and `bankref_pep` exist as document types in `verification_matrix` but are
   absent from `build_required_document_expectations()`, and nothing correlates
   PEP status with source-of-wealth evidence, senior approval or enhanced
   monitoring. The Supervisor names *which configured element* is missing, per
   the institution's policy — not a fixed document list. Per D5, the correct
   long-term remedy for genuinely mandatory elements is to configure them and
   let the gate enforce them; the Supervisor's lasting contribution here is the
   conditional and correlated requirements a gate cannot express.

5. **Adverse media scored clear because there was no data.** `d1_adverse`
   defaults to **1 (clear)** when screening data is absent. On screen this reads
   as a clean adverse-media assessment. It is an absence, not a clearance, and
   the distinction is currently invisible. Per D6, P-04 **reports** affected
   current-state cases; changing the scoring default is a separate governed
   remediation and is out of scope for the Supervisor implementation.

6. **Approval on sandbox or simulated screening.** `provider_mode` distinguishes
   `live_provider`, `sandbox_provider` and `simulated_fallback`. The
   time-of-decision question — *what mode was in force when this was approved?* —
   is not asked anywhere.

7. **Monitoring promised but never scheduled.** The memo commits to enhanced
   monitoring; whether a `periodic_reviews` row was created at the policy
   frequency is not checked against that commitment.

8. **A materially elevated risk factor with no accepted evidence behind it.**
   The score and the document list are both on screen — the *coupling* between
   them is not, and neither is whether the artifact present is one the
   institution's evidence policy actually accepts for that factor.

9. **Policy configuration that is incomplete.** Whether the institution has
   configured the thresholds, exemptions and evidence mappings its own regime
   requires is not visible anywhere. `F-28` makes an unconfigured control an
   explicit, attributable finding rather than a silent absence — a check nobody
   knew was not running.

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
| **Policy replay** | Re-run findings under a different policy version | `review_hash` explicitly separates `input_bundle_hash` from `policy_version` (§10.7) — replay is "same bundle, different policy". The institution `policy_profile` (§3.3.2) is itself versioned and immutable once active, so *institution* policy replay works by the same mechanism as *platform* policy replay | **None in design.** Unblocked in practice at stage 0B-4 (§11.3) |
| **Inspection readiness** | Export findings as evidence | Output contract sections 13–15 (`unavailable_checks`, `review_history`, `evidence_index`) are designed for it; slots into `evidence_pack_export.py` export types | **None** |

The subject model, taxonomy, finding schema and evidence model are all
subject-agnostic. Nothing in Phase 0A hard-codes "application".

---

## 14. Founder decisions

**Status: Phase 0A approved in principle, subject to the amendments recorded
below.** All seven decisions D1–D7 are resolved. §14.3 is the authoritative
register.

### 14.1 Changes from the existing Challenge Mode spec

| # | Change | Rationale |
|---|---|---|
| 1 | Challenge Mode is now **one capability inside** the Supervisor, not the whole product | Positioning; prevents the Supervisor collapsing into a single panel |
| 2 | Probe set reduced from 12 to **7**, with 5 dropped and 2 added | Reliability over coverage; every dropped probe is justified in §9.4 |
| 3 | "Risk-score divergence" (old C6) **reframed** as resolution integrity | The original framing implied a competing score, which conflicts with §2.1 |
| 4 | Phase 0B is **current state only** — no historic replay | §11.2: application state is not snapshotted |
| 5 | Findings now carry `not_replayable` as a distinct status | Required by 4 |

### 14.2 Founder amendments to Phase 0A

Applied in this revision.

| # | Amendment | Effect on the design |
|---|---|---|
| **A1** | **Institution policy configuration layer** introduced (§3.3) | New `policy_profile` object, an eight-key configuration register, the three-layer policy hierarchy, and the rule that an unconfigured policy yields `unavailable`, never a template default |
| **A2** | **P-01 reframed** as *ownership coverage and unexplained residual* | No universal sum-to-100% assumption. Five policy-driven sub-checks: overstated ownership, no identified owner where policy expects one, unexplained residual, chain not terminating in natural persons or a permitted fallback controller, missing exemption rationale |
| **A3** | **P-05 reframed** as *PEP evidence requirement gap* | Seven independently detectable evidence elements; requirements and severity come from `pep_evidence_requirements`; a bank reference is required only where policy says so; findings name the specific missing element |
| **A4** | **P-07 mapping becomes a versioned policy artifact** | Multiple acceptable evidence types per factor, satisfied by disjunction; four interpretive limits documented so a satisfied coupling is never read as substantive verification; manual acceptance governed by role and rationale |
| **A5** | **Decision-input snapshot moved to stage 0B-4** | Immediately after the probe harness, **before** any Supervisor UI. Immutable, versioned, hashed, with five capture triggers (§11.3) |
| **A6** | **Policy hierarchy corrected (D5)** | Deliberately leaving evidence outside the document gate so the Supervisor has something to find is rejected. Policy defines, the gate enforces, the Supervisor reviews |
| **A7** | **Adverse-media default separated (D6)** | Classified as a distinct governed remediation with a three-state target. P-04 reports; the Supervisor implementation changes no scoring |
| **A8** | **New taxonomy and schema members** | Category `F-28 policy_configuration`; availability status `policy_not_configured`; evidence type `policy_profile` |
| **A9** | **Phase 0B staged** into 0B-1 … 0B-4 (§9.5) | Policy-independent probes separated from policy-dependent ones so engineering is not blocked on compliance configuration, and vice versa |
| **A12** | **Bundle schema v1 → v2** (Phase 0B-2 preparation) | Four authorised read-only additions required by the approved 0B-2 probes: `screening.subjects[]` (per-subject provider mode, terminality, match state and disposition linkage, carrying conclusions rather than raw match payload), `risk.dimension_scores` (stored D1–D5, no derived tiers), `edd.routing_facts` (the stored `agent5_input_contract` projected to `REQUIRED_FACT_KEYS`), and `monitoring.periodic_reviews[].policy_version`. Adds §10.4.3 (`parse_review_date` clock hazard) and §10.9.1 (cross-version hash semantics). No probes implemented |
| **A11** | **Phase 0B-1 closure-pass corrections** | Four alignments made after implementation. (a) `build_screening_truth_summary()` recorded as a **material** clock hazard (§10.4.1) — found during implementation, absent from the original list. (b) Clock hazards now classified *cosmetic* vs *material*, since stripping a timestamp is sufficient for one and not the other. (c) `as_of` hash semantics documented (§10.4.2), including the three divergence causes review history must distinguish. (d) Privacy language corrected throughout (§7.5): the bundle is PII-minimised, not anonymous — retained IDs and name fingerprints are pseudonymous and linkable, so it is personal data. Also adds §10.9 on hash comparability across environments |
| **A10** | **Implementation boundary on the policy contract** (§3.3.5) | The policy profile is an input contract, not authorisation for a policy-management subsystem. 0B-1 and 0B-2 may introduce only the minimum typed, versioned contract needed to represent policy availability — no administration UI, rule builder, approval workflow, scoring change, gate change or authoritative enforcement. Persistence and administration require separate founder approval. Eight consistency invariants recorded in §14.6 |

### 14.3 Founder decision register

| ID | Decision | Status | Resolution | Implemented in |
|---|---|---|---|---|
| **D1** | UBO threshold and exemptions | ✅ **Approved** | **Configurable, not hard-coded.** `ubo_identification_threshold`, `ownership_reconciliation_tolerance`, `ownership_exemption_types`, `control_person_required` become institution-configured, versioned policy keys. A deployment template may *propose* 25%, but the institution must approve and version it. No universal hard-coded 25% rule | §3.3.3, §9.2 P-01 |
| **D2** | Factor-to-evidence mapping | ✅ **Approved in principle; detailed mapping pending** | The mapping is a **configurable, versioned institutional policy artifact** supporting multiple acceptable evidence types per factor. The specific per-factor mappings are still to be defined and are a prerequisite for stage 0B-3 | §3.3.3, §9.2 P-07 |
| **D3** | Decision-input snapshot sequencing | ✅ **Approved** | Scheduled as **stage 0B-4** — immediately after the deterministic probe harness and probe set, and **before** Supervisor UI development. Immutable, versioned, hashed. Capture on final approval, final rejection, decision override, completed EDD decision, and material post-approval risk recomputation | §9.5, §11.3 |
| **D4** | Supervisor surface visibility | ✅ **Approved** | The eventual Supervisor surface **may use an independent, pilot-visible feature flag**, provided it remains read-only, advisory, separately validated, and does not depend on the enterprise-gated `supervisor/` package | §2.3, §9.3 |
| **D5** | Evidence requirements and the document gate | ✅ **Approved** | Evidence requirements are **governed by institution policy**. Deliberate gate gaps must **not** be preserved so the Supervisor has something to surface. Hierarchy: policy defines → gate enforces → Supervisor reviews configuration completeness, evidence–risk coupling, conditional requirements and officer reliance. Any actual gate change stays **outside Phase 0B**, since it could affect approval behaviour | §3.3.1 |
| **D6** | Adverse-media absence default | ✅ **Approved** | `d1_adverse = 1` on absent data is a **separate governed remediation issue**, not a Supervisor change. P-04 may report affected current-state cases. The Supervisor implementation changes **no** risk scoring. Target future state distinguishes three states: **clear**, **adverse media detected**, **not assessed / unavailable** | §9.2 P-04, §12.2(5), §14.4 |
| **D7** | Control other than ownership | ✅ **Approved** | Added to the **product intake roadmap**. Not implemented in Phase 0B. Category `F-04` remains reserved so the probe can be added later without a taxonomy version bump | §4 domain 4, §5.2 F-04, §14.5 |

### 14.4 D6 — adverse-media remediation (separate workstream)

Recorded here for traceability; **not** part of Phase 0B.

| Aspect | Position |
|---|---|
| Current behaviour | `rule_engine` sets `d1_adverse = 1` (clear) when no screening data is available — an absence-means-clear default |
| Phase 0B scope | **Report only.** P-04 identifies current-state cases where an adverse-media clear rating rests on absent data |
| Explicitly out of scope | Any change to `rule_engine` scoring behaviour by the Supervisor implementation |
| Target future state | A three-state adverse-media assessment: `clear` · `adverse_media_detected` · `not_assessed_unavailable`. The third state must be distinguishable everywhere the first is displayed |
| Governance | A scoring change affects risk levels, EDD routing and approval outcomes. It requires its own change record, recomputation plan, and impact assessment over the existing book |

### 14.5 D7 — control-other-than-ownership (intake roadmap)

| Aspect | Position |
|---|---|
| Gap | No field anywhere in the schema captures control exercised other than through shareholding — voting agreements, board control, senior managing official fallback. `directors` captures appointment, not control rights |
| Consequence | An entire FATF-relevant control domain (domain 4) is invisible, and P-01 sub-check (d) can only test the *ownership* chain, not the *control* chain |
| Decision | Added to the **product intake roadmap**. Not a Phase 0B dependency |
| Reserved | Category `F-04 control` and the `control_person_required` policy key are defined now so the later probe requires no taxonomy or config version bump |

### 14.6 Consistency invariants

Eight invariants confirmed across this specification. Each names where it is
stated and where it is enforced. Any future revision that breaks one of these is
a governed change, not an editorial one.

| # | Invariant | Stated in | Enforced by |
|---|---|---|---|
| **1** | **0B-1 and 0B-2 do not depend on an approved institution policy profile**, except for representing availability metadata | §9.5 stages 0B-1, 0B-2 ("Policy dependency: none"); §3.3.5 | The four 0B-2 probes read only versioned *platform* policy already in code (`risk_config_version`, `edd_routing_policy_v1`, `document_reliance_gate_v2`, `RISK_FREQUENCY_MONTHS`). No probe in either stage reads a `policy_profile` key for anything except availability |
| **2** | **0B-3 remains blocked** until detailed policy mappings are approved | §9.5 stage 0B-3; §9.1; §9.3; D2 in §14.3 | Approval of the *contract* in Phase 0A is explicitly not approval of the *mappings*. D2 is recorded as approved in principle, detailed mapping pending |
| **3** | **`policy_not_configured` never becomes `clear`** | §3.3.4; §6.2 status table | `policy_not_configured` is an `availability_status` attached to `status: unavailable`. §6.2 makes it a rendering obligation that `unavailable` is never rendered as, aggregated with, or counted as `clear`. Stage 0B-3's exit criterion fixture-tests the unconfigured path explicitly |
| **4** | **Template values are never silently authoritative** | §3.3.2; §3.3.4; D1 in §14.3 | A template value is a proposal. A probe finding no approved configuration emits `unavailable` plus an `F-28` finding — it must not fall back to a template value, a RegMind default, or an inferred threshold |
| **5** | **P-01 sub-check (a) — ownership exceeding 100% — remains policy-independent** | §9.2 P-01, sub-check table and availability note | Holdings summing above 100% is a data-integrity defect under any regime. It is the one sub-check that remains available when policy is unconfigured; (b)–(e) correctly go dark |
| **6** | **P-05 and P-07 evaluate only active, approved institution policy** | §9.2 P-05 and P-07, closure conditions; F-14, F-16 in §5.2 | Both probes' severity, requirement sets and closure conditions derive from the approved `policy_version` in force. Neither has a fallback path to a fixed document list |
| **7** | **No Phase 0B probe changes existing workflow, risk scoring, EDD routing, document gates, screening, approval, monitoring or decision authority** | §2.3 (exhaustive table); §1.3; §2.1; D5 and D6 in §14.3 | Guard test at stage 0B-2 asserting no write path exists to any authority in §2.3. Frozen-module guard tests must stay green throughout |
| **8** | **The decision snapshot is a separate 0B-4 implementation** with its own change control and regression validation | §9.5 stage 0B-4; §11.3 | Requires a separate change record before work begins, full regression evidence that the five capture triggers do not alter decision-path behaviour, frozen-module clearance for the capture hooks, and write-path isolation so a capture failure can never fail a decision |

---

## 15. Final recommendation

### **Proceed to Phase 0B, staged per §9.5 — subject to review of this amended specification.**

Phase 0A is approved in principle. All seven founder decisions are resolved
(§14.3), twelve amendments are applied (§14.2), and eight consistency invariants
are confirmed (§14.6). Phase 0B code should not begin
until this amended specification has been reviewed.

**Why proceed.** The determinism claim is well founded.
`evaluate_edd_routing()` is documented as pure. `build_compliance_memo()` is
documented as pure computation with no DB or HTTP dependency.
`compute_risk_score()` is a pure function of `(app_data, config)` and already
emits per-factor evidence under a declared `schema_version`.
`build_screening_truth_summary()` returns a fully explicit state vector. The
reproducibility invariant in §10 is achievable against real data, not
aspirational.

The commercial case holds: §12.2 lists nine defects the platform computes today
and displays nowhere. None required inventing a new capability.

**What the founder amendments changed, and why they improve the design.** The
three revised probes were the three that had quietly hard-coded a compliance
policy into a product. P-01 assumed ownership sums to 100%, which is false for
listed entities, widely-held funds, co-operatives and foundations. P-05 assumed
a bank reference is universally required for every PEP, which no regime
actually mandates uniformly. P-07 assumed one document type per risk factor,
which conflates *the institution's evidence standard* with *substantive
verification*.

Each would have produced confident false positives on legitimate structures —
the precise failure mode that teaches officers to dismiss the panel, and the
one thing §1.2 says the product cannot survive. Making policy configurable is
not a concession; it is what allows the Supervisor to be deployed into more
than one institution without being wrong in each of them differently.

The D5 correction matters more than it appears. Leaving evidence outside the
gate so the Supervisor has something to find would have made the product's value
contingent on the platform staying weak. The three-layer hierarchy in §3.3.1
puts the Supervisor's value where it is durable: policy completeness, evidence–
risk coupling, conditional requirements, and officer reliance — questions a gate
structurally cannot ask, however well configured it is.

**Three constraints from the codebase review remain in force:**

1. **Historic replay is not available today** (§11.2). The `applications` row is
   mutated in place with no snapshot. Phase 0B is current-state only, and stage
   0B-4 now lands before UI so history starts accruing at the earliest
   defensible point.

2. **The document reliance gate is not replay-safe as called** (§10.4). It calls
   `datetime.now()` internally and derives staleness from it. Probes derive
   staleness from the injected `as_of` instead.

3. **Five of the twelve original probes do not survive contact with the data**
   (§9.4) — chiefly because cross-document field comparison depends on an
   extraction schema `documents.verification_results` does not guarantee.

**What must not be built:** business model plausibility, expected-activity
plausibility, semantic override scoring, control-other-than-ownership,
source-of-funds corroboration, any risk-scoring change inside the Supervisor
implementation (D6), any document-gate change inside Phase 0B (D5), and any
output listed in §12.3.

**The gate on Phase 0B remains stage 0B-1.** Build the bundle assembler and the
reproducibility test in §10.8 first. If `review_hash` is not stable across three
runs on real fixtures, stop and reassess before writing any probe.

**The parallel track.** Stage 0B-3 is blocked on institution policy
configuration (§3.3.3), not on engineering. That configuration work — D2's
detailed per-factor mappings in particular — should start now, alongside 0B-1,
or it will become the critical path.
