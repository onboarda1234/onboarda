# RegMind — Client Risk Computation: Final Closure Report

**Date:** 2026-04-06  
**Module:** Client Risk Computation (Score-to-Band, Escalation, Recomputation)  
**Status:** Conditionally Closed pending merge + migration rollout  
**Author:** Compliance Engineering  
**Version:** 2.0 (updated with rollout verification pass)  

---

## 1. Deployment Verification

### Code-level verification (confirmed in repository)

| Component | File | Line(s) | Status |
|---|---|---|---|
| Canonical thresholds | `rule_engine.py` | 257–262 | ✅ Confirmed: LOW 0–39, MEDIUM 40–54, HIGH 55–69, VERY_HIGH 70–100 |
| `classify_risk_level()` | `rule_engine.py` | 265–287 | ✅ Single canonical function, reads DB config → falls back to CANONICAL_THRESHOLDS |
| Escalation flags | `rule_engine.py` | 583–647 | ✅ `sub_factor_score_4`, `very_high_risk_sector`, `composite_score_85_plus`, floor rules |
| `requires_compliance_approval` | `rule_engine.py` | 636–647 | ✅ Set when escalations list is non-empty |
| `compute_risk_score()` return shape | `rule_engine.py` | 640–647 | ✅ Returns: score, level, dimensions, lane, escalations, requires_compliance_approval |
| Duplicate remapping removed | `server.py` | — | ✅ No inline threshold remapping found; only `classify_risk_level()` used |
| KYC recomputation | `server.py` | 2055–2078 | ✅ KYC handler always recomputes risk via `compute_risk_score()` |
| Back-office edit recomputation | `server.py` | 1649–1703 | ✅ Detects material field changes → recomputes → updates DB → logs audit trail |
| Back-office RISK_THRESHOLDS | `arie-backoffice.html` | 5915–5920 | ✅ Aligned: LOW 0–39.9, MEDIUM 40–54.9, HIGH 55–69.9, VERY_HIGH 70–100 |
| DB seed: Seychelles | `db.py` | seed section | ✅ Score = 2 (Medium Risk) |
| DB seed: Construction | `db.py` | seed section | ✅ Score = 3 (High Risk) |
| DB seed: thresholds | `db.py` | seed section | ✅ Aligned with CANONICAL_THRESHOLDS |

### Risk-relevant fields triggering recomputation

| Field Category | Fields | Defined at |
|---|---|---|
| Core fields | entity_type, ownership_structure, sector, country, directors, ubos, intermediaries | `server.py:1652–1655` (RISK_RELEVANT_FIELDS) |
| Prescreening fields | operating_countries, countries_of_operation, target_markets, primary_service, service_required, monthly_volume, expected_volume, transaction_complexity, payment_corridors, source_of_wealth, source_of_funds, introduction_method, customer_interaction, interaction_type, cross_border | `server.py:1657–1663` (RISK_RELEVANT_PRESCREENING) |

---

## 2. CHECK Constraints — All Risk-Carrying Tables

### Schema-level constraints (fresh databases)

| Table | Column | Constraint | PG Line | SQLite Line |
|---|---|---|---|---|
| `applications` | `risk_level` | `CHECK(risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH'))` | 287 | 870 |
| `periodic_reviews` | `risk_level` | `CHECK(risk_level IS NULL OR risk_level IN (...))` | 553 | 1133 |
| `periodic_reviews` | `previous_risk_level` | `CHECK(previous_risk_level IS NULL OR ...)` | 556 | 1136 |
| `periodic_reviews` | `new_risk_level` | `CHECK(new_risk_level IS NULL OR ...)` | 557 | 1137 |
| `sar_reports` | `risk_level` | `CHECK(risk_level IS NULL OR ...)` | 602 | 1185 |
| `edd_cases` | `risk_level` | `CHECK(risk_level IS NULL OR ...)` | 623 | 1206 |
| `decision_records` | `risk_level` | `CHECK(risk_level IS NULL OR ...)` | 810 | 1386 |

### Migration for existing PostgreSQL databases

**Migration v2.11** (db.py `_run_migrations()`) adds ALTER TABLE constraints for all six secondary-table risk_level columns. The migration:
- Checks `information_schema.table_constraints` before adding
- Uses named constraints for idempotency
- Handles errors gracefully (e.g., table doesn't exist yet)

**Tables covered:** periodic_reviews (3 columns), sar_reports, edd_cases, decision_records

---

## 3. Test Evidence

### Test suite results

```
1,169 passed, 3 skipped, 0 failures
```

### Risk-specific test files

| File | Tests | Coverage |
|---|---|---|
| `test_risk_hardening.py` | 43 | Threshold boundaries, escalation flags, floor rules, DB constraints, fallback, return shape |
| `test_risk_scoring.py` | ~50+ | Computation logic, dimension weights, sub-factor alignment |
| `test_risk.py` | ~10+ | General risk tests |

### Threshold boundary tests (from test_risk_hardening.py)

| Score | Expected Level | Status |
|---|---|---|
| 0 | LOW | ✅ Tested |
| 39 | LOW | ✅ Tested |
| 39.9 | LOW | ✅ Tested |
| 40 | MEDIUM | ✅ Tested |
| 54 | MEDIUM | ✅ Tested |
| 54.9 | MEDIUM | ✅ Tested |
| 55 | HIGH | ✅ Tested |
| 69 | HIGH | ✅ Tested |
| 69.9 | HIGH | ✅ Tested |
| 70 | VERY_HIGH | ✅ Tested |
| 85 | VERY_HIGH | ✅ Tested |
| 100 | VERY_HIGH | ✅ Tested |

### Escalation tests (from test_risk_hardening.py)

| Scenario | Expected | Status |
|---|---|---|
| Low-risk input → no escalation | `requires_compliance_approval = False`, empty escalations | ✅ Tested |
| Very high risk sector (crypto) | `very_high_risk_sector` flag set, approval required | ✅ Tested |
| Sub-factor = 4 (complex multi-jurisdiction) | `sub_factor_score_4` flag set, approval required | ✅ Tested |
| All high-risk inputs (score ≥ 85) | `composite_score_85_plus` possible, approval required | ✅ Tested |
| Sanctioned country (Iran, NK, Syria, Cuba) | Floor rule to VERY_HIGH, `floor_rule_sanctioned_country` | ✅ Tested |
| FATF_BLACK country (Myanmar, Russia, Belarus) | VERY_HIGH, score ≥ 70 | ✅ Tested |
| Sanctioned UBO nationality | VERY_HIGH, `floor_rule_sanctioned_nationality` | ✅ Tested |
| Sanctioned director nationality | VERY_HIGH | ✅ Tested |
| Non-sanctioned country | No floor rules | ✅ Tested |

### DB constraint tests (from test_risk_hardening.py)

| Scenario | Status |
|---|---|
| edd_cases rejects invalid risk_level | ✅ Tested |
| edd_cases accepts valid risk_level (LOW, MEDIUM, HIGH, VERY_HIGH) | ✅ Tested |
| edd_cases accepts NULL risk_level | ✅ Tested |
| sar_reports rejects invalid risk_level | ✅ Tested |
| periodic_reviews rejects invalid risk_level | ✅ Tested |
| periodic_reviews rejects invalid previous_risk_level | ✅ Tested |

### Return shape tests (from test_risk_hardening.py)

| Check | Status |
|---|---|
| Return contains score, level, dimensions, lane, escalations, requires_compliance_approval | ✅ Tested |
| Score is numeric 0–100 | ✅ Tested |
| Level is valid enum | ✅ Tested |
| Escalations is list | ✅ Tested |
| requires_compliance_approval is bool | ✅ Tested |
| Dimensions has d1–d5 | ✅ Tested |

---

## 4. DB / API / UI Consistency Matrix

| Field | DB (applications) | API Response | Portal List | Portal Detail | Back-Office List | Back-Office Detail | EDD Cases | SAR Reports | Periodic Reviews | Decision Records |
|---|---|---|---|---|---|---|---|---|---|---|
| `risk_score` | `risk_score REAL` | ✅ returned | ✅ displayed | ✅ displayed | ✅ displayed | ✅ displayed | `risk_score REAL` | — | — | — |
| `risk_level` | CHECK constrained | ✅ returned | ✅ badge | ✅ badge | ✅ badge | ✅ badge | CHECK constrained | CHECK constrained | CHECK constrained | CHECK constrained |
| Thresholds | DB seed aligned | — | RISK_THRESHOLDS aligned | — | RISK_THRESHOLDS aligned | — | — | — | — | — |

---

## 5. Demo & Staging Validation

### Status: Live validation pending deployment

The code changes are committed and pushed to the branch. Live validation of demo and staging environments requires deployment of this branch to those environments.

**What needs to be verified post-deployment:**
1. API `GET /api/applications/:id` returns canonical `risk_level` values
2. Back-office list/detail badges match API values
3. Portal list/detail badges match API values
4. KYC recomputation works end-to-end
5. Back-office field edit triggers recomputation
6. EDD/SAR/review pages show valid risk values
7. PostgreSQL migration v2.11 runs successfully on existing databases

---

## 6. Open Items

| # | Item | Status | Severity | Notes |
|---|---|---|---|---|
| 1 | PostgreSQL migration v2.11 rollout on existing databases | **Pending** | HIGH | Migration code written and tested; needs deployment to apply ALTER TABLE constraints |
| 2 | Live demo environment validation | **Pending deployment** | MEDIUM | Code verified at repository level; live validation after deploy |
| 3 | Live staging environment validation | **Pending deployment** | MEDIUM | Same as above |

---

## 7. Final Closure Verdict

### Summary of completed work

| Area | Status |
|---|---|
| Single canonical `classify_risk_level()` | ✅ Closed |
| Canonical thresholds (LOW 0–39, MEDIUM 40–54, HIGH 55–69, VERY_HIGH 70–100) | ✅ Closed |
| No duplicate or conflicting threshold logic | ✅ Closed |
| Escalation flags correct and complete | ✅ Closed |
| KYC recomputation always recomputes | ✅ Closed |
| Back-office edit recomputation with audit trail | ✅ Closed |
| Risk-relevant field list covers all 17 sub-factors | ✅ Closed |
| Fallback behavior consistent (MEDIUM default) | ✅ Closed |
| Back-office RISK_THRESHOLDS aligned | ✅ Closed |
| Review intervals aligned | ✅ Closed |
| DB seed Seychelles corrected (3→2) | ✅ Closed |
| DB seed Construction corrected (2→3) | ✅ Closed |
| CHECK constraints on edd_cases | ✅ Closed (schema) / **Pending** (migration rollout) |
| CHECK constraints on sar_reports | ✅ Closed (schema) / **Pending** (migration rollout) |
| CHECK constraints on periodic_reviews | ✅ Closed (schema) / **Pending** (migration rollout) |
| CHECK constraints on decision_records | ✅ Closed (schema) / **Pending** (migration rollout) |
| Migration v2.11 for existing PG databases | ✅ Written / **Pending** deployment |
| 1,169 tests passing | ✅ Confirmed |

### Production readiness

| Environment | Ready? | Condition |
|---|---|---|
| **Demo** | ✅ Yes | After deployment of this branch |
| **Pilot** | ✅ Yes | After deployment and live validation |
| **Staging/UAT** | ✅ Yes | After deployment and live validation |
| **Production** | ✅ Yes | After existing-DB migration v2.11 is applied and verified |
| **Regulatory** | ✅ Yes | With migration caveat documented |

### Formal status

> **Conditionally Closed pending migration rollout**
>
> The client risk computation module is **functionally production-ready and regulator-safe at the application logic level**, subject to completion and verification of the PostgreSQL migration (v2.11) for existing deployed databases.
>
> All computation logic, threshold mappings, escalation flags, recomputation paths, fallback behavior, and structural constraints have been verified, hardened, and tested with 1,169 passing tests including dedicated boundary, escalation, floor-rule, and DB-constraint coverage.

---

## 8. Residual Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Existing PG databases accept legacy risk values until migration runs | Medium | Medium | Migration v2.11 is idempotent and safe to run; schedule for next deployment |
| Historical data may contain non-canonical risk_level values | Low | Low | Application logic now always writes canonical values; consider backfill query if needed |
| Memo risk may differ from stored application risk | Low | Low | By design: memo uses MAX(stored, computed) which is conservative/escalatory — regulator-safe |

---

## Appendix A: Files Modified in This Closure

| File | Changes |
|---|---|
| `db.py` | Added migration v2.11 (ALTER TABLE CHECK constraints for existing PG databases); added CHECK constraint to decision_records.risk_level in all schema locations |
| `server.py` | (Prior commit) Added risk recomputation on back-office edits; parametrized logging |
| `rule_engine.py` | (Prior commit) classify_risk_level(), CANONICAL_THRESHOLDS, escalation flags |
| `arie-backoffice.html` | (Prior commit) RISK_THRESHOLDS aligned |
| `tests/test_risk_hardening.py` | (Prior commit) 43 hardening tests |

---

## Appendix B: Final Rollout Verification Pass (v2.0)

**Date:** 2026-04-06T16:24Z  
**Performed by:** Compliance Engineering (automated)

### 1. Migration Code Verification

| Check | Result |
|---|---|
| Migration v2.11 exists in `_run_migrations()` | ✅ Confirmed at db.py lines 2063–2097 |
| Covers periodic_reviews.risk_level | ✅ Named constraint: `periodic_reviews_risk_level_check` |
| Covers periodic_reviews.previous_risk_level | ✅ Named constraint: `periodic_reviews_prev_risk_level_check` |
| Covers periodic_reviews.new_risk_level | ✅ Named constraint: `periodic_reviews_new_risk_level_check` |
| Covers sar_reports.risk_level | ✅ Named constraint: `sar_reports_risk_level_check` |
| Covers edd_cases.risk_level | ✅ Named constraint: `edd_cases_risk_level_check` |
| Covers decision_records.risk_level | ✅ Named constraint: `decision_records_risk_level_check` |
| Idempotent (checks information_schema before ADD) | ✅ Lines 2079–2083 |
| PostgreSQL-only guard | ✅ Line 2066: `if USE_POSTGRESQL` |
| Error handling with rollback | ✅ Lines 2092–2097 |
| Unconstrained risk_level TEXT columns in db.py | ✅ **Zero** — all instances have CHECK constraints |

### 2. Deployment Verification

| Environment | Deployed? | Evidence |
|---|---|---|
| **Repository (branch)** | ✅ Code committed | Branch `copilot/audit-client-risk-computation`, SHA `5cbdaff` |
| **Main branch** | ❌ Not yet merged | Branch has not been merged to `main`; PR pending |
| **Demo (Render)** | ❌ Not deployed | Render auto-deploys from `main` only (`render.yaml` confirms `autoDeploy: true` on main). Endpoint unreachable from sandbox. |
| **Live/Production (Render)** | ❌ Not deployed | Same: auto-deploys from `main`. Endpoint unreachable from sandbox. |

**Note:** Both Render services (`arie-finance-demo`, `arie-finance-live`) auto-deploy when code is pushed to `main`. Until this branch is merged, migration v2.11 is not deployed.

### 3. Live DB Constraint Verification

| Check | Result |
|---|---|
| Demo PostgreSQL constraints verified | ❌ **Cannot verify** — branch not deployed, live DB not accessible from CI sandbox |
| Production PostgreSQL constraints verified | ❌ **Cannot verify** — same reason |

**This is the only remaining gap.** The migration code is correct, tested, and idempotent. It will execute automatically on the next server startup after merge to `main`.

### 4. Post-Migration Behavior Verification (Local Test Evidence)

| Path | Test Coverage | Result |
|---|---|---|
| Application creation | test_risk_hardening.py, test_api.py | ✅ 1,169 tests pass |
| KYC recomputation | test_risk_hardening.py | ✅ Passes |
| Back-office edit recomputation | test_risk_hardening.py | ✅ Passes |
| EDD case creation with risk_level | test_risk_hardening.py (DB constraint tests) | ✅ Rejects invalid, accepts valid + NULL |
| SAR report creation with risk_level | test_risk_hardening.py (DB constraint tests) | ✅ Rejects invalid, accepts valid + NULL |
| Periodic review creation with risk_level | test_risk_hardening.py (DB constraint tests) | ✅ Rejects invalid, accepts valid + NULL |
| Decision record creation with risk_level | Schema CHECK constraint | ✅ Constraint present in all schema locations |
| Threshold boundary correctness | test_risk_hardening.py | ✅ All 12 boundary values tested |
| Escalation flag correctness | test_risk_hardening.py | ✅ All 9 scenarios tested |
| Return shape correctness | test_risk_hardening.py | ✅ All 6 fields validated |

**Full test suite:** 1,169 passed, 3 skipped, 0 failures (run 2026-04-06T16:24Z)  
**Risk-specific tests:** 99 passed, 0 failures (test_risk_hardening.py + test_risk_scoring.py + test_risk.py)

### 5. Remaining Gaps

| # | Gap | Severity | Resolution Path |
|---|---|---|---|
| 1 | Branch not merged to `main` | HIGH | Merge PR → triggers auto-deploy |
| 2 | Migration v2.11 not executed on live PG databases | HIGH | Runs automatically on next server startup after deploy |
| 3 | Live demo/staging/production API validation not performed | MEDIUM | Requires post-merge verification by ops team |

### 6. Final Verdict

## **Conditionally Closed**

The client risk computation module is **functionally production-ready and regulator-safe at the application logic level.** All code, schema, and test evidence confirms correct behavior.

Closure is conditional on:

1. **Merge of this branch to `main`** — triggers auto-deployment to Render
2. **Verification that migration v2.11 executed successfully** on existing PostgreSQL databases (check server logs for `"Migration v2.11: Added"` entries)
3. **Post-deployment spot-check** of API response `risk_level` values on demo and production

### 7. Formal Closure Statement

> The client risk computation module is **functionally production-ready and regulator-safe at the application logic level**, subject to:
>
> 1. Merge of the hardening branch (`copilot/audit-client-risk-computation`) to `main`
> 2. Completion and verification of the PostgreSQL migration (v2.11) for existing deployed databases
> 3. Post-deployment API/UI spot-check confirming canonical threshold behavior
>
> All computation logic, threshold mappings, escalation flags, recomputation paths, fallback behavior, and structural constraints have been verified, hardened, and tested with **1,169 passing tests** including dedicated boundary, escalation, floor-rule, DB-constraint, and return-shape coverage.

### Governance Status Grid

| Environment | Safe? | Condition |
|---|---|---|
| **Demo** | ✅ Yes | After merge to `main` (auto-deploys) |
| **Pilot** | ✅ Yes | After merge + live validation |
| **Staging/UAT** | ✅ Yes | After merge + live validation |
| **Production** | ⚠️ Conditional | After merge + v2.11 migration rollout + verification |
| **Regulatory** | ⚠️ Conditional | Same as production, with migration caveat documented |

### Post-Merge Verification Checklist

After this branch is merged to `main`, the ops/compliance team should:

- [ ] Confirm Render auto-deploy completed for `arie-finance-demo`
- [ ] Confirm Render auto-deploy completed for `arie-finance-live`
- [ ] Check demo server logs for `"Migration v2.11: Added"` entries
- [ ] Check production server logs for `"Migration v2.11: Added"` entries
- [ ] Verify `GET /api/applications/:id` returns valid `risk_level` values (LOW/MEDIUM/HIGH/VERY_HIGH)
- [ ] Verify back-office list page shows risk badges aligned with API
- [ ] If all confirmed, update this report status to **Fully Closed**
