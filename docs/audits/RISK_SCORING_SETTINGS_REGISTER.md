# RegMind Risk Scoring Settings Register

**Audited commit:** `c83f0107c3227e538d2ad30f063c9cf14172221c`
**Inventory basis:** seeded `risk_config` row `id=1` plus operative rules in [`rule_engine.py`](../../arie-backend/rule_engine.py), [`edd_routing_policy.py`](../../arie-backend/edd_routing_policy.py), [`security_hardening.py`](../../arie-backend/security_hardening.py) and [`server.py`](../../arie-backend/server.py)
**Audit date:** 13 July 2026

## 1. Register scope and count

This register records both configurable settings and hardcoded decision rules. The canonical seeded database contains **204 configured entries** under this convention:

| Setting class | Count |
|---|---:|
| Dimension weights | 5 |
| Subcriterion weights | 17 |
| Threshold bands | 4 |
| Country score keys | 102 |
| Sector score keys | 53 |
| Entity-type score keys | 23 |
| **Total** | **204** |

Each threshold band is counted once even though it has `min` and `max`. Rule constants, aliases, parser branches, floors, lanes, escalation codes and missing-value behaviours are registered separately and not added to 204. The live database may differ because admins can edit the row; this is the seeded configuration at the audited commit, not a production export.

## 2. Storage, precedence and lifecycle

| Field | Storage | Runtime precedence | Mutation/governance |
|---|---|---|---|
| `dimensions` | JSONB (PostgreSQL) / TEXT JSON (SQLite) | DB, then hardcoded defaults | Admin PUT directly updates row `id=1`. |
| `thresholds` | JSONB / TEXT JSON | DB, then `CANONICAL_THRESHOLDS` | Runtime uses `min`; `max` is ignored. |
| `country_risk_scores` | JSONB / TEXT JSON | DB score map; hardcoded/manual lists for missing key/fallback | Manual map is active source; country snapshot governance is not active in scoring. |
| `sector_risk_scores` | JSONB / TEXT JSON | DB map; hardcoded `SECTOR_SCORES` fallback | First substring match wins. |
| `entity_type_scores` | JSONB / TEXT JSON | DB map; local hardcoded map fallback | First substring match wins. |
| `updated_at` | timestamp/text | Serialized as `risk_config:<updated_at>` on application | Timestamp identifier only, not immutable model content. |
| `updated_by` | text | Not used by scorer; omitted by GET response | Admin actor is also captured in audit trail. |

In staging/production, database read failure, missing row or **shape-validation** error raises `RiskConfigUnavailable`. In development/test/demo the loader returns `None` or nulls a malformed map and hardcoded fallbacks apply. There are no risk-model-specific environment variables for weights/thresholds. `ENVIRONMENT` controls the fail-closed posture.

The canonical row has no `model_id`, semantic version, content hash, status, effective date, expiry/review date, jurisdiction/regulator, tenant, change request, approver, validator, regulatory basis or rollback pointer. Updates recompute non-terminal active applications. Approved/rejected/withdrawn records are excluded. Existing manual values are preserved when seed repair adds missing defaults.

## 3. Dimension and subcriterion settings

| ID | Dimension | Weight | Subcriteria (weight within dimension) |
|---|---|---:|---|
| D1 | Customer / Entity Risk | 30% | Entity Type 20%; Ownership Structure 20%; PEP Status 25%; Adverse Media 15%; Source of Wealth 10%; Source of Funds 10% |
| D2 | Geographic Risk | 25% | Country of Incorporation 25%; UBO Nationalities 20%; Intermediary Shareholder Jurisdictions 20%; Countries of Operation 20%; Target Markets 15% |
| D3 | Product / Service Risk | 20% | Service Type 40%; Monthly Volume 35%; Transaction Complexity 25% |
| D4 | Industry / Sector Risk | 15% | Industry Sector 100% |
| D5 | Delivery Channel Risk | 10% | Introduction Method 50%; Delivery Channel 50% |

All five dimension weights sum to 100; each dimension's subweights sum to 100. The source test header incorrectly refers to 18 subfactors; there are 17.

## 4. Normalization and bands

```text
dimension_score = Σ(subfactor_score × subfactor_weight)
weighted_average = D1×0.30 + D2×0.25 + D3×0.20 + D4×0.15 + D5×0.10
base_score = round(((weighted_average - 1) / 3) × 100, 1)
```

| Band | Configured min | Configured max | Runtime rule |
|---|---:|---:|---|
| LOW | 0 | 39.9 | initial tier; selected until a greater/equal `min` is reached |
| MEDIUM | 40 | 54.9 | score ≥40 |
| HIGH | 55 | 69.9 | score ≥55 |
| VERY_HIGH | 70 | 100 | score ≥70 |

Runtime sorts by `min` and selects the last band for which `score >= min`. It does not check `max`. Reproduced consequences: 39.99 is Low, 54.99 is Medium and 101 is Very High. The loader validator verifies the four level names are present but does not validate numeric types, 0–100 range, order, continuity or overlap. The admin endpoint does additional numeric/range/order checks but is not the validator used to trust a loaded row.

## 5. Country score map — complete seeded register

Matching uses normalized lower-case names and a small alias/prefix normalizer. A known configured key returns the manual setting. A missing/unknown/blank country returns 2. Several demonyms are mapped for director/UBO nationality; unlisted demonyms are used as-is and can fall to 2.

| Score | Count | Keys |
|---:|---:|---|
| 1 | 29 | australia; canada; france; germany; hong kong; ireland; japan; luxembourg; netherlands; new zealand; singapore; switzerland; united kingdom; united states; austria; belgium; denmark; finland; norway; sweden; south korea; israel; iceland; italy; portugal; spain; taiwan; uk; usa |
| 2 | 31 | bahrain; botswana; brazil; chile; china; india; indonesia; kuwait; malaysia; mauritius; mexico; morocco; oman; qatar; rwanda; saudi arabia; turkey; uae; uganda; ghana; ivory coast; jordan; sri lanka; tunisia; jersey; guernsey; isle of man; liechtenstein; estonia; pakistan; seychelles |
| 3 | 25 | algeria; burkina faso; cameroon; democratic republic of congo; haiti; kenya; laos; lebanon; mali; monaco; mozambique; nigeria; philippines; senegal; south africa; south sudan; tanzania; venezuela; vietnam; yemen; bermuda; vanuatu; samoa; marshall islands; iraq |
| 4 | 17 | iran; north korea; myanmar; russia; syria; belarus; cuba; crimea; afghanistan; somalia; libya; eritrea; sudan; bvi; british virgin islands; cayman islands; panama |

### 5.1 Operative country rules

- incorporation score 4, or hardcoded `SANCTIONED`/`FATF_BLACK`, forces Very High and score ≥70;
- director/UBO nationality score 4, or hardcoded `SANCTIONED`/`FATF_BLACK`, forces Very High and score ≥70;
- incorporation score 3+ forces at least High and score ≥55;
- director/UBO, intermediary, operations and target values otherwise contribute through D2 weighting only;
- intermediary jurisdictions in the hardcoded secrecy set are boosted to 4 even where their configured country score is lower;
- missing operations/target markets reuse incorporation risk; no intermediary or no party nationality scores 1;
- unknown country scores 2 and records provenance as fallback.

### 5.2 Current-source variance at audit date

The active seed has no publication date. Compared with FATF's 19 June 2026 increased-monitoring list, it misses or under-classifies current jurisdictions including Angola, Bolivia, Bosnia and Herzegovina, Bulgaria, Côte d'Ivoire, Kuwait, Nepal and Papua New Guinea; it retains several removed jurisdictions at 3. This register records implementation, not an endorsement of those scores.

## 6. Sector score map — complete seeded register

The matcher lowercases the full label and returns the **first configured key contained anywhere in it**. It is not exact or longest-match. Missing/unmapped sector returns 2.

| Score | Count | Keys in operative insertion order |
|---:|---:|---|
| 1 | 6 | regulated financial; government; bank; listed company; agriculture; education |
| 2 | 12 | healthcare; technology; software; saas; manufacturing; retail; e-commerce; media; logistics; insurance; telecommunications; banking |
| 3 | 24 | construction; import; export; real estate; mining; oil; gas; energy; money services; forex; precious; non-profit; ngo; charity; advisory; management consulting; consulting; financial / tax advisory; fintech; e-money; legal; accounting; shipping; maritime |
| 4 | 11 | crypto; virtual asset; gambling; gaming; betting; arms; defence; military; shell company; nominee; precious metals |

High-risk sector is true when the resolved score is 4 or a hardcoded keyword matches: `crypto`, `virtual asset`, `gambling`, `casino`, `arms`, `weapons`, `defence`, `military`, `shell company`, `nominee service`. High-risk sector applies a High/55 floor.

### 6.1 Verified sector collisions and gaps

| Label | Actual | Reason | Configured/sensible candidate |
|---|---:|---|---:|
| Precious Metals / Gems | 3 | `precious` precedes `precious metals` | 4 |
| Banking-as-a-Service | 1 | `bank` precedes `banking` | 2 |
| Private Banking | 1 | `bank` match; no precise canonical entry | policy decision required |
| Crypto / Digital Assets Exchange | 4 | `crypto` | 4 |
| Education / EdTech | 1 | `education` | 1 (DB), while hardcoded fallback says education 2 |
| Unknown | 2 | default | policy decision required |

The hardcoded fallback and database seed differ for education and contain different coverage. The database is canonical when available.

## 7. Entity-type score map — complete seeded register

The matcher lowercases the label and returns the first configured key contained anywhere in it. Missing/unmapped entity type returns 2.

| Score | Count | Keys in operative insertion order |
|---:|---:|---|
| 1 | 9 | listed company; regulated financial institution; regulated fi; regulated entity; government; government body; public sector; listed; regulated |
| 2 | 5 | large private company; large private; sme; private company; regulated fund |
| 3 | 5 | newly incorporated; trust; foundation; ngo; non-profit |
| 4 | 4 | unregulated fund; spv; shell company; shell |

### 7.1 Verified entity collisions

| Portal label | Actual | Reason | Explicit configured intent |
|---|---:|---|---:|
| Unregulated Fund / SPV | 1 | substring `regulated` matches before `unregulated fund`/`spv` | 4 |
| Regulated Fund (CIS / Licensed) | 1 | `regulated` matches before `regulated fund` | 2 |
| Listed Company on Regulated Exchange | 1 | `listed company` first | 1 |
| Unknown | 2 | default | policy decision required |

## 8. Non-map factor settings

### 8.1 D1 Customer / Entity

| Factor | Score logic and default |
|---|---|
| Ownership | contains `simple` →1; `1-2` →2; `3+` →3; `complex` →4; otherwise 2. First match wins. |
| PEP | explicit declaration/officer verification true or status `declared_yes`/`confirmed_pep`; explicit negative wins; legacy `is_pep` only without declaration metadata. Domestic/unspecified PEP →3; foreign/international →4; none →1. Any PEP floors High. |
| Adverse media | confirmed/regulatory/criminal →4; minor/unsubstantiated →2; clear/none/no, unknown status or no data →1. Material keywords also create a screening High floor. |
| Source of wealth | business revenue/trading profits/investment/dividends/government funding/grants →1; sale of assets/property/venture capital/investor funding →2; inheritance/family wealth/loan/credit/other →3; missing/unknown →3; otherwise 2. First match wins. |
| Source of funds | company bank/parent/group/client payments/receivables/revenue/business operations →1; shareholder/director/capital injection/investment round/fundraise/sale of assets →2; loan/credit facility/other →3; missing/unknown →3; otherwise 2. First match wins. |

### 8.2 D2 Geographic

| Factor | Score logic and default |
|---|---|
| Incorporation | country map; missing/unknown 2. |
| UBO/director nationality | maximum normalized nationality; no recorded nationality 1. |
| Intermediary jurisdiction | maximum country score, secrecy hardcoded boost to 4; no intermediary 1. |
| Operations | maximum country score; missing copies incorporation. |
| Target markets | maximum country score; missing copies incorporation. |

### 8.3 D3 Product / Service

| Factor | Score logic and default |
|---|---|
| Service type | domestic + single →1; multi-currency →2; cross-border/international or `cross_border` flag →3; otherwise 2. Duplicate cross-border branch has no additional effect. |
| Monthly volume | contains `over`, `5,000,000`, `5000000` or `> 5` →4; else `500,000`/`500000` →3; else `50,000`/`50000` →2; else `under`/`< 50`/`below` →1; otherwise 2. DCI-110 arises from overlap. |
| Complexity/corridor | simple/single currency/domestic →1; standard/multi-currency →2; complex/multiple international →3; very complex/high-risk corridor →4; otherwise cross-border inference can return 2–4; default 2. DCI-108 arises because `complex` precedes `very complex`. |

### 8.4 D4 Sector

Uses the map and High-floor logic in section 6.

### 8.5 D5 Delivery Channel

| Factor | Score logic and default |
|---|---|
| Introduction | direct →1; regulated →1; non-regulated →3; unsolicited →4; otherwise 2. First match wins; DCI-109 makes `non-regulated` resolve as regulated/1. |
| Interaction | face-to-face/in-person →1; video →2; non-face/remote →2, or 3 when incorporation country score ≥3; anonymous/unverified →4; otherwise 2. Earlier conditions win. |

## 9. Screening and disposition settings

`compute_risk_score` treats these as material screening concern keywords:

- adverse media status containing confirmed, regulatory, criminal, serious or material;
- sanctions status/result containing match, hit, positive, adjacent or unresolved;
- PEP status/result containing confirmed, material, serious, high or unresolved;
- a non-clear explicit `screening_concern`.

One concern floors High. Two screening reason strings, or high-risk sector + elevated incorporation jurisdiction + a concern, floors Very High. A provider error, pending/no-result state or unknown adverse status is not itself a score penalty.

During recomputation, formal review dispositions add separate rules:

| Disposition/state | Minimum | Route/effect |
|---|---|---|
| `true_match` | High | EDD; approval remains screening-blocked until decisionable resolution. |
| `material_concern` | High | EDD. |
| `escalated_to_edd` | High | EDD. |
| `needs_more_information` | Medium | EDD; raw numeric score may stay below 40. |
| Raw completed match without formal clearance | High | EDD. |
| `false_positive_cleared` with required reviewers | none | Concern can be cleared. |

## 10. EDD routing settings

Policy version is `v1`. Deterministic EDD triggers include:

- final High/Very High;
- declared PEP;
- sector tier high/very high or virtual-asset/crypto indicator;
- elevated incorporation/jurisdiction facts;
- opaque/incomplete ownership;
- terminal/material screening match;
- relevant mandatory supervisor escalation;
- explicit EDD flags;
- incomplete routing contract/data.

High-risk/PEP/sector/jurisdiction/ownership/screening triggers generally require at least High. Other route-only triggers can require Medium. Routing is separate from score computation and is applied during submission/recompute. Some failure paths log and continue.

## 11. Floors, lanes and approval settings

| Final tier | Numeric floor | Lane | Approval posture |
|---|---:|---|---|
| LOW | 0 | Fast Lane | direct only when no escalation reason/control gate |
| MEDIUM | raw score retained | Standard Review (or EDD if separately routed) | direct only when no escalation; otherwise compliance |
| HIGH | 55 | EDD | dual control; two distinct authorized approvers |
| VERY_HIGH | 70 | EDD | dual control; two distinct authorized approvers |

Terminal authority includes admin/SCO/CO with restrictions. CO cannot approve High/Very High or escalated Low/Medium. AI override is limited to admin/SCO. Prohibited jurisdictions, unresolved screening, IDV/readiness, supervisor/memo and other integrity gates can block regardless of numeric tier.

## 12. Staleness and provenance settings

| Value/state | Behaviour |
|---|---|
| `risk_config:<updated_at>` matches current | passes config staleness check |
| known non-empty old version | approval blocked |
| `stale:recompute_failed` | approval blocked with quarantine reason |
| `stale:cm_recompute_pending:<id>` | approval blocked |
| config lookup failure | approval fails closed |
| blank/missing application version | explicitly allowed for legacy records |
| no current config version | staleness comparison does not block |

Memo metadata is compared to current application score/tier. Mismatch marks memo stale and blocks approval/export where memo is required. A legacy memo without a risk snapshot does not mismatch.

## 13. Recompute register

| Trigger | Recompute? | Notes |
|---|---|---|
| Initial prescreening submission | Yes | Score before background provider result; later screening can recompute. |
| Material application/prescreening edit | Yes | Controlled paths overlay corrected fields. |
| Officer correction | Yes | Risk-relevant fields only. |
| KYC submission | Yes | Refresh before downstream decision. |
| Screening completion/rerun/review disposition | Yes | Formal disposition floors applied. |
| Risk-config activation | Yes, active non-terminal cases | Failed cases stamped stale; terminal historic cases excluded. |
| Change-management implementation | Yes when risk relevant | Pending sentinel protects inter-transaction gap. |
| Periodic review suggestion | No | Audit-only suggestion; deliberately does not mutate application risk. |
| Periodic review completed with confirmed change | Yes | No automatic downgrade; officer/model/prior maximum. |
| Monitoring alert creation/upsert | No | May drive lifecycle/EDD/review, but broad risk drift is not active in the pilot. |

## 14. Known remediation references assessed

| Reference | Current condition | Audit impact | Implemented here? |
|---|---|---|---|
| DCI-108 | `Very complex` resolves 3, not 4 | Can under-score; jointly crosses a tier with DCI-109 | No |
| DCI-109 | `non-regulated` resolves 1, not 3 | Can under-score and suppress channel risk | No |
| DCI-110 | middle volume label resolves 4, not 3 | Over-scores and adds mandatory escalation | No |
| DCI-009 | unknown country defaults 2 | Missing-data policy decision remains | No |
| `PR-RISK-SECTOR-CALIBRATION-1` | sector taxonomy/calibration paused pending policy | Required before mapping correction; RSM-01 adds collision evidence | No |

## 15. Required controls for the future register

Every active model version should add: immutable ID/hash; owner; independent validator; approvers; legal/regulatory basis; source URLs/publication dates; tenant/regulator scope; effective/expiry dates; complete settings; controlled option IDs and aliases; missing-data policy; test/golden-set result; expected distribution/impact; change request; migration/rollback; monitoring thresholds; exception records; and superseded-version linkage.
