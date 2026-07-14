# RSMP Tier 0A Post-Approval Read-Only Dry Run

**Founder / accountable executive:** Aisha Sudally

**Approval date:** 2026-07-14

**Review status:** `HOLD — FOUNDER REVIEW OF REVISED DRY RUN REQUIRED`

**Activation status:** `OFF — DRAFT PRS; NO MERGE, DEPLOYMENT, ACTIVATION, OR RECOMPUTATION`

**Canonical Markdown SHA-256:** `8ab7b191b1f246dcc063cb2d07deb4ba5c2e9d1039ea0bcd07ba2924c9d0763f`

## 1. Executive result

The founder-approved 77-row exact-alias catalogue and six signed score-contract corrections were replayed read-only against the same 640 active scored applications used for the pre-approval run. The application keys, fixture split, live risk-config version, and legacy results matched exactly. The PostgreSQL transaction was read-only and recorded zero database writes.

The post-approval proposal produced 118 score deltas against legacy behavior, no tier delta, no EDD-route delta, and 592 approval-route blocks caused by unresolved mappings. Comparing the pre-approval and post-approval proposals directly, 40 application scores changed, 20 applications cleared their last unresolved mapping and became routable, 790 field-level sentinels were removed, no sentinel was acquired, and every score change was explained.

This report does not authorize merge, deployment, activation, production use, or Tier 0C recomputation. It is not a production-readiness claim.

## 2. Pinned replay evidence

| Control | Pre-approval replay | Post-approval replay | Result |
|---|---|---|---|
| Active scored applications | 640 | 640 | Exact match |
| Nonfixture / fixture | 365 / 275 | 365 / 275 | Exact match |
| Application keys | Pinned set | Same pinned set | Exact match |
| PostgreSQL mode | Transaction read only | Transaction read only | Exact match |
| Database writes | 0 | 0 | Exact match |
| Risk-config version | `risk_config:2026-07-13 07:15:16.941658` | `risk_config:2026-07-13 07:15:16.941658` | Exact match |
| Legacy result comparison | Baseline | 0 mismatches | Exact match |
| Activation default | OFF | OFF | Unchanged |

The replay ran against staging data in a `READ ONLY` database transaction. It did not update staging configuration or application records. Temporary runtime files were written only under the running container's `/tmp` directory.

## 3. Required pre/post metrics

The score, tier, approval-route, and EDD-route rows compare each proposal with the unchanged legacy calculation. “Unresolved cases” are applications containing an unresolved field in that family; each family occurs at most once per application.

| Metric | Pre-alias result | Post-approval result | Change |
|---|---:|---:|---:|
| Active applications | 640 | 640 | 0 |
| Score deltas vs legacy | 83 | 118 | +35 |
| Tier deltas vs legacy | 0 | 0 | 0 |
| EDD-route deltas vs legacy | 0 | 0 | 0 |
| Approval-route changes vs legacy | 612 | 592 | -20 |
| Unresolved sector cases | 564 | 527 | -37 |
| Unresolved entity-type cases | 569 | 278 | -291 |
| Blank-country cases | 13 | 13 | 0 |
| Quarantined catalogue rows | 105 | 105 | 0 |
| Rejected synthetic/test rows | 9 | 9 | 0 |

The earlier 107-row quarantine count was corrected before alias approval: `Investment Management` and `Cloud Services` moved into the approved sector catalogue, leaving 105 genuine quarantine rows. Reintroducing those two labels into quarantine would contradict the later founder-approved Gate 0 correction.

## 4. Score and route impact

### 4.1 Post-approval proposal versus legacy

| Impact | Nonfixture | Fixture | Total |
|---|---:|---:|---:|
| Score decreased | 106 | 3 | 109 |
| Score increased | 6 | 3 | 9 |
| Score unchanged | 253 | 269 | 522 |
| Tier upgraded | 0 | 0 | 0 |
| Tier downgraded | 0 | 0 | 0 |
| Approval route changed | 323 | 269 | 592 |
| EDD route changed | 0 | 0 | 0 |

All 640 tiers remained stable: 502 `LOW`, 115 `HIGH`, and 23 `VERY_HIGH`.

### 4.2 Pre-approval proposal versus post-approval proposal

| Impact | Nonfixture | Fixture | Total |
|---|---:|---:|---:|
| Score decreased | 31 | 0 | 31 |
| Score increased | 6 | 3 | 9 |
| Score changed | 37 | 3 | 40 |
| Tier upgraded | 0 | 0 | 0 |
| Tier downgraded | 0 | 0 | 0 |
| EDD route changed | 0 | 0 | 0 |

Approval-route transitions were:

| Pre-approval route | Post-approval route | Applications |
|---|---|---:|
| blocked | blocked | 592 |
| blocked | direct_low_medium | 11 |
| blocked | dual_control_required | 9 |
| direct_low_medium | direct_low_medium | 24 |
| dual_control_required | dual_control_required | 4 |

Exactly 20 applications cleared their final unresolved mapping and became routable. No application moved from a routable state into `blocked`. EDD routing was unchanged for every application: 138 remained `edd` and 502 remained `standard`.

## 5. Sentinel reconciliation

| Sentinel event | Nonfixture applications | Fixture applications | Total applications |
|---|---:|---:|---:|
| One or more sentinels acquired | 0 | 0 | 0 |
| One or more sentinels removed | 299 | 216 | 515 |

| Family | Field-level removals | Field-level acquisitions |
|---|---:|---:|
| complexity | 42 | 0 |
| entity type | 291 | 0 |
| introduction | 53 | 0 |
| monthly volume | 46 | 0 |
| ownership | 321 | 0 |
| sector | 37 | 0 |
| **Total** | **790** | **0** |

Applications can have several independent family sentinels. Removing one mapped field left every unrelated sentinel intact; 592 applications therefore remain blocked until all unresolved mappings are cleared.

## 6. Remaining unresolved mappings

| Family | Pre-approval | Post-approval nonfixture | Post-approval fixture | Post-approval total | Change |
|---|---:|---:|---:|---:|---:|
| complexity | 608 | 298 | 268 | 566 | -42 |
| entity type | 569 | 174 | 104 | 278 | -291 |
| incorporation country | 13 | 12 | 1 | 13 | 0 |
| introduction | 584 | 262 | 269 | 531 | -53 |
| monthly volume | 534 | 226 | 262 | 488 | -46 |
| ownership | 574 | 75 | 178 | 253 | -321 |
| sector | 564 | 281 | 246 | 527 | -37 |

The family totals overlap because one application may be unresolved in several families. Overall, 323 nonfixture and 269 fixture applications remain blocked, for 592 unresolved applications.

## 7. Alias, quarantine, and reject controls

| Catalogue | Rows | Active replay occurrences | Nonfixture | Fixture | Incorrect resolutions |
|---|---:|---:|---:|---:|---:|
| Founder-approved exact aliases | 77 | 785 | 514 | 271 | 0 |
| Quarantined ambiguous labels | 105 | 1,128 | 620 | 508 | 0 mapped |
| Rejected synthetic/test labels | 9 | 37 | 17 | 20 | 0 mapped |

Every one of the 77 approved rows occurred in the pinned population and resolved to its exact approved canonical label and score. Every one of the 105 quarantine rows occurred and remained unresolved. Every one of the nine rejected rows occurred and remained unresolved. No fuzzy or substring match, synthetic mapping, quarantine mapping, or silent score-2 fallback was observed.

### 7.1 Top remaining quarantined labels

| Rank | Row ID | Family | Legacy label | Nonfixture | Fixture | Total |
|---:|---|---|---|---:|---:|---:|
| 1 | B-062 | sector | Technology | 142 | 142 | 284 |
| 2 | B-014 | entity type | Private Company | 117 | 54 | 171 |
| 3 | B-032 | monthly volume | 0-50000 | 42 | 74 | 116 |
| 4 | B-063 | sector | Consulting | 9 | 42 | 51 |
| 5 | B-015 | entity type | company | 3 | 42 | 45 |
| 6 | B-001 | complexity | Low | 29 | 0 | 29 |
| 7 | B-046 | ownership | Direct ownership | 5 | 22 | 27 |
| 8 | B-033 | monthly volume | 10000 | 1 | 22 | 23 |
| 9 | B-064 | sector | Consulting and advisory services | 20 | 0 | 20 |
| 10 | B-016 | entity type | Private Limited Company | 20 | 0 | 20 |
| 11 | B-065 | sector | financial_services | 0 | 17 | 17 |
| 12 | B-066 | sector | Technology Services | 14 | 0 | 14 |
| 13 | B-067 | sector | Professional Services | 13 | 0 | 13 |
| 14 | B-047 | ownership | Closely held | 12 | 0 | 12 |
| 15 | B-034 | monthly volume | 50000 | 12 | 0 | 12 |
| 16 | B-048 | ownership | Transparent ownership; one natural-person UBO | 0 | 12 | 12 |
| 17 | B-035 | monthly volume | 1m+ | 1 | 10 | 11 |
| 18 | B-068 | sector | Professional services | 1 | 9 | 10 |
| 19 | B-069 | sector | Software / Technology | 9 | 0 | 9 |
| 20 | B-049 | ownership | Single UBO owns 100% | 9 | 0 | 9 |

## 8. Explained delta classes

The direct pre-to-post comparison found 40 score-changing applications in the following complete set of classes. Every class is attributable to founder-approved exact resolution or the approved entity-type score contract.

| Direction and explanation | Applications |
|---|---:|
| decrease — resolved complexity + entity type + introduction + monthly volume + ownership | 2 |
| decrease — resolved complexity + introduction + monthly volume | 3 |
| decrease — resolved entity type + ownership | 2 |
| decrease — resolved introduction + monthly volume + ownership | 14 |
| decrease — resolved introduction + monthly volume + ownership + sector | 2 |
| decrease — resolved monthly volume | 2 |
| decrease — resolved ownership | 5 |
| decrease — approved entity-type score contract | 1 |
| increase — resolved complexity | 2 |
| increase — resolved complexity + introduction + monthly volume + ownership | 1 |
| increase — resolved complexity + monthly volume | 1 |
| increase — resolved complexity + sector | 1 |
| increase — resolved entity type + ownership | 1 |
| increase — resolved monthly volume + ownership | 1 |
| increase — resolved sector | 2 |
| **Explained total** | **40** |
| **Unexplained remainder** | **0** |

Against unchanged legacy behavior, the 118 post-approval score deltas reconcile as 78 pre-existing Tier 0A changes, 35 newly approved changes, and five applications containing both pre-existing and newly approved changes. No application is unexplained.

## 9. Policy-specific checks

- A9 remains rename-only: `Complex multi-jurisdiction / opaque structure` resolves to `Opaque — UBOs cannot be fully identified` at score 4. Its existing ownership High floor and EDD behavior are unchanged.
- `Unsolicited / unknown referral source` scores 4 and affects only the weighted score. It creates no automatic High floor and does not emit `monthly_volume_score_4`.
- `Private Banking` resolves to sector score 4 behind the OFF flag and uses the existing sector-score-4 High floor. It is not exposed as a new portal option before deliberate activation.
- The six approved contract values resolve at their signed scores behind the OFF flag without overwriting staging configuration.
- PEP runtime behavior is unchanged in PR #753. Implementing the all-roles-score-4 policy would materially expand the reviewed six-family C9 boundary, so it remains a separate narrowly scoped PR HOLD item.

## 10. Activation and review hold

The activation flag remains OFF by default. Merge-to-main alone cannot activate these mappings because both the resolver and its callers require the explicit flag. No schema, migration, staging-config mutation, deployment, activation, merge, or recomputation was performed.

Founder review of this revised dry-run report is required before any deliberate activation decision. PR #753 and stacked PR #755 must remain draft and unmerged. PR #755 must later be rebased onto the final merged PR #753 main SHA before its final review.

## 11. Canonical hash method

The recorded SHA-256 covers the entire UTF-8 Markdown file with LF line endings after replacing the 64-hex value on the `Canonical Markdown SHA-256` line with the literal `{{CANONICAL_SHA256}}`.
