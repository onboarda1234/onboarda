# AI Compliance Supervisor — Review Aggregation Engine

**Status:** implemented, not merged · **Phase:** after 0B-2 · **Package:**
`arie-backend/supervisor_foundation/aggregation/`

Companion to
[`ai-compliance-supervisor-review-framework.md`](ai-compliance-supervisor-review-framework.md)
(§8 defines the output contract this implements) and
[`supervisor-0b2-real-case-validation.md`](supervisor-0b2-real-case-validation.md)
(the probe set this consumes).

---

## 1. What this layer is for

Four probes over a busy case produce a list. A list is not a review.

An officer reading eleven findings — four screening subjects, two unresolved
risk factors, an unattributed elevation, a monitoring gap — has to do the
aggregation in their head before they can act. The single most useful thing they
could be told is *which of these are the same problem seen more than once*, and
that is exactly what a flat list hides.

The engine converts one probe run into one `SupervisorReview`: an ordered
summary, a grouped detail view, a deduplicated action register, deterministic
regulator questions, an explicit statement of what could not be evaluated, and
an evidence index tying it together.

**It is a projection, not a second opinion.** Per framework §8.1, every section
is a view over the single finding list. No section invents a finding, changes a
severity, or reaches a conclusion the probes did not.

---

## 2. Boundary

| Property | How it holds |
|---|---|
| Deterministic | Identical inputs give byte-identical output, verified across processes and hash seeds |
| Pure | `aggregate_review(review, *, bundle)` — no other parameters; no database handle exists to pass |
| Read-only | No `execute`, `commit`, `cursor` anywhere in the package; table counts unchanged after a run |
| No clock | AST scan over the package; `as_of` is carried from the review |
| No LLM | AST and text scan for every model-call token; all text is a finding field or a governed template |
| No branding | No `branding` import, no environment read; asserted behaviourally by rebranding and comparing bytes |
| Non-authoritative | No authoritative module imports the package; `review.py` does not import it either |
| Advisory | No status value means approved, rejected, compliant, non-compliant, safe or acceptable |

Not implemented, deliberately: UI, API, persistence, acknowledgements, officer
disposition, feature flags, scheduled execution, narration.

---

## 3. The `SupervisorReview` contract

`AGGREGATION_SCHEMA_VERSION = "supervisor-aggregation-v1"`

Identity and provenance: `review_id`, `aggregation_hash`, `review_hash`,
`input_bundle_hash`, `subject_type`, `subject_id`, `as_of`, `created_at`,
`bundle_schema_version`, `probe_set_version`, `policy_version`,
`review_schema_version`, `aggregation_schema_version`, `finding_ids`.

Content sections, all projections of the finding list:

| Section | Contents |
|---|---|
| `overall_assessment` | `overall_status`, `findings_by_severity`, `material_concern_count`, `material_concerns_listed`, `material_concerns_omitted` |
| `review_completeness` | probe and finding counts by status and severity, evidence reference count, and the definition of the fraction |
| `grouped_findings` | the detail view — every finding, grouped by check |
| `material_concerns` | the ordered summary (critical and high hits) |
| `critical_findings` | every critical group, uncapped, with actions and close conditions |
| `contradictions` | typed conflicts between two structured facts |
| `missing_evidence` | five distinct classes, kept apart |
| `policy_deviations` | categories `edd`, `policy` (framework F-19, F-20) |
| `decision_inconsistencies` | categories `decision`, `override`, `governance` (F-22, F-23, F-25) |
| `officer_questions` | deduplicated `officer_question` values from hits |
| `potential_regulatory_challenges` | template-rendered questions, each mapped to findings, evidence and actions |
| `required_actions` | deduplicated action register with owner role, priority and closure |
| `close_conditions` | every closure condition on an open finding |
| `unavailable_checks` | every check that produced no answer, with the reason |
| `limitations` | what this review could not establish, and whether that is a boundary or a gap |
| `evidence_index` | one deduplicated record per reference cited |

### 3.1 `review_history` is deliberately absent

Framework §8.1 defines it as prior reviews of the same subject. It requires
persistence, which is out of scope. An always-empty list would read as "this
subject has never been reviewed before" — a claim this engine cannot make.

---

## 4. Overall status

Five values, one total order, evaluated top to bottom:

| # | Condition | Status |
|---|---|---|
| 1 | any `hit` at `critical` | `critical_concerns` |
| 2 | any `hit` at `high` | `material_concerns` |
| 3 | any `unavailable` or `not_replayable` | `incomplete_review` |
| 4 | any remaining `hit` | `concerns_identified` |
| 5 | otherwise | `no_material_findings` |

**Rule 3 above rule 4 is the one judgement call**, and it is deliberate. A
medium defect is a known quantity; a control that could not be evaluated is not,
and `concerns_identified` would imply the review was complete. Severity still
wins where severity is known: an established material failure outranks an
unanswered question.

`not_applicable` does not make a review incomplete — that check ran and
established there is no obligation.

---

## 5. Grouping

### 5.1 The check key

Findings carry no `check_id`; adding one would change the finding contract and
therefore `review_hash` for every stored review. The key is derived from
`primary_evidence_ref`, which every probe already sets to the field the check
examined:

```
screening:{app}#subject:{key}#provider_mode   ->  "provider_mode"
risk:{app}#factor:country_of_incorporation    ->  "factor"
periodic_review:{id}                          ->  ""
```

That derivation is a rule about reference *shape*, and shapes drift. A
`(probe_id, check_key)` pair absent from `CHECK_REGISTER` produces a **singleton
group**: the finding alone, fully visible, flagged `check_registered: false`.
The failure mode of a stale register is less aggregation, never wrong
aggregation. `test_the_register_covers_every_check_the_real_probes_emit` runs
the real probes over five case shapes and fails if the register has fallen
behind.

### 5.2 The group key

`(probe_id, status, availability_status, check_key)`.

- **`status`** is in the key: a `hit` and an `unavailable` on the same check are
  not the same concern. Merging them would let a check that could not run
  disappear behind one that did.
- **`availability_status`** is in the key: "no credentials" and "no historic
  snapshot" are different problems with different owners.
- **`category` is not in the key**, and that was a correction made during
  implementation. P-02's factor check reports `jurisdiction` for a country
  factor and `products` for a service factor. Keying on category split "two risk
  inputs did not resolve" into two single-finding groups and asked the same
  regulator question twice — the exact noise this layer exists to remove, on the
  exact example the specification uses. Categories are not lost: the group
  carries all of them and every owner role they route to.

### 5.3 What a group may not do

- Lose a finding — every `finding_id` is in `grouped_findings`
- Lose an evidence reference — every ref is in `evidence_index`
- Downgrade severity — the group takes the worst of its members
- Hide a differing closure condition — all distinct ones are listed
- Replace a single finding's claim with a template — a group of one keeps its
  own sentence, because "not defensible for 1 required screening subject" is
  worse writing and worse evidence than what the probe already wrote

---

## 6. Material concerns

Critical and high hits only (framework §8.1), ordered by severity then
deterministically by probe, check and group id.

`MATERIAL_CONCERN_LIMIT = 7`. The number is a judgement: the list exists to be
read in full before an officer opens the detail, and a summary long enough to
need scrolling has stopped summarising. It caps the *summary* only —
`material_concern_count` reports the true total and `material_concerns_omitted`
states how many were not listed.

**The cap never applies to a critical group.** More than seven critical concerns
produces a summary longer than seven: "the decision is not defensible as it
stands" is not a thing to truncate for tidiness.

---

## 7. Contradiction taxonomy

A contradiction is two structured facts that cannot both be right — not a
synonym for "serious finding". Six types, each declared on a register entry:

| Type | The conflict |
|---|---|
| `edd_routing_divergence` | Policy replayed on the decision's own facts returns EDD; the stored route is standard |
| `edd_case_absent` | Stored route is EDD; no EDD case exists |
| `approval_on_undefensible_screening` | Approved in reliance on a result that did not come from a live provider |
| `adverse_media_clear_on_absent_data` | Factor scored clear; no assessment was performed |
| `unattributed_risk_elevation` | Level raised above the computed base; no reason recorded |
| `material_match_closed_without_disposition` | Material match treated as closed; no governed disposition |

`contradiction_min_severity` carries the cases where the conflict depends on
reliance: an undefensible screening result is a defect on any case and a
*contradiction* only where the case was approved on it — which the probe already
encodes by raising that finding to critical.

---

## 8. Missing evidence — five classes, kept apart

| Class | Trigger |
|---|---|
| `evidence_absent` | `unavailable` / `data_absent`, or a hit on a check whose register entry says the evidence is missing |
| `evidence_malformed` | `unavailable` / `snapshot_incomplete` |
| `check_unavailable` | `unavailable` / `credentials_absent`, `dependency_gated`, `policy_not_configured` |
| `evidence_present_not_defensible` | a hit on a check whose register entry says so |
| `evidence_not_replayable` | `not_replayable` |

Collapsing them would send an operations ticket, a client request and a
governance question to the same place. Every entry carries what is missing, why
it matters, the affected findings, the required action and the close condition.

---

## 9. Required actions

Deduplicated on the action text itself: two findings that require literally the
same thing are one action; anything less exact stays separate.

Built from **findings, not groups**. A group can span categories, and taking the
owner from the group would stamp every action in it with every role the group
touches — telling Ops to do the SCO's work. Each action takes its owners from
the categories of the findings that actually require it, read from the governed
category register in framework §5.2.

`clear` and `not_applicable` groups contribute nothing: those probes write
"None." as their required action, which is correct on a finding and noise in an
action register.

Nothing here assigns work, sets a due date, or changes state.

---

## 10. Regulatory challenges

Rendered from register templates for checks that actually fired. No model, and
no generic questions: each entry carries the findings, evidence and action ids
that answer it.

Counts were removed from the question templates during implementation. "Who
reviewed each of the 1 material screening match" is what count-driven templates
produce on a singleton, and no amount of pluralisation logic makes that class of
bug go away. Counts belong in group claims, where they carry information.

---

## 11. Limitations

Each entry declares a kind: `product_boundary` (something the platform
deliberately does not claim), `environment_gap` (a dependency absent here that
could be present elsewhere), or `historic_replay`. Collapsing the two would be
dishonest in both directions — presenting a scope decision as a fault, and a
missing credential as a design.

Disclosed: the P-03 asymmetric-replay boundary, P-04 live-provider validation,
adverse-media source coverage, historic snapshot reconstruction, the
policy-dependent probes not implemented, absent registry credentials, and any
unavailable probe dependency.

**A limitation never softens a finding.** It bounds what a *clear* result would
have been worth.

---

## 12. Hash and identity

Three hashes, three questions, not interchangeable:

| Hash | Answers | Owner |
|---|---|---|
| `input_bundle_hash` | what evidence was read | bundle assembler |
| `review_hash` | what the probes concluded from it | probe runner |
| `aggregation_hash` | how that was assembled into one review | this engine |

`review_hash` and `input_bundle_hash` are **carried through unchanged**. The
merged hashing contract is not redefined.

`AGGREGATION_SCHEMA_VERSION` is versioned separately from
`REVIEW_SCHEMA_VERSION` because the two change for different reasons: how a
review is presented can be revised without the underlying findings changing at
all. A change here moves `aggregation_hash` and leaves `review_hash` alone,
which is exactly the discrimination an auditor needs — it distinguishes "the
findings changed" from "the presentation changed".

`review_id = sha256(subject_type, subject_id, as_of, review_hash,
aggregation_schema_version)[:12]`, prefixed `rev-`. Non-circular, derived from
declared inputs only. Group, action and challenge identifiers are derived the
same way from their own content — never from a counter, a clock or a random
source.

`BUNDLE_SCHEMA_VERSION` and `PROBE_SET_VERSION` are unchanged by this work.

---

## 13. Real-case validation

Harness: `arie-backend/tests/review_engine_corpus_report.py` (not
pytest-collected). Both stages seed deterministically.

| | Stage 1 (41 cases, stored fixtures) | Stage 2 (8 cases, production path) |
|---|---|---|
| Raw findings | 166 | 42 |
| Grouped concerns | 166 | 34 |
| Listed material concerns | 2 | 11 |
| Required actions | 102 | 17 |
| Regulator questions | 20 | 16 |
| Unavailable checks | 82 | 1 |
| Grouping compression | **0.0%** | **19.0%** |
| First-read compression | 98.8% | 73.8% |
| Findings lost | **0** | **0** |

Overall status spread — stage 1: 39 `incomplete_review`, 2 `material_concerns`.
Stage 2: 5 `critical_concerns`, 3 `material_concerns`.

### 13.1 Stage 1 compresses nothing, and that is the honest result

Grouping only helps where one check fires repeatedly on one case. The canonical
seeder writes no per-subject screening records, so stage 1 produces at most one
finding per check per case — four findings, four checks, nothing to group. **0%
is the correct answer for that corpus, not a defect**, and quoting only the
stage 2 figure would misrepresent it.

Where the compression comes from is visible in the per-check absorption: in
stage 2, `P-04.provider_mode` absorbed 16 findings into 8 groups. On the
multi-subject fixture case — a company, two directors and a UBO — 11 findings
become 7 groups (36%), and a real institution's file with three directors and
two UBOs sits further along that curve than anything in this corpus.

### 13.2 First-read compression is the number that matters to an officer

166 findings to 2 listed concerns in stage 1, 42 to 11 in stage 2. That is not
grouping; it is severity selection plus honest status. Both are reported
separately above so neither is quoted as the other.

---

## 14. Commercial assessment

| Question | Answer |
|---|---|
| Reduces noise without losing evidence? | Yes. 0 findings lost across all 49 corpus cases, asserted per case in the harness and by test |
| Identifies the top issues quickly? | Yes. Ordered summary, criticals never suppressed, count stated when the list is capped |
| Gives an actionable next step? | Yes. Deduplicated action register with a governed owner role, priority and closure condition |
| Preserves auditability? | Yes. Every group, concern, contradiction, action and question carries `finding_ids` and evidence refs; three hashes distinguish evidence, findings and presentation |
| Reads like an independent reviewer? | Substantially. "Screening reliance is not defensible for 4 required screening subjects" plus a regulator question is a reviewer's voice; a list of four sandbox findings is an alert queue |
| Better demo than raw findings? | Yes, on a multi-subject case. On a single-subject case it is close to a list, which the stage 1 result states plainly |

**Classification: SHIP**, with the stage 1 result disclosed rather than
smoothed. The layer earns its place on cases with repeated checks and does no
harm on cases without them: worst case it is a severity-ordered, status-honest
presentation of the same findings.

---

## 15. Known limitations of this layer

1. **Grouping value scales with case complexity.** Single-subject cases see
   little compression (§13.1).
2. **The check key is derived from reference shape.** Mitigated by the register
   and its coverage test, but a probe renaming an anchor degrades grouping until
   the register catches up.
3. **No `review_history`.** Requires persistence (§3.1).
4. **Contradiction detection is register-driven.** It finds the six declared
   conflicts and nothing else; a novel conflict needs a register entry, which is
   deliberate — the alternative is inference.
5. **Owner roles come from the category register**, so a finding carrying a
   category outside framework §5.2 reports `unassigned` rather than a guess.
6. **Inherited from the probe set.** P-03's asymmetric replay and P-04's
   name-fingerprint subject linkage are limitations of the findings this layer
   presents, not of the presentation.
