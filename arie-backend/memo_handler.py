"""
ARIE Finance — Memo Handler: Compliance Memo Building Logic
Extracted from ComplianceMemoHandler.post() during Sprint 2 monolith decomposition.

Provides:
    - build_compliance_memo(app, directors, ubos, documents) → (memo, rule_engine_result, supervisor_result, validation_result)
"""
import json
import logging
from datetime import datetime

from validation_engine import validate_compliance_memo
from supervisor_engine import run_memo_supervisor
from edd_routing_policy import evaluate_edd_routing as _evaluate_edd_routing
from rule_engine import (
    HIGH_RISK_COUNTRIES, OFFSHORE_COUNTRIES,
    HIGH_RISK_SECTORS, MINIMUM_MEDIUM_SECTORS, MEDIUM_RISK_SECTORS,
    HIGH_RISK_SECTOR_KEYWORDS, OPAQUE_OWNERSHIP_KEYWORDS,
    ALWAYS_RISK_DECREASING, ALWAYS_RISK_INCREASING,
    RISK_WEIGHTS, RISK_RANK,
    SANCTIONED, FATF_BLACK,
    classify_risk_level,
)
from environment import ENV, is_demo

logger = logging.getLogger("arie")


_VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}


def _memo_prescreening_data(raw_value):
    if isinstance(raw_value, dict):
        return raw_value
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            decoded = json.loads(raw_value)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}
    return {}


def _first_party_value(record, *keys):
    if not isinstance(record, dict):
        return ""
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return ""


def _party_full_name(record):
    full_name = _first_party_value(record, "full_name", "name", "person_name", "display_name")
    if full_name:
        return str(full_name).strip()
    first = str(_first_party_value(record, "first_name", "firstName")).strip()
    last = str(_first_party_value(record, "last_name", "lastName", "surname")).strip()
    return f"{first} {last}".strip()


def _normalise_memo_party(record, role):
    if not isinstance(record, dict):
        return None
    full_name = _party_full_name(record)
    if not full_name:
        return None
    normalised = dict(record)
    normalised["full_name"] = full_name
    normalised.setdefault("role", role)
    if "ownership_pct" not in normalised:
        pct = _first_party_value(
            record,
            "ownership_percentage",
            "shareholding_pct",
            "shareholding_percentage",
            "percentage",
            "ownership",
        )
        if pct not in (None, ""):
            normalised["ownership_pct"] = pct
    if "is_pep" not in normalised:
        pep = _first_party_value(record, "pep", "isPEP", "pep_status", "declared_pep")
        if pep not in (None, ""):
            normalised["is_pep"] = pep
    normalised["source"] = normalised.get("source") or "prescreening_data"
    return normalised


def _prescreening_party_list(prescreening_data, role):
    candidates = []
    root_keys = {
        "director": ("directors", "director_details", "board_members"),
        "ubo": ("ubos", "beneficial_owners", "ubo_details", "shareholders"),
    }[role]
    for key in root_keys:
        value = prescreening_data.get(key)
        if isinstance(value, list):
            candidates.extend(value)

    parties = prescreening_data.get("parties")
    if isinstance(parties, dict):
        for key in root_keys:
            value = parties.get(key)
            if isinstance(value, list):
                candidates.extend(value)

    seen = set()
    normalised = []
    for candidate in candidates:
        party = _normalise_memo_party(candidate, role)
        if not party:
            continue
        key = (
            str(party.get("person_key") or "").strip().lower(),
            str(party.get("full_name") or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        normalised.append(party)
    return normalised


def _resolve_memo_parties(directors, ubos, prescreening_data):
    resolved_directors = [dict(d) for d in (directors or []) if isinstance(d, dict)]
    resolved_ubos = [dict(u) for u in (ubos or []) if isinstance(u, dict)]
    source_summary = {
        "directors_source": "party_tables" if resolved_directors else "not_provided",
        "ubos_source": "party_tables" if resolved_ubos else "not_provided",
        "directors_count": len(resolved_directors),
        "ubos_count": len(resolved_ubos),
    }

    if not resolved_directors:
        resolved_directors = _prescreening_party_list(prescreening_data, "director")
        if resolved_directors:
            source_summary["directors_source"] = "prescreening_data"
            source_summary["directors_count"] = len(resolved_directors)
    if not resolved_ubos:
        resolved_ubos = _prescreening_party_list(prescreening_data, "ubo")
        if resolved_ubos:
            source_summary["ubos_source"] = "prescreening_data"
            source_summary["ubos_count"] = len(resolved_ubos)
    return resolved_directors, resolved_ubos, source_summary


def _normalise_risk_level(value):
    if value in (None, ""):
        return None
    candidate = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    return candidate if candidate in _VALID_RISK_LEVELS else None


def _normalise_risk_score(value):
    if value in (None, ""):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0 or score > 100:
        return None
    return int(score) if score.is_integer() else round(score, 2)


def _risk_display_context(app):
    level = _normalise_risk_level(
        app.get("final_risk_level") or app.get("risk_level") or app.get("risk_band")
    )
    score = _normalise_risk_score(
        app.get("final_risk_score")
        if app.get("final_risk_score") not in (None, "")
        else app.get("risk_score")
    )
    available = level is not None and score is not None
    if available:
        assessment = f"{level} — {score}/100"
        summary = f"The recorded canonical risk rating is {level} with a score of {score}/100."
        checklist = f"Canonical risk score reviewed: {score}/100 ({level})"
    else:
        assessment = "not yet rated — no canonical risk score is recorded"
        summary = (
            "No canonical risk rating or score is recorded on the application; "
            "the memo must be treated as not yet risk-rated."
        )
        checklist = "Canonical risk score not recorded — memo must be treated as not yet rated"
    return {
        "available": available,
        "level": level,
        "score": score,
        "assessment": assessment,
        "summary": summary,
        "checklist": checklist,
    }


def _screening_source_summary(screening_report):
    if not isinstance(screening_report, dict) or not screening_report:
        return {"providers": [], "provider": "not_configured", "api_statuses": [], "mode": "not_configured"}
    providers = set()
    statuses = set()

    def _collect(node):
        if not isinstance(node, dict):
            return
        source = node.get("source") or node.get("provider")
        status = node.get("api_status") or node.get("status")
        if source:
            providers.add(str(source))
        if status:
            statuses.add(str(status))

    company = screening_report.get("company_screening") or {}
    if isinstance(company, dict):
        _collect(company)
        _collect(company.get("sanctions") or {})
    for entry in (screening_report.get("director_screenings") or []) + (screening_report.get("ubo_screenings") or []):
        if isinstance(entry, dict):
            _collect(entry)
            _collect(entry.get("screening") or {})

    providers_list = sorted(providers)
    return {
        "providers": sorted(providers),
        "provider": providers_list[0] if providers_list else "not_configured",
        "api_statuses": sorted(statuses),
        "mode": screening_report.get("screening_mode") or ("configured" if providers else "not_configured"),
    }


def _screening_adverse_media_context(screening_report, prescreening_data):
    """
    Return the adverse-media terminality context used by the memo narrative.

    A clean adverse-media statement is defensible only when the report carries
    full adverse-media coverage or a legacy terminal adverse-media status. Sumsub
    normalized reports currently mark adverse_media_coverage='none', so those
    cases must be described as an evidence gap rather than as "no hits".
    """
    report = screening_report if isinstance(screening_report, dict) else {}
    prescreening = prescreening_data if isinstance(prescreening_data, dict) else {}

    coverage = str(
        report.get("adverse_media_coverage")
        or prescreening.get("adverse_media_coverage")
        or "none"
    ).strip().lower() or "none"

    values = []
    for entry in (report.get("director_screenings") or []) + (report.get("ubo_screenings") or []):
        if isinstance(entry, dict) and "has_adverse_media_hit" in entry:
            values.append(entry.get("has_adverse_media_hit"))
    if "has_adverse_media_hit" in report:
        values.append(report.get("has_adverse_media_hit"))

    legacy = report.get("adverse_media") or prescreening.get("adverse_media")
    legacy_status = ""
    if isinstance(legacy, dict):
        legacy_status = str(legacy.get("status") or legacy.get("result") or "").strip().lower()
        if "has_hit" in legacy:
            values.append(bool(legacy.get("has_hit")))
    elif legacy:
        legacy_status = str(legacy).strip().lower()

    hit_terms = ("hit", "match", "adverse", "negative", "concern")
    clear_terms = (
        "clear", "cleared", "completed_clear", "no_hits", "no hit",
        "no relevant hits", "no adverse media", "no adverse-media",
    )
    legacy_clear = any(term in legacy_status for term in clear_terms)
    legacy_hit = bool(legacy_status) and not legacy_clear and any(term in legacy_status for term in hit_terms)
    has_hit = any(v is True for v in values) or legacy_hit

    terminal = False
    if coverage == "full":
        terminal = True
    if legacy_status and (legacy_clear or legacy_hit):
        terminal = True

    if terminal and has_hit:
        phrase = (
            "Adverse Media Screening: terminal adverse-media coverage returned relevant hit(s); "
            "officer disposition and rationale are required before reliance."
        )
        checklist = "Adverse media review completed — relevant hit(s) require documented disposition"
    elif terminal:
        phrase = (
            "Adverse Media Screening: terminal full-coverage adverse-media review completed; "
            "no relevant adverse-media hits are recorded."
        )
        checklist = "Adverse media review completed — no relevant hits recorded"
    else:
        phrase = (
            "Adverse Media Screening: NOT COMPLETE — coverage is "
            + coverage
            + "; no clean adverse-media conclusion is recorded until an approved provider returns terminal coverage."
        )
        checklist = "Adverse media review NOT complete — clean adverse-media reliance is unavailable"

    return {
        "terminal": terminal,
        "coverage": coverage,
        "has_hit": bool(has_hit),
        "phrase": phrase,
        "checklist": checklist,
    }


def _quality_cap(code, max_score, severity, reason, fix):
    return {
        "code": code,
        "max_score": float(max_score),
        "severity": severity,
        "reason": reason,
        "fix": fix,
    }


def build_compliance_memo(app, directors, ubos, documents):
    """
    Build a complete compliance memo from application data.
    Pure computation — no DB or HTTP dependencies.

    Returns:
        tuple: (memo, rule_engine_result, supervisor_result, validation_result)
    """
    prescreening_data = _memo_prescreening_data(app.get("prescreening_data", "{}"))
    directors, ubos, party_source_summary = _resolve_memo_parties(
        directors, ubos, prescreening_data
    )

    # Collect PEP matches — from both declarations and screening results.
    # Priority A.2: accept any truthy form of `is_pep` (Yes/true/1/True)
    # so a declared PEP cannot be flattened to "no PEP" merely because of
    # a non-canonical input shape.
    def _is_declared_pep(person):
        v = person.get("is_pep")
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        return str(v).strip().lower() in ("yes", "true", "1")
    pep_directors = [d for d in directors if _is_declared_pep(d)]
    pep_ubos = [u for u in ubos if _is_declared_pep(u)]
    all_peps = pep_directors + pep_ubos

    # W2-5: Also check screening results for PEP hits not covered by declarations
    screening_report = prescreening_data.get("screening_report", {})
    if not isinstance(screening_report, dict):
        screening_report = {}
    # Check director and UBO screenings for PEP matches
    for entry in (screening_report.get("director_screenings", []) +
                  screening_report.get("ubo_screenings", [])):
        screening_data = entry.get("screening", {}) or {}
        results = screening_data.get("results", []) or []
        has_pep_hit = any(
            isinstance(r, dict) and r.get("is_pep")
            for r in results
        )
        if has_pep_hit:
            name = entry.get("person_name", "")
            already_declared = any(p.get("full_name", "") == name for p in all_peps)
            if not already_declared and name:
                all_peps.append({"full_name": name, "is_pep": "Yes", "source": "screening"})

    # W2-1: Deduplicate PEPs appearing in both director and UBO roles
    seen_pep_names = set()
    deduped_peps = []
    for p in all_peps:
        pname = (p.get("full_name") or "").strip().lower()
        if pname and pname not in seen_pep_names:
            seen_pep_names.add(pname)
            deduped_peps.append(p)
    all_peps = deduped_peps

    # ── Priority A: derive truthful screening completion signals ──
    # The memo must NOT claim "clean sanctions screening" or "screening
    # completed" when the provider-backed result is non-terminal,
    # not_configured, or unavailable. Compute three booleans that gate
    # the screening narrative below.
    try:
        from screening_state import (
            derive_screening_state as _derive_state,
            COMPLETED_CLEAR as _S_CLEAR,
            COMPLETED_MATCH as _S_MATCH,
            NOT_CONFIGURED as _S_NCFG,
            FAILED as _S_FAILED,
            TERMINAL_STATES as _S_TERMINAL,
        )
    except ImportError:  # pragma: no cover — defensive: never break memo build
        _derive_state = lambda _x: "not_started"
        _S_CLEAR = "completed_clear"
        _S_MATCH = "completed_match"
        _S_NCFG = "not_configured"
        _S_FAILED = "failed"
        _S_TERMINAL = frozenset({_S_CLEAR, _S_MATCH})

    _person_states = []
    for entry in (screening_report.get("director_screenings", []) +
                  screening_report.get("ubo_screenings", [])):
        _person_states.append(_derive_state((entry or {}).get("screening") or {}))
    _company_state = _derive_state(
        ((screening_report.get("company_screening") or {}).get("sanctions") or {})
    )
    _all_states = _person_states + ([_company_state] if screening_report.get("company_screening") else [])

    # ``screening_terminal`` is True only if every screened subject has a
    # terminal provider answer. Otherwise the memo must qualify its
    # screening claims.
    screening_terminal = bool(_all_states) and all(s in _S_TERMINAL for s in _all_states)
    screening_has_non_terminal = any(s not in _S_TERMINAL for s in _all_states)
    screening_has_failed = any(s == _S_FAILED for s in _all_states)
    screening_has_not_configured = any(s == _S_NCFG for s in _all_states)
    # Coverage of self-declared PEP exposure is independent of provider
    # state and remains a first-class signal.
    has_declared_pep = bool(pep_directors or pep_ubos)
    # Phrasing helpers used throughout the memo body to avoid asserting
    # "clean screening" when the underlying state does not support it.
    if screening_terminal:
        _screening_qualifier = ""  # safe to make assertive claims
        _screening_completion_phrase = "Sanctions screening completed across all major consolidated lists (UN, EU, OFAC, HMT)"
    else:
        _bits = []
        if screening_has_not_configured:
            _bits.append("provider not configured for at least one subject")
        if screening_has_failed:
            _bits.append("provider unavailable for at least one subject")
        if not _bits:
            _bits.append("provider has not yet returned a terminal result for at least one subject")
        _qual = "; ".join(_bits)
        _screening_qualifier = (
            " Screening is NOT complete: " + _qual + ". "
            "No reliance can be placed on the absence of matches at this time."
        )
        _screening_completion_phrase = (
            "Sanctions screening status: NOT COMPLETE — " + _qual
        )
    adverse_media_context = _screening_adverse_media_context(screening_report, prescreening_data)
    has_documents = len(documents) > 0
    verified_docs = [d for d in documents if d.get("verification_status") == "verified"]
    pending_docs = [d for d in documents if d.get("verification_status") != "verified"]
    documentation_complete = has_documents and not pending_docs
    now_ts = datetime.now().isoformat()
    country = app["country"] or "Information not provided"
    sector = app["sector"] or "Information not provided"
    entity_type = app["entity_type"] or "Information not provided"
    sof = app.get("source_of_funds") or "Information not provided"
    exp_vol = app.get("expected_volume") or "Information not provided"
    own_struct = app.get("ownership_structure") or "Information not provided"
    operating_countries = app.get("operating_countries") or "Information not provided"
    incorporation_date = app.get("incorporation_date") or "Information not provided"
    business_activity = app.get("business_activity") or "Information not provided"

    # Build risk sub-section ratings from canonical application risk fields.
    # Missing risk must not be silently rendered as MEDIUM/50 in a regulated
    # memo. We retain conservative internal defaults for routing math, but
    # every officer-visible risk line uses risk_display below.
    risk_display = _risk_display_context(app)
    risk_level = risk_display["level"] or "MEDIUM"
    risk_score = risk_display["score"] if risk_display["score"] is not None else 50

    # ── Fix Option C: Derive pre-elevation/original risk level from risk_escalations ──
    # When floor rules or escalation rules elevated the risk level beyond what the
    # composite score alone would produce, the stored risk_level is already elevated.
    # We must pass the true pre-elevation level to validation so it can distinguish
    # legitimate elevation from genuine memo inconsistency.
    pre_elevation_risk_level = risk_level  # default: assume no elevation
    try:
        raw_escalations = app.get("risk_escalations") or "[]"
        escalations_list = json.loads(raw_escalations) if isinstance(raw_escalations, str) else (raw_escalations or [])
        if escalations_list:
            # Escalations present — derive the score-based level (before floor rules)
            score_based_level = classify_risk_level(risk_score)
            if RISK_RANK.get(score_based_level, 2) < RISK_RANK.get(risk_level, 2):
                pre_elevation_risk_level = score_based_level
                logger.info(
                    "Memo metadata: risk elevated from %s (score-based) to %s (stored) — "
                    "escalations: %s. Using %s as original_risk_level.",
                    score_based_level, risk_level, escalations_list, score_based_level)
    except (json.JSONDecodeError, TypeError, ValueError):
        # Safe fallback: if escalations data is corrupt, use stored risk_level
        logger.warning("Could not parse risk_escalations for app %s — using stored risk_level as original",
                        app.get("id", "unknown"))

    doc_confidence = round(len(verified_docs) / max(len(documents), 1) * 100) if has_documents else 0

    # Jurisdiction risk classification with reasoning
    # Constants imported from rule_engine.py (single source of truth)

    is_high_risk_country = country in HIGH_RISK_COUNTRIES
    is_offshore = country in OFFSHORE_COUNTRIES
    is_sanctioned_country = country.lower().strip() in SANCTIONED or country.lower().strip() in FATF_BLACK
    # Priority B.2: Sector classification must use both the canonical
    # tuple AND the keyword set so non-canonical labels like
    # "Crypto Exchange", "Digital Assets Exchange", "Virtual Asset
    # Service Provider", "Online Gambling Operator" are not silently
    # flattened to LOW. The keyword check is case-insensitive and
    # substring-based; matching keywords are recorded for audit.
    _sector_lc = (sector or "").lower()
    _matched_high_sector_keywords = sorted({kw for kw in HIGH_RISK_SECTOR_KEYWORDS if kw in _sector_lc})
    is_high_risk_sector = (sector in HIGH_RISK_SECTORS) or bool(_matched_high_sector_keywords)
    is_medium_risk_sector = sector in MEDIUM_RISK_SECTORS
    is_minimum_medium_sector = sector in MINIMUM_MEDIUM_SECTORS

    jur_rating = "VERY_HIGH" if is_sanctioned_country else "HIGH" if is_high_risk_country else "MEDIUM" if is_offshore else "LOW"
    # Rule 4C: Sectors with inherent minimum MEDIUM risk floor
    biz_rating = "HIGH" if is_high_risk_sector else "MEDIUM" if (is_medium_risk_sector or is_minimum_medium_sector) else "LOW"
    fc_rating = "HIGH" if adverse_media_context["has_hit"] else "MEDIUM" if len(all_peps) > 0 else "LOW"
    tx_rating = risk_level  # Transaction risk mirrors overall application risk level
    doc_rating = "HIGH" if not has_documents else "LOW" if doc_confidence >= 80 else "MEDIUM" if doc_confidence >= 50 else "HIGH"
    dq_rating = "LOW" if (sof != "Information not provided" and exp_vol != "Information not provided") else "MEDIUM"

    jurisdiction_triggers = []
    if is_sanctioned_country:
        jurisdiction_triggers.append("sanctions_or_fatf_blacklist")
    if is_high_risk_country:
        jurisdiction_triggers.append("internal_high_risk_jurisdiction")
    if is_offshore:
        jurisdiction_triggers.append("offshore_financial_centre")
    if not jurisdiction_triggers:
        jurisdiction_triggers.append("not_on_fatf_or_internal_high_risk_tables")

    business_triggers = []
    if sector in HIGH_RISK_SECTORS:
        business_triggers.append("configured_high_risk_sector")
    if _matched_high_sector_keywords:
        business_triggers.append("high_risk_keyword:" + ",".join(_matched_high_sector_keywords))
    if sector in MEDIUM_RISK_SECTORS:
        business_triggers.append("configured_medium_risk_sector")
    if sector in MINIMUM_MEDIUM_SECTORS:
        business_triggers.append("minimum_medium_sector_floor")
    if not business_triggers:
        business_triggers.append("no_high_or_medium_sector_trigger")

    financial_crime_triggers = []
    if all_peps:
        financial_crime_triggers.append("declared_or_provider_pep")
    if not screening_terminal:
        financial_crime_triggers.append("screening_not_terminal")
    if adverse_media_context["has_hit"]:
        financial_crime_triggers.append("adverse_media_hit")
    if not adverse_media_context["terminal"]:
        financial_crime_triggers.append("adverse_media_not_terminal")
    if not financial_crime_triggers:
        financial_crime_triggers.append("terminal_clear_screening_no_pep")

    jurisdiction_evidence = {
        "rating": jur_rating,
        "source": "FATF/internal jurisdiction tables",
        "triggers": jurisdiction_triggers,
        "prose": (
            "Deterministic jurisdiction rating: "
            + jur_rating
            + " for "
            + str(country)
            + " based on "
            + ", ".join(jurisdiction_triggers)
            + "."
        ),
    }
    business_evidence = {
        "rating": biz_rating,
        "source": "configured sector risk tables and keyword floors",
        "triggers": business_triggers,
        "matched_keywords": _matched_high_sector_keywords,
        "prose": (
            "Deterministic sector rating: "
            + biz_rating
            + " for "
            + str(sector)
            + " based on "
            + ", ".join(business_triggers)
            + "."
        ),
    }
    financial_crime_evidence = {
        "rating": fc_rating,
        "source": "screening terminality, PEP declarations, adverse-media coverage",
        "triggers": financial_crime_triggers,
        "screening_terminal": screening_terminal,
        "adverse_media_terminal": adverse_media_context["terminal"],
        "adverse_media_coverage": adverse_media_context["coverage"],
        "prose": (
            "Deterministic financial-crime rating: "
            + fc_rating
            + " based on "
            + ", ".join(financial_crime_triggers)
            + "."
        ),
    }

    # ══════════════════════════════════════════════════════════
    # LAYER 1 — PRE-GENERATION RULE ENGINE (Hard Constraints)
    # Deterministic rules that enforce correctness BEFORE memo is built.
    # Every rule violation is logged. Rules CANNOT be overridden by AI.
    # ══════════════════════════════════════════════════════════
    rule_violations = []
    rule_enforcements = []

    # ── SANCTIONED COUNTRY FLOOR: Force VERY_HIGH jurisdiction risk ──
    if is_sanctioned_country:
        rule_enforcements.append({
            "rule": "SANCTIONED_COUNTRY_FLOOR",
            "original": jur_rating if jur_rating != "VERY_HIGH" else "VERY_HIGH",
            "enforced": "VERY_HIGH",
            "reason": country + " is a sanctioned or FATF blacklisted jurisdiction — jurisdiction risk floor is VERY_HIGH"
        })

    # ── RULE 4C: Business risk floor enforcement ──
    if is_minimum_medium_sector and biz_rating == "LOW":
        rule_enforcements.append({
            "rule": "BIZ_RISK_FLOOR",
            "original": "LOW",
            "enforced": "MEDIUM",
            "reason": sector + " sector carries minimum MEDIUM inherent risk per FATF risk-based approach guidance"
        })
        biz_rating = "MEDIUM"

    # ── Priority B.2: Sector keyword floor (non-canonical labels) ─────
    # When the literal sector string is not in HIGH_RISK_SECTORS but
    # contains a high-risk sector keyword (e.g. "Crypto Exchange",
    # "Digital Assets Exchange", "Virtual Asset Service Provider"),
    # business risk MUST be HIGH. This closes the normalization gap
    # where Agent 5 was receiving sector_risk_tier="low" for crypto
    # / virtual-asset cases solely because the label did not match a
    # canonical tuple member. We always record an audit-grade
    # enforcement row when the keyword path was responsible for HIGH.
    if _matched_high_sector_keywords and (sector not in HIGH_RISK_SECTORS):
        rule_enforcements.append({
            "rule": "BIZ_RISK_KEYWORD_FLOOR",
            "original": "LOW" if not is_minimum_medium_sector and not is_medium_risk_sector else "MEDIUM",
            "enforced": "HIGH",
            "reason": (
                "Sector label '" + str(sector) + "' contains high-risk keyword(s): "
                + ", ".join(_matched_high_sector_keywords)
                + " — business risk floor is HIGH"
            ),
        })
        biz_rating = "HIGH"

    # ── RULE 4A: Factor classification hard constraints ──
    # ALWAYS_RISK_DECREASING / ALWAYS_RISK_INCREASING imported from rule_engine.py

    # Structure complexity
    struct_complexity = "Complex" if len(ubos) > 3 or own_struct.lower().find("nominee") >= 0 else "Layered" if len(ubos) > 1 else "Simple"

    # Determine primary UBO for control statement
    primary_ubo = max(ubos, key=lambda u: float(u.get("ownership_pct", 0) or 0)) if ubos else None
    control_name = primary_ubo["full_name"] if primary_ubo else "Information not provided"
    control_pct = primary_ubo.get("ownership_pct", "N/A") if primary_ubo else "N/A"

    # Ownership risk: multi-factor assessment (Issue B fix)
    # Consider PEP count, missing ownership %, unclear control, UBO transparency
    own_risk_factors = 0
    own_risk_reasons = []
    if len(all_peps) > 1:
        own_risk_factors += 2
        own_risk_reasons.append("multiple PEPs in ownership/governance structure")
    elif len(all_peps) == 1:
        own_risk_factors += 1
        own_risk_reasons.append("PEP identified in ownership/governance structure")
    if not ubos:
        own_risk_factors += 2
        own_risk_reasons.append("no UBO information provided — critical data gap")
    else:
        ubos_missing_pct = [u for u in ubos if not u.get("ownership_pct") or str(u.get("ownership_pct", "")).strip() in ("", "0", "N/A")]
        if ubos_missing_pct:
            own_risk_factors += 1
            own_risk_reasons.append("ownership percentage missing for " + str(len(ubos_missing_pct)) + " UBO(s)")
        total_pct = sum(float(u.get("ownership_pct", 0) or 0) for u in ubos)
        if total_pct < 75 and ubos:
            own_risk_factors += 1
            own_risk_reasons.append("total disclosed ownership is " + str(round(total_pct)) + "% — incomplete transparency")
    if not primary_ubo or control_pct in ("N/A", None, "", "0"):
        own_risk_factors += 1
        own_risk_reasons.append("effective control cannot be clearly determined")
    if struct_complexity == "Complex":
        own_risk_factors += 1
        own_risk_reasons.append("complex corporate structure with potential layering")
    own_rating = "HIGH" if own_risk_factors >= 3 else "MEDIUM" if own_risk_factors >= 1 else "LOW"
    own_rating_justification = "; ".join(own_risk_reasons) if own_risk_reasons else "clean ownership structure with full transparency"

    # ── RULE 4B: Ownership risk floor enforcement ──
    # IF ownership % missing OR control undetermined OR no UBOs → ownership risk CANNOT be LOW
    ownership_has_gaps = (
        not ubos
        or any(not u.get("ownership_pct") or str(u.get("ownership_pct", "")).strip() in ("", "0", "N/A") for u in ubos)
        or not primary_ubo
        or control_pct in ("N/A", None, "", "0")
    )
    if ownership_has_gaps and own_rating == "LOW":
        rule_enforcements.append({
            "rule": "OWN_RISK_FLOOR",
            "original": "LOW",
            "enforced": "MEDIUM",
            "reason": "Ownership risk cannot be LOW when ownership percentages are missing, control is undetermined, or UBO data is absent"
        })
        own_rating = "MEDIUM"
        if not own_risk_reasons:
            own_risk_reasons.append("ownership data gaps prevent LOW classification")
        own_rating_justification = "; ".join(own_risk_reasons)

    # Risk aggregation consistency check (Issue C fix)
    # RISK_WEIGHTS and RISK_RANK imported from rule_engine.py
    sub_risk_vals = {
        "jurisdiction": RISK_RANK.get(jur_rating, 2),
        "business": RISK_RANK.get(biz_rating, 2),
        "transaction": RISK_RANK.get(tx_rating, 2),
        "ownership": RISK_RANK.get(own_rating, 2),
        "fincrime": RISK_RANK.get(fc_rating, 2),
        "documentation": RISK_RANK.get(doc_rating, 2),
        "data_quality": RISK_RANK.get(dq_rating, 2),
    }
    weighted_risk = sum(sub_risk_vals[k] * RISK_WEIGHTS[k] for k in RISK_WEIGHTS)
    # Derive effective risk level from weighted average
    effective_risk = "LOW" if weighted_risk < 1.5 else "MEDIUM" if weighted_risk < 2.5 else "HIGH" if weighted_risk < 3.5 else "VERY_HIGH"
    # Use the higher of app risk_level or computed effective_risk to be conservative
    final_risk = max(RISK_RANK.get(risk_level, 2), RISK_RANK.get(effective_risk, 2))
    RANK_RISK = {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "VERY_HIGH"}
    aggregated_risk = RANK_RISK.get(final_risk, risk_level)

    # ── RULE 4D: Multi-gap escalation enforcement ──
    # IF multiple critical gaps exist (documents, ownership, SoF) → must escalate
    critical_gaps = []
    if not ubos:
        critical_gaps.append("no_ubo_data")
    if not has_documents:
        critical_gaps.append("no_documents_uploaded")
    elif pending_docs and len(pending_docs) >= 2:
        critical_gaps.append("multiple_docs_outstanding")
    if sof == "Information not provided":
        critical_gaps.append("source_of_funds_missing")
    if exp_vol == "Information not provided":
        critical_gaps.append("expected_volume_missing")
    if ownership_has_gaps:
        critical_gaps.append("ownership_gaps")

    if len(critical_gaps) >= 3 and RISK_RANK.get(aggregated_risk, 2) < 2:
        rule_enforcements.append({
            "rule": "MULTI_GAP_ESCALATION",
            "original": aggregated_risk,
            "enforced": "MEDIUM",
            "reason": str(len(critical_gaps)) + " critical data gaps (" + ", ".join(critical_gaps) + ") prevent LOW overall risk classification"
        })
        aggregated_risk = "MEDIUM"
    elif len(critical_gaps) >= 4 and RISK_RANK.get(aggregated_risk, 2) < 3:
        rule_enforcements.append({
            "rule": "MULTI_GAP_ESCALATION",
            "original": aggregated_risk,
            "enforced": "HIGH",
            "reason": str(len(critical_gaps)) + " critical data gaps require escalation to HIGH risk"
        })
        aggregated_risk = "HIGH"

    # Confidence-to-decision linkage (Issue D fix)
    model_confidence = max(60, doc_confidence - 5)
    low_confidence = model_confidence < 70

    # Decision — uses aggregated risk and confidence linkage
    if low_confidence and aggregated_risk == "LOW":
        # Low confidence should not allow clean approval
        decision = "APPROVE_WITH_CONDITIONS"
        decision_label = "APPROVAL WITH CONDITIONS"
    elif aggregated_risk == "LOW":
        decision = "APPROVE"
        decision_label = "APPROVAL"
    elif aggregated_risk == "MEDIUM":
        decision = "APPROVE_WITH_CONDITIONS"
        decision_label = "APPROVAL WITH CONDITIONS"
    elif aggregated_risk == "HIGH":
        decision = "REVIEW"
        decision_label = "SENIOR COMPLIANCE OFFICER REVIEW"
    else:
        decision = "REJECT"
        decision_label = "REJECTION"

    # ── RULE 4E: Confidence enforcement ──────────────────────────────
    # If model confidence < 70%, decision MUST be at minimum APPROVE_WITH_CONDITIONS
    # with explicit escalation/review language. Clean APPROVE is forbidden.
    if model_confidence < 70 and decision == "APPROVE":
        rule_enforcements.append({
            "rule": "CONFIDENCE_FLOOR",
            "original_decision": decision,
            "enforced_decision": "APPROVE_WITH_CONDITIONS",
            "confidence": model_confidence,
            "reason": (
                "Model confidence of " + str(model_confidence) + "% is below the 70% threshold. "
                "Clean approval is not permitted — decision escalated to APPROVE_WITH_CONDITIONS "
                "with mandatory enhanced monitoring and 90-day review."
            )
        })
        decision = "APPROVE_WITH_CONDITIONS"
        decision_label = "APPROVAL WITH CONDITIONS"
    # If confidence < 60%, escalate further to REVIEW regardless of risk
    if model_confidence < 60 and decision not in ("REVIEW", "REJECT"):
        rule_enforcements.append({
            "rule": "CONFIDENCE_CRITICAL_FLOOR",
            "original_decision": decision,
            "enforced_decision": "REVIEW",
            "confidence": model_confidence,
            "reason": (
                "Model confidence of " + str(model_confidence) + "% is critically low (below 60%). "
                "Decision escalated to SENIOR COMPLIANCE OFFICER REVIEW."
            )
        })
        decision = "REVIEW"
        decision_label = "SENIOR COMPLIANCE OFFICER REVIEW"

    mon_tier = "Enhanced" if aggregated_risk in ("HIGH", "VERY_HIGH") or all_peps else "Standard"

    # ── Compile Rule Engine summary ───────────────────────────────────
    rule_engine_result = {
        "rules_checked": ["FACTOR_CLASSIFICATION", "OWNERSHIP_FLOOR", "BUSINESS_RISK_FLOOR", "MULTI_GAP_ESCALATION", "CONFIDENCE_FLOOR"],
        "violations": rule_violations,
        "enforcements": rule_enforcements,
        "total_enforcements": len(rule_enforcements),
        "total_violations": len(rule_violations),
        "engine_status": "ENFORCED" if rule_enforcements else "CLEAN",
        "timestamp": now_ts
    }

    screening_review_lines = []
    for review in app.get("screening_reviews") or []:
        if not isinstance(review, dict) or not review.get("disposition"):
            continue
        subject = f"{review.get('subject_type', 'subject')}:{review.get('subject_name', 'Unknown')}"
        disposition_text = str(review.get("disposition") or "").replace("_", " ")
        code = review.get("disposition_code") or "no code recorded"
        rationale = review.get("rationale") or review.get("notes") or "no rationale recorded"
        reviewer = review.get("reviewer_name") or review.get("reviewer_id") or "unknown reviewer"
        line = f"{subject} — {disposition_text} ({code}) by {reviewer}: {rationale}"
        if review.get("requires_four_eyes"):
            second_reviewer = review.get("second_reviewer_name") or review.get("second_reviewer_id")
            if second_reviewer:
                line += f" Second review completed by {second_reviewer}"
                if review.get("second_rationale"):
                    line += f": {review.get('second_rationale')}"
            else:
                line += " Second review required but not yet completed"
        screening_review_lines.append(line[:1000])
    screening_review_evidence = (
        " Officer disposition evidence: " + " ".join(screening_review_lines)
        if screening_review_lines else ""
    )

    # Generate Big 4-grade 11-section memo structure
    memo = {
        "application_ref": app["ref"],
        "company_name": app["company_name"],
        "memo_generated": now_ts,
        "sections": {
            "executive_summary": {
                "title": "Executive Summary",
                "content": (
                    f"This memo presents the compliance assessment of {app['company_name']} (BRN: {app['brn']}), "
                    f"a {entity_type} incorporated in {country}, operating in the {sector} sector. "
                    + (
                        f"The composite risk score of {risk_score}/100 (aggregated: {aggregated_risk}) reflects "
                        f"{'a balanced risk profile' if aggregated_risk == 'MEDIUM' else 'a low-risk profile with no material concerns' if aggregated_risk == 'LOW' else 'an elevated risk profile requiring enhanced scrutiny'}. "
                        if risk_display["available"] else
                        risk_display["summary"] + " Internal factor analysis is used only to route unresolved gaps and is not presented as a final risk rating. "
                    )
                    + f"Model confidence: {model_confidence}%"
                    + (f" — reduced due to {'no uploaded documentation and ' if not has_documents else 'outstanding documentation and ' if pending_docs else ''}{'limited historical transaction data' if True else ''}. " if model_confidence < 80 else ". ")
                    + f"The principal risk drivers are "
                    + (f"the presence of {len(all_peps)} Politically Exposed Person(s) ({', '.join([p['full_name'] for p in all_peps])})" if all_peps else "")
                    + (f"{',' if all_peps else ''} ownership risk rated {own_rating} ({own_rating_justification})" if own_rating in ("HIGH", "MEDIUM") and own_risk_reasons else "")
                    + (f"{',' if all_peps or own_rating in ('HIGH', 'MEDIUM') else ''} the {'high-risk' if is_high_risk_country else 'offshore'} jurisdictional classification of {country}" if is_high_risk_country or is_offshore else "")
                    + (f"no PEP exposure and a clean ownership structure" if not all_peps and own_rating == "LOW" and not is_high_risk_country and not is_offshore else "")
                    + ". "
                    + ("These risk factors are " if (all_peps or own_rating in ("HIGH", "MEDIUM") or is_high_risk_country or is_offshore) else "The low-risk profile is supported by ")
                    + ("materially offset by " if aggregated_risk in ("LOW", "MEDIUM") else "insufficiently offset by ")
                    + (f"clean sanctions screening across all major consolidated lists, " if (not all_peps and screening_terminal) else "")
                    + (f"a fully traceable beneficial ownership chain ({control_name} at {control_pct}%)" if primary_ubo and control_pct not in ("N/A", None, "", "0") else "beneficial ownership assessment")
                    + (f", and {len(verified_docs)} of {len(documents)} documents verified at {doc_confidence}% confidence. " if has_documents else ", and no uploaded documents are currently available to substantiate entity verification. ")
                    + ("No documents have been uploaded, so entity verification remains incomplete and cannot be treated as a mitigant. " if not has_documents else f"{len(pending_docs)} document(s) remain outstanding, representing a documentation gap that must be remedied within 14 business days. " if pending_docs else "")
                    + (f"Note: model confidence of {model_confidence}% is below threshold — this is reflected in the conditional nature of the recommendation. " if low_confidence else "")
                    + f"Recommendation: {decision_label}"
                    + (f" — subject to {'PEP declaration and enhanced monitoring' if all_peps else 'standard conditions including enhanced monitoring due to reduced confidence' if low_confidence else 'standard conditions'}." if aggregated_risk in ("MEDIUM", "HIGH") else ".")
                )
            },
            "client_overview": {
                "title": "Client Overview",
                "content": (
                    f"Entity Name: {app['company_name']}. Business Registration Number: {app['brn']}. "
                    f"Jurisdiction of Incorporation: {country}. "
                    f"Date of Incorporation: {incorporation_date}. "
                    f"Entity Type: {entity_type}. "
                    f"Sector: {sector}. Application Reference: {app['ref']}. "
                    f"Operating Countries: {operating_countries}. "
                    f"Business Activity: {business_activity}. "
                    f"Ownership Structure: {own_struct}. "
                    f"Source of Funds: {sof}"
                    + (". The stated source of funds " + ("appears consistent with the entity's business profile" if sof != "Information not provided" else "— this data gap prevents assessment of fund origin legitimacy and elevates residual risk") + ". ")
                    + f"Expected Transaction Volume: {exp_vol}"
                    + (f". {'Transaction volumes fall within expected parameters for the stated business activity and jurisdiction.' if exp_vol != 'Information not provided' else ' This data gap reduces the ability to benchmark transaction patterns and establishes a monitoring dependency.'}" if True else "")
                )
            },
            "ownership_and_control": {
                "title": "Ownership & Control",
                "structure_complexity": struct_complexity,
                "control_statement": f"{control_name} exercises effective control as {'majority' if float(control_pct or 0) > 50 else 'significant'} shareholder ({control_pct}%)." if primary_ubo else "Effective control cannot be determined — this represents a material data gap requiring resolution.",
                "content": (
                    f"The entity operates a {struct_complexity.lower()} corporate structure with {len(directors)} director(s) and {len(ubos)} UBO(s). "
                    + " ".join([
                        f"Director: {d['full_name']} ({d.get('nationality', 'nationality not provided')} national, DOB: {d.get('date_of_birth') or 'not provided'})"
                        + (f" — identified as PEP. Enhanced due diligence required under FATF Recommendation 12." if d.get("is_pep") == "Yes" else " — not a PEP.")
                        for d in directors
                    ]) + " "
                    + " ".join([
                        f"UBO: {u['full_name']} — {u.get('ownership_pct', 'Information not provided')}% direct ownership"
                        + f" ({u.get('nationality', 'nationality not provided')} national, DOB: {u.get('date_of_birth') or 'not provided'})"
                        + (f". Identified as PEP — this UBO exercises both ownership and potential political influence, significantly elevating risk." if u.get("is_pep") == "Yes" else ".")
                        + (f" Ownership percentage information not provided — this represents a data gap that prevents full UBO analysis." if not u.get("ownership_pct") else "")
                        for u in ubos
                    ])
                    + (f" No UBO information provided — this is a critical data gap that prevents beneficial ownership verification and materially elevates risk." if not ubos else "")
                    + f" No nominee arrangements or bearer shares were {'disclosed' if ubos else 'identifiable from available data'}."
                )
            },
            "risk_assessment": {
                "title": "Risk Assessment",
                "sub_sections": {
                    "jurisdiction_risk": {
                        "title": "Jurisdiction Risk",
                        "rating": jur_rating,
                        "content": (
                            f"Jurisdiction of incorporation: {country}. "
                            + jurisdiction_evidence["prose"] + " "
                            + (f"{country} is designated as a high-risk jurisdiction due to comprehensive international sanctions, FATF blacklisting, or active conflict zone status. This presents severe jurisdictional risk that materially affects the overall risk assessment." if is_high_risk_country
                               else f"{country} presents moderate jurisdictional risk. While {'currently compliant with FATF standards' if is_offshore else 'not on current FATF lists'}, the jurisdiction retains characteristics of an offshore financial centre — including cross-border capital flow facilitation and international business licence regimes — that elevate baseline risk for ML/TF purposes." if is_offshore
                               else f"{country} presents low jurisdictional risk. The jurisdiction maintains adequate AML/CFT frameworks, is not on FATF grey or black lists, and does not exhibit characteristics associated with elevated ML/TF risk.")
                            + f" Risk weighting factor: 0.20."
                        )
                    },
                    "business_risk": {
                        "title": "Business Risk",
                        "rating": biz_rating,
                        "content": (
                            f"Sector: {sector}. "
                            + business_evidence["prose"] + " "
                            + (f"The {sector} sector carries elevated inherent ML/TF risk due to the potential for anonymous transactions, rapid value transfer, or regulatory arbitrage. This significantly elevates the entity's business risk profile. FATF Guidance on a Risk-Based Approach identifies this sector as requiring enhanced scrutiny." if is_high_risk_sector
                               else f"The {sector} sector carries moderate inherent risk due to the intermediary nature of the business and exposure to third-party funds. However, the stated business model appears plausible and consistent with the entity's incorporation documents and jurisdictional profile, partially mitigating this concern." if is_medium_risk_sector
                               else f"The {sector} sector presents low inherent business risk. The stated business activity does not exhibit typology indicators associated with elevated ML/TF risk.")
                            + f" Source of funds: {sof}. "
                            + (f"The stated source of funds is consistent with the entity's business model and sector profile. " if sof != "Information not provided"
                               else "Source of funds was not provided — this data gap prevents assessment of fund origin legitimacy and modestly elevates residual business risk. ")
                            + f"Expected volume: {exp_vol}. "
                            + (f"Stated volumes are within expected parameters for a {entity_type} in the {sector} sector within {country}. " if exp_vol != "Information not provided"
                               else "Expected transaction volumes not provided — this limits the ability to assess proportionality and creates a monitoring dependency. ")
                            + f"Business model plausibility was assessed within Agent 5 (Compliance Memo & Risk Recommendation) using sector, source-of-funds, and business-description inputs. Risk weighting factor: 0.15."
                        )
                    },
                    "transaction_risk": {
                        "title": "Transaction Risk",
                        "rating": risk_level,
                        "content": (
                            f"Expected transaction volume: {exp_vol}. Source of funds: {sof}. "
                            + (f"{'Transaction volumes and source of funds are within expected parameters for the stated business activity.' if exp_vol != 'Information not provided' and sof != 'Information not provided' else 'Insufficient transaction data provided to conduct meaningful benchmarking — this data gap creates a monitoring dependency and modestly reduces confidence in the forward-looking risk assessment.'} "
                               f"As a {'newly onboarded' if True else 'existing'} entity, limited historical transaction data is available, which constrains the ability to identify anomalous patterns at this stage.")
                            + f" Risk weighting factor: 0.10."
                        )
                    },
                    "ownership_risk": {
                        "title": "Ownership Risk",
                        "rating": own_rating,
                        "content": (
                            f"The entity has {len(directors)} director(s) and {len(ubos)} UBO(s). "
                            f"Structure complexity: {struct_complexity}. "
                            f"Ownership risk rating: {own_rating} — based on: {own_rating_justification}. "
                            + (f"{len(all_peps)} PEP(s) identified among the ownership and governance structure. "
                               f"PEP exposure introduces corruption and undue influence risk per FATF Recommendation 12 requirements for enhanced scrutiny. "
                               + ("The PEP(s) hold direct ownership, elevating control risk. " if pep_ubos else "The PEP(s) hold board positions but no direct ownership stake, which partially mitigates control risk. ")
                               if all_peps else "No PEP exposure identified in the ownership or governance structure, which is a positive risk indicator. ")
                            + (f"Beneficial ownership has been identified to natural person level — {control_name} ({control_pct}%) exercises effective control. " if primary_ubo and control_pct not in ("N/A", None, "", "0")
                               else f"Beneficial ownership partially identified but {'ownership percentages are incomplete for some UBOs' if ubos else 'could not be verified — this is a critical gap'}. " if ubos
                               else "No UBO information provided — beneficial ownership verification is not possible. This is a critical deficiency. ")
                            + f"Risk weighting factor: 0.25."
                        )
                    },
                    "financial_crime_risk": {
                        "title": "Financial Crime Risk",
                        "rating": fc_rating,
                        "content": (
                            financial_crime_evidence["prose"] + " "
                            + (
                                f"Sanctions screening was conducted across UN Security Council, EU, OFAC SDN, and HMT consolidated lists. "
                                + ("No matches were returned for any director, UBO, or the entity itself. " if not all_peps else f"{len(all_peps)} PEP match(es) identified requiring enhanced assessment. ")
                                if screening_terminal else
                                _screening_completion_phrase + ". "
                                + ("Self-declared PEP exposure remains: " + ", ".join([p["full_name"] for p in all_peps]) + ". " if all_peps else "")
                                + "No reliance can be placed on the absence of provider matches at this time. "
                            )
                            + adverse_media_context["phrase"] + " "
                            + f"The entity's business model {'does not exhibit' if fc_rating in ('LOW', 'MEDIUM') else 'may exhibit'} typology indicators associated with money laundering, terrorist financing, or proliferation financing. "
                            + f"Risk weighting factor: 0.10."
                        )
                    }
                }
            },
            "screening_results": {
                "title": "Screening Results",
                "content": (
                    (
                        f"Sanctions Screening: Conducted against UN Security Council Consolidated List, EU Consolidated Financial Sanctions List, OFAC SDN List, and HMT Consolidated List. "
                        + ("No matches returned for any associated individual or the entity itself. " if not all_peps
                           else f"PEP matches identified — assessed as confirmed true positives based on verified identity data. ")
                        if screening_terminal else
                        _screening_completion_phrase + ". "
                        + "Provider-backed sanctions / PEP / watchlist screening is NOT yet complete for at least one subject; "
                        + "no reliance may be placed on the absence of matches at this time. "
                    )
                    + f"PEP Screening: {len(all_peps)} self-declared / detected match(es)"
                    + (" — " + ". ".join([p["full_name"] + " identified as PEP. PEP declaration form and enhanced due diligence documentation requested." for p in all_peps]) if all_peps else " — no declared or detected matches.")
                    + " " + adverse_media_context["phrase"] + " "
                    + screening_review_evidence
                    + f"Company Registry Verification: {app['company_name']} verified against registry records. "
                    + f"Registration details are {'consistent' if verified_docs else 'pending verification against'} application data."
                )
            },
            "document_verification": {
                "title": "Document Verification",
                "content": (
                    f"{len(documents)} document(s) submitted, {len(verified_docs)} verified. "
                    + " ".join([
                        f"{d.get('doc_type', 'Document')}: "
                        + (f"Verified — document is authentic and internally consistent with application data. Cross-referenced against entity name, registration number, and jurisdiction details. No discrepancies, alterations, or anomalies identified." if d.get("verification_status") == "verified"
                           else f"Pending verification — document has been received but automated and manual verification is in progress. Until verified, this document cannot be relied upon for compliance determination." if d.get("verification_status") == "pending"
                           else f"Not verified — {'document has been formally requested and must be received within 14 business days. This gap prevents full entity verification and is a material deficiency that elevates residual risk.' if d.get('verification_status') == 'missing' else 'verification status requires manual review by compliance officer.'}")
                        for d in documents
                    ])
                    + (" No documents have been uploaded. Entity verification cannot be completed until the required corporate and identity documents are received and reviewed." if not has_documents else "")
                    + (f" Professional judgement: {len(pending_docs)} document(s) remain outstanding. "
                       + f"The absence of {'these critical documents' if len(pending_docs) > 1 else 'this document'} prevents complete entity verification "
                       + f"and {'materially weakens' if len(pending_docs) >= 2 else 'partially reduces'} the overall assurance level. "
                       + f"This deficiency is reflected as a condition of approval with a 14-business-day remediation deadline." if pending_docs
                       else " All required documents have been received and verified. Document set is complete and internally consistent — high assurance in entity verification." if documentation_complete else "")
                    + f" Overall documentation adequacy: {doc_confidence}%. "
                    + ("Documentation confidence is 0% because no supporting documents have been uploaded yet. This is a material deficiency that prevents a fully supported onboarding recommendation."
                       if not has_documents else
                       f"Documentation confidence is reduced due to {len(pending_docs)} outstanding item(s). "
                       + f"Impact assessment: {'the documentation gap is material and would weaken regulatory defensibility of the compliance decision' if doc_confidence < 60 else 'documentation is partially complete but the gap must be remedied to achieve full compliance assurance'}."
                       if pending_docs else "Full documentation received — high confidence in entity verification. Documentation set meets regulatory expectations.")
                )
            },
            "ai_explainability": {
                "title": "AI Explainability Layer",
                "risk_increasing_factors": [f for f in [
                    (f"PEP presence ({len(all_peps)} identified) — weight: 0.25, elevating ownership risk due to FATF Recommendation 12 requirements" if all_peps else None),
                    (f"{'High-risk' if is_high_risk_country else 'Offshore'} jurisdiction ({country}) — weight: 0.20, elevating baseline cross-border risk" if is_high_risk_country or is_offshore else None),
                    (f"{sector} sector — weight: 0.15, elevated inherent sector risk" if is_high_risk_sector or is_medium_risk_sector else None),
                    (f"Ownership risk: {own_rating} — {own_rating_justification}" if own_rating in ("HIGH", "MEDIUM") else None),
                    ("No supporting documents uploaded — entity verification remains incomplete and documentation risk is elevated" if not has_documents else None),
                    (f"Documentation gap: {len(pending_docs)} document(s) outstanding, reducing verification confidence" if pending_docs else None),
                    ("Limited trading history — no historical transaction data for benchmarking" if True else None),
                ] if f is not None],
                "risk_decreasing_factors": [f for f in [
                    ("No PEP exposure among directors or UBOs — no contribution to ownership risk" if not all_peps else None),
                    (f"Low jurisdictional risk — {country} maintains adequate AML/CFT frameworks" if not is_high_risk_country and not is_offshore else None),
                    (f"Low sector risk — {sector} does not exhibit elevated ML/TF typology indicators" if not is_high_risk_sector and not is_medium_risk_sector else None),
                    ("Clean sanctions screening across all major consolidated lists (UN, EU, OFAC, HMT)" if (not all_peps and screening_terminal)
                     else ("Provider sanctions screening NOT complete — no reliance on absence of matches" if not screening_terminal
                           else "Screening completed — PEP(s) identified and flagged for enhanced measures")),
                    (f"Verified beneficial ownership traced to natural person level — {control_name} ({control_pct}%) exercises effective control" if primary_ubo and control_pct not in ("N/A", None, "", "0") else None),
                    (f"Full documentation received and verified at {doc_confidence}% confidence" if documentation_complete and doc_confidence >= 80 else None),
                ] if f is not None],
                "factor_enforcement_applied": True,
                "content": (
                    f"Risk scoring model: Onboarda Composite Risk Engine v2.1. "
                    f"Scoring methodology: Weighted multi-factor analysis across 5 risk dimensions, calibrated against Basel Committee and Wolfsberg Group risk factor guidance. "
                    + (
                        f"Overall risk score: {risk_score}/100 ({risk_level}). "
                        if risk_display["available"] else
                        "Overall risk score: Not yet scored — no canonical application risk score is recorded. "
                    )
                    + f"Model confidence: {max(60, doc_confidence - 5)}% — "
                    + (f"confidence is reduced from baseline due to {'no uploaded documentation and ' if not has_documents else 'outstanding documentation and ' if pending_docs else ''}limited historical transaction data. " if doc_confidence < 100 else "high confidence based on complete documentation. ")
                    + f"Risk-increasing factors: "
                    + (f"(1) PEP presence ({len(all_peps)} identified) — weight: 0.25, elevating ownership risk due to FATF Recommendation 12 requirements. " if all_peps else "")
                    + (f"{'(2) ' if all_peps else '(1) '}{'High-risk' if is_high_risk_country else 'Offshore'} jurisdiction ({country}) — weight: 0.20, elevating baseline cross-border risk. " if is_high_risk_country or is_offshore else "")
                    + (f"{'(3) ' if all_peps and (is_high_risk_country or is_offshore) else '(2) ' if all_peps or (is_high_risk_country or is_offshore) else '(1) '}{sector} sector — weight: 0.15, elevated inherent sector risk. " if is_high_risk_sector or is_medium_risk_sector else "")
                    + (f"Ownership risk ({own_rating}): {own_rating_justification}. " if own_rating in ("HIGH", "MEDIUM") else "")
                    + ("No uploaded documents are available, so documentation risk remains elevated and entity verification cannot be treated as complete. " if not has_documents else f"Documentation gap: {len(pending_docs)} outstanding document(s) reduce verification confidence. " if pending_docs else "")
                    + ("Limited trading history constrains forward-looking risk confidence. " if True else "")
                    + f"Risk-decreasing factors: "
                    + (f"(1) No PEP exposure — no contribution to ownership risk. " if not all_peps else "(1) PEP(s) identified and flagged for enhanced monitoring. ")
                    + (f"(2) Low jurisdictional risk — {country} maintains adequate AML/CFT frameworks. " if not is_high_risk_country and not is_offshore else "")
                    + (f"{'(3) ' if not is_high_risk_country and not is_offshore else '(2) '}{'Clean' if (not all_peps and screening_terminal) else 'Pending' if not screening_terminal else 'Completed'} sanctions screening across all major consolidated lists. " if True else "")
                    + (f"{'(4) ' if not is_high_risk_country and not is_offshore else '(3) '}Verified beneficial ownership to natural person level. " if primary_ubo and control_pct not in ("N/A", None, "", "0") else "")
                    + (f"Full documentation at {doc_confidence}% confidence. " if documentation_complete and doc_confidence >= 80 else "")
                    + f"Decision pathway: Agent 1 (Identity & Document Integrity) -> Agent 2 (External Database Cross-Verification) -> Agent 3 (FinCrime Screening Interpretation) -> Agent 4 (Corporate Structure & UBO Mapping) -> Agent 5 (Compliance Memo & Risk Recommendation). "
                    + "Supervisor module: Contradiction detection and inter-agent consistency check. "
                    + "Monitoring pipeline: Agent 6 (Periodic Review Preparation) -> Agent 7 (Adverse Media & PEP Monitoring) -> Agent 8 (Behaviour & Risk Drift) -> Agent 9 (Regulatory Impact) -> Agent 10 (Ongoing Compliance Review)."
                )
            },
            "red_flags_and_mitigants": {
                "title": "Red Flags & Mitigants",
                "red_flags": (
                    [f"Politically Exposed Person identified: {p['full_name']}. {'Board membership confers governance influence and introduces corruption and undue influence risk per FATF Recommendation 12, requiring enhanced scrutiny.' if p in pep_directors else 'Direct ownership by a PEP significantly elevates control risk and the potential for proceeds of corruption to enter the financial system.'}" for p in all_peps]
                    + (["No uploaded documents are available for corporate or identity verification. This prevents complete entity verification and materially weakens the evidential basis for approval."] if not has_documents else [])
                    + ([f"Documentation gap: {len(pending_docs)} of {len(documents)} required document(s) remain outstanding. This prevents complete entity verification and creates residual risk until remedied."] if pending_docs else [])
                    + ([f"Offshore jurisdiction: {country} retains characteristics of an international financial centre, elevating baseline risk for cross-border fund flows and regulatory arbitrage."] if is_offshore else [])
                    + ([f"High-risk jurisdiction: {country} is subject to comprehensive sanctions or FATF blacklisting, presenting severe jurisdictional risk."] if is_high_risk_country else [])
                    + ([f"High-risk sector: {sector} carries elevated inherent ML/TF risk due to the nature of the business activity."] if is_high_risk_sector else [])
                    + [f"Limited trading history: As a new onboarding, there is no historical transaction data against which to benchmark expected volumes. This reduces forward-looking risk confidence and creates a monitoring dependency."]
                ) or ["No material red flags identified. Standard monitoring applies."],
                "mitigants": (
                    [f"PEP ({p['full_name']}) {'holds no direct ownership stake, reducing control risk. PEP declaration and enhanced monitoring will be applied as conditions of approval.' if p in pep_directors else 'has been identified and PEP declaration, source of wealth verification, and enhanced monitoring are required as conditions.'}" for p in all_peps]
                    + (["Document collection has been initiated, but no uploaded documents are yet available to support entity verification. Approval must remain conditional on document receipt and review."] if not has_documents else [f"Outstanding documents have been formally requested with a 14-business-day deadline. Failure to provide will trigger automatic escalation to Senior Compliance Officer. The {len(verified_docs)} documents already verified are internally consistent."] if pending_docs else [f"All required documents received and verified at {doc_confidence}% confidence, providing strong assurance of entity legitimacy."])
                    + ([f"{country} is currently compliant with FATF standards following completion of its action plan. The entity's business activity is consistent with the jurisdiction's commercial profile."] if is_offshore else [])
                    + ([f"Sanctions screening completed across all major consolidated lists (UN, EU, OFAC, HMT) with {'no matches' if not all_peps else 'PEP identification and appropriate enhanced measures'}."] if screening_terminal else [
                        "Sanctions / PEP screening NOT yet complete for at least one subject. No reliance on absence of provider matches at this time."
                    ])
                    + ([f"Beneficial ownership fully traced to natural person level via {struct_complexity.lower()} structure. {control_name} ({control_pct}%) confirmed as exercising effective control."] if primary_ubo else [])
                    + ["Transaction monitoring will be applied on a quarterly basis for the first 12 months, with automated alerts for anomalous volumes, compensating for the absence of historical benchmarking data."]
                )
            },
            "compliance_decision": {
                "title": "Compliance Decision",
                "decision": decision,
                "content": (
                    f"On the basis of the composite risk assessment ({risk_display['assessment']}), "
                    f"{'clean' if (not all_peps and screening_terminal) else 'pending' if not screening_terminal else 'flagged'} screening results, "
                    f"{'unavailable' if not has_documents else 'verified' if not pending_docs else 'partially verified'} documentation ({doc_confidence}% confidence), "
                    f"and {'confirmed' if ubos else 'unverified'} beneficial ownership, "
                    f"this application is recommended for {decision_label}. "
                    + (f"The {'conditions' if risk_level in ('MEDIUM', 'HIGH') else 'recommendation'} reflect{'s' if risk_level == 'LOW' else ''} the residual risks identified — "
                       f"{'principally the PEP exposure and ' if all_peps else ''}{'absence of uploaded documents' if not has_documents else 'documentation gap' if pending_docs else 'limited trading history'}. " if risk_level != "LOW" else "The low-risk profile supports standard onboarding with no additional conditions. ")
                    + ("Conditions of approval: " if risk_level in ("MEDIUM", "HIGH") else "")
                    + (f"(1) {'PEP declaration form(s) must be completed and signed by ' + ', '.join([p['full_name'] for p in all_peps]) + ' within 14 business days. ' if all_peps else ''}" if risk_level != "LOW" else "")
                    + (f"{'(2) ' if all_peps else '(1) '}All required corporate and identity documents must be uploaded and reviewed within 14 business days before the onboarding decision can be treated as fully supported. " if not has_documents and risk_level != "LOW" else "")
                    + (f"{'(2) ' if all_peps else '(1) '}Outstanding documents must be received within 14 business days. Failure to comply will trigger escalation. " if has_documents and pending_docs and risk_level != "LOW" else "")
                    + (f"{'(3) ' if all_peps and (pending_docs or not has_documents) else '(2) ' if all_peps or pending_docs or not has_documents else '(1) '}Enhanced monitoring ({mon_tier.lower()} tier) for the first 12 months. " if risk_level in ("MEDIUM", "HIGH", "VERY_HIGH") or all_peps else "")
                    + f"Residual risk acknowledgement: {'Residual risk remains manageable within the conditions framework and does not warrant rejection at this stage.' if risk_level in ('MEDIUM', 'HIGH') else 'Minimal residual risk identified.' if risk_level == 'LOW' else 'Residual risk is elevated and requires SCO determination before proceeding.'}"
                )
            },
            "ongoing_monitoring": {
                "title": "Ongoing Monitoring & Review",
                "content": (
                    f"Monitoring tier: {mon_tier} — assigned due to "
                    + (f"the combination of {'PEP presence, ' if all_peps else ''}{'offshore jurisdiction, ' if is_offshore else ''}{'elevated sector risk, ' if is_high_risk_sector or is_medium_risk_sector else ''}{risk_level} composite risk." if mon_tier == "Enhanced"
                       else f"the {risk_level} composite risk profile with no material PEP or jurisdictional concerns.")
                    + f" Review frequency: {'6 months' if risk_level in ('HIGH', 'VERY_HIGH') else '12 months'} (standard for {risk_level} risk entities)"
                    + (f", with an interim 6-month review triggered by PEP conditions" if all_peps and risk_level not in ("HIGH", "VERY_HIGH") else "")
                    + f". Transaction monitoring: {'Quarterly' if risk_level != 'LOW' else 'Annual'} review of transaction patterns against stated business activity. "
                    + "Trigger events requiring immediate review: (1) Any change in beneficial ownership structure or directorship composition. "
                    + "(2) Adverse media alerts from continuous screening. "
                    + (f"(3) Change in PEP status for {', '.join([p['full_name'] for p in all_peps])}. " if all_peps else "(3) New PEP identification among associated persons. ")
                    + "(4) Transaction volumes exceeding 150% of stated expectations in any quarter. "
                    + "(5) Regulatory action, investigation, or enforcement proceedings against the entity or any associated person. "
                    + (f"(6) Failure to provide outstanding documents within the 14-business-day deadline." if pending_docs else "")
                )
            },
            "audit_and_governance": {
                "title": "Audit & Governance",
                "content": (
                    f"This compliance onboarding memo was generated by RegMind's deterministic memo builder and validated against structured onboarding data. "
                    f"It reflects the live onboarding control path used in this environment: deterministic memo generation, memo validation, and memo supervisor consistency checks. "
                    f"Separate monitoring agents and decision-support pipelines may exist elsewhere in the platform, but they are not represented here as authoritative memo-generation controls. "
                    f"The memo supervisor performs contradiction detection and consistency review over this memo output. "
                    f"Document classification: CONFIDENTIAL — this document contains personal data subject to applicable data protection legislation. "
                    f"Retention period: 7 years from date of generation or termination of business relationship, whichever is later. "
                    f"Applicable compliance frameworks: FATF 40 Recommendations (2012, as updated), applicable AML/CFT legislation in {country}. "
                    f"Generated: {now_ts}. Memo version: 1.0. "
                    f"Reviewed by: {app.get('assigned_to', 'Pending assignment')} — memo pending Senior Compliance Officer review."
                )
            }
        },
        "metadata": {
            "risk_rating": risk_display["level"] or "NOT_RATED",
            "risk_score": risk_display["score"],
            "computed_routing_risk": aggregated_risk,
            "computed_routing_score": risk_score,
            "canonical_risk": risk_display,
            "display_risk_rating": risk_display["level"] or "NOT_RATED",
            "display_risk_score": risk_display["score"],
            "original_risk_level": pre_elevation_risk_level,
            "aggregated_risk": aggregated_risk,
            "weighted_risk_score": round(weighted_risk, 2),
            "confidence_level": model_confidence / 100,
            "low_confidence_flag": low_confidence,
            "ownership_risk_rating": own_rating,
            "ownership_risk_justification": own_rating_justification,
            "supervisor_status": "pending",
            "ai_source": "deterministic" if not is_demo() else "demo",
            "approval_recommendation": decision,
            "document_count": len(documents),
            "verified_document_count": len(verified_docs),
            "pending_document_count": len(pending_docs),
            "documentation_complete": documentation_complete,
            "key_findings": [
                f"Beneficial ownership {'traced to natural persons via ' + struct_complexity.lower() + ' structure — ' + control_name + ' (' + str(control_pct) + '%) exercises effective control' if primary_ubo else 'could not be verified — critical data gap'}",
                f"{'PEP identified: ' + ', '.join([p['full_name'] + ' (' + ('Director' if p in pep_directors else 'UBO') + ')' for p in all_peps]) + '. Enhanced due diligence required.' if all_peps else 'No PEP exposure identified among directors or UBOs'}",
                f"Sanctions screening {'clear' if (not all_peps and screening_terminal) else ('NOT complete — provider result pending or unavailable for at least one subject' if not screening_terminal else 'completed with PEP identification')}; adverse media {'clear' if adverse_media_context['terminal'] and not adverse_media_context['has_hit'] else ('hit(s) require disposition' if adverse_media_context['has_hit'] else 'NOT complete — clean reliance unavailable')}",
                ("No documents uploaded — entity verification remains incomplete" if not has_documents else f"{len(verified_docs)} of {len(documents)} documents verified at {doc_confidence}% confidence" + (f"; {len(pending_docs)} outstanding" if pending_docs else " — full documentation")),
                f"{'Business model assessed as plausible and consistent with regulatory authorisations' if sector != 'Information not provided' else 'Business model assessment limited by insufficient sector data'}",
                f"{country} jurisdiction presents {'severe' if is_high_risk_country else 'moderate' if is_offshore else 'low'} risk — {'sanctions/FATF blacklist' if is_high_risk_country else 'offshore IFC classification' if is_offshore else 'adequate AML/CFT framework'}"
            ],
            "conditions": (
                ([f"PEP declaration form(s) to be completed and signed by {', '.join([p['full_name'] for p in all_peps])} within 14 business days"] if all_peps else [])
                + (["All required corporate and identity documents must be uploaded and reviewed before the onboarding recommendation can be relied upon"] if not has_documents else [])
                + ([f"Outstanding documents ({', '.join([d.get('doc_type', 'document') for d in pending_docs])}) to be received within 14 business days — escalation on non-compliance"] if pending_docs else [])
                + ([f"Enhanced monitoring ({mon_tier.lower()} tier) for first 12 months with quarterly transaction review"] if risk_level in ("MEDIUM", "HIGH", "VERY_HIGH") or all_peps else [])
                + ([f"Bank reference letter required for PEP(s): {', '.join([p['full_name'] for p in all_peps])}"] if all_peps else [])
            ),
            "review_checklist": [
                f"Company identity verified against registry — {'confirmed active' if verified_docs else 'not yet evidenced by uploaded documents' if not has_documents else 'pending verification'}",
                f"UBO chain mapped to natural persons: {control_name + ' (' + str(control_pct) + '%)' if primary_ubo else 'Not verified — data gap'}",
                f"PEP screening {'completed' if screening_terminal else 'NOT complete'} — {len(all_peps)} declared/detected match(es)" + (f": {', '.join([p['full_name'] for p in all_peps])}" if all_peps else ""),
                ("Sanctions screening completed — no matches across UN, EU, OFAC, HMT lists" if screening_terminal
                 else "Sanctions screening NOT complete — provider result pending or unavailable for at least one subject"),
                adverse_media_context["checklist"],
                f"Source of funds {'reviewed and assessed as consistent' if sof != 'Information not provided' else 'not provided — data gap flagged'}",
                f"Business model plausibility {'confirmed' if sector != 'Information not provided' else 'assessment limited by data gap'}",
                (f"Document verification not started — no uploaded documents available ({len(verified_docs)}/{len(documents)}) at {doc_confidence}% confidence" if not has_documents else f"Document verification completed ({len(verified_docs)}/{len(documents)}) at {doc_confidence}% confidence"),
                risk_display["checklist"] + f" at {max(60, doc_confidence - 5)}% model confidence",
                f"Compliance decision ({decision.replace('_', ' ')}) aligned with risk assessment findings and conditions framework"
            ],
            "rule_engine": rule_engine_result,
            "screening_state_summary": {
                "terminal": screening_terminal,
                "has_non_terminal": screening_has_non_terminal,
                "has_failed": screening_has_failed,
                "has_not_configured": screening_has_not_configured,
                "company_state": _company_state,
                "person_states": _person_states,
                "declared_pep_count": len(all_peps),
            },
            "risk_dimensions": {
                "jurisdiction": {"rating": jur_rating, "weight": 0.20, "evidence": jurisdiction_evidence},
                "ownership": {"rating": own_rating, "weight": 0.25, "justification": own_rating_justification},
                "business": {"rating": biz_rating, "weight": 0.15, "evidence": business_evidence},
                "fincrime": {"rating": fc_rating, "weight": 0.10, "evidence": financial_crime_evidence},
                "transaction": {"rating": tx_rating, "weight": 0.10},
                "documentation": {"rating": doc_rating, "weight": 0.10},
                "data_quality": {"rating": dq_rating, "weight": 0.10}
            },
            "factor_classification_rules": {
                "always_risk_decreasing": ALWAYS_RISK_DECREASING,
                "always_risk_increasing": ALWAYS_RISK_INCREASING
            }
        }
    }

    quality_caps = []
    if not screening_terminal:
        quality_caps.append(_quality_cap(
            "screening_not_terminal",
            6.4,
            "warning",
            "Provider-backed sanctions/PEP/watchlist screening is not terminal for every subject.",
            "Complete live screening before treating the memo as pilot-ready evidence.",
        ))
    if not adverse_media_context["terminal"]:
        quality_caps.append(_quality_cap(
            "adverse_media_not_terminal",
            7.5,
            "warning",
            "Adverse-media coverage is not terminal; the memo cannot claim a clean adverse-media result.",
            "Run approved adverse-media coverage or retain the evidence gap as a condition.",
        ))
    if not has_documents:
        quality_caps.append(_quality_cap(
            "no_documents_uploaded",
            6.5,
            "warning",
            "No supporting documents are available for entity or identity verification.",
            "Collect and verify required documents before relying on the memo for approval.",
        ))
    elif pending_docs:
        quality_caps.append(_quality_cap(
            "documents_pending_verification",
            7.5,
            "warning",
            str(len(pending_docs)) + " document(s) remain unverified.",
            "Verify or formally disposition outstanding documents.",
        ))
    missing_profile_fields = [
        label for label, value in (
            ("source_of_funds", sof),
            ("expected_volume", exp_vol),
            ("operating_countries", operating_countries),
            ("business_activity", business_activity),
        )
        if value == "Information not provided"
    ]
    if missing_profile_fields:
        quality_caps.append(_quality_cap(
            "critical_profile_data_missing",
            6.8,
            "warning",
            "Critical profile fields are missing: " + ", ".join(missing_profile_fields) + ".",
            "Complete the missing profile fields or document why the data gap is acceptable.",
        ))

    memo["metadata"]["memo_integrity_version"] = "phase3_v1"
    memo["metadata"]["adverse_media_state_summary"] = {
        "terminal": adverse_media_context["terminal"],
        "coverage": adverse_media_context["coverage"],
        "has_hit": adverse_media_context["has_hit"],
    }
    memo["metadata"]["risk_evidence"] = {
        "jurisdiction": jurisdiction_evidence,
        "business": business_evidence,
        "financial_crime": financial_crime_evidence,
    }
    memo["metadata"]["quality_caps"] = quality_caps
    # Source attribution — structured record of data sources used in this memo.
    # Enables a compliance buyer to trace each section back to its evidence base.
    memo["metadata"]["source_attribution"] = {
        "application_id": app.get("id"),
        "application_ref": app.get("ref"),
        "generation_pipeline": "rule_engine → memo_handler → validation_engine → supervisor",
        "screening_sources": {
            "company_screened": bool(screening_report.get("company_screening")),
            "persons_screened": len(_person_states),
            "screening_terminal": screening_terminal,
            **_screening_source_summary(screening_report),
        },
        "document_sources": {
            "total": len(documents),
            "verified": len(verified_docs),
            "pending": len(pending_docs),
            "types": sorted(list({d.get("doc_type", "unknown") for d in documents})),
        },
        "party_sources": party_source_summary,
        "rule_engine_checks": len(rule_engine_result.get("rules_checked", [])),
        "rule_engine_violations": rule_engine_result.get("total_violations", 0),
        "risk_factors_used": {
            "pep_count": len(all_peps),
            "jurisdiction": country,
            "sector": sector,
            "risk_score": risk_display["score"],
            "risk_recorded": risk_display["available"],
        },
    }

    # ── RULE 4A enforcement: Post-generation factor classification check ──
    # Verify no ALWAYS_RISK_DECREASING keywords appear in risk_increasing_factors
    # and no ALWAYS_RISK_INCREASING keywords appear in risk_decreasing_factors
    ai_section = memo.get("sections", {}).get("ai_explainability", {})
    inc_factors = ai_section.get("risk_increasing_factors", [])
    dec_factors = ai_section.get("risk_decreasing_factors", [])

    corrected_inc = []
    corrected_dec = list(dec_factors)
    for factor in inc_factors:
        factor_lower = factor.lower()
        is_misclassified = False
        for kw in ALWAYS_RISK_DECREASING:
            if kw in factor_lower:
                is_misclassified = True
                rule_violations.append({
                    "rule": "FACTOR_CLASSIFICATION",
                    "severity": "high",
                    "detail": "Risk-decreasing keyword '" + kw + "' found in risk_increasing_factors: " + factor[:80],
                    "action": "Moved to risk_decreasing_factors"
                })
                corrected_dec.append(factor)
                break
        if not is_misclassified:
            corrected_inc.append(factor)

    corrected_dec_final = []
    for factor in corrected_dec:
        factor_lower = factor.lower()
        is_misclassified = False
        for kw in ALWAYS_RISK_INCREASING:
            if kw in factor_lower:
                is_misclassified = True
                rule_violations.append({
                    "rule": "FACTOR_CLASSIFICATION",
                    "severity": "high",
                    "detail": "Risk-increasing keyword '" + kw + "' found in risk_decreasing_factors: " + factor[:80],
                    "action": "Moved to risk_increasing_factors"
                })
                corrected_inc.append(factor)
                break
        if not is_misclassified:
            corrected_dec_final.append(factor)

    # Apply corrections
    ai_section["risk_increasing_factors"] = corrected_inc
    ai_section["risk_decreasing_factors"] = corrected_dec_final

    # Update rule engine result with any new violations found during factor enforcement
    rule_engine_result["violations"] = rule_violations
    rule_engine_result["total_violations"] = len(rule_violations)
    if rule_violations:
        rule_engine_result["engine_status"] = "VIOLATIONS_DETECTED"
    memo["metadata"]["rule_engine"] = rule_engine_result

    # ── Priority A.2: Declared-PEP truthfulness guardrail ────────────
    # If any director or UBO is declared as PEP, the memo narrative
    # MUST NOT emit phrases that deny PEP exposure. We scan the built
    # memo for banned phrasings, rewrite each occurrence with a truthful
    # qualifier that preserves declared-PEP visibility, and record an
    # audit-grade rule violation per scrub. This is a hard guardrail:
    # even if upstream conditional gating fails, the memo cannot be
    # shipped claiming "no PEP exposure" while declared PEP exists.
    declared_pep_guardrail = {
        "applied": False,
        "declared_pep_count": len(all_peps),
        "scrubs": [],
    }
    if has_declared_pep:
        _declared_names = ", ".join([p.get("full_name", "") for p in all_peps if p.get("full_name")])
        _qualifier = (
            "Declared PEP exposure present"
            + (" (" + _declared_names + ")" if _declared_names else "")
        )
        # Phrase → replacement. Match is case-insensitive; replacement
        # preserves the original casing pattern by lowercasing the
        # surrounding string before substitution. Phrases ordered from
        # most specific to least specific to avoid double-rewrites.
        _banned_phrases = [
            "no pep exposure identified in the ownership or governance structure",
            "no pep exposure identified among directors or ubos",
            "no pep exposure identified",
            "no pep exposure",
            "no declared or detected matches",
            "no declared or detected pep",
            "no material pep or jurisdictional concerns",
            "no material pep concerns",
            "no material pep",
            "0 self-declared / detected match(es)",
            "0 self-declared / detected matches",
            "0 self-declared / detected match",
        ]

        def _scrub(text):
            if not isinstance(text, str) or not text:
                return text
            scrubbed = text
            lower = scrubbed.lower()
            for phrase in _banned_phrases:
                idx = 0
                while True:
                    found = lower.find(phrase, idx)
                    if found < 0:
                        break
                    end = found + len(phrase)
                    scrubbed = scrubbed[:found] + _qualifier + scrubbed[end:]
                    lower = scrubbed.lower()
                    declared_pep_guardrail["scrubs"].append({
                        "phrase": phrase,
                        "qualifier": _qualifier,
                    })
                    declared_pep_guardrail["applied"] = True
                    idx = found + len(_qualifier)
            return scrubbed

        def _walk_and_scrub(node):
            if isinstance(node, str):
                return _scrub(node)
            if isinstance(node, list):
                return [_walk_and_scrub(v) for v in node]
            if isinstance(node, dict):
                return {k: _walk_and_scrub(v) for k, v in node.items()}
            return node

        memo["sections"] = _walk_and_scrub(memo.get("sections") or {})
        # Also scrub key_findings / review_checklist / conditions which
        # are lists of free-text strings inside metadata.
        for _meta_key in ("key_findings", "review_checklist", "conditions"):
            if _meta_key in memo["metadata"]:
                memo["metadata"][_meta_key] = _walk_and_scrub(memo["metadata"][_meta_key])

        if declared_pep_guardrail["applied"]:
            rule_violations.append({
                "rule": "DECLARED_PEP_TRUTHFULNESS",
                "severity": "high",
                "detail": (
                    "Memo narrative contained PEP-denial phrasing while "
                    + str(len(all_peps))
                    + " declared PEP(s) exist. Phrases were rewritten with "
                    "truthful qualifier; supervisor and validator will "
                    "still surface this contradiction."
                ),
                "action": "Scrubbed " + str(len(declared_pep_guardrail["scrubs"])) + " occurrence(s)",
            })
            rule_engine_result["violations"] = rule_violations
            rule_engine_result["total_violations"] = len(rule_violations)
            rule_engine_result["engine_status"] = "VIOLATIONS_DETECTED"
            memo["metadata"]["rule_engine"] = rule_engine_result

    memo["metadata"]["declared_pep_guardrail"] = declared_pep_guardrail

    # ── Priority B / Workstream A: Agent 5 authoritative input contract ──
    # The contract pins the case facts that the memo narrative MUST
    # respect. Downstream guards (narrative contradiction check below,
    # supervisor mandatory_escalation, EDD routing policy) all read
    # from this single source so the decision path cannot diverge from
    # the facts. Keep the keys aligned with REQUIRED_FACT_KEYS in
    # edd_routing_policy.py.
    # ── Priority B.2: Ownership transparency normalization ────────────
    # The deterministic facts handed to Agent 5 / EDD routing must
    # reflect ALL signals, not just struct_complexity == "Complex" or
    # missing-pct gaps. A case explicitly described as multi-tier /
    # multi-jurisdiction / nominee / shell / opaque, OR a case where
    # the disclosed UBO ownership totals less than 75%, MUST normalize
    # to "incomplete" or "opaque" — never "transparent".
    _own_struct_lc = (own_struct or "").lower()
    _matched_opaque_keywords = sorted({kw for kw in OPAQUE_OWNERSHIP_KEYWORDS if kw in _own_struct_lc})
    # Multi-jurisdiction is a known opaqueness driver but not in the
    # core OPAQUE_OWNERSHIP_KEYWORDS set; surface it explicitly.
    if "multi-jurisdiction" in _own_struct_lc or "multi jurisdiction" in _own_struct_lc \
            or "multiple jurisdictions" in _own_struct_lc:
        _matched_opaque_keywords = sorted(set(_matched_opaque_keywords) | {"multi-jurisdiction"})
    _total_disclosed_pct = sum(float(u.get("ownership_pct", 0) or 0) for u in ubos) if ubos else 0.0

    _is_opaque = (
        own_rating == "HIGH"
        or struct_complexity == "Complex"
        or bool(_matched_opaque_keywords)
        or (ubos and _total_disclosed_pct < 50)
    )
    _is_incomplete = (
        ownership_has_gaps
        or (ubos and _total_disclosed_pct < 75)
    )
    _ownership_status = (
        "opaque" if _is_opaque
        else "incomplete" if _is_incomplete
        else "transparent"
    )
    _has_terminal_match = any(s == _S_MATCH for s in _all_states)
    edd_trigger_flags = []
    try:
        _raw_esc = app.get("risk_escalations") or "[]"
        _esc_list = json.loads(_raw_esc) if isinstance(_raw_esc, str) else (_raw_esc or [])
        if isinstance(_esc_list, list):
            for _esc in _esc_list:
                if isinstance(_esc, dict):
                    _label = _esc.get("rule") or _esc.get("reason") or ""
                else:
                    _label = str(_esc)
                if _label:
                    edd_trigger_flags.append(_label)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    agent5_input_contract = {
        "final_risk_level": aggregated_risk,
        "composite_score": risk_score,
        "risk_dimensions": {
            "jurisdiction": jur_rating,
            "ownership": own_rating,
            "business": biz_rating,
            "fincrime": fc_rating,
            "transaction": tx_rating,
            "documentation": doc_rating,
            "data_quality": dq_rating,
        },
        "declared_pep_present": has_declared_pep,
        "declared_pep_count": len(all_peps),
        "jurisdiction_risk_tier": jur_rating,
        "sector_risk_tier": biz_rating,
        "sector_label": sector,
        "ownership_transparency_status": _ownership_status,
        "screening_terminality_summary": {
            "terminal": screening_terminal,
            "has_non_terminal": screening_has_non_terminal,
            "has_failed": screening_has_failed,
            "has_not_configured": screening_has_not_configured,
            "has_terminal_match": _has_terminal_match,
            "company_screening_configured": bool(screening_report.get("company_screening")),
        },
        "company_screening_configured": bool(screening_report.get("company_screening")),
        "edd_trigger_flags": edd_trigger_flags,
        "decision_recommendation": decision,
        "monitoring_tier": mon_tier,
    }
    memo["metadata"]["agent5_input_contract"] = agent5_input_contract

    # ── Priority B / Workstream A: Narrative contradiction guard ──────
    # Walk the memo narrative and surface contradictions against the
    # authoritative input contract. Each contradiction is recorded as a
    # high-severity rule violation so the supervisor (which already
    # ingests rule_engine.violations as critical contradictions) cannot
    # return CONSISTENT for a memo that lies about the facts. We
    # intentionally do not rewrite the prose — officers must see the
    # supervisor verdict flip to INCONSISTENT and re-generate.
    def _narrative_text():
        chunks = []
        for sec in (memo.get("sections") or {}).values():
            if not isinstance(sec, dict):
                continue
            for k, v in sec.items():
                if isinstance(v, str):
                    chunks.append(v)
                elif isinstance(v, dict):
                    for sub in v.values():
                        if isinstance(sub, dict):
                            c = sub.get("content")
                            if isinstance(c, str):
                                chunks.append(c)
        for key in ("key_findings", "review_checklist", "conditions"):
            for s in (memo["metadata"].get(key) or []):
                if isinstance(s, str):
                    chunks.append(s)
        return "\n".join(chunks).lower()

    _narr_lower = _narrative_text()

    # Phrasings that flatten elevated facts to "low/clean/transparent".
    # Each entry: (predicate_bool, list_of_banned_phrases, rule_tag, description)
    _contradiction_checks = [
        (
            jur_rating in ("HIGH", "VERY_HIGH"),
            [
                "low jurisdictional risk", "low jurisdiction risk",
                "presents low jurisdictional risk",
                "presents low risk",
                "low-risk jurisdiction",
                "jurisdiction is low risk",
            ],
            "AGENT5_NARRATIVE_CONTRADICTION_JURISDICTION",
            "Jurisdiction risk is " + jur_rating + " but narrative describes it as low.",
        ),
        (
            biz_rating in ("HIGH",),
            [
                "low business risk", "low sector risk",
                "low-risk sector", "sector presents low risk",
                "no enhanced sector concern",
            ],
            "AGENT5_NARRATIVE_CONTRADICTION_SECTOR",
            "Sector/business risk is " + biz_rating + " but narrative describes it as low.",
        ),
        (
            not screening_terminal,
            [
                "sanctions screening completed across all major consolidated lists",
                "screening completed — no matches across un, eu, ofac, hmt lists",
                "sanctions screening completed — no matches",
                "clean sanctions screening across all major consolidated lists",
                "screening completed and clean",
                "no screening concerns identified",
            ],
            "AGENT5_NARRATIVE_CONTRADICTION_SCREENING",
            "Screening is non-terminal (provider pending/not_configured/failed for at least one subject) "
            "but narrative claims screening is completed/clean.",
        ),
        (
            _ownership_status in ("opaque", "incomplete"),
            [
                "fully transparent ownership",
                "complete ownership transparency",
                "ownership is fully transparent",
                "fully traceable beneficial ownership chain",
                "clean ownership structure with full transparency",
            ],
            "AGENT5_NARRATIVE_CONTRADICTION_OWNERSHIP",
            "Ownership transparency status is " + _ownership_status
            + " but narrative describes ownership as fully transparent.",
        ),
    ]

    for _predicate, _phrases, _rule_tag, _desc in _contradiction_checks:
        if not _predicate:
            continue
        _matched = [p for p in _phrases if p in _narr_lower]
        if _matched:
            rule_violations.append({
                "rule": _rule_tag,
                "severity": "high",
                "detail": _desc + " Matched phrasing: " + "; ".join(_matched[:3]),
                "action": "Contradiction surfaced — supervisor will mark INCONSISTENT.",
            })

    # EDD-trigger-but-described-as-standard check. We say a case is
    # "EDD-triggering" if final risk is HIGH/VERY_HIGH, or there is a
    # declared PEP, or jurisdiction is high/very_high, or sector is
    # high. This mirrors the policy in edd_routing_policy.py without
    # importing it (so this module remains policy-agnostic).
    _is_edd_triggering = (
        aggregated_risk in ("HIGH", "VERY_HIGH")
        or has_declared_pep
        or jur_rating in ("HIGH", "VERY_HIGH")
        or biz_rating == "HIGH"
        or _ownership_status == "opaque"
    )
    if _is_edd_triggering:
        _standard_phrases = [
            "standard review pathway",
            "no enhanced concern",
            "no enhanced compliance concern",
            "ordinary review process",
            "no edd required",
            "edd is not required",
            "no enhanced due diligence required",
        ]
        _matched = [p for p in _standard_phrases if p in _narr_lower]
        if _matched:
            rule_violations.append({
                "rule": "AGENT5_NARRATIVE_CONTRADICTION_EDD",
                "severity": "high",
                "detail": (
                    "Case is EDD-triggering (risk=" + aggregated_risk
                    + ", declared_pep=" + str(has_declared_pep)
                    + ", jurisdiction=" + jur_rating
                    + ", sector=" + biz_rating
                    + ", ownership=" + _ownership_status
                    + ") but narrative describes it as standard/no-enhanced-concern. "
                    "Matched phrasing: " + "; ".join(_matched[:3])
                ),
                "action": "Contradiction surfaced — supervisor will mark INCONSISTENT.",
            })

    # Re-sync rule engine result if narrative contradictions were added.
    if rule_violations is not rule_engine_result["violations"]:
        rule_engine_result["violations"] = rule_violations
    rule_engine_result["total_violations"] = len(rule_violations)
    if rule_engine_result["total_violations"] > 0:
        rule_engine_result["engine_status"] = "VIOLATIONS_DETECTED"
    memo["metadata"]["rule_engine"] = rule_engine_result

    # Run memo supervisor — contradiction detection & verdict
    supervisor_result = run_memo_supervisor(memo)
    memo["supervisor"] = supervisor_result
    memo["metadata"]["supervisor_status"] = supervisor_result["verdict"]
    memo["metadata"]["supervisor_confidence"] = supervisor_result["supervisor_confidence"]

    # ── Priority B / Workstream C: Server-side EDD routing policy ─────
    # Evaluate the deterministic policy from the authoritative input
    # contract (now augmented with the supervisor's mandatory_escalation
    # flag) and persist the routing decision on the memo. The audit-log
    # row is written by the HTTP handler that owns the DB cursor.
    try:
        routing_facts = dict(agent5_input_contract)
        routing_facts["supervisor_mandatory_escalation"] = bool(
            supervisor_result.get("mandatory_escalation", False)
        )
        edd_routing = _evaluate_edd_routing(routing_facts)
    except Exception as _routing_err:  # pragma: no cover — defensive
        logger.error("EDD routing evaluation failed: %s", _routing_err)
        edd_routing = {
            "policy_version": "edd_routing_policy_v1",
            "route": "edd",  # fail-closed
            "triggers": ["routing_evaluation_failed"],
            "inputs": {},
            "evaluated_at": now_ts,
        }
    memo["metadata"]["edd_routing"] = edd_routing

    # ── Priority B.2 / Workstream C: Bind memo recommendation to route ──
    # Deterministic guarantee: when the routing policy says EDD, OR
    # the supervisor has flagged mandatory_escalation, the memo's
    # approval_recommendation MUST NOT be APPROVE / APPROVE_WITH_CONDITIONS.
    # We override to the canonical escalation value ESCALATE_TO_EDD
    # and re-record the original value for auditability. This closes
    # the gap where memo could recommend approval while routing said
    # EDD and supervisor said mandatory_escalation.
    _route_is_edd = (edd_routing or {}).get("route") == "edd"
    _is_mandatory_escalation = bool(supervisor_result.get("mandatory_escalation", False))
    if _route_is_edd or _is_mandatory_escalation:
        _original_decision = memo["metadata"].get("approval_recommendation")
        _approval_like = ("APPROVE", "APPROVE_WITH_CONDITIONS")
        if _original_decision in _approval_like:
            rule_enforcements.append({
                "rule": "RECOMMENDATION_BOUND_TO_EDD_ROUTE",
                "original_decision": _original_decision,
                "enforced_decision": "ESCALATE_TO_EDD",
                "reason": (
                    "Routing policy route=" + str((edd_routing or {}).get("route"))
                    + " (triggers: " + ", ".join((edd_routing or {}).get("triggers", [])[:6]) + ")"
                    + "; supervisor.mandatory_escalation=" + str(_is_mandatory_escalation)
                    + ". Memo recommendation cannot be an approval value."
                ),
            })
            rule_engine_result["enforcements"] = rule_enforcements
            memo["metadata"]["rule_engine"] = rule_engine_result
        # Always set escalation values so officer-visible recommendation
        # and workflow state cannot diverge.
        memo["metadata"]["approval_recommendation_original"] = _original_decision
        memo["metadata"]["approval_recommendation"] = "ESCALATE_TO_EDD"
        memo["metadata"]["decision_label"] = "ESCALATE TO EDD"
        # Mirror inside the agent5 input contract so downstream
        # consumers reading the contract see the bound value too.
        try:
            agent5_input_contract["decision_recommendation"] = "ESCALATE_TO_EDD"
            memo["metadata"]["agent5_input_contract"] = agent5_input_contract
        except Exception:
            pass
        # Update the compliance_decision section content if it exists,
        # so officer-visible narrative does not contradict the workflow.
        try:
            _route_triggers = ", ".join((edd_routing or {}).get("triggers", [])[:6])
            _route_policy = str((edd_routing or {}).get("policy_version", ""))
            _esc_statement = (
                "Recommendation: ESCALATE TO EDD (ESCALATE_TO_EDD) — deterministic routing policy "
                + _route_policy
                + " requires Enhanced Due Diligence before any approval"
                + (" (triggers: " + _route_triggers + ")" if _route_triggers else "")
                + "."
            )
            _exec_sec = (memo.get("sections") or {}).get("executive_summary") or {}
            if isinstance(_exec_sec, dict):
                _exec_content = _exec_sec.get("content") or ""
                if "Recommendation:" in _exec_content:
                    _exec_sec["content"] = _exec_content.split("Recommendation:", 1)[0].rstrip() + " " + _esc_statement
                else:
                    _exec_sec["content"] = (_exec_content.rstrip() + " " + _esc_statement).strip()
                memo["sections"]["executive_summary"] = _exec_sec

            _decision_sec = (memo.get("sections") or {}).get("compliance_decision") or {}
            if isinstance(_decision_sec, dict):
                _decision_sec["decision"] = "ESCALATE_TO_EDD"
                _decision_sec["decision_label"] = "ESCALATE TO EDD"
                _decision_sec["content"] = (
                    _esc_statement
                    + " This memo is not an approval recommendation; it is an escalation artefact pending EDD outcome and senior compliance sign-off."
                )
                memo["sections"]["compliance_decision"] = _decision_sec

            memo["metadata"]["conditions"] = [
                "Enhanced Due Diligence must be completed before any approval decision can be relied upon.",
                "Senior compliance sign-off is required after EDD findings are recorded.",
            ]
            _checklist = list(memo["metadata"].get("review_checklist") or [])
            if _checklist:
                _checklist[-1] = "Compliance decision aligned to ESCALATE TO EDD; approval is unavailable until EDD is complete"
                memo["metadata"]["review_checklist"] = _checklist
        except Exception:
            pass

    # ── Priority B.2 / Workstream C: Contradiction guard (fail-closed) ──
    # Defensive cross-check: if for any reason the recommendation
    # ended up as an approval value while the route is EDD or
    # mandatory_escalation is set, block the memo. This is belt-and-
    # braces — the binding above should already prevent this — and
    # ensures persistence cannot silently store a contradicting memo.
    _final_decision = memo["metadata"].get("approval_recommendation")
    if (_route_is_edd or _is_mandatory_escalation) and _final_decision in ("APPROVE", "APPROVE_WITH_CONDITIONS"):
        memo["metadata"]["validation_status"] = "fail"
        memo["metadata"]["blocked"] = True
        memo["metadata"]["block_reason"] = (
            "Memo blocked: recommendation '" + str(_final_decision)
            + "' contradicts routing policy (route="
            + str((edd_routing or {}).get("route"))
            + ", mandatory_escalation=" + str(_is_mandatory_escalation)
            + "). Recommendation must be ESCALATE_TO_EDD."
        )

    # Run validation engine — now with rule engine awareness
    validation_result = validate_compliance_memo(memo)
    memo["validation"] = validation_result
    memo["metadata"]["quality_score"] = validation_result["quality_score"]
    memo["metadata"]["validation_status"] = validation_result["validation_status"]

    # ── Final gate: If rule violations exist AND supervisor is INCONSISTENT, block memo
    if (rule_engine_result["total_violations"] > 0
            and supervisor_result["verdict"] == "INCONSISTENT"):
        memo["metadata"]["validation_status"] = "fail"
        memo["metadata"]["blocked"] = True
        memo["metadata"]["block_reason"] = (
            "Memo blocked: " + str(rule_engine_result["total_violations"]) + " rule violation(s) detected "
            "AND supervisor verdict is INCONSISTENT. Manual review required before approval."
        )

    return memo, rule_engine_result, supervisor_result, validation_result
