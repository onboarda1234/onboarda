# PR-KYC-EDD-REQUIREMENTS-1A — Full Evidence Audit Report

**Generated:** `2026-06-18T15:01:20Z`
**Auditor role:** Read-only evidence auditor. No code changes made in this session.
**Branch audited:** `copilot/featurekyc-edd-matrix-alignment`
**HEAD commit:** `f4dd03c8fe94c0d119d95bca5c96cc176cfe5cb9`
  → Merge pull request #534 from `onboarda1234/feature/kyc-edd-matrix-alignment`
**Implementation commit (parent 2):** `504f8636fea81bca652d4e83c92c2fe97c53b9a2`
  → "Align KYC EDD requirements with matrix v5"

---

## 1 — Source of Truth Confirmation

**Spec file:** `docs/compliance/kyc-edd-matrix-v5.md`

```
$ ls -la docs/compliance/kyc-edd-matrix-v5.md
# file exists and is readable — confirmed in session
```

Tables present and fully readable:
| Table | Row count |
|---|---|
| Standard KYC — Canonical Document Policies | 16 rows |
| Enhanced Requirements — Target Rows | 14 rows |
| Section Mapping (portal visibility) | 7 rows |
| HIGH/VERY-HIGH Baseline Pack | 8 rows |

---

## 2 — Enhanced Requirements — Target Rows (14-row table)

**Evidence source:** `arie-backend/enhanced_requirements.py` lines 295–529 (default rule list literal),
lines 157–206 (`ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP`, `TARGET_ENHANCED_REQUIREMENT_SECTIONS`).

### 2A — Default rule attributes vs v5 target

| Requirement key | v5 mandatory | Code | v5 blocking | Code | v5 active default | Code | v5 audience | Code |
|---|---|---|---|---|---|---|---|---|
| `company_bank_reference` | Yes (conditional on bank account) | `mandatory=True` ✓ | No | `blocking_approval=False` ✓ | Active | `active` key absent (defaults True) ✓ | client | `audience=client` ✓ |
| `company_sof_evidence` | Yes | `mandatory=True` ✓ | No by default | `blocking_approval=False` ✓ | Active | `active` key absent ✓ | client | `audience=client` ✓ |
| `pep_declaration_details` | Yes | `mandatory=True` ✓ | Yes | `blocking_approval=True` ✓ | Active | `active` key absent ✓ | client | `audience=client` ✓ |
| `pep_adverse_media_assessment` | No | `mandatory=False` ✓ | No | `blocking_approval=False` ✓ | Active back-office | `active` key absent ✓ | backoffice | `audience=backoffice` ✓ |
| `pep_enhanced_monitoring_flag` | No | `mandatory=False` ✓ | No | `blocking_approval=False` ✓ | Active back-office | `active` key absent ✓ | backoffice | `audience=backoffice` ✓ |
| `aml_cft_policy` | No | `mandatory=False` ✓ | No | `blocking_approval=False` ✓ | Active advisory | `active` key absent ✓ | client | `audience=client` ✓ |
| `trust_nominee_foundation_documents` | Yes | `mandatory=True` ✓ | Yes | `blocking_approval=True` ✓ | Active | `active` key absent ✓ | client | `audience=client` ✓ |
| `jurisdiction_sof_evidence` | Yes | `mandatory=True` ✓ | Yes if active | `blocking_approval=True` ✓ | Inactive in some settings | `active=False` ✓ | client | `audience=client` ✓ |
| `jurisdiction_exposure_rationale` | Yes | `mandatory=True` ✓ | Configurable | `blocking_approval=True` ✓ | Active (conditional) | `active` key absent ✓ | client | `audience=client` ✓ |
| `jurisdiction_risk_assessment` | Yes | `mandatory=True` ✓ | Yes | `blocking_approval=True` ✓ | Active back-office | `active` key absent ✓ | backoffice | `audience=backoffice` ✓ |
| `contracts_invoices` | Yes | `mandatory=True` ✓ | No | `blocking_approval=False` ✓ | Active | `active` key absent ✓ | client | `audience=client` ✓ |
| `expected_transaction_flow_evidence` | Yes | `mandatory=True` ✓ | Yes if active | `blocking_approval=True` ✓ | Inactive by default | `active=False` ✓ | client | `audience=client` ✓ |
| `major_counterparties_explanation` | Yes | `mandatory=True` ✓ | No | `blocking_approval=False` ✓ | Active | `active` key absent ✓ | client | `audience=client` ✓ |
| `volume_rationale_vs_business_size` | Yes | `mandatory=True` ✓ | Yes | `blocking_approval=True` ✓ | Active | `active` key absent ✓ | client | `audience=client` ✓ |

**All 14 rows: PASS.** Zero discrepancies between v5 spec and code default attributes.

### 2B — Canonical doc_type mapping

**Evidence source:** `arie-backend/enhanced_requirements.py` lines 157–167
(`ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP`)

```python
ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP = {
    "company_bank_reference": "bankref",          # v5: bankref ✓
    "company_sof_evidence": "source_funds",       # v5: source_funds ✓
    "aml_cft_policy": "aml_policy",               # v5: aml_policy ✓
    "trust_nominee_foundation_documents": "trust_deed",  # v5: trust_deed ✓
    "jurisdiction_sof_evidence": "source_funds",  # v5: source_funds ✓
    "contracts_invoices": "contracts",            # v5: contracts ✓
    "expected_transaction_flow_evidence": "supporting_document",  # v5: supporting_document ✓
}
```

Explanation-type and back-office-only rows (`pep_declaration_details`,
`jurisdiction_exposure_rationale`, `major_counterparties_explanation`,
`volume_rationale_vs_business_size`, `pep_adverse_media_assessment`,
`pep_enhanced_monitoring_flag`, `jurisdiction_risk_assessment`) have no canonical
doc_type in the v5 spec (listed as `None` or `Internal`). The code correctly omits them
from `ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP`. **PASS.**

### 2C — Section assignments

**Evidence source:** `arie-backend/enhanced_requirements.py` lines 189–205
(`TARGET_ENHANCED_REQUIREMENT_SECTIONS`)

```python
TARGET_ENHANCED_REQUIREMENT_SECTIONS = {
    "company_bank_reference": "C",           # v5: C — Enhanced Evidence Docs ✓
    "company_sof_evidence": "C",             # v5: C ✓
    "pep_declaration_details": "E",          # v5: E — Portal Disclosures ✓
    "pep_adverse_media_assessment": "F",     # v5: Not portal-visible (Section F) ✓
    "pep_enhanced_monitoring_flag": "F",     # v5: Not portal-visible (Section F) ✓
    "aml_cft_policy": "C",                   # v5: C ✓
    "trust_nominee_foundation_documents": "C", # v5: C ✓
    "jurisdiction_sof_evidence": "C",        # v5: C ✓
    "jurisdiction_exposure_rationale": "E",  # v5: E ✓
    "jurisdiction_risk_assessment": "F",     # v5: Not portal-visible (Section F) ✓
    "contracts_invoices": "C",               # v5: C ✓
    "expected_transaction_flow_evidence": "C", # v5: C ✓
    "major_counterparties_explanation": "E", # v5: E ✓
    "volume_rationale_vs_business_size": "E", # v5: E ✓
}
```

All 14 section assignments match the v5 spec. **PASS.**

### 2D — Conditionality flags

**Evidence source:** `arie-backend/enhanced_requirements.py` lines 136–156

```python
BANK_ACCOUNT_DEPENDENT_REQUIREMENT_KEYS = {
    "company_bank_reference",  # v5: conditional on existing bank account ✓
}
```

The `company_bank_reference` default rule also carries:
```python
"applies_when": {"existing_bank_account": True},  # line 309 ✓
```

`jurisdiction_sof_evidence` and `expected_transaction_flow_evidence` use `active=False`
to represent "inactive unless activated/requested", matching v5. **PASS.**

---

## 3 — Standard KYC Section B — Person-Level Requirements

**Evidence source:** `arie-backend/enhanced_requirements.py` lines 882–971

### 3A — Generation logic

```python
def _section_b_person_document_rules(db, app):        # line 957
    high_or_very_high = _application_high_or_very_high(app)
    for subject in _section_b_subjects_for_person_requirements(db, app):
        is_director_or_ubo = subject_type in {"director", "ubo"}
        is_pep = bool(subject.get("is_pep"))
        bankref_required = is_director_or_ubo and (high_or_very_high or is_pep)
        source_wealth_required = is_director_or_ubo and (high_or_very_high or is_pep)
```

v5 spec says:
> Bank Reference Letter — conditional per person: HIGH/VERY HIGH risk, or director/UBO who is a PEP
> Source of Wealth — conditional per person: HIGH/VERY HIGH risk, or UBO/director who is a PEP

**Condition in code vs spec: PASS.**

### 3B — Key naming and doc_type

Generated keys follow patterns `bankref_{subject_type}_{suffix}` and
`source_wealth_{subject_type}_{suffix}`. The fallback doc_type lookup at
lines 248–251 matches these via `SECTION_B_PERSON_DOCUMENT_POLICY_PREFIXES`:

```python
SECTION_B_PERSON_DOCUMENT_POLICY_PREFIXES = {
    "bankref": "bankref",          # v5 Section B doc_type: bankref ✓
    "source_wealth": "source_wealth",  # v5 Section B doc_type: source_wealth ✓
}
```

v5 spec consolidation notes:
- "Carries the former EDD `pep_bank_reference` requirement (now consolidated here)" — `pep_bank_reference` is in `REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS` (line 214). **PASS.**
- "Consolidates `material_ubo_sow_evidence` and `pep_sow_evidence`" — both are in `REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS` (lines 210, 213). **PASS.**

---

## 4 — Removed Legacy Keys

**Evidence source:** `arie-backend/enhanced_requirements.py` lines 207–242
(`REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS`)

The set contains 35 keys. The minimum removal inventory from the v5 task (21 keys) is
fully covered. Verified presence of required keys:

| Key | In REMOVED set |
|---|---|
| `enhanced_business_activity_explanation` | ✓ line 208 |
| `company_bank_statements_6m` | ✓ line 209 |
| `material_ubo_sow_evidence` | ✓ line 210 |
| `pep_role_position` | ✓ line 211 |
| `pep_jurisdiction` | ✓ line 212 |
| `pep_sow_evidence` | ✓ line 213 |
| `pep_bank_reference` | ✓ line 214 |
| `pep_linked_sof_evidence` | ✓ line 215 |
| `licence_or_registration_evidence` | ✓ line 218 |
| `crypto_source_of_funds_evidence` | ✓ line 222 |
| `crypto_enhanced_monitoring_flag` | ✓ line 223 |
| `crypto_regulatory_status_assessment` | ✓ line 224 |
| `ownership_chain_documents` | ✓ line 226 |
| `enhanced_ubo_evidence` | ✓ line 227 |
| `jurisdiction_licensing_regulatory_evidence` | ✓ line 230 |
| `high_volume_bank_statements` | ✓ line 232 |
| `screening_disposition` | ✓ line 233 |
| `false_positive_rationale` | ✓ line 234 |
| `adverse_media_pep_sanctions_assessment` | ✓ line 235 |
| `material_screening_senior_review` | ✓ line 236 |
| `client_clarification_screening` | ✓ line 237 |
| `manual_edd_pack` | ✓ line 238 |

The set is passed to the DB deactivation function at lines 1767–1774 to suppress
legacy settings rows on upgrade. **No removed key appears in active default generation
rules or the active ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP.** PASS.

Remaining file-level references to removed keys are limited to:
- `LEGACY_ENHANCED_REQUIREMENT_DOCUMENT_POLICY_ALIASES` (lines 168–183) — read-only historical doc_type classification for pre-v5 records only
- `_PORTAL_SAFE_COPY_BY_REQUIREMENT_KEY` (lines 108–128) — client-safe label fallback for legacy portal-requested rows that predate v5; does not generate new requirements
- Safe-copy entries for `pep_role_position`, `pep_jurisdiction`, `pep_sow_evidence`, `pep_linked_sof_evidence` (lines 3347–3362) — same purpose

These usages are intentionally preserved for backward compatibility with already-generated
application rows and do not constitute active generation. **PASS.**

---

## 5 — Back Office UI (arie-backoffice.html)

### 5A — Section rendering for EDD requirements

**Evidence source:** `arie-backoffice.html` lines 11033–11149

The back office uses `enhancedRequirementPresentationType(req)` to route each
requirement to the correct section group:

```js
function enhancedRequirementPresentationType(req) {   // line 10568
    if (type === 'document') return 'evidence';         // → Section C
    if (type === 'review_task' || type === 'internal_control') return 'internal_control'; // → Section F
    if (type === 'declaration' || type === 'explanation') return 'portal_disclosure';     // → Section E
    ...
}
```

Three distinct section-group renderers exist:
- `renderEnhancedEvidenceDocumentsGroupHtml()` → labelled **"C — Enhanced Evidence Documents"** (line 11033) — receives `presentationType === 'evidence'` and `backOfficeGroup !== 'identity'`
- `renderUnifiedPortalDisclosures()` → labelled **"E — Portal Disclosures"** (line 11087) — receives `presentationType === 'portal_disclosure'`
- `renderUnifiedInternalControls()` → labelled **"F — Internal Controls"** (line 11127) — receives `presentationType === 'internal_control'`

Mapping to v5 routing for 14 target rows:

| Requirement key | v5 section | req type | presentationType | Back-office group rendered |
|---|---|---|---|---|
| `company_bank_reference` | C | document | evidence | C ✓ |
| `company_sof_evidence` | C | document | evidence | C ✓ |
| `pep_declaration_details` | E | declaration | portal_disclosure | E ✓ |
| `pep_adverse_media_assessment` | F (internal) | review_task | internal_control | F ✓ |
| `pep_enhanced_monitoring_flag` | F (internal) | internal_control | internal_control | F ✓ |
| `aml_cft_policy` | C | document | evidence | C ✓ |
| `trust_nominee_foundation_documents` | C | document | evidence | C ✓ |
| `jurisdiction_sof_evidence` | C | document | evidence | C ✓ |
| `jurisdiction_exposure_rationale` | E | explanation | portal_disclosure | E ✓ |
| `jurisdiction_risk_assessment` | F (internal) | review_task | internal_control | F ✓ |
| `contracts_invoices` | C | document | evidence | C ✓ |
| `expected_transaction_flow_evidence` | C | document | evidence | C ✓ |
| `major_counterparties_explanation` | E | explanation | portal_disclosure | E ✓ |
| `volume_rationale_vs_business_size` | E | explanation | portal_disclosure | E ✓ |

**All 14 rows correctly routed in back-office UI. PASS.**

### 5B — Section B identity group (person-level)

**Evidence source:** `arie-backoffice.html` lines 8816–8835

```js
function enhancedRequirementBackOfficeGroup(req) {
    if (key.indexOf('bankref_') === 0 || key.indexOf('source_wealth_') === 0) return 'identity';
    ...
}
```

`bankref_*` and `source_wealth_*` requirements (generated for HIGH/VERY HIGH or PEP persons)
return `'identity'` and are excluded from the Section C group (line 11029 filters
`backOfficeGroup(req) !== 'identity'`). These are rendered in the Section B person panels.
**Matches v5 spec "Section B — Directors & UBO Identity Documents". PASS.**

### 5C — Settings view

**Evidence source:** `arie-backoffice.html` lines 2925–2992

The "Enhanced Requirements" settings view (`view-enhanced-requirements`) presents:
- `requirement_type` selector: document / declaration / review_task / explanation / internal_control (line 2970)
- `audience` selector: client / backoffice / both (line 2969)
- `client_safe_label` and `client_safe_description` fields (lines 28023–28024)

All v5 type/audience combinations are representable in the admin form. **PASS.**

---

## 6 — Client Portal UI (arie-portal.html)

### 6A — Server-side portal filter

**Evidence source:** `arie-backend/enhanced_requirements.py` lines 3559–3591
(`list_portal_application_enhanced_requirements`)

```python
WHERE aer.application_id = ?
  AND aer.active = 1
  AND aer.audience IN ('client', 'both')                          -- excludes backoffice
  AND aer.requirement_type NOT IN ('review_task', 'internal_control')  -- excludes F-section
```

v5 requirements not visible in portal by spec:
- `pep_adverse_media_assessment` — `audience=backoffice`, `type=review_task` → **excluded server-side ✓**
- `pep_enhanced_monitoring_flag` — `audience=backoffice`, `type=internal_control` → **excluded server-side ✓**
- `jurisdiction_risk_assessment` — `audience=backoffice`, `type=review_task` → **excluded server-side ✓**

**PASS.**

### 6B — Portal Section C and Section E slots

**Evidence source:** `arie-portal.html` lines 3753–3868 (HTML), lines 9617–9675 (JS)

Portal HTML structure:
```html
<!-- Section C: Enhanced Evidence Documents -->         <!-- line 3753 ✓ -->
<div class="card-title">C — Enhanced Evidence Documents</div>
<div id="additional-info-required-container"></div>    <!-- receives document-type reqs -->

<!-- Section E: Portal Disclosures -->                  <!-- line 3854 ✓ -->
<div class="card-title">E — Portal Disclosures</div>
<div id="portal-disclosures-container"></div>          <!-- receives declaration/explanation reqs -->
```

Portal JS routing in `renderPortalEnhancedRequirements()`:
```js
var type = String(req && req.requirement_type || '').toLowerCase();
if (type === 'declaration' || type === 'explanation') {
    disclosureLevel.push(html);  // → Section E
    return;
}
applicationLevel.push(html);    // → Section C
```

Section B person-level routing in `renderPortalEnhancedRequirements()`:
```js
var panel = portalEnhancedRequirementPersonPanel(req);
if (panel) { holder.innerHTML += html; return; }  // → person card panel (Section B area)
```

`portalEnhancedRequirementIsPersonScoped()` returns true for scopes `director`, `ubo`,
`beneficial_owner`, `controller`, `person`, `screening_subject`.

Section B bankref/source_wealth requirements (scope=`director`/`ubo`) therefore inject
into the named-person card within the Section B panel — not into Section C. **Matches v5
spec "Use B for UBO/director-specific enhanced evidence where the document is tied to a person." PASS.**

### 6C — Review screen section labels

**Evidence source:** `arie-portal.html` lines 3926–3941

Pre-submission review screen shows correctly labelled section headers:
```html
Section C — Enhanced Evidence Documents   <!-- line 3926 ✓ -->
Section D — Other Documents               <!-- line 3932 ✓ -->
Section E — Portal Disclosures            <!-- line 3938 ✓ -->
```

**PASS.**

### 6D — Client-safe wording for `jurisdiction_exposure_rationale`

**Evidence source:** `arie-backend/enhanced_requirements.py` lines 108–117

```python
"jurisdiction_exposure_rationale": (
    "Country of incorporation information",
    "Required for certain countries of incorporation.",
),
```

The v5 spec notes this requirement's portal description must not contain unsafe trigger
wording (the former description said "High-risk jurisdiction" which would reveal the
trigger reason to clients). The current `client_safe_description` — "Required for certain
countries of incorporation." — is neutral and does not expose the trigger.

Confirmed by test assertion (line 231 of `test_enhanced_requirement_settings.py`):
```python
assert by_key["jurisdiction_exposure_rationale"]["client_safe_description"] == \
    "Required for certain countries of incorporation."
```

**PASS.**

---

## 7 — Test Evidence

### 7A — Test runs performed in this audit session

```
$ cd arie-backend && python3 -m pytest \
    tests/test_enhanced_requirement_settings.py \
    -v --tb=short
# Result: 20 passed in 2.73s
```

```
$ cd arie-backend && python3 -m pytest \
    tests/test_application_enhanced_requirements.py \
    tests/test_edd_actuation_fk_safety.py \
    tests/test_edd_completion_recognition.py \
    -v --tb=short
# Result: 89 passed, 19 warnings in 67.30s
```

**Total in this audit: 109 tests, 109 passed, 0 failed.**

### 7B — Key test assertions mapped to spec

| Test | Assertion | Spec row verified |
|---|---|---|
| `test_approved_taxonomy_rule_defaults_are_seeded` | `by_key["company_bank_reference"]["blocking_approval"] == 0` | v5: blocking=No ✓ |
| `test_approved_taxonomy_rule_defaults_are_seeded` | `by_key["company_bank_reference"]["mandatory"] == 1` | v5: mandatory=Yes ✓ |
| `test_approved_taxonomy_rule_defaults_are_seeded` | `by_key["pep_adverse_media_assessment"]["audience"] == "backoffice"` | v5: back-office only ✓ |
| `test_approved_taxonomy_rule_defaults_are_seeded` | `by_key["pep_adverse_media_assessment"]["mandatory"] == 0` | v5: mandatory=No ✓ |
| `test_approved_taxonomy_rule_defaults_are_seeded` | `by_key["pep_enhanced_monitoring_flag"]["requirement_type"] == "internal_control"` | v5: Internal task ✓ |
| `test_approved_taxonomy_rule_defaults_are_seeded` | `by_key["jurisdiction_sof_evidence"]["active"] == 0` | v5: Inactive in some settings ✓ |
| `test_approved_taxonomy_rule_defaults_are_seeded` | `by_key["expected_transaction_flow_evidence"]["active"] == 0` | v5: Inactive by default ✓ |
| `test_approved_taxonomy_rule_defaults_are_seeded` | `by_key["volume_rationale_vs_business_size"]["blocking_approval"] == 1` | v5: blocking=Yes ✓ |
| `test_list_endpoint_returns_seeded_rules_and_read_roles` | `company_bank["section"] == "C"` | v5: Section C ✓ |
| `test_list_endpoint_returns_seeded_rules_and_read_roles` | `company_bank["canonical_doc_type"] == "bankref"` | v5: doc_type=bankref ✓ |
| `test_portal_enhanced_requirements_are_client_safe_and_owned` | portal API returns only client-safe audience reqs | v5: portal visibility filter ✓ |
| `test_portal_hides_requested_requirements_from_disabled_source_rules` | inactive source rule rows hidden from portal | v5: inactive rows not shown ✓ |

---

## 8 — Section Mapping (v5 spec — 7 sections)

**Evidence source:** v5 spec Section Mapping table, cross-checked against HTML section names

| Section | v5 portal name | Portal HTML label | v5 client-visible | Code |
|---|---|---|---|---|
| A | A — Corporate Entity Documents | "A — Corporate Entity Documents" | Yes | Static section (not EDD) |
| B | B — Directors & UBO Identity Documents | "B — Directors & UBO Identity Documents" | Yes | Person cards + identity group |
| C | C — Enhanced Evidence Documents | "C — Enhanced Evidence Documents" (portal line 3758; backoffice line 11033) | Yes | Section C container ✓ |
| D | D — Other Documents | "D — Other Documents" (portal line 3835; backoffice line 11064) | Yes | Static catch-all ✓ |
| E | E — Portal Disclosures | "E — Portal Disclosures" (portal line 3859; backoffice line 11087) | Yes (safe wording) | Disclosure container ✓ |
| F | Not portal-visible | "F — Internal Controls" (backoffice line 11127) | No | Back-office `renderUnifiedInternalControls()` ✓ |
| G | Not portal-visible | "G — Verification History" | No | Not in EDD rendering |

**PASS for all 7 sections.**

---

## 9 — Outstanding Observations (Non-Blocking)

1. **`jurisdiction_exposure_rationale` blocking default is `True`:** The v5 spec labels this
   "Configurable". The code defaults to `blocking_approval=True`. This is within spec (a
   True default is a valid configuration choice). Admin operators can change it at runtime.
   **Not a deviation — informational only.**

2. **`pep_declaration_details` blocking is `True`:** The v5 spec says "blocking=Yes". Code
   confirms `blocking_approval=True`. This is correct; no action required.

3. **Legacy `_PORTAL_SAFE_COPY_BY_REQUIREMENT_KEY` retains entries for `pep_role_position`,
   `pep_jurisdiction`, `pep_sow_evidence`, `pep_linked_sof_evidence`:** These entries exist
   solely as safe-copy label fallbacks for historical portal-requested rows that were
   generated before v5. No new requirements are generated with these keys. The
   `REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS` set ensures the corresponding settings rows
   are deactivated on upgrade. **This is intentional backward-compatibility. Not a deviation.**

4. **`company_sof_evidence` was previously `blocking_approval=True` (pre-v5):** The
   `current_vs_target_diff.md` in `PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/` recorded
   this as a required fix. Current code confirms `blocking_approval=False`. **Fix confirmed.**

5. **`contracts_invoices` was previously `blocking_approval=True` (pre-v5):** Same as (4).
   Current code confirms `blocking_approval=False`. **Fix confirmed.**

6. **`major_counterparties_explanation` was previously `blocking_approval=True` (pre-v5):**
   Current code confirms `blocking_approval=False`. **Fix confirmed.**

---

## 10 — Overall Verdict

| Check | Result |
|---|---|
| v5 spec file present and readable | **PASS** |
| All 14 Enhanced Requirements target rows present in code | **PASS** |
| Mandatory/blocking/active flags match v5 for all 14 rows | **PASS** |
| Canonical doc_type mapping correct for all document-backed rows | **PASS** |
| Section assignments (C/E/F) correct for all 14 rows | **PASS** |
| Bank-account conditionality on `company_bank_reference` | **PASS** |
| Section B person-level `bankref` and `source_wealth` generation condition | **PASS** |
| Legacy key consolidation (pep_bank_reference → Section B bankref) | **PASS** |
| Legacy key consolidation (pep_sow_evidence → Section B source_wealth) | **PASS** |
| All minimum-removal keys present in REMOVED_ACTIVE set | **PASS** |
| No removed key active in default generation rules | **PASS** |
| Back-office UI routes requirements to correct Section C/E/F groups | **PASS** |
| Back-office UI routes person-level requirements to identity (Section B) group | **PASS** |
| Portal server-side filter excludes backoffice/internal-control rows | **PASS** |
| Portal Section C container receives document-type requirements | **PASS** |
| Portal Section E container receives declaration/explanation-type requirements | **PASS** |
| Portal Section B person panels receive person-scoped (bankref/source_wealth) requirements | **PASS** |
| `jurisdiction_exposure_rationale` client_safe_description is neutral (no trigger wording) | **PASS** |
| Test suite passes (20 settings tests + 89 application/EDD tests) | **PASS — 109/109** |

**PR-KYC-EDD-REQUIREMENTS-1A: v5 matrix CONFIRMED IMPLEMENTED in both back-office and portal UI.
No deviations found.**
