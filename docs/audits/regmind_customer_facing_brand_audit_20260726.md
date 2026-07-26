# RegMind customer-facing brand audit

Date: 2026-07-26
Baseline: `origin/main` at `6f47fbb773322e6059e05cad3ef5daa270638019`

## Scope and method

A case-insensitive repository scan was run for the standalone legacy brand
tokens `onboarda` and `arie`, including filenames and hidden repository files.
Substring-only matches such as `intermediaries` were excluded from the
standalone-token classification.

Baseline scan:

- 5,465 standalone-token matches in 561 text files.
- 803 `onboarda` matches.
- 4,662 `arie` matches.
- Binary Office/PDF artifacts and legacy-branded filenames were inventoried
  separately.

The high raw count is dominated by historical audit evidence, test output,
repository/infrastructure paths, technical identifiers, and fixtures. The
active presentation-layer findings are concentrated in the portal, compliance
workspace, marketing page, central brand configuration, transactional
password-reset email, generated memo/PDF branding, and decision-notification
templates.

## Classification

### 1. Customer-facing and must change

| Surface | Finding | Planned treatment |
| --- | --- | --- |
| Applicant portal (`arie-portal.html`) | Onboarda browser title, navigation/auth branding, product copy, consent/help text, success/error messages, support links, and one generic ARIE compliance-review message | Use **RegMind Portal** consistently; use neutral institution wording where the product must not impersonate the applicant's financial institution |
| Compliance workspace (`arie-backoffice.html`) | Onboarda email placeholders, default generic institution name, and Onboarda-branded correspondence templates; generic "Back Office" labels | Use **RegMind Compliance Workspace** and RegMind specialist-module names; preserve workflow identifiers |
| Marketing page (`index.html`) | Onboarda title, metadata, wordmark, product copy, contact links, and footer | Use **RegMind** and existing neutral visual treatment; do not create a new logo |
| Marketing JSX source (`onboarda-website.jsx`) | Onboarda visible copy and contact links | Update visible content only; retain the internal filename and component identifier |
| Central brand configuration (`arie-backend/branding.py`) | Onboarda portal/platform/PDF/email defaults and domains | Make RegMind the tenant-independent presentation default while retaining cookie/metric compatibility identifiers |
| Transactional email (`arie-backend/server.py`) | Onboarda password-reset sender fallback, subject, and body | Use **RegMind** / **RegMind Portal** |
| Generated compliance outputs | Onboarda PDF header and composite-risk-engine attribution | Use **RegMind Compliance Report** and RegMind model attribution without changing memo logic |
| Decision correspondence templates | Onboarda product/legal identity embedded in generic approve/reject/EDD messages | Use institution-neutral correspondence and **Sent via RegMind** attribution |
| Public repository overview (`README.md`) | Describes the current product as two Onboarda/RegMind brands | Describe one RegMind product and explicitly document retained technical paths |
| Customer-visible branding tests | Existing tests assert Onboarda defaults | Replace with semantic RegMind and legacy-absence assertions |

### 2. Customer-facing but intentionally customer-specific

The following content is not a generic product brand and must remain intact:

- ARIE Finance named as the regulated pilot/customer/partner in demo scenarios,
  sample applications, pilot correspondence, and evidential records.
- Actual legal-entity/application values containing ARIE.
- Tenant-configured institution names returned from persisted runtime data.
- Institution-specific email recipients and historical correspondence.

No production or staging data will be changed by this pull request.

### 3. Internal-only or historical and must remain unchanged

- Repository and GitHub owner/name (`onboarda1234/onboarda`).
- `arie-backend/`, `arie-portal.html`, and `arie-backoffice.html` paths.
- API routes, database paths/tables, migrations, environment variables,
  secrets, AWS/ECS/ECR/S3/Render identifiers, JWT issuer, Docker user, CI image
  names, loggers, metrics, cookie/local-storage keys, and test database names.
- Python/JavaScript identifiers and comments that exist only to describe the
  implementation.
- March 2026 versioned decks, commercial packs, investor packs, audit reports,
  sprint reports, and their exported PDFs. They are retained as historical
  artifacts rather than silently rewriting dated evidence. Current product and
  investor-facing documentation already lives under RegMind-named June 2026
  reports.
- Committed audit logs, screenshots, raw test results, reconciliation tables,
  remediation reports, and staging evidence.
- ARIE risk-score workbooks and AI register, which are controlled/evidential
  source material rather than tenant-independent RegMind branding.
- Weak-password denylist entries containing `onboarda`; removing them would
  weaken authentication behaviour and is outside a presentation-only rebrand.

### 4. Unclear findings resolved by evidence

| Finding | Evidence and decision |
| --- | --- |
| Static "Onboarda Ltd" licensing/terms copy in the generic portal | The same portal is deployed at `staging.regmind.co`, while ARIE Finance is identified elsewhere as a pilot institution. Hardcoding another institution's legal identity into a tenant-independent portal is inappropriate. Replace it with institution-neutral wording; do not claim RegMind itself is the licensed account provider. |
| No approved RegMind logo/favicon asset | No standalone approved RegMind logo, icon, favicon, or manifest asset was found. Remove legacy lettermark presentation where necessary and reuse the existing neutral inline favicon. Record branded asset creation as a follow-up; do not invent a logo in this PR. |
| Onboarda email addresses | Tenant-independent product/contact defaults and visible support/demo links must move to the existing `regmind.co` product domain. Persisted customer recipients and internal demo identities remain unchanged. |

## Naming decisions

- Overall platform: **RegMind**
- Applicant experience: **RegMind Portal**
- Internal officer experience: **RegMind Compliance Workspace**
- Supervisor module: **RegMind AI Compliance Supervisor**
- Monitoring module: **RegMind Monitoring**
- Regulatory intelligence module: **RegMind Regulatory Intelligence**
- Periodic review module: **RegMind Periodic Reviews**

## Guardrails

This work will not alter workflows, status transitions, KYC/KYB, screening,
risk scoring, document verification, memo decision logic, approval gates,
permissions, authentication/authorization, audit logging, monitoring,
periodic-review/EDD controls, provider integrations, API contracts, database
schema/data, migrations, tenant ownership, infrastructure, or deployment
configuration.

## Post-implementation residual classification

The final standalone-token content scan excludes `.git`, Python virtual
environments, bytecode, and pytest caches. It intentionally includes this
audit report, historical evidence, and test fixtures, so the result is not
expected to be zero.

| Required closure category | `onboarda` | `arie` | Total | Files | Justification |
| --- | ---: | ---: | ---: | ---: | --- |
| Historical/audit evidence | 614 | 2,969 | 3,583 | 213 | Dated reports, exported evidence, audit records, commercial/investor artifacts, and reconciliation material are not silently rewritten |
| Infrastructure/deployment identifier | 3 | 80 | 83 | 8 | CI, container, service, deployment, and environment identifiers are outside presentation scope |
| Intentionally deferred technical rename | 3 | 11 | 14 | 3 | `arie-portal.html`, `arie-backoffice.html`, and `onboarda-website.jsx` remain stable technical filenames; their visible content is RegMind |
| Internal technical identifier | 82 | 718 | 800 | 123 | Loggers, paths, cookie/metric prefixes, compatibility defaults, comments, migrations, database paths, and source identifiers remain stable |
| Test fixture | 47 | 920 | 967 | 215 | Controlled fixture data, compatibility assertions, and internal path references remain unchanged |
| Unresolved customer-facing defect | 0 | 0 | 0 | 0 | No unexplained customer-facing occurrence remains |
| **Total** | **749** | **4,698** | **5,447** | **562** | Raw count is disclosed; zero occurrences is not claimed |

Filename/path-name scan:

- 36 historical `Onboarda` artifact filenames: dated March 2026 decks,
  commercial/investor packs, audit reports, and sprint reports.
- 3 legitimate customer/evidential ARIE filenames:
  `ARIE_AI register.xlsx`, `ARIE_Risk_Score_Sheet.xlsx`, and its backup.
- 8 internal technical names: `arie-backend`, its local database, the proposed
  risk workbook, and virtual-team role files.
- 3 intentionally deferred live technical filenames:
  `arie-portal.html`, `arie-backoffice.html`, and `onboarda-website.jsx`.

The only live-surface source matches are justified as follows:

- `arie-portal.html`: `onboarda` remains once in the common-password denylist;
  removing it would weaken authentication behavior.
- `arie-backoffice.html`: `@onboarda.internal` remains in a fixture detector,
  and one comment names the internal `arie-backend` path.
- `branding.py`: `arie` remains as the cookie and Prometheus metric prefix for
  session and monitoring continuity.
- Backend source: remaining tokens are internal loggers, comments, paths,
  database/schema defaults, seeded internal identities, compatibility checks,
  and operator-only startup text. Customer-visible fallbacks and generated
  output branding are RegMind.
- `README.md`: remaining tokens document the unchanged repository layout and
  deployment service identifiers.

## Pre-staging verification evidence

- Targeted branding and regression suite: **317 passed** in 49.31 seconds.
- Branding and application-shell retest after assertion corrections:
  **31 passed** in 1.69 seconds.
- Broad backend run: **5,099 passed**, 83 skipped, and 4 expected xfails before
  the local temporary volume filled with per-test SQLite databases. The
  subsequent errors were all rooted in `ENOSPC`, not assertion failures.
- Bounded alphabetic tail rerun after clearing only pytest temporary data:
  **4,298 passed**, 58 skipped. Its single initial failure was caused by the
  untracked local `.venv` being scanned by a source-security guard; moving the
  environment outside the repository and rerunning that module produced
  **4 passed**.
- Python compilation, Node syntax validation, `git diff --check`, DOCX archive
  integrity, and PDF metadata checks passed.
- The active regulatory report rendered to 20 pages and every rendered page
  was visually inspected.
- The active sample compliance memo regenerated through WeasyPrint 68.1 as a
  five-page A4 PDF; every page was visually inspected. Customer evidence for
  Coral Bay Holdings remained intact.
- Email and generated-PDF HTML templates render under semantic automated
  tests, including a customer-specific `ARIE Holdings Ltd` preservation case.

GitHub CI and staging/browser evidence are recorded in the pull request after
the branch deployment completes.
