# RSMP Gate 0 v4 — Founder Approval

**Decision label:** Founder-approved Gate 0 v4

**Founder / accountable executive:** Aisha Sudally

**Approval date:** 2026-07-14

**GitHub baseline SHA:** `02eeae5062d1f1d8f77e7ca69c4629bac72c57b0`

**Canonical Markdown SHA-256:** `33cdcaac5f01ba431776a4b8a300aee4cb6e48f0d585d9c1665c726d655f66f0`

**Operational status:** `HOLD — DRAFT PRS; FLAG OFF; NO MERGE, DEPLOYMENT, ACTIVATION, OR RECOMPUTATION`

## 1. Scope, authority, and supersession

This is the Founder-approved Gate 0 v4 decision record for the RSMP Tier 0A′ and Tier 0B scope only. It supersedes the prior D01–D12 / eight-signatory Gate 0 record only for Tier 0A′/0B. It does not supersede that record for Tier 1B geography, later model work, production release governance, or any topic expressly deferred below.

This record approves the exact decision catalogue in Sections 2–5 and all 77 exact C4 legacy aliases in `RSMP_TIER0A_FOUNDER_ALIAS_DECISIONS.md`. The remaining 105 ambiguous rows stay quarantined and all nine synthetic/test markers stay rejected. The earlier 107-row quarantine count was the pre-correction count; `Investment Management` and `Cloud Services` were subsequently removed from quarantine by the founder-approved Gate 0 correction, leaving 105 actual quarantine rows. They must not be re-quarantined.

Approval of this record is not a production-readiness claim. PR #753 and stacked PR #755 remain draft; the activation flag remains OFF; merge, deployment, activation, and Tier 0C recomputation are outside this approval.

## 2. Full Gate 0 v4 decision table

| Decision | Founder-approved disposition |
|---|---|
| A1 | The approved Tier 0A sector catalogue consists only of the 39 exact labels and scores in Section 3.1. The remaining 22 current portal sector labels are Lane B and remain unresolved/fail-closed; no score is inferred. `Private Banking` is approved at score 4 and the existing sector-score-4 High floor applies. |
| A2 | The approved Tier 0A entity-type catalogue consists only of the 12 exact labels and scores in Section 3.2. |
| A3 | The approved Tier 0A ownership catalogue consists only of the four exact labels and scores in Section 3.3, including the A9 rename-only decision. |
| A4 | The approved Tier 0A transaction-complexity catalogue consists only of the four exact labels and scores in Section 3.4. |
| A5 | The approved Tier 0A introduction catalogue consists only of the four exact labels and scores in Section 3.5. |
| A6 | The approved Tier 0A monthly-volume catalogue consists only of the four exact labels and scores in Section 3.6. The score-4 band is subject to C2. |
| A7 | Every declared portal PEP role/type in Section 3.7 scores 4. This is an approved decision, but the current runtime gap must remain a HOLD item until separately implemented, reviewed, and replayed. |
| A9 | Rename only: `Opaque — UBOs cannot be fully identified` scores 4; the former label is an exact legacy alias; the existing ownership floor and EDD route are preserved; no new option or transparent/opaque split is introduced. |
| C2 | Monthly-volume score 4 uses only `monthly_volume_score_4`; it requires compliance review and introduces no automatic High tier floor. Generic score-4 evidence from sector, PEP, ownership, or other factors must not trigger the volume rule. |
| C4 | The 77 exact legacy alias rows in the founder decision artifact are approved for flag-gated runtime implementation. The 105 remaining ambiguous labels are `QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED`; all nine synthetic/fixture markers are rejected, not mapped. No fuzzy or substring matching is approved. |
| C9 | Tier 0A controlled-value migration applies only to sector, entity type, ownership, complexity, introduction, and monthly volume. It does not automatically apply to deferred countries or regions. |
| D1a | Geography is limited to the three exact aliases in Section 4 plus blank/missing incorporation country as fail-closed unresolved. All other country scoring and all regions remain deferred to Tier 1B with the pilot manual FATF check. |
| S1 | Unresolved sentinels use `stale:unmapped_<family>:<short_hash>`; raw labels are not embedded. Structured evidence is separate, coexistence is supported, and approval remains blocked until every unresolved mapping is cleared. |
| X1 | Merge-to-main must not silently activate changed scoring. The staging activation flag remains OFF by default; activation requires live/code/Gate 0 comparison, read-only replay, founder review, deliberate activation, and only then a separately approved Tier 0C recomputation. |

## 3. Approved exact controlled-value catalogue

### 3.1 Sector — exact labels and scores

| Exact portal label | Score |
|---|---:|
| Fintech / Payments | 3 |
| Forex / FX Trading (Retail) | 3 |
| Forex / FX Trading (Institutional) | 3 |
| Crypto / Digital Assets Exchange | 4 |
| Crypto / Digital Assets Custody | 4 |
| Crypto / Web3 / DeFi | 4 |
| Remittance / Money Transfer | 3 |
| E-Money / E-Wallet Provider | 3 |
| Insurance / InsurTech | 2 |
| Investment Management | 3 |
| Family Office / Wealth Management | 3 |
| Private Banking | 4 |
| Banking-as-a-Service | 2 |
| E-Commerce / Online Retail | 2 |
| Import / Export | 3 |
| Precious Metals / Gems | 3 |
| Oil & Gas / Energy Trading | 3 |
| Logistics / Freight Forwarding | 2 |
| Software / SaaS | 2 |
| Cloud Services | 2 |
| Telecommunications | 2 |
| Media Technology | 2 |
| iGaming / Online Gambling | 4 |
| Online Casino / Sports Betting | 4 |
| NFT / Gaming Assets | 4 |
| Entertainment / Media | 2 |
| MSB / Money Services Business | 3 |
| Law Firm / Legal Services | 3 |
| Accounting / Audit Firm | 3 |
| Real Estate (Commercial) | 3 |
| Real Estate (Development) | 3 |
| Management Consulting | 3 |
| Financial / Tax Advisory | 3 |
| Healthcare / MedTech | 2 |
| Education / EdTech | 1 |
| Manufacturing | 2 |
| Construction | 3 |
| Charity / NGO / Non-Profit | 3 |
| Government / Public Sector | 1 |

`Private Banking` is a score-4 sector and the existing sector-score-4 High floor applies. These catalogue corrections are implemented only in the flag-gated Tier 0A resolver; the flag remains OFF and staging configuration is unchanged.

The following 22 current portal options have no Gate 0 v4 score. They remain Lane B and must fail closed; no score is invented:

| Exact current portal label | Disposition |
|---|---|
| Payment Processing / Gateway | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Lending / Credit Services | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Capital Markets / Brokerage | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Private Equity / Venture Capital | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Hedge Fund | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Crowdfunding / P2P Lending | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Wholesale / Distribution | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Commodities Trading | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Agricultural Commodities | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| IT Services / Outsourcing | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Cybersecurity | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Artificial Intelligence / ML | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Video Games / Esports | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Streaming / Content Platforms | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Bureau de Change | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Licensed Brokerage | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Corporate Services Provider | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Trust / Fiduciary Services | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Travel & Hospitality | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Food & Beverage | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Fashion / Luxury Goods | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |
| Other | QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED |

### 3.2 Entity type — exact labels and scores

| Exact portal label | Score |
|---|---:|
| Listed Company on Regulated Exchange | 1 |
| Regulated Financial Institution | 1 |
| Government / Public Sector Entity | 1 |
| Large Private Company (revenue > USD 10m) | 2 |
| SME / Private Company | 2 |
| Newly Incorporated Company (< 1 year) | 3 |
| Trust | 3 |
| Foundation | 3 |
| Regulated Fund (CIS / Licensed) | 2 |
| Unregulated Fund / SPV | 3 |
| Non-Profit Organisation / NGO | 3 |
| Shell Company / Special Purpose Vehicle | 4 |

### 3.3 Ownership — exact labels and scores

| Exact portal label | Score |
|---|---:|
| Simple — direct identifiable UBOs | 1 |
| 1–2 ownership layers | 2 |
| 3+ ownership layers / nominee shareholders | 3 |
| Opaque — UBOs cannot be fully identified | 4 |

The exact legacy alias `Complex multi-jurisdiction / opaque structure` resolves to `Opaque — UBOs cannot be fully identified` at score 4. This is a rename only. It preserves the existing `floor_rule_opaque_ownership` High floor and `opaque_or_incomplete_ownership` EDD route. It adds no dropdown option and does not split transparent and opaque structures in Tier 0A.

### 3.4 Transaction complexity — exact labels and scores

| Exact portal label | Score |
|---|---:|
| Simple — single currency, domestic corridors | 1 |
| Standard — multi-currency, established corridors | 2 |
| Complex — multiple international corridors | 3 |
| Very complex — includes monitored corridors | 4 |

### 3.5 Introduction — exact labels and scores

| Exact portal label | Score |
|---|---:|
| Direct application — client initiated | 1 |
| Introduced by regulated intermediary / agent | 1 |
| Introduced by non-regulated intermediary | 3 |
| Unsolicited / unknown referral source | 4 |

### 3.6 Monthly volume — exact labels and scores

| Exact portal label | Score |
|---|---:|
| Under USD 50,000 per month | 1 |
| USD 50,000 to USD 500,000 per month | 2 |
| USD 500,000 to USD 5,000,000 per month | 3 |
| Over USD 5,000,000 per month | 4 |

Only the exact approved `Over USD 5,000,000 per month` resolution emits `monthly_volume_score_4`. It requires compliance review and creates no automatic High tier floor. Sector-score-4 High-floor behavior remains unchanged. PEP, ownership, and all other score-4 factors do not inherit this volume-specific rule.

### 3.7 PEP role/type — exact labels and scores

| Exact portal role/type | Score |
|---|---:|
| Domestic PEP | 4 |
| Foreign PEP | 4 |
| International organisation PEP | 4 |
| Family member of a PEP | 4 |
| Close associate of a PEP | 4 |

The baseline/current branch does not yet score every one of these categories at 4. PEP is outside the six-family C9 runtime boundary reviewed for PR #753, so the implementation remains a HOLD for a separate narrowly scoped PR and replay. Declared PEP retains the existing High floor.

## 4. Geography — D1a only

| Exact stored label | Exact normalized value | Tier 0A disposition |
|---|---|---|
| Hong Kong SAR | hong kong | Approved exact alias |
| Congo (DRC) | democratic republic of congo | Approved exact alias |
| Türkiye | turkey | Approved exact alias |
| blank / missing incorporation country | empty | Fail-closed unresolved state |

No other country receives a Tier 0A alias or inferred score. C9 does not automatically apply to all 130 deferred countries or the 19 regions. All other country scoring and all region treatment remain deferred to Tier 1B with the pilot manual FATF check.

## 5. Verbatim mandatory corrections

The following blocks preserve the founder directions verbatim for the decisions most likely to be misapplied.

### A9 — rename only

> The current final ownership option must be renamed to:
>
> `Opaque — UBOs cannot be fully identified`
>
> Treatment:
>
> - score 4;
> - preserve the existing applicable ownership floor and escalation behavior;
> - old label `Complex multi-jurisdiction / opaque structure` becomes an exact legacy alias;
> - do not add a new dropdown option;
> - do not split transparent and opaque structures in Tier 0A.

### C2 — volume-specific escalation

> Do not persist or consume generic:
>
> `sub_factor_score_4`
>
> for the volume policy.
>
> That generic reason can arise from sector, PEP, ownership or other score-4 factors.
>
> Create or use a volume-specific reason, for example:
>
> `monthly_volume_score_4`
>
> Required behavior:
>
> - only the approved `Over USD 5m` monthly-volume band emits it;
> - persist it through the existing risk/escalation evidence model;
> - approval classifier consumes this exact reason;
> - compliance review is required;
> - no automatic High tier floor;
> - sector-score-4 High-floor behavior remains unchanged;
> - other score-4 factors must not accidentally inherit this volume rule.
>
> If durable storage requires schema or migration work, STOP and propose a separate reviewed change.

### C9 — migrated families only

> C9 applies only to the Tier 0A controlled families being migrated:
>
> - sector;
> - entity type;
> - ownership;
> - complexity;
> - introduction;
> - monthly volume.
>
> It must not automatically apply to all 130 deferred countries or the 19 regions.
>
> Geography in Tier 0A is limited to:
>
> - Hong Kong SAR → hong kong;
> - Congo (DRC) → democratic republic of congo;
> - Türkiye → turkey;
> - blank/missing incorporation country → fail-closed unresolved state.
>
> All other country scoring and region treatment remain deferred to Tier 1B with the pilot manual FATF check.

### Sentinel safety

> Do not put the raw label directly into the sentinel.
>
> Use:
>
> `stale:unmapped_<family>:<short_hash>`
>
> Store structured evidence separately:
>
> - family;
> - raw value;
> - normalized value;
> - hash;
> - application_id;
> - request_id;
> - config version;
> - resolution status.
>
> Prove that:
>
> - multiple unresolved fields can coexist;
> - one sentinel does not overwrite another stale reason;
> - resolving one field does not clear unrelated staleness;
> - approval remains blocked until all unresolved mappings are cleared.

### Activation safety

> Do not allow merge-to-main to silently activate changed scoring before review.
>
> Preferred approach:
>
> - implement PR-1 behind a staging activation flag that is OFF by default;
> - export and diff live staging risk_config against code seed and Gate 0 v4;
> - produce a read-only dry run against all active scored applications;
> - founder reviews and approves the dry-run report;
> - activate deliberately;
> - then execute Tier 0C recomputation.
>
> If an activation flag is not feasible, STOP and propose a safe alternative before merging.

## 6. C4 alias approval boundary

Founder/accountable executive Aisha Sudally approved the exact 77-row C4 table on 2026-07-14. Runtime implementation is limited to those exact rows plus the previously approved A9 alias:

- the 77 exact aliases may resolve only to their recorded canonical label and score;
- the 105 remaining ambiguous rows remain `QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED`;
- the nine synthetic/fixture markers are rejected and never mapped to real sectors or entity types;
- case, whitespace, Unicode normalization, and dash-form normalization are permitted only as the existing identity-normalization boundary; fuzzy and substring matching remain prohibited;
- the activation flag remains OFF.

The founder instruction referenced 107 ambiguous rows, matching the pre-correction artifact. The authoritative corrected artifact has 105 because the later founder correction moved `Investment Management` and `Cloud Services` into the approved catalogue. This reconciliation preserves both later score decisions and every genuinely ambiguous row.

## 7. Activation and validation hold

After C4 implementation, the read-only replay must be rerun against the same pinned population or a refreshed explicitly pinned snapshot. The report must include score, tier, approval-route and EDD-route changes; sentinel acquisitions/removals; bidirectional score changes; top remaining unresolved labels; quarantines; and every unexplained delta. Founder review of that revised report remains required before any deliberate activation.

Focused validation must continue to prove flag-OFF runtime equivalence, no schema/migration change, no default-to-2 within the migrated flag-enabled path, exact approved-alias resolution, quarantine of ambiguous labels, rejection of fixture markers, unchanged sector-score-4 High floor, and isolation of the volume-specific escalation reason.

Nothing in this artifact authorizes merge, deployment, activation, production use, or Tier 0C recomputation. No production-readiness claim is made.

## 8. Canonical hash method

The recorded SHA-256 covers the entire UTF-8 Markdown file with LF line endings after replacing the 64-hex value on the `Canonical Markdown SHA-256` line with the literal `{{CANONICAL_SHA256}}`. This removes self-reference while keeping every decision, table, status, and metadata field within the canonical payload.
