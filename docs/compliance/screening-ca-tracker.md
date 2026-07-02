# Screening / ComplyAdvantage — Work Tracker

_Module: RegMind screening + Agent 3 + CA Mesh config. Last updated: 2026-07-02._

**Status:** ☐ Not started · ◐ In progress · ☑ Done · ⛔ Blocked
**Priority:** P1 (before prod) · P2 (soon) · P3 (nice-to-have)

---

## A. CA Sandbox — validate & finish config

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| CA-1 | Single-risk-type re-test — **PASSED, model validated** | ☑ | P1 | — | Both subtests pass with clean fixtures: **A** Mick Davis (PEP-only) → 75 → High; **B** DGUP Granitny (Sanctions-only) → 100 → Prohibited. The earlier Boris 450 was a poisoned fixture (his CA record carries 6 risk types), not a misconfig. Status is set at **entity level** (not per-risk-type). Models correctly calibrated. |
| CA-2 | Write **TP-marking SOP** — reframed to **entity-level** | ☑ | P1 | — | Drafted: `docs/compliance/sop-screening-true-positive-marking.md`. Officers confirm whether the matched **record (entity)** is genuinely the customer; risk categories come from CA, not picked per-type. Pending MLRO sign-off. |
| CA-3 | ~~Rescale scores if PEP over-grades~~ — **DROPPED** | ☑ | — | Confirmed unnecessary. CA-1 subtest A now proves a PEP-only entity scores 75 → High correctly. No rescale needed. |
| CA-4 | ~~Delete the two `webhook.site` webhooks~~ — **MITIGATED (inactive; UI delete unavailable)** | ☑ | P1 | — | Leak closed: inactive webhooks receive nothing. CA UI has no delete control; deletion would need `DELETE /v2/webhooks/{id}` with an Admin key. Parked inactive — **must never be copied to production.** |
| CA-5 | Confirm CA staging config wired (AWS) — **VERIFIED** | ☑ | P1 | — | Codex audit (task-def `regmind-staging:728`): webhook secret is a dedicated HMAC secret wired via ECS secrets ref (signature verification active — not fail-open). All 9 required CA vars present & correct, incl. `COMPLYADVANTAGE_SCREENING_CONFIG_ID` (plain env, matches `019e0308-…a6fe` = regmind-default-screening-v1). Workspace mode = **sandbox** (expected). |
| CA-6 | Add a **second CA Admin** user | ☐ | P2 | — | From audit: single human admin (Aisha) = key-person risk on the account gating all screening. |
| CA-7 | Verify API-user role needs **"Rescreen on demand"** | ☐ | P2 | — | From audit: not granted. If RegMind triggers programmatic rescreen (periodic review / monitoring refresh) it will 403. Confirm or grant. |
| CA-8 | Review collection source coverage (`regmind-default-sources-v1` uses **1/4**) | ☐ | P2 | — | Confirm enabled sources actually span the sanctions/PEP/adverse breadth claimed to clients. |
| CA-9 | Consider raising match threshold **70 → 75** after observing real alert volume | ☐ | P3 | — | Empirical tune; document rationale (FSC expects justified threshold). |
| CA-10 | Consider a **custom allowlist** to suppress known false positives | ☐ | P3 | — | From audit: no custom lists configured. Optional noise reducer. |
| CA-11 | Confirmatory PEP-grading check on production data | ☐ | P3 | prod CA live | Downgraded — sandbox already validated it (Mick Davis, PEP-only → 75 → High). Optional confirmation on real prod data. |
| CA-12 | Remove dead SM keys `COMPLYADVANTAGE_DEFAULT_WORKFLOW_ID` + `COMPLYADVANTAGE_WEBHOOK_SITE_TOKEN` | ☐ | P3 | — | Confirmed unused by any backend code (Codex + grep). Harmless but tidy; the webhook_site token relates to the deprecated leak endpoint. Optional cleanup. |

---

## B. CA Production — replication (sandbox validated ✅; webhooks gated on prod DNS)

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| PROD-1 | Replicate both risk models + screening config to production | ☐ | P1 | — | **Unblocked** — sandbox grading validated (CA-1 passed). Ready to replicate. Prod env must flip workspace flags off sandbox: `COMPLYADVANTAGE_WORKSPACE_MODE`, `COMPLYADVANTAGE_SCREENING_CONFIG_ID`/`_LABEL` → the **production** screening-config id, plus prod CA creds. |
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
- ☑ CA-1 single-risk-type re-test **passed** — both subtests: Mick Davis (PEP-only) → 75 → High; DGUP Granitny (Sanctions-only) → 100 → Prohibited. Models validated; Boris 450 was a poisoned fixture.
- ☑ CA-2 entity-level TP-marking SOP drafted (`sop-screening-true-positive-marking.md`, pending MLRO sign-off)
- ☑ CA-5 staging CA config verified via AWS audit (webhook secret wired + signature verification active; all 9 required vars correct; SCREENING_CONFIG_ID matches; workspace=sandbox)

---

## Recommended next order

1. **PROD-1** replication (unblocked — sandbox validated); **PROD-2/3** webhooks once prod DNS is live. MLRO sign-off on the SOP (CA-2).
2. **CA-4, CA-5** (webhook deletion + secret) — quick P1 hygiene, independent.
3. **BE-2** (risk-level plumbing) → **UI-2** → **UI-1** (de-dup PR).
4. **RPT-1/RPT-2** (screening-report PDF) — independent, anytime.
5. **CA-11 / BE-3 / BE-4 / RPT-3** — confirm on prod CA data.

_Strategic note: treat CA's risk level as a provider-side **triage** signal, not RegMind's authoritative risk grade — `rule_engine.py` owns LOW/MEDIUM/HIGH/VERY_HIGH. Don't over-fit CA's SUM scoring._
