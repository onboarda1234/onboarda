# Screening / ComplyAdvantage — Work Tracker

_Module: RegMind screening + Agent 3 + CA Mesh config. Last updated: 2026-07-03._

**Status:** ☐ Not started · ◐ In progress · ☑ Done · ⛔ Blocked
**Priority:** P1 (before prod) · P2 (soon) · P3 (nice-to-have)

---

## A. CA Sandbox — validate & finish config

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| CA-1 | Single-risk-type re-test — **PASSED, model validated** | ☑ | P1 | — | Both subtests pass with clean fixtures: **A** Mick Davis (PEP-only) → 75 → High; **B** DGUP Granitny (Sanctions-only) → 100 → Prohibited. The earlier Boris 450 was a poisoned fixture (his CA record carries 6 risk types), not a misconfig. Status is set at **entity level** (not per-risk-type). Models correctly calibrated. |
| CA-2 | Write **TP-marking SOP** — reframed to **entity-level** | ☑ | P1 | — | `docs/compliance/sop-screening-true-positive-marking.md`. **MLRO signed off (2026-07-02).** Officers confirm whether the matched **record (entity)** is genuinely the customer; risk categories come from CA, not picked per-type. |
| CA-3 | ~~Rescale scores if PEP over-grades~~ — **DROPPED** | ☑ | — | — | Confirmed unnecessary. CA-1 subtest A now proves a PEP-only entity scores 75 → High correctly. No rescale needed. |
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

## B. CA Production — replication (⛔ blocked: no production environment exists yet)

> **AWS discovery (Codex, read-only, 2026-07-02):** no `regmind/production` secret, no
> `regmind-production` ECS cluster/service — only `regmind-staging` exists. Production is a
> **provisioning project**, not a config task. Config is validated & ready; nothing can be
> wired until the prod environment is stood up.

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| PROD-0 | **Provision the production environment** (ECS cluster + RDS + Secrets Manager + `app.regmind.co` DNS) | ⛔ | P1 | infra/DevOps | Gating prerequisite for everything below. Owned by infra, not this workstream. Confirmed absent by AWS discovery. |
| PROD-0b | Confirm/create a **production CA workspace + API credential** (live equiv of `ca-staging-api`) | ☐ | P1 | — | CA-console prerequisite; the prod screening config + risk models are created here. Can be prepped independently of AWS. |
| PROD-1 | Replicate both risk models + screening config to the **production CA workspace** | ☐ | P1 | PROD-0b | Config validated & ready (CA-1 passed). Chrome prompt drafted. Produces the prod screening-config id + model ids for AWS wiring. |
| PROD-2 | Wire prod CA config into prod backend (secrets + env; `WORKSPACE_MODE=production`, prod `SCREENING_CONFIG_ID`, prod CA creds) | ⛔ | P1 | PROD-0, PROD-1 | Codex plan-table ready; apply once prod infra + values exist. |
| PROD-3 | Create prod webhooks → `app.regmind.co/api/webhooks/complyadvantage` + set matching `COMPLYADVANTAGE_WEBHOOK_SECRET` | ⛔ | P1 | PROD-0 (DNS live) | Do the CA-webhook + AWS-secret pair together so the signing secret matches. Deferred until DNS live. |
| PROD-4 | **Governance note:** never score Country/Channel/Basic-info/Product without re-reviewing overall thresholds | ☑ | P1 | — | Recorded (replaces unavailable zero-weighting; CA enforces min weight 1). |

---

## C. Backend code — data quality

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| BE-1 | Surface CA's `match_score` into the hit rows | ☑ | P2 | — | **Code correct & closed. `match_score` NOT populated by Mesh sandbox — do NOT rely on it as a primary signal.** Staging validation (2026-07-03, fresh scored fixture `DGUP Granitny`, clean run, 2 matches): both hits `match_score: null`. **Root cause:** the stored CA match had `profile: null` — the risk payload from `/v2/alerts/{id}/risks` + `/v2/entity-screening/risks/{id}` carries no embedded `profile`, so there is no `profile.match_details.match_score` to read (`orchestrator.py:533` only sets profile when `raw.get("profile")` is present). `_match_score_percentage` reads the right path (`output.py:186`); the field is simply absent. `surfaced_by_pass` (`relaxed`/`strict`) **did** persist — that is the real Mesh confidence signal (risk-profile based, not fuzzy name-match scoring). Code + tests stay (harmless when CA ever supplies a score). **Same `profile: null` root cause as BE-3.** |
| BE-2 | Panel/PDF confidence = **strict/relaxed pass** (match % only if present; risk level deferred) | ☐ | P2 | — | **Reframed (Option 1, 2026-07-03):** Mesh returns strict/relaxed pass as the reliable per-hit confidence, NOT a numeric match %. Panel leads with the **strict/relaxed badge + category + evidence**; render a numeric match % **only when `match_score` is non-null** (defensive — null on all Mesh sandbox data observed). No dependency on BE-1 populating. CA **risk level** (Medium/High/Prohibited) still needs CA *case* data — separate; deferred. |
| BE-3 | Root-fix `matched_name`-is-UUID | ☐ | P3 | prod CA data | **Same `profile: null` root cause as BE-1** (confirmed 2026-07-03). `_profile_name(profile) or profile_identifier` falls back to the UUID because the CA risk payload embeds no `profile` (no name records). Deprioritized to prod: real names appear only when CA embeds a profile; may additionally need a separate entity-profile fetch by `profile_identifier`. Validate on prod before any code fix. |
| BE-4 | Adverse-media source URL — **validate on prod data (no code)** | ☐ | P3 | prod CA data | Pipeline fully intact (`_canonicalize_article` → `canonical_url` → UI "source" link). Missing URLs = CA payload omits `article.url` (sandbox). Displays automatically when CA supplies it. |

---

## D. Back-office UI — Entity Screening de-dup (mockup in progress)

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| UI-1 | Scope the de-duplication PR (one decision banner, one hit card/match, collapse repeats) | ☐ | P2 | UI-2, BE-2 | Trace: body stacks 9 panels (arie-backoffice.html 15443–15486); CA match string prints 3×, name ~5×. Frontend-only, medium PR; static-contract test will need updates. Hit card leads with **strict/relaxed pass + category + evidence** (BE-2 reframe), not a match %. |
| UI-2 | Extend mockup with **sanctions + PEP evidence variants** | ◐ | P2 | — | Only adverse-media drawn so far. Evidence drawer must switch fields by category (sanctions: list/authority/program/ref; PEP: position/country/class). |
| UI-3 | Design decision: **two-tier disclosure** — substantive evidence one-click; technical UUID refs buried | ☑ | P2 | — | Locked in mockup: per-hit "View evidence" (article/source/snippet/link) separate from technical-refs disclosure. |
| UI-4 | ~~Sequence: BE-1 (match_score) before UI-1~~ — **SUPERSEDED** | ☑ | — | — | Original intent (render a real match %) is retired: BE-1 validation proved Mesh returns no `match_score` (`profile: null`). Hit cards render **strict/relaxed pass + category + evidence** instead (BE-2 reframe). No BE-1 gate on UI-1 anymore. |
| UI-5 | **Kill repetitive counts/status** (staging feedback 2026-07-02) | ☐ | P2 | — | Same facts restated 4–5×: provider-hit counts appear in the chip row **and** Plain-English summary **and** Key concerns; "Officer review required" appears in the recommendation badge, advisory line, Recommended disposition, summary **and** key concerns; the matched UUID repeats in the hit row **and** Evidence-used (2×). State each fact **once**: counts in one chip row, disposition once, matched entity once. |
| UI-6 | **Evaluate/remove the "Draft audit note" section** (staging feedback) | ☐ | P2 | — | Officer reports it unused. Check whether the paste-ready block / Copy / "Add to audit note" feeds anything real (does it write to the audit trail or a note field, or is it copy-only?). If copy-only with no workflow use → **remove**; if it has a genuine use, keep but justify. Decide before UI-1 lands. |
| UI-7 | **Compact "no hits / Clear" variant** (staging feedback 2026-07-02) | ☐ | P2 | — | With 0 provider hits the panel shows ~7 near-empty sections all restating "no hits found" (summary, Key concerns, FP box, adverse-media box, Recommended disposition, empty hit table, audit note). Render a **compact clear state** instead: one line — "No provider hits in stored screening results · Advisory: Clear · officer decision still required" — **keep the one real caveat** ("no hits ≠ no compliance risk", once), omit/collapse the empty hit table + FP boxes + audit note + evidence list behind a single toggle. Full layout only when hits exist. |

---

## E. Screening report generation (new)

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| RPT-1 | Backend endpoint `GET /api/applications/:id/screening/pdf` | ☑ | P2 | — | **Done** (`6adcc79`). `pdf_generator.build_screening_report_html` / `generate_screening_report_pdf` + `ScreeningReportPDFDownloadHandler`. Renders stored screening_report: subjects, matches, categories, list/source, strict/relaxed confidence, adverse-media evidence links; UUID→"Unnamed provider match". Read-only + audit-logged. 5 unit tests; real PDF renders (weasyprint). |
| RPT-2 | Frontend **"Screening report (PDF)"** button | ◐ | P2 | RPT-1, UI-1 | Handler `downloadScreeningReportPDF()` + endpoint shipped (`6adcc79`, on staging via #644). Button **hidden for now** (`4a6c8db`) — original entity-card-only placement was too buried; endpoint/helper stay dormant. **Re-surface with proper placement in PR-2** (Screening Review header, always visible). Hide takes effect on staging at next merge (option 1 — no dedicated deploy). |
| RPT-3 | Validate report content quality on prod data | ☐ | P3 | prod CA data, BE-3 | Sandbox report = UUIDs + missing URLs; real names/URLs only once CA prod flows. |

---

## Done (reference)

- ☑ Agent 3 screening-interpretation panel cleanup — **PR #640 merged + deployed to staging** (2026-07-02)
- ☑ CA match threshold 50 → 70 (screening config v2)
- ☑ Person risk model `regmind-default-risk-model-v1` created (v5, Active)
- ☑ Company risk model `regmind-default-risk-model-company-v1` created (v1, Active)
- ☑ `webhook.site` webhooks set Inactive (deletion still pending — CA-4)
- ☑ Trace: confirmed CA=screening / Sumsub=IDV responsibility split (screening_config.py)
- ☑ Trace: adverse-media URL pipeline intact (`_canonicalize_article` → `canonical_url` → UI link)
- ☑ Trace (final, 2026-07-03): `match_score` is **modeled** (`CAMatchDetails.match_score` on `CAProfile.match_details`) and the normalizer reads the correct path, but **Mesh sandbox does not populate it** — the CA risk payload embeds no `profile`, so `match.profile` is null (kills both BE-1 score and BE-3 name). The real Mesh confidence signal is **strict/relaxed pass** (`surfaced_by_pass`), not a numeric % (Mesh is risk-profile based, not fuzzy name-search). Supersedes the earlier "Mesh does return match_score" note. → BE-1 closed, BE-2 reframed to strict/relaxed.
- ☑ **PR #647** — CA workflow `ERRORED` status handled (no more 500; degraded `pending_provider`/re-screen report persisted). Merged (`e8eeffb`) + deployed to staging + verified 2026-07-03 (fresh screen returned 200, degraded report, no ValidationError). Also revealed the **duplicate-external-id → ERRORED** trigger (re-screening an existing customer errors — the fix covers it).
- ☑ RPT-1/RPT-2 screening-report PDF — **PR #644 merged + deploying to staging** (2026-07-02)
- ☑ CA-1 single-risk-type re-test **passed** — both subtests: Mick Davis (PEP-only) → 75 → High; DGUP Granitny (Sanctions-only) → 100 → Prohibited. Models validated; Boris 450 was a poisoned fixture.
- ☑ CA-2 entity-level TP-marking SOP drafted (`sop-screening-true-positive-marking.md`, pending MLRO sign-off)
- ☑ CA-5 staging CA config verified via AWS audit (webhook secret wired + signature verification active; all 9 required vars correct; SCREENING_CONFIG_ID matches; workspace=sandbox)

---

## Recommended next order

**3-PR plan (locked 2026-07-03; low-risk, no combining):** PR-2 = Agent 3 panel redesign (UI-1/2/5/6/7 + BE-2 render) · BE-3 = standalone `matched_name` fix (prod-gated) · RPT-5 = CA Screening Certificate (pending mockup sign-off).

1. **PR-2** (the bulk): **UI-2** (sanctions/PEP evidence variants) → **UI-1 + UI-5 + UI-6 + UI-7 + BE-2** as one de-dup PR. Hit card leads with **strict/relaxed pass + category + evidence**; match % only if present. No BE-1 gate (retired — Mesh returns no score).
2. **RPT-5** CA Screening Certificate — independent; start once the illustrative mockup is signed off.
3. **PROD-0/0b** provision prod env + CA workspace → **PROD-1/2/3** replication (config validated, gated on infra + prod DNS).
4. **CA-6/CA-7/CA-8** (2nd admin, rescreen perm, source coverage) — CA-console hygiene.
5. **CA-11 / BE-3 / BE-4 / RPT-3** — confirm on prod CA data (BE-3 real names + BE-4 URLs both need CA to embed a profile/URLs).

_Strategic note: treat CA's risk level as a provider-side **triage** signal, not RegMind's authoritative risk grade — `rule_engine.py` owns LOW/MEDIUM/HIGH/VERY_HIGH. Don't over-fit CA's SUM scoring._
