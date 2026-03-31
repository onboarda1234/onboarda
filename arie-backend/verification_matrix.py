"""
Onboarda — Document Verification Matrix (Backend Source of Truth)
================================================================
This module is the single canonical definition for:
  - Document types and their verification checks
  - Check classification: rule | hybrid | ai
  - Pre-screening field mappings for each check
  - Trigger conditions (conditional gates)
  - Escalation behaviour

ALL other copies of check definitions (claude_client.py fallback, db.py seed,
portal JS, backoffice JS) must derive from or stay in sync with this file.

Classification model:
  rule   — Deterministic. Run in Python against extracted fields + pre-screening.
            Never delegates to AI.
  hybrid — Rules first. AI fallback only when deterministic check is INCONCLUSIVE.
  ai     — Always runs through Claude. Reserved for genuine interpretation /
            plausibility / legal-language reading.

Policy decisions encoded here:
  - "Memorandum of Association" replaces "Memorandum & Articles of Association"
  - Certificate of Registration removed (checks retired, backward-compat preserved)
  - Shareholder Register — Currency check removed
  - Financial Statements — Audit Status check removed
  - Financial Statements — Completeness check removed
  - Regulatory Licence checks are conditional on pre-screening field
  - All documents require a Certification check (hybrid)
  - Document authenticity is suspicion/escalation only, never AI hard-fail
"""

# ── Status constants ──────────────────────────────────────────────
class CheckStatus:
    PASS = "pass"
    WARN = "warn"          # Soft warning — document advances but flagged
    FAIL = "fail"          # Hard fail — document requires review
    SKIP = "skip"          # Check not applicable (conditional gate not met)
    INCONCLUSIVE = "inconclusive"  # Hybrid: deterministic resolution failed, send to AI


class CheckClassification:
    RULE   = "rule"
    HYBRID = "hybrid"
    AI     = "ai"


class TriggerTiming:
    GATE          = "gate"           # Before any processing — instant
    AFTER_OCR     = "after_ocr"      # After extraction — <1s
    HYBRID_LAYER  = "hybrid_layer"   # After rule returns INCONCLUSIVE
    ASYNC_AI      = "async_ai"       # Async, queued
    CONDITIONAL   = "conditional"    # Only if gate condition is met


class EscalationOutcome:
    REJECT   = "reject"     # Hard reject — stop processing
    FAIL     = "fail"       # Fail check — flag document
    ESCALATE = "escalate"   # Route to compliance officer queue
    WARN     = "warn"       # Soft flag — advance but note
    SKIP     = "skip"       # Not applicable — skip silently


# ── Pre-screening field name constants ────────────────────────────
# These map to the keys in applications.prescreening_data
class PSField:
    COMPANY_NAME         = "registered_entity_name"
    INCORPORATION_NUMBER = "incorporation_number"
    INCORPORATION_DATE   = "incorporation_date"
    JURISDICTION         = "country_of_incorporation"
    REGISTERED_ADDRESS   = "registered_address"
    AUTHORISED_CAPITAL   = "authorised_share_capital"
    SHAREHOLDERS         = "shareholders"          # list
    UBOS                 = "ubos"                  # list
    DIRECTORS            = "directors"             # list
    AUTHORISED_SIGNATORY = "authorised_signatory"
    FINANCIAL_YEAR_END   = "financial_year_end"
    ANNUAL_TURNOVER      = "annual_turnover"
    BUSINESS_ACTIVITY    = "business_overview"
    INDUSTRY_SECTOR      = "sector"
    HOLDS_LICENCE        = "regulatory_licences"   # string; conditional if not 'None'/'none'/''
    LICENCE_NUMBER       = "licence_number"
    LICENCE_AUTHORITY    = "licence_authority"
    LICENCE_TYPE         = "licence_type"
    BANK_NAME            = "bank_name"
    # Person fields (persons[].*)
    PERSON_FULL_NAME     = "full_name"
    PERSON_DOB           = "date_of_birth"
    PERSON_NATIONALITY   = "nationality"
    PERSON_ID_NUMBER     = "id_document_number"
    PERSON_ADDRESS       = "residential_address"
    PERSON_ROLE          = "role"
    PERSON_SOW           = "source_of_wealth_detail"
    PEP_FUNCTION         = "pep_function"
    PEP_NET_WORTH        = "pep_net_worth"
    PEP_SOF              = "pep_source_of_funds"


def _check(id_, label, classification, ps_field, why, logic, trigger, escalation,
           rule_type=None, ai_prompt_hint=None):
    """Build a check definition dict with all required fields."""
    return {
        "id": id_,
        "label": label,
        "classification": classification,
        "ps_field": ps_field,
        "why": why,
        "logic": logic,
        "trigger": trigger,
        "escalation": escalation,
        "rule_type": rule_type,          # for rule checks: name|date|numeric|enum|set|presence|hash
        "ai_prompt_hint": ai_prompt_hint, # narrow hint for AI prompt when classification is ai/hybrid
    }


# ── Cross-cutting checks (applied to ALL documents) ───────────────
GATE_CHECKS = [
    _check(
        id_="GATE-01",
        label="File Format",
        classification=CheckClassification.RULE,
        ps_field=None,
        why="MIME type + magic bytes check is fully deterministic.",
        logic="Accept PDF, JPEG, PNG only. Check MIME type and magic bytes. Reject before any processing.",
        trigger=TriggerTiming.GATE,
        escalation=EscalationOutcome.REJECT,
        rule_type="enum",
    ),
    _check(
        id_="GATE-02",
        label="File Size",
        classification=CheckClassification.RULE,
        ps_field=None,
        why="Size threshold comparison is deterministic.",
        logic="Compare file size in bytes against configured maximum (25MB). Reject if exceeded.",
        trigger=TriggerTiming.GATE,
        escalation=EscalationOutcome.REJECT,
        rule_type="numeric",
    ),
    _check(
        id_="GATE-03",
        label="Duplicate Detection",
        classification=CheckClassification.RULE,
        ps_field=None,
        why="SHA-256 hash comparison is deterministic.",
        logic="Compute SHA-256 of file content. Compare against all existing upload hashes for this application.",
        trigger=TriggerTiming.GATE,
        escalation=EscalationOutcome.WARN,
        rule_type="hash",
    ),
]

CERTIFICATION_CHECK = _check(
    id_="CERT-01",
    label="Certification",
    classification=CheckClassification.HYBRID,
    ps_field=None,
    why="Certification wording and stamps vary by jurisdiction and issuer. "
        "Rule can detect keywords; AI resolves non-standard formats.",
    logic="OCR text scan for certification keywords ('certified true copy', 'notarised', "
          "'sworn before me'). Check for stamp/seal markers. Confirm certifier is notary, "
          "lawyer, or accountant. If not found by rules, AI examines for visual stamps.",
    trigger=TriggerTiming.AFTER_OCR,
    escalation=EscalationOutcome.ESCALATE,
    ai_prompt_hint="Determine whether this document has been certified by a notary, lawyer, "
                   "or accountant. Look for certification wording, stamps, seals, and certifier "
                   "credentials. PASS if certified by a notary/lawyer/accountant. "
                   "WARN if certified but certifier role unclear. FAIL if no certification found.",
)


# ── SECTION A — Corporate Entity Documents ────────────────────────

SECTION_A_CHECKS = {

    # ── Certificate of Incorporation ──
    "cert_inc": {
        "doc_name": "Certificate of Incorporation",
        "category": "entity",
        "conditional": None,
        "checks": [
            _check(
                id_="DOC-05",
                label="Entity Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.COMPANY_NAME,
                why="Normalised string comparison against pre-screening is deterministic.",
                logic="Extract company name via OCR. Normalise (trim, case-fold, expand/collapse legal "
                      "suffixes Ltd/Limited). Compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-06",
                label="Registration Number Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.INCORPORATION_NUMBER,
                why="Structured identifier — exact field comparison.",
                logic="Extract registration/incorporation number. Validate format against jurisdiction "
                      "regex. Compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="exact",
            ),
            _check(
                id_="DOC-07A",
                label="Document Clarity",
                classification=CheckClassification.HYBRID,
                ps_field=None,
                why="Image quality can be scored algorithmically; borderline scans need visual judgment.",
                logic="Score OCR confidence, blur detection, contrast, crop completeness. "
                      "If score < threshold, invoke AI for visual assessment.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess whether the document is legible, complete, and suitable for "
                               "compliance review. PASS if fully legible. WARN if partially legible. "
                               "FAIL if illegible or blank.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Memorandum of Association ──  (renamed from "Memorandum & Articles")
    "memarts": {
        "doc_name": "Memorandum of Association",
        "category": "entity",
        "conditional": None,
        "checks": [
            _check(
                id_="DOC-08",
                label="Entity Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.COMPANY_NAME,
                why="Normalised string comparison against pre-screening is deterministic.",
                logic="Extract company name from headers/first pages. Normalise and compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-09",
                label="Completeness",
                classification=CheckClassification.HYBRID,
                ps_field=None,
                why="Page count is rule-checkable; key MoA sections vary by jurisdiction/drafter.",
                logic="Rule: verify minimum page count and continuous pagination. "
                      "Hybrid: AI checks for presence of key MoA sections (objects, capital, subscribers).",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                ai_prompt_hint="Verify the Memorandum of Association appears complete with key sections "
                               "present (objects clause, share capital, subscribers/signatories). "
                               "PASS if complete. WARN if minor sections missing. FAIL if key sections absent.",
            ),
            _check(
                id_="DOC-MA-01",
                label="Business Objects / Activities",
                classification=CheckClassification.AI,
                ps_field=PSField.BUSINESS_ACTIVITY,
                why="Legal drafting of objects clauses requires reading comprehension — no deterministic "
                    "comparison possible. Critical compliance check: does the MoA authorise the declared business?",
                logic="AI reads objects clause. Determines whether the business_activity declared in "
                      "pre-screening falls within the stated objects. Outputs verdict, reasoning, confidence, "
                      "relevant clause citations.",
                trigger=TriggerTiming.ASYNC_AI,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Read the objects clause of this Memorandum of Association. Determine whether "
                               "the declared business activity falls within the stated objects. "
                               "PASS if declared activity is clearly within scope. "
                               "WARN if scope is broad/ambiguous but plausibly includes declared activity. "
                               "FAIL if declared activity appears outside objects.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Certificate of Registration — REMOVED (backward-compat entry only) ──
    # Checks retired per policy decision. Doc type preserved for historical records.
    "cert_reg": {
        "doc_name": "Certificate of Registration",
        "category": "entity",
        "conditional": None,
        "retired": True,     # No checks run; historical records preserved
        "checks": [],
    },

    # ── Shareholder Register ──
    "reg_sh": {
        "doc_name": "Shareholder Register",
        "category": "entity",
        "conditional": None,
        "checks": [
            # NOTE: DOC-16 Currency check REMOVED per policy decision
            _check(
                id_="DOC-14",
                label="Shareholder Name Match",
                classification=CheckClassification.HYBRID,
                ps_field=PSField.SHAREHOLDERS,
                why="Name matching requires fuzzy logic for transliterations, middle names, suffixes.",
                logic="Rule: normalised exact match first. If match rate < 100%, "
                      "AI resolves unmatched names via fuzzy matching.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Match the shareholder names on this register against the declared "
                               "shareholders. Allow for transliterations, name order variations, and "
                               "common abbreviations. PASS if all match. WARN if minor variations. "
                               "FAIL if names cannot be matched.",
            ),
            _check(
                id_="DOC-15",
                label="Shareholding Percentages Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.SHAREHOLDERS,
                why="Arithmetic comparison against declared percentages is deterministic.",
                logic="Parse percentage for each shareholder. Compare with corresponding declared "
                      "percentage from pre-screening shareholders list.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="numeric",
            ),
            _check(
                id_="DOC-15A",
                label="Total Shares Sum to 100%",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Pure arithmetic invariant — no comparison needed.",
                logic="Sum all shareholding percentages. Must equal 100% (±0.01% tolerance for rounding).",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="numeric",
            ),
            _check(
                id_="DOC-15B",
                label="UBO Identification (≥25%)",
                classification=CheckClassification.RULE,
                ps_field=PSField.UBOS,
                why="Threshold comparison + list membership is deterministic.",
                logic="Any shareholder with ≥25% must appear in declared UBO list from pre-screening.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="threshold",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Register of Directors ──
    "reg_dir": {
        "doc_name": "Register of Directors",
        "category": "entity",
        "conditional": None,
        "checks": [
            _check(
                id_="DOC-17",
                label="Director Name Match",
                classification=CheckClassification.HYBRID,
                ps_field=PSField.DIRECTORS,
                why="Name matching requires fuzzy logic for transliterations, aliases, name order.",
                logic="Rule: normalised exact match first. If match rate < 100%, "
                      "AI resolves unmatched directors via fuzzy matching.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Match the director names on this register against the declared directors. "
                               "Allow for transliterations, name order variations, and common abbreviations. "
                               "PASS if all declared directors appear. WARN if minor name variations. "
                               "FAIL if any declared director is missing.",
            ),
            _check(
                id_="DOC-18",
                label="Completeness",
                classification=CheckClassification.RULE,
                ps_field=PSField.DIRECTORS,
                why="Set comparison — all declared directors must appear — is deterministic.",
                logic="Confirm all directors from pre-screening appear in register. "
                      "Flag any declared director missing from register.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="set",
            ),
            _check(
                id_="DOC-19",
                label="Document Clarity",
                classification=CheckClassification.HYBRID,
                ps_field=None,
                why="Legibility can be scored; some scans need visual assessment.",
                logic="OCR confidence and image-quality thresholds. Escalate borderline cases.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess document legibility. PASS if legible. WARN if partially legible. "
                               "FAIL if illegible.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Latest Annual Financial Statements / Management Accounts ──
    "fin_stmt": {
        "doc_name": "Latest Annual Financial Statements / Management Accounts",
        "category": "entity",
        "conditional": None,
        "checks": [
            # NOTE: DOC-22 Audit Status REMOVED per policy decision
            # NOTE: DOC-23 Completeness REMOVED per policy decision
            _check(
                id_="DOC-20",
                label="Financial Period",
                classification=CheckClassification.RULE,
                ps_field=PSField.FINANCIAL_YEAR_END,
                why="Date/period comparison is deterministic.",
                logic="Extract reporting period or year-end date. Verify it covers the required financial "
                      "year (within 18 months). Allow forecast/management accounts if entity < 1 year old.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="date",
            ),
            _check(
                id_="DOC-21",
                label="Entity Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.COMPANY_NAME,
                why="Normalised string comparison against pre-screening is deterministic.",
                logic="Extract entity name from statements. Normalise and compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-21A",
                label="Revenue / Turnover Consistency",
                classification=CheckClassification.HYBRID,
                ps_field=PSField.ANNUAL_TURNOVER,
                why="Numeric comparison is possible but financial formats vary; AI may locate the figure.",
                logic="Rule: extract labelled 'Revenue'/'Turnover'/'Total Income', compare with "
                      "pre-screening annual_turnover. If extraction uncertain or >20% variance, "
                      "AI locates and interprets.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Locate the revenue, turnover, or total income figure in this financial "
                               "statement. Extract the amount and currency. Compare against the declared "
                               "annual turnover. PASS if consistent. WARN if >20% variance. "
                               "FAIL if contradicts declaration.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Proof of Registered Address ──
    "poa": {
        "doc_name": "Proof of Registered Address",
        "category": "entity",
        "conditional": None,
        "checks": [
            _check(
                id_="DOC-01",
                label="Document Date",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Date arithmetic against policy window is deterministic.",
                logic="Extract document date. Must be within 90 days of upload date.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="date",
            ),
            _check(
                id_="DOC-02",
                label="Entity Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.COMPANY_NAME,
                why="Normalised string comparison against pre-screening is deterministic.",
                logic="Extract entity name. Normalise and compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-04",
                label="Address Match",
                classification=CheckClassification.HYBRID,
                ps_field=PSField.REGISTERED_ADDRESS,
                why="Address formatting varies across jurisdictions. Rules handle normalised matches; "
                    "AI resolves ambiguity.",
                logic="Normalise address components (street, city, postcode, country). "
                      "Compare with ps_field. If similarity < 85%, invoke AI for semantic equivalence.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Compare the address on this document with the declared registered address. "
                               "Account for common formatting differences (abbreviations, line breaks, "
                               "missing postcodes). PASS if addresses match. WARN if partial match. "
                               "FAIL if addresses do not match.",
            ),
            _check(
                id_="DOC-03",
                label="Document Clarity",
                classification=CheckClassification.HYBRID,
                ps_field=None,
                why="Clarity and redaction are partly measurable, partly visual.",
                logic="Check OCR confidence, blur, crop, redaction indicators. Escalate ambiguous cases.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess document legibility and whether any content appears redacted or "
                               "obscured. PASS if fully legible and unredacted. WARN if partially legible. "
                               "FAIL if illegible or heavily redacted.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Board Resolution ──
    "board_res": {
        "doc_name": "Board Resolution",
        "category": "entity",
        "conditional": None,
        "checks": [
            _check(
                id_="DOC-24",
                label="Signatory Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.AUTHORISED_SIGNATORY,
                why="Signatory name can be cross-referenced deterministically against declared directors.",
                logic="Extract signatory name. Normalise and compare with declared authorised signatory "
                      "or directors list from pre-screening.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-25",
                label="Resolution Date",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Date freshness is deterministic.",
                logic="Extract resolution date. Must be within 12 months.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="date",
            ),
            _check(
                id_="DOC-26",
                label="Scope of Authority",
                classification=CheckClassification.AI,
                ps_field=PSField.BUSINESS_ACTIVITY,
                why="Board resolutions use varied legal language. Keyword scanning produces "
                    "false positives/negatives. Genuine legal text interpretation required.",
                logic="AI reads the resolution text, determines whether the authority granted "
                      "covers the specific purpose (account opening / entering into proposed relationship). "
                      "Outputs: verdict, reasoning, relevant clause citations.",
                trigger=TriggerTiming.ASYNC_AI,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Read this board resolution. Determine whether it clearly authorises the "
                               "signatory to open a bank/payment account or enter into the proposed "
                               "financial services relationship. PASS if explicit authorisation present. "
                               "WARN if implicit or broad authorisation. FAIL if no relevant authorisation.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Company Structure Chart ──
    "structure_chart": {
        "doc_name": "Company Structure Chart",
        "category": "entity",
        "conditional": None,
        "checks": [
            _check(
                id_="DOC-27",
                label="UBO Chain",
                classification=CheckClassification.HYBRID,
                ps_field=PSField.UBOS,
                why="Ownership chains are structured data but many charts are unstructured images.",
                logic="Parse entities, percentages, parent-child links where possible. "
                      "Use AI for image-heavy or irregular charts. Verify chain traces to UBOs.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Trace the ownership chain in this structure chart to identify the "
                               "ultimate beneficial owners (UBOs). Verify the chain is complete and "
                               "traces to natural persons. PASS if UBO chain complete. "
                               "WARN if chain incomplete but UBOs identifiable. FAIL if UBOs not identifiable.",
            ),
            _check(
                id_="DOC-28",
                label="Ownership Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.SHAREHOLDERS,
                why="Cross-document ownership consistency is deterministic when data is extracted.",
                logic="Compare chart entities and percentages against pre-screening shareholder/UBO fields.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="numeric",
            ),
            _check(
                id_="DOC-29",
                label="Legibility",
                classification=CheckClassification.HYBRID,
                ps_field=None,
                why="Image resolution is measurable; diagram density can defeat rules.",
                logic="Image resolution and OCR thresholds. AI review for dense or low-quality charts.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess whether the structure chart is legible and readable. "
                               "PASS if legible. WARN if partially legible. FAIL if illegible.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Bank Reference Letter (conditional: HIGH/VERY_HIGH risk only) ──
    "bankref": {
        "doc_name": "Bank Reference Letter",
        "category": "entity",
        "conditional": "high_risk",   # Only required for HIGH/VERY_HIGH risk applications
        "checks": [
            _check(
                id_="DOC-30",
                label="Bank Letterhead",
                classification=CheckClassification.HYBRID,
                ps_field=PSField.BANK_NAME,
                why="Letterhead recognition varies by bank and layout.",
                logic="Check for bank name, logo, branch markers against pre-screening bank_name. "
                      "Escalate if branding is weak or unfamiliar.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Determine whether this letter is on official bank letterhead. "
                               "Look for bank name, logo, branch details. PASS if on letterhead. "
                               "WARN if letterhead unclear. FAIL if no letterhead.",
            ),
            _check(
                id_="DOC-31",
                label="Document Date",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Date arithmetic is deterministic.",
                logic="Extract letter date. Must be within 90 days.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="date",
            ),
            _check(
                id_="DOC-32",
                label="Entity Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.COMPANY_NAME,
                why="Normalised string comparison is deterministic.",
                logic="Extract entity name from letter. Normalise and compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Regulatory Licence(s) — CONDITIONAL ──
    "licence": {
        "doc_name": "Regulatory Licence(s)",
        "category": "entity",
        "conditional": "holds_licence",   # Skip entirely if holds_regulatory_licence is false/None/empty
        "checks": [
            _check(
                id_="LIC-GATE",
                label="Licence Applicability Gate",
                classification=CheckClassification.RULE,
                ps_field=PSField.HOLDS_LICENCE,
                why="Boolean gate from pre-screening — deterministic. "
                    "If client has no licence, all licence checks are skipped.",
                logic="Check pre-screening regulatory_licences field. "
                      "If value is 'None', 'none', empty, or null — SKIP all licence checks.",
                trigger=TriggerTiming.GATE,
                escalation=EscalationOutcome.SKIP,
                rule_type="presence",
            ),
            _check(
                id_="DOC-33",
                label="Entity Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.COMPANY_NAME,
                why="Deterministic licensee-name comparison.",
                logic="Extract licensee name. Normalise and compare with pre-screening company name.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-34",
                label="Licence Validity",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Licence validity is date-driven — deterministic.",
                logic="Extract expiry date or status. Confirm licence is active and unexpired. "
                      "Warn if expiring within 30 days.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="date",
            ),
            _check(
                id_="DOC-35",
                label="Licence Scope",
                classification=CheckClassification.AI,
                ps_field=PSField.BUSINESS_ACTIVITY,
                why="Licence conditions are written in regulatory language unique to each authority. "
                    "Matching scope to declared activity requires interpretive legal reading.",
                logic="AI reads licence conditions/schedule, assesses whether declared business_activity "
                      "from pre-screening falls within licensed scope. Outputs: verdict, reasoning, "
                      "confidence, relevant condition citations.",
                trigger=TriggerTiming.ASYNC_AI,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Read this regulatory licence. Determine whether the declared business "
                               "activity falls within the scope of the licence. PASS if clearly within scope. "
                               "WARN if scope ambiguous but plausibly covers declared activity. "
                               "FAIL if declared activity appears outside licensed scope.",
            ),
            CERTIFICATION_CHECK,
        ],
    },
}


# ── SECTION B — Directors, UBOs & Intermediary Shareholders ───────

SECTION_B_CHECKS = {

    # ── Passport / Government ID ──
    "passport": {
        "doc_name": "Passport / Government ID",
        "category": "person",
        "conditional": None,
        "checks": [
            _check(
                id_="DOC-49",
                label="Document Expiry",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Expiry is date arithmetic — deterministic.",
                logic="Extract expiry date from OCR/MRZ. FAIL if expired. WARN if < 6 months remaining.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="date",
            ),
            _check(
                id_="DOC-51",
                label="Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.PERSON_FULL_NAME,
                why="Identity name match is deterministic when OCR/MRZ succeeds.",
                logic="Extract full name from OCR or MRZ. Normalise (trim, case-fold, name order). "
                      "Compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-49A",
                label="Date of Birth Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.PERSON_DOB,
                why="Date comparison is deterministic.",
                logic="Extract DOB from OCR/MRZ. Parse to ISO format. Compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="date",
            ),
            _check(
                id_="DOC-52",
                label="Nationality Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.PERSON_NATIONALITY,
                why="Nationality is a direct enum comparison — deterministic.",
                logic="Extract nationality from OCR/MRZ. Normalise country code. Compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="enum",
            ),
            _check(
                id_="DOC-50",
                label="Photo Quality",
                classification=CheckClassification.HYBRID,
                ps_field=None,
                why="Image quality can be scored; 'identifiable' is partly visual.",
                logic="Check resolution, blur, face detection, crop completeness. "
                      "Escalate borderline images.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess whether the photo in this identity document is clear and the "
                               "holder is identifiable. PASS if clear and identifiable. "
                               "WARN if partially obscured. FAIL if unidentifiable.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── National ID ──
    "national_id": {
        "doc_name": "National ID Card",
        "category": "person",
        "conditional": None,
        "checks": [
            _check(
                id_="DOC-53",
                label="Document Expiry",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Expiry is date arithmetic — deterministic.",
                logic="Extract expiry date. FAIL if expired. WARN if expiring within 30 days.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="date",
            ),
            _check(
                id_="DOC-55",
                label="Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.PERSON_FULL_NAME,
                why="Identity name match is deterministic when OCR succeeds.",
                logic="Extract full name from OCR. Normalise and compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-56",
                label="Nationality Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.PERSON_NATIONALITY,
                why="Nationality is a direct enum comparison — deterministic.",
                logic="Extract nationality. Normalise country code. Compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="enum",
            ),
            _check(
                id_="DOC-54",
                label="Photo Quality",
                classification=CheckClassification.HYBRID,
                ps_field=None,
                why="Image quality can be scored; 'identifiable' is partly visual.",
                logic="Check resolution, blur, face detection. Escalate borderline images.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess whether the photo in this ID is clear and the holder is "
                               "identifiable. PASS if clear. WARN if partially obscured. FAIL if unidentifiable.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Proof of Address (Personal) ──
    "poa_person": {
        "doc_name": "Proof of Address (Personal)",
        "category": "person",
        "conditional": None,
        "doc_type_alias": "poa",   # db doc_type is 'poa' for both entity and person
        "checks": [
            _check(
                id_="DOC-61",
                label="Document Date",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Date arithmetic is deterministic.",
                logic="Extract document/statement date. Must be within 90 days.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="date",
            ),
            _check(
                id_="DOC-62",
                label="Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.PERSON_FULL_NAME,
                why="Identity name match is deterministic.",
                logic="Extract name from document. Normalise and compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-62A",
                label="Address Match",
                classification=CheckClassification.HYBRID,
                ps_field=PSField.PERSON_ADDRESS,
                why="Address formatting varies. Rules handle normalised matches; AI resolves ambiguity.",
                logic="Normalise address components. Compare with ps_field. "
                      "If similarity < 85%, invoke AI for semantic equivalence.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Compare the address on this document with the declared residential address. "
                               "Allow for common formatting differences. PASS if addresses match. "
                               "WARN if partial match. FAIL if addresses do not match.",
            ),
            _check(
                id_="DOC-63",
                label="Document Clarity",
                classification=CheckClassification.HYBRID,
                ps_field=None,
                why="Readability and full-address visibility need quality scoring plus review.",
                logic="Check OCR confidence, crop completeness, full address lines present. "
                      "Escalate edge cases.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess document legibility and whether the full address is visible. "
                               "PASS if fully legible. WARN if partially legible. FAIL if illegible.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── CV / LinkedIn Profile ──
    "cv": {
        "doc_name": "CV / LinkedIn Profile",
        "category": "person",
        "conditional": None,
        "checks": [
            _check(
                id_="DOC-57",
                label="Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.PERSON_FULL_NAME,
                why="Identity name comparison is deterministic.",
                logic="Extract CV/profile name. Normalise and compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-57A",
                label="Employment History — Presence",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Structural check — presence of employment entries is deterministic.",
                logic="Confirm employment entries exist with dates and employer names. "
                      "FAIL if no substantive employment history present.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="presence",
            ),
            _check(
                id_="DOC-58",
                label="Employment History — Relevance",
                classification=CheckClassification.AI,
                ps_field=PSField.PERSON_ROLE,
                why="Assessing whether professional background is substantively relevant to "
                    "the declared role is judgment.",
                logic="AI evaluates whether employment history supports person's declared role "
                      "and entity's declared business activity. Outputs: verdict, reasoning, confidence.",
                trigger=TriggerTiming.ASYNC_AI,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess whether the professional background in this CV/profile is "
                               "substantively relevant to the person's declared role and the entity's "
                               "business activity. PASS if clearly relevant. WARN if tangentially relevant. "
                               "FAIL if no apparent relevance.",
            ),
        ],
    },

    # ── PEP Declaration Form ──
    "pep_declaration": {
        "doc_name": "PEP Declaration Form",
        "category": "person",
        "conditional": None,
        "doc_type_alias": "pep-declaration",
        "checks": [
            _check(
                id_="DOC-70",
                label="Completeness",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Required field presence is deterministic.",
                logic="Validate all mandatory PEP fields populated: public function, net worth, "
                      "origin of funds, period of office.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="presence",
            ),
            _check(
                id_="DOC-71",
                label="Source of Wealth Evidence",
                classification=CheckClassification.AI,
                ps_field=PSField.PERSON_SOW,
                why="Matching supporting documents to declared wealth when evidence is narrative "
                    "or indirect requires interpretation, not pattern matching.",
                logic="AI evaluates whether supporting documents substantiate declared source of "
                      "wealth. Considers document types, amounts, timelines, narrative consistency.",
                trigger=TriggerTiming.ASYNC_AI,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess whether this PEP declaration's source of wealth evidence "
                               "substantiates the declared source of wealth. Consider document types, "
                               "amounts, and narrative consistency. PASS if convincingly supported. "
                               "WARN if partially supported. FAIL if unsupported or contradictory.",
            ),
            _check(
                id_="DOC-72",
                label="Consistency / Plausibility",
                classification=CheckClassification.AI,
                ps_field=PSField.PEP_FUNCTION,
                why="Plausibility of declared assets vs public function is inherently judgmental.",
                logic="AI assesses whether declared wealth, net worth, and source of funds are "
                      "plausible given stated public function, jurisdiction, period of office.",
                trigger=TriggerTiming.ASYNC_AI,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess whether the declared wealth and source of funds are plausible "
                               "for the stated public function and period of office. "
                               "PASS if plausible. WARN if borderline. FAIL if implausible.",
            ),
        ],
    },

    # ── Bank Reference Letter (PEP — required for PEPs) ──
    "bankref_pep": {
        "doc_name": "Bank Reference Letter (PEP)",
        "category": "person",
        "conditional": None,
        "doc_type_alias": "bankref",
        "checks": [
            _check(
                id_="DOC-65",
                label="Document Date",
                classification=CheckClassification.RULE,
                ps_field=None,
                why="Date arithmetic is deterministic.",
                logic="Extract letter date. Must be within 90 days.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="date",
            ),
            _check(
                id_="DOC-66",
                label="Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.PERSON_FULL_NAME,
                why="Identity name comparison is deterministic.",
                logic="Extract name from letter. Normalise and compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-67",
                label="Bank Identification",
                classification=CheckClassification.HYBRID,
                ps_field=None,
                why="Official bank identification can often be rule-checked but formats vary.",
                logic="Check for bank name, letterhead, branch, SWIFT/BIC markers. "
                      "Escalate weak or unusual layouts.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Determine whether this is on official bank letterhead with identifiable "
                               "bank details (name, branch, SWIFT/BIC). PASS if clearly identified. "
                               "WARN if partial details. FAIL if bank cannot be identified.",
            ),
            _check(
                id_="DOC-68",
                label="Account Standing",
                classification=CheckClassification.AI,
                ps_field=None,
                why="Banks use highly variable language for account standing. "
                    "Keyword matching is unreliable. Requires semantic interpretation.",
                logic="AI reads the reference letter, interprets the bank's assessment of account "
                      "standing. Determines whether confirmed in good standing for ≥12 months.",
                trigger=TriggerTiming.ASYNC_AI,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Read this bank reference letter. Determine whether it confirms the "
                               "account has been in good standing for at least 12 months. "
                               "PASS if clearly confirmed. WARN if duration unclear. "
                               "FAIL if not in good standing or adverse wording detected.",
            ),
            CERTIFICATION_CHECK,
        ],
    },

    # ── Source of Wealth Declaration ──
    "sow": {
        "doc_name": "Source of Wealth Declaration",
        "category": "person",
        "conditional": None,
        "checks": [
            _check(
                id_="DOC-59",
                label="Name Match",
                classification=CheckClassification.RULE,
                ps_field=PSField.PERSON_FULL_NAME,
                why="Identity name comparison is deterministic.",
                logic="Extract name from declaration. Normalise and compare with ps_field.",
                trigger=TriggerTiming.AFTER_OCR,
                escalation=EscalationOutcome.FAIL,
                rule_type="name",
            ),
            _check(
                id_="DOC-60",
                label="Supporting Evidence",
                classification=CheckClassification.AI,
                ps_field=PSField.PERSON_SOW,
                why="Assessing whether evidence substantiates declared source of wealth requires "
                    "interpretation, not pattern matching.",
                logic="AI evaluates whether supporting documents contain credible evidence of "
                      "wealth origin consistent with declared source.",
                trigger=TriggerTiming.ASYNC_AI,
                escalation=EscalationOutcome.ESCALATE,
                ai_prompt_hint="Assess whether this source of wealth declaration contains credible "
                               "supporting evidence. PASS if credible evidence present. "
                               "WARN if evidence weak or incomplete. FAIL if no credible evidence.",
            ),
        ],
    },
}


# ── All checks combined — used for db seed and agent registration ──
ALL_DOC_CHECKS = {**SECTION_A_CHECKS, **SECTION_B_CHECKS}


def get_checks_for_doc_type(doc_type: str, category: str = "entity") -> list:
    """
    Return the check definitions for a given doc_type + category pair.
    Returns empty list for retired doc types (cert_reg).
    Handles poa disambiguation by category.
    """
    if doc_type == "poa":
        if category == "person":
            entry = ALL_DOC_CHECKS.get("poa_person", {})
        else:
            entry = ALL_DOC_CHECKS.get("poa", {})
    else:
        entry = ALL_DOC_CHECKS.get(doc_type, {})

    if entry.get("retired"):
        return []
    return entry.get("checks", [])


def get_ai_checks_for_doc_type(doc_type: str, category: str = "entity") -> list:
    """Return only AI and hybrid checks (those that go to Claude)."""
    all_checks = get_checks_for_doc_type(doc_type, category)
    return [c for c in all_checks
            if c["classification"] in (CheckClassification.AI, CheckClassification.HYBRID)]


def get_rule_checks_for_doc_type(doc_type: str, category: str = "entity") -> list:
    """Return only rule-based checks (deterministic, never call AI)."""
    all_checks = get_checks_for_doc_type(doc_type, category)
    return [c for c in all_checks if c["classification"] == CheckClassification.RULE]


def is_licence_applicable(prescreening_data: dict) -> bool:
    """
    Check whether the regulatory licence document is applicable for this application.
    Returns False if the client declared no licence (value is 'None', 'none', empty, or null).
    """
    if not prescreening_data:
        return False
    val = prescreening_data.get(PSField.HOLDS_LICENCE, "")
    if not val:
        return False
    val_stripped = str(val).strip().lower()
    return val_stripped not in ("none", "n/a", "na", "no", "")


def build_ai_checks_seed() -> list:
    """
    Build the ai_checks seed data from this matrix.
    Returns list of (category, doc_type, doc_name, checks_json) tuples.
    Used by db.py to seed the ai_checks table.

    Only includes checks that go to Claude (classification: ai or hybrid).
    The check list retains classification field for routing in the engine.
    """
    import json
    seed = []
    for key, entry in ALL_DOC_CHECKS.items():
        if entry.get("retired"):
            continue
        category = entry.get("category", "entity")
        doc_type = entry.get("doc_type_alias") or key
        if key == "poa_person":
            doc_type = "poa"
        doc_name = entry["doc_name"]
        # Build serialisable check list (all checks, with classification)
        checks = []
        for c in entry.get("checks", []):
            checks.append({
                "id": c["id"],
                "label": c["label"],
                "type": c.get("rule_type") or c["classification"],
                "classification": c["classification"],
                "rule": c["logic"],
                "ai_prompt_hint": c.get("ai_prompt_hint", ""),
                "ps_field": c.get("ps_field", ""),
                "escalation": c.get("escalation", "fail"),
            })
        seed.append((category, doc_type, doc_name, json.dumps(checks)))
    return seed


# ── Quick summary for logging / admin ─────────────────────────────
def summarise_matrix() -> dict:
    """Return a summary of the verification matrix for logging/admin."""
    total = rule = hybrid = ai = 0
    docs = {}
    for key, entry in ALL_DOC_CHECKS.items():
        if entry.get("retired"):
            continue
        checks = entry.get("checks", [])
        r = sum(1 for c in checks if c["classification"] == CheckClassification.RULE)
        h = sum(1 for c in checks if c["classification"] == CheckClassification.HYBRID)
        a = sum(1 for c in checks if c["classification"] == CheckClassification.AI)
        total += len(checks)
        rule += r
        hybrid += h
        ai += a
        docs[key] = {"doc_name": entry["doc_name"], "rule": r, "hybrid": h, "ai": a,
                     "total": len(checks), "conditional": entry.get("conditional")}
    return {
        "total_checks": total,
        "rule": rule,
        "hybrid": hybrid,
        "ai": ai,
        "documents": docs,
    }
