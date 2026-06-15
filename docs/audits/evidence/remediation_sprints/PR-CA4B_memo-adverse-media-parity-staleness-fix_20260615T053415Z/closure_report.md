# PR-CA4B Closure Report

PR: `PR-CA4B - Memo Adverse Media Parity and Staleness Fix`

## Status

Branch-stage implementation and validation are complete. Closure is pending PR merge, staging deployment, `/api/version` proof, API smoke, and browser smoke.

## Issues Addressed

- `CA-PAR-002` — adverse media lost or under-displayed in application detail / memo context.
- `CA-PAR-009` — memo must consume canonical adverse-media evidence.
- `CA-UX-001` — adverse media not clearly surfaced.
- `CA-UX-002` — hit counts inconsistent across queue/detail/memo.

## Diagnosis

The PR-CA4 staging smoke failed because queue/detail current truth showed unresolved CA adverse-media risk, but latest memo metadata still said `coverage=none`, `has_hit=false`, and `memo_is_stale=false`.

## Fix Implemented

- Added canonical memo-screening current snapshot.
- Injected current DB-backed CA evidence truth into memo inputs.
- Stored `canonical_screening_current_summary` on generated memo metadata.
- Included current screening snapshot in memo input fingerprinting.
- Added staleness detection for stored memo adverse-media/count metadata mismatch.
- Persisted stale memo trigger/audit before/after state when mismatch is enforced.
- Exposed current memo-screening snapshot on officer application detail API.

## Validation

- Focused memo/API tests: `40 passed`
- PR-CA4 / CA rollup regression tests: `55 passed`
- Closed-control regression subset: `99 passed`
- PR-CA2 / PR-CA3 evidence/webhook regressions: `63 passed`
- Full backend suite: `5374 passed, 25 skipped`

## Current Issue Status

| Issue | Status | Evidence |
|---|---|---|
| `CA-PAR-002` | PARTIALLY FIXED | Branch code/tests pass; staging API/browser smoke pending. |
| `CA-PAR-009` | PARTIALLY FIXED | Branch code/tests pass; staging API/browser smoke pending. |
| `CA-UX-001` | PARTIALLY FIXED | Backend parity fixed; staging browser smoke pending. |
| `CA-UX-002` | PARTIALLY FIXED | Memo count/staleness bridge fixed; staging smoke pending. |

## Closure Rule

Do not mark PR-CA4 or related CA-PAR/CA-UX issues `CLOSED` until:

- PR-CA4B is merged to `main`,
- merged main is deployed to staging,
- staging `/api/version` returns matching `git_sha` and `image_tag`,
- staging API smoke proves memo/adverse-media parity,
- staging browser smoke passes,
- evidence pack is complete.

No unrelated remediation item was marked closed.
