# PR-CA4 Diagnosis

## Scope

PR-CA4 re-diagnosed Mesh parity and officer-facing screening UI/UX gaps from latest `origin/main`.

- Base `origin/main` SHA: `b83e3052485d432a1e47adbe1f4d9bb1bbea4a58`
- Branch: `codex/pr-ca4-mesh-parity-screening-ui-ux`
- Dependency status:
  - PR-CA1 present: `5d664a51fb0d6161095aff88f17a657b5e23cacd`
  - PR-CA2 present: `6b6ea16881ae7f93a0eeb4256bb4f205692be757`
  - PR-CA3 present: `9b210f3884f9cd3bf0c28d82457e8f2b1dac69ca` and corrective merge `523ac8f1d93b2614eb3aa8286c255ea1cd8580eb`

## Re-diagnosis Result

The PR-CA1/CA2/CA3 foundation was present, but the default officer UI and memo inputs still had parity and usability gaps:

- Screening queue evidence preserved provider refs, but the queue/detail payload did not expose a compact canonical current-risk rollup separating current, unresolved, stale, historical, and duplicate provider records.
- Adverse-media provider evidence could exist while memo context still relied on older rollup flags and could understate coverage when those flags were absent.
- Provider evidence normalization did not carry all available article/source context through to officer-visible detail structures.
- UI labels still used ambiguous operational language such as `No Match`, `Match`, `Other`, and `Provider screening hit` in primary review surfaces.
- Approval blocker copy could expose generic or technical screening reason codes instead of subject-specific plain English.
- Evidence cards could repeatedly show generic provider hit wording and source-unavailable text without a useful next action.

## Affected Surfaces

- Screening Queue
- Application Screening Review
- Evidence drawer/cards
- Case Command Centre approval blockers
- Screening review disposition controls
- Compliance memo adverse-media context

## Out of Scope Confirmed

No PR-7, DOC, CR, post-approval locking, officer correction controls, or broad adverse-media Mesh clone work was started.
