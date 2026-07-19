"""
ARIE Finance — Rule Engine: Risk Scoring, Country/Sector Classification, Rules 4A-4E
Extracted from server.py during Sprint 2 monolith decomposition.

Provides:
    - Country risk classification (FATF grey/black, sanctioned, low-risk)
    - Sector risk scoring
    - Composite risk score computation (D1-D5 dimensions)
    - Rule 4A-4E constants for pre-generation enforcement
    - Risk aggregation weights and ranks
    - Reusable risk recomputation helper (EX-09)
"""
import ast
import json
import logging
import re
from datetime import datetime, timezone

from risk_controlled_values import (
    COUNTRY_EXACT_ALIASES,
    ControlledResolution,
    REGISTRY_VERSION,
    controlled_value_hash,
    mapping_fidelity_enabled,
    normalize_controlled_value,
    reconcile_mapping_staleness,
    resolve_controlled_score,
    resolve_tier0a_country_alias,
    structured_mapping_evidence,
    unresolved_mapping_sentinel,
)

logger = logging.getLogger("arie")

GATE0_DECLARED_PEP_SCORE = 4

# Runtime-owned parser catalog. These immutable values are consumed directly
# by ``compute_risk_score`` and exposed read-only by the Back Office model
# projection. Keeping the vocabulary beside the scorer prevents a second,
# manually maintained UI scoring list without changing scoring semantics.
ADVERSE_MEDIA_SCORE_4_KEYWORDS = ("confirmed", "regulatory", "criminal")
ADVERSE_MEDIA_SCORE_2_KEYWORDS = ("minor", "unsubstantiated")
ADVERSE_MEDIA_CLEAR_VALUES = ("clear", "none", "no")

SOURCE_OF_WEALTH_SCORE_MAP = {
    "business revenue": 1,
    "trading profits": 1,
    "investment": 1,
    "dividends": 1,
    "government funding": 1,
    "grants": 1,
    "sale of assets": 2,
    "property": 2,
    "venture capital": 2,
    "investor funding": 2,
    "inheritance": 3,
    "family wealth": 3,
    "loan": 3,
    "credit": 3,
    "other": 3,
}
SOURCE_OF_WEALTH_UNKNOWN_VALUES = ("information not provided", "not provided", "unknown")

SOURCE_OF_FUNDS_SCORE_MAP = {
    "company bank": 1,
    "parent company": 1,
    "group entity": 1,
    "client payments": 1,
    "receivables": 1,
    "revenue": 1,
    "business operations": 1,
    "shareholder": 2,
    "director": 2,
    "capital injection": 2,
    "investment round": 2,
    "fundraise": 2,
    "sale of assets": 2,
    "loan": 3,
    "credit facility": 3,
    "other": 3,
}
SOURCE_OF_FUNDS_UNKNOWN_VALUES = ("information not provided", "not provided", "unknown")

SERVICE_DOMESTIC_REQUIRED_KEYWORDS = ("domestic", "single")
SERVICE_SCORE_2_KEYWORDS = ("multi-currency", "multi currency")
SERVICE_SCORE_3_KEYWORDS = ("cross-border", "international")

DELIVERY_SCORE_1_KEYWORDS = ("face-to-face", "in-person", "in person")
DELIVERY_SCORE_2_KEYWORDS = ("video",)
DELIVERY_REMOTE_KEYWORDS = ("non-face", "remote")
DELIVERY_SCORE_4_KEYWORDS = ("anonymous", "unverified")


def _service_selection_values(value):
    """Return every selected service from supported portal/API/legacy shapes.

    This is structure parsing only: it does not fuzzy-match or assign scores.
    The plural service field has historically been persisted as an array, a
    JSON/Python-list string, a delimited string, or a nested canonical object.
    """
    if value in (None, "", [], {}, ()):  # Existing validation permits blanks.
        return [], "empty"
    if isinstance(value, dict):
        for key in (
            "primary_services", "services_required", "servicesRequired",
            "selected", "values",
        ):
            if key in value:
                selected, shape = _service_selection_values(value.get(key))
                return selected, f"object.{key}:{shape}"
        return [json.dumps(value, sort_keys=True, default=str)], "unsupported_object"
    if isinstance(value, (list, tuple, set)):
        sequence = sorted(value, key=str) if isinstance(value, set) else list(value)
        output = []
        for item in sequence:
            nested, _ = _service_selection_values(item)
            output.extend(nested)
        return output, type(value).__name__
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return [], "blank_string"
        if text[:1] in "[({":
            for parser, label in ((json.loads, "json"), (ast.literal_eval, "literal")):
                try:
                    parsed = parser(text)
                except (TypeError, ValueError, SyntaxError):
                    continue
                if isinstance(parsed, (list, tuple, set, dict)):
                    selected, shape = _service_selection_values(parsed)
                    return selected, f"{label}_{shape}"
        if re.search(r"[;|]", text):
            return [part.strip() for part in re.split(r"[;|]", text) if part.strip()], "delimited_string"
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()], "comma_delimited_string"
        return [value], "scalar_string"
    return [str(value)], type(value).__name__


def _service_selection_source(data):
    candidates = (
        ("_service_selections", data.get("_service_selections")),
        ("services_required", data.get("services_required")),
        ("servicesRequired", data.get("servicesRequired")),
    )
    for source, payload in candidates:
        selected, shape = _service_selection_values(payload)
        if selected:
            return selected, source, shape, True

    business = data.get("business")
    if isinstance(business, dict):
        services = business.get("services")
        selected, shape = _service_selection_values(services)
        if selected:
            return selected, "business.services.primary_services", shape, True

    for source in ("primary_service", "service_required"):
        selected, shape = _service_selection_values(data.get(source))
        if selected:
            return selected, source, shape, False
    return [], "missing", "empty", False


def _service_label_resolution(raw_value, cross_border):
    normalized = normalize_controlled_value(raw_value)
    if all(keyword in normalized for keyword in SERVICE_DOMESTIC_REQUIRED_KEYWORDS):
        return 1, "mapped", "domestic_and_single_keywords", normalized
    if any(keyword in normalized for keyword in SERVICE_SCORE_2_KEYWORDS):
        return 2, "mapped", "score_2_keyword", normalized
    if any(keyword in normalized for keyword in SERVICE_SCORE_3_KEYWORDS):
        return 3, "mapped", "score_3_keyword", normalized
    # Preserve the existing score contract while making the unmatched label
    # explicit. In a multi-select controlled collection this status becomes a
    # fail-closed sentinel; it is never silently presented as a resolved label.
    if cross_border:
        return 3, "unmatched", "cross_border_context_fallback", normalized
    return 2, "unmatched", "legacy_default_score_2", normalized


def _legacy_primary_service_risk(data):
    """Return the exact pre-hotfix D3.1 result for zero/one selection."""
    score = 2
    value = (data.get("primary_service") or data.get("service_required") or "").lower()
    if all(keyword in value for keyword in SERVICE_DOMESTIC_REQUIRED_KEYWORDS):
        score = 1
    elif any(keyword in value for keyword in SERVICE_SCORE_2_KEYWORDS):
        score = 2
    elif any(keyword in value for keyword in SERVICE_SCORE_3_KEYWORDS) or data.get("cross_border"):
        score = 3
    elif data.get("cross_border"):
        score = 3
    return score


def resolve_selected_service_risk(data, config=None):
    """Resolve D3.1 once for submission, replay, and recomputation.

    The final factor is the maximum effective score across every selection.
    Unmatched values retain the pre-hotfix numeric fallback for score
    continuity but make a multi-select result fail closed through a hashed
    mapping sentinel and structured evidence.
    """
    raw_values, source, payload_shape, controlled_collection = _service_selection_source(data)
    selected_raw = [str(value) for value in raw_values if str(value or "").strip()]
    strict_unmatched = controlled_collection and len(selected_raw) > 1
    try:
        from observability import get_request_id
        request_id = get_request_id() or data.get("request_id") or ""
    except Exception:
        request_id = data.get("request_id") or ""
    application_id = data.get("application_id") or data.get("id") or ""
    config_version = (
        (config or {}).get("_config_version")
        or (
            f"risk_config:{(config or {}).get('updated_at')}"
            if (config or {}).get("updated_at")
            else ""
        )
        or data.get("_risk_config_version")
        or REGISTRY_VERSION
    )
    individual = []
    sentinels = []
    unique_normalized = []
    for raw_value in selected_raw:
        score, status, rule, normalized = _service_label_resolution(
            raw_value, bool(data.get("cross_border"))
        )
        if normalized not in unique_normalized:
            unique_normalized.append(normalized)
        resolution_status = "unresolved" if strict_unmatched and status == "unmatched" else status
        digest = controlled_value_hash("service", normalized)
        sentinel = ""
        if resolution_status == "unresolved":
            sentinel = unresolved_mapping_sentinel("service", normalized)
            if sentinel not in sentinels:
                sentinels.append(sentinel)
        individual.append({
            "family": "service",
            "raw_value": raw_value,
            "normalized_value": normalized,
            "hash": digest,
            "application_id": str(application_id),
            "request_id": str(request_id),
            "config_version": str(config_version),
            "score": score,
            "resolution_status": resolution_status,
            "runtime_rule": rule,
            "sentinel": sentinel,
        })

    selected_max_score = max(
        (item["score"] for item in individual),
        default=_legacy_primary_service_risk(data),
    )
    # The approved correction is deliberately plural-only. A few legacy rows
    # carry a one-item service array plus a different historical primary alias;
    # retaining that exact primary-service result prevents a single-service
    # policy change while every true multi-select record uses the maximum.
    multi_service = len(selected_raw) > 1
    final_score = selected_max_score if multi_service else _legacy_primary_service_risk(data)
    return {
        "selection_source": source,
        "payload_shape": payload_shape,
        "controlled_collection": controlled_collection,
        "raw_services": selected_raw,
        "normalized_services": [item["normalized_value"] for item in individual],
        "unique_normalized_services": unique_normalized,
        "individual_resolutions": individual,
        "selected_max_score": selected_max_score,
        "final_max_score": final_score,
        "maximum_enforced": multi_service,
        "single_service_compatibility": not multi_service,
        "selection_count": len(selected_raw),
        "unique_selection_count": len(unique_normalized),
        "order_independent": True,
        "resolution_status": "unresolved" if sentinels else "resolved",
        "sentinels": sentinels,
    }

CONTROLLED_PRESCREENING_CORRECTION_SOURCE = "application_overview_prescreening_correction_mode"
CONTROLLED_PRESCREENING_CORRECTION_OVERLAY_MAP = {
    "country_of_incorporation": {
        "app_column": "country",
        "prescreening_keys": ("country_of_incorporation", "country"),
    },
    "sector": {
        "app_column": "sector",
        "prescreening_keys": ("sector",),
    },
    "entity_type": {
        "app_column": "entity_type",
        "prescreening_keys": ("entity_type",),
    },
    "ownership_structure": {
        "app_column": "ownership_structure",
        "prescreening_keys": ("ownership_structure",),
    },
    "introduction_method": {
        "prescreening_keys": ("introduction_method",),
    },
    "monthly_volume": {
        "prescreening_keys": ("monthly_volume", "expected_volume"),
    },
}


def safe_json_loads(val):
    """Safely parse JSON — handles PostgreSQL JSONB (already dict) and SQLite TEXT (string)."""
    if val is None:
        return {}
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _optional_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"yes", "true", "1", "y"}:
        return True
    if text in {"no", "false", "0", "n"}:
        return False
    return None


def _party_has_declared_or_confirmed_pep(person):
    if not isinstance(person, dict):
        return False
    declaration = safe_json_loads(person.get("pep_declaration"))
    status = str(person.get("pep_status") or declaration.get("pep_status") or "").strip().lower()

    declared = _optional_bool(person.get("client_declared_pep", declaration.get("client_declared_pep")))
    if declared is None:
        declared = _optional_bool(person.get("declared_pep", declaration.get("declared_pep")))
    officer_verified = _optional_bool(
        person.get("officer_verified_pep", declaration.get("officer_verified_pep"))
    )
    if officer_verified is None:
        officer_verified = _optional_bool(person.get("verified_pep", declaration.get("verified_pep")))

    if declared is True or officer_verified is True:
        return True
    if status in {"declared_yes", "confirmed_pep"}:
        return True
    if declared is False or officer_verified is False:
        return False
    if status in {"declared_no", "false_positive", "not_pep", "pending_review", "not_verified"}:
        return False

    # Legacy records with no declaration metadata pre-date the separated PEP
    # state model. Keep them counted; explicit negative metadata wins.
    raw_pep = str(person.get("is_pep") or person.get("isPEP") or "").strip().lower()
    return not declaration and raw_pep in {"yes", "true", "1", "y", "confirmed_pep", "declared_yes"}


def _declared_pep_score_evidence(person):
    """Return the approved Gate 0 score with the declaration's role evidence.

    The portal persists the authoritative role at
    ``pep_declaration.pep_role_type``. Legacy top-level role fields remain a
    read-only fallback, but role type never changes the approved score: every
    declared or officer-confirmed PEP is score 4.
    """
    if not _party_has_declared_or_confirmed_pep(person):
        return None

    declaration = safe_json_loads(person.get("pep_declaration"))
    if not isinstance(declaration, dict):
        declaration = {}
    role_type = declaration.get("pep_role_type")
    if not str(role_type or "").strip():
        role_type = person.get("pep_type") or person.get("pep_category") or ""
    return {
        "pep_role_type": str(role_type or "").strip(),
        "score": GATE0_DECLARED_PEP_SCORE,
    }


def _apply_controlled_prescreening_correction_overlays(db, app_id, application, prescreening_data):
    """Apply officer working-value overlays without mutating original submission JSON."""
    app_overlay = dict(application or {})
    prescreening_overlay = dict(prescreening_data or {})
    try:
        rows = db.execute(
            """
            SELECT after_state
            FROM application_corrections
            WHERE application_id = ?
              AND target_type = 'prescreening_field'
              AND correction_source = ?
            ORDER BY corrected_at ASC, id ASC
            """,
            (app_id, CONTROLLED_PRESCREENING_CORRECTION_SOURCE),
        ).fetchall()
    except Exception as exc:
        logger.warning(
            "controlled_prescreening_overlay_unavailable app_id=%s error=%s",
            app_id,
            exc,
        )
        return app_overlay, prescreening_overlay

    for row in rows:
        state = safe_json_loads((row or {}).get("after_state"))
        if not isinstance(state, dict):
            continue
        for field_path, cfg in CONTROLLED_PRESCREENING_CORRECTION_OVERLAY_MAP.items():
            if field_path not in state:
                continue
            value = state.get(field_path)
            if value in (None, ""):
                continue
            app_column = cfg.get("app_column")
            if app_column:
                app_overlay[app_column] = value
            for key in cfg.get("prescreening_keys", ()):
                prescreening_overlay[key] = value
    return app_overlay, prescreening_overlay


# ══════════════════════════════════════════════════════════
# COUNTRY RISK CLASSIFICATION
# ══════════════════════════════════════════════════════════

# v1.6: Country lists updated to match ARIE_Risk_Score_Sheet v1.6 (80 countries)
# Grey list = score 3 (FATF monitored jurisdictions)
FATF_GREY = {"algeria", "burkina faso", "cameroon", "democratic republic of congo",
             "haiti", "kenya", "laos", "lebanon", "mali", "monaco", "mozambique",
             "nigeria", "pakistan", "philippines", "senegal", "south africa", "south sudan",
             "tanzania", "venezuela", "vietnam", "yemen",
             # Offshore/secrecy jurisdictions scored 3 (not FATF grey but high risk)
             "bermuda", "vanuatu", "samoa", "marshall islands", "iraq"}

# Black list / score 4 = FATF blacklisted, sanctioned, or suspended
FATF_BLACK = {"iran", "north korea", "myanmar", "russia", "syria", "belarus",
              "cuba", "afghanistan", "somalia", "libya", "eritrea", "sudan"}

# Sanctioned countries (comprehensive) — used for hard blocks
SANCTIONED = {"iran", "north korea", "syria", "cuba", "crimea", "myanmar",
              "russia", "belarus"}

SANCTIONED_COUNTRIES_FULL = {"iran", "north korea", "syria", "cuba", "crimea", "myanmar", "russia", "belarus",
                              "venezuela", "afghanistan", "somalia", "yemen", "libya", "iraq", "south sudan",
                              "central african republic", "democratic republic of congo", "mali",
                              "guinea-bissau", "lebanon", "eritrea", "sudan"}

# Secrecy jurisdictions — score 4 for intermediary shareholder purposes
SECRECY_JURISDICTIONS = {"bvi", "british virgin islands", "cayman islands", "panama",
                          "seychelles", "bermuda", "jersey", "guernsey", "isle of man",
                          "liechtenstein", "vanuatu", "samoa", "marshall islands"}

ALLOWED_CURRENCIES = {"USD", "EUR", "GBP", "AED"}

# v1.6: Low risk = score 1 (FATF members, strong AML frameworks)
# Removed: portugal, spain, italy (now scored by country_risk_scores DB or default 2)
# Added: south korea, israel
LOW_RISK = {"united kingdom", "uk", "france", "germany", "sweden", "norway",
            "denmark", "finland", "australia", "new zealand", "canada", "usa", "united states",
            "japan", "singapore", "hong kong", "switzerland", "netherlands", "belgium", "luxembourg",
            "ireland", "austria", "south korea", "israel",
            # v1.6: EU members with strong AML
            "portugal", "spain", "italy"}

COUNTRY_ALIASES = {
    "uk": "united kingdom", "gb": "united kingdom", "gbr": "united kingdom",
    "great britain": "united kingdom", "britain": "united kingdom",
    "england": "united kingdom", "scotland": "united kingdom",
    "wales": "united kingdom", "northern ireland": "united kingdom",
    "england and wales": "united kingdom", "england & wales": "united kingdom",
    "us": "united states", "usa": "united states",
    "united states of america": "united states",
    "uae": "united arab emirates", "emirates": "united arab emirates",
    "korea": "south korea", "republic of korea": "south korea",
    "bvi": "british virgin islands",
    "hk": "hong kong", "sg": "singapore",
    "drc": "democratic republic of congo",
    "dr congo": "democratic republic of congo",
    "north korea (dprk)": "north korea",
    "dprk": "north korea",
}


def normalize_country_key(country):
    value = str(country or "").strip().lower()
    if not value:
        return ""
    value = " ".join(value.replace(",", " ").split())
    for prefix in ("republic of ", "state of ", "the ", "federation of "):
        if value.startswith(prefix) and len(value) > len(prefix):
            value = value[len(prefix):].strip()
    if mapping_fidelity_enabled():
        value = resolve_tier0a_country_alias(value)
    return COUNTRY_ALIASES.get(value, value)


def _manual_country_score_from_config(country_key, config_country_scores):
    if config_country_scores is not None and not isinstance(config_country_scores, dict):
        logger.error(
            "classify_country received non-dict config_country_scores: type=%s — using hardcoded FATF lists",
            type(config_country_scores).__name__,
        )
        return None, False
    if not config_country_scores:
        return None, False
    for raw_key, raw_score in config_country_scores.items():
        if normalize_country_key(raw_key) != country_key:
            continue
        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            return None, False
        if 1 <= score <= 4:
            return score, True
    return None, False


def _risk_rating_from_score(score):
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 2
    return {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "VERY_HIGH"}.get(score, "MEDIUM")


def country_risk_details(country_name, config_country_scores=None):
    """Return manual country-risk evidence used by scoring and memo generation.

    PR-CR1R: the active source of truth is the manual Risk Scoring Model
    settings stored in risk_config.country_risk_scores. The PR-CR1 imported
    snapshot is not used for scoring, memo evidence, gates, or recomputation.
    """
    key = normalize_country_key(country_name)
    if not key:
        return {
            "found": False,
            "is_unknown": True,
            "defaulted": True,
            "lookup_reason": "missing_country",
            "country_name": country_name or "",
            "country_key": key,
            "risk_rating": "MEDIUM",
            "risk_score": 2,
            "fatf_status": "none",
            "sanctions_status": "none",
            "high_risk_status": "unknown_country",
            "source_name": "Manual Risk Scoring Model default",
            "source": "manual_risk_config_default",
            "source_url": "",
            "source_publication_date": "",
            "effective_date": "",
            "active_source": "manual_settings",
            "notes": "Missing country defaults to MEDIUM, never LOW.",
        }

    score, found_in_config = _manual_country_score_from_config(key, config_country_scores)
    source_name = "Manual Risk Scoring Model country_risk_scores"
    source = "risk_config.country_risk_scores"
    if score is None:
        source_name = "Legacy manual country-risk fallback lists"
        source = "legacy_manual_country_risk_lists"
        if key in SANCTIONED or key in FATF_BLACK:
            score = 4
        elif key in FATF_GREY:
            score = 3
        elif key in LOW_RISK:
            score = 1
        else:
            score = 2

    fatf_status = (
        "black" if key in FATF_BLACK
        else "grey" if key in FATF_GREY and (not found_in_config or int(score) >= 3)
        else "none"
    )
    sanctions_status = "sanctioned" if key in SANCTIONED else "none"
    high_risk_status = "manual_high_risk" if score >= 3 and fatf_status == "none" and sanctions_status == "none" else "none"
    return {
        "found": bool(found_in_config or key in SANCTIONED or key in FATF_BLACK or key in FATF_GREY or key in LOW_RISK),
        "is_unknown": not bool(found_in_config or key in SANCTIONED or key in FATF_BLACK or key in FATF_GREY or key in LOW_RISK),
        "defaulted": not bool(found_in_config or key in SANCTIONED or key in FATF_BLACK or key in FATF_GREY or key in LOW_RISK),
        "lookup_reason": "manual_config" if found_in_config else source,
        "country_name": country_name or key,
        "country_key": key,
        "risk_rating": _risk_rating_from_score(score),
        "risk_score": int(score),
        "fatf_status": fatf_status,
        "sanctions_status": sanctions_status,
        "high_risk_status": high_risk_status,
        "source_name": source_name,
        "source": source,
        "source_url": "",
        "source_publication_date": "",
        "effective_date": "",
        "active_source": "manual_settings",
        "notes": "Manual country-risk settings are active for pilot; PR-CR1 snapshot is dormant.",
    }


def _country_scores_from_db_if_available():
    try:
        config = load_risk_config() or {}
        scores = config.get("country_risk_scores") if isinstance(config, dict) else None
        return scores if isinstance(scores, dict) and scores else None
    except RiskConfigUnavailable:
        # DCI-008: never launder a fail-closed condition into a silent
        # hardcoded-fallback path — let the decision abort.
        raise
    except Exception:
        return None


# ══════════════════════════════════════════════════════════
# SECTOR RISK SCORING
# ══════════════════════════════════════════════════════════

# v1.6: Sector scores aligned with ARIE_Risk_Score_Sheet Score Reference
SECTOR_SCORES = {
    "regulated financial": 1, "government": 1, "bank": 1, "listed company": 1,
    "healthcare": 2, "technology": 2, "software": 2, "saas": 2, "manufacturing": 2,
    "retail": 2, "e-commerce": 2, "education": 2, "media": 2, "logistics": 2,
    "hospitality": 2, "tourism": 2, "travel": 2,  # v1.6: added from Score Reference
    "import": 3, "export": 3, "real estate": 3, "construction": 3, "mining": 3,
    "oil": 3, "gas": 3, "money services": 3, "forex": 3, "precious": 3,
    "non-profit": 3, "ngo": 3, "charity": 3, "advisory": 3,
    "management consulting": 3, "consulting": 3, "financial / tax advisory": 3,
    "legal": 3, "accounting": 3,  # v1.6: Legal/Accounting/Advisory
    "private banking": 3, "wealth management": 3,  # v1.6: added from Score Reference
    "remittance": 3, "money transfer": 3,  # v1.6: MSB/Remittance
    "crypto": 4, "virtual asset": 4, "gambling": 4, "gaming": 4, "betting": 4,
    "arms": 4, "defence": 4, "military": 4, "shell company": 4, "nominee": 4,
    "adult": 4, "adult entertainment": 4,  # v1.6: added from Score Reference
}


# ══════════════════════════════════════════════════════════
# RULE 4A-4E CONSTANTS (Pre-generation enforcement)
# ══════════════════════════════════════════════════════════

HIGH_RISK_SECTORS = ("Cryptocurrency", "Money Services", "Gaming", "Arms", "Precious Metals")

# Sectors that score 4 (very-high risk) — used by elevation logic
HIGH_RISK_SECTOR_KEYWORDS = {
    "crypto", "virtual asset", "digital asset", "gambling", "gaming", "betting",
    "arms", "defence", "military", "shell company", "nominee",
    "adult", "adult entertainment",
}

# Keywords indicating opaque / shell-like / materially complex ownership
OPAQUE_OWNERSHIP_KEYWORDS = {
    "complex", "shell", "opaque", "nominee", "bearer", "multi-layered",
    "layered", "trust", "3+", "undisclosed",
}

MINIMUM_MEDIUM_SECTORS = ("Remittance", "Money Transfer", "Payment Services", "E-Money", "Virtual Assets", "MVTS")

MEDIUM_RISK_SECTORS = ("Financial Services", "Real Estate", "Legal Services", "Trust Services", "Art Dealing")

HIGH_RISK_COUNTRIES = ("Iran", "North Korea", "Syria", "Myanmar", "Afghanistan", "Yemen", "Libya", "Somalia")

OFFSHORE_COUNTRIES = ("Mauritius", "Seychelles", "Cayman Islands", "BVI", "Panama", "Jersey", "Guernsey", "Isle of Man", "Bermuda", "Luxembourg", "Liechtenstein")

# Sprint 2.5: Unified keyword lists — canonical source of truth.
# Union of both rule_engine and memo_handler versions. No keywords removed.
ALWAYS_RISK_DECREASING = [
    "all documents verified", "biometric match", "clean audit", "clean sanctions",
    "clean screening", "clear source of funds", "compliant jurisdiction",
    "consistent activity", "cooperative jurisdiction", "domestic entity", "domestic only",
    "established business", "face-to-face", "face-to-face verified", "fatf compliant",
    "fully verified", "licensed entity", "listed company", "long-standing relationship",
    "low jurisdictional risk", "low risk jurisdiction", "low risk sector",
    "no adverse findings", "no adverse media", "no bearer shares", "no money laundering",
    "no nominee shareholders", "no outstanding documents", "no pep exposure",
    "no regulatory action", "no risk factors identified", "no sanctions match",
    "no shell companies", "no terrorism financing", "no unusual transactions",
    "publicly listed", "regulated entity", "simple structure", "single jurisdiction",
    "source of funds verified", "transparent ownership", "verified identity",
]

ALWAYS_RISK_INCREASING = [
    "adverse media", "bearer shares", "cannot be determined", "cash intensive",
    "cash-intensive", "complex ownership", "complex structure", "criminal record",
    "cross-border high-risk", "data gap", "dormant company", "high risk jurisdiction",
    "incomplete documents", "layering", "limited trading history", "missing data",
    "multi-layered", "no financial statements", "no source of funds", "nominee director",
    "nominee shareholder", "non-cooperative jurisdiction", "not provided", "offshore",
    "ongoing investigation", "opacity score high", "opaque structure", "outstanding document",
    "recently incorporated", "regulatory action", "round-tripping", "sanctioned country",
    "sanctions match", "secrecy jurisdiction", "shell company", "structuring",
    "suspicious transaction", "tax haven", "undisclosed ubo", "unexplained wealth",
    "unusual transaction", "unverified source of funds", "virtual assets",
]


# ══════════════════════════════════════════════════════════
# RISK AGGREGATION
# ══════════════════════════════════════════════════════════

RISK_WEIGHTS = {
    "jurisdiction": 0.20,
    "business": 0.15,
    "transaction": 0.10,
    "ownership": 0.25,
    "fincrime": 0.10,
    "documentation": 0.10,
    "data_quality": 0.10,
}

RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
RISK_SCORE_FLOORS = {"LOW": 0.0, "MEDIUM": 40.0, "HIGH": 55.0, "VERY_HIGH": 70.0}
RISK_LANE_MAP = {
    "LOW": "Fast Lane",
    "MEDIUM": "Standard Review",
    "HIGH": "EDD",
    "VERY_HIGH": "EDD",
}


def _append_unique(items, value):
    if value and value not in items:
        items.append(value)


def _risk_tier_from_score(score):
    try:
        score = int(score)
    except (TypeError, ValueError):
        return ""
    if score >= 4:
        return "very_high"
    if score >= 3:
        return "elevated"
    if score <= 1:
        return "low"
    return "standard"


def _score_after_floor(current_score, minimum_level):
    if minimum_level == "MEDIUM":
        return current_score
    return max(current_score, RISK_SCORE_FLOORS[minimum_level])


def _ownership_transparency_tier(ownership_structure):
    return "opaque" if _is_opaque_ownership(ownership_structure) else "clear"


def apply_risk_floor(risk_dict, minimum_level, reason_code, reason_text):
    """Mutate a risk result so the final displayed level cannot sit below a mandatory floor."""
    if not isinstance(risk_dict, dict):
        return risk_dict

    minimum = str(minimum_level or "").strip().upper()
    if minimum not in RISK_RANK:
        return risk_dict

    current = str(
        risk_dict.get("final_risk_level")
        or risk_dict.get("level")
        or risk_dict.get("risk_level")
        or ""
    ).strip().upper()
    if current not in RISK_RANK:
        current = "LOW"

    if RISK_RANK[current] >= RISK_RANK[minimum]:
        return risk_dict

    previous_score = risk_dict.get("score")
    try:
        previous_score_num = float(previous_score)
    except (TypeError, ValueError):
        previous_score_num = 0.0

    risk_dict.setdefault("base_risk_score", previous_score_num)
    risk_dict.setdefault("base_risk_level", current)
    risk_dict["score"] = _score_after_floor(previous_score_num, minimum)
    risk_dict["level"] = minimum
    risk_dict["final_risk_level"] = minimum
    risk_dict["lane"] = RISK_LANE_MAP.get(minimum, "Standard Review")

    escalations = risk_dict.get("escalations")
    if not isinstance(escalations, list):
        escalations = []
    _append_unique(escalations, reason_code)
    risk_dict["escalations"] = escalations

    existing = str(risk_dict.get("elevation_reason_text") or "").strip()
    reason = str(reason_text or "").strip()
    if reason and reason not in existing:
        risk_dict["elevation_reason_text"] = f"{existing}; {reason}" if existing else reason
    elif existing:
        risk_dict["elevation_reason_text"] = existing
    else:
        risk_dict["elevation_reason_text"] = ""

    return risk_dict


# ══════════════════════════════════════════════════════════
# RISK CONFIG SCHEMA VALIDATION
# ══════════════════════════════════════════════════════════

def _normalize_score_map(value):
    """Attempt to normalize a malformed score map into a canonical dict.

    Handles the known corruption pattern where a list-of-dicts was stored
    instead of a flat dict.  E.g. [{"sme": 2}, {"shell": 4}] → {"sme": 2, "shell": 4}.

    Returns the dict on success, or None if normalization is not possible.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        # Try to merge list-of-dicts into a single dict
        merged = {}
        for item in value:
            if isinstance(item, dict):
                merged.update(item)
            else:
                return None  # Cannot normalize: list contains non-dict items
        if merged:
            return merged
    return None


def validate_score_map(value, column_name):
    """Validate that a score-mapping column is a dict {str → int/float}.

    Returns (valid_dict, errors) where valid_dict is the validated/normalized
    dict (or None if invalid) and errors is a list of error messages.
    """
    errors = []
    if value is None:
        return None, []

    if not isinstance(value, dict):
        # Attempt normalization (e.g. list-of-dicts → flat dict)
        normalized = _normalize_score_map(value)
        if normalized is not None:
            logger.warning(
                "risk_config %s: normalized %s to dict (%d entries)",
                column_name, type(value).__name__, len(normalized),
            )
            value = normalized
        else:
            errors.append(
                f"{column_name}: expected dict, got {type(value).__name__}"
            )
            return None, errors

    # Validate entries: keys must be strings, values must be numeric
    bad_entries = []
    for k, v in value.items():
        if not isinstance(k, str):
            bad_entries.append(f"key {k!r} is not a string")
        if not isinstance(v, (int, float)):
            bad_entries.append(f"value for {k!r} is {type(v).__name__}, expected int/float")
    if bad_entries:
        errors.append(f"{column_name}: invalid entries: {'; '.join(bad_entries[:5])}")

    return value, errors


def validate_dimensions(value):
    """Validate that dimensions is a list of objects with id, name, weight, subcriteria.

    Returns (valid_list, errors).
    """
    errors = []
    if value is None:
        return None, []

    if not isinstance(value, list):
        errors.append(f"dimensions: expected list, got {type(value).__name__}")
        return None, errors

    for i, dim in enumerate(value):
        if not isinstance(dim, dict):
            errors.append(f"dimensions[{i}]: expected dict, got {type(dim).__name__}")
            continue
        for required_key in ("id", "name", "weight"):
            if required_key not in dim:
                errors.append(f"dimensions[{i}]: missing required key '{required_key}'")
        if "weight" in dim and not isinstance(dim["weight"], (int, float)):
            errors.append(f"dimensions[{i}].weight: expected number, got {type(dim['weight']).__name__}")
        if "subcriteria" in dim:
            if not isinstance(dim["subcriteria"], list):
                errors.append(f"dimensions[{i}].subcriteria: expected list, got {type(dim['subcriteria']).__name__}")
            else:
                for j, sub in enumerate(dim["subcriteria"]):
                    if not isinstance(sub, dict):
                        errors.append(f"dimensions[{i}].subcriteria[{j}]: expected dict")
                    elif "name" not in sub or "weight" not in sub:
                        errors.append(f"dimensions[{i}].subcriteria[{j}]: missing name or weight")

    return value, errors


def validate_thresholds(value):
    """Validate that thresholds is a list of {level, min, max} objects.

    Returns (valid_list, errors).
    """
    errors = []
    if value is None:
        return None, []

    if not isinstance(value, list):
        errors.append(f"thresholds: expected list, got {type(value).__name__}")
        return None, errors

    required_levels = {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}
    found_levels = set()
    for i, t in enumerate(value):
        if not isinstance(t, dict):
            errors.append(f"thresholds[{i}]: expected dict, got {type(t).__name__}")
            continue
        for required_key in ("level", "min", "max"):
            if required_key not in t:
                errors.append(f"thresholds[{i}]: missing required key '{required_key}'")
        level = t.get("level")
        if level:
            found_levels.add(level)

    missing = required_levels - found_levels
    if missing and value:
        errors.append(f"thresholds: missing levels: {sorted(missing)}")

    return value, errors


def validate_risk_config(config):
    """Validate the full risk_config dict.

    Returns (validated_config, all_errors) where validated_config has
    malformed score maps normalized where possible and set to None where not.
    """
    all_errors = []
    validated = dict(config) if config else {}

    # Validate dimensions
    dims, errs = validate_dimensions(validated.get("dimensions"))
    validated["dimensions"] = dims
    all_errors.extend(errs)

    # Validate thresholds
    thresh, errs = validate_thresholds(validated.get("thresholds"))
    validated["thresholds"] = thresh
    all_errors.extend(errs)

    # Validate score maps
    for col in ("country_risk_scores", "sector_risk_scores", "entity_type_scores"):
        val, errs = validate_score_map(validated.get(col), col)
        validated[col] = val
        all_errors.extend(errs)

    return validated, all_errors


# ══════════════════════════════════════════════════════════
# RISK CONFIG LOADING (DB is canonical, hardcoded = fallback)
# ══════════════════════════════════════════════════════════

class RiskConfigUnavailable(Exception):
    """Raised when the live risk-scoring configuration cannot be loaded or
    fails validation in a fail-closed environment (staging/production).

    DCI-008: regulated decision paths must not silently score against the
    hardcoded default model when the approved live model in the DB is
    unavailable or malformed — the request must fail instead. Dev/test/demo
    keep the historical hardcoded fallback so local work and the pytest suite
    do not require a seeded risk_config row.
    """


def _risk_config_fail_closed():
    """True when a risk-config load failure must abort the decision path.

    Reads the environment at call time (not import time) so tests can
    exercise both postures via monkeypatched ENVIRONMENT.
    """
    from environment import get_environment
    return get_environment() in ("staging", "production")


def _parse_config_column(val):
    """Parse a risk_config column preserving the TRUE shape for validation.

    DCI-008: safe_json_loads() coerces any non-dict/list value to {} — on
    PostgreSQL, where these columns are JSONB and arrive already parsed, a
    malformed scalar (e.g. the number 5) would be silently laundered into an
    empty dict and never reach the validator, defeating the fail-closed gate
    in exactly the environments that need it. Here a malformed value is
    returned as-is (or the raw string when JSON-undecodable) so
    validate_risk_config() can flag it.
    """
    if val is None or val == "":
        return None
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val  # undecodable text — validator reports the bad shape
    if val == {} or val == []:
        # Empty container == not configured (pre-fix parity: the old
        # `if val` truthiness check mapped these to None on the PG/JSONB
        # path). Only genuinely malformed shapes should fail closed.
        return None
    return val  # JSONB already parsed: dict/list pass validation, scalars get flagged


def load_risk_config():
    """Load live risk scoring configuration from DB.

    Validates that score-mapping columns (country_risk_scores, sector_risk_scores,
    entity_type_scores) are dicts after JSON parsing, attempting normalization of
    list-of-dicts → flat dict before rejecting.

    Failure posture (DCI-008):
    - staging/production: raises RiskConfigUnavailable when the DB read fails,
      the risk_config row is missing, or validation reports errors — regulated
      decisions must never silently fall back to the hardcoded default model.
    - dev/test/demo: returns None on load failure and nulls malformed columns
      so the hardcoded fallback in the scoring functions takes over (historical
      behaviour, keeps local dev and the test suite runnable without a seeded row).
    """
    fail_closed = _risk_config_fail_closed()
    try:
        from db import get_db
        db = get_db()
        try:
            config = db.execute("SELECT * FROM risk_config WHERE id=1").fetchone()
        finally:
            try:
                db.close()
            except Exception:
                pass
        if config:
            result = {}
            for key in ("dimensions", "thresholds", "country_risk_scores",
                        "sector_risk_scores", "entity_type_scores"):
                try:
                    # DCI-008: parse WITHOUT coercing malformed values to {} so
                    # the validator sees the true shape (see _parse_config_column).
                    result[key] = _parse_config_column(config[key])
                except (KeyError, IndexError):
                    result[key] = None
            try:
                result["_config_version"] = (
                    f"risk_config:{config['updated_at']}" if config["updated_at"] else ""
                )
            except (KeyError, IndexError):
                result["_config_version"] = ""

            # ── Full schema validation with normalization ──
            validated, errors = validate_risk_config(result)
            for err in errors:
                logger.error("risk_config validation: %s", err)
            if errors and fail_closed:
                raise RiskConfigUnavailable(
                    "risk_config failed validation in a fail-closed environment: "
                    + "; ".join(str(e) for e in errors)
                )

            return validated
        if fail_closed:
            raise RiskConfigUnavailable(
                "risk_config row (id=1) is missing — the live risk model is not "
                "seeded; scoring is disabled in this environment"
            )
    except RiskConfigUnavailable:
        raise
    except Exception as e:
        if fail_closed:
            raise RiskConfigUnavailable(
                "Failed to load risk config from DB: " + str(e)
            ) from e
        logger.warning(f"Failed to load risk config from DB: {e}. Using hardcoded defaults.")
    return None


# ══════════════════════════════════════════════════════════
# SCORING FUNCTIONS
# ══════════════════════════════════════════════════════════

def classify_country(country_name, config_country_scores=None):
    """Return risk score 1-4 for a country.

    PR-CR1R: manual Risk Scoring Model settings are authoritative for pilot.
    Legacy hardcoded FATF/sanctions/low-risk lists remain fallback safeguards
    only when the manual score map has no entry.

    Handles common prefixes like "Republic of Mauritius" → "mauritius",
    and aliases like "England & Wales" → "united kingdom".
    """
    if not country_name:
        return 2
    if config_country_scores is None:
        config_country_scores = _country_scores_from_db_if_available()
    return int(country_risk_details(country_name, config_country_scores).get("risk_score") or 2)


def score_sector(sector_name, config_sector_scores=None):
    """Return risk score 1-4 for a sector. Uses DB config if provided, else hardcoded."""
    if not sector_name:
        return 2
    if mapping_fidelity_enabled():
        resolution = resolve_controlled_score(
            "sector", sector_name, configured_scores=config_sector_scores
        )
        if resolution.mapped:
            return int(resolution.score)
    s = sector_name.lower()
    # Type guard: if config is not a dict, discard it and log
    if config_sector_scores is not None and not isinstance(config_sector_scores, dict):
        logger.error(
            "score_sector received non-dict config_sector_scores: type=%s — using hardcoded SECTOR_SCORES",
            type(config_sector_scores).__name__,
        )
        config_sector_scores = None
    # DB config lookup (canonical source)
    scores = config_sector_scores if config_sector_scores else SECTOR_SCORES
    for key, score in scores.items():
        if key in s:
            return int(score)
    return 2


def _score_entity_type(entity_type_str, config_entity_scores=None):
    """Return risk score 1-4 for an entity type. Uses DB config if provided, else hardcoded."""
    if not entity_type_str:
        return 2
    if mapping_fidelity_enabled():
        resolution = resolve_controlled_score(
            "entity_type", entity_type_str, configured_scores=config_entity_scores
        )
        if resolution.mapped:
            return int(resolution.score)
    et = entity_type_str.lower()
    # Hardcoded fallback entity map
    _default_entity_map = {
        "listed": 1, "regulated fi": 1, "regulated fund": 2, "regulated": 1,
        "government": 1,
        "large private": 2, "sme": 2,
        "newly incorporated": 3, "trust": 3, "foundation": 3,
        "ngo": 3, "non-profit": 3,
        "unregulated fund": 4, "shell": 4,
    }
    # Type guard: if config is not a dict, discard it and log
    if config_entity_scores is not None and not isinstance(config_entity_scores, dict):
        logger.error(
            "_score_entity_type received non-dict config_entity_scores: type=%s — using hardcoded entity_map",
            type(config_entity_scores).__name__,
        )
        config_entity_scores = None
    scores = config_entity_scores if config_entity_scores else _default_entity_map
    for k, v in scores.items():
        if k in et:
            return int(v)
    return 2


def _controlled_mapping_evidence(data, config, country_scores, sector_scores, entity_scores):
    """Resolve only the approved Tier 0A/0B controlled families."""
    if not mapping_fidelity_enabled():
        return []

    try:
        from observability import get_request_id
        request_id = get_request_id() or data.get("request_id") or ""
    except Exception:
        request_id = data.get("request_id") or ""
    application_id = data.get("application_id") or data.get("id") or ""
    config_version = (
        (config or {}).get("_config_version")
        or (
            f"risk_config:{(config or {}).get('updated_at')}"
            if (config or {}).get("updated_at")
            else ""
        )
        or data.get("_risk_config_version")
        or REGISTRY_VERSION
    )
    values = {
        "sector": data.get("sector"),
        "entity_type": data.get("entity_type"),
        "ownership": data.get("ownership_structure"),
        "complexity": data.get("transaction_complexity") or data.get("payment_corridors"),
        "introduction": data.get("introduction_method"),
        "monthly_volume": data.get("monthly_volume") or data.get("expected_volume"),
    }
    configurable = {
        "sector": sector_scores,
        "entity_type": entity_scores,
    }
    evidence = []
    for family, raw_value in values.items():
        resolution = resolve_controlled_score(
            family,
            raw_value,
            configured_scores=configurable.get(family),
            config_version=config_version,
        )
        evidence.append(structured_mapping_evidence(
            resolution,
            application_id=application_id,
            request_id=request_id,
            config_version=config_version,
        ))

    raw_country = data.get("country")
    normalized_country = normalize_controlled_value(raw_country)
    country_resolution = None
    if not normalized_country:
        country_resolution = ControlledResolution(
            family="incorporation_country",
            raw_value=str(raw_country or ""),
            normalized_value="",
            status="unresolved",
            config_version=config_version,
        )
    elif normalized_country in COUNTRY_EXACT_ALIASES:
        canonical = COUNTRY_EXACT_ALIASES[normalized_country]
        country_resolution = ControlledResolution(
            family="incorporation_country",
            raw_value=str(raw_country or ""),
            normalized_value=normalized_country,
            status="mapped",
            score=classify_country(raw_country, country_scores),
            controlled_id=f"country_alias.{canonical.replace(' ', '_')}",
            canonical_label=canonical,
            config_key=canonical,
            config_version=config_version,
        )
    # Every other country and every region remain deferred to Tier 1B. They
    # retain pilot manual FATF treatment and do not enter Tier 0B staleness.
    if country_resolution:
        evidence.append(structured_mapping_evidence(
            country_resolution,
            application_id=application_id,
            request_id=request_id,
            config_version=config_version,
        ))
    return evidence


# ══════════════════════════════════════════════════════════
# CANONICAL RISK LEVEL CLASSIFICATION
# Single source of truth for score-to-band mapping.
# Aligned with Excel Risk Scoring Calculator v1.6:
#   Low (0–39) | Medium (40–54) | High (55–69) | Very High (70–100)
# No other code path may perform independent score-to-level mapping.
# ══════════════════════════════════════════════════════════

# Canonical hardcoded thresholds — must match DB seed and Excel
CANONICAL_THRESHOLDS = [
    {"level": "LOW", "min": 0, "max": 39.9},
    {"level": "MEDIUM", "min": 40, "max": 54.9},
    {"level": "HIGH", "min": 55, "max": 69.9},
    {"level": "VERY_HIGH", "min": 70, "max": 100},
]


def _is_elevated_jurisdiction(country_name, country_scores=None):
    """Return True if manual country settings classify the country as elevated."""
    if not country_name:
        return False
    if country_scores is None:
        country_scores = _country_scores_from_db_if_available()
    return int(country_risk_details(country_name, country_scores).get("risk_score") or 0) >= 3


def _country_triggers_very_high_floor(country_name, country_scores=None):
    """Return floor-rule decision for manually very-high or sanctioned countries."""
    if not country_name:
        return False, "", None
    if country_scores is None:
        country_scores = _country_scores_from_db_if_available()
    details = country_risk_details(country_name, country_scores)
    key = details.get("country_key") or normalize_country_key(country_name)
    score = int(details.get("risk_score") or 0)
    if key in SANCTIONED or key in FATF_BLACK:
        return True, "manual_sanctions_or_fatf_black", details
    if score >= 4:
        return True, "manual_country_score_4", details
    return False, "", details


def _is_high_risk_sector(sector_name, sector_scores=None):
    """Return True if sector scores 4 (very-high risk) or matches high-risk keywords."""
    if not sector_name:
        return False
    s = sector_name.lower()
    # Check keywords
    for kw in HIGH_RISK_SECTOR_KEYWORDS:
        if kw in s:
            return True
    # Check scored value
    actual_score = score_sector(sector_name, sector_scores)
    return actual_score >= 4


def _is_opaque_ownership(ownership_structure):
    """Return True if ownership structure is opaque, shell-like, or materially complex."""
    if not ownership_structure:
        return False
    os_val = ownership_structure.lower()
    for kw in OPAQUE_OWNERSHIP_KEYWORDS:
        if kw in os_val:
            return True
    return False


def _has_material_screening_concern(app_data):
    """Return True and a reason string if screening data indicates a material unresolved concern.

    Material concerns: serious PEP hit, adverse media, sanctions-adjacent match,
    or equivalent escalation signal requiring enhanced review.
    """
    reasons = []

    # Check adverse media
    adverse_media_data = app_data.get("adverse_media") or (
        app_data.get("screening_results", {}).get("adverse_media") if isinstance(app_data.get("screening_results"), dict) else None
    )
    if adverse_media_data:
        am_status = (adverse_media_data if isinstance(adverse_media_data, str) else
                     adverse_media_data.get("status", "") if isinstance(adverse_media_data, dict) else "").lower()
        if any(kw in am_status for kw in ("confirmed", "regulatory", "criminal", "serious", "material")):
            reasons.append("adverse_media:" + am_status)

    # Check screening results for sanctions-adjacent / unresolved PEP
    screening = app_data.get("screening_results") or {}
    if isinstance(screening, dict):
        sanctions = screening.get("sanctions") or screening.get("sanctions_screening") or {}
        if isinstance(sanctions, dict):
            s_status = (sanctions.get("status") or sanctions.get("result") or "").lower()
            if any(kw in s_status for kw in ("match", "hit", "positive", "adjacent", "unresolved")):
                reasons.append("sanctions_concern:" + s_status)

        pep = screening.get("pep") or screening.get("pep_screening") or {}
        if isinstance(pep, dict):
            p_status = (pep.get("status") or pep.get("result") or "").lower()
            if any(kw in p_status for kw in ("confirmed", "material", "serious", "high", "unresolved")):
                reasons.append("pep_concern:" + p_status)

    # Check for explicit screening_concern flag
    if app_data.get("screening_concern"):
        concern = app_data["screening_concern"]
        if isinstance(concern, str) and concern.lower() not in ("none", "clear", "no", "false", ""):
            reasons.append("screening_concern:" + concern.lower())
        elif isinstance(concern, bool) and concern:
            reasons.append("screening_concern:flagged")

    return (bool(reasons), reasons)


def classify_risk_level(composite_score, config=None):
    """
    Canonical score-to-band mapping.  ONE function, called from ONE place.
    Reads thresholds from DB config first; falls back to CANONICAL_THRESHOLDS.

    Thresholds (aligned with Excel Risk Scoring Calculator v1.6):
        LOW:       0  – 39.9
        MEDIUM:   40  – 54.9
        HIGH:     55  – 69.9
        VERY_HIGH: 70 – 100
    """
    if config and config.get("thresholds"):
        thresholds = sorted(config["thresholds"], key=lambda t: t.get("min", 0))
    else:
        thresholds = CANONICAL_THRESHOLDS

    level = "LOW"
    for t in thresholds:
        if composite_score >= t.get("min", 0):
            level = t.get("level", "LOW")
    return level


def compute_risk_score(app_data, config_override=None):
    """
    Compute composite risk score from application data.
    Reads scoring configuration from DB (canonical). Falls back to hardcoded defaults.

    Formula: composite = (weighted_avg - 1) / 3 × 100
    Thresholds: LOW 0-39, MEDIUM 40-54, HIGH 55-69, VERY_HIGH 70-100

    Returns: {
        score: float (0-100),                               # raw/floor-adjusted score; MEDIUM floors preserve raw score
        level: str (LOW|MEDIUM|HIGH|VERY_HIGH),          # final risk level (post-elevation)
        base_risk_score: float,                             # score-based risk before floor/elevation
        base_risk_level: str,                             # score-based level before elevation
        final_risk_level: str,                            # same as level (explicit alias)
        dimensions: {d1..d5},
        lane: str,
        escalations: list[str],
        elevation_reason_text: str,                       # human-readable elevation reason
        requires_compliance_approval: bool,
    }
    """
    data = safe_json_loads(app_data) if not isinstance(app_data, dict) else app_data

    # Load config from DB (or use override for testing)
    config = config_override or load_risk_config()

    # ── Extract dimension weights from config ──
    if config and config.get("dimensions"):
        dim_weights = {}
        dim_subcriteria = {}
        for dim in config["dimensions"]:
            dim_id = dim.get("id", "").upper()
            dim_weights[dim_id] = dim.get("weight", 0) / 100.0
            dim_subcriteria[dim_id] = dim.get("subcriteria", [])
        d1_weight = dim_weights.get("D1", 0.30)
        d2_weight = dim_weights.get("D2", 0.25)
        d3_weight = dim_weights.get("D3", 0.20)
        d4_weight = dim_weights.get("D4", 0.15)
        d5_weight = dim_weights.get("D5", 0.10)
    else:
        logger.warning("No dimension config from DB; using hardcoded weights.")
        d1_weight, d2_weight, d3_weight, d4_weight, d5_weight = 0.30, 0.25, 0.20, 0.15, 0.10
        dim_subcriteria = {}

    # ── Extract scoring lookups from config ──
    country_scores = (config.get("country_risk_scores") if config else None) or None
    sector_scores = (config.get("sector_risk_scores") if config else None) or None
    entity_scores = (config.get("entity_type_scores") if config else None) or None

    if not country_scores:
        logger.warning("No country_risk_scores from DB; using hardcoded FATF lists.")
    if not sector_scores:
        logger.warning("No sector_risk_scores from DB; using hardcoded SECTOR_SCORES.")
    if not entity_scores:
        logger.warning("No entity_type_scores from DB; using hardcoded entity_map.")

    # ── Extract D1 sub-factor weights from config ──
    d1_subs = dim_subcriteria.get("D1", [])
    if len(d1_subs) >= 6:
        d1_w = [s.get("weight", 0) / 100.0 for s in d1_subs]
    else:
        d1_w = [0.20, 0.20, 0.25, 0.15, 0.10, 0.10]

    # D1: Customer / Entity Risk
    owner_map = {"simple": 1, "1-2": 2, "3+": 3, "complex": 4, "opaque": 4}

    d1_entity = _score_entity_type(data.get("entity_type"), entity_scores)

    d1_owner = 2
    owner_resolution = None
    if mapping_fidelity_enabled():
        owner_resolution = resolve_controlled_score("ownership", data.get("ownership_structure"))
    if owner_resolution and owner_resolution.mapped:
        d1_owner = int(owner_resolution.score)
    else:
        os_val = (data.get("ownership_structure") or "").lower()
        for k, v in owner_map.items():
            if k in os_val:
                d1_owner = v
                break

    # D1.3 PEP Status — Gate 0 v4 uniform declared-PEP scoring.
    all_persons = data.get("directors", []) + data.get("ubos", [])
    pep_scores = []
    for p in all_persons:
        pep_evidence = _declared_pep_score_evidence(p)
        if pep_evidence:
            pep_scores.append(pep_evidence["score"])
    d1_pep = max(pep_scores) if pep_scores else 1

    # D1.4 Adverse Media / Negative News — scored from screening data (v1.6)
    adverse_media_data = data.get("adverse_media") or data.get("screening_results", {}).get("adverse_media")
    if adverse_media_data:
        am_status = (adverse_media_data if isinstance(adverse_media_data, str) else
                     adverse_media_data.get("status", "") if isinstance(adverse_media_data, dict) else "").lower()
        if any(keyword in am_status for keyword in ADVERSE_MEDIA_SCORE_4_KEYWORDS):
            d1_adverse = 4
        elif any(keyword in am_status for keyword in ADVERSE_MEDIA_SCORE_2_KEYWORDS):
            d1_adverse = 2
        elif am_status in ADVERSE_MEDIA_CLEAR_VALUES:
            d1_adverse = 1
        else:
            d1_adverse = 1  # No data = assume clear
    else:
        d1_adverse = 1  # No screening data available = assume clear

    # D1.5 Source of Wealth — scored from application data (v1.6)
    sow_val = (data.get("source_of_wealth") or "").lower()
    d1_sow = 2  # default medium if not declared
    if not sow_val or sow_val in SOURCE_OF_WEALTH_UNKNOWN_VALUES:
        d1_sow = 3  # Unknown source of wealth = high risk
    else:
        for k, v in SOURCE_OF_WEALTH_SCORE_MAP.items():
            if k in sow_val:
                d1_sow = v
                break

    # D1.6 Initial Source of Funds — scored from application data (v1.6)
    sof_val = (data.get("source_of_funds") or "").lower()
    d1_sof = 2  # default medium if not declared
    if not sof_val or sof_val in SOURCE_OF_FUNDS_UNKNOWN_VALUES:
        d1_sof = 3  # Unknown source of funds = high risk
    else:
        for k, v in SOURCE_OF_FUNDS_SCORE_MAP.items():
            if k in sof_val:
                d1_sof = v
                break

    d1 = (d1_entity * d1_w[0] + d1_owner * d1_w[1] + d1_pep * d1_w[2] +
          d1_adverse * d1_w[3] + d1_sow * d1_w[4] + d1_sof * d1_w[5])

    # ── Extract D2 sub-factor weights from config ──
    d2_subs = dim_subcriteria.get("D2", [])
    if len(d2_subs) >= 5:
        d2_w = [s.get("weight", 0) / 100.0 for s in d2_subs]
    else:
        d2_w = [0.25, 0.20, 0.20, 0.20, 0.15]

    # D2: Geographic Risk
    d2_inc = classify_country(data.get("country"), country_scores)

    # Intermediary shareholder jurisdictions
    intermediaries = data.get("intermediary_shareholders", [])
    if intermediaries:
        inter_scores = []
        for inter in intermediaries:
            j = (inter.get("jurisdiction") or "").strip()
            j_score = classify_country(j, country_scores)
            # v1.6: Boost secrecy/opacity jurisdictions to score 4 (was 3)
            if j.lower() in SECRECY_JURISDICTIONS:
                j_score = max(j_score, 4)
            inter_scores.append(j_score)
        d2_inter = max(inter_scores) if inter_scores else 1
    else:
        d2_inter = 1  # No intermediaries = low risk

    # UBO/Director nationalities
    nat_demonym_map = {
        "indian": "india", "singaporean": "singapore", "swedish": "sweden",
        "emirati": "uae", "russian": "russia", "estonian": "estonia",
        "senegalese": "senegal", "french": "france", "mauritian": "mauritius",
        "chinese": "china", "moroccan": "morocco", "nigerian": "nigeria",
        "british": "united kingdom", "american": "united states",
        "german": "germany", "japanese": "japan", "australian": "australia",
        "canadian": "canada", "lebanese": "lebanon", "iranian": "iran",
        "syrian": "syria", "afghan": "afghanistan", "belarusian": "belarus",
        "venezuelan": "venezuela", "cuban": "cuba", "north korean": "north korea",
        "pakistani": "pakistan", "south african": "south africa",
        "vietnamese": "vietnam", "filipino": "philippines",
    }
    all_persons = data.get("directors", []) + data.get("ubos", [])
    nat_scores = []
    for person in all_persons:
        nat = (person.get("nationality") or "").strip().lower()
        if nat:
            mapped = nat_demonym_map.get(nat, nat)
            nat_scores.append(classify_country(mapped, country_scores))
    d2_ubo_nat = max(nat_scores) if nat_scores else 1

    op_countries = data.get("operating_countries", [])
    d2_op = max([classify_country(c, country_scores) for c in op_countries]) if op_countries else d2_inc
    target_markets = data.get("target_markets", [])
    d2_tgt = max([classify_country(c, country_scores) for c in target_markets]) if target_markets else d2_inc
    d2 = d2_inc * d2_w[0] + d2_ubo_nat * d2_w[1] + d2_inter * d2_w[2] + d2_op * d2_w[3] + d2_tgt * d2_w[4]

    # ── Extract D3 sub-factor weights from config ──
    d3_subs = dim_subcriteria.get("D3", [])
    if len(d3_subs) >= 3:
        d3_w = [s.get("weight", 0) / 100.0 for s in d3_subs]
    else:
        d3_w = [0.40, 0.35, 0.25]

    # D3: Product / Service Risk
    # D3.1 Service risk. Flag OFF preserves the exact legacy primary-service
    # path; flag ON enforces the founder-approved maximum across all selections.
    service_selection_evidence = None
    if mapping_fidelity_enabled():
        service_selection_evidence = resolve_selected_service_risk(data, config=config)
        d3_svc = int(service_selection_evidence["final_max_score"])
    else:
        d3_svc = _legacy_primary_service_risk(data)

    # D3.2 Monthly volume — ordered checks to avoid substring false matches
    d3_vol = 2
    raw_volume = data.get("monthly_volume") or data.get("expected_volume") or ""
    volume_resolution = None
    if mapping_fidelity_enabled():
        volume_resolution = resolve_controlled_score("monthly_volume", raw_volume)
    if volume_resolution and volume_resolution.mapped:
        d3_vol = int(volume_resolution.score)
    else:
        vol = str(raw_volume).lower()
        if "over" in vol or "5,000,000" in vol or "5000000" in vol or "> 5" in vol:
            d3_vol = 4
        elif "500,000" in vol or "500000" in vol:
            d3_vol = 3
        elif "50,000" in vol or "50000" in vol:
            d3_vol = 2
        elif "under" in vol or "< 50" in vol or "below" in vol:
            d3_vol = 1

    # D3.3 Transaction Complexity & Corridors — scored from data (v1.6)
    d3_complexity = 2  # default
    raw_complexity = data.get("transaction_complexity") or data.get("payment_corridors") or ""
    complexity_resolution = None
    if mapping_fidelity_enabled():
        complexity_resolution = resolve_controlled_score("complexity", raw_complexity)
    complexity_val = str(raw_complexity).lower()
    if complexity_resolution and complexity_resolution.mapped:
        d3_complexity = int(complexity_resolution.score)
    elif "simple" in complexity_val or "single currency" in complexity_val or "domestic" in complexity_val:
        d3_complexity = 1
    elif "standard" in complexity_val or "multi-currency" in complexity_val:
        d3_complexity = 2
    elif "complex" in complexity_val or "multiple international" in complexity_val:
        d3_complexity = 3
    elif "very complex" in complexity_val or "high-risk corridor" in complexity_val:
        d3_complexity = 4
    elif data.get("cross_border") and data.get("target_markets"):
        # Infer from other fields: cross-border with multiple markets = at least standard
        tm = data.get("target_markets", [])
        high_risk_markets = sum(1 for c in tm if classify_country(c, country_scores) >= 3)
        if high_risk_markets > 0:
            d3_complexity = 4
        elif len(tm) > 2:
            d3_complexity = 3
        else:
            d3_complexity = 2

    d3 = d3_svc * d3_w[0] + d3_vol * d3_w[1] + d3_complexity * d3_w[2]

    # D4: Industry / Sector Risk
    d4 = score_sector(data.get("sector"), sector_scores)

    # ── Extract D5 sub-factor weights from config ──
    d5_subs = dim_subcriteria.get("D5", [])
    if len(d5_subs) >= 2:
        d5_w = [s.get("weight", 0) / 100.0 for s in d5_subs]
    else:
        d5_w = [0.50, 0.50]

    # D5: Delivery Channel Risk
    # D5.1 Introduction / Referral Method
    intro_map = {"direct": 1, "regulated": 1, "non-regulated": 3, "unsolicited": 4}
    d5_intro = 2
    raw_intro = data.get("introduction_method") or ""
    intro_resolution = None
    if mapping_fidelity_enabled():
        intro_resolution = resolve_controlled_score("introduction", raw_intro)
    if intro_resolution and intro_resolution.mapped:
        d5_intro = int(intro_resolution.score)
    else:
        intro = str(raw_intro).lower()
        for k, v in intro_map.items():
            if k in intro:
                d5_intro = v
                break

    # D5.2 Customer Interaction Type — scored from data (v1.6)
    d5_interaction = 2  # default = non-face-to-face low risk
    interaction_val = (data.get("customer_interaction") or data.get("interaction_type") or "").lower()
    if any(keyword in interaction_val for keyword in DELIVERY_SCORE_1_KEYWORDS):
        d5_interaction = 1
    elif any(keyword in interaction_val for keyword in DELIVERY_SCORE_2_KEYWORDS):
        d5_interaction = 2
    elif any(keyword in interaction_val for keyword in DELIVERY_REMOTE_KEYWORDS):
        # Check if high-risk jurisdiction for non-face-to-face
        inc_country = data.get("country", "")
        if classify_country(inc_country, country_scores) >= 3:
            d5_interaction = 3
        else:
            d5_interaction = 2
    elif any(keyword in interaction_val for keyword in DELIVERY_SCORE_4_KEYWORDS):
        d5_interaction = 4

    d5 = d5_intro * d5_w[0] + d5_interaction * d5_w[1]

    # Composite: weighted average on 1-4 scale, then normalize to 0-100
    weighted_avg = d1 * d1_weight + d2 * d2_weight + d3 * d3_weight + d4 * d4_weight + d5 * d5_weight
    composite = round((weighted_avg - 1) / 3 * 100, 1)

    # ── Classify risk level from score (single canonical mapping) ──
    base_score = composite
    base_level = classify_risk_level(composite, config)
    level = base_level  # will be elevated below if conditions met

    # ── Collect escalation flags ──
    escalations = []
    elevation_reasons = []

    def apply_local_floor(minimum_level, reason_code, reason_text):
        nonlocal composite, level
        minimum = str(minimum_level or "").strip().upper()
        if minimum not in RISK_RANK:
            return
        if RISK_RANK.get(level, 0) >= RISK_RANK[minimum]:
            return
        previous_level = level
        level = minimum
        composite = _score_after_floor(composite, minimum)
        _append_unique(escalations, reason_code)
        elevation_reasons.append(reason_text)
        logger.info(
            "RISK FLOOR: %s -> %s because %s (base_score=%s, final_score=%s)",
            previous_level,
            minimum,
            reason_code,
            base_score,
            composite,
        )

    # ── FLOOR RULE 1: Sanctioned / FATF_BLACK incorporation country → force VERY_HIGH ──
    # If the incorporation country is sanctioned or FATF blacklisted,
    # the overall risk level MUST be VERY_HIGH regardless of composite score.
    inc_country = normalize_country_key(data.get("country"))
    country_floor, country_floor_reason, country_floor_source = _country_triggers_very_high_floor(inc_country, country_scores)
    if inc_country and country_floor:
        if level != "VERY_HIGH":
            logger.info(
                "FLOOR RULE: Country '%s' triggers VERY_HIGH via %s — forcing VERY_HIGH (was %s, score %s)",
                inc_country, country_floor_reason, level, composite,
            )
        level = "VERY_HIGH"
        composite = max(composite, 70.0)
        escalations.append(f"floor_rule_sanctioned_country:{inc_country}")
        elevation_reasons.append(
            f"Sanctioned/FATF high-risk country: {inc_country} "
            f"({country_floor_reason}; source "
            f"{(country_floor_source or {}).get('source_name', 'manual settings')})"
        )

    # ── FLOOR RULE 2: UBO/Director sanctioned nationality → force VERY_HIGH ──
    # If any UBO or director holds nationality of a sanctioned/FATF_BLACK country,
    # the overall risk level MUST be VERY_HIGH regardless of composite score.
    for person in data.get("directors", []) + data.get("ubos", []):
        nat = (person.get("nationality") or "").strip().lower()
        if nat:
            mapped = nat_demonym_map.get(nat, nat)
            person_floor, person_floor_reason, person_floor_source = _country_triggers_very_high_floor(mapped, country_scores)
            if person_floor:
                person_name = person.get("full_name") or person.get("name") or "unknown"
                if level != "VERY_HIGH":
                    logger.info(
                        "FLOOR RULE: UBO/Director '%s' nationality '%s' maps to '%s' via %s — forcing VERY_HIGH (was %s, score %s)",
                        person_name, nat, mapped, person_floor_reason, level, composite,
                    )
                level = "VERY_HIGH"
                composite = max(composite, 70.0)
                escalations.append(f"floor_rule_sanctioned_nationality:{mapped}")
                elevation_reasons.append(
                    f"UBO/Director nationality sanctioned/FATF high-risk: {mapped} "
                    f"({person_floor_reason}; source "
                    f"{(person_floor_source or {}).get('source_name', 'manual settings')})"
                )
                break  # One match is sufficient

    # ── Extract scoring lookups for elevation checks ──
    country_scores_cfg = (config.get("country_risk_scores") if config else None) or None
    sector_scores_cfg = (config.get("sector_risk_scores") if config else None) or None

    # ── ELEVATION RULE 1: FATF grey-list + high-risk sector + opaque structure → HIGH ──
    # Narrow combination: all three conditions must be true simultaneously.
    is_grey = _is_elevated_jurisdiction(data.get("country"), country_scores_cfg)
    is_hr_sector = _is_high_risk_sector(data.get("sector"), sector_scores_cfg)
    is_opaque = _is_opaque_ownership(data.get("ownership_structure"))

    if is_grey and is_hr_sector and is_opaque:
        logger.info(
            "ELEVATION RULE 1: FATF grey-list + high-risk sector + opaque structure → HIGH "
            "(country=%s, sector=%s, ownership=%s, score=%s)",
            data.get("country"), data.get("sector"), data.get("ownership_structure"), composite
        )
        if RISK_RANK.get(level, 0) < RISK_RANK.get("HIGH", 3):
            level = "HIGH"
            composite = max(composite, RISK_SCORE_FLOORS["HIGH"])
        _append_unique(escalations, "elevation_grey_sector_opaque")
        elevation_reasons.append(
            f"Combination elevation: FATF grey-list jurisdiction ({data.get('country')}), "
            f"high-risk sector ({data.get('sector')}), opaque ownership structure"
        )

    # ── ELEVATION RULE 2: Screening-driven elevation ──
    # If screening identifies a material unresolved concern, elevate at least to HIGH.
    has_screening_concern, screening_reasons = _has_material_screening_concern(data)
    if has_screening_concern:
        if RISK_RANK.get(level, 0) < RISK_RANK.get("HIGH", 3):
            logger.info(
                "ELEVATION RULE 2: Material screening concern → at least HIGH "
                "(was %s, score=%s, reasons=%s)",
                level, composite, screening_reasons
            )
            level = "HIGH"
            composite = max(composite, RISK_SCORE_FLOORS["HIGH"])
            escalations.append("elevation_screening_concern")
            elevation_reasons.append(
                f"Screening-driven elevation to HIGH: {', '.join(screening_reasons)}"
            )

        # ── ELEVATION RULE 3: Severe combination → VERY_HIGH ──
        # High-risk sector + elevated jurisdiction + material screening concern,
        # or multiple material escalation signals together.
        is_hr_sector_severe = _is_high_risk_sector(data.get("sector"), sector_scores_cfg)
        is_elevated_jur = _is_elevated_jurisdiction(data.get("country"), country_scores_cfg)

        if (is_hr_sector_severe and is_elevated_jur and has_screening_concern) or len(screening_reasons) >= 2:
            if level != "VERY_HIGH":
                logger.info(
                    "ELEVATION RULE 3: Severe combination → VERY_HIGH "
                    "(sector_hr=%s, jurisdiction_elevated=%s, screening_reasons=%s, was %s)",
                    is_hr_sector_severe, is_elevated_jur, screening_reasons, level
                )
            level = "VERY_HIGH"
            composite = max(composite, 70.0)
            escalations.append("elevation_severe_combination")
            elevation_reasons.append(
                f"Severe-case elevation to VERY_HIGH: "
                f"{'high-risk sector + elevated jurisdiction + ' if is_hr_sector_severe and is_elevated_jur else ''}"
                f"screening concerns ({', '.join(screening_reasons)})"
            )

    # ── FLOOR RULE 3: EDD-policy trigger factors cannot remain final LOW ──
    # The routing policy sends declared PEP, high-risk sector, elevated
    # jurisdiction and opaque ownership cases into EDD. The regulator-facing
    # final risk classification must therefore not persist/display LOW while
    # those controls are required.
    if pep_scores:
        apply_local_floor(
            "HIGH",
            "floor_rule_declared_pep",
            "Declared PEP floor: declared PEP exposure requires at least HIGH final risk",
        )

    if _is_high_risk_sector(data.get("sector"), sector_scores_cfg):
        apply_local_floor(
            "HIGH",
            "floor_rule_high_risk_sector",
            f"High-risk sector floor: {data.get('sector') or 'unspecified'} requires at least HIGH final risk",
        )

    if _is_elevated_jurisdiction(data.get("country"), country_scores_cfg):
        apply_local_floor(
            "HIGH",
            "floor_rule_elevated_jurisdiction",
            f"Elevated jurisdiction floor: {data.get('country') or 'unspecified'} requires at least HIGH final risk",
        )

    if _is_opaque_ownership(data.get("ownership_structure")):
        apply_local_floor(
            "HIGH",
            "floor_rule_opaque_ownership",
            "Opaque ownership floor: opaque/complex ownership requires at least HIGH final risk",
        )

    # ── ESCALATION RULE A: score-4 evidence ──
    # Volume has an explicit compliance-review reason and no tier floor. The
    # generic reason remains for every other score-4 factor so sector, PEP,
    # ownership, and other existing behavior cannot inherit the volume rule.
    non_volume_sub_scores = [
        d1_entity, d1_owner, d1_pep, d1_adverse, d1_sow, d1_sof,
        d2_inc, d2_ubo_nat, d2_inter, d2_op, d2_tgt,
        d3_svc, d3_complexity,
        d4,
        d5_intro, d5_interaction
    ]
    if mapping_fidelity_enabled():
        if any(s >= 4 for s in non_volume_sub_scores):
            escalations.append("sub_factor_score_4")
        if (
            volume_resolution
            and volume_resolution.mapped
            and volume_resolution.controlled_id == "monthly_volume.over_usd_5m"
            and int(volume_resolution.score) == 4
        ):
            escalations.append("monthly_volume_score_4")
    elif any(s >= 4 for s in non_volume_sub_scores + [d3_vol]):
        # Flag OFF preserves the pre-Tier-0A scoring/evidence path.
        escalations.append("sub_factor_score_4")

    # ── ESCALATION RULE B: Very High Risk sector → mandatory compliance approval ──
    # Per Excel Methodology: "Business sector classified as Very High Risk"
    if d4 >= 4:
        escalations.append("very_high_risk_sector")

    # ── ESCALATION RULE C: Composite score ≥ 85 → mandatory compliance approval ──
    # Per Excel Methodology: "Overall composite score is 85 or above"
    if composite >= 85:
        escalations.append("composite_score_85_plus")

    mapping_evidence = _controlled_mapping_evidence(
        data, config, country_scores, sector_scores, entity_scores
    )
    if mapping_fidelity_enabled():
        escalations = reconcile_mapping_staleness(
            escalations,
            data.get("_existing_risk_escalations"),
            mapping_evidence,
        )
        for sentinel in (service_selection_evidence or {}).get("sentinels", []):
            _append_unique(escalations, sentinel)
    requires_compliance_approval = len(escalations) > 0

    elevation_reason_text = "; ".join(elevation_reasons) if elevation_reasons else ""
    country_risk_provenance = country_risk_details(data.get("country"), country_scores)
    def _factor_row(
        dimension_id, factor_key, factor_label, raw_value, normalized_value,
        rule_score, factor_weight, rule_identifier, evidence_source,
        resolution_status="resolved",
    ):
        return {
            "dimension_id": dimension_id,
            "factor_key": factor_key,
            "factor_label": factor_label,
            "raw_value": raw_value,
            "normalized_value": normalized_value,
            "rule_score": int(rule_score),
            "factor_weight": round(float(factor_weight) * 100, 4),
            "weighted_factor_contribution": round(
                float(rule_score) * float(factor_weight), 4
            ),
            "resolution_status": resolution_status,
            "rule_identifier": rule_identifier,
            "evidence_source": evidence_source,
        }

    def _normalized(value):
        if isinstance(value, str):
            return value.strip().lower()
        if isinstance(value, list):
            return [_normalized(item) for item in value]
        if isinstance(value, dict):
            return {str(key): _normalized(item) for key, item in value.items()}
        return value

    pep_raw = [
        pep_evidence["pep_role_type"]
        for person in all_persons
        for pep_evidence in [_declared_pep_score_evidence(person)]
        if pep_evidence
    ]
    d1_factors = [
        _factor_row("D1", "entity_type", "Entity Type", data.get("entity_type"), _normalized(data.get("entity_type")), d1_entity, d1_w[0], "entity_type_runtime_score", "rule_engine._score_entity_type"),
        _factor_row("D1", "ownership_structure", "Ownership Structure", data.get("ownership_structure"), _normalized(data.get("ownership_structure")), d1_owner, d1_w[1], "ownership_runtime_score", "rule_engine ownership resolution"),
        _factor_row("D1", "pep_status", "PEP Status", pep_raw or "No declared PEP", _normalized(pep_raw) if pep_raw else "no declared pep", d1_pep, d1_w[2], "declared_pep_runtime_score", "rule_engine._declared_pep_score_evidence"),
        _factor_row("D1", "adverse_media", "Adverse Media", adverse_media_data or "No adverse media", _normalized(adverse_media_data) if adverse_media_data else "no adverse media", d1_adverse, d1_w[3], "adverse_media_runtime_score", "rule_engine adverse-media evaluation"),
        _factor_row("D1", "source_of_wealth", "Source of Wealth", data.get("source_of_wealth"), _normalized(data.get("source_of_wealth")), d1_sow, d1_w[4], "source_of_wealth_runtime_score", "rule_engine source-of-wealth evaluation"),
        _factor_row("D1", "source_of_funds", "Source of Funds", data.get("source_of_funds"), _normalized(data.get("source_of_funds")), d1_sof, d1_w[5], "source_of_funds_runtime_score", "rule_engine source-of-funds evaluation"),
    ]
    d2_factors = [
        _factor_row("D2", "country_of_incorporation", "Country of Incorporation", data.get("country"), normalize_country_key(data.get("country")), d2_inc, d2_w[0], "country_runtime_score", "rule_engine.classify_country"),
        _factor_row("D2", "ubo_nationalities", "UBO / Director Nationalities", [person.get("nationality") for person in all_persons if person.get("nationality")], _normalized([person.get("nationality") for person in all_persons if person.get("nationality")]), d2_ubo_nat, d2_w[1], "nationality_max_runtime_score", "rule_engine.classify_country"),
        _factor_row("D2", "intermediary_jurisdictions", "Intermediary Shareholder Jurisdictions", [item.get("jurisdiction") for item in intermediaries if item.get("jurisdiction")], _normalized([item.get("jurisdiction") for item in intermediaries if item.get("jurisdiction")]), d2_inter, d2_w[2], "intermediary_jurisdiction_max_runtime_score", "rule_engine.classify_country"),
        _factor_row("D2", "countries_of_operation", "Countries of Operation", op_countries, _normalized(op_countries), d2_op, d2_w[3], "operating_country_max_runtime_score", "rule_engine.classify_country"),
        _factor_row("D2", "target_markets", "Target Markets", target_markets, _normalized(target_markets), d2_tgt, d2_w[4], "target_market_max_runtime_score", "rule_engine.classify_country"),
    ]
    selected_services = (
        (service_selection_evidence or {}).get("raw_selected_services")
        or data.get("services") or data.get("services_required") or data.get("service_type")
    )
    d3_factors = [
        _factor_row("D3", "service_type", "Service Type", selected_services, _normalized(selected_services), d3_svc, d3_w[0], "selected_service_runtime_score", "rule_engine service-risk evaluation"),
        _factor_row("D3", "monthly_volume", "Monthly Volume", raw_volume, _normalized(raw_volume), d3_vol, d3_w[1], "monthly_volume_runtime_score", "rule_engine monthly-volume evaluation"),
        _factor_row("D3", "transaction_complexity", "Transaction Complexity", raw_complexity, _normalized(raw_complexity), d3_complexity, d3_w[2], "transaction_complexity_runtime_score", "rule_engine transaction-complexity evaluation"),
    ]
    d4_factors = [
        _factor_row("D4", "industry_sector", "Industry Sector", data.get("sector"), _normalized(data.get("sector")), d4, 1.0, "sector_runtime_score", "rule_engine.score_sector"),
    ]
    raw_interaction = data.get("customer_interaction") or data.get("interaction_type") or ""
    d5_factors = [
        _factor_row("D5", "introduction_method", "Introduction Method", raw_intro, _normalized(raw_intro), d5_intro, d5_w[0], "introduction_runtime_score", "rule_engine introduction evaluation"),
        _factor_row("D5", "delivery_channel", "Delivery Channel", raw_interaction, _normalized(raw_interaction), d5_interaction, d5_w[1], "delivery_channel_runtime_score", "rule_engine delivery-channel evaluation"),
    ]
    controlled_factor_families = {
        "entity_type": "entity_type",
        "ownership_structure": "ownership",
        "country_of_incorporation": "country",
        "monthly_volume": "monthly_volume",
        "transaction_complexity": "complexity",
        "industry_sector": "sector",
        "introduction_method": "introduction",
    }
    mapping_by_family = {
        item.get("family"): item
        for item in mapping_evidence
        if isinstance(item, dict) and item.get("family")
    }
    for factor in d1_factors + d2_factors + d3_factors + d4_factors + d5_factors:
        family = controlled_factor_families.get(factor["factor_key"])
        mapping = mapping_by_family.get(family)
        if mapping:
            factor["normalized_value"] = mapping.get(
                "normalized_value", factor["normalized_value"]
            )
            factor["resolution_status"] = mapping.get(
                "resolution_status", factor["resolution_status"]
            )
        if factor["factor_key"] == "service_type" and service_selection_evidence:
            factor["normalized_value"] = service_selection_evidence.get(
                "normalized_services", factor["normalized_value"]
            )
            factor["resolution_status"] = service_selection_evidence.get(
                "resolution_status", factor["resolution_status"]
            )
    dimension_specs = [
        ("D1", d1, d1_weight, d1_factors),
        ("D2", d2, d2_weight, d2_factors),
        ("D3", d3, d3_weight, d3_factors),
        ("D4", d4, d4_weight, d4_factors),
        ("D5", d5, d5_weight, d5_factors),
    ]
    factor_evidence = []
    dimension_evidence = []
    for dimension_id, dimension_score, dimension_weight, factors in dimension_specs:
        stored_dimension_score = round(dimension_score, 2)
        factor_total = round(sum(item["weighted_factor_contribution"] for item in factors), 4)
        factor_evidence.extend(factors)
        dimension_evidence.append({
            "dimension_id": dimension_id,
            "dimension_score": stored_dimension_score,
            "dimension_weight": round(dimension_weight * 100, 4),
            "rounding_adjustment": round(stored_dimension_score - factor_total, 4),
            "composite_contribution": round(
                (dimension_score - 1) * dimension_weight / 3 * 100, 4
            ),
            "factor_keys": [item["factor_key"] for item in factors],
        })

    base_contribution_total = round(
        sum(item["composite_contribution"] for item in dimension_evidence), 4
    )
    computation_evidence = {
        "schema_version": "risk-factor-evidence-v1",
        "dimensions": dimension_evidence,
        "factors": factor_evidence,
        "base_composite_score": base_score,
        "policy_adjustment": round(composite - base_contribution_total, 4),
        "final_composite_score": composite,
    }

    risk_dimensions = {
        "d1": round(d1, 2),
        "d2": round(d2, 2),
        "d3": round(d3, 2),
        "d4": round(d4, 2),
        "d5": round(d5, 2),
        "factor_computation_evidence": computation_evidence,
    }
    if mapping_fidelity_enabled():
        risk_dimensions["controlled_mapping_evidence"] = mapping_evidence
        risk_dimensions["service_selection_evidence"] = service_selection_evidence

    return {
        "score": composite,
        "level": level,
        "base_risk_score": base_score,
        "base_risk_level": base_level,
        "final_risk_level": level,
        "dimensions": risk_dimensions,
        "lane": RISK_LANE_MAP.get(level, "Standard Review"),
        "escalations": escalations,
        "controlled_mapping_evidence": mapping_evidence,
        "service_selection_evidence": service_selection_evidence,
        "factor_computation_evidence": computation_evidence,
        "elevation_reason_text": elevation_reason_text,
        "requires_compliance_approval": requires_compliance_approval,
        "declared_pep_present": bool(pep_scores),
        "sector_label": data.get("sector") or "",
        "sector_risk_tier": _risk_tier_from_score(d4),
        "jurisdiction_risk_tier": _risk_tier_from_score(d2_inc),
        "country_risk_provenance": country_risk_provenance,
        "ownership_transparency_status": _ownership_transparency_tier(data.get("ownership_structure")),
    }


# ══════════════════════════════════════════════════════════
# EX-09: REUSABLE RISK RECOMPUTATION HELPER
# ══════════════════════════════════════════════════════════

# P10-3 / RDI-004: sentinel stamped onto applications whose recompute FAILED
# during a risk-config update. It is present and never equals a real
# `risk_config:*` version, so the decision-time staleness gate blocks approval
# of these apps (regardless of their prior — possibly NULL — provenance) until
# a successful re-score stamps the real current version.
RISK_CONFIG_VERSION_RECOMPUTE_FAILED = "stale:recompute_failed"

# P12-2 / DCI-012: sentinel PREFIX stamped onto an application IN THE SAME
# TRANSACTION as a change-request implementation that requires risk review
# (change_management.implement_change_request). The stored value is
# "<prefix>:<request_id>" — the suffix records which implementation is awaiting
# a re-score and makes concurrent implementations' sentinels distinguishable
# for the recompute persistence CAS below. It becomes durable atomically with
# the implemented change, so a post-commit recompute that fails — or never
# runs at all (process crash between commits, rule engine unavailable) — leaves
# the application quarantined behind the same decision-time staleness gate
# instead of approvable on its stale pre-change score. A successful recompute
# overwrites it with the real current config version.
RISK_CONFIG_VERSION_CM_RECOMPUTE_PENDING = "stale:cm_recompute_pending"


def _get_risk_config_version_strict(db):
    """Return the current risk-config version, raising on lookup failure.

    Returns ``None`` only when versioning is genuinely not in use (no
    ``risk_config`` row / blank ``updated_at``). A database error propagates so
    fail-closed callers (the approval staleness gate) can distinguish
    "versioning not in use" from "could not verify" and block on the latter.
    """
    row = db.execute("SELECT updated_at FROM risk_config WHERE id=1").fetchone()
    if row and row["updated_at"]:
        return f"risk_config:{row['updated_at']}"
    return None


def _get_risk_config_version(db):
    """Return the risk_config timestamp that produced the current risk result."""
    try:
        return _get_risk_config_version_strict(db)
    except Exception:
        return None


def _apply_edd_routing_floor_for_recompute(db, app, risk):
    """Apply the EDD routing floor before recomputed risk is persisted.

    Prescreening submit already floors LOW cases when deterministic routing
    sends them to EDD. Risk recomputation must do the same before writing DB
    risk fields; otherwise a later KYC submit can persist final LOW while the
    same facts still keep the application on the EDD lane.
    """
    if not isinstance(risk, dict):
        return {}
    try:
        from edd_routing_policy import evaluate_edd_routing, minimum_risk_level_for_routing
        from routing_actuator import (
            _declared_pep_present_in_party_rows,
            build_routing_facts,
        )

        facts = build_routing_facts(db=db, app_row=app, risk_dict=risk)
        try:
            app_id = dict(app or {}).get("id")
        except Exception:
            app_id = None
        if (
            not facts.get("declared_pep_present")
            and _declared_pep_present_in_party_rows(db, app_id)
        ):
            facts["declared_pep_present"] = True

        routing = dict(evaluate_edd_routing(facts) or {})
        if str(routing.get("route") or "").lower() != "edd":
            return routing

        minimum_level = minimum_risk_level_for_routing(routing) or "MEDIUM"
        triggers = ", ".join(str(t) for t in (routing.get("triggers") or []) if t)
        reason = "EDD routing floor: deterministic routing required EDD"
        if triggers:
            reason += f" ({triggers})"
        apply_risk_floor(
            risk,
            minimum_level,
            "floor_rule_edd_routing",
            reason,
        )
        risk["lane"] = "EDD"
        return routing
    except Exception as exc:
        logger.warning("EDD routing risk floor failed during recompute: %s", exc)
        return {}


_SCREENING_DISPOSITION_FLOOR_CODES = {
    "true_match",
    "material_concern",
    "escalated_to_edd",
    "needs_more_information",
}
_SCREENING_DISPOSITION_EDD_CODES = {
    "true_match",
    "material_concern",
    "needs_more_information",
    "escalated_to_edd",
}


def _row_get(row, key, default=None):
    try:
        if hasattr(row, "get"):
            return row.get(key, default)
        return row[key]
    except Exception:
        return default


def _truthy_db_flag(value):
    if value is True:
        return True
    if value in (False, None):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _screening_review_is_complete_clearance(review):
    if not review:
        return False
    code = str(_row_get(review, "disposition_code") or "").strip().lower()
    if code != "false_positive_cleared":
        return False
    if _truthy_db_flag(_row_get(review, "requires_four_eyes")):
        return bool(_row_get(review, "second_reviewer_id"))
    return True


def _screening_report_has_raw_completed_match(app, reviews=None):
    prescreening = safe_json_loads(_row_get(app, "prescreening_data"))
    report = prescreening.get("screening_report") if isinstance(prescreening, dict) else {}
    if not isinstance(report, dict):
        return False
    try:
        from screening_state import build_screening_terminality_summary

        summary = build_screening_terminality_summary(
            report,
            prescreening,
            screening_reviews=reviews or [],
        )
        return bool(summary.get("has_terminal_match"))
    except Exception as exc:
        logger.warning("Canonical screening terminality check failed during risk recompute: %s", exc)
        return False


def _latest_screening_reviews(db, app_id):
    try:
        return db.execute(
            """
            SELECT subject_type, subject_name, disposition, disposition_code,
                   requires_four_eyes, second_reviewer_id, updated_at, created_at
            FROM screening_reviews
            WHERE application_id=?
            ORDER BY updated_at DESC, created_at DESC, id DESC
            """,
            (app_id,),
        ).fetchall()
    except Exception as exc:
        logger.warning("Could not load screening reviews for risk recompute app_id=%s: %s", app_id, exc)
        return []


def _screening_disposition_floor_signal(db, app):
    """Return the current screening-disposition floor signal for recompute.

    Screening review rows are not part of the base prescreening score input.
    This helper bridges that state so formal dispositions that create/preserve
    EDD or unresolved match blocking cannot persist a final LOW classification.
    """
    app_id = _row_get(app, "id")
    reviews = _latest_screening_reviews(db, app_id)

    for review in reviews:
        code = str(_row_get(review, "disposition_code") or "").strip().lower()
        if code in _SCREENING_DISPOSITION_FLOOR_CODES:
            if code == "needs_more_information":
                return {
                    "code": code,
                    "minimum_level": "MEDIUM",
                    "reason_code": "screening_needs_more_information_floor",
                    "reason_text": (
                        "Screening disposition floor: needs_more_information keeps the match unresolved "
                        "and routes the case to EDD until formally resolved"
                    ),
                    "sets_edd_lane": True,
                }
            return {
                "code": code,
                "minimum_level": "HIGH",
                "reason_code": "material_screening_disposition_floor",
                "reason_text": (
                    "Screening disposition floor: "
                    + code
                    + " creates or preserves material screening/EDD controls and requires at least HIGH final risk"
                ),
                "sets_edd_lane": code in _SCREENING_DISPOSITION_EDD_CODES,
            }

    if _screening_report_has_raw_completed_match(app, reviews=reviews):
        cleared_reviews = [r for r in reviews if _screening_review_is_complete_clearance(r)]
        if not cleared_reviews:
            return {
                "code": "raw_completed_match",
                "minimum_level": "HIGH",
                "reason_code": "material_screening_disposition_floor",
                "reason_text": (
                    "Screening disposition floor: unresolved raw completed_match remains a material "
                    "screening concern requiring at least HIGH final risk until formally cleared"
                ),
                "sets_edd_lane": True,
            }

    return {}


def _append_floor_reason(risk, reason_code, reason_text):
    escalations = risk.get("escalations")
    if not isinstance(escalations, list):
        escalations = []
    _append_unique(escalations, reason_code)
    risk["escalations"] = escalations

    existing = str(risk.get("elevation_reason_text") or "").strip()
    reason = str(reason_text or "").strip()
    if reason and reason not in existing:
        risk["elevation_reason_text"] = f"{existing}; {reason}" if existing else reason
    elif existing:
        risk["elevation_reason_text"] = existing


def _apply_screening_disposition_floor_for_recompute(db, app, risk):
    signal = _screening_disposition_floor_signal(db, app)
    if not signal:
        return {}
    apply_risk_floor(
        risk,
        signal["minimum_level"],
        signal["reason_code"],
        signal["reason_text"],
    )
    _append_floor_reason(risk, signal["reason_code"], signal["reason_text"])
    if signal.get("sets_edd_lane"):
        risk["lane"] = "EDD"
    return signal


def _screening_floor_edd_trigger_flags(signal):
    if not isinstance(signal, dict) or not signal.get("sets_edd_lane"):
        return []
    code = str(signal.get("code") or "").strip().lower()
    if code == "needs_more_information":
        return ["screening_needs_more_information"]
    return ["material_screening_concern"]


_RISK_LEVEL_HOLD_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "VERY_HIGH": 3}


def _screening_report_is_non_terminal(report):
    """Fail-closed detector for reports that are not usable risk evidence.

    A screening run that errored, timed out, was degraded (e.g. provider
    customer-identifier conflict), or still carries unresolved subjects says
    nothing about the subject's risk — and especially does not say "zero
    hits". SRP-2 batch 1 proved the failure mode: a conflict-errored re-screen
    stored a 0-hit non-terminal report and the subsequent recompute dropped a
    HIGH application to LOW.
    """
    if not isinstance(report, dict) or not report:
        return False
    if report.get("degraded_sources"):
        return True
    if report.get("any_non_terminal_subject"):
        return True
    # Only an EXPLICIT "unknown" mode counts (the conflict-failure shape).
    # Reports that simply lack screening_mode (legacy/simulated/test shapes)
    # are judged by the explicit degradation signals above alone.
    return str(report.get("screening_mode") or "").strip().lower() == "unknown"


def _non_terminal_screening_blocks_lowering(prescreening, old_score, old_level, new_score, new_level):
    """True when a recompute would LOWER risk on non-terminal screening evidence.

    Raises are always allowed (holding a raise would itself be fail-open);
    only reductions are blocked until a terminal screening exists.
    """
    report = prescreening.get("screening_report") if isinstance(prescreening, dict) else None
    if not _screening_report_is_non_terminal(report):
        return False
    if old_score is None:
        return False
    try:
        score_drops = float(new_score) < float(old_score)
    except (TypeError, ValueError):
        score_drops = False
    old_rank = _RISK_LEVEL_HOLD_RANK.get(str(old_level or "").upper())
    new_rank = _RISK_LEVEL_HOLD_RANK.get(str(new_level or "").upper())
    level_drops = old_rank is not None and new_rank is not None and new_rank < old_rank
    return score_drops or level_drops


def recompute_risk(db, app_id, reason, user=None, log_audit_fn=None, apply_routing_policy=True):
    """Recompute risk score for a single application and persist the result.

    Args:
        db: Active database connection (caller manages commit/close).
        app_id: Application ID (primary key).
        reason: Human-readable reason for recomputation (for audit trail).
        user: Optional user dict (for audit logging).
        log_audit_fn: Optional callable(user, action, target, detail, **kwargs)
                      for audit logging. If None, audit is skipped.
        apply_routing_policy: When True (default), recomputation also runs the
                      canonical deterministic EDD routing evaluation + actuation
                      step. Callers may set this to False only when they will
                      immediately invoke the same routing helper themselves
                      after recomputation and want to avoid duplicate routing
                      audit/actuation writes.

    Returns:
        dict with keys:
            recomputed (bool): Whether risk was actually recomputed.
            old_score (float|None): Previous risk score.
            old_level (str|None): Previous risk level.
            new_score (float|None): New risk score (None if not recomputed).
            new_level (str|None): New risk level (None if not recomputed).
            changed (bool): Whether score or level changed.
    """
    from prescreening.risk_inputs import build_prescreening_risk_input

    result = {
        "recomputed": False,
        "old_score": None, "old_level": None,
        "new_score": None, "new_level": None,
        "base_risk_score": None,
        "base_risk_level": None,
        "final_risk_level": None,
        "elevation_reason_text": "",
        "risk_escalations": [],
        "changed": False,
    }

    try:
        app = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        if not app:
            logger.warning("recompute_risk: app_id=%s not found", app_id)
            return result

        # Only recompute if the app has been scored at least once
        if app.get("risk_score") is None:
            return result

        old_score = app["risk_score"]
        old_level = app["risk_level"]
        result["old_score"] = old_score
        result["old_level"] = old_level
        # Captured for the compare-and-swap persistence below: if another
        # writer changes the risk provenance while this recompute is in
        # flight (e.g. a concurrent change-request implementation stamps its
        # quarantine sentinel), this result was computed from pre-change data
        # and must not overwrite the fresher marker.
        old_provenance = app.get("risk_config_version")

        # Build scorer input from current app data plus controlled officer
        # working-value overlays. The original prescreening_data JSON remains
        # the immutable client submission.
        app_for_scoring = dict(app)
        prescreening = safe_json_loads(app["prescreening_data"])

        # Import from neutral shared module to avoid circular dependency
        # (importing from server.py would re-trigger Prometheus registration)
        from party_utils import get_application_parties
        directors, ubos, intermediaries = get_application_parties(db, app_id)
        app_for_scoring, prescreening = _apply_controlled_prescreening_correction_overlays(
            db,
            app_id,
            app_for_scoring,
            prescreening,
        )

        scoring_input = build_prescreening_risk_input(
            application=app_for_scoring,
            prescreening_data=prescreening,
            directors=directors,
            ubos=ubos,
            intermediaries=intermediaries,
        )
        new_risk = compute_risk_score(scoring_input)
        routing_floor = _apply_edd_routing_floor_for_recompute(db, app, new_risk)
        screening_floor = _apply_screening_disposition_floor_for_recompute(db, app, new_risk)

        new_score = new_risk["score"]
        new_level = new_risk["level"]
        result["new_score"] = new_score
        result["new_level"] = new_level
        result["base_risk_score"] = new_risk.get("base_risk_score")
        result["base_risk_level"] = new_risk.get("base_risk_level")
        result["final_risk_level"] = new_risk.get("final_risk_level", new_level)
        result["elevation_reason_text"] = new_risk.get("elevation_reason_text", "")
        result["risk_escalations"] = list(new_risk.get("escalations", []))
        result["edd_routing_route"] = routing_floor.get("route")
        result["edd_routing_triggers"] = list(routing_floor.get("triggers") or [])
        result["screening_disposition_floor"] = screening_floor
        # Fail-closed hold (SRP-2 batch-1 finding): never LOWER risk off a
        # non-terminal/degraded screening report. The recompute result is
        # discarded, the stored risk stands, and the hold is audited. Raises
        # still go through.
        if _non_terminal_screening_blocks_lowering(prescreening, old_score, old_level, new_score, new_level):
            result["recomputed"] = False
            result["held_non_terminal_screening"] = True
            logger.warning(
                "recompute_risk: held app_id=%s — screening report non-terminal/degraded; "
                "refusing to lower risk %s/%s -> computed %s/%s",
                app_id, old_score, old_level, new_score, new_level,
            )
            if log_audit_fn and user:
                log_audit_fn(
                    user,
                    "Risk Recompute Held",
                    app.get("ref") or app_id,
                    (
                        f"Fail-closed: screening report is non-terminal/degraded; risk held at "
                        f"{old_score} {old_level} (recompute produced {new_score} {new_level}; "
                        f"trigger: {reason}). A terminal screening is required before risk can decrease."
                    ),
                    db=db,
                    commit=False,
                )
            return result

        result["recomputed"] = True
        result["changed"] = (old_score != new_score or old_level != new_level)

        # Get risk config version
        config_version = _get_risk_config_version(db)
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Compare-and-swap on the provenance read at the start of this
        # recompute: a concurrent writer (another recompute, or a
        # change-request implementation stamping its per-request quarantine
        # sentinel) means this result was computed from stale reads and must
        # NOT overwrite the fresher provenance. 0 rows matched -> report
        # failure so callers quarantine/log instead of trusting a phantom
        # success; a later recompute (run against the current data) clears it.
        cur = db.execute(
            """UPDATE applications SET
                risk_score=?, risk_level=?, risk_dimensions=?, onboarding_lane=?,
                risk_computed_at=?, risk_config_version=?,
                risk_escalations=?,
                base_risk_level=?, final_risk_level=?, elevation_reason_text=?,
                updated_at=datetime('now')
                WHERE id=? AND COALESCE(risk_config_version,'') = COALESCE(?,'')""",
            (new_score, new_level,
             json.dumps(new_risk.get("dimensions", {})),
             new_risk.get("lane", "Standard Review"),
             now_ts, config_version,
             json.dumps(new_risk.get("escalations", [])),
             new_risk.get("base_risk_level", new_level),
             new_risk.get("final_risk_level", new_level),
             new_risk.get("elevation_reason_text", ""),
             app_id, old_provenance))
        rowcount = getattr(cur, "rowcount", None)
        if rowcount is None:
            # DBConnection.execute returns the wrapper; the count lives on
            # the underlying cursor (established repo pattern).
            rowcount = getattr(getattr(db, "_cursor", None), "rowcount", None)
        if rowcount == 0:
            logger.error(
                "recompute_risk: risk provenance for app_id=%s changed "
                "concurrently (was %r) — not persisting a score computed from "
                "pre-change data; a re-score against current data is required.",
                app_id, old_provenance)
            result["recomputed"] = False
            result["changed"] = False
            result["persist_conflict"] = True
            return result

        if result["changed"]:
            logger.info(
                "RISK RECOMPUTED: app_id=%s reason=%s score %s→%s, level %s→%s",
                app_id, reason, old_score, new_score, old_level, new_level)
        else:
            logger.info(
                "RISK RECOMPUTED (no change): app_id=%s reason=%s score=%s level=%s",
                app_id, reason, new_score, new_level)

        # Audit trail
        if log_audit_fn and user:
            before_state = {
                "risk_score": old_score,
                "risk_level": old_level,
                "final_risk_level": app.get("final_risk_level") or old_level,
                "base_risk_level": app.get("base_risk_level") or old_level,
                "elevation_reason_text": app.get("elevation_reason_text") or "",
            }
            after_state = {
                "risk_score": new_score,
                "base_risk_score": new_risk.get("base_risk_score"),
                "base_risk_level": new_risk.get("base_risk_level", new_level),
                "risk_level": new_level,
                "final_risk_level": new_risk.get("final_risk_level", new_level),
                "risk_escalations": new_risk.get("escalations", []),
                "elevation_reason_text": new_risk.get("elevation_reason_text", ""),
                "risk_computed_at": now_ts,
                "risk_config_version": config_version,
            }
            floor_detail = (
                f". Floor/elevation reason: {new_risk.get('elevation_reason_text')}"
                if new_risk.get("elevation_reason_text")
                else ""
            )
            detail = (
                f"Reason: {reason}. "
                f"Score: {old_score}→{new_score}, Level: {old_level}→{new_level}"
                f"{floor_detail}"
            )
            try:
                log_audit_fn(user, "Risk Recomputed", app.get("ref", app_id), detail,
                             db=db, before_state=before_state, after_state=after_state)
            except Exception as e:
                logger.warning("recompute_risk audit log failed: %s", e)


        # Priority E: re-run EDD routing policy after every recompute unless a
        # caller explicitly owns the routing step and will execute it
        # immediately afterwards using the same established helper.
        if apply_routing_policy:
            try:
                from routing_actuator import (
                    apply_routing_decision,
                    SOURCE_RISK_RECOMPUTE,
                )
                try:
                    _app_post = db.execute(
                        "SELECT * FROM applications WHERE id = ?", (app_id,)
                    ).fetchone()
                except Exception:
                    _app_post = app
                _risk_dict = dict(new_risk)
                _risk_dict.setdefault("score", new_score)
                _risk_dict.setdefault("level", new_level)
                _risk_dict.setdefault("final_risk_level", new_level)
                _risk_dict.setdefault("base_risk_level", new_risk.get("base_risk_level", new_level))
                _risk_dict.setdefault("sector_label", (dict(_app_post or {})).get("sector"))
                apply_routing_decision(
                    db=db,
                    app_row=(dict(_app_post) if _app_post else app),
                    risk_dict=_risk_dict,
                    edd_trigger_flags=_screening_floor_edd_trigger_flags(screening_floor),
                    user=user,
                    client_ip="",
                    source=SOURCE_RISK_RECOMPUTE,
                )
            except Exception as _re_err:
                logger.warning(
                    "apply_routing_decision (recompute) failed for app_id=%s: %s",
                    app_id, _re_err,
                )

    except RiskConfigUnavailable:
        # DCI-008: a fail-closed risk-config condition must never degrade into
        # a silent {"recomputed": False} no-op — the caller's decision path
        # (edit, KYC submit, screening disposition, periodic review) would
        # proceed with the STALE stored risk level while returning success.
        raise
    except Exception as e:
        logger.warning("recompute_risk failed for app_id=%s: %s", app_id, e)

    return result


def recompute_risk_for_active_apps(db, reason, user=None, log_audit_fn=None):
    """Recompute risk for all non-terminal applications.

    Used when risk config changes — all active apps need rescoring against new config.
    Terminal statuses (approved, rejected, withdrawn) are excluded.

    Returns:
        list of dicts — one per recomputed application.
    """
    TERMINAL_STATUSES = ("approved", "rejected", "withdrawn")
    try:
        rows = db.execute(
            "SELECT id FROM applications WHERE risk_score IS NOT NULL AND status NOT IN (?,?,?)",
            TERMINAL_STATUSES
        ).fetchall()
    except Exception as e:
        logger.warning("recompute_risk_for_active_apps: failed to list apps: %s", e)
        return []

    results = []
    for row in rows:
        r = recompute_risk(db, row["id"], reason, user=user, log_audit_fn=log_audit_fn)
        results.append({"app_id": row["id"], **r})
        if not r.get("recomputed"):
            # P10-3 / RDI-004: quarantine the app. A failed recompute leaves the
            # stored score computed under the OLD config; stamping the sentinel
            # (present, never equal to a real version) makes the decision-time
            # staleness gate block approval even when the prior provenance was
            # NULL/blank. Same transaction as the config save — committed (or
            # rolled back) together. A successful later re-score overwrites it.
            try:
                db.execute(
                    "UPDATE applications SET risk_config_version=?, updated_at=datetime('now') WHERE id=?",
                    (RISK_CONFIG_VERSION_RECOMPUTE_FAILED, row["id"]),
                )
            except Exception as quarantine_err:
                logger.error(
                    "recompute_risk_for_active_apps: failed to quarantine app_id=%s "
                    "after recompute failure: %s", row["id"], quarantine_err)

    changed_count = sum(1 for r in results if r.get("changed"))
    failed_count = sum(1 for r in results if not r.get("recomputed"))
    logger.info(
        "Bulk risk recomputation: reason=%s, apps=%d, changed=%d, failed(quarantined)=%d",
        reason, len(results), changed_count, failed_count)

    return results
