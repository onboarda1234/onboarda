# PR-B — Finalize-Gate Integrity (audit findings H1, H2, H3)

**Status:** DESIGN v2 — revised after independent verification (verdict:
direction sound, 4 corrections required). Awaiting founder sign-off (behaviour
change on the frozen Screening Queue module).

**v2 revision summary (what the verifier caught and how v2 fixes it):**
1. The uncapped denominator and subject hit-set come from
   `_screening_review_subject_context` — which the frozen `ScreeningReviewHandler`
   **already calls** (`row_context`, server.py ~24658) and which loads evidence
   **uncapped** (server.py ~22132). NOT from the queue-loop builders (inline,
   capped at 200/app) and NOT from `row.triage.total` (also capped). This
   resolves the original "cost" and "wrong denominator" risks at zero extra DB
   cost.
2. The server-stamped `hit_id` must be **deterministic and never empty**
   (`_screening_evidence_item_key` returns "" for id-less, source-less items);
   v2 hashes a stable fallback. The client reads `item.hit_id` only — no `idx-`
   fallback that would mismatch the server.
3. The `test_api.py` per-hit tests are **reclassified as CHANGED, not
   stay-green**: they currently POST fabricated ids against an evidence-less app
   expecting 200; under the anti-fabrication rule they must seed real evidence
   and derive ids from it. No empty-set carve-out (that would re-open the hole).
4. The GET `/screening/hit-disposition` response must **return the rollup
   (with the uncapped `total`)** so the browser has the true denominator, and
   "load all hits" must fetch the **uncapped** subject evidence, or a >200-hit
   subject can never reach `loaded == total` and finalize deadlocks.
**Branch:** to be cut fresh from `main` after PR-A (#829) — merged-PR protocol.
**Scope:** the per-hit screening-review disposition + finalize path only. No
change to risk scoring, memo, routing, or the four-eyes engine. Frozen engines
(`rule_engine`, `memo_handler`, `validation_engine`, `supervisor_engine`) stay
zero-diff.

---

## The problem in one sentence

A screening subject (company or person) can be marked **CLEAR** — closing its
approval blocker — without every real hit actually having been reviewed,
because hit identity, completeness, and the clear/match rule are all decided in
the browser from the partial data it happens to have loaded, and the server
never re-checks any of it.

Three concrete defects, all verified in the audit:

- **H1 — one click clears many hits.** `screeningHitId` (arie-backoffice.html
  ~17121) derives a hit's identity from the *first available* provider id,
  preferring `provider_profile_id`. The normal ComplyAdvantage shape is **one
  profile → many adverse-media articles**, so every article collapses to the
  same id and shares one disposition-state slot
  (`SCREENING_HIT_DISPOSITION_STATE[key][hitId]`). Clearing one card clears all
  its siblings; the rollup then counts them resolved and the finalize form
  opens — on hits the officer never opened. The card even says "this hit only".

- **H2 — completeness counted from what's on screen.** `screeningSubjectRollup`
  / `screeningSubjectHitStates` (~17150) treat `screening_evidence.items.length`
  as the universe. Evidence is capped/lazy-loaded, so if 3 of 10 hits are
  loaded and cleared, the rollup reports "CLEAR — every hit resolved"
  (~17171) and `screeningSubjectFinalizeSection` renders (~17214) while 7
  server-known hits were never shown.

- **H3 — no server enforcement.** `_screening_hit_rollup` (server.py ~24345)
  only counts recorded disposition rows; it has no denominator and never
  compares against stored evidence. The POST handler accepts any `hit_ids`
  string without checking it maps to a real hit (verified live: three
  fabricated ids `b1,b2,b3` persisted and inflated the "cleared" count). The
  frozen `/api/screening/review` finalize never reads
  `screening_hit_dispositions` at all, so the rule "CLEAR only when every hit
  is a cleared false-positive; TRUE MATCH if any hit is confirmed" exists
  **only** in the browser and any direct API caller bypasses it.

---

## Design principle

**Move hit identity and the completeness/rollup verdict to the server; make the
browser read them, not compute them.** The server already owns the evidence and
already has a canonical per-item identity function — we surface it instead of
re-deriving a weaker one client-side.

---

## H1 — Server-stamped, unique-per-hit identity

**Root cause:** two independent id derivations. The client's `screeningHitId`
picks one provider id (collides); the server's `_screening_evidence_item_key`
(server.py ~21971) already produces a **unique** identity — it joins *all*
provider ids (`case|alert|risk|match|profile`) and falls back to
`source_url|source_title|snippet`. Two articles under one profile differ in
alert/match id or source fields, so the server key never collides.

**Fix:** stop deriving ids on the client. When the server builds evidence items
(`_enrich_screening_queue_evidence` → `_screening_evidence_dedup`, server.py
~22447 — the single enrichment chokepoint that feeds BOTH the queue card and
the review workspace), stamp each surviving item with a **deterministic,
never-empty** id:

```
key = _screening_evidence_item_key(item)          # unique per item when non-empty
if not key:                                        # id-less AND source-less item
    key = "sha:" + hashlib.sha1(
        json.dumps(item, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]                             # stable content hash — mirrors dedup's own json fallback
item["hit_id"] = key
```

The frontend `screeningHitId(item)` becomes simply:

```
return String(item && item.hit_id ? item.hit_id : '');   // read server id; empty → not dispositionable, surfaced honestly
```

- **Why the hash fallback matters (verifier Finding 3):**
  `_screening_evidence_item_key` returns `""` for an item with no provider ids
  and no source_url/title/snippet. Two such items would both stamp `""`, the
  server hit-id *set* would collapse them to one (undercount), and a client
  `idx-` fallback would never match the server → the hit becomes
  un-dispositionable and finalize deadlocks. The content-hash fallback mirrors
  the `or json.dumps(item)` tiebreak `_screening_evidence_dedup` already uses
  (server.py ~21996), so every rendered card gets a distinct, stable id.
- The client **never** derives an id — no `idx-` fallback. If `item.hit_id` is
  ever empty (should be impossible post-stamp), the card renders read-only with
  an honest "hit cannot be identified for disposition" note rather than
  fabricating an id the server will reject.
- Uniqueness and client/server agreement are guaranteed because there is now
  exactly ONE derivation, on the server, and the client reads it.
- **Migration/orphan note:** existing `screening_hit_dispositions` rows are
  keyed by the *old* ids. They are not deleted. H3's server validation
  (below) recomputes the authoritative rollup against the real evidence hit-id
  set, so any orphaned old-id row simply stops matching a real hit and drops
  out of the count — the id-scheme change is self-healing, no data migration
  required. (Per-hit dispositions are the advisory layer, not the frozen
  subject decision, so a reset of unmatched rows is safe.)

---

## H3 — Server is the referee (validation + authoritative rollup + finalize gate)

Three additive server changes. None removes an existing code path; each adds a
fail-closed check, so behaviour is unchanged for any subject that has **no**
per-hit dispositions (backward compatible with every current test fixture).

### H3a — Enumerate a subject's real hits (reuse the uncapped context the frozen handler already builds)

**Do NOT** reuse the queue-loop builders (they are inline inside
`_build_screening_queue_payload`, scan all applications, and cap evidence at
200/app). Instead reuse `_screening_review_subject_context(db, app,
subject_type, subject_name)` (server.py ~22646) → `_attach_screening_review_
evidence_context` (server.py ~22132), which builds a **single subject's**
context with **uncapped**, subject-scoped `screening_evidence.items`. The frozen
`ScreeningReviewHandler` **already calls this** as `row_context` (server.py
~24658), so H3c gets the hit set with **zero extra DB cost** (verifier
Finding 4, resolves original open question 1).

Add `_screening_subject_hit_index(row_context)` returning:

```
{ item["hit_id"]: item for item in (row_context["screening_evidence"]["items"] or []) }
```

— the single source of truth for "is this hit real?" and "how many hits
exist?". Because the stamp (H1) runs in the shared enrichment chokepoint, the
same `hit_id`s appear here and on every rendered card.

**`context_error` behaviour (must be explicit):** when
`_screening_review_subject_context` returns `None`/error (server.py ~24659),
the server cannot compute a denominator. **Fail-closed**: the hit-disposition
POST and the finalize gate return `503 "screening evidence temporarily
unavailable — retry"` rather than accepting a write it cannot validate. This is
a transient-retry state, not a permanent block (contrast the cap deadlock,
which v2 removes), so it does not lock a subject out permanently.

### H3b — Validate + authoritative rollup in the POST handler

In `ScreeningHitDispositionHandler.post` (server.py ~24415):

- Reject any submitted `hit_id` not in the H3a hit index
  → `400 "unknown screening hit"` (closes the fabricated-id hole verified live
  with `b1,b2,b3`).
- Replace `_screening_hit_rollup` with a version that takes the real hit-id set
  and returns a denominator-aware, self-healing summary:

```
{ "total": <count of real stored hits>,          # UNCAPPED (from row_context)
  "match":   <real hits dispositioned 'match'>,
  "cleared": <real hits dispositioned 'cleared' (false positive)>,
  "open":    <real hits with no disposition or a non-terminal one>,
  "complete": open == 0,
  "verdict": "match" if match > 0 else ("clear" if complete else "in_progress") }
```

Dispositions whose hit_id is not in the real set are ignored (self-heals H1
orphans and stale re-screen ids).

- **The GET `/screening/hit-disposition` response must also return this rollup**
  (currently it returns only `{"dispositions": rows}`, server.py ~24410). The
  browser needs the uncapped `total` at first render to drive H2; without it
  the client has no true denominator (verifier Finding 2).

**Test impact (verifier Finding 1 — these tests CHANGE, they do not stay green):**
`TestScreeningHitDisposition` in `tests/test_api.py` (~8104-8237) currently
creates an evidence-less app and POSTs fabricated ids (`hit-A..E`, `hit-X`, `h`)
expecting 200. Under H3b those become 400. These fixtures **must be rewritten**
to seed real `monitoring_alert_evidence` for the subject and derive their
`hit_id`s from the stamped evidence. There is deliberately **no empty-set
carve-out** — skipping validation when the real set is empty would re-open the
fabrication hole on any not-yet-screened subject.

### H3c — Fail-closed finalize guard in `/api/screening/review` (FROZEN — needs sign-off)

`ScreeningReviewHandler` is the frozen finalize that writes `screening_reviews`
and drives the approval gates. Add ONE additive precondition, evaluated only
when the subject has ≥1 per-hit disposition recorded:

- a subject-level **cleared / false-positive** decision is rejected unless the
  H3b rollup is `complete` and `match == 0`;
- a subject-level decision that is **not** a true-match is rejected when
  `match > 0` (a confirmed hit must escalate to EDD, not clear).

Rejection is `409 "resolve every screening hit before recording the subject
decision"`. When the subject has **no** per-hit dispositions, the handler
behaves exactly as today — so every existing test and workflow is unchanged;
the guard only *adds* a safety rejection. This is the change that needs founder
sign-off because it strengthens the frozen finalize contract.

---

## H2 — Browser reads the server denominator, blocks on incomplete load

With H1/H3 in place the client stops owning the verdict:

- `screeningSubjectRollup` compares resolved-count against the **uncapped H3b
  rollup `total`** delivered by the GET — **NOT** `row.triage.total` and **NOT**
  `items.length`. Verifier Finding 2: `row.triage.total` is computed by
  `_screening_queue_row_triage` over the same 200-capped `items`, so it equals
  `items.length` above the cap and gives no protection. The authoritative total
  is the uncapped one from `_screening_review_subject_context`.
- If `loaded items < rollup.total`, `screeningSubjectFinalizeSection` does
  **not** render the finalize form. It shows an honest "N of M hits loaded —
  load all hits to finalise" state with a control that fetches the **uncapped**
  subject evidence.
- **The "load all hits" control must hit an uncapped path.** The current
  `ensureApplicationScreeningEvidenceRows` re-fetches
  `/screening/queue?limit=100&include_evidence=1` — the same 200-capped path
  (verifier Finding 2), so above the cap `loaded` can never reach `total` and
  finalize would deadlock. v2 requires an uncapped subject-evidence fetch: reuse
  the review-context evidence the drawer already loads uncapped, exposed via the
  subject GET (the hit-disposition GET already keys on the subject, so it is the
  natural carrier for both the uncapped items and the rollup).
- The rollup strip's status label is driven by the server verdict, so the UI
  can never say "every hit resolved" while the server knows of unresolved hits.

The server `409` from H3c is the backstop: even if the client is wrong, the
finalize write is refused — and because the denominator and "load all" are now
uncapped, a high-volume subject can always reach a finalizable state (no cap
deadlock).

---

## Operational notes (accepted behaviour — release-note items)

- **Re-screen mid-review.** If a subject sits in `pending_second_review`
  (four-eyes) and a re-screen changes/introduces hit ids, the second reviewer
  must resolve the current hits via the per-hit endpoint before the clearance
  completes. This is never a permanent deadlock (all hits are resolvable, and
  "load all" covers >cap), and it never falsely clears — it is the intended
  fail-closed/self-healing behaviour.
- **Legacy (pre-PR-B) per-hit dispositions.** A subject whose only per-hit rows
  use the old id scheme is treated as having open hits (the rollup ignores the
  orphans) and must be re-reviewed before it can clear. Fail-closed by design.
- **H3c scope.** The gate blocks the *clearance* path (which closes the approval
  blocker). `escalated` / `follow_up_required` subject decisions are allowed
  with a match present — `escalated` is the intended EDD route and both keep the
  subject open, so no blocker is ever wrongly cleared.

## What is deliberately NOT changed

- Risk scoring, memo generation, validation engine, supervisor engine — zero
  diff (frozen engines).
- The four-eyes / second-reviewer logic — untouched (a separate audit item).
- The subject-level `/api/screening/review` request/response *shape* — the only
  change is an added fail-closed precondition, not a contract change for the
  clean path.
- H4 (fabricated "near-identical" line), H5 (sanctions tile), H6 (rationale
  audit) — those are PR-C, not here.

---

## Guard tests (must ship with the change)

Backend:
- `hit_id` is stamped on every deduped evidence item and is unique across a
  subject's items (regression for H1).
- POST with a fabricated hit_id → 400; POST with real ids → rollup `total`
  equals the real stored hit count, not the count of disposition rows.
- Rollup ignores orphaned/stale disposition rows (self-heal).
- `/api/screening/review` cleared decision with an open hit → 409; with all
  hits cleared → accepted; with a match hit → 409 unless the decision is a
  true-match. Subject with no per-hit dispositions → unchanged (accepted as
  today).
- The uncapped enumeration returns the true total when evidence exceeds the
  display cap (regression for H2's denominator).

Frontend (node-extracted, mirrors existing `test_srp3_*` style):
- `screeningHitId` reads `item.hit_id`; two items sharing a `provider_profile_id`
  but differing in source get distinct ids (H1).
- `screeningSubjectRollup` uses the server total; finalize is suppressed when
  loaded < total (H2).

Frozen guard suites that must stay green: `test_srp3_phase_b_review_ui_static`,
`test_screening_queue*`, `test_inline_screening_runtime`,
`test_application_review_audit_fixes_static`, the approval/gate suite, and the
per-hit endpoint tests in `test_api.py`.

---

## Resolved / remaining questions

1. **RESOLVED — cost of enumeration.** Reusing `row_context` from
   `_screening_review_subject_context` (already built by the frozen finalize
   handler) makes the hit set zero-extra-cost. No caching layer needed.
2. **RESOLVED — H3c seam.** The additive fail-closed precondition in
   `ScreeningReviewHandler`, gated on "subject has ≥1 recorded per-hit
   disposition", is backward-compatible: the `/screening/review` test corpus
   (test_api.py ~5112-6970) never pre-writes per-hit dispositions, so those
   tests are unchanged. The 409 is a sound backstop seam.
3. **Known, accepted — over-inclusion for id-less shared hits (verifier
   Finding 5).** A provider candidate with no `matched_subject_name` but a
   shared case/alert/profile id links to every subject row carrying that id, so
   it counts under multiple subjects' denominators. This is **fail-closed**
   (finalize gets harder, never falsely-clear) and — critically — is *identical*
   between the rendered card and the H3a denominator (both call the same
   enrichment), so the denominator still matches what the officer sees. Accept
   and document; do not special-case.
4. **FOR FOUNDER — escalated / follow-up hits** hold the subject `open` (never
   finalize) and the per-hit path has no subject-level escalate route. Confirm
   that is the intended product rule, or PR-B should add a subject-level
   escalate/RFI action (small scope addition).
