# PR-CA4B Diagnosis

PR-CA4B re-diagnosed the failed PR-CA4 staging smoke from latest `origin/main`.

- Base `origin/main` SHA used for diagnosis: `af766c94f3540c02d11b22404070ccaa4923310d`
- Branch: `codex/pr-ca4b-memo-adverse-media-parity-staleness-fix`
- Failing staging case: `ARF-2026-900164`
- PR-CA4 deployment SHA that reproduced the failure: `af766c94f3540c02d11b22404070ccaa4923310d`

## Failed Smoke Reproduced From Evidence

The PR-CA4 staging API smoke showed the application-level CA truth and latest memo metadata disagreed:

- Queue/detail current truth: `Review Required`
- `current_risk_count = 7`
- `current_unresolved_risk_count = 7`
- `has_adverse_media_hit = true`
- Latest memo metadata:
  - `adverse_media_state_summary.coverage = none`
  - `adverse_media_state_summary.has_hit = false`
  - `memo_is_stale = false`
  - `memo_requires_regeneration = false`

This means an officer could see current unresolved ComplyAdvantage Mesh adverse-media risk while the memo remained reliance-ready and understated adverse media.

## Code Paths Inspected

- `arie-backend/memo_handler.py`
  - `_screening_adverse_media_context(...)`
  - fresh memo `adverse_media_state_summary`
  - financial-crime risk evidence triggers
- `arie-backend/server.py`
  - `ApplicationDetailHandler`
  - `_memo_staleness_view(...)`
  - `_memo_generation_fingerprint(...)`
  - `_memo_fingerprint_source(...)`
  - `_ensure_memo_fresh_or_mark_stale(...)`
  - screening queue evidence rollup helpers
- CA evidence and queue tests covering current/stale/historical risk counts.

## Diagnosis Result

Fresh memo generation had PR-CA4 logic to detect canonical adverse-media evidence when that evidence was present in the memo input. The missing bridge was between DB-backed current CA evidence rollups and stored memo freshness/readiness.

Specifically, application detail used `_memo_staleness_view(...)`, which checked:

- explicit persisted memo staleness,
- risk snapshot mismatch,
- application input timestamp staleness.

It did not compare the current canonical CA evidence rollup against the stored memo adverse-media/count metadata. Therefore, an old memo could remain `memo_is_stale=false` even when current CA evidence had unresolved adverse-media risks.

Verdict: PR-CA4 remained `PARTIALLY FIXED`; PR-CA4B is required before CA-PAR-002 / CA-PAR-009 / CA-UX-001 / CA-UX-002 can close.
