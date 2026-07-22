# PR-C — Honesty & Audit Integrity (audit findings H4, H5, H6 + undo role gate)

**Status:** DESIGN v2 — revised after independent verification (verdict: defects
correctly identified, fixes directionally right, but do not build as written —
4 revisions required). Awaiting founder sign-off (H4/H5 change displayed
compliance values; H6 changes audit-write behaviour — both on the frozen
Screening Queue module).

**v2 revision summary (what the verifier caught → how v2 fixes it):**
1. **H5 must NOT reorder the shared `_screening_evidence_category`** — that
   function also feeds evidence-to-subject linking, and returning "Sanctions"
   instead of "Adverse Media" for a multi-category item would drop it from
   `_screening_row_categories`, silently **detaching** an adverse-media-only
   candidate from the subject (there is no row-level sanctions fallback). v2
   adds a SEPARATE `_screening_evidence_bucket_category` used ONLY by the
   triage-bucket caller (server.py:21836), so linking is provably untouched.
2. **H4's `(matched_name, source_title)` distinct-story key was unsound** and
   contradicted the Agent-3 narrative (which groups on `(matched_name, score,
   reasons)`). v2 adopts the simpler, strictly-safer path: **drop the new
   server field; reword the strip to a count-only line that claims no
   duplication**, and leave the genuine grouping insight to the Agent-3
   narrative, which already computes and shows it.
3. **H6 `prior_disposition` wasn't readable** (the `existing` SELECT fetches
   `id` only) and a single scalar can't represent a batch. v2 widens the SELECT
   to `id, disposition` and logs a **per-hit** `{hit_id: prior_disposition}`
   map. The audit_log is **append-only** (not hash-chained — corrected wording),
   which is the property durability actually relies on. Rationale capped at
   `[:1000]` for parity with the subject-level handler.
4. **Undo gate needs a disposition read on the undo path** (undo goes straight
   to DELETE, never fetching `existing`). v2 pre-fetches each hit's current
   disposition before the write loop and gates undo/overwrite-of-`cleared` on
   clear-roles there.
**Branch:** fresh from `main` after PR-B (#831) — merged-PR protocol.
**Scope:** screening-review honesty + the per-hit audit record only. No change
to risk scoring, memo, routing, four-eyes. Frozen engines zero-diff.

These are compliance-credibility defects, not security: the UI states things it
hasn't verified, a sanctions hit can read as zero, and an officer's clearance
rationale isn't durably recorded.

---

## H5 — A sanctions hit can show under a "0" Sanctions tile

**Defect.** `_screening_evidence_category` (server.py:21315) collapses every
category signal (`match_category`, `match_categories`, `risk_type_labels`,
`evidence_type`) into one text blob and tests **adverse/media FIRST, pep
second, sanctions third, watchlist fourth**. ComplyAdvantage legitimately emits
multi-category hits (a profile that is both sanctioned AND in adverse media). A
sanctions+adverse hit is therefore categorised `"Adverse Media"`, so
`_screening_queue_row_triage` buckets it under adverse_media and
`buckets.sanctions = 0` — the strip renders **"Sanctions & watchlist: 0"** while
a real sanctions hit exists, filed under Adverse media. This is the exact
failure class F3 was written to prevent, one level up (across buckets, not
within tile 1). It is also the reverse of the UI's own severity ranking
(`evidenceCategoryPriority`, arie-backoffice.html:14677 — sanctions first).

**Fix (v2 — isolated, provably zero linking impact).** Do **not** touch the
shared `_screening_evidence_category` (server.py:21315) — it feeds
evidence-to-subject linking via `_screening_row_categories` (22377) and the
candidate categoriser (22439), and reordering its single return value would
change `row_categories` set membership and can **detach** an adverse-media
candidate from a sanctions+adverse subject (there is no row-level sanctions
fallback — verifier F1, confirmed against 22383-22389 / 22449 / 22470).

Instead add a dedicated bucket categoriser used ONLY by the triage-bucket
caller at 21836:

```
def _screening_evidence_bucket_category(*values):
    # Most-material-first, for TRIAGE BUCKETING ONLY (never linking/state).
    text = _screening_evidence_norm(" ".join(_screening_evidence_text(v) for v in values))
    if "sanction" in text: return "Sanctions"
    if "watchlist" in text or "warning" in text: return "Watchlist"
    if "pep" in text or "politically exposed" in text or "political" in text: return "PEP"
    if "adverse" in text or "media" in text: return "Adverse Media"
    return "Unclassified Provider Risk"
```

and call it at 21836 (the line that sets each item's `category`, which
`_screening_queue_row_triage` buckets on via `_TRIAGE_BUCKET_BY_CATEGORY`,
23613). The linking callers (22377, 22439) keep calling the unchanged
`_screening_evidence_category`, so their behaviour is byte-identical.

- A multi-category hit now buckets once under its most-material category, so the
  Sanctions tile can never read 0 while a sanctions signal exists; the
  bucket-sum-equals-total invariant is preserved (one category per item).
- The adverse-media tile may show one fewer for such a hit — the safe direction
  (sanctions is the material signal; the officer still sees the hit, under
  Sanctions).

**Guard test:** a multi-category (sanctions+adverse) item buckets to
`sanctions`; AND a linking regression — a name-matched adverse-media candidate
still links to a sanctions+adverse subject (proving the shared categoriser and
`_screening_row_categories` are untouched).

---

## H4 — "near-identical copies of one story" asserted from a count alone

**Defect.** `screeningSubjectRollupStrip` (arie-backoffice.html:17280) prints
"⚖️ N adverse-media hits, but **near-identical copies of one story** — grouped
so a single decision covers the set" whenever `triage.buckets.adverse_media >=
10`, with **no** consultation of any grouping/dedup signal. Ten genuinely
distinct adverse stories are described to the officer as duplicates of one
matter — a fabricated claim on a compliance-decision surface.

**Fix (v2 — reword to claim nothing; defer grouping to Agent 3).** The verifier
showed the proposed `(matched_name, source_title)` distinct key is unsound: it
would re-introduce the "one story" fabrication when items lack those fields, and
would contradict the Agent-3 narrative (which groups on `(matched_name, score,
reasons)`, server.py:26559) when a story spans differently-titled articles.
Rather than run a second, divergent grouping computation in the strip, remove
the fabricated claim entirely:

- Replace the `adverse >= 10` "near-identical copies of one story" block with a
  **count-only, non-duplication** line, e.g.:
  "⚖️ N adverse-media hits — review the ranked list; RegMind groups
  near-duplicates for review where detected."
- The **genuine** grouping insight already lives on the same screen in the
  Agent-3 triage narrative, which computes real
  `(matched_name, score, reasons)` groups and says e.g. "the remaining 99 are
  near-identical adverse-media hits (all triage 58) — no single one stands out."
  That is the correct, server-verified place for the duplication claim.

No new `triage` field, no divergent signature, no capped-set distinct count, no
cross-widget contradiction, no triage-invariant change. Strictly safer and
equally honest.

**Guard test:** the strip no longer contains "copies of one story" / any
duplication assertion gated on a bare count; the count-only line renders for a
high-adverse subject.

---

## H6 — Per-hit clearance rationale is not durably recorded

**Defect.** In `ScreeningHitDispositionHandler.post` the audit-log detail JSON
(server.py:24624) omits `rationale`; the row is overwritten on re-decision
(UPDATE, 24608) and DELETEd on undo (24596). So the rationale an officer wrote
when clearing a hit exists only in the live/overwritten row — a superseded or
undone clearance's reasoning is destroyed unrecoverably. The subject-level
handler logs `rationale[:1000]`; the per-hit handler must match. For a regulated
disposition system "who / when / **why**, immutable" is not met.

**Fix (v2).** The `audit_log` is **append-only** (no `UPDATE`/`DELETE
audit_log` anywhere in the backend; each POST writes a fresh row — verifier F4),
so recording the rationale there makes it the durable record even though the
`screening_hit_dispositions` row is overwritten/deleted. Two corrections over v1:

- **Widen the `existing` SELECT** (server.py:24602) from `SELECT id` to
  `SELECT id, disposition` so the prior disposition is actually readable at the
  log point.
- The handler writes a **batch** of hit_ids with one `log_audit` call after the
  loop, so a single scalar prior can't represent them. Capture a **per-hit map**
  and log both:

```
"rationale": (rationale or "")[:1000],           # parity with subject-level handler
"prior_dispositions": { hit_id: <prior disposition or None>, ... },
```

Build `prior_dispositions` in the write loop (read each hit's current
disposition before the UPDATE/INSERT/DELETE). The mutable row holds current
state; the append-only `audit_log` holds the immutable who/when/why/what-was-
superseded. No schema change; additive to the audit detail only.

---

## MEDIUM — Undo/overwrite has no role gate

**Defect.** The clear-role check (server.py:24560) fires only when the *new*
disposition is `cleared`. An analyst (barred from clearing) can therefore
**undo** (delete) an SCO's recorded `cleared` row, or **overwrite** it with a
non-cleared disposition (e.g. `escalated`) — silently reversing a privileged
clearance.

**Fix (v2).** The undo path goes straight to DELETE and never fetches `existing`
(server.py:24596), so the gate needs its own disposition read. **Pre-fetch each
hit's current disposition before the write loop** (one query, reused by H6's
`prior_dispositions`), then: if any targeted hit's current disposition is
`cleared` and the actor is not in `_SCREENING_HIT_CLEAR_ROLES`, return 403
(governance-logged) — for both undo and overwrite. Reversing a false-positive
clearance is as sensitive as making one. Admin/SCO/CO unaffected; existing
per-hit tests use admin.

*(Note: the audit's "typo'd subject accepted" MEDIUM is already closed by PR-B —
a non-existent subject yields an empty real-hit set, so a non-undo write is
fail-closed **rejected (503 when context is empty, or 400 unknown-hit)** and
undo is a harmless no-op DELETE. No further change; verifier confirmed the
rejection holds.)*

---

## What is deliberately NOT changed

- Risk / memo / validation / supervisor engines — zero diff.
- The four-eyes logic and the subject-level `/screening/review` contract.
- The bucket-sum-equals-total triage invariant (H5 keeps one category per item;
  H4 adds a *distinct* count alongside the total, never changing the total).
- H-items reserved for PR-D/PR-E (stuck loading, unknown-provider→CA default,
  declared-PEP truthy parity, data-outage flag, two-reviewer sync, box-collapse,
  low cleanup).

---

## Guard tests (ship with the change)

- **H5:** a multi-category (sanctions+adverse) item categorises as `Sanctions`;
  `_screening_queue_row_triage` counts it in `buckets.sanctions`, not
  adverse_media; buckets still sum to total. A linking test: reordering does not
  change subject attribution for a name-matched candidate.
- **H4:** `_screening_queue_row_triage` emits `adverse_media_distinct`; the strip
  shows the "grouped" line only when `distinct < total` and never claims "one
  story" when `distinct == total` (node-extracted render, like `test_srp3_*`).
- **H6:** the per-hit audit detail includes `rationale` (and `prior_disposition`
  on overwrite); an overwrite then undo leaves the earlier rationale recoverable
  from `audit_log`.
- **Undo gate:** an analyst undo/overwrite of a `cleared` row → 403 (governance
  logged); an SCO/admin → allowed.

Frozen guard suites that must stay green: `test_srp3_phase_b_review_ui_static`,
`test_screening_queue*`, `test_backoffice_ca_truthflow_static`,
`test_inline_screening_runtime`, the per-hit endpoint tests, and the approval/
four-eyes suite.

---

## Resolved (verifier)

1. **H5 linking safety** — RESOLVED by isolating the reorder to a new
   `_screening_evidence_bucket_category` used only at 21836; the shared
   categoriser (and therefore all linking/state) is untouched. Screening STATE
   derivation never called the categoriser anyway (confirmed).
2. **H4 signature basis** — RESOLVED by dropping the server field entirely
   (count-only reword); genuine grouping stays in the Agent-3 narrative.
3. **H6 cap** — RESOLVED at `[:1000]` (parity with the subject-level handler;
   the cap is a size limit, not redaction — rationale is compliance data that
   should be recorded).

## Implementation note

H6's `prior_dispositions` capture and the undo-gate's disposition check use the
**same** pre-loop read of each targeted hit's current disposition — one query,
two consumers.
