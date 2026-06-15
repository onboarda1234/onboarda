# PR-CA4 Root Cause

## Root Cause Summary

PR-CA1 established provider source-of-truth, PR-CA2 added durable evidence/audit fields, and PR-CA3 hardened canonical state integrity. PR-CA4 found that several officer-facing paths still consumed older or thinner projections instead of presenting the canonical CA evidence in a compact business-readable form.

## Exact Root Causes

1. Queue/detail/memo parity gap

   Queue rows and memo inputs did not share an explicit current-risk rollup derived from normalized CA evidence. That made it easy for UI and memo surfaces to count or describe risks differently.

2. Adverse-media under-display

   Memo adverse-media context used legacy summary flags first. When canonical provider evidence contained adverse-media records but summary flags were missing or `none`, memo context could still represent adverse-media coverage as absent.

3. Evidence context loss

   CA evidence normalization preserved provider identifiers, but article/source fields such as publisher, publication date, match rationale, relevance, and confidence were not consistently carried into display-ready evidence shapes.

4. Ambiguous officer wording

   Existing UI labels were technically compatible with stored disposition codes but weak for compliance operations. `No Match` did not clearly mean a false-positive clearance action; `Match` did not clearly mean true match confirmation; `Other` and `Provider screening hit` hid severity.

5. Blocker copy drift

   Approval blockers could show generic screening text or internal reason codes rather than explaining the exact subject/risk/action in plain English.

## Risk Before Fix

- Officers could underestimate adverse-media coverage.
- Queue/detail/memo could disagree on current risk counts.
- Generic labels could make a false-positive clearance or true-match confirmation hard to defend.
- Provider evidence with missing source context could fail to tell the officer what to do next.

## Design Constraint

The fix intentionally avoids cloning Mesh. It keeps default views compact and exposes detailed provider references and evidence through existing collapsible/detail controls.
