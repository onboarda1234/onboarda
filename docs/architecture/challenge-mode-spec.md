# Challenge Mode — Technical Specification

**Status:** Draft for founder review
**Owner:** TBC
**Target module:** new `arie-backend/challenge_engine.py` + `challenge_narrator.py`
**Change-control impact:** additive only; Application Review (FROZEN) requires
sign-off for the UI panel in Phase 4 (see §10).

---

## 1. Summary

Challenge Mode is an **adversarial, non-authoritative second opinion** on a
compliance decision. After a memo is generated (or before an approval is
committed), an officer clicks **Challenge this decision**. RegMind then argues
*against* the case: it enumerates every reason the file should not be approved,
what evidence is missing, which conclusions are unsupported, and which questions
an inspector would ask.

The commercial pitch:

> Before anyone approves a customer, RegMind independently challenges the
> decision. If it disagrees with your team, you know why — with evidence, and
> reproducibly.

**The governing design rule:** the *findings* are deterministic and
reproducible; the *prose* is generated. An LLM never decides anything, never
scores anything, and never sees raw case data. It renders findings that a
deterministic engine has already produced.

This is what makes Challenge Mode defensible where a competitor's LLM panel is
not: the same file challenged twice yields a byte-identical finding set, which
means it can be validated, evidenced, and re-run for a regulator.

### 1.1 Non-goals

- Challenge Mode does **not** gate approval. It cannot block, escalate, or
  change a decision. It informs a human.
- It does **not** replace `run_memo_supervisor()`. That remains the
  authoritative consistency control.
- It is **not** a chat interface.

---

## 2. Why this shape (design rationale)

Three properties are load-bearing and should not be traded away:

| Property | Why it matters |
|---|---|
| **Deterministic findings** | Reproducibility is the regulatory claim. A finding set that changes between runs cannot be validated under a model-risk framework, and cannot be defended in an inspection. |
| **LLM sees only structured findings** | Bounds the hallucination surface to prose. The model cannot invent a finding because it never sees the underlying data — it receives a finding list and writes sentences about it. |
| **Non-authoritative** | Keeps Challenge Mode outside the model-risk validation perimeter that applies to authoritative decision components. This is the difference between a 4-week build and a 9-month one. |

An alternative design — a panel of LLM agents debating to a verdict — was
considered and rejected: it is non-reproducible, it multiplies model-risk
obligations, "3 of 5 agents approved" is not a defensible control, and it
discards the determinism that is RegMind's actual differentiator.

---

## 3. What already exists (reuse map)

Challenge Mode is mostly an *aggregation and reframing* layer. The detection
logic largely exists.

| Capability | Existing implementation |
|---|---|
| Memo-section contradiction detection (11 checks) | `supervisor_engine.run_memo_supervisor()` — **live, pilot-active** |
| Cross-agent contradiction detection (9 checks, 9-category taxonomy) | `supervisor/contradictions.py::ContradictionDetector.detect_all()` |
| Severity / confidence vocabulary | `supervisor/schemas.py` — `Severity`, `ContradictionCategory`, `Contradiction`, `Finding`, `Evidence` |
| Confidence scoring + routing | `supervisor/confidence.py::ConfidenceEvaluator` |
| Executed risk model projection | `risk_model_view.py` (reads the same config `compute_risk_score` executes) |
| Risk scoring + D1–D5 dimensions | `rule_engine.compute_risk_score()` |
| Required-vs-present document expectations | `document_reliance_gate.build_required_document_expectations()`, `evaluate_document_reliance_gate()` |
| Per-doc-type verification checks | `verification_matrix.get_checks_for_doc_type()` |
| Enhanced requirement rules | `enhanced_requirements.decorate_application_requirements_for_backoffice()` |
| EDD trigger policy | `edd_routing_policy.evaluate_edd_routing()`, `assert_routing_invariant()` |
| Screening state + freshness | `screening_state.py`, `screening_freshness_metadata.py` |
| UBO / ownership mapping (deterministic, no AI) | Agent 4 executor in `supervisor/agent_executors.py` |
| Registry cross-verification (deterministic) | Agent 2 executor, `company_registry.py` |
| Override records | `decision_model.build_decision_record()` (`override_flag`, `override_reason`) |
| LLM narration scaffold + system prompt | `supervisor/compliance_assistant.py` |
| Hash-chained audit | `supervisor/audit.py::append_verdict_chain_entry()`, `AuditLogger` |

**Net new code is roughly: the probe set, the reproducibility harness, the
adversarial narration prompt, two tables, three endpoints, one UI panel.**

---

## 4. Critical constraint: which supervisor to build on

There are two supervisor systems in the repo, and picking the wrong one ships
Challenge Mode to nobody.

**A. `supervisor_engine.py` — LIVE.** Wired at
`/api/applications/:id/memo/supervisor` (`server.py:44155-44156`). Runs in the
pilot. Part of the **FROZEN** Application Review module.

**B. `supervisor/` package — GATED OFF.** The 10-agent pipeline. Guarded by
`server.py:4369`:

```python
def _ai_supervisor_enabled():
    return _enterprise_scope_permitted() and _feature_enabled("ENABLE_AI_SUPERVISOR")
```

`_enterprise_scope_permitted()` returns `not pilot_scope_active()`, so in any
pilot deployment this is refused **regardless of the feature flag**. This is the
"AI Compliance Supervisor — Coming Soon / Enterprise" surface de-scoped in
PR-PILOT-SCOPE-1.

### Decision

Challenge Mode reads from **both**, but is **gated independently of
`_enterprise_scope_permitted()`**:

```python
def _challenge_mode_enabled():
    return _feature_enabled("ENABLE_CHALLENGE_MODE")   # NOT enterprise-vetoed
```

Rationale: Challenge Mode is a pilot-differentiating feature. Putting it behind
the enterprise veto guarantees no pilot customer ever sees it. It is safe to
exempt because it is non-authoritative and read-only — the veto exists to stop
half-built modules presenting as active, which the Phase 0 reproducibility gate
addresses directly.

**This exemption is a founder decision and should be explicitly approved.**

Probes that depend on the gated `supervisor/` package (C4, C5 below) degrade
gracefully to `status: "unavailable"` when it is off — they do not fail the run.

---

## 5. Architecture

```
  DETERMINISTIC SOURCES (read-only)
  run_memo_supervisor()   rule_engine    document_reliance_gate
  contradictions.py       screening_state    edd_routing_policy
  verification_matrix     decision_model     company_registry
                    │
                    ▼
  ┌─────────────────────────────────────────────────┐
  │  challenge_engine.py            ← NEW           │
  │  • 12 challenge probes (versioned, pure)        │
  │  • severity + evidence linkage                  │
  │  • emits ChallengeRecord + challenge_hash       │
  │  NO WRITES TO CASE STATE                        │
  └─────────────────────────────────────────────────┘
                    │  structured findings only
                    ▼
  ┌─────────────────────────────────────────────────┐
  │  challenge_narrator.py          ← NEW           │
  │  • LLM prose: narrative + suggested questions   │
  │  • deterministic template fallback              │
  │  • CANNOT add/remove/re-score a finding         │
  └─────────────────────────────────────────────────┘
                    │
                    ▼
     POST /api/applications/:id/challenge/run
     GET  /api/applications/:id/challenge
     Back office: "Challenge this decision" panel
```

`challenge_engine` must be importable and runnable **without** a database
handle beyond a read-only connection, and must be a pure function of its input
bundle. This is what makes Phase 0's reproducibility test possible.

---

## 6. Challenge probes

Each probe is deterministic, independently testable, and cites the module it
derives from. `CHALLENGE_POLICY_VERSION = "challenge-v1"` covers the whole set;
bumping it is a governed change.

| ID | Probe | Question it asks | Derived from |
|---|---|---|---|
| **C1** | Evidence sufficiency | Does every risk dimension scored ≥3 have verified supporting evidence? | `rule_engine` D1–D5 + `document_reliance_gate` |
| **C2** | Unsupported conclusion | Does a memo section assert a conclusion with no cited artifact? | memo sections + `verification_matrix` |
| **C3** | Screening vs documents | Does screening name-match contradict document name-match? | `screening_state` + document verification |
| **C4** | Ownership arithmetic | Do declared UBO holdings reconcile to 100%? Circular/nominee structures? | Agent 4 executor *(degrades if gated)* |
| **C5** | Registry divergence | Do declared entity particulars match the registry? | Agent 2 / `company_registry` *(degrades if gated)* |
| **C6** | Risk-score divergence | Does the officer-entered value diverge from the document-derived value? | `risk_model_view` vs stored inputs |
| **C7** | Peer divergence | How does this rating compare to prior decisions on similar profiles? | Institution Memory index *(Phase 3)* |
| **C8** | Policy adherence | Was an EDD trigger present but not actioned? | `edd_routing_policy.evaluate_edd_routing()` |
| **C9** | Staleness | Is screening or a document stale relative to the decision date? | `screening_freshness_metadata`, doc expiry |
| **C10** | Override scrutiny | Is an override justified by a specific, non-boilerplate reason? | `decision_model` `override_flag` / `override_reason` |
| **C11** | Missing evidence set | Which required documents are absent or unverified? | `document_reliance_gate.build_required_document_expectations()` |
| **C12** | Inspector lens | Reframes C1–C11 hits as the question an inspector would ask | derived — adds no new findings |

C12 adds no independent findings; it is a presentation of C1–C11. This is
deliberate: every "an inspector would ask…" line is traceable to a
deterministic hit, so nothing in the output is unfalsifiable.

### 6.1 Finding record

```python
{
  "probe_id": "C1",
  "probe_version": "challenge-v1",
  "severity": "high",              # supervisor.schemas.Severity — reuse, don't invent
  "status": "hit",                 # hit | clear | unavailable | not_applicable
  "claim": "Source-of-wealth scored 4 (high) with no verified SOW evidence.",
  "evidence_refs": [
      {"kind": "risk_dimension", "ref": "D3", "value": 4},
      {"kind": "document_slot", "ref": "sow_evidence", "value": "absent"}
  ],
  "officer_question": "What corroborates the declared source of wealth?",
  "remediation": "Request audited accounts or bank statements covering 12 months."
}
```

`status: "unavailable"` is a first-class outcome and **must be rendered in the
UI**. A probe that could not run must never be presented as a probe that passed.

---

## 7. Reproducibility contract

The regulatory claim, and the CI gate.

```python
challenge_hash = sha256(
    canonical_json({
        "policy_version": CHALLENGE_POLICY_VERSION,
        "input_bundle_hash": <hash of the read-only input snapshot>,
        "findings": <findings sorted by (probe_id, claim)>,
    })
)
```

**Invariant:** identical input bundle + identical `CHALLENGE_POLICY_VERSION` ⇒
identical `challenge_hash`, on every run, on every host.

Narration is **excluded** from the hash. Prose may vary; findings may not.

**CI guard (`test_challenge_reproducibility.py`):** run every fixture case 3×
and assert hash stability. This test is the feature — if it cannot be made to
pass, Challenge Mode is not shippable as described, and that should be
discovered in Phase 0, not in an inspection.

---

## 8. The LLM boundary

| Rule | Enforcement |
|---|---|
| LLM receives **only** the findings array — never raw case data, documents, or PII beyond what a finding `claim` contains | narrator takes `List[Finding]`, not an application dict; enforced by function signature and test |
| LLM output is **prose only**: `narrative: str`, `suggested_questions: List[str]` | response schema has no severity/score/verdict/recommendation field |
| LLM **cannot** add, remove, or re-score a finding | post-generation assertion: finding set before == finding set after; violation logs and discards the narration |
| Template fallback always available | works under `CLAUDE_MOCK_MODE` and on API failure — never blocks the run |
| Output tagged | `ai_source: "claude-sonnet-…"` \| `"deterministic_template"`, mirroring the memo convention |

The narration prompt is an **adversarial** reframe of the existing
`COMPLIANCE_ASSISTANT_SYSTEM_PROMPT`. That prompt is written to *assist*; the
challenge prompt is written to *attack*: "You are a senior compliance officer
who did not work this case and is looking for reasons it should not be
approved."

Model choice: Sonnet. Narration is not a reasoning-hard task once findings are
computed, and per-case cost matters at monitoring scale.

---

## 9. Data model & API

### Tables

```sql
CREATE TABLE challenge_runs (
    id                  SERIAL PRIMARY KEY,
    run_id              TEXT NOT NULL UNIQUE,
    application_id      TEXT NOT NULL,
    trigger             TEXT NOT NULL CHECK(trigger IN
                            ('manual','pre_approval','periodic_review','monitoring')),
    policy_version      TEXT NOT NULL,
    input_bundle_hash   TEXT NOT NULL,
    challenge_hash      TEXT NOT NULL,
    findings_json       JSONB NOT NULL,
    narrative           TEXT,
    ai_source           TEXT NOT NULL,
    actor_id            TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_challenge_runs_app ON challenge_runs(application_id, created_at DESC);

CREATE TABLE challenge_acknowledgements (
    id              SERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES challenge_runs(run_id),
    probe_id        TEXT NOT NULL,
    disposition     TEXT NOT NULL CHECK(disposition IN ('accepted','rejected','actioned')),
    officer_note    TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

`challenge_acknowledgements` is what converts Challenge Mode from a report into
evidence: *"RegMind raised 6 challenges; the officer addressed each with a
recorded rationale."* That artifact is the sale.

### Audit

Log runs and acknowledgements through the existing `AuditLogger`. **Do not**
append to the verdict hash chain (`append_verdict_chain_entry`) — that chain is
authoritative and Challenge Mode is not. `challenge_hash` is its own integrity
mechanism.

### Endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/applications/:id/challenge/run` | Idempotent per `input_bundle_hash` — returns the cached run unless `force=true` |
| `GET` | `/api/applications/:id/challenge` | Latest run |
| `GET` | `/api/applications/:id/challenge/:run_id` | Specific run |
| `POST` | `/api/applications/:id/challenge/:run_id/acknowledge` | Officer disposition per probe |

All return `403 challenge_mode_inactive` when the flag is off, following the
`_enterprise_module_disabled` response shape.

---

## 10. Change control

Application Review is **FROZEN** (CLAUDE.md). Challenge Mode is designed to be
additive, but Phase 4 touches the frozen surface.

**Permitted without sign-off (Phases 0–3):** new modules, new tables, new
endpoints, new tests. No edits to `supervisor_engine.py`, `memo_handler.py`,
`validation_engine.py`, or the approval gates.

**Requires Aisha's sign-off (Phase 4):** the Application Review UI panel, since
it adds a control to a frozen page.

### Guard tests (must be written in Phase 0, before any UI)

| Test | Asserts |
|---|---|
| `test_challenge_reproducibility.py` | 3× run ⇒ identical `challenge_hash` |
| `test_challenge_non_authoritative.py` | No challenge code path writes to `applications`, memo, decision, or approval-gate state; `can_approve` / `requires_sco_review` unreachable from challenge code |
| `test_challenge_llm_boundary.py` | Narrator cannot mutate the finding set; narrator signature accepts findings only |
| `test_challenge_frozen_module_unchanged.py` | `run_memo_supervisor()` output byte-identical pre/post |
| `test_challenge_degraded_probes.py` | Gated-package probes report `unavailable`, never `clear` |

The existing frozen-module guards
(`test_application_review_audit_fixes_static.py`,
`test_approval_ux_gates_static.py`, `test_pr5_memo_governance.py`,
`test_memo_staleness_hard_gate.py`, `test_dual_approval_race.py`) must stay
green throughout.

---

## 11. Phasing

| Phase | Scope | Exit criterion |
|---|---|---|
| **0** | Probe engine (C1–C3, C8–C11), input bundle, hash, guard tests. No UI, no LLM. | Reproducibility test green on fixture corpus |
| **1** | Endpoints + template narration + acknowledgements | Officer can run a challenge via API and record dispositions |
| **2** | LLM narration + adversarial prompt | Prose quality accepted; boundary tests green |
| **3** | C7 peer divergence (Institution Memory index) | Peer comparison over the customer's own decision history |
| **4** | Application Review panel + evidence-pack section | **Founder sign-off required** |

Phase 0 is the risk. If findings cannot be made reproducible against real
historic cases, stop and reassess before building anything on top.

Phase 3 is where Challenge Mode starts compounding: peer divergence is only
possible against the customer's own decision history, which a competitor cannot
replicate on day one of a deployment. It is also the shared substrate with the
Policy Replay concept — both are "your own decision history is the asset."

---

## 12. Open questions

1. **Enterprise-veto exemption (§4)** — approve, or accept that Challenge Mode
   is enterprise-only and invisible in pilot?
2. **Pre-approval trigger** — should Challenge Mode run automatically before
   every approval, or stay officer-initiated? Automatic is the stronger pitch
   ("nothing gets approved unchallenged") but creates latency on the approval
   path and an implicit expectation that findings are dispositioned.
3. **Unacknowledged findings** — if an officer approves with unaddressed
   high-severity challenges, is that recorded as a soft flag on the decision
   record? This edges toward authoritative and needs care.
4. **Monitoring scope** — Phase 3+ could challenge live customers nightly, not
   just applications. Per-case LLM cost and alert-fatigue need sizing first.
5. **Fixture corpus for Phase 0** — which historic cases, and are their input
   bundles complete enough to replay?
