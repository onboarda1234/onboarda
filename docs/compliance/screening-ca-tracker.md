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
| BE-1 | Surface CA's `match_score` into the hit rows | ◐ | P2 | BE-5 | **REOPENED (2026-07-03) — it was a wrong-JSON-path bug, NOT a Mesh limitation.** CA support + raw-payload inspection confirmed `match_score` **IS** returned, at `risks[].detail.profile.match_score` — but our code reads the wrong location (see BE-5). **Scale caveat:** observed `0.7` AND `1.7` for two `exact_match` risks, so it is **not** 0–1 or a %; it's a provider-internal weight. **Action: capture & store via BE-5, but do NOT display as a % until CA clarifies the scale.** strict/relaxed stays the UI confidence signal. |
| BE-2 | Panel/PDF confidence = **strict/relaxed pass** (match % only if present; risk level deferred) | ☑ | P2 | — | **Done — PR #655** (backend surfaced `surfaced_by_pass` into `hit_rows` + `agent3ProviderEvidenceCellHtml` renders Strict/Relaxed/Strict+relaxed; numeric % only when non-null). CA **risk level** (Medium/High/Prohibited) still needs CA *case* data — separate; deferred. |
| BE-3 | Root-fix `matched_name`-is-UUID | ◐ | P2 | BE-5 | **REOPENED — code bug, un-gated from prod (2026-07-03).** Real name **IS** returned at `risks[].detail.profile.matching_name` (+ `company.names[]`) but dropped because we read the wrong profile path (BE-5). Fix = read `matching_name`; stop showing the UUID when a name exists. Confirmed on sandbox raw data. |
| BE-4 | Adverse-media source URL + category — **code fix (was mislabelled "prod-only")** | ◐ | P2 | BE-5 | **REOPENED — code bug, un-gated from prod (2026-07-03).** URL **IS** returned at `risks[].detail.profile.risk_indicators.media[].url` (+ title/snippet/publishing_date), and category at `risk_indicators.aml_types[]` (e.g. `sanction`, `adverse-media-v2-regulatory`) — both dropped because the profile path is wrong (BE-5). Fix = read them via BE-5 + map `aml_types` → stored categories (feeds #658 counts with real categories). |
| BE-5 | **Fix CA alert-risk profile JSON paths** (umbrella for BE-1/3/4) | ☐ | **P1** | — | **Root fix, confirmed on raw sandbox payloads (2026-07-03).** `_normalise_risk_as_alert` (`orchestrator.py:533`) reads top-level `raw["profile"]` (absent); live data is at `raw["detail"]["profile"]`. The `CAProfile` model shape (`match_details`, `risk_types`, list `risk_indicators`) doesn't match the live alert-risk profile (`match_score`, `match_types`, `matching_name`, `risk_indicators` **object** with `aml_types`+`media`). Fix at the normalize/model boundary: lift `detail.profile`, map `matching_name`→name, `match_score`→captured (not %-displayed), `aml_types`→categories, `media[]`→evidence. Reopens BE-1/3/4. Add tests for score `0.7` **and** `1.7`. |

---

## D. Back-office UI — Entity Screening de-dup (mockup in progress)

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| UI-1 | Scope the de-duplication PR (one decision banner, one hit card/match, collapse repeats) | ☑ | P2 | UI-2, BE-2 | **Done — PR #655** (merged + deployed + verified 2026-07-03). Recommendation/counts/advisory each render once (de-dup locked with `count==1` render tests); one collapsed audit trace. Hit card leads with strict/relaxed pass + category + evidence, no match %. |
| UI-2 | Extend mockup with **sanctions + PEP evidence variants** | ☑ | P2 | — | **Done — PR #655.** Per-hit "Evidence details" drawer renders category-specific fields (adverse-media title/source/snippet/URL with safe fallback; sanctions + PEP variants); UUID matched entity stays "Unnamed provider match" with the raw UUID labelled as a provider reference. |
| UI-3 | Design decision: **two-tier disclosure** — substantive evidence one-click; technical UUID refs buried | ☑ | P2 | — | Locked in mockup: per-hit "View evidence" (article/source/snippet/link) separate from technical-refs disclosure. |
| UI-4 | ~~Sequence: BE-1 (match_score) before UI-1~~ — **SUPERSEDED** | ☑ | — | — | Original intent (render a real match %) is retired: BE-1 validation proved Mesh returns no `match_score` (`profile: null`). Hit cards render **strict/relaxed pass + category + evidence** instead (BE-2 reframe). No BE-1 gate on UI-1 anymore. |
| UI-5 | **Kill repetitive counts/status** (staging feedback 2026-07-02) | ☑ | P2 | — | **Done — PR #655.** Recommendation, provider counts, and the advisory sentence each render once (locked by `count==1` render tests); UUID printed once. Advisory sentence also removed from the backend summary (fixed at source). |
| UI-6 | **Evaluate/remove the "Draft audit note" section** (staging feedback) | ☑ | P2 | — | **Done — PR #655: removed.** Confirmed copy-only (no audit-trail/API write); officers reported it unused → removed from the UI. Backend `draft_audit_note` field retained but no longer rendered. |
| UI-7 | **Compact "no hits / Clear" variant** (staging feedback 2026-07-02) | ☑ | P2 | — | **Done — PR #655.** 0-hit panel renders a one-line state + single caveat, with full detail behind a "Show full detail" toggle. Soft-green **only** when the provider result is terminal AND no reportable hits (degraded/errored → amber, not green — backend emits `screening_result_terminal`). |

---

## E. Screening report generation (new)

| ID | Task | Status | Pri | Depends on | Notes |
|----|------|--------|-----|-----------|-------|
| RPT-1 | Backend endpoint `GET /api/applications/:id/screening/pdf` | ☑ | P2 | — | **Done** (`6adcc79`). `pdf_generator.build_screening_report_html` / `generate_screening_report_pdf` + `ScreeningReportPDFDownloadHandler`. Renders stored screening_report: subjects, matches, categories, list/source, strict/relaxed confidence, adverse-media evidence links; UUID→"Unnamed provider match". Read-only + audit-logged. 5 unit tests; real PDF renders (weasyprint). |
| RPT-2 | ~~Frontend **"Screening report (PDF)"** button~~ — **SUPERSEDED by RPT-5** | ☑ | P2 | — | Endpoint/handler shipped but button stays **hidden/dormant**. Officer decided the UI already shows the needed info and regulator evidence should be **CA-native** (RPT-5 CA Screening Certificate), not a RegMind PDF. Not re-surfaced in PR-2. Endpoint left dormant (no removal). |
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
- ⚠️ Trace (2026-07-03, SUPERSEDED same day): earlier concluded Mesh "doesn't populate" score/name/URL because `match.profile` was null. **That was a symptom, not the cause** — see the CA-confirmed finding below.
- ☑ **Trace (FINAL, CA-confirmed 2026-07-03): wrong JSON paths, not a Mesh limitation.** CA support + raw-payload inspection (2 sandbox specimens) proved the data **is** returned: profile at `risks[].detail.profile` (we read top-level `raw["profile"]` → null), name at `.matching_name`, score at `.match_score` (**observed 0.7 and 1.7 — not 0–1, not a %; provider weight**), category at `.risk_indicators.aml_types[]`, media URL at `.risk_indicators.media[].url`. Our `CAProfile` model shape also mismatches (`match_details` vs `match_score`). → BE-1/3/4 reopened as **BE-5** (P1 code fix). Strict/relaxed stays the UI confidence signal (Option-1 validated); score captured but not %-displayed pending CA scale clarification. Open Q to CA: what is the `match_score` scale/semantics?
- ☑ **PR #647** — CA workflow `ERRORED` status handled (no more 500; degraded `pending_provider`/re-screen report persisted). Merged (`e8eeffb`) + deployed to staging + verified 2026-07-03 (fresh screen returned 200, degraded report, no ValidationError). Also revealed the **duplicate-external-id → ERRORED** trigger (re-screening an existing customer errors — the fix covers it).
- ☑ **PR-2 = PR #655 + #658** — Agent 3 panel redesign, both merged + deployed + verified PASS (2026-07-03). **#655** (evidence-led panel: strict/relaxed pass, de-dup, audit-note removal, compact no-hit state, conditional Declared-vs-Provider). **#658** (count reconciliation: fixed the `intermediary_screenings` → adverse-media substring bug — `"media"` inside `"inter**media**ry"` — via token matching; primary-category partitioning so headline counts sum to total without double-counting multi-risk hits, while severity still uses contains-category semantics). Both display-only; no workflow/provider/risk/status/approval change.
- ☑ RPT-1/RPT-2 screening-report PDF — **PR #644 merged + deploying to staging** (2026-07-02)
- ☑ CA-1 single-risk-type re-test **passed** — both subtests: Mick Davis (PEP-only) → 75 → High; DGUP Granitny (Sanctions-only) → 100 → Prohibited. Models validated; Boris 450 was a poisoned fixture.
- ☑ CA-2 entity-level TP-marking SOP drafted (`sop-screening-true-positive-marking.md`, pending MLRO sign-off)
- ☑ CA-5 staging CA config verified via AWS audit (webhook secret wired + signature verification active; all 9 required vars correct; SCREENING_CONFIG_ID matches; workspace=sandbox)

---

## Recommended next order

**3-PR plan (locked 2026-07-03; low-risk, no combining):** ✅ PR-2 = Agent 3 panel redesign (**DONE — #655 + #658**) · ☐ BE-3 = standalone `matched_name` fix (prod-gated) · ☐ RPT-5 = CA Screening Certificate (pending mockup sign-off).

1. ~~**PR-2**~~ — **DONE** (#655 + #658, merged + deployed + verified).
2. **BE-5 (P1, NEW top priority)** — fix CA alert-risk profile JSON paths. Reopens BE-1/3/4; lights up real names + categories + adverse-media URLs on sandbox data now (score captured, not %-displayed pending CA scale answer). Highest data-quality ROI.
3. **RPT-5** CA Screening Certificate — independent; start once the illustrative mockup is signed off (mockup delivered, awaiting sign-off).
4. **PROD-0/0b** provision prod env + CA workspace → **PROD-1/2/3** replication (config validated, gated on infra + prod DNS).
5. **CA-6/CA-7/CA-8** (2nd admin, rescreen perm, source coverage) — CA-console hygiene.
5. **CA-11 / BE-3 / BE-4 / RPT-3** — confirm on prod CA data (BE-3 real names + BE-4 URLs both need CA to embed a profile/URLs).

_Strategic note: treat CA's risk level as a provider-side **triage** signal, not RegMind's authoritative risk grade — `rule_engine.py` owns LOW/MEDIUM/HIGH/VERY_HIGH. Don't over-fit CA's SUM scoring._
