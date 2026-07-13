# RSMP Tier 0B Fail-Closed Routing Review Plan

**Status:** HOLD — stacked draft for human review; no merge or activation authorized

**Dependency:** PR-1 branch `codex/rsmp-tier0a-parser-mapping-fidelity`

## 1. Delivery and rebase rule

PR-2 is opened as a stacked draft with PR-1 as its review base. PR-1 must be
reviewed and human-merged first. After that merge, PR-2 must be rebased onto the
resulting `main` merge commit, conflicts resolved without dropping review
evidence, and the full Tier 0A/Tier 0B suite rerun. PR-2 must not merge while it
still targets the feature branch.

## 2. Fail-closed contract

When the Tier 0A activation flag is enabled, an unresolved controlled mapping
emits a reason in this form only:

`stale:unmapped_<family>:<12-character-sha256-prefix>`

The raw label never appears in the sentinel. Structured evidence is persisted
separately in `applications.risk_dimensions.controlled_mapping_evidence` using
the existing JSON risk evidence field. Every evidence record contains family,
raw value, normalized value, hash, application ID, request ID, config version,
resolution status, controlled ID, canonical label, and resolved score where
applicable. No schema or migration is required.

A fresh recomputation atomically replaces only mapping sentinels. It preserves
unrelated `stale:*` controls and emits every currently unresolved family, so:

- multiple unresolved fields coexist;
- resolving one field does not clear another unresolved family;
- unrelated recompute/change-management staleness is not overwritten;
- approval remains blocked while any `stale:unmapped_` reason remains.

## 3. Volume-specific policy

Only the exact approved `Over USD 5,000,000 per month` record emits
`monthly_volume_score_4`. The generic `sub_factor_score_4` reason is not emitted
for that volume factor and is not consumed as volume policy by the approval
classifier.

The exact volume reason selects the Compliance review route and compliance
package. It does not apply a High floor and does not select EDD by itself.
Sector score 4 retains its existing High floor, EDD route, generic score-4
evidence, and dual-control approval behavior. PEP, entity, and ownership score 4
cannot emit the volume-specific reason.

## 4. Geography boundary

Blank/missing incorporation country participates in the Tier 0B unresolved
state. Hong Kong SAR, Congo (DRC), and Türkiye use the exact Tier 0A aliases.
Every other country and all regions remain on the existing manual FATF pilot
path pending Tier 1B and do not automatically receive Tier 0B sentinels.

## 5. Activation and recomputation holds

The common Tier 0A activation flag remains OFF by default. Before deliberate
staging activation, reviewers must approve the live config/code/Gate 0 v4 diff
and the complete read-only active-application replay, including score, tier, EDD,
approval, and unresolved-mapping deltas. Tier 0C recomputation remains a later,
separately approved action.

Neither this plan nor the draft PR makes a production-readiness claim.
