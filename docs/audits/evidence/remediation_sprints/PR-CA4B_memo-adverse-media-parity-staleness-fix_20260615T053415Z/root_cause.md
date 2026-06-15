# PR-CA4B Root Cause

## Root Cause

The memo pipeline had two different truths:

1. Queue/detail truth used current CA evidence rollups, including DB-backed monitoring/evidence rows.
2. Memo freshness used stored memo metadata, risk snapshot, and timestamps.

When ComplyAdvantage Mesh adverse-media evidence appeared or changed after a memo was generated, the memo staleness gate did not compare:

- current risk count,
- current unresolved risk count,
- current adverse-media presence,
- provider evidence quality,
- current screening state,
- stored memo adverse-media metadata.

As a result, a memo with `coverage=none` and `has_hit=false` could remain `memo_is_stale=false` even when current canonical CA truth showed unresolved adverse-media risk.

## Fix Summary

PR-CA4B adds a compact canonical memo-screening snapshot:

- built from current application screening truth and DB-backed CA evidence rollup,
- attached to application detail for officer/staging API visibility,
- injected into memo generation input so regenerated memos consume current adverse-media truth,
- included in memo fingerprinting so DB-backed screening/evidence changes invalidate memo reuse,
- persisted in memo metadata as `canonical_screening_current_summary`,
- compared by `_memo_staleness_view(...)` against stored memo adverse-media/count metadata.

If the current snapshot shows adverse media or current unresolved risk and the memo metadata says none/false/zero/missing, the memo becomes stale with:

- trigger: `memo_screening_adverse_media_truth_mismatch`
- reason: current canonical ComplyAdvantage Mesh evidence no longer matches memo screening/adverse-media metadata.

## Out Of Scope

- No PR-7, DOC, CR, PR-CA5, or unrelated remediation work was started.
- No redesign of PR-CA4 UI surfaces was included.
- No changes to provider API credentials, CA webhooks, or Mesh dashboard integration were made.
