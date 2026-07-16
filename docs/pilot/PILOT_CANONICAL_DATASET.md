# Pilot Canonical Dataset v1

Status: **Founder review required — not seeded**

Dataset: **Pilot Canonical Dataset**

Version: **v1**

Manifest SHA-256: `525776b0507f4ed6d4b07cbf5636583ffeef75192df46228c80f4dca2c47c4e0`
Permanent application namespace: `RM-PILOT-001` through `RM-PILOT-038`

This is a deterministic, synthetic, non-production fixture dataset for staging demonstrations and regression validation. It does not remediate or delete historical synthetic data. This change does not activate RSMP, recompute an existing application, alter Gate 0, change a score or workflow policy, run a provider, or seed staging.

## Dataset summary

The dataset contains 38 applications: 5 Low scenarios, 8 Medium scenarios, 15 High/Very High scenarios, 2 additional manual-EDD scenarios, and 8 negative-path scenarios. Periodic-review and monitoring states are embedded in the relevant risk scenarios rather than represented as inconsistent duplicate companies.

Every root record carries all of these exact markers in `prescreening_data`, in addition to `applications.is_fixture=true`:

- `dataset_name = Pilot Canonical Dataset`
- `dataset_version = v1`
- `synthetic = true`
- `non_production = true`
- `fixture = true`
- `visible_in_back_office = true`
- `source = fixtures.pilot_canonical_seeder`
- its permanent `scenario_reference`, scenario slug, and reviewed manifest hash

Expected scores were generated from `rule_engine.compute_risk_score` using the repository-seeded risk configuration and the approved Tier 0 runtime contract. The manifest stores the complete risk inputs, dimension evidence, controlled-mapping evidence, service-selection evidence, floors/escalations, expected EDD state, approval route, and workflow outcome. The future staging preflight will refuse to seed if the current runtime/configuration does not reproduce those results exactly. The preflight never enables the activation flag itself.

## Canonical applications

| Reference | Purpose | Expected score | Expected tier | Expected workflow / approval | Expected outcome | Useful screens |
|---|---|---:|---|---|---|---|
| `RM-PILOT-001` | Domestic professional services | 12.0 | LOW | Fast Lane / direct low-medium | Approved through direct low/medium route | Application overview, risk summary, approved queue |
| `RM-PILOT-002` | Domestic cloud services | 7.0 | LOW | Fast Lane / direct low-medium | Fast-lane KYC document collection | Application overview, KYC documents |
| `RM-PILOT-003` | Domestic trading company | 12.0 | LOW | Fast Lane / direct low-medium | Approved trading-company control | Application overview, compliance memo |
| `RM-PILOT-004` | Simple manufacturer monitoring false positive | 7.0 | LOW | Fast Lane / direct low-medium | Monitoring alert dismissed as false positive | Monitoring queue, alert detail |
| `RM-PILOT-005` | Low-risk completed periodic review | 12.0 | LOW | Fast Lane / direct low-medium | Periodic review completed; risk unchanged | Periodic-review queue/workspace, memo |
| `RM-PILOT-006` | International trading | 43.3 | MEDIUM | Standard Review / direct low-medium | Standard compliance review | Application overview, risk breakdown |
| `RM-PILOT-007` | Investment management | 43.3 | MEDIUM | Standard Review / direct low-medium | Compliance review for investment-management profile | Application overview, risk breakdown, compliance queue |
| `RM-PILOT-008` | Family office with open periodic review | 42.1 | MEDIUM | Standard Review / compliance required | Open medium-risk periodic review | Periodic-review queue/workspace |
| `RM-PILOT-009` | Cross-border payments | 42.1 | MEDIUM | Standard Review / compliance required | Compliance review for cross-border payments | Application overview, risk breakdown |
| `RM-PILOT-010` | Corporate shareholders | 40.1 | MEDIUM | Standard Review / direct low-medium | Corporate-shareholder evidence review | Ownership workspace, risk breakdown |
| `RM-PILOT-011` | Multiple selected services | 40.4 | MEDIUM | Standard Review / direct low-medium | Maximum selected-service risk used | Application overview, risk breakdown, risk-model page |
| `RM-PILOT-012` | Higher transaction volume without High floor | 50.3 | MEDIUM | Standard Review / compliance required | `monthly_volume_score_4`; compliance review; no High floor | Risk breakdown, approval route |
| `RM-PILOT-013` | E-money cross-border services | 40.4 | MEDIUM | Standard Review / direct low-medium | Compliance review for e-money service | Application overview, risk breakdown |
| `RM-PILOT-014` | Private Banking with open high-risk review | 55.0 | HIGH | EDD / dual control | Sector score 4 High floor; open high-risk periodic review | Risk breakdown, EDD, periodic-review workspace |
| `RM-PILOT-015` | Declared Domestic PEP | 55.0 | HIGH | EDD / dual control | Declared-PEP High floor and EDD | PEP evidence, risk breakdown, EDD |
| `RM-PILOT-016` | Declared Foreign PEP | 55.0 | HIGH | EDD / dual control | Foreign-PEP High floor and EDD | PEP evidence, risk breakdown, EDD |
| `RM-PILOT-017` | International Organisation PEP | 55.0 | HIGH | EDD / dual control | Declared-PEP High floor and EDD | PEP evidence, EDD |
| `RM-PILOT-018` | Family member of a PEP | 55.0 | HIGH | EDD / dual control | Declared-PEP High floor and EDD | PEP evidence, EDD |
| `RM-PILOT-019` | Close associate of a PEP | 55.0 | HIGH | EDD / dual control | Declared-PEP High floor and EDD | PEP evidence, EDD |
| `RM-PILOT-020` | Cash-intensive business | 55.0 | HIGH | EDD / dual control | Sector High floor and EDD | Risk breakdown, EDD |
| `RM-PILOT-021` | Precious metals and gems | 55.0 | HIGH | EDD / dual control | Sector score 3 plus elevated-jurisdiction High floor | Risk breakdown, country evidence, EDD |
| `RM-PILOT-022` | High-risk jurisdiction | 55.0 | HIGH | EDD / dual control | Elevated-jurisdiction High floor and EDD | Country evidence, risk breakdown, EDD |
| `RM-PILOT-023` | Opaque ownership | 55.0 | HIGH | EDD / dual control | Opaque-ownership High floor and EDD | Ownership workspace, risk breakdown, EDD |
| `RM-PILOT-024` | Sanctions hit escalated from monitoring | 70.0 | VERY_HIGH | EDD / blocked | Post-onboarding sanctions alert escalated to EDD | Monitoring queue, screening detail, EDD |
| `RM-PILOT-025` | Material adverse-media monitoring alert | 70.0 | VERY_HIGH | EDD / dual control | Open monitoring alert requiring officer review | Monitoring queue, screening detail, EDD |
| `RM-PILOT-026` | Combined severe risk factors | 70.0 | VERY_HIGH | EDD / blocked | Combined-risk EDD with approval block | Risk breakdown, screening detail, EDD |
| `RM-PILOT-027` | EDD for complex ownership | 55.0 | HIGH | EDD / dual control | Ownership evidence collection in EDD | Ownership workspace, EDD |
| `RM-PILOT-028` | EDD for trust structure | 55.0 | HIGH | EDD / dual control | Trust deed and control-chain review | Ownership workspace, EDD, KYC documents |
| `RM-PILOT-029` | Source-of-wealth review | 40.4 | MEDIUM | EDD / compliance required | Manual EDD source-of-wealth evidence review | EDD, KYC documents, memo |
| `RM-PILOT-030` | Manual compliance review and officer escalation | 42.1 | MEDIUM | Standard Review / compliance required | Officer escalation; unsolicited-referral score 4 without High floor | Compliance queue, approval route, risk breakdown |
| `RM-PILOT-031` | Failed identity verification | 7.0 | LOW | Fast Lane / blocked | Approval blocked pending successful IDV | KYC documents, approval blockers |
| `RM-PILOT-032` | Missing required documents | 7.0 | LOW | Fast Lane / blocked | Approval blocked until required documents are supplied | KYC documents, approval blockers |
| `RM-PILOT-033` | Unknown controlled sector | 7.0 | LOW | Fast Lane / blocked | Lane B unresolved sector sentinel; approval blocked | Risk breakdown, approval blockers |
| `RM-PILOT-034` | Unknown controlled entity type | 7.0 | LOW | Fast Lane / blocked | Lane B unresolved entity sentinel; approval blocked | Risk breakdown, approval blockers |
| `RM-PILOT-035` | Missing incorporation country | 12.0 | LOW | Fast Lane / blocked | Unresolved country sentinel; approval blocked | Risk breakdown, approval blockers |
| `RM-PILOT-036` | Screening pending | 7.0 | LOW | Fast Lane / blocked | Approval blocked until screening is terminal | Screening queue, approval blockers |
| `RM-PILOT-037` | Explicit approval-blocked control | 7.0 | LOW | Fast Lane / blocked | Approval blocked by unresolved control evidence | Approval blockers, compliance memo |
| `RM-PILOT-038` | Rejected application | 7.0 | LOW | Fast Lane / rejected | Rejected with retained rationale | Application overview, audit timeline |

The score/tier fields above are model outputs, while the workflow/approval fields also reflect documentary, screening, and officer controls. A Low score therefore does not override a failed IDV, missing document, unresolved mapping, or pending-screening block.

## Scenario coverage matrix

| Workflow family | Canonical coverage | References |
|---|---|---|
| Low risk | Domestic professional services, cloud services, trading, simple ownership, low volume | 001–005 |
| Medium risk | International trading, investment management, family office, cross-border payments, corporate shareholders, multiple services, higher volume, e-money | 006–013 |
| High risk | Private Banking, five declared-PEP roles, cash intensive, precious metals, elevated jurisdiction, opaque ownership, sanctions, adverse media, combined risk | 014–026 |
| EDD | High-risk floors plus complex ownership, trust, source of wealth, manual officer escalation | 014–030 |
| Negative paths | Failed IDV, missing documents, unknown sector/entity/country, screening pending, explicit block, rejection | 031–038 |
| Periodic review | Low completed (005), Medium open (008), High open (014) | 005, 008, 014 |
| Monitoring | False positive (004), cleared (005), escalated sanctions (024), open adverse media (025) | 004, 005, 024, 025 |
| Multi-service maximum risk | Stored raw selections, per-service resolutions, normalized selections, and maximum score | 011 and supporting cross-border cases |
| Fail-closed mapping | Exact hashed sentinels and blocked routes for unresolved controlled values | 033–035 |

## Recommended demo sequence

The manifest assigns a unique deterministic `demo_step` from 1 through 38. Short walkthroughs should use these curated subsets:

1. **Core onboarding (10–12 minutes):** 001 → 006 → 011 → 014 → 015 → 023 → 026.
2. **Compliance controls (8–10 minutes):** 012 → 030 → 031 → 033 → 036 → 038.
3. **Ongoing monitoring (8 minutes):** 004 → 005 → 008 → 014 → 024 → 025.
4. **Ownership and EDD (8 minutes):** 010 → 023 → 027 → 028 → 029.
5. **Full regression run:** process all records in `demo_step` order from the manifest.

The primary screenshot set is application overview, risk breakdown, ownership, PEP evidence, EDD pipeline, compliance memo, approval blockers, monitoring queue/detail, and periodic-review workspace.

## Safe replacement strategy

### Option A — Archive historical synthetic applications

Keep historical evidence but remove it from default pilot views. This is attractive for auditability, but the current schema has no dataset-level archive contract. Implementing it now would require a workflow or schema decision and is outside this change.

### Option B — Sanctioned cleanup

Delete only records proven synthetic through a reviewed, marker-scoped cleanup manifest. The repository has a sanctioned cleanup mechanism for registered fixtures, but the accumulated legacy staging population includes mixed and incomplete markers. A generic wipe is unsafe. Historical cleanup should be a separate reviewed operation after classification and a backup.

### Option C — Parallel namespace (**recommended now**)

Create the canonical records under the reserved `RM-PILOT-*` namespace, leave all historical records untouched, and use `is_fixture` plus canonical dataset markers/ref prefix to select the clean population in Back Office demos and regression harnesses. This is reversible, does not reinterpret legacy records, and avoids accidental contact with pilot-relevant or real data.

Recommendation: approve Option C for the initial rollout. After the canonical dataset has been validated, decide separately whether to archive the old population (Option A) or execute a marker-scoped sanctioned cleanup (Option B). Do not combine historical cleanup with the first canonical seed.

## Staging execution plan — not executed by this PR

Estimated operator window: **60–90 minutes**, excluding founder review.

1. **Approve and pin (10 minutes):** approve this manifest hash, pin deployed main SHA, database identity, activation state, risk-config version/hash, and application/fixture counts.
2. **Backup and collision preflight (10–15 minutes):** take the sanctioned staging backup; prove all 38 references and deterministic IDs are unoccupied or owned by this exact dataset identity.
3. **Static validation (2 minutes):** from `arie-backend/`, run `python -m fixtures.pilot_canonical_cli validate` and compare the printed hash to the approved hash.
4. **Runtime-alignment dry run (10–15 minutes):** with the intended model contract already configured through its separately approved process, run the CLI `dry-run`. It performs all inserts in one transaction, verifies current runtime results, and rolls back. It does not toggle the activation flag.
5. **Founder/officer evidence review (10–15 minutes):** review the dry-run register, exact scores, tiers, floors, routes, child-record counts, and zero-write confirmation.
6. **Separately authorised apply (5 minutes):** only after explicit approval, use the staging-only environment gate, exact confirmation token, and reviewed manifest hash. Never seed a subset for the first canonical rollout.
7. **Read-only verification (15–25 minutes):** verify 38 unique roots, tags, no duplicate references, child evidence, representative UI screens/exports, no provider/email activity, no changes outside `RM-PILOT-*`, and unchanged historical counts.
8. **Closeout:** record the deployed SHA, database identity, manifest hash, operator, timestamps, row counts, audit rows, and decision. Staging remains a non-production environment.

Illustrative commands are intentionally inert without the separate staging gates:

```bash
cd arie-backend
python -m fixtures.pilot_canonical_cli validate
python -m fixtures.pilot_canonical_cli list

# Only after separate operational approval and with the intended model contract
# already active through its own governed process. This command rolls back.
python -m fixtures.pilot_canonical_cli dry-run

# Apply requires all three: ENVIRONMENT=staging,
# ALLOW_PILOT_CANONICAL_SEED=1, and the exact token/hash.
```

## Rollback and cleanup

- **Before commit:** the seeder is single-transaction. Any error or collision rolls the full dataset and fixture audit rows back. Dry-run always rolls back.
- **After commit, before use:** restore the pinned staging snapshot only if the change window owns the entire database and the restore has been separately authorised; otherwise do not use a broad restore.
- **After use:** use a separately reviewed sanctioned cleanup entry scoped to the exact 38 application IDs, `RM-PILOT-*` references, dataset identity, and declared child-table order. The cleanup must refuse any non-fixture or mismatched identity. This PR deliberately does not add or execute a delete path.
- **Failure containment:** remove the canonical namespace from demo filters and stop. Never delete historical or pilot-relevant records to compensate for a canonical-seed failure.
- **Evidence:** retain the preflight/dry-run/apply audit evidence and before/after counts even when a rollback is required.

## Key risks and controls

| Risk | Control |
|---|---|
| Reference collision | Preflight all selected refs and deterministic IDs before the first write; fail the transaction on any mismatch |
| Runtime/config drift | Re-score all 38 against the live validated loader; exact mismatch blocks dry-run/apply |
| Accidental production use | Staging-only apply gate, synthetic/non-production tags, reserved namespace, exact confirmation token and reviewed hash |
| Duplicate records on rerun | Stable root and child identities plus idempotent lookup/update contracts; regression-tested twice |
| Provider/email side effects | Seeder bypasses APIs/providers/notifications; static tests forbid those paths |
| Schema or migration side effects | Seeder requires an already-initialised DB and never calls `init_db`, creates a table, or alters a table |
| Legacy-data damage | Parallel namespace; no historical update/delete and no generic cleanup |
| Model activation confusion | Dataset preflight never toggles the flag; activation is a separate governed operation |
| Misleading negative cases | Score/tier evidence is preserved, while failed-IDV/document/screening/mapping controls independently block approval |

## Founder decision requested

Approve or reject:

1. the 38 scenarios and their exact expected outputs;
2. the permanent `RM-PILOT-*` namespace;
3. Option C (parallel namespace) as the initial replacement strategy;
4. the staged dry-run and evidence-review sequence; and
5. a separate later decision for historical archive/cleanup.

This dataset is ready for founder review, not for staging execution. No pilot-readiness or production-readiness claim is made.
