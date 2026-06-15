# PR-CA4C Root Cause

## Root Cause Summary

The Screening Queue had accumulated detail-view responsibilities inside the list-view path.

Specific root causes:

1. The queue list and row detail used the same full row shape. That made the default list payload carry heavy provider evidence and full screening evidence even when the officer only needed a summary.
2. The visible filter bar retained an older dedicated Application reference filter after the queue had gained a general search field, creating duplicate search concepts.
3. Provider and entity wording still reflected the older company-sanctions framing rather than the current ComplyAdvantage Mesh AML scope.
4. The type filter exposed internal subject taxonomy (`individual`) as an officer-facing default option, even when the queue had no uncategorized person rows.
5. Provider reference search depended too much on detailed evidence payload shape instead of explicit searchable summary reference arrays.

## Why This Matters

These defects made the queue slower and noisier than it needed to be. They also weakened officer clarity because the list view mixed broad AML screening with sanctions-only wording and exposed internal filter concepts.

The fix separates summary list behavior from detailed evidence behavior while preserving defensibility through lazy-loaded detail evidence.

