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
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("arie")


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


# ══════════════════════════════════════════════════════════
# COUNTRY RISK CLASSIFICATION
# ══════════════════════════════════════════════════════════

# v1.6: Country lists updated to match ARIE_Risk_Score_Sheet v1.6 (80 countries)
# Grey list = score 3 (FATF monitored jurisdictions)
FATF_GREY = {"algeria", "burkina faso", "cameroon", "democratic republic of congo",
             "haiti", "kenya", "laos", "lebanon", "mali", "monaco", "mozambique",
             "nigeria", "philippines", "senegal", "south africa", "south sudan",
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

def load_risk_config():
    """Load live risk scoring configuration from DB. Falls back to None if DB unavailable.

    Validates that score-mapping columns (country_risk_scores, sector_risk_scores,
    entity_type_scores) are dicts after JSON parsing.  If any column is malformed
    (e.g. stored as a list or scalar), it is logged and set to None so that the
    hardcoded fallback in the scoring functions takes over.

    Attempts normalization of list-of-dicts → flat dict before rejecting.
    """
    try:
        from db import get_db
        db = get_db()
        config = db.execute("SELECT * FROM risk_config WHERE id=1").fetchone()
        db.close()
        if config:
            result = {}
            for key in ("dimensions", "thresholds", "country_risk_scores",
                        "sector_risk_scores", "entity_type_scores"):
                try:
                    val = config[key]
                    result[key] = safe_json_loads(val) if val else None
                except (KeyError, IndexError):
                    result[key] = None

            # ── Full schema validation with normalization ──
            validated, errors = validate_risk_config(result)
            for err in errors:
                logger.error("risk_config validation: %s", err)

            return validated
    except Exception as e:
        logger.warning(f"Failed to load risk config from DB: {e}. Using hardcoded defaults.")
    return None


# ══════════════════════════════════════════════════════════
# SCORING FUNCTIONS
# ══════════════════════════════════════════════════════════

def classify_country(country_name, config_country_scores=None):
    """Return risk score 1-4 for a country. Uses DB config if provided, else hardcoded FATF lists.

    Handles common prefixes like "Republic of Mauritius" → "mauritius",
    and aliases like "England & Wales" → "united kingdom".
    """
    if not country_name:
        return 2
    c = country_name.lower().strip()

    # Apply known aliases first
    _ALIASES = {
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
    }
    c = _ALIASES.get(c, c)

    # Type guard: if config is not a dict, discard it and log
    if config_country_scores is not None and not isinstance(config_country_scores, dict):
        logger.error(
            "classify_country received non-dict config_country_scores: type=%s — using hardcoded FATF lists",
            type(config_country_scores).__name__,
        )
        config_country_scores = None

    def _lookup(name):
        """Check name against all risk lists. Returns score or None."""
        if config_country_scores:
            score = config_country_scores.get(name)
            if score is not None:
                return int(score)
        if name in SANCTIONED:
            return 4
        if name in FATF_BLACK:
            return 4
        if name in FATF_GREY:
            return 3
        if name in LOW_RISK:
            return 1
        return None

    # Try exact match first (handles "democratic republic of congo" etc.)
    result = _lookup(c)
    if result is not None:
        return result

    # Strip common country prefixes and retry
    for prefix in ("republic of ", "state of ", "the ", "federation of "):
        if c.startswith(prefix) and len(c) > len(prefix):
            stripped = c[len(prefix):].strip()
            result = _lookup(stripped)
            if result is not None:
                return result

    return 2  # standard


def score_sector(sector_name, config_sector_scores=None):
    """Return risk score 1-4 for a sector. Uses DB config if provided, else hardcoded."""
    if not sector_name:
        return 2
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
        score: float (0-100),
        level: str (LOW|MEDIUM|HIGH|VERY_HIGH),
        dimensions: {d1..d5},
        lane: str,
        escalations: list[str],
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
    owner_map = {"simple": 1, "1-2": 2, "3+": 3, "complex": 4}

    d1_entity = _score_entity_type(data.get("entity_type"), entity_scores)

    d1_owner = 2
    os_val = (data.get("ownership_structure") or "").lower()
    for k, v in owner_map.items():
        if k in os_val:
            d1_owner = v
            break

    # D1.3 PEP Status — 3-tier scoring (v1.6)
    all_persons = data.get("directors", []) + data.get("ubos", [])
    pep_scores = []
    for p in all_persons:
        if p.get("is_pep") == "Yes":
            pep_type = (p.get("pep_type") or p.get("pep_category") or "").lower()
            if "foreign" in pep_type or "international" in pep_type:
                pep_scores.append(4)
            else:
                # Domestic PEP or close associate = 3
                pep_scores.append(3)
    d1_pep = max(pep_scores) if pep_scores else 1

    # D1.4 Adverse Media / Negative News — scored from screening data (v1.6)
    adverse_media_data = data.get("adverse_media") or data.get("screening_results", {}).get("adverse_media")
    if adverse_media_data:
        am_status = (adverse_media_data if isinstance(adverse_media_data, str) else
                     adverse_media_data.get("status", "") if isinstance(adverse_media_data, dict) else "").lower()
        if "confirmed" in am_status or "regulatory" in am_status or "criminal" in am_status:
            d1_adverse = 4
        elif "minor" in am_status or "unsubstantiated" in am_status:
            d1_adverse = 2
        elif am_status in ("clear", "none", "no"):
            d1_adverse = 1
        else:
            d1_adverse = 1  # No data = assume clear
    else:
        d1_adverse = 1  # No screening data available = assume clear

    # D1.5 Source of Wealth — scored from application data (v1.6)
    _sow_map = {
        "business revenue": 1, "trading profits": 1, "investment": 1, "dividends": 1,
        "government funding": 1, "grants": 1,
        "sale of assets": 2, "property": 2, "venture capital": 2, "investor funding": 2,
        "inheritance": 3, "family wealth": 3, "loan": 3, "credit": 3, "other": 3,
    }
    sow_val = (data.get("source_of_wealth") or "").lower()
    d1_sow = 2  # default medium if not declared
    if not sow_val or sow_val in ("information not provided", "not provided", "unknown"):
        d1_sow = 3  # Unknown source of wealth = high risk
    else:
        for k, v in _sow_map.items():
            if k in sow_val:
                d1_sow = v
                break

    # D1.6 Initial Source of Funds — scored from application data (v1.6)
    _sof_map = {
        "company bank": 1, "parent company": 1, "group entity": 1, "client payments": 1,
        "receivables": 1, "revenue": 1, "business operations": 1,
        "shareholder": 2, "director": 2, "capital injection": 2, "investment round": 2,
        "fundraise": 2, "sale of assets": 2,
        "loan": 3, "credit facility": 3, "other": 3,
    }
    sof_val = (data.get("source_of_funds") or "").lower()
    d1_sof = 2  # default medium if not declared
    if not sof_val or sof_val in ("information not provided", "not provided", "unknown"):
        d1_sof = 3  # Unknown source of funds = high risk
    else:
        for k, v in _sof_map.items():
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
    # D3.1 Primary service
    d3_svc = 2  # default
    svc_val = (data.get("primary_service") or data.get("service_required") or "").lower()
    if "domestic" in svc_val and "single" in svc_val:
        d3_svc = 1
    elif "multi-currency" in svc_val or "multi currency" in svc_val:
        d3_svc = 2
    elif "cross-border" in svc_val or "international" in svc_val or data.get("cross_border"):
        d3_svc = 3
    elif data.get("cross_border"):
        d3_svc = 3

    # D3.2 Monthly volume — ordered checks to avoid substring false matches
    d3_vol = 2
    vol = (data.get("monthly_volume") or data.get("expected_volume") or "").lower()
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
    complexity_val = (data.get("transaction_complexity") or data.get("payment_corridors") or "").lower()
    if "simple" in complexity_val or "single currency" in complexity_val or "domestic" in complexity_val:
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
    intro = (data.get("introduction_method") or "").lower()
    for k, v in intro_map.items():
        if k in intro:
            d5_intro = v
            break

    # D5.2 Customer Interaction Type — scored from data (v1.6)
    d5_interaction = 2  # default = non-face-to-face low risk
    interaction_val = (data.get("customer_interaction") or data.get("interaction_type") or "").lower()
    if "face-to-face" in interaction_val or "in-person" in interaction_val or "in person" in interaction_val:
        d5_interaction = 1
    elif "video" in interaction_val:
        d5_interaction = 2
    elif "non-face" in interaction_val or "remote" in interaction_val:
        # Check if high-risk jurisdiction for non-face-to-face
        inc_country = data.get("country", "")
        if classify_country(inc_country, country_scores) >= 3:
            d5_interaction = 3
        else:
            d5_interaction = 2
    elif "anonymous" in interaction_val or "unverified" in interaction_val:
        d5_interaction = 4

    d5 = d5_intro * d5_w[0] + d5_interaction * d5_w[1]

    # Composite: weighted average on 1-4 scale, then normalize to 0-100
    weighted_avg = d1 * d1_weight + d2 * d2_weight + d3 * d3_weight + d4 * d4_weight + d5 * d5_weight
    composite = round((weighted_avg - 1) / 3 * 100, 1)

    # ── Classify risk level from score (single canonical mapping) ──
    level = classify_risk_level(composite, config)

    # ── Collect escalation flags ──
    escalations = []

    # ── FLOOR RULE 1: Sanctioned / FATF_BLACK incorporation country → force VERY_HIGH ──
    # If the incorporation country is sanctioned or FATF blacklisted,
    # the overall risk level MUST be VERY_HIGH regardless of composite score.
    inc_country = (data.get("country") or "").lower().strip()
    if inc_country and (inc_country in SANCTIONED or inc_country in FATF_BLACK):
        if level != "VERY_HIGH":
            logger.info(f"FLOOR RULE: Country '{inc_country}' is sanctioned/FATF_BLACK — forcing VERY_HIGH (was {level}, score {composite})")
        level = "VERY_HIGH"
        composite = max(composite, 70.0)
        escalations.append(f"floor_rule_sanctioned_country:{inc_country}")

    # ── FLOOR RULE 2: UBO/Director sanctioned nationality → force VERY_HIGH ──
    # If any UBO or director holds nationality of a sanctioned/FATF_BLACK country,
    # the overall risk level MUST be VERY_HIGH regardless of composite score.
    sanctioned_set = SANCTIONED | FATF_BLACK
    for person in data.get("directors", []) + data.get("ubos", []):
        nat = (person.get("nationality") or "").strip().lower()
        if nat:
            mapped = nat_demonym_map.get(nat, nat)
            if mapped in sanctioned_set:
                person_name = person.get("full_name") or person.get("name") or "unknown"
                if level != "VERY_HIGH":
                    logger.info(f"FLOOR RULE: UBO/Director '{person_name}' nationality '{nat}' maps to sanctioned '{mapped}' — forcing VERY_HIGH (was {level}, score {composite})")
                level = "VERY_HIGH"
                composite = max(composite, 70.0)
                escalations.append(f"floor_rule_sanctioned_nationality:{mapped}")
                break  # One match is sufficient

    # ── ESCALATION RULE A: Any sub-factor scores 4 → mandatory compliance approval ──
    # Per Excel Methodology: "Compliance approval is MANDATORY when any individual
    # sub-factor scores 4 (Very High Risk)"
    all_sub_scores = [
        d1_entity, d1_owner, d1_pep, d1_adverse, d1_sow, d1_sof,
        d2_inc, d2_ubo_nat, d2_inter, d2_op, d2_tgt,
        d3_svc, d3_vol, d3_complexity,
        d4,
        d5_intro, d5_interaction
    ]
    if any(s >= 4 for s in all_sub_scores):
        escalations.append("sub_factor_score_4")

    # ── ESCALATION RULE B: Very High Risk sector → mandatory compliance approval ──
    # Per Excel Methodology: "Business sector classified as Very High Risk"
    if d4 >= 4:
        escalations.append("very_high_risk_sector")

    # ── ESCALATION RULE C: Composite score ≥ 85 → mandatory compliance approval ──
    # Per Excel Methodology: "Overall composite score is 85 or above"
    if composite >= 85:
        escalations.append("composite_score_85_plus")

    requires_compliance_approval = len(escalations) > 0

    lane_map = {"LOW": "Fast Lane", "MEDIUM": "Standard Review", "HIGH": "EDD", "VERY_HIGH": "EDD"}

    return {
        "score": composite,
        "level": level,
        "dimensions": {"d1": round(d1, 2), "d2": round(d2, 2), "d3": round(d3, 2), "d4": round(d4, 2), "d5": round(d5, 2)},
        "lane": lane_map.get(level, "Standard Review"),
        "escalations": escalations,
        "requires_compliance_approval": requires_compliance_approval,
    }


# ══════════════════════════════════════════════════════════
# EX-09: REUSABLE RISK RECOMPUTATION HELPER
# ══════════════════════════════════════════════════════════

def _get_risk_config_version(db):
    """Return the risk_config updated_at timestamp as a version identifier."""
    try:
        row = db.execute("SELECT updated_at FROM risk_config WHERE id=1").fetchone()
        if row and row["updated_at"]:
            return str(row["updated_at"])
    except Exception:
        pass
    return None


def recompute_risk(db, app_id, reason, user=None, log_audit_fn=None):
    """Recompute risk score for a single application and persist the result.

    Args:
        db: Active database connection (caller manages commit/close).
        app_id: Application ID (primary key).
        reason: Human-readable reason for recomputation (for audit trail).
        user: Optional user dict (for audit logging).
        log_audit_fn: Optional callable(user, action, target, detail, **kwargs)
                      for audit logging. If None, audit is skipped.

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

        # Build scorer input from current app data
        prescreening = safe_json_loads(app["prescreening_data"])

        # Lazy import to avoid circular dependency
        try:
            from server import get_application_parties
            directors, ubos, intermediaries = get_application_parties(db, app_id)
        except ImportError:
            directors = [dict(d) for d in db.execute(
                "SELECT * FROM directors WHERE application_id=?", (app_id,)).fetchall()]
            ubos = [dict(u) for u in db.execute(
                "SELECT * FROM ubos WHERE application_id=?", (app_id,)).fetchall()]
            intermediaries = []
            for row in db.execute(
                    "SELECT * FROM intermediaries WHERE application_id=?", (app_id,)).fetchall():
                item = dict(row)
                item["full_name"] = item.get("entity_name", "")
                intermediaries.append(item)

        scoring_input = build_prescreening_risk_input(
            application=app,
            prescreening_data=prescreening,
            directors=directors,
            ubos=ubos,
            intermediaries=intermediaries,
        )
        new_risk = compute_risk_score(scoring_input)

        new_score = new_risk["score"]
        new_level = new_risk["level"]
        result["new_score"] = new_score
        result["new_level"] = new_level
        result["recomputed"] = True
        result["changed"] = (old_score != new_score or old_level != new_level)

        # Get risk config version
        config_version = _get_risk_config_version(db)
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        db.execute(
            """UPDATE applications SET
                risk_score=?, risk_level=?, risk_dimensions=?, onboarding_lane=?,
                risk_computed_at=?, risk_config_version=?,
                risk_escalations=?,
                updated_at=datetime('now') WHERE id=?""",
            (new_score, new_level,
             json.dumps(new_risk.get("dimensions", {})),
             new_risk.get("lane", "Standard Review"),
             now_ts, config_version,
             json.dumps(new_risk.get("escalations", [])),
             app_id))

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
                "risk_score": old_score, "risk_level": old_level,
            }
            after_state = {
                "risk_score": new_score, "risk_level": new_level,
                "risk_computed_at": now_ts,
                "risk_config_version": config_version,
            }
            detail = (
                f"Reason: {reason}. "
                f"Score: {old_score}→{new_score}, Level: {old_level}→{new_level}"
            )
            try:
                log_audit_fn(user, "Risk Recomputed", app.get("ref", app_id), detail,
                             db=db, before_state=before_state, after_state=after_state)
            except Exception as e:
                logger.warning("recompute_risk audit log failed: %s", e)

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

    changed_count = sum(1 for r in results if r.get("changed"))
    logger.info(
        "Bulk risk recomputation: reason=%s, apps=%d, changed=%d",
        reason, len(results), changed_count)

    return results
