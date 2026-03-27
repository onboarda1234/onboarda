"""
ARIE Finance — Rule Engine: Risk Scoring, Country/Sector Classification, Rules 4A-4E
Extracted from server.py during Sprint 2 monolith decomposition.

Provides:
    - Country risk classification (FATF grey/black, sanctioned, low-risk)
    - Sector risk scoring
    - Composite risk score computation (D1-D5 dimensions)
    - Rule 4A-4E constants for pre-generation enforcement
    - Risk aggregation weights and ranks
"""
import json
import logging

logger = logging.getLogger("arie")


# ══════════════════════════════════════════════════════════
# COUNTRY RISK CLASSIFICATION
# ══════════════════════════════════════════════════════════

FATF_GREY = {"syria", "myanmar", "iran", "north korea", "yemen", "haiti", "south sudan",
             "nigeria", "south africa", "kenya", "philippines", "tanzania", "mozambique",
             "democratic republic of congo", "cameroon", "burkina faso", "mali", "senegal"}

FATF_BLACK = {"iran", "north korea", "myanmar"}

SANCTIONED = {"iran", "north korea", "syria", "cuba", "crimea"}

SANCTIONED_COUNTRIES_FULL = {"iran", "north korea", "syria", "cuba", "crimea", "myanmar", "russia", "belarus",
                              "venezuela", "afghanistan", "somalia", "yemen", "libya", "iraq", "south sudan",
                              "central african republic", "democratic republic of congo", "mali",
                              "guinea-bissau", "lebanon"}

ALLOWED_CURRENCIES = {"USD", "EUR", "GBP", "AED"}

LOW_RISK = {"mauritius", "united kingdom", "uk", "france", "germany", "sweden", "norway",
            "denmark", "finland", "australia", "new zealand", "canada", "usa", "united states",
            "japan", "singapore", "hong kong", "switzerland", "netherlands", "belgium", "luxembourg",
            "ireland", "austria", "portugal", "spain", "italy"}


# ══════════════════════════════════════════════════════════
# SECTOR RISK SCORING
# ══════════════════════════════════════════════════════════

SECTOR_SCORES = {
    "regulated financial": 1, "government": 1, "bank": 1, "listed company": 1,
    "healthcare": 2, "technology": 2, "software": 2, "saas": 2, "manufacturing": 2,
    "retail": 2, "e-commerce": 2, "education": 2, "media": 2, "logistics": 2,
    "import": 3, "export": 3, "real estate": 3, "construction": 3, "mining": 3,
    "oil": 3, "gas": 3, "money services": 3, "forex": 3, "precious": 3,
    "non-profit": 3, "ngo": 3, "charity": 3, "advisory": 3,
    "management consulting": 3, "consulting": 3, "financial / tax advisory": 3,
    "crypto": 4, "virtual asset": 4, "gambling": 4, "gaming": 4, "betting": 4,
    "arms": 4, "defence": 4, "military": 4, "shell company": 4, "nominee": 4
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
# SCORING FUNCTIONS
# ══════════════════════════════════════════════════════════

def classify_country(country_name):
    """Return risk score 1-4 for a country."""
    if not country_name:
        return 2
    c = country_name.lower().strip()
    if c in SANCTIONED:
        return 4
    if c in FATF_BLACK:
        return 4
    if c in FATF_GREY:
        return 3
    if c in LOW_RISK:
        return 1
    return 2  # standard


def score_sector(sector_name):
    """Return risk score 1-4 for a sector."""
    if not sector_name:
        return 2
    s = sector_name.lower()
    for key, score in SECTOR_SCORES.items():
        if key in s:
            return score
    return 2


def compute_risk_score(app_data):
    """
    Compute composite risk score from application data.
    Returns: { score, level, dimensions: {d1..d5}, lane }
    """
    data = app_data if isinstance(app_data, dict) else json.loads(app_data)

    # D1: Customer / Entity Risk (30%)
    entity_map = {"listed": 1, "regulated fund": 2, "regulated": 1, "government": 1,
                  "large private": 2, "sme": 2,
                  "newly incorporated": 3, "trust": 3, "foundation": 3, "non-profit": 3,
                  "unregulated fund": 4, "shell": 4}
    owner_map = {"simple": 1, "1-2": 2, "3+": 3, "complex": 4}

    d1_entity = 2
    et = (data.get("entity_type") or "").lower()
    for k, v in entity_map.items():
        if k in et:
            d1_entity = v
            break

    d1_owner = 2
    os_val = (data.get("ownership_structure") or "").lower()
    for k, v in owner_map.items():
        if k in os_val:
            d1_owner = v
            break

    has_pep = any(d.get("is_pep") == "Yes" for d in data.get("directors", []))
    has_pep = has_pep or any(u.get("is_pep") == "Yes" for u in data.get("ubos", []))
    d1_pep = 3 if has_pep else 1

    d1 = d1_entity * 0.20 + d1_owner * 0.20 + d1_pep * 0.25 + 1 * 0.15 + 2 * 0.10 + 2 * 0.10

    # D2: Geographic Risk (25%)
    d2_inc = classify_country(data.get("country"))

    # Intermediary shareholder jurisdictions
    intermediaries = data.get("intermediary_shareholders", [])
    secrecy_jurisdictions = {"bvi", "cayman islands", "panama", "seychelles", "bermuda",
                             "jersey", "guernsey", "isle of man", "liechtenstein",
                             "vanuatu", "samoa", "marshall islands"}
    if intermediaries:
        inter_scores = []
        for inter in intermediaries:
            j = (inter.get("jurisdiction") or "").strip()
            j_score = classify_country(j)
            # Boost secrecy/opacity jurisdictions to at least 3
            if j.lower() in secrecy_jurisdictions:
                j_score = max(j_score, 3)
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
        nat = (person.get("nationality") or person.get("nat") or "").strip().lower()
        if nat:
            mapped = nat_demonym_map.get(nat, nat)
            nat_scores.append(classify_country(mapped))
    d2_ubo_nat = max(nat_scores) if nat_scores else 1

    op_countries = data.get("operating_countries", [])
    d2_op = max([classify_country(c) for c in op_countries]) if op_countries else d2_inc
    target_markets = data.get("target_markets", [])
    d2_tgt = max([classify_country(c) for c in target_markets]) if target_markets else d2_inc
    d2 = d2_inc * 0.25 + d2_ubo_nat * 0.20 + d2_inter * 0.20 + d2_op * 0.20 + d2_tgt * 0.15

    # D3: Product / Service Risk (20%)
    vol_map = {"under": 1, "50,000": 2, "500,000": 3, "over": 4}
    d3_svc = 2  # default
    if data.get("cross_border"):
        d3_svc = 3
    d3_vol = 2
    vol = (data.get("monthly_volume") or "").lower()
    for k, v in vol_map.items():
        if k in vol:
            d3_vol = v
            break
    d3 = d3_svc * 0.40 + d3_vol * 0.30 + 2 * 0.30

    # D4: Industry / Sector Risk (15%)
    d4 = score_sector(data.get("sector"))

    # D5: Delivery Channel Risk (10%)
    intro_map = {"direct": 1, "regulated": 1, "non-regulated": 3, "unsolicited": 4}
    d5_intro = 2
    intro = (data.get("introduction_method") or "").lower()
    for k, v in intro_map.items():
        if k in intro:
            d5_intro = v
            break
    d5 = d5_intro * 0.50 + 2 * 0.50  # non-face-to-face by default

    # Composite
    composite = (d1 * 0.30 + d2 * 0.25 + d3 * 0.20 + d4 * 0.15 + d5 * 0.10) / 4 * 100
    composite = round(composite, 1)

    if composite >= 70:
        level = "VERY_HIGH"
    elif composite >= 55:
        level = "HIGH"
    elif composite >= 40:
        level = "MEDIUM"
    else:
        level = "LOW"

    # ── FLOOR RULE 1: Sanctioned / FATF_BLACK incorporation country → force VERY_HIGH ──
    # If the incorporation country is sanctioned or FATF blacklisted,
    # the overall risk level MUST be VERY_HIGH regardless of composite score.
    inc_country = (data.get("country") or "").lower().strip()
    if inc_country and (inc_country in SANCTIONED or inc_country in FATF_BLACK):
        if level != "VERY_HIGH":
            logger.info(f"FLOOR RULE: Country '{inc_country}' is sanctioned/FATF_BLACK — forcing VERY_HIGH (was {level}, score {composite})")
        level = "VERY_HIGH"
        composite = max(composite, 70.0)

    # ── FLOOR RULE 2: UBO/Director sanctioned nationality → force VERY_HIGH ──
    # If any UBO or director holds nationality of a sanctioned/FATF_BLACK country,
    # the overall risk level MUST be VERY_HIGH regardless of composite score.
    sanctioned_set = SANCTIONED | FATF_BLACK
    for person in data.get("directors", []) + data.get("ubos", []):
        nat = (person.get("nationality") or person.get("nat") or "").strip().lower()
        if nat:
            mapped = nat_demonym_map.get(nat, nat)
            if mapped in sanctioned_set:
                person_name = person.get("full_name") or person.get("name") or "unknown"
                if level != "VERY_HIGH":
                    logger.info(f"FLOOR RULE: UBO/Director '{person_name}' nationality '{nat}' maps to sanctioned '{mapped}' — forcing VERY_HIGH (was {level}, score {composite})")
                level = "VERY_HIGH"
                composite = max(composite, 70.0)
                break  # One match is sufficient

    lane_map = {"LOW": "Fast Lane", "MEDIUM": "Standard Review", "HIGH": "EDD", "VERY_HIGH": "EDD"}

    return {
        "score": composite,
        "level": level,
        "dimensions": {"d1": round(d1, 2), "d2": round(d2, 2), "d3": round(d3, 2), "d4": round(d4, 2), "d5": round(d5, 2)},
        "lane": lane_map[level]
    }
