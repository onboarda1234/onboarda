# Screening / ComplyAdvantage — Work Tracker

_Module: RegMind screening + Agent 3 + CA Mesh config. Last updated: 2026-07-02._

**Status:** ☐ Not started · ◐ In progress · ☑ Done · ⛔ Blocked
**Priority:** P1 (before prod) · P2 (soon) · P3 (nice-to-have)

---

## A. CA Sandbox — validate & finish config

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| CA-1 | Single-risk-type re-test — **RESOLVED (answered)** | ☑ | P1 | — | Finding: CA sets status at **entity level**, not per-risk-type. Subtest B proved single-type entities score correctly (Sanctions-only → 100 → Prohibited). Subtest A unexecutable — sandbox has no PEP-only entity (fake "Boris" carries 6 types → 450). Model is **not** mis-scoring PEPs; a real PEP-only entity would score 75 → High. Over-grade = sandbox-data artifact. |
| CA-2 | Write **TP-marking SOP** — reframed to **entity-level** | ☐ | P1 | — | Officers confirm whether the matched **record (entity)** is genuinely the customer; risk categories come from CA, not picked per-type. (Original "confirm only matching risk types" is impossible — UI has no per-type control.) |
| CA-3 | ~~Rescale scores if PEP over-grades~~ — **LIKELY DROPPED** | ☑ | P3 | — | Not needed: model scores correctly per B; the 450 is fake sandbox data, not a misconfig. SUM escalation-on-combination is correct, fail-safe AML behaviour. Keep only as contingency if PROD-PEP validation (CA-11) shows real over-grading. |
| CA-4 | Delete the two `webhook.site` webhooks via CA REST API | ☐ | P1 | — | Currently only **Inactive**, not deleted. `DELETE /v2/webhooks/{id}`. API-user role lacks Developers perms — may need Admin key. |
| CA-5 | Confirm `COMPLYADVANTAGE_WEBHOOK_SECRET` set in staging (AWS Secrets Manager) | ☐ | P1 | — | Our handler is fail-**closed** in prod, fail-**open** in sandbox. Unset staging secret = unsigned webhooks accepted silently. |
| CA-6 | Add a **second CA Admin** user | ☐ | P2 | — | From audit: single human admin (Aisha) = key-person risk on the account gating all screening. |
| CA-7 | Verify API-user role needs **"Rescreen on demand"** | ☐ | P2 | — | From audit: not granted. If RegMind triggers programmatic rescreen (periodic review / monitoring refresh) it will 403. Confirm or grant. |
| CA-8 | Review collection source coverage (`regmind-default-sources-v1` uses **1/4**) | ☐ | P2 | — | Confirm enabled sources actually span the sanctions/PEP/adverse breadth claimed to clients. |
| CA-9 | Consider raising match threshold **70 → 75** after observing real alert volume | ☐ | P3 | — | Empirical tune; document rationale (FSC expects justified threshold). |
| CA-10 | Consider a **custom allowlist** to suppress known false positives | ☐ | P3 | — | From audit: no custom lists configured. Optional noise reducer. |
| CA-11 | **Validate PEP grading on production data** (new) | ☐ | P2 | prod CA live | The only test that can settle the PEP question — sandbox can't (no PEP-only entity). Screen one real, known PEP; confirm it grades **High** (or High + adverse), not Prohibited. If it over-grades, revisit CA-3. |

---

## B. CA Production — replication (⛔ blocked until Section A validated)

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| PROD-1 | Replicate both risk models + screening config to production | ⛔ | P1 | CA-1/CA-2 (or CA-3) | Do not replicate until sandbox grading is validated. |
| PROD-2 | Create prod webhooks → `app.regmind.co/api/webhooks/complyadvantage` | ⛔ | P1 | PROD DNS live | `app.regmind.co` is planned, DNS not yet provisioned. Set signing secret at creation. |
| PROD-3 | Set `COMPLYADVANTAGE_WEBHOOK_SECRET` in prod secrets (match webhook secret) | ⛔ | P1 | PROD-2 | Prod handler rejects all webhooks if secret missing/mismatched. |
| PROD-4 | **Governance note:** never score Country/Channel/Basic-info/Product without re-reviewing overall thresholds | ☑ | P1 | — | Recorded (replaces unavailable zero-weighting; CA enforces min weight 1). |

---

## C. Backend code — data quality

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| BE-1 | ~~Surface CA's match_score into the panel~~ → **REFRAMED** (see BE-2) | ☑ | — | **Trace disproved this.** CA Mesh returns **no numeric match score** (model has only `surfaced_by_pass` strict/relaxed/both). Nothing to "un-drop". |
| BE-2 | Replace panel "score" with **CA risk level** (+ optional strict/relaxed confidence chip) | ☐ | P2 | CA-1 | Honest to what Mesh provides. Small UI change, no backend. Uses risk model from Section A + free `surfaced_by_pass` signal. |
| BE-3 | Root-fix `matched_name`-is-UUID | ☐ | P2 | — | Currently only cosmetic "Unnamed provider match" fallback (shipped in #640). Real cause: `_profile_name(profile) or profile_identifier` (normalizer.py:385) falls back to UUID when CA profile has no name records (sandbox sparse data). Likely resolves on prod data — validate first. |
| BE-4 | Adverse-media source URL — **validate on prod data (no code)** | ☐ | P3 | prod CA data | Pipeline fully intact (`_canonicalize_article` → `canonical_url` → UI "source" link). Missing URLs = CA payload omits `article.url` (sandbox). Displays automatically when CA supplies it. |

---

## D. Back-office UI — Entity Screening de-dup (mockup in progress)

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| UI-1 | Scope the de-duplication PR (one decision banner, one hit card/match, collapse repeats) | ☐ | P2 | UI-2, BE-2 | Trace: body stacks 9 panels (arie-backoffice.html 15443–15486); CA match string prints 3×, name ~5×. Frontend-only, medium PR; static-contract test will need updates. |
| UI-2 | Extend mockup with **sanctions + PEP evidence variants** | ◐ | P2 | — | Only adverse-media drawn so far. Evidence drawer must switch fields by category (sanctions: list/authority/program/ref; PEP: position/country/class). |
| UI-3 | Design decision: **two-tier disclosure** — substantive evidence one-click; technical UUID refs buried | ☑ | P2 | — | Locked in mockup: per-hit "View evidence" (article/source/snippet/link) separate from technical-refs disclosure. |
| UI-4 | Sequence: **BE-2 (risk-level plumbing) before UI-1** | ☑ | — | — | So hit cards render a real risk level, not an empty score. |

---

## E. Screening report generation (new)

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| RPT-1 | Backend endpoint `GET /api/applications/:id/screening-report.pdf` | ☐ | P2 | — | Reuses `pdf_generator.py` "Section 5: Screening Results". Renders stored `screening_report`: subjects, hits, categories, evidence (+URLs where present), refs, risk level, disposition history. |
| RPT-2 | Frontend **"Generate screening report"** button on RegMind screening page | ☐ | P2 | RPT-1 | Calls endpoint → downloads PDF. |
| RPT-3 | Validate report content quality on prod data | ☐ | P3 | prod CA data, BE-3 | Sandbox report = UUIDs + missing URLs; real names/URLs only once CA prod flows. |

---

## Done (reference)

- ☑ Agent 3 screening-interpretation panel cleanup — **PR #640 merged + deployed to staging** (2026-07-02)
- ☑ CA match threshold 50 → 70 (screening config v2)
- ☑ Person risk model `regmind-default-risk-model-v1` created (v5, Active)
- ☑ Company risk model `regmind-default-risk-model-company-v1` created (v1, Active)
- ☑ `webhook.site` webhooks set Inactive (deletion still pending — CA-4)
- ☑ Trace: confirmed CA=screening / Sumsub=IDV responsibility split (screening_config.py)
- ☑ Trace: CA Mesh has no numeric match score; adverse-media URL pipeline intact
- ☑ CA-1 single-risk-type re-test resolved: entity-level status only; single-type entities score correctly; PEP over-grade is a sandbox-data artifact

---

## Recommended next order

1. **CA-2** (entity-level TP SOP) → then **PROD-1..4** replication (models are as-validated as sandbox allows).
2. **CA-4, CA-5** (webhook deletion + secret) — quick P1 hygiene, independent.
3. **BE-2** (risk-level plumbing) → **UI-2** → **UI-1** (de-dup PR).
4. **RPT-1/RPT-2** (screening-report PDF) — independent, anytime.
5. **CA-11 / BE-3 / BE-4 / RPT-3** — validate on prod CA data (incl. the real-PEP grading check).

_Strategic note: treat CA's risk level as a provider-side **triage** signal, not RegMind's authoritative risk grade — `rule_engine.py` owns LOW/MEDIUM/HIGH/VERY_HIGH. Don't over-fit CA's SUM scoring._
