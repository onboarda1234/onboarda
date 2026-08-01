# Phase 0B-2 — Real-Case Validation and Commercial Gate

**Probe set:** `probe-set-0b2-v1` (P-02, P-03, P-04, P-06)
**Bundle contract:** `supervisor-bundle-v2` (unchanged by this phase)
**Corpus:** `fixtures/pilot_canonical_dataset_v1.json` — 41 canonical pilot scenarios
**Review instant:** `as_of = 2026-08-01T00:00:00Z` (injected; no wall clock is read)
**Harness:** `arie-backend/tests/probe_corpus_report.py`

The benchmark for this phase was not "tests pass". It was: *would a competent
Senior Compliance Officer care about this finding, and could they have spotted it
from the existing screens?* This document records what the probes actually said
about 41 real-shaped cases, what that changed in the implementation, and a
SHIP / REVISE / DROP call for each probe.

No customer data appears here. Every subject is addressed by its pseudonymous
bundle key (`director_screening_0`) and every case by its fixture reference.
Nothing in this document states that a customer is or is not compliant.

---

## 1. Method

Two stages, because they answer different questions.

**Stage 1 — stored fixture evidence.** Seed the canonical pilot dataset through
`fixtures.pilot_canonical_seeder`, assemble a v2 bundle for each of the 41
applications, and run the four probes. This exercises the probes against the
evidence shapes the platform's own reference dataset produces.

**Stage 2 — production-path evidence.** For a sample of 8 cases, regenerate the
screening report through `screening.run_full_screening` and the compliance memo
through `memo_handler.build_compliance_memo`, then re-run. Stage 1 alone would
have been misleading: the canonical seeder writes memos and screening reports
directly rather than through those functions, so P-03 and P-04 have nothing to
read in stage 1. Stage 2 is the only evidence that they work against what
production actually stores.

Both stages are reproducible:

```
cd arie-backend
python -m pytest tests/probe_corpus_report.py::report \
    -s -q -o python_files=probe_corpus_report.py -o python_functions=report
python -m pytest tests/probe_corpus_report.py::production_path \
    -s -q -o python_files=probe_corpus_report.py -o python_functions=production_path
```

---

## 2. What the corpus run changed

Three implementation defects were found by running against the corpus that no
unit test had caught, and two more in the pre-merge closure review. Each is now
covered by a regression test that fails without the fix.

### 2.1 P-02 accepted only one of two spellings of "resolved" — 253 false positives

The first corpus run produced **253 hits across 41 cases, every one wrong**. The
probe treated `resolution_status == "resolved"` as the only success value.
`risk_controlled_values.resolve_controlled_score` writes `"mapped"`; only
`rule_engine` writes `"resolved"`. Every controlled-registry factor in the
dataset — the majority of factors on every case — was reported as unresolved.

Fixed by accepting both spellings, and by distinguishing three failure modes
instead of one: `unresolved` (no controlled entry), `unmatched` (scored by a
fallback rule, never mapped — the subtler case), and an unrecognised token
(reported as unrecognised, not assumed either way).

Three tests now cover it, and deliberately do not read the probe's own constant
— comparing the probe against itself is how the bug survived its first test:

- `test_mapped_is_a_resolved_status`
- `test_resolved_vocabulary_matches_what_the_engine_actually_writes` — reads the
  real `resolve_controlled_score` return value
- `test_probe_agrees_with_the_real_risk_engine_on_a_clean_input` — scores an
  application through `rule_engine.compute_risk_score` with the controlled-value
  contract flag both off and on, and asserts the probe agrees factor by factor

After the fix: **34 clear, 9 hit** across the 41 cases.

### 2.2 P-04 reported the same defect twice per subject

Stage 2 produced 33 findings across 8 cases. Roughly half were a terminality
finding restating a provider finding: `screening_state.derive_screening_state`
derives the state from the same provider record the mode comes from, so a
non-terminal state is *entailed* by a non-live provider mode. Two findings, one
fact, two different wordings.

The terminality check was removed and the derived state folded into the provider
finding's claim. `test_terminality_is_entailed_by_provider_mode` proves the
entailment across the whole provider-mode vocabulary, so if `screening_state`
ever allows the two to diverge, the removed check is reinstated deliberately
rather than missed.

### 2.3 P-04 asked whether an undefensible result was still current

A second redundancy: a subject screened against a sandbox also received a
freshness finding. "Is this result still current?" presupposes a result worth
being current, and the required action — re-run screening — was already stated.
Freshness is now evaluated only for subjects whose provider answer is
defensible. Disposition still runs regardless, because an undispositioned
material match is a failure of officer process that survives a re-screen.

Stage 2 volume: **33 → 16 findings**, one per subject, each with a distinct
action.

### 2.4 P-03 re-ran the policy on an incomplete input set

Found in the closure review, by capturing what `memo_handler` actually fed
`evaluate_edd_routing` and diffing it against what P-03 reconstructs.

`evaluate_edd_routing` reads **ten** fact keys. `REQUIRED_FACT_KEYS` — which the
bundle projects — names **eight**. The bundle treated the completeness contract
as the read set. The two keys read but not required:

| Key | Recoverable? | Effect when absent |
|---|---|---|
| `supervisor_mandatory_escalation_reasons` | Yes — already in the supervisor-verdict projection | `mandatory_escalation_edd_relevant` collapses to `False`, dropping the `supervisor_mandatory_escalation` trigger |
| `sector_label` | No — carrying it is a bundle-contract change | Drops `crypto_or_virtual_asset_sector`, and `high_risk_sector` where the tier does not already force it |

Measured across the corpus, comparing memo-time facts against the probe's
reconstruction on every evaluable case:

| | route mismatches | trigger-set mismatches |
|---|---:|---:|
| Before | 0 / 38 | **15 / 38** |
| After supplying the reasons list | 0 / 38 | **1 / 38** |

The remaining one is the single crypto case, missing
`crypto_or_virtual_asset_sector`; the route is unaffected because
`memo_handler`'s `BIZ_RISK_KEYWORD_FLOOR` independently forces
`sector_risk_tier = HIGH`.

**Why this was merge-blocking even at zero route mismatches.** An absent input
can only *remove* a trigger, so the recomputed route can only under-trigger. The
comparison "policy says standard, file says EDD" is therefore indistinguishable
from "an input is missing" — and P-03 was reporting it as HIGH-severity
divergence, i.e. accusing an officer of escalating beyond policy on evidence
that cannot support the claim. That the corpus happened not to trigger it is
luck, not safety: the synthetic reproduction is two lines.

P-03 now reports divergence only in the direction absent keys cannot
manufacture (stored `standard`, re-run `edd`). The opposite direction is
`not_replayable`. The conservative-over-routing check rested entirely on the
unsafe direction and was removed — extra diligence is not a control failure, so
an unfalsifiable claim about it is pure noise.

Two guards now hold the line: an AST scan of the policy's `facts.get(...)`
call sites against the keys the probe supplies (a new policy input fails the
build), and a behavioural test that captures a real memo's routing facts and
asserts the probe reproduces the same route with no invented triggers.

### 2.5 P-06 graded closed review cycles instead of live ones

Also found in the closure review, by reading `periodic_review_engine`. Completing
a review inserts the **next** cycle as a new `pending` row
(`_ensure_next_periodic_review_cycle`), so a reviewed customer accumulates
closed rows whose `next_review_date` is in the past.

`_governing_review` selected the earliest `next_review_date` across *all* rows.
From the second cycle onward that is always the oldest completed review. The
probe would have graded a closed historical cycle and reported `clear` while the
live schedule — which is what the probe exists to check — went unexamined.

Two consequences, both silent:

1. **Systematic false negative across the entire reviewed population.** An
   over-long live cycle is invisible behind any compliant closed one.
2. **A file holding only cancelled reviews read as compliant** — the exact
   inversion of "monitoring requirement not established".

The corpus did not catch it because the canonical dataset carries at most one
periodic review per application. P-06 now governs on open reviews only, and
treats "every review closed" as the same control gap as "no review at all".

### 2.6 Finding identity collapsed across subjects

Found before the corpus run but worth recording alongside the others. The runner
derived a finding's identity from its lowest-sorting evidence reference. Every
P-04 finding cites the same application-level approval references, so seven
findings on one busy case collapsed onto **two identifiers**. Probes now declare
`primary_evidence_ref` explicitly, naming the field the check examined.

---

## 3. Results

### 3.1 Stage 1 — stored fixture evidence (41 cases)

| Probe | clear | hit | unavailable | not_applicable |
|-------|------:|----:|------------:|---------------:|
| P-02  | 34    | 9   | 0           | 0              |
| P-03  | 0     | 0   | 41          | 0              |
| P-04  | 0     | 0   | 41          | 0              |
| P-06  | 3     | 9   | 0           | 29             |

Totals: 166 findings, 18 hits, 82 unevaluable.

Every case produced exactly 4–5 findings — one per probe, plus a second where a
probe fired on two independent defects. No case produced a wall of noise.

**P-03 and P-04 are 41/41 unavailable in stage 1 and this is correct, not a
defect.** The canonical seeder writes memos without `agent5_input_contract` and
screening reports without per-subject records. The probes report
`unavailable` / `data_absent` and say so in plain terms; neither reports
`clear`. That is the availability invariant working: a check that could not run
is never a check that passed.

### 3.2 Stage 2 — production-path evidence (8 cases)

| Probe | clear | hit | unavailable | not_applicable |
|-------|------:|----:|------------:|---------------:|
| P-02  | 7     | 1   | 0           | 0              |
| P-03  | 6     | 1   | 1           | 0              |
| P-04  | 0     | 16  | 0           | 0              |
| P-06  | 2     | 3   | 0           | 3              |

P-03 evaluates cleanly against memos produced by `memo_handler`: it re-runs
`evaluate_edd_routing` on the stored fact contract and reproduces the stored
route on 6 of 8 cases. P-04 produces exactly one finding per screening subject.

All 16 P-04 hits report the same true fact: this environment has no screening
provider credentials, so `run_full_screening` returned simulated-fallback
records, and the probe says so. On a deployment with live credentials these
would clear. The value of the run is that the per-subject machinery works
end to end against real `screening.py` output.

---

## 4. The headline finding

Nine of the 41 canonical cases record a risk escalation with **no attributable
reason**:

> A risk elevation was applied to this application — escalations
> `['sub_factor_score_4']` are recorded — but no elevation reason text is
> recorded against it.

Checked against the dataset manifest, these are true positives with a clear
pattern. Every *floor-rule* escalation records a reason:

| Escalation | `elevation_reason_text` |
|---|---|
| `floor_rule_declared_pep` | "Declared PEP floor: declared PEP exposure requires at least HIGH final risk" |
| `floor_rule_elevated_jurisdiction` | "Elevated jurisdiction floor: Nigeria requires at least HIGH final risk" |
| `floor_rule_opaque_ownership` | "Opaque ownership floor: opaque/complex ownership requires at least HIGH final risk" |
| `sub_factor_score_4` (alone) | *empty* |
| `monthly_volume_score_4` (alone) | *empty* |

So the platform attributes floor-driven elevations and does not attribute
sub-factor-driven ones. A reviewer asking "why was this customer uplifted?" has
something to point to in one case and nothing in the other, and **no screen
shows the difference** — the risk panel displays the final level either way.

This is the Phase 0A thesis demonstrated on the platform's own reference data:
a finding a Senior Compliance Officer would care about, that is not visible on
any existing surface, produced by relating two stored fields nobody relates.

It is reported as an *attributability* gap. P-02 does not claim the rating is
wrong; it has no standing to.

---

## 5. Commercial gate

| Probe | Verdict | Basis |
|-------|---------|-------|
| **P-02** Risk factor resolution integrity | **SHIP** | Highest-value probe in the set. Found a real, previously invisible attributability gap on 9 of 41 reference cases. Signal-to-noise after the vocabulary fix is 9 hits / 34 clears — precise, not chatty. Every hit names a specific field and a specific action. |
| **P-03** EDD routing divergence | **SHIP WITH LIMITATIONS** | Downgraded from SHIP in the closure review. The probe is sound *within the direction it can defend* (§2.4), and the under-routing check — the valuable one — is unaffected. But it cannot conclusively compare routes until the bundle carries `sector_label`, so one direction of its headline check reports `not_replayable` rather than an answer. It also cannot yet be credited with a caught defect: the reference corpus contains no divergence case. Ships because the under-routing check is genuinely free once a memo exists and nothing else in the product re-runs the policy; limited because a reviewer must understand that "not replayable" is a real state, not a failure. |
| **P-04** Screening reliance defensibility | **SHIP, with a volume watch** | Per-subject machinery works against real `screening.py` output. Two rounds of noise reduction were needed to get from 4 findings per subject to 1. Recommend re-measuring volume on a deployment with live provider credentials before exposing it to officers: in this environment every subject is a hit for the same reason, and that pattern (many subjects, one root cause) is the one most likely to need a roll-up. |
| **P-06** Monitoring requirement not established | **SHIP** | 3 clear / 9 hit / 29 not_applicable — correct scoping (only approved cases carry the obligation) and a credible hit rate. Carries the sharpest clock-safety property in the set: it refuses `parse_review_date`, which would have read a corrupt review date as "scheduled today" and passed. Also refuses `normalize_risk_level`'s silent fallback to MEDIUM, which would have attributed a governed 24-month cycle to an ungoverned risk level. The closed-cycle defect (§2.5) is fixed and regression-tested; note that the corpus could not have caught it, so the fix rests on unit coverage plus a reading of `periodic_review_engine`. |

**No probe is DROP. No probe is REVISE-before-ship.** The five defects — three
from the corpus run, two from the closure review — are fixed and covered by
regression tests that fail without the fix.

**What would move P-03 to a clean SHIP:** carrying `sector_label` in
`edd.routing_facts`. That is a bundle-contract change requiring a schema version
bump and founder authorisation, deliberately not taken in a closure review.

---

## 6. Limitations of this validation

Stated plainly, because a validation that overstates its own reach is worse than
none.

1. **This is a fixture corpus, not production traffic.** The 41 canonical
   scenarios are designed to exercise the risk engine's branches. They are
   realistic in shape but not a sample of real customer behaviour, and hit rates
   here should not be read as expected production hit rates.
2. **No live provider.** Every screening result in stage 2 is a simulated
   fallback. P-04's provider check is therefore proven to *fire* correctly but
   not proven to *clear* correctly against a live ComplyAdvantage response.
3. **No divergence cases for P-03.** The corpus contains no case where the
   stored route disagrees with the policy, so P-03's headline check is covered
   by unit tests only. One direction of that check is additionally limited by
   the `sector_label` gap and reports `not_replayable` (§2.4).
6. **P-04's disposition link is name-based.** The bundle matches a screening
   review to a subject on `(subject_type, sha256(name.strip().lower()))`. A
   spelling or spacing difference between the screening report and
   `screening_reviews.subject_name` breaks the link, and P-04 would report an
   undispositioned match that was in fact dispositioned. No instance was seen in
   the corpus, but the matching is exact and this is a real false-positive path
   inherited from the bundle, not from the probe.
4. **No officer feedback.** Whether these findings are *acted on* — the real
   commercial test — cannot be answered from a corpus run. That belongs to
   pilot usage.
5. **P-06 tests structured scheduling only.** Monitoring commitments made only
   in generated memo prose are outside its reach, and every P-06 finding says so.

---

## 7. Follow-ups (not authorised in this phase)

- Re-measure P-04 finding volume against a deployment with live screening
  credentials; decide then whether a per-application roll-up is warranted.
- Construct a routing-divergence case for P-03 in a QA fixture set, so its
  headline check has corpus coverage rather than unit coverage alone.
- Decide whether to carry `sector_label` (and any future policy input outside
  `REQUIRED_FACT_KEYS`) in a bundle v3, which would let P-03 compare routes in
  both directions and lift its SHIP WITH LIMITATIONS to SHIP.
- The `sub_factor_score_4` attributability gap in §4 is a finding *about the
  platform*, surfaced by the Supervisor. Whether `rule_engine` should record a
  reason for sub-factor elevations is a product decision for the founder, not a
  Supervisor change.
