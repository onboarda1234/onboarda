# SOC 2 Type I Readiness Assessment — Onboarda / RegMind

| | |
|---|---|
| **Prepared for** | Aisha Sudally (asudally@onboarda.com) |
| **Date** | 2026-08-05 |
| **Commit audited** | `9123196` (HEAD of `main` lineage) |
| **Scope of this assessment** | Full backend (`arie-backend/`, ~104 production modules, 213 routes), CI/CD (`.github/workflows/`), deployment config, and the `docs/` governance corpus |
| **Method** | Five parallel evidence-gathering passes over the codebase (access control, data protection, logging/monitoring, change management, vendor/availability/governance), each producing file:line evidence, plus direct verification of GitHub platform state and headline claims |
| **Status** | Draft for founder review — not an attestation, not a substitute for a CPA firm's examination |

> **Read this first.** This document is written to be handed to an auditor *and* to be a working remediation plan. It credits what is genuinely strong (there is a lot), and it is blunt about what is missing. The headline is in §1. The scoping in §2 changes what the word "compliant" even means here, so do not skip it.

---

## 1. Executive summary

**Verdict: Onboarda is not ready for a SOC 2 Type I examination today, and the single largest reason is not a control weakness — it is that the system a Type I would attest does not yet exist in production.** Everything audited runs on **staging**; production (`app.regmind.co`, production RDS) is unprovisioned, and the repo's own master register states plainly: *"Production: blocked — Audit-3 verdict REMEDIATE BEFORE PROCEEDING."* A Type I report describes the controls of a **defined production system as of a point in time**. You cannot attest a system that is not running.

Beyond that gating fact, the assessment found a consistent and unusual shape:

- **The engineering-level controls are markedly more mature than typical pre-SOC-2 companies.** bcrypt password hashing with a real 12-character complexity policy; hardened JWTs with fail-closed revocation; Fernet field-encryption for PII with a boot-time self-test; an append-only, hash-chained audit trail; SHA-pinned CI actions, hash-pinned dependency lockfiles, dual container scans with a fail-closed acceptance manifest; a frozen-module change-control regime enforced by CI; and a DR-posture verifier that tests whether point-in-time recovery *actually works* rather than merely that it is configured. This is real evidence and it will impress an auditor. (§4)

- **The governance, policy, and process layer — which is roughly 50–60% of a SOC 2 — is almost entirely absent.** There is no Information Security Policy, no access-control policy, no incident-response plan, no vendor/subprocessor register or DPAs, no organizational risk assessment, no access-review evidence, no security-awareness training, and no joiner-mover-leaver process. A code audit cannot find these because they are not code — but they are mandatory, and today they do not exist. (§6)

- **Change management runs through a single human.** The `main` branch has **no branch protection** (verified directly: `protected: false`), no `CODEOWNERS`, and no PR template. Every recent pull request was authored *and* merged by the single `onboarda1234` account, with AI agents (Claude, Codex) authoring ~40% of commits and having merged at least three themselves. The CI gates and AI adversarial review are genuinely good compensating controls — but SOC 2 CC8.1 expects a *documented, authorized* change process with independent review, and that is neither enforced at the platform level nor written down anywhere. (§5, finding CM-1/CM-2)

- **Several controls are "repo-ready but operator-unexecuted."** The DR restore drill has never been run (`rto_seconds_observed` is hardcoded `null` *on purpose*); CloudWatch alarms exist but route to zero-subscriber SNS topics that "look wired and page nobody"; the append-only database grants pack was written but never applied. Type I tests controls **as implemented and operating in design** — coded-but-never-executed is not implemented. (§5, §6)

- **Two internal audits already in the repo document HIGH/blocking findings that are still open at HEAD** — `regmind_audit_2_backend_security_authorization_20260725.md` (BSA-007/009/011) and `p12_1_regulated_record_deletion_discovery.md` (four critical live destructive paths). An auditor will read these before they read anything you present, and they are dated and specific.

**What this means practically.** The path to a SOC 2 Type I is a **10–16 week program**, not a code fix. Roughly 60% of the remaining work is document/policy/process authoring and control *operation* (evidence generation), 25% is provisioning production properly, and 15% is closing genuine technical gaps (MFA, failed-login logging, secret/SAST scanning, alerting). The good news: because the technical foundation is strong, the marginal cost of the technical closes is low, and the biggest single lever — writing the policy set and running the controls — is well-understood, cheap relative to engineering, and can start this week.

The rest of this document is the map: what SOC 2 Type I actually requires (§2–3), what you already have (§4), the ranked gap register (§5–6), the phased roadmap (§7), and how the audit engagement itself works (§8).

---

## 2. Scoping — what "SOC Type 1" means and what to scope

The request was "SOC Type 1." That phrase collapses two independent choices that materially change the work. Getting these right up front avoids auditing (and paying) for the wrong thing.

### 2.1 SOC 1 vs SOC 2 — you want SOC 2

- **SOC 1** attests controls over **financial reporting** (ICFR) — relevant when your system affects your *customers'* financial statements (e.g., a payroll processor, a payments ledger). Onboarda automates KYC/AML due diligence; it is not in your customers' financial-reporting path. **SOC 1 is not what your customers (regulated banks/EMIs/PIs) will ask for.**
- **SOC 2** attests controls over **security, availability, processing integrity, confidentiality, and privacy** — the questions a regulated financial institution's third-party-risk team asks before letting a vendor touch KYC data. **This is what you need.** Whenever this document says "compliant," it means SOC 2.

### 2.2 Type I vs Type II — start Type I, but plan for Type II

- **Type I** = an independent CPA's opinion that your controls are **suitably designed and implemented as of a single date** (e.g., "as of 30 November 2026"). It is a point-in-time snapshot. Timeline to achieve: driven by how fast you close gaps.
- **Type II** = the same controls **operating effectively over a period** (typically 3–12 months). It requires *evidence the controls ran* — access-review records, change tickets, alert logs — across the window.

**Recommendation:** target **Type I first** (it is the realistic near-term deliverable and unblocks early sales conversations), and **begin the Type II observation window the day after the Type I date**. Most buyers ultimately require Type II; Type I is the on-ramp, and the two share the same control set, so no work is wasted. Critically, **many of your gaps are Type-II-shaped even for Type I** — a control that has never once run (DR drill, alerting) fails even the Type I "implemented" test.

### 2.3 Which Trust Services Criteria to scope

Security (the **Common Criteria**, CC1–CC9) is **mandatory** in every SOC 2. The other four categories are opt-in. Recommendation for Onboarda:

| TSC category | Include in Type I? | Rationale |
|---|---|---|
| **Security (CC1–CC9)** | **Yes — mandatory** | The baseline. All nine common-criteria series apply. |
| **Confidentiality (C1)** | **Yes** | You hold passports, national IDs, UBO data, sanctions results. Buyers will expect it, and you already have encryption + retention machinery to point at. |
| **Availability (A1)** | **Yes** | You are selling a >99% uptime commitment in `docs/commercial/Onboarda_Pilot_Proposal.docx`. If you claim it commercially, scope it — but note this is currently your weakest category (single-container, no measured SLO). |
| **Processing Integrity (PI1)** | **Defer to Type II / later** | Tempting because the deterministic 4-layer pipeline is a genuine strength, but it adds audit surface. Add it once the core report exists. |
| **Privacy** | **Defer** | Highest-effort category (notice, consent, choice, DSAR operation). Your GDPR machinery is half-built (DSAR functions are dead code — DP-3). Do the privacy *engineering* now because it de-risks you legally, but keep the Privacy TSC out of the first report. |

**Recommended first-report scope: Security + Confidentiality + Availability.**

### 2.4 System boundary to declare

The auditor needs a crisp system boundary. Recommended:

> The Onboarda/RegMind production application (Python/Tornado backend, client portal, back-office), hosted on **AWS ECS Fargate (af-south-1)** with RDS PostgreSQL 15, S3 document storage, Secrets Manager, and CloudWatch; source control and CI/CD via **GitHub (`onboarda1234/onboarda`)** with GitHub Actions; and the subprocessors that process customer data (Anthropic, Sumsub, ComplyAdvantage — see §6.3).

**Explicitly exclude** the Render demo environment (`demo.regmind.co`) from the boundary — but only after you *prove* it holds no real customer data (today it runs with `ENABLE_DEBUG_ENDPOINTS`, `ENABLE_SHORTCUT_LOGIN`, and `ENABLE_ROLE_SWITCHER` all **on**, and auto-deploys from `main` with no CI gate — finding CM-11). The stale `railway.json` and the dormant `arie-finance-live` Render service should be removed from the repo so they cannot confuse an auditor about the production topology (finding CM-16, V-L1).

---

## 3. How this maps to the audit, and the readiness scorecard

A SOC 2 examination tests **controls**, and controls = *policy (what you commit to) + implementation (the mechanism) + evidence (proof it runs)*. You are strong on implementation, thin on policy, and thin on evidence-of-operation. The scorecard below is this assessor's judgment of design readiness per common-criteria series, on the recommended scope.

| TSC series | Theme | Readiness | One-line reason |
|---|---|---|---|
| **CC1** | Control environment (governance, org, HR) | 🔴 **Low** | No org chart, no InfoSec policy, no code of conduct, no background-check/onboarding process evidenced. |
| **CC2** | Communication & information | 🟠 **Partial** | Excellent internal engineering docs; no external-facing security/privacy commitments (dead legal links — V-H10). |
| **CC3** | Risk assessment | 🔴 **Low** | No organizational risk assessment exists; everything named "risk" is the AML product feature. |
| **CC4** | Monitoring of controls | 🟠 **Partial** | `REMEDIATION_MASTER_LIST.md` is genuinely strong control-monitoring evidence; no independent/periodic control review process. |
| **CC5** | Control activities | 🟠 **Partial** | Strong technical control activities; not tied to a policy/risk framework. |
| **CC6** | Logical & physical access | 🟠 **Partial** | Strong auth primitives; **no MFA** (IAM-1), decorative RBAC matrix (IAM-5), no access reviews (V-H1). |
| **CC7** | System operations (monitoring, incidents) | 🔴 **Low** | No wired alerting (LM-6), no incident-response plan (LM-7), failed logins not logged (LM-1). |
| **CC8** | Change management | 🟠 **Partial** | Superb CI/pipeline controls; no policy, no independent review, no IaC (CM-1/2/5). |
| **CC9** | Risk mitigation (vendors) | 🔴 **Low** | Zero vendor register, zero DPAs for 14 subprocessors handling PII (V-C1). |
| **A1** | Availability | 🔴 **Low** | Single process/container/desired-1; no measured SLO; unexecuted DR drill (V-H2, V-C2). |
| **C1** | Confidentiality | 🟠 **Partial** | Real encryption + retention; plaintext local doc cache (DP-1), name/DOB unencrypted (DP-2), no DPAs. |

Two 🟠 series can move to 🟢 with mostly documentation work. The 🔴 series need real build/provision/hire-a-process work. None is insurmountable in a quarter.

---

## 4. What is already strong (evidence to present to the auditor)

Do not rebuild these. Catalogue them — an auditor rewards demonstrable, tested controls, and these are your credibility anchors. Every item is backed by file:line evidence gathered in this audit.

**Access control & authentication (CC6)**
- bcrypt (`bcrypt==5.0.0`) with per-password salt on every credential path; no SHA/MD5/plaintext anywhere (`server.py:5349, 5439, …`).
- Real password-complexity policy — 12 chars, upper/lower/digit/special, common-password denylist — enforced on **every** write path (`security_hardening.py:3337-3391`).
- Hardened JWTs: HS256-only, issuer-verified, `require` exp/iat/sub (`auth.py:103-188`); DB-backed **fail-closed** token revocation with user-level cutoff and atomic credential-rotation transactions (`auth.py:29-37, 156-188`).
- **Every authenticated request re-validates the actor against live DB state** and overwrites the token's role/name claims — so deactivation and demotion take effect immediately, not at token expiry (`base_handler.py:795-920`).
- Segregation-of-duties for regulated decisions: screening second-review reviewer-≠-first enforcement, dual approval for HIGH/VERY_HIGH risk (`security_hardening.py:107, 408-428`).

**Data protection (CC6/C1)**
- Full security-header set incl. HSTS + enforcing CSP on every response (`base_handler.py:442-528`).
- Fernet application-level PII encryption with **fail-closed key handling and a boot-time encrypt→decrypt self-test that halts startup on mismatch** (`security_hardening.py:3165-3335`, `party_utils.py:32-70`).
- No secrets committed — repo *and* full git history scanned clean; centralized config with fail-hard validation in staging/production (`config.py:211-259`).
- Parameterized SQL throughout with allowlisted identifiers and a defense-in-depth SQL-identifier guard (`gdpr.py:263-276`).
- Retention policies seeded with legal basis (7y AML/CFT Act 2020 s.17; 10y audit), and audit tables structurally protected from automated purge (`db.py:11798-11813`, `gdpr.py:249-256`).

**Audit trail & logging (CC7)**
- Broad application audit trail with before/after state and 141 `log_audit` call sites; **document access is logged** (a common gap you don't have) (`base_handler.py:1056-1088`, `server.py:14769-14796`).
- **Database-level append-only enforcement** on `audit_log` via BEFORE UPDATE/DELETE triggers, run every boot, with a narrow, logged maintenance window (`db.py:7801-7883`).
- **Tamper-evident SHA-256 hash chains** on both `audit_log` and `supervisor_audit_log`, with anti-fork unique index and a verifier that detects tampering/forks/cycles (`db.py:6095-6400`).
- Denial auditing "complete by construction" — a terminal 403 catch-all in `on_finish` guarantees every authorization denial is recorded (`base_handler.py:265-292`).
- Evidence-pack export with per-file SHA-256 manifest and independently recomputable hash payloads (`evidence_pack_export.py:924-1195`).

**Change management & supply chain (CC8)**
- Multi-job, fail-closed CI: syntax, lint, migration-policy, `pip-audit` (no allowlist), pytest on real PostgreSQL+SSL, coverage gate, container build + smoke + security-header assertions (`.github/workflows/ci.yml`).
- Trivy container scanning, digest-pinned, all-severity, with a **fail-closed acceptance manifest that is currently empty and forbids ever accepting a CRITICAL** (`container-security.yml`, `container_vulnerability_acceptances.py`).
- Supply-chain integrity: **all GitHub Actions SHA-pinned**, dependency lockfiles hash-pinned (942 hashes) installed with `--require-hashes`, base image digest-pinned, `pip`/`setuptools` removed from the runtime image (`Dockerfile:49-50`).
- **Meta-guard tests that make the pipeline configuration itself tamper-evident** — a future edit cannot silently weaken a gate (`tests/test_supply_chain_pinning.py`, `test_dependency_hash_lock.py`, `test_ci_coverage_gate_fail_closed.py`).
- A named, machine-enforced **frozen-module change-control** regime (`CLAUDE.md:187-209`, `protected_module_regression_manifest.json`, dedicated CI job).
- Two CVE remediations traceable end-to-end in history (cryptography 48→50 for CVE-2026-69247/8/9, PR #934; WeasyPrint 68.1→69 for CVE-2026-49452, PR #891 — which *removed* an allowlist rather than extending it).

**Availability & DR engineering (A1)**
- Three-tier health endpoints (liveness/health/readiness), correctly separated and hardened, with the container healthcheck targeting liveness (`server.py:5242-5276`).
- Boot serialization via a PostgreSQL advisory lock that fails startup loudly rather than racing (`boot_lock.py`).
- A **DR-posture verifier that checks PITR *freshness*** (`LatestRestorableTime` lag ≤ 3600s) — proving recovery works, not just that backups are configured (`scripts/verify_dr_posture.py:98-136`).
- Thorough rollback runbook with a genuine DB decision tree distinguishing additive from destructive migrations (`docs/ROLLBACK_RUNBOOK.md`).

**Governance honesty (CC4)**
- `docs/REMEDIATION_MASTER_LIST.md` (789 lines, 239 tracked items) is a candid, ID-stable status register that refuses to over-claim. This is *favorable* CC4.1 evidence of control-monitoring — present it as such — even as it documents that 94 items remain open.

---

## 5. Gap register — technical & change-management (from the code)

Findings are consolidated from the five audit passes, ranked, and given stable IDs. Severity is **audit impact**: **P0** = blocks a Type I / guarantees a qualified opinion; **P1** = material design gap, likely exception; **P2** = should close before the Type II window; **P3** = hardening. Effort tag: **Doc** (policy/writing), **Config** (settings/wiring), **Eng** (code), **Infra** (provisioning).

### P0 — Type I blockers

| ID | Finding | TSC | Effort | Evidence / remediation |
|---|---|---|---|---|
| **BLK-1** | **Production does not exist.** All audited controls run on staging; prod RDS and `app.regmind.co` are unprovisioned; the Render "production" service is dormant. A Type I attests a production system. | CC-all, A1 | Infra | `CLAUDE.md:150-155`; `REMEDIATION_MASTER_LIST.md:32`. **Provision production via IaC (§7 Phase 1) before scheduling any audit.** |
| **BLK-2** | **No MFA anywhere** — zero implementation, scaffolding, DB columns, or flags. Admin/SCO accounts (which can rewrite the risk model, approve HIGH-risk onboardings, manage users, read the full audit trail) are protected by a single reusable password. No compensating control. | CC6.1, CC6.6 | Eng | Verified: no `totp/mfa/2fa` implementation. `REMEDIATION_MASTER_LIST.md:678` (5c.2, risk escalated). **Add TOTP MFA for all officer logins; enforce for admin/SCO.** Highest-severity technical gap. |
| **BLK-3** | **No wired alerting.** No Sentry/Datadog/PagerDuty/Slack/OTel; `SENTRY_DSN` is read and never used; alarm-provisioning scripts create **zero-subscriber SNS topics** that "look wired and page nobody"; the only in-app alerter (SMTP) no-ops because SMTP vars are in no deploy config. | CC7.2, CC7.3 | Config | `config.py:204`; `REMEDIATION_MASTER_LIST.md:606` (R3-OPS-001). **Wire alarms → SNS → a real on-call destination with a confirmed subscriber; add error tracking.** |
| **BLK-4** | **No incident-response plan, on-call rota, severity/escalation definitions, or breach-notification procedure.** Existing runbooks are deploy/rollback/ops, not IR. | CC7.3, CC7.4, CC7.5 | Doc | Searched all of `docs/`. **Write an IR plan (severities, roles, timelines, comms, regulator/customer breach notification); stand up an on-call rota.** |
| **BLK-5** | **No Information Security Policy set** (InfoSec, access control, acceptable use, data classification). This is the CC1/CC2/CC5 documentary foundation an auditor asks for on day one. | CC1, CC2, CC5 | Doc | See §6. **Adopt the core policy set (a readiness platform ships templates — §8).** |
| **BLK-6** | **No vendor/subprocessor register and no DPAs** for 14 external services processing customer PII, including full identity documents to Anthropic and Sumsub and DOB/nationality to ComplyAdvantage. | CC9.2, C1 | Doc | §6.3. **Build the register, execute DPAs with each subprocessor, publish a subprocessor list.** Pure documentation, but load-bearing. |
| **BLK-7** | **DR restore drill never executed** — no measured RTO/RPO. Tooling and runbook are ready; no evidence file has ever been produced (`rto_seconds_observed` hardcoded `null`). A never-run recovery control is not "implemented." | A1.2, A1.3 | Infra | `verify_dr_posture.py:167`; `REMEDIATION_MASTER_LIST.md:359-361`. **Execute the drill against production RDS; capture the evidence artifact.** |

### P1 — High (material design gaps)

| ID | Finding | TSC | Effort | Evidence / remediation |
|---|---|---|---|---|
| **CM-1** | **No documented change-management/SDLC policy.** Every technical control in §4 exists but is undescribed; CC8.1 requires a documented, authorized process (who authorizes, what approval, emergency changes). | CC8.1 | Doc | Write the policy — the controls already exist, they need describing. Highest ROI item in the register. |
| **CM-2** | **No independent review; `main` is unprotected.** Verified `protected: false`; no `CODEOWNERS`, no PR template; single account authors+approves+merges; AI agents merged ≥3 PRs. | CC8.1, CC6.3 | Config+Doc | **Enable branch protection (require PR + 1 review + passing checks), add `CODEOWNERS` and a PR template, and define who may approve/merge.** Capture a branch-protection screenshot as evidence. |
| **IAM-2** | **Login/registration brute-force protection fails *open*, is per-process and per-IP only** (no account dimension), and swallows all exceptions. = `FINDING-BSA-007` (HIGH/blocking), still present at HEAD. | CC6.1, CC6.6 | Eng | `server.py:5339`; `auth.py:234, 253-269`. **Move login to the fail-closed shared limiter; add an account dimension.** |
| **IAM-3** | **No account lockout.** No failed-attempt counter or `locked_until`; the `account_lockouts` table is referenced but never created. | CC6.1 | Eng | `regulated_deletion.py:106`. **Implement lockout/backoff after N failures.** |
| **LM-1** | **Failed authentication is not logged** — no audit row, log line, metric, or alarm; client logins log neither success nor failure. Credential stuffing is invisible and forensically unreconstructable. The runbook even promises a "failed-login spike" alarm with no telemetry to alarm on. | CC7.2, CC6.1 | Eng | `server.py:5349-5388`. **Log every authentication failure (and client success) to `audit_log`; emit a metric; alarm on spikes.** |
| **IAM-5** | **RBAC matrix is decorative.** `ROLE_PERMISSION_MATRIX` is served to the UI labelled `backend_policy` but never consulted by any authorization decision; 159 endpoints repeat literal role arrays that drift. Root cause of IAM-6/7. = `FINDING-BSA-008`. | CC6.3 | Eng | `server.py:17621-17677`. **Introduce a central `require_permission` primitive driven by the matrix.** |
| **IAM-6** | **Analyst can perform an SCO-only escalation** (move a case into the EDD lane) — the handler gates on bare `require_auth()` and only blocks clients. = `FINDING-BSA-009` (HIGH/blocking), still present at HEAD. | CC6.3 | Eng | `server.py:8959-8986`. **Restrict to admin/SCO/CO per the declared matrix.** |
| **DP-1** | **Plaintext KYC documents persist indefinitely** in a local `UPLOAD_DIR` "cache," deleted only on the S3-failure path; no eviction job; encryption of the Render/container disk unproven. Passports/IDs accumulate outside S3 encryption *and* outside the retention policy. | C1, CC6.1 | Eng | `server.py:12534-12541, 12595`. **Encrypt-at-rest or evict the local cache; add a TTL sweep; prove disk encryption.** |
| **DP-2** | **Names, dates of birth, and PEP status are stored in plaintext** — the encryption scope covers passport/ID/address but not `first_name`/`last_name`/`date_of_birth`/`is_pep`, the most sensitive combination in an AML dataset. | C1 | Eng | `party_utils.py:74-90` vs `db.py:822-855`. **Extend field encryption to name/DOB or document the risk-acceptance with compensating controls.** |
| **DP-7** | **Full KYC document images + director/UBO names sent to Anthropic** (and PII to Sumsub/ComplyAdvantage) with **no DPA, no data-minimization, no subprocessor artifact**. | C1, CC9.2 | Doc | `claude_client.py:2131-2161`. Ties to BLK-6. **Execute DPAs (Anthropic offers a zero-retention/no-training commercial DPA — confirm and file it); document the data flow.** |
| **CM-3** | **No SAST and no secret scanning in CI.** Only `pip-audit` + Trivy; the clean-repo secrets finding is therefore *unenforced*. | CC7.1, CC6.1 | Config | No `bandit/codeql/semgrep/gitleaks`. **Add secret scanning (gitleaks/trufflehog) and SAST (CodeQL/bandit/semgrep) to CI.** |
| **CM-4** | **No automated/scheduled dependency scanning.** No Dependabot/Renovate, no `schedule:` trigger; scans fire only on code change, so a CVE published post-merge is invisible until someone pushes. | CC7.1 | Config | **Add `dependabot.yml` and a nightly `schedule:` on the existing scan jobs.** |
| **CM-5** | **No Infrastructure as Code.** Zero Terraform/CloudFormation (verified); AWS is console/CLI-managed; **no task definition is stored in the repo** (deploy mutates the *live* revision). The entire infrastructure layer sits outside change management — unreviewed, unversioned, no drift detection. | CC8.1 | Infra | **Codify ECS/RDS/S3/Secrets/ALB/IAM in Terraform (import existing), then provision production from it (ties to BLK-1).** |
| **LM-3** | **The `audit_log` hash chain covers ~10 of ~60 writers** — wiring deliberately deferred; the load-bearing `Decision` write is a raw INSERT with `entry_hash IS NULL`. The tamper-evidence control does not protect the records that matter most (approvals/overrides/config/logins). | CC7.x | Eng | `db.py:6056`; `server.py:37018`. **Route the decision/override/config/auth writers through `append_audit_log`.** |
| **LM-4** | **The supervisor audit chain is disabled in staging and production** (`ENABLE_SUPERVISOR_AUDIT: False`); the strongest audit-integrity control produces no rows and the evidence-pack section is empty where it counts. | CC7.x | Config | `environment.py:221-223, 246-248`. **Decide whether the supervisor is in the production path; if so, enable its audit chain; if not, remove it from the evidence-pack claims.** |
| **V-H1** | **No user access-review process or evidence; no joiner-mover-leaver/offboarding procedure.** | CC6.2, CC6.3 | Doc | **Institute quarterly access reviews (evidence them) and a documented JML process.** |
| **V-H2** | **Single point of failure at every layer** — single-process single-container Tornado, ECS desired-count 1, no autoscaling, single-AZ RDS `t3.micro`, in-memory rate limiter that resets on restart. Undermines the >99% you sell. | A1.1, A1.2 | Infra | `DEPLOYMENT_RUNBOOK.md:469`. **Run ≥2 tasks across AZs; Multi-AZ RDS; move rate-limit state to shared storage.** |
| **V-H4** | **No graceful shutdown** — zero SIGTERM handlers; a rolling deploy or scale-in kills in-flight requests/transactions. | A1.2 | Eng | **Add SIGTERM handling with connection draining.** |
| **CM-7** | **The documented rollback tag `v4.1-stable` does not exist** (verified: only `v4.0-stable` and `v5.0-pre-screening-abstraction` on the remote). `CLAUDE.md`'s rollback instruction points at a non-existent ref *and* names the wrong procedure. | CC8.1 | Doc | **Correct `CLAUDE.md:173-177`.** Five-minute fix; an auditor testing your documented rollback would catch it. |
| **IAM-9** | **A permanent SCO-privileged non-human CI identity** (`github-actions:day6-staging-smoke`, `role=sco`, always active) whose token is hand-forged from `JWT_SECRET` pulled into CI — so anyone with the AWS keys can mint an arbitrary-role token. | CC6.1, CC6.3 | Eng+Config | `db.py:11737-11745`; `deploy-staging.yml:327-373`. **Scope it to just-in-time, minimal role; move to OIDC so raw signing keys aren't exposed to CI.** |
| **CM-8** | **Long-lived static AWS access keys in CI** (not OIDC); the deploy workflow mints an SCO token. | CC6.1 | Config | **Switch GitHub Actions → AWS to OIDC role assumption; drop static keys.** |
| **V-H10** | **Dead legal links in customer-facing surfaces.** Privacy/Terms/Cookie links are `href="#"`; the portal registration form gates a **required** consent checkbox on Terms + Privacy links that both point to `#` — you are collecting consent against documents that do not exist. | CC2.2, Privacy | Doc | Verified `index.html:416`, `arie-portal.html:1904`. **Publish a privacy policy and terms; wire the links before onboarding real applicants.** Also a legal exposure, not just SOC 2. |
| **V-H8** | **No penetration test; secret rotation is unsafe** — the runbook warns *against* rotating `JWT_SECRET`. | CC4.1, CC6.1 | Infra+Eng | **Commission an independent pentest (auditors expect one); make key rotation safe (see DP-6).** |
| **V-H9** | **No organizational risk assessment.** Everything named "risk" is the AML product. CC3 requires a documented enterprise risk assessment. | CC3.1–CC3.4 | Doc | **Produce an annual risk assessment (asset inventory, threats, likelihood/impact, treatment) — a readiness platform provides the framework.** |

### P2 — Medium (close before the Type II window)

| ID | Finding | TSC | Evidence |
|---|---|---|---|
| **DP-3** | GDPR **DSAR functions are dead code** — no routes/callers; **no data export/portability capability exists at all**. | Privacy | `gdpr.py:723-769` |
| **DP-4** | GDPR **erasure engine wired-but-OFF** while ≥6 live *unguarded* destructive paths exist (incl. a client-reachable delete that hard-deletes audit/SAR/memo records, and a boot migration that name-matches and deletes on every startup). Lawful erasure disabled; unlawful erasure reachable. | Privacy, C1 | `gdpr_erasure.py:3`; `p12_1_regulated_record_deletion_discovery.md` |
| **DP-5/6** | `PII_ENCRYPTION_KEY` absent from every deployment manifest (no provisioning/rotation evidence); **no key-rotation capability** (single static Fernet key; rotation = data loss). | CC6.1, C1 | `config.py:236-237`; `diagnose_pii_tokens.py:10-13` |
| **DP-8** | HTTPS redirect is **production-only**; staging (which runs live providers and real screening) serves regulated data over plaintext HTTP if `X-Forwarded-Proto: http`. | CC6.7 | `base_handler.py:256-258` |
| **DP-10** | **No malware/AV scanning on uploads**; a ZIP-container payload renamed `.docx` passes the magic-byte check and is served back to officers inline. | CC6.8 | `security_hardening.py:3945`; `server.py:14750` |
| **LM-2** | `production_controls.py` defines a **schema-incompatible second `audit_log` table**; `CREATE TABLE IF NOT EXISTS` first-writer-wins means a wrong import order would silently break all audit writes. Latent (never called) but unreviewed. | CC7.x | `production_controls.py:936-957` |
| **LM-8** | **No CloudWatch log-retention configured** anywhere in the repo; AWS default is never-expire, which is not a control and conflicts with GDPR minimization (logs carry IPs/user IDs). | CC7.2, C1 | No `retentionInDays` in repo |
| **LM-10/11** | Reading/exporting the audit trail is **not itself audited**; audit export **silently truncates** at 10,000 rows and reports the truncated count as the total. | CC7.x | `server.py:20536, 20631` |
| **LM-18** | Append-only enforcement is **defeatable by the app DB role** (owner can drop the trigger); the least-privilege **grants pack was written but never applied**. | CC7.x | `db.py:7818-7821`; `REMEDIATION_MASTER_LIST.md:510` (RDI-018) |
| **IAM-10/11** | Officers have **no self-service password change/reset in any environment**; the admin officer-reset endpoint 403s in production — so a production officer cannot rotate or recover a password. `must_change_password` is dead code. | CC6.1, CC6.2 | `server.py:4675-4677, 15745-15785` |
| **IAM-13** | No idle/inactivity session timeout; fixed 24h absolute lifetime; no concurrent-session limit. | CC6.1 | `auth.py:27` |
| **CM-9/10** | Coverage floor (30%) and test-count floor (3,800) are far below actuals (~8,694 tests) — neither gate can bind; `supervisor/*` is excluded from coverage measurement. | CC8.1 | `.coveragerc:17`; `ci.yml:267` |
| **CM-11** | Demo auto-deploys from `main` with **no CI gate** and debug/shortcut-login/role-switcher **on**; internet-facing. | CC8.1 | `render.yaml:117, 148-177` |
| **CM-12** | Migrations are **forward-only** (no down/revert) and execute at container boot, not as a reviewed pre-deploy gate. | CC8.1 | `migrate.py:9-12` |
| **V-M7** | OpenCorporates **silently falls back to simulated data** when unkeyed — "not a defensible AML screening source." | PI1 | `screening.py:299-311` |
| **V-M5** | Unpinned Cloudflare CDN `jspdf` (no SRI) executes in the authenticated officer session; Google Fonts loaded in all frontends. | CC6.8 | `arie-backoffice.html:1288` |

### P3 — Low (hardening, mostly Type II polish)

CSP retains `unsafe-inline` for scripts/styles (documented, deferred — DP-20); CSP Report-Only has no `report-uri` (DP-21/LM); `TRUSTED_PROXY_CIDRS` defaults permissive → spoofable IPs in the 10-year audit log (DP-23); commit signing not enforced (CM-17); migration checksums stored but never re-verified (CM-18); tests excluded from lint (CM-19); `docker-compose.yml` ships a hardcoded default DB password and defaults to `production` (CM-20); ~25 naive `datetime.now()` calls (correct only because the container is UTC — LM-19); duplicated security-header implementation where `/static/*` omits CSP (DP-17); Sumsub returns 401 (not 200) on bad webhook signature, confirming endpoint validity to a prober (IAM-20). Full detail with file:line in the working notes.

---

## 6. Gap register — organizational controls (NOT in the code)

**This section is the part a code audit cannot surface, and it is where most of the remaining SOC 2 effort lives.** SOC 2 is a controls framework over people and process, not just software. None of the following exists today; all are required for the recommended scope. These are **Doc/Org** effort — cheap in dollars, but they need an owner and calendar time.

### 6.1 Policies to author and adopt (CC1, CC2, CC5)

A standard SOC 2 policy set — every mainstream readiness platform ships editable templates for these:

1. Information Security Policy (the umbrella)
2. Access Control Policy (roles, least privilege, review cadence, MFA requirement)
3. Change Management Policy (**CM-1** — describe the controls you already have)
4. Incident Response Plan (**BLK-4** — severities, roles, timelines, comms, breach notification)
5. Business Continuity & Disaster Recovery Plan (reference the runbooks + drill evidence)
6. Risk Assessment Policy & the annual risk assessment itself (**V-H9**)
7. Vendor/Third-Party Risk Management Policy + subprocessor register + DPAs (**BLK-6**)
8. Data Classification & Handling Policy (you already classify implicitly via retention categories — formalize it)
9. Data Retention & Disposal Policy (the *values* live in a DB seed function today — **V-M3** — lift them into a versioned policy document)
10. Acceptable Use Policy
11. Secure SDLC Policy (describe CI gates, code review, testing)
12. Logging & Monitoring Policy
13. Encryption / Key Management Policy (**DP-5/6** — and build the key-rotation capability it will commit you to)
14. Backup Policy (reference RDS PITR + the executed drill)
15. Physical Security Policy (short — you are cloud-hosted; covers offices/laptops)

### 6.2 Human-resources & governance controls (CC1)

- **Org chart** and documented roles & responsibilities (who owns security? name a security officer — even if it is you initially).
- **Background checks** for personnel with production access, evidenced.
- **Onboarding/offboarding (JML)** procedure with a checklist and evidence (**V-H1**) — access granted on join, revoked on leave, within a stated SLA.
- **Security-awareness training** on hire and annually, with completion records (a platform automates this).
- **Code of conduct** and confidentiality/IP agreements signed by all personnel and contractors.
- **Board/management oversight** evidence — even a lightweight quarterly security review meeting with minutes satisfies CC1.2 for a company this size.

> **A note on your operating model.** AI agents (Claude, Codex) author a large share of commits and have merged PRs. This is legitimate, but an auditor will ask how it fits your change-management and access-control policies. Address it head-on: write into the Change Management Policy that AI-generated changes follow the *same* PR-review-and-approval gate as human changes, that a named human is accountable for every merge, and that no autonomous agent holds standing production credentials. Your CI + adversarial-review setup already substantiates this — it just needs to be stated as policy and enforced by branch protection (**CM-2**).

### 6.3 Vendor / subprocessor register (CC9.2, C1) — reconstruct and formalize

No register exists. Fourteen external services were identified from code. The material subprocessors (those processing customer PII) require DPAs **before** the audit. Minimum viable register:

| # | Subprocessor | Purpose | Customer data shared | DPA needed? |
|---|---|---|---|---|
| 1 | **Anthropic** | Document verification, memo drafting | **Full document images** + director/UBO names | **Yes — priority** |
| 2 | **Sumsub** | Individual IDV/KYC | Names, DOB, country, ID document images | **Yes — priority** |
| 3 | **ComplyAdvantage** | Sanctions/PEP/adverse-media screening | Full name, DOB, nationality, address | **Yes — priority** |
| 4 | **AWS** | Compute, DB, storage, secrets, logs | **All application data** | **Yes** (AWS DPA/BAA standard) |
| 5 | **OpenCorporates** | Corporate registry enrichment | Company name, jurisdiction | Yes (lower sensitivity) |
| 6 | **UK Companies House** | UK registry lookup | Company name/number | Assess |
| 7 | **ipapi.co** | IP geolocation risk signal | **Client IP** (personal data) | Assess / consider removing |
| 8 | **SMTP relay** (unnamed) | Notification email | Recipient email | Identify the vendor first |
| 9 | **GitHub** | Source control, CI/CD | Source code, CI secrets | Yes (GitHub DPA) |
| 10 | **GoDaddy** | DNS | DNS records | Low |
| 11 | **Render** | Demo environment | Demo data only (verify!) | Exclude from boundary if no real data |
| 12 | **Google Fonts** | Web fonts | End-user IP/UA | Consider self-hosting to eliminate |
| 13 | **Cloudflare CDNJS** | `jspdf` in back-office | Officer IP/UA; runs JS in authed session | Self-host + pin (also **V-M5**) |
| 14 | **Sentry** | (declared, never wired) | None currently | Wire it (**BLK-3**) or remove the dead config |

---

## 7. Remediation roadmap

Sequenced so the critical path (production must exist before it can be audited) drives the schedule, and the cheap high-leverage documentation work runs in parallel from week one. Durations assume a small team with focused effort; they are planning estimates, not commitments.

### Phase 0 — Decisions & kickoff (Week 1)
- Confirm scope: SOC 2 Type I, TSC = **Security + Confidentiality + Availability**, system boundary per §2.4.
- Name a **security officer / control owner** (accountable person).
- Select a **readiness platform** (Vanta / Drata / Secureframe — §8) and a **CPA audit firm**.
- Quick wins, same week: fix `CLAUDE.md` rollback tag (**CM-7**); enable **branch protection** + add `CODEOWNERS` + PR template (**CM-2**); add **secret scanning + SAST + Dependabot** to CI (**CM-3/4**); remove stale `railway.json` and dormant Render prod service (**CM-16**).

### Phase 1 — Provision production properly (Weeks 2–6) — *critical path*
- **Codify infrastructure in Terraform** and import existing staging resources (**CM-5**).
- **Provision production** from IaC: ECS ≥2 tasks across AZs, **Multi-AZ RDS**, S3 with SSE-KMS + verified lifecycle, Secrets Manager, ALB+ACM, CloudWatch (**BLK-1, V-H2**).
- **Wire alerting end-to-end**: CloudWatch alarms → SNS → real on-call destination with confirmed subscriber; add Sentry error tracking (**BLK-3**).
- Set **CloudWatch log retention** (**LM-8**); switch CI→AWS to **OIDC** (**CM-8**).
- Migrate to production and **execute the DR restore drill**, capturing the evidence artifact (**BLK-7**).
- **Apply the append-only DB grants pack** (**LM-18**).

### Phase 2 — Close technical control gaps (Weeks 3–9, overlaps Phase 1)
- **MFA (TOTP) for officer logins**, enforced for admin/SCO (**BLK-2**).
- **Failed-login logging + metric + alarm**; log client-login success (**LM-1**).
- **Fail-closed, account-aware brute-force protection + lockout** (**IAM-2/3**).
- **Central `require_permission` primitive** wired to the RBAC matrix; fix the analyst-escalation and audit-read authorization gaps (**IAM-5/6/7**).
- **Encrypt or evict the local document cache; extend field encryption to name/DOB** (**DP-1/2**); build **key-rotation** capability (**DP-5/6**).
- **Graceful shutdown / SIGTERM draining** (**V-H4**); **upload malware scanning** (**DP-10**).
- **Route decision/override/config/auth writers through the hash chain**; resolve the supervisor-audit flag (**LM-3/4**).
- Wire the customer-facing **privacy policy / terms links** (**V-H10**).

### Phase 3 — Author the governance layer (Weeks 2–10, runs in parallel throughout)
- Adopt the **15-policy set** (§6.1) — platform templates, tailored.
- Write the **Incident Response Plan** and stand up the **on-call rota** (**BLK-4**).
- Produce the **organizational risk assessment** (**V-H9**).
- Build the **vendor register and execute DPAs** with Anthropic, Sumsub, ComplyAdvantage, AWS, GitHub (**BLK-6**).
- Stand up **HR controls**: JML procedure, background checks, security-awareness training, signed policies (**§6.2, V-H1**).
- Run the **first quarterly access review** and evidence it.

### Phase 4 — Readiness review & remediation (Weeks 10–13)
- Readiness platform gap scan is green; run a **third-party gap assessment** (often the audit firm's pre-audit) and close residuals.
- Commission the **independent penetration test** (**V-H8**) and remediate findings.
- Assemble the **evidence package** (you already have excellent raw material in `REMEDIATION_MASTER_LIST.md`, the runbooks, and the evidence-pack exporter).

### Phase 5 — Type I examination (Weeks 13–16)
- Auditor fieldwork against the point-in-time date; produce the report.
- **Begin the Type II observation window the next day** — keep every control generating evidence.

**Critical path: BLK-1 → BLK-7 (production must exist and DR must be drilled before the audit date).** The documentation phases (3) are the long pole in calendar terms but can start immediately and in parallel; do not sequence them after the engineering.

---

## 8. The audit engagement — how you actually get the report

- **You cannot issue a SOC 2 report yourself.** It is an attestation by a **licensed CPA firm**. Budget roughly USD 10–25k for a Type I from a startup-focused firm (Type II more). Get quotes early; the firm will also tell you exactly which evidence they want, which de-risks Phase 4.
- **Use a readiness/compliance-automation platform.** Vanta, Drata, or Secureframe each: ship the policy templates you need (§6.1), connect to AWS/GitHub to **collect evidence continuously**, run a live gap dashboard, and map controls to the TSC. For a company starting near-zero on policy this compresses the timeline more than anything else, and it is what maintains the Type II window afterward. Budget ~USD 7–15k/year.
- **Two-vendor model is standard:** platform (continuous readiness + evidence) + CPA firm (the examination). The platform usually has a marketplace of partner auditors.
- **Sequence:** platform onboarding & policy adoption → connect integrations → close the gap dashboard → auditor readiness/gap assessment → pick the Type I "as of" date → fieldwork → report. With focused effort on the roadmap above, **a Type I "as of" date roughly 12–16 weeks out is realistic**, gated by production provisioning (Phase 1) and the DR drill (BLK-7), not by the paperwork.

---

## 9. Immediate next actions

**This week (cheap, high-leverage, no dependencies):**
1. Fix the rollback tag reference in `CLAUDE.md:173-177` (**CM-7**).
2. Turn on **branch protection** for `main` (require PR + 1 review + passing status checks); add `CODEOWNERS` + a PR template (**CM-2**).
3. Add **gitleaks/trufflehog** (secret scanning), **CodeQL/bandit** (SAST), and **Dependabot** + a nightly scan schedule to CI (**CM-3/4**).
4. Remove `railway.json` and the dormant Render "production" service from the repo (**CM-16**).
5. Publish a **privacy policy and terms**, and wire the dead `href="#"` links before onboarding any real applicant (**V-H10**) — this is legal exposure independent of SOC 2.

**This month:**
6. Choose a **readiness platform** and a **CPA firm**; start policy adoption (§6.1).
7. Start **Terraform-importing** staging and plan the **production provision** (**CM-5, BLK-1**).
8. Scope and start **MFA** (**BLK-2**) and **failed-login logging** (**LM-1**).
9. Draft the **Incident Response Plan** and **vendor register + DPA outreach** (**BLK-4, BLK-6**).

---

## Appendix A — Method & confidence

Five independent evidence-gathering passes were run over the codebase, each scoped to a control domain and each required to produce file:line evidence rather than impressions. Their findings cross-corroborated on every cross-cutting theme (production-doesn't-exist, engineering-strong/governance-absent, staging-holds-real-data, single-human change control). This assessor additionally verified the highest-impact, falsifiable claims directly: `main` branch protection is off; the `v4.1-stable` tag does not exist on the remote; there is no MFA/TOTP implementation; there are no `CODEOWNERS`/PR-template/IaC files; and the customer-facing legal links are dead — all confirmed. Where a finding originates in the repo's own prior audits (`regmind_audit_2_backend_security_authorization_20260725.md`, `p12_1_regulated_record_deletion_discovery.md`), the cited HIGH/blocking items were re-verified as still present at HEAD `9123196`.

This is a readiness assessment, not an attestation. Findings reflect the state of the codebase and repository at the audited commit; runtime configuration of the deployed environment (actual `ENVIRONMENT`, `ALLOWED_ORIGIN`, secret values, live AWS resource settings) cannot be verified from source and must be evidenced separately during the audit.

## Appendix B — Finding index

Technical/change-management findings use the IDs assigned in §5 (BLK-*, IAM-*, DP-*, LM-*, CM-*, V-*). These trace to the five audit passes and, where applicable, to canonical IDs in `docs/REMEDIATION_MASTER_LIST.md` and the two prior internal audits. Full per-finding file:line evidence beyond what is inlined here is retained in the assessment working notes and can be expanded into the auditor evidence package on request.
