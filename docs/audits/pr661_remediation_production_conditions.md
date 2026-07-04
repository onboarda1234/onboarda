# PR #661 — Remediation Scope & Documented Production Conditions

**Branch:** `claude/lucid-carson-ww52p3`  ·  **Base:** `main`
**Status:** complete for a **controlled pilot**; the conditions below must be closed before **uncontrolled production with real regulator scrutiny**.

PR #661 remediates a set of audit findings (B1, B2, H3, H12, H4, B4, H1, plus two
off-by-default drafts H2/H1-memo). It was independently re-audited twice, and the
core fixes were validated on a live PostgreSQL 16 instance. This document records
the items that were **deliberately scoped as production conditions** rather than
closed in this PR, so they are visible and tracked rather than silently assumed.

---

## Production conditions (must close before uncontrolled production)

### PC-1 — Independent full-chain *continuity* of the evidence pack (H4 residual)
**What works today:** the regulator evidence pack exports, per supervisor-audit
row (at `full_internal`), a `canonical_hash_payload` such that a third party can
recompute `sha256(canonical_hash_payload) == entry_hash` — i.e. **per-row
authenticity is independently verifiable** — plus the system's full-chain
`verify_chain_integrity()` attestation.

**The gap:** `supervisor_audit_log` is a **single global hash chain** across all
applications (`append_verdict_chain_entry` links off the global tail, not a
per-application tail), so entries interleave (`app A → app B → app A`). The pack
exports only `WHERE application_id = ?`, so an exported row's `previous_hash` can
point at another application's row that is not in the pack. A regulator can
therefore verify each exported row's authenticity, but **cannot independently
walk chain *continuity*** from the pack alone — continuity currently relies on
trusting the system's attestation.

**Acceptable for a controlled pilot** (per-row authenticity + system attestation),
**not** for uncontrolled production. **Fix options (pick one before production):**
1. **Hashes-only continuity ledger** — export the full chain's
   `(previous_hash, entry_hash)` pairs (no payload, no PII, no verdict content)
   so a regulator can walk genesis→tail, confirm one genesis / no forks / no
   orphans, and confirm this application's recomputable rows sit inside the
   verified chain. Privacy-safe; smallest change. *(Recommended.)*
2. **External anchoring** — periodically publish/notarize signed chain
   checkpoints (head hash + count) so continuity is provable against an anchor
   outside the system.
3. **Per-application sub-chains** — re-architect so each application has its own
   hash chain; a per-case export is then a complete, walkable chain. Cleanest
   long-term, largest change (touches append/verify/B2). Separate PR.

### PC-2 — Suffix-truncation detection of the supervisor chain (H3 residual)
`verify_chain_integrity` detects content edits, mid-chain deletion, forks,
cycles, and duplicate hashes, but — as any hash chain — it **cannot detect
suffix truncation** (deletion of the most recent N entries leaves a shorter,
internally-consistent chain). This is honestly documented in the verifier
docstring. Closing it requires a persisted/sealed external anchor (head hash +
entry count) — the same anchoring as PC-1 option 2. Not required for a pilot.

### PC-3 — Multi-task migration & boot concurrency (B3) — **CLOSED by PR-11**
~~Migrations and boot-time schema mutation run per ECS task with no cross-task
advisory lock, so a rolling deploy that boots ≥2 tasks concurrently can race
(`schema_version` UNIQUE violation / concurrent `ALTER`).~~ Closed: PR-11
serializes the whole boot mutation phase (init_db → seeds → migrations) and
the admin-reset re-seed under a bounded-wait PostgreSQL advisory lock
(`boot_lock.py`, key 8674309941) held on a dedicated connection; timeout
fails startup loudly instead of racing, and process exit (including crash)
releases the lock via disconnect. The supervisor-chain *append* fork race was
already closed in PR #661 (advisory lock + unique index).

### PC-4 — The two drafts must not be wired without their follow-ups
- **H2 erasure (`gdpr_erasure.py`)** — off, unwired, `dry_run=True` default.
  Before wiring: physical file/S3 object deletion (currently `documents` is
  intentionally excluded to avoid orphaning files), DB-level append-only
  protection for `gdpr_erasure_log`, per-application override selection, operator
  UI, and a second-person approval step.
- **H1-memo (`claude_memo_integration.py`)** — off (`ENABLE_CLAUDE_MEMO` unset),
  unwired. Before enabling: integrate so the Claude memo still flows through the
  validation + supervisor gates (fail-closed), decide deterministic-vs-Claude
  default, and review cost/latency.

---

## Verified closed in this PR (for reference)
B1 (audit-trail purge guard, PG-safe), B2 (legacy chain archived + sealed, boots
on legacy/modern/fresh/empty), H3/H12 verifier (link-following, deterministic on
PG, no false positives), H12/B3 concurrent-*append* fork (advisory lock + unique
partial index), H4 per-row recomputability, B4 (client screening removed;
endpoints officer-gated + rate-limited), H1 (fabricated portal cards removed),
migration hygiene (038 monitoring + 039 gdpr apply cleanly, no collision).
Validated on PostgreSQL 16; full suite green.
