"""
ARIE Finance — Memo Handler: Compliance Memo Building Logic
Extracted from ComplianceMemoHandler.post() during Sprint 2 monolith decomposition.

Provides:
    - build_compliance_memo(app, directors, ubos, documents) → (memo, rule_engine_result, supervisor_result, validation_result)
"""
import json
import logging
from copy import deepcopy
from datetime import datetime

from validation_engine import validate_compliance_memo
from supervisor_engine import run_memo_supervisor
from edd_routing_policy import evaluate_edd_routing as _evaluate_edd_routing
from branding import BRAND
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
MEMO_OUTPUT_PROFILE_VERSION = "pr5b_decision_paper_v2"


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
        person_key = str(party.get("person_key") or "").strip().lower()
        if person_key:
            key = ("person_key", person_key)
        else:
            key = (
                "identity",
                str(party.get("full_name") or "").strip().lower(),
                str(party.get("date_of_birth") or party.get("dob") or "").strip().lower(),
                str(party.get("nationality") or "").strip().lower(),
                str(party.get("ownership_pct") or "").strip().lower(),
                str(party.get("role") or role).strip().lower(),
            )
        if key in seen:
            continue
        seen.add(key)
        normalised.append(party)
    return normalised


def _resolve_memo_parties(directors, ubos, prescreening_data):
    resolved_directors = [deepcopy(d) for d in (directors or []) if isinstance(d, dict)]
    resolved_ubos = [deepcopy(u) for u in (ubos or []) if isinstance(u, dict)]
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
    # A non-LOW risk level with a zero score is a known stale/reporting shape
    # from Phase 0 and must not be rendered as a canonical rating. Other
    # score/level mismatches can be legitimate floor-rule elevations and are
    # handled by original_risk_level / validation below.
    stale_zero_score = level not in (None, "LOW") and score == 0
    available = level is not None and score is not None and not stale_zero_score
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
        "source": "applications.risk_score",
        "level_source": "applications.final_risk_level" if app.get("final_risk_level") not in (None, "") else "applications.risk_level",
        "calculated_at": app.get("risk_computed_at") or app.get("updated_at"),
        "risk_config_version": app.get("risk_config_version"),
        "assessment": assessment,
        "summary": summary,
        "checklist": checklist,
    }


def _screening_source_summary(screening_report, screening_reviews=None):
    if not isinstance(screening_report, dict) or not screening_report:
        return {"providers": [], "provider": "not_configured", "api_statuses": [], "mode": "not_configured"}
    try:
        from screening_state import build_screening_truth_summary as _build_screening_truth_summary
        truth_summary = _build_screening_truth_summary(screening_report, {}, screening_reviews or [])
    except Exception:  # pragma: no cover - source attribution must not break memo generation
        truth_summary = {}
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
    status_values = {str(s).strip().lower() for s in statuses if str(s).strip()}
    provider_values = {str(p).strip().lower() for p in providers if str(p).strip()}
    has_smoke_provider = any("smoke" in p for p in provider_values)
    has_simulated = bool(status_values & {"simulated", "mocked", "mock", "stubbed"}) or has_smoke_provider
    has_live = "live" in status_values
    if has_simulated and has_live:
        mode = "mixed"
    elif has_simulated:
        mode = "simulated"
    elif has_live:
        mode = "live"
    elif status_values:
        mode = "unknown"
    else:
        raw_mode = str(screening_report.get("screening_mode") or "").strip().lower()
        mode = raw_mode if raw_mode in {"live", "simulated", "not_configured", "disabled"} else "not_configured"
    return {
        "providers": providers_list,
        "provider": providers_list[0] if providers_list else "not_configured",
        "api_statuses": sorted(statuses),
        "mode": mode,
        "canonical_state": truth_summary.get("canonical_state"),
        "provider_availability": truth_summary.get("provider_availability"),
        "provider_mode": truth_summary.get("provider_mode"),
        "screening_result": truth_summary.get("screening_result"),
        "terminal": truth_summary.get("terminal"),
        "screening_terminal": truth_summary.get("screening_terminal"),
        "screening_provider_clear": truth_summary.get("screening_provider_clear"),
        "defensible_clear": truth_summary.get("defensible_clear"),
        "screening_gate_ready": truth_summary.get("screening_gate_ready"),
        "approval_gate_ready": truth_summary.get("approval_gate_ready"),
        "approval_ready": truth_summary.get("approval_ready"),
        "approval_ready_scope": truth_summary.get("approval_ready_scope"),
        "blocking_reasons": truth_summary.get("blocking_reasons") or [],
        "approval_blocked_reasons": truth_summary.get("approval_blocked_reasons") or [],
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


def _memo_collapse_text(value, max_len=None):
    text = " ".join(str(value or "").split()).strip()
    if max_len and len(text) > max_len:
        return text[: max_len - 3].rstrip() + "..."
    return text


def _memo_unique_text(items, max_items=None):
    seen = set()
    result = []
    for item in items or []:
        text = _memo_collapse_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if max_items and len(result) >= max_items:
            break
    return result


def _memo_word_count(value):
    if isinstance(value, str):
        return len(value.split())
    if isinstance(value, dict):
        return sum(_memo_word_count(v) for v in value.values())
    if isinstance(value, list):
        return sum(_memo_word_count(v) for v in value)
    return 0


def _memo_format_items(items):
    values = _memo_unique_text(items)
    return "; ".join(values) if values else "None recorded"


def _memo_clean_officer_note(value, max_len=240):
    text = _memo_collapse_text(value, max_len=max_len)
    if not text:
        return "no rationale recorded"
    lower = text.lower()
    rough_terms = ("lorem", "asdf", "dummy", "test note", "test text", "blah")
    if any(term in lower for term in rough_terms):
        return "Officer rationale recorded; raw note retained in audit evidence."
    return text


def _memo_quality_cap_label(cap):
    if not isinstance(cap, dict):
        return ""
    code = str(cap.get("code") or "").strip()
    labels = {
        "screening_not_terminal": "Screening is not terminal / not approval-ready",
        "adverse_media_not_terminal": "Adverse media review is not terminal",
        "no_documents_uploaded": "No supporting documents have been uploaded",
        "documents_pending_verification": "Document verification remains incomplete",
        "critical_profile_data_missing": "Critical profile data remains incomplete",
    }
    return labels.get(code) or cap.get("reason") or ""


def _memo_pep_detail(record):
    if not isinstance(record, dict):
        return ""
    declaration = record.get("pep_declaration") or {}
    if isinstance(declaration, str):
        try:
            declaration = json.loads(declaration)
        except Exception:
            declaration = {}
    if not isinstance(declaration, dict):
        declaration = {}
    values = []
    for label, keys in (
        ("role/type", ("pep_role_type", "role_type", "pep_type")),
        ("position/title", ("position_title", "public_function", "public_position")),
        ("jurisdiction", ("pep_country_jurisdiction", "country_jurisdiction", "jurisdiction")),
        ("relationship", ("relationship_type",)),
        ("source of wealth", ("source_of_wealth_detail", "source_of_wealth_note")),
        ("source of funds", ("source_of_funds_detail", "source_of_funds_note")),
        ("evidence/reference", ("supporting_note_evidence", "evidence_reference", "supporting_evidence_reference")),
    ):
        for key in keys:
            value = declaration.get(key)
            if value not in (None, "", []):
                values.append(label + ": " + _memo_collapse_text(value, max_len=120))
                break
    name = record.get("full_name") or record.get("name") or "associated party"
    if not values:
        pep_flag = str(record.get("is_pep") or record.get("pep") or "").strip().lower()
        if pep_flag in ("yes", "true", "1", "y"):
            return "PEP declaration recorded for " + str(name)
        return ""
    return "PEP declaration recorded for " + str(name) + ": " + "; ".join(values)


def _apply_decision_paper_cleanup(memo, context):
    """
    Rewrite the default memo into a concise decision-paper view while preserving
    the pre-cleanup sections as appendix evidence for audit/export consumers.
    """
    if not isinstance(memo, dict):
        return memo
    sections = memo.setdefault("sections", {})
    metadata = memo.setdefault("metadata", {})
    if not isinstance(sections, dict):
        return memo

    original_sections = deepcopy(sections)
    original_word_count = _memo_word_count(original_sections)
    sections = {}
    memo["sections"] = sections

    app = context.get("app") or {}
    company = app.get("company_name") or memo.get("company_name") or "Unknown entity"
    app_ref = app.get("ref") or memo.get("application_ref") or "N/A"
    brn = app.get("brn") or "not provided"
    country = context.get("country") or app.get("country") or "Information not provided"
    sector = context.get("sector") or app.get("sector") or "Information not provided"
    entity_type = context.get("entity_type") or app.get("entity_type") or "Information not provided"
    business_activity = context.get("business_activity") or "Information not provided"
    sof = context.get("sof") or "Information not provided"
    exp_vol = context.get("exp_vol") or "Information not provided"
    risk_display = context.get("risk_display") or {}
    aggregated_risk = context.get("aggregated_risk") or metadata.get("aggregated_risk") or metadata.get("risk_rating") or "NOT_RATED"
    display_risk_level = risk_display.get("level") if risk_display.get("available") else "NOT_RATED"
    risk_score = risk_display.get("score")
    if risk_score is None:
        risk_score = metadata.get("display_risk_score", metadata.get("risk_score", "not recorded"))
    model_confidence = context.get("model_confidence")
    if model_confidence is None:
        confidence = metadata.get("confidence_level")
        model_confidence = int(round(float(confidence or 0) * 100)) if confidence else "not recorded"

    screening_summary = metadata.get("screening_state_summary") or {}
    screening_blocks = bool(context.get("screening_truth_blocks_approval"))
    screening_terminal = bool(context.get("screening_terminal"))
    screening_defensible_clear = bool(context.get("screening_is_defensible_clear"))
    screening_formally_cleared_match = bool(context.get("screening_formally_cleared_match"))
    screening_completion = context.get("screening_completion_phrase") or "Screening status not recorded"
    screening_state = screening_summary.get("canonical_state") or "unknown"
    screening_mode = screening_summary.get("provider_mode") or "unknown"
    screening_blocker_reasons = (
        screening_summary.get("approval_blocked_reasons")
        or screening_summary.get("blocking_reasons")
        or []
    )

    documents = context.get("documents") or []
    verified_docs = context.get("verified_docs") or []
    pending_docs = context.get("pending_docs") or []
    has_documents = bool(context.get("has_documents"))
    doc_confidence = context.get("doc_confidence", 0)
    documentation_complete = bool(context.get("documentation_complete"))
    pending_doc_labels = [
        d.get("doc_type", "document") if isinstance(d, dict) else "document"
        for d in pending_docs
    ]
    all_peps = context.get("all_peps") or []
    directors = context.get("directors") or []
    ubos = context.get("ubos") or []
    director_names = _memo_unique_text(
        [d.get("full_name") for d in directors if isinstance(d, dict)],
        max_items=4,
    )
    ubo_names = _memo_unique_text(
        [u.get("full_name") for u in ubos if isinstance(u, dict)],
        max_items=4,
    )
    pep_detail_lines = _memo_unique_text([_memo_pep_detail(p) for p in all_peps], max_items=4)
    control_name = context.get("control_name") or "not determined"
    control_pct = context.get("control_pct") or "N/A"
    primary_ubo = context.get("primary_ubo")
    own_rating = context.get("own_rating") or "NOT_RATED"
    own_rating_justification = context.get("own_rating_justification") or "not recorded"
    struct_complexity = context.get("struct_complexity") or "Unknown"
    mon_tier = context.get("mon_tier") or "Standard"
    adverse_media_context = context.get("adverse_media_context") or {}
    enhanced_review_summary = context.get("enhanced_review_summary") or {}
    quality_caps = metadata.get("quality_caps") or []
    screening_review_evidence = _memo_clean_officer_note(
        context.get("screening_review_evidence") or ""
    )

    recommendation = str(metadata.get("approval_recommendation") or "REVIEW").strip().upper()
    approval_like = {"APPROVE", "APPROVE_WITH_CONDITIONS"}
    approval_blockers = []
    if screening_blocks:
        approval_blockers.append("Screening is not terminal / not approval-ready")
        approval_blockers.extend(screening_blocker_reasons)
    if not has_documents:
        approval_blockers.append("No supporting documents have been uploaded")
    elif pending_docs:
        approval_blockers.append(
            str(len(pending_docs)) + " document(s) outstanding: " + ", ".join(pending_doc_labels[:5])
        )
    mandatory_edd = enhanced_review_summary.get("blocking_outstanding_count") or enhanced_review_summary.get("mandatory_outstanding_count")
    if mandatory_edd:
        approval_blockers.append(str(mandatory_edd) + " enhanced review requirement(s) remain outstanding")
    for cap in quality_caps:
        if (
            isinstance(cap, dict)
            and cap.get("severity") == "critical"
            and cap.get("code") == "critical_profile_data_missing"
        ):
            approval_blockers.append(_memo_quality_cap_label(cap))
    approval_blockers = _memo_unique_text(approval_blockers, max_items=6)

    if screening_blocks and recommendation in approval_like:
        metadata.setdefault("approval_recommendation_original", recommendation)
        recommendation = "REVIEW"
        metadata["approval_recommendation"] = "REVIEW"
        metadata["decision_label"] = "SCREENING RESOLUTION REQUIRED"

    label = str(metadata.get("decision_label") or recommendation.replace("_", " ")).strip().upper()
    if screening_blocks and recommendation == "REVIEW":
        label = "SCREENING RESOLUTION REQUIRED"
    elif recommendation == "ESCALATE_TO_EDD":
        label = "ESCALATE TO EDD"
    elif approval_blockers and recommendation in approval_like:
        metadata.setdefault("approval_recommendation_original", recommendation)
        recommendation = "REVIEW"
        metadata["approval_recommendation"] = "REVIEW"
        label = "ACTION REQUIRED"
    authoritative_recommendation = recommendation + (" - " + label if label and label != recommendation.replace("_", " ") else "")

    approval_ready = (
        recommendation in approval_like
        and not approval_blockers
        and not screening_blocks
    )
    readiness = "READY" if approval_ready else "NOT READY"
    next_action = "Submit approval with recorded approval reason" if approval_ready else (
        "Complete live terminal screening and regenerate/revalidate this memo"
        if screening_blocks else
        "Resolve listed blocking items and regenerate/revalidate this memo"
    )

    screening_status = (
        "Complete - defensible clear"
        if screening_defensible_clear else
        "Complete - officer-cleared match"
        if screening_formally_cleared_match else
        "Terminal provider result with unresolved review"
        if screening_terminal else
        "Not terminal / approval blocked"
    )
    provider_reliance = (
        "No unresolved screening escalation remains"
        if screening_defensible_clear or screening_formally_cleared_match else
        "No clean/no-match reliance is available until screening reaches a terminal, defensible state"
    )
    if screening_defensible_clear:
        screening_marker = "Low-risk profile supported by clean sanctions screening."
    elif screening_formally_cleared_match:
        screening_marker = "Screening match(es) formally cleared as false positive; no unresolved screening escalation remains; not a no-match result."
    elif screening_terminal:
        screening_marker = "Screening returned match(es) requiring officer review and escalation."
    else:
        screening_marker = screening_completion + ". Not recommended for approval while screening remains unresolved."

    if not risk_display.get("available"):
        risk_statement = "No canonical risk rating or score is recorded; memo is not yet risk-rated"
    else:
        risk_statement = (
            f"{display_risk_level} risk"
            + (f" with score {risk_score}/100" if risk_score not in (None, "not recorded") else "")
        )
    routing_risk_statement = (
        ""
        if aggregated_risk in (None, "", "NOT_RATED", display_risk_level)
        else f" Routing diagnostics indicate {aggregated_risk} handling for escalation/EDD controls; this is separate from the canonical risk rating."
    )
    if recommendation == "ESCALATE_TO_EDD":
        recommendation_phrase = "Recommendation: ESCALATE TO EDD"
    elif recommendation == "REJECT":
        recommendation_phrase = "Recommendation: REJECT"
    else:
        recommendation_phrase = "Recommendation: " + authoritative_recommendation

    risk_increasing = [
        f"Documentation gap: {len(pending_docs)} outstanding item(s)" if pending_docs else None,
        "No supporting documents uploaded" if not has_documents else None,
        "Screening dependency remains unresolved" if screening_blocks else None,
        f"PEP exposure: {len(all_peps)} declared/detected party(ies)" if all_peps else None,
        f"Ownership risk: {own_rating} - {own_rating_justification}" if own_rating in ("MEDIUM", "HIGH", "VERY_HIGH") else None,
        "Limited trading history reduces forward-looking confidence",
    ]
    risk_decreasing = [
        "No declared/detected PEP exposure among directors or UBOs" if not all_peps else None,
        f"Low jurisdictional risk - {country} maintains adequate AML/CFT frameworks" if context.get("jur_rating") == "LOW" else None,
        f"Low sector risk - {sector} does not exhibit elevated ML/TF typology indicators" if context.get("biz_rating") == "LOW" else None,
        (
            "Screening complete with defensible clear result"
            if screening_defensible_clear else
            "Screening match(es) formally cleared with officer evidence"
            if screening_formally_cleared_match else
            None
        ),
        f"Beneficial ownership traced to natural person level - {control_name} ({control_pct}%)" if primary_ubo and control_pct not in ("N/A", None, "", "0") else None,
        f"Full documentation verified at {doc_confidence}% confidence" if documentation_complete and doc_confidence >= 80 else None,
    ]
    risk_increasing = _memo_unique_text([x for x in risk_increasing if x], max_items=5)
    risk_decreasing = _memo_unique_text([x for x in risk_decreasing if x], max_items=5)

    red_flags = _memo_unique_text([
        "Screening is not approval-ready; " + provider_reliance if screening_blocks else None,
        f"Documentation gap: {len(pending_docs)} of {len(documents)} document(s) remain outstanding" if pending_docs else None,
        "No uploaded documents are available for corporate or identity verification" if not has_documents else None,
        "Limited trading history; no historical transaction data is available for benchmark testing",
        f"PEP exposure requires enhanced scrutiny for {len(all_peps)} associated party(ies)" if all_peps else None,
    ], max_items=5)
    if not red_flags:
        red_flags = ["Residual onboarding risk remains subject to standard monitoring."]
    mitigants = _memo_unique_text([
        f"{len(verified_docs)} of {len(documents)} document(s) verified at {doc_confidence}% confidence" if verified_docs else None,
        f"Beneficial ownership traced to {control_name} ({control_pct}%)" if primary_ubo and control_pct not in ("N/A", None, "", "0") else None,
        "Screening complete with defensible clear result" if screening_defensible_clear else None,
        "Screening match(es) formally cleared by officer evidence" if screening_formally_cleared_match else None,
        "Transaction monitoring will be applied after onboarding decision",
    ], max_items=5)

    sections["executive_summary"] = {
        "title": "Decision Summary",
        "content": (
            f"{company} ({app_ref}) is assessed as {risk_statement}. "
            + recommendation_phrase + ". "
            + "Screening position: " + screening_marker + " "
            + f"Approval readiness: {readiness}. "
            + "Primary blockers: " + _memo_format_items(approval_blockers) + ". "
            + f"Required next action: {next_action}. "
            + f"Model confidence: {model_confidence}%."
        ),
        "decision_summary": {
            "recommendation": recommendation,
            "recommendation_label": label,
            "approval_readiness": readiness,
            "primary_blockers": approval_blockers,
            "required_next_action": next_action,
        },
    }
    sections["client_overview"] = {
        "title": "Case Facts",
        "content": (
            f"Entity: {company}. BRN: {brn}. Jurisdiction: {country}. "
            f"Entity type: {entity_type}. Sector: {sector}. Business activity: {business_activity}. "
            f"Source of funds: {sof}. Expected volume: {exp_vol}."
        ),
    }
    sections["ownership_and_control"] = {
        "title": "Ownership & Control",
        "structure_complexity": struct_complexity,
        "control_statement": (
            f"{control_name} exercises effective control at {control_pct}%."
            if primary_ubo and control_pct not in ("N/A", None, "", "0")
            else "Effective control cannot be fully determined from current ownership data."
        ),
        "content": (
            f"The entity has {len(directors)} director(s) and {len(ubos)} UBO(s). "
            + ("Directors: " + ", ".join(director_names) + ". " if director_names else "")
            + ("UBOs: " + ", ".join(ubo_names) + ". " if ubo_names else "")
            + (" ".join(pep_detail_lines) + ". " if pep_detail_lines else "")
            + f"Ownership risk rating: {own_rating} based on {own_rating_justification}. "
            f"Structure complexity: {struct_complexity}. "
            + (
                f"Control owner: {control_name} ({control_pct}%)."
                if primary_ubo and control_pct not in ("N/A", None, "", "0")
                else "Ownership/control data gaps remain and prevent full assurance."
            )
        ),
    }
    sections["risk_assessment"] = {
        "title": "Key Risk Position",
        "content": (
            f"Canonical risk rating: {display_risk_level}; recorded score: {risk_score}. "
            + routing_risk_statement
            + " "
            "Key risk-increasing factors: " + _memo_format_items(risk_increasing) + ". "
            "Key risk-reducing factors: " + _memo_format_items(risk_decreasing) + "."
        ),
        "sub_sections": {
            "jurisdiction_risk": {
                "rating": context.get("jur_rating") or "MEDIUM",
                "content": f"{country}: rating based on jurisdiction and operating-country evidence.",
            },
            "business_risk": {
                "rating": context.get("biz_rating") or "MEDIUM",
                "content": f"{sector}: rating based on sector typology and stated business activity.",
            },
            "transaction_risk": {
                "rating": context.get("tx_rating") or "MEDIUM",
                "content": f"Transaction risk rating based on expected volume: {exp_vol}.",
            },
            "ownership_risk": {
                "rating": own_rating,
                "content": f"Ownership risk rating based on {own_rating_justification}.",
            },
            "financial_crime_risk": {
                "rating": context.get("fc_rating") or "MEDIUM",
                "content": (
                    "Deterministic financial-crime rating: "
                    + str(context.get("fc_rating") or "MEDIUM")
                    + ". "
                    + str(adverse_media_context.get("phrase") or "")
                    + " Screening state: "
                    + screening_status
                    + "."
                ),
            },
        },
    }
    sections["screening_results"] = {
        "title": "Screening & Verification Status",
        "content": (
            f"Screening status: {screening_status}. Canonical state: {screening_state}; provider mode: {screening_mode}. "
            + (
                "Approval is blocked by screening until live terminal screening is completed, defensible clearance exists, and this memo is regenerated/revalidated. "
                if screening_blocks else
                "Screening does not currently block approval readiness. "
            )
            + f"{provider_reliance}. "
            + (
                "Screening match(es) were formally cleared as false positive. This is not a clear no-match result. "
                if screening_formally_cleared_match else
                ""
            )
            + (
                "no clean adverse-media conclusion is recorded. "
                if not adverse_media_context.get("terminal") else
                ""
            )
            + (screening_review_evidence + " " if screening_review_evidence else "")
            + f"Adverse media: {adverse_media_context.get('checklist') or 'not recorded'}."
        ),
        "screening_terminal": screening_terminal,
        "defensible_clear": bool(screening_defensible_clear or screening_formally_cleared_match),
        "approval_gate_ready": bool(screening_summary.get("approval_gate_ready")),
        "approval_blocking": screening_blocks,
        "approval_blocked_reasons": approval_blockers,
    }
    sections["document_verification"] = {
        "title": "Document Verification",
        "content": (
            f"{len(documents)} document(s) submitted; {len(verified_docs)} verified; "
            + (
                f"{len(pending_docs)} outstanding. "
                if pending_docs else
                "no open document conditions. "
            )
            + (
                "Professional judgement: no documents have been uploaded; entity verification cannot be relied upon. "
                if not has_documents else
                "Professional judgement: outstanding documents prevent complete assurance and must be resolved. "
                if pending_docs else
                "Professional judgement: document set is complete and internally consistent. "
            )
            + f"Overall documentation adequacy: {doc_confidence}%. "
            + (
                "Pending items: " + ", ".join(pending_doc_labels[:6]) + "."
                if pending_doc_labels else
                "Document file is complete."
            )
        ),
    }
    sections["enhanced_review_edd"] = _build_enhanced_review_memo_section(enhanced_review_summary)
    sections["ai_explainability"] = {
        "title": "AI / Rule Explainability",
        "content": (
            "Rule/model source: " + BRAND["platform_name"] + " Composite Risk Engine with deterministic memo controls. "
            + (
                "Overall risk score: Not yet scored. "
                if not risk_display.get("available") else
                "Overall risk score: " + str(risk_score) + "/100. "
            )
            + f"Confidence: {model_confidence}%. "
            + "Default memo shows only the material factors and limitations; detailed rule evidence is retained in the appendix. "
            + "Limitations: screening or document gaps must be resolved before approval reliance where listed as blockers."
        ),
        "risk_increasing_factors": risk_increasing,
        "risk_decreasing_factors": risk_decreasing,
        "factor_enforcement_applied": True,
    }
    sections["red_flags_and_mitigants"] = {
        "title": "Blocking Items / Conditions",
        "red_flags": red_flags,
        "mitigants": mitigants,
        "approval_blockers": approval_blockers,
        "conditions": _memo_unique_text(metadata.get("conditions") or [], max_items=6),
    }
    sections["compliance_decision"] = {
        "title": "Officer Recommendation",
        "decision": recommendation,
        "decision_label": label,
        "content": (
            f"Recommendation: {authoritative_recommendation}. "
            + (
                "Enhanced Due Diligence before any approval is required. "
                if recommendation == "ESCALATE_TO_EDD" else
                "This application is not recommended for approval at this stage. "
                if not approval_ready else
                ""
            )
            + (
                "Conditional approval is not available while approval-blocking items remain. "
                if not approval_ready else
                "Approval may proceed only with the required approval reason and retained audit evidence. "
            )
            + "Decision rationale: " + (
                "screening resolution is the controlling next step."
                if screening_blocks else
                "listed blocker resolution is required before a final approval recommendation."
                if approval_blockers else
                "all current memo blockers are clear."
            )
        ),
    }
    sections["ongoing_monitoring"] = {
        "title": "Required Next Action",
        "content": (
            f"Next action: {next_action}. Monitoring tier after onboarding decision: {mon_tier}. "
            "Monitoring dependencies: ownership changes, adverse-media alerts, PEP status changes, transaction drift, and document non-compliance."
        ),
    }
    sections["audit_and_governance"] = {
        "title": "Audit & Governance",
        "content": (
            "Source attribution, rule-engine evidence, validation result, supervisor result, quality caps, and generation timestamp are retained. "
            f"Generated: {context.get('now_ts') or memo.get('memo_generated') or 'not recorded'}. "
            "Full pre-cleanup evidence sections are retained in appendix_sections for audit/export review."
        ),
    }

    memo["appendix_sections"] = original_sections
    metadata["memo_output_profile"] = {
        "profile_version": MEMO_OUTPUT_PROFILE_VERSION,
        "default_view": "concise_decision_paper",
        "original_sections_preserved_as": "appendix_sections",
        "original_sections_word_count": original_word_count,
        "default_sections_word_count": _memo_word_count(sections),
        "authoritative_recommendation": recommendation,
        "recommendation_label": label,
        "approval_readiness": readiness,
        "primary_blockers": approval_blockers,
        "required_next_action": next_action,
        "screening_pending_is_blocker_not_mitigant": bool(screening_blocks),
    }
    metadata["approval_recommendation"] = recommendation
    metadata["decision_label"] = label
    metadata["approval_readiness"] = readiness
    metadata["primary_blockers"] = approval_blockers
    metadata["required_next_action"] = next_action
    checklist = list(metadata.get("review_checklist") or [])
    if checklist:
        checklist[-1] = (
            "Compliance decision aligned to " + authoritative_recommendation
            + "; next action: " + next_action
        )
        metadata["review_checklist"] = checklist
    return memo


def _enhanced_review_empty_summary():
    return {
        "triggered": False,
        "total_requirements": 0,
        "by_trigger": [],
        "requested": [],
        "submitted": [],
        "accepted": [],
        "rejected": [],
        "waived": [],
        "outstanding": [],
        "mandatory_outstanding_count": 0,
        "blocking_outstanding_count": 0,
        "client_facing_count": 0,
        "backoffice_only_count": 0,
        "document_submissions_count": 0,
        "text_responses_count": 0,
        "waiver_count": 0,
        "senior_review_items": [],
        "overall_status": "not_triggered",
        "warnings": [],
    }


def _enhanced_review_summary(app):
    summary = app.get("enhanced_review_summary")
    if not isinstance(summary, dict):
        return _enhanced_review_empty_summary()
    normalized = _enhanced_review_empty_summary()
    normalized.update(summary)
    return normalized


def _enhanced_review_item_line(item):
    label = str(item.get("requirement_label") or item.get("requirement_key") or "Requirement")
    status = str(item.get("memo_status") or item.get("status") or "Status not recorded")
    parts = [
        label,
        status,
        "audience: " + str(item.get("audience") or "not recorded"),
        "type: " + str(item.get("requirement_type") or "not recorded"),
    ]
    if item.get("mandatory"):
        parts.append("mandatory")
    if item.get("blocking_approval"):
        parts.append("blocking flag recorded")
    if item.get("linked_document_present"):
        parts.append("linked document present")
    if item.get("client_response_submitted"):
        parts.append("client response submitted")
    if item.get("waiver_reason"):
        parts.append("waiver reason: " + str(item.get("waiver_reason")))
    return " — ".join(parts)


def _enhanced_review_list(label, items, limit=8):
    if not items:
        return label + ": none."
    lines = [_enhanced_review_item_line(item) for item in items[:limit]]
    suffix = ""
    if len(items) > limit:
        suffix = " +" + str(len(items) - limit) + " more"
    return label + ": " + "; ".join(lines) + suffix + "."


def _build_enhanced_review_memo_section(summary):
    summary = summary if isinstance(summary, dict) else _enhanced_review_empty_summary()
    if not summary.get("triggered"):
        content = (
            "Onboarding Enhanced Review: Not triggered based on the current application "
            "data and available routing information."
        )
        return {
            "title": "Onboarding Enhanced Review",
            "triggered": False,
            "overall_status": "not_triggered",
            "content": content,
            "summary": summary,
        }

    by_trigger = summary.get("by_trigger") or []
    trigger_lines = []
    for group in by_trigger:
        if not isinstance(group, dict):
            continue
        label = group.get("trigger_label") or group.get("trigger_key") or "Unknown trigger"
        statuses = group.get("statuses") or {}
        status_text = ", ".join(
            str(k).replace("_", " ") + "=" + str(v)
            for k, v in sorted(statuses.items())
        ) or "no status breakdown"
        reason_text = ""
        reasons = [str(r) for r in (group.get("trigger_reasons") or []) if r]
        if reasons:
            reason_text = " Reason(s): " + ", ".join(reasons[:3]) + "."
        trigger_lines.append(
            str(label) + " (" + str(group.get("total") or 0) + " requirement(s); "
            + status_text + ")." + reason_text
        )

    outstanding_count = len(summary.get("outstanding") or [])
    mandatory_outstanding = int(summary.get("mandatory_outstanding_count") or 0)
    blocking_outstanding = int(summary.get("blocking_outstanding_count") or 0)
    if mandatory_outstanding or blocking_outstanding:
        residual = (
            "Enhanced Review remains incomplete pending resolution of outstanding "
            "mandatory or blocking items."
        )
    elif outstanding_count:
        residual = (
            "No mandatory or blocking enhanced review items remain unresolved; "
            "non-mandatory enhanced review items remain open."
        )
    else:
        residual = (
            "Enhanced Review requirements have been resolved based on accepted or "
            "waived items."
        )

    content_parts = [
        "Triggered: Yes.",
        "Requirement count: "
        + str(summary.get("total_requirements") or 0)
        + " total; "
        + str(summary.get("client_facing_count") or 0)
        + " client-facing; "
        + str(summary.get("backoffice_only_count") or 0)
        + " back-office/internal; "
        + str(outstanding_count)
        + " outstanding.",
        "Trigger groups: " + (" ".join(trigger_lines) if trigger_lines else "none recorded."),
        _enhanced_review_list("Requested from client", summary.get("requested") or []),
        _enhanced_review_list("Submitted by client / under review", summary.get("submitted") or []),
        _enhanced_review_list("Accepted", summary.get("accepted") or []),
        _enhanced_review_list("Rejected / further information required", summary.get("rejected") or []),
        _enhanced_review_list("Waived", summary.get("waived") or []),
        _enhanced_review_list("Outstanding", summary.get("outstanding") or []),
    ]
    senior_items = summary.get("senior_review_items") or []
    if senior_items:
        content_parts.append(_enhanced_review_list("Senior review tasks", senior_items))
    content_parts.append(
        "Mandatory outstanding: "
        + str(mandatory_outstanding)
        + "; blocking outstanding: "
        + str(blocking_outstanding)
        + ". "
        + residual
    )
    warnings = summary.get("warnings") or []
    if warnings:
        content_parts.append("Warnings: " + "; ".join(str(w) for w in warnings[:5]) + ".")

    return {
        "title": "Onboarding Enhanced Review",
        "triggered": True,
        "overall_status": summary.get("overall_status") or "incomplete",
        "mandatory_outstanding_count": mandatory_outstanding,
        "blocking_outstanding_count": blocking_outstanding,
        "content": " ".join(content_parts),
        "summary": summary,
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

    def _pep_declaration(person):
        raw = person.get("pep_declaration") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        return raw if isinstance(raw, dict) else {}

    def _pep_detail_phrase(person):
        declaration = _pep_declaration(person)

        def pick(*keys):
            for key in keys:
                value = declaration.get(key)
                if value not in (None, "", []):
                    return str(value)
            return ""

        parts = []
        role_type = pick("pep_role_type", "role_type", "pep_type")
        position = pick("position_title", "public_function", "public_position")
        jurisdiction = pick("pep_country_jurisdiction", "country_jurisdiction", "jurisdiction")
        relationship = pick("relationship_type")
        related_name = pick("related_pep_name")
        start_date = pick("start_date")
        end_date = "current" if declaration.get("current_status") else pick("end_date")
        sow = pick("source_of_wealth_detail", "source_of_wealth_note")
        sof = pick("source_of_funds_detail", "source_of_funds_note")
        evidence = pick("supporting_note_evidence", "evidence_reference", "supporting_evidence_reference")
        notes = pick("notes")
        if role_type:
            parts.append(f"role/type: {role_type.replace('_', ' ')}")
        if position:
            parts.append(f"position/title: {position}")
        if jurisdiction:
            parts.append(f"jurisdiction: {jurisdiction}")
        if relationship and relationship != "self":
            rel = relationship.replace("_", " ")
            if related_name:
                rel += f" to {related_name}"
            parts.append(f"relationship: {rel}")
        if start_date or end_date:
            parts.append(f"role period: {start_date or 'not provided'} to {end_date or 'not provided'}")
        if sow:
            parts.append(f"source of wealth: {sow}")
        if sof:
            parts.append(f"source of funds: {sof}")
        if evidence:
            parts.append(f"evidence/reference: {evidence}")
        if notes:
            parts.append(f"notes: {notes}")
        return "; ".join(parts)

    def _pep_sentence(person, role_label):
        details = _pep_detail_phrase(person)
        base = f"{role_label}: {person.get('full_name', 'Unknown party')} identified as a client-declared PEP"
        return base + (f" ({details})" if details else "")

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
            build_screening_terminality_summary as _build_screening_terminality_summary,
        )
    except ImportError:  # pragma: no cover — defensive: never break memo build
        def _build_screening_terminality_summary(_report, _prescreening=None, _reviews=None):
            return {
                "terminal": False,
                "has_non_terminal": True,
                "has_failed": False,
                "has_not_configured": False,
                "has_sandbox": False,
                "has_simulated": False,
                "provider_mode": None,
                "provider_availability": None,
                "canonical_state": None,
                "screening_result": None,
                "defensible_clear": False,
                "approval_blocking": True,
                "blocking_reasons": [],
                "has_terminal_match": False,
                "company_screening_configured": False,
                "person_states": [],
                "company_state": None,
            }

    _screening_terminality = _build_screening_terminality_summary(
        screening_report,
        prescreening_data,
        app.get("screening_reviews") or [],
    )
    _person_states = list(_screening_terminality.get("person_states") or [])
    _company_state = _screening_terminality.get("company_state")

    # ``screening_terminal`` is True only if every screened subject has a
    # terminal provider answer. Otherwise the memo must qualify its
    # screening claims. ``has_terminal_match`` is stricter: it means a
    # material terminal concern (PEP/sanctions/adverse/company hit), not
    # merely provider metadata or a non-material profile.
    screening_terminal = bool(_screening_terminality.get("terminal"))
    screening_has_non_terminal = bool(_screening_terminality.get("has_non_terminal"))
    screening_has_failed = bool(_screening_terminality.get("has_failed"))
    screening_has_not_configured = bool(_screening_terminality.get("has_not_configured"))
    screening_canonical_state = str(_screening_terminality.get("canonical_state") or "").strip()
    screening_provider_mode = str(_screening_terminality.get("provider_mode") or "").strip()
    screening_provider_availability = str(_screening_terminality.get("provider_availability") or "").strip()
    screening_result = str(_screening_terminality.get("screening_result") or "").strip()
    screening_has_sandbox = bool(
        _screening_terminality.get("has_sandbox") or screening_provider_mode == "sandbox_provider"
    )
    screening_has_simulated = bool(
        _screening_terminality.get("has_simulated") or screening_provider_mode == "simulated_fallback"
    )
    screening_has_terminal_match = bool(
        _screening_terminality.get("has_terminal_match")
        or screening_canonical_state == "completed_match"
        or screening_result == "match"
    )
    screening_has_formally_cleared_match = bool(_screening_terminality.get("has_formally_cleared_match"))
    screening_has_uncleared_completed_match = bool(_screening_terminality.get("has_uncleared_completed_match"))
    screening_formally_cleared_match = bool(
        screening_terminal
        and screening_canonical_state == "completed_match"
        and screening_has_formally_cleared_match
        and not screening_has_uncleared_completed_match
    )
    screening_defensible_clear = bool(_screening_terminality.get("defensible_clear"))
    screening_is_defensible_clear = (
        screening_terminal
        and screening_defensible_clear
        and screening_canonical_state == "completed_clear"
        and not screening_has_terminal_match
    )
    screening_is_terminal_match = (
        screening_terminal
        and screening_has_terminal_match
        and not screening_formally_cleared_match
    )
    screening_approval_ready = bool(_screening_terminality.get("approval_ready"))
    screening_truth_blocks_approval = bool(
        _screening_terminality.get("approval_blocking")
        or not screening_approval_ready
    )
    # Coverage of self-declared PEP exposure is independent of provider
    # state and remains a first-class signal.
    has_declared_pep = bool(pep_directors or pep_ubos)
    # Phrasing helpers used throughout the memo body to avoid asserting
    # "clean screening" when the underlying state does not support it.
    _screening_clean_phrase = "clean sanctions screening across all major consolidated lists"
    _screening_clear_phrase = (
        "Sanctions screening completed across all major consolidated lists (UN, EU, OFAC, HMT) with no matches"
    )
    if screening_is_defensible_clear:
        _screening_qualifier = ""  # safe to make assertive claims
        _screening_completion_phrase = "Sanctions screening completed across all major consolidated lists (UN, EU, OFAC, HMT)"
        _screening_fincrime_phrase = (
            "Sanctions screening was completed across UN Security Council, EU, OFAC SDN, "
            "and HMT consolidated lists. No matches were returned for any director, UBO, or the entity itself."
        )
        _screening_results_phrase = (
            "Sanctions Screening: Conducted against UN Security Council Consolidated List, "
            "EU Consolidated Financial Sanctions List, OFAC SDN List, and HMT Consolidated List. "
            "No matches returned for any associated individual or the entity itself."
        )
        _screening_mitigation_phrase = "Clean sanctions screening across all major consolidated lists (UN, EU, OFAC, HMT)"
        _screening_ai_factor_phrase = "Clean sanctions screening across all major consolidated lists (UN, EU, OFAC, HMT)"
        _screening_content_factor_phrase = "Clean sanctions screening across all major consolidated lists."
        _screening_key_finding = "Sanctions screening clear"
        _screening_review_check = "Sanctions screening completed — no matches across UN, EU, OFAC, HMT lists"
        _screening_decision_descriptor = "clean"
    elif screening_formally_cleared_match:
        _screening_qualifier = (
            " Live provider screening returned match(es) that have been formally cleared "
            "as false positives through officer disposition evidence; no unresolved screening escalation remains. "
            "This is not a no-match result."
        )
        _screening_completion_phrase = (
            "Sanctions / PEP / watchlist screening completed with match(es) formally cleared as false positives by officer disposition"
        )
        _screening_fincrime_phrase = (
            "Sanctions / PEP / watchlist screening returned live terminal match(es) that were formally cleared as false positives "
            "with documented officer disposition evidence. This is a cleared-match result, not a no-match result."
        )
        _screening_results_phrase = (
            "Sanctions Screening: live provider screening returned match(es) that were formally cleared as false positives "
            "by officer disposition evidence. This is not a clear no-match result."
        )
        _screening_mitigation_phrase = (
            "Live screening match(es) formally cleared as false positives through documented officer disposition evidence"
        )
        _screening_ai_factor_phrase = (
            "Screening completed with provider match(es) formally cleared as false positives; no unresolved screening escalation remains"
        )
        _screening_content_factor_phrase = (
            "Screening completed with provider match(es) formally cleared as false positives."
        )
        _screening_key_finding = "Sanctions / PEP / watchlist screening match(es) formally cleared as false positives"
        _screening_review_check = "Sanctions screening match(es) formally cleared as false positives through officer disposition evidence"
        _screening_decision_descriptor = "formally cleared match"
    elif screening_is_terminal_match:
        _screening_qualifier = (
            " Terminal provider screening returned match(es); clean or no-match screening cannot be asserted. "
            "Officer disposition, escalation, and enhanced review are required before approval reliance."
        )
        _screening_completion_phrase = (
            "Sanctions / PEP / watchlist screening completed with provider match(es) requiring officer review and escalation"
        )
        _screening_fincrime_phrase = (
            "Sanctions / PEP / watchlist screening returned live terminal match(es) requiring officer review, "
            "false-positive disposition, and escalation where material. Clean screening cannot be asserted."
        )
        _screening_results_phrase = (
            "Sanctions Screening: live provider screening returned match(es) requiring officer review and escalation. "
            "This is not a clear or no-match result."
        )
        _screening_mitigation_phrase = (
            "Live screening returned match(es); no clean-screening mitigant is available until disposition and escalation are completed"
        )
        _screening_ai_factor_phrase = (
            "Screening completed with provider match(es) requiring disposition; this is not clean screening"
        )
        _screening_content_factor_phrase = (
            "Screening completed with match/escalation requirements across provider watchlists."
        )
        _screening_key_finding = "Sanctions / PEP / watchlist screening completed with match(es) requiring escalation"
        _screening_review_check = "Sanctions screening completed with match(es) — officer disposition/escalation required"
        _screening_decision_descriptor = "matched/escalation"
    else:
        _bits = []
        if screening_has_not_configured:
            _bits.append("provider not configured for at least one subject")
        if screening_has_failed:
            _bits.append("provider unavailable for at least one subject")
        if screening_has_sandbox:
            _bits.append("sandbox provider result for at least one subject")
        if screening_has_simulated:
            _bits.append("simulated fallback result for at least one subject")
        if not _bits:
            _bits.append("provider has not yet returned a terminal result for at least one subject")
        _qual = "; ".join(_bits)
        if screening_provider_mode == "simulated_fallback" or screening_canonical_state == "simulated_fallback":
            _state_label = "SIMULATED FALLBACK"
            _state_sentence = "screening used simulated fallback data and is not production-live"
            _screening_decision_descriptor = "simulated / non-reliance-grade"
        elif screening_provider_mode == "sandbox_provider" or screening_canonical_state == "sandbox_provider":
            _state_label = "SANDBOX PROVIDER"
            _state_sentence = "screening used sandbox provider data and is not production-live"
            _screening_decision_descriptor = "sandbox / not production-live"
        elif screening_has_not_configured or screening_canonical_state == "not_configured":
            _state_label = "NOT CONFIGURED"
            _state_sentence = "required screening provider coverage is not configured"
            _screening_decision_descriptor = "not configured"
        elif screening_has_failed or screening_canonical_state == "failed":
            _state_label = "FAILED / UNAVAILABLE"
            _state_sentence = "required screening provider coverage failed or was unavailable"
            _screening_decision_descriptor = "failed / unavailable"
        else:
            _state_label = "PENDING"
            _state_sentence = "required screening has not yet returned a terminal provider result"
            _screening_decision_descriptor = "pending"
        _screening_qualifier = (
            " Screening is " + _state_label + ": " + _qual + ". "
            "No reliance can be placed on the absence of matches at this time."
        )
        _screening_completion_phrase = (
            "Sanctions screening status: " + _state_label + " — " + _state_sentence
        )
        _screening_fincrime_phrase = (
            _screening_completion_phrase + ". No clean or no-match conclusion can be drawn until live terminal screening is completed."
        )
        _screening_results_phrase = (
            _screening_completion_phrase
            + ". Provider-backed sanctions / PEP / watchlist screening is not reliance-grade; "
            "no clean or no-match conclusion can be drawn at this time."
        )
        _screening_mitigation_phrase = (
            _screening_completion_phrase + "; no reliance on absence of provider matches"
        )
        _screening_ai_factor_phrase = (
            _screening_completion_phrase + "; no clean-screening mitigant is available"
        )
        _screening_content_factor_phrase = (
            _screening_completion_phrase + "."
        )
        _screening_key_finding = (
            _screening_completion_phrase + " — no reliance on absence of provider matches"
        )
        _screening_review_check = (
            _screening_completion_phrase + " — live terminal screening required before approval reliance"
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
    enhanced_review_summary = _enhanced_review_summary(app)
    enhanced_review_section = _build_enhanced_review_memo_section(enhanced_review_summary)
    _approval_like_decisions = ("APPROVE", "APPROVE_WITH_CONDITIONS")
    _screening_decision_is_approval_like = decision in _approval_like_decisions
    _screening_recommendation_bound_to_review = False
    if screening_truth_blocks_approval and _screening_decision_is_approval_like:
        _screening_recommendation_statement = (
            "Recommendation: SCREENING RESOLUTION REQUIRED (REVIEW) — "
            + _screening_completion_phrase
            + ". This memo is not an approval recommendation; approval reliance is blocked until "
            "live terminal production screening is completed and the memo is regenerated/revalidated."
        )
        _screening_compliance_decision_statement = (
            "this application is not recommended for approval at this stage. "
            + _screening_completion_phrase
            + "; approval is blocked until screening is live, terminal, production-grade, "
            "and reflected in a regenerated memo. "
        )
    elif screening_truth_blocks_approval:
        _screening_recommendation_statement = (
            f"Recommendation: {decision_label}. "
            + _screening_completion_phrase
            + "; screening gate is blocked and cannot support any future approval reliance."
        )
        _screening_compliance_decision_statement = (
            f"this application remains subject to {decision_label}. "
            + _screening_completion_phrase
            + "; screening gate is blocked and cannot support approval reliance. "
        )
    else:
        _screening_recommendation_statement = (
            f"Recommendation: {decision_label}"
            + (f" — subject to {'PEP declaration and enhanced monitoring' if all_peps else 'standard conditions including enhanced monitoring due to reduced confidence' if low_confidence else 'standard conditions'}." if aggregated_risk in ("MEDIUM", "HIGH") else ".")
        )
        _screening_compliance_decision_statement = (
            f"this application is recommended for {decision_label}. "
        )

    if risk_display["available"]:
        if screening_is_defensible_clear:
            _executive_risk_profile = (
                "a balanced risk profile" if aggregated_risk == "MEDIUM"
                else "a low-risk profile with no material concerns" if aggregated_risk == "LOW"
                else "an elevated risk profile requiring enhanced scrutiny"
            )
        elif screening_formally_cleared_match:
            _executive_risk_profile = (
                "the numeric/base risk factors plus live provider match(es) "
                "formally cleared through documented officer disposition evidence; "
                "no unresolved screening escalation remains"
            )
        elif screening_is_terminal_match:
            _executive_risk_profile = (
                "the numeric/base risk factors only; live provider screening returned "
                "match(es) or a material screening concern requiring officer review, "
                "formal disposition, and escalation before approval reliance"
            )
        elif screening_has_simulated:
            _executive_risk_profile = (
                "the numeric/base risk factors only; screening used simulated fallback "
                "data and is not production-reliance-grade"
            )
        elif screening_has_sandbox:
            _executive_risk_profile = (
                "the numeric/base risk factors only; screening used sandbox provider "
                "data and is not production-live"
            )
        elif screening_has_not_configured:
            _executive_risk_profile = (
                "the numeric/base risk factors only; required screening provider "
                "coverage is not configured"
            )
        elif screening_has_failed:
            _executive_risk_profile = (
                "the numeric/base risk factors only; required screening provider "
                "coverage failed or was unavailable"
            )
        else:
            _executive_risk_profile = (
                "the numeric/base risk factors only; required screening has not yet "
                "returned a terminal provider result"
            )
        _executive_risk_sentence = (
            f"The composite risk score of {risk_score}/100 (aggregated: {aggregated_risk}) "
            f"reflects {_executive_risk_profile}. "
        )
    else:
        _executive_risk_sentence = (
            risk_display["summary"]
            + " Internal factor analysis is used only to route unresolved gaps and is not presented as a final risk rating. "
        )
    _has_principal_risk_driver = (
        bool(all_peps)
        or (own_rating in ("HIGH", "MEDIUM") and bool(own_risk_reasons))
        or is_high_risk_country
        or is_offshore
    )
    _ownership_driver_phrase = (
        "no PEP exposure and a clean ownership structure"
        if screening_is_defensible_clear
        else "no PEP exposure and a transparent ownership structure"
    )
    if _has_principal_risk_driver:
        _executive_offset_phrase = (
            "These risk factors are materially offset by "
            if aggregated_risk in ("LOW", "MEDIUM")
            else "These risk factors are insufficiently offset by "
        )
    elif screening_is_defensible_clear:
        _executive_offset_phrase = "The low-risk profile is supported by "
    else:
        _executive_offset_phrase = "The base risk score is supported by "

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
        rationale = review.get("rationale") or review.get("notes") or ""
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
        line = str(line).strip()
        if line and line[-1] not in ".!?":
            line += "."
        screening_review_lines.append(line[:1000])
    screening_review_evidence = (
        " Officer disposition evidence: " + " ".join(screening_review_lines) + " "
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
                    + _executive_risk_sentence
                    + f"Model confidence: {model_confidence}%"
                    + (f" — reduced due to {'no uploaded documentation and ' if not has_documents else 'outstanding documentation and ' if pending_docs else ''}{'limited historical transaction data' if True else ''}. " if model_confidence < 80 else ". ")
                    + "The principal risk drivers are "
                    + (f"the presence of {len(all_peps)} Politically Exposed Person(s) ({', '.join([p['full_name'] for p in all_peps])})" if all_peps else "")
                    + (f"{',' if all_peps else ''} ownership risk rated {own_rating} ({own_rating_justification})" if own_rating in ("HIGH", "MEDIUM") and own_risk_reasons else "")
                    + (f"{',' if all_peps or own_rating in ('HIGH', 'MEDIUM') else ''} the {'high-risk' if is_high_risk_country else 'offshore'} jurisdictional classification of {country}" if is_high_risk_country or is_offshore else "")
                    + (_ownership_driver_phrase if not all_peps and own_rating == "LOW" and not is_high_risk_country and not is_offshore else "")
                    + ". "
                    + _executive_offset_phrase
                    + ((_screening_clean_phrase + ", ") if (not all_peps and screening_is_defensible_clear) else "")
                    + (f"a fully traceable beneficial ownership chain ({control_name} at {control_pct}%)" if primary_ubo and control_pct not in ("N/A", None, "", "0") else "beneficial ownership assessment")
                    + (f", and {len(verified_docs)} of {len(documents)} documents verified at {doc_confidence}% confidence. " if has_documents else ", and no uploaded documents are currently available to substantiate entity verification. ")
                    + ("No documents have been uploaded, so entity verification remains incomplete and cannot be treated as a mitigant. " if not has_documents else f"{len(pending_docs)} document(s) remain outstanding, representing a documentation gap that must be remedied within 14 business days. " if pending_docs else "")
                    + (f"Note: model confidence of {model_confidence}% is below threshold — this is reflected in the conditional nature of the recommendation. " if low_confidence else "")
                    + _screening_recommendation_statement
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
                        + (f" — PEP declaration recorded: {_pep_detail_phrase(d)}. Enhanced due diligence required under FATF Recommendation 12." if _is_declared_pep(d) else " — PEP declaration not recorded.")
                        for d in directors
                    ]) + " "
                    + " ".join([
                        f"UBO: {u['full_name']} — {u.get('ownership_pct', 'Information not provided')}% direct ownership"
                        + f" ({u.get('nationality', 'nationality not provided')} national, DOB: {u.get('date_of_birth') or 'not provided'})"
                        + (f". PEP declaration recorded: {_pep_detail_phrase(u)}. This UBO exercises both ownership and potential political influence, significantly elevating risk." if _is_declared_pep(u) else ".")
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
                                _screening_fincrime_phrase + " "
                                + (f"{len(all_peps)} PEP match(es) identified requiring enhanced assessment. " if all_peps else "")
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
                        _screening_results_phrase + " "
                        + ("PEP matches identified — assessed as confirmed true positives based on verified identity data. " if all_peps else "")
                    )
                    + f"PEP Screening: {len(all_peps)} self-declared / detected match(es)"
                    + (" — " + ". ".join([_pep_sentence(p, "PEP") + ". PEP declaration form and enhanced due diligence documentation requested." for p in all_peps]) if all_peps else " — no declared or detected matches.")
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
            "enhanced_review_edd": enhanced_review_section,
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
                    (_screening_mitigation_phrase if not all_peps else "Screening completed — PEP(s) identified and flagged for enhanced measures"),
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
                    + (f"{'(3) ' if not is_high_risk_country and not is_offshore else '(2) '}{_screening_content_factor_phrase} " if True else "")
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
                    + ([_screening_clear_phrase + "."] if screening_is_defensible_clear else [_screening_mitigation_phrase + "."])
                    + ([f"Beneficial ownership fully traced to natural person level via {struct_complexity.lower()} structure. {control_name} ({control_pct}%) confirmed as exercising effective control."] if primary_ubo else [])
                    + ["Transaction monitoring will be applied on a quarterly basis for the first 12 months, with automated alerts for anomalous volumes, compensating for the absence of historical benchmarking data."]
                )
            },
            "compliance_decision": {
                "title": "Compliance Decision",
                "decision": decision,
                "content": (
                    f"On the basis of the composite risk assessment ({risk_display['assessment']}), "
                    f"{_screening_decision_descriptor} screening results, "
                    f"{'unavailable' if not has_documents else 'verified' if not pending_docs else 'partially verified'} documentation ({doc_confidence}% confidence), "
                    f"and {'confirmed' if ubos else 'unverified'} beneficial ownership, "
                    + _screening_compliance_decision_statement
                    + (
                        "The risk profile cannot override the screening truth control; screening resolution is a prerequisite to any approval reliance. "
                        if screening_truth_blocks_approval else
                        (f"The {'conditions' if risk_level in ('MEDIUM', 'HIGH') else 'recommendation'} reflect{'s' if risk_level == 'LOW' else ''} the residual risks identified — "
                         f"{'principally the PEP exposure and ' if all_peps else ''}{'absence of uploaded documents' if not has_documents else 'documentation gap' if pending_docs else 'limited trading history'}. " if risk_level != "LOW" else "The low-risk profile supports standard onboarding with no additional conditions. ")
                    )
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
            "risk_rating": risk_display["level"] if risk_display["available"] else "NOT_RATED",
            "risk_score": risk_display["score"] if risk_display["available"] else None,
            "computed_routing_risk": aggregated_risk,
            "computed_routing_score": risk_score,
            "canonical_risk": risk_display,
            "display_risk_rating": risk_display["level"] if risk_display["available"] else "NOT_RATED",
            "display_risk_score": risk_display["score"] if risk_display["available"] else None,
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
                f"{_screening_key_finding}; adverse media {'clear' if adverse_media_context['terminal'] and not adverse_media_context['has_hit'] else ('hit(s) require disposition' if adverse_media_context['has_hit'] else 'NOT complete — clean reliance unavailable')}",
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
                _screening_review_check,
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
                "has_sandbox": screening_has_sandbox,
                "has_simulated": screening_has_simulated,
                "canonical_state": _screening_terminality.get("canonical_state"),
                "provider_availability": _screening_terminality.get("provider_availability"),
                "provider_mode": _screening_terminality.get("provider_mode"),
                "screening_result": _screening_terminality.get("screening_result"),
                "defensible_clear": bool(_screening_terminality.get("defensible_clear")),
                "screening_gate_ready": bool(_screening_terminality.get("screening_gate_ready")),
                "approval_gate_ready": bool(_screening_terminality.get("approval_gate_ready")),
                "approval_ready": bool(_screening_terminality.get("approval_ready")),
                "approval_ready_scope": _screening_terminality.get("approval_ready_scope"),
                "approval_blocking": bool(_screening_terminality.get("approval_blocking")),
                "blocking_reasons": _screening_terminality.get("blocking_reasons") or [],
                "approval_blocked_reasons": _screening_terminality.get("approval_blocked_reasons") or [],
                "has_formally_cleared_match": bool(_screening_terminality.get("has_formally_cleared_match")),
                "has_uncleared_completed_match": bool(_screening_terminality.get("has_uncleared_completed_match")),
                "completed_match_blocking": bool(_screening_terminality.get("completed_match_blocking")),
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
            },
            "enhanced_review_summary": enhanced_review_summary,
            "enhanced_review_status": enhanced_review_summary.get("overall_status", "not_triggered"),
            "enhanced_review_outstanding_count": len(enhanced_review_summary.get("outstanding") or []),
            "enhanced_review_mandatory_outstanding_count": enhanced_review_summary.get("mandatory_outstanding_count", 0),
            "enhanced_review_blocking_outstanding_count": enhanced_review_summary.get("blocking_outstanding_count", 0),
            "enhanced_review_waiver_count": enhanced_review_summary.get("waiver_count", 0),
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

    if screening_truth_blocks_approval:
        _original_screening_decision = memo["metadata"].get("approval_recommendation")
        if _original_screening_decision in ("APPROVE", "APPROVE_WITH_CONDITIONS"):
            rule_enforcements.append({
                "rule": "RECOMMENDATION_BOUND_TO_SCREENING_TRUTH",
                "original_decision": _original_screening_decision,
                "enforced_decision": "REVIEW",
                "reason": (
                    "Screening truth gate is blocked "
                    f"(state={screening_canonical_state or 'unknown'}, "
                    f"provider_mode={screening_provider_mode or 'unknown'}, "
                    f"availability={screening_provider_availability or 'unknown'}). "
                    "Memo recommendation cannot be an approval value until live terminal screening is available."
                ),
            })
            memo["metadata"]["approval_recommendation_original"] = _original_screening_decision
            memo["metadata"]["approval_recommendation"] = "REVIEW"
            memo["metadata"]["decision_label"] = "SCREENING RESOLUTION REQUIRED"
            _screening_recommendation_bound_to_review = True
            _screening_conditions = list(memo["metadata"].get("conditions") or [])
            _screening_conditions.append(
                _screening_completion_phrase
                + "; complete live terminal production screening and regenerate/revalidate this memo before approval reliance."
            )
            memo["metadata"]["conditions"] = _screening_conditions
            _checklist = list(memo["metadata"].get("review_checklist") or [])
            if _checklist:
                _checklist[-1] = (
                    "Compliance decision blocked by screening truth; approval is unavailable until live terminal screening is complete"
                )
            memo["metadata"]["review_checklist"] = _checklist
        rule_engine_result["enforcements"] = rule_enforcements
        rule_engine_result["total_enforcements"] = len(rule_enforcements)
        if rule_enforcements and rule_engine_result.get("engine_status") == "CLEAN":
            rule_engine_result["engine_status"] = "ENFORCED"
        memo["metadata"]["rule_engine"] = rule_engine_result

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
            **_screening_source_summary(screening_report, app.get("screening_reviews") or []),
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
        "enhanced_review_sources": {
            "total_requirements": enhanced_review_summary.get("total_requirements", 0),
            "triggered": bool(enhanced_review_summary.get("triggered")),
            "overall_status": enhanced_review_summary.get("overall_status", "not_triggered"),
            "outstanding": len(enhanced_review_summary.get("outstanding") or []),
            "waivers": enhanced_review_summary.get("waiver_count", 0),
        },
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
    _has_terminal_match = bool(_screening_terminality.get("has_terminal_match"))
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
            "has_sandbox": screening_has_sandbox,
            "has_simulated": screening_has_simulated,
            "canonical_state": _screening_terminality.get("canonical_state"),
            "provider_availability": _screening_terminality.get("provider_availability"),
            "provider_mode": _screening_terminality.get("provider_mode"),
            "screening_result": _screening_terminality.get("screening_result"),
            "defensible_clear": bool(_screening_terminality.get("defensible_clear")),
            "screening_gate_ready": bool(_screening_terminality.get("screening_gate_ready")),
            "approval_gate_ready": bool(_screening_terminality.get("approval_gate_ready")),
            "approval_ready": bool(_screening_terminality.get("approval_ready")),
            "approval_ready_scope": _screening_terminality.get("approval_ready_scope"),
            "approval_blocking": bool(_screening_terminality.get("approval_blocking")),
            "blocking_reasons": _screening_terminality.get("blocking_reasons") or [],
            "approval_blocked_reasons": _screening_terminality.get("approval_blocked_reasons") or [],
            "has_terminal_match": _has_terminal_match,
            "has_formally_cleared_match": bool(_screening_terminality.get("has_formally_cleared_match")),
            "has_uncleared_completed_match": bool(_screening_terminality.get("has_uncleared_completed_match")),
            "completed_match_blocking": bool(_screening_terminality.get("completed_match_blocking")),
            "company_screening_configured": bool(_screening_terminality.get("company_screening_configured")),
        },
        "company_screening_configured": bool(_screening_terminality.get("company_screening_configured")),
        "edd_trigger_flags": edd_trigger_flags,
        "decision_recommendation": decision,
        "monitoring_tier": mon_tier,
    }
    edd_completion = app.get("edd_completion") if isinstance(app.get("edd_completion"), dict) else {}
    if edd_completion:
        agent5_input_contract["edd_completion"] = edd_completion
        memo["metadata"]["edd_completion"] = edd_completion
        if edd_completion.get("satisfied"):
            memo["metadata"].setdefault("key_findings", []).append(
                "Enhanced Due Diligence was completed and approved for the current trigger set; approval relies on that documented EDD evidence."
            )
    if _screening_recommendation_bound_to_review:
        agent5_input_contract["decision_recommendation"] = "REVIEW"
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
        routing_facts["supervisor_mandatory_escalation_reasons"] = list(
            supervisor_result.get("mandatory_escalation_reasons") or []
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
    # Deterministic guarantee: when the routing policy says EDD, the memo's
    # approval_recommendation MUST NOT be APPROVE / APPROVE_WITH_CONDITIONS.
    # We override to the canonical escalation value ESCALATE_TO_EDD
    # and re-record the original value for auditability. This closes
    # the gap where memo could recommend approval while routing said
    # EDD. Non-EDD supervisor mandatory escalations still block approval
    # through the supervisor/approval gate, but do not create false EDD.
    _route_is_edd = (edd_routing or {}).get("route") == "edd"
    _edd_completion_satisfied = bool(
        edd_completion.get("satisfied")
        and edd_completion.get("covers_current_triggers")
    )
    _is_mandatory_escalation = bool(supervisor_result.get("mandatory_escalation", False))
    _supervisor_can_approve = bool(supervisor_result.get("can_approve", False))
    if _route_is_edd and _edd_completion_satisfied:
        memo["metadata"]["edd_route_satisfied_by_completed_case"] = True
        try:
            _route_triggers = ", ".join((edd_routing or {}).get("triggers", [])[:6])
            _completion_statement = (
                "EDD was required"
                + (" for " + _route_triggers if _route_triggers else "")
                + " and has been completed/approved. This recommendation relies on the approved EDD case "
                + str(edd_completion.get("case_id") or "")
                + " and accepted enhanced requirement evidence; residual risk remains "
                + str(aggregated_risk)
                + "."
            )
            _decision_sec = (memo.get("sections") or {}).get("compliance_decision") or {}
            if isinstance(_decision_sec, dict):
                _existing = _decision_sec.get("content") or ""
                if _completion_statement not in _existing:
                    _decision_sec["content"] = (_existing.rstrip() + " " + _completion_statement).strip()
                memo["sections"]["compliance_decision"] = _decision_sec
            _conditions = list(memo["metadata"].get("conditions") or [])
            _conditions.append(
                "Approval relies on completed EDD case "
                + str(edd_completion.get("case_id") or "")
                + " and accepted enhanced requirements; this does not lower the residual risk classification."
            )
            memo["metadata"]["conditions"] = _conditions
        except Exception:
            pass
    elif _route_is_edd:
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
    elif _is_mandatory_escalation or not _supervisor_can_approve:
        _original_decision = memo["metadata"].get("approval_recommendation")
        _approval_like = ("APPROVE", "APPROVE_WITH_CONDITIONS")
        if _original_decision in _approval_like:
            _supervisor_reasons = list(supervisor_result.get("mandatory_escalation_reasons") or [])
            rule_enforcements.append({
                "rule": "RECOMMENDATION_BOUND_TO_SUPERVISOR_VETO",
                "original_decision": _original_decision,
                "enforced_decision": "REVIEW",
                "reason": (
                    "Supervisor veto prevents approval-like memo recommendation "
                    "(can_approve=" + str(_supervisor_can_approve)
                    + ", mandatory_escalation=" + str(_is_mandatory_escalation)
                    + (", reasons: " + ", ".join(_supervisor_reasons[:6]) if _supervisor_reasons else "")
                    + "). Memo remains on standard route and requires review."
                ),
            })
            rule_engine_result["enforcements"] = rule_enforcements
            memo["metadata"]["rule_engine"] = rule_engine_result
            memo["metadata"]["approval_recommendation_original"] = _original_decision
            memo["metadata"]["approval_recommendation"] = "REVIEW"
            memo["metadata"]["decision_label"] = "SUPERVISOR REVIEW REQUIRED"
            try:
                agent5_input_contract["decision_recommendation"] = "REVIEW"
                memo["metadata"]["agent5_input_contract"] = agent5_input_contract
            except Exception:
                pass
            try:
                _reason_text = (
                    ", ".join(_supervisor_reasons[:6])
                    if _supervisor_reasons
                    else "supervisor.can_approve=false"
                )
                _review_statement = (
                    "Recommendation: SUPERVISOR REVIEW REQUIRED (REVIEW) — "
                    "the memo supervisor vetoes approval-like recommendations "
                    + "(" + _reason_text + ")."
                )
                _decision_sec = (memo.get("sections") or {}).get("compliance_decision") or {}
                if isinstance(_decision_sec, dict):
                    _decision_sec["decision"] = "REVIEW"
                    _decision_sec["decision_label"] = "SUPERVISOR REVIEW REQUIRED"
                    _decision_sec["content"] = (
                        _review_statement
                        + " This is not an EDD escalation unless the deterministic routing policy separately routes the case to EDD."
                    )
                    memo["sections"]["compliance_decision"] = _decision_sec
                _conditions = list(memo["metadata"].get("conditions") or [])
                _conditions.append(
                    "Supervisor review/sign-off is required before any approval decision can be relied upon."
                )
                memo["metadata"]["conditions"] = _conditions
            except Exception:
                pass

    # ── Priority B.2 / Workstream C: Contradiction guard (fail-closed) ──
    # Defensive cross-check: if for any reason the recommendation
    # ended up as an approval value while the route is EDD, block the
    # memo. This is belt-and-braces — the binding above should already prevent this — and
    # ensures persistence cannot silently store a contradicting memo.
    _final_decision = memo["metadata"].get("approval_recommendation")
    if _route_is_edd and not _edd_completion_satisfied and _final_decision in ("APPROVE", "APPROVE_WITH_CONDITIONS"):
        memo["metadata"]["validation_status"] = "fail"
        memo["metadata"]["blocked"] = True
        memo["metadata"]["block_reason"] = (
            "Memo blocked: recommendation '" + str(_final_decision)
            + "' contradicts routing policy (route="
            + str((edd_routing or {}).get("route"))
            + ", mandatory_escalation=" + str(_is_mandatory_escalation)
            + "). Recommendation must be ESCALATE_TO_EDD."
        )
    if (not _route_is_edd) and (_is_mandatory_escalation or not _supervisor_can_approve) and _final_decision in ("APPROVE", "APPROVE_WITH_CONDITIONS"):
        memo["metadata"]["validation_status"] = "fail"
        memo["metadata"]["blocked"] = True
        memo["metadata"]["block_reason"] = (
            "Memo blocked: recommendation '" + str(_final_decision)
            + "' contradicts supervisor veto "
            + "(can_approve=" + str(_supervisor_can_approve)
            + ", mandatory_escalation=" + str(_is_mandatory_escalation)
            + "). Recommendation must be REVIEW or another non-approval value."
        )

    memo = _apply_decision_paper_cleanup(memo, {
        "app": app,
        "country": country,
        "sector": sector,
        "entity_type": entity_type,
        "business_activity": business_activity,
        "sof": sof,
        "exp_vol": exp_vol,
        "risk_display": risk_display,
        "aggregated_risk": aggregated_risk,
        "model_confidence": model_confidence,
        "screening_truth_blocks_approval": screening_truth_blocks_approval,
        "screening_terminal": screening_terminal,
        "screening_is_defensible_clear": screening_is_defensible_clear,
        "screening_formally_cleared_match": screening_formally_cleared_match,
        "screening_completion_phrase": _screening_completion_phrase,
        "documents": documents,
        "verified_docs": verified_docs,
        "pending_docs": pending_docs,
        "directors": directors,
        "ubos": ubos,
        "has_documents": has_documents,
        "doc_confidence": doc_confidence,
        "documentation_complete": documentation_complete,
        "all_peps": all_peps,
        "control_name": control_name,
        "control_pct": control_pct,
        "primary_ubo": primary_ubo,
        "own_rating": own_rating,
        "own_rating_justification": own_rating_justification,
        "struct_complexity": struct_complexity,
        "mon_tier": mon_tier,
        "adverse_media_context": adverse_media_context,
        "enhanced_review_summary": enhanced_review_summary,
        "jur_rating": jur_rating,
        "biz_rating": biz_rating,
        "tx_rating": tx_rating,
        "fc_rating": fc_rating,
        "now_ts": now_ts,
        "screening_review_evidence": screening_review_evidence,
    })

    # Re-run the supervisor after the officer-facing memo is condensed so the
    # displayed supervisor summary is based on the same decision-paper text.
    supervisor_result = run_memo_supervisor(memo)
    if not has_documents:
        supervisor_result["can_approve"] = False
        reasons = list(supervisor_result.get("mandatory_escalation_reasons") or [])
        if "no_documents_uploaded" not in reasons:
            reasons.append("no_documents_uploaded")
        supervisor_result["mandatory_escalation_reasons"] = reasons
    memo["supervisor"] = supervisor_result
    memo["metadata"]["supervisor_status"] = supervisor_result["verdict"]
    memo["metadata"]["supervisor_confidence"] = supervisor_result["supervisor_confidence"]
    try:
        routing_facts = dict(agent5_input_contract)
        routing_facts["supervisor_mandatory_escalation"] = bool(
            supervisor_result.get("mandatory_escalation", False)
        )
        routing_facts["supervisor_mandatory_escalation_reasons"] = list(
            supervisor_result.get("mandatory_escalation_reasons") or []
        )
        edd_routing = _evaluate_edd_routing(routing_facts)
    except Exception as _routing_err:  # pragma: no cover - defensive
        logger.error("EDD routing evaluation after memo cleanup failed: %s", _routing_err)
        edd_routing = {
            "policy_version": "edd_routing_policy_v1",
            "route": "edd",
            "triggers": ["routing_evaluation_failed"],
            "inputs": {},
            "evaluated_at": now_ts,
        }
    memo["metadata"]["edd_routing"] = edd_routing
    _final_route_is_edd = (edd_routing or {}).get("route") == "edd"
    _final_edd_satisfied = bool(
        edd_completion.get("satisfied")
        and edd_completion.get("covers_current_triggers")
    )
    _final_supervisor_can_approve = bool(supervisor_result.get("can_approve", False))
    _final_supervisor_mandatory = bool(supervisor_result.get("mandatory_escalation", False))
    _current_recommendation = memo["metadata"].get("approval_recommendation")
    if _final_route_is_edd and not _final_edd_satisfied and _current_recommendation in ("APPROVE", "APPROVE_WITH_CONDITIONS"):
        memo["metadata"]["approval_recommendation_original"] = _current_recommendation
        memo["metadata"]["approval_recommendation"] = "ESCALATE_TO_EDD"
        memo["metadata"]["decision_label"] = "ESCALATE TO EDD"
        _decision_sec = (memo.get("sections") or {}).get("compliance_decision") or {}
        if isinstance(_decision_sec, dict):
            _decision_sec["decision"] = "ESCALATE_TO_EDD"
            _decision_sec["decision_label"] = "ESCALATE TO EDD"
            _decision_sec["content"] = (
                "Recommendation: ESCALATE TO EDD (ESCALATE_TO_EDD) — deterministic routing policy "
                "requires Enhanced Due Diligence before any approval. This memo is not an approval recommendation."
            )
            memo["sections"]["compliance_decision"] = _decision_sec
    elif (
        (not _final_route_is_edd)
        and (_final_supervisor_mandatory or not _final_supervisor_can_approve)
        and _current_recommendation in ("APPROVE", "APPROVE_WITH_CONDITIONS")
    ):
        memo["metadata"]["approval_recommendation_original"] = _current_recommendation
        memo["metadata"]["approval_recommendation"] = "REVIEW"
        memo["metadata"]["decision_label"] = "SUPERVISOR REVIEW REQUIRED"
        _decision_sec = (memo.get("sections") or {}).get("compliance_decision") or {}
        if isinstance(_decision_sec, dict):
            _decision_sec["decision"] = "REVIEW"
            _decision_sec["decision_label"] = "SUPERVISOR REVIEW REQUIRED"
            _decision_sec["content"] = (
                "Recommendation: SUPERVISOR REVIEW REQUIRED (REVIEW) — the final memo supervisor "
                "verdict prevents approval-like recommendations."
            )
            memo["sections"]["compliance_decision"] = _decision_sec

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
