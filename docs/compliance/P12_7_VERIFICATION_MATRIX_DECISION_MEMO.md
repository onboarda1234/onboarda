# P12-7 Decision Memo — Verification-Matrix Fidelity (DCI-014 / DCI-015)

**Status:** AWAITING FOUNDER SIGN-OFF · **Prepared:** 2026-07-22 (overnight batch)
**Approver:** Aisha Sudally (asudally@onboarda.com)
**Register row:** P12-7, Phase 11 (`docs/REMEDIATION_MASTER_LIST.md`)

This memo carries the two halves of P12-7 that are **decisions, not code**: the
five `# TODO confirm` document-policy mappings (DCI-015) and the authoring of
deterministic evaluators for HYBRID verification checks (DCI-014). The
accompanying PR lands the *mechanism* only — flag-gated OFF, zero live
behaviour change — because both halves alter compliance behaviour inside or
adjacent to the frozen Application Review module and therefore require
explicit approval per `CLAUDE.md` Module Status & Change Control.

---

## Part A — DCI-014: hybrid rules-first gate (mechanism landed OFF)

`verification_matrix.py` has always documented HYBRID checks as *"rules
first, AI fallback only when deterministic check is INCONCLUSIVE"*, but no
deterministic first pass existed — every HYBRID check went straight to
Claude on every run, and `CheckStatus.INCONCLUSIVE` was never produced
anywhere at runtime.

**What the PR ships (no approval needed — inert by default):**
- `ENABLE_HYBRID_INCONCLUSIVE_GATE` config flag, **default OFF**.
- `HYBRID_DETERMINISTIC_EVALUATORS` registry (**empty**) + runner in
  `document_verification.py`. Fail-safe by construction: an unevaluated,
  None-returning, or raising evaluator leaves the check INCONCLUSIVE → AI
  path, exactly as today. The pass can only *reduce* the AI set, never
  invent a silent pass.
- `_aggregate` is now INCONCLUSIVE-aware: a surviving INCONCLUSIVE flags the
  document for manual review instead of silently diluting confidence.

**What needs your sign-off (per evaluator, later):** each deterministic
evaluator registered for a HYBRID check id changes which checks stop going
to Claude — i.e. live verification behaviour feeding the frozen approval
gates via `document_reliance_gate.py`. Recommended first candidates (purely
computational, lowest judgement content): document expiry-date checks and
certification-date arithmetic. Each registration should come as its own
reviewed PR citing this memo, with the flag flipped on staging only after
the evaluator set is approved.

**Sign-off (Part A activation):**
- [ ] Approved to author evaluators for the checks listed in the activation PR
- [ ] Approved to set `ENABLE_HYBRID_INCONCLUSIVE_GATE=true` on staging
- Signature / date: ______________________

---

## Part B — DCI-015: the five `# TODO confirm` mappings

Source: `enhanced_requirements.py` `ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP`
(PR-PRS-B block). These map periodic-review EDD document-request keys to
canonical Agent 1 document policies. Changing any of them changes which
verification checks run for that upload row in the Application Review inline
document flow (frozen scope) — hence sign-off, not silent edits. **No code
change ships in this PR; current runtime behaviour continues unchanged.**

| # | Requirement key | Current mapping | Question | Recommendation |
|---|-----------------|-----------------|----------|----------------|
| 1 | `updated_cap_table` | `reg_sh` | reg_sh vs structure_chart? | **Keep `reg_sh`.** A cap table is a shareholder register artefact (names, holdings, classes) — `reg_sh` checks (name-match against declared shareholders) apply directly; `structure_chart` checks assume a diagram. |
| 2 | `proof_of_ownership_or_control` | `structure_chart` | confirm | **Keep `structure_chart`.** "Ownership or control" evidence is structural (chains, SPVs, nominee layers); the structure-chart policy's UBO-trace checks are the closest fit. If submissions in practice are share certificates, revisit toward `reg_sh`. |
| 3 | `updated_company_extract` | `cert_inc` | cert_inc vs business registration? | **Keep `cert_inc`.** A registry extract carries the same primary facts the cert_inc policy verifies (legal name, number, incorporation date, status). A dedicated `registry_extract` policy is a v7 nice-to-have, not a pilot need. |
| 4 | `updated_registered_office_proof` | `poa` | entity registered-address policy? | **Keep `poa` for pilot, with a caveat.** The poa policy is person-oriented (name-match against a person); for an entity registered office the name-match target is the entity. Confirm the poa checks receive the entity name as the match subject for entity-category rows; if they hard-assume a person, this mapping needs a small policy variant before heavy use. |
| 5 | `updated_authorised_contact_confirmation` | `board_res` | confirm | **Keep `board_res`.** An authorised-contact confirmation is functionally a board authorisation; board-resolution checks (signatories, date, entity name) match. If practice shows plain letters, downgrade to `supporting_document` (manual) instead. |

Net recommendation: **confirm all five as-is** (with the #4 caveat verified),
which converts the TODOs into signed decisions with zero runtime change — the
lowest-risk closure of DCI-015. Any mapping you decide differently becomes a
one-line PR + lockstep update of `test_pr_prs_b_evidence_gates.py` (which
pins #3 and #5).

**Sign-off (Part B):**
- [ ] All five confirmed as recommended (TODO comments may be removed citing this memo)
- [ ] Exceptions: ______________________
- Signature / date: ______________________
