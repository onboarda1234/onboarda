"""
Onboarda — Document Verification Engine (Agent 1)
==================================================
Implements the layered verification pipeline:

  Layer 0: Gate checks (file format, size, duplicate, applicability)
  Layer 1: Rule-based checks (deterministic, no AI)
  Layer 2: Hybrid checks (rules first, AI fallback on INCONCLUSIVE)
  Layer 3: AI checks (genuine interpretation, always via Claude)
  Layer 4: Aggregation + routing

The verification_matrix.py module is the single source of truth for check
definitions. This engine executes them.

API contract (unchanged from original verify_document flow):
  Returns:
    {
      "checks": [{"id", "label", "type", "classification", "result", "message",
                  "ps_field", "ps_value", "extracted_value", "confidence", "source"}, ...],
      "overall": "verified" | "flagged",
      "confidence": 0.0–1.0,
      "red_flags": [...],
      "engine_version": "layered_v1"
    }

Document authenticity: treated as suspicion/escalation signal only.
AI never makes final onboarding approval/rejection decisions.
"""

import os
import re
import json
import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from verification_matrix import (
    GATE_CHECKS,
    SECTION_A_CHECKS,
    SECTION_B_CHECKS,
    ALL_DOC_CHECKS,
    CheckClassification,
    CheckStatus,
    TriggerTiming,
    EscalationOutcome,
    PSField,
    get_checks_for_doc_type,
    get_ai_checks_for_doc_type,
    get_rule_checks_for_doc_type,
    is_licence_applicable,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024   # 25MB
ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/jpg"}
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
ALLOWED_MAGIC_BYTES = {
    b"%PDF": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG": "image/png",
}
NAME_MATCH_PASS_THRESHOLD = 0.90       # ≥90% similarity = pass
NAME_MATCH_WARN_THRESHOLD = 0.70       # 70-89% = warn; <70% = fail
DATE_WINDOW_3_MONTHS  = 90             # days
DATE_WINDOW_12_MONTHS = 365
DATE_WINDOW_18_MONTHS = 548
DATE_WINDOW_6_MONTHS  = 182
UBO_THRESHOLD_PCT = 25.0               # ≥25% shareholding → must be declared UBO


# ── Result builder helpers ─────────────────────────────────────────

def _result(id_, label, classification, result, message,
            ps_field=None, ps_value=None, extracted_value=None,
            confidence=None, source="rule", rule_type=None):
    """Build a single check result dict."""
    out = {
        "id": id_,
        "label": label,
        "classification": classification,
        "type": rule_type or classification,
        "result": result,
        "message": message,
        "source": source,
    }
    if ps_field:
        out["ps_field"] = ps_field
    if ps_value is not None:
        out["ps_value"] = str(ps_value)
    if extracted_value is not None:
        out["extracted_value"] = str(extracted_value)
    if confidence is not None:
        out["confidence"] = round(float(confidence), 3)
    return out


def _pass(id_, label, classification, message, **kw):
    return _result(id_, label, classification, CheckStatus.PASS, message, **kw)


def _warn(id_, label, classification, message, **kw):
    return _result(id_, label, classification, CheckStatus.WARN, message, **kw)


def _fail(id_, label, classification, message, **kw):
    return _result(id_, label, classification, CheckStatus.FAIL, message, **kw)


def _skip(id_, label, classification, message, **kw):
    return _result(id_, label, classification, CheckStatus.SKIP, message, source="gate", **kw)


# ── Name normalisation & fuzzy matching ───────────────────────────

def _normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not name:
        return ""
    n = name.lower()
    n = re.sub(r"[.,'\-]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _legal_suffix_strip(name: str) -> str:
    """Remove common legal suffixes for comparison."""
    suffixes = [
        "limited", "ltd", "llc", "l.l.c", "inc", "incorporated", "corp",
        "corporation", "plc", "p.l.c", "llp", "lp", "sa", "sas", "sarl",
        "bv", "nv", "gmbh", "ag", "pty", "pty ltd", "co", "company",
    ]
    n = _normalise_name(name)
    for sfx in sorted(suffixes, key=len, reverse=True):
        if n.endswith(" " + sfx):
            n = n[: -(len(sfx) + 1)].rstrip()
            break
    return n.strip()


def _name_similarity(a: str, b: str) -> float:
    """
    Simple trigram similarity between two normalised names.
    Returns 0.0–1.0.
    """
    if not a or not b:
        return 0.0
    a = _legal_suffix_strip(a)
    b = _legal_suffix_strip(b)
    if a == b:
        return 1.0
    # Exact match after normalisation
    if _normalise_name(a) == _normalise_name(b):
        return 1.0

    def trigrams(s):
        s = " " + s + " "
        return {s[i:i+3] for i in range(len(s) - 2)}

    tg_a = trigrams(a)
    tg_b = trigrams(b)
    intersection = tg_a & tg_b
    union = tg_a | tg_b
    return len(intersection) / len(union) if union else 0.0


def _check_name_match(id_, label, extracted: str, declared: str,
                      classification=CheckClassification.RULE) -> dict:
    """Run a name match check and return result dict."""
    if not extracted:
        return _fail(id_, label, classification,
                     "Name could not be extracted from document — manual review required",
                     ps_field=label, ps_value=declared, extracted_value=extracted)
    sim = _name_similarity(extracted, declared)
    if sim >= NAME_MATCH_PASS_THRESHOLD:
        return _pass(id_, label, classification,
                     f"Name match confirmed ({int(sim*100)}%)",
                     ps_field=label, ps_value=declared, extracted_value=extracted,
                     confidence=sim, rule_type="name")
    if sim >= NAME_MATCH_WARN_THRESHOLD:
        return _warn(id_, label, classification,
                     f"Name partially matches ({int(sim*100)}%) — verify manually",
                     ps_field=label, ps_value=declared, extracted_value=extracted,
                     confidence=sim, rule_type="name")
    return _fail(id_, label, classification,
                 f"Name mismatch: document has '{extracted}', declared is '{declared}' ({int(sim*100)}%)",
                 ps_field=label, ps_value=declared, extracted_value=extracted,
                 confidence=sim, rule_type="name")


# ── Date checking ──────────────────────────────────────────────────

def _parse_date(val) -> Optional[date]:
    """Try several common date formats, return date or None."""
    if not val:
        return None
    if isinstance(val, (date, datetime)):
        return val.date() if isinstance(val, datetime) else val
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %B %Y",
                "%d-%m-%Y", "%B %d, %Y", "%d %b %Y", "%Y"):
        try:
            return datetime.strptime(str(val).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _check_date_recency(id_, label, extracted_date_str, max_days: int,
                        classification=CheckClassification.RULE) -> dict:
    """Check a document date is within max_days of today."""
    d = _parse_date(extracted_date_str)
    if not d:
        return _warn(id_, label, classification,
                     "Date could not be extracted — manual verification required",
                     rule_type="date")
    delta = (date.today() - d).days
    if delta < 0:
        return _pass(id_, label, classification,
                     f"Date is {abs(delta)} days in future (valid)", rule_type="date")
    if delta <= max_days:
        return _pass(id_, label, classification,
                     f"Date within required window ({delta} days old)", rule_type="date")
    warn_threshold = max_days * 2
    if delta <= warn_threshold:
        return _warn(id_, label, classification,
                     f"Date is {delta} days old (window is {max_days} days) — verify",
                     rule_type="date")
    return _fail(id_, label, classification,
                 f"Date is {delta} days old, exceeds {max_days}-day policy window",
                 rule_type="date")


def _check_not_expired(id_, label, expiry_date_str,
                       warn_days=30, classification=CheckClassification.RULE) -> dict:
    """Check a document expiry date has not passed."""
    d = _parse_date(expiry_date_str)
    if not d:
        return _warn(id_, label, classification,
                     "Expiry date could not be extracted — manual verification required",
                     rule_type="date")
    days_to_expiry = (d - date.today()).days
    if days_to_expiry < 0:
        return _fail(id_, label, classification,
                     f"Document expired {abs(days_to_expiry)} days ago",
                     rule_type="date")
    if days_to_expiry <= warn_days:
        return _warn(id_, label, classification,
                     f"Document expires in {days_to_expiry} days — renewal recommended",
                     rule_type="date")
    return _pass(id_, label, classification,
                 f"Document valid for {days_to_expiry} more days", rule_type="date")


# ── Gate checks ────────────────────────────────────────────────────

def run_gate_checks(file_path: str, file_size: int, mime_type: str,
                   existing_hashes: List[str]) -> List[dict]:
    """
    Layer 0: Gate checks. Run before any OCR/AI processing.
    Return list of check result dicts.
    """
    results = []

    # GATE-01: File format
    file_exists = bool(file_path and os.path.isfile(file_path))
    ext = os.path.splitext(file_path)[1].lower() if file_path else ""
    magic_ok = False
    if file_exists:
        try:
            with open(file_path, "rb") as f:
                header = f.read(8)
            for magic, _ in ALLOWED_MAGIC_BYTES.items():
                if header.startswith(magic):
                    magic_ok = True
                    break
        except OSError:
            pass

    if not file_exists:
        # No file available — gate check cannot pass
        results.append(_fail("GATE-01", "File Format", CheckClassification.RULE,
                             "File not accessible for format verification — "
                             "this is a system issue, not a document problem.",
                             rule_type="enum"))
    else:
        mime_ok = mime_type in ALLOWED_MIME_TYPES if mime_type else False
        ext_ok = ext in ALLOWED_EXTENSIONS
        if (mime_ok or ext_ok) and magic_ok:
            results.append(_pass("GATE-01", "File Format", CheckClassification.RULE,
                                 f"File format accepted ({ext or mime_type})", rule_type="enum"))
        else:
            results.append(_fail("GATE-01", "File Format", CheckClassification.RULE,
                                 f"File format not accepted: {mime_type} / {ext}. "
                                 "Only PDF, JPEG, PNG are allowed.", rule_type="enum"))

    # GATE-02: File size
    if file_size and file_size > MAX_FILE_SIZE_BYTES:
        results.append(_fail("GATE-02", "File Size", CheckClassification.RULE,
                             f"File size {file_size // (1024*1024)}MB exceeds 25MB limit",
                             rule_type="numeric"))
    else:
        results.append(_pass("GATE-02", "File Size", CheckClassification.RULE,
                             f"File size within limit ({file_size // 1024 if file_size else '?'}KB)",
                             rule_type="numeric"))

    # GATE-03: Duplicate detection
    if file_exists:
        try:
            h = hashlib.sha256(open(file_path, "rb").read()).hexdigest()
            if existing_hashes and h in existing_hashes:
                results.append(_warn("GATE-03", "Duplicate Detection", CheckClassification.RULE,
                                     "This file has already been uploaded for this application — "
                                     "please confirm this is intentional", rule_type="hash"))
            else:
                results.append(_pass("GATE-03", "Duplicate Detection", CheckClassification.RULE,
                                     "No duplicate detected", rule_type="hash"))
        except OSError:
            results.append(_warn("GATE-03", "Duplicate Detection", CheckClassification.RULE,
                                 "Duplicate check skipped — file not accessible", rule_type="hash"))
    else:
        results.append(_warn("GATE-03", "Duplicate Detection", CheckClassification.RULE,
                             "Duplicate check skipped — file not accessible (system issue)",
                             rule_type="hash"))

    return results


# ── Rule-based check execution ─────────────────────────────────────

def run_rule_checks(doc_type: str, category: str,
                   extracted_fields: dict,
                   prescreening_data: dict,
                   risk_level: str = "LOW") -> List[dict]:
    """
    Layer 1: Deterministic rule checks.
    extracted_fields: dict of fields extracted from the document (by OCR/Claude vision).
    prescreening_data: dict from applications.prescreening_data.
    Returns list of check result dicts.
    """
    results = []
    ps = prescreening_data or {}
    ef = extracted_fields or {}
    today = date.today()

    def ps_get(*keys):
        """Get first non-empty value from prescreening_data for any of the given keys."""
        for k in keys:
            v = ps.get(k)
            if v not in (None, "", [], {}):
                return v
        return None

    checks = get_rule_checks_for_doc_type(doc_type, category)

    for chk in checks:
        id_ = chk["id"]
        label = chk["label"]
        cls = CheckClassification.RULE
        rtype = chk.get("rule_type")

        # ── Entity Name Match ──
        if label in ("Entity Name Match", "Signatory Match", "Name Match") and rtype == "name":
            declared = ps_get(PSField.COMPANY_NAME, "company_name",
                              PSField.PERSON_FULL_NAME, "full_name",
                              "registered_entity_name", "entity_name")
            extracted = ef.get("entity_name") or ef.get("name") or ef.get("company_name", "")
            if not declared:
                results.append(_warn(id_, label, cls,
                                     "No declared name found in pre-screening to compare against",
                                     rule_type=rtype))
                continue
            results.append(_check_name_match(id_, label, extracted, declared, cls))

        # ── Registration Number Match ──
        elif id_ == "DOC-06":
            declared = ps_get(PSField.INCORPORATION_NUMBER, "incorporation_number",
                              "registration_number", "brn")
            extracted = ef.get("registration_number") or ef.get("incorporation_number", "")
            if not declared:
                results.append(_warn(id_, label, cls,
                                     "Incorporation number not declared in pre-screening",
                                     rule_type=rtype))
                continue
            if not extracted:
                results.append(_warn(id_, label, cls,
                                     "Registration number could not be extracted — manual check required",
                                     rule_type=rtype))
                continue
            # Normalise: strip spaces and hyphens for comparison
            d_norm = re.sub(r"[\s\-]", "", str(declared).upper())
            e_norm = re.sub(r"[\s\-]", "", str(extracted).upper())
            if d_norm == e_norm:
                results.append(_pass(id_, label, cls, f"Registration number matches ({extracted})",
                                     ps_field=PSField.INCORPORATION_NUMBER,
                                     ps_value=declared, extracted_value=extracted,
                                     rule_type=rtype))
            else:
                results.append(_fail(id_, label, cls,
                                     f"Registration number mismatch: document has '{extracted}', "
                                     f"declared is '{declared}'",
                                     ps_field=PSField.INCORPORATION_NUMBER,
                                     ps_value=declared, extracted_value=extracted,
                                     rule_type=rtype))

        # ── Document Date / Recency ──
        elif rtype == "date" and id_ in ("DOC-01", "DOC-61", "DOC-31", "DOC-65"):
            # 3-month recency window
            extracted_date = ef.get("document_date") or ef.get("date")
            results.append(_check_date_recency(id_, label, extracted_date,
                                               DATE_WINDOW_3_MONTHS, cls))

        # ── Resolution Date ──
        elif id_ == "DOC-25":
            extracted_date = ef.get("resolution_date") or ef.get("date")
            results.append(_check_date_recency(id_, label, extracted_date,
                                               DATE_WINDOW_12_MONTHS, cls))

        # ── Financial Period ──
        elif id_ == "DOC-20":
            extracted_date = ef.get("financial_year_end") or ef.get("period_end") or ef.get("date")
            results.append(_check_date_recency(id_, label, extracted_date,
                                               DATE_WINDOW_18_MONTHS, cls))

        # ── Document Expiry (passport, national_id, licence) ──
        elif rtype == "date" and "expiry" in label.lower() or id_ in ("DOC-49", "DOC-53", "DOC-34"):
            extracted_date = ef.get("expiry_date") or ef.get("expiry") or ef.get("validity_to")
            warn_days = 180 if id_ in ("DOC-49", "DOC-53") else 30
            results.append(_check_not_expired(id_, label, extracted_date, warn_days, cls))

        # ── Date of Birth Match ──
        elif id_ == "DOC-49A":
            declared_dob = ps_get(PSField.PERSON_DOB, "date_of_birth", "dob")
            extracted_dob = ef.get("date_of_birth") or ef.get("dob")
            if not declared_dob:
                results.append(_warn(id_, label, cls,
                                     "Date of birth not declared in pre-screening",
                                     rule_type=rtype))
                continue
            if not extracted_dob:
                results.append(_warn(id_, label, cls,
                                     "Date of birth could not be extracted — manual check required",
                                     rule_type=rtype))
                continue
            d_d = _parse_date(declared_dob)
            d_e = _parse_date(extracted_dob)
            if d_d and d_e and d_d == d_e:
                results.append(_pass(id_, label, cls, f"Date of birth matches ({extracted_dob})",
                                     ps_field=PSField.PERSON_DOB,
                                     ps_value=str(declared_dob), extracted_value=str(extracted_dob),
                                     rule_type=rtype))
            else:
                results.append(_fail(id_, label, cls,
                                     f"Date of birth mismatch: document has '{extracted_dob}', "
                                     f"declared is '{declared_dob}'",
                                     ps_field=PSField.PERSON_DOB,
                                     ps_value=str(declared_dob), extracted_value=str(extracted_dob),
                                     rule_type=rtype))

        # ── Nationality Match ──
        elif id_ in ("DOC-52", "DOC-56"):
            declared_nat = ps_get(PSField.PERSON_NATIONALITY, "nationality", "country_of_nationality")
            extracted_nat = ef.get("nationality") or ef.get("country")
            if not declared_nat or not extracted_nat:
                results.append(_warn(id_, label, cls,
                                     "Nationality not extractable or not declared — manual check required",
                                     rule_type=rtype))
                continue
            # Normalise to 2-letter ISO or full name comparison
            d_n = _normalise_name(declared_nat)
            e_n = _normalise_name(extracted_nat)
            if d_n == e_n or d_n[:3] == e_n[:3]:
                results.append(_pass(id_, label, cls, f"Nationality matches ({extracted_nat})",
                                     ps_field=PSField.PERSON_NATIONALITY,
                                     ps_value=declared_nat, extracted_value=extracted_nat,
                                     rule_type=rtype))
            else:
                results.append(_fail(id_, label, cls,
                                     f"Nationality mismatch: document has '{extracted_nat}', "
                                     f"declared is '{declared_nat}'",
                                     ps_field=PSField.PERSON_NATIONALITY,
                                     ps_value=declared_nat, extracted_value=extracted_nat,
                                     rule_type=rtype))

        # ── Shareholding Percentages Match ──
        elif id_ == "DOC-15":
            declared_shareholders = ps_get(PSField.SHAREHOLDERS, "shareholders", "ubos")
            extracted_holders = ef.get("shareholders", [])
            if not declared_shareholders:
                results.append(_warn(id_, label, cls,
                                     "Shareholders not declared in pre-screening",
                                     rule_type=rtype))
                continue
            if not extracted_holders:
                results.append(_warn(id_, label, cls,
                                     "Shareholding data could not be extracted — manual check required",
                                     rule_type=rtype))
                continue
            # Simple check: count match
            results.append(_pass(id_, label, cls,
                                 f"Shareholding data extracted for {len(extracted_holders)} holders",
                                 rule_type=rtype))

        # ── Total Shares Sum to 100% ──
        elif id_ == "DOC-15A":
            holders = ef.get("shareholders", [])
            if not holders:
                results.append(_warn(id_, label, cls,
                                     "Shareholding data not extracted — cannot verify total",
                                     rule_type=rtype))
                continue
            total = sum(float(h.get("percentage", 0)) for h in holders
                        if h.get("percentage") is not None)
            if abs(total - 100.0) <= 0.01:
                results.append(_pass(id_, label, cls, f"Total shareholdings = {total:.1f}%",
                                     rule_type=rtype))
            elif 95.0 <= total <= 100.01:
                results.append(_warn(id_, label, cls,
                                     f"Total shareholdings = {total:.1f}% (rounding tolerance)",
                                     rule_type=rtype))
            else:
                results.append(_fail(id_, label, cls,
                                     f"Total shareholdings = {total:.1f}% — does not sum to 100%",
                                     rule_type=rtype))

        # ── UBO Identification (≥25%) ──
        elif id_ == "DOC-15B":
            declared_ubos = ps_get(PSField.UBOS, "ubos") or []
            extracted_holders = ef.get("shareholders", [])
            if not isinstance(declared_ubos, list):
                declared_ubos = []
            declared_ubo_names = [_normalise_name(u.get("full_name", u) if isinstance(u, dict) else u)
                                  for u in declared_ubos]
            over_threshold = [h for h in extracted_holders
                              if float(h.get("percentage", 0)) >= UBO_THRESHOLD_PCT]
            missing = []
            for holder in over_threshold:
                hname = _normalise_name(holder.get("name", ""))
                if not any(_name_similarity(hname, ubo) >= NAME_MATCH_WARN_THRESHOLD
                           for ubo in declared_ubo_names):
                    missing.append(holder.get("name", "unknown"))
            if missing:
                results.append(_fail(id_, label, cls,
                                     f"Shareholder(s) with ≥25% not declared as UBO: {', '.join(missing)}",
                                     rule_type=rtype))
            else:
                results.append(_pass(id_, label, cls,
                                     "All shareholders ≥25% are declared as UBOs",
                                     rule_type=rtype))

        # ── Director Completeness (set comparison) ──
        elif id_ == "DOC-18":
            declared_dirs = ps_get(PSField.DIRECTORS, "directors") or []
            extracted_dirs = ef.get("directors", [])
            if not isinstance(declared_dirs, list):
                declared_dirs = []
            if not extracted_dirs:
                results.append(_warn(id_, label, cls,
                                     "Director list could not be extracted — manual check required",
                                     rule_type=rtype))
                continue
            extracted_names = [_normalise_name(d.get("name", d) if isinstance(d, dict) else d)
                               for d in extracted_dirs]
            missing = []
            for d in declared_dirs:
                dname = _normalise_name(d.get("full_name", d.get("name", d))
                                        if isinstance(d, dict) else d)
                if not any(_name_similarity(dname, e) >= NAME_MATCH_WARN_THRESHOLD
                           for e in extracted_names):
                    missing.append(dname)
            if missing:
                results.append(_fail(id_, label, cls,
                                     f"Declared director(s) not found in register: {', '.join(missing)}",
                                     rule_type=rtype))
            else:
                results.append(_pass(id_, label, cls, "All declared directors found in register",
                                     rule_type=rtype))

        # ── Ownership Match (structure chart vs pre-screening) ──
        elif id_ == "DOC-28":
            results.append(_warn(id_, label, cls,
                                 "Ownership match: cross-document comparison requires extracted data",
                                 rule_type=rtype))

        # ── CV Employment History — Presence ──
        elif id_ == "DOC-57A":
            has_history = ef.get("has_employment_history", False)
            if has_history:
                results.append(_pass(id_, label, cls,
                                     "Employment history entries found in document",
                                     rule_type="presence"))
            else:
                results.append(_fail(id_, label, cls,
                                     "No substantive employment history found — document may be incomplete",
                                     rule_type="presence"))

        # ── PEP Declaration Completeness ──
        elif id_ == "DOC-70":
            required_fields = ef.get("pep_required_fields", {})
            missing_fields = [k for k, v in required_fields.items() if not v]
            if missing_fields:
                results.append(_fail(id_, label, cls,
                                     f"PEP declaration missing required fields: {', '.join(missing_fields)}",
                                     rule_type="presence"))
            else:
                results.append(_pass(id_, label, cls,
                                     "All required PEP declaration fields are present",
                                     rule_type="presence"))

        else:
            # Unknown rule check — return warn rather than silently skip
            results.append(_warn(id_, label, cls,
                                 f"Rule check not implemented for id={id_} — manual review required",
                                 rule_type=rtype))

    return results


# ── Aggregation ────────────────────────────────────────────────────

def _aggregate(all_results: List[dict], confidence: float = None) -> dict:
    """
    Layer 4: Aggregate all check results into a document-level outcome.
    """
    if not all_results:
        return {
            "checks": [],
            "overall": "flagged",
            "confidence": 0.0,
            "red_flags": ["No checks were executed — manual review required"],
            "engine_version": "layered_v1",
        }

    fail_results = [r for r in all_results if r.get("result") == CheckStatus.FAIL]
    warn_results = [r for r in all_results if r.get("result") == CheckStatus.WARN]
    pass_results = [r for r in all_results if r.get("result") == CheckStatus.PASS]

    red_flags = [r["message"] for r in fail_results]
    warnings   = [r["message"] for r in warn_results]

    if fail_results:
        overall = "flagged"
    elif warn_results:
        overall = "flagged"
    else:
        overall = "verified"

    if confidence is None:
        n = len([r for r in all_results if r.get("result") != CheckStatus.SKIP])
        confidence = len(pass_results) / n if n else 0.0

    return {
        "checks": all_results,
        "overall": overall,
        "confidence": round(confidence, 3),
        "red_flags": red_flags,
        "warnings": warnings,
        "engine_version": "layered_v1",
    }


# ── Main entry point ───────────────────────────────────────────────

def verify_document_layered(
    doc_type: str,
    category: str,
    file_path: Optional[str],
    file_size: int,
    mime_type: str,
    prescreening_data: dict,
    risk_level: str,
    existing_hashes: List[str],
    claude_client=None,
    entity_name: str = "",
    person_name: str = "",
    directors: List[str] = None,
    ubos: List[str] = None,
    check_overrides: Optional[List[dict]] = None,
    file_name: str = "",
) -> dict:
    """
    Main verification entry point for Agent 1.

    Runs the full 4-layer pipeline:
      L0: Gate checks
      L1: Rule checks (deterministic Python)
      L2: Hybrid checks (rules first, AI fallback)
      L3: AI checks (Claude)
      L4: Aggregation

    Args:
        doc_type:          Normalised document type (e.g. 'cert_inc', 'passport')
        category:          'entity' or 'person'
        file_path:         Local file path (may be None)
        file_size:         File size in bytes
        mime_type:         MIME type from upload
        prescreening_data: From applications.prescreening_data
        risk_level:        'LOW'|'MEDIUM'|'HIGH'|'VERY_HIGH'
        existing_hashes:   SHA-256 hashes of other files already uploaded for this app
        claude_client:     ClaudeClient instance (or None)
        entity_name:       Company name (for AI context)
        person_name:       Person name (for AI context)
        directors:         List of declared director names
        ubos:              List of declared UBO names
        check_overrides:   Optional override check list from ai_checks DB table
        file_name:         Original upload filename

    Returns: aggregated result dict (backward-compatible with existing verify_document output)
    """
    all_results = []

    # ── Conditional gate: licence applicability ──────────────────
    if doc_type == "licence":
        if not is_licence_applicable(prescreening_data):
            return _aggregate([_skip("LIC-GATE", "Licence Applicability Gate",
                                     CheckClassification.RULE,
                                     "Regulatory licence checks skipped — client declared no licence",
                                     ps_field=PSField.HOLDS_LICENCE)])

    # ── Retired document type ────────────────────────────────────
    entry = ALL_DOC_CHECKS.get(doc_type, {})
    if entry.get("retired"):
        return _aggregate([_skip("RETIRED", doc_type.upper(),
                                 CheckClassification.RULE,
                                 f"Verification checks for '{doc_type}' have been retired. "
                                 "Historical records preserved.")])

    # ── Pre-check: file accessibility ────────────────────────────
    file_accessible = bool(file_path and os.path.isfile(file_path))
    if not file_accessible:
        logger.warning(f"[verify-layered] File not accessible for {doc_type}: file_path={file_path!r}")

    # ── Layer 0: Gate checks ──────���───────────────────────────────
    gate_results = run_gate_checks(file_path or "", file_size, mime_type, existing_hashes)
    all_results.extend(gate_results)

    gate_hard_fail = any(r["result"] == CheckStatus.FAIL and r["id"].startswith("GATE")
                         for r in gate_results)
    if gate_hard_fail:
        return _aggregate(all_results)

    # ── Extract document fields via Claude vision ──────────────���──
    # Claude extracts structured fields; rule engine then evaluates deterministically
    extracted_fields = {}
    if claude_client and file_path and file_accessible:
        try:
            extracted_fields = claude_client.extract_document_fields(
                doc_type=doc_type,
                file_path=file_path,
                file_name=file_name,
                entity_name=entity_name,
                person_name=person_name,
            )
            logger.info(f"Extracted fields for {doc_type}: {list(extracted_fields.keys())}")
        except Exception as e:
            logger.warning(f"Field extraction failed for {doc_type}: {e} — rules will use available data")

    # ── Layer 1: Rule-based checks ────────────────────────────────
    rule_results = run_rule_checks(doc_type, category, extracted_fields, prescreening_data, risk_level)
    all_results.extend(rule_results)

    # ── Layers 2+3: Hybrid and AI checks via Claude ────��──────────
    if claude_client and not file_accessible:
        # File not accessible — skip AI analysis, mark as system-level inconclusive
        all_results.append(_warn("SYS-FILE", "File Access", CheckClassification.RULE,
                                 "Document file is not accessible — AI verification skipped. "
                                 "This is a system issue, not a document problem. Manual review required.",
                                 source="system"))
    elif claude_client:
        # Determine which checks go to Claude
        if check_overrides:
            # DB overrides take priority, but filter to hybrid/AI only
            ai_hybrid_checks = [c for c in check_overrides
                                 if c.get("classification") in
                                 (CheckClassification.AI, CheckClassification.HYBRID)]
            # Fallback: if override has no classification, include all
            if not ai_hybrid_checks:
                ai_hybrid_checks = check_overrides
        else:
            ai_hybrid_checks = get_ai_checks_for_doc_type(doc_type, category)

        if ai_hybrid_checks:
            try:
                ai_result = claude_client.verify_document(
                    doc_type=doc_type,
                    file_name=file_name,
                    person_name=person_name,
                    doc_category=category,
                    file_path=file_path,
                    check_overrides=ai_hybrid_checks,
                    entity_name=entity_name,
                    directors=directors or [],
                    ubos=ubos or [],
                )

                # P0-2: Guard against rejected/invalid AI responses
                if ai_result.get("_rejected") or ai_result.get("_validated") is False:
                    all_results.append(_warn("AI-VAL", "AI Verification", CheckClassification.AI,
                                            "AI output failed validation — manual review required",
                                            source="ai"))
                else:
                    ai_checks = ai_result.get("checks", [])
                    if not ai_checks:
                        # P0-5: No pass without evidence
                        all_results.append(_warn("AI-EMPTY", "AI Verification", CheckClassification.AI,
                                                 "AI returned no checks — manual review required",
                                                 source="ai"))
                    else:
                        for c in ai_checks:
                            c["source"] = "ai"
                            if "classification" not in c:
                                c["classification"] = CheckClassification.AI
                        all_results.extend(ai_checks)

            except Exception as e:
                logger.error(f"AI verification failed for {doc_type}: {e}")
                all_results.append(_warn("AI-ERR", "AI Verification", CheckClassification.AI,
                                         f"AI verification error: {str(e)[:100]}. Manual review required.",
                                         source="ai_error"))
    else:
        # No AI client — add warn for hybrid/AI checks
        ai_hybrid = get_ai_checks_for_doc_type(doc_type, category)
        if ai_hybrid:
            all_results.append(_warn("AI-UNAVAIL", "AI Verification", CheckClassification.AI,
                                     "AI client unavailable — hybrid/AI checks require manual review",
                                     source="ai_unavailable"))

    # ── Layer 4: Aggregate ────────────────────────────────────────
    ai_confidence = None
    if claude_client:
        try:
            last_ai = [r for r in all_results if r.get("source") == "ai"]
            ai_confidence = None  # Will be computed in _aggregate
        except Exception:
            pass

    return _aggregate(all_results, confidence=ai_confidence)


# ── Public helper: format result for backward compatibility ────────

def to_legacy_result(layered_result: dict) -> dict:
    """
    Convert layered engine result to the legacy verification_results format
    that the back office renderer and existing code expect.

    Legacy format: {"checks": [...], "overall": "verified"|"flagged",
                    "confidence": float, "red_flags": [...]}

    The new format is a superset of the legacy format, so this is mostly
    a pass-through. But it ensures older code paths still work.
    """
    return {
        "checks": layered_result.get("checks", []),
        "overall": layered_result.get("overall", "flagged"),
        "confidence": layered_result.get("confidence", 0.0),
        "red_flags": layered_result.get("red_flags", []),
        "engine_version": layered_result.get("engine_version", "layered_v1"),
        "warnings": layered_result.get("warnings", []),
    }
