# RegMind Triage Score — Methodology (rts-1.1)

**Purpose:** rank screening hits for officer attention. **Nothing else.**
**Effective:** 2026-07-17 (PR #790, rts-1.0) · **Current version:** `rts-1.1` (stamped on every scored hit as `triage_score_version`)
**Owner:** compliance engineering; weight changes are governed changes requiring founder approval and a version bump.

## Version history
- **rts-1.1 (2026-07-19):** defect fix — category base points were not applied to
  live provider payloads that carry categories only in `provider_aml_types_raw`
  (typed indicator models absent); such hits scored under the uncategorized floor
  (base 5) despite a correctly displayed category. Category detection now falls
  back to the same aml-type → category mapping the display layer uses. **Weights
  unchanged**; scores for affected hits rise by their category base (e.g.
  watchlist `["warning"]` + exact name: 40 → 53; adverse media + exact name +
  article evidence: 48 → 58). Historical rts-1.0 scores stay stored and
  interpretable under their own version label.
- **rts-1.0 (2026-07-17, PR #790):** initial version.

## 1. What the score is — and is not
The RegMind triage score is a deterministic 0–100 ranking computed at screening time
for each provider hit, from provider-supplied facts only. It answers one question for
the reviewing officer: *"in what order should I look at these hits?"*

It is explicitly **not**:
- a match probability or confidence percentage;
- an input to risk scoring, approval gates, memo content, or any automated decision
  (statically test-enforced: the risk engine contains no reference to it);
- a filter — no hit is ever hidden or suppressed by its score. Every hit remains
  individually reviewable regardless of rank.

## 2. Inputs and weights (v1)
All inputs are facts returned by the screening provider (ComplyAdvantage Mesh) and
stored verbatim alongside the score. The provider's own raw relevance value
(`provider_match_score_raw`) is preserved untouched and unused pending the provider's
scale confirmation.

| Component | Points | Provider fact used |
|---|---|---|
| Sanctions list match | 40 | sanctions indicator on the hit |
| PEP class 1–2 | 30 | PEP indicator + class |
| PEP (other/unknown class) | 22 | PEP indicator |
| Watchlist entry (non-sanctions) | 18 | watchlist indicator |
| Adverse media | 15 | media indicator |
| Uncategorized match (floor) | 5 | none of the above present |
| Multiple risk categories | +6 | ≥2 categories on one hit (strongest counts as base) |
| Exact name match | +35 | provider match types (`name_exact` / `exact_match` / `aka_exact`) |
| Strict-search match (fallback) | +15 | surfaced by strict or both passes, no exact token |
| Relaxed-only match (fallback) | +6 | surfaced by relaxed pass only |
| Article evidence attached | +8 | provider media evidence present |
| Relative / close associate | +6 | provider RCA relationship |

Score = min(100, max(1, sum)). Each applied component emits a plain-English reason
string stored with the hit (`triage_score_reasons`) — the officer sees *why* a hit
ranks where it does.

## 3. Determinism, versioning, and audit
- Pure function of the provider match: same hit → same score, always. No randomness,
  timestamps, or environment input.
- Stored per hit at screening time inside the immutable report snapshot, with the
  formula version. Superseded reports are archived, never mutated — every score an
  officer ever saw remains reproducible and attributable to its formula version.
- Recalibration process: new weights → new version string (`rts-1.1`, …) → founder
  approval → this document updated → old snapshots keep their original version.

## 4. Presentation rules
Displayed as "triage" or "relevance" ranking with its reasons — never as a percentage,
probability, or "confidence". The weak-tail display threshold (currently 40, server
constant) groups low-ranked hits for collapsed presentation; grouped hits remain fully
reviewable and are counted honestly. Hits screened before rts-1.0 have no score and are
labelled unscored — never silently classified weak.

## 5. Known v1 limitations (honest)
- Birth-year/nationality corroboration is not yet weighted: the matched profile's DOB
  is captured (strictly parsed — malformed values rejected, never guessed) but
  comparison against applicant data lands in a future version once live field presence
  is confirmed on enriched staging data.
- Weight calibration is provisional pending review against the first enriched staging
  sample; any adjustment follows the versioning process above.
